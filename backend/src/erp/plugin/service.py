"""插件机器面服务层（R2-13 13a 通道与拉取语义；13d 补回填/异常/物流语义）。

## 三层越权防线（验收③「跨团队必败」就靠这三层）

| 层 | 落点 | 挡什么 |
|---|---|---|
| ① 授权链 | `token → plugin_instance.team_id`（`plugin/auth.py`） | **唯一授权来源** |
| ② SQL 谓词 | 本文件每条业务 SQL 都带 `team_id = :t` | 拿别的团队的 po_id 来调 |
| ③ RLS | 路由层用 `ctx_tx(team_id=principal.team_id)` | 跨团队（兜底，不是主防线） |

**`customerId` 是路由参数，不是鉴权凭据**（身份模型更正，Owner 2026-07-30，图纸
`07:288-340`）：令牌回答「这是不是团队 T 的一台授权浏览器」，`customerId` 回答「这台
浏览器此刻登的是哪个买家号」，解析只在团队 T 内进行（`plugin/identity.py`）。
**越权边界＝跨团队必败**，不是「实例只能取自己账号的任务」；错误码
`PLUGIN_CUSTOMER_MISMATCH` 随此整条删除。

## 为什么插件路径不复用 worker 的 `system_tx`

worker 跨团队派任务，必须绕 RLS；插件**绑定唯一团队**（`plugin_instance.team_id`），
没有任何理由自己关掉 RLS 兜底。故只有认证那一步走 `system_tx`（还不知道团队），
之后全部换 `ctx_tx`。本文件的每个函数都假定自己跑在**已绑定团队**的会话里。

## 13d 的四条定性决定（写反任何一条都会造出「钱花了系统不知道」）

**D1 —— `exception_kind` 是「问题分类」列，不是状态列。**
`status='exception'` 专指**任务失败、未产生采购**（端点 3）。回填后核对不通过
（ASIN / 邮编 / 金额不平）属**已花钱但有问题**：必须 `status='purchased'` + 只写
`exception_kind`。改成 exception 会丢掉「已拍单」这个事实，运营看到会以为没拍，
然后**重拍一次**。

**D2 —— 落异常 ≠ 不落数据。**
金额自校验不平、ASIN 不符时，金额与单号**照实入库**，只是额外落 `exception_kind`
与告警。钱已经花了，**不记账比记错更糟**。

**D5 —— 插件路径不写 `backfilled`。**
那是「回填全部完成（含物流）」的人工/门户终态。插件走 `assigned → purchased →
shipped`——R2-05 的 docstring 早写明「purchased/shipped 细分随物流接入」，本步就是
那次接入。

**D6 —— 插件的 `shipped` 绝不推 `channel_order.internal_status`。**
那一列是「我方已发货给 Walmart 买家」，由 `order/ship.py` 驱动。
**亚马逊侧包裹在路上 ≠ 我方已发货**，写反了 Walmart 侧对账全线错位。

## 端点与写入面的对应

| 端点 | 写什么 | 不写什么 |
|---|---|---|
| 2 回填 | 单号/成本三分/卡后四位/预计送达 → `purchased` + `purchasing` | `backfilled`（D5） |
| 3 失败 99 | `status='exception'` + 原文 + 归类 | **一个金额列都不写**（验收⑧） |
| 4 渠道 91/92 | 只落 `exception_kind` + critical 告警 | **`status` 一律不动**（D1） |
| 7 物流 | 运单/承运商/轨迹事件 + `purchased → shipped` | `internal_status`（D6）；整页 HTML |

## `last_seen_at` 只在两个拉取端点写

写路径不写（§3.1 的理由：每个写端点多一次 UPDATE 与行锁竞争，而回填/物流的并发最高）。
掉线判断本就该看「还在不在拉单」，一个只回填不拉单的实例并不是活着的实例。
"""

import json
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.errors import BusinessError
from erp.notify.service import Severity, notify
from erp.order import dispatch, procurement
from erp.plugin import classify, identity, parse, schemas
from erp.plugin.auth import PluginPrincipal

log = structlog.get_logger()

# 拉取批量的上界交给配置中心决定：这里传硬上限，`dispatch.claim_tasks_for_account`
# 内部会 `min(limit, procurement.plugin_dispatch.pull_batch_max)`。插件侧不带数量参数
# （厂商协议就没有），故批量完全由服务端配置控制。
_PULL_LIMIT = dispatch.PULL_BATCH_HARD_MAX

# 任务头：渠道单号 + 收货地址。`orderNo` 取**渠道单号**而不是 `PO-{id}`——插件只把它
# 写日志与回调参数，取渠道单号让插件日志能与 Walmart 后台直接对上。
_TASK_HEAD_SQL = """
SELECT po.id AS po_id, co.channel_order_no, co.ship_to
  FROM app.procurement_order po
  JOIN app.channel_order co ON co.id = po.order_id AND co.order_date = po.order_date
 WHERE po.id = ANY(:ids)
"""

# 任务行：ASIN 来自 product.source_ref（source_channel='amazon'）。
# LEFT JOIN 是刻意的——**要能看见「这一行没有 ASIN」**，用 INNER JOIN 会把缺 ASIN 的行
# 直接滤掉，于是一张四行的单被下发成三行，插件照着买，少买的那件永远不会有人发现。
_TASK_LINE_SQL = """
SELECT po.id AS po_id, ol.channel_line_no, ol.qty, p.source_ref
  FROM app.procurement_order po
  JOIN app.order_line ol
    ON ol.order_id = po.order_id AND ol.order_date = po.order_date
  LEFT JOIN app.product p
    ON p.id = ol.product_id AND p.source_channel = 'amazon'
 WHERE po.id = ANY(:ids)
 ORDER BY po.id, ol.channel_line_no
"""

# 待物流同步：已拍单且有渠道单号的。`status = 'shipped'` 的排在后面——它们运单号已回填，
# 再同步只为补物流事件，不该把新拍的单挤出这一批（`LIMIT` 是有限的）。
_SYNC_SQL = """
SELECT po.id AS po_id, co.channel_order_no, po.purchase_order_ref
  FROM app.procurement_order po
  JOIN app.channel_order co ON co.id = po.order_id AND co.order_date = po.order_date
 WHERE po.buyer_account_id = :a
   AND po.status IN ('purchased','shipped')
   AND po.purchase_order_ref IS NOT NULL
 ORDER BY (po.status = 'shipped'), po.purchased_at NULLS LAST, po.id
 LIMIT :n
"""

