"""R2-14 14b 验收：主体硬删除（user / role / purchaser + deleted_principal 墓碑）。

| 判据 | 用例 |
|---|---|
| ①级：无历史用户直删无墓碑 | `TestUserDelete::test_no_history_user_plain_delete` |
| ②级+坑1：user_id 无 ON DELETE 不挡删 | `TestUserDelete::test_with_history_unlinks_purchaser` |
| 验收③：已删操作人显示「XX（已删除）」 | `TestUserDelete::test_acceptance3_actor_label_resolves` |
| 验收⑤：audit_log / procurement_order 行数不变 | 上述两用例内联断言 |
| 自删守卫 | `TestUserDelete::test_self_delete_refused` |
| 在挂角色拒删 → 解绑后可删 + 模板 404 | `TestRoleDelete` |
| 在途执行单拒删 → 终态后可删 + ③级 id 保留 | `TestPurchaserDelete` |
| 坑2：(kind,id) 复合 PK 跨类不撞 | `TestTombstonePk::test_same_id_across_kinds` |

buyer_account 类不在本轮（表由 R2-13 13b 建，007「三个实现坑」第 3 条）。
"""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.core.security import hash_password

from .test_identity_api import PASSWORD, _login

ADMIN = "r14b_admin"
TEAM = "R2-14b 主体删除测试团队"


def _q(reason: str = "测试删除") -> str:
    return f"?reason={reason}"


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM app.app_user WHERE username LIKE 'r14b_%'")
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        for tbl, cond in (
            ("procurement_order", "team_id = %s"),
            ("purchaser", "team_id = %s"),
            ("deleted_principal", "team_id = %s"),
            ("user_role", "role_id IN (SELECT id FROM app.role WHERE team_id = %s)"),
            ("role", "team_id = %s"),
        ):
            conn.execute(f"DELETE FROM app.{tbl} WHERE {cond}", (team_id,))
        uid = conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '主体删除管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        role_id = conn.execute(
            "INSERT INTO app.role (team_id, name) VALUES (%s, '14b测试角色') RETURNING id",
            (team_id,),
        ).fetchone()[0]
        for code in (
            "identity.user_read", "identity.user_write", "identity.user_delete",
            "identity.role_write", "identity.role_delete",
            "procurement.purchaser_delete", "identity.audit_read",
        ):  # fmt: skip
            conn.execute(
                "INSERT INTO app.role_permission (role_id, permission_code) VALUES (%s, %s)",
                (role_id, code),
            )
        conn.execute("INSERT INTO app.user_role (user_id, role_id) VALUES (%s, %s)", (uid, role_id))
    return {"team": team_id, "admin": uid}


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> TestClient:
    from erp.main import app

    return TestClient(app)


def _mk_user(conn: psycopg.Connection, team_id: int, name: str) -> int:
    return conn.execute(
        "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (team_id, f"r14b_{name}_{uuid.uuid4().hex[:6]}", hash_password(PASSWORD), name),
    ).fetchone()[0]


def _mk_audit_row(conn: psycopg.Connection, team_id: int, actor_id: int) -> None:
    """给目标用户造一条「操作过」的审计行（②级判定的历史来源）。"""
    conn.execute(
        "INSERT INTO app.audit_log (team_id, actor_type, actor_id, action, object_type, object_id)"
        " VALUES (%s, 'user', %s, 'test.noop', 'test', '0')",
        (team_id, actor_id),
    )


def _tombstone(conn: psycopg.Connection, kind: str, entity_id: int) -> tuple | None:
    return conn.execute(
        "SELECT label, team_id, deleted_by FROM app.deleted_principal WHERE kind = %s AND id = %s",
        (kind, entity_id),
    ).fetchone()


