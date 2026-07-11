"""R2 验收：import_job 商标域导入通道（refdata.trademark，喂 L2-R5 LIVE 反查）。

幂等 upsert（serial_no 键，重导更新不新增）/ mark_norm 小写归一 / is_live 派生（显式列
优先，否则查 tm_status_code 字典）/ nice_classes 解析 smallint[] / 行数不符→failed /
缺 serial|mark 计 err / R5 反查平价（导入 LIVE 商标后 R5 精确 SQL + run_l2 都能命中）。
"""

import asyncio

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from erp.audit.pipeline import run_l2
from erp.compliance import import_service
from erp.core.db import system_tx
from erp.core.errors import BusinessError

from .conftest import APP_URL, MIGRATOR_URL, _pg_dsn


def _sessions():  # type: ignore[no-untyped-def]
    return async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)


PREFIX = "ZTESTTM"  # serial_no 测试前缀，便于幂等清理
CODE_PREFIX = "ZT_"  # tm_status_code 字典测试前缀


@pytest.fixture(autouse=True)
def _clean(migrated_db: str):  # type: ignore[no-untyped-def]
    """每个用例前后清掉本测试造的 refdata 行与 job（幂等重跑）。migrator 建表→可 DELETE。"""

    def _wipe() -> None:
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            conn.execute("DELETE FROM refdata.trademark WHERE serial_no LIKE %s", (f"{PREFIX}%",))
            conn.execute(
                "DELETE FROM refdata.tm_status_code WHERE code LIKE %s", (f"{CODE_PREFIX}%",)
            )
            conn.execute("DELETE FROM app.import_job WHERE source_name LIKE %s", (f"{PREFIX}%",))

    _wipe()
    yield
    _wipe()


def _seed_status_codes() -> None:
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        conn.execute(
            "INSERT INTO refdata.tm_status_code (code, description, is_live) VALUES"
            " (%s, 'Registered', true), (%s, 'Abandoned', false)"
            " ON CONFLICT (code) DO UPDATE SET is_live = EXCLUDED.is_live",
            (f"{CODE_PREFIX}LIVE", f"{CODE_PREFIX}DEAD"),
        )


async def _import(rows: list[dict], *, declared: int | None = None) -> dict:
    sessions = _sessions()
    async with system_tx(sessions) as s:
        job = await import_service.create_job(
            s,
            domain=import_service.TRADEMARK_DOMAIN,
            source_name=f"{PREFIX}-trademark.csv",
            total_rows=declared if declared is not None else len(rows),
        )
    async with system_tx(sessions) as s:
        return await import_service.import_rows(s, job_id=job["id"], rows=rows)


def _fetchall(sql: str, params: tuple) -> list[tuple]:
    with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
        return conn.execute(sql, params).fetchall()


def test_idempotent_upsert_on_serial() -> None:
    """serial_no 幂等：重导更新同一行、不新增；每次都计 ok，表内恒一行。"""
    r1 = asyncio.run(_import([{"serial_no": f"{PREFIX}001", "mark_text": "Acme"}]))
    assert r1["ok"] == 1
    assert r1["skip"] == 0
    assert r1["err"] == 0
    # 再导：mark_text 改名 → 更新同一行，仍 ok=1（非 skip），表内仍一行
    r2 = asyncio.run(_import([{"serial_no": f"{PREFIX}001", "mark_text": "AcmeCorp"}]))
    assert r2["ok"] == 1
    rows = _fetchall(
        "SELECT mark_text, mark_norm FROM refdata.trademark WHERE serial_no = %s",
        (f"{PREFIX}001",),
    )
    assert len(rows) == 1
    assert rows[0] == ("AcmeCorp", "acmecorp")  # 更新生效 + mark_norm 小写


def test_mark_norm_lowercased() -> None:
    """mark_text='Dyson' → mark_norm='dyson'（R5 反查用小写候选，必须锁死）。"""
    asyncio.run(_import([{"serial_no": f"{PREFIX}DYS", "mark_text": "Dyson"}]))
    rows = _fetchall(
        "SELECT mark_norm FROM refdata.trademark WHERE serial_no = %s", (f"{PREFIX}DYS",)
    )
    assert rows[0][0] == "dyson"
    # 提供 mark_norm 列同样小写归一；多空格压一
    asyncio.run(
        _import([{"serial_no": f"{PREFIX}MT", "mark_text": "Foo Bar", "mark_norm": "Foo   BAR"}])
    )
    rows = _fetchall(
        "SELECT mark_norm FROM refdata.trademark WHERE serial_no = %s", (f"{PREFIX}MT",)
    )
    assert rows[0][0] == "foo bar"


def test_is_live_derivation() -> None:
    """is_live：显式列优先；否则按 status_code 查字典；字典缺该码 → NULL（未知）。"""
    _seed_status_codes()
    asyncio.run(
        _import(
            [
                # 只给 status_code → 查字典派生 LIVE
                {
                    "serial_no": f"{PREFIX}L",
                    "mark_text": "Live1",
                    "status_code": f"{CODE_PREFIX}LIVE",
                },
                # 只给 status_code → 查字典派生 DEAD
                {
                    "serial_no": f"{PREFIX}D",
                    "mark_text": "Dead1",
                    "status_code": f"{CODE_PREFIX}DEAD",
                },
                # 显式 is_live=DEAD 覆盖 status_code=LIVE（显式优先）
                {
                    "serial_no": f"{PREFIX}X",
                    "mark_text": "Over1",
                    "status_code": f"{CODE_PREFIX}LIVE",
                    "is_live": "DEAD",
                },
                # 未知 status_code 且无显式 → NULL
                {
                    "serial_no": f"{PREFIX}U",
                    "mark_text": "Unk1",
                    "status_code": f"{CODE_PREFIX}NOPE",
                },
            ]
        )
    )
    got = dict(
        _fetchall(
            "SELECT serial_no, is_live FROM refdata.trademark WHERE serial_no LIKE %s",
            (f"{PREFIX}%",),
        )
    )
    assert got[f"{PREFIX}L"] is True
    assert got[f"{PREFIX}D"] is False
    assert got[f"{PREFIX}X"] is False  # 显式 DEAD 覆盖字典 LIVE
    assert got[f"{PREFIX}U"] is None  # 字典无此码 → 未知


