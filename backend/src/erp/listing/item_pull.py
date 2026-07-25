"""全店后台 SKU 拉取对账（R2-12 增量4a；D-Q65② 上游，P0-2 读写分离）。

协议（考古 raw-C + 旧仓 daily_cleanup/feishu_sync 实战语义逐字移植）：
- GET /v3/items 按 publishedStatus 逐态扫描；带 query 必须显式
  endpoint_key="GET /v3/items?q"（60/min 桶——缺省剥 query 会错落 300/min 桶撞 429）；
- items 翻页语义特殊（旧仓实测）：nextCursor 是会话 ID 恒定不变，真翻页靠 offset
  递增；终止 = 本页 < limit 或 offset ≥ totalItems；跨页重复 SKU 去重（首见为准）。
- 三类差异**只发现不执行**（P0-2 拍板，执行走 maintenance runner）：
  ① 后台有本地无 → 通知聚合 + sync_state.stats 留档样例（listing.product_id NOT NULL，
     拉取侧无产品链路，不造半基线行）；
  ② 状态漂移（本地 live/published、渠道 UNPUBLISHED/SYSTEM_PROBLEM）→ transition
     degraded（error_code=ITEM_PULL_{类}）+ 13 类归类 + 按类生成 maintenance_task
     （A 过期→end_date_renewal；B/C/D/E/F/G/H/I/K 处置类→delist；J stage 待发布/
      L 系统瞬时/Z 兜底→不生成）；
  ③ 永久禁售类（B/C/E/F/G/K，旧仓六类清单）→ error_recycle **pending** 候选断言
     （D-Q65② 人工闸；asin 域恒记，品牌域仅 C/E 且非占位词；作用域=该 listing 的
      team——渠道证据默认不跨团队放大，人工确认时可另行升全局）。
- 本地在架（live/published）而渠道全扫不见 → 只计数进通知（不迁移——逐态扫描
  可能非全集，保守）。
"""

import json
from collections.abc import Callable
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.audit.pipeline import NON_BRAND_PLACEHOLDERS, _norm
from erp.channel.gateway import gateway
from erp.compliance import assertion as assertion_svc
from erp.core.db import system_tx
from erp.listing.service import transition
from erp.notify.service import notify

log = structlog.get_logger()

SYNC_SCOPE = "item_pull"
ITEMS_ENDPOINT_KEY = "GET /v3/items?q"  # 60/min 桶；绝不可省（缺省剥 query 落错桶）

