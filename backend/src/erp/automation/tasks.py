"""beat 周期任务注册表（R2-04）。

契约：async (sessions, config) -> stats dict。
- sessions = async_sessionmaker，任务自管事务：短任务自开一个 system_tx；
  渠道任务走三段式（tx → HTTP → tx），期间不得有挂起的外层事务/连接；
- config = schedule.config（jsonb），任务级业务参数的运营编辑面（零硬编码铁律）；
- 返回值记入 task_run.stats；抛异常 = task_run failed + notify critical。

显式注册（与 listing.APPLIERS 同构）：schedule.code 出现未注册值由 beat 记失败，
不静默跳过（09-platform「任何静默失败都是缺陷」）。

渠道类任务全部复用既有函数（poll_feed / verify_back / drain / resolve_verify），
不新开渠道调用面——网关模式与限流语义自动继承（不绕过 walmart_client 语义铁律）。
"""

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.aftersale import pull as return_pull_service
from erp.channel import outbox
from erp.channel import service as channel_service
from erp.channel.gateway import gateway
from erp.core import idempotency
from erp.core.db import ctx_tx, system_tx
from erp.core.errors import BusinessError
from erp.listing import service as listing_service
from erp.notify.service import notify
from erp.order import pull as order_pull_service
from erp.order import ship as order_ship_service
from erp.pricing import service as pricing_service
from erp.scrape import service as scrape_service
from erp.tools.drain_channel_outbox import drain

log = structlog.get_logger()

Sessions = async_sessionmaker[AsyncSession]
TaskFn = Callable[[Sessions, dict[str, Any]], Awaitable[dict[str, Any]]]


# ── 维护类（单 system_tx）──


async def partition_maintain(sessions: Sessions, config: dict[str, Any]) -> dict[str, Any]:
    """为 app 下全部 RANGE 分区父表预建未来分区（00-conventions：beat 负责并有告警）。

    父表从系统目录动态发现——新增分区表自动纳入维护，无需改本任务。
    """
    months_ahead = int(config.get("months_ahead", 3))
    async with system_tx(sessions) as session:
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


async def api_idempotency_sweep(sessions: Sessions, config: dict[str, Any]) -> dict[str, Any]:
    """api_idempotency 全表按龄清扫——run_idempotent 键内惰性清理的全表版（RS-03b 尾账）。

    阈值与惰性清理共用同一配置源 system_config['api.idempotency']（TTL 契约 24h），
    保证两条清理路径永不口径分叉。
    """
    async with system_tx(sessions) as session:
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


async def llm_cache_lru(sessions: Sessions, config: dict[str, Any]) -> dict[str, Any]:
    """llm_cache 淘汰：闲置逐出 + 容量上限（最久未命中先出；从未命中按 created_at）。"""
    max_idle_days = int(config.get("max_idle_days", 90))
    max_rows = int(config.get("max_rows", 200_000))
    async with system_tx(sessions) as session:
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


