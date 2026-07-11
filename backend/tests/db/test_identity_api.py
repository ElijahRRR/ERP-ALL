"""R1-05 后端验收：契约端点全流程（团队→成员→角色→登录→越权→审计）。"""

import os
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.core.security import hash_password

from .conftest import APP_URL

SUPER = "api_super"
MEMBER = "api_member"
PASSWORD = "super-secret-pass-1"
TEAM_NAME = "API测试团队X"


@pytest.fixture(scope="module")
def super_seeded(migrated_db: str) -> None:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        # 幂等清场：上一轮的测试团队及其派生数据
        conn.execute("DELETE FROM app.app_user WHERE username IN (%s, %s)", (SUPER, MEMBER))
        conn.execute(
            "DELETE FROM app.role WHERE team_id IN (SELECT id FROM app.team WHERE name = %s)",
            (TEAM_NAME,),
        )
        conn.execute("DELETE FROM app.team WHERE name = %s", (TEAM_NAME,))
        conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name, is_super)"
            " VALUES (NULL, %s, %s, '超管', true)",
            (SUPER, hash_password(PASSWORD)),
        )


@pytest.fixture(scope="module")
def client(super_seeded: None) -> Iterator[TestClient]:
    os.environ["ERP_DATABASE_URL"] = APP_URL
    from erp.core.db import get_session_factory
    from erp.core.settings import get_settings

    get_settings.cache_clear()
    get_session_factory.cache_clear()
    from erp.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()
    get_session_factory.cache_clear()


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


