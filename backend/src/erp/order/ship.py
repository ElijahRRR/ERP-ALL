"""发货回传（R2-05 增量4；复用 RS-03b outbox 三段式——L2「测试单全流程」核心）。

- POST /orders/{id}/ship 由 run_idempotent 包裹（Idempotency-Key required，契约 002）
  ——RS-03b 尾账「ship 幂等接入」收账。
- 渠道态 Created 先 enqueue order_ack 再 enqueue order_ship：同店 FIFO 车道天然保序
  （Walmart 要求 ship 前 ack；契约不暴露 ack 端点，内部自动步骤——考古 §1.4）。
- 请求体保真 erp-core 生产实战版：Shipped + statusQuantity{EACH, str(qty)} +
  trackingInfo{shipDateTime=isoformat, carrierName, methodCode(config 默认 Standard),
  trackingNumber}。POST 永不盲重试（BR-GW-005）。
- 归位：200→shipment pushed+行 shipped+订单联动；明确拒→failed+notify（人工重推=
  换幂等键再发，生成新 shipment/命令）；无响应→verify_pending 等 ship_recon 对账。
"""

import json
from datetime import UTC, datetime
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

log = structlog.get_logger()


async def _ship_method_code(session: AsyncSession) -> str:
    row = (
        await session.execute(text("SELECT value FROM app.system_config WHERE key = 'order.ship'"))
    ).first()
    if row is not None and isinstance(row[0], dict) and row[0].get("method_code"):
        return str(row[0]["method_code"])
    return "Standard"


