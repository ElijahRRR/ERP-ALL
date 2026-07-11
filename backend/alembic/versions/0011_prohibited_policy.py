"""refdata.prohibited_policy —— 沃尔玛 37 条禁售政策（L3 静态 prompt 全清单）

R2-02 审核弹药：37 条政策全量静态拼进 L3 system prompt 末尾（吃 provider prefix cache，
命中率 ~63%→~95%，见 .agent/evidence/R2-02/l3-policy-design.md）。数据经 import_job
`domain=policy` 灌入（Owner 从飞书 OJSrkV 导）；空表时 L3 退回单策略行为。

参考数据（无 team、导入专写、业务只读）→ 放 refdata schema，与 trademark 同域。
依据 R1-10 archaeology §L3 + specs/001 §04。
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


# import_job.domain 全集 = 0010 原集 + policy（本单新增导入域）
_DOMAINS_0010 = (
    "blacklist_brand", "blacklist_seller", "blacklist_asin", "blacklist_category",
    "tro", "phishing", "category_map", "gtin", "trademark", "suspension_case", "product",
)  # fmt: skip
_DOMAINS_0011 = (*_DOMAINS_0010, "policy")


def _domain_check(domains: tuple[str, ...]) -> str:
    vals = ", ".join(f"'{d}'" for d in domains)
    return (
        "ALTER TABLE app.import_job DROP CONSTRAINT ck_import_job_domain;"
        f"ALTER TABLE app.import_job ADD CONSTRAINT ck_import_job_domain"
        f" CHECK (domain IN ({vals}));"
    )


def upgrade() -> None:
    # 放开 domain=policy（0010 CHECK 依 spec 原域集，未含 policy）
    op.execute(_domain_check(_DOMAINS_0011))
    op.execute(
        """
        CREATE TABLE refdata.prohibited_policy (
          category_en       text PRIMARY KEY,
          seq               int,
          category_zh       text,
          overall_status    text,
          prohibited_items  text,
          conditional_items text,
          preapproval_items text,
          zh_seller_risk    text,
          zh_seller_notes   text,
          updated_at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_prohibited_policy_seq ON refdata.prohibited_policy (seq NULLS LAST, category_en);
        GRANT SELECT, INSERT, UPDATE ON refdata.prohibited_policy TO erp_app;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS refdata.prohibited_policy CASCADE")
    op.execute(_domain_check(_DOMAINS_0010))  # 还原 domain CHECK（去掉 policy）
