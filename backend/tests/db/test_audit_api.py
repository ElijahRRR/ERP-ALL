"""R1-10 验收：审核最小闭环。

单 ASIN 走 L0→L2→L3 出 verdict；同输入二次运行 cache 命中 cost=0；usage_log 有行。
LLM 全程替身（MockTransport）——不出真调用。
"""

import json

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.audit.llm import llm_client
from erp.audit.pipeline import coerce_l3_result
from erp.core.security import hash_password

from .test_identity_api import PASSWORD, _login

ADMIN = "audit_admin"
TEAM = "审核测试团队"


class _FakeLlm:
    """OpenAI 兼容替身：按脚本吐 JSON；记录调用数；http_status 可模拟 provider 故障。"""

    def __init__(self) -> None:
        self.calls = 0
        self.script: list[dict] = []
        self.http_status: int | None = None  # 设为 500 等模拟 provider HTTP 错误

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.http_status is not None:
            return httpx.Response(self.http_status, text="provider boom")
        body = self.script.pop(0) if self.script else _pass_verdict()
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(body, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
            },
        )


def _pass_verdict() -> dict:
    return {
        "verdict": "pass",
        "reason_category": "none",
        "reason_text": "",
        "signals": {},
        "blacklist_brand_verdict": [],
        "llm_confidence": "high",
    }


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    """团队 + 管理员 + refdata 商标子集 + LLM 单价表。黑名单 10 行由 0008 种子提供。"""
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM app.app_user WHERE username = %s", (ADMIN,))
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        uid = conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '审核管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO app.role (team_id, name, description)
            SELECT %s, name, description FROM app.role tpl
            WHERE tpl.team_id IS NULL AND tpl.name = '团队管理员'
              AND NOT EXISTS (SELECT 1 FROM app.role r
                              WHERE r.team_id = %s AND r.name = '团队管理员')
            """,
            (team_id, team_id),
        )
        role_id = conn.execute(
            "SELECT id FROM app.role WHERE team_id = %s AND name = '团队管理员'", (team_id,)
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO app.role_permission (role_id, permission_code)
            SELECT %s, rp.permission_code FROM app.role tpl
            JOIN app.role_permission rp ON rp.role_id = tpl.id
            WHERE tpl.team_id IS NULL AND tpl.name = '团队管理员'
            ON CONFLICT DO NOTHING
            """,
            (role_id,),
        )
        conn.execute(
            "INSERT INTO app.user_role (user_id, role_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (uid, role_id),
        )
        # refdata 商标子集（R5 用）：dyson = LIVE
        conn.execute(
            "INSERT INTO refdata.trademark (serial_no, mark_text, mark_norm, is_live)"
            " VALUES ('78000001', 'DYSON', 'dyson', true)"
            " ON CONFLICT (serial_no) DO NOTHING"
        )
        # LLM 单价表（运营可调，禁止写死）
        conn.execute(
            """
            INSERT INTO app.system_config (key, value) VALUES
            ('llm.pricing', '{"deepseek-chat": {"input_per_1m": 0.27, "output_per_1m": 1.1}}')
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """
        )
        conn.execute(
            "DELETE FROM app.llm_usage_log WHERE team_id = %s OR team_id IS NULL", (team_id,)
        )
        conn.execute("DELETE FROM app.llm_cache WHERE true")
        conn.execute("DELETE FROM app.audit_hit WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.audit_run WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.product WHERE team_id = %s", (team_id,))
    return {"team": team_id, "user": uid}


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> TestClient:
    from erp.main import app

    return TestClient(app)


@pytest.fixture()
def fake_llm() -> _FakeLlm:
    fake = _FakeLlm()
    llm_client._transport_factory = lambda: httpx.MockTransport(fake.handler)
    yield fake
    llm_client._transport_factory = None


def _mk_product(migrated_db: str, team: int, **kw) -> int:
    row = {
        "source_ref": kw.get("asin", "B0AUDIT001"),
        "title": kw.get("title", "Plain Ceramic Mug 12oz"),
        "brand": kw.get("brand"),
        "attrs": json.dumps(kw.get("attrs") or {}, ensure_ascii=False),
    }
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        return conn.execute(
            "INSERT INTO app.product (team_id, source_channel, source_ref, title, brand, attrs)"
            " VALUES (%s, 'amazon', %s, %s, %s, %s::jsonb)"
            " ON CONFLICT (team_id, source_channel, source_ref) DO UPDATE SET"
            "  title = excluded.title, brand = excluded.brand, attrs = excluded.attrs,"
            "  status = 'ingested'"
            " RETURNING id",
            (team, row["source_ref"], row["title"], row["brand"], row["attrs"]),
        ).fetchone()[0]


