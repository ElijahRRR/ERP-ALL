"""R2-17 17d：订单人工指派给买家账号——插件路径的人工入口（自动派发休眠后的唯一派发口）。

| 判据 | 用例 |
|---|---|
| 指派 → 插件拉取闭环（含审计 + 渠道订单联动）
  | `test_assign_to_buyer_account_then_pull_closed_loop` |
| `AssignIn` 二选一：两个都给/都不给 ⇒ 422
  | `test_assign_requires_exactly_one_target` |
| 非 active 账号 / 不存在 / 跨团队 ⇒ 拒绝
  | `test_assign_rejects_unusable_accounts` |
| 与人工采购方双向互斥（先 release 才能换道）
  | `test_assign_mutual_exclusion_with_manual_purchaser` |
| 重指派＝换账号：新账号拉得到、旧账号拉不到
  | `test_reassign_switches_account` |

**不在本文件**：拉取语义细节与首见登记（`test_r2_13a_plugin_auth.py`）、共享 token
认证正反面（`test_r2_17c_shared_token.py`）、release 出边（`test_r2_13b_dispatch.py`）。
"""

import os
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.core.security import hash_password

from .test_identity_api import PASSWORD, _login

SHARED_TOKEN = "r17d-shared-token-0123456789abcdef"
ADMIN = "r17d_admin"
TEAM = "R2-17d 人工指派测试团队"
PLUGIN = "/api/v1/purchase-plugin"


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        for t in ("procurement_order", "order_line", "channel_order", "product",
                  "buyer_account", "purchaser", "store"):  # fmt: skip
            conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.app_user WHERE username LIKE 'r17d_%'")
        uid = conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '指派测试管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute("DELETE FROM app.role WHERE team_id = %s AND name = '17d测试角色'", (team_id,))
        role_id = conn.execute(
            "INSERT INTO app.role (team_id, name) VALUES (%s, '17d测试角色') RETURNING id",
            (team_id,),
        ).fetchone()[0]
        for code in ("order.read", "order.assign", "procurement.read"):
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
            " VALUES (%s, %s, 'PL17D', '指派测试店', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
        conn.execute("DELETE FROM app.system_config WHERE key = 'procurement.plugin_team_id'")
        conn.execute(
            "INSERT INTO app.system_config (key, value) VALUES"
            " ('procurement.plugin_team_id', to_jsonb(%s::bigint))",
            (team_id,),
        )
    return {"team": team_id, "user": uid, "store": store_id}


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> Iterator[TestClient]:
    os.environ["ERP_PLUGIN_SHARED_TOKEN"] = SHARED_TOKEN
    from erp.core.settings import get_settings

    get_settings.cache_clear()
    from erp.main import app

    try:
        with TestClient(app) as c:
            yield c
    finally:
        os.environ.pop("ERP_PLUGIN_SHARED_TOKEN", None)
        get_settings.cache_clear()


SHIP_TO = (
    '{"name": "Jane Roe", "phone": "+1-555-0100", "address1": "123 Main St",'
    ' "city": "Marion", "state": "AL", "postalCode": "36759", "country": "US"}'
)


def _mk_account(
    conn: psycopg.Connection, seeded: dict[str, int], *, status: str = "active"
) -> dict[str, Any]:
    tag = uuid.uuid4().hex[:8]
    customer_id = f"A{tag.upper()}"
    account_id = conn.execute(
        "INSERT INTO app.buyer_account (team_id, label, site, external_customer_id, status)"
        " VALUES (%s, %s, 'amazon_com', %s, %s) RETURNING id",
        (seeded["team"], f"指纹浏览器-{tag}", customer_id, status),
    ).fetchone()[0]
    return {"id": int(account_id), "customerId": customer_id}


