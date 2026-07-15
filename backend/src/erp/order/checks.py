"""订单四检引擎（R2-05 增量2；软标记，C7=公式沿用 BR-ORD-005/008/009 全新实现）。

四检 = phishing / purchaser / price_limit / consistency（001/07 + 002 冻结契约口径）。
- phishing（BR-ORD-005）：地址 A1+A2 标准化（去空格/大写/去标点）↔ blacklist_address
  双向 substring（<8 字符条目跳过防误伤）；邮编取前 5 位 ↔ blacklist_zip；证据三档。
  BR-ORD-006：flagged 未放行不可被重检覆盖，人工 resolve 才清。
- purchaser：团队无 active 采购方 → flagged（BR-ORD-007 候选匹配降档，档案扩列未决——考古 §1.5）。
- price_limit（BR-ORD-008）：限价 = 渠道行单价 × margin × usd_rmb ÷ 采购方汇率（active 最高）；
  货源价（product.price_snapshot.list）> 限价 → flagged；货源价缺失 → flagged
  （BR-ORD-010 采集失败档）。
- consistency（BR-ORD-009）：SequenceMatcher.ratio(渠道品名, 本地 product.title) < 阈值 → flagged。
  渠道品名不入库（order_line 无此列，07 设计）——检时缓存进 detail，rerun 复用缓存。

参数一律配置中心（D-Q11/C10）：team_config > system_config 'order.checks'
{margin_factor: 0.85, usd_rmb_rate: 6.8, consistency_ratio: 0.9}。
"""

import json
import re
from difflib import SequenceMatcher
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.notify.service import notify

log = structlog.get_logger()

_DEFAULTS = {"margin_factor": 0.85, "usd_rmb_rate": 6.8, "consistency_ratio": 0.9}
_MIN_BLACKLIST_LEN = 8  # BR-ORD-005：黑名单条目 <8 字符跳过（防「123 Main St」误伤）
_NON_ALNUM = re.compile(r"[^0-9A-Z]")

CHECK_KINDS = ("phishing", "purchaser", "price_limit", "consistency")


def normalize_street(value: str) -> str:
    """BR-ORD-005 标准化：去空格/大写/去标点。导入通道与查表共用（口径永不分叉）。"""
    return _NON_ALNUM.sub("", value.upper())


def normalize_zip5(value: str) -> str:
    """zip+4 取前 5 位（仅保留数字后截取）。"""
    digits = re.sub(r"[^0-9]", "", value)
    return digits[:5]


async def _config(session: AsyncSession, team_id: int) -> dict[str, float]:
    cfg = dict(_DEFAULTS)
    for sql, params in (
        ("SELECT value FROM app.system_config WHERE key = 'order.checks'", {}),
        (
            "SELECT value FROM app.team_config WHERE team_id = :t AND key = 'order.checks'",
            {"t": team_id},
        ),
    ):  # 后读 team 覆盖 system
        row = (await session.execute(text(sql), params)).first()
        if row is not None and isinstance(row[0], dict):
            cfg.update({k: float(v) for k, v in row[0].items() if k in _DEFAULTS})
    return cfg


