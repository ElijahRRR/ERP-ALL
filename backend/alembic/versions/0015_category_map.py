"""refdata.category_map —— Amazon 类目路径 → Walmart PT 候选映射（DG1，L1 类目判定前置）

源：walmart-audit-system db/schema.sql `walmart_category_map`（列级保真移植，去 raw/synced_at，
溯源统一走 import_job）。L1 主路径 = 本表候选召回 + LLM 复排（D-Q55：映射表+LLM，非嵌入）。
含特殊条目 walmart_product_type='无对应Walmart PT'（unmapped 标记，业务数据照导）。

import_job domain='category_map' 已在 0010 CHECK 全集内，无需改约束；
挂 0014 dataset_revision 统计触发器（任何写入方 bump，L1 候选缓存版本失效用）。
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE refdata.category_map (
          amazon_category      text NOT NULL,
          walmart_product_type text NOT NULL,
          confidence           text,
          requires_certificate boolean NOT NULL DEFAULT false,
          zh_seller_forbidden  boolean NOT NULL DEFAULT false,
          requirements         text,
          notes                text,
          updated_at           timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (amazon_category, walmart_product_type)
        );
        CREATE INDEX ix_category_map_pt ON refdata.category_map (walmart_product_type);
        GRANT SELECT, INSERT, UPDATE ON refdata.category_map TO erp_app;
        CREATE TRIGGER trg_rev_category_map
          AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON refdata.category_map
          FOR EACH STATEMENT EXECUTE FUNCTION refdata.bump_dataset_revision('category_map');
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS refdata.category_map CASCADE")
