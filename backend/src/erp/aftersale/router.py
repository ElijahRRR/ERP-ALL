"""售后域 API（R2-07 增量1；契约 002 §Aftersale——退货查询只读面）。

退款/取消申请（refund_request，D-Q29 三档）随增量2；前端售后页随增量4。
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.authn import CurrentUser, require_permission
from erp.core.db import get_session
from erp.core.errors import BusinessError
from erp.identity.schemas import Page

aftersale_router = APIRouter(tags=["aftersale"])


@aftersale_router.get("/returns")
async def list_returns(
    user: Annotated[CurrentUser, Depends(require_permission("aftersale.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    store_id: int | None = Query(default=None),
    internal_status: str | None = Query(default=None),
    return_after: datetime | None = Query(default=None),
    q: str | None = Query(default=None, description="RMA / 客户订单号 精确查"),
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
    if return_after is not None:
        where += " AND return_date > :after"
        params["after"] = return_after
    if q:
        where += " AND (channel_return_no = :q OR customer_order_no = :q)"
        params["q"] = q
    total = (
        await session.execute(text(f"SELECT count(*) FROM app.channel_return {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                "SELECT id, store_id, channel_return_no, customer_order_no, order_id,"
                " return_date, return_by_date, channel_status, internal_status,"
                " refund_mode, qty, refund_amount, currency, reason"
                f" FROM app.channel_return {where}"
                " ORDER BY return_date DESC, id DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    return Page(items=[dict(r) for r in rows], total=total, page=page, size=size)


@aftersale_router.get("/returns/{return_id}")
async def return_detail(
    return_id: int,
    user: Annotated[CurrentUser, Depends(require_permission("aftersale.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    header = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, store_id, channel_return_no, customer_order_no,"
                    " order_id, order_date, return_date, return_by_date, reason,"
                    " channel_status, internal_status, refund_mode, qty, refund_amount,"
                    " currency, customer, pulled_at"
                    " FROM app.channel_return WHERE id = :r"
                ),
                {"r": return_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if header is None or header["team_id"] != user.team_id:
        raise BusinessError("RETURN_NOT_FOUND", "退货单不存在")
    lines = [
        dict(r)
        for r in (
            await session.execute(
                text(
                    "SELECT line_no, channel_sku, product_name, condition, qty, refunded_qty,"
                    " unit_price, line_status, refund_status, delivery_status, return_method,"
                    " return_reason, return_description, status_time, purchase_order_no,"
                    " carrier, tracking_no"
                    " FROM app.channel_return_line WHERE return_id = :r ORDER BY line_no"
                ),
                {"r": return_id},
            )
        ).mappings()
    ]
    events = [
        dict(r)
        for r in (
            await session.execute(
                text(
                    "SELECT line_no, changes, observed_at FROM app.channel_return_event"
                    " WHERE return_id = :r ORDER BY observed_at DESC, id DESC LIMIT 50"
                ),
                {"r": return_id},
            )
        ).mappings()
    ]
    return {**dict(header), "lines": lines, "events": events}
