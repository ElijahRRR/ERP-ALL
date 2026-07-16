"""R2-06 增量3 验收：价格同步管道（单品 PUT 通道全链）。

- push_price 三段式：渠道 200 → 两段式回填 current_price + price_history
  （BR-LC-011）；明确拒 → failed + notify + 价格不动；传输失败 → verify_pending
  （BR-GW-005 永不盲重试）→ price_recon 拉渠道实价对账归位；
- BR-PR-006 价差 < $0.01 跳过不 enqueue；
- 批量重定价 POST /pricing/reprice（策略算价 → price_changed 过滤 → 逐条 PUT）；
- dry_run 请求快照证据（.agent/evidence/R2-06/dryrun-price-push.json）
  ——断言 json_body 硬性无促销字段（考古口径：同端点靠字段区分，误用即事故）；
- rate_limiter 桶键修正：POST /v3/feeds:PRICE_AND_PROMOTION 命中 10/hour
  （不再错键落 _default）。
渠道全程 MockTransport 替身——A152 真调在 Owner 部署机执行。
"""

import json
import uuid
from pathlib import Path

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.automation import tasks
from erp.channel.gateway import gateway
from erp.channel.gateway.rate_limiter import RateLimiterRegistry
from erp.core.db import get_session_factory
from erp.core.security import hash_password
from erp.core.settings import get_settings

from .test_identity_api import PASSWORD, _login

ADMIN = "price_push_admin"
TEAM = "价格同步团队"
STORE_CODE = "A152PS"  # 渠道内全局唯一（A152P 已被 test_pricing_api 占用）

_BANDS = {"FBA": [[0, 30, 2.75], [30, 80, 2.5]], "FBM": [[15, 80, 2.75], [80, 1000, 2.2]]}
# 19.99（FBM 默认履约）× 2.75 = 54.97
STRATEGY_PRICE = 54.97


def _idem(auth: dict) -> dict:
    return {**auth, "Idempotency-Key": str(uuid.uuid4())}


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
            " VALUES (%s, %s, %s, '价格同步管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute(
            "DELETE FROM app.role WHERE team_id = %s AND name = '价格同步角色'", (team_id,)
        )
        role_id = conn.execute(
            "INSERT INTO app.role (team_id, name) VALUES (%s, '价格同步角色') RETURNING id",
            (team_id,),
        ).fetchone()[0]
        for code in ("listing.read", "listing.submit", "pricing.read", "pricing.write"):
            conn.execute(
                "INSERT INTO app.role_permission (role_id, permission_code) VALUES (%s, %s)",
                (role_id, code),
            )
        conn.execute("INSERT INTO app.user_role (user_id, role_id) VALUES (%s, %s)", (uid, role_id))
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        # 清场（FK 顺序）
        conn.execute("DELETE FROM app.price_history WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.notification WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.listing_state_history WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.channel_command WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.api_idempotency WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.feed_item WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.feed WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.listing WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.pricing_strategy WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.product WHERE team_id = %s", (team_id,))
        conn.execute(
            "DELETE FROM app.store_credential WHERE store_id IN"
            " (SELECT id FROM app.store WHERE team_id = %s)",
            (team_id,),
        )
        conn.execute("DELETE FROM app.store WHERE team_id = %s", (team_id,))
        store_id = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, %s, '价格同步店', true) RETURNING id",
            (team_id, channel_id, STORE_CODE),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO app.store_credential (store_id, client_id, client_secret_encrypted)"
            " VALUES (%s, 'pricepush-client-01', pgp_sym_encrypt('pricepush-secret-01', %s))",
            (store_id, key),
        )
        strategy_id = conn.execute(
            "INSERT INTO app.pricing_strategy"
            " (team_id, offer_mode, name, algo_code, params, created_by)"
            " VALUES (%s, 'build', '同步默认', 'cost_plus', %s, %s) RETURNING id",
            (team_id, json.dumps({"bands": _BANDS, "min_price": 6.99}), uid),
        ).fetchone()[0]
        out: dict[str, int] = {
            "team": team_id, "user": uid, "store": store_id, "strategy": strategy_id,
        }  # fmt: skip
        # live listing 六枚（同店同产品形态；channel_sku 唯一）
        for i in range(1, 7):
            pid = conn.execute(
                "INSERT INTO app.product (team_id, master_sku, source_channel, source_ref,"
                " title, price_snapshot, status)"
                " VALUES (%s, %s, 'amazon', %s, %s, '{\"list\": 19.99}', 'ready') RETURNING id",
                (team_id, f"MPP26{i:04d}", f"B0PUSH{i:04d}", f"价格同步商品{i}"),
            ).fetchone()[0]
            out[f"lid{i}"] = conn.execute(
                "INSERT INTO app.listing (team_id, store_id, product_id, offer_mode,"
                " channel_sku, status, current_price)"
                " VALUES (%s, %s, %s, 'build', %s, 'live', %s) RETURNING id",
                (team_id, store_id, pid, f"MPP26{i:04d}", STRATEGY_PRICE),
            ).fetchone()[0]
        conn.execute(
            "INSERT INTO app.system_config (key, value)"
            " VALUES ('channel.gateway_mode', '\"live_test\"'::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value"
        )
    return out


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> TestClient:
    from erp.main import create_app

    return TestClient(create_app())