# 越权闸（本轮改形）。**两个谓词各挡一件事，缺一不可**：
#
# - `team_id = :t` —— **跨团队必败**（防线②的显式谓词，不靠 RLS 单撑）。
# - `buyer_account_id IS NOT NULL` —— **人工/门户单不许被插件写**。这是审查 A1
#   「人工/插件双买」那族修复的落点，不得回退。
#
# **写回四端点的请求体根本不带 `customerId`**（厂商协议如此，已逐字取证：
# `.agent/evidence/R2-13/owner-reverse-engineering-20260730.md` C.2/E.2 与 `schemas.py`），
# 故写回路径的归属检查**不可能**也**不应该**基于身份。
#
# **同团队、但派给了另一个账号的单 → 放行**，这是有意的语义变更：一台浏览器在拉单后被
# 换登了另一个亚马逊号，它手上那笔**已经花出去的钱**仍然必须报得回来。旧模型下这会 403
# ⇒「钱花了系统不知道」，比「同团队内浏览器串号」严重得多（后者按图纸 07:338-340 明确
# **不在威胁模型内**：自家机器、自家运营，且只能用实际登录的号付款，串号只会让自己拿到
# 买不了的单）。
#
# 取不到行的两种情形（不存在 / 不是本团队的插件单）回同一个错误码——越权应当在日志里
# 一眼看见，同时不泄露存在性。
# `note` 一并取回：演练/校验两档要往它后面追加痕迹，而追加前必须先看它里面有没有同一条
# （去重与长度闸都在 Python 侧，见 `_append_note`）。
_TEAM_PLUGIN_PO_SQL = """
SELECT id, team_id, order_id, order_date, status, buyer_account_id, purchase_order_ref,
       purchase_cost, tax_amount, freight_cost, exchange_rate_locked, exception_kind,
       exception_reason, note
  FROM app.procurement_order
 WHERE id = :p AND team_id = :t AND buyer_account_id IS NOT NULL
 FOR UPDATE
"""

# 回填后核对要用的两样：收货邮编（比 postCodeCheck）与该单每行的亚马逊 ASIN（比 asinCheck）。
# LEFT JOIN 同 `_TASK_LINE_SQL`：缺 ASIN 的行要能被看见，不是被静默滤掉。
_ORDER_FACTS_SQL = """
SELECT co.channel_order_no, co.ship_to
  FROM app.channel_order co
 WHERE co.id = :o AND co.order_date = :d
"""
_ORDER_ASINS_SQL = """
SELECT p.source_ref
  FROM app.order_line ol
  LEFT JOIN app.product p ON p.id = ol.product_id AND p.source_channel = 'amazon'
 WHERE ol.order_id = :o AND ol.order_date = :d
"""

# 轨迹事件重投走 upsert 而不是堆重复行（唯一键 `(procurement_order_id, seq)`，0046）。
_EVENT_UPSERT_SQL = """
INSERT INTO app.procurement_logistics_event
  (procurement_order_id, team_id, occurred_at, raw_day, raw_time, description, city,
   state_code, seq)
VALUES (:po, :t, :at, :day, :time, :desc, :city, :state, :seq)
ON CONFLICT (procurement_order_id, seq) DO UPDATE SET
  occurred_at = excluded.occurred_at, raw_day = excluded.raw_day,
  raw_time = excluded.raw_time, description = excluded.description,
  city = excluded.city, state_code = excluded.state_code
"""

# 采购完成时允许推到 `purchased` 的来源状态。**白名单而不是黑名单**：
# `shipped`（端点 7 先到）不该被拉回，`backfilled`/`cancelled` 是终态，
# 将来新增的状态默认不许被这条路径推走（fail-closed）。
#
# **`pending_review` 不在表里**（审查 B7）：它是 13c 护栏拦下的专用停留位，本轮
# **零转入路径**（0045 头注三只扩词表）。先写进白名单等于替 13c 预开一条出边——
# 而那条出边该怎么走恰恰是 13c 要定的：护栏拦下的单必须先由人**放行或驳回**，
# 不能由「插件说它买了」自动生效。13c 落地时，本表与人工侧的出边要**一并**设计，
# 别只放开其中一侧——人工侧现在同样把它挡在外面：`procurement.py::assign_po` 只收
# `('unassigned','assigned')`、`backfill_po` 只收 `('unassigned','assigned','claimed',
# 'purchased')`，`pending_review` 撞的是 `PROCUREMENT_STATE_INVALID` 409。
# 那两处**本次不动**（零转入路径的现状下改它们同样是替 13c 预开边）。
# 落地后果如实记：真出现 `pending_review` 的单被回填时，金额与单号照常入库（D2），
# 只是 `status` 停在原地等人处置——不记账比记错更糟，而自动推进比两者都糟。
_PURCHASABLE_FROM = ("unassigned", "assigned", "claimed", "exception")
# 允许被端点 3 推到 `exception` 的来源状态：**已拍单之后的一律不动**（D1）。
# 同样不含 `pending_review`（理由同上）。
_FAILABLE_FROM = ("unassigned", "assigned", "claimed", "exception")

# `exception_kind` 一列存不下多类：多项命中时按本序取第一个，全部原文进 `exception_reason`。
# 顺序即「谁更需要有人立刻处置」：买错东西 > 寄错地方 > 账目对不上。
_KIND_PRIORITY: tuple[str, ...] = ("product", "address", "other")

# 单次物流回填最多落几条轨迹事件（审查 B4）。`trackingJson` 是**外部输入且无上界**——
# 一条畸形/恶意载荷能在一个请求里塞进几万条，每条都是一次 upsert，把一次回填变成
# 长事务并撑爆表。真实亚马逊轨迹是十几到几十条量级，200 已是数倍余量。
# 超出部分**丢尾不丢头**：`seq` = 渠道数组下标、**0 是最新**（0046 头注），故截断掉的
# 是最老的那截；且轨迹本就是可重抓的投影（同一头注），丢了还能再同步回来——
# 这与「运单号是承重的、绝不能丢」是同一条纪律的两半。
_MAX_TRACKING_EVENTS = 200

# ── 端点 2 的三种执行档写入面 ──
#
# 三条 SQL 里的金额列一律 `coalesce(:新值, 旧值)`：**解析失败传进来的是 None，绝不能
# 因此把已经落好的金额抹成 NULL**。这与「解析失败不写 0」是同一条纪律的两半——
# 前者不许写假数，后者不许删真数。
_LIVE_BACKFILL_SQL = """
UPDATE app.procurement_order SET
  purchase_platform    = 'amazon',
  purchase_currency    = 'USD',
  -- D-Q32 同币种分支：USD 采购记 1.0 且不折算。**不能沿用 backfill_po 那句
  -- `(SELECT exchange_rate FROM purchaser WHERE id = purchaser_id)`**——插件单
  -- `purchaser_id IS NULL`，那句会取到 NULL，R2-08 过账时静默取不到费率。
  exchange_rate_locked = coalesce(exchange_rate_locked, 1.0),
  purchase_order_ref   = coalesce(:ref, purchase_order_ref),
  purchase_cost        = coalesce(:cost, purchase_cost),
  tax_amount           = coalesce(:tax, tax_amount),
  freight_cost         = coalesce(:freight, freight_cost),
  payment_card_last4   = coalesce(:card, payment_card_last4),
  delivery_est_date    = coalesce(:ddate, delivery_est_date),
  delivery_est_raw     = coalesce(:draw, delivery_est_raw),
  -- 0045 扩的第四个值。标 op_direct 是事实错误（污染 D-Q50② 统计），留 NULL 是丢信息。
  backfill_actor_kind  = 'plugin',
  backfill_actor_id    = :inst,
  exception_kind       = coalesce(:kind, exception_kind),
  exception_reason     = coalesce(:reason, exception_reason),
  -- **不写 backfilled**（D5）：那是含物流的人工终态，插件走 purchased → shipped。
  status               = CASE WHEN status = ANY(:from_) THEN 'purchased' ELSE status END,
  purchased_at         = CASE WHEN status = ANY(:from_) THEN coalesce(purchased_at, now())
                              ELSE purchased_at END
WHERE id = :p
"""

