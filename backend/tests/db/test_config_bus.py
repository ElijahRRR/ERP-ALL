"""R2-04 增量4 验收：Redis pubsub 配置失效广播。

- fail-open：Redis 不可达时 set_system/set_team 照常成功（只降级为 TTL 收敛）。
- round-trip：进程 A 缓存旧值 → 进程 B 写新值（PUBLISH）→ A 的订阅循环收到
  失效消息 → A 不等 TTL 立即读到新值。Redis 不可达则 skip（CI 有 redis 服务）。
"""

import asyncio
import contextlib
import socket
import uuid

import pytest

from erp.core.config_service import ConfigService, run_invalidation_subscriber
from erp.core.db import get_session_factory
from erp.core.settings import get_settings


def _redis_reachable() -> bool:
    url = get_settings().redis_url  # redis://host:port/db
    hostport = url.split("//", 1)[1].split("/", 1)[0]
    host, _, port = hostport.partition(":")
    try:
        with socket.create_connection((host or "localhost", int(port or 6379)), timeout=2):
            return True
    except OSError:
        return False


class TestFailOpen:
    async def test_write_succeeds_with_redis_down(self, migrated_db: str) -> None:
        svc = ConfigService(get_session_factory(), redis_url="redis://127.0.0.1:1/0")
        key = f"beat.bus_failopen_{uuid.uuid4().hex[:6]}"
        await svc.set_system(key, {"v": 1})  # 不得抛——广播失败只打 warning
        assert await svc.get(key) == {"v": 1}


class TestRoundTrip:
    async def test_cross_process_invalidation(self, migrated_db: str) -> None:
        if not _redis_reachable():
            pytest.skip("Redis 不可达（CI 有 redis 服务；本地需先起 redis-server）")
        sessions = get_session_factory()
        reader = ConfigService(sessions, ttl_seconds=3600)  # TTL 拉满：只有广播能让它变
        writer = ConfigService(sessions)
        key = f"beat.bus_roundtrip_{uuid.uuid4().hex[:6]}"

        await writer.set_system(key, "old")
        assert await reader.get(key) == "old"  # reader 进缓存

        subscriber = asyncio.create_task(run_invalidation_subscriber(reader))
        try:
            await asyncio.sleep(0.3)  # 等订阅建立
            await writer.set_system(key, "new")
            got = None
            for _ in range(50):  # 最多 5s
                got = await reader.get(key)
                if got == "new":
                    break
                await asyncio.sleep(0.1)
            assert got == "new", "订阅失效未生效：reader 仍持旧缓存"
        finally:
            subscriber.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await subscriber
