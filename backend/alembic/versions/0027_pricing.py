"""R2-06 增量1：定价域建表（specs/001 §06:163-196 原样落）+ outbox 扩展

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-16

- pricing_strategy（D-Q23 策略注册表）：build/match 双模式各配一套；
  store_id NULL=团队默认、非空=店铺覆盖，解析顺序 store 级 > team 级；
  (team_id, COALESCE(store_id,0), offer_mode) 活跃唯一（001/06:178 原文）。
- algo_code 本期只入 cost_plus / manual（005:56 圈定；D-Q23 match 现行=人工指定价）；
  follow_buybox 为图纸「未来扩展」预留名——零实现不入枚举，加了没有实现反而危险，
  接入时随实现扩 CHECK（同 ck_cc_action 扩展模式）。
- price_history（月分区 by created_at）：detail 记策略引擎计算明细（可追溯，
  001/06:179 契约——输入成本/竞争价/参数 → 输出价格+明细）。与图纸记名差异
  （R2-06 考古口径，随单冻结）：occurred_at→created_at（写入即事件时间，不二列）、
  actor_type/actor_id→created_by（同 0025 房例）、currency 不落列
  （币种权威在 app.listing.currency，全链 USD）。
- channel_command action 扩 'price_push'（价格同步走 RS-03b outbox 三段式）。
- 调度种子不在本单（价格同步管道随 R2-06 增量3 接线时再种）。
"""

from alembic import op

revision = "0027"
down_revision = "0026"
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


def upgrade() -> None:
    # ── pricing_strategy 定价策略注册表（D-Q23）──
    op.execute(
        """
        CREATE TABLE app.pricing_strategy (
          id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id    bigint NOT NULL REFERENCES app.team(id),
          store_id   bigint REFERENCES app.store(id),
          offer_mode text NOT NULL
                       CONSTRAINT ck_pricing_strategy_mode
                       CHECK (offer_mode IN ('build','match')),
          name       text NOT NULL,
          -- follow_buybox 预留（图纸「未来扩展」）：本期无实现，不入枚举
          algo_code  text NOT NULL
                       CONSTRAINT ck_pricing_strategy_algo
                       CHECK (algo_code IN ('cost_plus','manual')),
          params     jsonb NOT NULL DEFAULT '{}',
          status     text NOT NULL DEFAULT 'active'
                       CONSTRAINT ck_pricing_strategy_status
                       CHECK (status IN ('active','disabled')),
          version    int NOT NULL DEFAULT 1,
          created_by bigint,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX uq_pricing_strategy
          ON app.pricing_strategy (team_id, COALESCE(store_id, 0), offer_mode)
          WHERE status = 'active';
        """
    )
    op.execute(TOUCH.format(t="pricing_strategy"))
    op.execute(TEAM_RLS.format(t="pricing_strategy"))

    # ── price_history 价格变更史（月分区 by created_at；append-only 无 TOUCH）──
    op.execute(
        """
        CREATE TABLE app.price_history (
          id               bigint GENERATED ALWAYS AS IDENTITY,
          listing_id       bigint NOT NULL,
          team_id          bigint NOT NULL,
          old_price        numeric(12,2),
          new_price        numeric(12,2) NOT NULL,
          reason           text NOT NULL
                             CONSTRAINT ck_price_history_reason
                             CHECK (reason IN ('strategy','manual','watchdog','initial')),
          strategy_id      bigint,
          strategy_version int,
          detail           jsonb NOT NULL DEFAULT '{}',
          created_by       bigint,
          created_at       timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at);
        CREATE INDEX ix_price_history_listing
          ON app.price_history (listing_id, created_at DESC);
        SELECT app.ensure_month_partitions('app.price_history');
        """
    )
    op.execute(TEAM_RLS.format(t="price_history"))

    # ── outbox action 扩展（价格同步：price_push）──
    op.execute(
        """
        ALTER TABLE app.channel_command DROP CONSTRAINT ck_cc_action;
        ALTER TABLE app.channel_command ADD CONSTRAINT ck_cc_action
          CHECK (action IN ('feed_submit','item_retire','order_ack','order_ship',
                            'price_push'));
        """
    )

    # ── 权限（最小面：无 DELETE）──
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE ON app.pricing_strategy, app.price_history TO erp_app;
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO erp_app;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM app.channel_command WHERE action = 'price_push';
        ALTER TABLE app.channel_command DROP CONSTRAINT ck_cc_action;
        ALTER TABLE app.channel_command ADD CONSTRAINT ck_cc_action
          CHECK (action IN ('feed_submit','item_retire','order_ack','order_ship'));
        """
    )
    for t in ("price_history", "pricing_strategy"):
        op.execute(f"DROP TABLE IF EXISTS app.{t} CASCADE")
