"""R2-11 二期A（D-Q64④）：feed_kind 检查约束放行 item_regroup

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-18

item_regroup = live 组成员补挂 VG 段重投通道（与 item_build 同为 MP_ITEM feed，
独立归位路径：提交被拒/条目失败不动 listing 状态、不释放 GTIN，仅返还配额）。
仅改 CHECK 枚举，无表结构变化。downgrade 先清该 kind 的 feed_item/feed 行
（FK 序）再收窄约束——该通道产物是渠道侧补挂记录，回滚场景可弃。
"""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

_KINDS_NEW = "('item_build','item_regroup','item_match','price','inventory','delete','lag_time')"
_KINDS_OLD = "('item_build','item_match','price','inventory','delete','lag_time')"


def upgrade() -> None:
    op.execute("ALTER TABLE app.feed DROP CONSTRAINT ck_feed_kind")
    op.execute(
        f"ALTER TABLE app.feed ADD CONSTRAINT ck_feed_kind CHECK (feed_kind IN {_KINDS_NEW})"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM app.feed_item WHERE feed_id IN"
        " (SELECT id FROM app.feed WHERE feed_kind = 'item_regroup')"
    )
    op.execute("DELETE FROM app.feed WHERE feed_kind = 'item_regroup'")
    op.execute("ALTER TABLE app.feed DROP CONSTRAINT ck_feed_kind")
    op.execute(
        f"ALTER TABLE app.feed ADD CONSTRAINT ck_feed_kind CHECK (feed_kind IN {_KINDS_OLD})"
    )
