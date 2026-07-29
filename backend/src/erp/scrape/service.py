"""采集域服务：作业/任务生命周期 + worker 拨入协议 + product 入库。

移植源：amazon-scraper-v3 server（考古对照表见 .agent/evidence/R1-09/archaeology.md）。
语义保真要点：
- 租约协议：scrape_task.attempt 兼作 v3 的 lease_epoch——每次派发 +1，回传必须携带
  匹配的 attempt；租约不符（迟到结果/被回收后旧 worker 回传）→ 拒收并标 stale。
- 领任务：v3 的 SQLite 写锁 + BEGIN IMMEDIATE 在 PG 换成 FOR UPDATE SKIP LOCKED，
  多 worker 并发领取天然不互踩。
- 回收：心跳超时节点 → offline；其任务与硬超时任务一并归还 pending（等同 v3
  reclaim_dead_worker_tasks 的合成一条 SQL 原则）。
- product 入库：ON CONFLICT (team_id, source_channel, source_ref) DO UPDATE 刷新
  title/price_snapshot/attrs，**不重置 status**（specs/001 §03 去重协议，D-Q31）。

节点认证：node_key + 高熵机器令牌（sha256 摘要入库，注册时一次性下发）。
注册需 system_config `scrape.worker_enroll_token`（Owner 设置；未设置=注册关闭）。
"""

import hashlib
import hmac
import json
import secrets
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.catalog import variant as variant_svc
from erp.core.errors import BusinessError

log = structlog.get_logger()

R1_SUPPORTED_KINDS = ("product_detail",)


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── 作业 ──


async def create_job(
    session: AsyncSession,
    *,
    team_id: int,
    source: str,
    job_kind: str,
    input_: dict[str, Any],
    priority: int = 100,
    requested_by: int | None = None,
) -> dict[str, Any]:
    """建作业并展开任务。R1 仅支持 product_detail（input.targets = ASIN 列表）。"""
    if job_kind not in R1_SUPPORTED_KINDS:
        raise BusinessError(
            "SCRAPE_KIND_UNSUPPORTED",
            f"作业类型 {job_kind} 尚未开放（R1 仅 product_detail）",
            {"supported": list(R1_SUPPORTED_KINDS)},
        )
    targets_raw = input_.get("targets")
    if not isinstance(targets_raw, list) or not targets_raw:
        raise BusinessError("SCRAPE_INPUT_INVALID", "input.targets 必须是非空列表")
    targets = list(dict.fromkeys(str(t).strip() for t in targets_raw if str(t).strip()))
    if not targets:
        raise BusinessError("SCRAPE_INPUT_INVALID", "input.targets 无有效目标")

    # ── 墓碑预筛（R2-14 14a-3）──
    #
    # ⚠️ **这一层是优化，不是保证。** 真正的保证在 `product_upsert` 的入库前检查：
    # 本次预筛之后、任务落地之前的那段时间里商品可能才被删，那样的任务照样会跑到入库点。
    # 预筛在这里的价值是省掉真实抓取（代理带宽、时间、渠道风控暴露），并让运营在建作业
    # 当场就看见「有几个被墓碑挡下」，而不是抓完一轮才发现白抓。
    #
    # 两处判读并存本身有漂移风险（同 core/automation.py 模块头注说的那类）。故此处
    # **不复制判定逻辑**，与入库点共用同一个查询函数 `_deleted_refs`。
    blocked = await _deleted_refs(session, team_id=team_id, source=source, refs=targets)
    if blocked:
        targets = [t for t in targets if t not in blocked]
        log.info(
            "scrape.targets_blocked_by_tombstone",
            team_id=team_id,
            source=source,
            blocked=len(blocked),
        )
    if not targets:
        # 不建空作业：0 任务的作业永远等不到 `_finish_job_if_complete`，会以 pending
        # 僵在列表里，看起来像卡住的采集——而真相是没什么可采。
        raise BusinessError(
            "SCRAPE_ALL_TARGETS_DELETED",
            "全部目标都已被删除并留有墓碑，不会重新入库",
            {"blocked": sorted(blocked)},
        )
    if blocked:
        input_ = {**input_, "targets": targets, "skipped_deleted": sorted(blocked)}

    row = (
        await session.execute(
            text(
                "INSERT INTO app.scrape_job"
                " (team_id, source, job_kind, input, priority, total_tasks,"
                "  requested_by, created_by)"
                " VALUES (:t, :s, :k, cast(:i AS jsonb), :p, :n, :u, :u)"
                " RETURNING id, status, total_tasks, created_at"
            ),
            {
                "t": team_id,
                "s": source,
                "k": job_kind,
                "i": _json(input_),
                "p": priority,
                "n": len(targets),
                "u": requested_by,
            },
        )
    ).one()
    for ref in targets:
        await session.execute(
            text("INSERT INTO app.scrape_task (job_id, team_id, target_ref) VALUES (:j, :t, :r)"),
            {"j": row.id, "t": team_id, "r": ref},
        )
    return {"id": row.id, "status": row.status, "total_tasks": row.total_tasks}


