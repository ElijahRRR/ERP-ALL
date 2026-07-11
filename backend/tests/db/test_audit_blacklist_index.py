"""L2 R4 黑名单 Aho-Corasick 索引（blacklist_index）DB 集成验收。

覆盖：
- 占位符 / 短单词跳过与旧 run_l2 一致；
- 版本失效——不重启进程即感知新增 active 品牌；
- 命中确定性——按 brand_norm 升序、去重、同输入同序；
- run_l2 R4 evidence 形状不变（{"matches": [{"brand": ...}]}，≤10、升序）。

测试造的全局黑名单行统一以 reason='zqbltest' 标记，前后幂等清理（不动 10 行种子）。
"""

import asyncio
from typing import Any

import psycopg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from erp.audit import blacklist_index, pipeline
from erp.audit.blacklist_index import scan_blacklist
from erp.core.db import system_tx

from .conftest import APP_URL, MIGRATOR_URL, _pg_dsn

REASON = "zqbltest"  # 本测试行标记，便于幂等清理
PREFIX = "zqbl"
TEAM = 0  # 仅用全局行（team_id NULL）；team_id=0 无实体团队，RLS 下全局行仍可读


def _sessions():  # type: ignore[no-untyped-def]
    return async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)


def _insert(*brands: str, team_id: int | None = None) -> None:
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        for b in brands:
            conn.execute(
                "INSERT INTO app.blacklist_brand (team_id, brand_norm, source, reason)"
                " VALUES (%s, %s, 'manual', %s)",
                (team_id, b, REASON),
            )


def _wipe() -> None:
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        conn.execute("DELETE FROM app.blacklist_brand WHERE reason = %s", (REASON,))


@pytest.fixture(autouse=True)
def _clean(migrated_db: str):  # type: ignore[no-untyped-def]
    blacklist_index.clear_cache()
    _wipe()
    yield
    _wipe()
    blacklist_index.clear_cache()


def _scan(text: str, team_id: int = TEAM) -> list[dict[str, str]]:
    async def _run() -> list[dict[str, str]]:
        sessions = _sessions()
        async with system_tx(sessions) as s:
            return await scan_blacklist(s, team_id=team_id, text=text)

    return asyncio.run(_run())


def _run_l2(product: dict[str, Any]) -> list[dict[str, Any]]:
    async def _run() -> list[dict[str, Any]]:
        sessions = _sessions()
        async with system_tx(sessions) as s:
            return await pipeline.run_l2(s, product)

    return asyncio.run(_run())


def test_skip_placeholder_and_short_single_word() -> None:
    # oem=占位符（跳过）；zqb=短单词无空格（跳过）；zqbkept=正常保留；"z z"=短但含空格→保留
    _insert("oem", "zqb", "zqbkept", "z z")
    got = _scan("buy zqb now genuine oem part zqbkept and z z here")
    brands = {m["brand"] for m in got}
    assert "oem" not in brands  # NON_BRAND_PLACEHOLDERS 跳过
    assert "zqb" not in brands  # 长度<4 且无空格跳过
    assert "zqbkept" in brands
    assert "z z" in brands  # 含空格豁免短规则


def test_version_invalidation_without_restart() -> None:
    _insert("zqblphaseone")
    first = _scan("has zqblphaseone and zqblphasetwo here")
    assert {m["brand"] for m in first} == {"zqblphaseone"}

    # 不清缓存，直接新增一个 active 品牌 → 版本键（count+max(added_at)）变化
    _insert("zqblphasetwo")
    second = _scan("has zqblphaseone and zqblphasetwo here")
    assert {m["brand"] for m in second} == {"zqblphaseone", "zqblphasetwo"}


def test_matches_sorted_and_deduped_deterministic() -> None:
    _insert("zqblcharlie", "zqblalpha", "zqblbravo")
    text = "zqblcharlie zqblbravo zqblalpha zqblcharlie"  # 乱序 + 重复出现
    got = _scan(text)
    ours = [m["brand"] for m in got if m["brand"].startswith(PREFIX)]
    assert ours == ["zqblalpha", "zqblbravo", "zqblcharlie"]  # 升序、去重
    assert _scan(text) == got  # 同输入 → 完全一致（含 dict 顺序）


def test_run_l2_r4_evidence_shape() -> None:
    _insert("zqblfoo")
    # 文本命中一个种子品牌（nike）+ 一个自造品牌（zqblfoo）；title 全小写避免触发 R5
    product: dict[str, Any] = {
        "team_id": TEAM,
        "title": "cheap nike style sneakers",
        "brand": "generic",
        "attrs": {"bullets": ["fits zqblfoo model"], "description": "great zqblfoo gear"},
    }
    hits = _run_l2(product)
    r4 = [h for h in hits if h["rule_code"] == "l2_r4_title_desc_blacklist"]
    assert len(r4) == 1
    hit = r4[0]
    assert hit["is_hard"] is False
    # 形状与旧契约一致：evidence 只有 matches，元素只有 brand，且按 brand_norm 升序
    assert hit["evidence"] == {"matches": [{"brand": "nike"}, {"brand": "zqblfoo"}]}
    assert len(hit["evidence"]["matches"]) <= 10


def test_run_l2_r4_evidence_caps_at_ten() -> None:
    brands = [f"{PREFIX}cap{i:02d}" for i in range(12)]  # 12 个可命中品牌
    _insert(*brands)
    product: dict[str, Any] = {
        "team_id": TEAM,
        "title": "x",  # 无大写候选 → 不触发 R5
        "brand": "generic",
        "attrs": {"description": " ".join(brands)},
    }
    hits = _run_l2(product)
    r4 = next(h for h in hits if h["rule_code"] == "l2_r4_title_desc_blacklist")
    ms = r4["evidence"]["matches"]
    assert len(ms) == 10  # [:10] 上限
    assert [m["brand"] for m in ms] == sorted(brands)[:10]  # 升序前十
