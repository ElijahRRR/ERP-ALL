"""R2-06 增量3：price_recon 调度种子（price_push verify_pending 对账）。

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-16
"""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO app.schedule (code, description, cron, config) VALUES
          ('price_recon',
           '改价命令结果未知对账（渠道商品实价为权威，绝不重发）',
           '*/15 * * * *', '{"batch": 10, "min_age_s": 300, "grace_s": 3600}')
        ON CONFLICT (code) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM app.schedule WHERE code = 'price_recon'")
