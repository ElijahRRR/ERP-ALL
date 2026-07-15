"""R2-05 增量3 验收：采购执行单双入口（D-Q50①②）+ 采购方 CRUD。

- 建档：internal 必绑成员；portal 字段本期拒绝（R2#6）。
- 流转：建单→分配→领单（锁汇率快照 D-Q32）→回填（backfilled + 订单 purchasing）。
- 改采购方汇率只影响未锁单（已锁单快照不变）。
- 运营代填：不分配直接回填 → backfill_actor_kind=op_direct。
- order_block 档位：semi/auto + flagged 未放行 → 建单/分配 409 冻结；manual 放行。
- mine=true：只看绑定自己的采购方的单（D-Q50①「我的单」）。
"""

import json
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.core.security import hash_password

from .test_identity_api import PASSWORD, _login

ADMIN = "proc_admin"
TEAM = "采购执行测试团队"


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        # purchaser 引用 app_user——先清业务表再删用户（重跑幂等）
        for t in ("shipment", "procurement_order", "order_check", "order_line",
                  "channel_order", "purchaser", "automation_policy", "store"):  # fmt: skip
            conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.app_user WHERE username = %s", (ADMIN,))
        uid = conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '采购执行管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute("DELETE FROM app.role WHERE team_id = %s AND name = 'proc角色'", (team_id,))
        role_id = conn.execute(
            "INSERT INTO app.role (team_id, name) VALUES (%s, 'proc角色') RETURNING id",
            (team_id,),
        ).fetchone()[0]
        for code in ("order.read", "order.check", "order.assign",
                     "procurement.read", "procurement.execute", "procurement.admin"):  # fmt: skip
            conn.execute(
                "INSERT INTO app.role_permission (role_id, permission_code) VALUES (%s, %s)",
                (role_id, code),
            )
        conn.execute("INSERT INTO app.user_role (user_id, role_id) VALUES (%s, %s)", (uid, role_id))
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        store_id = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, 'PROC1', '采购执行测试店', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
    return {"team": team_id, "user": uid, "store": store_id}


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> TestClient:
    from erp.main import app

    return TestClient(app)


def _mk_checked_order(conn, seeded: dict, po: str, *, flagged: bool = False) -> int:
    oid = conn.execute(
        "INSERT INTO app.channel_order (team_id, store_id, channel_order_no, order_date,"
        " channel_status, customer, ship_to, order_total, item_count, pulled_at,"
        " internal_status, has_flag)"
        " VALUES (%s, %s, %s, now() - interval '1 day', 'Created', '{}',"
        "         '{\"name\": \"B\"}', 10, 1, now(), 'checked', %s)"
        " RETURNING id",
        (seeded["team"], seeded["store"], po, flagged),
    ).fetchone()[0]
    if flagged:
        conn.execute(
            "INSERT INTO app.order_check (team_id, order_id, order_date, check_kind, result,"
            " detail) SELECT %s, id, order_date, 'phishing', 'flagged', '{}'"
            " FROM app.channel_order WHERE id = %s",
            (seeded["team"], oid),
        )
    return int(oid)


class TestPurchaserCrud:
    def test_create_patch_and_portal_refused(self, client: TestClient, seeded: dict) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        r = client.post(
            "/api/v1/purchasers",
            headers=auth,
            json={"name": "内部采购A", "purchaser_kind": "internal", "exchange_rate": 6.5},
        )
        assert r.status_code == 422  # internal 必绑成员
        assert r.json()["error"]["code"] == "PURCHASER_USER_REQUIRED"
        r = client.post(
            "/api/v1/purchasers",
            headers=auth,
            json={
                "name": "内部采购A", "purchaser_kind": "internal",
                "exchange_rate": 6.5, "user_id": seeded["user"],
            },
        )  # fmt: skip
        assert r.status_code == 201, r.text
        seeded["purchaser_internal"] = r.json()["id"]
        r = client.post(
            "/api/v1/purchasers",
            headers=auth,
            json={
                "name": "外部采购B", "purchaser_kind": "external", "exchange_rate": 7.0,
                "portal_username": "waibu",
            },
        )  # fmt: skip
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "PORTAL_NOT_AVAILABLE"
        r = client.post(
            "/api/v1/purchasers",
            headers=auth,
            json={"name": "外部采购B", "purchaser_kind": "external", "exchange_rate": 7.0},
        )
        assert r.status_code == 201
        seeded["purchaser_external"] = r.json()["id"]
        r = client.get("/api/v1/purchasers", headers=auth)
        assert {p["name"] for p in r.json()} >= {"内部采购A", "外部采购B"}


