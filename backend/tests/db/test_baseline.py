"""R1-03 migration 基线的行为验收：RLS 隔离 / 审计不可篡改 / 认证通道 / 种子。"""

import psycopg
import pytest


def _set_team(conn: psycopg.Connection, team_id: int | None, *, super_: bool = False) -> None:
    conn.execute("RESET ALL")
    if team_id is not None:
        conn.execute(f"SET app.current_team = '{team_id}'")
    if super_:
        conn.execute("SET app.is_super = 'on'")


class TestRLS:
    def test_team_isolation(self, app_conn: psycopg.Connection, team_ids: tuple[int, int]) -> None:
        a, b = team_ids
        _set_team(app_conn, a)
        app_conn.execute(
            "INSERT INTO app.proxy (team_id, kind, host, port) VALUES (%s,'http','10.0.0.1',8080)"
            " ON CONFLICT DO NOTHING",
            (a,),
        )
        app_conn.commit()
        assert app_conn.execute("SELECT count(*) FROM app.proxy").fetchone()[0] >= 1

        _set_team(app_conn, b)
        assert app_conn.execute("SELECT count(*) FROM app.proxy").fetchone()[0] == 0

        _set_team(app_conn, None, super_=True)
        assert app_conn.execute("SELECT count(*) FROM app.proxy").fetchone()[0] >= 1

    def test_no_guc_sees_nothing(self, app_conn: psycopg.Connection) -> None:
        _set_team(app_conn, None)
        assert app_conn.execute("SELECT count(*) FROM app.proxy").fetchone()[0] == 0

    def test_cross_team_insert_rejected(
        self, app_conn: psycopg.Connection, team_ids: tuple[int, int]
    ) -> None:
        a, b = team_ids
        _set_team(app_conn, a)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app_conn.execute(
                "INSERT INTO app.proxy (team_id, kind, host, port)"
                " VALUES (%s,'http','10.0.0.2',8080)",
                (b,),
            )
        app_conn.rollback()


class TestAuditImmutable:
    def test_insert_ok_update_delete_denied(
        self, app_conn: psycopg.Connection, team_ids: tuple[int, int]
    ) -> None:
        a, _ = team_ids
        _set_team(app_conn, a)
        app_conn.execute(
            "INSERT INTO app.audit_log (actor_type, actor_id, team_id, action,"
            " object_type, object_id, after)"
            " VALUES ('user', 1, %s, 'test.write', 'proxy', '1', '{}')",
            (a,),
        )
        app_conn.commit()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app_conn.execute("UPDATE app.audit_log SET action = 'tampered'")
        app_conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app_conn.execute("DELETE FROM app.audit_log")
        app_conn.rollback()


class TestAuthChannel:
    def test_login_lookup_without_guc(
        self, migrated_db: str, app_conn: psycopg.Connection, team_ids: tuple[int, int]
    ) -> None:
        """认证发生在 GUC 注入前——只有 SECURITY DEFINER 函数可查到用户。"""
        a, _ = team_ids
        with psycopg.connect(migrated_db, autocommit=True) as admin:
            admin.execute(
                "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
                " VALUES (%s, 'rls_test_user', 'x', 'RLS测试')"
                " ON CONFLICT (username) DO NOTHING",
                (a,),
            )
        _set_team(app_conn, None)
        direct = app_conn.execute(
            "SELECT count(*) FROM app.app_user WHERE username = 'rls_test_user'"
        ).fetchone()[0]
        assert direct == 0  # 直查被 RLS 挡住
        via_fn = app_conn.execute(
            "SELECT count(*) FROM app.auth_user_by_username('rls_test_user')"
        ).fetchone()[0]
        assert via_fn == 1  # 认证通道可见


class TestSeeds:
    def test_permissions_roles_channel(self, migrated_db: str) -> None:
        with psycopg.connect(migrated_db) as conn:
            assert (  # 36 基线 + 0007 scrape.* 3 + 0008 audit.* 3
                # + 0010 compliance.import_* 2 + 0030 aftersale.read 1 + 0031 refund.* 2
                # + 0033 catalog.brand_* 2 + 0035 compliance.blacklist_*/trademark/tro 4
                # + 0040 automation.* 2 + 0041 catalog.product_delete 1
                # + 0047 identity.user_delete/role_delete + procurement.purchaser_delete 3
                conn.execute("SELECT count(*) FROM app.permission").fetchone()[0] == 59
            )
            assert (
                conn.execute("SELECT count(*) FROM app.role WHERE team_id IS NULL").fetchone()[0]
                == 7
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM app.channel WHERE code = 'walmart_us'"
                ).fetchone()[0]
                == 1
            )
            # 分区表初始分区已建（本月分区存在）
            assert (
                conn.execute(
                    "SELECT count(*) FROM pg_tables WHERE schemaname='app'"
                    " AND tablename LIKE 'audit_log_p%'"
                ).fetchone()[0]
                >= 4
            )


