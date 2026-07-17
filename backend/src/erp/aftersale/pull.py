"""渠道退货全量拉取（R2-07 增量1；BR-AS-001~007 保真 + 旧仓生产语义）。

协议（.agent/evidence/R2-07/archaeology.md §3/§4）：
- Returns API **无时间过滤**（BR-AS-001）→ 每轮全量；limit=200 翻页；
  nextCursor 是完整 URL（?sellerId=...&offset=...），parse_qs 解析回参数发下一页
  （与订单域"整串拼路径"协议不同——旧仓 fetch_walmart_returns.py:52 实战语义）；
- 响应结构：顶层 returnOrders[] + meta.nextCursor（订单域是 list.elements 嵌套）；
- 行级展开（BR-AS-003）：一行 = returnOrderLine；status/refundStatus/deliveryStatus
  均行级（BR-AS-004）；行身份 = salesOrderLineNumber（缺失回退序号）；
- upsert by (RMA, line)（C6 已决）：同步列覆盖刷新、internal_status 永不触碰；
  行状态字段变化写 channel_return_event 留痕（旧系统覆盖式丢历史是 BR-AS-006 缺陷）；
- 限流 50/min 由网关 GCRA 闸管（替代旧仓手工 1.3s/页）；
- 写入量骤降 >50% 告警不重跑（BR-AS-007）——upsert 模型下降级只意味着少见，不丢数据；
- 承运商/跟踪号取 returnLineGroups[0] 首条 label（旧仓 first_carrier 同义），行间重复存。

事务纪律：prepare 短事务 → 逐页 HTTP（零事务）→ 每页 upsert 短事务（行锁不跨 HTTP）。
"""

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.channel.gateway import gateway
from erp.core.db import system_tx
from erp.notify.service import notify

log = structlog.get_logger()

SYNC_SCOPE = "return_pull"
# 行级状态字段：变化即写 channel_return_event（C6 历史快照）
_TRACKED_FIELDS = ("line_status", "refund_status", "delivery_status", "refunded_qty",
                   "tracking_no")  # fmt: skip


def _parse_ts(value: Any) -> datetime | None:
    """渠道时间戳容错：ISO8601（含 Z）为主（旧仓实测），epoch 毫秒兜底。"""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
        except (ValueError, OSError):
            return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _first_carrier(order: dict[str, Any]) -> tuple[str | None, str | None]:
    """returnLineGroups[0] 首条 label 的承运商信息（旧仓 first_carrier 语义）。"""
    groups = order.get("returnLineGroups") or []
    for label in (groups[0] if groups else {}).get("labels") or []:
        for c in label.get("carrierInfoList") or []:
            return c.get("carrierName"), c.get("trackingNo")
    return None, None


def _unit_price(line: dict[str, Any]) -> float | None:
    for ch in line.get("charges") or []:
        if ch.get("chargeCategory") == "PRODUCT" and ch.get("chargeName") == "ItemPrice":
            amount = (ch.get("chargePerUnit") or {}).get("currencyAmount")
            return float(amount) if amount is not None else None
    return None


