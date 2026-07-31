"""采购执行单服务（R2-05 增量3；D-Q50 双入口①②——外部门户③随 R2#6）。

- 状态机：unassigned → assigned → claimed → purchased/shipped → backfilled（+exception/cancelled）。
  本单实现 unassigned/assigned/claimed/backfilled/exception；purchased/shipped 细分随物流接入。
- 汇率锁定（D-Q32/评审 C4）：领单或回填时从 purchaser.exchange_rate 锁快照
  ——之后改采购方汇率不影响已锁单。
- order_block 档位（automation_policy，07:82）：manual=纯软标记（默认）；
  semi/auto=存在未放行 flagged 的订单冻结在 checked，不允许建单/分配。
- 订单联动：assign/claim → internal_status=assigned；backfill → purchasing；
  release → 退回 checked。
- 插件路径（R2-13 13b）走另一条支线：`buyer_account_id` 非空即代表「已派给买家账号」，
  派发算法在 `order/dispatch.py`，本文件负责两件事：
  ① **与它互斥**——人工写动作一律过 `_reject_if_plugin_dispatched`（assign/claim/backfill）；
  ② **给它出边**——`release_po` 把插件异常单显式释放回待派池（0045 头注二的承诺）。
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.automation import AutomationFlow, Mode, resolve_mode
from erp.core.errors import BusinessError

BACKFILL_FIELDS = (
    "purchase_platform",
    "purchase_order_ref",
    "purchase_cost",
    "purchase_currency",
    "freight_cost",
    "carrier",
    "tracking_no",
    "note",
    # R2-13 0045 新增的四列同属「回填」语义（人工在订单页也要能补），故进本表。
    # **`exception_kind` 不在此列**——它是问题分类不是回填字段，走 exception_po 的 kind 参数。
    "tax_amount",
    "payment_card_last4",
    "delivery_est_date",
    "delivery_est_raw",
)

# R2-13 审查 A2：同一渠道订单的「在途执行单」唯一性（建单入口的应用层闸）。
#
# `uq_po_active_dispatch`（0045）的谓词带 `buyer_account_id IS NOT NULL`，**只约束插件侧**
# ——人工建单那条路它完全不管。于是有一个与「混派」等价的双买口子：插件已把某订单派给
# 买家账号（`assigned` + `buyer_account_id` 非空），运营在订单页对**同一渠道订单**再建
# 一张全新执行单（新行、`buyer_account_id` 为 NULL ⇒ 部分唯一索引整条谓词不成立、
# DB 放行），然后分配给采购方去下单——两边都真下单。
#
# 修法只能在建单入口挡，**不能去收紧那条索引**：`test_manual_path_unaffected` 钉着的
# R2-05 既有行为（同一订单可有多张人工执行单）会被索引一收就锁死，且索引改动追溯不到
# 存量数据。排除项与 `uq_po_active_dispatch` 的谓词同源再加一个 `backfilled`：
# `cancelled` 是明确作废终态（不排除会锁死「取消后重建单」），`backfilled` 是人工路径
# 的完成终态（单已办完，再建一张是合法的重新采购）。其余状态一律算在途 —— 含
# `pending_review`（13c 的护栏停留位）与 `exception`，**fail-closed**：
# 异常单要重新流转走的是显式出边（`release_po`），不是再开一张单绕过去。
_IN_FLIGHT_SIBLING_SQL = """
SELECT id FROM app.procurement_order
 WHERE team_id = :t AND order_id = :o
   AND status <> ALL (ARRAY['cancelled', 'backfilled'])
 LIMIT 1
