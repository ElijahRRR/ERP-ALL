"""beat 任务单跑工具（演练/运维用）。

用途：不等 cron 到点，人工一次性触发某个已注册的 beat 任务并打印 stats JSON。
config 从 app.schedule 同源读取（与 beat 派发一致，零旁路配置）；不落 task_run
记账（区别于周期派发——单跑属人工操作，审计线索在操作者 shell 历史与输出）。

  python -m erp.tools.run_task suspension_reminder
  python -m erp.tools.run_task variant_group_sync
"""

import argparse
import asyncio
import json
import sys

from sqlalchemy import text

from erp.automation.tasks import TASKS
from erp.core.db import get_session_factory, system_tx


async def _run(code: str) -> int:
    fn = TASKS.get(code)
    if fn is None:
        print(f"未知任务：{code}；可选：{', '.join(sorted(TASKS))}", file=sys.stderr)
        return 2
    sessions = get_session_factory()
    async with system_tx(sessions) as s:
        cfg = (
            await s.execute(text("SELECT config FROM app.schedule WHERE code = :c"), {"c": code})
        ).scalar_one_or_none()
    stats = await fn(sessions, cfg or {})
    print(json.dumps(stats, ensure_ascii=False, default=str))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="beat 任务单跑（config 与 beat 同源读 app.schedule）")
    p.add_argument("task_code", help="TASKS 注册表中的任务码，如 suspension_reminder")
    args = p.parse_args()
    return asyncio.run(_run(args.task_code))


if __name__ == "__main__":
    sys.exit(main())
