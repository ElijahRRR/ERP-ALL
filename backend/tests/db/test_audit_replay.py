"""R2-02 对拍 harness 验收：groundtruth 重跑 → 一致率 + 混淆矩阵 + 分歧清单。

verdict 归一（approved→pass、blocked→reject）；端到端：L0 拒/L1 直判过+L3 过/
旧拒新过=分歧。LLM 替身（MockTransport）不出真调用。
"""

import asyncio
import json

import httpx
import psycopg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from erp.audit.llm import llm_client
from erp.tools.audit_replay import _norm_verdict, replay

from .conftest import APP_URL, MIGRATOR_URL, _pg_dsn

PREFIX = "ZRP"
TEAM = "ZRP对拍test"
BL_NORM = "zrpnike"  # 归一化后的黑名单品牌（product brand "ZRPNike" → _norm → 此值）


def _sessions():  # type: ignore[no-untyped-def]
    return async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)


class _FakeLlm:
    def __init__(self) -> None:
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        body = {
            "verdict": "pass",
            "reason_category": "none",
            "reason_text": "",
            "signals": {},
            "blacklist_brand_verdict": [],
            "llm_confidence": "high",
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(body, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 500, "completion_tokens": 80},
            },
        )


@pytest.fixture()
def fake_llm() -> _FakeLlm:
    fake = _FakeLlm()
    llm_client._transport_factory = lambda: httpx.MockTransport(fake.handler)
    yield fake
    llm_client._transport_factory = None


@pytest.fixture(autouse=True)
def _seed(migrated_db: str):  # type: ignore[no-untyped-def]
    def _clean(conn) -> None:  # type: ignore[no-untyped-def]
        conn.execute(
            "DELETE FROM app.audit_hit WHERE team_id IN (SELECT id FROM app.team WHERE name=%s)",
            (TEAM,),
        )
        conn.execute(
            "DELETE FROM app.audit_run WHERE team_id IN (SELECT id FROM app.team WHERE name=%s)",
            (TEAM,),
        )
        conn.execute(
            "DELETE FROM app.product WHERE team_id IN (SELECT id FROM app.team WHERE name=%s)",
            (TEAM,),
        )
        conn.execute("DELETE FROM app.team WHERE name=%s", (TEAM,))
        conn.execute("DELETE FROM app.blacklist_brand WHERE brand_norm=%s", (BL_NORM,))
        like = (f"{PREFIX}%",)
        conn.execute("DELETE FROM refdata.category_map WHERE amazon_category LIKE %s", like)
        conn.execute("DELETE FROM refdata.pt_meta WHERE walmart_product_type LIKE %s", like)
        conn.execute("DELETE FROM app.llm_cache")  # 确定性 LLM 调用数（不吃陈缓存）

    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _clean(conn)
        # brand_norm 必须是归一化(小写)形态——L0 查表用 _norm(product.brand)
        conn.execute(
            "INSERT INTO app.blacklist_brand (team_id, brand_norm, brand_display, source, status)"
            " VALUES (NULL, %s, 'ZRPNike', 'import', 'active')",
            (BL_NORM,),
        )
        conn.execute(
            "INSERT INTO refdata.pt_meta"
            " (walmart_product_type, walmart_category, zh_seller_forbidden,"
            "  access_state, zh_can_do)"
            " VALUES (%s,'Home',false,'普通商品','是')",
            (f"{PREFIX}_Drinkware",),
        )
        conn.execute(
            "INSERT INTO refdata.category_map (amazon_category, walmart_product_type)"
            " VALUES (%s,%s)",
            (f"{PREFIX}/Home/Mugs", f"{PREFIX}_Drinkware"),
        )
        conn.execute(
            "INSERT INTO app.system_config (key, value) VALUES ('llm.pricing', %s)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            ('{"deepseek-chat": {"input_per_1m": 0.27, "output_per_1m": 1.1}}',),
        )
    yield
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _clean(conn)


def test_norm_verdict() -> None:
    assert _norm_verdict("approved") == "pass"
    assert _norm_verdict("BLOCKED") == "reject"
    assert _norm_verdict("manual") == "needs_review"
    assert _norm_verdict("reject") == "reject"


def _rows() -> list[dict]:
    return [
        # A: 黑名单品牌 → L0 拒；旧 reject → 一致
        {
            "asin": f"{PREFIX}A",
            "title": "ZRPNike Shoe",
            "brand": "ZRPNike",
            "old_verdict": "reject",
        },
        # B: 类目直判可售 + 占位品牌 → L1 过 + L3 过；旧 approved(→pass) → 一致
        {
            "asin": f"{PREFIX}B",
            "title": "Plain Mug",
            "brand": "N/A",
            "category_path": f"{PREFIX}/Home/Mugs",
            "old_verdict": "approved",
        },
        # C: 同 B 但旧系统 blocked(→reject)，新过 → 分歧（旧拒新过）
        {
            "asin": f"{PREFIX}C",
            "title": "Plain Cup",
            "brand": "N/A",
            "category_path": f"{PREFIX}/Home/Mugs",
            "old_verdict": "blocked",
            "old_reason": "旧系统类目拦截",
        },
        # D: 类目缺图 → L1 软标记放行 → L3 过；旧 pass → 一致（缺图不再阻审核）
        {
            "asin": f"{PREFIX}D",
            "title": "Plain Bowl",
            "brand": "N/A",
            "category_path": f"{PREFIX}/NoMap/Bowls",
            "old_verdict": "pass",
        },
        # E: 旧系统 L4(视觉)拒 → 从分母剔除（现阶段无视觉模型，D-Q58）
        {
            "asin": f"{PREFIX}E",
            "title": "Image Only Reject",
            "brand": "N/A",
            "old_verdict": "reject",
            "old_stage": "L4",
        },
    ]


def test_replay_agreement_confusion_and_disagreements(fake_llm: _FakeLlm) -> None:
    report = asyncio.run(
        replay(_sessions(), team_name=TEAM, rows=_rows(), levels=["l0", "l1", "l2", "l3"])
    )
    assert report["total"] == 4  # E(L4) 不进分母
    assert report["excluded_l4"] == 1  # E 被剔除（D-Q58：无视觉模型）
    assert report["agree"] == 3  # A、B、D 一致
    assert report["rate"] == round(3 / 4, 4)
    assert report["confusion"].get("reject->reject") == 1  # A
    assert report["confusion"].get("pass->pass") == 2  # B、D
    assert report["confusion"].get("reject->pass") == 1  # C 旧拒新过
    assert report["unmapped"] == 1  # D 类目缺图（软标记计数）
    dis = report["disagreements"]
    assert len(dis) == 1
    assert dis[0]["asin"] == f"{PREFIX}C"
    assert dis[0]["old"] == "reject"
    assert dis[0]["new"] == "pass"
    assert dis[0]["category"] == f"{PREFIX}/Home/Mugs"  # 诊断字段：类目
    assert dis[0]["old_reason"] == "旧系统类目拦截"  # 诊断字段：旧因
    assert any(h.startswith("l1:") for h in dis[0]["hits"])  # 诊断字段：命中链
    assert dis[0]["l1"]["wpt"] == f"{PREFIX}_Drinkware"  # 诊断字段：L1 map 旗标
    assert fake_llm.calls == 3  # B、C、D 到 L3（A 在 L0 短路；D 缺图不再挡 L3）
