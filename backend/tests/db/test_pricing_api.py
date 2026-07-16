"""R2-06 增量1/2：定价策略注册表 CRUD + 重定价预览。

增量1：活跃唯一（team×store×offer_mode）→ 409；min_price 可选（填了须 >0，D-Q62 补充）；
params 每改 version +1；停用旧策略后可建新活跃策略。
增量2：/pricing-strategies/preview 只读试算（clamp 版展示 + 严格判定并列；
store 级策略优先 team 级；无策略 422 PRICING_STRATEGY_NOT_FOUND）。
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
        # 清场（FK 顺序：价史→策略→产品→店）
        conn.execute("DELETE FROM app.price_history WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.pricing_strategy WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.product WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.store WHERE team_id = %s", (team_id,))
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
        # 预览用店 + 产品（增量2）：在区间 19.99 / 低于区间下界 5.0
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        store_id = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, 'A152P', '定价预览店', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
        p_in = conn.execute(
            "INSERT INTO app.product (team_id, source_channel, source_ref, title,"
            " price_snapshot, status, attrs) VALUES (%s, 'amazon', 'B0PVIEW001', '预览在区间',"
            " '{\"list\": 19.99}', 'ready', '{\"is_fba\": \"No\"}') RETURNING id",
            (team_id,),
        ).fetchone()[0]
        p_low = conn.execute(
            "INSERT INTO app.product (team_id, source_channel, source_ref, title,"
            " price_snapshot, status, attrs) VALUES (%s, 'amazon', 'B0PVIEW002', '预览区间外',"
            " '{\"list\": 5.0}', 'ready', '{\"is_fba\": \"No\"}') RETURNING id",
            (team_id,),
        ).fetchone()[0]
    return {"team": team_id, "store": store_id, "p_in": p_in, "p_low": p_low}


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

        # min_price 可选（D-Q62 补充）：缺省可建；填了必须 >0
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
        assert r3.status_code == 201, r3.text
        r3b = client.patch(
            f"/api/v1/pricing-strategies/{r3.json()['id']}",
            headers=auth,
            json={"params": {"min_price": 0}},
        )
        assert r3b.status_code == 422
        assert r3b.json()["error"]["code"] == "PRICING_MIN_PRICE_INVALID"
        # 用完即停用——防活跃 match 策略污染后续「无策略」预览用例
        assert (
            client.patch(
                f"/api/v1/pricing-strategies/{r3.json()['id']}",
                headers=auth,
                json={"status": "disabled"},
            ).status_code
            == 200
        )

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


class TestPreview:
    """R2-06 增量2：/pricing-strategies/preview 只读试算。"""

    def test_preview_with_clamp_flags(self, client: TestClient, seeded: dict) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        # 店铺级策略——预览应选它而非 team 级（store 级 > team 级）
        r = client.post(
            "/api/v1/pricing-strategies",
            headers=auth,
            json={
                "store_id": seeded["store"],
                "offer_mode": "build",
                "name": "店铺预览策略",
                "algo_code": "cost_plus",
                "params": {"bands": _BANDS, "min_price": 6.99},
            },
        )
        assert r.status_code == 201, r.text
        sid = r.json()["id"]

        r2 = client.post(
            "/api/v1/pricing-strategies/preview",
            headers=auth,
            json={
                "store_id": seeded["store"],
                "offer_mode": "build",
                "product_ids": [seeded["p_in"], seeded["p_low"], 99999999],
            },
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["strategy"]["id"] == sid  # store 级优先
        assert body["strategy"]["algo_code"] == "cost_plus"
        items = {it["product_id"]: it for it in body["items"]}
        # 在区间：FBM 19.99 × 2.75 = 54.97，严格判定 ok
        ok_item = items[seeded["p_in"]]
        assert ok_item["ok"] is True and ok_item["new_price"] == 54.97
        assert ok_item["detail"]["out_of_band"] is False
        # 区间外（低于 FBM 下界 15）：clamp 低段倍数出参考价 5×2.75=13.75，
        # 严格判定 out_of_band（上架链不出价）——detail 带 clamp 标记
        low = items[seeded["p_low"]]
        assert low["ok"] is False and low["reason"] == "out_of_band"
        assert low["new_price"] == 13.75
        assert low["detail"]["out_of_band"] is True and low["detail"]["clamp"] == "low"
        # 不存在的产品逐条报告
        assert items[99999999]["reason"] == "not_found"

    def test_preview_no_strategy_422(self, client: TestClient, seeded: dict) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        # 本模块从未成功创建 match 策略 → 无活跃策略
        r = client.post(
            "/api/v1/pricing-strategies/preview",
            headers=auth,
            json={
                "store_id": seeded["store"],
                "offer_mode": "match",
                "product_ids": [seeded["p_in"]],
            },
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "PRICING_STRATEGY_NOT_FOUND"

    def test_preview_input_exclusive(self, client: TestClient, seeded: dict) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        r = client.post(
            "/api/v1/pricing-strategies/preview",
            headers=auth,
            json={"store_id": seeded["store"], "offer_mode": "build"},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "PRICING_PREVIEW_INPUT_INVALID"
