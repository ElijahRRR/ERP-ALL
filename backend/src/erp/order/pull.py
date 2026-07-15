"""渠道订单增量拉取（R2-05 增量1；BR-ORD-001~004 保真 + 旧系统实战语义）。

协议（.agent/evidence/R2-05/archaeology.md §3）：
- 增量主键 = lastModifiedStartDate（捕获老单状态变化）；窗口 = max(last_sync − 重叠, now − 兜底)；
- **恒传 createdStartDate = now − 179d**（BR-ORD-002：不传则 Walmart 默认 7 天窗架空增量）；
- nextCursor 是带 ? 的完整 query 串：下一页 path = /v3/orders{cursor} 且不再带 params，
  店内串行翻页（游标 ~2 分钟时效）；
- upsert 幂等（BR-ORD-003）：同步列覆盖刷新，内部列（internal_status/has_flag）永不触碰；
  渠道 Cancelled 强制覆盖内部状态并出 notification（001/07:45）；
- 行状态 = orderLineStatuses[-1].status；trackingInfo 深埋 status 对象内；
  金额 = Σ chargeType==PRODUCT（旧系统一致，实战语义）；
- 成功整店才推 sync_state.last_sync（失败保留窗口重拉）。

事务纪律：prepare 短事务 → 逐页 HTTP（零事务）→ 每页 upsert 短事务（行锁不跨 HTTP）。
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.channel.gateway import gateway
from erp.core.db import system_tx
from erp.notify.service import notify

log = structlog.get_logger()

SYNC_SCOPE = "order_pull"
# 渠道行状态 → 本地 line_status（响应枚举含 Refund，映射到 refunded）
_LINE_STATUS_MAP = {
    "created": "created",
    "acknowledged": "acknowledged",
    "shipped": "shipped",
    "delivered": "shipped",  # 行级无 delivered 态：已发货后的物流终态归 shipped
    "cancelled": "cancelled",
    "refund": "refunded",
    "refunded": "refunded",
}
# 订单级聚合排序：最落后的未取消行决定订单渠道态
_STAGE_ORDER = ["Created", "Acknowledged", "Shipped", "Delivered", "Refund"]


def _epoch_ms(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError):
        return None


def _sum_charges(line: dict[str, Any], charge_type: str) -> float:
    total = 0.0
    for ch in (line.get("charges") or {}).get("charge") or []:
        if str(ch.get("chargeType")) == charge_type:
            amount = (ch.get("chargeAmount") or {}).get("amount")
            if amount is not None:
                total += float(amount)
    return total


def _line_status_raw(line: dict[str, Any]) -> str:
    statuses = (line.get("orderLineStatuses") or {}).get("orderLineStatus") or []
    return str(statuses[-1].get("status")) if statuses else "Created"


def _line_tracking(line: dict[str, Any]) -> dict[str, Any]:
    """trackingInfo 深埋在 status 对象内（历史 bug 修复点）——遍历取有值项。"""
    for st in (line.get("orderLineStatuses") or {}).get("orderLineStatus") or []:
        info = st.get("trackingInfo")
        if info:
            carrier_name = info.get("carrierName") or {}
            return {
                "carrier": carrier_name.get("carrier") or carrier_name.get("otherCarrier"),
                "tracking_no": info.get("trackingNumber"),
                "shipped_at": _epoch_ms(info.get("shipDateTime")),
            }
    return {"carrier": None, "tracking_no": None, "shipped_at": None}


def map_order(raw: dict[str, Any]) -> dict[str, Any]:
    """渠道 order JSON → 本地字段（订单 + 行）。纯函数，供拉取与对账 harness 共用。"""
    lines: list[dict[str, Any]] = []
    raw_lines = (raw.get("orderLines") or {}).get("orderLine") or []
    order_total = 0.0
    stages: list[str] = []
    for ln in raw_lines:
        status_raw = _line_status_raw(ln)
        stages.append(status_raw)
        product_sum = _sum_charges(ln, "PRODUCT")
        qty = int(float((ln.get("orderLineQuantity") or {}).get("amount") or 1))
        order_total += product_sum
        tracking = _line_tracking(ln)
        lines.append(
            {
                "channel_line_no": str(ln.get("lineNumber")),
                "channel_sku": str((ln.get("item") or {}).get("sku") or ""),
                "product_name": str((ln.get("item") or {}).get("productName") or ""),
                "qty": qty,
                "unit_price": round(product_sum / qty, 2) if qty else product_sum,
                "line_status": _LINE_STATUS_MAP.get(status_raw.lower(), "created"),
                **tracking,
            }
        )
    # 订单级渠道态 = 最落后的未取消行；全取消 = Cancelled（确定性聚合，specs 落笔）
    active = [s for s in stages if s != "Cancelled"]
    if not stages:
        channel_status = "Created"
    elif not active:
        channel_status = "Cancelled"
    else:
        channel_status = min(
            active, key=lambda s: _STAGE_ORDER.index(s) if s in _STAGE_ORDER else 0
        )
    shipping = raw.get("shippingInfo") or {}
    address = shipping.get("postalAddress") or {}
    return {
        "channel_order_no": str(raw.get("purchaseOrderId")),
        "customer_order_no": str(raw.get("customerOrderId") or ""),
        "order_date": _epoch_ms(raw.get("orderDate")) or datetime.now(UTC),
        "channel_status": channel_status,
        "customer": {"name": address.get("name")},  # PII 最小化（00 §10）
        "ship_to": {**address, "phone": shipping.get("phone")},
        "order_total": round(order_total, 2),
        "item_count": len(lines),
        "lines": lines,
    }


async def _upsert_order(
    session: AsyncSession, *, team_id: int, store_id: int, mapped: dict[str, Any]
) -> dict[str, Any]:
    """单订单 upsert（BR-ORD-003：同步列覆盖，内部列不触碰）。返回 {order_id, cancelled_now}。"""
    row = (
        await session.execute(
            text(
                "INSERT INTO app.channel_order"
                " (team_id, store_id, channel_order_no, order_date, channel_status,"
                "  customer, ship_to, order_total, item_count, pulled_at)"
                " VALUES (:t, :s, :no, :d, :cs, cast(:cust AS jsonb), cast(:ship AS jsonb),"
                "         :total, :n, now())"
                " ON CONFLICT (store_id, channel_order_no, order_date) DO UPDATE SET"
                "   channel_status = excluded.channel_status,"
                "   customer = excluded.customer, ship_to = excluded.ship_to,"
                "   order_total = excluded.order_total, item_count = excluded.item_count,"
                "   pulled_at = excluded.pulled_at"
                " RETURNING id, internal_status"
            ),
            {
                "t": team_id,
                "s": store_id,
                "no": mapped["channel_order_no"],
                "d": mapped["order_date"],
                "cs": mapped["channel_status"],
                "cust": json.dumps(mapped["customer"], ensure_ascii=False),
                "ship": json.dumps(mapped["ship_to"], ensure_ascii=False),
                "total": mapped["order_total"],
                "n": mapped["item_count"],
            },
        )
    ).one()
    order_id = int(row.id)
    for ln in mapped["lines"]:
        await session.execute(
            text(
                "INSERT INTO app.order_line"
                " (order_id, order_date, team_id, channel_line_no, channel_sku, listing_id,"
                "  product_id, qty, unit_price, line_status, carrier, tracking_no, shipped_at)"
                " VALUES (:o, :d, :t, :ln, :sku,"
                "   (SELECT l.id FROM app.listing l WHERE l.store_id = :store"
                "     AND l.channel_sku = :sku ORDER BY l.id DESC LIMIT 1),"
                "   (SELECT l.product_id FROM app.listing l WHERE l.store_id = :store"
                "     AND l.channel_sku = :sku ORDER BY l.id DESC LIMIT 1),"
                "   :q, :p, :st, :ca, :tn, :sa)"
                " ON CONFLICT (order_id, channel_line_no, order_date) DO UPDATE SET"
                "   channel_sku = excluded.channel_sku, qty = excluded.qty,"
                "   unit_price = excluded.unit_price, line_status = excluded.line_status,"
                "   carrier = coalesce(excluded.carrier, app.order_line.carrier),"
                "   tracking_no = coalesce(excluded.tracking_no, app.order_line.tracking_no),"
                "   shipped_at = coalesce(excluded.shipped_at, app.order_line.shipped_at)"
            ),
            {
                "o": order_id,
                "d": mapped["order_date"],
                "t": team_id,
                "store": store_id,
                "ln": ln["channel_line_no"],
                "sku": ln["channel_sku"],
                "q": ln["qty"],
                "p": ln["unit_price"],
                "st": ln["line_status"],
                "ca": ln["carrier"],
                "tn": ln["tracking_no"],
                "sa": ln["shipped_at"],
            },
        )
    # 渠道 Cancelled 强制覆盖内部状态并出 notification（001/07:45）
    cancelled_now = mapped["channel_status"] == "Cancelled" and row.internal_status != "cancelled"
    if cancelled_now:
        await session.execute(
            text(
                "UPDATE app.channel_order SET internal_status = 'cancelled'"
                " WHERE id = :o AND order_date = :d"
            ),
            {"o": order_id, "d": mapped["order_date"]},
        )
        await notify(
            session,
            team_id=team_id,
            severity="warn",
            category="order_flag",
            title=f"渠道订单 {mapped['channel_order_no']} 已在 Walmart 侧取消",
            body="内部状态已强制置 cancelled；如有在途采购请及时处理",
            object_type="channel_order",
            object_id=str(order_id),
            dedupe_key=f"order_cancelled:{order_id}",
        )
    return {"order_id": order_id, "cancelled_now": cancelled_now}


async def pull_store_orders(
    sessions: async_sessionmaker[AsyncSession], store_id: int, *, config: dict[str, Any]
) -> dict[str, Any]:
    """单店增量拉取。返回 stats；任何页失败即抛（sync_state 不推进，下轮重拉同窗口）。"""
    overlap_h = int(config.get("overlap_hours", 1))
    initial_days = int(config.get("initial_days", 30))
    created_floor_days = int(config.get("created_floor_days", 179))
    page_limit = int(config.get("page_limit", 200))
    max_pages = int(config.get("max_pages", 30))

    started_at = datetime.now(UTC)
    async with system_tx(sessions) as session:
        team_id = (
            await session.execute(
                text("SELECT team_id FROM app.store WHERE id = :s"), {"s": store_id}
            )
        ).scalar_one()
        last_sync = (
            await session.execute(
                text("SELECT last_sync_at FROM app.sync_state WHERE scope = :sc AND ref_id = :r"),
                {"sc": SYNC_SCOPE, "r": store_id},
            )
        ).scalar_one_or_none()
        ctx, mode = await gateway.prepare(session, store_id)

    floor = started_at - timedelta(days=initial_days)
    since = max(last_sync - timedelta(hours=overlap_h), floor) if last_sync else floor
    params: dict[str, Any] | None = {
        "lastModifiedStartDate": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "createdStartDate": (started_at - timedelta(days=created_floor_days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "limit": str(page_limit),
    }

    stats = {"orders": 0, "lines": 0, "cancelled": 0, "pages": 0}
    path = "/v3/orders"
    for _ in range(max_pages):
        resp = await gateway.request_prepared(
            ctx, mode, "GET", path, endpoint_key="GET /v3/orders", params=params
        )
        if resp.dry_run:
            return {**stats, "dry_run": True}
        if resp.status != 200 or not isinstance(resp.data, dict):  # noqa: PLR2004
            page_no = stats["pages"] + 1
            raise RuntimeError(
                f"order_pull 店铺 {store_id} 失败：HTTP {resp.status}（页 {page_no}）"
            )
        stats["pages"] += 1
        elements = (resp.data.get("list") or {}).get("elements") or {}
        orders = elements.get("order") or []
        async with system_tx(sessions) as session:
            for raw in orders:
                mapped = map_order(raw)
                outcome = await _upsert_order(
                    session, team_id=team_id, store_id=store_id, mapped=mapped
                )
                stats["orders"] += 1
                stats["lines"] += len(mapped["lines"])
                stats["cancelled"] += 1 if outcome["cancelled_now"] else 0
        cursor = ((resp.data.get("list") or {}).get("meta") or {}).get("nextCursor")
        if not cursor or "hasMoreElements=false" in str(cursor) or not orders:
            break
        path = f"/v3/orders{cursor}"  # nextCursor 是带 ? 的完整 query 串
        params = None

    async with system_tx(sessions) as session:  # 成功才推 high-water mark（BR-ORD-001）
        await session.execute(
            text(
                "INSERT INTO app.sync_state (scope, ref_id, last_sync_at, stats)"
                " VALUES (:sc, :r, :at, cast(:st AS jsonb))"
                " ON CONFLICT (scope, ref_id) DO UPDATE SET"
                "   last_sync_at = excluded.last_sync_at, stats = excluded.stats,"
                "   updated_at = now()"
            ),
            {
                "sc": SYNC_SCOPE,
                "r": store_id,
                "at": started_at,
                "st": json.dumps(stats, ensure_ascii=False),
            },
        )
    return stats


async def run(sessions: async_sessionmaker[AsyncSession], config: dict[str, Any]) -> dict[str, Any]:
    """beat 任务本体：遍历有凭证店铺逐店拉取（店间失败隔离，全败才抛）。"""
    async with system_tx(sessions) as session:
        store_ids = [
            int(r.id)
            for r in await session.execute(
                text(
                    "SELECT s.id FROM app.store s"
                    " JOIN app.store_credential c ON c.store_id = s.id"
                    " WHERE s.status = 'active' ORDER BY s.id"
                )
            )
        ]
    totals = {"stores": len(store_ids), "ok": 0, "failed": 0, "orders": 0, "lines": 0,
              "cancelled": 0}  # fmt: skip
    for sid in store_ids:
        try:
            st = await pull_store_orders(sessions, sid, config=config)
        except Exception as exc:
            totals["failed"] += 1
            log.warning("order_pull.store_failed", store_id=sid, error=str(exc))
            continue
        totals["ok"] += 1
        for k in ("orders", "lines", "cancelled"):
            totals[k] += int(st.get(k, 0))
    if store_ids and totals["failed"] and not totals["ok"]:
        raise RuntimeError(f"order_pull 全部店铺失败：{totals['failed']}/{len(store_ids)}")
    return totals
