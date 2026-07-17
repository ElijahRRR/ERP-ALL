"""RS-03b 验收：channel 写路径 outbox + 幂等（review_list.acceptance 逐条对拍）。

① 同key同payload同结果 / 异payload 409（outbox 单元 + API 重放两层）
② 外部成功后 DB 回写前崩溃 → verify-back 不重复提交（故障注入）
③ lease/fencing token 拒迟到 worker
④ 同store命令 FIFO 有序（跨店不互挡；verify_pending 背压挡道）
⑤ HTTP 调用期间行锁已释放（listing/feed/channel_command 三行 NOWAIT 探针）
⑥ outbox payload 凭证/PII 脱敏（构造性 + 防御断言两层）
"""

import asyncio
import json
import uuid

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.channel import outbox
from erp.channel.gateway import gateway
from erp.core.db import get_session_factory, system_tx
from erp.core.errors import BusinessError
from erp.core.security import hash_password
from erp.core.settings import get_settings

from .test_identity_api import PASSWORD, _login

ADMIN = "outbox_admin"
TEAM = "outbox验收团队"
STORE_CODE = "A152X"
STORE2_CODE = "A152Y"


def _idem(auth: dict) -> dict:
    return {**auth, "Idempotency-Key": str(uuid.uuid4())}


def _ean13(seed: int) -> str:
    body = f"69{seed:010d}"
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(body))
    return body + str((10 - total % 10) % 10)


class _FakeChannel:
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


def _mk_store(conn, team_id: int, channel_id: int, code: str, key: str) -> int:
    sid = conn.execute(
        "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
        " VALUES (%s, %s, %s, %s, true) RETURNING id",
        (team_id, channel_id, code, f"outbox测试店{code}"),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO app.store_credential (store_id, client_id, client_secret_encrypted)"
        " VALUES (%s, 'outbox-client-01', pgp_sym_encrypt('outbox-secret-01', %s))",
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
            " VALUES (%s, %s, %s, 'outbox管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute("DELETE FROM app.role WHERE team_id = %s AND name = 'outbox角色'", (team_id,))
        role_id = conn.execute(
            "INSERT INTO app.role (team_id, name) VALUES (%s, 'outbox角色') RETURNING id",
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
        store_id = _mk_store(conn, team_id, channel_id, STORE_CODE, key)
        store2_id = _mk_store(conn, team_id, channel_id, STORE2_CODE, key)
        # 车道隔离：outbox 原语用例的 pending/verify_pending 残留不得挡 API 用例（FIFO 按店）
        store_unit = _mk_store(conn, team_id, channel_id, "A152U", key)
        store_f1 = _mk_store(conn, team_id, channel_id, "A152F1", key)
        store_f2 = _mk_store(conn, team_id, channel_id, "A152F2", key)
        for i in range(5001, 5011):
            conn.execute(
                "INSERT INTO app.gtin_pool (team_id, gtin, gtin_kind, source)"
                " VALUES (%s, %s, 'ean_13', 'generator_import')",
                (team_id, _ean13(i)),
            )
        pids = {}
        for n in range(1, 4):
            asin = f"B0OUTBOX{n:03d}"
            pids[asin] = conn.execute(
                "INSERT INTO app.product (team_id, source_channel, source_ref, title, attrs,"
                " price_snapshot, status)"
                " VALUES (%s, 'amazon', %s, %s,"
                '  \'{"wpt": "Drinkware", "bullets": ["solid"], "description": "nice"}\','
                "  '{\"list\": 12.99}', 'audit_passed') RETURNING id",
                (team_id, asin, f"Outbox Test Mug Number {n} Blue"),
            ).fetchone()[0]
        conn.execute(
            "INSERT INTO app.system_config (key, value)"
            " VALUES ('channel.gateway_mode', '\"live_test\"'::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value"
        )
    return {
        "team": team_id,
        "user": uid,
        "store": store_id,
        "store2": store2_id,
        "store_unit": store_unit,
        "store_f1": store_f1,
        "store_f2": store_f2,
        **pids,
    }


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


def _payload(n: int = 1) -> dict:
    return {
        "method": "POST",
        "path": "/v3/feeds",
        "endpoint_key": "POST /v3/feeds:MP_ITEM",
        "params": {"feedType": "MP_ITEM"},
        "json_body": {"marker": n},
    }


async def _enqueue(seeded: dict, *, key: str, payload: dict, store: str = "store") -> dict:
    sessions = get_session_factory()
    async with system_tx(sessions) as s:
        return await outbox.enqueue(
            s,
            team_id=seeded["team"],
            store_id=seeded[store],
            action="feed_submit",
            payload=payload,
            idempotency_key=key,
        )


async def _claim(command_id: int, lease: int = 120) -> dict | None:
    sessions = get_session_factory()
    async with system_tx(sessions) as s:
        return await outbox.claim(s, command_id, lease_seconds=lease)


async def _state(command_id: int) -> dict | None:
    sessions = get_session_factory()
    async with system_tx(sessions) as s:
        return await outbox.command_state(s, command_id)


def _drain_lane(db: str, store_id: int) -> None:
    """测试清场：把指定店铺车道的未终局残留直接终局（绕协议仅测试用）。"""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "UPDATE app.channel_command SET status = 'succeeded', completed_at = now(),"
            " lease_expires_at = NULL WHERE store_id = %s"
            " AND status IN ('pending','inflight','verify_pending')",
            (store_id,),
        )


def _age_lease(db: str, command_id: int) -> None:
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "UPDATE app.channel_command SET lease_expires_at = now() - interval '1 min'"
            " WHERE id = %s",
            (command_id,),
        )


