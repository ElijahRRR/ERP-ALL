"""alembic 环境。migration 是 ar 帽独占产物（CLAUDE.md 角色制）。

- 连接用 migrator URL（DDL 角色），与应用运行角色分离。
- R1-03 起 target_metadata 指向领域 ORM base；当前为 None（空基线）。
"""

from alembic import context
from sqlalchemy import create_engine

from erp.core.settings import get_settings

target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().migrator_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_settings().migrator_database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
