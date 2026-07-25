"""maintenance_task runner 最小档（R2-12 增量4a；P0-2 人工/半自动执行位）。

- 只认领 config.kinds（默认 ['delist']）中已到期的 scheduled 任务，批量小步执行；
  SKIP LOCKED 抢占，beat 多实例安全。
- delist：走既有三段式下架服务（配额消费 / outbox RETIRE_ITEM 真渠道写 / 状态机
  全链复用）；degraded 状态放行（渠道已 unpublished 的清理型下架，service 侧守卫
  已扩档）。
- end_date_renewal：item_pull 会生成（A 过期类）但本档**不认领**——MP_MAINTENANCE
  feed 通道随增量4b（需扩 ck_cc_action / ck_feed_kind + spec 组装），在 kinds 配置
  加入前恒留在队列，人工可见。
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
    kinds = [str(k) for k in config.get("kinds", ["delist"])]
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
            else:  # kinds 配置闸住，理论不达；达了也留痕不吞
                raise BusinessError(
                    "MAINT_KIND_UNSUPPORTED", f"任务类型 {t.task_kind} 执行通道未接（增量4b）"
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
