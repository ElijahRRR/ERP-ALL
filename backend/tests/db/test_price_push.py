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
            cp = conn.execute(
                "SELECT current_price FROM app.listing WHERE id = %s", (lid,)
            ).fetchone()[0]
            assert float(cp) == 60.0
            reason, detail = conn.execute(
                "SELECT reason, detail FROM app.price_history WHERE listing_id = %s"
                " ORDER BY id DESC LIMIT 1",
                (lid,),
            ).fetchone()
        assert reason == "manual" and detail["via"] == "price_recon"


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

        with psycopg.connect(migrated_db) as conn:
            cp = conn.execute(
                "SELECT current_price FROM app.listing WHERE id = %s", (lid_change,)
            ).fetchone()[0]
            assert float(cp) == STRATEGY_PRICE
            reason, sid = conn.execute(
                "SELECT reason, strategy_id FROM app.price_history WHERE listing_id = %s"
                " ORDER BY id DESC LIMIT 1",
                (lid_change,),
            ).fetchone()
        assert reason == "strategy" and sid == seeded["strategy"]
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
