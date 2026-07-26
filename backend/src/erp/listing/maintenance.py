"""maintenance_task runner（R2-12 增量4a 最小档 + 增量4b end_date_renewal 通道）。

- P0-2 人工/半自动执行位：只认领 config.kinds 中已到期的 scheduled 任务，批量小步执行；
  SKIP LOCKED 抢占，beat 多实例安全。**默认人工档 kinds=[]**（D-Q13/29 三档，半自动需
  运营显式开）。
- delist：走既有三段式下架服务（配额消费 / outbox RETIRE_ITEM 真渠道写 / 状态机全链
  复用）；degraded 放行（渠道已 unpublished 的清理型下架，service 守卫已扩档）。
- end_date_renewal（增量4b）：走 renew_end_date（提交 MP_MAINTENANCE feed 延 endDate 让
  商品 republish，item_maintenance 命令 200 即成，degraded→live）。
- 结果回写任务行（done/failed + result/error），全程可追溯。
"""

import json
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from erp.core.db import system_tx
from erp.core.errors import BusinessError
from erp.listing import service as listing_service

log = structlog.get_logger()


async def run(sessions: async_sessionmaker, config: dict[str, Any]) -> dict[str, Any]:  # type: ignore[type-arg]
    batch = int(config.get("batch", 5))
    # 默认必须是空表（人工档）：D-Q13/29 三档口径、本文件 docstring、0037 种子
    # ('{"batch": 5, "kinds": []}') 三处一致。此处曾误写 ["delist"] fallback，构成 fail-open——
    # beat.py:129 / run_task.py:32 都是 `config or {}` 不补键，schedule.config 一旦丢 kinds 键
    # （0037 用 ON CONFLICT DO NOTHING，既有行不会被覆盖），runner 会直接发 RETIRE_ITEM
    # outbox 真渠道下架，绕过 D-Q65② 人工闸。空表=认领不到任何任务，fail-closed。
    kinds = [str(k) for k in config.get("kinds", [])]
    async with system_tx(sessions) as session:
        tasks = (
            await session.execute(
                text(
                    "UPDATE app.maintenance_task SET status = 'running', started_at = now()"
                    " WHERE id IN (SELECT id FROM app.maintenance_task"
                    "   WHERE status = 'scheduled' AND task_kind = ANY(cast(:k AS text[]))"
                    "     AND scheduled_at <= now()"
                    "   ORDER BY priority, scheduled_at LIMIT :n FOR UPDATE SKIP LOCKED)"
                    " RETURNING id, team_id, store_id, listing_id, task_kind"
                ),
                {"k": kinds, "n": batch},
            )
        ).all()
    stats = {"claimed": len(tasks), "done": 0, "failed": 0}
    for t in tasks:
        try:
            if t.task_kind == "delist":
                res = await listing_service.delist(
                    sessions, team_id=int(t.team_id), listing_id=int(t.listing_id), is_super=True
                )
                result = {"status": res.get("status")}
            elif t.task_kind == "end_date_renewal":
                res = await listing_service.renew_end_date(
                    sessions, team_id=int(t.team_id), listing_id=int(t.listing_id), is_super=True
                )
                result = {"status": res.get("status")}
            else:  # kinds 配置闸住，理论不达；达了也留痕不吞
                raise BusinessError(
                    "MAINT_KIND_UNSUPPORTED", f"任务类型 {t.task_kind} 执行通道未接"
                )
            async with system_tx(sessions) as session:
                await session.execute(
                    text(
                        "UPDATE app.maintenance_task SET status = 'done', finished_at = now(),"
                        " result = cast(:r AS jsonb) WHERE id = :id"
                    ),
                    {"r": _json(result), "id": t.id},
                )
            stats["done"] += 1
        except Exception as exc:
            async with system_tx(sessions) as session:
                await session.execute(
                    text(
                        "UPDATE app.maintenance_task SET status = 'failed', finished_at = now(),"
                        " error = :e WHERE id = :id"
                    ),
                    {"e": str(exc)[:500], "id": t.id},
                )
            stats["failed"] += 1
            log.warning("maintenance_run.task_failed", task_id=t.id, error=str(exc))
    return stats


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
