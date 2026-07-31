"""采购执行单服务（R2-05 增量3；D-Q50 双入口①②——外部门户③随 R2#6）。

- 状态机：unassigned → assigned → claimed → purchased/shipped → backfilled（+exception/cancelled）。
  本单实现 unassigned/assigned/claimed/backfilled/exception；purchased/shipped 细分随物流接入。
- 汇率锁定（D-Q32/评审 C4）：领单或回填时从 purchaser.exchange_rate 锁快照
  ——之后改采购方汇率不影响已锁单。
- order_block 档位（automation_policy，07:82）：manual=纯软标记（默认）；
  semi/auto=存在未放行 flagged 的订单冻结在 checked，不允许建单/分配。
- 订单联动：assign/claim → internal_status=assigned；backfill → purchasing。
- 插件路径（R2-13 13b）走另一条支线：`buyer_account_id` 非空即代表「已派给买家账号」，
  派发算法在 `order/dispatch.py`，本文件只负责与它互斥（见 assign_po 的守卫）。
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.automation import AutomationFlow, Mode, resolve_mode
from erp.core.errors import BusinessError

BACKFILL_FIELDS = (
    "purchase_platform",
    "purchase_order_ref",
    "purchase_cost",
    "purchase_currency",
    "freight_cost",
    "carrier",
    "tracking_no",
    "note",
    # R2-13 0045 新增的四列同属「回填」语义（人工在订单页也要能补），故进本表。
    # **`exception_kind` 不在此列**——它是问题分类不是回填字段，走 exception_po 的 kind 参数。
    "tax_amount",
    "payment_card_last4",
    "delivery_est_date",
    "delivery_est_raw",
)


async def _order_block_gate(session: AsyncSession, *, team_id: int, order_id: int) -> None:
    """semi/auto 档 + 未放行 flagged → 冻结（409）。manual（默认）纯软标记放行。"""
    # R2-09 增量1：改走三档内核，不再各写各的 SQL。**行为逐条对齐**——
    # 内核对「无行 / enabled=false / 取值不是三档之一」一律回 manual，与原来
    # `AND enabled` + `scalar_one_or_none()` + `not in ("semi","auto")` 的结果相同；
    # 非法档位（如本 flow 不该有的 semi）内核只告警不改写，故此处仍会拦截。
    mode = await resolve_mode(session, team_id=team_id, flow=AutomationFlow.ORDER_BLOCK)
    if mode not in (Mode.SEMI, Mode.AUTO):
        return
    flagged = (
        await session.execute(
            text(
                "SELECT 1 FROM app.order_check WHERE order_id = :o AND result = 'flagged'"
                " AND resolved_at IS NULL LIMIT 1"
            ),
            {"o": order_id},
        )
    ).first()
    if flagged is not None:
        raise BusinessError(
            "ORDER_BLOCKED",
            "四检 flagged 未放行且 order_block 档位为拦截——放行或调档后再分配",
            http_status=409,
        )


async def _load_po(session: AsyncSession, po_id: int, team_id: int) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, store_id, order_id, order_date, status,"
                    " assignee_kind, purchaser_id, exchange_rate_locked, buyer_account_id"
                    " FROM app.procurement_order WHERE id = :p FOR UPDATE"
                ),
                {"p": po_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["team_id"] != team_id:
        raise BusinessError("PROCUREMENT_NOT_FOUND", "执行单不存在")
    return dict(row)


async def advance_order(
    session: AsyncSession,
    *,
    order_id: int,
    order_date: Any,
    to_status: str,
    from_statuses: tuple[str, ...],
) -> None:
    """渠道订单 `internal_status` 的条件推进（`from_statuses` 之外一律不动）。

    R2-13 13d 起有第二个消费者（`plugin/service.py` 的采购完成回填），故由模块私有
    改为公开——**唯一的推进入口只应有这一处**，两处各写各的 UPDATE 迟早会漂。
    """
    await session.execute(
        text(
            "UPDATE app.channel_order SET internal_status = :to"
            " WHERE id = :o AND order_date = :d AND internal_status = ANY(:froms)"
        ),
        {"to": to_status, "o": order_id, "d": order_date, "froms": list(from_statuses)},
    )


async def create_po(
    session: AsyncSession,
    *,
    team_id: int,
    order_id: int,
    purchaser_id: int | None,
    actor_id: int | None,
) -> dict[str, Any]:
    order = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, store_id, order_date, internal_status"
                    " FROM app.channel_order WHERE id = :o FOR UPDATE"
                ),
                {"o": order_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if order is None or order["team_id"] != team_id:
        raise BusinessError("ORDER_NOT_FOUND", "订单不存在")
    if order["internal_status"] not in ("checked", "assigned"):
        raise BusinessError(
            "ORDER_STATE_INVALID",
            f"订单状态 {order['internal_status']} 不可建执行单（需已过四检且未进入履约终态）",
        )
    await _order_block_gate(session, team_id=team_id, order_id=order_id)
    po_id = (
        await session.execute(
            text(
                "INSERT INTO app.procurement_order"
                " (team_id, store_id, order_id, order_date, created_by)"
                " VALUES (:t, :s, :o, :d, :u) RETURNING id"
            ),
            {
                "t": team_id,
                "s": order["store_id"],
                "o": order_id,
                "d": order["order_date"],
                "u": actor_id,
            },
        )
    ).scalar_one()
    if purchaser_id is not None:
        await assign_po(
            session, team_id=team_id, po_id=po_id, purchaser_id=purchaser_id, actor_id=actor_id
        )
    return {"id": int(po_id), "order_id": order_id}


async def assign_po(
    session: AsyncSession, *, team_id: int, po_id: int, purchaser_id: int, actor_id: int | None
) -> None:
    po = await _load_po(session, po_id, team_id)
    if po["status"] not in ("unassigned", "assigned"):
        raise BusinessError(
            "PROCUREMENT_STATE_INVALID", f"状态 {po['status']} 不可分配", http_status=409
        )
    # R2-13 13b：铁律「同一订单只派一个买家账号」在应用层的对偶。
    # 不加这条就有一个重复下单的口子：插件派走的单是 `assigned`，而本函数允许
    # `unassigned|assigned` 再分配给 purchaser——于是同一订单**既派机器人又派人**，
    # 两边都会真下单。DB 侧的 `uq_po_active_dispatch` 只管「一个 order 一个买家账号」，
    # 管不到「买家账号 + 人工采购方」这种混派。
    if po["buyer_account_id"] is not None:
        raise BusinessError(
            "PROCUREMENT_PLUGIN_DISPATCHED",
            "该执行单已派给买家账号（插件可能正在执行），不能同时改派人工采购方"
            "——请先处置该单（异常/取消）再改派",
            http_status=409,
        )
    purchaser = (
        (
            await session.execute(
                text("SELECT id, team_id, purchaser_kind, status FROM app.purchaser WHERE id = :p"),
                {"p": purchaser_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if purchaser is None or purchaser["team_id"] != team_id:
        raise BusinessError("PURCHASER_NOT_FOUND", "采购方不存在")
    if purchaser["status"] != "active":
        raise BusinessError("PURCHASER_DISABLED", "采购方已停用")
    await _order_block_gate(session, team_id=team_id, order_id=po["order_id"])
    await session.execute(
        text(
            "UPDATE app.procurement_order SET purchaser_id = :p, assignee_kind = :ak,"
            " status = 'assigned', assigned_by = :u, assigned_at = now() WHERE id = :id"
        ),
        {"p": purchaser_id, "ak": purchaser["purchaser_kind"], "u": actor_id, "id": po_id},
    )
    await advance_order(
        session,
        order_id=po["order_id"],
        order_date=po["order_date"],
        to_status="assigned",
        from_statuses=("checked",),
    )


async def claim_po(session: AsyncSession, *, team_id: int, po_id: int) -> None:
    """内部领单（D-Q50①）：assigned → claimed，并锁定汇率快照（D-Q32）。"""
    po = await _load_po(session, po_id, team_id)
    if po["status"] != "assigned":
        raise BusinessError(
            "PROCUREMENT_STATE_INVALID", f"状态 {po['status']} 不可领单", http_status=409
        )
    await session.execute(
        text(
            "UPDATE app.procurement_order SET status = 'claimed', claimed_at = now(),"
            " exchange_rate_locked = coalesce(exchange_rate_locked,"
            "   (SELECT exchange_rate FROM app.purchaser WHERE id = purchaser_id))"
            " WHERE id = :id"
        ),
        {"id": po_id},
    )


async def backfill_po(
    session: AsyncSession,
    *,
    team_id: int,
    po_id: int,
    actor_id: int | None,
    fields: dict[str, Any],
) -> None:
    """回填采购与物流。运营代填（assignee 为 none）标 op_direct（D-Q50②）。"""
    po = await _load_po(session, po_id, team_id)
    if po["status"] not in ("unassigned", "assigned", "claimed", "purchased"):
        raise BusinessError(
            "PROCUREMENT_STATE_INVALID", f"状态 {po['status']} 不可回填", http_status=409
        )
    sets = ["status = 'backfilled'", "backfilled_at = now()",
            "purchased_at = coalesce(purchased_at, now())",
            "backfill_actor_kind = :bak", "backfill_actor_id = :actor",
            "exchange_rate_locked = coalesce(exchange_rate_locked,"
            " (SELECT exchange_rate FROM app.purchaser WHERE id = purchaser_id))"]  # fmt: skip
    params: dict[str, Any] = {
        "id": po_id,
        "bak": "op_direct" if po["assignee_kind"] == "none" else "internal",
        "actor": actor_id,
    }
    for f in BACKFILL_FIELDS:
        if f in fields and fields[f] is not None:
            sets.append(f"{f} = :{f}")
            params[f] = fields[f]
    await session.execute(
        text(f"UPDATE app.procurement_order SET {', '.join(sets)} WHERE id = :id"), params
    )
    await advance_order(
        session,
        order_id=po["order_id"],
        order_date=po["order_date"],
        to_status="purchasing",
        from_statuses=("checked", "assigned"),
    )


async def exception_po(
    session: AsyncSession, *, team_id: int, po_id: int, reason: str, kind: str | None = None
) -> None:
    """标异常。`kind` = 问题分类（0045 的九值词表），可空。

    `exception_kind` 用 `coalesce(:kind, exception_kind)` 写：不传就保持原值，
    **不会把已有的分类抹成 NULL**——重复标异常（比如插件先落 product 类、人再补一句
    原因）不该丢掉第一次的归类。
    """
    po = await _load_po(session, po_id, team_id)
    if po["status"] in ("backfilled", "cancelled"):
        raise BusinessError(
            "PROCUREMENT_STATE_INVALID", f"状态 {po['status']} 不可标异常", http_status=409
        )
    await session.execute(
        text(
            "UPDATE app.procurement_order SET status = 'exception', exception_reason = :r,"
            " exception_kind = coalesce(:kind, exception_kind) WHERE id = :id"
        ),
        {"r": reason, "kind": kind, "id": po_id},
    )
