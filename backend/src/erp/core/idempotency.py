"""API 层 Idempotency-Key 消费（RS-03b/评审 C2，契约 002 §写操作幂等）。

协议（.agent/evidence/RS-03b/archaeology.md §2.3）：
- 首见：短事务占位（response=NULL）→ 执行处理器（自管事务）→ 短事务回填响应。
- 重放（<24h）：同 payload_hash → 返回存储响应（带 idempotent_replay 标记）；
  异 payload → 409 IDEMPOTENCY_CONFLICT。
- 并发同键：占位唯一约束让后到者看到"进行中"→ 409 IDEMPOTENCY_IN_PROGRESS。
- 处理器异常：占位即时删除（错误响应不缓存，客户端可重试）；
  处理器崩溃遗留占位：超 stale 阈值视为失效可重占。
- 过期行按键惰性清理（TTL 契约定 24h；全表清扫随 R2-04 维护任务）。
"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.core.db import ctx_tx
from erp.core.errors import BusinessError

_DEFAULTS = {"ttl_hours": 24, "stale_minutes": 10}


def _payload_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


async def _config(session: AsyncSession) -> dict[str, Any]:
    row = (
        await session.execute(
            text("SELECT value FROM app.system_config WHERE key = 'api.idempotency'")
        )
    ).first()
    cfg = dict(_DEFAULTS)
    if row and isinstance(row[0], dict):
        cfg.update(row[0])
    return cfg


async def run_idempotent(
    sessions: async_sessionmaker[AsyncSession],
    *,
    team_id: int,
    is_super: bool = False,
    endpoint: str,
    idem_key: str,
    payload: Any,
    created_by: int | None = None,
    handler: Callable[[], Awaitable[dict[str, Any]]],
    stale_minutes: int | None = None,
) -> dict[str, Any]:
    """stale_minutes：占位失效阈值的端点级覆盖——同步长批量端点（如 reprice，
    最坏时长可超默认 10 分钟）必须显式给出大于最坏执行时长的值，否则首个
    handler 仍在跑时同键重试会重占占位并发双跑（R2-06 增量3 评审发现 12）。
    响应回填/错误清理均按本次占位行 id 定位——即便占位被并发重占，先完成者
    也不会覆写后占者的行。
    """
    h = _payload_hash(payload)
    params = {"t": team_id, "e": endpoint, "k": idem_key}
    async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as s:
        cfg = await _config(s)
        stale = int(stale_minutes if stale_minutes is not None else cfg["stale_minutes"])
        # 键内惰性清理：过期行（>TTL）与失效占位（response NULL 超 stale）
        await s.execute(
            text(
                "DELETE FROM app.api_idempotency"
                " WHERE team_id = :t AND endpoint = :e AND idem_key = :k"
                " AND (created_at < now() - make_interval(hours => :ttl)"
                "   OR (response IS NULL"
                "       AND created_at < now() - make_interval(mins => :stale)))"
            ),
            {**params, "ttl": int(cfg["ttl_hours"]), "stale": stale},
        )
        reserved = (
            await s.execute(
                text(
                    "INSERT INTO app.api_idempotency"
                    " (team_id, endpoint, idem_key, payload_hash, created_by)"
                    " VALUES (:t, :e, :k, :h, :u)"
                    " ON CONFLICT (team_id, endpoint, idem_key) DO NOTHING RETURNING id"
                ),
                {**params, "h": h, "u": created_by},
            )
        ).first()
        if reserved is None:
            existing = (
                await s.execute(
                    text(
                        "SELECT payload_hash, response FROM app.api_idempotency"
                        " WHERE team_id = :t AND endpoint = :e AND idem_key = :k"
                    ),
                    params,
                )
            ).one()
            if existing.payload_hash != h:
                raise BusinessError(
                    "IDEMPOTENCY_CONFLICT",
                    "Idempotency-Key 已被不同请求载荷使用（同键必须同载荷）",
                    http_status=409,
                )
            if existing.response is None:
                raise BusinessError(
                    "IDEMPOTENCY_IN_PROGRESS",
                    "同幂等键请求正在处理中，请稍后以同一键重试获取结果",
                    http_status=409,
                )
            return {**existing.response, "idempotent_replay": True}
        row_id = int(reserved.id)

    try:
        result = await handler()
    except Exception:
        # 错误响应不缓存：删除本次占位行（按 id——不误删并发重占者），客户端可携同键重试
        async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as s:
            await s.execute(
                text("DELETE FROM app.api_idempotency WHERE id = :id AND response IS NULL"),
                {"id": row_id},
            )
        raise

    async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as s:
        await s.execute(
            text(
                "UPDATE app.api_idempotency SET response = cast(:r AS jsonb), status_code = 200"
                " WHERE id = :id AND response IS NULL"
            ),
            {"id": row_id, "r": json.dumps(result, ensure_ascii=False, default=str)},
        )
    return result
