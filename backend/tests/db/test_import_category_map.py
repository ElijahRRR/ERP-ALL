"""DG1 验收：category_map 导入域（L1 类目判定弹药，refdata.category_map）。

基本导入（别名列/布尔宽容/unmapped 条目照导）/ 幂等重导不增行 /
dataset_revision('category_map') 触发器 bump / 行数不符守卫沿用通道既有测试。
"""

import asyncio

import psycopg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from erp.compliance import import_service
from erp.core.db import system_tx
from erp.core.errors import BusinessError

from .conftest import APP_URL, MIGRATOR_URL, _pg_dsn

PREFIX = "ZCMAP"  # 测试专用 amazon_category 前缀


def _sessions():  # type: ignore[no-untyped-def]
    return async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)


@pytest.fixture(autouse=True)
def _clean(migrated_db: str):  # type: ignore[no-untyped-def]
    def _wipe() -> None:
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            conn.execute(
                "DELETE FROM refdata.category_map WHERE amazon_category LIKE %s", (f"{PREFIX}%",)
            )
            conn.execute("DELETE FROM app.import_job WHERE source_name LIKE %s", (f"{PREFIX}%",))

    _wipe()
    yield
    _wipe()


async def _import(rows: list[dict], *, declared: int | None = None) -> dict:
    sessions = _sessions()
    async with system_tx(sessions) as s:
        job = await import_service.create_job(
            s,
            domain=import_service.CATEGORY_MAP_DOMAIN,
            source_name=f"{PREFIX}-map.csv",
            total_rows=declared if declared is not None else len(rows),
        )
    async with system_tx(sessions) as s:
        return await import_service.import_rows(s, job_id=job["id"], rows=rows)


def _rows() -> list[dict]:
    return [
        {
            "amazon_category": f"{PREFIX}/Home & Kitchen/Mugs",
            "walmart_product_type": "Drinkware",
            "confidence": "高",
            "requires_certificate": "FALSE",
            "zh_seller_forbidden": "FALSE",
            "requirements": None,
            "notes": "主力类目",
        },
        {
            # 别名列 + 布尔变体 + unmapped 特殊条目
            "amazon_path": f"{PREFIX}/Toys/Weird Stuff",
            "walmart_pt": "无对应Walmart PT",
            "confidence": "低",
        },
        {
            "amazon_category": f"{PREFIX}/Baby/Car Seats",
            "product_type": "Car Seats",
            "confidence": "高",
            "requires_cert": "1",  # 别名 + 数字布尔
            "seller_forbidden": "是",  # 中文布尔
        },
        {"amazon_category": f"{PREFIX}/No PT Here"},  # err：缺 walmart_product_type
    ]


def test_basic_import_aliases_and_flags() -> None:
    r = asyncio.run(_import(_rows()))
    assert r["ok"] == 3
    assert r["err"] == 1  # 缺 PT
    with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
        rows = conn.execute(
            "SELECT amazon_category, walmart_product_type, confidence,"
            " requires_certificate, zh_seller_forbidden FROM refdata.category_map"
            " WHERE amazon_category LIKE %s ORDER BY amazon_category",
            (f"{PREFIX}%",),
        ).fetchall()
    assert len(rows) == 3
    by_path = {r[0]: r for r in rows}
    car = by_path[f"{PREFIX}/Baby/Car Seats"]
    assert car[1] == "Car Seats"
    assert car[3] is True  # requires_cert=1
    assert car[4] is True  # seller_forbidden=是
    unmapped = by_path[f"{PREFIX}/Toys/Weird Stuff"]
    assert unmapped[1] == "无对应Walmart PT"  # unmapped 标记照导
    mug = by_path[f"{PREFIX}/Home & Kitchen/Mugs"]
    assert mug[2] == "高"
    assert mug[3] is False


def test_source_columns_imported() -> None:
    """0016 补源列（飞书映射明细全列无损导入）：leaf/browse_node/rank/match/batch。"""
    asyncio.run(
        _import(
            [
                {
                    "amazon_category": f"{PREFIX}/Electronics/Cables",
                    "walmart_product_type": "Cables",
                    "amazon_leaf": "Cables",
                    "browse_node_id": "172500",
                    "rank_no": "2.0",  # 浮点串宽容
                    "match_type": "leaf_exact",
                    "source_batch": "2026-05",
                }
            ]
        )
    )
    with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
        row = conn.execute(
            "SELECT amazon_leaf, browse_node_id, rank_no, match_type, source_batch"
            " FROM refdata.category_map WHERE amazon_category = %s",
            (f"{PREFIX}/Electronics/Cables",),
        ).fetchone()
    assert row == ("Cables", "172500", 2, "leaf_exact", "2026-05")


def test_idempotent_reimport_no_dup() -> None:
    asyncio.run(_import(_rows()))
    r2 = asyncio.run(_import(_rows()))
    assert r2["ok"] == 3  # ON CONFLICT 恒更新，仍计 ok
    assert r2["err"] == 1
    with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
        n = conn.execute(
            "SELECT count(*) FROM refdata.category_map WHERE amazon_category LIKE %s",
            (f"{PREFIX}%",),
        ).fetchone()[0]
    assert n == 3  # 不增行


def test_revision_bumped() -> None:
    def _rev() -> int:
        with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
            row = conn.execute(
                "SELECT revision FROM refdata.dataset_revision WHERE dataset = 'category_map'"
            ).fetchone()
            return row[0] if row else 0

    r0 = _rev()
    asyncio.run(_import([{"amazon_category": f"{PREFIX}/Rev", "walmart_product_type": "X"}]))
    assert _rev() > r0  # 0015 触发器：导入即 bump（L1 候选缓存版本失效用）


def test_row_count_mismatch_fails() -> None:
    with pytest.raises(BusinessError) as ei:
        asyncio.run(
            _import([{"amazon_category": f"{PREFIX}/A", "walmart_product_type": "B"}], declared=9)
        )
    assert ei.value.code == "IMPORT_ROW_COUNT_MISMATCH"
