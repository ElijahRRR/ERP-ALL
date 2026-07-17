"""R2-07 增量2（07a 收尾）：refund_request 表落地 + 依赖件

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-17

- refund_request（specs/001 §07 冻结图纸原样落；D-Q29 三档）：本单只开
  record/approval 两档本地闭环；auto 档在 R2-09 flow=refund 接线前 fail-closed
  拒绝（服务层闸），渠道退款执行不在本单。
- 权限点 refund.request / refund.approve（0002 未预埋）：订单员挂 request，
  团队管理员挂两点——模板角色与既有团队同名角色一并补挂（同 0030 模式）。
- sys_dict 种子 dict_type=refund_reason（图纸：reason_code 走字典，运营可维护）。
"""

from alembic import op

revision = "0031"
down_revision = "0030"
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
    # ── refund_request 退款/取消申请（三档，D-Q29；001 §07 图纸原样）──
    op.execute(
        """
        CREATE TABLE app.refund_request (
          id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id           bigint NOT NULL,
          store_id          bigint NOT NULL,
          order_id          bigint NOT NULL,
          order_date        timestamptz NOT NULL,
          kind              text NOT NULL
                              CONSTRAINT ck_refund_request_kind
                              CHECK (kind IN ('cancel','refund')),
          lines             jsonb NOT NULL DEFAULT '[]',
          amount            numeric(12,2) NOT NULL,
          currency          char(3) NOT NULL DEFAULT 'USD',
          reason_code       text NOT NULL,
          reason_text       text,
          mode_applied      text NOT NULL
                              CONSTRAINT ck_refund_request_mode
                              CHECK (mode_applied IN ('record','approval','auto')),
          status            text NOT NULL DEFAULT 'recorded'
                              CONSTRAINT ck_refund_request_status
                              CHECK (status IN ('recorded','pending_approval','approved',
                                                'rejected','executing','executed','failed')),
          requested_by_kind text NOT NULL
                              CONSTRAINT ck_refund_request_by_kind
                              CHECK (requested_by_kind IN ('user','system')),
          requested_by      bigint NOT NULL,
          approved_by       bigint,
          decided_at        timestamptz,
          executed_at       timestamptz,
          channel_ref       text,
          created_at        timestamptz NOT NULL DEFAULT now(),
          updated_at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_refund_request_status ON app.refund_request (team_id, status);
        CREATE INDEX ix_refund_request_order ON app.refund_request (order_id);
        """
    )
    op.execute(TOUCH.format(t="refund_request"))
    op.execute(TEAM_RLS.format(t="refund_request"))
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.refund_request TO erp_app;")

    # ── 权限点（0002 未预埋售后写面）──
    op.execute(
        """
        INSERT INTO app.permission (code, module, name) VALUES
          ('refund.request', 'aftersale', '退款/取消申请'),
          ('refund.approve', 'aftersale', '退款/取消审批')
        ON CONFLICT (code) DO NOTHING;
        INSERT INTO app.role_permission (role_id, permission_code)
        SELECT r.id, p.code FROM app.role r
        JOIN (VALUES ('订单员', 'refund.request'),
                     ('团队管理员', 'refund.request'),
                     ('团队管理员', 'refund.approve')) AS p(role_name, code)
          ON r.name = p.role_name
        WHERE NOT EXISTS (SELECT 1 FROM app.role_permission rp
                          WHERE rp.role_id = r.id AND rp.permission_code = p.code);
        """
    )

    # ── reason_code 字典（图纸：sys_dict(refund_reason)，运营可维护）──
    op.execute(
        """
        INSERT INTO app.sys_dict (dict_type, code, label, sort) VALUES
          ('refund_reason', 'customer_request', '客户要求', 10),
          ('refund_reason', 'item_not_received', '未收到商品', 20),
          ('refund_reason', 'damaged_item', '商品损坏', 30),
          ('refund_reason', 'wrong_item', '发错商品', 40),
          ('refund_reason', 'quality_issue', '质量问题', 50),
          ('refund_reason', 'price_adjust', '价格调整', 60),
          ('refund_reason', 'other', '其他', 90)
        ON CONFLICT (dict_type, code) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM app.sys_dict WHERE dict_type = 'refund_reason'")
    op.execute(
        "DELETE FROM app.role_permission"
        " WHERE permission_code IN ('refund.request','refund.approve')"
    )
    op.execute("DELETE FROM app.permission WHERE code IN ('refund.request','refund.approve')")
    op.execute("DROP TABLE IF EXISTS app.refund_request CASCADE")
