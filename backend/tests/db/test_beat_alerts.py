"""R2-04 增量3 验收：告警任务两件（gtin_watermark / llm_budget_check）。

- 水位：默认阈值 warn<15% / critical<5%；team_config 覆盖生效；健康池不告警。
- 预算：team_config llm_budget_daily_usd 超限 → critical（含降级建议）；
  未设预算 / 未超限 → 零打扰。
"""

import psycopg
import pytest

from erp.automation import tasks
from erp.core.db import get_session_factory

TEAM_A = "beat告警团队A"
TEAM_B = "beat告警团队B"


def _ean13(seed: int) -> str:
    body = f"71{seed:010d}"
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(body))
    return body + str((10 - total % 10) % 10)


def _seed_pool(conn, team_id: int, kind: str, total: int, free: int, base: int) -> None:
    for i in range(total):
        conn.execute(
            "INSERT INTO app.gtin_pool (team_id, gtin, gtin_kind, source, status)"
            " VALUES (%s, %s, %s, 'generator_import', %s)",
            (team_id, _ean13(base + i), kind, "free" if i < free else "used"),
        )


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        ids = {}
        for name in (TEAM_A, TEAM_B):
            conn.execute(
                "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (name,)
            )
            ids[name] = conn.execute("SELECT id FROM app.team WHERE name = %s", (name,)).fetchone()[
                0
            ]
        for tid in ids.values():
            conn.execute("DELETE FROM app.gtin_pool WHERE team_id = %s", (tid,))
            conn.execute("DELETE FROM app.team_config WHERE team_id = %s", (tid,))
            conn.execute("DELETE FROM app.llm_usage_log WHERE team_id = %s", (tid,))
            conn.execute("DELETE FROM app.notification WHERE team_id = %s", (tid,))
    return {"a": ids[TEAM_A], "b": ids[TEAM_B]}


def _notes(conn, team_id: int, category: str) -> list[tuple[str, str]]:
    return conn.execute(
        "SELECT severity, dedupe_key FROM app.notification"
        " WHERE team_id = %s AND category = %s ORDER BY id",
        (team_id, category),
    ).fetchall()


class TestGtinWatermark:
    async def test_default_thresholds_and_team_override(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        a, b = seeded["a"], seeded["b"]
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _seed_pool(conn, a, "ean_13", total=100, free=10, base=100000)  # 10% → warn
            _seed_pool(conn, a, "upc_a", total=100, free=4, base=200000)  # 4% → critical
            _seed_pool(conn, b, "ean_13", total=100, free=30, base=300000)  # 30% → 默认健康
        stats = await tasks.gtin_watermark(get_session_factory(), {})
        assert stats["warned"] >= 1 and stats["critical"] >= 1
        with psycopg.connect(migrated_db) as conn:
            got = {k: s for s, k in _notes(conn, a, "gtin_watermark")}
            assert got[f"gtin_watermark:{a}:ean_13"] == "warn"
            assert got[f"gtin_watermark:{a}:upc_a"] == "critical"
            assert _notes(conn, b, "gtin_watermark") == []

        # team_config 覆盖：B 队把 warn 阈值提到 50% → 30% 池告警
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.team_config (team_id, key, value) VALUES (%s, %s, '50')",
                (b, "gtin.warn_pct"),
            )
        await tasks.gtin_watermark(get_session_factory(), {})
        with psycopg.connect(migrated_db) as conn:
            got_b = {k: s for s, k in _notes(conn, b, "gtin_watermark")}
            assert got_b[f"gtin_watermark:{b}:ean_13"] == "warn"


class TestLlmBudgetCheck:
    async def test_over_budget_alerts_and_quiet_otherwise(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        a, b = seeded["a"], seeded["b"]
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.team_config (team_id, key, value) VALUES (%s, %s, '1.0')",
                (a, "llm_budget_daily_usd"),
            )
            for cost in ("0.60", "0.70"):  # A 队今日 $1.30 > $1.00
                conn.execute(
                    "INSERT INTO app.llm_usage_log (team_id, module, model, prompt_tokens,"
                    " completion_tokens, cost_usd) VALUES (%s, 'audit', 'test-m', 100, 50, %s)",
                    (a, cost),
                )
            # B 队无预算配置 + 有花费 → 不告警
            conn.execute(
                "INSERT INTO app.llm_usage_log (team_id, module, model, prompt_tokens,"
                " completion_tokens, cost_usd) VALUES (%s, 'audit', 'test-m', 100, 50, '9.99')",
                (b,),
            )
        stats = await tasks.llm_budget_check(get_session_factory(), {})
        assert stats["over_budget"] >= 1
        with psycopg.connect(migrated_db) as conn:
            got = _notes(conn, a, "budget")
            assert got == [("critical", f"llm_budget:{a}")]
            assert _notes(conn, b, "budget") == []

    async def test_under_budget_quiet(self, migrated_db: str, seeded: dict[str, int]) -> None:
        b = seeded["b"]
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.team_config (team_id, key, value) VALUES (%s, %s, '100.0')"
                " ON CONFLICT (team_id, key) DO UPDATE SET value = excluded.value",
                (b, "llm_budget_daily_usd"),
            )
        await tasks.llm_budget_check(get_session_factory(), {})
        with psycopg.connect(migrated_db) as conn:
            assert _notes(conn, b, "budget") == []
