"""channel 域：channel / proxy / store / store_credential / quota_config
/ quota_usage / store_incident + RLS + 种子

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-10

依据 specs/001-domain-model/02-channel.md。
关键约束：代理独占（uq_store_proxy 部分唯一）；凭证/配额无 team_id，RLS 经 store JOIN。
"""

from alembic import op

revision = "0003"
down_revision = "0002"
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

STORE_JOIN_RLS = """
ALTER TABLE app.{t} ENABLE ROW LEVEL SECURITY;
CREATE POLICY {t}_all ON app.{t} FOR ALL
  USING (EXISTS (SELECT 1 FROM app.store s WHERE s.id = store_id
         AND (s.team_id = app.current_team() OR app.is_super())))
  WITH CHECK (EXISTS (SELECT 1 FROM app.store s WHERE s.id = store_id
         AND (s.team_id = app.current_team() OR app.is_super())));
"""

TOUCH = """
CREATE TRIGGER {t}_touch BEFORE UPDATE ON app.{t}
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
"""


def upgrade() -> None:
    # ── channel（全局种子表，应用只读）──
    op.execute(
        """
        CREATE TABLE app.channel (
          id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          code       text NOT NULL UNIQUE,
          name       text NOT NULL,
          adapter    text NOT NULL,
          status     text NOT NULL DEFAULT 'active'
                       CONSTRAINT ck_channel_status CHECK (status IN ('active','disabled')),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        INSERT INTO app.channel (code, name, adapter) VALUES ('walmart_us', 'Walmart US', 'walmart');
        """
    )
    op.execute(TOUCH.format(t="channel"))

    # ── proxy ──
    op.execute(
        """
        CREATE TABLE app.proxy (
          id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id            bigint NOT NULL REFERENCES app.team(id),
          kind               text NOT NULL CONSTRAINT ck_proxy_kind CHECK (kind IN ('socks5','http')),
          host               text NOT NULL,
          port               int NOT NULL CONSTRAINT ck_proxy_port CHECK (port BETWEEN 1 AND 65535),
          username           text,
          password_encrypted bytea,
          vendor             text,
          status             text NOT NULL DEFAULT 'active'
                               CONSTRAINT ck_proxy_status
                               CHECK (status IN ('active','expired','disabled')),
          expires_at         date,
          purchased_at       date,
          monthly_cost       numeric(12,2),
          cost_currency      char(3),
          note               text,
          created_at         timestamptz NOT NULL DEFAULT now(),
          updated_at         timestamptz NOT NULL DEFAULT now(),
          created_by         bigint
        );
        CREATE UNIQUE INDEX uq_proxy ON app.proxy (host, port, COALESCE(username, ''));
        CREATE INDEX ix_proxy_team ON app.proxy (team_id, status);
        CREATE INDEX ix_proxy_expiry ON app.proxy (expires_at) WHERE status = 'active';
        """
    )
    op.execute(TEAM_RLS.format(t="proxy"))
    op.execute(TOUCH.format(t="proxy"))

    # ── store ──
    op.execute(
        """
        CREATE TABLE app.store (
          id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id        bigint NOT NULL REFERENCES app.team(id),
          channel_id     bigint NOT NULL REFERENCES app.channel(id),
          code           text NOT NULL,
          name           text NOT NULL,
          status         text NOT NULL DEFAULT 'active'
                           CONSTRAINT ck_store_status
                           CHECK (status IN ('active','paused','suspended','closed')),
          operating_mode text NOT NULL DEFAULT 'build'
                           CONSTRAINT ck_store_mode
                           CHECK (operating_mode IN ('build','match','mixed')),
          dedup_exempt   boolean NOT NULL DEFAULT false,
          is_test        boolean NOT NULL DEFAULT false,
          proxy_id       bigint REFERENCES app.proxy(id),
          legal_entity   text,
          payment_label  text,
          opened_at      date,
          suspended_at   timestamptz,
          closed_at      timestamptz,
          profile        jsonb NOT NULL DEFAULT '{}',
          notes          text,
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now(),
          created_by     bigint
        );
        CREATE UNIQUE INDEX uq_store ON app.store (channel_id, code);
        CREATE INDEX ix_store_team ON app.store (team_id, status);
        -- 防关联铁律：一个代理出口最多绑一家未关店
        CREATE UNIQUE INDEX uq_store_proxy ON app.store (proxy_id)
          WHERE proxy_id IS NOT NULL AND status <> 'closed';
        """
    )
    op.execute(TEAM_RLS.format(t="store"))
    op.execute(TOUCH.format(t="store"))

    # ── store_credential（加密凭证，1:1）──
    op.execute(
        """
        CREATE TABLE app.store_credential (
          id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          store_id                bigint NOT NULL UNIQUE REFERENCES app.store(id),
          client_id               text NOT NULL,
          client_secret_encrypted bytea NOT NULL,
          created_at              timestamptz NOT NULL DEFAULT now(),
          updated_at              timestamptz NOT NULL DEFAULT now(),
          updated_by              bigint
        );
        """
    )
    op.execute(STORE_JOIN_RLS.format(t="store_credential"))
    op.execute(TOUCH.format(t="store_credential"))

    # ── quota_config / quota_usage ──
    op.execute(
        """
        CREATE TABLE app.quota_config (
          id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          store_id    bigint NOT NULL REFERENCES app.store(id),
          quota_kind  text NOT NULL
                        CONSTRAINT ck_quota_kind
                        CHECK (quota_kind IN ('listing_create','listing_delete','maintenance')),
          daily_limit int NOT NULL CONSTRAINT ck_quota_limit CHECK (daily_limit >= 0),
          enabled     boolean NOT NULL DEFAULT true,
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now(),
          updated_by  bigint,
          CONSTRAINT uq_quota_config UNIQUE (store_id, quota_kind)
        );
        CREATE TABLE app.quota_usage (
          id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          store_id    bigint NOT NULL REFERENCES app.store(id),
          quota_kind  text NOT NULL
                        CONSTRAINT ck_quota_usage_kind
                        CHECK (quota_kind IN ('listing_create','listing_delete','maintenance')),
          window_date date NOT NULL,
          used        int NOT NULL DEFAULT 0,
          updated_at  timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_quota_usage UNIQUE (store_id, quota_kind, window_date)
        );
        """
    )
    op.execute(STORE_JOIN_RLS.format(t="quota_config"))
    op.execute(TOUCH.format(t="quota_config"))
    op.execute(STORE_JOIN_RLS.format(t="quota_usage"))

    # ── store_incident ──
    op.execute(
        """
        CREATE TABLE app.store_incident (
          id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id            bigint NOT NULL REFERENCES app.team(id),
          store_id           bigint NOT NULL REFERENCES app.store(id),
          incident_kind      text NOT NULL
                               CONSTRAINT ck_incident_kind
                               CHECK (incident_kind IN ('suspension','warning','listing_block','other')),
          source             text NOT NULL
                               CONSTRAINT ck_incident_source CHECK (source IN ('mail','manual')),
          mail_message_id    bigint,
          occurred_at        timestamptz NOT NULL,
          reason             text,
          mail_body_snapshot text,
          status             text NOT NULL DEFAULT 'open'
                               CONSTRAINT ck_incident_status
                               CHECK (status IN ('open','observing','appealing','resolved','closed')),
          sku_released_at    timestamptz,
          brand_released_at  timestamptz,
          appeal_notes       text,
          closed_at          timestamptz,
          created_at         timestamptz NOT NULL DEFAULT now(),
          updated_at         timestamptz NOT NULL DEFAULT now(),
          created_by         bigint
        );
        CREATE INDEX ix_incident_team ON app.store_incident (team_id, status);
        CREATE INDEX ix_incident_store ON app.store_incident (store_id, occurred_at DESC);
        """
    )
    op.execute(TEAM_RLS.format(t="store_incident"))
    op.execute(TOUCH.format(t="store_incident"))

    # ── 授权 ──
    op.execute(
        """
        GRANT SELECT ON app.channel TO erp_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.proxy, app.store, app.store_credential,
          app.quota_config, app.quota_usage, app.store_incident TO erp_app;
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO erp_app;
        """
    )


def downgrade() -> None:
    for t in (
        "store_incident",
        "quota_usage",
        "quota_config",
        "store_credential",
        "store",
        "proxy",
        "channel",
    ):
        op.execute(f"DROP TABLE IF EXISTS app.{t} CASCADE")
