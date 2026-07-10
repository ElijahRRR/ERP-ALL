"""R1-02 ConfigService：team > system > default 优先级链 + 缓存失效。"""

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from erp.core.config_service import ConfigService

from .conftest import MIGRATOR_URL


@pytest.fixture(scope="module")
def sessions(migrated_db: str):  # type: ignore[no-untyped-def]
    # 配置写路径当前经 migrator 连接（system_config 全局表）；
    # 团队态读写在服务层带 GUC 的会话中进行——测试聚焦优先级语义，用 migrator 免 GUC 干扰。
    engine = create_async_engine(MIGRATOR_URL)
    yield async_sessionmaker(engine, expire_on_commit=False)


async def test_priority_chain(sessions, team_ids: tuple[int, int]) -> None:  # type: ignore[no-untyped-def]
    a, _ = team_ids
    # 本地持久库可能残留上次运行的值——先清场保证幂等
    async with sessions() as s, s.begin():
        await s.execute(text("DELETE FROM app.team_config WHERE key = 'gtin.warn_pct'"))
        await s.execute(text("DELETE FROM app.system_config WHERE key = 'gtin.warn_pct'"))
    svc = ConfigService(sessions, ttl_seconds=60)

    # 全无 → default
    assert await svc.get("gtin.warn_pct", team_id=a, default=15) == 15
    # system 层
    await svc.set_system("gtin.warn_pct", 20)
    assert await svc.get("gtin.warn_pct", team_id=a, default=15) == 20
    # team 覆盖
    await svc.set_team(a, "gtin.warn_pct", 25)
    assert await svc.get("gtin.warn_pct", team_id=a, default=15) == 25
    # 不带 team → system 值
    assert await svc.get("gtin.warn_pct", default=15) == 20


async def test_cache_and_invalidate(sessions, migrated_db: str) -> None:  # type: ignore[no-untyped-def]
    svc = ConfigService(sessions, ttl_seconds=3600)
    await svc.set_system("cache.probe", "v1")
    assert await svc.get("cache.probe") == "v1"

    # 绕过服务直接改库 → 缓存内旧值仍在；invalidate 后读到新值
    with psycopg.connect(
        migrated_db.replace("postgresql+psycopg://", "postgresql://"), autocommit=True
    ) as conn:
        conn.execute("""UPDATE app.system_config SET value = '"v2"' WHERE key = 'cache.probe'""")
    assert await svc.get("cache.probe") == "v1"
    svc.invalidate()
    assert await svc.get("cache.probe") == "v2"
