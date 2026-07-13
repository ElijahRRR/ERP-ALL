"""L1 弹药补全：refdata.pt_meta（PT 元数据主表）+ category_map 补源列 + domain=pt_meta

考古修正（本单）：Owner 活数据 = 飞书两张 sheet——
  「映射明细」15,771 行（Amazon 路径→WPT 多对多候选 + 置信度/匹配方式/排名/来源批次）
  「沃尔玛类目」7,033 行（一行一 PT 的元数据主表，pt_meta）。
L1 候选必须 INNER JOIN pt_meta 过滤废弃 PT（源仓 2026-05-09 修复教训）。
001 §03 旧 category_map 设计（leaf 唯一→单 WPT + map_source）被 D-Q55（映射表+LLM 候选
复排）实质取代，随本单修订 001 正文。

- category_map 补 5 列（amazon_leaf/browse_node_id/rank_no/match_type/source_batch），
  Owner 从飞书映射明细全列导出可无损导入；
- pt_meta 新表（列级保真源仓 walmart_pt_meta，去 raw/synced_at——溯源走 import_job）；
- import_job domain CHECK += 'pt_meta'。
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

# import_job.domain 全集演进：0010 原集 + policy(0011) + pt_meta(本单)
_DOMAINS_0011 = (
    "blacklist_brand", "blacklist_seller", "blacklist_asin", "blacklist_category",
    "tro", "phishing", "category_map", "gtin", "trademark", "suspension_case", "product",
    "policy",
)  # fmt: skip
_DOMAINS_0016 = (*_DOMAINS_0011, "pt_meta")


def _domain_check(domains: tuple[str, ...]) -> str:
    vals = ", ".join(f"'{d}'" for d in domains)
    return (
        "ALTER TABLE app.import_job DROP CONSTRAINT ck_import_job_domain;"
        f"ALTER TABLE app.import_job ADD CONSTRAINT ck_import_job_domain"
        f" CHECK (domain IN ({vals}));"
    )


def upgrade() -> None:
    op.execute(_domain_check(_DOMAINS_0016))
    op.execute(
        """
        ALTER TABLE refdata.category_map
          ADD COLUMN amazon_leaf    text,
          ADD COLUMN browse_node_id text,
          ADD COLUMN rank_no        int,
          ADD COLUMN match_type     text,
          ADD COLUMN source_batch   text;

        CREATE TABLE refdata.pt_meta (
          walmart_product_type text PRIMARY KEY,
          walmart_category     text,
          walmart_ptg          text,
          access_state         text,
          zh_can_do            text,
          zh_seller_forbidden  boolean NOT NULL DEFAULT false,
          requirements         text,
          notes                text,
          total_fields         int,
          required_count       int,
          required_fields      text,
          updated_at           timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_pt_meta_category ON refdata.pt_meta (walmart_category);
        GRANT SELECT, INSERT, UPDATE ON refdata.pt_meta TO erp_app;
        CREATE TRIGGER trg_rev_pt_meta
          AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON refdata.pt_meta
          FOR EACH STATEMENT EXECUTE FUNCTION refdata.bump_dataset_revision('pt_meta');
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS refdata.pt_meta CASCADE")
    op.execute(
        "ALTER TABLE refdata.category_map"
        " DROP COLUMN IF EXISTS amazon_leaf, DROP COLUMN IF EXISTS browse_node_id,"
        " DROP COLUMN IF EXISTS rank_no, DROP COLUMN IF EXISTS match_type,"
        " DROP COLUMN IF EXISTS source_batch"
    )
    op.execute(_domain_check(_DOMAINS_0011))
