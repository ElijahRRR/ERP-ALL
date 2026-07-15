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

from erp.channel import outbox
from erp.core.db import get_session_factory, system_tx
from erp.listing.service import APPLIERS


async def drain(*, sweep_only: bool = False, limit: int = 100) -> dict[str, int]:
    sessions = get_session_factory()
    stats = {"swept": 0, "executed": 0, "blocked": 0}
    async with system_tx(sessions) as s:
        stats["swept"] = len(await outbox.sweep_expired(s))
    if sweep_only:
        return stats
    for _ in range(limit):
        async with system_tx(sessions) as s:
            cid = await outbox.pick_next(s)
            cmd = await outbox.command_state(s, cid) if cid is not None else None
        if cid is None or cmd is None:
            break
        outcome = await outbox.execute_command(
            sessions, cid, team_id=None, is_super=True, applier=APPLIERS[cmd["action"]]
        )
        if outcome.get("command_status") == "pending":
            stats["blocked"] += 1  # 车道被挡（verify_pending 背压）——等待对账归位
            break
        stats["executed"] += 1
        print(f"command {cid}: {outcome}")
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
