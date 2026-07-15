"""RS-03b：channel 写路径 transactional outbox + API 幂等消费存储

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-15

依据 external-review-round-1.md §A7 处方 + .agent/evidence/RS-03b/archaeology.md §2.1：
- channel_command：短事务落命令（UNIQUE(team_id,action,idempotency_key) + payload_hash），
  worker claim（fence+lease）后事务外调渠道，回写受 fence+status 双重校验；
  同店 FIFO（更早未终局命令挡道，verify_pending 背压=fail-closed）。
- api_idempotency：契约 002 §写操作幂等（Idempotency-Key 24h 去重）的消费存储，
  先占位后执行，响应回填供重放。
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

TEAM_RLS = """
ALTER TABLE app.{t} ENABLE ROW LEVEL SECURITY;
CREATE POLICY {t}_sel ON app.{t} FOR SELECT
  USING (team_id = app.current_team() OR app.is_super());
CREATE POLICY {t}_ins ON app.{t} FOR INSERT
  WITH CHECK (team_id = app.current_team() OR app.is_super());
CREATE POLICY {t}_upd ON app.{t} FOR UPDATE
  USING (team_id = app.current_team() OR app.is_super())
  WITH CHECK (team_id = app.current_team() OR app.is_super());
CREATE POLICY {t}_del ON app.{t} FOR DELETE
  USING (team_id = app.current_team() OR app.is_super());
"""

TOUCH = """
CREATE TRIGGER {t}_touch BEFORE UPDATE ON app.{t}
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
"""


def upgrade() -> None:
    # ── channel_command（transactional outbox）──
    op.execute(
        """
        CREATE TABLE app.channel_command (
          id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id          bigint NOT NULL REFERENCES app.team(id),
          store_id         bigint NOT NULL REFERENCES app.store(id),
          action           text NOT NULL
                             CONSTRAINT ck_cc_action CHECK (action IN
                             ('feed_submit','item_retire')),
          object_type      text,
          object_id        bigint,
          idempotency_key  text NOT NULL,
          payload          jsonb NOT NULL,
          payload_hash     text NOT NULL,
          status           text NOT NULL DEFAULT 'pending'
                             CONSTRAINT ck_cc_status CHECK (status IN
                             ('pending','inflight','succeeded','failed','verify_pending')),
          fence            int NOT NULL DEFAULT 0,
          attempts         int NOT NULL DEFAULT 0,
          lease_expires_at timestamptz,
          result           jsonb,
          error_code       text,
          claimed_at       timestamptz,
          completed_at     timestamptz,
          created_at       timestamptz NOT NULL DEFAULT now(),
          updated_at       timestamptz NOT NULL DEFAULT now(),
          created_by       bigint,
          CONSTRAINT uq_channel_command_idem UNIQUE (team_id, action, idempotency_key)
        );
        -- 同店 FIFO 车道判定 + 领取扫描
        CREATE INDEX ix_cc_lane ON app.channel_command (store_id, id)
          WHERE status IN ('pending','inflight','verify_pending');
        CREATE INDEX ix_cc_object ON app.channel_command (object_type, object_id);
        """
    )
    op.execute(TOUCH.format(t="channel_command"))
    op.execute(TEAM_RLS.format(t="channel_command"))

    # ── api_idempotency（Idempotency-Key 消费存储，24h 惰性清理）──
    op.execute(
        """
        CREATE TABLE app.api_idempotency (
          id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id      bigint NOT NULL REFERENCES app.team(id),
          endpoint     text NOT NULL,
          idem_key     text NOT NULL,
          payload_hash text NOT NULL,
          status_code  int,
          response     jsonb,
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now(),
          created_by   bigint,
          CONSTRAINT uq_api_idem UNIQUE (team_id, endpoint, idem_key)
        );
        CREATE INDEX ix_api_idem_age ON app.api_idempotency (created_at);
        """
    )
    op.execute(TOUCH.format(t="api_idempotency"))
    op.execute(TEAM_RLS.format(t="api_idempotency"))

    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE ON app.channel_command TO erp_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.api_idempotency TO erp_app;
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO erp_app;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.api_idempotency CASCADE")
    op.execute("DROP TABLE IF EXISTS app.channel_command CASCADE")
