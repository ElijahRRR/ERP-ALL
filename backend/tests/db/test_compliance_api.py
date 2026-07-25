"""R2-12 增量5（合规中心页）后端验收：断言账本管理 + 商标/TRO 查询 + 导入报错报告。

- 黑名单：人工登记 block → canonical active + 断言追溯可见；allow 裁决压 block → canonical
  removed；候选断言裁决（approve→active / reject→revoked）；撤销一源余源仍拉黑（B5① 语义
  经 HTTP 面复现）。
- 商标查询：mark_norm trigram 模糊 + Nice 类过滤 + LIVE 过滤。
- TRO 案件：品牌词/状态过滤。
- 导入报错报告：verify.sample_errors 逐行透出（008§6 对账可见性）。
- 权限：无 compliance 权限点 → 403。
"""

import json
import os
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.core.security import hash_password

from .conftest import APP_URL
from .test_identity_api import PASSWORD, _login

SUPER = "compl_super"
PLAIN = "compl_plain"
PLAIN_TEAM = "合规无权团队"


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM app.app_user WHERE username IN (%s, %s)", (SUPER, PLAIN))
        conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name, is_super)"
            " VALUES (NULL, %s, %s, '合规超管', true)",
            (SUPER, hash_password(PASSWORD)),
        )
        # 无 compliance 权限的普通用户（自建团队 + 空角色），用于 403 门控
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (PLAIN_TEAM,)
        )
        pteam = conn.execute("SELECT id FROM app.team WHERE name = %s", (PLAIN_TEAM,)).fetchone()[0]
        conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '无权用户')",
            (pteam, PLAIN, hash_password(PASSWORD)),
        )

        # 清场：本测试用的黑名单主体（zc 前缀）——断言账本 + 六张 canonical
        conn.execute("DELETE FROM app.blacklist_assertion WHERE subject_norm LIKE 'zc%'")
        for t, col in (("blacklist_brand", "brand_norm"), ("blacklist_seller", "seller_ref")):
            conn.execute(f"DELETE FROM app.{t} WHERE {col} LIKE 'zc%'")

        # 商标子集：dyson=LIVE(nice 7,9)，garden=LIVE(nice 35)，deadmark=非 LIVE
        conn.execute("DELETE FROM refdata.trademark WHERE serial_no LIKE 'ZC%'")
        conn.execute(
            "INSERT INTO refdata.trademark"
            " (serial_no, mark_text, mark_norm, status_code, is_live, nice_classes) VALUES"
            " ('ZC000001', 'DYSON', 'dyson', 'REGISTERED', true, '{7,9}'),"
            " ('ZC000002', 'GARDEN', 'garden', 'REGISTERED', true, '{35}'),"
            " ('ZC000003', 'DEADMARK', 'deadmark', 'DEAD', false, '{9}')"
        )

        # TRO 案件子集
        conn.execute("DELETE FROM app.tro_case WHERE case_no LIKE 'ZC-%'")
        conn.execute(
            "INSERT INTO app.tro_case (case_no, court, plaintiff, brand_terms, status) VALUES"
            " ('ZC-2026-1', 'N.D. Ill.', 'ACME Corp', '[\"acmebrand\"]', 'active'),"
            " ('ZC-2026-2', 'N.D. Ill.', 'Beta LLC', '[\"betabrand\"]', 'dismissed')"
        )

        # 导入作业（带报错样本）
        conn.execute("DELETE FROM app.import_job WHERE source_name = 'zc-report.csv'")
        verify = json.dumps(
            {
                "chunks": [{"chunk": 0, "expected": 3, "loaded": 3}],
                "sample_errors": [
                    {"line": 1, "reason": "主体字段为空", "row": {}},
                    {"line": 3, "reason": "serial_no 缺失", "row": {"x": 1}},
                ],
            }
        )
        job_id = conn.execute(
            "INSERT INTO app.import_job"
            " (team_id, domain, source_name, format, status, total_rows, ok_rows, err_rows,"
            "  verify) VALUES (NULL, 'trademark', 'zc-report.csv', 'csv', 'done', 3, 1, 2,"
            "  %s::jsonb) RETURNING id",
            (verify,),
        ).fetchone()[0]
    return {"job": int(job_id), "pteam": int(pteam)}


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> Iterator[TestClient]:
    os.environ["ERP_DATABASE_URL"] = APP_URL
    from erp.core.db import get_session_factory
    from erp.core.settings import get_settings

    get_settings.cache_clear()
    get_session_factory.cache_clear()
    from erp.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()
    get_session_factory.cache_clear()