class TestUserDelete:
    def test_no_history_user_plain_delete(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """①级：从未操作过的用户 → 物理行消失、无墓碑。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            uid = _mk_user(conn, seeded["team"], "无历史用户")
        auth = _login(client, ADMIN, PASSWORD)
        r = client.delete(f"/api/v1/users/{uid}{_q()}", headers=auth)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["level"] == "no_history" and body["tombstoned"] is False
        with psycopg.connect(migrated_db) as conn:
            assert (
                conn.execute("SELECT count(*) FROM app.app_user WHERE id = %s", (uid,)).fetchone()[
                    0
                ]
                == 0
            )
            assert _tombstone(conn, "user", uid) is None

    def test_with_history_unlinks_purchaser(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """②级 + 坑1承重：purchaser.user_id 无 ON DELETE（0025:96），不显式置 NULL
        则外键直接拒绝——本用例过 = 显式处置在位。同时内联验收⑤：audit_log 行数不变。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            uid = _mk_user(conn, seeded["team"], "干过活用户")
            _mk_audit_row(conn, seeded["team"], uid)
            pid = conn.execute(
                "INSERT INTO app.purchaser (team_id, name, purchaser_kind, user_id, exchange_rate)"
                " VALUES (%s, '内部小采', 'internal', %s, 7.2) RETURNING id",
                (seeded["team"], uid),
            ).fetchone()[0]
            audit_before = conn.execute(
                "SELECT count(*) FROM app.audit_log WHERE actor_type='user' AND actor_id = %s",
                (uid,),
            ).fetchone()[0]
        auth = _login(client, ADMIN, PASSWORD)
        r = client.delete(f"/api/v1/users/{uid}{_q()}", headers=auth)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["level"] == "with_history" and body["tombstoned"] is True
        assert body["purchasers_unlinked"] == 1
        with psycopg.connect(migrated_db) as conn:
            assert (
                conn.execute("SELECT count(*) FROM app.app_user WHERE id = %s", (uid,)).fetchone()[
                    0
                ]
                == 0
            )
            # 采购方实体保留、链接置空（不是连带删除）
            assert conn.execute(
                "SELECT user_id FROM app.purchaser WHERE id = %s", (pid,)
            ).fetchone() == (None,)
            ts = _tombstone(conn, "user", uid)
            assert ts is not None and ts[0] == "干过活用户" and ts[1] == seeded["team"]
            # 验收⑤：③级一行不动（删除动作自身的新行挂在管理员名下，不影响本计数）
            assert (
                conn.execute(
                    "SELECT count(*) FROM app.audit_log WHERE actor_type='user' AND actor_id = %s",
                    (uid,),
                ).fetchone()[0]
                == audit_before
            )

    def test_acceptance3_actor_label_resolves(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """验收③承重：删掉操作过的用户后，audit-logs 里其行显示「XX（已删除）」；
        在册用户显示本名。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            uid = _mk_user(conn, seeded["team"], "留痕后删除")
            _mk_audit_row(conn, seeded["team"], uid)
        auth = _login(client, ADMIN, PASSWORD)
        assert client.delete(f"/api/v1/users/{uid}{_q()}", headers=auth).status_code == 200
        rows = client.get(f"/api/v1/audit-logs?actor_id={uid}", headers=auth).json()["items"]
        assert rows, "被删用户的审计行应仍可查（③级）"
        assert all(r["actor_label"] == "留痕后删除（已删除）" for r in rows)
        # 在册操作人（管理员自己刚产生的删除留痕）→ 显示本名，无（已删除）后缀
        own = client.get(
            f"/api/v1/audit-logs?actor_id={seeded['admin']}&action=identity.user_delete",
            headers=auth,
        ).json()["items"]
        assert own and all(r["actor_label"] == "主体删除管理员" for r in own)

    def test_self_delete_refused(self, client: TestClient, seeded: dict) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        r = client.delete(f"/api/v1/users/{seeded['admin']}{_q()}", headers=auth)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "USER_DELETE_SELF"


class TestRoleDelete:
    def test_in_use_refused_then_deletable(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        # 经 API 建角色 → audit_log 里有 object_type='role' 的行 → ②级路径
        rid = client.post(
            "/api/v1/roles", headers=auth, json={"name": "14b临时角色", "description": "x"}
        ).json()["id"]
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            member = _mk_user(conn, seeded["team"], "角色占用者")
            conn.execute(
                "INSERT INTO app.user_role (user_id, role_id) VALUES (%s, %s)", (member, rid)
            )
            conn.execute(
                "INSERT INTO app.role_permission (role_id, permission_code)"
                " VALUES (%s, 'identity.user_read')",
                (rid,),
            )
        r = client.delete(f"/api/v1/roles/{rid}{_q()}", headers=auth)
        assert r.status_code == 409 and r.json()["error"]["code"] == "ROLE_IN_USE"
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute("DELETE FROM app.user_role WHERE role_id = %s", (rid,))
        r2 = client.delete(f"/api/v1/roles/{rid}{_q()}", headers=auth)
        assert r2.status_code == 200, r2.text
        assert r2.json()["tombstoned"] is True
        with psycopg.connect(migrated_db) as conn:
            assert (
                conn.execute("SELECT count(*) FROM app.role WHERE id = %s", (rid,)).fetchone()[0]
                == 0
            )
            # role_permission 随 CASCADE 清空（角色自身构成，非外部历史）
            assert (
                conn.execute(
                    "SELECT count(*) FROM app.role_permission WHERE role_id = %s", (rid,)
                ).fetchone()[0]
                == 0
            )
            assert _tombstone(conn, "role", rid) is not None

    def test_template_role_404(self, client: TestClient, migrated_db: str) -> None:
        """全局模板（team_id IS NULL）不可删——删了它新团队就长不出这个角色。"""
        with psycopg.connect(migrated_db) as conn:
            tpl = conn.execute("SELECT id FROM app.role WHERE team_id IS NULL LIMIT 1").fetchone()
        assert tpl is not None, "0002 种子应有全局模板角色"
        auth = _login(client, ADMIN, PASSWORD)
        r = client.delete(f"/api/v1/roles/{tpl[0]}{_q()}", headers=auth)
        assert r.status_code == 404


class TestPurchaserDelete:
    def test_in_flight_refused_then_terminal_ok(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """在途执行单拒删；终态后可删。③级行一行不删且 purchaser_id 原值保留
        （0047 外键改软引用的承重证明——外键仍在的话这里删除会直接被拒）。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            pid = conn.execute(
                "INSERT INTO app.purchaser (team_id, name, purchaser_kind, exchange_rate)"
                " VALUES (%s, '外协老李', 'external', 7.2) RETURNING id",
                (seeded["team"],),
            ).fetchone()[0]
            po = conn.execute(
                "INSERT INTO app.procurement_order"
                " (team_id, store_id, order_id, order_date, status, assignee_kind, purchaser_id)"
                " VALUES (%s, 1, 990001, now(), 'claimed', 'external', %s) RETURNING id",
                (seeded["team"], pid),
            ).fetchone()[0]
        auth = _login(client, ADMIN, PASSWORD)
        r = client.delete(f"/api/v1/purchasers/{pid}{_q()}", headers=auth)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "PURCHASER_DELETE_IN_FLIGHT"
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.procurement_order SET status = 'backfilled' WHERE id = %s", (po,)
            )
            po_count_before = conn.execute(
                "SELECT count(*) FROM app.procurement_order WHERE team_id = %s", (seeded["team"],)
            ).fetchone()[0]
        r2 = client.delete(f"/api/v1/purchasers/{pid}{_q()}", headers=auth)
        assert r2.status_code == 200, r2.text
        assert r2.json()["level"] == "with_history" and r2.json()["tombstoned"] is True
        with psycopg.connect(migrated_db) as conn:
            assert (
                conn.execute("SELECT count(*) FROM app.purchaser WHERE id = %s", (pid,)).fetchone()[
                    0
                ]
                == 0
            )
            # 验收⑤ + 软引用承重：行数不变、id 原值保留（不是被置 NULL）
            assert (
                conn.execute(
                    "SELECT count(*) FROM app.procurement_order WHERE team_id = %s",
                    (seeded["team"],),
                ).fetchone()[0]
                == po_count_before
            )
            assert conn.execute(
                "SELECT purchaser_id FROM app.procurement_order WHERE id = %s", (po,)
            ).fetchone() == (pid,)
            assert _tombstone(conn, "purchaser", pid) is not None

    def test_never_assigned_no_tombstone(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            pid = conn.execute(
                "INSERT INTO app.purchaser (team_id, name, purchaser_kind, exchange_rate)"
                " VALUES (%s, '从未接单', 'external', 7.2) RETURNING id",
                (seeded["team"],),
            ).fetchone()[0]
        auth = _login(client, ADMIN, PASSWORD)
        r = client.delete(f"/api/v1/purchasers/{pid}{_q()}", headers=auth)
        assert r.status_code == 200 and r.json()["level"] == "no_history"
        with psycopg.connect(migrated_db) as conn:
            assert _tombstone(conn, "purchaser", pid) is None


class TestZeroRowGuard:
    def test_zero_row_delete_raises_and_rolls_back(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """审查 N1：钉住 `_delete_row_or_die` 本体——本单唯一的类级防线。

        它抓的场景（RLS 无 DELETE 策略 → 静默零行）在策略在位时永远不会发生，
        故此前九条判据全绿也钉不住它（审查侧变异实测：掏空检查照样全绿）。
        本用例把策略真拆掉复现「零行」，断言**炸得干净**：抛 RuntimeError、
        事务回滚、墓碑没写、实体还在。跑完恢复策略（finally，失败也不留残局）。
        """
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            pid = conn.execute(
                "INSERT INTO app.purchaser (team_id, name, purchaser_kind, exchange_rate)"
                " VALUES (%s, '守卫测试采购方', 'external', 7.2) RETURNING id",
                (seeded["team"],),
            ).fetchone()[0]
        auth = _login(client, ADMIN, PASSWORD)
        try:
            with psycopg.connect(migrated_db, autocommit=True) as conn:
                conn.execute("DROP POLICY purchaser_del ON app.purchaser")
            with pytest.raises(RuntimeError, match="匹配零行"):
                client.delete(f"/api/v1/purchasers/{pid}{_q()}", headers=auth)
        finally:
            with psycopg.connect(migrated_db, autocommit=True) as conn:
                conn.execute(
                    "CREATE POLICY purchaser_del ON app.purchaser FOR DELETE"
                    " USING (team_id = app.current_team() OR app.is_super())"
                )
        with psycopg.connect(migrated_db) as conn:
            # 炸得干净：实体还在、墓碑没写（事务整体回滚）
            assert conn.execute(
                "SELECT count(*) FROM app.purchaser WHERE id = %s", (pid,)
            ).fetchone() == (1,)
            assert _tombstone(conn, "purchaser", pid) is None
        # 策略恢复后同一实体可正常删除（顺带证明 finally 恢复到位）
        r = client.delete(f"/api/v1/purchasers/{pid}{_q()}", headers=auth)
        assert r.status_code == 200 and r.json()["level"] == "no_history"


class TestTombstonePk:
    def test_same_id_across_kinds(self, seeded: dict, migrated_db: str) -> None:
        """坑2承重：四类主体各有自增序列，跨类 id 必然撞号——PK 是 (kind, id) 才放得下。
        单列 id 做 PK 时本用例第二条 INSERT 直接唯一冲突。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM app.deleted_principal WHERE id = 999999 AND team_id = %s",
                (seeded["team"],),
            )
            for kind in ("user", "role"):
                conn.execute(
                    "INSERT INTO app.deleted_principal (kind, id, team_id, label)"
                    " VALUES (%s, 999999, %s, %s)",
                    (kind, seeded["team"], f"撞号样本-{kind}"),
                )
            n = conn.execute(
                "SELECT count(*) FROM app.deleted_principal WHERE id = 999999"
            ).fetchone()[0]
        assert n == 2
