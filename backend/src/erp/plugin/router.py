"""采购插件回调面路由（R2-13 13a）——仓内**第四个认证域**。

| 域 | 载体 | 谁在用 |
|---|---|---|
| `bearerAuth` | JWT（audience=erp） | 内部用户 |
| `portalAuth` | JWT（audience=portal） | 采购方门户 |
| `X-Node-Key` + `X-Node-Token` | 机器令牌 | 采集 worker |
| **`X-Plugin-Instance` + `X-Plugin-Token`** | **实例专属机器令牌** | **本文件** |

## 路径末段保留厂商方法名（camelCase）是有意的

`/api/v1/purchase-plugin/getNeedPurchaseOrders` 这种形状不符合仓内 REST 命名惯例，
但**本组是机器协议不是 UI 契约**，与 worker 的 `/worker/v1/tasks/pull` 同性质。
理由两条：① 13e 的回切演练要拿 fork 版与厂商原版逐条对照，路径逐字对应做到零翻译；
② 插件 `CONFIG.API.endpoints` 的键名与末段一一对应，改名就得同步改插件。
**前缀收成一组**（`/purchase-plugin/*`）是 Owner 2026-07-30 的裁定，厂商侧原本分
`amazonOrder` / `amazonOrderPig` 两个命名空间。

## ⛔ 本组故意不含 `updateBuyerCookie`

Owner 2026-07-30 二次裁定「先不收 cookie」，理由**不是「暂时不用」而是用途已被证伪**：
那份 jar 的字段整形（`hostOnly` / `sameSite:'no_restriction'` / `storeId:'1'` /
`id:index+1`）正是 Cookie-Editor 类插件的导出格式，该格式唯一目的是**被导入回浏览器**
——它是整套可搬运的登录态而非标识；「哪个买家号」由同一调用的独立参数 `customerId`
承载。jar 一旦落库，持有者即可以买家身份下单。
出处：`specs/001-domain-model/07-order-sourcing-aftersale.md:286-311`、
`.agent/evidence/R2-13/owner-rulings-20260730.md` 补-1、
`specs/007-mvp-completion-plan/README.md:308-315`。
**ERP 侧不建路由、不建 `buyer_session` 表、任何 cookie jar 不落库**；
`tests/db/test_r2_13_no_cookie_chain.py` 把这条从一次性动作变成结构性不变量。

## 响应信封 `{code, data}` 是厂商兼容回声

插件读的是 `response.data`（一手来源 `:428`「返回结构 `{ code: 200, data: Order[] }`」），
故**成功一律回 `{"code": 200, "data": ...}`**，`code` 恒为 200。
**失败仍走本仓统一 `ErrorResponse` + 真实 HTTP 状态码**——绝不做「HTTP 200 里塞
code:500」那种把错误藏进成功响应的形状。插件侧 fork 改为按 HTTP 状态判定。

## 两段事务：认证 `system_tx`，业务 `ctx_tx`

认证阶段还不知道团队，只能 `system_tx`（同 `scrape/router.py::_node_auth`）；
拿到 principal 之后**必须**换 `ctx_tx(team_id=principal.team_id)`——插件绑定唯一团队，
没有任何理由自己关掉 RLS 兜底。这与 worker 形态的区别是刻意的（worker 跨团队派任务）。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query

from erp.core.db import ctx_tx, get_session_factory, system_tx
from erp.plugin import auth, service
from erp.plugin.auth import PluginPrincipal
from erp.plugin.schemas import (
    PluginAmzOrderStatusIn,
    PluginBackfillIn,
    PluginOrderStatusIn,
    PluginTrackingIn,
)

plugin_router = APIRouter(tags=["PurchasePlugin"], prefix="/purchase-plugin")

CustomerId = Annotated[str, Query(alias="customerId", min_length=1, max_length=64)]
# 插件版本（选填）：拉取时顺带回写 `plugin_instance.version`，便于灰度期间看清
# 「哪台浏览器还在跑旧版」。没带就不动该列。
PluginVersion = Annotated[str | None, Query(alias="v", max_length=40)]


async def _plugin_auth(
    x_plugin_instance: Annotated[str | None, Header()] = None,
    x_plugin_token: Annotated[str | None, Header()] = None,
) -> PluginPrincipal:
    """双头部认证依赖。

    头缺失也回 **401**（而不是 FastAPI 默认的 422 缺参）——「认证失败一律同码同状态」
    这条纪律要覆盖到「压根没带凭证」这一种，否则错误形状本身就能被用来探测端点。
    """
    if not x_plugin_instance or not x_plugin_token:
        raise auth.auth_failed()
    async with system_tx(get_session_factory()) as s:
        return await auth.authenticate_instance(s, x_plugin_instance, x_plugin_token)


PluginAuth = Annotated[PluginPrincipal, Depends(_plugin_auth)]


@plugin_router.get("/getNeedPurchaseOrders")
async def get_need_purchase_orders(
    principal: PluginAuth, customerId: CustomerId, v: PluginVersion = None
) -> dict[str, Any]:
    """端点 1：拉待采购任务（**拉取即认领**，GET 但有副作用；重复调用幂等）。"""
    async with ctx_tx(get_session_factory(), team_id=principal.team_id) as s:
        data = await service.pull_purchase_tasks(s, principal, customer_id=customerId, version=v)
    return {"code": 200, "data": data}


@plugin_router.get("/getNeedSyncOrders")
async def get_need_sync_orders(
    principal: PluginAuth, customerId: CustomerId, v: PluginVersion = None
) -> dict[str, Any]:
    """端点 6：拉待物流同步订单。"""
    async with ctx_tx(get_session_factory(), team_id=principal.team_id) as s:
        data = await service.pull_sync_orders(s, principal, customer_id=customerId, version=v)
    return {"code": 200, "data": data}


@plugin_router.post("/purchaseOrderFinishUpdate")
async def purchase_order_finish_update(
    principal: PluginAuth, body: PluginBackfillIn
) -> dict[str, Any]:
    """端点 2：采购完成回填（金额/单号/卡号后四位/预计送达）。"""
    async with ctx_tx(get_session_factory(), team_id=principal.team_id) as s:
        data = await service.record_purchase_finish(s, principal, body)
    return {"code": 200, "data": data}


@plugin_router.post("/updateOrderStatus")
async def update_order_status(principal: PluginAuth, body: PluginOrderStatusIn) -> dict[str, Any]:
    """端点 3：拍单失败（status=99 + failReason 原文）。"""
    async with ctx_tx(get_session_factory(), team_id=principal.team_id) as s:
        data = await service.record_task_failure(s, principal, body)
    return {"code": 200, "data": data}


@plugin_router.post("/updateAmzOrderStatus")
async def update_amz_order_status(
    principal: PluginAuth, body: PluginAmzOrderStatusIn
) -> dict[str, Any]:
    """端点 4：渠道侧回报（91=已取消/退款，92=订单不存在）。"""
    async with ctx_tx(get_session_factory(), team_id=principal.team_id) as s:
        data = await service.record_channel_status(s, principal, body)
    return {"code": 200, "data": data}


@plugin_router.post("/updateTrackingInfo")
async def update_tracking_info(principal: PluginAuth, body: PluginTrackingIn) -> dict[str, Any]:
    """端点 7：物流回填（运单号 + 承运商 + 轨迹事件）。"""
    async with ctx_tx(get_session_factory(), team_id=principal.team_id) as s:
        data = await service.record_tracking(s, principal, body)
    return {"code": 200, "data": data}
