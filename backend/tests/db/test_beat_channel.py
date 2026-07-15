"""R2-04 增量2 验收：渠道类 beat 任务四件（feed_poll / feed_verify_back /
channel_outbox_drain / retire_recon）。

- 验收①锚点：提交后**无人工点查**——beat tick 自动轮询回写至 live。
- feed_verify_back：min_age 门槛 + adopt 归位（命令 succeeded、listing submitted）。
- retire_recon：商品实况为权威——404→delisted+succeeded；仍在架超 grace→live+failed；
  仍在架未过 grace→维持 verify_pending（车道继续背压，fail-closed）。
- feed_poll：min_interval 节流 + 卡死告警（poll_attempts 阈值 → notification）。

假渠道按 method+path 前缀路由（非顺序脚本）——beat 批量扫描的请求顺序不可预设。
"""

import uuid

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.automation import tasks
from erp.automation.beat import tick
from erp.channel.gateway import gateway
from erp.core.db import get_session_factory
from erp.core.security import hash_password
from erp.core.settings import get_settings

from .test_identity_api import PASSWORD, _login

ADMIN = "beatch_admin"
TEAM = "beat渠道测试团队"


class _FakeChannel:
    """method+path 前缀路由（最长前缀优先）；未命中回 200 {"ok": true}。"""

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


def _ean13(seed: int) -> str:
    body = f"69{seed:010d}"
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(body))
    return body + str((10 - total % 10) % 10)


def _mk_store(conn, team_id: int, channel_id: int, code: str, key: str) -> int:
    sid = conn.execute(
        "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
        " VALUES (%s, %s, %s, %s, true) RETURNING id",
        (team_id, channel_id, code, f"beat渠道店{code}"),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO app.store_credential (store_id, client_id, client_secret_encrypted)"
        " VALUES (%s, 'beatch-client-01', pgp_sym_encrypt('beatch-secret-01', %s))",
        (sid, key),
    )
    return sid


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
            " VALUES (%s, %s, %s, 'beat渠道管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute("DELETE FROM app.role WHERE team_id = %s AND name = 'beatch角色'", (team_id,))
        role_id = conn.execute(
            "INSERT INTO app.role (team_id, name) VALUES (%s, 'beatch角色') RETURNING id",
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
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        conn.execute(
            "DELETE FROM app.store_credential WHERE store_id IN"
            " (SELECT id FROM app.store WHERE team_id = %s)",
            (team_id,),
        )
        for t in ("listing_state_history", "channel_command", "api_idempotency", "feed_item",
                  "feed", "listing_spec", "listing", "gtin_pool", "product", "store"):  # fmt: skip
            conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (team_id,))
        # 全库遗留的可轮询/可对账 feed 终局化——beat 任务按状态全局扫描，
        # 他模块残留会吃掉本模块的路由响应（测试清场，仅测试用）
        conn.execute(
            "UPDATE app.feed SET status = 'processed', completed_at = now()"
            " WHERE status IN ('submitted','processing','verify_pending')"
        )
        conn.execute(
            "UPDATE app.channel_command SET status = 'succeeded', completed_at = now(),"
            " lease_expires_at = NULL WHERE status IN ('pending','inflight','verify_pending')"
        )
        stores = {
            name: _mk_store(conn, team_id, channel_id, code, key)
            for name, code in [
                ("store_main", "BEATC0"),
                ("store_r1", "BEATC1"),
                ("store_r2", "BEATC2"),
                ("store_r3", "BEATC3"),
            ]
        }
        for i in range(7001, 7011):
            conn.execute(
                "INSERT INTO app.gtin_pool (team_id, gtin, gtin_kind, source)"
                " VALUES (%s, %s, 'ean_13', 'generator_import')",
                (team_id, _ean13(i)),
            )
        pids = {}
        for n in range(1, 6):
            asin = f"B0BEATCH{n:03d}"
            pids[asin] = conn.execute(
                "INSERT INTO app.product (team_id, source_channel, source_ref, title, attrs,"
                " price_snapshot, status)"
                " VALUES (%s, 'amazon', %s, %s,"
                '  \'{"wpt": "Drinkware", "bullets": ["solid"], "description": "nice"}\','
                "  '{\"list\": 12.99}', 'audit_passed') RETURNING id",
                (team_id, asin, f"Beat Channel Test Mug Number {n} Blue"),
            ).fetchone()[0]
        conn.execute(
            "INSERT INTO app.system_config (key, value)"
            " VALUES ('channel.gateway_mode', '\"live_test\"'::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value"
        )
    return {"team": team_id, "user": uid, **stores, **pids}


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


