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

# D-Q62/BR-PR-007：改价路由阈值——单店改价条数 ≤ 阈值走 PUT /v3/price（100/hour），
# 更多聚合一个 PRICE_AND_PROMOTION feed（10/hour 店铺级共享池）。默认 5，配置中心可覆盖。
PUT_ROUTE_THRESHOLD_KEY = "pricing.put_route_threshold"
DEFAULT_PUT_ROUTE_THRESHOLD = 5

# PRICE_AND_PROMOTION feed header（考古口径2 canonical envelope；技术校准常量，
# system_config `pricing.price_feed_header` 可逐字段覆盖——spec.py _HEADER_DEFAULTS 同款模式）
PRICE_FEED_TYPE = "PRICE_AND_PROMOTION"
_PRICE_FEED_HEADER_DEFAULTS: dict[str, Any] = {
    "business_unit": "WALMART_US",
    "locale": "en",
    "version": "2.0.20240126-12_25_52-api",  # 旧仓 A 版生产验证 + 官方 curl 示例一致
}

# 限流闸最长等待（秒，技术参数非业务参数）：PUT /v3/price 100/hour 配额耗尽时
# 快速拒绝（命令归还 pending 由 beat 兜底），绝不在同步请求内长睡等 token；
# 必须远小于 outbox lease_seconds（默认 120s），否则睡眠中 lease 过期会被误扫。
_GATE_MAX_WAIT_S = 10.0

