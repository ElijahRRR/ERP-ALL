"""beat 调度循环（R2-04：schedule 表驱动，specs/001 §09-platform schedule）。

- 单进程 asyncio；tick 间隔 system_config beat.tick_seconds（默认 30s，运行期热调）。
- 原子领取：单条 UPDATE 带 next_run_at 乐观校验（IS NOT DISTINCT FROM 领取时所见值）
  ——多副本 / 与运营手改表并发时谁先更新谁执行，输者 0 行静默让位。
- next_run_at 为 NULL（新种子/首次启动）只按 cron 初始化不执行——防重启风暴。
- cron 解析 cronsim + 表内 timezone（zoneinfo）；解析失败按 1h 兜底推迟并记
  task_run failed（notify critical）——坏 cron 必须可见，但不许拖死循环。
- 每次执行经 run_tracked 记账（task_run + 失败告警，09-platform「任何静默失败都是缺陷」）。
- 任务在 tick 内串行执行：现役任务皆短平快（批量上限在各自 config），并行化待有实测需要再上。
- 任务级硬超时（2026-07-16 生产停摆教训）：串行循环里一个挂起的渠道/DB 调用会顶死全部
  周期任务，故每个任务经 asyncio.timeout 包裹——schedule.config.task_timeout_seconds >
  system_config beat.task_timeout_seconds > 默认 900s。超时按失败记账+告警，循环继续。
- 启动时回收崩溃遗留的 task_run running 行（reclaim_stale_runs）——正常路径由超时护栏收尾。
"""

import asyncio
import contextlib
import signal
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from cronsim import CronSim
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.automation.task_runner import reclaim_stale_runs, run_tracked
from erp.automation.tasks import TASKS, TaskFn
from erp.core.config_service import get_config_service, run_invalidation_subscriber
from erp.core.db import get_session_factory, system_tx

log = structlog.get_logger()

_DEFAULT_TICK_SECONDS = 30.0
_DEFAULT_TASK_TIMEOUT_SECONDS = 900.0
_PARSE_FAIL_BACKOFF = timedelta(hours=1)


def next_fire(cron: str, timezone: str, after: datetime) -> datetime:
    """按表内时区算下一次触发（cronsim 对 aware datetime 含 DST 语义）。"""
    return next(CronSim(cron, after.astimezone(ZoneInfo(timezone))))


def _bind(fn: TaskFn, config: dict[str, Any]) -> Any:
    async def run(sessions: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
        return await fn(sessions, config)

    return run


def _raiser(message: str) -> Any:
    async def run(_sessions: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
        raise RuntimeError(message)

    return run


async def tick(sessions: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """一轮调度：领取全部到期 schedule 并执行。返回本轮计数（供日志/测试断言）。"""
    stats = {"initialized": 0, "executed": 0, "failed": 0, "lost_race": 0}
    async with system_tx(sessions) as s:
        due = (
            await s.execute(
                text(
                    "SELECT id, code, cron, timezone, config, next_run_at"
                    " FROM app.schedule"
                    " WHERE enabled AND (next_run_at IS NULL OR next_run_at <= now())"
                    " ORDER BY id"
                )
            )
        ).all()
        default_timeout = _DEFAULT_TASK_TIMEOUT_SECONDS
        if due:
            row = (
                await s.execute(
                    text(
                        "SELECT value #>> '{}' FROM app.system_config"
                        " WHERE key = 'beat.task_timeout_seconds'"
                    )
                )
            ).first()
            if row and row[0]:
                default_timeout = float(row[0])

    for row in due:
        now = datetime.now(UTC)
        parse_error: str | None = None
        try:
            nxt = next_fire(row.cron, row.timezone, now)
        except Exception as exc:
            nxt = now + _PARSE_FAIL_BACKOFF
            parse_error = f"cron/timezone 无法解析（1h 后重试）：{exc}"
        is_run = row.next_run_at is not None and parse_error is None

        async with system_tx(sessions) as s:
            claimed = (
                await s.execute(
                    text(
                        "UPDATE app.schedule SET next_run_at = :nxt,"
                        " last_run_at = CASE WHEN cast(:run AS boolean)"
                        "                    THEN now() ELSE last_run_at END"
                        " WHERE id = :id AND enabled"
                        "   AND next_run_at IS NOT DISTINCT FROM :seen"
                        " RETURNING id"
                    ),
                    {"nxt": nxt, "run": is_run, "id": row.id, "seen": row.next_run_at},
                )
            ).first()
        if claimed is None:  # 另一副本/运营手改抢先——让位
            stats["lost_race"] += 1
            continue

        if parse_error is not None:
            await run_tracked(sessions, row.code, _raiser(parse_error), schedule_id=row.id)
            stats["failed"] += 1
        elif not is_run:
            stats["initialized"] += 1
        elif (fn := TASKS.get(row.code)) is None:
            await run_tracked(
                sessions,
                row.code,
                _raiser(f"schedule code 未注册任务函数：{row.code}"),
                schedule_id=row.id,
            )
            stats["failed"] += 1
        else:
            config = row.config or {}
            timeout = float(config.get("task_timeout_seconds") or default_timeout)
            ok = await run_tracked(
                sessions, row.code, _bind(fn, config), schedule_id=row.id, timeout_s=timeout
            )
            stats["executed" if ok else "failed"] += 1
    return stats


async def _tick_seconds(sessions: async_sessionmaker[AsyncSession]) -> float:
    async with system_tx(sessions) as s:
        row = (
            await s.execute(
                text("SELECT value #>> '{}' FROM app.system_config WHERE key = 'beat.tick_seconds'")
            )
        ).first()
    return float(row[0]) if row and row[0] else _DEFAULT_TICK_SECONDS


async def run_forever() -> None:
    sessions = get_session_factory()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    # 配置失效订阅（R2-04 pubsub；beat 与 api 对等各持一份进程缓存）
    subscriber = asyncio.create_task(run_invalidation_subscriber(get_config_service()))
    log.info("beat.start")
    with contextlib.suppress(Exception):  # 回收失败不阻启动（DB 未就绪等下一 tick）
        await reclaim_stale_runs(sessions, older_than_s=2 * _DEFAULT_TASK_TIMEOUT_SECONDS)
    try:
        while not stop.is_set():
            try:
                stats = await tick(sessions)
                if any(stats.values()):
                    log.info("beat.tick", **stats)
            except Exception as exc:  # DB 闪断等：beat 不退出，下一 tick 重试
                log.error("beat.tick_error", error=str(exc))
            try:
                interval = await _tick_seconds(sessions)
            except Exception:
                interval = _DEFAULT_TICK_SECONDS
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval)
    finally:
        subscriber.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await subscriber
    log.info("beat.stop")


def main() -> int:
    asyncio.run(run_forever())
    return 0
