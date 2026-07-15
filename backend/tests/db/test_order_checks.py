"""R2-05 增量2 验收：订单四检引擎 + 查询/重检/放行端点。

- phishing：地址/邮编黑名单双向 substring + 前 5 位邮编（BR-ORD-005）；
  flagged 未放行不可被重检覆盖（BR-ORD-006）；resolve 后重检可翻 pass。
- purchaser：无 active 采购方 → flagged（降档口径）。
- price_limit：货源价 > 单价×0.85×6.8÷汇率 → flagged（证据含差额行）。
- consistency：标题 ratio < 0.9 → flagged（渠道品名缓存进 detail 供 rerun）。
- 收口：has_flag / pulled→checked / order_flag 通知 / resolve 重算 has_flag。
"""

import json
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.core.db import get_session_factory, system_tx
from erp.core.security import hash_password
from erp.order import checks as order_checks

from .test_identity_api import PASSWORD, _login

ADMIN = "ordcheck_admin"
TEAM = "订单四检测试团队"


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM app.app_user WHERE username = %s", (ADMIN,))
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        uid = conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '四检管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute("DELETE FROM app.role WHERE team_id = %s AND name = 'ordcheck角色'",
                     (team_id,))  # fmt: skip
        role_id = conn.execute(
            "INSERT INTO app.role (team_id, name) VALUES (%s, 'ordcheck角色') RETURNING id",
            (team_id,),
        ).fetchone()[0]
        for code in ("order.read", "order.check"):
            conn.execute(
                "INSERT INTO app.role_permission (role_id, permission_code) VALUES (%s, %s)",
                (role_id, code),
            )
        conn.execute("INSERT INTO app.user_role (user_id, role_id) VALUES (%s, %s)", (uid, role_id))
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        conn.execute(
            "DELETE FROM app.store_credential WHERE store_id IN"
            " (SELECT id FROM app.store WHERE team_id = %s)",
            (team_id,),
        )
        for t in ("order_check", "order_line", "channel_order", "purchaser", "listing",
                  "product", "store"):  # fmt: skip
            conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.blacklist_address WHERE reason = 'ordcheck-test'")
        conn.execute("DELETE FROM app.blacklist_zip WHERE reason = 'ordcheck-test'")
        store_id = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, 'ORDCHK1', '四检测试店', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
        # 采购方（active，汇率 6.5）
        purchaser_id = conn.execute(
            "INSERT INTO app.purchaser (team_id, name, purchaser_kind, exchange_rate)"
            " VALUES (%s, '测试采购方', 'external', 6.5) RETURNING id",
            (team_id,),
        ).fetchone()[0]
        # 产品 + listing 回连（channel_sku M9100001；货源价 12.99）
        pid = conn.execute(
            "INSERT INTO app.product (team_id, source_channel, source_ref, title, attrs,"
            " price_snapshot, status)"
            " VALUES (%s, 'amazon', 'B0ORDCHK01', 'Nice Blue Mug Classic',"
            "  '{}', '{\"list\": 12.99}', 'audit_passed') RETURNING id",
            (team_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO app.listing (team_id, store_id, product_id, offer_mode, channel_sku)"
            " VALUES (%s, %s, %s, 'build', 'M9100001')",
            (team_id, store_id, pid),
        )
        # 钓鱼黑名单（全局）：地址 + 邮编
        conn.execute(
            "INSERT INTO app.blacklist_address (team_id, street_norm, reason, source)"
            " VALUES (NULL, %s, 'ordcheck-test', 'import')",
            (order_checks.normalize_street("666 Scam Street"),),
        )
        conn.execute(
            "INSERT INTO app.blacklist_zip (team_id, zip5, reason, source)"
            " VALUES (NULL, '66666', 'ordcheck-test', 'import')"
        )
    return {"team": team_id, "store": store_id, "purchaser": purchaser_id, "product": pid}


def _mk_order(
    conn,
    seeded: dict,
    po: str,
    *,
    address1: str = "123 Fake Street",
    zipcode: str = "78701",
    unit_price: float = 10.50,
    sku: str = "M9100001",
) -> tuple[int, str]:
    """直插一张 pulled 单（1 行）；返回 (order_id, order_date iso)。"""
    ship_to = {"name": "Buyer", "address1": address1, "postalCode": zipcode}
    row = conn.execute(
        "INSERT INTO app.channel_order (team_id, store_id, channel_order_no, order_date,"
        " channel_status, customer, ship_to, order_total, item_count, pulled_at)"
        " VALUES (%s, %s, %s, now() - interval '1 day', 'Created', '{}', %s, %s, 1, now())"
        " RETURNING id, order_date",
        (seeded["team"], seeded["store"], po, json.dumps(ship_to), unit_price),
    ).fetchone()
    conn.execute(
        "INSERT INTO app.order_line (order_id, order_date, team_id, channel_line_no,"
        " channel_sku, product_id, qty, unit_price, line_status)"
        " VALUES (%s, %s, %s, '1', %s,"
        "   (SELECT product_id FROM app.listing WHERE channel_sku = %s AND team_id = %s"
        "    LIMIT 1), 1, %s, 'created')",
        (row[0], row[1], seeded["team"], sku, sku, seeded["team"], unit_price),
    )
    return int(row[0]), row[1]


async def _run_checks(seeded: dict, order_id: int, order_date, titles: dict | None) -> dict:
    async with system_tx(get_session_factory()) as s:
        return await order_checks.run_order_checks(
            s,
            order_id=order_id,
            order_date=order_date,
            team_id=seeded["team"],
            channel_titles=titles,
        )


class TestChecksEngine:
    async def test_all_pass_and_status_advances(self, seeded: dict, migrated_db: str) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            oid, od = _mk_order(conn, seeded, f"OC-PASS-{uuid.uuid4().hex[:6]}", unit_price=10.50)
        # 货源 12.99 ≤ 限价？ 10.50×0.85×6.8÷6.5 = 9.34 → 12.99 超限……
        # 用低货源单测 pass：改单价拉高限价（unit_price=20 → 限价 17.78 > 12.99）
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute("UPDATE app.order_line SET unit_price = 20 WHERE order_id = %s", (oid,))
        results = await _run_checks(seeded, oid, od, {"1": "Nice Blue Mug Classic"})
        assert results == {
            "phishing": "pass", "purchaser": "pass",
            "price_limit": "pass", "consistency": "pass",
        }  # fmt: skip
        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT has_flag, internal_status FROM app.channel_order WHERE id = %s", (oid,)
            ).fetchone()
            assert row == (False, "checked")  # pulled→checked

    async def test_phishing_flagged_sticky_until_resolved(
        self, seeded: dict, migrated_db: str
    ) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            oid, od = _mk_order(
                conn, seeded, f"OC-PHISH-{uuid.uuid4().hex[:6]}",
                address1="Suite 5 666 scam street", zipcode="66666-9999", unit_price=20,
            )  # fmt: skip
        results = await _run_checks(seeded, oid, od, {"1": "Nice Blue Mug Classic"})
        assert results["phishing"] == "flagged"
        with psycopg.connect(migrated_db) as conn:
            detail, has_flag = conn.execute(
                "SELECT c.detail, o.has_flag FROM app.order_check c"
                " JOIN app.channel_order o ON o.id = c.order_id"
                " WHERE c.order_id = %s AND c.check_kind = 'phishing'",
                (oid,),
            ).fetchone()
            assert detail["level"] == "address+zip"  # 双命中三档证据
            assert has_flag is True
            assert (
                conn.execute(
                    "SELECT 1 FROM app.notification WHERE dedupe_key = %s",
                    (f"order_flag:{oid}",),
                ).fetchone()
                is not None
            )
        # BR-ORD-006：拿掉黑名单后重检——flagged 未放行不可覆盖
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.blacklist_address SET status = 'removed' WHERE reason = 'ordcheck-test'"
            )
            conn.execute(
                "UPDATE app.blacklist_zip SET status = 'removed' WHERE reason = 'ordcheck-test'"
            )
        results = await _run_checks(seeded, oid, od, None)
        assert results["phishing"] == "flagged"  # 依然 flagged（不可覆盖）
        # 人工放行后重检 → 可翻 pass
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.order_check SET resolved_by = 1, resolved_at = now()"
                " WHERE order_id = %s AND check_kind = 'phishing'",
                (oid,),
            )
        results = await _run_checks(seeded, oid, od, None)
        assert results["phishing"] == "pass"
        # 还原黑名单供后续模块（无后续依赖，保险起见）
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.blacklist_address SET status = 'active' WHERE reason = 'ordcheck-test'"
            )
            conn.execute(
                "UPDATE app.blacklist_zip SET status = 'active' WHERE reason = 'ordcheck-test'"
            )

    async def test_price_limit_and_consistency_flag_with_evidence(
        self, seeded: dict, migrated_db: str
    ) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            oid, od = _mk_order(conn, seeded, f"OC-PRICE-{uuid.uuid4().hex[:6]}", unit_price=10.50)
        # 限价 = 10.50×0.85×6.8÷6.5 ≈ 9.34 < 货源 12.99 → flagged；标题不符 → flagged
        results = await _run_checks(seeded, oid, od, {"1": "Totally Different Gadget XYZ"})
        assert results["price_limit"] == "flagged"
        assert results["consistency"] == "flagged"
        with psycopg.connect(migrated_db) as conn:
            pl = conn.execute(
                "SELECT detail FROM app.order_check WHERE order_id = %s"
                " AND check_kind = 'price_limit'",
                (oid,),
            ).fetchone()[0]
            assert pl["over"][0]["source"] == 12.99 and pl["over"][0]["limit"] > 0
            cs = conn.execute(
                "SELECT detail FROM app.order_check WHERE order_id = %s"
                " AND check_kind = 'consistency'",
                (oid,),
            ).fetchone()[0]
            assert cs["mismatches"][0]["ratio"] < 0.9
            assert cs["channel_titles"]["1"]  # 品名缓存供 rerun

    async def test_purchaser_flagged_when_none_active(self, seeded: dict, migrated_db: str) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.purchaser SET status = 'disabled' WHERE id = %s",
                (seeded["purchaser"],),
            )
            oid, od = _mk_order(conn, seeded, f"OC-NOPUR-{uuid.uuid4().hex[:6]}", unit_price=20)
        try:
            results = await _run_checks(seeded, oid, od, {"1": "Nice Blue Mug Classic"})
            assert results["purchaser"] == "flagged"
            assert results["price_limit"] == "pass"  # 无汇率跳过金额比较，由 purchaser 检兜底
        finally:
            with psycopg.connect(migrated_db, autocommit=True) as conn:
                conn.execute(
                    "UPDATE app.purchaser SET status = 'active' WHERE id = %s",
                    (seeded["purchaser"],),
                )


