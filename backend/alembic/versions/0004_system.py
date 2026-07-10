"""system 域：sys_dict / system_config / team_config / schedule / task_run(月分区)

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-10

依据 specs/001-domain-model/09-platform.md §system/automation。
schedule/task_run 先建表（R1-06 beat 启用）；notification 三表随 R1-06 交付。
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.sys_dict (
          id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          dict_type  text NOT NULL,
          code       text NOT NULL,
          label      text NOT NULL,
          sort       smallint NOT NULL DEFAULT 0,
          enabled    boolean NOT NULL DEFAULT true,
          meta       jsonb NOT NULL DEFAULT '{}',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          updated_by bigint,
          CONSTRAINT uq_sys_dict UNIQUE (dict_type, code)
        );
        CREATE TRIGGER sys_dict_touch BEFORE UPDATE ON app.sys_dict
          FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
        """
    )

    op.execute(
        """
        CREATE TABLE app.system_config (
          key         text PRIMARY KEY,
          value       jsonb NOT NULL,
          description text,
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now(),
          updated_by  bigint
        );
        CREATE TRIGGER system_config_touch BEFORE UPDATE ON app.system_config
          FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
        """
    )

    op.execute(
        """
        CREATE TABLE app.team_config (
          team_id    bigint NOT NULL REFERENCES app.team(id),
          key        text NOT NULL,
          value      jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          updated_by bigint,
          PRIMARY KEY (team_id, key)
        );
        CREATE TRIGGER team_config_touch BEFORE UPDATE ON app.team_config
          FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
        ALTER TABLE app.team_config ENABLE ROW LEVEL SECURITY;
        CREATE POLICY team_config_all ON app.team_config FOR ALL
          USING (team_id = app.current_team() OR app.is_super())
          WITH CHECK (team_id = app.current_team() OR app.is_super());
        """
    )

    op.execute(
        """
        CREATE TABLE app.schedule (
          id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          code        text NOT NULL UNIQUE,
          description text NOT NULL,
          cron        text NOT NULL,
          timezone    text NOT NULL DEFAULT 'Asia/Shanghai',
          enabled     boolean NOT NULL DEFAULT true,
          config      jsonb NOT NULL DEFAULT '{}',
          last_run_at timestamptz,
          next_run_at timestamptz,
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now(),
          updated_by  bigint
        );
        CREATE TRIGGER schedule_touch BEFORE UPDATE ON app.schedule
          FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
        -- 首个调度种子：分区自动维护（beat 上线即接管；在此登记保证不被遗忘）
        INSERT INTO app.schedule (code, description, cron)
        VALUES ('partition_maintain', '为分区表预建未来 3 个月分区', '0 3 1 * *');
        """
    )

    op.execute(
        """
        CREATE TABLE app.task_run (
          id          bigint GENERATED ALWAYS AS IDENTITY,
          task_code   text NOT NULL,
          schedule_id bigint,
          team_id     bigint,
          status      text NOT NULL
                        CONSTRAINT ck_task_run_status
                        CHECK (status IN ('running','done','failed')),
          started_at  timestamptz NOT NULL DEFAULT now(),
          finished_at timestamptz,
          stats       jsonb NOT NULL DEFAULT '{}',
          error       text,
          PRIMARY KEY (id, started_at)
        ) PARTITION BY RANGE (started_at);
        CREATE INDEX ix_task_run_code ON app.task_run (task_code, started_at DESC);
        SELECT app.ensure_month_partitions('app.task_run');
        ALTER TABLE app.task_run ENABLE ROW LEVEL SECURITY;
        CREATE POLICY task_run_sel ON app.task_run FOR SELECT
          USING (team_id IS NULL OR team_id = app.current_team() OR app.is_super());
        CREATE POLICY task_run_ins ON app.task_run FOR INSERT WITH CHECK (true);
        CREATE POLICY task_run_upd ON app.task_run FOR UPDATE USING (true) WITH CHECK (true);
        """
    )

    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE ON app.sys_dict, app.system_config, app.schedule TO erp_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.team_config TO erp_app;
        GRANT SELECT, INSERT, UPDATE ON app.task_run TO erp_app;
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO erp_app;
        """
    )


def downgrade() -> None:
    for t in ("task_run", "schedule", "team_config", "system_config", "sys_dict"):
        op.execute(f"DROP TABLE IF EXISTS app.{t} CASCADE")