class TestL0:
    def test_brand_blacklist_short_circuit(
        self, client: TestClient, seeded: dict, migrated_db: str, fake_llm: _FakeLlm
    ) -> None:
        """L0 品牌黑名单命中即拒，不进 L3（无 LLM 调用）。"""
        pid = _mk_product(migrated_db, seeded["team"], asin="B0AUDL0001", brand="Nike")
        auth = _login(client, ADMIN, PASSWORD)
        r = client.post(f"/api/v1/products/{pid}/audit", headers=auth, json={})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["verdict"] == "reject"
        assert body["reject_level"] == "l0"
        assert body["product_status"] == "audit_rejected"
        assert fake_llm.calls == 0
        run = client.get(f"/api/v1/audit-runs/{body['run_id']}", headers=auth).json()
        assert run["hits"][0]["rule_code"] == "l0_blacklist_brand"
        assert run["hits"][0]["is_hard"] is True

    def test_trademark_symbol(
        self, client: TestClient, seeded: dict, migrated_db: str, fake_llm: _FakeLlm
    ) -> None:
        pid = _mk_product(migrated_db, seeded["team"], asin="B0AUDL0002", title="Premium Mug® Gift")
        auth = _login(client, ADMIN, PASSWORD)
        body = client.post(f"/api/v1/products/{pid}/audit", headers=auth, json={}).json()
        assert body["verdict"] == "reject"
        assert body["reject_level"] == "l0"
        assert fake_llm.calls == 0

    def test_placeholder_brand_not_blocked(
        self, client: TestClient, seeded: dict, migrated_db: str, fake_llm: _FakeLlm
    ) -> None:
        """占位符品牌（N/A）不当品牌拦——进 L3 后 pass。"""
        pid = _mk_product(migrated_db, seeded["team"], asin="B0AUDL0003", brand="N/A")
        auth = _login(client, ADMIN, PASSWORD)
        body = client.post(f"/api/v1/products/{pid}/audit", headers=auth, json={}).json()
        assert body["verdict"] == "pass"
        assert body["product_status"] == "audit_passed"
        assert fake_llm.calls == 1


class TestL2L3Loop:
    ASIN = "B0AUDL2301"
    TITLE = "Filter Compatible for Dyson V6 Vacuum Replacement"

    def test_full_reject_with_evidence_and_cost(
        self, client: TestClient, seeded: dict, migrated_db: str, fake_llm: _FakeLlm
    ) -> None:
        """验收主线：L0 过 → L2 R4/R5 收证据 → L3 reject；usage_log 有行、cost>0。"""
        fake_llm.script = [
            {
                "verdict": "reject",
                "reason_category": "Intellectual Property",
                "reason_text": "compatible for Dyson 语法铁证，未授权引用品牌",
                "signals": {"has_trademark_symbol": False, "has_authorization_claim": False},
                "blacklist_brand_verdict": [
                    {"brand": "dyson", "is_real_brand": True, "evidence": "compatible for 语法"}
                ],
                "llm_confidence": "high",
            }
        ]
        pid = _mk_product(
            migrated_db, seeded["team"], asin=self.ASIN, title=self.TITLE, brand="Unbranded"
        )
        auth = _login(client, ADMIN, PASSWORD)
        r = client.post(f"/api/v1/products/{pid}/audit", headers=auth, json={})
        body = r.json()
        assert body["verdict"] == "reject"
        assert body["reject_level"] == "l3"
        assert body["llm_cost_usd"] > 0  # 1000×0.27/1M + 200×1.1/1M

        run = client.get(f"/api/v1/audit-runs/{body['run_id']}", headers=auth).json()
        codes = {h["rule_code"] for h in run["hits"]}
        assert "l2_r4_title_desc_blacklist" in codes  # dyson 在黑名单词表
        assert "l2_r5_trademark_live" in codes  # refdata LIVE 命中
        l3 = next(h for h in run["hits"] if h["level"] == "l3")
        assert l3["rule_code"] == "llm_intellectual_property"
        assert l3["evidence"]["blacklist_brand_verdict"][0]["is_real_brand"] is True

        with psycopg.connect(migrated_db) as conn:
            rows = conn.execute(
                "SELECT cache_hit, cost_usd FROM app.llm_usage_log WHERE team_id = %s",
                (seeded["team"],),
            ).fetchall()
        assert any(not r[0] and float(r[1]) > 0 for r in rows)  # 真调用行

    def test_second_run_cache_hit_cost_zero(
        self, client: TestClient, seeded: dict, migrated_db: str, fake_llm: _FakeLlm
    ) -> None:
        """验收：同输入二次运行 → cache 命中、cost=0、无新 LLM 调用。"""
        fake_llm.script = []  # 不应被消费
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            pid = conn.execute(
                "SELECT id FROM app.product WHERE team_id = %s AND source_ref = %s",
                (seeded["team"], self.ASIN),
            ).fetchone()[0]
        auth = _login(client, ADMIN, PASSWORD)
        body = client.post(
            f"/api/v1/products/{pid}/audit",
            headers=auth,
            json={"trigger_kind": "re_audit"},
        ).json()
        assert body["verdict"] == "reject"  # 缓存回放同一判定
        assert body["llm_cost_usd"] == 0
        assert fake_llm.calls == 0  # 未出真调用

        run = client.get(f"/api/v1/audit-runs/{body['run_id']}", headers=auth).json()
        assert float(run["cache_hit_rate"]) == 1.0
        with psycopg.connect(migrated_db) as conn:
            hit_count = conn.execute("SELECT max(hit_count) FROM app.llm_cache").fetchone()[0]
            cached_rows = conn.execute(
                "SELECT count(*) FROM app.llm_usage_log WHERE cache_hit AND cost_usd = 0"
            ).fetchone()[0]
        assert hit_count >= 1
        assert cached_rows >= 1  # 命中也记行（保真实调用画像）


