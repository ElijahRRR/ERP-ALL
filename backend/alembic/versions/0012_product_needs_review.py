"""product.status += needs_review —— 外部评审 round-1 A4 采纳（fail-open → fail-closed）

原实现：L3 输出无法解析 / verdict 非法 → 强转 pass（合规 fail-open，外部评审指出）。
改为 needs_review：audit_run.verdict 原生已支持（0008 CHECK 含 needs_review），
product.status 补该值承接（人工复核 UI 由 RC-01 工单跟进；当前先在产品列表可见可重审）。
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


_STATUSES_0007 = (
    "ingested", "auditing", "audit_passed", "audit_rejected",
    "sourcing", "ready", "listed", "retired",
)  # fmt: skip
_STATUSES_0012 = (*_STATUSES_0007, "needs_review")


def _status_check(statuses: tuple[str, ...]) -> str:
    vals = ", ".join(f"'{s}'" for s in statuses)
    return (
        "ALTER TABLE app.product DROP CONSTRAINT ck_product_status;"
        f"ALTER TABLE app.product ADD CONSTRAINT ck_product_status"
        f" CHECK (status IN ({vals}));"
    )


def upgrade() -> None:
    op.execute(_status_check(_STATUSES_0012))


def downgrade() -> None:
    # ⚠️ 不可逆：needs_review 保守归入 audit_rejected（宁误杀不漏放），升回后无法区分
    # 哪些行原本是 needs_review——执行前确认可接受该语义损失（评审 round-2 R2-19）
    op.execute("UPDATE app.product SET status = 'audit_rejected' WHERE status = 'needs_review'")
    op.execute(_status_check(_STATUSES_0007))