class TestProcurementFlow:
    def test_full_flow_with_rate_lock(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            oid = _mk_checked_order(conn, seeded, f"PROC-{uuid.uuid4().hex[:6]}")
        r = client.post("/api/v1/procurement-orders", headers=auth, json={"order_id": oid})
        assert r.status_code == 201, r.text
        po_id = r.json()["id"]

        r = client.post(
            f"/api/v1/procurement-orders/{po_id}/assign",
            headers=auth,
            json={"purchaser_id": seeded["purchaser_external"]},
        )
        assert r.status_code == 200, r.text
        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT status, assignee_kind, exchange_rate_locked"
                " FROM app.procurement_order WHERE id = %s",
                (po_id,),
            ).fetchone()
            assert row == ("assigned", "external", None)  # 分配不锁汇率
            assert (
                conn.execute(
                    "SELECT internal_status FROM app.channel_order WHERE id = %s", (oid,)
                ).fetchone()[0]
                == "assigned"
            )

        r = client.post(f"/api/v1/procurement-orders/{po_id}/claim", headers=auth)
        assert r.status_code == 200, r.text
        with psycopg.connect(migrated_db) as conn:
            locked = conn.execute(
                "SELECT exchange_rate_locked FROM app.procurement_order WHERE id = %s", (po_id,)
            ).fetchone()[0]
            assert float(locked) == 7.0  # 领单锁快照（D-Q32）

        # 改采购方汇率——已锁单不受影响
        r = client.patch(
            f"/api/v1/purchasers/{seeded['purchaser_external']}",
            headers=auth,
            json={"exchange_rate": 7.5},
        )
        assert r.status_code == 200
        with psycopg.connect(migrated_db) as conn:
            locked = conn.execute(
                "SELECT exchange_rate_locked FROM app.procurement_order WHERE id = %s", (po_id,)
            ).fetchone()[0]
            assert float(locked) == 7.0

        r = client.post(
            f"/api/v1/procurement-orders/{po_id}/backfill",
            headers=auth,
            json={
                "purchase_platform": "1688", "purchase_order_ref": "1688-XYZ",
                "purchase_cost": 45.80, "carrier": "YTO", "tracking_no": "YT123456",
            },
        )  # fmt: skip
        assert r.status_code == 200, r.text
        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT status, backfill_actor_kind, purchase_cost"
                " FROM app.procurement_order WHERE id = %s",
                (po_id,),
            ).fetchone()
            assert (row[0], row[1], float(row[2])) == ("backfilled", "internal", 45.80)
            assert (
                conn.execute(
                    "SELECT internal_status FROM app.channel_order WHERE id = %s", (oid,)
                ).fetchone()[0]
                == "purchasing"
            )

    def test_op_direct_backfill_without_assign(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """D-Q50②：不分配、运营直接代填 → op_direct。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            oid = _mk_checked_order(conn, seeded, f"PROC-OP-{uuid.uuid4().hex[:6]}")
        po_id = client.post(
            "/api/v1/procurement-orders", headers=auth, json={"order_id": oid}
        ).json()["id"]
        r = client.post(
            f"/api/v1/procurement-orders/{po_id}/backfill",
            headers=auth,
            json={"purchase_platform": "拼多多", "carrier": "ZTO", "tracking_no": "ZT001"},
        )
        assert r.status_code == 200, r.text
        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT status, backfill_actor_kind, assignee_kind"
                " FROM app.procurement_order WHERE id = %s",
                (po_id,),
            ).fetchone()
            assert row == ("backfilled", "op_direct", "none")

    def test_exception_and_mine_filter(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            oid = _mk_checked_order(conn, seeded, f"PROC-EX-{uuid.uuid4().hex[:6]}")
        po_id = client.post(
            "/api/v1/procurement-orders",
            headers=auth,
            json={"order_id": oid, "purchaser_id": seeded["purchaser_internal"]},
        ).json()["id"]
        r = client.post(
            f"/api/v1/procurement-orders/{po_id}/exception",
            headers=auth,
            json={"reason": "缺货"},
        )
        assert r.status_code == 200
        # mine=true：internal 采购方绑定了当前用户 → 能看到这单
        r = client.get("/api/v1/procurement-orders", headers=auth, params={"mine": True})
        assert any(x["id"] == po_id for x in r.json()["items"])
        r = client.get(
            "/api/v1/procurement-orders",
            headers=auth,
            params={"purchaser_id": seeded["purchaser_external"]},
        )
        assert all(x["purchaser_id"] == seeded["purchaser_external"] for x in r.json()["items"])


class TestOrderBlockGate:
    def test_semi_mode_freezes_flagged_order(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            oid = _mk_checked_order(conn, seeded, f"PROC-BLK-{uuid.uuid4().hex[:6]}", flagged=True)
            conn.execute(
                "INSERT INTO app.automation_policy (team_id, flow_code, mode, config)"
                " VALUES (%s, 'order_block', 'semi', %s)"
                " ON CONFLICT (team_id, flow_code) DO UPDATE SET mode = 'semi'",
                (seeded["team"], json.dumps({"check_kinds": ["phishing"]})),
            )
        try:
            r = client.post("/api/v1/procurement-orders", headers=auth, json={"order_id": oid})
            assert r.status_code == 409, r.text
            assert r.json()["error"]["code"] == "ORDER_BLOCKED"
            # 人工放行后可建单（manual 档同理）
            r = client.post(f"/api/v1/orders/{oid}/checks/phishing/resolve", headers=auth)
            assert r.status_code == 200
            r = client.post("/api/v1/procurement-orders", headers=auth, json={"order_id": oid})
            assert r.status_code == 201, r.text
        finally:
            with psycopg.connect(migrated_db, autocommit=True) as conn:
                conn.execute(
                    "UPDATE app.automation_policy SET mode = 'manual'"
                    " WHERE team_id = %s AND flow_code = 'order_block'",
                    (seeded["team"],),
                )