async def scrape_reclaim(sessions: Sessions, config: dict[str, Any]) -> dict[str, Any]:
    """采集断连回收兜底 + 停摆可见性（R2-04 验收；2026-07-16 卡死教训扩展）。

    复用 scrape.service.reclaim（sync/pull 内联版同一实现，幂等 UPDATE 并发安全）；
    阈值仍走 system_config scrape.heartbeat_timeout_s / scrape.task_timeout_min。
    扩展三件（管线原本无任何采集侧告警，「任何静默失败都是缺陷」）：
    - 收口结算兜底：submit_result 漏结算/reclaim 判死后的 running job 补收口；
    - 无 worker 告警：有待执行任务却 0 在线节点超宽限期 → critical（运营在 UI
      看不到节点健康，通知中心是唯一信号面）；
    - 零进展告警：开放 job 超时限仍 0 成 0 败 → warn（逐单 dedupe）。
    """
    no_worker_grace_min = int(config.get("no_worker_grace_minutes", 10))
    stall_alert_min = int(config.get("stall_alert_minutes", 60))
    async with system_tx(sessions) as session:
        reclaimed = await scrape_service.reclaim(session)
        settled = (
            await session.execute(
                text(
                    "UPDATE app.scrape_job SET"
                    " status = CASE WHEN done_tasks = 0 THEN 'failed'"
                    "               WHEN failed_tasks > 0 THEN 'partial'"
                    "               ELSE 'done' END,"
                    " finished_at = now()"
                    " WHERE status = 'running' AND done_tasks + failed_tasks >= total_tasks"
                    " RETURNING id"
                )
            )
        ).all()
        backlog = (
            (
                await session.execute(
                    text(
                        "SELECT count(*) AS n,"
                        "  coalesce(min(t.created_at)"
                        "           < now() - make_interval(mins => :grace), false) AS overdue"
                        " FROM app.scrape_task t"
                        " JOIN app.scrape_job j ON j.id = t.job_id"
                        " WHERE t.status = 'pending' AND j.status IN ('pending','running')"
                    ),
                    {"grace": no_worker_grace_min},
                )
            )
            .mappings()
            .one()
        )
        online = (
            await session.execute(
                text("SELECT count(*) FROM app.worker_node WHERE status = 'online'")
            )
        ).scalar_one()
        no_worker = bool(backlog["n"] and not online and backlog["overdue"])
        if no_worker:
            await notify(
                session,
                team_id=None,
                severity="critical",
                category="scrape_stall",
                title="采集停摆：无在线采集节点",
                body=(
                    f"{backlog['n']} 个任务待执行但 0 个 worker 在线（最老已等待超过"
                    f" {no_worker_grace_min} 分钟）。检查 scraper 容器是否启动/崩溃循环"
                    "（enroll token、代理配置），见 docs 采集 runbook。"
                ),
                object_type="worker_node",
                object_id="none_online",
                dedupe_key="scrape_no_worker",
            )
        stalled = (
            await session.execute(
                text(
                    "SELECT id, team_id FROM app.scrape_job"
                    " WHERE status IN ('pending','running') AND finished_at IS NULL"
                    "   AND done_tasks = 0 AND failed_tasks = 0"
                    "   AND created_at < now() - make_interval(mins => :stall)"
                ),
                {"stall": stall_alert_min},
            )
        ).all()
        for job in stalled:
            await notify(
                session,
                team_id=job.team_id,
                severity="warn",
                category="scrape_stall",
                title=f"采集工单 #{job.id} 零进展",
                body=(
                    f"建单已超 {stall_alert_min} 分钟仍 0 成 0 败——worker 未在跑"
                    "或目标全部受阻，需人工介入（详见采集作业页）。"
                ),
                object_type="scrape_job",
                object_id=str(job.id),
                dedupe_key=f"scrape_stall:{job.id}",
            )
    return {
        "reclaimed": reclaimed,
        "settled": len(settled),
        "no_worker_alert": int(no_worker),
        "stalled_alerts": len(stalled),
    }


# ── 渠道类（三段式，复用 listing/outbox 既有函数）──


async def feed_poll(sessions: Sessions, config: dict[str, Any]) -> dict[str, Any]:
    """feed 自动轮询（R2-04 验收①：A152 提交后无人工点查自动轮询回写）。

    复用 poll_feed 三段式（tx2 FOR UPDATE 复核——与人工点查并发安全，先完成者为准）。
    dry_run 档轮询无副作用，仅记 last_polled_at 作节流簿记，防同批 feed 每 tick 重扫。
    """
    batch = int(config.get("batch", 20))
    min_interval_s = int(config.get("min_interval_s", 300))
    stuck_attempts = int(config.get("stuck_alert_attempts", 48))
    async with system_tx(sessions) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, team_id, poll_attempts FROM app.feed"
                    " WHERE status IN ('submitted','processing')"
                    "   AND (last_polled_at IS NULL"
                    "        OR last_polled_at < now() - make_interval(secs => :iv))"
                    " ORDER BY last_polled_at ASC NULLS FIRST, id LIMIT :n"
                ),
                {"iv": min_interval_s, "n": batch},
            )
        ).all()
    polled = errors = skipped = 0
    for row in rows:
        try:
            result = await listing_service.poll_feed(sessions, row.id, is_super=True)
        except BusinessError as exc:
            if exc.code == "FEED_NOT_POLLABLE":  # 与人工点查竞态：已被轮到终态
                skipped += 1
                continue
            errors += 1
            log.warning("beat.feed_poll_rejected", feed_id=row.id, code=exc.code)
            continue
        except Exception as exc:
            errors += 1
            log.warning("beat.feed_poll_error", feed_id=row.id, error=str(exc))
            continue
        polled += 1
        if result.get("dry_run"):
            async with system_tx(sessions) as session:
                await session.execute(
                    text("UPDATE app.feed SET last_polled_at = now() WHERE id = :f"),
                    {"f": row.id},
                )
        if row.poll_attempts + 1 >= stuck_attempts:
            async with system_tx(sessions) as session:
                await notify(
                    session,
                    team_id=row.team_id,
                    severity="warn",
                    category="listing_feed",
                    title=f"Feed #{row.id} 轮询 {row.poll_attempts + 1} 次仍未终态",
                    body="渠道处理异常缓慢或卡死；请到 feed 明细页人工核对",
                    object_type="feed",
                    object_id=str(row.id),
                    dedupe_key=f"feed_stuck:{row.id}",
                )
    if rows and errors and not polled and not skipped:
        raise RuntimeError(f"feed_poll 全批失败：{errors}/{len(rows)}（见日志）")
    return {"scanned": len(rows), "polled": polled, "skipped": skipped, "errors": errors}


