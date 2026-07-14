"""R5 Nice Class 过滤验收（源仓 pt_nice_class 移植，round-4 补齐）。

walmart_category 已知 → 只命中相关 Nice 分类的 LIVE 商标（通用词商标压制）；
Nice 未知（NULL）的商标行在过滤态不命中（源仓 brand_nice_class join 同语义）；
未传类目 → 不过滤（既有行为）。
"""

import asyncio

import psycopg
import pytest

from erp.audit import nice_class, pipeline
from erp.core.db import system_tx

from .conftest import MIGRATOR_URL, _pg_dsn
from .test_l1_rerank import _sessions

PREFIX = "zrnice"  # mark_norm 全小写（_norm 契约）；纯字母（R5 候选正则只认字母词）


@pytest.fixture(scope="module", autouse=True)
def _seed(migrated_db: str):  # type: ignore[no-untyped-def]
    def _wipe(conn) -> None:  # type: ignore[no-untyped-def]
        conn.execute("DELETE FROM refdata.trademark WHERE serial_no LIKE %s", ("99R5%",))

    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _wipe(conn)
        # 三个 LIVE 商标：class 21 餐厨（Home 相关）/ class 25 服装（Home 无关）/ Nice 未知
        conn.execute(
            "INSERT INTO refdata.trademark"
            " (serial_no, mark_text, mark_norm, is_live, nice_classes) VALUES"
            " ('99R5001', 'ZRNICEKITCHEN', %s, true, '{21}'),"
            " ('99R5002', 'ZRNICEAPPAREL', %s, true, '{25}'),"
            " ('99R5003', 'ZRNICENONICE',  %s, true, NULL)",
            (f"{PREFIX}kitchen", f"{PREFIX}apparel", f"{PREFIX}nonice"),
        )
    yield
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _wipe(conn)


def test_classes_for_mapping() -> None:
    assert 21 in nice_class.classes_for("Home")  # 精确
    assert nice_class.classes_for("home") == nice_class.classes_for("Home")  # 大小写
    assert nice_class.classes_for(None) == nice_class.classes_for("")  # 空→default
    assert len(nice_class.classes_for("完全不存在的类目")) > 0  # default 兜底非空


def _r5(title: str, walmart_category: str | None) -> list[str]:
    async def _run() -> list[str]:
        async with system_tx(_sessions()) as s:
            hits = await pipeline.run_l2(
                s, {"title": title, "team_id": 0, "attrs": {}},
                walmart_category=walmart_category,
            )
        for h in hits:
            if h["rule_code"] == "l2_r5_trademark_live":
                return list(h["evidence"]["matched_marks"])
        return []

    return asyncio.run(_run())


TITLE = "Zrnicekitchen Zrniceapparel Zrnicenonice Mug Set"  # 大写开头纯字母词×3


def test_filtered_only_relevant_class_hits() -> None:
    """Home 类目（Nice 6/11/20/21）→ 只命中 class 21 的商标；25 与 NULL 被滤。"""
    marks = _r5(TITLE, "Home")
    assert f"{PREFIX}kitchen" in marks
    assert f"{PREFIX}apparel" not in marks  # class 25 与 Home 无关 → 压掉
    assert f"{PREFIX}nonice" not in marks  # Nice 未知 → 过滤态不命中（源仓同）


def test_unfiltered_all_hit() -> None:
    """未传类目（未跑 L1/未直判）→ 不过滤，三个都命中（既有行为）。"""
    marks = _r5(TITLE, None)
    assert {f"{PREFIX}kitchen", f"{PREFIX}apparel", f"{PREFIX}nonice"} <= set(marks)
