"""任务运行记账（task_run）+ 失败告警联动。

任何后台任务（beat 调度/队列 worker）必须经 run_tracked() 执行：
- 开始/结束落 app.task_run（事实源，Redis 之外的对账依据）；
- 失败 → 通知中心 critical（dedupe 按 task_code 每日一条，防风暴）——
  「任何静默失败都是缺陷」（00-conventions/草稿系统教训）。

fn 接 sessionmaker 自管事务（R2-04）：渠道类任务是三段式（tx → HTTP → tx），
若由本层包一个大事务，HTTP 期间会挂着空转连接/快照——RS-03「行锁不跨 HTTP」
纪律的连接版。短任务自己开一个 system_tx 即可。
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import text

from erp.core.db import system_tx
from erp.notify.service import notify

log = structlog.get_logger()


async def run_tracked(
    sessions: async_sessionmaker[AsyncSession],
    task_code: str,
    fn: Callable[[async_sessionmaker[AsyncSession]], Awaitable[dict[str, Any] | None]],
    *,
    team_id: int | None = None,
    schedule_id: int | None = None,
) -> bool:
    """执行任务并记账。返回是否成功。fn 自管事务；返回的 dict 记入 stats。"""
    async with system_tx(sessions) as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO app.task_run (task_code, schedule_id, team_id, status)"
                    " VALUES (:c, :sid, :t, 'running') RETURNING id, started_at"
                ),
                {"c": task_code, "sid": schedule_id, "t": team_id},
            )
        ).one()

    try:
        stats = await fn(sessions) or {}
        async with system_tx(sessions) as s:
            await s.execute(
                text(
                    "UPDATE app.task_run SET status='done', finished_at=now(),"
                    " stats=cast(:st AS jsonb) WHERE id=:id AND started_at=:sa"
                ),
                {"st": json.dumps(stats, ensure_ascii=False), "id": row.id, "sa": row.started_at},
            )
        return True
    except Exception as exc:
        log.error("task.failed", task_code=task_code, error=str(exc))
        async with system_tx(sessions) as s:
            await s.execute(
                text(
                    "UPDATE app.task_run SET status='failed', finished_at=now(), error=:e"
                    " WHERE id=:id AND started_at=:sa"
                ),
                {"e": str(exc)[:2000], "id": row.id, "sa": row.started_at},
            )
            await notify(
                s,
                team_id=team_id,
                severity="critical",
                category="task_fail",
                title=f"任务失败：{task_code}",
                body=str(exc)[:500],
                object_type="task_run",
                object_id=str(row.id),
                dedupe_key=f"task_fail:{task_code}",
            )
        return False
