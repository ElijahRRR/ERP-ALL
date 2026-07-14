"""R3b 弹药验收：pt_spec 导入域（官方 spec 认证层，refdata.pt_spec）。

jsonl 原生 jsonb 列保真 / 布尔宽容 / 缺 PT 计 err / 幂等重导不增行。
"""

import asyncio

import psycopg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from erp.compliance import import_service
from erp.core.db import system_tx

from .conftest import APP_URL, MIGRATOR_URL, _pg_dsn

PREFIX = "ZPS"


def _sessions():  # type: ignore[no-untyped-def]
    return async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)


@pytest.fixture(autouse=True)
def _clean(migrated_db: str):  # type: ignore[no-untyped-def]
    def _wipe() -> None:
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            conn.execute(
                "DELETE FROM refdata.pt_spec WHERE walmart_product_type LIKE %s", (f"{PREFIX}%",)
            )
            conn.execute("DELETE FROM app.import_job WHERE source_name LIKE %s", (f"{PREFIX}%",))

    _wipe()
    yield
    _wipe()


async def _import(rows: list[dict]) -> dict:
    sessions = _sessions()
    async with system_tx(sessions) as s:
        job = await import_service.create_job(
            s,
            domain=import_service.PT_SPEC_DOMAIN,
            source_name=f"{PREFIX}-spec.jsonl",
            total_rows=len(rows),
        )
    async with system_tx(sessions) as s:
        return await import_service.import_rows(s, job_id=job["id"], rows=rows)


def _rows() -> list[dict]:
    return [
        {
            "walmart_product_type": f"{PREFIX}_Cameras",
            "has_real_cert": True,
            "real_cert_fields": ["nrtl_certification"],
            "has_soft_cert": False,
            "total_fields": 68,
            "required_count": 15,
            "required_fields": ["brand", "price"],
        },
        {"walmart_product_type": f"{PREFIX}_Glasses", "has_real_cert": "false", "total_fields": 50},
        {"has_real_cert": True},  # err：缺 PT
    ]


def test_basic_import_and_types() -> None:
    r = asyncio.run(_import(_rows()))
    assert r["ok"] == 2
    assert r["err"] == 1
    with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
        row = conn.execute(
            "SELECT has_real_cert, real_cert_fields, total_fields, required_count"
            " FROM refdata.pt_spec WHERE walmart_product_type = %s",
            (f"{PREFIX}_Cameras",),
        ).fetchone()
    assert row[0] is True
    assert row[1] == ["nrtl_certification"]  # jsonb 原生保真
    assert (row[2], row[3]) == (68, 15)


def test_idempotent_reimport_no_dup() -> None:
    asyncio.run(_import(_rows()))
    r2 = asyncio.run(_import(_rows()))
    assert r2["ok"] == 2
    with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
        n = conn.execute(
            "SELECT count(*) FROM refdata.pt_spec WHERE walmart_product_type LIKE %s",
            (f"{PREFIX}%",),
        ).fetchone()[0]
    assert n == 2