class TestIdempotencyContract:
    """验收①：同key同payload同结果；异payload 409（outbox 层 + API 层）。"""

    def test_outbox_same_key_same_payload_returns_existing(self, seeded: dict) -> None:
        key = f"acc1:{uuid.uuid4()}"
        first = asyncio.run(_enqueue(seeded, key=key, payload=_payload(1), store="store_unit"))
        again = asyncio.run(_enqueue(seeded, key=key, payload=_payload(1), store="store_unit"))
        assert first["existing"] is False and again["existing"] is True
        assert again["command_id"] == first["command_id"]  # 同结果=复用同一命令

    def test_outbox_same_key_different_payload_409(self, seeded: dict) -> None:
        key = f"acc1b:{uuid.uuid4()}"
        asyncio.run(_enqueue(seeded, key=key, payload=_payload(1), store="store_unit"))
        with pytest.raises(BusinessError) as e:
            asyncio.run(_enqueue(seeded, key=key, payload=_payload(2), store="store_unit"))
        assert e.value.code == "IDEMPOTENCY_CONFLICT"
        assert e.value.http_status == 409

    def test_api_replay_same_response_no_reexecution(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        alloc = client.post(
            "/api/v1/listings/allocate",
            headers=_idem(auth),
            json={
                "product_ids": [seeded["B0OUTBOX001"]],
                "store_id": seeded["store"],
                "offer_mode": "build",
            },
        ).json()
        lid = alloc["created"][0]["id"]
        fake.script = [httpx.Response(200, json={"feedId": "F-OUTBOX-001"})]
        headers = _idem(auth)  # 固定键，重放复用
        body = {"listing_ids": [lid]}
        r1 = client.post("/api/v1/listings/submit", headers=headers, json=body)
        assert r1.status_code == 202 and r1.json()["feed_status"] == "submitted"
        posts_after_first = len(fake.requests)

        r2 = client.post("/api/v1/listings/submit", headers=headers, json=body)
        assert r2.status_code == 202
        replay = r2.json()
        assert replay.pop("idempotent_replay") is True
        assert replay == r1.json()  # 同结果（存储响应原样重放）
        assert len(fake.requests) == posts_after_first  # 未再发包=未重复执行
        with psycopg.connect(migrated_db) as conn:
            feeds = conn.execute(
                "SELECT count(*) FROM app.feed WHERE team_id = %s", (seeded["team"],)
            ).fetchone()[0]
        assert feeds == 1  # 单 feed——处理器未重跑

        r3 = client.post(
            "/api/v1/listings/submit", headers=headers, json={"listing_ids": [lid, 999999]}
        )
        assert r3.status_code == 409
        assert r3.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


class TestCrashRecovery:
    """验收②：外部成功后 DB 回写前崩溃 → verify-back 对账采认，不重复提交。"""

    def test_crash_after_channel_success_verify_back_adopts_without_resubmit(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        alloc = client.post(
            "/api/v1/listings/allocate",
            headers=_idem(auth),
            json={
                "product_ids": [seeded["B0OUTBOX002"]],
                "store_id": seeded["store"],
                "offer_mode": "build",
            },
        ).json()
        lid = alloc["created"][0]["id"]

        # 故障注入：POST 后进程死（响应丢失）——对渠道是成功，对 ERP 是无响应
        fake.script = [httpx.ConnectTimeout("crash: response lost after channel accepted")]
        r = client.post("/api/v1/listings/submit", headers=_idem(auth), json={"listing_ids": [lid]})
        body = r.json()
        assert body["feed_status"] == "verify_pending"
        feed_id = body["feed_id"]
        assert len(fake.requests) == 1  # 只发过一次
        with psycopg.connect(migrated_db) as conn:
            cmd_id, cmd_status = conn.execute(
                "SELECT id, status FROM app.channel_command WHERE object_type='feed'"
                " AND object_id = %s",
                (feed_id,),
            ).fetchone()
        assert cmd_status == "verify_pending"

        # 车道背压：结果未知期间同店新命令不可领（fail-closed）
        blocked = asyncio.run(_enqueue(seeded, key=f"acc2:{uuid.uuid4()}", payload=_payload(9)))
        assert asyncio.run(_claim(blocked["command_id"])) is None

        # verify-back：渠道近程列表有唯一匹配（渠道确实收到了）→ 采认，绝不重发
        fake.script = [
            httpx.Response(
                200,
                json={
                    "results": {
                        "feed": [
                            {"feedId": "F-CRASH-01", "feedType": "MP_ITEM", "itemsReceived": 1}
                        ]
                    }
                },
            )
        ]
        vb = client.post(f"/api/v1/feeds/{feed_id}/verify-back", headers=auth).json()
        assert vb["feed_status"] == "submitted"
        assert vb["channel_feed_id"] == "F-CRASH-01"
        # 对账全程只多了一个 GET——零次重复 POST
        assert [x.method for x in fake.requests] == ["POST", "GET"]
        cmd = asyncio.run(_state(cmd_id))
        assert cmd and cmd["status"] == "succeeded"
        assert cmd["result"]["via"] == "verify_back_adopt"

        # 命令终局后车道解锁
        claimed = asyncio.run(_claim(blocked["command_id"]))
        assert claimed is not None
        asyncio.run(_complete(claimed, "succeeded"))  # 清场，不挡后续用例


async def _complete(cmd: dict, status: str) -> bool:
    sessions = get_session_factory()
    async with system_tx(sessions) as s:
        return await outbox.complete(s, command_id=cmd["id"], fence=cmd["fence"], status=status)


class TestFencing:
    """验收③：lease 过期被清扫后，迟到 worker 的回写被 fence+status 双重拒绝。"""

    def test_stale_worker_write_back_rejected(self, seeded: dict, migrated_db: str) -> None:
        _drain_lane(migrated_db, seeded["store_unit"])
        enq = asyncio.run(
            _enqueue(seeded, key=f"acc3:{uuid.uuid4()}", payload=_payload(3), store="store_unit")
        )
        cmd = asyncio.run(_claim(enq["command_id"]))
        assert cmd is not None
        stale_fence = cmd["fence"]

        _age_lease(migrated_db, cmd["id"])

        async def _sweep() -> list[dict]:
            sessions = get_session_factory()
            async with system_tx(sessions) as s:
                return await outbox.sweep_expired(s)

        swept = asyncio.run(_sweep())
        assert any(x["id"] == cmd["id"] for x in swept)

        # 迟到 worker 携旧 fence 回写 → 拒绝且不改状态
        ok = asyncio.run(_complete({"id": cmd["id"], "fence": stale_fence}, "succeeded"))
        assert ok is False
        state = asyncio.run(_state(cmd["id"]))
        assert state and state["status"] == "verify_pending"
        assert state["fence"] == stale_fence + 1  # 清扫已推进 fence

        # 对账权威归位可终局（清场）
        async def _resolve() -> bool:
            sessions = get_session_factory()
            async with system_tx(sessions) as s:
                return await outbox.resolve_verify(
                    s, command_id=cmd["id"], status="failed", error_code="FEED_LOST"
                )

        assert asyncio.run(_resolve()) is True


class TestStoreFifo:
    """验收④：同店命令严格 FIFO；跨店互不挡道。"""

    def test_same_store_ordered_cross_store_independent(self, seeded: dict) -> None:
        a = asyncio.run(
            _enqueue(seeded, key=f"acc4a:{uuid.uuid4()}", payload=_payload(41), store="store_f1")
        )
        b = asyncio.run(
            _enqueue(seeded, key=f"acc4b:{uuid.uuid4()}", payload=_payload(42), store="store_f1")
        )
        c = asyncio.run(
            _enqueue(seeded, key=f"acc4c:{uuid.uuid4()}", payload=_payload(43), store="store_f2")
        )
        # 后到者先领 → 被更早未终局命令挡住
        assert asyncio.run(_claim(b["command_id"])) is None
        cmd_a = asyncio.run(_claim(a["command_id"]))
        assert cmd_a is not None
        # A inflight 期间：同店 B 仍不可领；异店 C 可领
        assert asyncio.run(_claim(b["command_id"])) is None
        cmd_c = asyncio.run(_claim(c["command_id"]))
        assert cmd_c is not None
        # A 终局后 B 可领
        assert asyncio.run(_complete(cmd_a, "succeeded")) is True
        cmd_b = asyncio.run(_claim(b["command_id"]))
        assert cmd_b is not None
        asyncio.run(_complete(cmd_b, "succeeded"))
        asyncio.run(_complete(cmd_c, "succeeded"))


class TestLockFreeHttp:
    """验收⑤：渠道 HTTP 期间 listing/feed/channel_command 三行均无行锁（NOWAIT 探针）。"""

    def test_no_row_locks_during_channel_call(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        alloc = client.post(
            "/api/v1/listings/allocate",
            headers=_idem(auth),
            json={
                "product_ids": [seeded["B0OUTBOX003"]],
                "store_id": seeded["store"],
                "offer_mode": "build",
            },
        ).json()
        lid = alloc["created"][0]["id"]
        probe: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v3/token":
                return httpx.Response(200, json={"access_token": "tok", "expires_in": 900})
            with psycopg.connect(migrated_db) as conn:
                for label, sql, args in (
                    ("listing", "SELECT id FROM app.listing WHERE id = %s"
                                " FOR UPDATE NOWAIT", (lid,)),
                    ("feed", "SELECT id FROM app.feed WHERE team_id = %s"
                             " AND status = 'submitting' FOR UPDATE NOWAIT", (seeded["team"],)),
                    ("command", "SELECT id FROM app.channel_command WHERE team_id = %s"
                                " AND status = 'inflight' FOR UPDATE NOWAIT", (seeded["team"],)),
                ):  # fmt: skip
                    try:
                        rows = conn.execute(sql, args).fetchall()
                        probe[label] = {"locked": False, "rows": len(rows)}
                    except psycopg.errors.LockNotAvailable:
                        probe[label] = {"locked": True}
                    conn.rollback()
            return httpx.Response(200, json={"feedId": "F-LOCKFREE-01"})

        gateway._clients.clear()
        gateway._transport_factory = lambda proxy: httpx.MockTransport(handler)
        r = client.post("/api/v1/listings/submit", headers=_idem(auth), json={"listing_ids": [lid]})
        assert r.json()["feed_status"] == "submitted"
        # 三行全部即刻可锁（评审 A7 核心：HTTP 期间零行锁）；且 tx1 产物已提交可见
        assert probe["listing"] == {"locked": False, "rows": 1}
        assert probe["feed"] == {"locked": False, "rows": 1}
        assert probe["command"] == {"locked": False, "rows": 1}


class TestPayloadRedaction:
    """验收⑥：outbox payload 无凭证物料（构造性保证 + enqueue 防御断言）。"""

    def test_forbidden_keys_rejected(self, seeded: dict) -> None:
        for bad in (
            {"json_body": {"client_secret": "x"}},
            {"params": {"Authorization": "Bearer y"}},
            {"json_body": {"nested": [{"proxy_password": "z"}]}},
        ):
            with pytest.raises(BusinessError) as e:
                asyncio.run(
                    _enqueue(seeded, key=f"acc6:{uuid.uuid4()}", payload={**_payload(), **bad})
                )
            assert e.value.code == "OUTBOX_PAYLOAD_FORBIDDEN"

    def test_real_submit_payload_contains_no_credentials(
        self, seeded: dict, migrated_db: str
    ) -> None:
        """真实 submit 落下的命令 payload 全文扫描：凭证/代理值零出现。"""
        with psycopg.connect(migrated_db) as conn:
            rows = conn.execute(
                "SELECT payload::text FROM app.channel_command WHERE team_id = %s"
                " AND object_type = 'feed'",
                (seeded["team"],),
            ).fetchall()
        assert rows  # 前序用例已产生真实 feed 命令
        for (txt,) in rows:
            for secret in ("outbox-client-01", "outbox-secret-01", "Bearer", "access_token"):
                assert secret not in txt
            assert outbox.scan_forbidden_keys(json.loads(txt)) is None


class TestSuspensionGate:
    """R2-07 07b 封店门控（§02:185）：非 active 店 listing 类命令冻结不可领，
    order_ack/order_ship 放行且不被同店被冻结命令挡道；恢复 active 自动续。"""

    def test_suspended_store_freezes_listing_but_not_orders(
        self, seeded: dict, migrated_db: str
    ) -> None:
        key = get_settings().credential_key
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            channel_id = conn.execute(
                "SELECT id FROM app.channel WHERE code='walmart_us'"
            ).fetchone()[0]
            sid = _mk_store(conn, seeded["team"], channel_id, "A152S", key)

        async def _enq(action: str, n: int) -> dict:
            sessions = get_session_factory()
            async with system_tx(sessions) as s:
                return await outbox.enqueue(
                    s,
                    team_id=seeded["team"],
                    store_id=sid,
                    action=action,
                    payload=_payload(n),
                    idempotency_key=f"gate:{action}:{uuid.uuid4()}",
                )

        f1 = asyncio.run(_enq("feed_submit", 71))  # 先入队：listing 维护类
        o1 = asyncio.run(_enq("order_ack", 72))  # 后入队：订单类（同车道，id 更大）
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute("UPDATE app.store SET status = 'suspended' WHERE id = %s", (sid,))
        # 冻结：listing 命令不可领
        assert asyncio.run(_claim(f1["command_id"])) is None
        # 放行：订单命令可领——被冻结的 f1 在前也不得挡道
        cmd_o = asyncio.run(_claim(o1["command_id"]))
        assert cmd_o is not None
        assert asyncio.run(_complete(cmd_o, "succeeded")) is True
        # 恢复 active：f1 按原序可领，自动续
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute("UPDATE app.store SET status = 'active' WHERE id = %s", (sid,))
        cmd_f = asyncio.run(_claim(f1["command_id"]))
        assert cmd_f is not None
        asyncio.run(_complete(cmd_f, "succeeded"))
