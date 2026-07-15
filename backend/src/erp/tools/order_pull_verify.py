"""订单拉取对账 harness（R2-05 验收 L1：真实订单只读拉取入库对账一致）。

以渠道为准：重新 GET 同窗口全量订单（只读），与 DB channel_order/order_line 逐单比对，
输出差异清单。部署机在 order_pull 至少跑过一轮后执行：

  docker compose -f infra/docker-compose.yml exec api \\
    python -m erp.tools.order_pull_verify --store <store_id> [--days 30]

比对口径（与 erp.order.pull.map_order 共用映射，口径永不分叉）：
  单号集合（缺/多）· 渠道状态 · 行数 · 订单金额（±0.01）· 行状态。
退出码：0=一致；1=有差异；2=运行错误。
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from erp.channel.gateway import gateway
from erp.core.db import get_session_factory, system_tx
from erp.order.pull import map_order


async def _fetch_channel(store_id: int, days: int) -> dict[str, dict[str, Any]]:
    sessions = get_session_factory()
    async with system_tx(sessions) as s:
        ctx, mode = await gateway.prepare(s, store_id)
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params: dict[str, Any] | None = {"createdStartDate": since, "limit": "200"}
    path = "/v3/orders"
    out: dict[str, dict[str, Any]] = {}
    for _ in range(50):
        resp = await gateway.request_prepared(
            ctx, mode, "GET", path, endpoint_key="GET /v3/orders", params=params
        )
        if resp.dry_run:
            raise RuntimeError("dry_run 档无法对账——需 live_test/live")
        if resp.status != 200 or not isinstance(resp.data, dict):  # noqa: PLR2004
            raise RuntimeError(f"渠道拉取失败 HTTP {resp.status}")
        elements = (resp.data.get("list") or {}).get("elements") or {}
        for raw in elements.get("order") or []:
            mapped = map_order(raw)
            out[mapped["channel_order_no"]] = mapped
        cursor = ((resp.data.get("list") or {}).get("meta") or {}).get("nextCursor")
        if not cursor or "hasMoreElements=false" in str(cursor):
            break
        path = f"/v3/orders{cursor}"
        params = None
    return out


async def verify(store_id: int, days: int) -> dict[str, Any]:
    channel = await _fetch_channel(store_id, days)
    sessions = get_session_factory()
    async with system_tx(sessions) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT o.channel_order_no, o.channel_status, o.order_total, o.item_count,"
                    " (SELECT count(*) FROM app.order_line l WHERE l.order_id = o.id"
                    "   AND l.order_date = o.order_date) AS line_rows"
                    " FROM app.channel_order o WHERE o.store_id = :s"
                    "   AND o.order_date >= now() - make_interval(days => :d)"
                ),
                {"s": store_id, "d": days},
            )
        ).all()
    db = {r.channel_order_no: r for r in rows}
    diffs: list[str] = []
    for po, mapped in channel.items():
        row = db.get(po)
        if row is None:
            diffs.append(f"缺单：渠道有 {po}（{mapped['channel_status']}）DB 无")
            continue
        if row.channel_status != mapped["channel_status"]:
            diffs.append(
                f"状态差：{po} 渠道={mapped['channel_status']} DB={row.channel_status}"
                "（可能为窗口内正常滞后，重跑 order_pull 后复核）"
            )
        if abs(float(row.order_total) - float(mapped["order_total"])) > 0.01:  # noqa: PLR2004
            diffs.append(
                f"金额差：{po} 渠道={mapped['order_total']} DB={float(row.order_total)}"
            )
        if int(row.line_rows) != mapped["item_count"]:
            diffs.append(f"行数差：{po} 渠道={mapped['item_count']} DB={row.line_rows}")
    for po in set(db) - set(channel):
        diffs.append(f"多单：DB 有 {po} 渠道窗口内未见（窗口边界/渠道 180 天上限属正常）")
    return {"channel": len(channel), "db": len(db), "diffs": diffs}


def main() -> int:
    p = argparse.ArgumentParser(description="订单拉取对账（渠道为准，只读）")
    p.add_argument("--store", type=int, required=True, help="店铺 id")
    p.add_argument("--days", type=int, default=30, help="对账窗口天数（默认 30）")
    args = p.parse_args()
    try:
        report = asyncio.run(verify(args.store, args.days))
    except Exception as e:
        print(f"对账运行失败：{e}", file=sys.stderr)
        return 2
    print(f"渠道单数 {report['channel']} / DB 单数 {report['db']}")
    if not report["diffs"]:
        print("对账一致 ✅")
        return 0
    print(f"差异 {len(report['diffs'])} 项：")
    for d in report["diffs"]:
        print(f"  - {d}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