"""


async def _order_block_gate(session: AsyncSession, *, team_id: int, order_id: int) -> None:
    """semi/auto 档 + 未放行 flagged → 冻结（409）。manual（默认）纯软标记放行。"""
    # R2-09 增量1：改走三档内核，不再各写各的 SQL。**行为逐条对齐**——
    # 内核对「无行 / enabled=false / 取值不是三档之一」一律回 manual，与原来
    # `AND enabled` + `scalar_one_or_none()` + `not in ("semi","auto")` 的结果相同；
    # 非法档位（如本 flow 不该有的 semi）内核只告警不改写，故此处仍会拦截。
    mode = await resolve_mode(session, team_id=team_id, flow=AutomationFlow.ORDER_BLOCK)
    if mode not in (Mode.SEMI, Mode.AUTO):
        return
    flagged = (
        await session.execute(
            text(
                "SELECT 1 FROM app.order_check WHERE order_id = :o AND result = 'flagged'"
                " AND resolved_at IS NULL LIMIT 1"
            ),
            {"o": order_id},
        )
    ).first()
    if flagged is not None:
        raise BusinessError(
            "ORDER_BLOCKED",
            "四检 flagged 未放行且 order_block 档位为拦截——放行或调档后再分配",
            http_status=409,
        )


async def _load_po(session: AsyncSession, po_id: int, team_id: int) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, store_id, order_id, order_date, status,"
                    " assignee_kind, purchaser_id, exchange_rate_locked, buyer_account_id,"
                    # R2-13 审查 A3：release_po 的守卫与审计 before 要这四列
                    # （异常分类/原文进审计；单号/拍单时刻是「钱已花」的判据）。
                    " exception_kind, exception_reason, purchase_order_ref, purchased_at"
                    " FROM app.procurement_order WHERE id = :p FOR UPDATE"
                ),
                {"p": po_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["team_id"] != team_id:
        raise BusinessError("PROCUREMENT_NOT_FOUND", "执行单不存在")
    return dict(row)


def _reject_if_plugin_dispatched(po: dict[str, Any], *, action: str) -> None:
    """插件路径与人工路径在应用层的互斥闸（0045 头注二在应用层的对偶）。

    `buyer_account_id` 非空 ＝ 这张单已经派给某个买家账号、插件**可能此刻正在真下单**。
    此时任何人工写动作都会造成第二次采购、或掩盖第一次采购：

    - 改派人工采购方 / 人工领单 → 机器人与人各下一次单，**双重下单**；
    - 人工回填 → `status` 落 `'backfilled'`，而插件侧的白名单
      （`plugin/service.py::_PURCHASABLE_FROM`）**不含** `backfilled`，插件真下出去的
      那笔金额从此再也写不进来——「钱花了系统不知道」，与账号闸那条纪律同一根。

    DB 侧的 `uq_po_active_dispatch` 管不到这一层：它的谓词是「一个 order 一个买家账号」，
    而这里是「买家账号 + 人工」的混派。唯一出路是先把单显式处置掉
    （`release_po` 释放回池 / 标异常 / 取消），**不是放宽本守卫**。
    """
    if po["buyer_account_id"] is not None:
        raise BusinessError(
            "PROCUREMENT_PLUGIN_DISPATCHED",
            f"该执行单已派给买家账号（插件可能正在执行），不能{action}"
            "——请先处置该单（释放回池 /release、标异常或取消）再操作",
            http_status=409,
        )


async def advance_order(
    session: AsyncSession,
    *,
    order_id: int,
    order_date: Any,
    to_status: str,
    from_statuses: tuple[str, ...],
) -> None:
    """渠道订单 `internal_status` 的条件推进（`from_statuses` 之外一律不动）。

    R2-13 13d 起有第二个消费者（`plugin/service.py` 的采购完成回填），故由模块私有
    改为公开——**唯一的推进入口只应有这一处**，两处各写各的 UPDATE 迟早会漂。
    """
    await session.execute(
        text(
            "UPDATE app.channel_order SET internal_status = :to"
            " WHERE id = :o AND order_date = :d AND internal_status = ANY(:froms)"
        ),
        {"to": to_status, "o": order_id, "d": order_date, "froms": list(from_statuses)},
    )


async def create_po(
    session: AsyncSession,
    *,
    team_id: int,
    order_id: int,
    purchaser_id: int | None,
    actor_id: int | None,
) -> dict[str, Any]:
    order = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, store_id, order_date, internal_status"
                    " FROM app.channel_order WHERE id = :o FOR UPDATE"
                ),
                {"o": order_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if order is None or order["team_id"] != team_id:
        raise BusinessError("ORDER_NOT_FOUND", "订单不存在")
    if order["internal_status"] not in ("checked", "assigned"):
        raise BusinessError(
            "ORDER_STATE_INVALID",
            f"订单状态 {order['internal_status']} 不可建执行单（需已过四检且未进入履约终态）",
        )
    # 在途兄弟单闸（审查 A2，口径见 `_IN_FLIGHT_SIBLING_SQL` 头注）。放在档位闸**之前**：
    # 它是关于这张订单自身的事实、且不读配置，比 order_block 更具体也更便宜。
    sibling = (
        await session.execute(text(_IN_FLIGHT_SIBLING_SQL), {"t": team_id, "o": order_id})
    ).first()
    if sibling is not None:
        raise BusinessError(
            "PROCUREMENT_ORDER_IN_FLIGHT",
            f"该渠道订单已有在途执行单 #{int(sibling[0])}，不能再建第二张"
            "——同时存在两张在途单会导致同一订单被下两次（插件与人工各一次）；"
            "请先处置已有的那张（回填 / 释放回池 / 取消）",
            http_status=409,
        )
    await _order_block_gate(session, team_id=team_id, order_id=order_id)
    po_id = (
        await session.execute(
            text(
                "INSERT INTO app.procurement_order"
                " (team_id, store_id, order_id, order_date, created_by)"
                " VALUES (:t, :s, :o, :d, :u) RETURNING id"
            ),
            {
                "t": team_id,
                "s": order["store_id"],
                "o": order_id,
                "d": order["order_date"],
                "u": actor_id,
            },
        )
    ).scalar_one()
    if purchaser_id is not None:
        await assign_po(
            session, team_id=team_id, po_id=po_id, purchaser_id=purchaser_id, actor_id=actor_id
        )
    return {"id": int(po_id), "order_id": order_id}


async def assign_po(
    session: AsyncSession, *, team_id: int, po_id: int, purchaser_id: int, actor_id: int | None
) -> None:
    po = await _load_po(session, po_id, team_id)
    if po["status"] not in ("unassigned", "assigned"):
        raise BusinessError(
            "PROCUREMENT_STATE_INVALID", f"状态 {po['status']} 不可分配", http_status=409
        )
    _reject_if_plugin_dispatched(po, action="同时改派人工采购方")
    purchaser = (
        (
            await session.execute(
                text("SELECT id, team_id, purchaser_kind, status FROM app.purchaser WHERE id = :p"),
                {"p": purchaser_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if purchaser is None or purchaser["team_id"] != team_id:
        raise BusinessError("PURCHASER_NOT_FOUND", "采购方不存在")
    if purchaser["status"] != "active":
        raise BusinessError("PURCHASER_DISABLED", "采购方已停用")
    await _order_block_gate(session, team_id=team_id, order_id=po["order_id"])
    await session.execute(
        text(
            "UPDATE app.procurement_order SET purchaser_id = :p, assignee_kind = :ak,"
            " status = 'assigned', assigned_by = :u, assigned_at = now() WHERE id = :id"
        ),
        {"p": purchaser_id, "ak": purchaser["purchaser_kind"], "u": actor_id, "id": po_id},
    )
    await advance_order(
        session,
        order_id=po["order_id"],
        order_date=po["order_date"],
        to_status="assigned",
        from_statuses=("checked",),
    )


async def claim_po(session: AsyncSession, *, team_id: int, po_id: int) -> None:
    """内部领单（D-Q50①）：assigned → claimed，并锁定汇率快照（D-Q32）。"""
    po = await _load_po(session, po_id, team_id)
    if po["status"] != "assigned":
        raise BusinessError(
            "PROCUREMENT_STATE_INVALID", f"状态 {po['status']} 不可领单", http_status=409
        )
    # 审查 A1：**插件派发落的正是 `assigned`**，上面那句状态判断放行它 ⇒ 人工领单 200 ⇒
    # 机器人与人各下一次单。`assign_po` 早有同款守卫，这里与 `backfill_po` 当时漏了。
    _reject_if_plugin_dispatched(po, action="人工领单")
    await session.execute(
        text(
            "UPDATE app.procurement_order SET status = 'claimed', claimed_at = now(),"
            " exchange_rate_locked = coalesce(exchange_rate_locked,"
            "   (SELECT exchange_rate FROM app.purchaser WHERE id = purchaser_id))"
            " WHERE id = :id"
        ),
        {"id": po_id},
    )


async def backfill_po(
    session: AsyncSession,
    *,
    team_id: int,
    po_id: int,
    actor_id: int | None,
    fields: dict[str, Any],
) -> None:
    """回填采购与物流（**人工路径**）。运营代填（assignee 为 none）标 op_direct（D-Q50②）。

    插件的回填走 `plugin/service.py` 的 `_LIVE_BACKFILL_SQL`（落 `purchased`，D5），
    不经本函数——故本函数对插件派发单一律拒（见下方守卫）。
    """
    po = await _load_po(session, po_id, team_id)
    if po["status"] not in ("unassigned", "assigned", "claimed", "purchased"):
        raise BusinessError(
            "PROCUREMENT_STATE_INVALID", f"状态 {po['status']} 不可回填", http_status=409
        )
    # 审查 A1：允许的四个状态里 `assigned`/`purchased` 正是插件单会停的位置。人工在这里
    # 回填会把 `status` 推成 `backfilled`，而插件侧白名单不含它 ⇒ 插件真下出去的金额
    # 此后永远写不进来；同时运营看到「未拍单」而自行采购一次，就是第二次扣款。
    #
    # 「账号被停时写路径必须照常通」那条纪律**没有被这条守卫削弱**：那条说的是
    # **插件的**写路径（13a 认证域，`test_paused_account_pulls_nothing_but_writes_authenticate`
    # 钉的就是它），本守卫挡的是**人工的**写路径，两者不是同一条路。
    _reject_if_plugin_dispatched(po, action="人工回填")
    sets = ["status = 'backfilled'", "backfilled_at = now()",
            "purchased_at = coalesce(purchased_at, now())",
            "backfill_actor_kind = :bak", "backfill_actor_id = :actor",
            "exchange_rate_locked = coalesce(exchange_rate_locked,"
            " (SELECT exchange_rate FROM app.purchaser WHERE id = purchaser_id))"]  # fmt: skip
    params: dict[str, Any] = {
        "id": po_id,
        "bak": "op_direct" if po["assignee_kind"] == "none" else "internal",
        "actor": actor_id,
    }
    for f in BACKFILL_FIELDS:
        if f in fields and fields[f] is not None:
            sets.append(f"{f} = :{f}")
            params[f] = fields[f]
    await session.execute(
        text(f"UPDATE app.procurement_order SET {', '.join(sets)} WHERE id = :id"), params
    )
    await advance_order(
        session,
        order_id=po["order_id"],
        order_date=po["order_date"],
        to_status="purchasing",
        from_statuses=("checked", "assigned"),
    )


async def exception_po(
    session: AsyncSession, *, team_id: int, po_id: int, reason: str, kind: str | None = None
) -> None:
    """标异常。`kind` = 问题分类（0045 的九值词表），可空。

    `exception_kind` 用 `coalesce(:kind, exception_kind)` 写：不传就保持原值，
    **不会把已有的分类抹成 NULL**——重复标异常（比如插件先落 product 类、人再补一句
    原因）不该丢掉第一次的归类。
    """
    po = await _load_po(session, po_id, team_id)
    if po["status"] in ("backfilled", "cancelled"):
        raise BusinessError(
            "PROCUREMENT_STATE_INVALID", f"状态 {po['status']} 不可标异常", http_status=409
        )
    await session.execute(
        text(
            "UPDATE app.procurement_order SET status = 'exception', exception_reason = :r,"
            " exception_kind = coalesce(:kind, exception_kind) WHERE id = :id"
        ),
        {"r": reason, "kind": kind, "id": po_id},
    )


async def release_po(session: AsyncSession, *, team_id: int, po_id: int) -> dict[str, Any]:
    """人工把**插件异常单**释放回待派池——0045 头注二承诺的那条「显式出边」。

    0045 头注二把 `exception` 故意留在 `uq_po_active_dispatch` 的谓词内（钱可能已经花了，
    放宽约束＝二次扣款），并写下「异常单要重新流转，走的是**清空 `buyer_account_id`
    的显式出边**」。那条出边此前在代码里**不存在**：插件把单标成 `exception` 后，
    `buyer_account_id` 永远非空 ⇒ 唯一索引长期占着该 `order_id` ⇒ 该渠道订单再也派不出去，
    人工侧又被 `_reject_if_plugin_dispatched` 挡住，整张单卡死。本函数即那条出边。

    三条守卫（顺序即诊断价值，都 409）：

    1. **必须是插件单**（`buyer_account_id` 非空）。人工 purchaser 的 `exception` 单属
       FX-0730B 既有缺口，出边是「删采购方时退回池」（`identity/delete.py`），本次不扩；
    2. **必须停在 `exception`**。`assigned`/`claimed` 是插件正在执行，释放就是把它手里的
       活抽走再派给别人 —— 那才是真的双买；
    3. **必须没拍成单**（`purchase_order_ref` 与 `purchased_at` 都空）。这一条是 0045
       头注二那句「钱已经花了」在代码里的落点：`exception_po` 允许把 `purchased` 的单
       标成 `exception`（它只排除 backfilled/cancelled），于是「已拍单 + exception」这个
       形状是可达的；对它释放回池 ⇒ 重新派发 ⇒ **二次扣款**。fail-closed 挡掉。

    副作用与 14b 退回池那段（`identity/delete.py`）逐条同形：清 `buyer_account_id`
    （否则唯一索引仍占着，单回了池也派不出去）、清 `exception_kind`/`exception_reason`
    （陈旧分类不许随单回池，否则会被插件侧 `coalesce(:kind, exception_kind)` 固化）、
    清 `assigned_by`/`assigned_at`（`unassigned` 的单不该顶着分配时刻），并把渠道订单的
    `internal_status` 从 `assigned` 对称推回 `checked`。

    **额度语义（口径，不是副作用）**：`daily_cap` 的分子按 `buyer_account_id` 计
    （`dispatch.py::_USED_TODAY_SQL`），清空该列即把这一单的今日额度还给原账号。
    这与「释放＝这单没派出去过」自洽——单确实没拍成（守卫 3 已保证）。
    """
    po = await _load_po(session, po_id, team_id)
    if po["buyer_account_id"] is None:
        raise BusinessError(
            "PROCUREMENT_NOT_PLUGIN_DISPATCHED",
            "该执行单不是插件派发单（未挂买家账号），不走本出边"
            "——人工采购方的异常单按 FX-0730B 既有路径处置",
            http_status=409,
        )
    if po["status"] != "exception":
        raise BusinessError(
            "PROCUREMENT_STATE_INVALID",
            f"状态 {po['status']} 不可释放回池（只接受 exception：插件仍在执行的单"
            "不能把活从它手里抽走再派给别人）",
            http_status=409,
        )
    if po["purchase_order_ref"] is not None or po["purchased_at"] is not None:
        raise BusinessError(
            "PROCUREMENT_ALREADY_PURCHASED",
            "该执行单已有采购单号/拍单时刻，释放回池会被重新派发造成二次扣款"
            "——请核实后按取消处置，或由插件重试回填",
            http_status=409,
        )
    await session.execute(
        text(
            "UPDATE app.procurement_order SET status = 'unassigned', buyer_account_id = NULL,"
            " exception_kind = NULL, exception_reason = NULL,"
            " assigned_by = NULL, assigned_at = NULL WHERE id = :id"
        ),
        {"id": po_id},
    )
    await advance_order(
        session,
        order_id=po["order_id"],
        order_date=po["order_date"],
        to_status="checked",
        from_statuses=("assigned",),
    )
    return {
        "id": po_id,
        "status": "unassigned",
        "before": {
            "status": "exception",
            "buyer_account_id": po["buyer_account_id"],
            "exception_kind": po["exception_kind"],
            "exception_reason": po["exception_reason"],
        },
    }


async def plugin_manual_backfill(
    session: AsyncSession,
    *,
    team_id: int,
    po_id: int,
    actor_id: int | None,
    ref: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """人工补录**插件单**的已发生购买（Owner 2026-07-31 裁定异常 #16 口径）。

    场景：插件在亚马逊拍成了单，但回传失败（异常 #16「回传订单失败，请手动复制
    AMZ 订单号回填」）。裁定的操作序：**先去亚马逊买家号核实确有这条购买记录，
    再把单号手填进来**；插件端点 2 重试仍是主通道（一般能自动查到），本函数是
    实例失联/重试无望时的人工兜底。

    这与 `backfill_po`（人工采购的回填）是**两个语义**：那边是「人买的、人回填」，
    推 `backfilled` 终态；这边是「插件买的、人代抄」，按插件路径落 `purchased`
    （D5：插件走 purchased → shipped，物流链随端点 7 继续）。写入面镜像
    `plugin/service.py::_LIVE_BACKFILL_SQL`（USD/汇率 1.0 的 D-Q32 同币种分支——
    插件单 `purchaser_id IS NULL`，取不到采购方费率），主体标 `op_direct`
    （0045 词表；`plugin` 是留给真插件回传的，人代抄不许冒充）。

    与 `release_po` 的关系即裁定的两个分支：查到购买记录 → 走本函数（落 ref 后
    release 的守卫 3 自动拒绝，误释放重买的洞随之闭合）；确认没有购买记录 →
    走 release 释放回池。守卫三条（都 409）：必须插件单 / 必须停在
    assigned·exception（购买可能已发生而 ERP 不知情的两个停位）/ 必须尚无 ref
    （已有 ref 说明回传其实成功了，重复补录按幂等冲突处置——若人工查到的单号与
    库内不同，那是重复下单的形状，走标异常而不是覆盖）。
    """
    po = await _load_po(session, po_id, team_id)
    if po["buyer_account_id"] is None:
        raise BusinessError(
            "PROCUREMENT_NOT_PLUGIN_DISPATCHED",
            "该执行单不是插件派发单——人工采购的回填走 /backfill",
            http_status=409,
        )
    if po["status"] not in ("assigned", "exception"):
        raise BusinessError(
            "PROCUREMENT_STATE_INVALID",
            f"状态 {po['status']} 不可人工补录（只接受 assigned/exception：插件已回传过的"
            "单不需要补录，重复补录请核对库内单号）",
            http_status=409,
        )
    if po["purchase_order_ref"] is not None:
        raise BusinessError(
            "PROCUREMENT_ALREADY_PURCHASED",
            f"该执行单已有采购单号 {po['purchase_order_ref']}——若与你在亚马逊查到的单号"
            "不一致，那是重复下单的形状，请标异常并人工核实，不要覆盖",
            http_status=409,
        )
    sets = [
        "purchase_platform = 'amazon'",
        "purchase_currency = 'USD'",
        "exchange_rate_locked = coalesce(exchange_rate_locked, 1.0)",
        "purchase_order_ref = :ref",
        "backfill_actor_kind = 'op_direct'",
        "backfill_actor_id = :actor",
        # 补录即处置完毕：陈旧分类不许留下（release/14b 退回池同一条纪律，
        # 否则会被插件侧 coalesce(:kind, exception_kind) 固化）。
        "exception_kind = NULL",
        "exception_reason = NULL",
        "status = 'purchased'",
        "purchased_at = coalesce(purchased_at, now())",
    ]
    params: dict[str, Any] = {"id": po_id, "ref": ref, "actor": actor_id}
    for f in ("purchase_cost", "tax_amount", "freight_cost", "payment_card_last4"):
        if fields.get(f) is not None:
            sets.append(f"{f} = :{f}")
            params[f] = fields[f]
    await session.execute(
        text(f"UPDATE app.procurement_order SET {', '.join(sets)} WHERE id = :id"), params
    )
    await advance_order(
        session,
        order_id=po["order_id"],
        order_date=po["order_date"],
        to_status="purchasing",
        from_statuses=("checked", "assigned"),
    )
    return {
        "id": po_id,
        "status": "purchased",
        "before": {
            "status": po["status"],
            "exception_kind": po["exception_kind"],
            "exception_reason": po["exception_reason"],
        },
    }