# 付款前停：金额抓得回来（验收⑥⑦要的就是这个），但**不写单号、不写 purchased_at、
# status 停在原地、不推 channel_order**——那笔钱确实没花。
_DRILL_BACKFILL_SQL = """
UPDATE app.procurement_order SET
  -- 金额是 USD，币种列却默认 'CNY'——只写金额不写币种，库里就多一句假话。
  purchase_currency    = 'USD',
  exchange_rate_locked = coalesce(exchange_rate_locked, 1.0),
  purchase_cost        = coalesce(:cost, purchase_cost),
  tax_amount           = coalesce(:tax, tax_amount),
  freight_cost         = coalesce(:freight, freight_cost),
  payment_card_last4   = coalesce(:card, payment_card_last4),
  delivery_est_date    = coalesce(:ddate, delivery_est_date),
  delivery_est_raw     = coalesce(:draw, delivery_est_raw),
  exception_kind       = coalesce(:kind, exception_kind),
  exception_reason     = coalesce(:reason, exception_reason),
  -- `coalesce(:note, note)`：传 NULL ＝ 本次不追加（已有同一条痕迹，或 note 已到长度上限）。
  -- 去重与长度闸在 Python 侧算好（`_append_note`），SQL 侧只负责落值。
  note                 = coalesce(:note, note)
WHERE id = :p
"""

# 只校验档：**一个金额列都不写**，只留一行痕迹说明「这个实例确实跑过、跑的是 dry_run」。
_DRY_RUN_SQL = """
UPDATE app.procurement_order
   SET note = coalesce(:note, note)
 WHERE id = :p
"""

# 端点 3：拍单失败。**零金额列**（验收⑧ 服务端半段）。已拍单之后的状态一律不动（D1）。
_FAIL_SQL = """
UPDATE app.procurement_order SET
  status           = CASE WHEN status = ANY(:from_) THEN 'exception' ELSE status END,
  exception_kind   = :kind,
  exception_reason = coalesce(:reason, exception_reason)
WHERE id = :p
"""

# 端点 4：渠道回报 91/92。**status 一字不动**——这两条发生在已拍单之后。
_CHANNEL_STATUS_SQL = """
UPDATE app.procurement_order SET
  exception_kind   = :kind,
  exception_reason = coalesce(:reason, exception_reason)
WHERE id = :p
"""

# 端点 7：物流回填。**两种 coalesce 方向是刻意的**——
# 运单/承运商 `coalesce(:新, 旧)`：本端点是它们的权威来源，新值覆盖；
# 金额四项 `coalesce(旧, :新)`：端点 2 落的才是权威，本端点只补「端点 2 没成功」的空洞。
_TRACKING_SQL = """
UPDATE app.procurement_order SET
  carrier            = coalesce(:carrier, carrier),
  tracking_no        = coalesce(:tracking_no, tracking_no),
  payment_card_last4 = coalesce(payment_card_last4, :card),
  delivery_est_date  = coalesce(delivery_est_date, :ddate),
  delivery_est_raw   = coalesce(delivery_est_raw, :draw),
  purchase_cost      = coalesce(purchase_cost, :cost),
  tax_amount         = coalesce(tax_amount, :tax),
  freight_cost       = coalesce(freight_cost, :freight),
  -- 只从 purchased 推到 shipped，**绝不从 assigned 跳**：没拍单哪来的运单。
  status             = CASE WHEN :tracking_no IS NOT NULL AND status = 'purchased'
                            THEN 'shipped' ELSE status END,
  shipped_at         = CASE WHEN :tracking_no IS NOT NULL AND status = 'purchased'
                                 AND shipped_at IS NULL THEN now() ELSE shipped_at END
WHERE id = :p
"""


async def touch_last_seen(
    session: AsyncSession,
    principal: PluginPrincipal,
    *,
    account_id: int | None,
    customer_id: str,
    version: str | None = None,
) -> None:
    """刷新账号与实例的「最近露面」，并记下这台浏览器此刻登的是哪个号。

    **只在两个拉取端点调用**，不放进认证依赖：放进去的话每个写端点都要多一次 UPDATE
    与行锁竞争，而回填/物流那几条路径的并发恰好最高。

    - **待认领账号也 touch `last_seen_at`**：管理端据此显示「这个未认领的号此刻真的在
      活动」，认领动作才有依据。
    - `last_seen_customer_id` **每次拉取都写**（哪怕解析不到账号 ⇒ `account_id is None`）
      ——它是观察值，与解析结果无关。**任何鉴权判定都不得读它**（0044 头注六）。
    """
    if account_id is not None:
        await session.execute(
            text("UPDATE app.buyer_account SET last_seen_at = now() WHERE id = :a"),
            {"a": account_id},
        )
    await session.execute(
        text(
            "UPDATE app.plugin_instance SET last_seen_at = now(),"
            " version = coalesce(:v, version), last_seen_customer_id = :c WHERE id = :i"
        ),
        {"v": version, "c": customer_id, "i": principal.instance_id},
    )


def _address(ship_to: dict[str, Any]) -> str | None:
    """Walmart `postalAddress` 的两行地址 → 插件的单字段 `receivingAddress`。

    形状见 `order/pull.py:117-125`（`ship_to = {**postalAddress, phone}`）。
    """
    line1 = str(ship_to.get("address1") or "").strip()
    line2 = str(ship_to.get("address2") or "").strip()
    if line1 and line2:
        return f"{line1}\n{line2}"
    return line1 or line2 or None


def _opt(ship_to: dict[str, Any], key: str) -> str | None:
    value = ship_to.get(key)
    return None if value is None or str(value) == "" else str(value)


