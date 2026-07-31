"""插件机器面服务层（R2-13 13a 通道与拉取语义；13d 补回填/异常/物流语义）。

## 三层越权防线（验收③「A 实例取不到 B 账号的任务」就靠这三层）

| 层 | 落点 | 挡什么 |
|---|---|---|
| ① 授权链 | `token → plugin_instance → buyer_account_id`（`plugin/auth.py`） | **唯一授权来源** |
| ② SQL 谓词 | 本文件每条业务 SQL 都带 `buyer_account_id = :bound` | 拿别人的 po_id 来调 |
| ③ RLS | 路由层用 `ctx_tx(team_id=principal.team_id)` | 跨团队（兜底，不是主防线） |

**`customerId` 永远只做一致性校验，绝不作授权依据**——它是请求体/查询串里的自称身份，
把它当依据等于让调用方自选身份。不符即 403，但那是「插件配错了买家号」的诊断信号，
不是安全边界。

## 为什么插件路径不复用 worker 的 `system_tx`

worker 跨团队派任务，必须绕 RLS；插件**绑定唯一团队**（`plugin_instance.team_id`），
没有任何理由自己关掉 RLS 兜底。故只有认证那一步走 `system_tx`（还不知道团队），
之后全部换 `ctx_tx`。本文件的每个函数都假定自己跑在**已绑定团队**的会话里——
`_account_snapshot` 取不到账号行即 fail-closed 回 401，那正是 RLS 生效的证据。

## 本步（13a）的边界

端点 1（拉待采购）与端点 6（拉待同步）**语义完整**；端点 2/3/4/7 只落**通道**——
认证、请求体校验、`load_owned_po` 越权闸都是真的，但业务写入随 13d 落地，
在此之前显式 501。**不做「返回 200 但什么都不写」的桩**：那会让插件以为回填成功，
而钱已经花出去了，是本单最不该制造的失效模式。
"""

from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.errors import BusinessError
from erp.notify.service import notify
from erp.order import dispatch
from erp.plugin import schemas
from erp.plugin.auth import PluginPrincipal, auth_failed

log = structlog.get_logger()

# 拉取批量的上界交给配置中心决定：这里传硬上限，`dispatch.claim_tasks_for_account`
# 内部会 `min(limit, procurement.plugin_dispatch.pull_batch_max)`。插件侧不带数量参数
# （厂商协议就没有），故批量完全由服务端配置控制。
_PULL_LIMIT = dispatch.PULL_BATCH_HARD_MAX

_ACCOUNT_SQL = "SELECT site, status, daily_cap FROM app.buyer_account WHERE id = :a"

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

# 越权闸：**谓词里的 `buyer_account_id = :a` 就是防线②**。取不到行的两种情形
# （不存在 / 是别人的）回同一个错误码——越权应当在日志里一眼看见，同时不泄露存在性。
_OWNED_PO_SQL = """
SELECT id, team_id, order_id, order_date, status, buyer_account_id, purchase_order_ref,
       purchase_cost, tax_amount, freight_cost, exchange_rate_locked, exception_kind
  FROM app.procurement_order
 WHERE id = :p AND buyer_account_id = :a
 FOR UPDATE
"""


def ensure_customer(principal: PluginPrincipal, customer_id: str) -> None:
    """`customerId` 一致性校验（**不是授权**，见模块头注）。

    不符即 403：这通常意味着指纹浏览器里登录的买家号与本实例绑定的账号对不上，
    继续跑下去就是「用 A 的浏览器拍 B 的单」。fail-closed。
    """
    if customer_id != principal.external_customer_id:
        raise BusinessError(
            "PLUGIN_CUSTOMER_MISMATCH",
            "customerId 与本实例绑定的买家账号不符——请确认浏览器登录的账号与实例绑定一致",
            http_status=403,
        )


