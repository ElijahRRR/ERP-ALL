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
                conn.execute("SELECT count(*) FROM app.permission").fetchone()[0] == 53
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
