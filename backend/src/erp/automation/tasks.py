"""beat 周期任务注册表（R2-04）。

契约：async (session, config) -> stats dict。
- session 来自 run_tracked 的 system_tx（系统上下文，绕 RLS——beat 是系统角色）；
- config = schedule.config（jsonb），任务级业务参数的运营编辑面（零硬编码铁律）；
- 返回值记入 task_run.stats；抛异常 = task_run failed + notify critical。

显式注册（与 listing.APPLIERS 同构）：schedule.code 出现未注册值由 beat 记失败，
不静默跳过（09-platform「任何静默失败都是缺陷」）。
"""

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core import idempotency
from erp.scrape import service as scrape_service

TaskFn = Callable[[AsyncSession, dict[str, Any]], Awaitable[dict[str, Any]]]


async def partition_maintain(session: AsyncSession, config: dict[str, Any]) -> dict[str, Any]:
    """为 app 下全部 RANGE 分区父表预建未来分区（00-conventions：beat 负责并有告警）。

    父表从系统目录动态发现——新增分区表自动纳入维护，无需改本任务。
    """
    months_ahead = int(config.get("months_ahead", 3))
    parents = [
        row.parent
        for row in await session.execute(
            text(
                "SELECT p.partrelid::regclass::text AS parent"
                " FROM pg_partitioned_table p"
                " JOIN pg_class c ON c.oid = p.partrelid"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'app' ORDER BY 1"
            )
        )
    ]
    created = 0
    for parent in parents:
        created += (
            await session.execute(
                text("SELECT app.ensure_month_partitions(cast(:p AS regclass), :m)"),
                {"p": f"app.{parent}" if "." not in parent else parent, "m": months_ahead},
            )
        ).scalar_one()
    return {"parents": len(parents), "created": created}


async def api_idempotency_sweep(session: AsyncSession, config: dict[str, Any]) -> dict[str, Any]:
    """api_idempotency 全表按龄清扫——run_idempotent 键内惰性清理的全表版（RS-03b 尾账）。

    阈值与惰性清理共用同一配置源 system_config['api.idempotency']（TTL 契约 24h），
    保证两条清理路径永不口径分叉。
    """
    cfg = await idempotency._config(session)  # 共用惰性清理的配置读取，防口径分叉
    swept = (
        await session.execute(
            text(
                "DELETE FROM app.api_idempotency"
                " WHERE created_at < now() - make_interval(hours => :ttl)"
                "    OR (response IS NULL"
                "        AND created_at < now() - make_interval(mins => :stale))"
                " RETURNING 1"
            ),
            {"ttl": int(cfg["ttl_hours"]), "stale": int(cfg["stale_minutes"])},
        )
    ).all()
    return {"swept": len(swept)}


async def llm_cache_lru(session: AsyncSession, config: dict[str, Any]) -> dict[str, Any]:
    """llm_cache 淘汰：闲置逐出 + 容量上限（最久未命中先出；从未命中按 created_at）。"""
    max_idle_days = int(config.get("max_idle_days", 90))
    max_rows = int(config.get("max_rows", 200_000))
    idle = (
        await session.execute(
            text(
                "DELETE FROM app.llm_cache"
                " WHERE coalesce(last_hit_at, created_at)"
                "       < now() - make_interval(days => :d)"
                " RETURNING 1"
            ),
            {"d": max_idle_days},
        )
    ).all()
    over = (
        await session.execute(
            text(
                "DELETE FROM app.llm_cache WHERE cache_key IN ("
                " SELECT cache_key FROM app.llm_cache"
                " ORDER BY coalesce(last_hit_at, created_at) DESC"
                " OFFSET :cap) RETURNING 1"
            ),
            {"cap": max_rows},
        )
    ).all()
    return {"idle_evicted": len(idle), "cap_evicted": len(over)}


async def scrape_reclaim(session: AsyncSession, config: dict[str, Any]) -> dict[str, Any]:
    """采集断连回收兜底（R2-04 验收：模拟断连自动回收，不依赖任何 worker 存活）。

    复用 scrape.service.reclaim（sync/pull 内联版同一实现，幂等 UPDATE 并发安全）；
    阈值仍走 system_config scrape.heartbeat_timeout_s / scrape.task_timeout_min。
    """
    reclaimed = await scrape_service.reclaim(session)
    return {"reclaimed": reclaimed}


TASKS: dict[str, TaskFn] = {
    "partition_maintain": partition_maintain,
    "api_idempotency_sweep": api_idempotency_sweep,
    "llm_cache_lru": llm_cache_lru,
    "scrape_reclaim": scrape_reclaim,
}
