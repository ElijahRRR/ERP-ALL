"""订单域 API（R2-05；契约 002 §Order——订单/四检/采购执行单/采购方/买家账号池）。

发货 /orders/{id}/ship 随增量4（outbox + 幂等）；portal 外部端点随 R2#6。

买家账号池与采购插件实例（R2-13 13b）挂在本 tag 下与 purchaser 并列，服务在
`order/buyer_account.py`；插件自己的机器面（双头部认证、非 JWT）不在这里，见 `erp/plugin/`。
"""

import json
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.audit import AuditWriter
from erp.core.authn import CurrentUser, require_permission
from erp.core.db import get_session, get_session_factory
from erp.core.errors import BusinessError
from erp.core.idempotency import run_idempotent
from erp.identity import delete as principal_delete
from erp.identity.schemas import Page
from erp.order import buyer_account as buyer_account_service
from erp.order import checks as order_checks
from erp.order import procurement
from erp.order import ship as ship_service
from erp.order.buyer_account import BuyerAccountIn, PluginInstanceIssueIn, PluginInstancePatchIn

order_router = APIRouter(tags=["Order"])

# 契约 002 §写操作幂等：Idempotency-Key 必填头（与 listing 三端点同构）
IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)]


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


# ── 采购执行单（D-Q50 双入口①②）与采购方 ──


class ProcurementCreateIn(BaseModel):
    order_id: int
    purchaser_id: int | None = None


class AssignIn(BaseModel):
    purchaser_id: int


class BackfillIn(BaseModel):
    purchase_platform: str | None = None
    purchase_order_ref: str | None = None
    purchase_cost: float | None = None
    purchase_currency: str | None = Field(default=None, max_length=3)
    freight_cost: float | None = None
    carrier: str | None = None
    tracking_no: str | None = None
    note: str | None = None
    # R2-13 0045 增列：人工在订单页也要能补这四项（插件路径的回填不走本端点）
    tax_amount: float | None = None
    payment_card_last4: str | None = Field(default=None, max_length=8)
    delivery_est_date: date | None = None
    delivery_est_raw: str | None = Field(default=None, max_length=200)


class PluginManualBackfillIn(BaseModel):
    """人工补录插件单（Owner 2026-07-31 异常 #16 裁定）。单号必填，金额可选。"""

    purchase_order_ref: str = Field(min_length=1, max_length=64)
    purchase_cost: float | None = Field(default=None, ge=0)
    tax_amount: float | None = Field(default=None, ge=0)
    freight_cost: float | None = Field(default=None, ge=0)
    payment_card_last4: str | None = Field(default=None, pattern=r"^\d{4}$")


class ExceptionIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    # 问题分类（0045 九值词表）。可空 = 不改已有分类（exception_po 的 coalesce 语义）。
    kind: (
        Literal[
            "address", "stock", "product", "delivery", "checkout",
            "price_guard", "channel_cancelled", "order_not_found", "other",
        ]
        | None
    ) = None  # fmt: skip


class PurchaserIn(BaseModel):
    name: str | None = None
    purchaser_kind: str | None = Field(default=None, pattern="^(internal|external)$")
    user_id: int | None = None
    contact: dict[str, Any] | None = None
    exchange_rate: float | None = None
    status: str | None = Field(default=None, pattern="^(active|disabled)$")
    portal_username: str | None = None
    portal_password: str | None = None