@pytest.fixture()
def fake(seeded: dict[str, int]) -> _FakeChannel:
    f = _FakeChannel()
    gateway._clients.clear()  # 连接池缓存旧 transport，必须清（单例网关）
    gateway._transport_factory = lambda proxy: httpx.MockTransport(f.handler)
    yield f
    gateway._clients.clear()
    gateway._transport_factory = gateway._default_transport


def _sku(migrated_db: str, lid: int) -> str:
    with psycopg.connect(migrated_db) as conn:
        return conn.execute("SELECT channel_sku FROM app.listing WHERE id = %s", (lid,)).fetchone()[
            0
        ]


def _command(migrated_db: str, lid: int) -> tuple[str, str | None]:
    with psycopg.connect(migrated_db) as conn:
        return conn.execute(
            "SELECT status, error_code FROM app.channel_command"
            " WHERE action = 'price_push' AND object_id = %s ORDER BY id DESC LIMIT 1",
            (lid,),
        ).fetchone()


class TestPushSuccess:
    def test_patch_live_pushes_put_and_backfills(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """① 渠道 200 → 命令 succeeded + current_price 回填 + price_history(manual)。"""
        auth = _login(client, ADMIN, PASSWORD)
        lid = seeded["lid1"]
        sku = _sku(migrated_db, lid)
        fake.script = [
            httpx.Response(200, json={"sku": sku, "message": "Thank you. Price updated."})
        ]
        r = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 60.0})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "succeeded"

        # 出门请求 = canonical 单品体（口径2）：BASE 常规价、无促销字段
        assert len(fake.requests) == 1
        req = fake.requests[0]
        assert req.method == "PUT" and req.url.path == "/v3/price"
        sent = json.loads(req.content)
        assert sent == {
            "sku": sku,
            "pricing": [
                {"currentPriceType": "BASE", "currentPrice": {"currency": "USD", "amount": 60.0}}
            ],
        }
        assert "promo" not in req.url.query.decode().lower()

        with psycopg.connect(migrated_db) as conn:
            cp = conn.execute(
                "SELECT current_price FROM app.listing WHERE id = %s", (lid,)
            ).fetchone()[0]
            assert float(cp) == 60.0
            hist = conn.execute(
                "SELECT old_price, new_price, reason, detail FROM app.price_history"
                " WHERE listing_id = %s ORDER BY id",
                (lid,),
            ).fetchall()
        assert len(hist) == 1
        old, new, reason, detail = hist[0]
        assert (float(old), float(new), reason) == (STRATEGY_PRICE, 60.0, "manual")
        assert detail["channel_message"] == "Thank you. Price updated."
        assert _command(migrated_db, lid)[0] == "succeeded"


class TestPushRejected:
    def test_channel_4xx_fails_without_backfill(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """② 明确拒（4xx）→ failed + notify(warn/pricing)，价格不回填。"""
        auth = _login(client, ADMIN, PASSWORD)
        lid = seeded["lid2"]
        err_body = {
            "errors": {
                "error": [
                    {"code": "CONTENT_NOT_FOUND", "field": "sku", "description": "sku not found"}
                ]
            }
        }
        fake.script = [httpx.Response(400, json=err_body)]
        r = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 60.0})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "failed"

        status, error_code = _command(migrated_db, lid)
        assert status == "failed"
        # 网关只解析 2xx 响应体（4xx data=None）→ 回落 HTTP_400
        assert error_code == "HTTP_400"
        with psycopg.connect(migrated_db) as conn:
            cp = conn.execute(
                "SELECT current_price FROM app.listing WHERE id = %s", (lid,)
            ).fetchone()[0]
            assert float(cp) == STRATEGY_PRICE  # 价格不动
            hist_n = conn.execute(
                "SELECT count(*) FROM app.price_history WHERE listing_id = %s", (lid,)
            ).fetchone()[0]
            assert hist_n == 0  # 无回填
            note = conn.execute(
                "SELECT severity, category FROM app.notification WHERE dedupe_key = %s",
                (f"price_push:{lid}",),
            ).fetchone()
        assert note == ("warn", "pricing")