async def feed_verify_back(sessions: Sessions, config: dict[str, Any]) -> dict[str, Any]:
    """verify_pending feed 周期对账（adopt-or-lost，绝不重发——RS-03b 对账入口周期化）。

    min_age_s：刚被 sweep 的 feed 缓一缓再对账——渠道近程列表尚不可见时会被
    保守判 lost（语义安全但无谓抖动：配额返还 + listing 重回 queued）。
    """
    batch = int(config.get("batch", 10))
    min_age_s = int(config.get("min_age_s", 600))
    async with system_tx(sessions) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id FROM app.feed WHERE status = 'verify_pending'"
                    "   AND updated_at < now() - make_interval(secs => :age)"
                    " ORDER BY id LIMIT :n"
                ),
                {"age": min_age_s, "n": batch},
            )
        ).all()
    adopted = lost = errors = skipped = 0
    for row in rows:
        try:
            result = await listing_service.verify_back(sessions, row.id, is_super=True)
        except BusinessError as exc:
            if exc.code == "FEED_NOT_VERIFY_PENDING":  # 与人工对账竞态
                skipped += 1
                continue
            errors += 1
            log.warning("beat.verify_back_rejected", feed_id=row.id, code=exc.code)
            continue
        except Exception as exc:
            errors += 1
            log.warning("beat.verify_back_error", feed_id=row.id, error=str(exc))
            continue
        if result.get("dry_run"):
            skipped += 1
            async with system_tx(sessions) as session:  # 触发 touch 推迟下次扫描
                await session.execute(
                    text("UPDATE app.feed SET last_polled_at = now() WHERE id = :f"),
                    {"f": row.id},
                )
        elif result.get("feed_status") == "lost":
            lost += 1
        else:
            adopted += 1
    return {
        "scanned": len(rows),
        "adopted": adopted,
        "lost": lost,
        "skipped": skipped,
        "errors": errors,
    }


async def channel_outbox_drain(sessions: Sessions, config: dict[str, Any]) -> dict[str, Any]:
    """outbox 排空周期化（RS-03b 尾账：崩溃遗留 pending 补执行 + 过期 inflight 懒清扫）。"""
    return await drain(sweep_only=bool(config.get("sweep_only", False)),
                       limit=int(config.get("limit", 100)))  # fmt: skip


