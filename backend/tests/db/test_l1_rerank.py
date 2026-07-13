"""L1-b 验收：类目映射 LLM 复排（module=category_map，写回 refdata.category_map）。

祖先前缀召回 / coerce fail-closed（非JSON、候选外 WPT →不写回）/ resolve 写回
match_type=ai_rerank + usage module=category_map + 写回后 L1-a 直判即命中 /
无候选不调 LLM。LLM 全程替身（MockTransport），不出真调用。
"""

import asyncio
import json

import httpx
import psycopg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from erp.audit import l1_category, l1_rerank
from erp.audit.llm import llm_client
from erp.core.db import system_tx

from .conftest import APP_URL, MIGRATOR_URL, _pg_dsn

PREFIX = "ZRB"  # 独立前缀，避开 test_l1_category 的 ZL1% 清理


def _sessions():  # type: ignore[no-untyped-def]
    return async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)


class _FakeLlm:
    def __init__(self) -> None:
        self.calls = 0
        self.script: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        body = self.script.pop(0) if self.script else {"wpt": "", "confidence": "低"}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(body, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 300, "completion_tokens": 50},
            },
        )


@pytest.fixture()
def fake_llm() -> _FakeLlm:
    fake = _FakeLlm()
    llm_client._transport_factory = lambda: httpx.MockTransport(fake.handler)
    yield fake
    llm_client._transport_factory = None


@pytest.fixture(scope="module", autouse=True)
def _seed(migrated_db: str):  # type: ignore[no-untyped-def]
    def _wipe(conn) -> None:  # type: ignore[no-untyped-def]
        like = (f"{PREFIX}%",)
        conn.execute("DELETE FROM refdata.category_map WHERE amazon_category LIKE %s", like)
        conn.execute("DELETE FROM refdata.pt_meta WHERE walmart_product_type LIKE %s", like)

    pricing = '{"deepseek-chat": {"input_per_1m": 0.27, "output_per_1m": 1.1}}'
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _wipe(conn)
        conn.execute(
            "INSERT INTO refdata.pt_meta"
            " (walmart_product_type, walmart_category, zh_seller_forbidden)"
            " VALUES (%s,'Home',false),(%s,'Weapons',true)",
            (f"{PREFIX}_Drinkware", f"{PREFIX}_Ammo"),
        )
        # 祖先映射：<PFX>/Home → Drinkware（供子类目祖先前缀召回）
        conn.execute(
            "INSERT INTO refdata.category_map (amazon_category, walmart_product_type)"
            " VALUES (%s,%s)",
            (f"{PREFIX}/Home", f"{PREFIX}_Drinkware"),
        )
        conn.execute(
            "INSERT INTO app.system_config (key, value) VALUES ('llm.pricing', %s)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (pricing,),
        )
    yield
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _wipe(conn)


# ── 纯单元：coerce / cacheable ──


def test_coerce_valid_in_candidates() -> None:
    r = l1_rerank.coerce_rerank(json.dumps({"wpt": "A", "confidence": "高"}), {"A", "B"})
    assert r is not None
    assert r["wpt"] == "A"
    assert r["confidence"] == "高"


def test_coerce_out_of_candidates_none() -> None:
    # 选了召回候选外的 WPT（含杜撰）→ fail-closed None
    assert l1_rerank.coerce_rerank(json.dumps({"wpt": "Z"}), {"A", "B"}) is None


def test_coerce_bad_json_none() -> None:
    assert l1_rerank.coerce_rerank("不是JSON", {"A"}) is None
    assert l1_rerank.coerce_rerank(json.dumps(["A"]), {"A"}) is None


def test_rerank_cacheable() -> None:
    assert l1_rerank.rerank_cacheable(json.dumps({"wpt": "A"})) is True
    assert l1_rerank.rerank_cacheable(json.dumps({"wpt": ""})) is False
    assert l1_rerank.rerank_cacheable("boom") is False


# ── 召回 ──


def test_recall_ancestor_prefix() -> None:
    async def _r() -> list:
        async with system_tx(_sessions()) as s:
            return await l1_rerank.recall_candidates(s, f"{PREFIX}/Home/Mugs")

    cands = asyncio.run(_r())
    assert any(c["wpt"] == f"{PREFIX}_Drinkware" for c in cands)


# ── resolve：写回 + L1-a 直判 + fail-closed ──


def test_resolve_writes_back_and_l1a_direct_hits(fake_llm: _FakeLlm) -> None:
    fake_llm.script = [{"wpt": f"{PREFIX}_Drinkware", "confidence": "高", "reason": "杯子归饮具"}]
    target = f"{PREFIX}/Home/Mugs"
    out = asyncio.run(l1_rerank.resolve_category(_sessions(), target))
    assert out["resolved"] is True
    assert out["wpt"] == f"{PREFIX}_Drinkware"
    assert fake_llm.calls == 1

    with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
        row = conn.execute(
            "SELECT walmart_product_type, match_type FROM refdata.category_map"
            " WHERE amazon_category = %s",
            (target,),
        ).fetchone()
        assert row == (f"{PREFIX}_Drinkware", "ai_rerank")
        n_mod = conn.execute(
            "SELECT count(*) FROM app.llm_usage_log WHERE module = 'category_map'"
        ).fetchone()[0]
    assert n_mod >= 1  # usage 归因 category_map

    # 写回后 L1-a 直判即命中（0 LLM）
    async def _l1() -> dict:
        async with system_tx(_sessions()) as s:
            return await l1_category.run_l1(s, {"category_path": target, "amazon_leaf_id": None})

    r = asyncio.run(_l1())
    assert r["verdict"] == "pass"
    assert r["wpt"] == f"{PREFIX}_Drinkware"


def test_resolve_out_of_candidates_not_written(fake_llm: _FakeLlm) -> None:
    fake_llm.script = [{"wpt": f"{PREFIX}_Ghost", "confidence": "高"}]  # 不在召回候选
    target = f"{PREFIX}/Home/Chairs"
    out = asyncio.run(l1_rerank.resolve_category(_sessions(), target))
    assert out["resolved"] is False
    with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
        n = conn.execute(
            "SELECT count(*) FROM refdata.category_map WHERE amazon_category = %s", (target,)
        ).fetchone()[0]
    assert n == 0  # fail-closed：候选外 WPT 不写回


def test_resolve_no_candidates_no_llm(fake_llm: _FakeLlm) -> None:
    out = asyncio.run(l1_rerank.resolve_category(_sessions(), "ZZNOANCESTOR/x/y"))
    assert out["resolved"] is False
    assert out["reason"] == "no_candidates"
    assert fake_llm.calls == 0  # 无候选不调 LLM
