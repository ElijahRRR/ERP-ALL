"""R3b 弹药：refdata.pt_spec（官方 spec 认证字段，源仓 walmart_pt_spec 列级保真）。

R2-02 round-7 后最后一块可移植缺口：R3 证书判定的第 2 层数据源（官方 spec 驱动，
958 PT NRTL）——has_real_cert=true 且 PT 为整机（nrtl 分类器）→ 硬拒。
去 synced_at（溯源走 import_job）。+ import_job domain CHECK += 'pt_spec'
+ dataset_revision('pt_spec') 触发器。
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

_DOMAINS_0016 = (
    "blacklist_brand", "blacklist_seller", "blacklist_asin", "blacklist_category",
    "tro", "phishing", "category_map", "gtin", "trademark", "suspension_case", "product",
    "policy", "pt_meta",
)  # fmt: skip
_DOMAINS_0019 = (*_DOMAINS_0016, "pt_spec")


def _domain_check(domains: tuple[str, ...]) -> str:
    vals = ", ".join(f"'{d}'" for d in domains)
    return (
        "ALTER TABLE app.import_job DROP CONSTRAINT ck_import_job_domain;"
        f"ALTER TABLE app.import_job ADD CONSTRAINT ck_import_job_domain"
        f" CHECK (domain IN ({vals}));"
    )


def upgrade() -> None:
    op.execute(_domain_check(_DOMAINS_0019))
    op.execute(
        """
        CREATE TABLE refdata.pt_spec (
          walmart_product_type text PRIMARY KEY,
          has_real_cert        boolean NOT NULL DEFAULT false,
          real_cert_fields     jsonb,
          has_soft_cert        boolean NOT NULL DEFAULT false,
          soft_cert_fields     jsonb,
          total_fields         int,
          required_count       int,
          required_fields      jsonb,
          updated_at           timestamptz NOT NULL DEFAULT now()
        );
        GRANT SELECT, INSERT, UPDATE ON refdata.pt_spec TO erp_app;
        CREATE TRIGGER trg_rev_pt_spec
          AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON refdata.pt_spec
          FOR EACH STATEMENT EXECUTE FUNCTION refdata.bump_dataset_revision('pt_spec');
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS refdata.pt_spec CASCADE")
    op.execute(_domain_check(_DOMAINS_0016))
