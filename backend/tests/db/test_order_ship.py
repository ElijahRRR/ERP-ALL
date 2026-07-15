"""R2-05 增量4 验收：发货回传（L2「测试单全流程流转」的测试化锚点）。

- 全链：checked 单 → POST ship（Idempotency-Key）→ 自动先 ack 后 ship（FIFO 保序）
  → shipment pushed / 行 shipped+tracking / 订单 internal shipped / 命令 succeeded。
- 幂等重放：同键再发零发包、响应原样（idempotent_replay）。
- 明确拒：shipment failed + 命令 failed + notify + API 422 ERP_SHIP_REJECTED。
- 结果未知：verify_pending → ship_recon 渠道实况确认 → 落账（绝不重发）。
- 已 Acknowledged 单：跳过 ack（仅一次 POST shipping）。
"""

import uuid

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.automation import tasks
from erp.channel.gateway import gateway
from erp.core.db import get_session_factory
from erp.core.security import hash_password

from .test_identity_api import PASSWORD, _login

ADMIN = "ordship_admin"
TEAM = "发货回传测试团队"


class _FakeChannel:
    def __init__(self) -> None:
        self.routes: dict[str, httpx.Response | Exception] = {}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 900})
        self.requests.append(request)
        key = f"{request.method} {request.url.path}"
        for prefix in sorted(self.routes, key=len, reverse=True):
            if key.startswith(prefix):
                item = self.routes[prefix]
                if isinstance(item, Exception):
                    raise item
                return item
        return httpx.Response(200, json={"ok": True})


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    from erp.core.settings import get_settings

    key = get_settings().credential_key
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        conn.execute(
            "DELETE FROM app.store_credential WHERE store_id IN"
            " (SELECT id FROM app.store WHERE team_id = %s)",
            (team_id,),
        )
        for t in ("shipment", "procurement_order", "order_check", "order_line",
                  "channel_order", "api_idempotency", "channel_command", "store"):  # fmt: skip
            conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.app_user WHERE username = %s", (ADMIN,))
        uid = conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '发货管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute("DELETE FROM app.role WHERE team_id = %s AND name = 'ordship角色'",
                     (team_id,))  # fmt: skip
        role_id = conn.execute(
            "INSERT INTO app.role (team_id, name) VALUES (%s, 'ordship角色') RETURNING id",
            (team_id,),
        ).fetchone()[0]
        for code in ("order.read", "order.ship"):
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
            " VALUES (%s, %s, 'SHIP1', '发货测试店', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO app.store_credential (store_id, client_id, client_secret_encrypted)"
            " VALUES (%s, 'ship-client-01', pgp_sym_encrypt('ship-secret-01', %s))",
            (store_id, key),
        )
        conn.execute(
            "INSERT INTO app.system_config (key, value)"
            " VALUES ('channel.gateway_mode', '\"live_test\"'::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value"
        )
    return {"team": team_id, "store": store_id, "user": uid}


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> TestClient:
    from erp.main import app

    return TestClient(app)


@pytest.fixture()
def fake(seeded: dict[str, int]) -> _FakeChannel:
    f = _FakeChannel()
    gateway._clients.clear()
    gateway._transport_factory = lambda proxy: httpx.MockTransport(f.handler)
    yield f
    gateway._clients.clear()
    gateway._transport_factory = gateway._default_transport


def _idem(auth: dict) -> dict:
    return {**auth, "Idempotency-Key": str(uuid.uuid4())}


def _mk_order(
    conn, seeded: dict, po: str, *, channel_status: str = "Created", lines: int = 1
) -> int:
    oid = conn.execute(
        "INSERT INTO app.channel_order (team_id, store_id, channel_order_no, order_date,"
        " channel_status, customer, ship_to, order_total, item_count, pulled_at,"
        " internal_status)"
        " VALUES (%s, %s, %s, now() - interval '1 day', %s, '{}', '{\"name\":\"B\"}',"
        "         10, %s, now(), 'checked') RETURNING id",
        (seeded["team"], seeded["store"], po, channel_status, lines),
    ).fetchone()[0]
    for n in range(1, lines + 1):
        conn.execute(
            "INSERT INTO app.order_line (order_id, order_date, team_id, channel_line_no,"
            " channel_sku, qty, unit_price, line_status)"
            " SELECT id, order_date, %s, %s, 'M9200001', 1, 10, 'created'"
            " FROM app.channel_order WHERE id = %s",
            (seeded["team"], str(n), oid),
        )
    return int(oid)


def _ship(client: TestClient, headers: dict, oid: int) -> object:
    return client.post(
        f"/api/v1/orders/{oid}/ship",
        headers=headers,
        json={"lines": [{"line_no": "1", "qty": 1}], "carrier": "UPS", "tracking_no": "1Z777"},
    )


