"""R2-07 增量2 验收：refund_request 两档本地闭环（D-Q29 record/approval）。

- 档位快照随建：无策略/manual → record（recorded 终态）；semi → approval（pending_approval）；
  auto → fail-closed 拒绝（REFUND_AUTO_NOT_WIRED，R2-09 接线前不做静默降档）。
- 审批流：pending_approval → approve/reject（refund.approve 权限）；重复裁决拒绝。
- reason_code 走 sys_dict(refund_reason) 字典校验（fail-closed）。
- Idempotency-Key 重放：同键返回同结果不重复建行。
- 越权：他队申请不可见、不可裁决。
"""

import uuid

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.core.security import hash_password

from .conftest import APP_URL
from .test_identity_api import PASSWORD, _login

REQUESTER = "refund_requester"
APPROVER = "refund_approver"
TEAM = "退款申请测试团队"


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    """团队 + 订单员（refund.request）+ 团队管理员（refund.approve）+ 一张渠道订单。"""
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        for t in ("refund_request", "channel_order", "store"):
            conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.automation_policy WHERE team_id = %s", (team_id,))
        store_id = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, 'REFREQ1', '退款申请测试店', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
        order_id = conn.execute(
            "INSERT INTO app.channel_order (team_id, store_id, channel_order_no, order_date,"
            " channel_status, ship_to, order_total, item_count, pulled_at)"
            " VALUES (%s, %s, 'PO-REF-1', now() - interval '5 day', 'Delivered',"
            "         '{}', 42.00, 2, now()) RETURNING id",
            (team_id, store_id),
        ).fetchone()[0]
        # 两个角色用户：订单员=request；团队管理员=request+approve（0031 角色挂载）
        users = {}
        for username, role in ((REQUESTER, "订单员"), (APPROVER, "团队管理员")):
            conn.execute("DELETE FROM app.app_user WHERE username = %s", (username,))
            uid = conn.execute(
                "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
                " VALUES (%s, %s, %s, %s) RETURNING id",
                (team_id, username, hash_password(PASSWORD), username),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO app.role (team_id, name, description)
                SELECT %s, name, description FROM app.role tpl
                WHERE tpl.team_id IS NULL AND tpl.name = %s
                  AND NOT EXISTS (SELECT 1 FROM app.role r
                                  WHERE r.team_id = %s AND r.name = %s)
                """,
                (team_id, role, team_id, role),
            )
            conn.execute(
                """
                INSERT INTO app.role_permission (role_id, permission_code)
                SELECT r.id, rp.permission_code FROM app.role r
                JOIN app.role tpl ON tpl.team_id IS NULL AND tpl.name = r.name
                JOIN app.role_permission rp ON rp.role_id = tpl.id
                WHERE r.team_id = %s AND r.name = %s
                ON CONFLICT DO NOTHING
                """,
                (team_id, role),
            )
            conn.execute(
                """
                INSERT INTO app.user_role (user_id, role_id)
                SELECT %s, r.id FROM app.role r WHERE r.team_id = %s AND r.name = %s
                ON CONFLICT DO NOTHING
                """,
                (uid, team_id, role),
            )
            users[username] = uid
    return {"team": team_id, "store": store_id, "order": order_id, **users}


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> TestClient:
    import os

    os.environ["ERP_DATABASE_URL"] = APP_URL
    from erp.core.db import get_session_factory as gsf
    from erp.core.settings import get_settings as gs

    gs.cache_clear()
    gsf.cache_clear()
    from erp.main import create_app

    return TestClient(create_app())


def _create(client: TestClient, h: dict, order_id: int, **overrides) -> httpx.Response:
    idem = overrides.pop("idem", str(uuid.uuid4()))
    body = {
        "order_id": order_id,
        "kind": "refund",
        "amount": 21.0,
        "reason_code": "damaged_item",
        **overrides,
    }
    return client.post("/api/v1/refund-requests", json=body, headers={**h, "Idempotency-Key": idem})


def _set_policy(migrated_db: str, team_id: int, flow: str, mode: str) -> None:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.automation_policy (team_id, flow_code, mode)"
            " VALUES (%s, %s, %s)"
            " ON CONFLICT (team_id, flow_code) DO UPDATE SET mode = excluded.mode",
            (team_id, flow, mode),
        )


class TestRefundRequestFlow:
    def test_no_policy_defaults_to_record(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        h = _login(client, REQUESTER, PASSWORD)
        r = _create(client, h, seeded["order"])
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["mode_applied"] == "record" and data["status"] == "recorded"
        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT kind, amount, currency, status, requested_by_kind"
                " FROM app.refund_request WHERE id = %s",
                (data["id"],),
            ).fetchone()
            assert row == ("refund", 21.00, "USD", "recorded", "user")

    def test_semi_policy_approval_flow(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        _set_policy(migrated_db, seeded["team"], "refund", "semi")
        h = _login(client, REQUESTER, PASSWORD)
        r = _create(client, h, seeded["order"])
        assert r.status_code == 201
        req = r.json()
        assert req["mode_applied"] == "approval" and req["status"] == "pending_approval"

        # 订单员无 refund.approve → 403
        assert (
            client.post(f"/api/v1/refund-requests/{req['id']}/approve", headers=h).status_code
            == 403
        )
        ha = _login(client, APPROVER, PASSWORD)
        ok = client.post(f"/api/v1/refund-requests/{req['id']}/approve", headers=ha)
        assert ok.status_code == 200 and ok.json()["status"] == "approved"
        # 重复裁决拒绝
        again = client.post(f"/api/v1/refund-requests/{req['id']}/reject", headers=ha)
        assert again.status_code == 422
        assert again.json()["error"]["code"] == "REFUND_REQUEST_NOT_PENDING"
        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT status, approved_by, decided_at FROM app.refund_request WHERE id = %s",
                (req["id"],),
            ).fetchone()
            assert row[0] == "approved" and row[1] == seeded[APPROVER]
            assert row[2] is not None

    def test_reject_flow(self, client: TestClient, seeded: dict, migrated_db: str) -> None:
        _set_policy(migrated_db, seeded["team"], "cancel", "semi")
        h = _login(client, REQUESTER, PASSWORD)
        req = _create(client, h, seeded["order"], kind="cancel", reason_code="other").json()
        assert req["status"] == "pending_approval"
        ha = _login(client, APPROVER, PASSWORD)
        r = client.post(f"/api/v1/refund-requests/{req['id']}/reject", headers=ha)
        assert r.status_code == 200 and r.json()["status"] == "rejected"

    def test_auto_policy_fail_closed(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """R2-09 接线前 auto 档拒绝创建，不做静默降档。"""
        _set_policy(migrated_db, seeded["team"], "refund", "auto")
        h = _login(client, REQUESTER, PASSWORD)
        r = _create(client, h, seeded["order"])
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "REFUND_AUTO_NOT_WIRED"
        _set_policy(migrated_db, seeded["team"], "refund", "manual")  # 还原

    def test_reason_code_dictionary_gate(self, client: TestClient, seeded: dict) -> None:
        h = _login(client, REQUESTER, PASSWORD)
        r = _create(client, h, seeded["order"], reason_code="nonsense")
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "REASON_CODE_UNKNOWN"

    def test_idempotency_key_replay(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        h = _login(client, REQUESTER, PASSWORD)
        key = f"idem-{uuid.uuid4()}"
        body = {
            "order_id": seeded["order"],
            "kind": "refund",
            "amount": 5.0,
            "reason_code": "price_adjust",
        }
        first = client.post(
            "/api/v1/refund-requests", json=body, headers={**h, "Idempotency-Key": key}
        )
        replay = client.post(
            "/api/v1/refund-requests", json=body, headers={**h, "Idempotency-Key": key}
        )
        assert first.json()["id"] == replay.json()["id"]
        with psycopg.connect(migrated_db) as conn:
            n = conn.execute(
                "SELECT count(*) FROM app.refund_request WHERE id = %s",
                (first.json()["id"],),
            ).fetchone()[0]
            assert n == 1

    def test_cross_team_isolated(self, client: TestClient, seeded: dict, migrated_db: str) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.team (name) VALUES ('退款隔离他队') ON CONFLICT (name) DO NOTHING"
            )
            other_team = conn.execute(
                "SELECT id FROM app.team WHERE name = '退款隔离他队'"
            ).fetchone()[0]
            other_req = conn.execute(
                "INSERT INTO app.refund_request (team_id, store_id, order_id, order_date,"
                " kind, amount, reason_code, mode_applied, status, requested_by_kind,"
                " requested_by)"
                " VALUES (%s, %s, %s, now(), 'refund', 1.00, 'other', 'approval',"
                "         'pending_approval', 'system', 0) RETURNING id",
                (other_team, seeded["store"], seeded["order"]),
            ).fetchone()[0]
        ha = _login(client, APPROVER, PASSWORD)
        r = client.post(f"/api/v1/refund-requests/{other_req}/approve", headers=ha)
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "REFUND_REQUEST_NOT_FOUND"
        assert not any(
            i["id"] == other_req
            for i in client.get("/api/v1/refund-requests", headers=ha).json()["items"]
        )
