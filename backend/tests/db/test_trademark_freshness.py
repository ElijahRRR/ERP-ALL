"""R2-12 增量3 验收（USPTO 链，D-Q65①）：trademark_freshness 新鲜度告警。

- 新鲜（max(filed_date)=今天）→ 不告警；
- 滞后：>critical_days → critical；介于 warn/critical → warn（通知 team_id NULL 全局）；
- 库空 → critical（R5 反查失明）；
- system_config 阈值覆盖 schedule.config 默认（放宽后同样滞后不再告警）；
- 0036 调度种子在册。
"""

import psycopg
import pytest

from erp.automation.tasks import trademark_freshness
from erp.core.db import get_session_factory

from .conftest import MIGRATOR_URL, _pg_dsn

SERIAL = "ZTESTTMFRESH1"


@pytest.fixture(autouse=True)
def _clean(migrated_db: str):  # type: ignore[no-untyped-def]
    """清掉本测试的通知/配置残留（商标行由各用例自控台面）。"""

    def _wipe() -> None:
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            conn.execute("DELETE FROM app.notification WHERE category = 'trademark_freshness'")
            conn.execute("DELETE FROM app.system_config WHERE key LIKE 'trademark.freshness%'")
            conn.execute("DELETE FROM refdata.trademark WHERE serial_no = %s", (SERIAL,))

    _wipe()
    yield
    _wipe()


def _set_stage(migrated_db: str, *, lag_days: int | None, wipe_all: bool) -> None:
    """摆台面：可选清空商标表，插入一条距今 lag_days 天的行（None=不插）。"""
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        if wipe_all:
            conn.execute("DELETE FROM refdata.trademark")
        if lag_days is not None:
            conn.execute(
                "INSERT INTO refdata.trademark (serial_no, mark_text, mark_norm, filed_date)"
                " VALUES (%s, 'ZTest Fresh Mark', 'ztest fresh mark',"
                "         current_date - %s * interval '1 day')"
                " ON CONFLICT (serial_no) DO UPDATE SET"
                "   filed_date = EXCLUDED.filed_date",
                (SERIAL, lag_days),
            )


def _alerts(migrated_db: str) -> list[tuple[str, str]]:
    with psycopg.connect(migrated_db) as conn:
        return conn.execute(
            "SELECT severity, title FROM app.notification"
            " WHERE category = 'trademark_freshness' ORDER BY id"
        ).fetchall()


def _clear_alerts(migrated_db: str) -> None:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM app.notification WHERE category = 'trademark_freshness'")


class TestTrademarkFreshness:
    async def test_fresh_no_alert(self, migrated_db: str) -> None:
        _set_stage(migrated_db, lag_days=0, wipe_all=False)  # 今天的行压住 max()
        stats = await trademark_freshness(get_session_factory(), {})
        assert stats["severity"] == "ok"
        assert stats["lag_days"] == 0
        assert _alerts(migrated_db) == []

    async def test_stale_warn_then_critical(self, migrated_db: str) -> None:
        _set_stage(migrated_db, lag_days=10, wipe_all=True)  # 7 < 10 ≤ 14 → warn
        stats = await trademark_freshness(get_session_factory(), {})
        assert stats["severity"] == "warn"
        assert [s for s, _ in _alerts(migrated_db)] == ["warn"]
        _clear_alerts(migrated_db)
        _set_stage(migrated_db, lag_days=30, wipe_all=True)  # >14 → critical
        stats = await trademark_freshness(get_session_factory(), {})
        assert stats["severity"] == "critical"
        alerts = _alerts(migrated_db)
        assert [s for s, _ in alerts] == ["critical"]
        assert "滞后 30 天" in alerts[0][1]

    async def test_empty_table_critical(self, migrated_db: str) -> None:
        _set_stage(migrated_db, lag_days=None, wipe_all=True)
        stats = await trademark_freshness(get_session_factory(), {})
        assert stats == {
            "total": 0, "newest_filed": None, "lag_days": None, "severity": "critical",
        }  # fmt: skip
        assert "为空" in _alerts(migrated_db)[0][1]

    async def test_system_config_overrides_thresholds(self, migrated_db: str) -> None:
        _set_stage(migrated_db, lag_days=30, wipe_all=True)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.system_config (key, value) VALUES"
                " ('trademark.freshness_warn_days', '60'),"
                " ('trademark.freshness_critical_days', '90')"
                " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            )
        stats = await trademark_freshness(get_session_factory(), {})
        assert stats["severity"] == "ok"  # 放宽后 30 天滞后不再告警
        assert _alerts(migrated_db) == []

    def test_schedule_seeded(self, migrated_db: str) -> None:
        with psycopg.connect(migrated_db) as conn:
            cron, cfg = conn.execute(
                "SELECT cron, config FROM app.schedule WHERE code = 'trademark_freshness'"
            ).fetchone()
        assert cron == "0 10 * * *"
        assert cfg["warn_days"] == 7
        assert cfg["critical_days"] == 14