# allocate 拒绝口径（rejected.code = 'PRICING_' + reason.upper()）的中文说明
REJECT_MESSAGES = {
    "out_of_band": "成本总价不在策略区间内，不出价（BR-PR-004 区间外不上架）",
    "below_min_price": "算出价低于策略 min_price 底线，不出价",
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


async def put_route_threshold(session: AsyncSession, team_id: int) -> int:
    """PUT/feed 路由阈值（D-Q62）：team_config > system_config > 默认 5。"""
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
            {"t": team_id, "k": PUT_ROUTE_THRESHOLD_KEY},
        )
    ).scalar_one_or_none()
    return int(float(raw)) if raw is not None else DEFAULT_PUT_ROUTE_THRESHOLD


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
    """两段式回填（BR-LC-011 保真）：渠道确认成功后才落 current_price + 价史，
    并清 pending_price 在途标记（派发半程在 push_price/push_price_feed tx1 写入）。

    applier（HTTP 200）/price_recon（对账确认）/feed 结果回写共用——
    全部确认路径同一落账口径。
    """
    new_price = float(payload["new_price"])
    await session.execute(
        text("UPDATE app.listing SET current_price = :p, pending_price = NULL WHERE id = :id"),
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


async def clear_pending_price(session: AsyncSession, listing_ids: list[int]) -> None:
    """终态失败复位（BR-LC-011 失败半程）：只清在途标记，current_price/价史不动。"""
    if not listing_ids:
        return
    await session.execute(
        text("UPDATE app.listing SET pending_price = NULL WHERE id = ANY(:ids)"),
        {"ids": listing_ids},
    )


async def _lock_listing_for_push(
    session: AsyncSession, listing_id: int, team_id: int
) -> dict[str, Any]:
    """tx1 行锁 + 推价准入校验（PUT 单品与 feed 聚合两条派发路径共用一套口径）。"""
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, store_id, offer_mode, channel_sku, status,"
                    " is_locked, current_price, pending_price"
                    " FROM app.listing WHERE id = :id FOR UPDATE"
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
    if listing["pending_price"] is not None:
        raise BusinessError(
            "PRICING_PUSH_IN_FLIGHT",
            f"该 listing 有改价在途（目标 {float(listing['pending_price'])}），"
            "待渠道终态确认后再推",
            detail={"pending_price": float(listing["pending_price"])},
            http_status=409,
        )
    return listing


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
    - 同 listing 改价在途（pending_price 非空——上一命令/feed 未终局）→ 409 拒绝：
      FIFO 车道只串行化命令执行、不拦 enqueue，若放行会以陈旧 old_price 落命令
      （价史链断裂 + 30% 阈值基数错误 + 用户回摆被吞，评审发现 9）；
    - reason='manual'：策略 min_price 守护先行（guard_manual_price fail-closed）；
    - 30% 确认阈值对 live 同样先行（BR-PR-008，force 透传）；
    - 价差 < $0.01 直接跳过不 enqueue（BR-PR-006 省配额）；
    - 幂等键带轮次（episode = 该 listing 既有 price_push 命令数，retire 房例同款）：
      失败后同价重推开新命令、价格回摆 A→B→A 不撞历史键（评审发现 1/7/17）；
    - 传输失败/结果未知 → 命令 verify_pending 等 price_recon 对账（BR-GW-005
      永不盲重试）；限流闸拒绝/渠道 429 → 命令留 pending 由 beat 兜底继续推。
    """
    if new_price <= 0:
        raise BusinessError("LISTING_PRICE_INVALID", "价格必须大于 0")
    price = round(float(new_price), 2)
    async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as session:
        listing = await _lock_listing_for_push(session, listing_id, team_id)
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
                        "人工价低于策略 min_price 底线（拒绝）",
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
        # 轮次维度（retire:{id}:{episode} 房例）：同 listing 第 N 次派发占第 N 个键——
        # 渠道拒/对账判败后同价重推开新命令，不复用终态旧命令（否则永久黑洞）；
        # in-flight 守卫保证计数时既有命令均已终局，无并发跳号。
        episode = (
            await session.execute(
                text(
                    "SELECT count(*) FROM app.channel_command"
                    " WHERE action = 'price_push' AND object_type = 'listing'"
                    "   AND object_id = :l"
                ),
                {"l": listing_id},
            )
        ).scalar_one()
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
            idempotency_key=f"price:{listing_id}:{price}:{episode}",
            object_type="listing",
            object_id=listing_id,
            created_by=actor_id,
        )
        # 派发半程（BR-LC-011 两段式）：在途标记随命令同事务落库；
        # 渠道终态成功 → apply_price_backfill 回填并清；失败 → clear_pending_price 复位
        await session.execute(
            text("UPDATE app.listing SET pending_price = :p WHERE id = :id"),
            {"p": price, "id": listing_id},
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
        await clear_pending_price(session, [listing_id])  # 零发包，无在途
        return {"status": "succeeded", "dry_run": True, "request_snapshot": resp.request_snapshot}

    if resp is None or resp.status is None:
        # 结果未知：不回填价格、不重发（BR-GW-005）——等 price_recon 拉渠道实价对账
        # （pending_price 保留：改价确实在途）
        if not await outbox.complete(session, command_id=cid, fence=fence, status="verify_pending"):
            return {"command_status": "superseded"}
        return {"status": "verify_pending"}

    if resp.status == 429:  # noqa: PLR2004
        # 渠道限流拒收 = 确定未受理（暂态，非业务拒）——与闸前拒绝同类处置：
        # 归还 pending 由 beat 在配额恢复后继续推，绝不判终局 failed（评审发现 5）。
        # 进程内 GCRA 在重启/多 worker 下会丢状态，真实 429 可达。
        if not await outbox.release_claim(session, command_id=cid, fence=fence):
            return {"command_status": "superseded"}
        log.info("pricing.push_429_released", command_id=cid, listing_id=listing_id)
        return {"status": "pending", "error_code": "HTTP_429"}

    if resp.status == 200:  # noqa: PLR2004
        data = resp.data if isinstance(resp.data, dict) else {}
        inner = data.get("ItemPriceResponse")
        if not isinstance(inner, dict):
            inner = data
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

    # 渠道明确拒（4xx 业务拒等）：命令 failed + 告警，**不回填价格**、清在途标记
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
    await clear_pending_price(session, [listing_id])
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


# ── 价格同步管道（R2-06 增量3：feed 聚合通道，D-Q62 路由 >阈值 走 PRICE_AND_PROMOTION）──


async def price_feed_header(session: AsyncSession) -> dict[str, Any]:
    """PRICE_AND_PROMOTION feed header（口径2）：默认常量 + system_config 逐字段覆盖。"""
    cfg = dict(_PRICE_FEED_HEADER_DEFAULTS)
    row = (
        await session.execute(
            text("SELECT value FROM app.system_config WHERE key = 'pricing.price_feed_header'")
        )
    ).scalar_one_or_none()
    if isinstance(row, dict):
        cfg.update({k: row[k] for k in _PRICE_FEED_HEADER_DEFAULTS if k in row})
    return {
        "businessUnit": cfg["business_unit"],
        "locale": cfg["locale"],
        "version": cfg["version"],
    }


def build_price_feed_envelope(
    header: dict[str, Any], items: list[tuple[str, float]]
) -> dict[str, Any]:
    """PRICE_AND_PROMOTION canonical envelope（考古口径2，旧仓 A 版生产验证）。

    ⚠️ 同 build_put_price_body：只产常规价，促销字段（promoId/effectiveDate/
    expirationDate/processMode 等）硬性禁止出现。
    """
    return {
        "MPItemFeedHeader": header,
        "MPItem": [
            {"Promo&Discount": {"sku": str(sku), "price": round(float(price), 2)}}
            for sku, price in items
        ],
    }


async def push_price_feed(
    sessions: async_sessionmaker[AsyncSession],
    *,
    team_id: int,
    store_id: int,
    items: list[dict[str, Any]],
    applier: outbox.Applier,
    force: bool = False,
    actor_id: int | None = None,
    is_super: bool = False,
) -> dict[str, Any]:
    """单店批量改价的 feed 聚合通道（D-Q62：条数 > put_route_threshold 时走此路）。

    items: [{listing_id, new_price, reason, strategy, detail}]。三段式同 push_price：
    tx1 逐条准入校验（与 PUT 路完全同口径）+ 组 feed（feed_kind='price'）+ 落
    feed_submit 命令 + 写 pending_price 在途标记 → HTTP → tx2 归位
    （applier=listing._apply_feed_submit，其按 feed_kind 分派回本模块）。
    渠道 item 级 SUCCESS 才回填（feed_poll price 分支，BR-LC-011 两段式）。
    """
    pushed: list[int] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as session:
        threshold = await confirm_threshold(session, team_id)
        eligible: list[dict[str, Any]] = []
        for item in items:
            lid = int(item["listing_id"])
            price = round(float(item["new_price"]), 2)
            try:
                listing = await _lock_listing_for_push(session, lid, team_id)
            except BusinessError as exc:
                failed.append({"listing_id": lid, "code": exc.code})
                continue
            if listing["store_id"] != store_id:
                failed.append({"listing_id": lid, "code": "PRICING_FEED_MIXED_STORE"})
                continue
            old_price = (
                float(listing["current_price"]) if listing["current_price"] is not None else None
            )
            if (
                old_price is not None
                and engine.exceeds_confirm_threshold(old_price, price, pct=threshold)
                and not force
            ):
                failed.append({"listing_id": lid, "code": "PRICING_CONFIRM_REQUIRED"})
                continue
            if not engine.price_changed(old_price, price):
                skipped.append({"listing_id": lid, "reason": "unchanged"})
                continue
            strategy = item.get("strategy")
            eligible.append(
                {
                    "listing_id": lid,
                    "channel_sku": listing["channel_sku"],
                    "old_price": old_price,
                    "new_price": price,
                    "reason": str(item.get("reason", "strategy")),
                    "strategy_id": strategy["id"] if strategy else None,
                    "strategy_version": strategy["version"] if strategy else None,
                    "detail": dict(item.get("detail") or {}),
                }
            )
        if not eligible:
            return {"feed_id": None, "pushed": pushed, "skipped": skipped, "failed": failed}

        await gateway.prepare(session, store_id)  # 模式闸预检（拒绝 → tx1 整体回滚）
        feed_id = (
            await session.execute(
                text(
                    "INSERT INTO app.feed (team_id, store_id, feed_kind, item_count,"
                    " status, created_by)"
                    " VALUES (:t, :s, 'price', :n, 'submitting', :u) RETURNING id"
                ),
                {"t": team_id, "s": store_id, "n": len(eligible), "u": actor_id},
            )
        ).scalar_one()
        for entry in eligible:
            await session.execute(
                text(
                    "INSERT INTO app.feed_item (feed_id, team_id, listing_id, channel_sku)"
                    " VALUES (:f, :t, :l, :sku)"
                ),
                {"f": feed_id, "t": team_id, "l": entry["listing_id"], "sku": entry["channel_sku"]},
            )
        header = await price_feed_header(session)
        envelope = build_price_feed_envelope(
            header, [(e["channel_sku"], e["new_price"]) for e in eligible]
        )
        enq = await outbox.enqueue(
            session,
            team_id=team_id,
            store_id=store_id,
            action="feed_submit",
            payload={
                "method": "POST",
                "path": "/v3/feeds",
                "endpoint_key": f"POST /v3/feeds:{PRICE_FEED_TYPE}",
                "gate_max_wait": _GATE_MAX_WAIT_S,
                "params": {"feedType": PRICE_FEED_TYPE},
                "json_body": envelope,
                # item 级回填数据（feed_poll price 分支按 sku 取用）
                "items": {e["channel_sku"]: e for e in eligible},
            },
            idempotency_key=f"feed:{feed_id}",
            object_type="feed",
            object_id=feed_id,
            created_by=actor_id,
        )
        # 派发半程（BR-LC-011）：feed 内每个 listing 记在途标记
        for entry in eligible:
            await session.execute(
                text("UPDATE app.listing SET pending_price = :p WHERE id = :id"),
                {"p": entry["new_price"], "id": entry["listing_id"]},
            )
        pushed = [e["listing_id"] for e in eligible]
    outcome = await outbox.execute_command(
        sessions, enq["command_id"], team_id=team_id, is_super=is_super, applier=applier
    )
    return {
        "feed_id": feed_id,
        "command_id": enq["command_id"],
        "pushed": pushed,
        "skipped": skipped,
        "failed": failed,
        **outcome,
    }


async def _feed_listing_ids(session: AsyncSession, feed_id: int) -> list[int]:
    rows = await session.execute(
        text("SELECT listing_id FROM app.feed_item WHERE feed_id = :f"), {"f": feed_id}
    )
    return [int(r[0]) for r in rows]


async def apply_price_feed_submit(  # noqa: PLR0911 归位分支=渠道响应形态（dry_run/未知/成功/明确拒）
    session: AsyncSession, cmd: dict[str, Any], resp: GatewayResponse | None
) -> dict[str, Any]:
    """price feed 提交的 tx2 归位（listing._apply_feed_submit 按 feed_kind 分派至此）。

    与 item feed 归位的差异：明确拒不涉及 listing_create 配额/GTIN/状态机——
    只清 pending_price 复位 + 告警；成功仅记 submitted（回填等 item 级结果）。
    """
    feed_id = int(cmd["object_id"])
    cid, fence = int(cmd["id"]), int(cmd["fence"])

    if resp is not None and resp.dry_run:
        if not await outbox.complete(
            session, command_id=cid, fence=fence, status="succeeded", result={"dry_run": True}
        ):
            return {"command_status": "superseded"}
        await session.execute(
            text(
                "UPDATE app.feed SET status = 'building',"
                " headline = cast(:h AS jsonb) WHERE id = :f"
            ),
            {"f": feed_id, "h": json.dumps({"dry_run": True}, ensure_ascii=False)},
        )
        await clear_pending_price(session, await _feed_listing_ids(session, feed_id))
        return {
            "feed_status": "building",
            "dry_run": True,
            "request_snapshot": resp.request_snapshot,
        }

    if resp is None or resp.status is None:
        # 提交无响应：结果未知 → verify_pending，等 feed verify-back 对账（永不盲重试）
        if not await outbox.complete(session, command_id=cid, fence=fence, status="verify_pending"):
            return {"command_status": "superseded"}
        await session.execute(
            text(
                "UPDATE app.feed SET status = 'verify_pending', submitted_at = now() WHERE id = :f"
            ),
            {"f": feed_id},
        )
        return {"status": "verify_pending", "feed_status": "verify_pending"}

    if resp.status == 429:  # noqa: PLR2004 渠道限流=确定未受理，归还 pending 等 beat（同 PUT 路）
        if not await outbox.release_claim(session, command_id=cid, fence=fence):
            return {"command_status": "superseded"}
        await session.execute(
            text("UPDATE app.feed SET status = 'building' WHERE id = :f"), {"f": feed_id}
        )
        return {"status": "pending", "error_code": "HTTP_429"}

    channel_feed_id = (resp.data or {}).get("feedId")
    if resp.status == 200 and channel_feed_id:  # noqa: PLR2004
        if not await outbox.complete(
            session,
            command_id=cid,
            fence=fence,
            status="succeeded",
            result={"channel_feed_id": channel_feed_id},
        ):
            return {"command_status": "superseded"}
        await session.execute(
            text(
                "UPDATE app.feed SET status = 'submitted', channel_feed_id = :cf,"
                " submitted_at = now() WHERE id = :f"
            ),
            {"cf": channel_feed_id, "f": feed_id},
        )
        return {"status": "succeeded", "feed_status": "submitted",
                "channel_feed_id": channel_feed_id}  # fmt: skip

    # 渠道明确拒：feed error + 命令 failed + 清在途标记 + 告警（价格不回填）
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
    await session.execute(
        text("UPDATE app.feed SET status = 'error', headline = cast(:h AS jsonb) WHERE id = :f"),
        {"f": feed_id, "h": json.dumps({"status": resp.status}, ensure_ascii=False)},
    )
    await clear_pending_price(session, await _feed_listing_ids(session, feed_id))
    await notify(
        session,
        team_id=int(cmd["team_id"]),
        severity="warn",
        category="pricing",
        title=f"价格 Feed #{feed_id} 提交被渠道拒绝",
        body=f"POST /v3/feeds({PRICE_FEED_TYPE}) HTTP {resp.status}（{error_code}）；"
        "价格未回填，核对后在定价页重推",
        object_type="feed",
        object_id=str(feed_id),
        dedupe_key=f"price_feed:{feed_id}",
    )
    return {"status": "failed", "error_code": error_code, "http_status": resp.status}


async def apply_price_feed_results(
    session: AsyncSession, feed: dict[str, Any], data: dict[str, Any]
) -> dict[str, Any]:
    """price feed 的 item 级权威回写（feed_poll 终态分支按 feed_kind 分派至此）。

    SUCCESS → 两段式回填（apply_price_backfill，含清 pending_price）；
    error → feed_item error + 清 pending_price 复位（价格不回填）。headline 不可信，
    一律以 item 级为准（总账铁律）。
    """
    feed_id = int(feed["id"])
    payload_items: dict[str, Any] = {}
    cmd_row = (
        await session.execute(
            text(
                "SELECT payload FROM app.channel_command"
                " WHERE action = 'feed_submit' AND object_type = 'feed' AND object_id = :f"
                " ORDER BY id DESC LIMIT 1"
            ),
            {"f": feed_id},
        )
    ).first()
    if cmd_row is not None and isinstance(cmd_row[0], dict):
        payload_items = cmd_row[0].get("items") or {}

    ok = err = 0
    for item in data.get("itemDetails", {}).get("itemIngestionStatus", []):
        sku = str(item.get("sku") or "")
        entry = payload_items.get(sku)
        if entry is None:
            continue
        lid = int(entry["listing_id"])
        if str(item.get("ingestionStatus")) == "SUCCESS":
            ok += 1
            await session.execute(
                text(
                    "UPDATE app.feed_item SET status = 'success', raw = cast(:r AS jsonb)"
                    " WHERE feed_id = :f AND channel_sku = :sku"
                ),
                {"f": feed_id, "sku": sku, "r": json.dumps(item, ensure_ascii=False)},
            )
            await apply_price_backfill(
                session,
                listing_id=lid,
                team_id=int(feed["team_id"]),
                payload=entry,
                detail_extra={"via": "price_feed", "feed_id": feed_id},
            )
        else:
            err += 1
            errors = item.get("ingestionErrors", {}).get("ingestionError") or [{}]
            code = str(errors[0].get("code") or "WM_UNKNOWN")
            await session.execute(
                text(
                    "UPDATE app.feed_item SET status = 'error', error_code = :c,"
                    " error_msg = :m, raw = cast(:r AS jsonb)"
                    " WHERE feed_id = :f AND channel_sku = :sku"
                ),
                {
                    "c": code,
                    "m": str(errors[0].get("description") or "")[:500],
                    "r": json.dumps(item, ensure_ascii=False),
                    "f": feed_id,
                    "sku": sku,
                },
            )
            await clear_pending_price(session, [lid])
    final = "processed" if err == 0 else ("partial" if ok else "error")
    await session.execute(
        text("UPDATE app.feed SET status = :s, completed_at = now() WHERE id = :f"),
        {"s": final, "f": feed_id},
    )
    if err:
        await notify(
            session,
            team_id=int(feed["team_id"]),
            severity="critical" if final == "error" else "warn",
            category="pricing",
            title=f"价格 Feed #{feed_id} {'全部失败' if final == 'error' else '部分失败'}",
            body=f"成功 {ok} / 失败 {err}；失败项价格未回填（feed 明细页可查），核对后重推",
            object_type="feed",
            object_id=str(feed_id),
            dedupe_key=f"price_feed_result:{feed_id}",
        )
    return {"feed_id": feed_id, "feed_status": final, "success": ok, "error": err}


async def apply_price_feed_lost(session: AsyncSession, feed: dict[str, Any]) -> dict[str, Any]:
    """price feed 对账判 lost（verify-back 渠道未收到）：清在途标记复位 + 告警。

    与 item feed lost 的差异：不涉及 listing_create 配额与状态机——价格不回填即复位。
    """
    feed_id = int(feed["id"])
    ids = await _feed_listing_ids(session, feed_id)
    await clear_pending_price(session, ids)
    await notify(
        session,
        team_id=int(feed["team_id"]),
        severity="warn",
        category="pricing",
        title=f"价格 Feed #{feed_id} 渠道未收到（对账 lost）",
        body="提交未达渠道，价格未回填；核对后在定价页重推",
        object_type="feed",
        object_id=str(feed_id),
        dedupe_key=f"price_feed:{feed_id}",
    )
    return {"feed_id": feed_id, "feed_status": "lost", "reset": len(ids)}


# outbox 命令 → tx2 归位函数（drain 工具按 action 索取，与 listing.APPLIERS 同构；
# price feed 走 action='feed_submit'，由 listing._apply_feed_submit 按 feed_kind 分派）
APPLIERS: dict[str, outbox.Applier] = {"price_push": _apply_price_push}
