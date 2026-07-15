"""R2-05 增量1：订单域建表（specs/001 §07 原样落）+ 依赖件

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-16

- purchaser / channel_order(月分区) / order_line(月分区) / order_check(月分区)
  / procurement_order / shipment —— channel_return/refund_request 随售后单再建。
- automation_policy（09 §automation 三档开关面板；本单消费 flow=order_block）。
- blacklist_address / blacklist_zip（钓鱼黑名单，0008 工厂同构；BR-ORD-005 数据源）。
- sync_state（006 规划最小形态：order_pull 每店 high-water mark，BR-ORD-001）。
- channel_command action 扩 order_ack / order_ship（发货回传走 RS-03b outbox）。
- 订单三分区表：order_date 是外部数据（可达 now-180d）——显式预建 [-7..+3] 月
  + DEFAULT 兜底分区（超范围旧单不炸拉单任务；ensure_month_partitions 继续管未来）。
- schedule 种子 order_pull（15min/店，001/07:45）。
"""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

TOUCH = """
CREATE TRIGGER {t}_touch BEFORE UPDATE ON app.{t}
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
"""

TEAM_RLS = """
ALTER TABLE app.{t} ENABLE ROW LEVEL SECURITY;
CREATE POLICY {t}_sel ON app.{t} FOR SELECT
  USING (team_id = app.current_team() OR app.is_super());
CREATE POLICY {t}_ins ON app.{t} FOR INSERT
  WITH CHECK (team_id = app.current_team() OR app.is_super());
CREATE POLICY {t}_upd ON app.{t} FOR UPDATE
  USING (team_id = app.current_team() OR app.is_super())
  WITH CHECK (team_id = app.current_team() OR app.is_super());
"""

BLACKLIST_RLS = """
ALTER TABLE app.{t} ENABLE ROW LEVEL SECURITY;
CREATE POLICY {t}_sel ON app.{t} FOR SELECT
  USING (team_id IS NULL OR team_id = app.current_team() OR app.is_super());
CREATE POLICY {t}_wr ON app.{t} FOR ALL
  USING (team_id = app.current_team() OR app.is_super())
  WITH CHECK (team_id = app.current_team() OR app.is_super());
"""

# 分区预建：外部 order_date 覆盖 [-7..+3] 月 + DEFAULT 兜底
PARTITIONS = """
DO $$ DECLARE m date; BEGIN
  FOR i IN -7..3 LOOP
    m := date_trunc('month', now())::date + make_interval(months => i);
    EXECUTE format(
      'CREATE TABLE IF NOT EXISTS app.%I PARTITION OF app.{t} FOR VALUES FROM (%L) TO (%L)',
      '{t}_p' || to_char(m, 'YYYYMM'), m, m + interval '1 month');
  END LOOP;
END $$;
CREATE TABLE app.{t}_pdefault PARTITION OF app.{t} DEFAULT;
"""


def _blacklist_table(name: str, subject_cols: str, unique_expr: str) -> str:
    return f"""
    CREATE TABLE app.{name} (
      id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      team_id    bigint REFERENCES app.team(id),
      {subject_cols},
      reason     text,
      source     text NOT NULL DEFAULT 'manual'
                   CONSTRAINT ck_{name}_source
                   CHECK (source IN ('import','manual','tro_sync','trademark_sync')),
      status     text NOT NULL DEFAULT 'active'
                   CONSTRAINT ck_{name}_status CHECK (status IN ('active','removed')),
      added_by   bigint,
      added_at   timestamptz NOT NULL DEFAULT now(),
      removed_at timestamptz
    );
    CREATE UNIQUE INDEX uq_{name}_active ON app.{name} ({unique_expr})
      WHERE status = 'active';
    """


