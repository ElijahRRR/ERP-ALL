"""审核标准模型定标 deepseek-v4-flash（D-Q58，Owner 长期实测：好用且成本低）。

只改仍为 0008 种子默认值（deepseek-chat）的行——部署侧已手工切到 v4-flash 的
配置（version 已递增）不被覆盖。version+1 使 llm_cache 键随 prompt 版本前缀
自然失效。坏输出兜底：strip_json_fences + coerce fail-closed（既有）。
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE app.audit_policy
        SET config = jsonb_set(config, '{model}', '"deepseek-v4-flash"'),
            version = version + 1,
            updated_at = now()
        WHERE code = 'l3_intellectual_property'
          AND config->>'model' = 'deepseek-chat'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE app.audit_policy
        SET config = jsonb_set(config, '{model}', '"deepseek-chat"'),
            version = version + 1,
            updated_at = now()
        WHERE code = 'l3_intellectual_property'
          AND config->>'model' = 'deepseek-v4-flash'
        """
    )