def _allocate(client: TestClient, auth: dict, seeded: dict, product_key: str, store: str) -> int:
    alloc = client.post(
        "/api/v1/listings/allocate",
        headers=_idem(auth),
        json={
            "product_ids": [seeded[product_key]],
            "store_id": seeded[store],
            "offer_mode": "build",
        },
    ).json()
    return int(alloc["created"][0]["id"])


def _delist_to_verify_pending(
    client: TestClient, auth: dict, migrated_db: str, fake: _FakeChannel, lid: int
) -> int:
    """把 listing 推到 delist_pending + 命令 verify_pending（故障注入：响应丢失）。"""
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("UPDATE app.listing SET status = 'live' WHERE id = %s", (lid,))
    fake.routes["POST /v3/feeds"] = httpx.ConnectTimeout("crash: response lost")
    r = client.post(f"/api/v1/listings/{lid}/delist", headers=_idem(auth))
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "delist_pending"
    del fake.routes["POST /v3/feeds"]
    with psycopg.connect(migrated_db) as conn:
        cmd_id, status = conn.execute(
            "SELECT id, status FROM app.channel_command WHERE action='item_retire'"
            " AND object_type='listing' AND object_id = %s ORDER BY id DESC LIMIT 1",
            (lid,),
        ).fetchone()
    assert status == "verify_pending"
    return int(cmd_id)