def upgrade() -> None:
    # ── purchaser 采购方（D-Q27/32/50）──
    op.execute(
        """
        CREATE TABLE app.purchaser (
          id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id         bigint NOT NULL REFERENCES app.team(id),
          name            text NOT NULL,
          purchaser_kind  text NOT NULL
                            CONSTRAINT ck_purchaser_kind
                            CHECK (purchaser_kind IN ('internal','external')),
          user_id         bigint REFERENCES app.app_user(id),
          contact         jsonb NOT NULL DEFAULT '{}',
          exchange_rate   numeric(12,6) NOT NULL,
          settle_currency char(3) NOT NULL DEFAULT 'CNY',
          status          text NOT NULL DEFAULT 'active'
                            CONSTRAINT ck_purchaser_status
                            CHECK (status IN ('active','disabled')),
          created_at      timestamptz NOT NULL DEFAULT now(),
          updated_at      timestamptz NOT NULL DEFAULT now(),
          created_by      bigint
        );
        CREATE UNIQUE INDEX uq_purchaser_user ON app.purchaser (user_id)
          WHERE user_id IS NOT NULL;
        """
    )
    op.execute(TOUCH.format(t="purchaser"))
    op.execute(TEAM_RLS.format(t="purchaser"))

    # ── channel_order 渠道订单（月分区 by order_date；永久保留 D-Q18）──
    op.execute(
        """
        CREATE TABLE app.channel_order (
          id               bigint GENERATED ALWAYS AS IDENTITY,
          team_id          bigint NOT NULL,
          store_id         bigint NOT NULL,
          channel_order_no text NOT NULL,
          order_date       timestamptz NOT NULL,
          channel_status   text NOT NULL,
          internal_status  text NOT NULL DEFAULT 'pulled'
                             CONSTRAINT ck_channel_order_internal
                             CHECK (internal_status IN
                             ('pulled','checked','assigned','purchasing','shipped',
                              'completed','cancelled','refunded')),
          customer         jsonb NOT NULL DEFAULT '{}',
          ship_to          jsonb NOT NULL,
          order_total      numeric(12,2) NOT NULL,
          currency         char(3) NOT NULL DEFAULT 'USD',
          item_count       int NOT NULL,
          has_flag         boolean NOT NULL DEFAULT false,
          pulled_at        timestamptz NOT NULL,
          raw_ref          text,
          created_at       timestamptz NOT NULL DEFAULT now(),
          updated_at       timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (id, order_date)
        ) PARTITION BY RANGE (order_date);
        CREATE UNIQUE INDEX uq_channel_order
          ON app.channel_order (store_id, channel_order_no, order_date);
        CREATE INDEX ix_channel_order_status
          ON app.channel_order (team_id, internal_status, order_date DESC);
        CREATE INDEX ix_channel_order_store ON app.channel_order (store_id, order_date DESC);
        CREATE INDEX ix_channel_order_flag ON app.channel_order (has_flag, order_date DESC)
          WHERE has_flag;
        """
    )
    op.execute(PARTITIONS.format(t="channel_order"))
    op.execute(TOUCH.format(t="channel_order"))
    op.execute(TEAM_RLS.format(t="channel_order"))

    # ── order_line 订单行（月分区随单）──
    op.execute(
        """
        CREATE TABLE app.order_line (
          id              bigint GENERATED ALWAYS AS IDENTITY,
          order_id        bigint NOT NULL,
          order_date      timestamptz NOT NULL,
          team_id         bigint NOT NULL,
          channel_line_no text NOT NULL,
          channel_sku     text NOT NULL,
          listing_id      bigint,
          product_id      bigint,
          qty             int NOT NULL,
          unit_price      numeric(12,2) NOT NULL,
          line_status     text NOT NULL
                            CONSTRAINT ck_order_line_status
                            CHECK (line_status IN
                            ('created','acknowledged','shipped','cancelled','refunded')),
          carrier         text,
          tracking_no     text,
          shipped_at      timestamptz,
          created_at      timestamptz NOT NULL DEFAULT now(),
          updated_at      timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (id, order_date)
        ) PARTITION BY RANGE (order_date);
        CREATE UNIQUE INDEX uq_order_line
          ON app.order_line (order_id, channel_line_no, order_date);
        CREATE INDEX ix_order_line_sku ON app.order_line (channel_sku);
        CREATE INDEX ix_order_line_order ON app.order_line (order_id);
        """
    )
    op.execute(PARTITIONS.format(t="order_line"))
    op.execute(TOUCH.format(t="order_line"))
    op.execute(TEAM_RLS.format(t="order_line"))

    # ── order_check 订单四检（月分区，软标记）──
    op.execute(
        """
        CREATE TABLE app.order_check (
          id          bigint GENERATED ALWAYS AS IDENTITY,
          team_id     bigint NOT NULL,
          order_id    bigint NOT NULL,
          order_date  timestamptz NOT NULL,
          check_kind  text NOT NULL
                        CONSTRAINT ck_order_check_kind
                        CHECK (check_kind IN ('phishing','purchaser','price_limit','consistency')),
          result      text NOT NULL
                        CONSTRAINT ck_order_check_result CHECK (result IN ('pass','flagged')),
          detail      jsonb NOT NULL DEFAULT '{}',
          resolved_by bigint,
          resolved_at timestamptz,
          checked_at  timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (id, order_date)
        ) PARTITION BY RANGE (order_date);
        CREATE UNIQUE INDEX uq_order_check
          ON app.order_check (order_id, check_kind, order_date);
        """
    )
    op.execute(PARTITIONS.format(t="order_check"))
    op.execute(TEAM_RLS.format(t="order_check"))

    # ── procurement_order 采购执行单（D-Q50 双入口核心）──
    op.execute(
        """
        CREATE TABLE app.procurement_order (
          id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id              bigint NOT NULL,
          store_id             bigint NOT NULL,
          order_id             bigint NOT NULL,
          order_date           timestamptz NOT NULL,
          status               text NOT NULL DEFAULT 'unassigned'
                                 CONSTRAINT ck_procurement_status
                                 CHECK (status IN
                                 ('unassigned','assigned','claimed','purchased','shipped',
                                  'backfilled','exception','cancelled')),
          assignee_kind        text NOT NULL DEFAULT 'none'
                                 CONSTRAINT ck_procurement_assignee
                                 CHECK (assignee_kind IN ('none','internal','external')),
          purchaser_id         bigint REFERENCES app.purchaser(id),
          assigned_by          bigint,
          assigned_at          timestamptz,
          claimed_at           timestamptz,
          purchase_platform    text,
          purchase_order_ref   text,
          purchase_cost        numeric(12,2),
          purchase_currency    char(3) NOT NULL DEFAULT 'CNY',
          exchange_rate_locked numeric(12,6),
          freight_cost         numeric(12,2),
          carrier              text,
          tracking_no          text,
          purchased_at         timestamptz,
          shipped_at           timestamptz,
          backfilled_at        timestamptz,
          backfill_actor_kind  text
                                 CONSTRAINT ck_procurement_backfill_actor
                                 CHECK (backfill_actor_kind IN ('internal','external','op_direct')),
          backfill_actor_id    bigint,
          exception_reason     text,
          note                 text,
          created_at           timestamptz NOT NULL DEFAULT now(),
          updated_at           timestamptz NOT NULL DEFAULT now(),
          created_by           bigint
        );
        CREATE INDEX ix_procurement_team ON app.procurement_order (team_id, status);
        CREATE INDEX ix_procurement_purchaser ON app.procurement_order (purchaser_id, status);
        CREATE INDEX ix_procurement_order ON app.procurement_order (order_id);
        """
    )
    op.execute(TOUCH.format(t="procurement_order"))
    op.execute(TEAM_RLS.format(t="procurement_order"))

    # ── shipment 发货回传（push 走 RS-03b outbox）──
    op.execute(
        """
        CREATE TABLE app.shipment (
          id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id              bigint NOT NULL,
          store_id             bigint NOT NULL,
          order_id             bigint NOT NULL,
          order_date           timestamptz NOT NULL,
          procurement_order_id bigint REFERENCES app.procurement_order(id),
          lines                jsonb NOT NULL,
          carrier              text NOT NULL,
          tracking_no          text NOT NULL,
          push_status          text NOT NULL DEFAULT 'pending'
                                 CONSTRAINT ck_shipment_push
                                 CHECK (push_status IN ('pending','pushed','failed')),
          pushed_at            timestamptz,
          channel_response     jsonb,
          created_at           timestamptz NOT NULL DEFAULT now(),
          updated_at           timestamptz NOT NULL DEFAULT now(),
          created_by           bigint
        );
        CREATE INDEX ix_shipment_pending ON app.shipment (push_status)
          WHERE push_status = 'pending';
        CREATE INDEX ix_shipment_order ON app.shipment (order_id);
        """
    )
    op.execute(TOUCH.format(t="shipment"))
    op.execute(TEAM_RLS.format(t="shipment"))

    # ── automation_policy 三档开关面板（09 §automation；本单消费 flow=order_block）──
    op.execute(
        """
        CREATE TABLE app.automation_policy (
          id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id    bigint NOT NULL REFERENCES app.team(id),
          flow_code  text NOT NULL,
          mode       text NOT NULL DEFAULT 'manual'
                       CONSTRAINT ck_automation_mode CHECK (mode IN ('manual','semi','auto')),
          config     jsonb NOT NULL DEFAULT '{}',
          enabled    boolean NOT NULL DEFAULT true,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          updated_by bigint
        );
        CREATE UNIQUE INDEX uq_automation_policy ON app.automation_policy (team_id, flow_code);
        """
    )
    op.execute(TOUCH.format(t="automation_policy"))
    op.execute(TEAM_RLS.format(t="automation_policy"))

    # ── 钓鱼黑名单两表（BR-ORD-005 数据源；0008 工厂同构）──
    op.execute(
        _blacklist_table(
            "blacklist_address",
            "street_norm text NOT NULL",
            "COALESCE(team_id, 0), street_norm",
        )
    )
    op.execute(
        _blacklist_table("blacklist_zip", "zip5 text NOT NULL", "COALESCE(team_id, 0), zip5")
    )
    for t in ("blacklist_address", "blacklist_zip"):
        op.execute(BLACKLIST_RLS.format(t=t))

    # ── sync_state（006 规划最小形态：拉取域 high-water mark；仅系统路径读写）──
    op.execute(
        """
        CREATE TABLE app.sync_state (
          scope        text NOT NULL,
          ref_id       bigint NOT NULL,
          last_sync_at timestamptz,
          stats        jsonb NOT NULL DEFAULT '{}',
          updated_at   timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (scope, ref_id)
        );
        ALTER TABLE app.sync_state ENABLE ROW LEVEL SECURITY;
        CREATE POLICY sync_state_all ON app.sync_state FOR ALL
          USING (app.is_super()) WITH CHECK (app.is_super());
        """
    )

    # ── outbox action 扩展（发货回传：order_ack / order_ship）──
    op.execute(
        """
        ALTER TABLE app.channel_command DROP CONSTRAINT ck_cc_action;
        ALTER TABLE app.channel_command ADD CONSTRAINT ck_cc_action
          CHECK (action IN ('feed_submit','item_retire','order_ack','order_ship'));
        """
    )

    # ── 权限（最小面：无 DELETE）+ 调度种子 ──
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE ON app.purchaser, app.channel_order, app.order_line,
          app.order_check, app.procurement_order, app.shipment, app.automation_policy,
          app.blacklist_address, app.blacklist_zip, app.sync_state TO erp_app;
        """
    )
    op.execute(
        """
        INSERT INTO app.schedule (code, description, cron, config) VALUES
          ('order_pull',
           '渠道订单增量拉取（lastModified 窗口 + upsert 幂等 + 四检随拉；BR-ORD-001~004）',
           '*/15 * * * *',
           '{"overlap_hours": 1, "initial_days": 30, "created_floor_days": 179,
             "page_limit": 200, "max_pages": 30}')
        ON CONFLICT (code) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM app.schedule WHERE code = 'order_pull'")
    op.execute(
        """
        DELETE FROM app.channel_command WHERE action IN ('order_ack','order_ship');
        ALTER TABLE app.channel_command DROP CONSTRAINT ck_cc_action;
        ALTER TABLE app.channel_command ADD CONSTRAINT ck_cc_action
          CHECK (action IN ('feed_submit','item_retire'));
        """
    )
    for t in (
        "sync_state", "blacklist_zip", "blacklist_address", "automation_policy",
        "shipment", "procurement_order", "order_check", "order_line", "channel_order",
        "purchaser",
    ):  # fmt: skip
        op.execute(f"DROP TABLE IF EXISTS app.{t} CASCADE")