def map_return(raw: dict[str, Any]) -> dict[str, Any]:
    """渠道 returnOrder JSON → 本地字段（头 + 行）。纯函数，供拉取与对拍共用。"""
    carrier, tracking = _first_carrier(raw)
    lines: list[dict[str, Any]] = []
    for idx, ln in enumerate(raw.get("returnOrderLines") or []):
        item = ln.get("item") or {}
        qty_raw = (ln.get("quantity") or {}).get("measurementValue")
        lines.append(
            {
                # 行身份 = 销售行号（同 RMA 一订单行一退货行）；缺失回退序号
                "line_no": str(ln.get("salesOrderLineNumber") or idx + 1),
                "channel_sku": str(item.get("sku") or ""),
                "product_name": item.get("productName"),
                "condition": item.get("condition"),
                "qty": int(float(qty_raw)) if qty_raw is not None and qty_raw != "" else 1,
                "refunded_qty": int(float(ln.get("refundedQty") or 0)),
                "unit_price": _unit_price(ln),
                "line_status": str(ln.get("status") or ""),
                "refund_status": ln.get("currentRefundStatus"),
                "delivery_status": ln.get("currentDeliveryStatus"),
                "return_method": ln.get("returnMethod"),
                "return_reason": ln.get("returnReason"),
                "return_description": ln.get("returnDescription"),
                "status_time": _parse_ts(ln.get("statusTime")),
                "purchase_order_no": ln.get("purchaseOrderId"),
                "carrier": carrier,
                "tracking_no": tracking,
            }
        )
    cn = raw.get("customerName") or {}
    full_name = f"{cn.get('firstName', '')} {cn.get('lastName', '')}".strip() or None
    amt = raw.get("totalRefundAmount") or {}
    # 头级渠道态 = 行状态聚合：全行一致取其值，分歧标 MIXED（行级才是权威，BR-AS-004）
    statuses = {ln["line_status"] for ln in lines if ln["line_status"]}
    channel_status = statuses.pop() if len(statuses) == 1 else ("MIXED" if statuses else "UNKNOWN")
    return {
        "channel_return_no": str(raw.get("returnOrderId")),
        "customer_order_no": str(raw.get("customerOrderId") or "") or None,
        "return_date": _parse_ts(raw.get("returnOrderDate")) or datetime.now(UTC),
        "return_by_date": _parse_ts(raw.get("returnByDate")),
        "reason": next((ln["return_reason"] for ln in lines if ln["return_reason"]), None),
        "channel_status": channel_status,
        "refund_mode": raw.get("refundMode"),
        "qty": sum(ln["qty"] for ln in lines),
        "refund_amount": (
            float(amt["currencyAmount"]) if amt.get("currencyAmount") is not None else None
        ),
        "currency": str(amt.get("currencyUnit") or "USD")[:3],
        "customer": {"name": full_name, "email": raw.get("customerEmailId")},
        "purchase_order_no": next(
            (ln["purchase_order_no"] for ln in lines if ln["purchase_order_no"]), None
        ),
        "lines": lines,
    }


