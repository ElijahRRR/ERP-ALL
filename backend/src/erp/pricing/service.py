"""定价域服务层（R2-06 增量2/3）：策略解析 / 价格历史 / 单品出价 / 价格同步管道。

- resolve_strategy：active 策略解析，store 级覆盖 > team 级默认（D-Q23；
  0027 活跃唯一索引保证每级至多一条）；
- record_price_history：app.price_history 唯一写入口（reason ∈
  strategy/manual/watchdog/initial，0027 CHECK 同步）；
- price_product / preview_product：engine 纯函数的产品级组装——上架/改价链走
  price_product（compute_price 严格版，区间外不出价 fail-closed，BR-PR-004）；
  预览链走 preview_product（compute_price_clamped 展示版 + 严格判定并列返回）。
- push_price（增量3）：live 在架改价唯一入口——outbox price_push 三段式推
  PUT /v3/price（考古口径2 canonical 单品体），渠道 200 才两段式回填
  current_price + price_history（BR-LC-011 保真）；结果未知 → verify_pending
  等 price_recon 对账（BR-GW-005 永不盲重试）。

业务参数（区间/min_price）一律来自 pricing_strategy.params；30% 确认阈值来自
配置中心 pricing.confirm_threshold_pct（team > system > 默认 0.30，考古口径 9）。
"""

import json
from collections.abc import Mapping
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.channel import outbox
from erp.channel.gateway import gateway
from erp.channel.gateway.client import GatewayResponse
from erp.core.db import ctx_tx
from erp.core.errors import BusinessError
from erp.notify.service import notify
from erp.pricing import engine
from erp.pricing.engine import PriceResult

log = structlog.get_logger()

CONFIRM_THRESHOLD_KEY = "pricing.confirm_threshold_pct"
DEFAULT_CONFIRM_THRESHOLD = 0.30

# 限流闸最长等待（秒，技术参数非业务参数）：PUT /v3/price 100/hour 配额耗尽时
# 快速拒绝（命令归还 pending 由 beat 兜底），绝不在同步请求内长睡等 token；
# 必须远小于 outbox lease_seconds（默认 120s），否则睡眠中 lease 过期会被误扫。
_GATE_MAX_WAIT_S = 10.0

# allocate 拒绝口径（rejected.code = 'PRICING_' + reason.upper()）的中文说明
REJECT_MESSAGES = {
    "out_of_band": "成本总价不在策略区间内，不出价（BR-PR-004 区间外不上架）",
    "below_min_price": "算出价低于策略 min_price 硬底线，不出价",
    "min_price_required": "策略缺 min_price 硬底线（fail-closed 不出价）",
    "no_source_price": "产品无可计价源价（price_snapshot 缺失或无法解析）",
    "no_bands": "策略无该履约类型的可解析区间，不出价",
    "manual_price_required": "manual 策略不自动出价，需人工经改价入口给价",
}


