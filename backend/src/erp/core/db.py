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
async def ctx_tx(
    sessions: async_sessionmaker[AsyncSession],
    *,
    team_id: int | None,
    is_super: bool = False,
) -> AsyncIterator[AsyncSession]:
    """用户上下文自管短事务（RS-03 三段式）：每段事务重放请求级 GUC。

    用于把网络调用移出请求事务的编排（tx1 → HTTP → tx2）——SET LOCAL 随
    COMMIT 失效，故每段都需重放。团队/超管身份必须来自 authn 的真实用户，
    禁止借本入口提权（与 system_tx 同一纪律）。
    """
    async with sessions() as s, s.begin():
        if is_super:
            await s.execute(text("SELECT set_config('app.is_super', 'on', true)"))
        if team_id is not None:
            await s.execute(
                text("SELECT set_config('app.current_team', :t, true)"), {"t": str(team_id)}
            )
        yield s


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