def _mk_unassigned_po(conn: psycopg.Connection, seeded: dict[str, int]) -> dict[str, Any]:
    """一张四检已过的渠道订单 + 一张待指派执行单（`unassigned`，internal_status=checked）。"""
    tag = uuid.uuid4().hex[:8]
    order = conn.execute(
        "INSERT INTO app.channel_order (team_id, store_id, channel_order_no, order_date,"
        " channel_status, customer, ship_to, order_total, item_count, pulled_at,"
        " internal_status, has_flag)"
        " VALUES (%s, %s, %s, now() - interval '1 day', 'Created', '{}',"
        "         %s::jsonb, 10, 1, now(), 'checked', false) RETURNING id, order_date",
        (seeded["team"], seeded["store"], f"PL17D-{tag}", SHIP_TO),
    ).fetchone()
    product_id = conn.execute(
        "INSERT INTO app.product (team_id, source_channel, source_ref, title)"
        " VALUES (%s, 'amazon', %s, '17d测试商品') RETURNING id",
        (seeded["team"], f"B017D-{tag}"),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO app.order_line (order_id, order_date, team_id, channel_line_no,"
        " channel_sku, product_id, qty, unit_price, line_status)"
        " VALUES (%s, %s, %s, '1', 'M0000001', %s, 1, 9.99, 'created')",
        (order[0], order[1], seeded["team"], product_id),
    )
    po = int(
        conn.execute(
            "INSERT INTO app.procurement_order (team_id, store_id, order_id, order_date,"
            " status, assignee_kind) VALUES (%s, %s, %s, %s, 'unassigned', 'none') RETURNING id",
            (seeded["team"], seeded["store"], order[0], order[1]),
        ).fetchone()[0]
    )
    return {"po": po, "order": int(order[0])}


def _assign(client: TestClient, auth: dict[str, str], po_id: int, **body: Any) -> Any:
    return client.post(f"/api/v1/procurement-orders/{po_id}/assign", headers=auth, json=body)


def _pull(client: TestClient, customer_id: str) -> Any:
    return client.get(
        f"{PLUGIN}/getNeedPurchaseOrders",
        headers={"X-Plugin-Token": SHARED_TOKEN},
        params={"customerId": customer_id},
    )


class TestManualAssign:
    def test_assign_to_buyer_account_then_pull_closed_loop(
        self, client: TestClient, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        """指派 → 拉取的闭环：这是 17d 的产品主链，四件事一次钉住。

        ① 指派落库（buyer_account_id + status=assigned + assigned_by/at）；
        ② 渠道订单联动 checked → assigned（与 purchaser 指派同一条 advance）；
        ③ 审计 `procurement.assign` 带 buyer_account_id（人工流可辨，验收②的人工腿）；
        ④ 插件按 customerId 拉到这张单。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            account = _mk_account(conn, seeded)
            env = _mk_unassigned_po(conn, seeded)

        r = _assign(client, auth, env["po"], buyer_account_id=account["id"])
        assert r.status_code == 200, r.text
        assert r.json() == {"id": env["po"], "status": "assigned"}

        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT buyer_account_id, purchaser_id, status, assignee_kind, assigned_by,"
                " assigned_at FROM app.procurement_order WHERE id = %s",
                (env["po"],),
            ).fetchone()
            assert row[0] == account["id"] and row[1] is None
            assert row[2] == "assigned" and row[3] == "none"
            assert row[4] == seeded["user"] and row[5] is not None
            assert conn.execute(
                "SELECT internal_status FROM app.channel_order WHERE id = %s", (env["order"],)
            ).fetchone()[0] == "assigned", "渠道订单没跟着推进"  # fmt: skip
            audit = conn.execute(
                "SELECT actor_type, actor_id, after FROM app.audit_log WHERE team_id = %s"
                " AND action = 'procurement.assign' AND object_id = %s",
                (seeded["team"], str(env["po"])),
            ).fetchall()
        assert len(audit) == 1
        assert audit[0][0] == "user" and audit[0][1] == seeded["user"]
        assert audit[0][2]["buyer_account_id"] == account["id"]

        pulled = _pull(client, account["customerId"])
        assert pulled.status_code == 200, pulled.text
        assert env["po"] in [t["id"] for t in pulled.json()["data"]], "指派的单插件拉不到"

    def test_assign_requires_exactly_one_target(
        self, client: TestClient, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        """二选一校验：都给 / 都不给一律 422（pydantic 层，不进服务）。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            account = _mk_account(conn, seeded)
            env = _mk_unassigned_po(conn, seeded)
        both = _assign(client, auth, env["po"], purchaser_id=1, buyer_account_id=account["id"])
        neither = _assign(client, auth, env["po"])
        assert both.status_code == neither.status_code == 422, (both.text, neither.text)

    def test_assign_rejects_unusable_accounts(
        self, client: TestClient, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        """paused 账号拒（指派了也永远拉不走——在入口就说清）；不存在的 id 拒。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            paused = _mk_account(conn, seeded, status="paused")
            env = _mk_unassigned_po(conn, seeded)

        r = _assign(client, auth, env["po"], buyer_account_id=paused["id"])
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "BUYER_ACCOUNT_NOT_ACTIVE"

        ghost = _assign(client, auth, env["po"], buyer_account_id=2_000_000_111)
        assert ghost.status_code == 422, ghost.text
        assert ghost.json()["error"]["code"] == "BUYER_ACCOUNT_NOT_FOUND"

        with psycopg.connect(migrated_db) as conn:
            assert conn.execute(
                "SELECT status, buyer_account_id FROM app.procurement_order WHERE id = %s",
                (env["po"],),
            ).fetchone() == ("unassigned", None), "拒绝的指派不得留下任何写入"  # fmt: skip

    def test_assign_mutual_exclusion_with_manual_purchaser(
        self, client: TestClient, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        """与人工采购方**双向互斥**：两头各买一次是资金事故，换道必须先走 /release。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            account = _mk_account(conn, seeded)
            manual = _mk_unassigned_po(conn, seeded)
            plugin_side = _mk_unassigned_po(conn, seeded)
            purchaser_id = int(
                conn.execute(
                    "INSERT INTO app.purchaser (team_id, name, purchaser_kind, exchange_rate)"
                    " VALUES (%s, %s, 'internal', 7.2) RETURNING id",
                    (seeded["team"], f"采购员-{uuid.uuid4().hex[:6]}"),
                ).fetchone()[0]
            )

        # 人工在位 → 指派买家账号 409
        assert _assign(client, auth, manual["po"], purchaser_id=purchaser_id).status_code == 200
        r = _assign(client, auth, manual["po"], buyer_account_id=account["id"])
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "PROCUREMENT_MANUAL_ASSIGNED"

        # 买家账号在位 → 指派人工 409（既有 _reject_if_plugin_dispatched，反向验一次）
        assert (
            _assign(client, auth, plugin_side["po"], buyer_account_id=account["id"]).status_code
            == 200
        )
        r2 = _assign(client, auth, plugin_side["po"], purchaser_id=purchaser_id)
        assert r2.status_code == 409, r2.text
        assert r2.json()["error"]["code"] == "PROCUREMENT_PLUGIN_DISPATCHED"

    def test_reassign_switches_account(
        self, client: TestClient, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        """重指派＝换账号：B 拉得到、A 拉不到（残余风险的口径见服务层 docstring）。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            acc_a, acc_b = _mk_account(conn, seeded), _mk_account(conn, seeded)
            env = _mk_unassigned_po(conn, seeded)

        assert _assign(client, auth, env["po"], buyer_account_id=acc_a["id"]).status_code == 200
        assert _assign(client, auth, env["po"], buyer_account_id=acc_b["id"]).status_code == 200

        assert env["po"] in [t["id"] for t in _pull(client, acc_b["customerId"]).json()["data"]]
        assert env["po"] not in [
            t["id"] for t in _pull(client, acc_a["customerId"]).json()["data"]
        ], "换账号后旧账号还拉得到——两台浏览器会各买一次"