async def _upsert_return(
    session: AsyncSession, *, team_id: int, store_id: int, mapped: dict[str, Any]
) -> dict[str, Any]:
    """单 RMA upsert（C6：同步列刷新、internal_status 不触碰）。返回 {return_id, events}。"""
    row = (
        await session.execute(
            text(
                "INSERT INTO app.channel_return"
                " (team_id, store_id, channel_return_no, customer_order_no, order_id,"
                "  order_date, return_date, return_by_date, reason, channel_status,"
                "  refund_mode, qty, refund_amount, currency, customer, pulled_at)"
                " VALUES (:t, :s, :no, :cno,"
                "   (SELECT o.id FROM app.channel_order o WHERE o.store_id = :s"
                "     AND o.channel_order_no = :po ORDER BY o.order_date DESC LIMIT 1),"
                "   (SELECT o.order_date FROM app.channel_order o WHERE o.store_id = :s"
                "     AND o.channel_order_no = :po ORDER BY o.order_date DESC LIMIT 1),"
                "   :rd, :rbd, :rsn, :cs, :rm, :q, :amt, :cur, cast(:cust AS jsonb), now())"
                " ON CONFLICT (store_id, channel_return_no) DO UPDATE SET"
                "   customer_order_no = excluded.customer_order_no,"
                "   order_id = coalesce(excluded.order_id, app.channel_return.order_id),"
                "   order_date = coalesce(excluded.order_date, app.channel_return.order_date),"
                "   return_by_date = excluded.return_by_date, reason = excluded.reason,"
                "   channel_status = excluded.channel_status,"
                "   refund_mode = excluded.refund_mode, qty = excluded.qty,"
                "   refund_amount = excluded.refund_amount, currency = excluded.currency,"
                "   customer = excluded.customer, pulled_at = excluded.pulled_at"
                " RETURNING id"
            ),
            {
                "t": team_id,
                "s": store_id,
                "no": mapped["channel_return_no"],
                "cno": mapped["customer_order_no"],
                "po": mapped["purchase_order_no"],
                "rd": mapped["return_date"],
                "rbd": mapped["return_by_date"],
                "rsn": mapped["reason"],
                "cs": mapped["channel_status"],
                "rm": mapped["refund_mode"],
                "q": mapped["qty"],
                "amt": mapped["refund_amount"],
                "cur": mapped["currency"],
                "cust": json.dumps(mapped["customer"], ensure_ascii=False),
            },
        )
    ).one()
    return_id = int(row.id)

    # 既有行快照（事件 diff 用；RMA 行数很小，全取无压力）
    existing = {
        r["line_no"]: dict(r)
        for r in (
            await session.execute(
                text(
                    "SELECT line_no, line_status, refund_status, delivery_status,"
                    " refunded_qty, tracking_no"
                    " FROM app.channel_return_line WHERE return_id = :r"
                ),
                {"r": return_id},
            )
        ).mappings()
    }

    events = 0
    for ln in mapped["lines"]:
        await session.execute(
            text(
                "INSERT INTO app.channel_return_line"
                " (return_id, team_id, line_no, channel_sku, product_name, condition,"
                "  qty, refunded_qty, unit_price, line_status, refund_status,"
                "  delivery_status, return_method, return_reason, return_description,"
                "  status_time, purchase_order_no, carrier, tracking_no)"
                " VALUES (:r, :t, :ln, :sku, :pn, :cond, :q, :rq, :up, :st, :rs, :ds,"
                "         :rm, :rr, :rdesc, :stt, :po, :ca, :tn)"
                " ON CONFLICT (return_id, line_no) DO UPDATE SET"
                "   channel_sku = excluded.channel_sku, product_name = excluded.product_name,"
                "   condition = excluded.condition, qty = excluded.qty,"
                "   refunded_qty = excluded.refunded_qty, unit_price = excluded.unit_price,"
                "   line_status = excluded.line_status, refund_status = excluded.refund_status,"
                "   delivery_status = excluded.delivery_status,"
                "   return_method = excluded.return_method,"
                "   return_reason = excluded.return_reason,"
                "   return_description = excluded.return_description,"
                "   status_time = excluded.status_time,"
                "   purchase_order_no = excluded.purchase_order_no,"
                "   carrier = coalesce(excluded.carrier, app.channel_return_line.carrier),"
                "   tracking_no = coalesce(excluded.tracking_no,"
                "                          app.channel_return_line.tracking_no)"
            ),
            {
                "r": return_id,
                "t": team_id,
                "ln": ln["line_no"],
                "sku": ln["channel_sku"],
                "pn": ln["product_name"],
                "cond": ln["condition"],
                "q": ln["qty"],
                "rq": ln["refunded_qty"],
                "up": ln["unit_price"],
                "st": ln["line_status"],
                "rs": ln["refund_status"],
                "ds": ln["delivery_status"],
                "rm": ln["return_method"],
                "rr": ln["return_reason"],
                "rdesc": ln["return_description"],
                "stt": ln["status_time"],
                "po": ln["purchase_order_no"],
                "ca": ln["carrier"],
                "tn": ln["tracking_no"],
            },
        )
        old = existing.get(ln["line_no"])
        if old is None:
            continue  # 首次入库 = 行本身即快照，不记事件
        changes = {f: {"old": old[f], "new": ln[f]} for f in _TRACKED_FIELDS if old[f] != ln[f]}
        # coalesce 语义：渠道回 NULL 不覆盖本地 tracking_no，也就不算变化
        if "tracking_no" in changes and ln["tracking_no"] is None:
            del changes["tracking_no"]
        if changes:
            events += 1
            await session.execute(
                text(
                    "INSERT INTO app.channel_return_event (team_id, return_id, line_no, changes)"
                    " VALUES (:t, :r, :ln, cast(:c AS jsonb))"
                ),
                {
                    "t": team_id,
                    "r": return_id,
                    "ln": ln["line_no"],
                    "c": json.dumps(changes, ensure_ascii=False),
                },
            )
    return {"return_id": return_id, "events": events}