class TestShipHappyPath:
    def test_full_chain_ack_then_ship_then_writeback(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        po = f"SHIP-{uuid.uuid4().hex[:6]}"
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            oid = _mk_order(conn, seeded, po)
        fake.routes[f"POST /v3/orders/{po}/acknowledge"] = httpx.Response(200, json={"ok": 1})
        fake.routes[f"POST /v3/orders/{po}/shipping"] = httpx.Response(200, json={"ok": 1})
        headers = _idem(auth)
        r = _ship(client, headers, oid)
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["push_status"] == "pushed"
        # 先 ack 后 ship（FIFO 保序）
        paths = [x.url.path for x in fake.requests]
        assert paths == [f"/v3/orders/{po}/acknowledge", f"/v3/orders/{po}/shipping"]
        with psycopg.connect(migrated_db) as conn:
            line = conn.execute(
                "SELECT line_status, carrier, tracking_no FROM app.order_line WHERE order_id = %s",
                (oid,),
            ).fetchone()
            assert line == ("shipped", "UPS", "1Z777")
            assert (
                conn.execute(
                    "SELECT internal_status FROM app.channel_order WHERE id = %s", (oid,)
                ).fetchone()[0]
                == "shipped"
            )
            cmds = dict(
                conn.execute(
                    "SELECT action, status FROM app.channel_command WHERE object_id IN"
                    " (SELECT id FROM app.shipment WHERE order_id = %s) AND action='order_ship'"
                    " UNION SELECT action, status FROM app.channel_command"
                    " WHERE action='order_ack' AND object_id = %s",
                    (oid, oid),
                ).fetchall()
            )
            assert cmds == {"order_ack": "succeeded", "order_ship": "succeeded"}
        # 幂等重放：同键零发包、响应原样
        n_before = len(fake.requests)
        r2 = _ship(client, headers, oid)
        assert r2.status_code == 202
        replay = r2.json()
        assert replay.pop("idempotent_replay") is True
        assert replay == body
        assert len(fake.requests) == n_before

    def test_acknowledged_order_skips_ack(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        po = f"SHIPA-{uuid.uuid4().hex[:6]}"
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            oid = _mk_order(conn, seeded, po, channel_status="Acknowledged")
        fake.routes[f"POST /v3/orders/{po}/shipping"] = httpx.Response(200, json={"ok": 1})
        r = _ship(client, _idem(auth), oid)
        assert r.status_code == 202, r.text
        assert [x.url.path for x in fake.requests] == [f"/v3/orders/{po}/shipping"]


class TestShipFailurePaths:
    def test_channel_reject_fails_loud(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        po = f"SHIPR-{uuid.uuid4().hex[:6]}"
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            oid = _mk_order(conn, seeded, po, channel_status="Acknowledged")
        fake.routes[f"POST /v3/orders/{po}/shipping"] = httpx.Response(
            400, json={"errors": ["bad tracking"]}
        )
        r = _ship(client, _idem(auth), oid)
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "ERP_SHIP_REJECTED"
        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT push_status FROM app.shipment WHERE order_id = %s ORDER BY id DESC LIMIT 1",
                (oid,),
            ).fetchone()
            assert row[0] == "failed"
            assert (
                conn.execute(
                    "SELECT line_status FROM app.order_line WHERE order_id = %s", (oid,)
                ).fetchone()[0]
                == "created"  # 行未动
            )
            assert (
                conn.execute(
                    "SELECT 1 FROM app.notification WHERE dedupe_key LIKE 'ship_failed:%%'"
                    " AND team_id = %s",
                    (seeded["team"],),
                ).fetchone()
                is not None
            )

    async def test_unknown_result_then_recon_confirms(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """崩溃窗口：渠道收到但响应丢失 → verify_pending → ship_recon 实况确认落账。"""
        auth = _login(client, ADMIN, PASSWORD)
        po = f"SHIPU-{uuid.uuid4().hex[:6]}"
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            oid = _mk_order(conn, seeded, po, channel_status="Acknowledged")
        fake.routes[f"POST /v3/orders/{po}/shipping"] = httpx.ConnectTimeout("response lost")
        r = _ship(client, _idem(auth), oid)
        assert r.status_code == 202, r.text
        assert r.json()["push_status"] == "pending"
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            cmd_id, status = conn.execute(
                "SELECT id, status FROM app.channel_command WHERE action = 'order_ship'"
                " AND object_id IN (SELECT id FROM app.shipment WHERE order_id = %s)",
                (oid,),
            ).fetchone()
            assert status == "verify_pending"
            conn.execute(  # 越过滞留门槛
                "UPDATE app.channel_command SET updated_at = now() - interval '10 minutes'"
                " WHERE id = %s",
                (cmd_id,),
            )
        # 渠道实况：行已 Shipped（渠道确实收到了）
        del fake.routes[f"POST /v3/orders/{po}/shipping"]
        fake.routes[f"GET /v3/orders/{po}"] = httpx.Response(
            200,
            json={
                "order": {
                    "purchaseOrderId": po,
                    "orderLines": {
                        "orderLine": [
                            {
                                "lineNumber": "1",
                                "orderLineStatuses": {"orderLineStatus": [{"status": "Shipped"}]},
                            }
                        ]
                    },
                }
            },
        )
        stats = await tasks.ship_recon(get_session_factory(), {"min_age_s": 0, "grace_s": 3600})
        assert stats["succeeded"] >= 1
        with psycopg.connect(migrated_db) as conn:
            assert (
                conn.execute(
                    "SELECT status FROM app.channel_command WHERE id = %s", (cmd_id,)
                ).fetchone()[0]
                == "succeeded"
            )
            row = conn.execute(
                "SELECT push_status FROM app.shipment WHERE order_id = %s", (oid,)
            ).fetchone()
            assert row[0] == "pushed"
            assert (
                conn.execute(
                    "SELECT line_status FROM app.order_line WHERE order_id = %s", (oid,)
                ).fetchone()[0]
                == "shipped"
            )
        # 对账阶段零 POST 重发
        assert all(x.method == "GET" for x in fake.requests if x.url.path == f"/v3/orders/{po}")
