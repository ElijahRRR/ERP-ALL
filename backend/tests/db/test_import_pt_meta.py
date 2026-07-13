"""L1 弹药验收：pt_meta 导入域（Walmart PT 元数据主表，refdata.pt_meta）。

基本导入（别名列/布尔宽容/整数宽容）/ 缺 PT 计 err / 幂等重导不增行 /
dataset_revision('pt_meta') 触发器 bump / 行数不符守卫沿用通道既有守卫。
"""

import asyncio

import psycopg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from erp.compliance import import_service
from erp.core.db import system_tx
from erp.core.errors import BusinessError

from .conftest import APP_URL, MIGRATOR_URL, _pg_dsn

PREFIX = "ZPTM"  # 测试专用 walmart_product_type 前缀


def _sessions():  # type: ignore[no-untyped-def]
    return async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)


@pytest.fixture(autouse=True)
def _clean(migrated_db: str):  # type: ignore[no-untyped-def]
    def _wipe() -> None:
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            conn.execute(
                "DELETE FROM refdata.pt_meta WHERE walmart_product_type LIKE %s", (f"{PREFIX}%",)
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
            domain=import_service.PT_META_DOMAIN,
            source_name=f"{PREFIX}-pt.csv",
            total_rows=declared if declared is not None else len(rows),
        )
    async with system_tx(sessions) as s:
        return await import_service.import_rows(s, job_id=job["id"], rows=rows)


def _rows() -> list[dict]:
    return [
        {
            "walmart_product_type": f"{PREFIX}Drinkware",
            "walmart_category": "Home",
            "walmart_ptg": "Kitchen & Dining",
            "access_state": "open",
            "zh_can_do": "可做",
            "zh_seller_forbidden": "FALSE",
            "requirements": None,
            "notes": "主力类目",
            "total_fields": "42",
            "required_count": "8",
            "required_fields": "brandName,color",
        },
        {
            # 别名列 + 布尔中文变体 + 浮点整数串
            "walmart_pt": f"{PREFIX}Ammunition",
            "category": "Sports",
            "seller_forbidden": "是",
            "total": "15.0",  # openpyxl 数字列常见浮点串
            "required_num": "3",
        },
        {"walmart_category": "缺 PT"},  # err：缺 walmart_product_type
    ]


def test_basic_import_aliases_and_types() -> None:
    r = asyncio.run(_import(_rows()))
    assert r["ok"] == 2
    assert r["err"] == 1  # 缺 PT
    with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
        rows = conn.execute(
            "SELECT walmart_product_type, walmart_category, walmart_ptg, zh_seller_forbidden,"
            " total_fields, required_count FROM refdata.pt_meta"
            " WHERE walmart_product_type LIKE %s ORDER BY walmart_product_type",
            (f"{PREFIX}%",),
        ).fetchall()
    assert len(rows) == 2
    by_pt = {r[0]: r for r in rows}
    ammo = by_pt[f"{PREFIX}Ammunition"]
    assert ammo[1] == "Sports"  # category 别名
    assert ammo[3] is True  # seller_forbidden=是
    assert ammo[4] == 15  # total=15.0 浮点串
    assert ammo[5] == 3  # required_num 别名
    drink = by_pt[f"{PREFIX}Drinkware"]
    assert drink[2] == "Kitchen & Dining"
    assert drink[3] is False
    assert drink[4] == 42


def test_idempotent_reimport_no_dup() -> None:
    asyncio.run(_import(_rows()))
    r2 = asyncio.run(_import(_rows()))
    assert r2["ok"] == 2  # ON CONFLICT 恒更新，仍计 ok
    assert r2["err"] == 1
    with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
        n = conn.execute(
            "SELECT count(*) FROM refdata.pt_meta WHERE walmart_product_type LIKE %s",
            (f"{PREFIX}%",),
        ).fetchone()[0]
    assert n == 2  # 不增行


def test_revision_bumped() -> None:
    def _rev() -> int:
        with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
            row = conn.execute(
                "SELECT revision FROM refdata.dataset_revision WHERE dataset = 'pt_meta'"
            ).fetchone()
            return row[0] if row else 0

    r0 = _rev()
    asyncio.run(_import([{"walmart_product_type": f"{PREFIX}Rev"}]))
    assert _rev() > r0  # 0016 触发器：导入即 bump（L1 候选缓存版本失效用）


def test_row_count_mismatch_fails() -> None:
    with pytest.raises(BusinessError) as ei:
        asyncio.run(_import([{"walmart_product_type": f"{PREFIX}A"}], declared=9))
    assert ei.value.code == "IMPORT_ROW_COUNT_MISMATCH"
