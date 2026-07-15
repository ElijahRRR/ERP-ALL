"""llm_usage_log 增 cached_input_tokens（RS-08 记账项提前落地，D-Q58 随行）。

DeepSeek 缓存命中输入价 ≈ 未命中的 1/50（v4-flash $0.0028 vs $0.14 /1M）——
不记命中 token 数就无法真实计价、也无法度量 prefix-cache 优化收益
（评审 A6：实测 prefix-cache 假设）。分区父表 ALTER 自动传播到各月分区。
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE app.llm_usage_log ADD COLUMN cached_input_tokens int NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.llm_usage_log DROP COLUMN cached_input_tokens")
