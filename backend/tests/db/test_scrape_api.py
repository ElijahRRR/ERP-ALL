"""R1-09 验收：采集最小闭环。

建 job → worker 注册/拨入 → 领任务（租约）→ 回传 → product upsert（去重协议）
→ 作业计数；断连/超时任务回收；迟到结果（旧租约）拒收。
"""

import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.core.security import hash_password

from .test_identity_api import PASSWORD, _login

ADMIN = "scrape_admin"
TEAM = "采集测试团队"
ENROLL = "test-enroll-token-0001"
ASIN1, ASIN2 = "B0TEST00A1", "B0TEST00A2"


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    """自给自足：团队 + 管理员（模板角色含 scrape.*）+ 注册令牌配置。"""
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM app.app_user WHERE username = %s", (ADMIN,))
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        uid = conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '采集管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO app.role (team_id, name, description)
            SELECT %s, name, description FROM app.role tpl
            WHERE tpl.team_id IS NULL AND tpl.name = '团队管理员'
              AND NOT EXISTS (SELECT 1 FROM app.role r
                              WHERE r.team_id = %s AND r.name = '团队管理员')
            """,
            (team_id, team_id),
        )
        role_id = conn.execute(
            "SELECT id FROM app.role WHERE team_id = %s AND name = '团队管理员'", (team_id,)
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO app.role_permission (role_id, permission_code)
            SELECT %s, rp.permission_code FROM app.role tpl
            JOIN app.role_permission rp ON rp.role_id = tpl.id
            WHERE tpl.team_id IS NULL AND tpl.name = '团队管理员'
            ON CONFLICT DO NOTHING
            """,
            (role_id,),
        )
        conn.execute(
            "INSERT INTO app.user_role (user_id, role_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (uid, role_id),
        )
        # 注册令牌 + 清理既有采集数据/节点（幂等重跑）
        conn.execute(
            "INSERT INTO app.system_config (key, value)"
            " VALUES ('scrape.worker_enroll_token', to_jsonb(%s::text))"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (ENROLL,),
        )
        conn.execute("DELETE FROM app.scrape_result WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.scrape_task WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.scrape_job WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.product WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.worker_node WHERE node_key LIKE 'testnode-%'")
    return {"team": team_id, "user": uid}


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> TestClient:
    from erp.main import app

    return TestClient(app)


