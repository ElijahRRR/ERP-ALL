"""llm_cache 补 DELETE 授权 —— 坏缓存行驱逐需要（评审 round-2 R2-21）

0008 只授 SELECT/INSERT/UPDATE；修复"坏响应先入缓存"后，命中存量坏行时要 DELETE 驱逐
再走真调用（自愈），故补该授权。
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT DELETE ON app.llm_cache TO erp_app")


def downgrade() -> None:
    op.execute("REVOKE DELETE ON app.llm_cache FROM erp_app")