async def retire_recon(sessions: Sessions, config: dict[str, Any]) -> dict[str, Any]:
    """item_retire verify_pending 渠道对账（RS-03b 尾账）——绝不重发，商品实况为权威。

    - GET /v3/items/{sku} 已 404 / lifecycle RETIRED → 命令 succeeded + listing→delisted；
    - 商品仍在架 且 命令滞龄 > grace_s → 判提交未生效：failed 归位
      （配额返还 + delist_pending→live，镜像 _apply_item_retire 明确拒绝分支）；
    - 仍在架未过 grace → 维持 verify_pending 等渠道处理（该店车道继续背压，有意 fail-closed）。
    """
    batch = int(config.get("batch", 10))
    min_age_s = int(config.get("min_age_s", 300))
    grace_s = int(config.get("grace_s", 3600))
    async with system_tx(sessions) as session:
        cmds = (
            await session.execute(
                text(
                    "SELECT c.id, c.team_id, c.store_id, c.object_id,"
                    "       extract(epoch FROM now() - c.updated_at) AS age_s,"
                    "       l.channel_sku, l.status AS listing_status"
                    " FROM app.channel_command c JOIN app.listing l ON l.id = c.object_id"
                    " WHERE c.action = 'item_retire' AND c.status = 'verify_pending'"
                    "   AND c.updated_at < now() - make_interval(secs => :age)"
                    " ORDER BY c.id LIMIT :n"
                ),
                {"age": min_age_s, "n": batch},
            )
        ).all()
    resolved_ok = resolved_failed = pending = errors = 0
    for cmd in cmds:
        try:
            outcome = await _recon_one_retire(sessions, cmd, grace_s=grace_s)
        except Exception as exc:
            errors += 1
            log.warning("beat.retire_recon_error", command_id=cmd.id, error=str(exc))
            continue
        if outcome == "succeeded":
            resolved_ok += 1
        elif outcome == "failed":
            resolved_failed += 1
        else:
            pending += 1
    return {
        "scanned": len(cmds),
        "succeeded": resolved_ok,
        "failed": resolved_failed,
        "pending": pending,
        "errors": errors,
    }


async def _recon_one_retire(sessions: Sessions, cmd: Any, *, grace_s: int) -> str:
    """单命令三段式对账。返回 succeeded / failed / pending。"""
    # tx1：prepare（读凭证/模式，无行锁跨 HTTP）
    async with ctx_tx(sessions, team_id=None, is_super=True) as session:
        ctx, mode = await gateway.prepare(session, int(cmd.store_id))
    resp = await gateway.request_prepared(
        ctx, mode, "GET", f"/v3/items/{cmd.channel_sku}", endpoint_key="GET /v3/items/{id}"
    )
    if resp.dry_run or resp.status is None:
        return "pending"  # dry_run 档或传输失败：不臆断，下轮再看

    item_retired = resp.status == 404  # noqa: PLR2004
    if resp.status == 200 and isinstance(resp.data, dict):  # noqa: PLR2004
        items = resp.data.get("ItemResponse") or []
        first = items[0] if isinstance(items, list) and items else {}
        item_retired = str(first.get("lifecycleStatus") or "").upper() == "RETIRED"

    # tx2：重锁复核归位（resolve_verify 自带 status='verify_pending' 守卫）
    async with ctx_tx(sessions, team_id=None, is_super=True) as session:
        listing = await listing_service._load_listing(session, int(cmd.object_id))
        if item_retired:
            if await outbox.resolve_verify(
                session,
                command_id=int(cmd.id),
                status="succeeded",
                result={"via": "retire_recon", "http_status": resp.status},
            ):
                if listing["status"] == "delist_pending":
                    await listing_service.transition(
                        session, listing, "delisted", reason_code="retire_recon_confirmed"
                    )
                return "succeeded"
            return "pending"  # 已被人工/并发归位
        if float(cmd.age_s) > grace_s:
            if await outbox.resolve_verify(
                session,
                command_id=int(cmd.id),
                status="failed",
                error_code="RETIRE_NOT_EFFECTIVE",
                result={"via": "retire_recon", "http_status": resp.status},
            ):
                await channel_service.release_quota(session, int(cmd.store_id), "listing_delete")
                if listing["status"] == "delist_pending":
                    await listing_service.transition(
                        session, listing, "live", reason_code="RETIRE_NOT_EFFECTIVE"
                    )
                return "failed"
            return "pending"
        return "pending"  # 未过 grace：等渠道把 RETIRE feed 处理完


