"""R2-04 beat 底座增量3：告警任务调度种子两条。

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-15

- gtin_watermark：D-Q39/03-catalog 水位告警（阈值 team_config gtin.warn_pct /
  gtin.critical_pct，默认 15/5，任务 config 可改兜底值）。
- llm_budget_check：05-audit 预算闸告警版，每小时聚合（事前原子预留=RS-08 另单）。
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO app.schedule (code, description, cron, config) VALUES
          ('gtin_watermark',
           'GTIN 池水位告警（free 占比低于阈值 → notification，只告警不动池）',
           '15 */6 * * *', '{"warn_pct": 15, "critical_pct": 5}'),
          ('llm_budget_check',
           'LLM 日预算超限告警（聚合 llm_usage_log vs team_config llm_budget_daily_usd）',
           '5 * * * *', '{"timezone": "Asia/Shanghai"}')
        ON CONFLICT (code) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM app.schedule WHERE code IN ('gtin_watermark', 'llm_budget_check');")
