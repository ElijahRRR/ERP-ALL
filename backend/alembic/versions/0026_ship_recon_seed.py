"""R2-05 增量4：ship_recon 调度种子（order_ack/order_ship verify_pending 对账）。

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-16
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO app.schedule (code, description, cron, config) VALUES
          ('ship_recon',
           '发货/确认命令结果未知对账（渠道订单实况为权威，绝不重发）',
           '*/15 * * * *', '{"batch": 10, "min_age_s": 300, "grace_s": 3600}')
        ON CONFLICT (code) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM app.schedule WHERE code = 'ship_recon'")