async def price_recon(sessions: Sessions, config: dict[str, Any]) -> dict[str, Any]:
    """price_push verify_pending 渠道对账（R2-06 增量3，镜像 retire_recon）——绝不重发。

    权威 = 渠道商品实价（GET /v3/items/{sku} 的 price.amount）：
    - 渠道价 == 目标价（±$0.01，含浮点补偿）→ succeeded + 补两段式回填
      （current_price + 价史 + 清 pending_price）；
    - 任何确定态不匹配（200 价不等 / 404 / 403 / 5xx 等明确 HTTP 状态）且命令
      滞龄 > grace_s → 判提交未生效：failed + notify + 清 pending_price 复位
      （价格不回填，人工核对后重推=价格链再走一次 push_price）——与 retire_recon
      同等收敛保证：确定态最终必归位，不许 verify_pending 永久堵死店铺车道；
    - 真暂态（dry_run 档、传输失败无状态）→ 留待下轮（车道继续背压，fail-closed）。
    """
    batch = int(config.get("batch", 10))
    min_age_s = int(config.get("min_age_s", 300))
    grace_s = int(config.get("grace_s", 3600))
    async with system_tx(sessions) as session:
        cmds = (
            await session.execute(
                text(
                    "SELECT c.id, c.team_id, c.store_id, c.object_id, c.payload, c.created_by,"
                    "       extract(epoch FROM now() - c.updated_at) AS age_s,"
                    "       l.channel_sku"
                    " FROM app.channel_command c JOIN app.listing l ON l.id = c.object_id"
                    " WHERE c.action = 'price_push' AND c.status = 'verify_pending'"
                    "   AND c.updated_at < now() - make_interval(secs => :age)"
                    " ORDER BY c.id LIMIT :n"
                ),
                {"age": min_age_s, "n": batch},
            )
        ).all()
    stats = {"scanned": len(cmds), "succeeded": 0, "failed": 0, "pending": 0, "errors": 0}
    for cmd in cmds:
        try:
            outcome = await _recon_one_price(sessions, cmd, grace_s=grace_s)
        except Exception as exc:
            stats["errors"] += 1
            log.warning("beat.price_recon_error", command_id=cmd.id, error=str(exc))
            continue
        stats[outcome] += 1
    return stats


def _channel_item_price(data: dict[str, Any]) -> float | None:
    """GET /v3/items/{sku} 响应 → 渠道现价（ItemResponse[0].price.amount）。"""
    items = data.get("ItemResponse") or []
    first = items[0] if isinstance(items, list) and items else {}
    price_obj = first.get("price") if isinstance(first, dict) else None
    if not isinstance(price_obj, dict):
        return None
    try:
        return float(price_obj["amount"])
    except (KeyError, TypeError, ValueError):
        return None


# 对账容差 ±$0.01（docstring 契约）：裸浮点 <= 会把恰好 1 美分差误判不匹配
# （abs(59.99-60.0)=0.01000…5 > 0.01）——同 engine._EPS 口径补偿二进制误差
_PRICE_RECON_TOL = 0.01 + 1e-9


async def _recon_one_price(sessions: Sessions, cmd: Any, *, grace_s: int) -> str:
    """单命令三段式对账。返回 succeeded / failed / pending。"""
    # tx1：prepare（读凭证/模式，无行锁跨 HTTP）
    async with ctx_tx(sessions, team_id=None, is_super=True) as session:
        ctx, mode = await gateway.prepare(session, int(cmd.store_id))
    # endpoint_key 同 retire_recon（限流表无 "GET /v3/items/{id}" 键 → 落 _default 120/min）
    resp = await gateway.request_prepared(
        ctx, mode, "GET", f"/v3/items/{cmd.channel_sku}", endpoint_key="GET /v3/items/{id}"
    )
    if resp.dry_run or resp.status is None:
        return "pending"  # dry_run 档/传输失败：不臆断，下轮再看
    # 确定态：200 取实价对比；404/403/5xx 等一律 channel_price=None（渠道价未确认），
    # 与「价不等」同走 grace 判败通道——保证 verify_pending 最终收敛（评审发现 2）
    channel_price = (
        _channel_item_price(resp.data)
        if resp.status == 200 and isinstance(resp.data, dict)  # noqa: PLR2004
        else None
    )
    target = float(cmd.payload["new_price"])

    # tx2：重锁复核归位（resolve_verify 自带 status='verify_pending' 守卫）
    async with ctx_tx(sessions, team_id=None, is_super=True) as session:
        if channel_price is not None and abs(channel_price - target) <= _PRICE_RECON_TOL:
            if await outbox.resolve_verify(
                session,
                command_id=int(cmd.id),
                status="succeeded",
                result={"via": "price_recon", "channel_price": channel_price},
            ):
                # 渠道实价确认 → 补两段式回填（与 applier 同一落账口径）
                await pricing_service.apply_price_backfill(
                    session,
                    listing_id=int(cmd.object_id),
                    team_id=int(cmd.team_id),
                    payload=cmd.payload,
                    detail_extra={"via": "price_recon", "channel_price": channel_price},
                    actor_id=cmd.created_by,
                )
                return "succeeded"
            return "pending"  # 已被人工/并发归位
        if float(cmd.age_s) > grace_s:
            if await outbox.resolve_verify(
                session,
                command_id=int(cmd.id),
                status="failed",
                error_code="PRICE_PUSH_NOT_EFFECTIVE",
                result={
                    "via": "price_recon",
                    "channel_price": channel_price,
                    "http_status": resp.status,
                },
            ):
                # 失败半程复位（BR-LC-011）：清在途标记，价格不回填
                await pricing_service.clear_pending_price(session, [int(cmd.object_id)])
                await notify(
                    session,
                    team_id=int(cmd.team_id),
                    severity="warn",
                    category="pricing",
                    title=f"改价结果未确认（listing #{cmd.object_id}）——已判未生效",
                    body=(
                        f"渠道现价 {channel_price}（HTTP {resp.status}）≠ 目标 {target}"
                        "（超对账宽限）；价格未回填，核对后在定价页重推"
                    ),
                    object_type="listing",
                    object_id=str(cmd.object_id),
                    dedupe_key=f"price_push:{cmd.object_id}",
                )
                return "failed"
            return "pending"
        return "pending"  # 未过 grace：等渠道价格生效（渠道侧可能有分钟级延迟）