async def _check_phishing(
    session: AsyncSession, *, team_id: int, ship_to: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    street = normalize_street(f"{ship_to.get('address1') or ''}{ship_to.get('address2') or ''}")
    zip5 = normalize_zip5(str(ship_to.get("postalCode") or ""))
    addr_hit = None
    if street:
        addr_hit = (
            await session.execute(
                text(
                    "SELECT id, street_norm FROM app.blacklist_address"
                    " WHERE status = 'active' AND (team_id IS NULL OR team_id = :t)"
                    "   AND length(street_norm) >= :minlen"
                    "   AND (position(street_norm IN :street) > 0"
                    "        OR position(:street IN street_norm) > 0)"
                    " LIMIT 1"
                ),
                {"t": team_id, "street": street, "minlen": _MIN_BLACKLIST_LEN},
            )
        ).first()
    zip_hit = None
    if zip5:
        zip_hit = (
            await session.execute(
                text(
                    "SELECT id FROM app.blacklist_zip WHERE status = 'active'"
                    " AND (team_id IS NULL OR team_id = :t) AND zip5 = :z LIMIT 1"
                ),
                {"t": team_id, "z": zip5},
            )
        ).first()
    if addr_hit and zip_hit:
        level = "address+zip"
    elif addr_hit:
        level = "address"
    elif zip_hit:
        level = "zip"
    else:
        return "pass", {}
    detail: dict[str, Any] = {"level": level}
    if addr_hit:
        detail["address_hit_id"] = int(addr_hit.id)
    if zip_hit:
        detail["zip_hit_id"] = int(zip_hit.id)
    return "flagged", detail


async def _max_active_rate(session: AsyncSession, team_id: int) -> float | None:
    rate = (
        await session.execute(
            text(
                "SELECT max(exchange_rate) FROM app.purchaser"
                " WHERE team_id = :t AND status = 'active'"
            ),
            {"t": team_id},
        )
    ).scalar_one_or_none()
    return float(rate) if rate is not None else None


async def _source_prices(
    session: AsyncSession, lines: list[dict[str, Any]]
) -> dict[str, float | None]:
    """行 channel_sku → 货源价（product.price_snapshot.list）；无回连/无价 = None。"""
    out: dict[str, float | None] = {}
    for ln in lines:
        pid = ln.get("product_id")
        if not pid:
            out[ln["channel_line_no"]] = None
            continue
        snap = (
            await session.execute(
                text("SELECT price_snapshot FROM app.product WHERE id = :p"), {"p": pid}
            )
        ).scalar_one_or_none()
        price = snap.get("list") if isinstance(snap, dict) else None
        out[ln["channel_line_no"]] = float(price) if price is not None else None
    return out


async def run_order_checks(
    session: AsyncSession,
    *,
    order_id: int,
    order_date: Any,
    team_id: int,
    channel_titles: dict[str, str] | None = None,
) -> dict[str, str]:
    """跑四检并落 order_check（upsert）。返回 {check_kind: result}。

    channel_titles（行号→渠道品名）拉单时传入；rerun 时为 None → consistency 复用
    上次 detail 缓存的品名，无缓存则维持上次结果（渠道品名不入库，07 设计）。
    """
    order = (
        (
            await session.execute(
                text(
                    "SELECT ship_to, internal_status FROM app.channel_order"
                    " WHERE id = :o AND order_date = :d FOR UPDATE"
                ),
                {"o": order_id, "d": order_date},
            )
        )
        .mappings()
        .one()
    )
    lines = [
        dict(r)
        for r in (
            await session.execute(
                text(
                    "SELECT channel_line_no, channel_sku, product_id, unit_price"
                    " FROM app.order_line WHERE order_id = :o AND order_date = :d"
                    " ORDER BY channel_line_no"
                ),
                {"o": order_id, "d": order_date},
            )
        ).mappings()
    ]
    cfg = await _config(session, team_id)
    results: dict[str, str] = {}

    # ── phishing ──
    result, detail = await _check_phishing(session, team_id=team_id, ship_to=order["ship_to"])
    results["phishing"] = await _upsert_check(
        session, order_id=order_id, order_date=order_date, team_id=team_id,
        kind="phishing", result=result, detail=detail,
    )  # fmt: skip

    # ── purchaser（降档口径：存在 active 采购方即可）──
    rate = await _max_active_rate(session, team_id)
    results["purchaser"] = await _upsert_check(
        session, order_id=order_id, order_date=order_date, team_id=team_id,
        kind="purchaser",
        result="pass" if rate is not None else "flagged",
        detail={} if rate is not None else {"reason": "no_active_purchaser"},
    )  # fmt: skip

    # ── price_limit（BR-ORD-008；无采购方汇率时跳过金额比较=pass，由 purchaser 检兜底）──
    source = await _source_prices(session, lines)
    over: list[dict[str, Any]] = []
    missing: list[str] = []
    if rate is not None:
        for ln in lines:
            src = source[ln["channel_line_no"]]
            if src is None:
                missing.append(ln["channel_line_no"])
                continue
            limit = float(ln["unit_price"]) * cfg["margin_factor"] * cfg["usd_rmb_rate"] / rate
            if src > limit:
                over.append(
                    {"line": ln["channel_line_no"], "source": src, "limit": round(limit, 2)}
                )
    pl_result = "flagged" if (over or missing) else "pass"
    results["price_limit"] = await _upsert_check(
        session, order_id=order_id, order_date=order_date, team_id=team_id,
        kind="price_limit", result=pl_result,
        detail={"over": over, "source_missing": missing} if pl_result == "flagged" else {},
    )  # fmt: skip

    # ── consistency（BR-ORD-009）──
    if channel_titles is None:
        prev = (
            await session.execute(
                text(
                    "SELECT detail FROM app.order_check WHERE order_id = :o"
                    " AND order_date = :d AND check_kind = 'consistency'"
                ),
                {"o": order_id, "d": order_date},
            )
        ).scalar_one_or_none()
        channel_titles = (prev or {}).get("channel_titles") or {}
    mismatches: list[dict[str, Any]] = []
    for ln in lines:
        title = (channel_titles or {}).get(ln["channel_line_no"])
        if not title or not ln.get("product_id"):
            continue  # 无渠道品名缓存/无回连——不制造噪声（price_limit 已覆盖缺数据面）
        local = (
            await session.execute(
                text("SELECT title FROM app.product WHERE id = :p"), {"p": ln["product_id"]}
            )
        ).scalar_one_or_none()
        if not local:
            continue
        ratio = SequenceMatcher(None, _title_norm(title), _title_norm(str(local))).ratio()
        if ratio < cfg["consistency_ratio"]:
            mismatches.append(
                {"line": ln["channel_line_no"], "ratio": round(ratio, 3), "channel": title}
            )
    results["consistency"] = await _upsert_check(
        session, order_id=order_id, order_date=order_date, team_id=team_id,
        kind="consistency",
        result="flagged" if mismatches else "pass",
        detail={"mismatches": mismatches, "channel_titles": channel_titles or {}},
    )  # fmt: skip

    # ── 收口：has_flag + pulled→checked + 通知 ──
    flagged = [k for k, v in results.items() if v == "flagged"]
    await session.execute(
        text(
            "UPDATE app.channel_order SET has_flag = :f,"
            " internal_status = CASE WHEN internal_status = 'pulled' THEN 'checked'"
            "                        ELSE internal_status END"
            " WHERE id = :o AND order_date = :d"
        ),
        {"f": bool(flagged), "o": order_id, "d": order_date},
    )
    if flagged:
        await notify(
            session,
            team_id=team_id,
            severity="warn",
            category="order_flag",
            title=f"订单 #{order_id} 四检命中：{'/'.join(flagged)}",
            body="软标记不拦截（order_block 档位可改）；请到订单详情核对处置",
            object_type="channel_order",
            object_id=str(order_id),
            dedupe_key=f"order_flag:{order_id}",
        )
    return results


def _title_norm(value: str) -> str:
    return re.sub(r"[^0-9a-z ]", "", value.lower())


async def _upsert_check(
    session: AsyncSession,
    *,
    order_id: int,
    order_date: Any,
    team_id: int,
    kind: str,
    result: str,
    detail: dict[str, Any],
) -> str:
    """重检 upsert；BR-ORD-006：phishing flagged 未放行不可覆盖（返回既有 flagged）。"""
    if kind == "phishing":
        existing = (
            await session.execute(
                text(
                    "SELECT result, resolved_at FROM app.order_check WHERE order_id = :o"
                    " AND order_date = :d AND check_kind = 'phishing'"
                ),
                {"o": order_id, "d": order_date},
            )
        ).first()
        if existing is not None and existing.result == "flagged" and existing.resolved_at is None:
            return "flagged"
    await session.execute(
        text(
            "INSERT INTO app.order_check"
            " (team_id, order_id, order_date, check_kind, result, detail)"
            " VALUES (:t, :o, :d, :k, :r, cast(:dt AS jsonb))"
            " ON CONFLICT (order_id, check_kind, order_date) DO UPDATE SET"
            "   result = excluded.result, detail = excluded.detail, checked_at = now(),"
            "   resolved_by = CASE WHEN excluded.result = 'flagged' THEN NULL"
            "                      ELSE app.order_check.resolved_by END,"
            "   resolved_at = CASE WHEN excluded.result = 'flagged' THEN NULL"
            "                      ELSE app.order_check.resolved_at END"
        ),
        {
            "t": team_id,
            "o": order_id,
            "d": order_date,
            "k": kind,
            "r": result,
            "dt": json.dumps(detail, ensure_ascii=False),
        },
    )
    return result
