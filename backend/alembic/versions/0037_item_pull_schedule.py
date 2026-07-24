"""R2-12 增量4a（全店对账+报错回收，D-Q65②）：item_pull / maintenance_run 调度种子。

- item_pull：每日全店 GET /v3/items 逐态扫描对账（三类差异发现 + 13 类归类 +
  维护任务生成 + error_recycle pending 候选断言）。
- maintenance_run：runner 最小档，每小时认领执行 kinds 配置内的到期维护任务
  （默认仅 delist；end_date_renewal 待增量4b 接 MP_MAINTENANCE 通道后加入）。
节奏与参数均可运营改（schedule.cron / schedule.config），零硬编码。

Revision ID: 0037
Revises: 0036
Create Date: 2026-07-24
"""

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO app.schedule (code, description, cron, config) VALUES
          ('item_pull',
           '全店后台 SKU 拉取对账（D-Q65②）：三类差异发现+归类+维护任务+候选断言，只发现不执行',
           '0 9 * * *',
           '{"statuses": ["PUBLISHED", "UNPUBLISHED", "SYSTEM_PROBLEM"],
             "page_limit": 200, "max_pages": 100}'),
          ('maintenance_run',
           'maintenance_task runner 最小档：认领执行到期维护任务。kinds 三档开关（D-Q13/29）：'
           '[]=人工档（默认，只积累不执行）；["delist"]=半自动档（运营显式开启）',
           '25 * * * *', '{"batch": 5, "kinds": []}')
        ON CONFLICT (code) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM app.schedule WHERE code IN ('item_pull', 'maintenance_run')")