def _register_node(client: TestClient, key: str) -> dict[str, str]:
    r = client.post(
        "/api/v1/worker/v1/register",
        json={"node_key": key, "kind": "scraper_amazon", "enroll_token": ENROLL},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return {"X-Node-Key": key, "X-Node-Token": body["node_token"]}


class TestWorkerLoop:
    def test_full_minimal_loop(self, client: TestClient, seeded: dict[str, int]) -> None:
        """建 job(2 ASIN) → 领 → 一成一败×3 → product 入库 + 作业 partial 计数。"""
        auth = _login(client, ADMIN, PASSWORD)
        r = client.post(
            "/api/v1/scrape-jobs",
            headers=auth,
            json={
                "source": "amazon",
                "job_kind": "product_detail",
                "input": {"targets": [ASIN1, ASIN2]},
            },
        )
        assert r.status_code == 201, r.text
        job_id = r.json()["id"]
        assert r.json()["total_tasks"] == 2

        node = _register_node(client, "testnode-a")
        pulled = client.get("/api/v1/worker/v1/tasks/pull", headers=node).json()["tasks"]
        assert {t["target_ref"] for t in pulled} == {ASIN1, ASIN2}
        assert all(t["attempt"] == 1 for t in pulled)
        t1 = next(t for t in pulled if t["target_ref"] == ASIN1)
        t2 = next(t for t in pulled if t["target_ref"] == ASIN2)

        # ASIN1 成功回传 → product upsert
        r = client.post(
            "/api/v1/worker/v1/tasks/result",
            headers=node,
            json={
                "task_id": t1["task_id"],
                "attempt": t1["attempt"],
                "success": True,
                "payload": {
                    "title": "Test Widget Pro",
                    "brand": "TestBrand",
                    "price_snapshot": {"list": 19.99},
                    "attrs": {"color": "red"},
                },
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["accepted"] is True
        # R2-15 判据④⑤：master_sku 生成式改为「不足 9 位左填零、超出原样输出」，
        # 故最短是 M+9=10 字符。原断言写死 == 8（M+7），那是被修掉的截断式的长度。
        # 这里断言**不变量**而不是某个具体长度：M 前缀 + 纯数字 + 至少 9 位。
        # 「越过 9 位后仍不截断」由 test_master_sku_no_truncate.py 直接断言（判据④
        # 明写不得只测小序号），本处只保证常规路径的格式。
        master_sku = body["product"]["master_sku"]
        assert master_sku.startswith("M")
        assert master_sku[1:].isdigit()
        assert len(master_sku[1:]) >= 9

        # ASIN2 连续失败 3 次（每次失败归还→重领，attempt 递增）→ dead
        task2 = t2
        for expect_attempt in (1, 2, 3):
            assert task2["attempt"] == expect_attempt
            r = client.post(
                "/api/v1/worker/v1/tasks/result",
                headers=node,
                json={
                    "task_id": task2["task_id"],
                    "attempt": task2["attempt"],
                    "success": False,
                    "error_type": "blocked",
                    "error_detail": "503",
                },
            )
            assert r.json()["accepted"] is True
            if expect_attempt < 3:
                again = client.get("/api/v1/worker/v1/tasks/pull", headers=node).json()["tasks"]
                assert len(again) == 1
                task2 = again[0]

        job = client.get(f"/api/v1/scrape-jobs/{job_id}", headers=auth).json()
        assert job["status"] == "partial"
        assert job["done_tasks"] == 1
        assert job["failed_tasks"] == 1
        assert job["task_stats"] == {"done": 1, "dead": 1}

    def test_product_in_db_and_dedup_no_status_reset(
        self, client: TestClient, seeded: dict[str, int], migrated_db: str
    ) -> None:
        """验收：product 表出现该 ASIN；重复采集刷新快照但不重置 status（03 §去重协议）。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            row = conn.execute(
                "SELECT id, master_sku, title, status FROM app.product"
                " WHERE team_id = %s AND source_ref = %s",
                (seeded["team"], ASIN1),
            ).fetchone()
            assert row is not None and row[3] == "ingested"
            conn.execute("UPDATE app.product SET status = 'audit_passed' WHERE id = %s", (row[0],))

        auth = _login(client, ADMIN, PASSWORD)
        job_id = client.post(
            "/api/v1/scrape-jobs",
            headers=auth,
            json={
                "source": "amazon",
                "job_kind": "product_detail",
                "input": {"targets": [ASIN1]},
            },
        ).json()["id"]
        node = _register_node(client, "testnode-b")
        task = client.get("/api/v1/worker/v1/tasks/pull", headers=node).json()["tasks"][0]
        r = client.post(
            "/api/v1/worker/v1/tasks/result",
            headers=node,
            json={
                "task_id": task["task_id"],
                "attempt": task["attempt"],
                "payload": {"title": "Test Widget Pro V2", "price_snapshot": {"list": 17.5}},
            },
        )
        assert r.json()["accepted"] is True

        with psycopg.connect(migrated_db, autocommit=True) as conn:
            title, status, sku_count = conn.execute(
                "SELECT title, status,"
                " (SELECT count(*) FROM app.product WHERE team_id = %s AND source_ref = %s)"
                " FROM app.product WHERE team_id = %s AND source_ref = %s",
                (seeded["team"], ASIN1, seeded["team"], ASIN1),
            ).fetchone()
        assert title == "Test Widget Pro V2"  # 快照已刷新
        assert status == "audit_passed"  # status 未被重置
        assert sku_count == 1  # 去重未产生第二行
        job = client.get(f"/api/v1/scrape-jobs/{job_id}", headers=auth).json()
        assert job["status"] == "done"


class TestLeaseProtocol:
    def test_disconnected_worker_reclaim_and_stale_reject(
        self, client: TestClient, seeded: dict[str, int], migrated_db: str
    ) -> None:
        """验收：断连回收——心跳超时节点的任务归还；旧租约迟到回传拒收。"""
        auth = _login(client, ADMIN, PASSWORD)
        client.post(
            "/api/v1/scrape-jobs",
            headers=auth,
            json={
                "source": "amazon",
                "job_kind": "product_detail",
                "input": {"targets": ["B0RECLAIM1"]},
            },
        )
        dead = _register_node(client, "testnode-dead")
        task = client.get("/api/v1/worker/v1/tasks/pull", headers=dead).json()["tasks"][0]

        # 模拟断连：心跳时间倒拨（超过默认 90s 阈值）
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.worker_node SET last_heartbeat_at = now() - interval '10 min'"
                " WHERE node_key = 'testnode-dead'",
            )

        # 另一节点 sync 触发回收 → 任务应可被重新领取
        live = _register_node(client, "testnode-live")
        client.post("/api/v1/worker/v1/sync", headers=live, json={})
        repulled = client.get("/api/v1/worker/v1/tasks/pull", headers=live).json()["tasks"]
        assert [t["target_ref"] for t in repulled] == ["B0RECLAIM1"]
        assert repulled[0]["attempt"] == 2  # 重新派发租约递增

        # 死节点用旧租约迟到回传 → stale 拒收
        r = client.post(
            "/api/v1/worker/v1/tasks/result",
            headers=dead,
            json={
                "task_id": task["task_id"],
                "attempt": task["attempt"],
                "payload": {"title": "late result"},
            },
        )
        assert r.json()["accepted"] is False
        assert r.json()["stale"] is True

        # 新租约正常收货
        r = client.post(
            "/api/v1/worker/v1/tasks/result",
            headers=live,
            json={
                "task_id": repulled[0]["task_id"],
                "attempt": repulled[0]["attempt"],
                "payload": {"title": "Reclaimed OK"},
            },
        )
        assert r.json()["accepted"] is True

    def test_release_returns_task(self, client: TestClient, seeded: dict[str, int]) -> None:
        """worker 优雅归还：release 后任务可被再次领取。"""
        auth = _login(client, ADMIN, PASSWORD)
        client.post(
            "/api/v1/scrape-jobs",
            headers=auth,
            json={
                "source": "amazon",
                "job_kind": "product_detail",
                "input": {"targets": ["B0RELEASE1"]},
            },
        )
        node = _register_node(client, "testnode-rel")
        task = client.get("/api/v1/worker/v1/tasks/pull", headers=node).json()["tasks"][0]
        r = client.post(
            "/api/v1/worker/v1/tasks/release",
            headers=node,
            json={"tasks": [{"task_id": task["task_id"], "attempt": task["attempt"]}]},
        )
        assert r.json()["released"] == 1
        again = client.get("/api/v1/worker/v1/tasks/pull", headers=node).json()["tasks"]
        assert [t["target_ref"] for t in again] == ["B0RELEASE1"]
        assert again[0]["attempt"] == task["attempt"] + 1


class TestNodeSecurity:
    def test_register_gate(self, client: TestClient, seeded: dict[str, int]) -> None:
        r = client.post(
            "/api/v1/worker/v1/register",
            json={"node_key": "testnode-x", "kind": "scraper_amazon", "enroll_token": "wrong"},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "SCRAPE_ENROLL_TOKEN_INVALID"

    def test_bad_token_rejected(self, client: TestClient, seeded: dict[str, int]) -> None:
        _register_node(client, "testnode-sec")
        r = client.get(
            "/api/v1/worker/v1/tasks/pull",
            headers={"X-Node-Key": "testnode-sec", "X-Node-Token": "forged"},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "SCRAPE_NODE_AUTH"

    def test_duplicate_node_key_rejected(self, client: TestClient, seeded: dict[str, int]) -> None:
        r = client.post(
            "/api/v1/worker/v1/register",
            json={"node_key": "testnode-sec", "kind": "scraper_amazon", "enroll_token": ENROLL},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "SCRAPE_NODE_EXISTS"


class TestUiEndpoints:
    def test_job_list_and_nodes(self, client: TestClient, seeded: dict[str, int]) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        jobs = client.get("/api/v1/scrape-jobs", headers=auth).json()
        assert jobs["total"] >= 1
        nodes = client.get("/api/v1/worker-nodes", headers=auth).json()
        assert any(n["node_key"] == "testnode-a" for n in nodes)

    def test_unsupported_kind_rejected(self, client: TestClient, seeded: dict[str, int]) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        r = client.post(
            "/api/v1/scrape-jobs",
            headers=auth,
            json={"source": "amazon", "job_kind": "keyword", "input": {"targets": ["x"]}},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "SCRAPE_KIND_UNSUPPORTED"

    def test_cancel_job(self, client: TestClient, seeded: dict[str, int]) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        job_id = client.post(
            "/api/v1/scrape-jobs",
            headers=auth,
            json={
                "source": "amazon",
                "job_kind": "product_detail",
                "input": {"targets": ["B0CANCEL01"]},
            },
        ).json()["id"]
        r = client.post(f"/api/v1/scrape-jobs/{job_id}/cancel", headers=auth)
        assert r.json()["status"] == "cancelled"
        job = client.get(f"/api/v1/scrape-jobs/{job_id}", headers=auth).json()
        assert job["task_stats"] == {"dead": 1}
