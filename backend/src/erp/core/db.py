"""数据库引擎与会话工厂。

- 应用统一走 erp_app 角色（受 RLS）；请求级 GUC（app.current_team/app.is_super）
  由 API 依赖在事务开始时 SET LOCAL（R1-04 实现）。
- alembic 独立使用 migrator URL，不经此模块。
"""

from collections.abc import AsyncIterator
from functools import lru_cache

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