class TestRlsDeleteInvariant:
    def test_no_delete_grant_without_delete_policy(self, migrated_db: str) -> None:
        """schema 不变量（PR #49 审查 N2）：不允许「授了 DELETE 却没有 DELETE 策略」。

        RLS 下无 DELETE 策略的 DELETE **静默匹配零行、不报错**——R2-14 14b 实撞
        （0025 的 TEAM_RLS 是三策略模板，purchaser 补授 DELETE 后端点回 200、
        墓碑照写、实体行还在）。`_delete_row_or_die` 是调用点纪律，管不住将来
        不用它的新删除路径；本条是结构性防线，天然覆盖所有未来的表。
        """
        with psycopg.connect(migrated_db) as conn:
            rows = conn.execute(
                "SELECT c.relname FROM pg_class c"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname='app' AND c.relkind='r' AND c.relrowsecurity"
                "   AND has_table_privilege('erp_app', c.oid, 'DELETE')"
                "   AND NOT EXISTS (SELECT 1 FROM pg_policies p"
                "                   WHERE p.schemaname='app' AND p.tablename=c.relname"
                "                     AND p.cmd IN ('DELETE','ALL'))"
                " ORDER BY 1"
            ).fetchall()
        assert rows == [], (
            "以下表授了 erp_app 的 DELETE 却没有 DELETE 策略（删除会静默零行）："
            f"{[r[0] for r in rows]}——补 CREATE POLICY 或收回授权，二选一"
        )


_DANGLING_PURCHASER_SQL = (
    "SELECT po.id, po.purchaser_id FROM app.procurement_order po"
    " WHERE po.purchaser_id IS NOT NULL"
    "   AND NOT EXISTS (SELECT 1 FROM app.purchaser p WHERE p.id = po.purchaser_id)"
    "   AND NOT EXISTS (SELECT 1 FROM app.deleted_principal dp"
    "                   WHERE dp.kind = 'purchaser' AND dp.id = po.purchaser_id)"
    " ORDER BY po.id"
)


class TestSoftReferenceInvariant:
    def test_query_catches_bypass_delete_then_clean(self, migrated_db: str) -> None:
        """§7.1.1(b) 后半（PR #49 审查 N6，判据形状按五轮 C 重做）。

        空库上只断言「结果为空」是空判据（空 ⊆ 空恒绿，五轮 C 实测：注释掉
        _write_tombstone 它照样绿）。故本用例**先自己造 violation**——绕过删除服务
        直删一个有单的采购方（migrator 连接，模拟坏路径）——断言查询**抓得到**；
        清掉后再断言全库干净。前半证「查询有牙齿」，后半才是对现库的不变量。
        """
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            pid = conn.execute(
                "INSERT INTO app.purchaser (team_id, name, purchaser_kind, exchange_rate)"
                " SELECT id, 'N6违例样本', 'external', 7.2 FROM app.team LIMIT 1 RETURNING id"
            ).fetchone()[0]
            po = conn.execute(
                "INSERT INTO app.procurement_order"
                " (team_id, store_id, order_id, order_date, status, purchaser_id,"
                "  exchange_rate_locked)"
                " SELECT team_id, 1, 998877, now(), 'backfilled', id, 7.2"
                " FROM app.purchaser WHERE id = %s RETURNING id",
                (pid,),
            ).fetchone()[0]
            try:
                # 绕过删除服务直删——不写墓碑，制造悬空引用
                conn.execute("DELETE FROM app.purchaser WHERE id = %s", (pid,))
                caught = conn.execute(_DANGLING_PURCHASER_SQL).fetchall()
                assert (po, pid) in caught, "查询没抓到人为制造的悬空引用——不变量无牙齿"
            finally:
                conn.execute("DELETE FROM app.procurement_order WHERE id = %s", (po,))
            rows = conn.execute(_DANGLING_PURCHASER_SQL).fetchall()
        assert rows == [], (
            f"以下执行单的 purchaser_id 在主表与墓碑都查不到（永久无法解析）：{rows}"
            "——多半是绕过删除服务的直删或墓碑漏写"
        )
