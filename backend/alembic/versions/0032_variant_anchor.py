"""R2-11 增量1：variant_group 补 anchor 落点 + 归组任务种子

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-17

- variant_group.anchor_store_id（D-Q63①）：D-Q2 点名的 anchor 实体在 001 §03 图纸
  缺落点，Owner 拍板加列承载——组首次上架时锁定店铺（增量2 写入），anchor 店暂停
  则成员上架拒绝不自动转移（BR-LST-013 保真）。
- schedule 种子 variant_group_sync：消费采集 twister 素材的自动归组（BR-ASC-003
  采集端变体侦测的下游）。变体表三件套 0007 已建，本单不建表。
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE app.variant_group
          ADD COLUMN anchor_store_id bigint REFERENCES app.store(id);
        """
    )
    op.execute(
        """
        INSERT INTO app.schedule (code, description, cron, config) VALUES
          ('variant_group_sync',
           '变体自动归组（twister 素材→variant_group/member + broken 判定 v1；D-Q2/D-Q63）',
           '40 * * * *',
           '{}')
        ON CONFLICT (code) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM app.schedule WHERE code = 'variant_group_sync'")
    op.execute("ALTER TABLE app.variant_group DROP COLUMN IF EXISTS anchor_store_id")