@order_router.get("/procurement-orders")
async def list_procurement(
    user: Annotated[CurrentUser, Depends(require_permission("procurement.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    status: str | None = Query(default=None),
    purchaser_id: int | None = Query(default=None),
    mine: bool = Query(default=False, description="内部采购执行入口「我的单」（D-Q50①）"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
) -> Page[dict[str, Any]]:
    where = "WHERE p.team_id = :team"
    params: dict[str, Any] = {"team": user.team_id}
    if status:
        where += " AND p.status = :st"
        params["st"] = status
    if purchaser_id is not None:
        where += " AND p.purchaser_id = :pid"
        params["pid"] = purchaser_id
    if mine:
        where += " AND p.purchaser_id IN (SELECT id FROM app.purchaser WHERE user_id = :uid)"
        params["uid"] = user.id
    total = (
        await session.execute(text(f"SELECT count(*) FROM app.procurement_order p {where}"), params)
    ).scalar_one()
    rows = [
        dict(r)
        for r in (
            await session.execute(
                text(
                    "SELECT p.id, p.order_id, p.store_id, p.status, p.assignee_kind,"
                    " p.purchaser_id, p.purchase_platform, p.purchase_order_ref, p.purchase_cost,"
                    " p.purchase_currency, p.exchange_rate_locked, p.freight_cost, p.carrier,"
                    " p.tracking_no, p.exception_reason, p.created_at,"
                    # R2-13 0045 增列：买家账号（插件路径）+ 成本三分的税额 + 预计送达 + 异常分类
                    " p.buyer_account_id, p.tax_amount, p.payment_card_last4,"
                    " p.delivery_est_date, p.delivery_est_raw, p.exception_kind"
                    f" FROM app.procurement_order p {where}"
                    " ORDER BY p.id DESC LIMIT :lim OFFSET :off"
                ),
                {**params, "lim": size, "off": (page - 1) * size},
            )
        ).mappings()
    ]
    # 采购方解析（§7.1.1(b) 前半）：purchaser_id 是软引用（0047），主体删除后 id 仍在
    # ③级行上——先查主表、查不到再查墓碑显示「XX（已删除）」，与 audit-logs 的操作人
    # 解析同款两段式。不冗余 purchaser_name 列（§7.1.1「顺带三条」明令禁止双真相源）。
    pids = sorted({r["purchaser_id"] for r in rows if r["purchaser_id"] is not None})
    labels: dict[int, str] = {}
    if pids:
        for pr in await session.execute(
            text("SELECT id, name FROM app.purchaser WHERE id = ANY(:ids)"), {"ids": pids}
        ):
            labels[int(pr[0])] = str(pr[1])
        gone = [p for p in pids if p not in labels]
        if gone:
            labels.update(
                await principal_delete.deleted_labels(session, kind="purchaser", ids=gone)
            )
    for r in rows:
        r["purchaser_label"] = labels.get(r["purchaser_id"]) if r["purchaser_id"] else None
    # 买家账号解析（R2-13 13b）：与 purchaser_label 同款「不冗余名字列」的做法。
    # 这里只查主表、**不查墓碑**——buyer_account 尚无删除路径（0043 不授 DELETE），
    # 删除分支落地时（14b 之后）这里要跟着补一段墓碑回落，同 `deleted_labels` 的形状。
    bids = sorted({r["buyer_account_id"] for r in rows if r["buyer_account_id"] is not None})
    ba_labels: dict[int, str] = {}
    if bids:
        for br in await session.execute(
            text("SELECT id, label FROM app.buyer_account WHERE id = ANY(:ids)"), {"ids": bids}
        ):
            ba_labels[int(br[0])] = str(br[1])
    for r in rows:
        bid = r["buyer_account_id"]
        r["buyer_account_label"] = ba_labels.get(bid) if bid else None
    return Page(items=rows, total=total, page=page, size=size)


@order_router.post("/procurement-orders", status_code=201)
async def create_procurement(
    body: ProcurementCreateIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("order.assign"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    out = await procurement.create_po(
        session,
        team_id=user.team_id or -1,
        order_id=body.order_id,
        purchaser_id=body.purchaser_id,
        actor_id=user.id,
    )
    await AuditWriter.for_user(session, user, request).log(
        "procurement.create", "procurement_order", out["id"], after=body.model_dump()
    )
    return out


@order_router.post("/procurement-orders/{po_id}/assign")
async def assign_procurement(
    po_id: int,
    body: AssignIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("order.assign"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    await procurement.assign_po(
        session,
        team_id=user.team_id or -1,
        po_id=po_id,
        purchaser_id=body.purchaser_id,
        actor_id=user.id,
    )
    await AuditWriter.for_user(session, user, request).log(
        "procurement.assign", "procurement_order", po_id, after=body.model_dump()
    )
    return {"id": po_id, "status": "assigned"}


@order_router.post("/procurement-orders/{po_id}/claim")
async def claim_procurement(
    po_id: int,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("procurement.execute"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    await procurement.claim_po(session, team_id=user.team_id or -1, po_id=po_id)
    await AuditWriter.for_user(session, user, request).log(
        "procurement.claim", "procurement_order", po_id
    )
    return {"id": po_id, "status": "claimed"}


@order_router.post("/procurement-orders/{po_id}/backfill")
async def backfill_procurement(
    po_id: int,
    body: BackfillIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("procurement.execute"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    await procurement.backfill_po(
        session,
        team_id=user.team_id or -1,
        po_id=po_id,
        actor_id=user.id,
        fields=body.model_dump(exclude_none=True),
    )
    await AuditWriter.for_user(session, user, request).log(
        "procurement.backfill", "procurement_order", po_id,
        after=body.model_dump(exclude_none=True),
    )  # fmt: skip
    return {"id": po_id, "status": "backfilled"}


@order_router.post("/procurement-orders/{po_id}/plugin-backfill")
async def plugin_backfill_procurement(
    po_id: int,
    body: PluginManualBackfillIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("procurement.execute"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """人工补录插件单的已发生购买（异常 #16 兜底；主通道仍是插件端点 2 重试）。

    操作前提写在服务层 docstring：先查亚马逊买家号确有购买记录再补录；
    确认没有 → 走 /release 释放回池，两个分支互斥（补录落 ref 后 release 自动拒绝）。
    """
    out = await procurement.plugin_manual_backfill(
        session,
        team_id=user.team_id or -1,
        po_id=po_id,
        actor_id=user.id,
        ref=body.purchase_order_ref,
        fields=body.model_dump(exclude_none=True),
    )
    await AuditWriter.for_user(session, user, request).log(
        "procurement.plugin_backfill", "procurement_order", po_id,
        before=out["before"], after=body.model_dump(exclude_none=True),
    )  # fmt: skip
    return {"id": out["id"], "status": out["status"]}


@order_router.post("/procurement-orders/{po_id}/exception")
async def exception_procurement(
    po_id: int,
    body: ExceptionIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("procurement.execute"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    await procurement.exception_po(
        session, team_id=user.team_id or -1, po_id=po_id, reason=body.reason, kind=body.kind
    )
    await AuditWriter.for_user(session, user, request).log(
        "procurement.exception", "procurement_order", po_id,
        after={"reason": body.reason, "kind": body.kind},
    )  # fmt: skip
    return {"id": po_id, "status": "exception"}


@order_router.post("/procurement-orders/{po_id}/release")
async def release_procurement(
    po_id: int,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("order.assign"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """把插件异常单释放回待派池（R2-13 审查 A3；0045 头注二承诺的显式出边）。

    权限点取 `order.assign` 而不是 `procurement.execute`：本动作改的是「这单归谁做」，
    与建单/分配同类，不是执行侧的领单/回填。
    """
    out = await procurement.release_po(session, team_id=user.team_id or -1, po_id=po_id)
    await AuditWriter.for_user(session, user, request).log(
        "order.po_release", "procurement_order", po_id,
        before=out["before"], after={"status": "unassigned"},
    )  # fmt: skip
    return {"id": out["id"], "status": out["status"]}


@order_router.get("/purchasers")
async def list_purchasers(
    user: Annotated[CurrentUser, Depends(require_permission("procurement.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT id, name, purchaser_kind, user_id, contact, exchange_rate,"
                " settle_currency, status FROM app.purchaser WHERE team_id = :t ORDER BY id"
            ),
            {"t": user.team_id},
        )
    ).mappings()
    return [dict(r) for r in rows]


@order_router.post("/purchasers", status_code=201)
async def create_purchaser(
    body: PurchaserIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("procurement.admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    if not body.name or not body.purchaser_kind or body.exchange_rate is None:
        raise BusinessError("PURCHASER_FIELDS_REQUIRED", "name/purchaser_kind/exchange_rate 必填")
    if body.purchaser_kind == "internal" and body.user_id is None:
        raise BusinessError("PURCHASER_USER_REQUIRED", "internal 采购方必须绑定内部成员（1:1）")
    if body.portal_username or body.portal_password:
        raise BusinessError(
            "PORTAL_NOT_AVAILABLE", "采购方门户账号随 R2#6 上线——本期仅建档，分配走内部双入口"
        )
    pid = (
        await session.execute(
            text(
                "INSERT INTO app.purchaser"
                " (team_id, name, purchaser_kind, user_id, contact, exchange_rate,"
                "  status, created_by)"
                " VALUES (:t, :n, :k, :u, cast(:c AS jsonb), :r, :st, :by) RETURNING id"
            ),
            {
                "t": user.team_id,
                "n": body.name,
                "k": body.purchaser_kind,
                "u": body.user_id,
                "c": json.dumps(body.contact or {}, ensure_ascii=False),
                "r": body.exchange_rate,
                "st": body.status or "active",
                "by": user.id,
            },
        )
    ).scalar_one()
    await AuditWriter.for_user(session, user, request).log(
        "purchaser.create", "purchaser", pid, after={"name": body.name}
    )
    return {"id": int(pid)}


@order_router.patch("/purchasers/{purchaser_id}")
async def update_purchaser(
    purchaser_id: int,
    body: PurchaserIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("procurement.admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    if body.portal_username or body.portal_password:
        raise BusinessError("PORTAL_NOT_AVAILABLE", "采购方门户账号随 R2#6 上线")
    owned = (
        await session.execute(
            text("SELECT team_id FROM app.purchaser WHERE id = :p"), {"p": purchaser_id}
        )
    ).scalar_one_or_none()
    if owned is None or owned != user.team_id:
        raise BusinessError("PURCHASER_NOT_FOUND", "采购方不存在")
    sets: list[str] = []
    params: dict[str, Any] = {"id": purchaser_id}
    for col, val in (
        ("name", body.name), ("user_id", body.user_id),
        ("exchange_rate", body.exchange_rate), ("status", body.status),
    ):  # fmt: skip
        if val is not None:
            sets.append(f"{col} = :{col}")
            params[col] = val
    if body.contact is not None:
        sets.append("contact = cast(:contact AS jsonb)")
        params["contact"] = json.dumps(body.contact, ensure_ascii=False)
    if sets:
        await session.execute(
            text(f"UPDATE app.purchaser SET {', '.join(sets)} WHERE id = :id"), params
        )
    await AuditWriter.for_user(session, user, request).log(
        "purchaser.update", "purchaser", purchaser_id, after=body.model_dump(exclude_none=True)
    )
    return {"id": purchaser_id}


@order_router.delete("/purchasers/{purchaser_id}")
async def delete_purchaser(
    purchaser_id: int,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("procurement.purchaser_delete"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    reason: str = Query(min_length=1, max_length=200, description="删除原因，落墓碑与审计留痕"),
) -> dict[str, Any]:
    """硬删除一个采购方（§7.1；R2-14 14b）。在途执行单未走完一律拒删。

    `reason` 走 query、不消费 `Idempotency-Key`——理由同 catalog/identity 的
    DELETE 端点（DELETE body 沿途会被丢；重放第二次 404 而实体确已删除）。
    """
    return await principal_delete.delete_purchaser(
        session, user=user, request=request, purchaser_id=purchaser_id, reason=reason
    )


# ── 买家账号池 + 采购插件实例（R2-13 13b；服务在 order/buyer_account.py）──
#
# 三个权限码分层（0043）：读 `buyer_account_read` / 改账号 `buyer_account_admin` /
# **签发与吊销实例单列 `plugin_instance_admin`**——签发 = 发一个能代表本团队真下单的
# 凭证，与「改个备注名」不是同一量级。**本组无 DELETE 端点**（删除分支挂 14b 之后另补）。
#
# **实例端点本轮由账号级改为团队级**（身份模型更正，图纸 07:288-340）：令牌绑「一台授权
# 浏览器」而不是买家账号，故 `/buyer-accounts/{id}/plugin-instances` 两条**已删除**——
# 那个形状本身就是「先有账号才能发令牌」的死循环，留着等于把被推翻的模型留一份可用副本。
# 认领（`pending_claim → active`）与驳回（`→ rejected`）复用既有 PATCH，不新增路由。


@order_router.get("/buyer-accounts")
async def list_buyer_accounts(
    user: Annotated[CurrentUser, Depends(require_permission("procurement.buyer_account_read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    site: str | None = Query(default=None),
    status: str | None = Query(
        default=None,
        description="pending_claim=插件首见自动登记的待认领行（一律不派单）；rejected=已驳回终态",
    ),
    include_rejected: bool = Query(
        default=False,
        description="是否包含已驳回（rejected）行；默认折叠。显式传 status 时本参数不生效。",
    ),
    q: str | None = Query(default=None, description="按 label 模糊搜索"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
) -> Page[dict[str, Any]]:
    """列买家账号池（服务端分页）。**默认折叠 `rejected`**。

    折叠的理由不是美观：驳回是终态且**不删行**（本表无 DELETE 授权，粘性即治理），
    所以被人灌过一轮伪造 customerId 的团队，账号池里会长期躺着一堆已驳回行。
    默认视图把它们收起来，与 14c 产品页折叠 `retired` 同一做法与同一语义
    （`catalog/router.py::list_products`）：**显式筛选优先，折叠不叠加**——
    运营在状态下拉里选「已驳回」时必须真能看到它们，否则看起来就是功能坏了。
    """
    return await buyer_account_service.list_accounts(
        session,
        team_id=user.team_id or -1,
        site=site,
        status=status,
        include_rejected=include_rejected,
        q=q,
        page=page,
        size=size,
    )


@order_router.post("/buyer-accounts", status_code=201)
async def create_buyer_account(
    body: BuyerAccountIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("procurement.buyer_account_admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """手工预建买家账号（**可选捷径，不是必经通道**）。

    主路径是首见自动登记：让插件在那台浏览器上跑一次拉取，服务端把它带上来的
    `customerId` 落成一条待认领行，运营补齐名称与站点后认领即可。本端点只在运营手上
    **已经**有 customerId 时省一轮往返。
    """
    return await buyer_account_service.create_account(
        session, user=user, request=request, body=body
    )


@order_router.patch("/buyer-accounts/{buyer_account_id}")
async def update_buyer_account(
    buyer_account_id: int,
    body: BuyerAccountIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("procurement.buyer_account_admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """改买家账号；**认领与驳回也走这里**。

    - 认领 `pending_claim → active`：须同时补齐 `label` 与 `site`，缺则 422
      `BUYER_ACCOUNT_CLAIM_INCOMPLETE`。
    - 驳回 `pending_claim → rejected`：**终态**，该 customerId 此后被永久占用、
      不会再自动登记。
    - 非法转移（含 `rejected → *`、任何态 → `pending_claim`）一律 409
      `BUYER_ACCOUNT_STATUS_TRANSITION`。
    """
    return await buyer_account_service.update_account(
        session, user=user, request=request, account_id=buyer_account_id, body=body
    )


@order_router.get("/plugin-instances")
async def list_plugin_instances(
    user: Annotated[CurrentUser, Depends(require_permission("procurement.buyer_account_read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    status: str | None = Query(default=None, description="active | revoked"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
) -> Page[dict[str, Any]]:
    """列**本团队**的插件实例（服务端分页）。**响应不含 token 的任何形态**（含 hash）。

    `last_seen_customer_id` 及 JOIN 出来的账号名/账号状态是**观察值**，不参与鉴权
    （0044 头注六）——它回答的是排障问题「这台机器现在登的是哪个号、认领了没有」。
    """
    return await buyer_account_service.list_instances(
        session, team_id=user.team_id or -1, status=status, page=page, size=size
    )


@order_router.post("/plugin-instances", status_code=201)
async def issue_plugin_instance(
    body: PluginInstanceIssueIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("procurement.plugin_instance_admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """签发实例令牌（**团队级，不挂账号**）。

    明文只在本响应里出现一次，关闭后无法再取（遗失请吊销后重签）。插件侧只需配置
    `token` 与 `baseUrl` 两项——**ERP 侧契约不要求插件存储任何身份**。
    """
    return await buyer_account_service.issue_instance(
        session, user=user, request=request, body=body
    )


@order_router.post("/plugin-instances/{plugin_instance_id}/revoke")
async def revoke_plugin_instance(
    plugin_instance_id: int,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("procurement.plugin_instance_admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    return await buyer_account_service.revoke_instance(
        session, user=user, request=request, instance_id=plugin_instance_id
    )


@order_router.patch("/plugin-instances/{plugin_instance_id}")
async def update_plugin_instance(
    plugin_instance_id: int,
    body: PluginInstancePatchIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("procurement.plugin_instance_admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """改执行档。切到 `live` 即该实例此后真实下单花钱——前端须二次确认，服务端留审计。"""
    return await buyer_account_service.update_instance(
        session, user=user, request=request, instance_id=plugin_instance_id, body=body
    )


# ── 发货回传（增量4；Idempotency-Key required——RS-03b 尾账收账）──


class ShipLineIn(BaseModel):
    line_no: str
    qty: int = Field(ge=1)


class ShipIn(BaseModel):
    lines: list[ShipLineIn] = Field(min_length=1)
    carrier: str = Field(min_length=1, max_length=40)
    tracking_no: str = Field(min_length=1, max_length=100)
    procurement_order_id: int | None = None


@order_router.post("/orders/{order_id}/ship", status_code=202)
async def ship_order(
    order_id: int,
    body: ShipIn,
    request: Request,
    idempotency_key: IdemKey,
    user: Annotated[CurrentUser, Depends(require_permission("order.ship"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """发货回传（异步推渠道；行级）。人工重推 = 换幂等键再次调用（生成新回传单）。"""
    team_id = user.team_id or -1

    async def _handler() -> dict[str, Any]:
        return await ship_service.ship(
            get_session_factory(),
            team_id=team_id,
            order_id=order_id,
            lines=[ln.model_dump() for ln in body.lines],
            carrier=body.carrier,
            tracking_no=body.tracking_no,
            procurement_order_id=body.procurement_order_id,
            actor_id=user.id,
            is_super=user.is_super,
        )

    result = await run_idempotent(
        get_session_factory(),
        team_id=team_id,
        is_super=user.is_super,
        endpoint="POST /orders/{orderId}/ship",
        idem_key=idempotency_key,
        payload={"order_id": order_id, **body.model_dump()},
        created_by=user.id,
        handler=_handler,
    )
    await AuditWriter.for_user(session, user, request).log(
        "order.ship", "channel_order", order_id,
        after={"lines": [ln.model_dump() for ln in body.lines], "tracking_no": body.tracking_no},
    )  # fmt: skip
    return result
