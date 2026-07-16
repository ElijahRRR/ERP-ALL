"""定价策略注册表 CRUD（R2-06 增量1；契约 002 /pricing-strategies 三端点，D-Q23）。

- (team_id, COALESCE(store_id,0), offer_mode) 活跃唯一（0027 部分唯一索引），
  撞唯一 → 409 PRICING_STRATEGY_CONFLICT；
- params 必含 min_price 硬底线（001/06:173，fail-closed：缺失/非正数拒绝入库）；
  cost_plus 另需非空 bands（区间数组，倍数由 engine.parse_multiplier 防御解析）；
- params 每改 version +1（001/06:175）——计算明细回溯用（price_history.strategy_version）。
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.audit import AuditWriter
from erp.core.authn import CurrentUser, require_permission
from erp.core.db import get_session
from erp.core.errors import BusinessError
from erp.pricing import service

pricing_router = APIRouter(tags=["Listing"])

_ALGO_CODES = ("cost_plus", "manual")


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


def _validate_params(algo_code: str, params: dict[str, Any]) -> None:
    min_price = params.get("min_price")
    if isinstance(min_price, bool) or not isinstance(min_price, (int, float)) or min_price <= 0:
        raise BusinessError(
            "PRICING_MIN_PRICE_REQUIRED",
            "params.min_price 硬底线必填且必须 > 0（001/06 图纸；低于底线不出价）",
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
