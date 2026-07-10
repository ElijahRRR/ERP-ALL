"""认证支撑：登录锁定列 + 登录结果记账函数（SECURITY DEFINER）

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-10

登录发生在 GUC 注入前（无团队身份），对 app_user 的登录态更新只能经
SECURITY DEFINER 函数——与 0002 的 auth_user_by_* 同一原则。
锁定语义：连续 p_max_attempts 次失败 → locked_until = now() + p_lock_minutes；
status='locked' 保留给管理员手工封禁，两者独立。
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.app_user ADD COLUMN locked_until timestamptz")
    op.execute(
        """
        CREATE FUNCTION app.auth_record_login(
          p_user_id bigint,
          p_success boolean,
          p_max_attempts int DEFAULT 5,
          p_lock_minutes int DEFAULT 15
        ) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = app AS $$
        BEGIN
          IF p_success THEN
            UPDATE app.app_user
              SET failed_attempts = 0, locked_until = NULL, last_login_at = now()
              WHERE id = p_user_id;
          ELSE
            UPDATE app.app_user
              SET failed_attempts = failed_attempts + 1
              WHERE id = p_user_id;
            UPDATE app.app_user
              SET locked_until = now() + make_interval(mins => p_lock_minutes),
                  failed_attempts = 0
              WHERE id = p_user_id AND failed_attempts >= p_max_attempts;
          END IF;
        END $$;
        """
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION app.auth_record_login(bigint, boolean, int, int) TO erp_app"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app.auth_record_login(bigint, boolean, int, int)")
    op.execute("ALTER TABLE app.app_user DROP COLUMN IF EXISTS locked_until")
