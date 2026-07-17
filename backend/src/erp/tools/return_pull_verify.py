"""退货拉取对账 harness（R2-07 验收①：A152 真实 returns 拉取与渠道对账一致）。

以渠道为准：重新 GET 全量退货单（只读，BR-AS-001 无时间过滤），与 DB
channel_return/channel_return_line 逐 RMA 比对，输出差异清单。
部署机在 return_pull 至少跑过一轮后执行：

  docker compose -f infra/docker-compose.yml exec api \\
    python -m erp.tools.return_pull_verify --store <store_id>

支持 --pull-first：对账前先跑一轮该店 return_pull（写路径），免等 08:00 beat。

比对口径（与 erp.aftersale.pull.map_return 共用映射，口径永不分叉）：
  RMA 集合（缺/多）· 头级聚合状态 · 行数 · 总退款金额（±0.01）· 行级退款状态。
退出码：0=一致；1=有差异；2=运行错误。
"""

import argparse
import asyncio
import sys
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import text

from erp.aftersale.pull import map_return, pull_store_returns
from erp.channel.gateway import gateway
from erp.core.db import get_session_factory, system_tx

_MAX_PAGES = 50


async def _fetch_channel(store_id: int) -> dict[str, dict[str, Any]]:
    sessions = get_session_factory()
    async with system_tx(sessions) as s:
        ctx, mode = await gateway.prepare(s, store_id)
    base_params = {"limit": "200", "replacementInfo": "true"}
    params: dict[str, Any] = dict(base_params)
    out: dict[str, dict[str, Any]] = {}
    for _ in range(_MAX_PAGES):
        resp = await gateway.request_prepared(
            ctx, mode, "GET", "/v3/returns", endpoint_key="GET /v3/returns", params=params
        )
        if resp.dry_run:
            raise RuntimeError("dry_run 档无法对账——需 live_test/live")
        if resp.status != 200 or not isinstance(resp.data, dict):  # noqa: PLR2004
            raise RuntimeError(f"渠道拉取失败 HTTP {resp.status}")
        orders = resp.data.get("returnOrders") or []
        for raw in orders:
            mapped = map_return(raw)
            out[mapped["channel_return_no"]] = mapped
        cursor = (resp.data.get("meta") or {}).get("nextCursor")
        if not cursor or not orders:
            break
        params = dict(base_params)
        params.update({k: v[0] for k, v in parse_qs(urlparse(str(cursor)).query).items()})
    return out


async def verify(store_id: int, *, pull_first: bool) -> dict[str, Any]:
    sessions = get_session_factory()
    if pull_first:
        stats = await pull_store_returns(sessions, store_id, config={})
        print(f"return_pull 先行：{stats}")
    channel = await _fetch_channel(store_id)
    async with system_tx(sessions) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT r.channel_return_no, r.channel_status, r.refund_amount,"
                    " (SELECT count(*) FROM app.channel_return_line l"
                    "   WHERE l.return_id = r.id) AS line_rows,"
                    " (SELECT array_agg(l.refund_status ORDER BY l.line_no)"
                    "   FROM app.channel_return_line l WHERE l.return_id = r.id)"
                    "   AS line_refund_statuses"
                    " FROM app.channel_return r WHERE r.store_id = :s"
                ),
                {"s": store_id},
            )
        ).all()
    db = {r.channel_return_no: r for r in rows}
    diffs: list[str] = []
    for rma, mapped in channel.items():
        row = db.get(rma)
        if row is None:
            diffs.append(f"缺单：渠道有 {rma}（{mapped['channel_status']}）DB 无")
            continue
        if row.channel_status != mapped["channel_status"]:
            diffs.append(
                f"状态差：{rma} 渠道={mapped['channel_status']} DB={row.channel_status}"
                "（拉取后渠道又变属正常，重跑 return_pull 后复核）"
            )
        want_amount = mapped["refund_amount"]
        have_amount = float(row.refund_amount) if row.refund_amount is not None else None
        if (want_amount is None) != (have_amount is None) or (
            want_amount is not None
            and have_amount is not None
            and abs(have_amount - want_amount) > 0.01  # noqa: PLR2004
        ):
            diffs.append(f"金额差：{rma} 渠道={want_amount} DB={have_amount}")
        if int(row.line_rows) != len(mapped["lines"]):
            diffs.append(f"行数差：{rma} 渠道={len(mapped['lines'])} DB={row.line_rows}")
        else:
            want_rs = [ln["refund_status"] for ln in
                       sorted(mapped["lines"], key=lambda x: x["line_no"])]  # fmt: skip
            have_rs = list(row.line_refund_statuses or [])
            if want_rs != have_rs:
                diffs.append(f"行退款状态差：{rma} 渠道={want_rs} DB={have_rs}")
    for rma in set(db) - set(channel):
        diffs.append(f"多单：DB 有 {rma} 渠道未见（渠道归档/删除属可解释项，BR-AS-006 保留历史）")
    return {"channel": len(channel), "db": len(db), "diffs": diffs}


def main() -> int:
    p = argparse.ArgumentParser(description="退货拉取对账（渠道为准，只读；--pull-first 先拉一轮）")
    p.add_argument("--store", type=int, required=True, help="店铺 id")
    p.add_argument("--pull-first", action="store_true", help="对账前先跑一轮该店 return_pull")
    args = p.parse_args()
    try:
        report = asyncio.run(verify(args.store, pull_first=args.pull_first))
    except Exception as e:
        print(f"对账运行失败：{e}", file=sys.stderr)
        return 2
    print(f"渠道 RMA 数 {report['channel']} / DB RMA 数 {report['db']}")
    if not report["diffs"]:
        print("对账一致 ✅")
        return 0
    print(f"差异 {len(report['diffs'])} 项：")
    for d in report["diffs"]:
        print(f"  - {d}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
