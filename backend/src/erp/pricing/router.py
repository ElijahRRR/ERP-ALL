"""定价策略注册表 CRUD（R2-06 增量1；契约 002 /pricing-strategies 三端点，D-Q23）。

- (team_id, COALESCE(store_id,0), offer_mode) 活跃唯一（0027 部分唯一索引），
  撞唯一 → 409 PRICING_STRATEGY_CONFLICT；
- params.min_price 底线可选（D-Q62 补充裁定）：填了必须 > 0，缺省不设防；
  cost_plus 另需非空 bands（区间数组，倍数由 engine.parse_multiplier 防御解析）；
- params 每改 version +1（001/06:175）——计算明细回溯用（price_history.strategy_version）。
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.audit import AuditWriter
from erp.core.authn import CurrentUser, require_permission
from erp.core.db import ctx_tx, get_session, get_session_factory
from erp.core.errors import BusinessError
from erp.core.idempotency import run_idempotent
from erp.listing.service import APPLIERS as _LISTING_APPLIERS
from erp.pricing import engine, service

pricing_router = APIRouter(tags=["Listing"])

_ALGO_CODES = ("cost_plus", "manual")

# price feed 走 action='feed_submit'（tx2 归位在 listing._apply_feed_submit，
# 其按 feed_kind='price' 分派回 pricing.apply_price_feed_submit）
_FEED_SUBMIT_APPLIER = _LISTING_APPLIERS["feed_submit"]

# reprice 同步批量最坏时长（单店 ≤5 条 PUT × (闸等待 10s + 超时 30s) + feed 提交，
# 跨店累加）可远超 run_idempotent 默认 10 分钟失效占位阈值——同键重试会重占占位
# 并发双跑（评审发现 12）。显式给出大于最坏执行时长的覆盖值。
_REPRICE_STALE_MINUTES = 180


class PricingStrategyIn(BaseModel):
    store_id: int | None = None
    offer_mode: str | None = None
    name: str | None = None
    algo_code: str | None = None
    params: dict[str, Any] | None = None
    status: str | None = None


class PricingPreviewIn(BaseModel):
    store_id: int
    offer_mode: str = Field(pattern="^(build|match)$")
    product_ids: list[int] | None = Field(default=None, max_length=200)
    listing_ids: list[int] | None = Field(default=None, max_length=200)


class RepriceIn(BaseModel):
    listing_ids: list[int] = Field(max_length=200)
    force: bool = False  # BR-PR-008：变动超阈（默认 30%）需 force 二次确认


# Idempotency-Key（契约 002 必填头）：同键同载荷重放存储响应，异载荷 409
IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)]


def _validate_params(algo_code: str, params: dict[str, Any]) -> None:
    min_price = params.get("min_price")
    if min_price is not None and (
        isinstance(min_price, bool) or not isinstance(min_price, (int, float)) or min_price <= 0
    ):
        raise BusinessError(
            "PRICING_MIN_PRICE_INVALID",
            "params.min_price 若填必须为 > 0 的数字（可选底线，D-Q62 补充；低于底线不出价）",
        )
    if algo_code == "cost_plus":
        bands = params.get("bands")
        if not isinstance(bands, dict) or not bands:
            raise BusinessError(
                "PRICING_BANDS_REQUIRED",
                'cost_plus 需 params.bands 区间表（如 {"FBA": [[0,30,2.75]], ...}）',
            )


def _require_team(user: CurrentUser) -> int:
    if user.team_id is None:
        raise BusinessError("PRICING_TEAM_REQUIRED", "超管需切换到具体团队")
    return user.team_id


@pricing_router.get("/pricing-strategies")
async def list_strategies(
    user: Annotated[CurrentUser, Depends(require_permission("pricing.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT id, store_id, offer_mode, name, algo_code, params, status, version"
                " FROM app.pricing_strategy ORDER BY offer_mode, COALESCE(store_id, 0), id"
            )
        )
    ).mappings()
    return [dict(r) for r in rows]


@pricing_router.post("/pricing-strategies", status_code=201)
async def create_strategy(
    body: PricingStrategyIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("pricing.write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    team_id = _require_team(user)
    if body.offer_mode not in ("build", "match"):
        raise BusinessError("PRICING_OFFER_MODE_INVALID", "offer_mode 必须为 build/match")
    if not body.name:
        raise BusinessError("PRICING_NAME_REQUIRED", "name 必填")
    if body.algo_code not in _ALGO_CODES:
        raise BusinessError(
            "PRICING_ALGO_INVALID",
            f"algo_code 必须为 {'/'.join(_ALGO_CODES)}（follow_buybox 未实现）",
        )
    params = body.params or {}
    _validate_params(body.algo_code, params)
    try:
        sid = (
            await session.execute(
                text(
                    "INSERT INTO app.pricing_strategy"
                    " (team_id, store_id, offer_mode, name, algo_code, params, status, created_by)"
                    " VALUES (:t, :s, :m, :n, :a, cast(:p AS jsonb), :st, :by) RETURNING id"
                ),
                {
                    "t": team_id,
                    "s": body.store_id,
                    "m": body.offer_mode,
                    "n": body.name,
                    "a": body.algo_code,
                    "p": json.dumps(params, ensure_ascii=False),
                    "st": body.status or "active",
                    "by": user.id,
                },
            )
        ).scalar_one()
    except IntegrityError as exc:
        raise BusinessError(
            "PRICING_STRATEGY_CONFLICT",
            "同 (团队×店铺×offer_mode) 已有活跃策略（D-Q23 活跃唯一）——先停用旧策略",
            http_status=409,
        ) from exc
    await AuditWriter.for_user(session, user, request).log(
        "pricing.strategy_create", "pricing_strategy", sid,
        after={"name": body.name, "algo_code": body.algo_code},
    )  # fmt: skip
    return {"id": int(sid)}


@pricing_router.patch("/pricing-strategies/{strategy_id}")
async def update_strategy(
    strategy_id: int,
    body: PricingStrategyIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("pricing.write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    team_id = _require_team(user)
    row = (
        (
            await session.execute(
                text(
                    "SELECT team_id, algo_code, params, version FROM app.pricing_strategy"
                    " WHERE id = :id FOR UPDATE"
                ),
                {"id": strategy_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["team_id"] != team_id:
        raise BusinessError("PRICING_STRATEGY_NOT_FOUND", "策略不存在")
    algo_code = body.algo_code or row["algo_code"]
    if algo_code not in _ALGO_CODES:
        raise BusinessError("PRICING_ALGO_INVALID", f"algo_code 必须为 {'/'.join(_ALGO_CODES)}")
    sets: list[str] = []
    params: dict[str, Any] = {"id": strategy_id}
    if body.name is not None:
        sets.append("name = :name")
        params["name"] = body.name
    if body.algo_code is not None:
        sets.append("algo_code = :algo")
        params["algo"] = body.algo_code
    if body.status is not None:
        if body.status not in ("active", "disabled"):
            raise BusinessError("PRICING_STATUS_INVALID", "status 必须为 active/disabled")
        sets.append("status = :status")
        params["status"] = body.status
    if body.params is not None:
        _validate_params(algo_code, body.params)
        sets.append("params = cast(:params AS jsonb)")
        sets.append("version = version + 1")  # 001/06:175 params 每改 +1
        params["params"] = json.dumps(body.params, ensure_ascii=False)
    elif body.algo_code is not None:
        _validate_params(algo_code, dict(row["params"]))  # 改算法需既有 params 兼容新算法
    if not sets:
        return {"id": strategy_id, "version": row["version"]}
    try:
        version = (
            await session.execute(
                text(
                    f"UPDATE app.pricing_strategy SET {', '.join(sets)}"
                    " WHERE id = :id RETURNING version"
                ),
                params,
            )
        ).scalar_one()
    except IntegrityError as exc:
        raise BusinessError(
            "PRICING_STRATEGY_CONFLICT",
            "同 (团队×店铺×offer_mode) 已有活跃策略——先停用旧策略",
            http_status=409,
        ) from exc
    await AuditWriter.for_user(session, user, request).log(
        "pricing.strategy_update", "pricing_strategy", strategy_id,
        after={k: v for k, v in params.items() if k != "id"},
    )  # fmt: skip
    return {"id": strategy_id, "version": int(version)}


def _outcome_status(outcome: dict[str, Any]) -> str:
    """push 结果状态统一提取：applier 返回 'status'，claim 不可用路径返回
    'command_status'（评审发现 8：漏读 command_status 会把终局 failed 误计 pushed）。"""
    return str(outcome.get("status") or outcome.get("command_status") or "unknown")


# 在途/已入队状态（计入 pushed）：succeeded / verify_pending（对账中）/
# pending（限流闸拒或渠道 429 归还，beat 在配额恢复后继续推——已入队必达）
_PUSHED_STATUSES = ("succeeded", "verify_pending", "pending")


async def _reprice_run(
    *, team_id: int, listing_ids: list[int], force: bool, actor_id: int, is_super: bool
) -> dict[str, Any]:
    """批量重定价执行体（run_idempotent handler，自管短事务——不嵌请求事务）。

    逐条对 live/published listing 解析策略算价（cost_plus）→ price_changed 过滤 →
    按 D-Q62/BR-PR-007 路由：单店改价条数 ≤ put_route_threshold（默认 5，配置中心
    可覆盖）逐条走 PUT /v3/price；更多聚合为一个 PRICE_AND_PROMOTION feed
    （10/hour 店铺级共享池——批量 >100 逐条 PUT 必然撞 100/hour 配额堆积 pending）。
    限流语义：闸挡超额的命令留 pending（零字节出门，非盲重试），由 beat
    channel_outbox_drain 在配额恢复后继续推，此类命令计入 pushed（已入队必达）。
    """
    sessions = get_session_factory()
    async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT l.id AS listing_id, l.store_id, l.offer_mode, l.status,"
                    " l.is_locked, l.current_price, l.pending_price, p.attrs, p.price_snapshot"
                    " FROM app.listing l JOIN app.product p ON p.id = l.product_id"
                    " WHERE l.team_id = :t AND l.id = ANY(:ids)"
                ),
                {"t": team_id, "ids": list(dict.fromkeys(listing_ids))},
            )
        ).mappings()
        by_id = {int(r["listing_id"]): dict(r) for r in rows}
        strategies: dict[tuple[int, str], dict[str, Any] | None] = {}
        for item in by_id.values():
            key = (int(item["store_id"]), str(item["offer_mode"]))
            if key not in strategies:
                strategies[key] = await service.resolve_strategy(
                    s, team_id=team_id, store_id=key[0], offer_mode=key[1]
                )
        route_max = await service.put_route_threshold(s, team_id)

    pushed = 0
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    plan: dict[int, list[dict[str, Any]]] = {}  # store_id → 可推清单（保持入参顺序）
    for lid in dict.fromkeys(listing_ids):
        row = by_id.get(lid)
        strategy = strategies.get((int(row["store_id"]), str(row["offer_mode"]))) if row else None
        skip_reason, result = _reprice_gate(row, strategy)
        if skip_reason is not None:
            skipped.append({"listing_id": lid, "reason": skip_reason})
            continue
        assert row is not None and result is not None and result.price is not None
        plan.setdefault(int(row["store_id"]), []).append(
            {
                "listing_id": lid,
                "new_price": result.price,
                "reason": "strategy",
                "strategy": strategy,
                "detail": result.detail,
            }
        )

    for store_id, entries in plan.items():
        if len(entries) <= route_max:
            for entry in entries:
                pushed += await _push_one_put(
                    sessions, entry, team_id=team_id, force=force, actor_id=actor_id,
                    is_super=is_super, skipped=skipped, failed=failed,
                )  # fmt: skip
            continue
        # 聚合 feed 通道（D-Q62：单店 > 阈值）
        try:
            outcome = await service.push_price_feed(
                sessions,
                team_id=team_id,
                store_id=store_id,
                items=entries,
                applier=_FEED_SUBMIT_APPLIER,
                force=force,
                actor_id=actor_id,
                is_super=is_super,
            )
        except BusinessError as exc:
            failed.extend({"listing_id": int(e["listing_id"]), "code": exc.code} for e in entries)
            continue
        skipped.extend(outcome["skipped"])
        failed.extend(outcome["failed"])
        in_feed: list[int] = outcome["pushed"]
        if not in_feed:
            continue
        status = _outcome_status(outcome)
        if status in _PUSHED_STATUSES:
            pushed += len(in_feed)  # 回填等 feed_poll item 级结果（两段式）
        else:
            failed.extend(
                {"listing_id": lid, "code": outcome.get("error_code") or status} for lid in in_feed
            )
    return {"pushed": pushed, "skipped": skipped, "failed": failed}


async def _push_one_put(
    sessions: Any,
    entry: dict[str, Any],
    *,
    team_id: int,
    force: bool,
    actor_id: int,
    is_super: bool,
    skipped: list[dict[str, Any]],
    failed: list[dict[str, Any]],
) -> int:
    """单条 PUT 路推送 → 计入 pushed 的数量（0/1）；skip/fail 就地追加到出参列表。"""
    lid = int(entry["listing_id"])
    try:
        outcome = await service.push_price(
            sessions,
            team_id=team_id,
            listing_id=lid,
            new_price=float(entry["new_price"]),
            reason="strategy",
            strategy=entry["strategy"],
            detail=entry["detail"],
            force=force,
            actor_id=actor_id,
            is_super=is_super,
        )
    except BusinessError as exc:
        failed.append({"listing_id": lid, "code": exc.code})
        return 0
    if outcome.get("skipped"):
        skipped.append({"listing_id": lid, "reason": str(outcome.get("reason"))})
        return 0
    status = _outcome_status(outcome)
    if status in _PUSHED_STATUSES:
        return 1
    failed.append({"listing_id": lid, "code": outcome.get("error_code") or status})
    return 0


def _reprice_gate(  # noqa: PLR0911 预检分支=跳过原因清单（契约 skipped.reason 枚举）
    row: dict[str, Any] | None, strategy: dict[str, Any] | None
) -> tuple[str | None, Any]:
    """单条预检 → (跳过原因, 出价结果)。原因为 None 表示可推（结果必非空）。

    live/published 可推（与 push_price 准入同口径，评审发现 16 对齐）；
    push_in_flight = 改价在途（pending_price 非空——上一命令/feed 未终局）；
    manual 策略跳过（D-Q23 match 现行=人工指定价，不自动重定价）；
    unchanged = BR-PR-006 价差 < $0.01 省配额过滤。
    """
    if row is None:
        return "not_found", None
    if row["status"] not in ("live", "published"):
        return "not_live", None
    if row["is_locked"]:
        return "locked", None
    if row.get("pending_price") is not None:
        return "push_in_flight", None
    if strategy is None:
        return "no_strategy", None
    if strategy["algo_code"] == "manual":
        return "manual", None
    result = service.price_product(strategy, row)
    if not result.ok or result.price is None:
        return result.reason or "rejected", None
    old = float(row["current_price"]) if row["current_price"] is not None else None
    if not engine.price_changed(old, result.price):
        return "unchanged", None
    return None, result


@pricing_router.post("/pricing/reprice", status_code=202)
async def reprice(
    body: RepriceIn,
    request: Request,
    idempotency_key: IdemKey,
    user: Annotated[CurrentUser, Depends(require_permission("pricing.write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """批量重定价（R2-06 增量3）：live listing 按策略算价 → PUT 通道同步推渠道。"""
    team_id = _require_team(user)

    async def _handler() -> dict[str, Any]:
        return await _reprice_run(
            team_id=team_id,
            listing_ids=body.listing_ids,
            force=body.force,
            actor_id=user.id,
            is_super=user.is_super,
        )

    result = await run_idempotent(
        get_session_factory(),
        team_id=team_id,
        is_super=user.is_super,
        endpoint="POST /pricing/reprice",
        idem_key=idempotency_key,
        payload=body.model_dump(),
        created_by=user.id,
        handler=_handler,
        stale_minutes=_REPRICE_STALE_MINUTES,
    )
    await AuditWriter.for_user(session, user, request).log(
        "pricing.reprice", "listing", None,
        after={"pushed": result["pushed"], "skipped": len(result["skipped"]),
               "failed": len(result["failed"])},
    )  # fmt: skip
    return result


@pricing_router.post("/pricing-strategies/preview")
async def preview_strategy(
    body: PricingPreviewIn,
    user: Annotated[CurrentUser, Depends(require_permission("pricing.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """重定价预览（R2-06 增量2；只读试算不落库）。

    clamp 版出价展示区间外情况（detail 带 out_of_band/clamp 标记）+ compute_price
    严格判定 ok/reason 并列返回；manual 策略 → reason='manual' 不试算。
    """
    team_id = _require_team(user)
    if (body.product_ids is None) == (body.listing_ids is None):
        raise BusinessError(
            "PRICING_PREVIEW_INPUT_INVALID", "product_ids 与 listing_ids 必须二选一"
        )
    store_ok = (
        await session.execute(
            text("SELECT 1 FROM app.store WHERE id = :s AND team_id = :t"),
            {"s": body.store_id, "t": team_id},
        )
    ).scalar_one_or_none()
    if store_ok is None:
        raise BusinessError("STORE_NOT_FOUND", "店铺不存在")
    strategy = await service.resolve_strategy(
        session, team_id=team_id, store_id=body.store_id, offer_mode=body.offer_mode
    )
    if strategy is None:
        raise BusinessError("PRICING_STRATEGY_NOT_FOUND", "该 (店铺×offer_mode) 无活跃定价策略")

    items: list[dict[str, Any]] = []
    if body.product_ids is not None:
        rows = (
            await session.execute(
                text(
                    "SELECT id, attrs, price_snapshot FROM app.product"
                    " WHERE team_id = :t AND id = ANY(:ids)"
                ),
                {"t": team_id, "ids": list(dict.fromkeys(body.product_ids))},
            )
        ).mappings()
        by_id = {int(r["id"]): dict(r) for r in rows}
        for pid in dict.fromkeys(body.product_ids):
            product = by_id.get(pid)
            if product is None:
                items.append(
                    {"product_id": pid, "ok": False, "reason": "not_found",
                     "new_price": None, "detail": {}}
                )  # fmt: skip
                continue
            items.append({"product_id": pid, **service.preview_product(strategy, product)})
    else:
        rows = (
            await session.execute(
                text(
                    "SELECT l.id AS listing_id, l.product_id, l.current_price,"
                    " p.attrs, p.price_snapshot"
                    " FROM app.listing l JOIN app.product p ON p.id = l.product_id"
                    " WHERE l.team_id = :t AND l.id = ANY(:ids)"
                ),
                {"t": team_id, "ids": list(dict.fromkeys(body.listing_ids or []))},
            )
        ).mappings()
        by_lid = {int(r["listing_id"]): dict(r) for r in rows}
        for lid in dict.fromkeys(body.listing_ids or []):
            row = by_lid.get(lid)
            if row is None:
                items.append(
                    {"listing_id": lid, "ok": False, "reason": "not_found",
                     "new_price": None, "detail": {}}
                )  # fmt: skip
                continue
            old = float(row["current_price"]) if row["current_price"] is not None else None
            items.append(
                {
                    "product_id": int(row["product_id"]),
                    "listing_id": lid,
                    "old_price": old,
                    **service.preview_product(strategy, row),
                }
            )
    return {
        "strategy": {
            "id": strategy["id"],
            "name": strategy["name"],
            "algo_code": strategy["algo_code"],
            "version": strategy["version"],
        },
        "items": items,
    }
