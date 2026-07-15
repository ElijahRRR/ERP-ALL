"""R2-04 增量1 验收：beat 调度循环 + 低风险任务四件。

- tick 语义：NULL 初始化不执行 / 到期执行并推进 / 禁用不动 / 未注册 code 记失败告警
  / 坏 cron 兜底推迟 1h 并记失败 / 已推进不重复执行。
- 验收锚点「模拟断连自动回收」：死心跳节点 + 硬超时任务，beat tick（不依赖任何
  worker 拨入）→ 节点 offline、任务归还 pending。
- 任务件：api_idempotency_sweep（按龄全表清扫）/ llm_cache_lru（闲置+容量）
  / partition_maintain（动态发现分区父表并预建）。
"""

import psycopg
import pytest
from sqlalchemy import text

from erp.automation import tasks
from erp.automation.beat import tick
from erp.core.db import get_session_factory, system_tx

TEAM = "beat测试团队"


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        conn.execute("DELETE FROM app.schedule WHERE code LIKE 'beat_t_%'")
        conn.execute("DELETE FROM app.task_run WHERE task_code LIKE 'beat_t_%'")
        conn.execute("DELETE FROM app.scrape_task WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.scrape_job WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.worker_node WHERE node_key LIKE 'beatnode-%'")
    return {"team": team_id}


def _runs(conn: psycopg.Connection, code: str) -> list[tuple[str, str | None]]:
    return conn.execute(
        "SELECT status, error FROM app.task_run WHERE task_code = %s ORDER BY id", (code,)
    ).fetchall()


class TestTick:
    async def test_null_next_run_initializes_without_running(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.schedule (code, description, cron) VALUES"
                " ('beat_t_init', '初始化用例', '0 3 1 * *')"
            )
        await tick(get_session_factory())
        with psycopg.connect(migrated_db) as conn:
            nxt, last = conn.execute(
                "SELECT next_run_at, last_run_at FROM app.schedule WHERE code = 'beat_t_init'"
            ).fetchone()
            assert nxt is not None and nxt > conn.execute("SELECT now()").fetchone()[0]
            assert last is None  # 初始化不算执行
            assert _runs(conn, "beat_t_init") == []

    async def test_due_registered_executes_and_advances(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        """到期的 scrape_reclaim 种子被执行且推进；再 tick 不重复执行。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            # 改成月度 cron 防跨分钟边界 flake；置为已到期
            conn.execute(
                "UPDATE app.schedule SET cron = '0 3 1 * *',"
                " next_run_at = now() - interval '1 second'"
                " WHERE code = 'scrape_reclaim'"
            )
            before = len(_runs(conn, "scrape_reclaim"))
        sessions = get_session_factory()
        await tick(sessions)
        await tick(sessions)  # 已推进到下月——不得重复执行
        with psycopg.connect(migrated_db) as conn:
            runs = _runs(conn, "scrape_reclaim")
            assert len(runs) == before + 1
            assert runs[-1][0] == "done"
            nxt, last = conn.execute(
                "SELECT next_run_at, last_run_at FROM app.schedule WHERE code = 'scrape_reclaim'"
            ).fetchone()
            assert nxt > conn.execute("SELECT now()").fetchone()[0]
            assert last is not None

    async def test_disabled_row_untouched(self, migrated_db: str, seeded: dict[str, int]) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.schedule (code, description, cron, enabled, next_run_at) VALUES"
                " ('beat_t_disabled', '禁用用例', '0 3 1 * *', false, now() - interval '1 hour')"
            )
        await tick(get_session_factory())
        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT last_run_at FROM app.schedule WHERE code = 'beat_t_disabled'"
            ).fetchone()
            assert row[0] is None
            assert _runs(conn, "beat_t_disabled") == []

    async def test_unregistered_code_fails_loud(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        """未注册 code：task_run failed + notify critical——不静默跳过。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.schedule (code, description, cron, next_run_at) VALUES"
                " ('beat_t_unknown', '未注册用例', '0 3 1 * *', now() - interval '1 second')"
            )
        await tick(get_session_factory())
        with psycopg.connect(migrated_db) as conn:
            runs = _runs(conn, "beat_t_unknown")
            assert len(runs) == 1 and runs[0][0] == "failed"
            assert "未注册" in runs[0][1]
            assert (
                conn.execute(
                    "SELECT 1 FROM app.notification WHERE dedupe_key = 'task_fail:beat_t_unknown'"
                ).fetchone()
                is not None
            )

    async def test_bad_cron_backs_off_and_fails_loud(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.schedule (code, description, cron, next_run_at) VALUES"
                " ('beat_t_badcron', '坏cron用例', 'not a cron', now() - interval '1 second')"
            )
        await tick(get_session_factory())
        with psycopg.connect(migrated_db) as conn:
            runs = _runs(conn, "beat_t_badcron")
            assert len(runs) == 1 and runs[0][0] == "failed"
            assert "无法解析" in runs[0][1]
            nxt = conn.execute(
                "SELECT next_run_at FROM app.schedule WHERE code = 'beat_t_badcron'"
            ).fetchone()[0]
            # 兜底推迟约 1h：既不得死循环（>50min），也不得静默禁用（<70min）
            row = conn.execute(
                "SELECT now() + interval '50 minutes', now() + interval '70 minutes'"
            ).fetchone()
            assert row[0] < nxt < row[1]


