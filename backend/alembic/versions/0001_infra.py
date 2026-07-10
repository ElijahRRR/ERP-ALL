"""基础设施：schema / 角色 / 公共函数（updated_at 触发器、月分区助手、GUC 读取）

Revision ID: 0001
Revises:
Create Date: 2026-07-10

约定（specs/001-domain-model/00-conventions.md）：
- 业务表在 schema `app`；alembic_version 留在 public。
- 三角色：erp_migrator(DDL/对象属主) / erp_app(DML,受RLS) / portal_app(门户白名单)。
  本地由 infra/pg-init 创建（带口令）；CI/新库若缺且当前连接有 CREATEROLE 则代建（无口令，
  口令由运维 ALTER ROLE 注入）；两者都不行 → 报错提示先跑 pg-init。
- 扩展 pgcrypto/pg_trgm 应预先存在（pg-init / RDS 控制台）；缺失时尝试创建，无权则明确报错。
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ROLES = ("erp_migrator", "erp_app", "portal_app")
EXTENSIONS = ("pgcrypto", "pg_trgm")


def upgrade() -> None:
    # 扩展：存在即过；缺失则尝试创建并给出可操作的错误信息
    for ext in EXTENSIONS:
        op.execute(
            f"""
            DO $$
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = '{ext}') THEN
                BEGIN
                  CREATE EXTENSION "{ext}";
                EXCEPTION WHEN insufficient_privilege THEN
                  RAISE EXCEPTION '缺少扩展 {ext} 且当前角色无权创建——请先以超级用户执行 infra/pg-init/01-extensions.sql（本地）或在 RDS 控制台开启（EA-004 §6）';
                END;
              END IF;
            END $$;
            """
        )

    # 角色：缺失且有权则代建（NOLOGIN 占位，口令/LOGIN 由运维层管理）
    for role in ROLES:
        op.execute(
            f"""
            DO $$
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                BEGIN
                  CREATE ROLE {role};
                EXCEPTION WHEN insufficient_privilege THEN
                  RAISE EXCEPTION '角色 {role} 不存在且当前连接无 CREATEROLE——请先执行 infra/pg-init/01-extensions.sql';
                END;
              END IF;
            END $$;
            """
        )

    op.execute("CREATE SCHEMA IF NOT EXISTS app")
    op.execute("GRANT USAGE ON SCHEMA app TO erp_app, portal_app")

    # updated_at 统一触发器函数
    op.execute(
        """
        CREATE FUNCTION app.touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          NEW.updated_at := now();
          RETURN NEW;
        END $$;
        """
    )

    # 请求级 GUC 读取（RLS policy 统一经由这两个函数，便于审计与替换）
    op.execute(
        """
        CREATE FUNCTION app.current_team() RETURNS bigint LANGUAGE sql STABLE AS
        $$ SELECT nullif(current_setting('app.current_team', true), '')::bigint $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION app.is_super() RETURNS boolean LANGUAGE sql STABLE AS
        $$ SELECT coalesce(current_setting('app.is_super', true), '') = 'on' $$;
        """
    )

    # 月分区助手：为 RANGE(时间) 分区表补齐 [本月-1, 本月+months_ahead] 分区。
    # beat 任务（R1-06+）按 schedule 周期调用；migration 建表后也调用一次做初始分区。
    op.execute(
        """
        CREATE FUNCTION app.ensure_month_partitions(parent regclass, months_ahead int DEFAULT 3)
        RETURNS int LANGUAGE plpgsql AS $$
        DECLARE
          m date;
          created int := 0;
          part_name text;
        BEGIN
          FOR i IN -1..months_ahead LOOP
            m := date_trunc('month', now())::date + make_interval(months => i);
            part_name := parent::text || '_p' || to_char(m, 'YYYYMM');
            IF to_regclass(part_name) IS NULL THEN
              EXECUTE format(
                'CREATE TABLE %s PARTITION OF %s FOR VALUES FROM (%L) TO (%L)',
                part_name, parent, m, m + interval '1 month');
              created := created + 1;
            END IF;
          END LOOP;
          RETURN created;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app.ensure_month_partitions(regclass, int)")
    op.execute("DROP FUNCTION IF EXISTS app.is_super()")
    op.execute("DROP FUNCTION IF EXISTS app.current_team()")
    op.execute("DROP FUNCTION IF EXISTS app.touch_updated_at()")
    op.execute("DROP SCHEMA IF EXISTS app")
    # 角色与扩展是集群级资产，downgrade 不回收
