"""任务运行记账（task_run）+ 失败告警联动。

任何后台任务（beat 调度/队列 worker）必须经 run_tracked() 执行：
- 开始/结束落 app.task_run（事实源，Redis 之外的对账依据）；
- 失败 → 通知中心 critical（dedupe 按 task_code 每日一条，防风暴）——
  「任何静默失败都是缺陷」（00-conventions/草稿系统教训）。
- timeout_s：任务级硬超时。beat 是单进程串行循环，一个挂起的渠道/DB 调用
  会顶死全部周期任务（2026-07-16 生产停摆教训）——超时取消是第二道防线
  （第一道是 gateway httpx 显式四相超时），超时按失败记账+告警，循环继续。

fn 接 sessionmaker 自管事务（R2-04）：渠道类任务是三段式（tx → HTTP → tx），
若由本层包一个大事务，HTTP 期间会挂着空转连接/快照——RS-03「行锁不跨 HTTP」
纪律的连接版。短任务自己开一个 system_tx 即可。取消发生在 fn 内部 await 点，
各段 system_tx 上下文管理器负责回滚，不留脏事务。
"""

import asyncio
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
    timeout_s: float | None = None,
) -> bool:
    """执行任务并记账。返回是否成功。fn 自管事务；返回的 dict 记入 stats。

    timeout_s 非空时对 fn 整体施加 asyncio 超时：到点取消、按失败收尾并告警。
    """
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
        if timeout_s is None:
            stats = await fn(sessions) or {}
        else:
            async with asyncio.timeout(timeout_s):
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
        err = (
            f"TASK_TIMEOUT：任务超过 {timeout_s:g}s 被强制中止（挂起的渠道/DB 调用已取消）"
            if isinstance(exc, TimeoutError) and timeout_s is not None
            else str(exc)
        )
        log.error("task.failed", task_code=task_code, error=err)
        async with system_tx(sessions) as s:
            await s.execute(
                text(
                    "UPDATE app.task_run SET status='failed', finished_at=now(), error=:e"
                    " WHERE id=:id AND started_at=:sa"
                ),
                {"e": err[:2000], "id": row.id, "sa": row.started_at},
            )
            await notify(
                s,
                team_id=team_id,
                severity="critical",
                category="task_fail",
                title=f"任务失败：{task_code}",
                body=err[:500],
                object_type="task_run",
                object_id=str(row.id),
                dedupe_key=f"task_fail:{task_code}",
            )
        return False


async def reclaim_stale_runs(
    sessions: async_sessionmaker[AsyncSession], *, older_than_s: float
) -> int:
    """beat 启动时回收崩溃遗留的 running 行（正常路径超时护栏已保证收尾）。

    只动明显超龄的行（> 任务超时上限），多副本并存时不会误杀在跑任务；
    时间窗下界 7 天配合按月分区裁剪，避免全表扫描。
    """
    async with system_tx(sessions) as s:
        rows = (
            await s.execute(
                text(
                    "UPDATE app.task_run SET status='failed', finished_at=now(),"
                    " error='STALE_RUNNING：beat 崩溃遗留，启动时回收'"
                    " WHERE status='running'"
                    "   AND started_at < now() - make_interval(secs => :cut)"
                    "   AND started_at > now() - interval '7 days'"
                    " RETURNING id"
                ),
                {"cut": older_than_s},
            )
        ).all()
    if rows:
        log.warning("task.stale_running_reclaimed", count=len(rows))
    return len(rows)