class TestTransportFailThenRecon:
    async def test_verify_pending_then_price_recon_confirms(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """③ 传输失败 → verify_pending（不重发、价格不动）→ price_recon 渠道实价对账
        确认 → resolve succeeded + 补两段式回填。"""
        auth = _login(client, ADMIN, PASSWORD)
        lid = seeded["lid3"]
        sku = _sku(migrated_db, lid)
        fake.script = [httpx.ConnectTimeout("boom: response lost")]
        r = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 60.0})
        assert r.json()["status"] == "verify_pending"
        assert len(fake.requests) == 1  # 只发了一次——永不盲重试
        assert _command(migrated_db, lid)[0] == "verify_pending"
        with psycopg.connect(migrated_db) as conn:
            cp = conn.execute(
                "SELECT current_price FROM app.listing WHERE id = %s", (lid,)
            ).fetchone()[0]
        assert float(cp) == STRATEGY_PRICE  # 结果未知不回填

        # 对账：渠道实价 == 目标价（±0.01）→ succeeded + 回填
        fake.script = [
            httpx.Response(
                200,
                json={"ItemResponse": [{"sku": sku, "price": {"currency": "USD", "amount": 60.0}}]},
            )
        ]
        stats = await tasks.price_recon(
            get_session_factory(), {"batch": 10, "min_age_s": 0, "grace_s": 3600}
        )
        assert stats["succeeded"] == 1 and stats["failed"] == 0
        # 对账阶段零 PUT（绝不重发）
        assert all(x.method == "GET" for x in fake.requests[1:])
        assert _command(migrated_db, lid)[0] == "succeeded"
        with psycopg.connect(migrated_db) as conn:
            cp, pp = conn.execute(
                "SELECT current_price, pending_price FROM app.listing WHERE id = %s", (lid,)
            ).fetchone()
            assert float(cp) == 60.0
            assert pp is None  # 在途标记随确认清除（BR-LC-011 两段式）
            hist = conn.execute(
                "SELECT old_price, new_price, reason, detail FROM app.price_history"
                " WHERE listing_id = %s ORDER BY id",
                (lid,),
            ).fetchall()
        assert len(hist) == 1
        old, new, reason, detail = hist[0]
        assert (float(old), float(new), reason) == (STRATEGY_PRICE, 60.0, "manual")
        assert detail["via"] == "price_recon" and float(detail["channel_price"]) == 60.0


class TestUnchangedSkip:
    def test_sub_cent_delta_skips_enqueue(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """④ 价差 < $0.01（BR-PR-006）→ 跳过：不 enqueue、不发包、不写价史。"""
        auth = _login(client, ADMIN, PASSWORD)
        lid = seeded["lid1"]  # ① 已回填 60.0
        with psycopg.connect(migrated_db) as conn:
            before = conn.execute(
                "SELECT count(*) FROM app.channel_command WHERE action = 'price_push'"
                " AND object_id = %s",
                (lid,),
            ).fetchone()[0]
        r = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 60.0})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["skipped"] is True and body["reason"] == "unchanged"
        assert fake.requests == []
        with psycopg.connect(migrated_db) as conn:
            after = conn.execute(
                "SELECT count(*) FROM app.channel_command WHERE action = 'price_push'"
                " AND object_id = %s",
                (lid,),
            ).fetchone()[0]
        assert after == before


