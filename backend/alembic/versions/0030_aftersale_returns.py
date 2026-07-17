"""R2-07 增量1：售后域退货三表 + 依赖件

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-16

- channel_return（specs/001 §07 冻结头表，量小不分区、永久保留 D-Q18）
  + channel_return_line（行表，C6/BR-AS-003/004/006：行级状态、upsert by (RMA, line)）
  + channel_return_event（行级状态变更历史——旧系统覆盖式写入丢历史是已知缺陷，
    新系统按 C6 保留快照；07 文档随本单注记）。
- 权限点 aftersale.read（0002 未预埋售后域）：种入 permission 并挂到
  订单员/财务/团队管理员——模板角色与既有团队的同名复制角色一并补挂。
- schedule 种子 return_pull（BR-SCH-002 旧系统节律 08:00/日；无时间过滤只能全量，BR-AS-001）。
"""

from alembic import op

revision = "0030"
down_revision = "0029"
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
    # ── channel_return 渠道退货头（RMA 级；07 冻结列 + 渠道实况扩展列）──
    op.execute(
        """
        CREATE TABLE app.channel_return (
          id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id           bigint NOT NULL,
          store_id          bigint NOT NULL,
          channel_return_no text NOT NULL,
          customer_order_no text,
          order_id          bigint,
          order_date        timestamptz,
          return_date       timestamptz NOT NULL,
          return_by_date    timestamptz,
          reason            text,
          channel_status    text NOT NULL,
          internal_status   text NOT NULL DEFAULT 'pulled'
                              CONSTRAINT ck_channel_return_internal
                              CHECK (internal_status IN ('pulled','reviewed','closed')),
          refund_mode       text,
          qty               int NOT NULL,
          refund_amount     numeric(12,2),
          currency          char(3) NOT NULL DEFAULT 'USD',
          customer          jsonb NOT NULL DEFAULT '{}',
          pulled_at         timestamptz NOT NULL,
          raw_ref           text,
          created_at        timestamptz NOT NULL DEFAULT now(),
          updated_at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX uq_channel_return
          ON app.channel_return (store_id, channel_return_no);
        CREATE INDEX ix_channel_return_status
          ON app.channel_return (team_id, internal_status, return_date DESC);
        CREATE INDEX ix_channel_return_store
          ON app.channel_return (store_id, return_date DESC);
        CREATE INDEX ix_channel_return_order ON app.channel_return (order_id)
          WHERE order_id IS NOT NULL;
        """
    )
    op.execute(TOUCH.format(t="channel_return"))
    op.execute(TEAM_RLS.format(t="channel_return"))

    # ── channel_return_line 退货行（一行 = returnOrderLine；状态三字段行级 BR-AS-004）──
    op.execute(
        """
        CREATE TABLE app.channel_return_line (
          id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          return_id          bigint NOT NULL REFERENCES app.channel_return(id),
          team_id            bigint NOT NULL,
          line_no            text NOT NULL,
          channel_sku        text NOT NULL,
          product_name       text,
          condition          text,
          qty                int NOT NULL,
          refunded_qty       int NOT NULL DEFAULT 0,
          unit_price         numeric(12,2),
          line_status        text NOT NULL,
          refund_status      text,
          delivery_status    text,
          return_method      text,
          return_reason      text,
          return_description text,
          status_time        timestamptz,
          purchase_order_no  text,
          carrier            text,
          tracking_no        text,
          created_at         timestamptz NOT NULL DEFAULT now(),
          updated_at         timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX uq_channel_return_line
          ON app.channel_return_line (return_id, line_no);
        CREATE INDEX ix_channel_return_line_sku ON app.channel_return_line (channel_sku);
        """
    )
    op.execute(TOUCH.format(t="channel_return_line"))
    op.execute(TEAM_RLS.format(t="channel_return_line"))

    # ── channel_return_event 行级变更历史（C6：upsert + 保留历史快照）──
    op.execute(
        """
        CREATE TABLE app.channel_return_event (
          id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id     bigint NOT NULL,
          return_id   bigint NOT NULL REFERENCES app.channel_return(id),
          line_no     text NOT NULL,
          changes     jsonb NOT NULL,
          observed_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_channel_return_event ON app.channel_return_event (return_id, observed_at);
        """
    )
    op.execute(TEAM_RLS.format(t="channel_return_event"))

    # ── 权限（最小面：无 DELETE）──
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE ON app.channel_return, app.channel_return_line,
          app.channel_return_event TO erp_app;
        """
    )

    # ── 权限点 aftersale.read：0002 未预埋——补种并挂模板角色 + 既有团队同名角色 ──
    op.execute(
        """
        INSERT INTO app.permission (code, module, name)
        VALUES ('aftersale.read', 'aftersale', '售后查看')
        ON CONFLICT (code) DO NOTHING;
        INSERT INTO app.role_permission (role_id, permission_code)
        SELECT r.id, 'aftersale.read' FROM app.role r
        WHERE r.name IN ('订单员', '财务', '团队管理员')
          AND NOT EXISTS (SELECT 1 FROM app.role_permission rp
                          WHERE rp.role_id = r.id AND rp.permission_code = 'aftersale.read');
        """
    )

    # ── 调度种子（BR-AS-001 全量拉取；BR-SCH-002 旧系统 08:00/日节律）──
    op.execute(
        """
        INSERT INTO app.schedule (code, description, cron, config) VALUES
          ('return_pull',
           '渠道退货全量拉取（无时间过滤 limit=200 翻页 + 行级 upsert + 变更留痕；BR-AS-001~007）',
           '0 8 * * *',
           '{"page_limit": 200, "max_pages": 50, "replacement_info": true,
             "drop_alert_ratio": 0.5}')
        ON CONFLICT (code) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM app.schedule WHERE code = 'return_pull'")
    op.execute("DELETE FROM app.role_permission WHERE permission_code = 'aftersale.read'")
    op.execute("DELETE FROM app.permission WHERE code = 'aftersale.read'")
    for t in ("channel_return_event", "channel_return_line", "channel_return"):
        op.execute(f"DROP TABLE IF EXISTS app.{t} CASCADE")
