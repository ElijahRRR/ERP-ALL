"""R2-07 07b 验收：品牌占用/释放闭环。

① build 分配产生占用（无品牌产品豁免、不阻塞）；② 异店同品牌冲突拒绝
（BRAND_OCCUPIED_OTHER_STORE，message 含占用店名）；③ match 豁免不占用；
④ suspension 建单 → 批量 released + incident_id 回链 + brand/sku_released_at 回填
+ notify 落行；⑤ 重复建单幂等（不二次释放）；⑥ manual release 端点 + 非 occupied
409 BRAND_NOT_OCCUPIED；⑦ 释放后同品牌可被别店重新占用（partial unique 生效）；
⑧ 同店同品牌重复分配放行且不重复建占用行。
"""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from erp.core.security import hash_password

from .conftest import APP_URL
from .test_identity_api import PASSWORD, _login

ADMIN = "brand_admin"
TEAM = "品牌占用团队"


def _idem(auth: dict) -> dict:
    return {**auth, "Idempotency-Key": str(uuid.uuid4())}


def _ean13(seed: int) -> str:
    body = f"78{seed:010d}"
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(body))
    return body + str((10 - total % 10) % 10)


_PRODUCTS = (  # (key, master_sku, source_ref, brand)
    ("p1", "MBR001", "BR0001", "BrandOne"),
    ("p2a", "MBR002", "BR0002", "BrandTwo"),
    ("p2b", "MBR003", "BR0003", "BrandTwo"),
    ("p3", "MBR004", "BR0004", "BrandThree"),
    ("p4", "MBR005", "BR0005", "BrandFour"),
    ("p6", "MBR006", "BR0006", "BrandSix"),
    ("p7a", "MBR007", "BR0007", "BrandSeven"),
    ("p7b", "MBR008", "BR0008", "BrandSeven"),
    ("p_nobrand", "MBR009", "BR0009", None),
    ("p1b", "MBR010", "BR0010", "BrandOne"),
)


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM app.app_user WHERE username = %s", (ADMIN,))
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        uid = conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '品牌管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute("DELETE FROM app.role WHERE team_id = %s AND name = '品牌角色'", (team_id,))
        role_id = conn.execute(
            "INSERT INTO app.role (team_id, name) VALUES (%s, '品牌角色') RETURNING id", (team_id,)
        ).fetchone()[0]
        for code in (
            "listing.read", "listing.allocate", "catalog.gtin_read", "catalog.import_write",
            "channel.store_read", "channel.incident_read", "channel.incident_write",
            "catalog.brand_read", "catalog.brand_release",
        ):  # fmt: skip
            conn.execute(
                "INSERT INTO app.role_permission (role_id, permission_code) VALUES (%s, %s)",
                (role_id, code),
            )
        conn.execute("INSERT INTO app.user_role (user_id, role_id) VALUES (%s, %s)", (uid, role_id))
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        # 清场（FK 顺序：品牌占用/通知/价史/状态史/幂等→listing→GTIN→事件→产品→店）
        for tbl in (
            "brand_assignment", "notification", "price_history", "listing_state_history",
            "api_idempotency", "listing", "gtin_pool", "store_incident", "product", "store",
        ):  # fmt: skip
            conn.execute(f"DELETE FROM app.{tbl} WHERE team_id = %s", (team_id,))
        stores = {}
        for key in ("A", "B", "C"):
            stores[key] = conn.execute(
                "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
                " VALUES (%s, %s, %s, %s, true) RETURNING id",
                (team_id, channel_id, f"BR_{key}", f"品牌店{key}"),
            ).fetchone()[0]
        pids = {}
        for key, sku, ref, brand in _PRODUCTS:
            pids[key] = conn.execute(
                "INSERT INTO app.product (team_id, master_sku, source_channel, source_ref,"
                " title, brand, status, price_snapshot)"
                " VALUES (%s, %s, 'amazon', %s, %s, %s, 'ready', '{\"list\": 19.99}')"
                " RETURNING id",
                (team_id, sku, ref, f"品牌品 {key}", brand),
            ).fetchone()[0]
    return {"team": team_id, "user": uid, "stores": stores, **pids}


@pytest.fixture(scope="module")
def client(seeded: dict) -> TestClient:
    import os

    os.environ["ERP_DATABASE_URL"] = APP_URL
    from erp.core.db import get_session_factory
    from erp.core.settings import get_settings

    get_settings.cache_clear()
    get_session_factory.cache_clear()
    from erp.main import create_app

    return TestClient(create_app())


@pytest.fixture(scope="module")
def auth(client: TestClient) -> dict:
    return _login(client, ADMIN, PASSWORD)


@pytest.fixture(scope="module", autouse=True)
def _gtins(client: TestClient, auth: dict) -> None:
    r = client.post(
        "/api/v1/gtin-pool/import",
        headers=auth,
        json={"gtins": [_ean13(i) for i in range(90001, 90041)]},
    )
    assert r.status_code == 201, r.text


