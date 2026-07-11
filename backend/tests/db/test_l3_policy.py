"""R2-02 验收：L3 静态 37 政策 prompt 片。

政策导入（domain=policy 幂等 upsert）/ 政策块空表退回 / 灌入后块含全清单 + 类目扩展 /
版本失效重建 / coerce 接受政策 category_en（否则被误回退 IP）。
"""

import asyncio

import psycopg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from erp.audit import policy_block
from erp.audit.pipeline import coerce_l3_result
from erp.compliance import import_service
from erp.core.db import system_tx
from erp.core.errors import BusinessError

from .conftest import APP_URL, MIGRATOR_URL, _pg_dsn


def _sessions():  # type: ignore[no-untyped-def]
    return async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)


PREFIX = "ZTESTP"  # 政策测试专用 category_en 前缀


@pytest.fixture(autouse=True)
def _clean(migrated_db: str):  # type: ignore[no-untyped-def]
    """政策表仅本组测试使用 → 每例前后全清 + 清政策块内存缓存，保证隔离与确定性。"""

    def _wipe() -> None:
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            conn.execute("DELETE FROM refdata.prohibited_policy")
            conn.execute("DELETE FROM app.import_job WHERE source_name LIKE %s", (f"{PREFIX}%",))
        policy_block.clear_cache()

    _wipe()
    yield
    _wipe()


async def _import_policies(rows: list[dict], *, declared: int | None = None) -> dict:
    sessions = _sessions()
    async with system_tx(sessions) as s:
        job = await import_service.create_job(
            s,
            domain=import_service.POLICY_DOMAIN,
            source_name=f"{PREFIX}-policy.csv",
            total_rows=declared if declared is not None else len(rows),
        )
    async with system_tx(sessions) as s:
        return await import_service.import_rows(s, job_id=job["id"], rows=rows)


def _rows() -> list[dict]:
    return [
        {
            "category_en": f"{PREFIX} Intellectual Property",
            "seq": 1,
            "category_zh": "知识产权",
            "overall_status": "Prohibited",
            "prohibited_items": "Counterfeit goods • Unauthorized brand names • Replica items",
            "zh_seller_risk": "高",
        },
        {
            "category_en": f"{PREFIX} Hazardous Items",
            "id": 2,  # seq 别名
            "category_zh": "危险品",
            "prohibited_items": "Lithium batteries loose • Flammable aerosols",
            "zh_seller_risk": "中",
        },
        {"category": f"{PREFIX} No Category En Alias", "prohibited_items": "x"},  # category 别名
        {"prohibited_items": "无 category_en"},  # err：缺 category_en
    ]


def test_policy_import_idempotent_and_err() -> None:
    r1 = asyncio.run(_import_policies(_rows()))
    assert r1["ok"] == 3
    assert r1["err"] == 1  # 缺 category_en
    # 幂等：重导 category_en 相同 → ON CONFLICT 更新，仍计 ok（无 skip），表内不重复
    r2 = asyncio.run(_import_policies(_rows()))
    assert r2["ok"] == 3
    assert r2["err"] == 1
    with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
        n = conn.execute(
            "SELECT count(*) FROM refdata.prohibited_policy WHERE category_en LIKE %s",
            (f"{PREFIX}%",),
        ).fetchone()[0]
    assert n == 3  # 三条唯一 category_en，重导不增行


def test_policy_block_empty_fallback() -> None:
    async def _check() -> tuple[str, set[str]]:
        sessions = _sessions()
        async with system_tx(sessions) as s:
            return (
                await policy_block.load_policy_block(s),
                await policy_block.valid_reason_categories(s),
            )

    block, cats = asyncio.run(_check())
    assert block == ""  # 空表 → 空块（L3 退回单策略）
    assert cats == {"intellectual property", "brand_misuse", "none"}


def test_policy_block_after_import_and_version_invalidation() -> None:
    asyncio.run(_import_policies(_rows()))

    async def _load() -> tuple[str, set[str]]:
        sessions = _sessions()
        async with system_tx(sessions) as s:
            return (
                await policy_block.load_policy_block(s),
                await policy_block.valid_reason_categories(s),
            )

    block, cats = asyncio.run(_load())
    # 块含政策 header + seq 排序（Intellectual Property seq=1 在 Hazardous seq=2 前）
    assert f"## {PREFIX} Intellectual Property" in block
    assert f"## {PREFIX} Hazardous Items" in block
    assert block.index("Intellectual Property") < block.index("Hazardous Items")
    # 类目候选扩展为小写 category_en + 静态两类
    assert f"{PREFIX} hazardous items".lower() in cats
    assert "brand_misuse" in cats and "none" in cats

    # 版本失效：再导一条新政策 → 块自动重建含新条（无需重启/手动清缓存）
    asyncio.run(
        _import_policies([{"category_en": f"{PREFIX} Weapons", "prohibited_items": "guns"}])
    )
    block2, cats2 = asyncio.run(_load())
    assert f"## {PREFIX} Weapons" in block2
    assert f"{PREFIX} weapons".lower() in cats2


def test_coerce_accepts_policy_category_only_when_expanded() -> None:
    raw = '{"verdict":"reject","reason_category":"ZTESTP Hazardous Items","reason_text":"x"}'
    # 未扩展（默认静态集）→ 政策类目非法 → 回退 intellectual property
    default_out = coerce_l3_result(raw)
    assert default_out["reason_category"] == "intellectual property"
    # 扩展集含该政策类目（小写）→ 保留原判定类目
    expanded = {"intellectual property", "brand_misuse", "none", "ztestp hazardous items"}
    out = coerce_l3_result(raw, expanded)
    assert out["reason_category"] == "ztestp hazardous items"


def test_policy_row_count_mismatch_fails() -> None:
    with pytest.raises(BusinessError) as ei:
        asyncio.run(_import_policies([{"category_en": f"{PREFIX} A"}], declared=9))
    assert ei.value.code == "IMPORT_ROW_COUNT_MISMATCH"
