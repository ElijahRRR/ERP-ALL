"""R2-12 增量2 验收（TRO 链，D-Q65① 上游）：tro 导入域。

- 案件入库：tro_case 幂等 upsert（键 case_no+plaintiff），brand_terms 原文无损存 jsonb；
- 断言派生：active 案 brand_terms → 全局（team_id NULL）tro_sync block 断言
  （source_ref=case_no）→ canonical blacklist_brand active（主导源 tro_sync）；
- L2 命中复现：派生品牌词在 scan_blacklist（审核 R4 自动机）中命中；
- 撤销链：案 dismissed → 该案 tro_sync 断言撤销 + canonical 摘除；余源（manual）在册
  则不误删；案恢复 active → 断言重挂（append-only 新行）；
- 幂等：重导同案 → tro_case 恒一行、断言不重复（ok 语义同商标域：恒更新即 ok）；
- 守卫：case_no 缺失 / status 非法 → err；占位符品牌词（unbranded…）不派生断言。
"""

import psycopg
import pytest

from erp.audit import blacklist_index
from erp.compliance import assertion as assertion_svc
from erp.compliance import import_service
from erp.core.db import get_session_factory, system_tx

from .conftest import MIGRATOR_URL, _pg_dsn

PREFIX = "ztesttro"  # case_no 前缀；品牌词统一 ztro* 前缀，便于幂等清理


@pytest.fixture(autouse=True)
def _clean(migrated_db: str):  # type: ignore[no-untyped-def]
    """每个用例前后清掉本测试造的案件/断言/canonical 行（幂等重跑）。"""

    def _wipe() -> None:
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            conn.execute("DELETE FROM app.tro_case WHERE case_no LIKE %s", (f"{PREFIX}%",))
            conn.execute(
                "DELETE FROM app.blacklist_assertion WHERE source_ref LIKE %s"
                " OR subject_norm LIKE 'ztro%%'",
                (f"{PREFIX}%",),
            )
            conn.execute("DELETE FROM app.blacklist_brand WHERE brand_norm LIKE 'ztro%'")
            conn.execute("DELETE FROM app.import_job WHERE source_name LIKE %s", (f"{PREFIX}%",))
        blacklist_index.clear_cache()

    _wipe()
    yield
    _wipe()


async def _import(rows: list[dict]) -> dict:
    sf = get_session_factory()
    async with system_tx(sf) as s:
        job = await import_service.create_job(
            s,
            domain="tro",
            source_name=f"{PREFIX}-cases.jsonl",
            total_rows=len(rows),
            fmt="jsonl",
        )
    async with system_tx(sf) as s:
        return await import_service.import_rows(s, job_id=job["id"], rows=rows)


def _canonical(migrated_db: str, brand_norm: str) -> tuple[str, str] | None:
    """全局 canonical 行 → (status, source)；无 active 行时取任意留痕行。"""
    with psycopg.connect(migrated_db) as conn:
        row = conn.execute(
            "SELECT status, source FROM app.blacklist_brand"
            " WHERE team_id IS NULL AND brand_norm = %s"
            " ORDER BY (status = 'active') DESC LIMIT 1",
            (brand_norm,),
        ).fetchone()
    return (row[0], row[1]) if row else None


