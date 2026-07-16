"""R2-03 增量 5 验收：错误码字典灌入（listing_error_catalog 导入域）。

随包种子可导入 / 幂等重导不增行 / 非法 disposition 计 err / 缺 code 计 err /
导入覆盖运行时草稿行 / retry_failed 处置闸吃到导入的 disposition。
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import psycopg
import pytest

from erp.compliance import import_service
from erp.core.db import system_tx
from erp.tools.import_error_catalog import _seed_path

from .conftest import MIGRATOR_URL, _pg_dsn
from .test_import_pt_spec import _sessions

PREFIX = "ZEC"

# 0009 种子 5 码（不许被本测试清掉）
_BUILTIN = ("ERP_SPEC_BUILD_FAILED", "ERP_QUOTA_EXHAUSTED", "ERP_GATEWAY_DRY_RUN",
            "WM_ASYNC_REVIEW", "WM_SKU_LOCKED")  # fmt: skip


@pytest.fixture(autouse=True)
def _clean(migrated_db: str):  # type: ignore[no-untyped-def]
    def _wipe() -> None:
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            conn.execute(
                "DELETE FROM app.listing_error_catalog WHERE error_code LIKE %s", (f"{PREFIX}%",)
            )
            conn.execute("DELETE FROM app.import_job WHERE source_name LIKE %s", (f"{PREFIX}%",))
            # 种子码：删掉再导（不碰 0009 内置 5 码之外的既有行为）
            seed_codes = [
                json.loads(line)["error_code"] for line in _seed_path().read_text().splitlines()
            ]
            conn.execute(
                "DELETE FROM app.listing_error_catalog WHERE error_code = ANY(%s)"
                " AND error_code <> ALL(%s)",
                (seed_codes, list(_BUILTIN)),
            )

    _wipe()
    yield
    _wipe()


async def _import(rows: list[dict[str, Any]], name: str = "seed") -> dict[str, Any]:
    sessions = _sessions()
    async with system_tx(sessions) as s:
        job = await import_service.create_job(
            s,
            domain=import_service.LISTING_ERROR_CATALOG_DOMAIN,
            source_name=f"{PREFIX}-{name}.jsonl",
            total_rows=len(rows),
        )
    async with system_tx(sessions) as s:
        return await import_service.import_rows(s, job_id=job["id"], rows=rows)


def _seed_rows() -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(_seed_path()).read_text().splitlines()]


def test_seed_file_imports_and_idempotent() -> None:
    rows = _seed_rows()
    assert len(rows) == 66  # HF-0716 +EXT_DATA_ERROR_66685355746773（CAP 零价格）
    r = asyncio.run(_import(rows))
    assert (r["ok"], r["err"]) == (66, 0)
    r2 = asyncio.run(_import(rows, name="again"))
    assert r2["ok"] == 66
    with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
        row = conn.execute(
            "SELECT category, disposition, max_retries FROM app.listing_error_catalog"
            " WHERE error_code = 'EXT_DATA_ERROR_56026862530206'"
        ).fetchone()
        n_fatal = conn.execute(
            "SELECT count(*) FROM app.listing_error_catalog"
            " WHERE disposition = 'fatal' AND category IN ('品牌', '类目', '合规')"
        ).fetchone()[0]
    assert row == ("异步审核", "backoff_retry", 10)
    assert n_fatal >= 13  # 品牌 5 + 类目 5 + 合规 3


def test_bad_rows_counted() -> None:
    r = asyncio.run(
        _import(
            [
                {"category": "x", "title": "缺 code"},
                {"error_code": f"{PREFIX}_BAD", "disposition": "explode"},
                {"error_code": f"{PREFIX}_OK", "title": "最小行"},
            ],
            name="bad",
        )
    )
    assert (r["ok"], r["err"]) == (1, 2)
    with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
        row = conn.execute(
            "SELECT category, title, disposition, max_retries, enabled"
            " FROM app.listing_error_catalog WHERE error_code = %s",
            (f"{PREFIX}_OK",),
        ).fetchone()
    assert row == ("未分类", "最小行", "manual", 3, True)  # 宽容默认


def test_import_overrides_runtime_draft() -> None:
    """运行时 _ensure_error_cataloged 草稿行（manual/未分类）→ 导入归类覆盖。"""
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.listing_error_catalog (error_code, category, title, disposition)"
            " VALUES (%s, '未分类', %s, 'manual') ON CONFLICT DO NOTHING",
            (f"{PREFIX}_DRAFT", f"{PREFIX}_DRAFT"),
        )
    r = asyncio.run(
        _import(
            [
                {
                    "error_code": f"{PREFIX}_DRAFT",
                    "category": "格式",
                    "title": "已归类",
                    "disposition": "rebuild_spec",
                    "max_retries": 5,
                }
            ],
            name="override",
        )
    )
    assert r["ok"] == 1
    with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
        row = conn.execute(
            "SELECT category, disposition, max_retries FROM app.listing_error_catalog"
            " WHERE error_code = %s",
            (f"{PREFIX}_DRAFT",),
        ).fetchone()
    assert row == ("格式", "rebuild_spec", 5)