class TestReclaimAcceptance:
    async def test_disconnected_worker_reclaimed_by_beat_alone(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        """验收「模拟断连自动回收」：无任何 worker 拨入，仅 beat tick 即回收。

        双路径同测：死心跳节点的任务（dispatched_at 未超时）+ 活节点的硬超时任务。
        收尾必须清掉本用例的任务/作业：pull 队列是全局的，遗留 pending 会被
        后续套件（test_scrape_api）的节点领走造成跨文件污染。
        """
        team_id = seeded["team"]
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            dead_node = conn.execute(
                "INSERT INTO app.worker_node (node_key, kind, token_hash, status,"
                " last_heartbeat_at) VALUES ('beatnode-dead', 'scraper_amazon', 'x', 'online',"
                " now() - interval '10 minutes') RETURNING id"
            ).fetchone()[0]
            live_node = conn.execute(
                "INSERT INTO app.worker_node (node_key, kind, token_hash, status,"
                " last_heartbeat_at) VALUES ('beatnode-live', 'scraper_amazon', 'x', 'online',"
                " now()) RETURNING id"
            ).fetchone()[0]
            job_id = conn.execute(
                "INSERT INTO app.scrape_job (team_id, source, job_kind, input, status,"
                " total_tasks) VALUES (%s, 'amazon', 'product_detail', '{}', 'running', 2)"
                " RETURNING id",
                (team_id,),
            ).fetchone()[0]
            t_dead = conn.execute(
                "INSERT INTO app.scrape_task (job_id, team_id, target_ref, status, attempt,"
                " worker_id, dispatched_at) VALUES (%s, %s, 'B0BEATDEAD1', 'dispatched', 1,"
                " %s, now() - interval '1 minute') RETURNING id",
                (job_id, team_id, dead_node),
            ).fetchone()[0]
            t_timeout = conn.execute(
                "INSERT INTO app.scrape_task (job_id, team_id, target_ref, status, attempt,"
                " worker_id, dispatched_at) VALUES (%s, %s, 'B0BEATSLOW1', 'dispatched', 1,"
                " %s, now() - interval '20 minutes') RETURNING id",
                (job_id, team_id, live_node),
            ).fetchone()[0]
            conn.execute(
                "UPDATE app.schedule SET cron = '0 3 1 * *',"
                " next_run_at = now() - interval '1 second'"
                " WHERE code = 'scrape_reclaim'"
            )

        try:
            await tick(get_session_factory())

            with psycopg.connect(migrated_db) as conn:
                assert (
                    conn.execute(
                        "SELECT status FROM app.worker_node WHERE id = %s", (dead_node,)
                    ).fetchone()[0]
                    == "offline"
                )
                rows = dict(
                    conn.execute(
                        "SELECT id, status FROM app.scrape_task WHERE id IN (%s, %s)",
                        (t_dead, t_timeout),
                    ).fetchall()
                )
                assert rows[t_dead] == "pending" and rows[t_timeout] == "pending"
                assert (
                    conn.execute(
                        "SELECT worker_id FROM app.scrape_task WHERE id = %s", (t_dead,)
                    ).fetchone()[0]
                    is None
                )
                runs = _runs(conn, "scrape_reclaim")
                assert runs[-1][0] == "done"
        finally:
            with psycopg.connect(migrated_db, autocommit=True) as conn:
                conn.execute("DELETE FROM app.scrape_task WHERE team_id = %s", (team_id,))
                conn.execute("DELETE FROM app.scrape_job WHERE team_id = %s", (team_id,))


class TestMaintenanceTasks:
    async def test_api_idempotency_sweep_by_age(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        team_id = seeded["team"]
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute("DELETE FROM app.api_idempotency WHERE team_id = %s", (team_id,))
            conn.execute(
                "INSERT INTO app.api_idempotency (team_id, endpoint, idem_key, payload_hash,"
                " response, created_at) VALUES"
                " (%(t)s, 'e', 'old-done', 'h', '{}', now() - interval '25 hours'),"
                " (%(t)s, 'e', 'stale-placeholder', 'h', NULL, now() - interval '30 minutes'),"
                " (%(t)s, 'e', 'fresh-done', 'h', '{}', now() - interval '1 hour'),"
                " (%(t)s, 'e', 'fresh-placeholder', 'h', NULL, now() - interval '1 minute')",
                {"t": team_id},
            )
        sessions = get_session_factory()
        async with system_tx(sessions) as s:
            stats = await tasks.api_idempotency_sweep(s, {})
        assert stats["swept"] >= 2  # 全表清扫可能连带他团队过期残留，本团队断言看集合
        with psycopg.connect(migrated_db) as conn:
            left = {
                r[0]
                for r in conn.execute(
                    "SELECT idem_key FROM app.api_idempotency WHERE team_id = %s", (team_id,)
                )
            }
            assert left == {"fresh-done", "fresh-placeholder"}

    async def test_llm_cache_lru_idle_and_cap(self, migrated_db: str) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute("DELETE FROM app.llm_cache WHERE cache_key LIKE 'beat-t-%'")
            conn.execute(
                "INSERT INTO app.llm_cache (cache_key, model, response_text, created_at,"
                " last_hit_at) VALUES"
                " ('beat-t-idle', 'm', 'x', now() - interval '200 days',"
                "  now() - interval '100 days'),"
                " ('beat-t-nohit-old', 'm', 'x', now() - interval '100 days', NULL),"
                " ('beat-t-warm', 'm', 'x', now() - interval '100 days', now()),"
                " ('beat-t-cool', 'm', 'x', now(), now() - interval '10 days')"
            )
        sessions = get_session_factory()
        async with system_tx(sessions) as s:
            stats = await tasks.llm_cache_lru(s, {"max_idle_days": 90, "max_rows": 1_000_000})
        assert stats["idle_evicted"] >= 2  # idle 与 nohit-old 必中（同库他团队行不计入断言）
        with psycopg.connect(migrated_db) as conn:
            left = {
                r[0]
                for r in conn.execute(
                    "SELECT cache_key FROM app.llm_cache WHERE cache_key LIKE 'beat-t-%'"
                )
            }
            assert left == {"beat-t-warm", "beat-t-cool"}
        # 容量上限：只留最近命中的 1 行（全表口径；容量裁剪的存活者需为全表最新才可断言）
        async with system_tx(sessions) as s:
            await s.execute(
                text("UPDATE app.llm_cache SET last_hit_at = now() WHERE cache_key = 'beat-t-warm'")
            )
            stats = await tasks.llm_cache_lru(s, {"max_idle_days": 3650, "max_rows": 1})
        with psycopg.connect(migrated_db) as conn:
            left = {
                r[0]
                for r in conn.execute(
                    "SELECT cache_key FROM app.llm_cache WHERE cache_key LIKE 'beat-t-%'"
                )
            }
            assert left == {"beat-t-warm"}

    async def test_partition_maintain_discovers_and_creates(self, migrated_db: str) -> None:
        """动态发现全部分区父表；months_ahead=4 必然新建（迁移只建到 +3）。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "DO $$ BEGIN"
                " EXECUTE 'DROP TABLE IF EXISTS app.task_run_p'"
                "   || to_char(now() + interval '4 months', 'YYYYMM');"
                " END $$"
            )
        sessions = get_session_factory()
        async with system_tx(sessions) as s:
            stats = await tasks.partition_maintain(s, {"months_ahead": 4})
        assert stats["parents"] >= 8  # audit_log/task_run/notification/scrape_*/audit_run/...
        assert stats["created"] >= 1
        with psycopg.connect(migrated_db) as conn:
            part = conn.execute(
                "SELECT to_regclass('app.task_run_p'"
                " || to_char(now() + interval '4 months', 'YYYYMM'))"
            ).fetchone()[0]
            assert part is not None
