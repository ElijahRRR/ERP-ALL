"""identity 域：team / app_user / role / permission / role_permission / user_role
/ audit_log(月分区,不可篡改) / shared_resource + RLS + 授权 + 种子

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-10

依据 specs/001-domain-model/01-identity.md。
偏离记录：portal_account 延后至 R2#6（其 purchaser_id 外键目标 purchaser 属 order 域，
R1 不建空悬表）——已回写 progress.md。
登录路径说明：erp_app 受 RLS，认证时 GUC 尚未注入，故提供 SECURITY DEFINER 的
app.auth_user_by_username / app.auth_user_by_id（属主 = migration 执行角色，绕过 RLS，
仅返回认证所需列），R1-04 的认证服务只允许经由这两个函数查用户。
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _team_rls(table: str, team_col: str = "team_id") -> str:
    """标准团队域 RLS 四策略（SELECT/INSERT/UPDATE/DELETE）。"""
    return f"""
    ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY;
    CREATE POLICY {table}_sel ON app.{table} FOR SELECT
      USING ({team_col} = app.current_team() OR app.is_super());
    CREATE POLICY {table}_ins ON app.{table} FOR INSERT
      WITH CHECK ({team_col} = app.current_team() OR app.is_super());
    CREATE POLICY {table}_upd ON app.{table} FOR UPDATE
      USING ({team_col} = app.current_team() OR app.is_super())
      WITH CHECK ({team_col} = app.current_team() OR app.is_super());
    CREATE POLICY {table}_del ON app.{table} FOR DELETE
      USING ({team_col} = app.current_team() OR app.is_super());
    """


def _touch(table: str) -> str:
    return f"""
    CREATE TRIGGER {table}_touch BEFORE UPDATE ON app.{table}
      FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
    """


def upgrade() -> None:
    # ── team ──
    op.execute(
        """
        CREATE TABLE app.team (
          id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          name        text NOT NULL UNIQUE,
          status      text NOT NULL DEFAULT 'active'
                        CONSTRAINT ck_team_status CHECK (status IN ('active','disabled')),
          settings    jsonb NOT NULL DEFAULT '{}',
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE app.team ENABLE ROW LEVEL SECURITY;
        CREATE POLICY team_sel ON app.team FOR SELECT
          USING (id = app.current_team() OR app.is_super());
        CREATE POLICY team_ins ON app.team FOR INSERT WITH CHECK (app.is_super());
        CREATE POLICY team_upd ON app.team FOR UPDATE
          USING (app.is_super()) WITH CHECK (app.is_super());
        CREATE POLICY team_del ON app.team FOR DELETE USING (app.is_super());
        """
    )
    op.execute(_touch("team"))

    # ── app_user ──
    op.execute(
        """
        CREATE TABLE app.app_user (
          id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id         bigint REFERENCES app.team(id),
          username        text NOT NULL UNIQUE,
          password_hash   text NOT NULL,
          display_name    text NOT NULL,
          is_super        boolean NOT NULL DEFAULT false,
          status          text NOT NULL DEFAULT 'active'
                            CONSTRAINT ck_app_user_status
                            CHECK (status IN ('active','disabled','locked')),
          token_version   int NOT NULL DEFAULT 0,
          failed_attempts smallint NOT NULL DEFAULT 0,
          last_login_at   timestamptz,
          created_at      timestamptz NOT NULL DEFAULT now(),
          updated_at      timestamptz NOT NULL DEFAULT now(),
          created_by      bigint,
          CONSTRAINT ck_app_user_team CHECK (is_super OR team_id IS NOT NULL)
        );
        CREATE INDEX ix_app_user_team ON app.app_user (team_id, status);
        """
    )
    op.execute(_team_rls("app_user"))
    op.execute(_touch("app_user"))

    # 认证专用 SECURITY DEFINER 查找（绕过 RLS 的唯一合法通道，见文件头说明）
    op.execute(
        """
        CREATE FUNCTION app.auth_user_by_username(p_username text)
        RETURNS SETOF app.app_user LANGUAGE sql SECURITY DEFINER STABLE
        SET search_path = app AS
        $$ SELECT * FROM app.app_user WHERE username = lower(p_username) $$;

        CREATE FUNCTION app.auth_user_by_id(p_id bigint)
        RETURNS SETOF app.app_user LANGUAGE sql SECURITY DEFINER STABLE
        SET search_path = app AS
        $$ SELECT * FROM app.app_user WHERE id = p_id $$;
        """
    )

    # ── role / permission / 关联 ──
    op.execute(
        """
        CREATE TABLE app.role (
          id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id     bigint REFERENCES app.team(id),
          name        text NOT NULL,
          description text,
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now(),
          created_by  bigint
        );
        CREATE UNIQUE INDEX uq_role ON app.role (COALESCE(team_id, 0), name);
        ALTER TABLE app.role ENABLE ROW LEVEL SECURITY;
        CREATE POLICY role_sel ON app.role FOR SELECT
          USING (team_id IS NULL OR team_id = app.current_team() OR app.is_super());
        CREATE POLICY role_ins ON app.role FOR INSERT
          WITH CHECK (team_id = app.current_team() OR app.is_super());
        CREATE POLICY role_upd ON app.role FOR UPDATE
          USING (team_id = app.current_team() OR app.is_super())
          WITH CHECK (team_id = app.current_team() OR app.is_super());
        CREATE POLICY role_del ON app.role FOR DELETE
          USING (team_id = app.current_team() OR app.is_super());
        """
    )
    op.execute(_touch("role"))

    op.execute(
        """
        CREATE TABLE app.permission (
          code        text PRIMARY KEY,
          module      text NOT NULL,
          name        text NOT NULL,
          description text
        );
        """
    )

    op.execute(
        """
        CREATE TABLE app.role_permission (
          role_id         bigint NOT NULL REFERENCES app.role(id) ON DELETE CASCADE,
          permission_code text NOT NULL REFERENCES app.permission(code),
          PRIMARY KEY (role_id, permission_code)
        );
        ALTER TABLE app.role_permission ENABLE ROW LEVEL SECURITY;
        CREATE POLICY role_permission_sel ON app.role_permission FOR SELECT
          USING (EXISTS (SELECT 1 FROM app.role r WHERE r.id = role_id
                 AND (r.team_id IS NULL OR r.team_id = app.current_team() OR app.is_super())));
        CREATE POLICY role_permission_all ON app.role_permission FOR ALL
          USING (EXISTS (SELECT 1 FROM app.role r WHERE r.id = role_id
                 AND (r.team_id = app.current_team() OR app.is_super())))
          WITH CHECK (EXISTS (SELECT 1 FROM app.role r WHERE r.id = role_id
                 AND (r.team_id = app.current_team() OR app.is_super())));
        """
    )

    op.execute(
        """
        CREATE TABLE app.user_role (
          user_id    bigint NOT NULL REFERENCES app.app_user(id) ON DELETE CASCADE,
          role_id    bigint NOT NULL REFERENCES app.role(id) ON DELETE CASCADE,
          granted_by bigint,
          granted_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, role_id)
        );
        ALTER TABLE app.user_role ENABLE ROW LEVEL SECURITY;
        CREATE POLICY user_role_all ON app.user_role FOR ALL
          USING (EXISTS (SELECT 1 FROM app.app_user u WHERE u.id = user_id
                 AND (u.team_id = app.current_team() OR app.is_super())))
          WITH CHECK (EXISTS (SELECT 1 FROM app.app_user u WHERE u.id = user_id
                 AND (u.team_id = app.current_team() OR app.is_super())));
        """
    )

    # ── audit_log：月分区 + append-only ──
    op.execute(
        """
        CREATE TABLE app.audit_log (
          id          bigint GENERATED ALWAYS AS IDENTITY,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          actor_type  text NOT NULL
                        CONSTRAINT ck_audit_actor CHECK (actor_type IN ('user','portal','system')),
          actor_id    bigint,
          team_id     bigint,
          action      text NOT NULL,
          object_type text NOT NULL,
          object_id   text NOT NULL,
          before      jsonb,
          after       jsonb,
          ip          inet,
          user_agent  text,
          request_id  text,
          PRIMARY KEY (id, occurred_at)
        ) PARTITION BY RANGE (occurred_at);
        CREATE INDEX ix_audit_team_time  ON app.audit_log (team_id, occurred_at);
        CREATE INDEX ix_audit_object     ON app.audit_log (object_type, object_id, occurred_at);
        CREATE INDEX ix_audit_actor      ON app.audit_log (actor_type, actor_id, occurred_at);
        SELECT app.ensure_month_partitions('app.audit_log');
        ALTER TABLE app.audit_log ENABLE ROW LEVEL SECURITY;
        CREATE POLICY audit_sel ON app.audit_log FOR SELECT
          USING (team_id = app.current_team() OR app.is_super());
        CREATE POLICY audit_ins ON app.audit_log FOR INSERT WITH CHECK (true);
        -- 无 UPDATE/DELETE policy + 无对应 GRANT = 双重不可篡改
        """
    )

    # ── shared_resource ──
    op.execute(
        """
        CREATE TABLE app.shared_resource (
          id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          resource_domain text NOT NULL
                            CONSTRAINT ck_shared_domain
                            CHECK (resource_domain IN ('catalog','compliance','gtin')),
          owner_team_id   bigint NOT NULL REFERENCES app.team(id),
          grantee_team_id bigint NOT NULL REFERENCES app.team(id),
          granted_by      bigint NOT NULL,
          granted_at      timestamptz NOT NULL DEFAULT now(),
          revoked_at      timestamptz
        );
        CREATE UNIQUE INDEX uq_shared_resource
          ON app.shared_resource (resource_domain, owner_team_id, grantee_team_id)
          WHERE revoked_at IS NULL;
        ALTER TABLE app.shared_resource ENABLE ROW LEVEL SECURITY;
        CREATE POLICY shared_sel ON app.shared_resource FOR SELECT
          USING (owner_team_id = app.current_team()
                 OR grantee_team_id = app.current_team() OR app.is_super());
        CREATE POLICY shared_ins ON app.shared_resource FOR INSERT WITH CHECK (app.is_super());
        CREATE POLICY shared_upd ON app.shared_resource FOR UPDATE
          USING (app.is_super()) WITH CHECK (app.is_super());
        CREATE POLICY shared_del ON app.shared_resource FOR DELETE USING (app.is_super());
        """
    )

    # ── 授权（erp_app）──
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.team, app.app_user, app.role,
          app.role_permission, app.user_role, app.shared_resource TO erp_app;
        GRANT SELECT ON app.permission TO erp_app;
        GRANT SELECT, INSERT ON app.audit_log TO erp_app;
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO erp_app;
        GRANT EXECUTE ON FUNCTION app.auth_user_by_username(text) TO erp_app;
        GRANT EXECUTE ON FUNCTION app.auth_user_by_id(bigint) TO erp_app;
        """
    )

    # ── 种子：权限点（EA-003 附录 A）──
    perms: list[tuple[str, str, str]] = [
        ("identity.team_admin", "identity", "团队管理（超管）"),
        ("identity.user_read", "identity", "成员查看"),
        ("identity.user_write", "identity", "成员管理"),
        ("identity.role_write", "identity", "角色管理"),
        ("identity.audit_read", "identity", "审计日志查看"),
        ("channel.store_read", "channel", "店铺查看"),
        ("channel.store_write", "channel", "店铺管理"),
        ("channel.credential_view", "channel", "凭证状态查看"),
        ("channel.credential_write", "channel", "凭证维护"),
        ("channel.proxy_read", "channel", "代理查看"),
        ("channel.proxy_write", "channel", "代理管理"),
        ("channel.quota_write", "channel", "配额配置"),
        ("channel.incident_read", "channel", "店铺事件查看"),
        ("channel.incident_write", "channel", "店铺事件处理"),
        ("catalog.product_read", "catalog", "产品查看"),
        ("catalog.product_write", "catalog", "产品编辑"),
        ("catalog.gtin_read", "catalog", "GTIN 池查看"),
        ("catalog.import_read", "catalog", "导入作业查看"),
        ("catalog.import_write", "catalog", "数据导入"),
        ("catalog.category_write", "catalog", "类目映射修正"),
        ("catalog.source_write", "catalog", "货源录入"),
        ("listing.read", "listing", "刊登查看"),
        ("listing.allocate", "listing", "分配上架"),
        ("listing.submit", "listing", "提交上架"),
        ("listing.delist", "listing", "下架"),
        ("listing.maintain", "listing", "在架维护"),
        ("listing.error_admin", "listing", "错误字典维护"),
        ("pricing.read", "pricing", "定价策略查看"),
        ("pricing.write", "pricing", "定价策略管理"),
        ("order.read", "order", "订单查看"),
        ("order.check", "order", "订单检查处理"),
        ("order.assign", "order", "采购单分配"),
        ("order.ship", "order", "发货回传"),
        ("procurement.read", "order", "采购执行单查看"),
        ("procurement.execute", "order", "采购执行（内部领单/回填）"),
        ("procurement.admin", "order", "采购方管理"),
    ]
    values = ",".join(f"('{c}','{m}','{n}')" for c, m, n in perms)
    op.execute(f"INSERT INTO app.permission (code, module, name) VALUES {values}")

    # ── 种子：全局模板角色（team_id NULL，建团队时复制；权限映射运营可调）──
    role_perm: dict[str, list[str]] = {
        "采集员": ["catalog.product_read"],
        "审核员": ["catalog.product_read", "catalog.product_write"],
        "上架员": [
            "catalog.product_read",
            "catalog.gtin_read",
            "channel.store_read",
            "listing.read",
            "listing.allocate",
            "listing.submit",
            "listing.delist",
            "pricing.read",
        ],
        "维护员": ["channel.store_read", "listing.read", "listing.maintain", "pricing.read"],
        "订单员": [
            "order.read",
            "order.check",
            "order.assign",
            "order.ship",
            "procurement.read",
        ],
        "财务": ["order.read", "channel.store_read"],
        "团队管理员": [
            "identity.user_read",
            "identity.user_write",
            "identity.role_write",
            "identity.audit_read",
            "channel.store_read",
            "channel.store_write",
            "channel.credential_view",
            "channel.credential_write",
            "channel.proxy_read",
            "channel.proxy_write",
            "channel.quota_write",
            "channel.incident_read",
            "channel.incident_write",
            "catalog.product_read",
            "catalog.gtin_read",
            "listing.read",
            "pricing.read",
            "order.read",
            "procurement.read",
        ],
    }
    for role_name, codes in role_perm.items():
        code_list = ",".join(f"'{c}'" for c in codes)
        op.execute(
            f"""
            WITH r AS (
              INSERT INTO app.role (team_id, name, description)
              VALUES (NULL, '{role_name}', '全局模板角色') RETURNING id
            )
            INSERT INTO app.role_permission (role_id, permission_code)
            SELECT r.id, p.code FROM r, app.permission p WHERE p.code IN ({code_list});
            """
        )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app.auth_user_by_username(text)")
    op.execute("DROP FUNCTION IF EXISTS app.auth_user_by_id(bigint)")
    for t in (
        "shared_resource",
        "audit_log",
        "user_role",
        "role_permission",
        "permission",
        "role",
        "app_user",
        "team",
    ):
        op.execute(f"DROP TABLE IF EXISTS app.{t} CASCADE")