class TestCoerce:
    def test_illegal_verdict_needs_review(self) -> None:
        """fail-closed（评审 round-1 A4）：非法 verdict 不再默认 pass，转人工复核。"""
        out = coerce_l3_result(json.dumps({"verdict": "maybe"}))
        assert out["verdict"] == "needs_review"
        assert out["reason_category"] == "none"

    def test_bad_json_needs_review(self) -> None:
        out = coerce_l3_result("哈哈我不是 JSON")
        assert out["verdict"] == "needs_review"
        assert out.get("parse_error") is True

    def test_real_brand_does_not_override_illegal_verdict(self) -> None:
        """顶层 verdict 非法→整份响应不可信：嵌套 is_real_brand 不升级硬拒（round-2 R2-22）。

        坏输出误升 reject 会以假信号污染反馈闭环（B3），保持 needs_review 交人工。
        """
        out = coerce_l3_result(
            json.dumps(
                {
                    "verdict": "maybe",
                    "blacklist_brand_verdict": [{"brand": "dyson", "is_real_brand": True}],
                }
            )
        )
        assert out["verdict"] == "needs_review"
        # 嵌套字段保留供人工参考，但不驱动判定
        assert out["blacklist_brand_verdict"][0]["is_real_brand"] is True

    def test_real_brand_forces_reject(self) -> None:
        """⭐ 源仓强制翻案：LLM 说 pass 但 is_real_brand=true → reject。"""
        out = coerce_l3_result(
            json.dumps(
                {
                    "verdict": "pass",
                    "reason_category": "none",
                    "blacklist_brand_verdict": [{"brand": "nike", "is_real_brand": True}],
                }
            )
        )
        assert out["verdict"] == "reject"
        assert out["reason_category"] == "intellectual property"
        assert "nike" in (out["reason_text"] or "")

    def test_legacy_category_mapped(self) -> None:
        out = coerce_l3_result(
            json.dumps({"verdict": "reject", "reason_category": "ip_infringement"})
        )
        assert out["reason_category"] == "intellectual property"


