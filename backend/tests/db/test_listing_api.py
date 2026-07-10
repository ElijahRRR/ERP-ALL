"""R1-11 验收：上架最小闭环。

GTIN 20 枚入池（held→used 全程）→ allocate（去重/预占/初价）→ submit
（dry-run 证据 + live_test 提交）→ poll（item 级权威回写→live）→ delist 收尾；
verify-back 分支（channel_feed_id=NULL 永不盲重试）单独验证。
渠道全程 MockTransport 替身——A152 真调在 Owner 部署机执行。
"""

import json
from pathlib import Path

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.channel.gateway import gateway
from erp.core.security import hash_password
from erp.core.settings import get_settings

from .test_identity_api import PASSWORD, _login

ADMIN = "listing_admin"
TEAM = "上架测试团队"
STORE_CODE = "A152L"


def _ean13(seed: int) -> str:
    body = f"69{seed:010d}"
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(body))
    return body + str((10 - total % 10) % 10)


class _FakeChannel:
    """渠道替身：/v3/token 发 token；业务路径按脚本队列响应。"""

    def __init__(self) -> None:
        self.script: list[httpx.Response | Exception] = []
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 900})
        self.requests.append(request)
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return httpx.Response(200, json={"ok": True})


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    key = get_settings().credential_key
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM app.app_user WHERE username = %s", (ADMIN,))
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        uid = conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '上架管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        # 直建专用角色：上架全链权限（模板«团队管理员»缺 allocate/submit/delist）
        conn.execute(
            "DELETE FROM app.role WHERE team_id = %s AND name = '上架测试角色'", (team_id,)
        )
        role_id = conn.execute(
            "INSERT INTO app.role (team_id, name) VALUES (%s, '上架测试角色') RETURNING id",
            (team_id,),
        ).fetchone()[0]
        for code in (
            "listing.read", "listing.allocate", "listing.submit", "listing.delist",
            "catalog.gtin_read", "catalog.import_write",
        ):  # fmt: skip
            conn.execute(
                "INSERT INTO app.role_permission (role_id, permission_code) VALUES (%s, %s)",
                (role_id, code),
            )
        conn.execute("INSERT INTO app.user_role (user_id, role_id) VALUES (%s, %s)", (uid, role_id))
        # 测试店（is_test，走 live_test 模式）+ 凭证
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        conn.execute(
            "DELETE FROM app.store_credential WHERE store_id IN"
            " (SELECT id FROM app.store WHERE team_id = %s)",
            (team_id,),
        )
        conn.execute("DELETE FROM app.listing_state_history WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.feed_item WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.feed WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.maintenance_task WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.listing_spec WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.listing WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.gtin_pool WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.product WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.store WHERE team_id = %s", (team_id,))
        store_id = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, %s, '上架测试店', true) RETURNING id",
            (team_id, channel_id, STORE_CODE),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO app.store_credential (store_id, client_id, client_secret_encrypted)"
            " VALUES (%s, 'listing-client-01', pgp_sym_encrypt('listing-secret-01', %s))",
            (store_id, key),
        )
        # 产品：审核通过 ×2 + 未审核 ×1
        pids = {}
        for asin, status, title in (
            ("B0LIST0001", "audit_passed", "Ceramic Mug Classic White 12oz"),
            ("B0LIST0002", "audit_passed", "Bamboo Cutting Board Large"),
            ("B0LIST0003", "ingested", "Not Audited Yet Item"),
        ):
            pids[asin] = conn.execute(
                "INSERT INTO app.product (team_id, source_channel, source_ref, title, attrs,"
                " price_snapshot, status)"
                " VALUES (%s, 'amazon', %s, %s,"
                '  \'{"wpt": "Drinkware", "bullets": ["solid"], "description": "nice"}\','
                "  '{\"list\": 19.99}', %s) RETURNING id",
                (team_id, asin, title, status),
            ).fetchone()[0]
        # 网关模式：live_test（is_test 店可真发→替身）
        conn.execute(
            "INSERT INTO app.system_config (key, value)"
            " VALUES ('channel.gateway_mode', '\"live_test\"'::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value"
        )
    return {"team": team_id, "user": uid, "store": store_id, **pids}


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> TestClient:
    from erp.main import app

    return TestClient(app)