# ── 告警类（单 system_tx：聚合 + notify，不动业务数据）──


async def gtin_watermark(sessions: Sessions, config: dict[str, Any]) -> dict[str, Any]:
    """GTIN 池水位告警（D-Q39/03-catalog：按 (team, kind) 统计 free 占比 → notification）。

    阈值 team_config > system_config > 默认（warn <15% / critical <5%）；
    只告警不动池——补号动作永远是人（导入通道）。dedupe 24h=每日至多一条。
    """
    default_warn = float(config.get("warn_pct", 15))
    default_critical = float(config.get("critical_pct", 5))
    async with system_tx(sessions) as session:
        groups = (
            await session.execute(
                text(
                    "SELECT team_id, gtin_kind, count(*) AS total,"
                    " count(*) FILTER (WHERE status = 'free') AS free"
                    " FROM app.gtin_pool GROUP BY team_id, gtin_kind ORDER BY 1, 2"
                )
            )
        ).all()
        overrides: dict[tuple[int, str], float] = {
            (r.team_id, r.key): float(r.value)
            for r in await session.execute(
                text(
                    "SELECT team_id, key, value FROM app.team_config"
                    " WHERE key IN ('gtin.warn_pct', 'gtin.critical_pct')"
                )
            )
        }
        system_over: dict[str, float] = {
            r.key: float(r.value)
            for r in await session.execute(
                text(
                    "SELECT key, value FROM app.system_config"
                    " WHERE key IN ('gtin.warn_pct', 'gtin.critical_pct')"
                )
            )
        }
        warned = critical = 0
        for g in groups:
            warn_pct = overrides.get(
                (g.team_id, "gtin.warn_pct"), system_over.get("gtin.warn_pct", default_warn)
            )
            critical_pct = overrides.get(
                (g.team_id, "gtin.critical_pct"),
                system_over.get("gtin.critical_pct", default_critical),
            )
            free_pct = g.free * 100.0 / g.total
            if free_pct >= warn_pct:
                continue
            severity: Any = "critical" if free_pct < critical_pct else "warn"
            if severity == "critical":
                critical += 1
            else:
                warned += 1
            await notify(
                session,
                team_id=g.team_id,
                severity=severity,
                category="gtin_watermark",
                title=f"GTIN 池水位告警：{g.gtin_kind} 剩余 {free_pct:.1f}%",
                body=f"可用 {g.free}/{g.total}；请尽快导入补号（上架管理 → GTIN 池导入）",
                object_type="gtin_pool",
                object_id=g.gtin_kind,
                dedupe_key=f"gtin_watermark:{g.team_id}:{g.gtin_kind}",
            )
    return {"groups": len(groups), "warned": warned, "critical": critical}


