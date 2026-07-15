"""R2-03 上架弹药 DDL 三件（一次迁移收齐，避免碎片化）。

1) refdata.pt_spec += fields jsonb：per-PT 官方 MP_ITEM v5 **原始 schema 节点**
   （properties/required/allOf 无损原样）。为何不用旧审核库 walmart_pt_spec.fields：
   那是压缩版（enum 截断 10、丢 minLength/maxLength/pattern/allOf 条件必填，
   源 sync_pt_specs.py:100-110）——用作校验依据会假拒。本列由
   tools/extract_mp_item_spec.py 无损产出（输入=T7 MPSetup monolith 或
   POST /v3/items/spec live 响应）。
   伪行协议：walmart_product_type='__orderable__' 存共享 Orderable 段 schema
   （全 PT 共用一份；不与真实 PT 名冲突，INNER JOIN pt_meta 类消费方天然滤掉）。
2) ck_llm_usage_module += 'listing'：AI 属性填写记账归因（R2-03 增量 3）。
3) ck_import_job_domain += 'listing_error_catalog'：错误码字典批量灌入
   （R2-03 增量 5；service 侧 SUPPORTED_DOMAINS 在该增量同步放行）。

downgrade 还原三处。CHECK 收窄前先处理存量行（否则 ADD CONSTRAINT 验证失败——
CI 演练在 pytest 之后跑，库里有测试写入的 'listing' usage 行）：
module='listing' → 重映射 'other'（保留计费记录，语义=0020 前无此归因）；
domain='listing_error_catalog' 的 import_job 行随特性删除（该域在旧 schema 不存在）。
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_DOMAINS_0019 = (
    "blacklist_brand", "blacklist_seller", "blacklist_asin", "blacklist_category",
    "tro", "phishing", "category_map", "gtin", "trademark", "suspension_case", "product",
    "policy", "pt_meta", "pt_spec",
)  # fmt: skip
_DOMAINS_0020 = (*_DOMAINS_0019, "listing_error_catalog")

_MODULES_0008 = ("audit", "category_map", "mail_classify", "other")
_MODULES_0020 = ("audit", "category_map", "mail_classify", "listing", "other")


def _domain_check(domains: tuple[str, ...]) -> str:
    vals = ", ".join(f"'{d}'" for d in domains)
    return (
        "ALTER TABLE app.import_job DROP CONSTRAINT ck_import_job_domain;"
        f"ALTER TABLE app.import_job ADD CONSTRAINT ck_import_job_domain"
        f" CHECK (domain IN ({vals}));"
    )


def _module_check(modules: tuple[str, ...]) -> str:
    vals = ", ".join(f"'{m}'" for m in modules)
    return (
        "ALTER TABLE app.llm_usage_log DROP CONSTRAINT ck_llm_usage_module;"
        f"ALTER TABLE app.llm_usage_log ADD CONSTRAINT ck_llm_usage_module"
        f" CHECK (module IN ({vals}));"
    )


def upgrade() -> None:
    op.execute("ALTER TABLE refdata.pt_spec ADD COLUMN fields jsonb")
    op.execute(_module_check(_MODULES_0020))
    op.execute(_domain_check(_DOMAINS_0020))


def downgrade() -> None:
    op.execute("DELETE FROM app.import_job WHERE domain = 'listing_error_catalog'")
    op.execute(_domain_check(_DOMAINS_0019))
    op.execute("UPDATE app.llm_usage_log SET module = 'other' WHERE module = 'listing'")
    op.execute(_module_check(_MODULES_0008))
    op.execute("ALTER TABLE refdata.pt_spec DROP COLUMN fields")