class TestFailClosed:
    def test_bad_llm_output_puts_product_in_needs_review(
        self, client: TestClient, seeded: dict, migrated_db: str, fake_llm: _FakeLlm
    ) -> None:
        """L3 verdict 非法 → run/product 双双 needs_review（评审 round-1 A4 验收）。"""
        fake_llm.script = [{"verdict": "maybe", "reason_category": "什么鬼"}]
        pid = _mk_product(
            migrated_db, seeded["team"], asin="B0AUDNR001", title="Plain Mug B0AUDNR001"
        )
        auth = _login(client, ADMIN, PASSWORD)
        body = client.post(f"/api/v1/products/{pid}/audit", headers=auth, json={}).json()
        assert body["verdict"] == "needs_review"
        assert body["product_status"] == "needs_review"
        assert body["reject_level"] is None  # review 不是否决，不占用否决层语义（R2-23）
        run = client.get(f"/api/v1/audit-runs/{body['run_id']}", headers=auth).json()
        l3 = next(h for h in run["hits"] if h["level"] == "l3")
        assert l3["rule_code"] == "llm_needs_review"
        assert l3["is_hard"] is False

    def test_bad_response_not_cached_reaudit_self_heals(
        self, client: TestClient, seeded: dict, migrated_db: str, fake_llm: _FakeLlm
    ) -> None:
        """坏响应不落缓存（round-2 R2-21）：重审走真调用而非复放坏结果，可自愈。"""
        fake_llm.script = [{"verdict": "maybe"}]
        pid = _mk_product(
            migrated_db, seeded["team"], asin="B0AUDNR002", title="Plain Mug B0AUDNR002"
        )
        auth = _login(client, ADMIN, PASSWORD)
        body1 = client.post(f"/api/v1/products/{pid}/audit", headers=auth, json={}).json()
        assert body1["verdict"] == "needs_review"
        assert fake_llm.calls == 1
        with psycopg.connect(migrated_db) as conn:
            bad_cached = conn.execute(
                "SELECT count(*) FROM app.llm_cache WHERE response_text LIKE '%maybe%'"
            ).fetchone()[0]
        assert bad_cached == 0  # 坏响应未入缓存
        # 同输入重审：LLM 恢复正常 → 真调用（calls=2 证明没吃缓存）→ pass
        fake_llm.script = [_pass_verdict()]
        body2 = client.post(
            f"/api/v1/products/{pid}/audit", headers=auth, json={"trigger_kind": "re_audit"}
        ).json()
        assert body2["verdict"] == "pass"
        assert fake_llm.calls == 2

    def test_provider_http_error_persists_needs_review(
        self, client: TestClient, seeded: dict, migrated_db: str, fake_llm: _FakeLlm
    ) -> None:
        """provider HTTP 500 → 不再整体回滚无痕：audit_run 落痕 + needs_review（R2-20）。"""
        fake_llm.http_status = 500
        pid = _mk_product(
            migrated_db, seeded["team"], asin="B0AUDNR004", title="Plain Mug B0AUDNR004"
        )
        auth = _login(client, ADMIN, PASSWORD)
        r = client.post(f"/api/v1/products/{pid}/audit", headers=auth, json={})
        assert r.status_code == 201, r.text  # 异常被兜住，不是 4xx/5xx
        body = r.json()
        assert body["verdict"] == "needs_review"
        assert body["product_status"] == "needs_review"
        assert body["reject_level"] is None
        run = client.get(f"/api/v1/audit-runs/{body['run_id']}", headers=auth).json()
        l3 = next(h for h in run["hits"] if h["level"] == "l3")
        assert l3["rule_code"] == "llm_unavailable"
        assert l3["evidence"]["error"] == "LLM_CALL_FAILED"

    def test_policy_missing_needs_review(
        self, client: TestClient, seeded: dict, migrated_db: str, fake_llm: _FakeLlm
    ) -> None:
        """请求了 L3 但策略被禁 → needs_review 而非静默 pass（R2-20 第二漏口）。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.audit_policy SET enabled = false"
                " WHERE code = 'l3_intellectual_property'"
            )
        try:
            pid = _mk_product(
                migrated_db, seeded["team"], asin="B0AUDNR005", title="Plain Mug B0AUDNR005"
            )
            auth = _login(client, ADMIN, PASSWORD)
            body = client.post(f"/api/v1/products/{pid}/audit", headers=auth, json={}).json()
            assert body["verdict"] == "needs_review"
            assert fake_llm.calls == 0
            run = client.get(f"/api/v1/audit-runs/{body['run_id']}", headers=auth).json()
            l3 = next(h for h in run["hits"] if h["level"] == "l3")
            assert l3["rule_code"] == "l3_policy_missing"
        finally:
            with psycopg.connect(migrated_db, autocommit=True) as conn:
                conn.execute(
                    "UPDATE app.audit_policy SET enabled = true"
                    " WHERE code = 'l3_intellectual_property'"
                )

    def test_legacy_bad_cache_row_evicted(self, migrated_db: str, fake_llm: _FakeLlm) -> None:
        """修复前已入库的坏缓存行：命中即驱逐 + 走真调用（存量自愈，R2-21）。"""
        import asyncio

        from sqlalchemy import text as sa_text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from erp.audit import llm as llm_module
        from erp.audit.pipeline import l3_response_cacheable
        from erp.core.db import system_tx

        from .conftest import APP_URL

        msgs = [{"role": "user", "content": "legacy-bad-cache-probe"}]
        key = llm_module.cache_key("deepseek-chat", msgs, 0.0, 100)

        async def _run() -> tuple[str, bool]:
            sessions = async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)
            async with system_tx(sessions) as s:
                await s.execute(
                    sa_text(
                        "INSERT INTO app.llm_cache (cache_key, model, response_text,"
                        " response_meta) VALUES (:k, 'deepseek-chat', '垃圾不是JSON', '{}')"
                        " ON CONFLICT (cache_key) DO NOTHING"
                    ),
                    {"k": key},
                )
            async with system_tx(sessions) as s:
                content, _cost, hit = await llm_client.chat(
                    s,
                    model="deepseek-chat",
                    messages=msgs,
                    max_tokens=100,
                    cacheable=l3_response_cacheable,
                )
                return content, hit

        content, hit = asyncio.run(_run())
        assert hit is False  # 坏行被驱逐，未按命中返回
        assert fake_llm.calls == 1  # 走了真调用
        assert "verdict" in content  # 拿到的是替身的正常响应而非坏行


class TestApiSurface:
    def test_list_runs(self, client: TestClient, seeded: dict) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        page = client.get("/api/v1/audit-runs", headers=auth).json()
        assert page["total"] >= 4
        assert all(r["status"] == "done" for r in page["items"])

    def test_l4_rejected(self, client: TestClient, seeded: dict, migrated_db: str) -> None:
        pid = _mk_product(migrated_db, seeded["team"], asin="B0AUDL4001")
        auth = _login(client, ADMIN, PASSWORD)
        r = client.post(
            f"/api/v1/products/{pid}/audit",
            headers=auth,
            json={"levels": ["l0", "l4"]},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "AUDIT_L4_DISABLED"


class TestLlmOutOfLock:
    """RS-03a 验收：LLM HTTP 期间不持 product 行锁；遗孤 running run 懒清扫。"""

    def test_row_lock_released_during_provider_call(
        self, client: TestClient, seeded: dict, migrated_db: str, fake_llm: _FakeLlm
    ) -> None:
        """在替身 provider 处理请求的当口，从旁路连接 FOR UPDATE NOWAIT 锁同一行——
        必须立刻拿到（锁已随 tx1 提交释放），否则说明 HTTP 仍在事务内（评审 A7 核心）。"""
        pid = _mk_product(migrated_db, seeded["team"], asin="B0AUDLOCK1", title="Mug LockProbe")
        probe: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            with psycopg.connect(migrated_db) as conn:
                try:
                    conn.execute(
                        "SELECT id FROM app.product WHERE id = %s FOR UPDATE NOWAIT", (pid,)
                    )
                    probe["locked_during_http"] = False
                except psycopg.errors.LockNotAvailable:
                    probe["locked_during_http"] = True
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": json.dumps(_pass_verdict(), ensure_ascii=False)}}
                    ],
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
                },
            )

        llm_client._transport_factory = lambda: httpx.MockTransport(handler)
        auth = _login(client, ADMIN, PASSWORD)
        body = client.post(f"/api/v1/products/{pid}/audit", headers=auth, json={}).json()
        assert probe == {"locked_during_http": False}  # HTTP 期间行锁已释放（实证）
        assert body["verdict"] == "pass"
        assert body["product_status"] == "audit_passed"

    def test_stale_running_run_swept(
        self, client: TestClient, seeded: dict, migrated_db: str, fake_llm: _FakeLlm
    ) -> None:
        """模拟 tx1 后崩溃的遗孤（running 超 10min）：下次审核同品自动清为 failed。"""
        pid = _mk_product(migrated_db, seeded["team"], asin="B0AUDSTALE1", title="Mug Stale")
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            old_id = conn.execute(
                "INSERT INTO app.audit_run"
                " (team_id, product_id, trigger_kind, levels_requested, status, started_at)"
                " VALUES (%s, %s, 'manual', '{l0,l2,l3}', 'running', now() - interval '20 min')"
                " RETURNING id",
                (seeded["team"], pid),
            ).fetchone()[0]
        auth = _login(client, ADMIN, PASSWORD)
        body = client.post(f"/api/v1/products/{pid}/audit", headers=auth, json={}).json()
        assert body["verdict"] == "pass"
        with psycopg.connect(migrated_db) as conn:
            st = conn.execute(
                "SELECT status FROM app.audit_run WHERE id = %s", (old_id,)
            ).fetchone()[0]
        assert st == "failed"  # 遗孤被懒清扫，商品可正常重审
