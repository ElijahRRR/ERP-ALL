"""R2-13：procurement_order 插件路径增列 + 两条 CHECK 扩词表 + 唯一派发约束

Revision ID: 0045
Revises: 0044
Create Date: 2026-07-31

本文件是 R2-13 本轮**唯一改既有表**的迁移。口径
`specs/001-domain-model/07-order-sourcing-aftersale.md:88-113`（成本三分、异常九类、
`pending_review`）。

## 一、buyer_account_id 为什么**没有**外键

图纸 `07:267` 写 `REFERENCES buyer_account`，本文件落为软引用（无 FK），理由与
`0047:110-112` 把 `purchaser_id` 的外键拆掉时**逐字同款**：§7.1 三级规则下，
`procurement_order` 属③级（订单族，D-Q18 永久保留、一行不删），而买家账号属②级
（`deleted_principal.kind` 词表 `0047:83-84` 已预含 `'buyer_account'`，即该主体将来
要能删）。带外键则②级删除对 buyer_account 不可达；置 NULL 则历史订单从此不知道
「当时是哪个买家号拍的」。**③级表指向可删实体的列本就应当是软引用**——同
`audit_log.actor_id` / `order_line.product_id` / `deleted_product.deleted_by`。

> **请审计侧确认或改判**（形状同 `0047` 头注那句）：若判定外键必须保留，
> 则②级对 buyer_account 不可达，图纸 `07:267` 与 §7.1 需二择其一改写。

对照（本轮更新）：0044 的 `buyer_account_id` **已随身份模型更正整列删除**——令牌绑
浏览器不绑账号，实例与买家账号之间不再有配对关系（见 `0044` 头注一/二）。本表这一列
**保持软引用不变**：它记的是**这单当时派给了哪个买家号**，是③级订单族的历史事实，
与实例无关。**两列同名，此后连对象都不同，勿按对称性统一。**

## 二、uq_po_active_dispatch 的谓词为什么长这样

```sql
UNIQUE (order_id) WHERE buyer_account_id IS NOT NULL AND status <> 'cancelled'
```

这是「**同一渠道订单只派一个买家账号**」（图纸 07:270）的 **DB 层兜底**——应用层
`SKIP LOCKED` 只挡得住同一瞬间的行级竞争，挡不住两张不同 PO 指向同一 `order_id`。

- 带 `buyer_account_id IS NOT NULL` ⇒ **人工/门户路径（该列恒 NULL）完全不受影响**，
  R2-05 既有绿测一条不会红；
- 排除 `cancelled` ⇒ 它是唯一明确的「这张单作废」终态，不排除会锁死「取消后重建单」
  这条既有人工路径；
- **`exception` 故意不排除**：17 条异常原文里 #16 是「回传订单失败，请手动复制 AMZ
  订单号回填」——**那意味着单已经真下出去、钱已经花了，只是回传失败**。若把
  exception 也排除在谓词外，此刻允许同一订单再派给另一个账号，就是**二次扣款**。
  异常单要重新流转，走的是「清空 `buyer_account_id`」的显式出边，不靠放宽约束。

## 三、status 扩 `pending_review`：本轮**只扩词表，零转入路径**

`pending_review` ＝ 护栏拦下的专用停留位（图纸 07:93，2026-07-30 补，原约束漏列致
图纸自相矛盾）。**产生它的写入路径属 13c（护栏评估点），不在本轮**；DEFAULT 仍是
`unassigned`，以免扰动已验收在跑的人工采购路径（R2-05）。号段表 `007:604` 把这条
CHECK 扩容排在本轮，故在此一次做完，13c 落地时无需再动约束。

## 四、backfill_actor_kind 扩 `'plugin'`（超出图纸 07:109，已列入批注回传）

不扩就只有两条路，两条都要撒谎：
① 让插件回填被 `backfill_po` 的 `assignee_kind=='none' → 'op_direct'` 分支标成
「运营在订单页直接代填」——**事实错误，且污染 D-Q50② 的统计**；
② 留 NULL——「谁填的」永久丢失。
扩一个值是三者里唯一不撒谎的。`backfill_actor_id` 在 `kind='plugin'` 时解读为
`plugin_instance.id`（原语义「app_user.id 或 portal_account.id，按 kind 解读」的自然延伸）。

## 五、成本三分不得合并

`purchase_cost`（税前商品额）· `tax_amount`（销售税）· `freight_cost`（运费）三项分列，
合计由计算得出、**不落库**。只存 total 则税额永久丢失，而财务域（§08）过账需要区分
商品成本与税费；`total` 的用途是回填后自校验（三项之和不等即落 `exception_kind='other'`）。
"""

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE app.procurement_order
          -- 软引用，无 FK（见头注一）。插件路径必填；人工/门户路径恒 NULL。
          ADD COLUMN buyer_account_id   bigint,
          -- 销售税（插件回填 tax）。美亚采购必有销售税，缺此列则利润核算从第一天就偏低。
          ADD COLUMN tax_amount         numeric(12,2),
          -- 付款卡后四位（插件回填 creditCardNumber）——资金来源可追溯，财务对账用。
          ADD COLUMN payment_card_last4 text,
          -- 预计送达（插件回填 deliveryTime，已格式化）
          ADD COLUMN delivery_est_date  date,
          -- 渠道原文（origDeliveryTime，如 `Thursday, August 7`）——存原文比重新抓便宜。
          ADD COLUMN delivery_est_raw   text,
          -- 异常六类 + 三特例（07:107，源自厂商插件生产实测 17 条 failContent）。
          -- **这是「问题分类」列，不是状态列**：回填后核对不通过属「已花钱但有问题」，
          -- 此时 status 必须保持 purchased，只写本列——改成 exception 会丢掉「已拍单」
          -- 这个事实，运营看到会以为没拍然后重拍一次。
          ADD COLUMN exception_kind     text;

        ALTER TABLE app.procurement_order
          ADD CONSTRAINT ck_procurement_exception_kind
          CHECK (exception_kind IS NULL OR exception_kind IN
                 ('address','stock','product','delivery','checkout','price_guard',
                  'channel_cancelled','order_not_found','other'));
        """
    )

    # status 扩 pending_review（头注三：本轮只扩词表，零转入路径）
    op.execute(
        """
        ALTER TABLE app.procurement_order DROP CONSTRAINT ck_procurement_status;
        ALTER TABLE app.procurement_order ADD CONSTRAINT ck_procurement_status
          CHECK (status IN
                 ('unassigned','pending_review','assigned','claimed','purchased','shipped',
                  'backfilled','exception','cancelled'));
        """
    )

    # backfill_actor_kind 扩 plugin（头注四）
    op.execute(
        """
        ALTER TABLE app.procurement_order DROP CONSTRAINT ck_procurement_backfill_actor;
        ALTER TABLE app.procurement_order ADD CONSTRAINT ck_procurement_backfill_actor
          CHECK (backfill_actor_kind IN ('internal','external','op_direct','plugin'));
        """
    )

    op.execute(
        """
        CREATE INDEX ix_procurement_buyer
          ON app.procurement_order (buyer_account_id, status);
        -- 「同一渠道订单只派一个买家账号」的 DB 层兜底（头注二）。
        CREATE UNIQUE INDEX uq_po_active_dispatch ON app.procurement_order (order_id)
          WHERE buyer_account_id IS NOT NULL AND status <> 'cancelled';
        """
    )


def downgrade() -> None:
    """降级把两条 CHECK 按 0025 原文还原。

    **若库中已存在 `status='pending_review'` 或 `backfill_actor_kind='plugin'` 的行，
    ADD CONSTRAINT 会硬失败**——这是降级的固有代价（同 0047 downgrade 的口径：
    升级期间已发生的事实无法靠降级抹掉），不是本降级的缺陷。真要降级须先人工处置
    那些行。六个新列连同其上的数据一并丢弃。
    """
    op.execute(
        """
        DROP INDEX IF EXISTS app.uq_po_active_dispatch;
        DROP INDEX IF EXISTS app.ix_procurement_buyer;
        """
    )
    op.execute(
        """
        ALTER TABLE app.procurement_order DROP CONSTRAINT ck_procurement_backfill_actor;
        ALTER TABLE app.procurement_order ADD CONSTRAINT ck_procurement_backfill_actor
          CHECK (backfill_actor_kind IN ('internal','external','op_direct'));
        ALTER TABLE app.procurement_order DROP CONSTRAINT ck_procurement_status;
        ALTER TABLE app.procurement_order ADD CONSTRAINT ck_procurement_status
          CHECK (status IN
                 ('unassigned','assigned','claimed','purchased','shipped',
                  'backfilled','exception','cancelled'));
        ALTER TABLE app.procurement_order DROP CONSTRAINT ck_procurement_exception_kind;
        """
    )
    op.execute(
        """
        ALTER TABLE app.procurement_order
          DROP COLUMN exception_kind,
          DROP COLUMN delivery_est_raw,
          DROP COLUMN delivery_est_date,
          DROP COLUMN payment_card_last4,
          DROP COLUMN tax_amount,
          DROP COLUMN buyer_account_id;
        """
    )
