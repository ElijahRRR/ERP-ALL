"""R2-04 beat 底座增量2：渠道任务调度种子四条。

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-15

节奏/批量/滞留门槛全部在 schedule.cron / schedule.config，运营可调（零硬编码）。
速率语义：轮询与对账均走网关限流闸（GET /v3/feeds 共享 5000/min、GET /v3/items
300/min），beat 批量上限只是软节流。
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO app.schedule (code, description, cron, config) VALUES
          ('feed_poll',
           'feed 自动轮询（submitted/processing → item 级权威回写；R2-04 验收①）',
           '*/2 * * * *', '{"batch": 20, "min_interval_s": 300, "stuck_alert_attempts": 48}'),
          ('feed_verify_back',
           'verify_pending feed 周期对账（adopt-or-lost，绝不重发）',
           '*/10 * * * *', '{"batch": 10, "min_age_s": 600}'),
          ('channel_outbox_drain',
           'outbox 排空（崩溃遗留 pending 补执行 + 过期 inflight 懒清扫）',
           '*/5 * * * *', '{"limit": 100}'),
          ('retire_recon',
           'item_retire verify_pending 渠道对账（商品实况为权威，绝不重发）',
           '*/15 * * * *', '{"batch": 10, "min_age_s": 300, "grace_s": 3600}')
        ON CONFLICT (code) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM app.schedule WHERE code IN
          ('feed_poll', 'feed_verify_back', 'channel_outbox_drain', 'retire_recon');
        """
    )