class TestOrderApi:
    def test_list_detail_rerun_resolve(self, seeded: dict, migrated_db: str) -> None:
        from erp.main import app

        client = TestClient(app)
        auth = _login(client, ADMIN, PASSWORD)
        po = f"OC-API-{uuid.uuid4().hex[:6]}"
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            oid, _od = _mk_order(
                conn, seeded, po, address1="666 Scam Street", zipcode="66666", unit_price=20
            )
        r = client.post(f"/api/v1/orders/{oid}/checks/rerun", headers=auth)
        assert r.status_code == 200, r.text
        assert r.json()["results"]["phishing"] == "flagged"

        r = client.get("/api/v1/orders", headers=auth, params={"has_flag": True})
        assert r.status_code == 200
        assert any(x["channel_order_no"] == po for x in r.json()["items"])

        r = client.get(f"/api/v1/orders/{oid}", headers=auth)
        body = r.json()
        assert body["has_flag"] is True and len(body["lines"]) == 1
        assert {c["check_kind"] for c in body["checks"]} == set(order_checks.CHECK_KINDS)

        r = client.post(f"/api/v1/orders/{oid}/checks/phishing/resolve", headers=auth)
        assert r.status_code == 200 and r.json()["resolved"] is True
        with psycopg.connect(migrated_db) as conn:
            has_flag = conn.execute(
                "SELECT has_flag FROM app.channel_order WHERE id = %s", (oid,)
            ).fetchone()[0]
            assert has_flag is False  # 唯一 flagged 项放行后重算
        # 重复放行 → 业务拒绝
        r = client.post(f"/api/v1/orders/{oid}/checks/phishing/resolve", headers=auth)
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "CHECK_NOT_FLAGGED"


class TestNormalizers:
    def test_street_and_zip_normalization(self) -> None:
        assert order_checks.normalize_street("  666 Scam St. #B ") == "666SCAMSTB"
        assert order_checks.normalize_zip5("78701-1234") == "78701"
        assert order_checks.normalize_zip5("TX 66666") == "66666"
