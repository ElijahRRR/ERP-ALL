"""R2-12 增量1 验收（RS-04D 断言账本，评审 B5① 四条硬验收 + 导入路径改造）。

- 验收①三源并存：manual+tro_sync+trademark_sync 断言在 blacklist_brand 同主体并存；
- 验收②撤销一源不误删：撤销一源仍拉黑（余源有效），全撤才摘除（removed 留行）；
- 验收③人工裁决压自动源 + 可回滚：manual allow 压制 block 源，撤销 allow 恢复；
- 验收④canonical 可全量重建且一致：弄脏 canonical 两向差异 → rebuild 修复且幂等；
- 导入路径：import_job 通道黑名单行 → 记 import 源断言 + canonical 投影 +
  dataset_revision('blacklist') 递增（L0 失效契约不变）。
- 候选闸（D-Q65②）：pending 断言不投影，人工 decide 后生效/否决。
"""

import psycopg
import pytest

from erp.compliance import assertion as assertion_svc
from erp.compliance import import_service
from erp.core.db import get_session_factory, system_tx

TEAM = "断言账本测试团队"


@pytest.fixture(scope="module")
def team_id(migrated_db: str) -> int:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        tid = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        conn.execute("DELETE FROM app.blacklist_assertion WHERE team_id = %s", (tid,))
        conn.execute("DELETE FROM app.blacklist_brand WHERE team_id = %s", (tid,))
        conn.execute(
            "DELETE FROM app.import_job WHERE team_id = %s AND domain = 'blacklist_brand'",
            (tid,),
        )
    return int(tid)


def _canonical(migrated_db: str, tid: int, brand: str) -> str | None:
    with psycopg.connect(migrated_db) as conn:
        row = conn.execute(
            "SELECT status FROM app.blacklist_brand"
            " WHERE COALESCE(team_id, 0) = %s AND brand_norm = %s"
            " ORDER BY (status = 'active') DESC LIMIT 1",
            (tid, brand),
        ).fetchone()
    return row[0] if row else None


