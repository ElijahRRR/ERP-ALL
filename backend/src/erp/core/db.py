"""数据库引擎与会话工厂。

- 应用统一走 erp_app 角色（受 RLS）；请求级 GUC（app.current_team/app.is_super）
  由 API 依赖在事务开始时 SET LOCAL（R1-04 实现）。
- alembic 独立使用 migrator URL，不经此模块。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from erp.core.settings import get_settings


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：一请求一会话一事务。"""
    async with get_session_factory()() as session, session.begin():
        yield session


@asynccontextmanager
async def system_tx(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """系统上下文事务（worker/beat/系统联动写）。

    以 is_super GUC 执行，绕过团队 RLS——**仅限非用户代码路径**：
    用户请求一律走 get_session + authn 注入的真实身份，禁止借用本入口提权。
    """
    async with sessions() as s, s.begin():
        await s.execute(text("SELECT set_config('app.is_super', 'on', true)"))
        yield s
