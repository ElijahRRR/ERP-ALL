"""R2-06 增量2 接线验收：allocate 自动算价 + 改价守护（min_price / 超阈 force）。

种子：team + walmart_us 测试店 + team 级 cost_plus 策略（bands + min_price 6.99）
+ 产品三枚（在区间 19.99 / 区间外 5.0 / 无源价）。allocate 走 build 模式
（GTIN 经导入 API 预热）；本文件不触达渠道——纯 DB 接线验证。

验收点：①allocate 自动算价（current_price=公式值 + price_history initial 行）
②区间外/无源价 fail-closed 拒绝（PRICING_OUT_OF_BAND / PRICING_NO_SOURCE_PRICE，
BR-PR-004）③人工改价低于 min_price 拒（PRICING_BELOW_MIN_PRICE）④变动超 30%
需 force、force=true 通过且 price_history 有 manual 行（BR-PR-008）。
"""

import json
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.core.security import hash_password

from .test_identity_api import PASSWORD, _login

ADMIN = "pricing_wire_admin"
TEAM = "定价接线团队"
STORE_CODE = "A152W"

_BANDS = {"FBA": [[0, 30, 2.75], [30, 80, 2.5]], "FBM": [[15, 80, 2.75], [80, 1000, 2.2]]}


def _idem(auth: dict) -> dict:
    """写端点必填 Idempotency-Key（契约 002）——每次调用新键。"""
    return {**auth, "Idempotency-Key": str(uuid.uuid4())}


def _ean13(seed: int) -> str:
    body = f"69{seed:010d}"
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(body))
    return body + str((10 - total % 10) % 10)


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
            " VALUES (%s, %s, %s, '定价接线管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute(
            "DELETE FROM app.role WHERE team_id = %s AND name = '定价接线角色'", (team_id,)
        )
        role_id = conn.execute(
            "INSERT INTO app.role (team_id, name) VALUES (%s, '定价接线角色') RETURNING id",
            (team_id,),
        ).fetchone()[0]
        for code in (
            "listing.read", "listing.allocate", "listing.submit",
            "catalog.gtin_read", "catalog.import_write", "pricing.read",
        ):  # fmt: skip
            conn.execute(
                "INSERT INTO app.role_permission (role_id, permission_code) VALUES (%s, %s)",
                (role_id, code),
            )
        conn.execute("INSERT INTO app.user_role (user_id, role_id) VALUES (%s, %s)", (uid, role_id))
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        # 清场（FK 顺序：价史/状态史→listing→策略→GTIN→产品→店）
        conn.execute("DELETE FROM app.price_history WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.listing_state_history WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.api_idempotency WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.listing WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.pricing_strategy WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.gtin_pool WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.product WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.store WHERE team_id = %s", (team_id,))
        store_id = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, %s, '定价接线店', true) RETURNING id",
            (team_id, channel_id, STORE_CODE),
        ).fetchone()[0]
        strategy_id = conn.execute(
            "INSERT INTO app.pricing_strategy"
            " (team_id, offer_mode, name, algo_code, params, created_by)"
            " VALUES (%s, 'build', '接线默认', 'cost_plus', %s, %s) RETURNING id",
            (team_id, json.dumps({"bands": _BANDS, "min_price": 6.99}), uid),
        ).fetchone()[0]
        pids = {}
        for key, sku, ref, title, snap, attrs in (
            (
                "p_in",
                "MW260716A",
                "B0WIRE0001",
                "接线在区间 19.99",
                '{"list": 19.99}',
                '{"is_fba": "No"}',
            ),
            (
                "p_oob",
                "MW260716B",
                "B0WIRE0002",
                "接线区间外 5.0",
                '{"list": 5.0}',
                '{"is_fba": "No"}',
            ),
            ("p_nosrc", "MW260716C", "B0WIRE0003", "接线无源价", None, '{"is_fba": "No"}'),
            (
                "p_fba",
                "MW260716D",
                "B0WIRE0004",
                "接线FBA 19.99",
                '{"list": 19.99}',
                '{"is_fba": "Yes"}',
            ),
            ("p_nofb", "MW260716E", "B0WIRE0005", "接线判不出履约", '{"list": 19.99}', None),
        ):
            pids[key] = conn.execute(
                "INSERT INTO app.product (team_id, master_sku, source_channel, source_ref,"
                " title, price_snapshot, status, attrs)"
                " VALUES (%s, %s, 'amazon', %s, %s, %s, 'ready', coalesce(%s::jsonb, '{}'))"
                " RETURNING id",
                (team_id, sku, ref, title, snap, attrs),
            ).fetchone()[0]
    return {"team": team_id, "user": uid, "store": store_id, "strategy": strategy_id, **pids}


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> TestClient:
    from erp.main import create_app

    return TestClient(create_app())