async def llm_budget_check(sessions: Sessions, config: dict[str, Any]) -> dict[str, Any]:
    """LLM 日预算闸·告警版（05-audit §预算闸：聚合 llm_usage_log → 超限 critical）。

    不自动停，人决策（降级建议入通知正文）。事前原子预留是 RS-08 加固单，
    本任务只做 schedule 底座上的事后聚合口径。预算键：team_config llm_budget_daily_usd。
    """
    tz = str(config.get("timezone", "Asia/Shanghai"))
    async with system_tx(sessions) as session:
        budgets = (
            await session.execute(
                text(
                    "SELECT team_id, value FROM app.team_config WHERE key = 'llm_budget_daily_usd'"
                )
            )
        ).all()
        over = 0
        for b in budgets:
            budget_usd = float(b.value)
            if budget_usd <= 0:
                continue
            spent = (
                await session.execute(
                    text(
                        "SELECT coalesce(sum(cost_usd), 0) FROM app.llm_usage_log"
                        " WHERE team_id = :t AND occurred_at >="
                        "   date_trunc('day', now() AT TIME ZONE :tz) AT TIME ZONE :tz"
                    ),
                    {"t": b.team_id, "tz": tz},
                )
            ).scalar_one()
            if float(spent) < budget_usd:
                continue
            over += 1
            await notify(
                session,
                team_id=b.team_id,
                severity="critical",
                category="budget",
                title=f"LLM 日预算超限：${float(spent):.2f} / ${budget_usd:.2f}",
                body="建议：临时下调审核档位（L4/L3→L1/L2）或暂停批量映射后再继续"
                "——不自动停，由人决策（D-Q19/05-audit 预算闸）",
                object_type="team",
                object_id=str(b.team_id),
                dedupe_key=f"llm_budget:{b.team_id}",
            )
    return {"teams_with_budget": len(budgets), "over_budget": over}


async def order_pull(sessions: Sessions, config: dict[str, Any]) -> dict[str, Any]:
    """渠道订单增量拉取（R2-05：15min/店；协议与实战语义见 erp.order.pull 模块头注）。"""
    return await order_pull_service.run(sessions, config)


async def return_pull(sessions: Sessions, config: dict[str, Any]) -> dict[str, Any]:
    """渠道退货全量拉取（R2-07：08:00/日；协议与实战语义见 erp.aftersale.pull 模块头注）。"""
    return await return_pull_service.run(sessions, config)


async def ship_recon(sessions: Sessions, config: dict[str, Any]) -> dict[str, Any]:
    """order_ack/order_ship verify_pending 渠道对账（镜像 retire_recon；绝不重发）。

    权威 = 渠道订单实况（GET /v3/orders/{po}）：
    - order_ack：任一行已离开 Created → succeeded；仍 Created 且超 grace → failed
      （解车道，后续 ship 在渠道端暴露真实错误）；
    - order_ship：回传单各行渠道侧已 Shipped → succeeded + 业务落账
      （apply_ship_success，与 applier 共用）；未 Shipped 且超 grace → failed +
      shipment failed + notify（人工重推=换幂等键再发）。
    """
    batch = int(config.get("batch", 10))
    min_age_s = int(config.get("min_age_s", 300))
    grace_s = int(config.get("grace_s", 3600))
    async with system_tx(sessions) as session:
        cmds = (
            await session.execute(
                text(
                    "SELECT c.id, c.action, c.team_id, c.store_id, c.object_id,"
                    "       extract(epoch FROM now() - c.updated_at) AS age_s"
                    " FROM app.channel_command c"
                    " WHERE c.action IN ('order_ack','order_ship')"
                    "   AND c.status = 'verify_pending'"
                    "   AND c.updated_at < now() - make_interval(secs => :age)"
                    " ORDER BY c.id LIMIT :n"
                ),
                {"age": min_age_s, "n": batch},
            )
        ).all()
    stats = {"scanned": len(cmds), "succeeded": 0, "failed": 0, "pending": 0, "errors": 0}
    for cmd in cmds:
        try:
            outcome = await _recon_one_order_cmd(sessions, cmd, grace_s=grace_s)
        except Exception as exc:
            stats["errors"] += 1
            log.warning("beat.ship_recon_error", command_id=cmd.id, error=str(exc))
            continue
        stats[outcome] += 1
    return stats