class TestRepriceBatch:
    def test_reprice_pushes_changed_and_skips_unchanged(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """⑤ 两条 live：一条价变（50 → 54.97）推送、一条已在策略价跳过 unchanged。"""
        auth = _login(client, ADMIN, PASSWORD)
        lid_change, lid_same = seeded["lid4"], seeded["lid5"]
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute("UPDATE app.listing SET current_price = 50.0 WHERE id = %s", (lid_change,))
        fake.script = [httpx.Response(200, json={"message": "Thank you."})]
        r = client.post(
            "/api/v1/pricing/reprice",
            headers=_idem(auth),
            json={"listing_ids": [lid_change, lid_same]},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["pushed"] == 1 and body["failed"] == []
        assert body["skipped"] == [{"listing_id": lid_same, "reason": "unchanged"}]

        # 出门请求恰一发（多发即静默超发配额），体内价格 = 策略算价 54.97
        assert len(fake.requests) == 1
        req = fake.requests[0]
        assert req.method == "PUT" and req.url.path == "/v3/price"
        sent = json.loads(req.content)
        assert sent["pricing"][0]["currentPrice"]["amount"] == STRATEGY_PRICE

        with psycopg.connect(migrated_db) as conn:
            cp = conn.execute(
                "SELECT current_price FROM app.listing WHERE id = %s", (lid_change,)
            ).fetchone()[0]
            assert float(cp) == STRATEGY_PRICE
            hist = conn.execute(
                "SELECT old_price, new_price, reason, strategy_id, strategy_version"
                " FROM app.price_history WHERE listing_id = %s ORDER BY id",
                (lid_change,),
            ).fetchall()
        assert len(hist) == 1
        old, new, reason, sid, sver = hist[0]
        assert (float(old), float(new)) == (50.0, STRATEGY_PRICE)
        assert reason == "strategy" and sid == seeded["strategy"] and sver == 1
        assert _command(migrated_db, lid_change)[0] == "succeeded"


class TestDryRunEvidence:
    def test_dry_run_snapshot_no_promo_fields(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """⑥ dry_run 请求快照证据：endpoint_key=PUT /v3/price 且体内硬性无促销字段。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.system_config SET value = '\"dry_run\"'::jsonb"
                " WHERE key = 'channel.gateway_mode'"
            )
        try:
            auth = _login(client, ADMIN, PASSWORD)
            lid = seeded["lid6"]
            r = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 61.0})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body.get("dry_run") is True
            snap = body["request_snapshot"]
            assert snap["method"] == "PUT" and snap["url"].endswith("/v3/price")
            assert snap["endpoint_key"] == "PUT /v3/price"
            assert set(snap["json_body"]) == {"sku", "pricing"}
            assert snap["json_body"]["pricing"][0]["currentPriceType"] == "BASE"
            # 促销字段硬性禁止（考古口径：同端点靠 query/字段区分，误用即事故）
            serialized = json.dumps(snap, ensure_ascii=False).lower()
            for token in ("promo", "effectivedate", "expirationdate", "processmode"):
                assert token not in serialized
            assert not snap.get("params")  # 无 ?promo=true 类查询参数
            repo_root = Path(__file__).resolve().parents[3]
            out = repo_root / ".agent" / "evidence" / "R2-06" / "dryrun-price-push.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
            assert fake.requests == []  # dry_run 零发包
        finally:
            with psycopg.connect(migrated_db, autocommit=True) as conn:
                conn.execute(
                    "UPDATE app.system_config SET value = '\"live_test\"'::jsonb"
                    " WHERE key = 'channel.gateway_mode'"
                )


class TestRateLimiterKeys:
    def test_price_feed_bucket_hits_corrected_limit(self) -> None:
        """⑦ 桶键修正：POST /v3/feeds:PRICE_AND_PROMOTION 命中 10/3600（考古口径3），
        不再因错键落 _default(120/60)；PUT /v3/price 100/3600 维持。"""
        reg = RateLimiterRegistry()
        bucket = reg.get("s1", "POST /v3/feeds:PRICE_AND_PROMOTION")
        assert (bucket.limit, bucket.period) == (10, 3600)
        put_bucket = reg.get("s1", "PUT /v3/price")
        assert (put_bucket.limit, put_bucket.period) == (100, 3600)


# ── R2-06 增量3 评审修复回归（幂等轮次/429/对账收敛/阈值守护/路由/两段式）──


def _listing_state(migrated_db: str, lid: int) -> tuple[float | None, float | None, int]:
    """→ (current_price, pending_price, price_history 行数)。"""
    with psycopg.connect(migrated_db) as conn:
        cp, pp = conn.execute(
            "SELECT current_price, pending_price FROM app.listing WHERE id = %s", (lid,)
        ).fetchone()
        n = conn.execute(
            "SELECT count(*) FROM app.price_history WHERE listing_id = %s", (lid,)
        ).fetchone()[0]
    return (float(cp) if cp is not None else None, float(pp) if pp is not None else None, n)


def _command_count(migrated_db: str, lid: int) -> int:
    with psycopg.connect(migrated_db) as conn:
        return conn.execute(
            "SELECT count(*) FROM app.channel_command"
            " WHERE action = 'price_push' AND object_id = %s",
            (lid,),
        ).fetchone()[0]


def _mk_listing(
    migrated_db: str,
    seeded: dict,
    tag: str,
    *,
    status: str = "live",
    current_price: float | None = 50.0,
    offer_mode: str = "build",
    is_locked: bool = False,
    pending_price: float | None = None,
    price_snapshot: str = '{"list": 19.99}',
    store_id: int | None = None,
) -> int:
    """评审回归用 listing 工厂（product + listing 一体，channel_sku 唯一）。"""
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        pid = conn.execute(
            "INSERT INTO app.product (team_id, master_sku, source_channel, source_ref,"
            " title, price_snapshot, status)"
            " VALUES (%s, %s, 'amazon', %s, %s, %s, 'ready') RETURNING id",
            (seeded["team"], f"MPPX{tag}", f"B0PX{tag}", f"评审商品{tag}", price_snapshot),
        ).fetchone()[0]
        return conn.execute(
            "INSERT INTO app.listing (team_id, store_id, product_id, offer_mode, channel_sku,"
            " status, current_price, is_locked, pending_price)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                seeded["team"],
                store_id or seeded["store"],
                pid,
                offer_mode,
                f"MPPX{tag}",
                status,
                current_price,
                is_locked,
                pending_price,
            ),
        ).fetchone()[0]


class TestEpisodeRetryAfterFailure:
    """评审发现 1/7/17：failed 终局后同价重推必须真发包（幂等键带轮次不复用旧命令）。"""

    def test_same_price_retry_after_channel_reject_sends_new_put(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        lid = seeded["lid2"]  # 测试②里 60.0 已被渠道 4xx 拒、命令终局 failed
        assert _command(migrated_db, lid) == ("failed", "HTTP_400")
        before = _command_count(migrated_db, lid)
        fake.script = [httpx.Response(200, json={"message": "Thank you."})]
        r = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 60.0})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "succeeded"
        # 真发了新 PUT（旧实现：复用 failed 命令 → 零发包返回 failed）
        assert len(fake.requests) == 1 and fake.requests[0].method == "PUT"
        assert _command_count(migrated_db, lid) == before + 1
        assert _command(migrated_db, lid)[0] == "succeeded"
        cp, pp, hist_n = _listing_state(migrated_db, lid)
        assert (cp, pp, hist_n) == (60.0, None, 1)

    def test_price_oscillation_a_b_a_no_conflict(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """A→B→A 回摆不撞历史幂等键（旧实现：payload_hash 异 → 409）。"""
        auth = _login(client, ADMIN, PASSWORD)
        lid = seeded["lid1"]  # 当前 60.0
        fake.script = [httpx.Response(200, json={}), httpx.Response(200, json={})]
        r1 = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 65.0})
        assert r1.status_code == 200 and r1.json()["status"] == "succeeded"
        r2 = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 60.0})
        assert r2.status_code == 200, r2.text  # 旧实现此处 409 IDEMPOTENCY_CONFLICT
        assert r2.json()["status"] == "succeeded"
        cp, pp, _ = _listing_state(migrated_db, lid)
        assert (cp, pp) == (60.0, None)


class TestChannel429:
    """评审发现 5/13：429=渠道确定未受理（暂态）——归还 pending 等 beat，绝非终局 failed。"""

    def test_channel_429_releases_command_to_pending(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        lid = seeded["lid4"]  # 当前 54.97
        _, _, hist_before = _listing_state(migrated_db, lid)
        fake.script = [httpx.Response(429, headers={"retry-after": "0"}, json={})]
        r = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 58.0})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "pending" and body["error_code"] == "HTTP_429"
        status, _error_code = _command(migrated_db, lid)
        assert status == "pending"  # 留 pending 由 beat 续推，不判 failed
        cp, pp, hist_n = _listing_state(migrated_db, lid)
        assert (cp, pp) == (STRATEGY_PRICE, 58.0)  # 价格不动，在途标记保留
        assert hist_n == hist_before
        # 清场：终局残留命令 + 复位在途标记（防跨文件 drain 领走污染）
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM app.channel_command WHERE action = 'price_push'"
                " AND object_id = %s AND status = 'pending'",
                (lid,),
            )
            conn.execute("UPDATE app.listing SET pending_price = NULL WHERE id = %s", (lid,))

    def test_gate_refused_keeps_pending_zero_bytes(
        self,
        client: TestClient,
        seeded: dict,
        migrated_db: str,
        fake: _FakeChannel,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """限流闸拒绝（发包前）：命令留 pending、计入 pushed、零字节出门。"""
        from erp.channel.gateway.rate_limiter import registry

        async def _refuse(*_a: object, **_k: object) -> float:
            return -1.0

        monkeypatch.setattr(registry, "gate", _refuse)
        auth = _login(client, ADMIN, PASSWORD)
        lid = seeded["lid4"]
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute("UPDATE app.listing SET current_price = 50.0 WHERE id = %s", (lid,))
        r = client.post("/api/v1/pricing/reprice", headers=_idem(auth), json={"listing_ids": [lid]})
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["pushed"] == 1 and body["failed"] == [] and body["skipped"] == []
        assert fake.requests == []  # 零字节出门
        assert _command(migrated_db, lid)[0] == "pending"
        cp, pp, _ = _listing_state(migrated_db, lid)
        assert (cp, pp) == (50.0, STRATEGY_PRICE)
        # 清场：删残留 pending 命令 + 复位（防跨文件 drain 领走）
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM app.channel_command WHERE action = 'price_push'"
                " AND object_id = %s AND status = 'pending'",
                (lid,),
            )
            conn.execute(
                "UPDATE app.listing SET pending_price = NULL, current_price = %s WHERE id = %s",
                (STRATEGY_PRICE, lid),
            )


class TestPriceReconBranches:
    """评审发现 2/14：不等分支（grace 内 pending / 超 grace 判败）+ 非 200 确定态收敛。"""

    async def test_mismatch_within_grace_then_fail_over_grace(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        lid = seeded["lid5"]  # 当前 54.97，无历史命令
        sku = _sku(migrated_db, lid)
        fake.script = [httpx.ConnectTimeout("boom")]
        r = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 60.0})
        assert r.json()["status"] == "verify_pending"
        cp, pp, _ = _listing_state(migrated_db, lid)
        assert (cp, pp) == (STRATEGY_PRICE, 60.0)

        # ① 渠道价不等 + 未超 grace → pending（命令滞留 verify_pending，不判败）
        mismatch = httpx.Response(
            200,
            json={"ItemResponse": [{"sku": sku, "price": {"currency": "USD", "amount": 59.0}}]},
        )
        fake.script = [mismatch]
        stats = await tasks.price_recon(
            get_session_factory(), {"batch": 10, "min_age_s": 0, "grace_s": 3600}
        )
        assert stats["pending"] == 1 and stats["failed"] == 0
        assert _command(migrated_db, lid)[0] == "verify_pending"

        # ② 渠道 404（确定态非 200）+ 未超 grace → 仍 pending（不臆断）
        fake.script = [httpx.Response(404, json={})]
        stats = await tasks.price_recon(
            get_session_factory(), {"batch": 10, "min_age_s": 0, "grace_s": 3600}
        )
        assert stats["pending"] == 1 and stats["failed"] == 0

        # ③ 渠道价不等 + 超 grace → failed + PRICE_PUSH_NOT_EFFECTIVE + notify，不回填
        fake.script = [
            httpx.Response(
                200,
                json={"ItemResponse": [{"sku": sku, "price": {"currency": "USD", "amount": 59.0}}]},
            )
        ]
        stats = await tasks.price_recon(
            get_session_factory(), {"batch": 10, "min_age_s": 0, "grace_s": 0}
        )
        assert stats["failed"] == 1
        assert _command(migrated_db, lid) == ("failed", "PRICE_PUSH_NOT_EFFECTIVE")
        cp, pp, hist_n = _listing_state(migrated_db, lid)
        assert (cp, pp, hist_n) == (STRATEGY_PRICE, None, 0)  # 不回填 + 在途标记复位
        with psycopg.connect(migrated_db) as conn:
            note = conn.execute(
                "SELECT severity, category FROM app.notification WHERE dedupe_key = %s",
                (f"price_push:{lid}",),
            ).fetchone()
        assert note == ("warn", "pricing")

    async def test_persistent_404_converges_to_failed_over_grace(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """确定态 404 超 grace 必须收敛 failed——不许 verify_pending 永久堵死店铺车道。"""
        auth = _login(client, ADMIN, PASSWORD)
        lid = seeded["lid6"]
        fake.script = [httpx.ConnectTimeout("boom")]
        r = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 62.0})
        assert r.json()["status"] == "verify_pending"
        fake.script = [httpx.Response(404, json={})]
        stats = await tasks.price_recon(
            get_session_factory(), {"batch": 10, "min_age_s": 0, "grace_s": 0}
        )
        assert stats["failed"] == 1  # 旧实现：非 200 永远 pending，车道死锁
        assert _command(migrated_db, lid) == ("failed", "PRICE_PUSH_NOT_EFFECTIVE")
        cp, pp, hist_n = _listing_state(migrated_db, lid)
        assert (cp, pp, hist_n) == (STRATEGY_PRICE, None, 0)


class TestConfirmThresholdLive:
    """评审发现 15：BR-PR-008 30% 阈值 / min_price 守护在 live 推送路径的行为钉死。"""

    def test_patch_live_over_threshold_requires_force(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        lid = seeded["lid3"]  # 当前 60.0
        before = _command_count(migrated_db, lid)
        r = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 100.0})
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "PRICING_CONFIRM_REQUIRED"
        assert fake.requests == []  # tx1 拒绝，零发包
        assert _command_count(migrated_db, lid) == before  # 无残留命令
        cp, pp, _ = _listing_state(migrated_db, lid)
        assert (cp, pp) == (60.0, None)

        fake.script = [httpx.Response(200, json={})]
        r2 = client.patch(
            f"/api/v1/listings/{lid}", headers=auth, json={"price": 100.0, "force": True}
        )
        assert r2.status_code == 200 and r2.json()["status"] == "succeeded"
        cp, _, _ = _listing_state(migrated_db, lid)
        assert cp == 100.0

    def test_patch_live_below_min_price_rejected(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        lid = seeded["lid3"]
        r = client.patch(
            f"/api/v1/listings/{lid}", headers=auth, json={"price": 5.0, "force": True}
        )
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "PRICING_BELOW_MIN_PRICE"  # 策略 min_price=6.99
        assert fake.requests == []

    def test_reprice_over_threshold_failed_then_force_pushes(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        lid = seeded["lid4"]
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute("UPDATE app.listing SET current_price = 10.0 WHERE id = %s", (lid,))
        r = client.post("/api/v1/pricing/reprice", headers=_idem(auth), json={"listing_ids": [lid]})
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["pushed"] == 0
        assert body["failed"] == [{"listing_id": lid, "code": "PRICING_CONFIRM_REQUIRED"}]
        assert fake.requests == []

        fake.script = [httpx.Response(200, json={})]
        r2 = client.post(
            "/api/v1/pricing/reprice",
            headers=_idem(auth),
            json={"listing_ids": [lid], "force": True},
        )
        assert r2.status_code == 202, r2.text
        assert r2.json()["pushed"] == 1 and r2.json()["failed"] == []
        cp, pp, _ = _listing_state(migrated_db, lid)
        assert (cp, pp) == (STRATEGY_PRICE, None)


class TestRepriceSkipReasons:
    """评审发现 16：skipped.reason 枚举全量钉死（重构 _reprice_gate 的回归网）。"""

    def test_all_skip_reasons_exact(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        delisted = _mk_listing(migrated_db, seeded, "SK01", status="delisted")
        locked = _mk_listing(migrated_db, seeded, "SK02", is_locked=True)
        no_strategy = _mk_listing(migrated_db, seeded, "SK03", offer_mode="match")
        no_source = _mk_listing(migrated_db, seeded, "SK04", price_snapshot='{"list": "N/A"}')
        in_flight = _mk_listing(migrated_db, seeded, "SK05", pending_price=99.0)
        # 店铺级 manual 策略（store2）：resolve 优先 store 级 → manual 跳过
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            channel_id = conn.execute(
                "SELECT id FROM app.channel WHERE code='walmart_us'"
            ).fetchone()[0]
            store2 = conn.execute(
                "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
                " VALUES (%s, %s, 'A152PS2', '价格同步店2', true) RETURNING id",
                (seeded["team"], channel_id),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO app.pricing_strategy"
                " (team_id, store_id, offer_mode, name, algo_code, params, created_by)"
                " VALUES (%s, %s, 'build', '人工策略', 'manual', %s, %s)",
                (seeded["team"], store2, json.dumps({"min_price": 6.99}), seeded["user"]),
            )
        manual = _mk_listing(migrated_db, seeded, "SK06", store_id=store2)
        missing = 999999999
        ids = [missing, delisted, locked, no_strategy, manual, no_source, in_flight]
        r = client.post("/api/v1/pricing/reprice", headers=_idem(auth), json={"listing_ids": ids})
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["pushed"] == 0 and body["failed"] == []
        assert fake.requests == []  # 全部预检拦截，零发包
        reasons = {s["listing_id"]: s["reason"] for s in body["skipped"]}
        assert reasons == {
            missing: "not_found",
            delisted: "not_live",
            locked: "locked",
            no_strategy: "no_strategy",
            manual: "manual",
            no_source: "no_source_price",
            in_flight: "push_in_flight",
        }


class TestPublishedListingPipeline:
    """评审发现 16：published 口径钉死——PATCH 与 reprice 都走定价管道（与 push_price
    准入 live/published 对齐，不再掉进 draft 直改分支）。"""

    def test_published_patch_and_reprice_go_through_pipeline(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        lid = _mk_listing(migrated_db, seeded, "PB01", status="published")
        fake.script = [httpx.Response(200, json={})]
        r = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 60.0})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "succeeded"  # 渠道管道（draft 直改无 status 键值）
        assert _command(migrated_db, lid)[0] == "succeeded"  # 真走了 outbox price_push
        cp, _, _ = _listing_state(migrated_db, lid)
        assert cp == 60.0

        fake.script = [httpx.Response(200, json={})]
        r2 = client.post(
            "/api/v1/pricing/reprice", headers=_idem(auth), json={"listing_ids": [lid]}
        )
        assert r2.status_code == 202 and r2.json()["pushed"] == 1  # gate 放行 published
        cp, _, _ = _listing_state(migrated_db, lid)
        assert cp == STRATEGY_PRICE


class TestFeedRoutingBatch:
    """评审发现 4/6：D-Q62 双通道路由——单店 > 阈值(5) 聚合 PRICE_AND_PROMOTION feed；
    pending_price 两段式：派发只记在途，item 级 SUCCESS 才回填。"""

    async def test_batch_over_threshold_routes_to_feed_then_poll_backfills(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        lids = [_mk_listing(migrated_db, seeded, f"FD{i:02d}") for i in range(1, 8)]  # 7 条
        fake.script = [httpx.Response(200, json={"feedId": "PF0001"})]
        r = client.post("/api/v1/pricing/reprice", headers=_idem(auth), json={"listing_ids": lids})
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["pushed"] == 7 and body["failed"] == [] and body["skipped"] == []

        # 出门恰一发：POST /v3/feeds?feedType=PRICE_AND_PROMOTION，canonical envelope
        assert len(fake.requests) == 1
        req = fake.requests[0]
        assert req.method == "POST" and req.url.path == "/v3/feeds"
        assert req.url.params["feedType"] == "PRICE_AND_PROMOTION"
        envelope = json.loads(req.content)
        assert envelope["MPItemFeedHeader"] == {
            "businessUnit": "WALMART_US",
            "locale": "en",
            "version": "2.0.20240126-12_25_52-api",
        }
        assert len(envelope["MPItem"]) == 7
        for item in envelope["MPItem"]:
            assert set(item) == {"Promo&Discount"}
            assert set(item["Promo&Discount"]) == {"sku", "price"}
            assert item["Promo&Discount"]["price"] == STRATEGY_PRICE

        with psycopg.connect(migrated_db) as conn:
            feed_id, kind, fstatus, cf, n = conn.execute(
                "SELECT id, feed_kind, status, channel_feed_id, item_count FROM app.feed"
                " WHERE feed_kind = 'price' AND team_id = %s ORDER BY id DESC LIMIT 1",
                (seeded["team"],),
            ).fetchone()
            cmd_status = conn.execute(
                "SELECT status FROM app.channel_command WHERE action = 'feed_submit'"
                " AND object_type = 'feed' AND object_id = %s",
                (feed_id,),
            ).fetchone()[0]
        assert (kind, fstatus, cf, n) == ("price", "submitted", "PF0001", 7)
        assert cmd_status == "succeeded"
        # 两段式派发半程：current_price 不动、pending_price 记在途
        for lid in lids:
            cp, pp, hist_n = _listing_state(migrated_db, lid)
            assert (cp, pp, hist_n) == (50.0, STRATEGY_PRICE, 0)

        # feed_poll price 分支：6 SUCCESS 回填 + 1 error 复位
        from erp.listing import service as listing_service

        skus = {lid: _sku(migrated_db, lid) for lid in lids}
        statuses = [
            {"sku": skus[lid], "ingestionStatus": "SUCCESS", "wpid": None} for lid in lids[:6]
        ]
        statuses.append(
            {
                "sku": skus[lids[6]],
                "ingestionStatus": "DATA_ERROR",
                "ingestionErrors": {
                    "ingestionError": [{"code": "ERR_PRICE_X", "description": "bad price"}]
                },
            }
        )
        fake.script = [
            httpx.Response(
                200,
                json={
                    "feedStatus": "PROCESSED",
                    "itemDetails": {"itemIngestionStatus": statuses},
                },
            )
        ]
        out = await listing_service.poll_feed(get_session_factory(), feed_id, is_super=True)
        assert out["feed_status"] == "partial" and out["success"] == 6 and out["error"] == 1
        for lid in lids[:6]:
            cp, pp, hist_n = _listing_state(migrated_db, lid)
            assert (cp, pp, hist_n) == (STRATEGY_PRICE, None, 1)
            with psycopg.connect(migrated_db) as conn:
                old, new, reason, detail = conn.execute(
                    "SELECT old_price, new_price, reason, detail FROM app.price_history"
                    " WHERE listing_id = %s",
                    (lid,),
                ).fetchone()
            assert (float(old), float(new), reason) == (50.0, STRATEGY_PRICE, "strategy")
            assert detail["via"] == "price_feed" and detail["feed_id"] == feed_id
        # 失败项：不回填 + 在途复位
        cp, pp, hist_n = _listing_state(migrated_db, lids[6])
        assert (cp, pp, hist_n) == (50.0, None, 0)
        with psycopg.connect(migrated_db) as conn:
            fi = conn.execute(
                "SELECT status, error_code FROM app.feed_item WHERE feed_id = %s"
                " AND channel_sku = %s",
                (feed_id, skus[lids[6]]),
            ).fetchone()
            note = conn.execute(
                "SELECT severity, category FROM app.notification WHERE dedupe_key = %s",
                (f"price_feed_result:{feed_id}",),
            ).fetchone()
        assert fi == ("error", "ERR_PRICE_X")
        assert note == ("warn", "pricing")


class TestPushInFlightGuard:
    """评审发现 9：前一命令未终局时新推价必须被拒（409），不许以陈旧 old_price 入队。"""

    def test_patch_while_verify_pending_rejected_409(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        lid = _mk_listing(migrated_db, seeded, "IF01")
        fake.script = [httpx.ConnectTimeout("boom")]
        r = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 60.0})
        assert r.json()["status"] == "verify_pending"
        before = _command_count(migrated_db, lid)
        # 在途窗口内改回原价：旧实现静默 unchanged 吞掉用户意图；现拒 409 明示在途
        r2 = client.patch(f"/api/v1/listings/{lid}", headers=auth, json={"price": 50.0})
        assert r2.status_code == 409, r2.text
        assert r2.json()["error"]["code"] == "PRICING_PUSH_IN_FLIGHT"
        assert _command_count(migrated_db, lid) == before  # 未入队新命令
        # 清场：终局残留 verify_pending 命令 + 复位在途标记
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.channel_command SET status = 'failed', completed_at = now(),"
                " lease_expires_at = NULL WHERE action = 'price_push' AND object_id = %s"
                " AND status = 'verify_pending'",
                (lid,),
            )
            conn.execute("UPDATE app.listing SET pending_price = NULL WHERE id = %s", (lid,))