class TestAssertionLedger:
    async def test_three_sources_coexist_then_revoke_by_one(
        self, team_id: int, migrated_db: str
    ) -> None:
        sf = get_session_factory()
        async with system_tx(sf) as s:
            a1 = await assertion_svc.record_assertion(
                s, team_id=team_id, domain="brand", subject_norm="acmebrand",
                subject_display="AcmeBrand", source="manual", reason="人工拉黑",
            )  # fmt: skip
            a2 = await assertion_svc.record_assertion(
                s, team_id=team_id, domain="brand", subject_norm="acmebrand",
                source="tro_sync", source_ref="23-cv-001",
            )  # fmt: skip
            a3 = await assertion_svc.record_assertion(
                s, team_id=team_id, domain="brand", subject_norm="acmebrand",
                source="trademark_sync", source_ref="SN123",
            )  # fmt: skip
        assert all(x["created"] for x in (a1, a2, a3))
        assert _canonical(migrated_db, team_id, "acmebrand") == "active"
        with psycopg.connect(migrated_db) as conn:
            live, src, disp = conn.execute(
                "SELECT (SELECT count(*) FROM app.blacklist_assertion WHERE team_id = %s"
                "         AND domain = 'brand' AND subject_norm = 'acmebrand'"
                "         AND status = 'active'),"
                " b.source, b.brand_display"
                " FROM app.blacklist_brand b"
                " WHERE b.team_id = %s AND b.brand_norm = 'acmebrand' AND b.status = 'active'",
                (team_id, team_id),
            ).fetchone()
        assert live == 3  # 验收①：三源并存
        assert src == "manual"  # canonical 主导源=最高优先级
        assert disp == "AcmeBrand"
        # 验收②：撤销一源仍拉黑
        sf = get_session_factory()
        async with system_tx(sf) as s:
            await assertion_svc.revoke_assertion(
                s, assertion_id=a2["assertion_id"], team_id=team_id, reason="TRO 案撤销"
            )
        assert _canonical(migrated_db, team_id, "acmebrand") == "active"
        # 全部撤销 → 摘除（removed 留行，账本行也留）
        async with system_tx(sf) as s:
            await assertion_svc.revoke_assertion(
                s, assertion_id=a1["assertion_id"], team_id=team_id
            )
            await assertion_svc.revoke_assertion(
                s, assertion_id=a3["assertion_id"], team_id=team_id
            )
        assert _canonical(migrated_db, team_id, "acmebrand") == "removed"
        with psycopg.connect(migrated_db) as conn:
            revoked = conn.execute(
                "SELECT count(*) FROM app.blacklist_assertion WHERE team_id = %s"
                " AND subject_norm = 'acmebrand' AND status = 'revoked'",
                (team_id,),
            ).fetchone()[0]
        assert revoked == 3  # append-only：撤销留行

    async def test_manual_allow_overrides_then_rollback(
        self, team_id: int, migrated_db: str
    ) -> None:
        """验收③：人工 allow 裁决压自动源；撤销 allow 即回滚。"""
        sf = get_session_factory()
        async with system_tx(sf) as s:
            await assertion_svc.record_assertion(
                s, team_id=team_id, domain="brand", subject_norm="okbrand",
                source="trademark_sync", source_ref="SN999",
            )  # fmt: skip
        assert _canonical(migrated_db, team_id, "okbrand") == "active"
        async with system_tx(sf) as s:
            allow = await assertion_svc.record_assertion(
                s, team_id=team_id, domain="brand", subject_norm="okbrand",
                source="manual", verdict="allow", reason="误伤，人工放行",
            )  # fmt: skip
        assert _canonical(migrated_db, team_id, "okbrand") == "removed"  # 压制生效
        async with system_tx(sf) as s:
            await assertion_svc.revoke_assertion(
                s, assertion_id=allow["assertion_id"], team_id=team_id
            )
        assert _canonical(migrated_db, team_id, "okbrand") == "active"  # 回滚

    async def test_pending_candidate_gate(self, team_id: int, migrated_db: str) -> None:
        """D-Q65②：候选断言（pending）不投影；人工确认后生效，否决则不生效。"""
        sf = get_session_factory()
        async with system_tx(sf) as s:
            cand = await assertion_svc.record_assertion(
                s, team_id=team_id, domain="brand", subject_norm="riskbrand",
                source="error_recycle", source_ref="listing:999", status="pending",
            )  # fmt: skip
        assert _canonical(migrated_db, team_id, "riskbrand") is None  # 未投影
        async with system_tx(sf) as s:
            await assertion_svc.decide_assertion(
                s, assertion_id=cand["assertion_id"], team_id=team_id, approve=True
            )
        assert _canonical(migrated_db, team_id, "riskbrand") == "active"
        # 第二个候选被否决 → 状态不变
        async with system_tx(sf) as s:
            c2 = await assertion_svc.record_assertion(
                s, team_id=team_id, domain="brand", subject_norm="finebrand",
                source="error_recycle", status="pending",
            )  # fmt: skip
            await assertion_svc.decide_assertion(
                s, assertion_id=c2["assertion_id"], team_id=team_id, approve=False,
                reason="误报",
            )  # fmt: skip
        assert _canonical(migrated_db, team_id, "finebrand") is None

    async def test_rebuild_canonical_two_way_and_idempotent(
        self, team_id: int, migrated_db: str
    ) -> None:
        """验收④：canonical 可由断言全量重建且一致（两向差异修复 + 幂等）。"""
        sf = get_session_factory()
        async with system_tx(sf) as s:
            await assertion_svc.record_assertion(
                s, team_id=team_id, domain="brand", subject_norm="rebuildbrand",
                source="manual",
            )  # fmt: skip
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            # 差异①：应拉黑的 canonical 被手工弄丢
            conn.execute(
                "UPDATE app.blacklist_brand SET status = 'removed', removed_at = now()"
                " WHERE team_id = %s AND brand_norm = 'rebuildbrand'",
                (team_id,),
            )
            # 差异②：无断言支撑的野 active 行
            conn.execute(
                "INSERT INTO app.blacklist_brand (team_id, brand_norm, source)"
                " VALUES (%s, 'ghostbrand', 'manual')",
                (team_id,),
            )
        async with system_tx(sf) as s:
            stats = await assertion_svc.rebuild_canonical(s, domain="brand", team_id=team_id)
        assert stats["changed"] >= 2
        assert _canonical(migrated_db, team_id, "rebuildbrand") == "active"
        assert _canonical(migrated_db, team_id, "ghostbrand") == "removed"
        async with system_tx(sf) as s:
            again = await assertion_svc.rebuild_canonical(s, domain="brand", team_id=team_id)
        assert again["changed"] == 0  # 幂等

    async def test_import_path_records_assertion_and_bumps_revision(
        self, team_id: int, migrated_db: str
    ) -> None:
        """导入改造：import_job 黑名单行 → import 源断言 + canonical + revision 递增。"""
        with psycopg.connect(migrated_db) as conn:
            before = conn.execute(
                "SELECT revision FROM refdata.dataset_revision WHERE dataset = 'blacklist'"
            ).fetchone()
        sf = get_session_factory()
        async with system_tx(sf) as s:
            job = await import_service.create_job(
                s, domain="blacklist_brand", source_name="test.csv", total_rows=1,
                fmt="csv", team_id=team_id,
            )  # fmt: skip
        async with system_tx(sf) as s:
            summary = await import_service.import_rows(
                s, job_id=job["id"], rows=[{"brand": "ImportedBrand", "reason": "导入测试"}]
            )
        assert summary["ok"] == 1
        assert _canonical(migrated_db, team_id, "importedbrand") == "active"
        with psycopg.connect(migrated_db) as conn:
            src = conn.execute(
                "SELECT source FROM app.blacklist_assertion WHERE team_id = %s"
                " AND domain = 'brand' AND subject_norm = 'importedbrand'"
                " AND status = 'active'",
                (team_id,),
            ).fetchone()[0]
            after = conn.execute(
                "SELECT revision FROM refdata.dataset_revision WHERE dataset = 'blacklist'"
            ).fetchone()
        assert src == "import"
        # L0 失效契约：投影写入触发 dataset_revision('blacklist') 递增
        assert after is not None
        assert before is None or after[0] > before[0]
        # 重导幂等：同源断言在册 → skip
        async with system_tx(sf) as s:
            job2 = await import_service.create_job(
                s, domain="blacklist_brand", source_name="test2.csv", total_rows=1,
                fmt="csv", team_id=team_id,
            )  # fmt: skip
        async with system_tx(sf) as s:
            summary2 = await import_service.import_rows(
                s, job_id=job2["id"], rows=[{"brand": "ImportedBrand"}]
            )
        assert summary2["skip"] == 1
