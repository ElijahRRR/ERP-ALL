"""R2-04 beat 底座增量1：调度种子（低风险任务三件；partition_maintain 0004 已种）
+ ensure_month_partitions 提权供 beat（erp_app 连接）调用。

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-15

- 分区父表属主为 migrator 角色，erp_app 直调 ensure_month_partitions 会因
  CREATE TABLE PARTITION OF 需要父表属主权限而失败——改 SECURITY DEFINER
  （函数属主=migrator，仅建分区不触数据，无 RLS 绕越面）并钉死 search_path。
- 种子节奏与阈值均可运营改（schedule.cron / schedule.config），非硬编码。
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        -- app 必须列首：无 schema 限定的 CREATE TABLE 以 search_path 首位为落点
        -- （pg_catalog 无论列序总是先于其余项被查找，不需要显式置前）
        ALTER FUNCTION app.ensure_month_partitions(regclass, int)
          SECURITY DEFINER SET search_path = app, pg_catalog;
        """
    )
    op.execute(
        """
        INSERT INTO app.schedule (code, description, cron, config) VALUES
          ('scrape_reclaim',
           '采集断连回收兜底：心跳超时节点下线 + 超时任务归还（阈值走 scrape.* 配置）',
           '* * * * *', '{}'),
          ('api_idempotency_sweep',
           'api_idempotency 全表按龄清扫（阈值与键内惰性清理共用 api.idempotency 配置）',
           '40 4 * * *', '{}'),
          ('llm_cache_lru',
           'llm_cache 淘汰：闲置逐出 + 容量上限（最久未命中先出）',
           '20 4 * * *', '{"max_idle_days": 90, "max_rows": 200000}')
        ON CONFLICT (code) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM app.schedule
         WHERE code IN ('scrape_reclaim', 'api_idempotency_sweep', 'llm_cache_lru');
        ALTER FUNCTION app.ensure_month_partitions(regclass, int)
          SECURITY INVOKER RESET search_path;
        """
    )
