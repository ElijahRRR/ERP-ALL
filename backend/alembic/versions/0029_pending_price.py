"""R2-06 增量3 评审修复：listing.pending_price 在途标记（BR-LC-011 两段式派发半程）

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-16

考古口径5「派发只写 pending_price + 状态 updating」的落地形态（口径回写见
.agent/evidence/R2-06/archaeology.md 口径5 勘注）：
- pending_price 非空 = 该 listing 有改价在途（PUT 命令未终局 / price feed 未出结果），
  运营/前端可见，且作为改价并发闸（在途期间拒绝新推，防 old_price 陈旧竞态）；
- 渠道终态确认成功 → 回填 current_price + price_history 并清 pending_price；
  终态失败（明确拒 / 对账判未生效 / feed item error / feed lost）→ 只清 pending_price 复位；
- status='updating' **不落 listing.status 枚举**——状态机不引入渠道暂态
  （live/published 在途改价不改变生命周期状态），以 pending_price IS NOT NULL 表达。
"""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.listing ADD COLUMN pending_price numeric(12,2)")


def downgrade() -> None:
    op.execute("ALTER TABLE app.listing DROP COLUMN pending_price")