async def pull_purchase_tasks(
    session: AsyncSession, principal: PluginPrincipal, *, customer_id: str, version: str | None
) -> list[dict[str, Any]]:
    """端点 1：拉待采购任务（**拉取即认领**，GET 但有副作用）。

    重复调用幂等：已认领给本账号的单会**再次返回**（`dispatch` 的续拉），不产生新派发、
    不重复消耗 `daily_cap`。插件重启/掉线后必须还能看见自己手上的单，否则那批单没人管。

    缺 ASIN 的单在派发层就不进候选（`dispatch._CANDIDATE_SQL` 的 `NOT EXISTS`）；本函数
    再兜一次——**已派出去之后商品被删**（14a 硬删）会让老单变成缺 ASIN，那种单只能
    告警 + 不下发，不能猜一个 ASIN 给插件去买。

    `customerId` 在**本团队范围内**解析成账号（`plugin/identity.py`）：解析不到即当新号
    首见登记为 `pending_claim` 并回空数组——**四种情形响应逐字节同形**，不泄漏存在性。
    """
    resolved = await identity.resolve_customer(session, principal, customer_id)
    # `touch_last_seen` 必须留在**派发之后**（见下方 B1 提醒），但 `last_seen_customer_id`
    # 与解析结果无关，故它对「解析不到」这一支同样要跑——两件事在同一个函数里。
    if resolved is None or resolved.status != "active":
        await touch_last_seen(
            session,
            principal,
            account_id=None if resolved is None else resolved.id,
            customer_id=customer_id,
            version=version,
        )
        log.info(
            "plugin.pull.empty",
            instance=principal.instance_id,
            account=None if resolved is None else resolved.id,
            reason="CUSTOMER_UNRESOLVED" if resolved is None else f"ACCOUNT_{resolved.status}",
        )
        return []

    # **`status != 'active'` 的短路在调用方**：`dispatch.py` 不该知道 `pending_claim` /
    # `rejected` 这些身份态存在，它只认「能不能派单」。`claim_tasks_for_account` 的闸①
    # 作为**双层冗余保留**（13b 的测试直接调它）。
    po_ids, reason = await dispatch.claim_tasks_for_account(
        session,
        team_id=principal.team_id,
        buyer_account_id=resolved.id,
        site=resolved.site,
        account_status=resolved.status,
        daily_cap=resolved.daily_cap,
        limit=_PULL_LIMIT,
    )
    # ⚠️ **顺序承重**：`touch_last_seen` 会 UPDATE 同一行账号，它必须留在派发**之后**。
    # 挪到前面，账号行锁就变成「由 touch 隐式获得」——正是 `dispatch.py` 第③步注释
    # 明令警告的那种「将来任何一次挪动都会静默拿掉 daily_cap 串行化」。
    await touch_last_seen(
        session, principal, account_id=resolved.id, customer_id=customer_id, version=version
    )
    if not po_ids:
        log.info(
            "plugin.pull.empty",
            instance=principal.instance_id,
            account=resolved.id,
            reason=reason,
        )
        return []

    heads = {
        int(r["po_id"]): r
        for r in (await session.execute(text(_TASK_HEAD_SQL), {"ids": po_ids})).mappings()
    }
    lines: dict[int, list[dict[str, Any]]] = {}
    for row in (await session.execute(text(_TASK_LINE_SQL), {"ids": po_ids})).mappings():
        lines.setdefault(int(row["po_id"]), []).append(dict(row))

    tasks: list[dict[str, Any]] = []
    for po_id in po_ids:
        head = heads.get(po_id)
        rows = lines.get(po_id, [])
        missing = [r for r in rows if not str(r["source_ref"] or "").strip()]
        if head is None or not rows or missing:
            await _warn_undeliverable_task(
                session, principal, po_id, account_id=resolved.id, empty=not rows
            )
            continue
        ship_to = dict(head["ship_to"] or {})
        state = _opt(ship_to, "state")
        tasks.append(
            schemas.PluginTask(
                id=po_id,
                orderNo=str(head["channel_order_no"]),
                receivingName=_opt(ship_to, "name"),
                receivingPhone=_opt(ship_to, "phone"),
                receivingAddress=_address(ship_to),
                receivingCity=_opt(ship_to, "city"),
                receivingDistrict=_opt(ship_to, "district"),
                receivingPostCode=_opt(ship_to, "postalCode"),
                receivingCountry=_opt(ship_to, "country"),
                # 两个字段名同值下发：插件读的是 `order.state || order.receivingState`。
                state=state,
                receivingState=state,
                products=[
                    schemas.PluginTaskProduct(
                        asin=str(r["source_ref"]).strip(), quantity=int(r["qty"])
                    )
                    for r in rows
                ],
                execMode=principal.exec_mode,
            ).model_dump()
        )
    return tasks


async def _warn_undeliverable_task(
    session: AsyncSession,
    principal: PluginPrincipal,
    po_id: int,
    *,
    account_id: int,
    empty: bool,
) -> None:
    """已派给本账号、但下发不出去的单（缺 ASIN / 缺行 / 订单头查不到）。

    **只告警不改状态**：这单已经挂在账号名下（`uq_po_active_dispatch` 也据此挡住重派），
    自动把它退回池会与「插件可能正在执行」冲突；自动标异常又会在没有出边的状态里
    再堆一张。留给人处置，并让人**看得见**。
    """
    body = (
        f"执行单 #{po_id} 已派给买家账号 {account_id}，"
        + ("但该订单没有任何订单行" if empty else "但存在缺少亚马逊 ASIN 的订单行")
        + "，已跳过下发（不猜 ASIN）——请补齐商品来源或人工处置该单"
    )
    await notify(
        session,
        team_id=principal.team_id,
        severity="warn",
        category="procurement",
        title="采购任务缺 ASIN，无法下发给插件",
        body=body,
        object_type="procurement_order",
        object_id=str(po_id),
        dedupe_key=f"plugin.no_asin.{po_id}",
    )


async def pull_sync_orders(
    session: AsyncSession, principal: PluginPrincipal, *, customer_id: str, version: str | None
) -> list[dict[str, Any]]:
    """端点 6：拉待物流同步订单（`{id, orderNo, platformOrderNo}`）。

    响应字段未逐字取证，见 `schemas.PluginSyncOrder` 的说明——**如实登记缺口，不编字段**。

    **本端点不设 `status` 闸**（与端点 1 的区别是刻意的）：它拉的是「已经买了、等物流」
    的单。账号被 `paused`/`blocked` 时**必须照常同步**——包裹在路上，钱已经花了
    （同 `plugin/auth.py` 头注那条纪律）。`pending_claim` 账号名下不可能有已拍单，
    `_SYNC_SQL` 自然返回空，无需特判。

    `po.buyer_account_id = :a` 的语义正确：这台浏览器现在登着 A 号，就同步 A 号买的单。
    """
    resolved = await identity.resolve_customer(session, principal, customer_id)
    if resolved is None:
        # 撞洪水闸：连待认领行都没落，没有账号可指 ⇒ 无单可同步（响应同形）。
        await touch_last_seen(
            session, principal, account_id=None, customer_id=customer_id, version=version
        )
        return []
    cfg = await dispatch.dispatch_config(session, principal.team_id)
    rows = (
        await session.execute(text(_SYNC_SQL), {"a": resolved.id, "n": cfg.pull_batch_max})
    ).mappings()
    out = [
        schemas.PluginSyncOrder(
            id=int(r["po_id"]),
            orderNo=str(r["channel_order_no"]),
            platformOrderNo=str(r["purchase_order_ref"]),
        ).model_dump()
        for r in rows
    ]
    await touch_last_seen(
        session, principal, account_id=resolved.id, customer_id=customer_id, version=version
    )
    return out