class TestAutoPollAcceptance:
    async def test_submit_then_beat_polls_to_live_without_manual_poll(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """验收①「A152 提交后无人工点查自动轮询回写」的测试化版本。"""
        auth = _login(client, ADMIN, PASSWORD)
        lid = _allocate(client, auth, seeded, "B0BEATCH001", "store_main")
        fake.routes["POST /v3/feeds"] = httpx.Response(200, json={"feedId": "F-BEAT-001"})
        r = client.post("/api/v1/listings/submit", headers=_idem(auth), json={"listing_ids": [lid]})
        assert r.status_code == 202 and r.json()["feed_status"] == "submitted"
        feed_id = r.json()["feed_id"]
        with psycopg.connect(migrated_db) as conn:
            sku = conn.execute(
                "SELECT channel_sku FROM app.feed_item WHERE feed_id = %s", (feed_id,)
            ).fetchone()[0]

        # 渠道已处理完：轮询应回写 item 级权威结果
        fake.routes["GET /v3/feeds/F-BEAT-001"] = httpx.Response(
            200,
            json={
                "feedStatus": "PROCESSED",
                "summary": {"itemsSucceeded": 1},
                "itemDetails": {
                    "itemIngestionStatus": [
                        {"sku": sku, "ingestionStatus": "SUCCESS", "wpid": "WBEAT001"}
                    ]
                },
            },
        )
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.schedule SET cron = '0 3 1 * *',"
                " next_run_at = now() - interval '1 second' WHERE code = 'feed_poll'"
            )

        # 唯一动作：beat tick——全程不碰 /feeds/{id}/poll 人工端点
        await tick(get_session_factory())

        with psycopg.connect(migrated_db) as conn:
            listing = conn.execute(
                "SELECT status, wpid FROM app.listing WHERE id = %s", (lid,)
            ).fetchone()
            assert listing == ("live", "WBEAT001")
            feed_status = conn.execute(
                "SELECT status FROM app.feed WHERE id = %s", (feed_id,)
            ).fetchone()[0]
            assert feed_status == "processed"
            gtin_status = conn.execute(
                "SELECT status FROM app.gtin_pool WHERE used_listing_id = %s", (lid,)
            ).fetchone()[0]
            assert gtin_status == "used"
            run = conn.execute(
                "SELECT status, stats FROM app.task_run WHERE task_code = 'feed_poll'"
                " ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert run[0] == "done" and run[1]["polled"] >= 1
        # 轮询确实只经由 beat 发生（一次 GET 该 feed）
        polls = [x for x in fake.requests if x.url.path == "/v3/feeds/F-BEAT-001"]
        assert len(polls) == 1 and polls[0].method == "GET"


class TestFeedVerifyBackTask:
    async def test_min_age_gate_then_adopt(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        lid = _allocate(client, auth, seeded, "B0BEATCH002", "store_main")
        # 故障注入：渠道收到但响应丢失 → feed/命令双 verify_pending
        fake.routes["POST /v3/feeds"] = httpx.ConnectTimeout("crash: response lost")
        r = client.post("/api/v1/listings/submit", headers=_idem(auth), json={"listing_ids": [lid]})
        assert r.json()["feed_status"] == "verify_pending"
        feed_id = r.json()["feed_id"]
        del fake.routes["POST /v3/feeds"]
        after_inject = len(fake.requests)

        sessions = get_session_factory()
        # 滞留门槛：刚翻 verify_pending 的 feed 不该被扫（渠道列表可能尚不可见）
        stats = await tasks.feed_verify_back(sessions, {"min_age_s": 3600})
        assert stats["scanned"] == 0

        fake.routes["GET /v3/feeds"] = httpx.Response(
            200,
            json={
                "results": {
                    "feed": [{"feedId": "F-VB-01", "feedType": "MP_ITEM", "itemsReceived": 1}]
                }
            },
        )
        stats = await tasks.feed_verify_back(sessions, {"min_age_s": 0})
        assert stats["adopted"] == 1 and stats["lost"] == 0
        with psycopg.connect(migrated_db) as conn:
            feed = conn.execute(
                "SELECT status, channel_feed_id FROM app.feed WHERE id = %s", (feed_id,)
            ).fetchone()
            assert feed == ("submitted", "F-VB-01")
            cmd_status = conn.execute(
                "SELECT status FROM app.channel_command WHERE object_type='feed'"
                " AND object_id = %s",
                (feed_id,),
            ).fetchone()[0]
            assert cmd_status == "succeeded"
            listing_status = conn.execute(
                "SELECT status FROM app.listing WHERE id = %s", (lid,)
            ).fetchone()[0]
            assert listing_status == "submitted"
        # 对账阶段零 POST（绝不重发；注入阶段那次 POST 不计）
        assert all(x.method == "GET" for x in fake.requests[after_inject:])


class TestRetireRecon:
    async def test_item_gone_confirms_delisted(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        lid = _allocate(client, auth, seeded, "B0BEATCH003", "store_r1")
        cmd_id = _delist_to_verify_pending(client, auth, migrated_db, fake, lid)
        after_inject = len(fake.requests)

        fake.routes["GET /v3/items/"] = httpx.Response(404, json={"errors": ["not found"]})
        stats = await tasks.retire_recon(get_session_factory(), {"min_age_s": 0, "grace_s": 3600})
        assert stats["succeeded"] == 1
        with psycopg.connect(migrated_db) as conn:
            assert (
                conn.execute("SELECT status FROM app.listing WHERE id = %s", (lid,)).fetchone()[0]
                == "delisted"
            )
            cmd = conn.execute(
                "SELECT status, result->>'via' FROM app.channel_command WHERE id = %s", (cmd_id,)
            ).fetchone()
            assert cmd == ("succeeded", "retire_recon")
        # 对账阶段只读——零 POST 重发（注入阶段那次 POST 不计）
        assert all(x.method == "GET" for x in fake.requests[after_inject:])

    async def test_item_alive_beyond_grace_reverts_to_live(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        lid = _allocate(client, auth, seeded, "B0BEATCH004", "store_r2")
        cmd_id = _delist_to_verify_pending(client, auth, migrated_db, fake, lid)

        fake.routes["GET /v3/items/"] = httpx.Response(
            200, json={"ItemResponse": [{"lifecycleStatus": "ACTIVE"}]}
        )
        stats = await tasks.retire_recon(get_session_factory(), {"min_age_s": 0, "grace_s": 0})
        assert stats["failed"] == 1
        with psycopg.connect(migrated_db) as conn:
            assert (
                conn.execute("SELECT status FROM app.listing WHERE id = %s", (lid,)).fetchone()[0]
                == "live"
            )
            cmd = conn.execute(
                "SELECT status, error_code FROM app.channel_command WHERE id = %s", (cmd_id,)
            ).fetchone()
            assert cmd == ("failed", "RETIRE_NOT_EFFECTIVE")

    async def test_item_alive_within_grace_keeps_waiting(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        lid = _allocate(client, auth, seeded, "B0BEATCH005", "store_r3")
        cmd_id = _delist_to_verify_pending(client, auth, migrated_db, fake, lid)

        fake.routes["GET /v3/items/"] = httpx.Response(
            200, json={"ItemResponse": [{"lifecycleStatus": "ACTIVE"}]}
        )
        stats = await tasks.retire_recon(get_session_factory(), {"min_age_s": 0, "grace_s": 3600})
        assert stats["pending"] == 1
        with psycopg.connect(migrated_db) as conn:
            assert (
                conn.execute("SELECT status FROM app.listing WHERE id = %s", (lid,)).fetchone()[0]
                == "delist_pending"
            )
            assert (
                conn.execute(
                    "SELECT status FROM app.channel_command WHERE id = %s", (cmd_id,)
                ).fetchone()[0]
                == "verify_pending"
            )


class TestFeedPollHousekeeping:
    async def test_throttle_and_stuck_alert(
        self, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            feed_id = conn.execute(
                "INSERT INTO app.feed (team_id, store_id, feed_kind, status, channel_feed_id,"
                " item_count, submitted_at, poll_attempts)"
                " VALUES (%s, %s, 'item_build', 'processing', 'F-STUCK-1', 1, now(), 47)"
                " RETURNING id",
                (seeded["team"], seeded["store_main"]),
            ).fetchone()[0]
        fake.routes["GET /v3/feeds/F-STUCK-1"] = httpx.Response(
            200, json={"feedStatus": "INPROGRESS"}
        )
        sessions = get_session_factory()
        stats = await tasks.feed_poll(sessions, {"min_interval_s": 300, "stuck_alert_attempts": 48})
        assert stats["polled"] >= 1
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            assert (
                conn.execute("SELECT status FROM app.feed WHERE id = %s", (feed_id,)).fetchone()[0]
                == "processing"
            )
            assert (
                conn.execute(
                    "SELECT 1 FROM app.notification WHERE dedupe_key = %s",
                    (f"feed_stuck:{feed_id}",),
                ).fetchone()
                is not None
            )
            # 节流：last_polled_at 刚刷新 → 立即再扫为空
            stats = await tasks.feed_poll(
                sessions, {"min_interval_s": 300, "stuck_alert_attempts": 48}
            )
            assert stats["scanned"] == 0
            # 收尾终局化，防污染他模块的全局扫描
            conn.execute(
                "UPDATE app.feed SET status = 'error', completed_at = now() WHERE id = %s",
                (feed_id,),
            )


class TestDrainRegistered:
    async def test_channel_outbox_drain_smoke(self, seeded: dict) -> None:
        stats = await tasks.channel_outbox_drain(get_session_factory(), {"limit": 5})
        assert set(stats) == {"swept", "executed", "blocked"}
