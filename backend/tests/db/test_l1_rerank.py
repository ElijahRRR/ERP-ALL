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
        self.models: list[str] = []  # 每次真调用的 model 参数（验配置驱动）

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.models.append(str(json.loads(request.content).get("model")))
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
            " (walmart_product_type, walmart_category, zh_seller_forbidden,"
            "  access_state, zh_can_do)"
            " VALUES (%s,'Home',false,'普通商品','是'),(%s,'Weapons',true,'普通商品','是')",
            (f"{PREFIX}_Drinkware", f"{PREFIX}_Ammo"),
        )
        # 祖先映射：<PFX>/Home → Drinkware（供子类目祖先前缀召回）
        conn.execute(
            "INSERT INTO refdata.category_map (amazon_category, walmart_product_type)"
            " VALUES (%s,%s)",
            (f"{PREFIX}/Home", f"{PREFIX}_Drinkware"),
        )
        # 兄弟叶子映射（'>' 分隔，真数据格式）：同分支兄弟召回（round-2 召回 v2）
        conn.execute(
            "INSERT INTO refdata.category_map (amazon_category, walmart_product_type)"
            " VALUES (%s,%s)",
            (f"{PREFIX}knit > Knitting & Crochet > Storage", f"{PREFIX}_Drinkware"),
        )
        conn.execute(
            "INSERT INTO app.system_config (key, value) VALUES ('llm.pricing', %s)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (pricing,),
        )
        conn.execute("DELETE FROM app.llm_cache")  # 确定性 LLM 调用数（不吃上轮陈缓存）
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


def _recall(target: str) -> list:
    async def _r() -> list:
        async with system_tx(_sessions()) as s:
            return await l1_rerank.recall_candidates(s, target)

    return asyncio.run(_r())


def test_recall_ancestor_prefix() -> None:
    cands = _recall(f"{PREFIX}/Home/Mugs")
    assert any(c["wpt"] == f"{PREFIX}_Drinkware" for c in cands)


def test_recall_sibling_branch() -> None:
    """round-2 召回 v2：目标无祖先行，但 map 有同分支兄弟叶子（真数据 '>' 格式）→ 召回。"""
    cands = _recall(f"{PREFIX}knit > Knitting & Crochet > Knitting Needles")
    assert any(c["wpt"] == f"{PREFIX}_Drinkware" for c in cands)


def test_recall_top_level_only_too_broad() -> None:
    """仅共享顶级段（<2 段共同）→ 不召（噪声大不如无候选）。"""
    assert _recall(f"{PREFIX}knit > TotallyDifferent > Thing") == []


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


def test_rerank_model_from_config(fake_llm: _FakeLlm) -> None:
    """模型参数化：system_config 'category_map.rerank' 驱动，不写死代码（部署可切）。"""
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.system_config (key, value)"
            " VALUES ('category_map.rerank', '{\"model\": \"cfg-model-x\"}')"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value"
        )
    try:
        fake_llm.script = [{"wpt": f"{PREFIX}_Drinkware", "confidence": "高"}]
        out = asyncio.run(l1_rerank.resolve_category(_sessions(), f"{PREFIX}/Home/CfgTest"))
        assert out["resolved"] is True
        assert fake_llm.models == ["cfg-model-x"]  # 用了配置里的模型
    finally:
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            conn.execute("DELETE FROM app.system_config WHERE key = 'category_map.rerank'")


def test_rerank_fenced_json_accepted(fake_llm: _FakeLlm) -> None:
    """复排输出带 markdown 栏（v4-flash 实测）→ 剥栏解析，正常写回。"""
    fenced = f'```json\n{{"wpt": "{PREFIX}_Drinkware", "confidence": "高"}}\n```'
    assert l1_rerank.rerank_cacheable(fenced) is True
    assert l1_rerank.coerce_rerank(fenced, {f"{PREFIX}_Drinkware"}) is not None