async def resolve_strategy(
    session: AsyncSession, *, team_id: int, store_id: int, offer_mode: str
) -> dict[str, Any] | None:
    """解析 (team×store×offer_mode) 的生效策略：store 级优先，缺则回落 team 级。"""
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, store_id, offer_mode, name, algo_code, params, version"
                    " FROM app.pricing_strategy"
                    " WHERE team_id = :t AND offer_mode = :m AND status = 'active'"
                    "   AND (store_id = :s OR store_id IS NULL)"
                    " ORDER BY store_id NULLS LAST LIMIT 1"
                ),
                {"t": team_id, "m": offer_mode, "s": store_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


async def record_price_history(
    session: AsyncSession,
    *,
    listing_id: int,
    team_id: int,
    old_price: float | None,
    new_price: float,
    reason: str,
    strategy: Mapping[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
    actor_id: int | None = None,
) -> None:
    """价格变更史唯一写入口（0027 月分区表；strategy_id/version 供计算明细回溯）。"""
    await session.execute(
        text(
            "INSERT INTO app.price_history"
            " (listing_id, team_id, old_price, new_price, reason,"
            "  strategy_id, strategy_version, detail, created_by)"
            " VALUES (:l, :t, :o, :n, :r, :sid, :sv, cast(:d AS jsonb), :by)"
        ),
        {
            "l": listing_id,
            "t": team_id,
            "o": old_price,
            "n": new_price,
            "r": reason,
            "sid": strategy["id"] if strategy else None,
            "sv": strategy["version"] if strategy else None,
            "d": json.dumps(detail or {}, ensure_ascii=False),
            "by": actor_id,
        },
    )


async def confirm_threshold(session: AsyncSession, team_id: int) -> float:
    """30% 确认阈值（BR-PR-008 参数化）：team_config > system_config > 默认 0.30。

    经请求会话直读（GUC 已就位，RLS 生效）——ConfigService 自管会话无团队上下文，
    读不到 team 级覆盖，故不经它。
    """
    raw = (
        await session.execute(
            text(
                "SELECT value #>> '{}' AS v FROM ("
                "  SELECT value, 0 AS pri FROM app.team_config"
                "   WHERE team_id = :t AND key = :k"
                "  UNION ALL"
                "  SELECT value, 1 AS pri FROM app.system_config WHERE key = :k"
                ") c ORDER BY pri LIMIT 1"
            ),
            {"t": team_id, "k": CONFIRM_THRESHOLD_KEY},
        )
    ).scalar_one_or_none()
    return float(raw) if raw is not None else DEFAULT_CONFIRM_THRESHOLD


def _fulfillment(product: Mapping[Any, Any], params: dict[str, Any]) -> str:
    """履约类型判定：attrs.fulfillment / fulfillment_type 含 'FBA' 判 FBA，
    否则取策略 params.default_fulfillment（缺省 FBM——自发货为常态）。"""
    attrs = product.get("attrs") or {}
    if isinstance(attrs, dict):
        raw = str(attrs.get("fulfillment") or attrs.get("fulfillment_type") or "")
        if "FBA" in raw.upper():
            return "FBA"
    return str(params.get("default_fulfillment", "FBM"))


def price_product(strategy: Mapping[str, Any], product: Mapping[Any, Any]) -> PriceResult:
    """按策略给单个产品出价（上架/改价链：compute_price 严格版，区间外不出价）。

    - manual 策略不自动出价（D-Q23 match 现行=人工指定价）→ manual_price_required；
    - 源价无法解析（price_snapshot 缺失/N/A）→ no_source_price。
    """
    if strategy["algo_code"] == "manual":
        return PriceResult(ok=False, reason="manual_price_required", detail={"algo": "manual"})
    params: dict[str, Any] = strategy["params"] or {}
    snap = product.get("price_snapshot") or {}
    total = engine.source_total(snap) if isinstance(snap, dict) else None
    if total is None:
        return PriceResult(ok=False, reason="no_source_price", detail={"algo": "cost_plus"})
    return engine.compute_price(total, fulfillment=_fulfillment(product, params), params=params)


def preview_product(strategy: Mapping[str, Any], product: Mapping[Any, Any]) -> dict[str, Any]:
    """预览试算（只读展示链）：clamp 版出价 + compute_price 严格判定并列返回。

    new_price/detail 取 compute_price_clamped（区间外仍给参考价，detail 带
    out_of_band/clamp 标记）；ok/reason 取 compute_price 严格口径——前端据此
    区分「可直接上架」与「仅供参考」。manual 策略 → reason='manual' 不试算。
    """
    if strategy["algo_code"] == "manual":
        return {"ok": False, "reason": "manual", "new_price": None, "detail": {"algo": "manual"}}
    params: dict[str, Any] = strategy["params"] or {}
    snap = product.get("price_snapshot") or {}
    total = engine.source_total(snap) if isinstance(snap, dict) else None
    if total is None:
        return {
            "ok": False,
            "reason": "no_source_price",
            "new_price": None,
            "detail": {"algo": "cost_plus"},
        }
    ftype = _fulfillment(product, params)
    clamped = engine.compute_price_clamped(total, fulfillment=ftype, params=params)
    strict = engine.compute_price(total, fulfillment=ftype, params=params)
    return {
        "ok": strict.ok,
        "reason": strict.reason,
        "new_price": clamped.price,
        "detail": clamped.detail,
    }


# ── 价格同步管道（R2-06 增量3：单品 PUT 通道）──


def build_put_price_body(sku: str, price: float, currency: str = "USD") -> dict[str, Any]:
    """PUT /v3/price canonical 单品体（考古口径2：官方 Price.json 扁平结构，
    旧仓 A/C 两套一致生产验证）。

    ⚠️ 硬性禁止促销字段（考古范围边界）：本函数只产 BASE 常规价。
    ?promo=true 查询参数、promotionInformation / effectiveDate / expirationDate /
    processMode / promoId / REDUCED|CLEARANCE priceType 等促销字段**绝不出现**——
    渠道同端点仅靠 query/字段区分常规价与促销价，误带促销字段即事故。
    促销价管道不在本期范围（archaeology.md §一）。
    """
    return {
        "sku": sku,
        "pricing": [
            {
                "currentPriceType": "BASE",
                "currentPrice": {"currency": currency, "amount": round(float(price), 2)},
            }
        ],
    }


async def apply_price_backfill(
    session: AsyncSession,
    *,
    listing_id: int,
    team_id: int,
    payload: Mapping[str, Any],
    detail_extra: dict[str, Any] | None = None,
    actor_id: int | None = None,
) -> None:
    """两段式回填（BR-LC-011 保真）：渠道确认成功后才落 current_price + 价史。

    applier（HTTP 200）与 price_recon（对账确认）共用——两条确认路径同一落账口径。
    """
    new_price = float(payload["new_price"])
    await session.execute(
        text("UPDATE app.listing SET current_price = :p WHERE id = :id"),
        {"p": new_price, "id": listing_id},
    )
    strategy: dict[str, Any] | None = None
    if payload.get("strategy_id") is not None:
        strategy = {"id": payload["strategy_id"], "version": payload.get("strategy_version")}
    detail = dict(payload.get("detail") or {})
    detail.update(detail_extra or {})
    await record_price_history(
        session,
        listing_id=listing_id,
        team_id=team_id,
        old_price=payload.get("old_price"),
        new_price=new_price,
        reason=str(payload["reason"]),
        strategy=strategy,
        detail=detail,
        actor_id=actor_id,
    )


async def push_price(
    sessions: async_sessionmaker[AsyncSession],
    *,
    team_id: int,
    listing_id: int,
    new_price: float,
    reason: str,
    strategy: Mapping[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
    force: bool = False,
    actor_id: int | None = None,
    is_super: bool = False,
) -> dict[str, Any]:
    """在架改价唯一入口。三段式（照 listing delist 房例）：
    tx1 校验+守护+落 outbox 命令 → COMMIT → HTTP（PUT /v3/price）→ tx2 归位。

    - 仅 live/published 可推渠道价；锁定拒绝；
    - reason='manual'：策略 min_price 守护先行（guard_manual_price fail-closed）；
    - 30% 确认阈值对 live 同样先行（BR-PR-008，force 透传）；
    - 价差 < $0.01 直接跳过不 enqueue（BR-PR-006 省配额）；
    - 传输失败/结果未知 → 命令 verify_pending 等 price_recon 对账（BR-GW-005
      永不盲重试）；限流闸拒绝 → 命令留 pending 由 beat 兜底继续推。
    """
    if new_price <= 0:
        raise BusinessError("LISTING_PRICE_INVALID", "价格必须大于 0")
    price = round(float(new_price), 2)
    async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT id, team_id, store_id, offer_mode, channel_sku, status,"
                        " is_locked, current_price FROM app.listing WHERE id = :id FOR UPDATE"
                    ),
                    {"id": listing_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["team_id"] != team_id:
            raise BusinessError("LISTING_NOT_FOUND", "listing 不存在")
        listing = dict(row)
        if listing["is_locked"]:
            raise BusinessError("LISTING_LOCKED", "listing 已锁定")
        if listing["status"] not in ("live", "published"):
            raise BusinessError(
                "LISTING_STATE_INVALID",
                f"状态 {listing['status']} 不可推渠道价（仅 live/published）",
            )
        old_price = (
            float(listing["current_price"]) if listing["current_price"] is not None else None
        )
        guard_detail = dict(detail or {})
        if reason == "manual":
            resolved = await resolve_strategy(
                session,
                team_id=team_id,
                store_id=listing["store_id"],
                offer_mode=listing["offer_mode"],
            )
            if resolved is not None:
                guard = engine.guard_manual_price(price, resolved["params"] or {})
                if not guard.ok:
                    raise BusinessError(
                        "PRICING_BELOW_MIN_PRICE",
                        "人工价未过策略 min_price 硬底线（fail-closed 拒绝）",
                        detail=guard.detail,
                    )
                guard_detail.update(guard.detail)
            if strategy is None:
                strategy = resolved
        threshold = await confirm_threshold(session, team_id)
        if (
            old_price is not None
            and engine.exceeds_confirm_threshold(old_price, price, pct=threshold)
            and not force
        ):
            raise BusinessError(
                "PRICING_CONFIRM_REQUIRED",
                f"价格变动超 {threshold:.0%}，需 force 确认",
                detail={"old": old_price, "new": price, "threshold_pct": threshold},
                http_status=422,
            )
        if not engine.price_changed(old_price, price):
            # BR-PR-006：价差 < $0.01 视为未变——不 enqueue 不耗配额
            return {
                "listing_id": listing_id,
                "skipped": True,
                "reason": "unchanged",
                "current_price": old_price,
            }
        # 模式闸预检（live 未放量等拒绝 → tx1 整体回滚，无残留命令）
        await gateway.prepare(session, listing["store_id"])
        enq = await outbox.enqueue(
            session,
            team_id=team_id,
            store_id=listing["store_id"],
            action="price_push",
            payload={
                "method": "PUT",
                "path": "/v3/price",
                "endpoint_key": "PUT /v3/price",
                "gate_max_wait": _GATE_MAX_WAIT_S,
                "json_body": build_put_price_body(listing["channel_sku"], price),
                "store_id": listing["store_id"],
                "listing_id": listing_id,
                "old_price": old_price,
                "new_price": price,
                "reason": reason,
                "strategy_id": strategy["id"] if strategy else None,
                "strategy_version": strategy["version"] if strategy else None,
                "detail": guard_detail,
            },
            idempotency_key=f"price:{listing_id}:{price}",
            object_type="listing",
            object_id=listing_id,
            created_by=actor_id,
        )
    outcome = await outbox.execute_command(
        sessions,
        enq["command_id"],
        team_id=team_id,
        is_super=is_super,
        applier=_apply_price_push,
    )
    return {"listing_id": listing_id, "command_id": enq["command_id"], **outcome}


def _channel_error_code(resp: GatewayResponse) -> tuple[str, list[dict[str, Any]]]:
    """从渠道错误体提取首个错误码（GatewayError 形态 code/field/description）。

    注意：网关当前仅解析 2xx 响应体（4xx 时 resp.data 为 None）——此处防御式
    解析，取不到时回落 HTTP_{status}。errors 全量入命令 result 供人工核对。
    """
    data = resp.data if isinstance(resp.data, dict) else {}
    raw = data.get("errors")
    if isinstance(raw, dict):
        raw = raw.get("error")
    errors = [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
    code = str(errors[0].get("code")) if errors and errors[0].get("code") else f"HTTP_{resp.status}"
    return code, errors


async def _apply_price_push(  # noqa: PLR0911 归位分支=渠道响应形态（dry_run/未知/成功/明确拒）
    session: AsyncSession, cmd: dict[str, Any], resp: GatewayResponse | None
) -> dict[str, Any]:
    """price_push 的 tx2 归位（outbox.Applier 契约：complete(fence) 成功才落业务态）。"""
    payload = cmd["payload"]
    listing_id = int(cmd["object_id"])
    cid, fence = int(cmd["id"]), int(cmd["fence"])

    if resp is not None and resp.dry_run:
        if not await outbox.complete(
            session, command_id=cid, fence=fence, status="succeeded", result={"dry_run": True}
        ):
            return {"command_status": "superseded"}
        return {"status": "succeeded", "dry_run": True, "request_snapshot": resp.request_snapshot}

    if resp is None or resp.status is None:
        # 结果未知：不回填价格、不重发（BR-GW-005）——等 price_recon 拉渠道实价对账
        if not await outbox.complete(session, command_id=cid, fence=fence, status="verify_pending"):
            return {"command_status": "superseded"}
        return {"status": "verify_pending"}

    if resp.status == 200:  # noqa: PLR2004
        data = resp.data if isinstance(resp.data, dict) else {}
        inner = data.get("ItemPriceResponse") if isinstance(data.get("ItemPriceResponse"), dict) else data  # fmt: skip
        message = str(inner.get("message") or "") or None
        if not await outbox.complete(
            session,
            command_id=cid,
            fence=fence,
            status="succeeded",
            result={"http_status": 200, "message": message},
        ):
            return {"command_status": "superseded"}
        # 两段式回填（BR-LC-011）：渠道 200 确认才落 current_price + price_history
        await apply_price_backfill(
            session,
            listing_id=listing_id,
            team_id=int(cmd["team_id"]),
            payload=payload,
            detail_extra={"channel_message": message} if message else {},
            actor_id=cmd.get("created_by"),
        )
        return {"status": "succeeded", "current_price": payload["new_price"]}

    # 渠道明确拒（4xx 业务拒等）：命令 failed + 告警，**不回填价格**
    error_code, errors = _channel_error_code(resp)
    if not await outbox.complete(
        session,
        command_id=cid,
        fence=fence,
        status="failed",
        result={"http_status": resp.status, "errors": errors},
        error_code=error_code,
    ):
        return {"command_status": "superseded"}
    await notify(
        session,
        team_id=int(cmd["team_id"]),
        severity="warn",
        category="pricing",
        title=f"改价被渠道拒绝（listing #{listing_id}）",
        body=f"PUT /v3/price HTTP {resp.status}（{error_code}）；价格未回填，核对后重试",
        object_type="listing",
        object_id=str(listing_id),
        dedupe_key=f"price_push:{listing_id}",
    )
    return {"status": "failed", "error_code": error_code, "http_status": resp.status}


# outbox 命令 → tx2 归位函数（drain 工具按 action 索取，与 listing.APPLIERS 同构）
APPLIERS: dict[str, outbox.Applier] = {"price_push": _apply_price_push}