def _allocate(client: TestClient, auth: dict, pids: list[int], store_id: int, mode: str) -> dict:
    r = client.post(
        "/api/v1/listings/allocate",
        headers=_idem(auth),
        json={"product_ids": pids, "store_id": store_id, "offer_mode": mode},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _brand_rows(dsn: str, team_id: int, brand_norm: str) -> list[dict]:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT * FROM app.brand_assignment WHERE team_id = %s AND brand_norm = %s ORDER BY id",
            (team_id, brand_norm),
        ).fetchall()


def _notes(dsn: str, team_id: int, category: str) -> list[tuple[str, str]]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT severity, dedupe_key FROM app.notification"
            " WHERE team_id = %s AND category = %s ORDER BY id",
            (team_id, category),
        ).fetchall()


class TestBrandLifecycle:
    def test_build_allocate_occupies_brand(
        self, client: TestClient, auth: dict, seeded: dict, migrated_db: str
    ) -> None:
        # ① 品牌品 + 无品牌品同批 build：两条 listing 都建，仅品牌品产生占用行。
        body = _allocate(
            client, auth, [seeded["p1"], seeded["p_nobrand"]], seeded["stores"]["A"], "build"
        )
        assert len(body["created"]) == 2, body
        rows = _brand_rows(migrated_db, seeded["team"], "brandone")
        assert len(rows) == 1
        assert rows[0]["status"] == "occupied"
        assert rows[0]["store_id"] == seeded["stores"]["A"]
        assert rows[0]["brand_display"] == "BrandOne"
        # 无品牌产品未落占用行：全表此刻仅 brandone 一条（本文件首个用例，清场后无残留）。
        with psycopg.connect(migrated_db) as conn:
            total = conn.execute(
                "SELECT count(*) FROM app.brand_assignment WHERE team_id = %s", (seeded["team"],)
            ).fetchone()[0]
        assert total == 1

    def test_other_store_conflict_rejected(
        self, client: TestClient, auth: dict, seeded: dict, migrated_db: str
    ) -> None:
        # ② BrandTwo 先由 A 占，异店 B 用同品牌另一产品 → 拒绝（message 含占用店名）。
        assert (
            len(_allocate(client, auth, [seeded["p2a"]], seeded["stores"]["A"], "build")["created"])
            == 1
        )
        body = _allocate(client, auth, [seeded["p2b"]], seeded["stores"]["B"], "build")
        assert body["created"] == []
        rej = body["rejected"]
        assert len(rej) == 1
        assert rej[0]["code"] == "BRAND_OCCUPIED_OTHER_STORE"
        assert "品牌店A" in rej[0]["message"]
        occupied = [r for r in _brand_rows(migrated_db, seeded["team"], "brandtwo")
                    if r["status"] == "occupied"]  # fmt: skip
        assert [r["store_id"] for r in occupied] == [seeded["stores"]["A"]]

    def test_match_mode_exempt(
        self, client: TestClient, auth: dict, seeded: dict, migrated_db: str
    ) -> None:
        # ③ match 不携带品牌绑定语义：建 listing 但无占用行。
        assert (
            len(_allocate(client, auth, [seeded["p3"]], seeded["stores"]["A"], "match")["created"])
            == 1
        )
        assert _brand_rows(migrated_db, seeded["team"], "brandthree") == []

    def test_suspension_batch_release(
        self, client: TestClient, auth: dict, seeded: dict, migrated_db: str
    ) -> None:
        # ④ BrandFour 占 C → 封店 C → 批量 released + 回链 + 时间戳回填 + notify。
        assert (
            len(_allocate(client, auth, [seeded["p4"]], seeded["stores"]["C"], "build")["created"])
            == 1
        )
        rid = client.post(
            "/api/v1/store-incidents",
            headers=auth,
            json={
                "store_id": seeded["stores"]["C"],
                "incident_kind": "suspension",
                "occurred_at": "2026-07-10T08:00:00Z",
                "reason": "封店释放测试",
            },
        ).json()["id"]
        rows = _brand_rows(migrated_db, seeded["team"], "brandfour")
        assert len(rows) == 1
        assert rows[0]["status"] == "released"
        assert rows[0]["release_reason"] == "suspension"
        assert rows[0]["incident_id"] == rid
        assert rows[0]["released_at"] is not None
        with psycopg.connect(migrated_db, row_factory=dict_row) as conn:
            inc = conn.execute(
                "SELECT brand_released_at, sku_released_at FROM app.store_incident WHERE id = %s",
                (rid,),
            ).fetchone()
        assert inc["brand_released_at"] is not None
        assert inc["sku_released_at"] is not None  # listing 停止维护标记一并回填
        assert any(k == f"brand_release:{rid}" for _, k in _notes(migrated_db, seeded["team"],
                                                                  "store_incident"))  # fmt: skip
        # 供 ⑤ 复用
        seeded["_incident4"] = rid

    def test_repeat_suspension_idempotent(
        self, client: TestClient, auth: dict, seeded: dict, migrated_db: str
    ) -> None:
        # ⑤ 二次封店同店：BrandFour 已 released，不被二次释放（回链仍指首单）。
        rid2 = client.post(
            "/api/v1/store-incidents",
            headers=auth,
            json={
                "store_id": seeded["stores"]["C"],
                "incident_kind": "suspension",
                "occurred_at": "2026-07-11T08:00:00Z",
            },
        ).json()["id"]
        rows = _brand_rows(migrated_db, seeded["team"], "brandfour")
        assert len(rows) == 1  # 无新增释放行
        assert rows[0]["incident_id"] == seeded["_incident4"]  # 未被第二单改写
        assert rid2 != seeded["_incident4"]
        with psycopg.connect(migrated_db, row_factory=dict_row) as conn:
            inc2 = conn.execute(
                "SELECT brand_released_at FROM app.store_incident WHERE id = %s", (rid2,)
            ).fetchone()
        assert inc2["brand_released_at"] is not None  # 作业执行（释放 0 条）仍回填

    def test_manual_release_and_not_occupied_409(
        self, client: TestClient, auth: dict, seeded: dict, migrated_db: str
    ) -> None:
        # ⑥ 手动释放 occupied → 200；再释放（非 occupied）→ 409 BRAND_NOT_OCCUPIED。
        assert (
            len(_allocate(client, auth, [seeded["p6"]], seeded["stores"]["A"], "build")["created"])
            == 1
        )
        aid = _brand_rows(migrated_db, seeded["team"], "brandsix")[0]["id"]
        r = client.post(
            f"/api/v1/brand-assignments/{aid}/release", headers=auth, json={"notes": "手动释放"}
        )
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["status"] == "released"
        assert out["release_reason"] == "manual"
        assert out["store_name"] == "品牌店A"
        assert out["incident_id"] is None  # 手动释放不动回链
        r = client.post(f"/api/v1/brand-assignments/{aid}/release", headers=auth, json={})
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "BRAND_NOT_OCCUPIED"

    def test_reoccupy_after_release(
        self, client: TestClient, auth: dict, seeded: dict, migrated_db: str
    ) -> None:
        # ⑦ BrandSeven 占 A → 手动释放 → 别店 B 用同品牌另一产品重新占（partial unique 放行）。
        assert (
            len(_allocate(client, auth, [seeded["p7a"]], seeded["stores"]["A"], "build")["created"])
            == 1
        )
        aid = _brand_rows(migrated_db, seeded["team"], "brandseven")[0]["id"]
        assert client.post(f"/api/v1/brand-assignments/{aid}/release", headers=auth,
                           json={}).status_code == 200  # fmt: skip
        assert (
            len(_allocate(client, auth, [seeded["p7b"]], seeded["stores"]["B"], "build")["created"])
            == 1
        )
        occupied = [r for r in _brand_rows(migrated_db, seeded["team"], "brandseven")
                    if r["status"] == "occupied"]  # fmt: skip
        assert len(occupied) == 1
        assert occupied[0]["store_id"] == seeded["stores"]["B"]

    def test_same_store_repeat_allocation_passes(
        self, client: TestClient, auth: dict, seeded: dict, migrated_db: str
    ) -> None:
        # ⑧ BrandOne 已由 A 占（用例①）：同店同品牌另一产品再分配 → 放行且不新增占用行。
        assert (
            len(_allocate(client, auth, [seeded["p1b"]], seeded["stores"]["A"], "build")["created"])
            == 1
        )
        rows = _brand_rows(migrated_db, seeded["team"], "brandone")
        assert len(rows) == 1  # 仍只一条，未重复建行
        assert rows[0]["status"] == "occupied"
        assert rows[0]["store_id"] == seeded["stores"]["A"]

    def test_list_endpoint_joins_store_and_filters(
        self, client: TestClient, auth: dict, seeded: dict
    ) -> None:
        # GET 契约：store_name JOIN + status 过滤 + store_id 过滤。
        r = client.get("/api/v1/brand-assignments", headers=auth, params={"status": "occupied"})
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert items and all(it["status"] == "occupied" for it in items)
        assert all(it["store_name"] for it in items)  # JOIN store 提供
        r2 = client.get(
            "/api/v1/brand-assignments",
            headers=auth,
            params={"status": "released", "store_id": seeded["stores"]["A"]},
        )
        assert r2.status_code == 200, r2.text
        rel = r2.json()["items"]
        assert rel and all(
            it["status"] == "released" and it["store_id"] == seeded["stores"]["A"] for it in rel
        )
