"""R2-13 13d：procurement_logistics_event 采购物流事件（结构化轨迹）

Revision ID: 0046
Revises: 0045
Create Date: 2026-07-31

口径 `specs/001-domain-model/07-order-sourcing-aftersale.md:200-247` 逐列落地。
承运商／运单号／预计送达存 `procurement_order` 主表，**本表不重复存**。

## seq = 渠道数组下标，**0 = 最新，整个数组倒序**

任何 `ORDER BY seq ASC` 当时间序读的代码都是错的。要时间序请用 `occurred_at`
（可能为 NULL）或 `ORDER BY seq DESC`。唯一约束 `(procurement_order_id, seq)` 让
重投的同一批轨迹走 upsert 而不是堆重复行——故 RLS 需要 UPDATE 策略（用 TEAM_RLS
三策略模板），授权含 UPDATE。

## occurred_at 可空是刻意的

由渠道原文 `raw_day`+`raw_time`（英文，如 `July 28, 2026` / `8:42 AM`）解析；
**解析失败不丢事件**，原文仍入库。丢事件等于丢轨迹，而原文永远可以事后重解析。

## 明确**不存**的三个字段（防实现侧照抄厂商字段顺手加列）

| 字段 | 为什么不存 |
|---|---|
| `trackingHtml` | 渠道回传的 base64 整页 HTML，单条数十至数百 KB、**万单即 GB 级**；其信息已由本表结构化承载。图纸 `07:245-247` 的**明确不做决定** |
| `trackingUrl` | 可由 carrier + tracking_no 重建，无独立消费者 |
| `receiptPicture` | 同上，可重抓；存储代价与价值不成比例 |

三者同一条理由链：**可重抓、无消费者、存储代价与价值不成比例**。端点接收后立即丢弃。

## 硬外键在这里成立

`procurement_order` 是③级永不删（D-Q18），外键永远挡不到任何东西；且它非分区表、
PK 是单列 `id`，外键可直接建。与 0045 那条软引用的差别不在写法偏好，在被指向的
实体能不能删。

## 无 TOUCH 触发器

本表无 `updated_at` 列——事件是渠道事实的投影，upsert 覆盖即可，不需要「谁改的」。
同理无 DELETE 授权（授了就得配 DELETE 策略，见 test_baseline 的 RLS 不变量）。
"""

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None

# 逐字复制自 `0025_order_domain.py:32-39`（同 0043/0044，不 import 跨迁移）。
TEAM_RLS = """
ALTER TABLE app.{t} ENABLE ROW LEVEL SECURITY;
CREATE POLICY {t}_sel ON app.{t} FOR SELECT
  USING (team_id = app.current_team() OR app.is_super());
CREATE POLICY {t}_ins ON app.{t} FOR INSERT
  WITH CHECK (team_id = app.current_team() OR app.is_super());
CREATE POLICY {t}_upd ON app.{t} FOR UPDATE
  USING (team_id = app.current_team() OR app.is_super())
  WITH CHECK (team_id = app.current_team() OR app.is_super());
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.procurement_logistics_event (
          id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          -- 硬外键成立：procurement_order 属③级永不删（见头注）。
          procurement_order_id bigint NOT NULL REFERENCES app.procurement_order(id),
          team_id              bigint NOT NULL,
          -- 由 raw_day+raw_time 解析；解析失败不丢事件（本列留 NULL，原文照存）。
          occurred_at          timestamptz,
          raw_day              text,
          raw_time             text,
          -- 事件描述（渠道 tracking_info）。缺失则该条不入库。
          description          text NOT NULL,
          city                 text,
          state_code           text,
          -- 渠道数组下标，**0 = 最新**（渠道倒序，勿按 seq 升序当时间序）。
          seq                  int NOT NULL,
          created_at           timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX uq_proc_logistics_event
          ON app.procurement_logistics_event (procurement_order_id, seq);
        """
    )
    # 三策略（sel/ins/upd）：upd 是必需的——重投轨迹走 ON CONFLICT DO UPDATE。
    op.execute(TEAM_RLS.format(t="procurement_logistics_event"))
    # 无 DELETE（见头注）。
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.procurement_logistics_event TO erp_app;")


def downgrade() -> None:
    """降级丢全部轨迹事件（渠道侧可重抓，本表本就是投影）。"""
    op.execute("DROP TABLE IF EXISTS app.procurement_logistics_event CASCADE;")
