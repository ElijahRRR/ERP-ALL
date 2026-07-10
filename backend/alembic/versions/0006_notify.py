"""notify 域：notification(月分区) / notification_target / notification_receipt

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-10

依据 specs/001-domain-model/09-platform.md §notify。
- target/receipt 不分区（量级=通知数×常数），保留 created_at 冗余便于随主表清理；
  按 FK 纪律不建指向分区表的外键。
- dedupe 抑制（同 dedupe_key 24h 一条）在服务层判定（notify service），
  分区表上的条件唯一无法完整表达。
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.notification (
          id          bigint GENERATED ALWAYS AS IDENTITY,
          team_id     bigint,
          severity    text NOT NULL
                        CONSTRAINT ck_notification_severity
                        CHECK (severity IN ('info','warn','critical')),
          category    text NOT NULL,
          title       text NOT NULL,
          body        text,
          object_type text,
          object_id   text,
          dedupe_key  text,
          created_at  timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at);
        CREATE INDEX ix_notification_team ON app.notification (team_id, created_at DESC);
        CREATE INDEX ix_notification_dedupe ON app.notification (dedupe_key, created_at)
          WHERE dedupe_key IS NOT NULL;
        SELECT app.ensure_month_partitions('app.notification');
        ALTER TABLE app.notification ENABLE ROW LEVEL SECURITY;
        CREATE POLICY notification_sel ON app.notification FOR SELECT
          USING (team_id IS NULL OR team_id = app.current_team() OR app.is_super());
        CREATE POLICY notification_ins ON app.notification FOR INSERT WITH CHECK (true);
        """
    )

    op.execute(
        """
        CREATE TABLE app.notification_target (
          notification_id bigint NOT NULL,
          created_at      timestamptz NOT NULL,
          target_kind     text NOT NULL
                            CONSTRAINT ck_target_kind
                            CHECK (target_kind IN ('team','role','user')),
          target_id       bigint NOT NULL,
          PRIMARY KEY (notification_id, target_kind, target_id)
        );
        ALTER TABLE app.notification_target ENABLE ROW LEVEL SECURITY;
        CREATE POLICY notification_target_all ON app.notification_target FOR ALL
          USING (true) WITH CHECK (true);
        """
    )

    op.execute(
        """
        CREATE TABLE app.notification_receipt (
          notification_id bigint NOT NULL,
          user_id         bigint NOT NULL,
          read_at         timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (notification_id, user_id)
        );
        ALTER TABLE app.notification_receipt ENABLE ROW LEVEL SECURITY;
        CREATE POLICY notification_receipt_all ON app.notification_receipt FOR ALL
          USING (EXISTS (SELECT 1 FROM app.app_user u WHERE u.id = user_id
                 AND (u.team_id = app.current_team() OR app.is_super())))
          WITH CHECK (EXISTS (SELECT 1 FROM app.app_user u WHERE u.id = user_id
                 AND (u.team_id = app.current_team() OR app.is_super())));
        """
    )

    op.execute(
        """
        GRANT SELECT, INSERT ON app.notification, app.notification_target TO erp_app;
        GRANT SELECT, INSERT, UPDATE ON app.notification_receipt TO erp_app;
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO erp_app;
        """
    )


def downgrade() -> None:
    for t in ("notification_receipt", "notification_target", "notification"):
        op.execute(f"DROP TABLE IF EXISTS app.{t} CASCADE")
