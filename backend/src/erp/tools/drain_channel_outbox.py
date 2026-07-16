"""channel outbox 排空工具（RS-03b）。

用途：进程在 tx1 后崩溃遗留的 pending 命令补执行、过期 inflight 懒清扫。
请求内三段式已覆盖正常路径——本工具供部署机人工/beat（R2-04）周期调用。

  python -m erp.tools.drain_channel_outbox            # 排空可领命令
  python -m erp.tools.drain_channel_outbox --sweep-only  # 只清扫不执行

verify_pending 命令不在此处理（对账入口：POST /feeds/{id}/verify-back，
或 R2-04 维护任务）；同店车道被其挡住属有意背压（fail-closed）。
"""

import argparse
import asyncio
import sys

import structlog

from erp.channel import outbox
from erp.core.db import get_session_factory, system_tx
from erp.listing.service import APPLIERS as LISTING_APPLIERS
from erp.order.ship import APPLIERS as ORDER_APPLIERS
from erp.pricing.service import APPLIERS as PRICING_APPLIERS

# 全量 action 归位注册表（feed_submit/item_retire + order_ack/order_ship + price_push）
APPLIERS = {**LISTING_APPLIERS, **ORDER_APPLIERS, **PRICING_APPLIERS}

log = structlog.get_logger()


async def drain(*, sweep_only: bool = False, limit: int = 100) -> dict[str, int]:
    sessions = get_session_factory()
    stats = {"swept": 0, "executed": 0, "blocked": 0}
    async with system_tx(sessions) as s:
        stats["swept"] = len(await outbox.sweep_expired(s))
    if sweep_only:
        return stats
    # 单店受阻（限流闸拒 → 归还 pending / 车道 verify_pending 背压）只封该店本轮，
    # 继续排其他店铺——否则全局最低 id 恒被同一受限店占住，其余店被跨店饿死
    # （R2-06 增量3 评审发现 3）。
    blocked_stores: set[int] = set()
    for _ in range(limit):
        async with system_tx(sessions) as s:
            pick = await outbox.pick_next(s, exclude_store_ids=sorted(blocked_stores))
            cmd = await outbox.command_state(s, pick["id"]) if pick is not None else None
        if pick is None or cmd is None:
            break
        outcome = await outbox.execute_command(
            sessions, pick["id"], team_id=None, is_super=True, applier=APPLIERS[cmd["action"]]
        )
        status = outcome.get("command_status") or outcome.get("status")
        if status == "pending":
            stats["blocked"] += 1  # 该店本轮受阻（限流待恢复/对账背压）——跳店继续
            blocked_stores.add(int(pick["store_id"]))
            continue
        stats["executed"] += 1
        log.info("outbox.drained", command_id=pick["id"], outcome=outcome)
    return stats


def main() -> int:
    p = argparse.ArgumentParser(description="channel outbox 排空（补执行 pending / 清扫过期）")
    p.add_argument("--sweep-only", action="store_true", help="只做过期 inflight 懒清扫")
    p.add_argument("--limit", type=int, default=100, help="本次最多执行命令数")
    args = p.parse_args()
    stats = asyncio.run(drain(sweep_only=args.sweep_only, limit=args.limit))
    print(f"清扫 {stats['swept']} / 执行 {stats['executed']} / 车道受阻 {stats['blocked']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
