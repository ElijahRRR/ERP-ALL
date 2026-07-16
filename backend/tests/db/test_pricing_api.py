"""R2-06 增量1：定价策略注册表 CRUD（契约 /pricing-strategies 三端点，D-Q23）。

活跃唯一（team×store×offer_mode）→ 409；min_price 硬底线必填 fail-closed；
params 每改 version +1；停用旧策略后可建新活跃策略。
"""

import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.core.security import hash_password

from .test_identity_api import PASSWORD, _login

ADMIN = "pricing_admin"
TEAM = "定价测试团队"

_BANDS = {"FBA": [[0, 30, 2.75], [30, 80, "250%"]], "FBM": [[15, 80, 2.75], [80, 1000, 2.2]]}


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM app.app_user WHERE username = %s", (ADMIN,))
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        conn.execute("DELETE FROM app.pricing_strategy WHERE team_id = %s", (team_id,))
        uid = conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '定价管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute(
            "DELETE FROM app.role WHERE team_id = %s AND name = '定价测试角色'", (team_id,)
        )
        role_id = conn.execute(
            "INSERT INTO app.role (team_id, name) VALUES (%s, '定价测试角色') RETURNING id",
            (team_id,),
        ).fetchone()[0]
        for code in ("pricing.read", "pricing.write"):
            conn.execute(
                "INSERT INTO app.role_permission (role_id, permission_code) VALUES (%s, %s)",
                (role_id, code),
            )
        conn.execute("INSERT INTO app.user_role (user_id, role_id) VALUES (%s, %s)", (uid, role_id))
    return {"team": team_id}


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> TestClient:
    from erp.main import create_app

    return TestClient(create_app())


class TestStrategyCrud:
    def test_create_list_conflict_and_version(self, client: TestClient, seeded: dict) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        r = client.post(
            "/api/v1/pricing-strategies",
            headers=auth,
            json={
                "offer_mode": "build",
                "name": "建品默认",
                "algo_code": "cost_plus",
                "params": {"bands": _BANDS, "min_price": 6.99},
            },
        )
        assert r.status_code == 201, r.text
        sid = r.json()["id"]

        # 活跃唯一：同 (team×NULL店×build) 再建 → 409
        r2 = client.post(
            "/api/v1/pricing-strategies",
            headers=auth,
            json={
                "offer_mode": "build",
                "name": "重复策略",
                "algo_code": "cost_plus",
                "params": {"bands": _BANDS, "min_price": 1},
            },
        )
        assert r2.status_code == 409
        assert r2.json()["error"]["code"] == "PRICING_STRATEGY_CONFLICT"

        # min_price 缺失 fail-closed
        r3 = client.post(
            "/api/v1/pricing-strategies",
            headers=auth,
            json={
                "offer_mode": "match",
                "name": "无底线",
                "algo_code": "manual",
                "params": {},
            },
        )
        assert r3.status_code == 422
        assert r3.json()["error"]["code"] == "PRICING_MIN_PRICE_REQUIRED"

        # cost_plus 缺 bands
        r4 = client.post(
            "/api/v1/pricing-strategies",
            headers=auth,
            json={
                "offer_mode": "match",
                "name": "缺区间",
                "algo_code": "cost_plus",
                "params": {"min_price": 5},
            },
        )
        assert r4.status_code == 422 and r4.json()["error"]["code"] == "PRICING_BANDS_REQUIRED"

        # PATCH params → version +1
        r5 = client.patch(
            f"/api/v1/pricing-strategies/{sid}",
            headers=auth,
            json={"params": {"bands": _BANDS, "min_price": 8.99}},
        )
        assert r5.status_code == 200 and r5.json()["version"] == 2

        # 停用后可建新活跃
        r6 = client.patch(
            f"/api/v1/pricing-strategies/{sid}", headers=auth, json={"status": "disabled"}
        )
        assert r6.status_code == 200
        r7 = client.post(
            "/api/v1/pricing-strategies",
            headers=auth,
            json={
                "offer_mode": "build",
                "name": "接任策略",
                "algo_code": "cost_plus",
                "params": {"bands": _BANDS, "min_price": 6.99},
            },
        )
        assert r7.status_code == 201, r7.text

        listed = client.get("/api/v1/pricing-strategies", headers=auth).json()
        assert {s["name"] for s in listed} >= {"建品默认", "接任策略"}
        assert all("version" in s and "params" in s for s in listed)

    def test_algo_whitelist(self, client: TestClient, seeded: dict) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        r = client.post(
            "/api/v1/pricing-strategies",
            headers=auth,
            json={
                "offer_mode": "match",
                "name": "竞价",
                "algo_code": "follow_buybox",
                "params": {"min_price": 5},
            },
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "PRICING_ALGO_INVALID"