def _draft_listing_id(migrated_db: str, team: int) -> int:
    with psycopg.connect(migrated_db) as conn:
        return conn.execute(
            "SELECT id FROM app.listing WHERE team_id = %s ORDER BY id LIMIT 1", (team,)
        ).fetchone()[0]


class TestAllocateAutoPricing:
    def test_allocate_prices_and_rejects(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        # GTIN 预热（build 模式占号）
        r = client.post(
            "/api/v1/gtin-pool/import",
            headers=auth,
            json={"gtins": [_ean13(i) for i in range(8001, 8011)]},
        )
        assert r.status_code == 201, r.text
        assert r.json()["imported"] == 10

        r = client.post(
            "/api/v1/listings/allocate",
            headers=_idem(auth),
            json={
                "product_ids": [seeded["p_in"], seeded["p_oob"], seeded["p_nosrc"]],
                "store_id": seeded["store"],
                "offer_mode": "build",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # ① 自动算价：FBM 默认履约，19.99 × 2.75 = 54.97
        assert len(body["created"]) == 1
        created = body["created"][0]
        assert created["product_id"] == seeded["p_in"]
        assert created["current_price"] == 54.97
        # ② 区间外/无源价 fail-closed 拒绝（BR-PR-004 区间外不上架）
        rej = {x["product_id"]: x["code"] for x in body["rejected"]}
        assert rej[seeded["p_oob"]] == "PRICING_OUT_OF_BAND"
        assert rej[seeded["p_nosrc"]] == "PRICING_NO_SOURCE_PRICE"

        with psycopg.connect(migrated_db) as conn:
            cp = conn.execute(
                "SELECT current_price FROM app.listing WHERE id = %s", (created["id"],)
            ).fetchone()[0]
            assert float(cp) == 54.97
            hist = conn.execute(
                "SELECT old_price, new_price, reason, strategy_id, strategy_version, detail"
                " FROM app.price_history WHERE listing_id = %s ORDER BY id",
                (created["id"],),
            ).fetchall()
        assert len(hist) == 1
        old, new, reason, sid, sver, detail = hist[0]
        assert old is None and float(new) == 54.97
        assert reason == "initial"
        assert sid == seeded["strategy"] and sver == 1
        assert detail["formula"].startswith("FBM")  # 计算明细可追溯（001/06:179）

    def test_fulfillment_semantics(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """履约判定（2026-07-16 验收缺陷修复）：is_fba=Yes 走 FBA 区间；
        判不出（attrs 无 is_fba）→ 拒绝 PRICING_FULFILLMENT_UNKNOWN，
        策略 params.default_fulfillment 显式设置后才兜底。"""
        auth = _login(client, ADMIN, PASSWORD)
        r = client.post(
            "/api/v1/listings/allocate",
            headers=_idem(auth),
            json={
                "product_ids": [seeded["p_fba"], seeded["p_nofb"]],
                "store_id": seeded["store"],
                "offer_mode": "build",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # is_fba=Yes → FBA 区间 [0,30) 倍数 2.75：19.99×2.75=54.97（与 FBM [15,80)
        # 同倍数——用价史 formula 断言区间归属而非价格值）
        assert len(body["created"]) == 1
        fba_created = body["created"][0]
        assert fba_created["product_id"] == seeded["p_fba"]
        rej = {x["product_id"]: x["code"] for x in body["rejected"]}
        assert rej[seeded["p_nofb"]] == "PRICING_FULFILLMENT_UNKNOWN"
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            detail = conn.execute(
                "SELECT detail FROM app.price_history WHERE listing_id = %s AND reason = 'initial'",
                (fba_created["id"],),
            ).fetchone()[0]
            assert detail["formula"].startswith("FBA")
            # default_fulfillment 兜底：策略 params 显式设 FBM 后，判不出的产品可出价
            conn.execute(
                "UPDATE app.pricing_strategy SET params = params ||"
                ' \'{"default_fulfillment": "FBM"}\'::jsonb WHERE id = %s',
                (seeded["strategy"],),
            )
        try:
            r2 = client.post(
                "/api/v1/listings/allocate",
                headers=_idem(auth),
                json={
                    "product_ids": [seeded["p_nofb"]],
                    "store_id": seeded["store"],
                    "offer_mode": "build",
                },
            )
            assert r2.status_code == 200, r2.text
            assert len(r2.json()["created"]) == 1
        finally:
            with psycopg.connect(migrated_db, autocommit=True) as conn:
                conn.execute(
                    "UPDATE app.pricing_strategy SET params = params -"
                    " 'default_fulfillment' WHERE id = %s",
                    (seeded["strategy"],),
                )


class TestManualPriceGuard:
    def test_below_min_price_rejected(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """③ 人工价低于策略 min_price 硬底线 → fail-closed 拒绝。"""
        auth = _login(client, ADMIN, PASSWORD)
        lid = _draft_listing_id(migrated_db, seeded["team"])
        r = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 3.0})
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "PRICING_BELOW_MIN_PRICE"

    def test_confirm_threshold_and_force(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """④ 变动超 30%（54.97→80.0 = +45.5%）需 force；force=true 通过 + manual 价史。"""
        auth = _login(client, ADMIN, PASSWORD)
        lid = _draft_listing_id(migrated_db, seeded["team"])
        r = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 80.0})
        assert r.status_code == 422, r.text
        err = r.json()["error"]
        assert err["code"] == "PRICING_CONFIRM_REQUIRED"
        assert err["detail"]["old"] == 54.97 and err["detail"]["new"] == 80.0

        r2 = client.patch(
            f"/api/v1/listings/{lid}", headers=auth, json={"price": 80.0, "force": True}
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["current_price"] == 80.0
        # 阈内变动无需 force（80→75 = -6.25%）
        r3 = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 75.0})
        assert r3.status_code == 200, r3.text

        with psycopg.connect(migrated_db) as conn:
            hist = conn.execute(
                "SELECT old_price, new_price, created_by FROM app.price_history"
                " WHERE listing_id = %s AND reason = 'manual' ORDER BY id",
                (lid,),
            ).fetchall()
        assert [(float(o), float(n)) for o, n, _ in hist] == [(54.97, 80.0), (80.0, 75.0)]
        assert all(cb == seeded["user"] for _, _, cb in hist)

    def test_preview_by_listing_ids(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """listing_ids 试算：join listing 取 product + current_price 作 old_price。"""
        auth = _login(client, ADMIN, PASSWORD)
        lid = _draft_listing_id(migrated_db, seeded["team"])
        r = client.post(
            "/api/v1/pricing-strategies/preview",
            headers=auth,
            json={"store_id": seeded["store"], "offer_mode": "build", "listing_ids": [lid]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["strategy"]["id"] == seeded["strategy"]
        item = body["items"][0]
        assert item["listing_id"] == lid and item["product_id"] == seeded["p_in"]
        assert item["old_price"] == 75.0  # 上一用例改后的现价
        assert item["ok"] is True and item["new_price"] == 54.97
