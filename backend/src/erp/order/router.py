"""订单域 API（R2-05 增量2；契约 002 §Order——列表/详情/四检重跑/人工放行）。

发货 /orders/{id}/ship 随增量4（outbox + 幂等）；采购执行单端点随增量3。
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.audit import AuditWriter
from erp.core.authn import CurrentUser, require_permission
from erp.core.db import get_session
from erp.core.errors import BusinessError
from erp.identity.schemas import Page
from erp.order import checks as order_checks

order_router = APIRouter(tags=["order"])


async def _load_order(session: AsyncSession, order_id: int, team_id: int) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, store_id, channel_order_no, order_date,"
                    " channel_status, internal_status, customer, ship_to, order_total,"
                    " currency, item_count, has_flag"
                    " FROM app.channel_order WHERE id = :o"
                ),
                {"o": order_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["team_id"] != team_id:
        raise BusinessError("ORDER_NOT_FOUND", "订单不存在")
    return dict(row)


@order_router.get("/orders")
async def list_orders(
    user: Annotated[CurrentUser, Depends(require_permission("order.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    store_id: int | None = Query(default=None),
    internal_status: str | None = Query(default=None),
    has_flag: bool | None = Query(default=None),
    order_after: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
) -> Page[dict[str, Any]]:
    where = "WHERE team_id = :team"
    params: dict[str, Any] = {"team": user.team_id}
    if store_id is not None:
        where += " AND store_id = :sid"
        params["sid"] = store_id
    if internal_status:
        where += " AND internal_status = :ist"
        params["ist"] = internal_status
    if has_flag is not None:
        where += " AND has_flag = :hf"
        params["hf"] = has_flag
    if order_after is not None:
        where += " AND order_date > :after"
        params["after"] = order_after
    total = (
        await session.execute(text(f"SELECT count(*) FROM app.channel_order {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                "SELECT id, store_id, channel_order_no, order_date, channel_status,"
                " internal_status, order_total, currency, item_count, has_flag"
                f" FROM app.channel_order {where}"
                " ORDER BY order_date DESC, id DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    return Page(items=[dict(r) for r in rows], total=total, page=page, size=size)


@order_router.get("/orders/{order_id}")
async def order_detail(
    order_id: int,
    user: Annotated[CurrentUser, Depends(require_permission("order.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    order = await _load_order(session, order_id, user.team_id or -1)
    lines = [
        dict(r)
        for r in (
            await session.execute(
                text(
                    "SELECT channel_line_no, channel_sku, listing_id, product_id, qty,"
                    " unit_price, line_status, carrier, tracking_no, shipped_at"
                    " FROM app.order_line WHERE order_id = :o AND order_date = :d"
                    " ORDER BY channel_line_no"
                ),
                {"o": order_id, "d": order["order_date"]},
            )
        ).mappings()
    ]
    checks = [
        dict(r)
        for r in (
            await session.execute(
                text(
                    "SELECT check_kind, result, detail, resolved_by, resolved_at, checked_at"
                    " FROM app.order_check WHERE order_id = :o AND order_date = :d"
                    " ORDER BY check_kind"
                ),
                {"o": order_id, "d": order["order_date"]},
            )
        ).mappings()
    ]
    procurement = (
        (
            await session.execute(
                text(
                    "SELECT id, status, assignee_kind, purchaser_id, purchase_cost,"
                    " purchase_currency, exchange_rate_locked, carrier, tracking_no"
                    " FROM app.procurement_order WHERE order_id = :o"
                    " ORDER BY id DESC LIMIT 1"
                ),
                {"o": order_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return {
        **order,
        "lines": lines,
        "checks": checks,
        "procurement": dict(procurement) if procurement else None,
    }


@order_router.post("/orders/{order_id}/checks/rerun")
async def rerun_checks(
    order_id: int,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("order.check"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """重跑四检（consistency 复用缓存渠道品名；phishing flagged 未放行不可覆盖）。"""
    order = await _load_order(session, order_id, user.team_id or -1)
    results = await order_checks.run_order_checks(
        session,
        order_id=order_id,
        order_date=order["order_date"],
        team_id=order["team_id"],
        channel_titles=None,
    )
    await AuditWriter.for_user(session, user, request).log(
        "order.checks_rerun", "channel_order", order_id, after={"results": results}
    )
    return {"order_id": order_id, "results": results}


@order_router.post("/orders/{order_id}/checks/{check_kind}/resolve")
async def resolve_check(
    order_id: int,
    check_kind: str,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("order.check"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """人工放行 flagged 检查项（含 phishing——BR-ORD-006 复审须人工清列的唯一通道）。"""
    if check_kind not in order_checks.CHECK_KINDS:
        raise BusinessError("CHECK_KIND_UNKNOWN", f"未知检查项：{check_kind}")
    order = await _load_order(session, order_id, user.team_id or -1)
    resolved = (
        await session.execute(
            text(
                "UPDATE app.order_check SET resolved_by = :u, resolved_at = now()"
                " WHERE order_id = :o AND order_date = :d AND check_kind = :k"
                "   AND result = 'flagged' AND resolved_at IS NULL RETURNING id"
            ),
            {"u": user.id, "o": order_id, "d": order["order_date"], "k": check_kind},
        )
    ).first()
    if resolved is None:
        raise BusinessError("CHECK_NOT_FLAGGED", "该检查项不在待放行状态")
    # 重算快捷标记：仍有未放行 flagged 才保持 has_flag
    await session.execute(
        text(
            "UPDATE app.channel_order SET has_flag = EXISTS ("
            " SELECT 1 FROM app.order_check WHERE order_id = :o AND order_date = :d"
            "   AND result = 'flagged' AND resolved_at IS NULL)"
            " WHERE id = :o AND order_date = :d"
        ),
        {"o": order_id, "d": order["order_date"]},
    )
    await AuditWriter.for_user(session, user, request).log(
        "order.check_resolve", "channel_order", order_id, after={"check_kind": check_kind}
    )
    return {"order_id": order_id, "check_kind": check_kind, "resolved": True}
