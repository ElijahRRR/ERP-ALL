"""R2-07 07b 验收：封店定时提醒 beat（suspension_reminder）。

到期（已封天数 >= remind_days）按周期号 cycle 发提醒（dedupe_key 带 cycle）；
未到期零打扰；resolved 不提醒；同周期不重发——判据是 cycle 键历史存在（无时间窗，
上一条已出 notify 24h 窗仍不得重发，防每日 cron 隔天刷屏架空 remind_days）。
断言均按本团队过滤——task 全租户扫描（is_super），不依赖全局计数。
"""

import psycopg
import pytest
from psycopg.rows import dict_row

from erp.automation import tasks
from erp.core.db import get_session_factory

TEAM = "封店提醒团队"


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        conn.execute("DELETE FROM app.notification WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.store_incident WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.store WHERE team_id = %s", (team_id,))
        store_id = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, 'SR_A', '提醒店A', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
        ids = {}
        for key, days_ago, status in (
            ("due", 10, "open"),  # 10 天 → cycle 1
            ("early", 3, "observing"),  # 未到 7 天 → 零打扰
            ("resolved", 10, "resolved"),  # 已解决 → 不在提醒集合
            ("cycle2", 15, "appealing"),  # 15 天 → cycle 2
        ):
            ids[key] = conn.execute(
                "INSERT INTO app.store_incident (team_id, store_id, incident_kind, source,"
                " occurred_at, status) VALUES (%s, %s, 'suspension', 'manual',"
                " now() - make_interval(days => %s), %s) RETURNING id",
                (team_id, store_id, days_ago, status),
            ).fetchone()[0]
    return {"team": team_id, "store": store_id, "ids": ids}


def _keys(dsn: str, team_id: int) -> set[str]:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT dedupe_key FROM app.notification"
            " WHERE team_id = %s AND category = 'store_incident'"
            " AND dedupe_key LIKE 'suspension_reminder:%%'",
            (team_id,),
        ).fetchall()
    return {r["dedupe_key"] for r in rows}


class TestSuspensionReminder:
    async def test_due_cycles_dedupe_and_quiet(self, migrated_db: str, seeded: dict) -> None:
        stats = await tasks.suspension_reminder(get_session_factory(), {})
        assert stats["reminded"] >= 2  # 全租户扫描，本团队至少 due + cycle2
        ids = seeded["ids"]
        keys = _keys(migrated_db, seeded["team"])
        # 到期：周期号形状正确
        assert f"suspension_reminder:{ids['due']}:1" in keys
        assert f"suspension_reminder:{ids['cycle2']}:2" in keys
        # 未到期 + resolved 零打扰（本团队无其 dedupe_key）
        assert not any(k.startswith(f"suspension_reminder:{ids['early']}:") for k in keys)
        assert not any(k.startswith(f"suspension_reminder:{ids['resolved']}:") for k in keys)
        # 本团队恰两条提醒
        assert len(keys) == 2

    async def test_same_cycle_no_resend(self, migrated_db: str, seeded: dict) -> None:
        before = _keys(migrated_db, seeded["team"])
        assert len(before) == 2
        await tasks.suspension_reminder(get_session_factory(), {})
        # 同周期重复运行：cycle 键已存在，抑制重发，本团队仍两条
        assert _keys(migrated_db, seeded["team"]) == before

    async def test_same_cycle_no_resend_beyond_24h(self, migrated_db: str, seeded: dict) -> None:
        # 上一条提醒回拨 48h（出 notify 24h 窗）：同周期仍不得重发——每日 cron 下
        # 若仅靠 24h 窗，此处会隔天刷屏；无时间窗的历史键判重必须拦住。
        before = _keys(migrated_db, seeded["team"])
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.notification SET created_at = created_at - interval '48 hours'"
                " WHERE team_id = %s AND dedupe_key LIKE 'suspension_reminder:%%'",
                (seeded["team"],),
            )
        await tasks.suspension_reminder(get_session_factory(), {})
        with psycopg.connect(migrated_db) as conn:
            count = conn.execute(
                "SELECT count(*) FROM app.notification WHERE team_id = %s"
                " AND dedupe_key LIKE 'suspension_reminder:%%'",
                (seeded["team"],),
            ).fetchone()[0]
        assert count == len(before)  # 行数不变（不止键集合不变，同键也未加行）

    async def test_remind_days_config_gate(self, migrated_db: str, seeded: dict) -> None:
        # remind_days 抬到 30：15 天/10 天两单均未到期 → 本团队无新增提醒。
        before = _keys(migrated_db, seeded["team"])
        stats = await tasks.suspension_reminder(get_session_factory(), {"remind_days": 30})
        assert _keys(migrated_db, seeded["team"]) == before
        assert isinstance(stats["scanned"], int)