class TestAcceptanceFlow:
    """EA-003 R1-05 验收判据：开团队→开成员→赋角色→登录→越权 403。"""

    def test_full_flow(self, client: TestClient, migrated_db: str) -> None:
        sup = _login(client, SUPER, PASSWORD)

        # 1. 超管建团队 → 模板角色自动复制
        resp = client.post("/api/v1/teams", json={"name": TEAM_NAME}, headers=sup)
        assert resp.status_code == 201, resp.text
        team_id = resp.json()["id"]
        with psycopg.connect(migrated_db) as conn:
            copied = conn.execute(
                "SELECT count(*) FROM app.role WHERE team_id = %s", (team_id,)
            ).fetchone()[0]
        assert copied == 7  # 7 个模板角色全部复制

        # 2. 超管建成员（指定团队）
        resp = client.post(
            "/api/v1/users",
            json={
                "username": MEMBER,
                "password": PASSWORD,
                "display_name": "测试上架员",
                "team_id": team_id,
            },
            headers=sup,
        )
        assert resp.status_code == 201, resp.text
        member_id = resp.json()["id"]

        # 3. 赋角色：该团队的「上架员」
        with psycopg.connect(migrated_db) as conn:
            role_id = conn.execute(
                "SELECT id FROM app.role WHERE team_id = %s AND name = '上架员'", (team_id,)
            ).fetchone()[0]
        resp = client.put(
            f"/api/v1/users/{member_id}/roles", json={"role_ids": [role_id]}, headers=sup
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["roles"][0]["name"] == "上架员"

        # 4. 成员登录 → /me 权限点正确
        mem = _login(client, MEMBER, PASSWORD)
        me = client.get("/api/v1/me", headers=mem).json()
        assert me["user"]["username"] == MEMBER
        assert "listing.read" in me["permissions"]
        assert "identity.user_write" not in me["permissions"]

        # 5. 越权矩阵：上架员无 identity.* 权限
        assert client.get("/api/v1/users", headers=mem).status_code == 403
        assert client.get("/api/v1/teams", headers=mem).status_code == 403
        assert (
            client.post(
                "/api/v1/users",
                json={"username": "x2", "password": PASSWORD, "display_name": "x"},
                headers=mem,
            ).status_code
            == 403
        )
        # 有权限的读接口正常
        assert client.get("/api/v1/permissions", headers=mem).status_code == 200

        # 6. 审计留痕：本流程的三类写操作都在
        logs = client.get("/api/v1/audit-logs", params={"size": 200}, headers=sup).json()
        actions = {log["action"] for log in logs["items"]}
        assert {
            "identity.team_create",
            "identity.user_create",
            "identity.user_roles_set",
        } <= actions

    def test_logout_revokes(self, client: TestClient) -> None:
        mem = _login(client, MEMBER, PASSWORD)
        assert client.get("/api/v1/me", headers=mem).status_code == 200
        assert client.post("/api/v1/auth/logout", headers=mem).status_code == 204
        # token_version 已 +1，旧 access 立即失效
        assert client.get("/api/v1/me", headers=mem).status_code == 401

    def test_wrong_login_envelope(self, client: TestClient) -> None:
        resp = client.post("/api/v1/auth/login", json={"username": SUPER, "password": "wrong"})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


class TestRoleGuards:
    def test_template_role_not_attachable(self, client: TestClient, migrated_db: str) -> None:
        sup = _login(client, SUPER, PASSWORD)
        with psycopg.connect(migrated_db) as conn:
            tpl_role = conn.execute(
                "SELECT id FROM app.role WHERE team_id IS NULL LIMIT 1"
            ).fetchone()[0]
            member_id = conn.execute(
                "SELECT id FROM app.app_user WHERE username = %s", (MEMBER,)
            ).fetchone()[0]
        resp = client.put(
            f"/api/v1/users/{member_id}/roles", json={"role_ids": [tpl_role]}, headers=sup
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "ROLE_INVALID"

    def test_unknown_permission_code_rejected(self, client: TestClient, migrated_db: str) -> None:
        sup = _login(client, SUPER, PASSWORD)
        with psycopg.connect(migrated_db) as conn:
            role_id = conn.execute(
                "SELECT id FROM app.role WHERE team_id IS NOT NULL AND name = '上架员'"
                " ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        resp = client.put(
            f"/api/v1/roles/{role_id}/permissions",
            json={"permission_codes": ["no.such_permission"]},
            headers=sup,
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "PERMISSION_INVALID"


class TestActTeam:
    """超管 X-Act-Team 代表团队操作（试点期核心用法）。"""

    def test_super_acts_as_team(self, client: TestClient, migrated_db: str) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:  # 幂等重跑清理
            conn.execute(
                "DELETE FROM app.user_role WHERE user_id IN"
                " (SELECT id FROM app.app_user WHERE username LIKE 'act_member%')"
            )
            conn.execute("DELETE FROM app.app_user WHERE username LIKE 'act_member%'")
            tid = conn.execute("SELECT id FROM app.team WHERE name = '切换测试团队'").fetchone()
            if tid:
                conn.execute("DELETE FROM app.scrape_task WHERE team_id = %s", (tid[0],))
                conn.execute("DELETE FROM app.scrape_job WHERE team_id = %s", (tid[0],))
                conn.execute(
                    "DELETE FROM app.role_permission WHERE role_id IN"
                    " (SELECT id FROM app.role WHERE team_id = %s)",
                    (tid[0],),
                )
                conn.execute("DELETE FROM app.role WHERE team_id = %s", (tid[0],))
                conn.execute("DELETE FROM app.team WHERE id = %s", (tid[0],))
        sup = _login(client, SUPER, PASSWORD)
        team = client.post("/api/v1/teams", headers=sup, json={"name": "切换测试团队"}).json()
        act = {**sup, "X-Act-Team": str(team["id"])}

        # 建用户不再必须显式 team_id（取作用团队）
        r = client.post(
            "/api/v1/users",
            headers=act,
            json={"username": "act_member", "password": PASSWORD, "display_name": "切换建"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["team_id"] == team["id"]

        # 团队域业务（建采集作业）以作用团队执行
        r2 = client.post(
            "/api/v1/scrape-jobs",
            headers=act,
            json={
                "source": "amazon",
                "job_kind": "product_detail",
                "input": {"targets": ["B0ACTTEAM1"]},
            },
        )
        assert r2.status_code == 201, r2.text
        # 收尾：取消作业（pending 任务判死），避免泄漏进其他模块的 worker 领取队列
        client.post(f"/api/v1/scrape-jobs/{r2.json()['id']}/cancel", headers=act)

        # 未切换 → 明确报错引导
        r3 = client.post(
            "/api/v1/users",
            headers=sup,
            json={"username": "act_member2", "password": PASSWORD, "display_name": "x"},
        )
        assert r3.status_code == 422
        assert r3.json()["error"]["code"] == "TEAM_REQUIRED"

    def test_invalid_act_team_rejected(self, client: TestClient) -> None:
        sup = _login(client, SUPER, PASSWORD)
        r = client.get("/api/v1/users", headers={**sup, "X-Act-Team": "999999"})
        assert r.status_code == 401

    def test_non_super_header_ignored(self, client: TestClient, migrated_db: str) -> None:
        member = _login(client, MEMBER, PASSWORD)
        me = client.get("/api/v1/me", headers=member).json()
        me2 = client.get("/api/v1/me", headers={**member, "X-Act-Team": "999999"}).json()
        assert me2["user"]["team_id"] == me["user"]["team_id"]  # 普通用户忽略该头
