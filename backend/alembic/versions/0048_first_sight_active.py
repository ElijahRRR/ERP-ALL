"""17d（D-Q73）：放松 `ck_buyer_account_claimed`——首见即 active 需要「active + 空 label」可表示。

## 为什么这条 CHECK 可以放（且只放它）

0043 头注二-3 给它的定位是「认领闸：一旦进入**会被派单**的状态，label/site 必须齐全」。
两个依赖都随单人模式休眠了：

- **site 的消费者是自动派发的站点路由**（`dispatch._CANDIDATE_SQL` 按 site 圈候选）。
  17d 拉取改为「取本账号名下已指派单」，人工指派不读 site——「active 但没站点」
  不再意味着「会被派错站点的单」，只意味着「运营还没补这一栏」。
- **label 的消费者是人**。Owner 2026-07-31 的口径就是「我给他认领的时候写一个账号名
  我也能自己分辨」——账号名是后补的备注，不是准入条件。

于是 17d 的「customerId 首见即 active」（`plugin/identity.py::_REGISTER_SQL`）与这条
CHECK 正面冲突：约束不放，首见登记就只能二选一——编占位 label（撞
`uq_buyer_account_label` 的 500 路径，0043 头注二-2 已论证过不可走）或伪造 site
（数据撒谎：.ca 浏览器被记成 .com）。放约束是唯一不撒谎的路。

## 休眠与恢复

多机多人形态重启（恢复自动派发）时**必须把它加回来**：先把所有 `active` 且
label/site 为空的行补齐或转 `paused`，再执行本文件 `downgrade()`。violating 行
存在时 downgrade 会硬失败——这是设计内的 fail-loud：站点路由恢复之前，
「没站点的 active 账号」必须先被人处理掉，而不是静默放进候选池。
"""

from alembic import op

revision = "0048"
down_revision = "0046"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_buyer_account_claimed"
_CHECK_SQL = "status IN ('pending_claim','rejected') OR (label IS NOT NULL AND site IS NOT NULL)"


def upgrade() -> None:
    op.execute(f"ALTER TABLE app.buyer_account DROP CONSTRAINT {_CONSTRAINT}")


def downgrade() -> None:
    # 遗留「active + 空 label/site」行会让这条 ADD 硬失败——先补齐或转 paused（见头注）。
    op.execute(f"ALTER TABLE app.buyer_account ADD CONSTRAINT {_CONSTRAINT} CHECK ({_CHECK_SQL})")