def test_nice_classes_parsed_to_array() -> None:
    """nice_classes 逗号/空格混合整数串 → smallint[]；空/无 → NULL。"""
    asyncio.run(
        _import(
            [
                {"serial_no": f"{PREFIX}N1", "mark_text": "N1", "nice_classes": "9, 35 42"},
                {"serial_no": f"{PREFIX}N2", "mark_text": "N2"},  # 无 → NULL
            ]
        )
    )
    got = dict(
        _fetchall(
            "SELECT serial_no, nice_classes FROM refdata.trademark WHERE serial_no LIKE %s",
            (f"{PREFIX}N%",),
        )
    )
    assert got[f"{PREFIX}N1"] == [9, 35, 42]
    assert got[f"{PREFIX}N2"] is None


def test_missing_serial_or_mark_is_err() -> None:
    """缺 serial_no 或 mark_text 的行计 err（不写库），其余正常 ok。"""
    r = asyncio.run(
        _import(
            [
                {"serial_no": f"{PREFIX}OK", "mark_text": "Good"},
                {"mark_text": "NoSerial"},  # 缺 serial → err
                {"serial_no": f"{PREFIX}NOMARK"},  # 缺 mark → err
            ]
        )
    )
    assert r["ok"] == 1
    assert r["err"] == 2
    assert r["skip"] == 0
    assert r["verify"][0]["expected"] == 3
    assert r["verify"][0]["loaded"] == 3  # 每行都被分类（1 ok + 2 err）


def test_row_count_mismatch_fails_job() -> None:
    """源截断守卫复用：声明行数≠实到 → IMPORT_ROW_COUNT_MISMATCH。"""
    rows = [{"serial_no": f"{PREFIX}A", "mark_text": "A"}]
    with pytest.raises(BusinessError) as ei:
        asyncio.run(_import(rows, declared=5))
    assert ei.value.code == "IMPORT_ROW_COUNT_MISMATCH"


def test_r5_parity_live_lookup_and_run_l2() -> None:
    """R5 反查平价：导入 LIVE 商标后，R5 精确 SQL（小写候选）+ run_l2 都能命中。

    这是导入器必须喂对 R5 的证明：mark_norm 小写 + is_live 布尔正确，缺一 R5 就查不到。
    """
    # LIVE 商标（显式 is_live=LIVE），mark_text 取一个非黑名单种子的造词避免 R4 噪声
    asyncio.run(
        _import([{"serial_no": f"{PREFIX}R5", "mark_text": "Zorptechh", "is_live": "LIVE"}])
    )
    candidate = "zorptechh"  # R5 候选是 title 大写词 .lower()

    async def _r5_sql_and_l2() -> tuple[list[str], list[dict]]:
        sessions = _sessions()
        async with system_tx(sessions) as s:
            # 1) R5 的精确反查 SQL（audit/pipeline.py 原样）
            marks = (
                (
                    await s.execute(
                        text(
                            "SELECT DISTINCT mark_norm FROM refdata.trademark"
                            " WHERE mark_norm = ANY(:c) AND is_live"
                            " ORDER BY mark_norm LIMIT 10"
                        ),
                        {"c": [candidate]},
                    )
                )
                .scalars()
                .all()
            )
            # 2) 端到端跑 run_l2：title 含该商标（大写）→ l2_r5_trademark_live 命中
            hits = await run_l2(s, {"team_id": 0, "title": "Zorptechh Wireless Charger Pad"})
            return list(marks), hits

    marks, hits = asyncio.run(_r5_sql_and_l2())
    assert candidate in marks  # R5 精确 SQL 命中导入的 LIVE 商标
    r5_hits = [h for h in hits if h["rule_code"] == "l2_r5_trademark_live"]
    assert r5_hits, f"run_l2 未命中 R5：{hits}"
    assert candidate in r5_hits[0]["evidence"]["matched_marks"]


def test_dead_mark_not_hit_by_r5() -> None:
    """反证：DEAD 商标不应被 R5 命中（is_live 布尔必须正确参与过滤）。"""
    asyncio.run(
        _import([{"serial_no": f"{PREFIX}DEAD", "mark_text": "Deadmarkk", "is_live": "DEAD"}])
    )

    async def _lookup() -> list[str]:
        sessions = _sessions()
        async with system_tx(sessions) as s:
            return list(
                (
                    await s.execute(
                        text(
                            "SELECT DISTINCT mark_norm FROM refdata.trademark"
                            " WHERE mark_norm = ANY(:c) AND is_live"
                            " ORDER BY mark_norm LIMIT 10"
                        ),
                        {"c": ["deadmarkk"]},
                    )
                ).scalars()
            )

    assert asyncio.run(_lookup()) == []  # DEAD → 被 is_live 过滤掉
