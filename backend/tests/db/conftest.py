"""DB 集成测试基座。

需要可达的 PostgreSQL（CI: pg16 service；本地沙盒: apt postgresql）。
环境变量：
  ERP_MIGRATOR_DATABASE_URL —— DDL 连接（CI 为 postgres 超级用户）
  ERP_DATABASE_URL          —— erp_app 连接（RLS 生效方）
不可达则整目录 skip（单元测试不受影响）。
"""

import contextlib
import os

import psycopg
import pytest
from alembic import command
from alembic.config import Config


def _pg_dsn(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://")


MIGRATOR_URL = os.environ.get(
    "ERP_MIGRATOR_DATABASE_URL",
    "postgresql+psycopg://erp_migrator:erp_migrator@localhost:5432/erp_all",
)
APP_URL = os.environ.get(
    "ERP_DATABASE_URL",
    "postgresql+psycopg://erp_app:erp_app@localhost:5432/erp_all",
)


def _reachable(dsn: str) -> bool:
    try:
        with psycopg.connect(_pg_dsn(dsn), connect_timeout=3):
            return True
    except Exception:
        return False


if not _reachable(MIGRATOR_URL):
    pytest.skip("PostgreSQL 不可达，跳过 DB 集成测试", allow_module_level=True)


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """升级到 head 并确保测试角色可登录、测试团队存在。返回 migrator DSN。"""
    os.environ["ERP_MIGRATOR_DATABASE_URL"] = MIGRATOR_URL
    command.upgrade(Config("alembic.ini"), "head")

    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        # CI 里角色由 migration 代建（无口令），测试需要真实登录；
        # 本地角色由 pg-init/运维预建（migrator 无 CREATEROLE），无权则跳过
        with contextlib.suppress(psycopg.errors.InsufficientPrivilege):
            conn.execute("ALTER ROLE erp_app LOGIN PASSWORD 'erp_app'")
        conn.execute(
            "INSERT INTO app.team (name) VALUES ('测试团队A'), ('测试团队B')"
            " ON CONFLICT (name) DO NOTHING"
        )
    return _pg_dsn(MIGRATOR_URL)


@pytest.fixture(scope="session")
def team_ids(migrated_db: str) -> tuple[int, int]:
    with psycopg.connect(migrated_db) as conn:
        rows = conn.execute(
            "SELECT id, name FROM app.team WHERE name IN ('测试团队A','测试团队B') ORDER BY name"
        ).fetchall()
    return rows[0][0], rows[1][0]


@pytest.fixture()
def app_conn(migrated_db: str):  # type: ignore[no-untyped-def]
    """以 erp_app（受 RLS）连接；用例内自行 SET app.current_team。"""
    with psycopg.connect(_pg_dsn(APP_URL)) as conn:
        yield conn
