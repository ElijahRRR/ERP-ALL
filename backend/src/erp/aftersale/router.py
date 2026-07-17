"""售后域 API（R2-07；契约 002 §Aftersale）。

增量1：退货查询只读面（/returns）；增量2：退款/取消申请（/refund-requests，
D-Q29 record/approval 两档本地闭环——渠道执行与 auto 档随 R2-09）。前端售后页随后续增量。
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.aftersale import refund as refund_service
from erp.core.audit import AuditWriter
from erp.core.authn import CurrentUser, require_permission
from erp.core.db import ctx_tx, get_session, get_session_factory
from erp.core.errors import BusinessError
from erp.core.idempotency import run_idempotent
from erp.identity.schemas import Page

aftersale_router = APIRouter(tags=["aftersale"])

# 契约 002 §写操作幂等：Idempotency-Key 必填头（与 order/listing 写端点同构）
IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)]


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


# ── 退款/取消申请（增量2；D-Q29 record/approval 两档本地闭环）──


class RefundLineIn(BaseModel):
    line_no: str = Field(min_length=1, max_length=20)
    qty: int = Field(ge=1)
    amount: float = Field(gt=0)


class RefundRequestIn(BaseModel):
    order_id: int
    kind: Literal["cancel", "refund"]
    amount: float = Field(gt=0)
    reason_code: str = Field(min_length=1, max_length=40)
    reason_text: str | None = Field(default=None, max_length=500)
    lines: list[RefundLineIn] = Field(default_factory=list)


@aftersale_router.post("/refund-requests", status_code=201)
async def create_refund_request(
    body: RefundRequestIn,
    request: Request,
    idempotency_key: IdemKey,
    user: Annotated[CurrentUser, Depends(require_permission("refund.request"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """创建退款/取消申请。档位随建快照；auto 档 R2-09 接线前拒绝（fail-closed）。"""
    team_id = user.team_id or -1

    async def _handler() -> dict[str, Any]:
        async with ctx_tx(get_session_factory(), team_id=team_id, is_super=user.is_super) as s:
            return await refund_service.create_request(
                s,
                team_id=team_id,
                order_id=body.order_id,
                kind=body.kind,
                amount=body.amount,
                reason_code=body.reason_code,
                lines=[ln.model_dump() for ln in body.lines],
                reason_text=body.reason_text,
                requested_by=user.id,
            )

    result = await run_idempotent(
        get_session_factory(),
        team_id=team_id,
        is_super=user.is_super,
        endpoint="POST /refund-requests",
        idem_key=idempotency_key,
        payload=body.model_dump(),
        created_by=user.id,
        handler=_handler,
    )
    await AuditWriter.for_user(session, user, request).log(
        "refund.request_create", "refund_request", result["id"],
        after={"order_id": body.order_id, "kind": body.kind, "amount": body.amount,
               "mode_applied": result["mode_applied"]},
    )  # fmt: skip
    return result


@aftersale_router.get("/refund-requests")
async def list_refund_requests(
    user: Annotated[CurrentUser, Depends(require_permission("aftersale.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    order_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
) -> Page[dict[str, Any]]:
    where = "WHERE team_id = :team"
    params: dict[str, Any] = {"team": user.team_id}
    if status:
        where += " AND status = :st"
        params["st"] = status
    if kind:
        where += " AND kind = :k"
        params["k"] = kind
    if order_id is not None:
        where += " AND order_id = :o"
        params["o"] = order_id
    total = (
        await session.execute(text(f"SELECT count(*) FROM app.refund_request {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                "SELECT id, store_id, order_id, kind, lines, amount, currency, reason_code,"
                " reason_text, mode_applied, status, requested_by_kind, requested_by,"
                " approved_by, decided_at, executed_at, created_at"
                f" FROM app.refund_request {where}"
                " ORDER BY id DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    return Page(items=[dict(r) for r in rows], total=total, page=page, size=size)


@aftersale_router.post("/refund-requests/{request_id}/approve")
async def approve_refund_request(
    request_id: int,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("refund.approve"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """审批通过（approval 档）。approved 驻留待 R2-09 执行接线，本端点不触达渠道。"""
    result = await refund_service.decide_request(
        session, request_id=request_id, team_id=user.team_id or -1,
        decision="approved", decided_by=user.id,
    )  # fmt: skip
    await AuditWriter.for_user(session, user, request).log(
        "refund.request_approve", "refund_request", request_id, after=result
    )
    return result


@aftersale_router.post("/refund-requests/{request_id}/reject")
async def reject_refund_request(
    request_id: int,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("refund.approve"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    result = await refund_service.decide_request(
        session, request_id=request_id, team_id=user.team_id or -1,
        decision="rejected", decided_by=user.id,
    )  # fmt: skip
    await AuditWriter.for_user(session, user, request).log(
        "refund.request_reject", "refund_request", request_id, after=result
    )
    return result
