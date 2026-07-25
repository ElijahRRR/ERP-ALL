"""R2-12 增量4b（end_date_renewal 执行通道）：channel_command action 扩 item_maintenance。

A 过期类维护任务的执行通道——提交 MP_MAINTENANCE feed 把 endDate 延期让商品 republish
（旧仓 relisting 语义）。走 item_retire 同款「listing 级直命令 + 200 即成」路径（非 feed
状态机），故只扩 ck_cc_action，不动 ck_feed_kind（不建 app.feed 行）。

Revision ID: 0038
Revises: 0037
Create Date: 2026-07-24
"""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

# 现行集合（0025 加 order_ack/order_ship、0027 加 price_push）——扩 item_maintenance
_OLD = "('feed_submit','item_retire','order_ack','order_ship','price_push')"
_NEW = "('feed_submit','item_retire','order_ack','order_ship','price_push','item_maintenance')"


def upgrade() -> None:
    op.execute("ALTER TABLE app.channel_command DROP CONSTRAINT ck_cc_action")
    op.execute(
        f"ALTER TABLE app.channel_command ADD CONSTRAINT ck_cc_action CHECK (action IN {_NEW})"
    )


def downgrade() -> None:
    op.execute("DELETE FROM app.channel_command WHERE action = 'item_maintenance'")
    op.execute("ALTER TABLE app.channel_command DROP CONSTRAINT ck_cc_action")
    op.execute(
        f"ALTER TABLE app.channel_command ADD CONSTRAINT ck_cc_action CHECK (action IN {_OLD})"
    )