def _auth(client: TestClient) -> dict[str, str]:
    return _login(client, SUPER, PASSWORD)


class TestBlacklist:
    def test_manual_record_trace_and_canonical(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """人工登记 block → canonical active；列表可见；断言追溯见 manual 源。"""
        auth = _auth(client)
        r = client.post(
            "/api/v1/blacklist/assertions",
            headers=auth,
            json={
                "domain": "brand",
                "subject_norm": "zcnike",
                "subject_display": "ZcNike",
                "verdict": "block",
                "reason": "仿冒",
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["canonical"] == "active"

        page = client.get("/api/v1/blacklist?domain=brand&q=zcnike", headers=auth).json()
        assert page["total"] == 1
        assert page["items"][0]["subject"] == "zcnike"
        assert page["items"][0]["source"] == "manual"

        trace = client.get(
            "/api/v1/blacklist/trace?domain=brand&subject=zcnike", headers=auth
        ).json()
        assert len(trace) == 1
        assert trace[0]["source"] == "manual"
        assert trace[0]["verdict"] == "block"
        assert trace[0]["status"] == "active"

    def test_allow_suppresses_block(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """manual allow 白名单裁决压 block → canonical removed（撤 allow 可回滚，此处只验压制）。"""
        auth = _auth(client)
        client.post(
            "/api/v1/blacklist/assertions",
            headers=auth,
            json={"domain": "brand", "subject_norm": "zcallow", "verdict": "block"},
        )
        r = client.post(
            "/api/v1/blacklist/assertions",
            headers=auth,
            json={
                "domain": "brand",
                "subject_norm": "zcallow",
                "verdict": "allow",
                "reason": "自有品牌",
            },
        )
        assert r.status_code == 201
        assert r.json()["canonical"] == "removed"  # allow 压 block
        page = client.get("/api/v1/blacklist?domain=brand&q=zcallow", headers=auth).json()
        assert page["total"] == 0  # canonical 已摘除
        trace = client.get(
            "/api/v1/blacklist/trace?domain=brand&subject=zcallow", headers=auth
        ).json()
        assert {t["verdict"] for t in trace} == {"block", "allow"}  # 两断言均留册

    def test_pending_decide(self, client: TestClient, seeded: dict, migrated_db: str) -> None:
        """候选断言（error_recycle pending）经 HTTP 裁决：approve→active / reject→revoked。"""
        auth = _auth(client)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            aid_ok = conn.execute(
                "INSERT INTO app.blacklist_assertion"
                " (team_id, domain, subject_norm, verdict, source, status)"
                " VALUES (NULL, 'brand', 'zcpend1', 'block', 'error_recycle', 'pending')"
                " RETURNING id"
            ).fetchone()[0]
            aid_no = conn.execute(
                "INSERT INTO app.blacklist_assertion"
                " (team_id, domain, subject_norm, verdict, source, status)"
                " VALUES (NULL, 'brand', 'zcpend2', 'block', 'error_recycle', 'pending')"
                " RETURNING id"
            ).fetchone()[0]

        page = client.get("/api/v1/blacklist/pending?domain=brand", headers=auth).json()
        assert {i["subject_norm"] for i in page["items"]} >= {"zcpend1", "zcpend2"}

        ok = client.post(
            f"/api/v1/blacklist/assertions/{aid_ok}/decide", headers=auth, json={"approve": True}
        ).json()
        assert ok["approved"] is True
        assert ok["canonical"] == "active"

        no = client.post(
            f"/api/v1/blacklist/assertions/{aid_no}/decide",
            headers=auth,
            json={"approve": False, "reason": "误报"},
        ).json()
        assert no["approved"] is False
        assert no["canonical"] == "removed"

        with psycopg.connect(migrated_db) as conn:
            st = dict(
                conn.execute(
                    "SELECT id, status FROM app.blacklist_assertion WHERE id IN (%s, %s)",
                    (aid_ok, aid_no),
                ).fetchall()
            )
        assert st[aid_ok] == "active"
        assert st[aid_no] == "revoked"

    def test_revoke_keeps_when_other_source(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """撤销 manual 源后，import 源仍在 → 主体保持拉黑（B5① 验收②语义经 HTTP 复现）。"""
        auth = _auth(client)
        rec = client.post(
            "/api/v1/blacklist/assertions",
            headers=auth,
            json={"domain": "brand", "subject_norm": "zcmulti", "verdict": "block"},
        ).json()
        manual_id = rec["assertion_id"]
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.blacklist_assertion"
                " (team_id, domain, subject_norm, verdict, source, status)"
                " VALUES (NULL, 'brand', 'zcmulti', 'block', 'import', 'active')"
            )

        rv = client.post(
            f"/api/v1/blacklist/assertions/{manual_id}/revoke", headers=auth, json={}
        ).json()
        assert rv["canonical"] == "active"  # import 源仍在，仍拉黑
        page = client.get("/api/v1/blacklist?domain=brand&q=zcmulti", headers=auth).json()
        assert page["total"] == 1
        assert page["items"][0]["source"] == "import"  # 主导源降级到 import


class TestTrademark:
    def test_trgm_nice_live_filters(self, client: TestClient, seeded: dict) -> None:
        auth = _auth(client)
        r = client.get("/api/v1/trademarks?q=dyso", headers=auth).json()
        assert any(i["mark_norm"] == "dyson" for i in r["items"])

        r9 = client.get("/api/v1/trademarks?nice=9", headers=auth).json()
        norms9 = {i["mark_norm"] for i in r9["items"]}
        assert "dyson" in norms9 and "garden" not in norms9  # garden 是 nice 35

        live = client.get("/api/v1/trademarks?q=mark&live_only=true", headers=auth).json()
        assert all(i["is_live"] for i in live["items"])
        assert "deadmark" not in {i["mark_norm"] for i in live["items"]}


class TestTroCases:
    def test_query_and_status(self, client: TestClient, seeded: dict) -> None:
        auth = _auth(client)
        r = client.get("/api/v1/tro-cases?q=acmebrand", headers=auth).json()
        assert any(c["case_no"] == "ZC-2026-1" for c in r["items"])

        act = client.get("/api/v1/tro-cases?status=active&q=ZC-", headers=auth).json()
        assert all(c["status"] == "active" for c in act["items"])
        assert "ZC-2026-2" not in {c["case_no"] for c in act["items"]}  # dismissed 被滤


class TestImportErrorReport:
    def test_error_report_surfaces_sample_errors(self, client: TestClient, seeded: dict) -> None:
        auth = _auth(client)
        rep = client.get(f"/api/v1/import-jobs/{seeded['job']}/error-report", headers=auth).json()
        assert rep["err_rows"] == 2
        assert len(rep["errors"]) == 2
        assert rep["errors"][0]["reason"] == "主体字段为空"
        assert rep["chunks"][0]["expected"] == 3

    def test_missing_job_404(self, client: TestClient, seeded: dict) -> None:
        auth = _auth(client)
        r = client.get("/api/v1/import-jobs/999999999/error-report", headers=auth)
        assert r.status_code == 422  # BusinessError 默认（codebase 约定：not-found=422）
        assert r.json()["error"]["code"] == "IMPORT_JOB_NOT_FOUND"


class TestPermission:
    def test_no_compliance_permission_forbidden(self, client: TestClient, seeded: dict) -> None:
        """无 compliance 权限点的普通用户 → 403（门控三处之一：权限点）。"""
        auth = _login(client, PLAIN, PASSWORD)
        r = client.get("/api/v1/blacklist?domain=brand", headers=auth)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "PERMISSION_DENIED"