async def _recon_one_order_cmd(  # noqa: PLR0911 归位分支=动作×渠道实况形态
    sessions: Sessions, cmd: Any, *, grace_s: int
) -> str:
    """单命令三段式对账。返回 succeeded / failed / pending。"""
    async with ctx_tx(sessions, team_id=None, is_super=True) as session:
        if cmd.action == "order_ship":
            shipment = await order_ship_service._load_shipment(session, int(cmd.object_id))
            order_id = int(shipment["order_id"])
            want_lines = [str(ln["line_no"]) for ln in shipment["lines"]]
        else:
            order_id, want_lines = int(cmd.object_id), []
        po_no = (
            await session.execute(
                text("SELECT channel_order_no FROM app.channel_order WHERE id = :o"),
                {"o": order_id},
            )
        ).scalar_one()
        ctx, mode = await gateway.prepare(session, int(cmd.store_id))
    resp = await gateway.request_prepared(
        ctx, mode, "GET", f"/v3/orders/{po_no}", endpoint_key="GET /v3/orders"
    )
    if resp.dry_run or resp.status != 200 or not isinstance(resp.data, dict):  # noqa: PLR2004
        return "pending"  # dry_run 档/传输失败/渠道侧异常：不臆断，下轮再看
    order_obj = resp.data.get("order") or resp.data
    statuses = {
        str(ln.get("lineNumber")): order_pull_service._line_status_raw(ln)
        for ln in (order_obj.get("orderLines") or {}).get("orderLine") or []
    }

    async with ctx_tx(sessions, team_id=None, is_super=True) as session:
        if cmd.action == "order_ack":
            acked = any(s != "Created" for s in statuses.values())
            if acked:
                if await outbox.resolve_verify(
                    session, command_id=int(cmd.id), status="succeeded",
                    result={"via": "ship_recon"},
                ):  # fmt: skip
                    return "succeeded"
                return "pending"
            if float(cmd.age_s) > grace_s and await outbox.resolve_verify(
                session, command_id=int(cmd.id), status="failed", error_code="ACK_NOT_EFFECTIVE"
            ):
                return "failed"
            return "pending"
        # order_ship：回传单各行渠道侧均已 Shipped 才算确认
        shipped = want_lines and all(
            statuses.get(line_no) in ("Shipped", "Delivered") for line_no in want_lines
        )
        if shipped:
            if await outbox.resolve_verify(
                session, command_id=int(cmd.id), status="succeeded", result={"via": "ship_recon"}
            ):
                await order_ship_service.apply_ship_success(session, shipment_id=int(cmd.object_id))
                return "succeeded"
            return "pending"
        if float(cmd.age_s) > grace_s and await outbox.resolve_verify(
            session, command_id=int(cmd.id), status="failed", error_code="SHIP_NOT_EFFECTIVE"
        ):
            await session.execute(
                text("UPDATE app.shipment SET push_status = 'failed' WHERE id = :s"),
                {"s": int(cmd.object_id)},
            )
            await notify(
                session,
                team_id=int(cmd.team_id),
                severity="critical",
                category="order_flag",
                title=f"发货回传结果未确认（订单 #{order_id}）——已判未生效",
                body="渠道侧行状态未变更为 Shipped；核对后在订单页重新发货",
                object_type="shipment",
                object_id=str(cmd.object_id),
                dedupe_key=f"ship_failed:{cmd.object_id}",
            )
            return "failed"
        return "pending"


TASKS: dict[str, TaskFn] = {
    "partition_maintain": partition_maintain,
    "api_idempotency_sweep": api_idempotency_sweep,
    "llm_cache_lru": llm_cache_lru,
    "scrape_reclaim": scrape_reclaim,
    "feed_poll": feed_poll,
    "feed_verify_back": feed_verify_back,
    "channel_outbox_drain": channel_outbox_drain,
    "retire_recon": retire_recon,
    "price_recon": price_recon,
    "gtin_watermark": gtin_watermark,
    "llm_budget_check": llm_budget_check,
    "order_pull": order_pull,
    "ship_recon": ship_recon,
    "return_pull": return_pull,
}
