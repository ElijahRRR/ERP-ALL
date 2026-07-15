"""GTIN 占号修复回归（真机缺陷 2026-07-15：真实 UPC 入池后 allocate 500）。

① hold_one 写死 ean_13 → upc_a 池 30 枚 free 取不到（GTIN_POOL_EMPTY）
② 池空补偿走 DELETE FROM app.listing，而 erp_app 无 DELETE 权限（最小权限）
   → 权限错误把业务提示覆盖成 HTTP 500

修后契约：占号按优先序逐池尝试（gtin.kind_preference，team>system>默认
[upc_a, ean_13]）；池空经 SAVEPOINT 回滚本品 INSERT，干净返回 rejected 项。
"""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.core.security import hash_password

from .test_identity_api import PASSWORD, _login

ADMIN = "gtinalloc_admin"
TEAM = "GTIN占号测试团队"


def _gtin(body: str) -> str:
    digits = [int(c) for c in body]
    total = sum(d * (3 if (len(digits) - 1 - i) % 2 == 0 else 1) for i, d in enumerate(digits))
    return body + str((10 - total % 10) % 10)


def _upc(seed: int) -> str:
    return _gtin(f"04{seed:09d}")  # 12 位 UPC-A


def _ean(seed: int) -> str:
    return _gtin(f"73{seed:010d}")  # 13 位 EAN-13


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
            " VALUES (%s, %s, %s, 'GTIN占号管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute(
            "DELETE FROM app.role WHERE team_id = %s AND name = 'gtinalloc角色'", (team_id,)
        )
        role_id = conn.execute(
            "INSERT INTO app.role (team_id, name) VALUES (%s, 'gtinalloc角色') RETURNING id",
            (team_id,),
        ).fetchone()[0]
        for code in ("listing.read", "listing.allocate", "catalog.gtin_read"):
            conn.execute(
                "INSERT INTO app.role_permission (role_id, permission_code) VALUES (%s, %s)",
                (role_id, code),
            )
        conn.execute("INSERT INTO app.user_role (user_id, role_id) VALUES (%s, %s)", (uid, role_id))
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        for t in ("listing_state_history", "api_idempotency", "listing", "gtin_pool",
                  "product", "store"):  # fmt: skip
            conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.system_config WHERE key = 'gtin.kind_preference'")
        store_id = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, 'GTALLOC1', 'GTIN占号测试店', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
        pids = {}
        for n in range(1, 5):
            asin = f"B0GTALLOC{n:02d}"
            pids[asin] = conn.execute(
                "INSERT INTO app.product (team_id, source_channel, source_ref, title, attrs,"
                " price_snapshot, status)"
                " VALUES (%s, 'amazon', %s, %s,"
                "  '{\"wpt\": \"Drinkware\"}', '{\"list\": 9.99}', 'audit_passed')"
                " RETURNING id",
                (team_id, asin, f"Gtin Alloc Test Item {n}"),
            ).fetchone()[0]
    return {"team": team_id, "user": uid, "store": store_id, **pids}


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> TestClient:
    from erp.main import app

    return TestClient(app)


def _idem(auth: dict) -> dict:
    return {**auth, "Idempotency-Key": str(uuid.uuid4())}


def _allocate(client: TestClient, auth: dict, seeded: dict, product_key: str) -> object:
    return client.post(
        "/api/v1/listings/allocate",
        headers=_idem(auth),
        json={
            "product_ids": [seeded[product_key]],
            "store_id": seeded["store"],
            "offer_mode": "build",
        },
    )


class TestUpcPool:
    def test_build_allocate_takes_upc_a_when_only_pool(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """真机场景复刻：只有 upc_a 池有号（真实购入 UPC）→ 必须能占到。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.gtin_pool (team_id, gtin, gtin_kind, source)"
                " VALUES (%s, %s, 'upc_a', 'generator_import')",
                (seeded["team"], _upc(1)),
            )
        auth = _login(client, ADMIN, PASSWORD)
        r = _allocate(client, auth, seeded, "B0GTALLOC01")
        assert r.status_code == 200, r.text
        created = r.json()["created"]
        assert len(created) == 1 and len(created[0]["gtin"]) == 12  # UPC-A
        with psycopg.connect(migrated_db) as conn:
            status = conn.execute(
                "SELECT status FROM app.gtin_pool WHERE gtin = %s", (created[0]["gtin"],)
            ).fetchone()[0]
            assert status == "held"

    def test_pool_empty_rejected_cleanly_no_500_no_residue(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """池空必须是业务拒绝（rejected 项）而非 500；SAVEPOINT 回滚零残留行。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db) as conn:
            before = conn.execute(
                "SELECT count(*) FROM app.listing WHERE team_id = %s", (seeded["team"],)
            ).fetchone()[0]
        r = _allocate(client, auth, seeded, "B0GTALLOC02")  # 上一用例已占走唯一号
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["created"] == []
        assert body["rejected"][0]["code"] == "GTIN_POOL_EMPTY"
        with psycopg.connect(migrated_db) as conn:
            after = conn.execute(
                "SELECT count(*) FROM app.listing WHERE team_id = %s", (seeded["team"],)
            ).fetchone()[0]
            assert after == before  # INSERT 已随 SAVEPOINT 回滚，无孤儿行

    def test_kind_preference_config_override(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """system_config gtin.kind_preference 覆盖默认优先序（业务参数不写死）。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.gtin_pool (team_id, gtin, gtin_kind, source) VALUES"
                " (%(t)s, %(u)s, 'upc_a', 'generator_import'),"
                " (%(t)s, %(e)s, 'ean_13', 'generator_import')",
                {"t": seeded["team"], "u": _upc(2), "e": _ean(2)},
            )
            conn.execute(
                "INSERT INTO app.system_config (key, value)"
                " VALUES ('gtin.kind_preference', '[\"ean_13\", \"upc_a\"]'::jsonb)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value"
            )
        try:
            r = _allocate(client, auth, seeded, "B0GTALLOC03")
            assert r.status_code == 200, r.text
            assert len(r.json()["created"][0]["gtin"]) == 13  # 覆盖后 ean_13 优先
        finally:
            with psycopg.connect(migrated_db, autocommit=True) as conn:
                conn.execute("DELETE FROM app.system_config WHERE key = 'gtin.kind_preference'")

        # 清掉覆盖后回到默认序：upc_a 优先
        r = _allocate(client, auth, seeded, "B0GTALLOC04")
        assert r.status_code == 200, r.text
        assert len(r.json()["created"][0]["gtin"]) == 12
