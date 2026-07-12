"""R2-02 验收：import_job 黑名单导入通道。

幂等 upsert（重复→skip）/ 占位符品牌跳过 / 空主体计 err / 行数不符→failed /
四域基本导入 / 归一化与 L0 查表一致（导入即被审核命中）。
"""

import asyncio

import psycopg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from erp.audit.pipeline import _blacklist_lookup, _norm
from erp.compliance import import_service
from erp.core.db import system_tx
from erp.core.errors import BusinessError

from .conftest import APP_URL, MIGRATOR_URL, _pg_dsn


def _sessions():  # type: ignore[no-untyped-def]
    return async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)


PREFIX = "ztestbl"  # 测试专用前缀，便于幂等清理


@pytest.fixture(autouse=True)
def _clean(migrated_db: str):  # type: ignore[no-untyped-def]
    """每个用例前后清掉本测试造的全局黑名单行（幂等重跑）。"""

    def _wipe() -> None:
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            for t, col in (
                ("blacklist_brand", "brand_norm"),
                ("blacklist_seller", "seller_ref"),
                ("blacklist_asin", "asin"),
                ("blacklist_category", "category_ref"),
            ):
                conn.execute(f"DELETE FROM app.{t} WHERE {col} LIKE %s", (f"{PREFIX}%",))
            conn.execute("DELETE FROM app.import_job WHERE source_name LIKE %s", (f"{PREFIX}%",))

    _wipe()
    yield
    _wipe()


async def _import(domain: str, rows: list[dict], *, declared: int | None = None) -> dict:
    sessions = _sessions()
    async with system_tx(sessions) as s:
        job = await import_service.create_job(
            s,
            domain=domain,
            source_name=f"{PREFIX}-{domain}.csv",
            total_rows=declared if declared is not None else len(rows),
        )
    async with system_tx(sessions) as s:
        return await import_service.import_rows(s, job_id=job["id"], rows=rows)


def test_brand_idempotent_and_placeholder() -> None:
    rows = [
        {"brand": f"{PREFIX}Nike", "reason": "TRO"},
        {"brand": f"{PREFIX}Disney"},
        {"brand": "unbranded"},  # 占位符 → skip
        {"brand": "  "},  # 空 → err
    ]
    r1 = asyncio.run(_import("blacklist_brand", rows))
    assert r1["ok"] == 2
    assert r1["skip"] == 1  # 占位符
    assert r1["err"] == 1  # 空主体
    # 再导一次：两个真品牌变 skip（幂等），占位符仍 skip，空仍 err
    r2 = asyncio.run(_import("blacklist_brand", rows))
    assert r2["ok"] == 0
    assert r2["skip"] == 3


def test_imported_brand_hits_l0_lookup() -> None:
    """归一化必须与 L0 查表一致：导入的品牌大小写/空格变体都应被命中。"""
    asyncio.run(_import("blacklist_brand", [{"brand": f"{PREFIX} Fancy  Brand "}]))

    async def _check() -> int | None:
        sessions = _sessions()
        async with system_tx(sessions) as s:
            # 审核侧对 "ZTESTBL FANCY BRAND"（大写多空格）归一后应查到同一行
            return await _blacklist_lookup(
                s, "blacklist_brand", "brand_norm", _norm(f"{PREFIX} FANCY   BRAND"), team_id=0
            )

    assert asyncio.run(_check()) is not None


def test_row_count_mismatch_fails_job() -> None:
    rows = [{"brand": f"{PREFIX}A"}, {"brand": f"{PREFIX}B"}]
    with pytest.raises(BusinessError) as ei:
        asyncio.run(_import("blacklist_brand", rows, declared=5))  # 声明 5 实到 2
    assert ei.value.code == "IMPORT_ROW_COUNT_MISMATCH"


def test_all_four_domains() -> None:
    assert asyncio.run(_import("blacklist_seller", [{"seller_id": f"{PREFIX}SELLER1"}]))["ok"] == 1
    assert asyncio.run(_import("blacklist_asin", [{"asin": f"{PREFIX}ASIN01"}]))["ok"] == 1
    assert asyncio.run(_import("blacklist_category", [{"category": f"{PREFIX}Weapons"}]))["ok"] == 1


def test_unsupported_domain_rejected() -> None:
    # trademark/category_map 域已实现 → 改用仍未实现的 gtin 验证「未支持域被拒」
    async def _try() -> None:
        sessions = _sessions()
        async with system_tx(sessions) as s:
            await import_service.create_job(
                s, domain="gtin", source_name=f"{PREFIX}.csv", total_rows=0
            )

    with pytest.raises(BusinessError) as ei:
        asyncio.run(_try())
    assert ei.value.code == "IMPORT_DOMAIN_UNSUPPORTED"


def test_verify_records_chunks() -> None:
    rows = [{"brand": f"{PREFIX}b{i}"} for i in range(7)]
    r = asyncio.run(_import("blacklist_brand", rows))
    assert r["ok"] == 7
    assert r["verify"][0]["expected"] == 7
    assert r["verify"][0]["loaded"] == 7