async def _account_snapshot(session: AsyncSession, principal: PluginPrincipal) -> dict[str, Any]:
    """读本实例绑定账号的路由属性（site / status / daily_cap）。

    取不到行 = 该账号在**当前团队上下文**里不可见。正常路径不可能发生（团队来自实例行），
    真发生就是 RLS 上下文与实例绑定不一致，**按认证失败处理**（fail-closed，不猜）。
    """
    row = (
        (await session.execute(text(_ACCOUNT_SQL), {"a": principal.buyer_account_id}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        log.warning(
            "plugin.account_invisible",
            instance=principal.instance_id,
            account=principal.buyer_account_id,
            team=principal.team_id,
        )
        raise auth_failed()
    return dict(row)


async def touch_last_seen(
    session: AsyncSession, principal: PluginPrincipal, *, version: str | None = None
) -> None:
    """刷新账号与实例的「最近露面」时间。

    **只在两个拉取端点调用**，不放进认证依赖：放进去的话每个写端点都要多一次 UPDATE
    与行锁竞争，而回填/物流那几条路径的并发恰好最高。
    """
    await session.execute(
        text("UPDATE app.buyer_account SET last_seen_at = now() WHERE id = :a"),
        {"a": principal.buyer_account_id},
    )
    await session.execute(
        text(
            "UPDATE app.plugin_instance SET last_seen_at = now(),"
            " version = coalesce(:v, version) WHERE id = :i"
        ),
        {"v": version, "i": principal.instance_id},
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
    """
    ensure_customer(principal, customer_id)
    account = await _account_snapshot(session, principal)
    po_ids, reason = await dispatch.claim_tasks_for_account(
        session,
        team_id=principal.team_id,
        buyer_account_id=principal.buyer_account_id,
        site=account["site"],
        account_status=account["status"],
        daily_cap=account["daily_cap"],
        limit=_PULL_LIMIT,
    )
    await touch_last_seen(session, principal, version=version)
    if not po_ids:
        log.info(
            "plugin.pull.empty",
            instance=principal.instance_id,
            account=principal.buyer_account_id,
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
            await _warn_undeliverable_task(session, principal, po_id, empty=not rows)
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
    session: AsyncSession, principal: PluginPrincipal, po_id: int, *, empty: bool
) -> None:
    """已派给本账号、但下发不出去的单（缺 ASIN / 缺行 / 订单头查不到）。

    **只告警不改状态**：这单已经挂在账号名下（`uq_po_active_dispatch` 也据此挡住重派），
    自动把它退回池会与「插件可能正在执行」冲突；自动标异常又会在没有出边的状态里
    再堆一张。留给人处置，并让人**看得见**。
    """
    body = (
        f"执行单 #{po_id} 已派给买家账号 {principal.buyer_account_id}，"
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
    """
    ensure_customer(principal, customer_id)
    await _account_snapshot(session, principal)
    _, batch_max = await dispatch.dispatch_config(session, principal.team_id)
    rows = (
        await session.execute(text(_SYNC_SQL), {"a": principal.buyer_account_id, "n": batch_max})
    ).mappings()
    out = [
        schemas.PluginSyncOrder(
            id=int(r["po_id"]),
            orderNo=str(r["channel_order_no"]),
            platformOrderNo=str(r["purchase_order_ref"]),
        ).model_dump()
        for r in rows
    ]
    await touch_last_seen(session, principal, version=version)
    return out


async def load_owned_po(
    session: AsyncSession, principal: PluginPrincipal, po_id: int
) -> dict[str, Any]:
    """取一张**属于本实例绑定账号**的执行单并加行锁；否则 403。

    不存在的 po_id 与别人的 po_id **回同一码同一状态**：越权是安全事件，应当在日志里
    一眼看见（`plugin.task_not_owned`），但不该顺带告诉调用方「这个 id 是存在的」。
    """
    row = (
        (await session.execute(text(_OWNED_PO_SQL), {"p": po_id, "a": principal.buyer_account_id}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        log.warning(
            "plugin.task_not_owned",
            instance=principal.instance_id,
            account=principal.buyer_account_id,
            po_id=po_id,
        )
        raise BusinessError(
            "PLUGIN_TASK_NOT_OWNED",
            "该采购任务不属于本插件实例绑定的买家账号",
            http_status=403,
        )
    return dict(row)


def _pending_13d(endpoint: str) -> BusinessError:
    """13a 只通「通道」，业务语义随 13d。**显式 501，不做静默成功的桩。**"""
    return BusinessError(
        "PLUGIN_SEMANTICS_PENDING",
        f"{endpoint} 的服务端语义随 R2-13 13d 落地；本版本只开通了认证与越权闸",
        http_status=501,
    )


async def record_purchase_finish(
    session: AsyncSession, principal: PluginPrincipal, body: schemas.PluginBackfillIn
) -> dict[str, Any]:
    """端点 2：采购完成回填。**13a 只落越权闸**，字段映射与幂等随 13d。"""
    await load_owned_po(session, principal, body.id)
    raise _pending_13d("purchaseOrderFinishUpdate")


async def record_task_failure(
    session: AsyncSession, principal: PluginPrincipal, body: schemas.PluginOrderStatusIn
) -> dict[str, Any]:
    """端点 3：拍单失败（status=99）。**13a 只落越权闸**，归类与告警随 13d。"""
    await load_owned_po(session, principal, body.id)
    raise _pending_13d("updateOrderStatus")


async def record_channel_status(
    session: AsyncSession, principal: PluginPrincipal, body: schemas.PluginAmzOrderStatusIn
) -> dict[str, Any]:
    """端点 4：渠道回报 91/92。**13a 只落越权闸**，`exception_kind` 落库随 13d。"""
    await load_owned_po(session, principal, body.id)
    raise _pending_13d("updateAmzOrderStatus")


async def record_tracking(
    session: AsyncSession, principal: PluginPrincipal, body: schemas.PluginTrackingIn
) -> dict[str, Any]:
    """端点 7：物流回填。**13a 只落越权闸**，运单与事件写入随 13d。"""
    await load_owned_po(session, principal, body.orderId)
    raise _pending_13d("updateTrackingInfo")