@pytest.fixture()
def fake(seeded: dict[str, int]) -> _FakeChannel:
    f = _FakeChannel()
    gateway._clients.clear()  # 连接池缓存旧 transport，必须清（单例网关）
    gateway._transport_factory = lambda proxy: httpx.MockTransport(f.handler)
    yield f
    gateway._clients.clear()
    gateway._transport_factory = gateway._default_transport


class TestGtinPool:
    def test_import_20_ean13(self, client: TestClient, seeded: dict) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        gtins = [_ean13(i) for i in range(1, 21)]
        bad = "6900000000010"  # 校验位错误
        r = client.post(
            "/api/v1/gtin-pool/import",
            headers=auth,
            json={"gtins": [*gtins, bad, gtins[0]]},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["imported"] == 20
        codes = {x["code"] for x in body["rejected"]}
        assert codes == {"GTIN_CHECKSUM"}  # 同批重复被输入级去重静默合并
        # 跨批重复 → GTIN_DUPLICATE
        r2 = client.post("/api/v1/gtin-pool/import", headers=auth, json={"gtins": [gtins[0]]})
        assert r2.json()["rejected"][0]["code"] == "GTIN_DUPLICATE"
        stats = client.get("/api/v1/gtin-pool/stats", headers=auth).json()
        assert {"gtin_kind": "ean_13", "status": "free", "count": 20} in stats


class TestAllocate:
    def test_allocate_dedup_and_gate(self, client: TestClient, seeded: dict) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        r = client.post(
            "/api/v1/listings/allocate",
            headers=auth,
            json={
                "product_ids": [
                    seeded["B0LIST0001"],
                    seeded["B0LIST0002"],
                    seeded["B0LIST0003"],
                ],
                "store_id": seeded["store"],
                "offer_mode": "build",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["created"]) == 2
        assert all(c["gtin"] and len(c["gtin"]) == 13 for c in body["created"])
        assert all(c["current_price"] == 19.99 for c in body["created"])
        assert all(c["channel_sku"].startswith("M") for c in body["created"])
        assert body["rejected"][0]["code"] == "PRODUCT_NOT_READY"

        # 同 product 再分配 → 团队内去重拒绝
        r2 = client.post(
            "/api/v1/listings/allocate",
            headers=auth,
            json={
                "product_ids": [seeded["B0LIST0001"]],
                "store_id": seeded["store"],
                "offer_mode": "build",
            },
        )
        assert r2.json()["rejected"][0]["code"] == "LISTING_DUP_IN_TEAM"

        auth2 = _login(client, ADMIN, PASSWORD)
        stats = client.get("/api/v1/gtin-pool/stats", headers=auth2).json()
        assert {"gtin_kind": "ean_13", "status": "held", "count": 2} in stats


class TestSubmitChain:
    def _listing_ids(self, migrated_db: str, team: int, status: str | None = None) -> list[int]:
        with psycopg.connect(migrated_db) as conn:
            q = "SELECT id FROM app.listing WHERE team_id = %s"
            args: list = [team]
            if status:
                q += " AND status = %s"
                args.append(status)
            return [r[0] for r in conn.execute(q + " ORDER BY id", args).fetchall()]

    def test_submit_live_test_then_poll_to_live(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """主链：submit→feed submitted→poll PROCESSED→一成一败→live/failed 回写。"""
        auth = _login(client, ADMIN, PASSWORD)
        ids = self._listing_ids(migrated_db, seeded["team"], "draft")
        assert len(ids) == 2
        fake.script = [httpx.Response(200, json={"feedId": "F-CHAN-001"})]
        r = client.post("/api/v1/listings/submit", headers=auth, json={"listing_ids": ids})
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["queued"] == 2
        assert body["feed_status"] == "submitted"
        assert body["channel_feed_id"] == "F-CHAN-001"
        feed_id = body["feed_id"]
        # 提交体含两个 MPItem 且 GTIN/价格已实例化
        sent = json.loads(fake.requests[0].content)
        assert len(sent["MPItem"]) == 2
        assert all(i["Orderable"]["price"] == 19.99 for i in sent["MPItem"])
        assert all(
            len(i["Orderable"]["productIdentifiers"]["productId"]) == 13 for i in sent["MPItem"]
        )

        # poll：sku1 SUCCESS(wpid) / sku2 ERROR(未登记码)
        with psycopg.connect(migrated_db) as conn:
            skus = dict(
                conn.execute(
                    "SELECT id, channel_sku FROM app.listing WHERE team_id = %s ORDER BY id",
                    (seeded["team"],),
                ).fetchall()
            )
        sku_list = list(skus.values())
        fake.script = [
            httpx.Response(
                200,
                json={
                    "feedStatus": "PROCESSED",
                    "summary": {"itemsSucceeded": 99},  # headline 故意造假——权威在 item 级
                    "itemDetails": {
                        "itemIngestionStatus": [
                            {
                                "sku": sku_list[0],
                                "ingestionStatus": "SUCCESS",
                                "wpid": "WPID001",
                            },
                            {
                                "sku": sku_list[1],
                                "ingestionStatus": "DATA_ERROR",
                                "ingestionErrors": {
                                    "ingestionError": [
                                        {"code": "WM_TEST_NEWCODE", "description": "bad attr"}
                                    ]
                                },
                            },
                        ]
                    },
                },
            )
        ]
        pr = client.post(f"/api/v1/feeds/{feed_id}/poll", headers=auth).json()
        assert pr["feed_status"] == "partial"
        assert pr["success"] == 1 and pr["error"] == 1

        with psycopg.connect(migrated_db) as conn:
            rows = dict(
                conn.execute(
                    "SELECT channel_sku, status FROM app.listing WHERE team_id = %s",
                    (seeded["team"],),
                ).fetchall()
            )
            assert rows[sku_list[0]] == "live"
            assert rows[sku_list[1]] == "failed"
            # GTIN：成功→used（终身绑定）；失败→归还 free
            pool = dict(
                conn.execute(
                    "SELECT status, count(*) FROM app.gtin_pool WHERE team_id = %s GROUP BY status",
                    (seeded["team"],),
                ).fetchall()
            )
            assert pool.get("used") == 1
            assert pool.get("held") is None
            # 未登记错误码自动入字典草稿
            dispo = conn.execute(
                "SELECT disposition, category FROM app.listing_error_catalog"
                " WHERE error_code = 'WM_TEST_NEWCODE'"
            ).fetchone()
            assert dispo == ("manual", "未分类")

    def test_state_history_full_chain_and_delist(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """验收：listing_state_history 完整链 + delist 收尾 + used 永不回收。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db) as conn:
            live_id = conn.execute(
                "SELECT id FROM app.listing WHERE team_id = %s AND status = 'live'",
                (seeded["team"],),
            ).fetchone()[0]
        fake.script = [httpx.Response(200, json={"feedId": "F-RETIRE-01"})]
        r = client.post(f"/api/v1/listings/{live_id}/delist", headers=auth)
        assert r.status_code == 202, r.text
        assert r.json()["status"] == "delisted"

        detail = client.get(f"/api/v1/listings/{live_id}", headers=auth).json()
        chain = [(h["from_status"], h["to_status"]) for h in detail["state_history"]]
        assert chain == [
            ("draft", "draft"),  # allocated
            ("draft", "queued"),
            ("queued", "submitted"),
            ("submitted", "published"),
            ("published", "live"),
            ("live", "delist_pending"),
            ("delist_pending", "delisted"),
        ]
        with psycopg.connect(migrated_db) as conn:
            used = conn.execute(
                "SELECT count(*) FROM app.gtin_pool WHERE team_id = %s AND status = 'used'",
                (seeded["team"],),
            ).fetchone()[0]
        assert used == 1  # delist 后 GTIN 仍 used——永不回收

    def test_retry_failed_requires_disposition(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db) as conn:
            failed_id = conn.execute(
                "SELECT id FROM app.listing WHERE team_id = %s AND status = 'failed'",
                (seeded["team"],),
            ).fetchone()[0]
        r = client.post(f"/api/v1/listings/{failed_id}/retry", headers=auth)
        assert r.status_code == 202, r.text
        assert r.json()["status"] == "queued"
        detail = client.get(f"/api/v1/listings/{failed_id}", headers=auth).json()
        assert detail["gtin"] and len(detail["gtin"]) == 13  # 重投重新占号


class TestVerifyBack:
    def test_no_response_never_blind_retry(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """总账铁律：提交无响应 → verify_pending（不重发）→ 对账 lost → 回队。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db) as conn:
            queued_id = conn.execute(
                "SELECT id FROM app.listing WHERE team_id = %s AND status = 'queued'",
                (seeded["team"],),
            ).fetchone()[0]
        fake.script = [httpx.ConnectTimeout("boom")]
        r = client.post("/api/v1/listings/submit", headers=auth, json={"listing_ids": [queued_id]})
        body = r.json()
        assert body["feed_status"] == "verify_pending"
        feed_id = body["feed_id"]
        assert len(fake.requests) == 1  # 只发了一次——永不盲重试
        with psycopg.connect(migrated_db) as conn:
            cf = conn.execute(
                "SELECT channel_feed_id FROM app.feed WHERE id = %s", (feed_id,)
            ).fetchone()[0]
        assert cf is None

        # 对账：渠道近程无匹配 → lost，listing 回 queued
        fake.script = [httpx.Response(200, json={"results": {"feed": []}})]
        vb = client.post(f"/api/v1/feeds/{feed_id}/verify-back", headers=auth).json()
        assert vb["feed_status"] == "lost"
        assert vb["requeued"] == 1

    def test_verify_back_adopt(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """对账唯一匹配 → 采认渠道 feedId，listing 推进 submitted。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db) as conn:
            queued_id = conn.execute(
                "SELECT id FROM app.listing WHERE team_id = %s AND status = 'queued'"
                " ORDER BY id LIMIT 1",
                (seeded["team"],),
            ).fetchone()[0]
        fake.script = [httpx.ConnectTimeout("boom")]
        feed_id = client.post(
            "/api/v1/listings/submit", headers=auth, json={"listing_ids": [queued_id]}
        ).json()["feed_id"]
        fake.script = [
            httpx.Response(
                200,
                json={
                    "results": {
                        "feed": [
                            {"feedId": "F-ADOPT-01", "feedType": "MP_ITEM", "itemsReceived": 1}
                        ]
                    }
                },
            )
        ]
        vb = client.post(f"/api/v1/feeds/{feed_id}/verify-back", headers=auth).json()
        assert vb["feed_status"] == "submitted"
        assert vb["channel_feed_id"] == "F-ADOPT-01"
        detail = client.get(f"/api/v1/listings/{queued_id}", headers=auth).json()
        assert detail["status"] == "submitted"


class TestDryRunEvidence:
    def test_dry_run_snapshot(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """dry_run 模式提交 → 全量请求快照入证据（A152 真调前的最终形态确认）。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.system_config SET value = '\"dry_run\"'::jsonb"
                " WHERE key = 'channel.gateway_mode'"
            )
            sub_id = conn.execute(
                "SELECT id FROM app.listing WHERE team_id = %s AND status = 'submitted'"
                " ORDER BY id LIMIT 1",
                (seeded["team"],),
            ).fetchone()[0]
            # 拨回 queued 以便重新提交（直改状态仅测试用）
            conn.execute("UPDATE app.listing SET status = 'queued' WHERE id = %s", (sub_id,))
        auth = _login(client, ADMIN, PASSWORD)
        r = client.post("/api/v1/listings/submit", headers=auth, json={"listing_ids": [sub_id]})
        body = r.json()
        assert body.get("dry_run") is True
        assert body["feed_status"] == "building"
        snap = body["request_snapshot"]
        assert snap["url"].endswith("/v3/feeds")
        repo_root = Path(__file__).resolve().parents[3]
        out = repo_root / ".agent" / "evidence" / "R1-11" / "dry-run-feed-snapshot.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.system_config SET value = '\"live_test\"'::jsonb"
                " WHERE key = 'channel.gateway_mode'"
            )
