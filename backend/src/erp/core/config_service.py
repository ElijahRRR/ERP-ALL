"""业务参数配置中心（R1-02）。

读取优先级：team_config > system_config > 代码调用方给的 default（D-Q11）。
- 业务参数（阈值/频率/开关/单价表）一律经此服务读取，禁止写死（CLAUDE.md 禁区）。
- 进程内缓存 TTL 60s（可调）；写操作立即失效本进程缓存。跨进程失效广播
  （Redis pubsub）随 worker 角色在 R1-06 接入，当前 60s 收敛窗已满足配置类参数。
- 写操作的审计经 R1-04 的 audit 出口统一记录，本服务只透传 updated_by。
"""

import json
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_MISS = object()


class ConfigService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        ttl_seconds: float = 60.0,
    ) -> None:
        self._sessions = session_factory
        self._ttl = ttl_seconds
        # 键: ("system", key) / ("team", team_id, key) → (存入时刻, 值或 _MISS)
        self._cache: dict[tuple[Any, ...], tuple[float, Any]] = {}

    async def get(self, key: str, *, team_id: int | None = None, default: Any = None) -> Any:
        if team_id is not None:
            v = await self._lookup(("team", team_id, key))
            if v is not _MISS:
                return v
        v = await self._lookup(("system", key))
        return default if v is _MISS else v

    async def set_system(self, key: str, value: Any, *, updated_by: int | None = None) -> None:
        async with self._sessions() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO app.system_config (key, value, updated_by)"
                    " VALUES (:k, cast(:v AS jsonb), :u)"
                    " ON CONFLICT (key) DO UPDATE"
                    " SET value = excluded.value, updated_by = excluded.updated_by"
                ),
                {"k": key, "v": _to_json(value), "u": updated_by},
            )
        self.invalidate()

    async def set_team(
        self, team_id: int, key: str, value: Any, *, updated_by: int | None = None
    ) -> None:
        async with self._sessions() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO app.team_config (team_id, key, value, updated_by)"
                    " VALUES (:t, :k, cast(:v AS jsonb), :u)"
                    " ON CONFLICT (team_id, key) DO UPDATE"
                    " SET value = excluded.value, updated_by = excluded.updated_by"
                ),
                {"t": team_id, "k": key, "v": _to_json(value), "u": updated_by},
            )
        self.invalidate()

    def invalidate(self) -> None:
        self._cache.clear()

    async def _lookup(self, cache_key: tuple[Any, ...]) -> Any:
        hit = self._cache.get(cache_key)
        if hit is not None and (time.monotonic() - hit[0]) < self._ttl:
            return hit[1]
        if cache_key[0] == "system":
            sql = text("SELECT value FROM app.system_config WHERE key = :k")
            params: dict[str, Any] = {"k": cache_key[1]}
        else:
            sql = text("SELECT value FROM app.team_config WHERE team_id = :t AND key = :k")
            params = {"t": cache_key[1], "k": cache_key[2]}
        async with self._sessions() as s:
            row = (await s.execute(sql, params)).first()
        value: Any = _MISS if row is None else row[0]
        self._cache[cache_key] = (time.monotonic(), value)
        return value


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