async def load_team_plugin_po(
    session: AsyncSession, principal: PluginPrincipal, po_id: int
) -> dict[str, Any]:
    """取一张**本团队的插件派发单**并加行锁；否则 403（谓词与理由见 `_TEAM_PLUGIN_PO_SQL`）。

    函数名不再暗示「账号归属」——写回四端点的请求体不带 `customerId`，归属检查是
    「团队 + 是插件派发单」，**不是**身份一致性。**同团队、派给另一个账号的单放行**
    （有意的语义变更：钱已经花出去了，报得回来比「浏览器串号」重要得多）。

    **写回路径不加任何身份一致性校验**：`principal.instance_id` 已经落进
    `backfill_actor_id`（0045 词表 `kind='plugin'`），「这单是哪台浏览器填的」有据可查；
    再加一个「实例最近登的号 vs 单的账号」的闸，只会在正常换号时产生噪声告警。

    不存在的 po_id 与别人团队的 po_id **回同一码同一状态**：越权是安全事件，应当在日志里
    一眼看见（`plugin.task_not_owned`），但不该顺带告诉调用方「这个 id 是存在的」。
    错误码 `PLUGIN_TASK_NOT_OWNED` **逐字保留**——runbook、openapi、部署指令三处都钉着
    它，改码等于作废已完成的真机取证；只改了 message 文案。
    """
    row = (
        (await session.execute(text(_TEAM_PLUGIN_PO_SQL), {"p": po_id, "t": principal.team_id}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        log.warning(
            "plugin.task_not_owned",
            instance=principal.instance_id,
            team=principal.team_id,
            po_id=po_id,
        )
        raise BusinessError(
            "PLUGIN_TASK_NOT_OWNED",
            "该采购任务不是本团队的插件派发任务",
            http_status=403,
        )
    return dict(row)


# ── 13d 公共件：问题累积 / 原文追加 / 告警 ──


def _rank(kind: str) -> int:
    return _KIND_PRIORITY.index(kind) if kind in _KIND_PRIORITY else len(_KIND_PRIORITY)


class _Findings:
    """回填后核对的问题累积器。

    一次回填可能同时命中多类（ASIN 不符 + 邮编不符 + 金额不平），而 `exception_kind`
    只有一列。**分类取优先序第一个，原文全部保留**——分类决定谁被叫醒，原文决定人
    看到之后判断得出发生了什么，两者缺一都会让这条异常沦为噪声。
    """

    def __init__(self) -> None:
        self._items: list[tuple[str, str, Severity]] = []

    def add(self, kind: str, reason: str, severity: Severity = "critical") -> None:
        self._items.append((kind, reason, severity))

    def __bool__(self) -> bool:
        return bool(self._items)

    @property
    def kind(self) -> str | None:
        if not self._items:
            return None
        return min((k for k, _, _ in self._items), key=_rank)

    @property
    def text(self) -> str | None:
        return "\n".join(r for _, r, _ in self._items) or None

    @property
    def severity(self) -> Severity:
        return "critical" if any(s == "critical" for _, _, s in self._items) else "warn"


def _clean(raw: str | None) -> str | None:
    """空白串等于没给。插件抓不到的字段送的是 `""` 不是 `null`（`extPostCode` 就是）。"""
    value = (raw or "").strip()
    return value or None


def _append_reason(prior: str | None, new: str | None) -> str | None:
    """把新原文接到已有 `exception_reason` 之后（换行分隔）；**已含则回 None**。

    回 None 的语义是「SQL 侧 `coalesce(:reason, exception_reason)` 保持原值」。
    幂等是必需的：端点 4 的重投送来的是同一段文字，无脑追加会把那一列撑成一堵墙，
    而墙里的信息量与一行完全相同。
    """
    if not new:
        return None
    if not prior:
        return new
    return None if new in prior else f"{prior}\n{new}"


# `note` 的长度上限。这一列是运营看的自由文本，只增不减；一个坏掉的插件在循环重投时
# 能把它撑成几 MB——那时它既没人读得下去，也会拖慢每一条带 note 的查询。到顶就停手 +
# 告警，**不截断已有内容**（截断＝删别人写下的字，那是数据损失，不是保护）。
_NOTE_MAX_CHARS = 4000


def _append_note(prior: str | None, marker: str, *, po_id: int) -> str | None:
    """把演练/校验痕迹接到 `note` 之后；**已含或已到上限则回 None**（审查 B3）。

    回 None 的语义与 `_append_reason` 一致：SQL 侧 `coalesce(:note, note)` 保持原值。

    幂等是必需的：端点 2 的重投送来的是同一段请求，`_marker` 也刻意不带时间戳，
    于是「note 里已经有这串」就是可靠的判重依据——同一次演练重投十次，note 仍是一行。
    """
    if prior and marker in prior:
        return None
    if prior and len(prior) >= _NOTE_MAX_CHARS:
        log.warning("plugin.note.limit_reached", po_id=po_id, length=len(prior))
        return None
    return marker if not prior else f"{prior}\n{marker}"


async def _alert(
    session: AsyncSession,
    principal: PluginPrincipal,
    po_id: int,
    *,
    severity: Severity,
    title: str,
    body: str,
    dedupe_key: str,
) -> None:
    await notify(
        session,
        team_id=principal.team_id,
        severity=severity,
        category="procurement",
        title=title,
        body=body,
        object_type="procurement_order",
        object_id=str(po_id),
        dedupe_key=dedupe_key,
    )


async def _mark_kind(
    session: AsyncSession, po_id: int, *, kind: str | None, reason: str | None
) -> None:
    """只落分类与原文，**一列业务数据都不碰**（幂等冲突与端点 4 共用）。"""
    await session.execute(
        text(
            "UPDATE app.procurement_order"
            " SET exception_kind = coalesce(:kind, exception_kind),"
            "     exception_reason = coalesce(:reason, exception_reason)"
            " WHERE id = :p"
        ),
        {"kind": kind, "reason": reason, "p": po_id},
    )


async def _order_facts(session: AsyncSession, po: dict[str, Any]) -> dict[str, Any]:
    """取该执行单对应渠道订单的单号与收货地址（回填后核对的比对基准）。"""
    row = (
        (
            await session.execute(
                text(_ORDER_FACTS_SQL), {"o": po["order_id"], "d": po["order_date"]}
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return {"channel_order_no": None, "ship_to": {}}
    return {"channel_order_no": row["channel_order_no"], "ship_to": dict(row["ship_to"] or {})}


# ── 端点 2：采购完成回填 ──


_MONEY_FIELDS: tuple[tuple[str, str], ...] = (
    ("totalBeforeTax", "purchase_cost"),
    ("tax", "tax_amount"),
    ("shipping", "freight_cost"),
)


def _resolve_exec_mode(principal: PluginPrincipal, echoed: str | None, *, po_id: int) -> str:
    """确定本次回填按哪个执行档处理（不符即 409）。

    **没带 `execMode`** → 按实例档执行。能通过双头部认证的必然是 fork 版（厂商原版
    指向他们自己的 SaaS、不带令牌头，根本到不了这里），所以「没带」只可能是 fork 漏送，
    不可能是「原版正按旧流程真下单」；而契约里这个字段本就是可选的（13a 已发布），
    据此拒收等于拒掉一个合规请求。

    **带了且与实例档不符** → 409。这条挡的是「实例已降档、插件仍按旧档在真下单」：
    放行就等于默许它继续按错档买下去。**代价如实登记**：若这次回填确实带回了渠道单号，
    拒收意味着那笔账 ERP 侧没有记录，须人工照插件日志补——故同时 `log.error`，
    别让它只留在插件的浏览器控制台里。
    """
    if echoed is None:
        log.warning(
            "plugin.backfill.exec_mode_absent",
            instance=principal.instance_id,
            po_id=po_id,
            mode=principal.exec_mode,
        )
        return principal.exec_mode
    if echoed != principal.exec_mode:
        log.error(
            "plugin.backfill.exec_mode_mismatch",
            instance=principal.instance_id,
            po_id=po_id,
            echoed=echoed,
            expected=principal.exec_mode,
        )
        raise BusinessError(
            "PLUGIN_EXEC_MODE_MISMATCH",
            f"插件回报的执行档 {echoed} 与实例当前档 {principal.exec_mode} 不符——"
            "回填已拒收；若该单实际已下出去，请照插件日志人工回填",
            http_status=409,
        )
    return principal.exec_mode


def _parse_amounts(
    body: schemas.PluginBackfillIn, findings: _Findings
) -> dict[str, Decimal | None]:
    """成本三分逐项解析。解析失败 → 该列留 NULL + 落 `other`，**绝不写 0**。"""
    out: dict[str, Decimal | None] = {}
    for src, col in _MONEY_FIELDS:
        raw = getattr(body, src)
        try:
            out[col] = parse.parse_money(raw)
        except parse.MoneyParseError:
            out[col] = None
            findings.add(
                "other",
                f"金额字段 {src} 无法解析，渠道原文 {raw!r}——该列留空未入库（不按 0 记）",
            )
    return out


def _self_check(
    body: schemas.PluginBackfillIn, amounts: dict[str, Decimal | None], findings: _Findings
) -> None:
    """三项之和 == 渠道 `total`。不平**照实入库**，只额外落异常（D2）。"""
    try:
        total = parse.parse_money(body.total)
    except parse.MoneyParseError:
        findings.add("other", f"合计 total 无法解析，渠道原文 {body.total!r}——已跳过自校验")
        return
    cost, tax, freight = (
        amounts["purchase_cost"],
        amounts["tax_amount"],
        amounts["freight_cost"],
    )
    if total is None or cost is None or tax is None or freight is None:
        # 缺任一项都没法判断「不平」——那是渠道少给，不是我们算错。
        log.info("plugin.backfill.self_check_skipped", po_id=body.id)
        return
    got = cost + tax + freight
    if got != total:
        findings.add(
            "other",
            f"金额自校验不平：税前 {cost} + 税 {tax} + 运费 {freight} = {got}，渠道合计为 {total}",
        )


async def _asin_check(
    session: AsyncSession, po: dict[str, Any], body: schemas.PluginBackfillIn, findings: _Findings
) -> None:
    """回填后核对：实际买到的 ASIN 集合 == 该单应买的集合。不符 → `product`（07:190）。

    **不可前置化**：`asinCheck` 的语义就是「拍单**后**实际值与下单时是否一致」，
    买错东西必须能被发现。渠道没给 `asins` 时跳过——「没得核对」不等于「核对通过」，
    故不落任何结论。
    """
    got = set(parse.split_asins(body.asins))
    if not got:
        return
    rows = (
        await session.execute(text(_ORDER_ASINS_SQL), {"o": po["order_id"], "d": po["order_date"]})
    ).all()
    expected = {str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()}
    if got != expected:
        findings.add(
            "product",
            f"回填 ASIN 与订单不符：期望 {sorted(expected)}，实收 {sorted(got)}",
        )


def _postcode_check(
    ship_to: dict[str, Any], body: schemas.PluginBackfillIn, findings: _Findings
) -> None:
    """回填后核对：实际寄往的邮编 == 订单收货邮编。不符 → `address`（07:191）。

    比对前两侧都归一到主段（`36759` 与 `36759-0000` 视为相同）——假阳会让运营不再
    相信这类异常，于是真的那条也没人看。
    """
    got = parse.join_postcode(body.mainPostCode, body.extPostCode)
    if got is None:
        return
    expected = ship_to.get("postalCode")
    if not parse.postcode_matches(got, None if expected is None else str(expected)):
        findings.add(
            "address",
            f"回填邮编与订单收货邮编不符：订单 {expected!r}，实收 {got!r}",
            severity="warn",
        )


async def _idempotency_gate(
    session: AsyncSession, principal: PluginPrincipal, po: dict[str, Any], ref: str | None
) -> dict[str, Any] | None:
    """以 `purchase_order_ref` 为**自然幂等键**。返回非 None 表示本次到此为止。

    **不用 `Idempotency-Key`**：插件侧没有生成它的地方；自然键更强——跨插件重装、
    换实例、换浏览器仍然成立。

    | 现值 vs 本次 | 处置 |
    |---|---|
    | 现值为空 | 正常首次回填（返回 None，继续走） |
    | 相同 | **200 no-op**，一列不动（重投/网络重试） |
    | 不同 | **不覆盖原值** + `other` + critical 告警——**这是重复下单的形状，必须让人看见** |
    """
    existing = _clean(po["purchase_order_ref"])
    if existing is None or ref is None or existing == ref:
        if existing is not None and existing == ref:
            log.info("plugin.backfill.idempotent", po_id=po["id"], ref=ref)
            return {"id": int(po["id"]), "status": po["status"], "idempotent": True}
        return None

    reason = f"同一任务回填了两个不同的渠道单号：已有 {existing}，本次 {ref}"
    await _mark_kind(
        session,
        int(po["id"]),
        kind="other",
        reason=_append_reason(po["exception_reason"], reason),
    )
    await _alert(
        session,
        principal,
        int(po["id"]),
        severity="critical",
        title="同一采购任务回填了两个渠道单号",
        body=f"执行单 #{po['id']}：{reason}。原单号未被覆盖——请核实是否发生重复下单",
        dedupe_key=f"plugin.dup_ref.{po['id']}",
    )
    return {"id": int(po["id"]), "status": po["status"], "conflict": True}


async def record_purchase_finish(
    session: AsyncSession, principal: PluginPrincipal, body: schemas.PluginBackfillIn
) -> dict[str, Any]:
    """端点 2：采购完成回填（金额/单号/卡后四位/预计送达 + 回填后核对）。

    顺序是有讲究的：**越权闸 → 档位 → 幂等 → 解析与核对 → 落库 → 告警**。
    幂等排在解析之前，是为了让重投连一次核对告警都不重复发；核对排在落库之前，
    是因为 `exception_kind` 与业务列要在同一条 UPDATE 里落地（同一事务，不会出现
    「金额进去了、异常标记没进去」的中间态）。

    返回体里的 `exception_kind` 是**本次核对的结论**：live/演练档同时也是落库值，
    `dry_run` 档只回结论不落库（那一档的全部意义就是「只告诉我核对结果」）。
    """
    po = await load_team_plugin_po(session, principal, body.id)
    po_id = int(po["id"])
    mode = _resolve_exec_mode(principal, body.execMode, po_id=po_id)
    ref = _clean(body.platformOrderNo)

    settled = await _idempotency_gate(session, principal, po, ref)
    if settled is not None:
        return settled

    findings = _Findings()
    amounts = _parse_amounts(body, findings)
    _self_check(body, amounts, findings)
    await _asin_check(session, po, body, findings)
    facts = await _order_facts(session, po)
    _postcode_check(facts["ship_to"], body, findings)

    # **渠道单号是「钱已经花出去了」的实证**，比实例上配的档位更硬：演练档不该拿得到
    # 一个真实的亚马逊订单号。出现即按已下单入账（D2：不记账比记错更糟），同时报警。
    if ref is not None and mode != "live":
        findings.add(
            "other",
            f"实例执行档为 {mode}（该档不应真实下单），但本次回填带回了渠道单号 {ref}"
            "——已按已下单入账，请核实实例档位与插件版本",
        )
        mode = "live"

    params: dict[str, Any] = {
        "p": po_id,
        "ref": ref,
        "cost": amounts["purchase_cost"],
        "tax": amounts["tax_amount"],
        "freight": amounts["freight_cost"],
        "card": _clean(body.creditCardNumber),
        "ddate": parse.parse_iso_date(body.deliveryTime),
        "draw": _clean(body.origDeliveryTime),
        "inst": principal.instance_id,
        "kind": findings.kind,
        "reason": _append_reason(po["exception_reason"], findings.text),
        "from_": list(_PURCHASABLE_FROM),
    }

    if mode == "dry_run":
        # 只校验档：**一个金额列都不写**。核对结论也只进 note——写 exception_kind 会让
        # 一次纯演练在运营的异常列表里留下一张需要处置的单。
        marker = _marker("dry_run 校验回传", principal, findings)
        await session.execute(
            text(_DRY_RUN_SQL),
            {"p": po_id, "note": _append_note(po["note"], marker, po_id=po_id)},
        )
        status = str(po["status"])
    elif mode == "stop_before_payment":
        marker = _marker("stop_before_payment 演练", principal, findings)
        await session.execute(
            text(_DRILL_BACKFILL_SQL),
            {**params, "note": _append_note(po["note"], marker, po_id=po_id)},
        )
        status = str(po["status"])
    else:
        await session.execute(text(_LIVE_BACKFILL_SQL), params)
        status = "purchased" if po["status"] in _PURCHASABLE_FROM else str(po["status"])
        # 渠道订单联动：checked|assigned → purchasing（与人工回填同一入口，不另写 SQL）。
        await procurement.advance_order(
            session,
            order_id=po["order_id"],
            order_date=po["order_date"],
            to_status="purchasing",
            from_statuses=("checked", "assigned"),
        )

    if findings:
        await _alert(
            session,
            principal,
            po_id,
            severity=findings.severity,
            title="采购回填核对未通过",
            body=f"执行单 #{po_id}（执行档 {mode}）：\n{findings.text}",
            dedupe_key=f"plugin.backfill_check.{po_id}.{findings.kind}",
        )
    return {"id": po_id, "status": status, "exception_kind": findings.kind}


def _marker(label: str, principal: PluginPrincipal, findings: _Findings) -> str:
    """演练/校验档往 `note` 里留的一行痕迹（实例 + 核对结论）。

    **不带时间戳**（审查 B3）：这一行同时是幂等键——`_append_note` 靠「note 里已经有这串」
    判重。带上秒级时间戳的话，同一个请求重投两次就会追加两行内容完全相同、只有秒数不同的
    痕迹，而重投恰是插件侧最常见的形态（网络重试、页面刷新）。「什么时候跑的」由
    `updated_at` 与结构化日志承载，不必也不该塞进这一列。
    """
    head = f"[{label} instance={principal.instance_id}]"
    return f"{head} {findings.text}" if findings else head


# ── 端点 3：拍单失败（status=99）──


async def record_task_failure(
    session: AsyncSession, principal: PluginPrincipal, body: schemas.PluginOrderStatusIn
) -> dict[str, Any]:
    """端点 3：拍单失败落异常。**零金额列写入**（验收⑧ 的服务端半段就靠这条）。

    `status='exception'` 只在**尚未产生采购**时才写（`_FAILABLE_FROM`）：若该单已是
    `purchased`/`shipped`，说明钱已经花出去了，把它改成 exception 会抹掉「已拍单」
    这个事实——运营看到会以为没拍，然后**重拍一次**。那种情形只落分类与原文并升级告警。

    该单此后不会再被自动派发：`status='exception'` 不在 `dispatch` 的候选集
    （`status='unassigned'`）里，天然停住，不需要额外的「拉黑」动作。
    """
    po = await load_team_plugin_po(session, principal, body.id)
    po_id = int(po["id"])
    raw = _clean(body.failReason)
    kind = classify.classify_failure(raw)
    # 原文一字不改（便于运营辨识与后续归类）；渠道没给原因时也要留下一句可读的话，
    # 否则运营看到的是一张没有任何线索的异常单。
    reason = raw or "渠道未回传失败原因（updateOrderStatus status=99）"
    stopped = po["status"] in _FAILABLE_FROM

    await session.execute(
        text(_FAIL_SQL),
        {
            "p": po_id,
            "kind": kind,
            "reason": _append_reason(po["exception_reason"], reason),
            "from_": list(_FAILABLE_FROM),
        },
    )
    severity: Severity = "critical" if (kind in classify.CRITICAL_KINDS or not stopped) else "warn"
    body_text = f"执行单 #{po_id} 拍单失败（归类 {kind}）：{reason}"
    if not stopped:
        body_text += (
            f"\n⚠️ 该单当前状态为 {po['status']}（已产生采购），状态未改为 exception"
            "——请核实是否已实际下单，避免重复采购"
        )
    await _alert(
        session,
        principal,
        po_id,
        severity=severity,
        title="采购插件拍单失败",
        body=body_text,
        dedupe_key=f"plugin.task_failed.{po_id}.{kind}",
    )
    return {"id": po_id, "status": "exception" if stopped else str(po["status"]), "kind": kind}


# ── 端点 4：渠道回报 91/92 ──


_CHANNEL_STATUS_KINDS: dict[int, str] = {91: "channel_cancelled", 92: "order_not_found"}


async def record_channel_status(
    session: AsyncSession, principal: PluginPrincipal, body: schemas.PluginAmzOrderStatusIn
) -> dict[str, Any]:
    """端点 4：渠道侧回报（91=已取消/退款，92=订单不存在）。**`status` 一律不动**（D1）。

    这两条发生在**已拍单之后**——把 status 改成 exception 会抹掉「已拍单」这个事实。
    我方也**不复制厂商的双枚举结构**（他们把 91/92 放在物流状态里）：统一收进
    `exception_kind` 的两个细分，物流状态另走既有字段（07:168-170）。

    `orderNo` 只做一致性校验，**不符仅记 warn 不拒**：渠道单号漂移不该挡住
    「渠道那边把这单取消了」这个事实入库——那是要发起退款/换购的信号。
    """
    po = await load_team_plugin_po(session, principal, body.id)
    po_id = int(po["id"])
    kind = _CHANNEL_STATUS_KINDS[body.status]
    reason = f"渠道回报状态 {body.status}（91=已取消/退款，92=订单不存在）"

    claimed = _clean(body.orderNo)
    if claimed is not None:
        facts = await _order_facts(session, po)
        if facts["channel_order_no"] and claimed != str(facts["channel_order_no"]):
            log.warning(
                "plugin.channel_status.order_no_mismatch",
                po_id=po_id,
                claimed=claimed,
                expected=facts["channel_order_no"],
            )

    await session.execute(
        text(_CHANNEL_STATUS_SQL),
        {"p": po_id, "kind": kind, "reason": _append_reason(po["exception_reason"], reason)},
    )
    await _alert(
        session,
        principal,
        po_id,
        severity="critical",
        title="渠道侧回报采购订单异常",
        body=(
            f"执行单 #{po_id}：{reason}。执行单状态保持 {po['status']} 不变"
            "（已拍单的事实不能抹掉）。⚠️ 当前需人工处置，且系统内暂无重买通道："
            "已拍单的插件单回不了待派池（释放守卫拒绝已有单号的单，防二次扣款），"
            "同一订单也开不了第二张执行单（混派闸）——重买请先线下处置，"
            "系统侧通道的缺口已立单 FX-0731A，落地前别在界面上找按钮"
        ),
        dedupe_key=f"plugin.channel_status.{po_id}.{kind}",
    )
    return {"id": po_id, "status": str(po["status"]), "exception_kind": kind}


# ── 端点 7：物流回填 + 轨迹事件 ──


def _tracking_amount(raw: str | float | None, field: str, po_id: int) -> Decimal | None:
    """端点 7 的金额只补空洞，**解析失败不升级成异常**——只记日志。

    权威在端点 2；把物流同步里的一次解析失败也变成异常单，只会在真正要看的异常里
    掺噪声。留 NULL 与写 0 的区别照旧：这里返回 None，绝不返回 0。
    """
    try:
        return parse.parse_money(raw)
    except parse.MoneyParseError:
        log.warning("plugin.tracking.bad_amount", po_id=po_id, field=field, raw=raw)
        return None


async def _write_events(
    session: AsyncSession, principal: PluginPrincipal, po: dict[str, Any], raw_json: str | None
) -> int:
    """写结构化轨迹事件。**`seq` = 渠道数组下标，0 = 最新，不排序不反转**（0046 头注）。

    整体解析失败时**不影响主表**：运单号是承重的，事件是可重抓的投影——
    不能因为轨迹解析不出来就把运单号一起丢掉。
    """
    po_id = int(po["id"])
    if not raw_json or not raw_json.strip():
        return 0
    try:
        parsed = json.loads(raw_json)
    except (TypeError, ValueError):
        parsed = None
    if not isinstance(parsed, list):
        log.warning("plugin.tracking.bad_json", po_id=po_id)
        await _alert(
            session,
            principal,
            po_id,
            severity="warn",
            title="采购物流轨迹解析失败",
            body=f"执行单 #{po_id} 的 trackingJson 不是合法的 JSON 数组，轨迹事件未入库"
            "（运单号已照常回填）",
            dedupe_key=f"plugin.tracking_json.{po_id}",
        )
        return 0

    if len(parsed) > _MAX_TRACKING_EVENTS:
        # 截断而不是拒收：运单号与主表已经写好了，为一条超长轨迹把整次回填退回去，
        # 代价是「包裹在路上但系统不知道运单号」——比丢掉最老的几条轨迹严重得多。
        log.warning("plugin.tracking.too_many_events", po_id=po_id, count=len(parsed))
        await _alert(
            session,
            principal,
            po_id,
            severity="warn",
            title="采购物流轨迹条数超上限，已截断",
            body=(
                f"执行单 #{po_id} 本次回传 {len(parsed)} 条轨迹事件，超过上限"
                f" {_MAX_TRACKING_EVENTS}——只保留最新的 {_MAX_TRACKING_EVENTS} 条"
                "（运单号已照常回填；轨迹可重新同步）。请核实插件版本与回传载荷"
            ),
            dedupe_key=f"plugin.tracking_overflow.{po_id}",
        )
        parsed = parsed[:_MAX_TRACKING_EVENTS]

    written = 0
    for seq, item in enumerate(parsed):
        if not isinstance(item, dict):
            log.warning("plugin.tracking.bad_event", po_id=po_id, seq=seq)
            continue
        desc = _clean(item.get("tracking_info"))
        if desc is None:
            # `description` NOT NULL：没有事件描述的一条轨迹没有任何信息量。
            log.warning("plugin.tracking.event_without_description", po_id=po_id, seq=seq)
            continue
        day, clock = _clean(item.get("day")), _clean(item.get("time"))
        await session.execute(
            text(_EVENT_UPSERT_SQL),
            {
                "po": po_id,
                "t": po["team_id"],
                # 解析失败仍入库，原文两列照存（0046 头注：丢事件等于丢轨迹）。
                "at": parse.parse_event_time(day, clock),
                "day": day,
                "time": clock,
                "desc": desc,
                "city": _clean(item.get("city")),
                "state": _clean(item.get("state")),
                "seq": seq,
            },
        )
        written += 1
    return written


async def record_tracking(
    session: AsyncSession, principal: PluginPrincipal, body: schemas.PluginTrackingIn
) -> dict[str, Any]:
    """端点 7：物流回填（运单号 + 承运商 + 结构化轨迹）。

    **`trackingHtml` / `trackingUrl` / `receiptPicture` 接收后立即丢弃**——base64 整页
    HTML 单条数十至数百 KB、万单即 GB 级，其信息已由 `procurement_logistics_event`
    结构化承载（图纸 07:245-247 的明确不做决定）。这三个字段在 schema 里声明，
    只是为了如实登记「插件确实发了、我们确实丢了」。

    **绝不动 `channel_order.internal_status`**（D6）：那一列是「我方已发货给 Walmart
    买家」，由 `order/ship.py` 驱动。亚马逊侧包裹在路上 ≠ 我方已发货。
    """
    po = await load_team_plugin_po(session, principal, body.orderId)
    po_id = int(po["id"])
    tracking_no = _clean(body.trackingNumber)

    await session.execute(
        text(_TRACKING_SQL),
        {
            "p": po_id,
            "carrier": _clean(body.carrier),
            "tracking_no": tracking_no,
            "card": _clean(body.card),
            "ddate": parse.parse_iso_date(body.deliveryDate),
            "draw": _clean(body.origDeliveryDate),
            "cost": _tracking_amount(body.totalBeforeTax, "totalBeforeTax", po_id),
            "tax": _tracking_amount(body.tax, "tax", po_id),
            "freight": _tracking_amount(body.shipping, "shipping", po_id),
        },
    )
    events = await _write_events(session, principal, po, body.trackingJson)
    status = "shipped" if (tracking_no and po["status"] == "purchased") else str(po["status"])
    return {"id": po_id, "status": status, "events": events}