async def ship(
    sessions: async_sessionmaker[AsyncSession],
    *,
    team_id: int,
    order_id: int,
    lines: list[dict[str, Any]],
    carrier: str,
    tracking_no: str,
    procurement_order_id: int | None,
    actor_id: int | None,
    is_super: bool = False,
) -> dict[str, Any]:
    """发货回传编排：tx1（校验+shipment+落命令）→ ack/ship 命令三段式执行。"""
    async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as session:
        order = (
            (
                await session.execute(
                    text(
                        "SELECT id, team_id, store_id, order_date, channel_order_no,"
                        " channel_status, internal_status FROM app.channel_order"
                        " WHERE id = :o FOR UPDATE"
                    ),
                    {"o": order_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if order is None or order["team_id"] != team_id:
            raise BusinessError("ORDER_NOT_FOUND", "订单不存在")
        if order["internal_status"] in ("cancelled", "refunded", "completed"):
            raise BusinessError(
                "ORDER_STATE_INVALID", f"订单状态 {order['internal_status']} 不可发货"
            )
        wanted = {str(ln["line_no"]): int(ln["qty"]) for ln in lines}
        db_lines = {
            r.channel_line_no: r
            for r in await session.execute(
                text(
                    "SELECT channel_line_no, qty, line_status FROM app.order_line"
                    " WHERE order_id = :o AND order_date = :d FOR UPDATE"
                ),
                {"o": order_id, "d": order["order_date"]},
            )
        }
        for line_no in wanted:
            row = db_lines.get(line_no)
            if row is None:
                raise BusinessError("ORDER_LINE_NOT_FOUND", f"订单行 {line_no} 不存在")
            if row.line_status in ("shipped", "cancelled", "refunded"):
                raise BusinessError(
                    "ORDER_LINE_STATE_INVALID", f"订单行 {line_no} 状态 {row.line_status} 不可发货"
                )
        method_code = await _ship_method_code(session)
        # 模式闸预检（拒绝 → tx1 整体回滚，与 listing delist 同构）
        await gateway.prepare(session, order["store_id"])
        shipment_id = (
            await session.execute(
                text(
                    "INSERT INTO app.shipment (team_id, store_id, order_id, order_date,"
                    " procurement_order_id, lines, carrier, tracking_no, created_by)"
                    " VALUES (:t, :s, :o, :d, :po, cast(:ls AS jsonb), :ca, :tn, :u)"
                    " RETURNING id"
                ),
                {
                    "t": team_id,
                    "s": order["store_id"],
                    "o": order_id,
                    "d": order["order_date"],
                    "po": procurement_order_id,
                    "ls": json.dumps(lines, ensure_ascii=False),
                    "ca": carrier,
                    "tn": tracking_no,
                    "u": actor_id,
                },
            )
        ).scalar_one()
        po_no = order["channel_order_no"]
        ack_cmd: int | None = None
        if order["channel_status"] == "Created":  # ship 前须 ack（内部自动步骤）
            enq = await outbox.enqueue(
                session,
                team_id=team_id,
                store_id=order["store_id"],
                action="order_ack",
                payload={
                    "method": "POST",
                    "path": f"/v3/orders/{po_no}/acknowledge",
                    "endpoint_key": "POST /v3/orders:acknowledge",
                    "params": None,
                    "json_body": None,
                },
                idempotency_key=f"ack:{order_id}",
                object_type="channel_order",
                object_id=order_id,
                created_by=actor_id,
            )
            ack_cmd = int(enq["command_id"])
        ship_body = {
            "orderShipment": {
                "orderLines": {
                    "orderLine": [
                        {
                            "lineNumber": line_no,
                            "sellerOrderId": str(order_id)[:30],
                            "orderLineStatuses": {
                                "orderLineStatus": [
                                    {
                                        "status": "Shipped",
                                        "statusQuantity": {
                                            "unitOfMeasurement": "EACH",
                                            "amount": str(qty),
                                        },
                                        "trackingInfo": {
                                            "shipDateTime": datetime.now(UTC).isoformat(),
                                            "carrierName": {"carrier": carrier},
                                            "methodCode": method_code,
                                            "trackingNumber": tracking_no,
                                        },
                                    }
                                ]
                            },
                        }
                        for line_no, qty in wanted.items()
                    ]
                }
            }
        }
        enq = await outbox.enqueue(
            session,
            team_id=team_id,
            store_id=order["store_id"],
            action="order_ship",
            payload={
                "method": "POST",
                "path": f"/v3/orders/{po_no}/shipping",
                "endpoint_key": "POST /v3/orders:shipping",
                "params": None,
                "json_body": ship_body,
            },
            idempotency_key=f"ship:{shipment_id}",
            object_type="shipment",
            object_id=shipment_id,
            created_by=actor_id,
        )
        ship_cmd = int(enq["command_id"])

    if ack_cmd is not None:
        await outbox.execute_command(
            sessions, ack_cmd, team_id=team_id, is_super=is_super, applier=_apply_order_ack
        )
    outcome = await outbox.execute_command(
        sessions, ship_cmd, team_id=team_id, is_super=is_super, applier=_apply_order_ship
    )
    if outcome.get("error_code") == "SHIP_REJECTED":
        raise BusinessError(
            "ERP_SHIP_REJECTED", f"发货回传被渠道拒绝 HTTP {outcome.get('http_status')}"
        )
    return {
        "order_id": order_id,
        "shipment_id": int(shipment_id),
        "push_status": outcome.get("push_status", "pending"),
        "command_status": outcome.get("command_status", "inflight"),
    }


async def _apply_order_ack(
    session: AsyncSession, cmd: dict[str, Any], resp: GatewayResponse | None
) -> dict[str, Any]:
    """ack 归位：200=确认；明确拒=failed 终局（不阻 ship——多为「已确认过」）；未知=对账。"""
    cid, fence = int(cmd["id"]), int(cmd["fence"])
    if resp is not None and resp.dry_run:
        if not await outbox.complete(
            session, command_id=cid, fence=fence, status="succeeded", result={"dry_run": True}
        ):
            return {"command_status": "superseded"}
        return {"acked": True, "dry_run": True}
    if resp is None or resp.status is None:
        if not await outbox.complete(session, command_id=cid, fence=fence, status="verify_pending"):
            return {"command_status": "superseded"}
        return {"acked": False, "verify": "unknown"}
    ok = resp.status == 200  # noqa: PLR2004
    if not await outbox.complete(
        session,
        command_id=cid,
        fence=fence,
        status="succeeded" if ok else "failed",
        result={"http_status": resp.status},
        error_code=None if ok else "ACK_REJECTED",
    ):
        return {"command_status": "superseded"}
    return {"acked": ok}


async def _apply_order_ship(  # noqa: PLR0911 归位分支=渠道响应形态
    session: AsyncSession, cmd: dict[str, Any], resp: GatewayResponse | None
) -> dict[str, Any]:
    """ship 归位（outbox.Applier 契约：complete(fence) 成功才落业务态）。"""
    shipment_id = int(cmd["object_id"])
    cid, fence = int(cmd["id"]), int(cmd["fence"])

    if resp is not None and resp.dry_run:
        if not await outbox.complete(
            session, command_id=cid, fence=fence, status="succeeded", result={"dry_run": True}
        ):
            return {"command_status": "superseded"}
        return {"push_status": "pending", "dry_run": True}

    if resp is None or resp.status is None:
        # 结果未知：保持 pending，绝不重发——ship_recon 对账（BR-GW-005）
        if not await outbox.complete(session, command_id=cid, fence=fence, status="verify_pending"):
            return {"command_status": "superseded"}
        return {"push_status": "pending", "verify": "unknown"}

    if resp.status == 200:  # noqa: PLR2004
        if not await outbox.complete(session, command_id=cid, fence=fence, status="succeeded"):
            return {"command_status": "superseded"}
        await apply_ship_success(session, shipment_id=shipment_id)
        return {"push_status": "pushed"}

    if not await outbox.complete(
        session,
        command_id=cid,
        fence=fence,
        status="failed",
        result={"http_status": resp.status},
        error_code="SHIP_REJECTED",
    ):
        return {"command_status": "superseded"}
    shipment = await _load_shipment(session, shipment_id)
    await session.execute(
        text(
            "UPDATE app.shipment SET push_status = 'failed',"
            " channel_response = cast(:r AS jsonb) WHERE id = :s"
        ),
        {"r": json.dumps({"http_status": resp.status}), "s": shipment_id},
    )
    await notify(
        session,
        team_id=int(shipment["team_id"]),
        severity="critical",
        category="order_flag",
        title=f"发货回传被渠道拒绝：订单 #{shipment['order_id']}（HTTP {resp.status}）",
        body="核对物流信息后在订单页重新发货（生成新回传单）",
        object_type="shipment",
        object_id=str(shipment_id),
        dedupe_key=f"ship_failed:{shipment_id}",
    )
    return {"push_status": "failed", "error_code": "SHIP_REJECTED", "http_status": resp.status}


async def _load_shipment(session: AsyncSession, shipment_id: int) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, store_id, order_id, order_date, procurement_order_id,"
                    " lines, carrier, tracking_no, push_status FROM app.shipment"
                    " WHERE id = :s FOR UPDATE"
                ),
                {"s": shipment_id},
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def apply_ship_success(session: AsyncSession, *, shipment_id: int) -> None:
    """渠道确认发货后的业务落账（applier 与 ship_recon 共用）。"""
    shipment = await _load_shipment(session, shipment_id)
    if shipment["push_status"] == "pushed":
        return  # 幂等：已落账
    await session.execute(
        text(
            "UPDATE app.shipment SET push_status = 'pushed', pushed_at = now(),"
            " channel_response = cast(:r AS jsonb) WHERE id = :s"
        ),
        {"r": json.dumps({"http_status": 200}), "s": shipment_id},
    )
    for ln in shipment["lines"]:
        await session.execute(
            text(
                "UPDATE app.order_line SET line_status = 'shipped', carrier = :ca,"
                " tracking_no = :tn, shipped_at = now()"
                " WHERE order_id = :o AND order_date = :d AND channel_line_no = :ln"
            ),
            {
                "ca": shipment["carrier"],
                "tn": shipment["tracking_no"],
                "o": shipment["order_id"],
                "d": shipment["order_date"],
                "ln": str(ln["line_no"]),
            },
        )
    await session.execute(  # 全行终态（shipped/cancelled/refunded）→ 订单 shipped
        text(
            "UPDATE app.channel_order SET internal_status = 'shipped'"
            " WHERE id = :o AND order_date = :d"
            "   AND internal_status NOT IN ('cancelled','refunded','completed')"
            "   AND NOT EXISTS (SELECT 1 FROM app.order_line WHERE order_id = :o"
            "     AND order_date = :d AND line_status NOT IN ('shipped','cancelled','refunded'))"
        ),
        {"o": shipment["order_id"], "d": shipment["order_date"]},
    )
    if shipment["procurement_order_id"] is not None:
        await session.execute(
            text(
                "UPDATE app.procurement_order SET status = 'shipped', shipped_at = now()"
                " WHERE id = :p AND status IN ('claimed','purchased')"
            ),
            {"p": shipment["procurement_order_id"]},
        )


APPLIERS: dict[str, outbox.Applier] = {
    "order_ack": _apply_order_ack,
    "order_ship": _apply_order_ship,
}