class TestTroImport:
    async def test_active_case_derives_global_assertions_and_l2_hit(self, migrated_db: str) -> None:
        summary = await _import(
            [
                {
                    "case_no": f"{PREFIX}-23-cv-001",
                    "plaintiff": "Acme IP LLC",
                    "court": "N.D. Ill.",
                    "filed_date": "2026-06-01",
                    "law_firm": "GBC",
                    "brand_terms": "ztroAlphaBrand; unbranded; ztroBetaBrand",
                },
                {"case_no": f"{PREFIX}-23-cv-002", "brand_terms": ["ztroGammaBrand"]},
            ]
        )
        assert summary["ok"] == 2
        assert summary["err"] == 0
        with psycopg.connect(migrated_db) as conn:
            terms, status = conn.execute(
                "SELECT brand_terms, status FROM app.tro_case WHERE case_no = %s",
                (f"{PREFIX}-23-cv-001",),
            ).fetchone()
            assert status == "active"
            assert terms == ["ztroAlphaBrand", "unbranded", "ztroBetaBrand"]  # 原文无损
            rows = conn.execute(
                "SELECT subject_norm, status, team_id FROM app.blacklist_assertion"
                " WHERE source = 'tro_sync' AND source_ref LIKE %s ORDER BY subject_norm",
                (f"{PREFIX}%",),
            ).fetchall()
        # 占位符 unbranded 不派生；全部为全局（team_id NULL）active 断言
        assert [(r[0], r[1], r[2]) for r in rows] == [
            ("ztroalphabrand", "active", None),
            ("ztrobetabrand", "active", None),
            ("ztrogammabrand", "active", None),
        ]
        assert _canonical(migrated_db, "ztroalphabrand") == ("active", "tro_sync")
        # L2 命中复现：审核 R4 自动机（全局词对任意 team 生效）扫描产品文本命中
        sf = get_session_factory()
        async with system_tx(sf) as s:
            hits = await blacklist_index.scan_blacklist(
                s, team_id=0, text="hot sale ztroalphabrand phone case"
            )
        assert {"brand": "ztroalphabrand"} in hits

    async def test_dismissed_revokes_then_reactivate(self, migrated_db: str) -> None:
        case = f"{PREFIX}-24-cv-100"
        row = {"case_no": case, "brand_terms": "ztroDeltaBrand"}
        await _import([row])
        assert _canonical(migrated_db, "ztrodeltabrand") == ("active", "tro_sync")
        # 案 dismissed → 该案断言全撤（留行），canonical 摘除
        r2 = await _import([{**row, "status": "dismissed"}])
        assert r2["ok"] == 1
        with psycopg.connect(migrated_db) as conn:
            st = conn.execute(
                "SELECT status FROM app.tro_case WHERE case_no = %s", (case,)
            ).fetchone()[0]
            live, revoked = conn.execute(
                "SELECT count(*) FILTER (WHERE status IN ('pending','active')),"
                "       count(*) FILTER (WHERE status = 'revoked')"
                " FROM app.blacklist_assertion WHERE source = 'tro_sync' AND source_ref = %s",
                (case,),
            ).fetchone()
        assert st == "dismissed"
        assert (live, revoked) == (0, 1)
        assert _canonical(migrated_db, "ztrodeltabrand") == ("removed", "tro_sync")
        # 案恢复 active（重导）→ 断言重挂为新行（append-only），canonical 回 active
        r3 = await _import([row])
        assert r3["ok"] == 1
        assert _canonical(migrated_db, "ztrodeltabrand") == ("active", "tro_sync")

    async def test_dismissed_keeps_subject_when_other_source_live(self, migrated_db: str) -> None:
        """余源在册不误删：manual 源仍支撑主体拉黑，主导源切 manual。"""
        case = f"{PREFIX}-24-cv-200"
        await _import([{"case_no": case, "brand_terms": "ztroEpsilonBrand"}])
        sf = get_session_factory()
        async with system_tx(sf) as s:
            await assertion_svc.record_assertion(
                s, team_id=None, domain="brand", subject_norm="ztroepsilonbrand",
                source="manual", reason="人工另行拉黑",
            )  # fmt: skip
        await _import([{"case_no": case, "status": "dismissed"}])
        assert _canonical(migrated_db, "ztroepsilonbrand") == ("active", "manual")

    async def test_reimport_idempotent(self, migrated_db: str) -> None:
        case = f"{PREFIX}-25-cv-007"
        rows = [{"case_no": case, "plaintiff": "P1", "brand_terms": "ztroZetaBrand"}]
        r1 = await _import(rows)
        r2 = await _import(rows)
        assert (r1["ok"], r2["ok"]) == (1, 1)  # ON CONFLICT 恒更新，重导即 ok
        with psycopg.connect(migrated_db) as conn:
            cases = conn.execute(
                "SELECT count(*) FROM app.tro_case WHERE case_no = %s", (case,)
            ).fetchone()[0]
            live = conn.execute(
                "SELECT count(*) FROM app.blacklist_assertion"
                " WHERE source = 'tro_sync' AND source_ref = %s AND status = 'active'",
                (case,),
            ).fetchone()[0]
        assert cases == 1  # uq (case_no, COALESCE(plaintiff,'')) 幂等
        assert live == 1  # 断言不重复

    async def test_guards_err_rows(self, migrated_db: str) -> None:
        assert "tro" in import_service.SUPPORTED_DOMAINS
        r = await _import(
            [
                {"plaintiff": "NoCase"},  # case_no 缺失
                {"case_no": f"{PREFIX}-26-cv-1", "status": "vacated"},  # status 非法
            ]
        )
        assert (r["ok"], r["err"]) == (0, 2)
