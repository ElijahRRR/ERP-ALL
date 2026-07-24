"""R2-12 增量3（USPTO 链，D-Q65①）：trademark_freshness 调度种子。

USPTO 供给整链驻部署机（旧仓 daily_update → 日增量 csv → bulk_import_trademark），
新系统侧只保留新鲜度守卫：beat 每日检查 refdata.trademark max(filed_date) 滞后，
超阈值 notification 告警（阈值可运营改：schedule.config 或 system_config
trademark.freshness_warn_days / freshness_critical_days，代码默认 7/14 天）。

Revision ID: 0036
Revises: 0035
Create Date: 2026-07-24
"""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO app.schedule (code, description, cron, config) VALUES
          ('trademark_freshness',
           '商标库新鲜度守卫（D-Q65①）：max(filed_date) 滞后超阈值告警，只告警不动数据',
           '0 10 * * *', '{"warn_days": 7, "critical_days": 14}')
        ON CONFLICT (code) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM app.schedule WHERE code = 'trademark_freshness'")
