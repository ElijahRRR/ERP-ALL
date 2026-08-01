"""插件机器面的请求/响应模型（R2-13 13a）。

## 字段名逐字保留厂商的 camelCase

本组是**机器协议不是 UI 契约**：fork 版插件的字段组装代码一行不改（13e 回切演练要与
原版逐条对照），所以 ERP 侧照单全收。仓内的 snake_case 惯例适用于 UI 契约面，
worker 拨入协议（`scrape/router.py` 的 `NodeSyncIn` 等）同样是另一套形状。
**不要顺手把这些字段改成 snake_case**——那等于要求同 PR 改插件、且回切时对不上账。

## 请求体全是宽松可空

除了主键 `id` 与状态码，插件送来的每个字段都可能缺失（结账页 DOM 抓不到就是 None）。
**在 schema 层拒收 = 把一次「少抓了张卡号后四位」升级成「整笔回填丢失」**，而钱已经
花出去了。校验与告警放在服务层（13d），schema 只负责结构。

## 金额一律 `str | float`，不在此处转 Decimal

插件送的是结账页原文 `"$1,234.56"`，也可能是数字。转换与「解析失败不写 0」的纪律
统一在 `plugin/parse.py`（13d）里做——放在 schema 会让一个解析不出的金额变成 422，
于是整笔回填被拒，同样是「钱花了系统不知道」。

## 三个明确不落库的字段仍要声明

`trackingHtml`（base64 整页 HTML，万单 GB 级）/ `trackingUrl` / `receiptPicture`：
图纸 `07:245-247` 明确不存。声明它们是为了**如实登记「插件确实发了、我们确实丢了」**
——不声明会让读者以为插件没发，也会在将来有人开 `extra=forbid` 时炸掉真实流量。
"""

from typing import Literal

from pydantic import AliasChoices, BaseModel, Field

EXEC_MODE_PATTERN = "^(dry_run|stop_before_payment|live)$"


class PluginTaskProduct(BaseModel):
    """任务里的一行商品。插件拍单流程只读 `asin` 与 `quantity`。

    `price` / `imgUrl` / `status` 是厂商网页端展示用的字段，插件不读——**不造**。
    """

    asin: str
    quantity: int


class PluginTask(BaseModel):
    """端点 1 下发的一条待采购任务（字段集＝插件实读，`07:216-224`）。

    `state` 与 `receivingState` **两个字段名都返回、同值**：插件写的是
    `order.state || order.receivingState`，两个名字并存是厂商侧的历史包袱，
    少给一个就得赌另一个非空。
    """

    id: int
    orderNo: str
    receivingName: str | None = None
    receivingPhone: str | None = None
    receivingAddress: str | None = None
    receivingCity: str | None = None
    # JP 必填。**日本站本轮不做但字段与分支保留**（铁律 9：不做不等于删掉）。
    receivingDistrict: str | None = None
    receivingPostCode: str | None = None
    receivingCountry: str | None = None
    state: str | None = None
    receivingState: str | None = None
    products: list[PluginTaskProduct] = Field(default_factory=list)
    # 下发执行档：插件按此决定「只校验 / 付款前停 / 完整下单」，回报时原样回声。
    execMode: str


class PluginSyncOrder(BaseModel):
    """端点 6 下发的一条待物流同步订单。

    ⚠️ **取证缺口如实登记**：考古只对端点 1 给出了逐字段清单（`archaeology.md:24`），
    本端点的响应字段**未逐字取证**。此处按最小集声明；fork 时以插件 `handleOrderSync`
    实读字段为准，如有出入**以插件为准并同 PR 修契约**。不许编字段。
    """

    id: int
    orderNo: str
    platformOrderNo: str


class PluginBackfillProduct(BaseModel):
    """回填体里的商品行。四个价格/图片字段**不落库**（图纸未指定落点，不自造落点）。"""

    asin: str | None = None
    quantity: int | None = None
    unitPrice: str | float | None = None
    totalPrice: str | float | None = None
    productImage: str | None = None


class PluginBackfillIn(BaseModel):
    """端点 2 `purchaseOrderFinishUpdate` 请求体（逐字对齐
    `owner-reverse-engineering-20260730.md:456-495`）。

    `execMode` 是 fork 新增的回声字段（补-4 的三档能力）：服务端据此判断插件是不是
    还按旧档在跑——档不符即拒，防「实例已降档但插件仍在真下单」。
    """

    id: int
    platformOrderNo: str | None = None
    asins: str | None = None
    deliveryTime: str | None = None
    origDeliveryTime: str | None = None
    creditCardNumber: str | None = None
    mainPostCode: str | None = None
    extPostCode: str | None = None
    shipping: str | float | None = None
    totalBeforeTax: str | float | None = None
    tax: str | float | None = None
    total: str | float | None = None
    products: list[PluginBackfillProduct] = Field(default_factory=list)
    execMode: str | None = Field(default=None, pattern=EXEC_MODE_PATTERN)


class PluginOrderStatusIn(BaseModel):
    """端点 3 `updateOrderStatus` 请求体：拍单失败。

    `status` 收窄成 `Literal[99]`——**厂商的数字状态机不进我方模型**，只认这一个值，
    其余一律 422。多认一个值就等于把他们的状态枚举悄悄搬进来。

    `failReason` 接受 `failContent` 作为别名：一手来源里 JS 调用签名写的是
    `updateOrderStatus(id, 99, failReason)`（`:213`），而订单对象上的同义字段叫
    `failContent`（`:123`），**请求体到底用哪个名字未逐字取证**。两个名字一个含义，
    都收下比赌一个更稳——这个端点的全部载荷就是这段原文，丢了就没有异常原因了。
    """

    id: int
    status: Literal[99]
    failReason: str | None = Field(
        default=None, validation_alias=AliasChoices("failReason", "failContent")
    )


class PluginAmzOrderStatusIn(BaseModel):
    """端点 4 `updateAmzOrderStatus` 请求体：渠道侧回报（91=已取消/退款，92=订单不存在）。

    `orderNo` 只做一致性校验、不作授权依据（授权只来自实例令牌）。
    """

    id: int
    orderNo: str | None = None
    status: Literal[91, 92]


class PluginTrackingIn(BaseModel):
    """端点 7 `updateTrackingInfo` 请求体（逐字对齐
    `owner-reverse-engineering-20260730.md:568-591`）。

    - 主键叫 `orderId` 而不是 `id`——厂商这一组就是这么发的，别为了「统一」改名。
    - `trackingJson` 是 **JSON 编码的字符串**不是数组，消费方必须先 `json.loads`。
    - `trackingHtml` / `trackingUrl` / `receiptPicture` **接收后立即丢弃**（见模块头注）。
    """

    orderId: int
    trackingNumber: str | None = None
    trackingUrl: str | None = None
    trackingHtml: str | None = None
    mainPostCode: str | None = None
    extPostCode: str | None = None
    card: str | None = None
    trackingJson: str | None = None
    asins: str | None = None
    orderDate: str | None = None
    subtotal: str | float | None = None
    shipping: str | float | None = None
    totalBeforeTax: str | float | None = None
    tax: str | float | None = None
    total: str | float | None = None
    carrier: str | None = None
    receiptPicture: str | None = None
    deliveryDate: str | None = None
    origDeliveryDate: str | None = None
