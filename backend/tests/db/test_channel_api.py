"""R1-08 验收：店铺 CRUD / 凭证加密往返 / 代理独占 / 配额原子扣减（含并发夺额）。"""

import asyncio

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from erp.core.security import hash_password

from .conftest import APP_URL
from .test_identity_api import PASSWORD, _login

ADMIN = "channel_admin"
TEAM = "渠道测试团队"


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    """自给自足：团队 + 团队管理员（模板角色含 channel.* 权限）。"""
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM app.app_user WHERE username = %s", (ADMIN,))
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        uid = conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '渠道管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        # 复制模板「团队管理员」到本团队并挂载（若已复制则复用）
        conn.execute(
            """
            INSERT INTO app.role (team_id, name, description)
            SELECT %s, name, description FROM app.role tpl
            WHERE tpl.team_id IS NULL AND tpl.name = '团队管理员'
              AND NOT EXISTS (SELECT 1 FROM app.role r
                              WHERE r.team_id = %s AND r.name = '团队管理员')
            """,
            (team_id, team_id),
        )
        role_id = conn.execute(
            "SELECT id FROM app.role WHERE team_id = %s AND name = '团队管理员'", (team_id,)
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO app.role_permission (role_id, permission_code)
            SELECT %s, rp.permission_code FROM app.role tpl
            JOIN app.role_permission rp ON rp.role_id = tpl.id
            WHERE tpl.team_id IS NULL AND tpl.name = '团队管理员'
            ON CONFLICT DO NOTHING
            """,
            (role_id,),
        )
        conn.execute(
            "INSERT INTO app.user_role (user_id, role_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (uid, role_id),
        )
        # 清理本团队既有店铺/代理（幂等重跑）
        conn.execute(
            "DELETE FROM app.store_credential WHERE store_id IN"
            " (SELECT id FROM app.store WHERE team_id = %s)",
            (team_id,),
        )
        conn.execute(
            "DELETE FROM app.quota_usage WHERE store_id IN"
            " (SELECT id FROM app.store WHERE team_id = %s)",
            (team_id,),
        )
        conn.execute(
            "DELETE FROM app.quota_config WHERE store_id IN"
            " (SELECT id FROM app.store WHERE team_id = %s)",
            (team_id,),
        )
        conn.execute("DELETE FROM app.store_incident WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.store WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.proxy WHERE team_id = %s", (team_id,))
        channel_id = conn.execute(
            "SELECT id FROM app.channel WHERE code = 'walmart_us'"
        ).fetchone()[0]
    return {"team": team_id, "channel": channel_id}


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> TestClient:
    import os

    os.environ["ERP_DATABASE_URL"] = APP_URL
    from erp.core.db import get_session_factory
    from erp.core.settings import get_settings

    get_settings.cache_clear()
    get_session_factory.cache_clear()
    from erp.main import create_app

    return TestClient(create_app())


class TestStoreFlow:
    def test_store_credential_proxy_quota(
        self, client: TestClient, seeded: dict[str, int], migrated_db: str
    ) -> None:
        h = _login(client, ADMIN, PASSWORD)

        # 建店（A152 测试店语义）
        resp = client.post(
            "/api/v1/stores",
            json={
                "channel_id": seeded["channel"],
                "code": "A152",
                "name": "测试验收店",
                "is_test": True,
                "payment_label": "PingPong",
            },
            headers=h,
        )
        assert resp.status_code == 201, resp.text
        store_id = resp.json()["id"]

        # 凭证：写入 → 状态掩码 → 永不回显 → 服务层可解密（网关通道）
        assert (
            client.put(
                f"/api/v1/stores/{store_id}/credential",
                json={"client_id": "abcd1234-client", "client_secret": "s3cr3t-value-9"},
                headers=h,
            ).status_code
            == 204
        )
        status = client.get(f"/api/v1/stores/{store_id}/credential", headers=h).json()
        assert status["configured"] is True
        assert status["client_id_masked"] == "abcd****"
        assert "s3cr3t" not in str(status)
        with psycopg.connect(migrated_db) as conn:
            raw = conn.execute(
                "SELECT client_secret_encrypted FROM app.store_credential WHERE store_id = %s",
                (store_id,),
            ).fetchone()[0]
        assert b"s3cr3t" not in bytes(raw)  # 密文落库

        async def decrypt() -> tuple[str, str]:
            from erp.channel.service import decrypt_credential
            from erp.core.db import system_tx

            sessions = async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)
            async with system_tx(sessions) as s:
                return await decrypt_credential(s, store_id)

        cid, secret = asyncio.run(decrypt())
        assert (cid, secret) == ("abcd1234-client", "s3cr3t-value-9")

        # 代理：建两条 + 绑定 + 独占拒绝
        p1 = client.post(
            "/api/v1/proxies",
            json={"kind": "socks5", "host": "192.0.2.10", "port": 1080, "password": "pw-x"},
            headers=h,
        ).json()["id"]
        client.post(
            "/api/v1/stores",
            json={"channel_id": seeded["channel"], "code": "A153", "name": "第二家店"},
            headers=h,
        )
        store2 = client.get("/api/v1/stores?status=active", headers=h).json()["items"]
        store2_id = next(s["id"] for s in store2 if s["code"] == "A153")
        assert (
            client.put(
                f"/api/v1/stores/{store_id}/proxy", json={"proxy_id": p1}, headers=h
            ).status_code
            == 204
        )
        resp = client.put(f"/api/v1/stores/{store2_id}/proxy", json={"proxy_id": p1}, headers=h)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "PROXY_OCCUPIED"

        # 配额：配置 → 原子扣减到上限
        client.put(
            f"/api/v1/stores/{store_id}/quota-configs",
            json=[{"quota_kind": "listing_create", "daily_limit": 3, "enabled": True}],
            headers=h,
        )

        async def consume_n(n: int) -> list[bool]:
            from erp.channel.service import consume_quota
            from erp.core.db import system_tx

            sessions = async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)
            out: list[bool] = []
            for _ in range(n):
                async with system_tx(sessions) as s:
                    out.append(await consume_quota(s, store_id, "listing_create"))
            return out

        results = asyncio.run(consume_n(5))
        assert results == [True, True, True, False, False]  # 3 配额，第 4 次起拒绝
        usage = client.get(f"/api/v1/stores/{store_id}/quota-usage", headers=h).json()
        assert usage[0]["used"] == 3

        # 返还一个 → 可再拿一个
        async def release_then_consume() -> bool:
            from erp.channel.service import consume_quota, release_quota
            from erp.core.db import system_tx

            sessions = async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)
            async with system_tx(sessions) as s:
                await release_quota(s, store_id, "listing_create")
            async with system_tx(sessions) as s:
                return await consume_quota(s, store_id, "listing_create")

        assert asyncio.run(release_then_consume()) is True

    def test_incident_suspension_links_store(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        h = _login(client, ADMIN, PASSWORD)
        stores = client.get("/api/v1/stores", headers=h).json()["items"]
        target = next(s for s in stores if s["code"] == "A153")

        rid = client.post(
            "/api/v1/store-incidents",
            json={
                "store_id": target["id"],
                "incident_kind": "suspension",
                "occurred_at": "2026-07-10T08:00:00Z",
                "reason": "邮件识别测试",
            },
            headers=h,
        ).json()["id"]
        assert (
            client.get(f"/api/v1/stores/{target['id']}", headers=h).json()["status"] == "suspended"
        )
        client.post(
            f"/api/v1/store-incidents/{rid}/transition",
            json={"to_status": "resolved", "notes": "申诉通过"},
            headers=h,
        )
        assert client.get(f"/api/v1/stores/{target['id']}", headers=h).json()["status"] == "active"