async def cancel_job(session: AsyncSession, job_id: int) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "UPDATE app.scrape_job SET status = 'cancelled', finished_at = now()"
                " WHERE id = :j AND status IN ('pending','running')"
                " RETURNING id"
            ),
            {"j": job_id},
        )
    ).one_or_none()
    if row is None:
        raise BusinessError("SCRAPE_JOB_NOT_CANCELLABLE", "作业不存在或已进入终态")
    # 未派发任务直接判死；在途任务的迟到结果照常入库，但作业状态保持 cancelled
    await session.execute(
        text(
            "UPDATE app.scrape_task SET status = 'dead', finished_at = now(),"
            " error = 'job cancelled'"
            " WHERE job_id = :j AND status = 'pending'"
        ),
        {"j": job_id},
    )
    return {"id": job_id, "status": "cancelled"}


# ── 节点注册 / 认证 / 心跳 ──


async def register_node(
    session: AsyncSession,
    *,
    node_key: str,
    kind: str,
    enroll_token: str,
    version: str | None = None,
    capacity: int = 1,
) -> dict[str, Any]:
    """首次注册发放机器令牌（仅此一次返回明文）；重复注册需持有效令牌（见 sync）。"""
    expected = (
        await session.execute(
            text(
                "SELECT value #>> '{}' FROM app.system_config"
                " WHERE key = 'scrape.worker_enroll_token'"
            )
        )
    ).scalar_one_or_none()
    if not expected:
        raise BusinessError("SCRAPE_ENROLL_DISABLED", "节点注册未开放（未配置注册令牌）")
    if not hmac.compare_digest(enroll_token, expected):
        raise BusinessError("SCRAPE_ENROLL_TOKEN_INVALID", "注册令牌不正确")

    exists = (
        await session.execute(
            text("SELECT id FROM app.worker_node WHERE node_key = :k"), {"k": node_key}
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise BusinessError("SCRAPE_NODE_EXISTS", "节点已注册；令牌遗失需管理员删除节点后重新注册")

    token = secrets.token_urlsafe(32)
    node_id = (
        await session.execute(
            text(
                "INSERT INTO app.worker_node"
                " (node_key, token_hash, kind, version, capacity, status, last_heartbeat_at)"
                " VALUES (:k, :h, :kind, :v, :c, 'online', now())"
                " RETURNING id"
            ),
            {
                "k": node_key,
                "h": _token_digest(token),
                "kind": kind,
                "v": version,
                "c": capacity,
            },
        )
    ).scalar_one()
    return {"node_id": node_id, "node_token": token}


async def authenticate_node(session: AsyncSession, node_key: str, token: str) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, node_key, token_hash, kind, status, capacity"
                    " FROM app.worker_node WHERE node_key = :k"
                ),
                {"k": node_key},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or not hmac.compare_digest(row["token_hash"], _token_digest(token)):
        raise BusinessError("SCRAPE_NODE_AUTH", "节点认证失败")
    return dict(row)


async def sync_node(
    session: AsyncSession,
    node: dict[str, Any],
    *,
    version: str | None = None,
    metrics: dict[str, Any] | None = None,
    window_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """心跳 + 指标上报 + 设置下发（v3 /api/worker/sync 语义）。顺带跑一轮回收。"""
    await session.execute(
        text(
            "UPDATE app.worker_node SET last_heartbeat_at = now(),"
            " status = CASE WHEN status = 'draining' THEN 'draining' ELSE 'online' END,"
            " version = COALESCE(:v, version),"
            " stats = stats || cast(:m AS jsonb),"
            " window_state = cast(:w AS jsonb)"
            " WHERE id = :id"
        ),
        {
            "v": version,
            "m": _json(metrics or {}),
            "w": _json(window_state or {}),
            "id": node["id"],
        },
    )
    await reclaim(session)
    settings_row = (
        await session.execute(
            text("SELECT value FROM app.system_config WHERE key = 'scrape.worker_settings'")
        )
    ).scalar_one_or_none()
    return {"settings": settings_row or {}, "draining": node["status"] == "draining"}


# ── 任务派发 / 回传（租约协议核心）──


async def pull_tasks(
    session: AsyncSession, node: dict[str, Any], *, count: int = 10
) -> list[dict[str, Any]]:
    if node["status"] == "draining":
        return []
    await reclaim(session)
    rows = (
        (
            await session.execute(
                text(
                    "SELECT t.id, t.created_at, t.job_id, t.target_ref, t.attempt,"
                    "       t.max_attempts, j.source, j.job_kind, j.team_id"
                    " FROM app.scrape_task t"
                    " JOIN app.scrape_job j ON j.id = t.job_id"
                    " WHERE t.status = 'pending' AND j.status IN ('pending','running')"
                    " ORDER BY j.priority DESC, t.id"
                    " LIMIT :n"
                    " FOR UPDATE OF t SKIP LOCKED"
                ),
                {"n": count},
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        return []
    leased: list[dict[str, Any]] = []
    job_ids = set()
    for r in rows:
        new_attempt = (
            await session.execute(
                text(
                    "UPDATE app.scrape_task"
                    " SET status = 'dispatched', attempt = attempt + 1,"
                    "     worker_id = :w, dispatched_at = now()"
                    " WHERE id = :id AND created_at = :ca"
                    " RETURNING attempt"
                ),
                {"w": node["id"], "id": r["id"], "ca": r["created_at"]},
            )
        ).scalar_one()
        job_ids.add(r["job_id"])
        leased.append(
            {
                "task_id": r["id"],
                "job_id": r["job_id"],
                "source": r["source"],
                "job_kind": r["job_kind"],
                "target_ref": r["target_ref"],
                "attempt": new_attempt,
                "max_attempts": r["max_attempts"],
            }
        )
    await session.execute(
        text(
            "UPDATE app.scrape_job SET status = 'running'"
            " WHERE id = ANY(:ids) AND status = 'pending'"
        ),
        {"ids": list(job_ids)},
    )
    return leased


async def release_tasks(
    session: AsyncSession, node: dict[str, Any], items: list[dict[str, int]]
) -> int:
    """worker 主动归还（优雅下线）。租约校验后归还 pending；attempt 保持，下次派发再 +1。"""
    released = 0
    for it in items:
        rows = (
            await session.execute(
                text(
                    "UPDATE app.scrape_task SET status = 'pending', worker_id = NULL"
                    " WHERE id = :id AND worker_id = :w AND attempt = :a"
                    "   AND status IN ('dispatched','running')"
                    " RETURNING id"
                ),
                {"id": it["task_id"], "w": node["id"], "a": it["attempt"]},
            )
        ).all()
        released += len(rows)
    return released


async def submit_result(
    session: AsyncSession,
    node: dict[str, Any],
    *,
    task_id: int,
    attempt: int,
    success: bool,
    payload: dict[str, Any] | None = None,
    payload_ref: str | None = None,
    fetched_at: datetime | None = None,
    error_type: str | None = None,
    error_detail: str | None = None,
) -> dict[str, Any]:
    """结果回传：租约校验 → 落 result → 终态推进 → product 入库 → 作业计数。"""
    task = (
        (
            await session.execute(
                text(
                    "SELECT t.id, t.created_at, t.job_id, t.team_id, t.target_ref,"
                    "       t.attempt, t.max_attempts, t.status, t.worker_id,"
                    "       j.source, j.job_kind"
                    " FROM app.scrape_task t JOIN app.scrape_job j ON j.id = t.job_id"
                    " WHERE t.id = :id FOR UPDATE OF t"
                ),
                {"id": task_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if (
        task is None
        or task["status"] not in ("dispatched", "running")
        or task["attempt"] != attempt
        or task["worker_id"] != node["id"]
    ):
        return {"accepted": False, "stale": True}

    product: dict[str, Any] | None = None
    if success:
        await session.execute(
            text(
                "INSERT INTO app.scrape_result"
                " (task_id, team_id, target_ref, payload, payload_ref, fetched_at)"
                " VALUES (:t, :team, :r, cast(:p AS jsonb), :pr, :f)"
            ),
            {
                "t": task_id,
                "team": task["team_id"],
                "r": task["target_ref"],
                "p": _json(payload) if payload is not None else None,
                "pr": payload_ref,
                "f": fetched_at or datetime.now().astimezone(),
            },
        )
        await session.execute(
            text(
                "UPDATE app.scrape_task SET status = 'done', finished_at = now(), error = NULL"
                " WHERE id = :id AND created_at = :ca"
            ),
            {"id": task_id, "ca": task["created_at"]},
        )
        await session.execute(
            text("UPDATE app.scrape_job SET done_tasks = done_tasks + 1 WHERE id = :j"),
            {"j": task["job_id"]},
        )
        if task["job_kind"] == "product_detail" and payload:
            product = await product_upsert(
                session,
                team_id=task["team_id"],
                source=task["source"],
                source_ref=task["target_ref"],
                payload=payload,
            )
    else:
        err = f"{error_type or 'unknown'}: {error_detail or ''}".strip(": ")
        if attempt >= task["max_attempts"]:
            await session.execute(
                text(
                    "UPDATE app.scrape_task SET status = 'dead', finished_at = now(),"
                    " error = :e WHERE id = :id AND created_at = :ca"
                ),
                {"e": err, "id": task_id, "ca": task["created_at"]},
            )
            await session.execute(
                text("UPDATE app.scrape_job SET failed_tasks = failed_tasks + 1 WHERE id = :j"),
                {"j": task["job_id"]},
            )
        else:
            await session.execute(
                text(
                    "UPDATE app.scrape_task SET status = 'pending', worker_id = NULL,"
                    " error = :e WHERE id = :id AND created_at = :ca"
                ),
                {"e": err, "id": task_id, "ca": task["created_at"]},
            )
    await _finish_job_if_complete(session, task["job_id"])
    return {"accepted": True, "stale": False, "product": product}


async def _finish_job_if_complete(session: AsyncSession, job_id: int) -> None:
    await session.execute(
        text(
            "UPDATE app.scrape_job SET"
            " status = CASE WHEN done_tasks = 0 THEN 'failed'"
            "               WHEN failed_tasks > 0 THEN 'partial'"
            "               ELSE 'done' END,"
            " finished_at = now()"
            " WHERE id = :j AND status = 'running'"
            "   AND done_tasks + failed_tasks >= total_tasks"
        ),
        {"j": job_id},
    )


async def reclaim(
    session: AsyncSession,
    *,
    heartbeat_timeout_s: int | None = None,
    task_timeout_min: int | None = None,
) -> int:
    """心跳超时节点下线 + 死节点/硬超时任务归还（v3 合成一条 SQL 原则）。

    阈值经 system_config 可调（scrape.heartbeat_timeout_s / scrape.task_timeout_min）。

    已达重试上限的任务不再归还而是判 dead 并计入 failed_tasks（2026-07-16 卡死教训：
    worker 反复在回报前死亡时，attempt 只在派发时递增、终态只在 submit_result 产生，
    任务会在 pending↔dispatched 之间无限乒乓、job 永不收口）；其 job 随即尝试收口。
    """
    if heartbeat_timeout_s is None or task_timeout_min is None:
        cfg = {
            r.key: r.v
            for r in await session.execute(
                text(
                    "SELECT key, value #>> '{}' AS v FROM app.system_config"
                    " WHERE key IN ('scrape.heartbeat_timeout_s','scrape.task_timeout_min')"
                )
            )
        }
        heartbeat_timeout_s = heartbeat_timeout_s or int(
            cfg.get("scrape.heartbeat_timeout_s") or 90
        )
        task_timeout_min = task_timeout_min or int(cfg.get("scrape.task_timeout_min") or 10)
    await session.execute(
        text(
            "UPDATE app.worker_node SET status = 'offline'"
            " WHERE status = 'online'"
            "   AND last_heartbeat_at < now() - make_interval(secs => :hb)"
        ),
        {"hb": heartbeat_timeout_s},
    )
    stale = (
        "status IN ('dispatched','running')"
        "   AND (dispatched_at < now() - make_interval(mins => :tm)"
        "        OR worker_id IN (SELECT id FROM app.worker_node WHERE status = 'offline'))"
    )
    reclaimed = (
        await session.execute(
            text(
                "UPDATE app.scrape_task SET status = 'pending', worker_id = NULL"
                f" WHERE attempt < max_attempts AND {stale} RETURNING id"
            ),
            {"tm": task_timeout_min},
        )
    ).all()
    # 达上限的判死 + 计入 failed_tasks（乒乓终结）
    dead_jobs = (
        await session.execute(
            text(
                "WITH d AS ("
                "  UPDATE app.scrape_task SET status = 'dead', finished_at = now(),"
                "         worker_id = NULL,"
                "         error = 'RECLAIM_MAX_ATTEMPTS：worker 中途死亡/超时达重试上限'"
                f"  WHERE attempt >= max_attempts AND {stale}"
                "  RETURNING id, job_id)"
                " UPDATE app.scrape_job j"
                " SET failed_tasks = failed_tasks + agg.n"
                " FROM (SELECT job_id, count(*) AS n FROM d GROUP BY job_id) agg"
                " WHERE j.id = agg.job_id"
                " RETURNING j.id, agg.n"
            ),
            {"tm": task_timeout_min},
        )
    ).all()
    for row in dead_jobs:
        await _finish_job_if_complete(session, row.id)
    return len(reclaimed) + sum(int(row.n) for row in dead_jobs)


# ── product 入库（specs/001 §03 去重协议）──


async def _deleted_refs(
    session: AsyncSession, *, team_id: int, source: str, refs: list[str]
) -> set[str]:
    """这批 source_ref 里哪些已有墓碑（R2-14 14a）。

    **墓碑查询的唯一实现**——预筛与入库前检查共用它，避免两处各写一份判定后漂移。
    键与 `uq_product` / 入库 `ON CONFLICT` 严格同形：`(team_id, source_channel, source_ref)`。
    """
    if not refs:
        return set()
    rows = (
        await session.execute(
            text(
                "SELECT source_ref FROM app.deleted_product"
                " WHERE team_id = :t AND source_channel = :sc AND source_ref = ANY(:refs)"
            ),
            {"t": team_id, "sc": source, "refs": refs},
        )
    ).scalars()
    return {str(r) for r in rows}


async def product_upsert(
    session: AsyncSession,
    *,
    team_id: int,
    source: str,
    source_ref: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    title = str(payload.get("title") or "").strip()
    if not title:
        raise BusinessError("SCRAPE_PAYLOAD_INVALID", "payload 缺少 title，无法入库 product")

    # ── 墓碑检查（R2-14 14a-3）——**验收②的承重件就是这一句** ──
    #
    # 无墓碑硬删 = 垃圾循环回流（§7.1 称之为「硬删除唯一的技术陷阱」）。检查放在
    # INSERT 之前而不是靠约束挡：`deleted_product` 与 `product` 是两张表，数据库层面
    # 没有任何东西会阻止重新插入——**挡住它的只有这里**。
    #
    # 不抛错、只跳过：采集任务本身没做错什么，抓回一个已删商品是正常输入。抛错会让
    # 作业计数进 failed，运营看到的是「采集失败」，而事实是「按你的删除意愿没入库」。
    if await _deleted_refs(session, team_id=team_id, source=source, refs=[source_ref]):
        log.info(
            "scrape.upsert_skipped_by_tombstone",
            team_id=team_id,
            source=source,
            source_ref=source_ref,
        )
        return {"skipped": True, "reason": "deleted_tombstone", "source_ref": source_ref}

    row = (
        await session.execute(
            text(
                "INSERT INTO app.product (team_id, source_channel, source_ref, title,"
                "  brand, category_path, amazon_leaf_id, images, attrs, price_snapshot)"
                " VALUES (:t, :sc, :sr, :title, :brand, :cat, :leaf,"
                "  cast(:img AS jsonb), cast(:attrs AS jsonb), cast(:price AS jsonb))"
                " ON CONFLICT (team_id, source_channel, source_ref) DO UPDATE SET"
                "  title = excluded.title,"
                "  price_snapshot = excluded.price_snapshot,"
                "  attrs = excluded.attrs"
                " RETURNING id, master_sku, status"
            ),
            {
                "t": team_id,
                "sc": source,
                "sr": source_ref,
                "title": title,
                "brand": payload.get("brand"),
                "cat": payload.get("category_path"),
                "leaf": payload.get("amazon_leaf_id"),
                "img": _json(payload.get("images") or []),
                "attrs": _json(payload.get("attrs") or {}),
                "price": _json(payload["price_snapshot"])
                if payload.get("price_snapshot") is not None
                else None,
            },
        )
    ).one()
    # 实时归组钩子（D-Q64①）：素材落地即归组，SAVEPOINT 隔离——归组任何异常不反噬
    # 采集入库（beat variant_group_sync 兜底收敛）。无 twister 素材/已分组则内部跳过。
    try:
        async with session.begin_nested():
            await variant_svc.sync_product(session, team_id, int(row.id))
    except Exception as exc:  # 隔离面：入库是关键路径，归组失败只记警不反噬
        log.warning("scrape.variant_sync_failed", product_id=int(row.id), error=str(exc))
    return {"product_id": row.id, "master_sku": row.master_sku, "status": row.status}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
