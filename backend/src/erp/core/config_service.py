"""业务参数配置中心（R1-02；跨进程失效广播 R2-04）。

读取优先级：team_config > system_config > 代码调用方给的 default（D-Q11）。
- 业务参数（阈值/频率/开关/单价表）一律经此服务读取，禁止写死（CLAUDE.md 禁区）。
- 进程内缓存 TTL 60s（可调）；写操作立即失效本进程缓存，并经 Redis pubsub
  广播到其余进程（api/beat 在 lifespan 各起一份订阅循环）。
- **fail-open**：Redis 不可用时写不受阻、订阅退避重连——只降级为 60s TTL
  自然收敛，不影响正确性（广播是加速器不是正确性依赖）。
- 写操作的审计经 R1-04 的 audit 出口统一记录，本服务只透传 updated_by。
"""

import asyncio
import json
import time
from functools import lru_cache
from typing import Any

import redis.asyncio as aioredis
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.core.db import get_session_factory
from erp.core.settings import get_settings

log = structlog.get_logger()

_MISS = object()

CONFIG_INVALIDATE_CHANNEL = "erp:config:invalidate"


class ConfigService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        ttl_seconds: float = 60.0,
        redis_url: str | None = None,
    ) -> None:
        self._sessions = session_factory
        self._ttl = ttl_seconds
        self._redis_url = (
            redis_url  # None = 取 settings.redis_url（测试可注入坏地址验证 fail-open）
        )
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
        await self._publish(key)

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
        await self._publish(key)

    def invalidate(self) -> None:
        self._cache.clear()

    async def _publish(self, key: str) -> None:
        """跨进程失效广播（提交后调用）。配置写是低频管理操作——按次连接，不养长连接。"""
        url = self._redis_url or get_settings().redis_url
        try:
            client = aioredis.from_url(url, socket_connect_timeout=1.0, socket_timeout=2.0)
            try:
                await client.publish(CONFIG_INVALIDATE_CHANNEL, key)
            finally:
                await client.aclose()
        except Exception as exc:  # fail-open：广播失败只降级为 TTL 收敛
            log.warning("config_bus.publish_degraded", key=key, error=str(exc))

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


@lru_cache
def get_config_service() -> ConfigService:
    """进程级单例（api/beat 共用读路径；订阅循环失效的就是这份缓存）。"""
    return ConfigService(get_session_factory())


async def run_invalidation_subscriber(service: ConfigService) -> None:
    """配置失效订阅循环（api/beat 进程 lifespan 各起一份；任务取消即退出）。

    收到任一键的失效消息即整体 invalidate（缓存条目少、重建便宜，不做按键精细失效）。
    Redis 断连按指数退避重连——fail-open，见模块头注。
    """
    url = service._redis_url or get_settings().redis_url
    backoff = 1.0
    while True:
        try:
            client = aioredis.from_url(url, socket_connect_timeout=3.0)
            try:
                pubsub = client.pubsub()
                await pubsub.subscribe(CONFIG_INVALIDATE_CHANNEL)
                log.info("config_bus.subscribed", channel=CONFIG_INVALIDATE_CHANNEL)
                backoff = 1.0
                async for msg in pubsub.listen():
                    if msg.get("type") == "message":
                        service.invalidate()
            finally:
                await client.aclose()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("config_bus.subscribe_retry", error=str(exc), retry_in_s=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