# 13 类归类表（旧仓 feishu_sync._CATEGORIES 逐字移植；文本恒 lower 后匹配）
_CATEGORIES: dict[str, tuple[str, Callable[[str], bool]]] = {
    "A": ("过期", lambda t: "end date has passed" in t),
    "B": (
        "禁售",
        lambda t: (
            "prohibited product policy" in t
            or ("prohibited due to" in t and "walmart" in t)
            or "for violating walmart's marketplace" in t
            or "reference code biz" in t
            or "cpsc recall" in t
            or "safety warning" in t
            or "circumvent walmart" in t
        ),
    ),
    "C": (
        "品牌",
        lambda t: (
            "partnered with select brands" in t or "biz-cn" in t or "restricts certain brands" in t
        ),
    ),
    "D": ("价格", lambda t: "price gouging" in t),
    "E": ("知产", lambda t: "intellectual property" in t),
    "F": (
        "限类",
        lambda t: "restricted to certain sellers" in t or "restricted category that requires" in t,
    ),
    "G": ("药品", lambda t: "drugs and drug paraphernalia" in t),
    "H": (
        "信息",
        lambda t: (
            "tax code" in t
            or "no price was found" in t
            or "shipping information was not added" in t
        ),
    ),
    "I": (
        "内容",
        lambda t: (
            "content standards" in t
            or "content policy" in t
            or "authenticity claims" in t
            or "title wasn't in the correct format" in t
            or "missing a primary image" in t
            or "main image url" in t
        ),
    ),
    "J": (
        "特殊",
        lambda t: (
            "preorder program" in t
            or "restored program" in t
            or "pre-owned program" in t
            or "pre-owned policy" in t
            or "restored policy" in t
            or "refurbished or restored" in t
            or "stage status until you go live" in t
        ),
    ),
    "K": ("审查", lambda t: "flagged by our internal team" in t),
    "L": ("系统", lambda t: "internal error occurred" in t),
}
# 严重性顺序（旧仓：具体归类优先，B 通用禁售次之，A 过期最后）
_SEVERITY_ORDER = ("C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "B", "A")
# 永久禁售六类（旧仓 blacklist_sync 清单）→ 黑名单候选断言
PERMANENT_BAN = frozenset({"B", "C", "E", "F", "G", "K"})
# 归类 → 维护任务类型（J/L/Z 不生成：待发布/瞬时/兜底）
_TASK_KIND_BY_CATEGORY = {
    "A": "end_date_renewal",
    **{c: "delist" for c in ("B", "C", "D", "E", "F", "G", "H", "I", "K")},
}
_DRIFT_REMOTE = ("UNPUBLISHED", "SYSTEM_PROBLEM")
_ON_CHANNEL_LOCAL = ("live", "published")


def categorize(reason_text: str) -> str:
    """错误原因文本 → A-L 或 Z 兜底（多条 reason 以 ' | ' 拼接整体匹配）。"""
    t = (reason_text or "").lower()
    for code in _SEVERITY_ORDER:
        if _CATEGORIES[code][1](t):
            return code
    return "Z"


def _published_status(item: dict[str, Any], default: str) -> str:
    raw = item.get("publishedStatus")
    if isinstance(raw, dict):  # openapi：publishedStatus.status；宽容裸串
        return str(raw.get("status") or default)
    return str(raw or default)


def _reasons(item: dict[str, Any]) -> list[str]:
    raw = (item.get("unpublishedReasons") or {}).get("reason") or []
    return [str(r) for r in raw][:5]


async def pull_store_items(
    sessions: async_sessionmaker[AsyncSession], store_id: int, *, config: dict[str, Any]
) -> dict[str, Any]:
    """单店全量扫描 + 差异落点。任何页失败即抛（下轮整店重扫，幂等）。"""
    statuses = list(config.get("statuses", ["PUBLISHED", "UNPUBLISHED", "SYSTEM_PROBLEM"]))
    page_limit = int(config.get("page_limit", 200))
    max_pages = int(config.get("max_pages", 100))

    async with system_tx(sessions) as session:
        team_id = int(
            (
                await session.execute(
                    text("SELECT team_id FROM app.store WHERE id = :s"), {"s": store_id}
                )
            ).scalar_one()
        )
        ctx, mode = await gateway.prepare(session, store_id)

    remote: dict[str, dict[str, Any]] = {}
    pages = 0
    for st in statuses:
        offset = 0
        for _ in range(max_pages):
            resp = await gateway.request_prepared(
                ctx,
                mode,
                "GET",
                "/v3/items",
                endpoint_key=ITEMS_ENDPOINT_KEY,
                params={
                    "limit": str(page_limit),
                    "offset": str(offset),
                    "publishedStatus": st,
                },
            )
            if resp.dry_run:
                return {"dry_run": True, "pages": pages}
            if resp.status != 200 or not isinstance(resp.data, dict):  # noqa: PLR2004
                raise RuntimeError(
                    f"item_pull 店铺 {store_id} 失败：HTTP {resp.status}（{st} offset {offset}）"
                )
            pages += 1
            items = resp.data.get("ItemResponse") or []
            total = int(resp.data.get("totalItems") or 0)
            for it in items:
                sku = str(it.get("sku") or "").strip()
                if not sku or sku in remote:
                    continue  # 跨页重复 SKU 去重（旧仓教训），首见为准
                remote[sku] = {
                    "published_status": _published_status(it, st),
                    "lifecycle": str(it.get("lifecycleStatus") or ""),
                    "reasons": _reasons(it),
                }
            offset += len(items)
            if not items or len(items) < page_limit or (total and offset >= total):
                break

    async with system_tx(sessions) as session:
        stats = await land_store_diff(session, team_id=team_id, store_id=store_id, remote=remote)
        await session.execute(
            text(
                "INSERT INTO app.sync_state (scope, ref_id, last_sync_at, stats)"
                " VALUES (:sc, :r, now(), cast(:st AS jsonb))"
                " ON CONFLICT (scope, ref_id) DO UPDATE SET"
                "  last_sync_at = now(), stats = excluded.stats, updated_at = now()"
            ),
            {"sc": SYNC_SCOPE, "r": store_id, "st": _json(stats)},
        )
    return {**stats, "pages": pages, "remote": len(remote)}


async def land_store_diff(
    session: AsyncSession, *, team_id: int, store_id: int, remote: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """三类差异落点（单事务；可独立测试，不触渠道）。幂等：重跑零新增。"""
    rows = (
        await session.execute(
            text(
                "SELECT l.id, l.channel_sku, l.status, l.team_id,"
                "       p.source_ref AS asin, p.brand"
                " FROM app.listing l JOIN app.product p ON p.id = l.product_id"
                " WHERE l.store_id = :s"
            ),
            {"s": store_id},
        )
    ).all()
    local = {str(r.channel_sku): r for r in rows}
    by_category: dict[str, int] = {}
    stats: dict[str, Any] = {
        "local": len(local), "missing_local": 0, "gone_remote": 0,
        "drift_degraded": 0, "tasks": 0, "candidates": 0,
    }  # fmt: skip

    missing = sorted(sku for sku in remote if sku not in local)
    stats["missing_local"] = len(missing)

    for sku, r in local.items():
        rm = remote.get(sku)
        if rm is None:
            if r.status in _ON_CHANNEL_LOCAL:
                stats["gone_remote"] += 1  # 只发现：逐态扫描可能非全集，不迁移
            continue
        if rm["published_status"] not in _DRIFT_REMOTE or r.status not in _ON_CHANNEL_LOCAL:
            continue
        reasons_text = " | ".join(rm["reasons"])
        code = categorize(reasons_text)
        by_category[code] = by_category.get(code, 0) + 1
        if code == "J":
            continue  # 特殊程序/stage 待发布：不该动（旧仓 is_stage_pending 语义）
        name = _CATEGORIES[code][0] if code in _CATEGORIES else "其他"
        listing = {"id": int(r.id), "team_id": int(r.team_id), "status": str(r.status)}
        await transition(
            session,
            listing,
            "degraded",
            reason_code=f"ITEM_PULL_{code}",
            detail={
                "category": code,
                "category_name": name,
                "published_status": rm["published_status"],
                "reasons": rm["reasons"],
            },
        )
        stats["drift_degraded"] += 1
        kind = _TASK_KIND_BY_CATEGORY.get(code)
        if kind:
            stats["tasks"] += await _ensure_task(
                session,
                team_id=int(r.team_id),
                store_id=store_id,
                listing_id=int(r.id),
                kind=kind,
                priority=50 if kind == "delist" else 80,
            )
        if code in PERMANENT_BAN:
            stats["candidates"] += await _record_candidates(
                session, row=r, code=code, name=name, reasons_text=reasons_text
            )

    stats["by_category"] = by_category
    stats["missing_sample"] = missing[:50]
    if missing or stats["gone_remote"]:
        await notify(
            session,
            team_id=team_id,
            severity="warn",
            category="item_recon",
            title=(
                f"全店对账差异：后台有本地无 {len(missing)} 条"
                f" / 本地在架渠道缺失 {stats['gone_remote']} 条"
            ),
            body=("后台样例 SKU：" + ", ".join(missing[:10])) if missing else None,
            object_type="store",
            object_id=str(store_id),
            dedupe_key=f"item_recon:{store_id}",
        )
    return stats


async def _ensure_task(
    session: AsyncSession, *, team_id: int, store_id: int, listing_id: int, kind: str, priority: int
) -> int:
    """维护任务去重生成：同 listing 同 kind 已有在途（scheduled/running）则不重复。"""
    row = (
        await session.execute(
            text(
                "INSERT INTO app.maintenance_task"
                " (team_id, store_id, listing_id, task_kind, priority, scheduled_at)"
                " SELECT :t, :s, :l, :k, :p, now()"
                " WHERE NOT EXISTS (SELECT 1 FROM app.maintenance_task"
                "   WHERE listing_id = :l AND task_kind = :k"
                "     AND status IN ('scheduled','running'))"
                " RETURNING id"
            ),
            {"t": team_id, "s": store_id, "l": listing_id, "k": kind, "p": priority},
        )
    ).scalar_one_or_none()
    return 1 if row is not None else 0


async def _record_candidates(
    session: AsyncSession, *, row: Any, code: str, name: str, reasons_text: str
) -> int:
    """永久禁售类 → error_recycle pending 候选断言（人工闸，D-Q65②）。"""
    created = 0
    reason = f"渠道报错回收 {code} 类（{name}）：{reasons_text[:200]}"
    ref = f"listing:{row.id}"
    if row.asin:
        res = await assertion_svc.record_assertion(
            session,
            team_id=int(row.team_id),
            domain="asin",
            subject_norm=_norm(str(row.asin)),
            subject_display=str(row.asin),
            source="error_recycle",
            source_ref=ref,
            reason=reason,
            status="pending",
        )
        created += 1 if res["created"] else 0
    if code in ("C", "E") and row.brand:  # 品牌/知产两类才有品牌级证据
        bn = _norm(str(row.brand))
        if bn and bn not in NON_BRAND_PLACEHOLDERS:
            res = await assertion_svc.record_assertion(
                session,
                team_id=int(row.team_id),
                domain="brand",
                subject_norm=bn,
                subject_display=str(row.brand),
                source="error_recycle",
                source_ref=ref,
                reason=reason,
                status="pending",
            )
            created += 1 if res["created"] else 0
    return created


async def run(sessions: async_sessionmaker[AsyncSession], config: dict[str, Any]) -> dict[str, Any]:
    """beat 任务本体：遍历有凭证店铺逐店扫描（店间失败隔离，全败才抛）。"""
    async with system_tx(sessions) as session:
        store_ids = [
            int(r.id)
            for r in await session.execute(
                text(
                    "SELECT s.id FROM app.store s"
                    " JOIN app.store_credential c ON c.store_id = s.id"
                    " WHERE s.status = 'active' ORDER BY s.id"
                )
            )
        ]
    totals = {"stores": len(store_ids), "ok": 0, "failed": 0, "missing_local": 0,
              "drift_degraded": 0, "tasks": 0, "candidates": 0}  # fmt: skip
    for sid in store_ids:
        try:
            st = await pull_store_items(sessions, sid, config=config)
        except Exception as exc:
            totals["failed"] += 1
            log.warning("item_pull.store_failed", store_id=sid, error=str(exc))
            continue
        totals["ok"] += 1
        for k in ("missing_local", "drift_degraded", "tasks", "candidates"):
            totals[k] += int(st.get(k, 0))
    if store_ids and totals["failed"] and not totals["ok"]:
        raise RuntimeError(f"item_pull 全部店铺失败：{totals['failed']}/{len(store_ids)}")
    return totals


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
