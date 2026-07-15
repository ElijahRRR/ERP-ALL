"""R1-06 验收：通知发送/dedupe 抑制/task_run 失败联动/API 可见性与已读。"""

import asyncio
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from erp.core.db import system_tx

from .conftest import APP_URL
from .test_identity_api import (
    MEMBER,
    PASSWORD,
    TEAM_NAME,
    _login,
    super_seeded,  # noqa: F401
)
from .test_identity_api import client as identity_client  # noqa: F401 复用 app 夹具


def _sessions():  # type: ignore[no-untyped-def]
    return async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)


@pytest.fixture()
def member_team(migrated_db: str, identity_client: TestClient) -> int:
    """自给自足：确保测试团队与成员存在（不依赖其他测试模块的副作用）。"""
    from erp.core.security import hash_password

    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
            (TEAM_NAME,),
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM_NAME,)).fetchone()[
            0
        ]
        exists = conn.execute(
            "SELECT 1 FROM app.app_user WHERE username = %s", (MEMBER,)
        ).fetchone()
        if exists is None:
            conn.execute(
                "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
                " VALUES (%s, %s, %s, '通知测试成员')",
                (team_id, MEMBER, hash_password(PASSWORD)),
            )
    return team_id


class TestNotifyService:
    def test_dedupe_24h(self, identity_client: TestClient, member_team: int) -> None:
        from erp.notify.service import notify

        sessions = _sessions()
        key = f"t:{uuid.uuid4().hex[:8]}"

        async def flow() -> tuple[int | None, int | None]:
            async with system_tx(sessions) as s:
                first = await notify(
                    s,
                    team_id=member_team,
                    severity="warn",
                    category="quota",
                    title="配额告警",
                    dedupe_key=key,
                )
            async with system_tx(sessions) as s:
                second = await notify(
                    s,
                    team_id=member_team,
                    severity="warn",
                    category="quota",
                    title="配额告警",
                    dedupe_key=key,
                )
            return first, second

        first, second = asyncio.run(flow())
        assert first is not None
        assert second is None  # 24h 内同键抑制

    def test_task_fail_creates_notification(
        self,
        identity_client: TestClient,
        migrated_db: str,
        member_team: int,
    ) -> None:
        from erp.automation.task_runner import run_tracked

        sessions = _sessions()
        code = f"demo_task_{uuid.uuid4().hex[:6]}"

        async def boom(_s) -> None:  # type: ignore[no-untyped-def]
            raise RuntimeError("演示失败")

        ok = asyncio.run(run_tracked(sessions, code, boom, team_id=member_team))
        assert ok is False
        with psycopg.connect(migrated_db) as conn:
            status = conn.execute(
                "SELECT status, error FROM app.task_run WHERE task_code = %s", (code,)
            ).fetchone()
            assert status[0] == "failed"
            assert "演示失败" in status[1]
            n = conn.execute(
                "SELECT count(*) FROM app.notification WHERE category = 'task_fail'"
                " AND title LIKE %s",
                (f"%{code}%",),
            ).fetchone()[0]
            assert n == 1

    def test_task_success_records_stats(
        self,
        identity_client: TestClient,
        migrated_db: str,
    ) -> None:
        from erp.automation.task_runner import run_tracked

        sessions = _sessions()
        code = f"demo_ok_{uuid.uuid4().hex[:6]}"

        async def work(sess) -> dict[str, int]:  # type: ignore[no-untyped-def]
            from erp.core.db import system_tx

            async with system_tx(sess) as s:  # fn 自管事务（R2-04 契约）
                await s.execute(text("SELECT 1"))
            return {"processed": 42}

        assert asyncio.run(run_tracked(sessions, code, work)) is True
        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT status, stats->>'processed' FROM app.task_run WHERE task_code = %s",
                (code,),
            ).fetchone()
        assert row == ("done", "42")


class TestNotifyApi:
    def test_visibility_and_read_flow(
        self,
        identity_client: TestClient,
        member_team: int,
    ) -> None:
        from erp.notify.service import notify

        sessions = _sessions()

        async def send() -> None:
            async with system_tx(sessions) as s:
                await notify(
                    s,
                    team_id=member_team,
                    severity="info",
                    category="system",
                    title="团队通知可见性测试",
                )

        asyncio.run(send())
        mem = _login(identity_client, MEMBER, PASSWORD)

        count0 = identity_client.get("/api/v1/notifications/unread-count", headers=mem).json()[
            "count"
        ]
        assert count0 >= 1

        listing = identity_client.get("/api/v1/notifications?unread_only=true", headers=mem).json()
        target = next(item for item in listing["items"] if item["title"] == "团队通知可见性测试")

        assert (
            identity_client.post(
                f"/api/v1/notifications/{target['id']}/read", headers=mem
            ).status_code
            == 204
        )
        count1 = identity_client.get("/api/v1/notifications/unread-count", headers=mem).json()[
            "count"
        ]
        assert count1 == count0 - 1

        assert (
            identity_client.post("/api/v1/notifications/read-all", headers=mem).status_code == 204
        )
        assert (
            identity_client.get("/api/v1/notifications/unread-count", headers=mem).json()["count"]
            == 0
        )