async def pull_store_returns(
    sessions: async_sessionmaker[AsyncSession], store_id: int, *, config: dict[str, Any]
) -> dict[str, Any]:
    """单店全量拉取。任何页失败即抛（sync_state 不写，下轮整店重拉）。"""
    page_limit = int(config.get("page_limit", 200))
    max_pages = int(config.get("max_pages", 50))
    replacement_info = bool(config.get("replacement_info", True))
    drop_ratio = float(config.get("drop_alert_ratio", 0.5))
    # 骤降告警的最小基线（低于此的店样本太小，比值告警无意义）
    drop_floor = int(config.get("drop_alert_floor", 20))

    started_at = datetime.now(UTC)
    async with system_tx(sessions) as session:
        team_id = (
            await session.execute(
                text("SELECT team_id FROM app.store WHERE id = :s"), {"s": store_id}
            )
        ).scalar_one()
        prev_lines = (
            await session.execute(
                text(
                    "SELECT (stats ->> 'lines')::int FROM app.sync_state"
                    " WHERE scope = :sc AND ref_id = :r"
                ),
                {"sc": SYNC_SCOPE, "r": store_id},
            )
        ).scalar_one_or_none()
        ctx, mode = await gateway.prepare(session, store_id)

    base_params: dict[str, Any] = {"limit": str(page_limit)}
    if replacement_info:
        base_params["replacementInfo"] = "true"
    params = dict(base_params)

    stats = {"returns": 0, "lines": 0, "events": 0, "pages": 0}
    for _ in range(max_pages):
        resp = await gateway.request_prepared(
            ctx, mode, "GET", "/v3/returns", endpoint_key="GET /v3/returns", params=params
        )
        if resp.dry_run:
            return {**stats, "dry_run": True}
        if resp.status != 200 or not isinstance(resp.data, dict):  # noqa: PLR2004
            page_no = stats["pages"] + 1
            raise RuntimeError(
                f"return_pull 店铺 {store_id} 失败：HTTP {resp.status}（页 {page_no}）"
            )
        stats["pages"] += 1
        orders = resp.data.get("returnOrders") or []
        async with system_tx(sessions) as session:
            for raw in orders:
                mapped = map_return(raw)
                outcome = await _upsert_return(
                    session, team_id=team_id, store_id=store_id, mapped=mapped
                )
                stats["returns"] += 1
                stats["lines"] += len(mapped["lines"])
                stats["events"] += outcome["events"]
        cursor = (resp.data.get("meta") or {}).get("nextCursor")
        if not cursor or not orders:
            break
        # nextCursor 是完整 URL：解析 query 合回参数（旧仓 fetch_walmart_returns.py:52）
        params = dict(base_params)
        params.update({k: v[0] for k, v in parse_qs(urlparse(str(cursor)).query).items()})
    else:
        stats["truncated"] = True
        log.warning("return_pull.truncated", store_id=store_id, max_pages=max_pages)

    async with system_tx(sessions) as session:
        # BR-AS-007：见单量骤降告警（upsert 不丢数据，只提示排查代理/渠道，不重跑）
        if prev_lines and prev_lines >= drop_floor and stats["lines"] < prev_lines * drop_ratio:
            await notify(
                session,
                team_id=team_id,
                severity="warn",
                category="aftersale",
                title=f"退货拉取量骤降：店铺 {store_id} 本轮 {stats['lines']} 行"
                f"（上轮 {prev_lines}）",
                body="BR-AS-007：先排查代理或 Walmart 服务问题，勿盲目重跑",
                object_type="store",
                object_id=str(store_id),
                dedupe_key=f"return_pull_drop:{store_id}",
            )
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
    totals = {"stores": len(store_ids), "ok": 0, "failed": 0, "returns": 0, "lines": 0,
              "events": 0}  # fmt: skip
    for sid in store_ids:
        try:
            st = await pull_store_returns(sessions, sid, config=config)
        except Exception as exc:
            totals["failed"] += 1
            log.warning("return_pull.store_failed", store_id=sid, error=str(exc))
            continue
        totals["ok"] += 1
        for k in ("returns", "lines", "events"):
            totals[k] += int(st.get(k, 0))
    if store_ids and totals["failed"] and not totals["ok"]:
        raise RuntimeError(f"return_pull 全部店铺失败：{totals['failed']}/{len(store_ids)}")
    return totals
