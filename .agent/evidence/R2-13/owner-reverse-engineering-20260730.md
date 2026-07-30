> **归档说明（审计侧 2026-07-30）**：本文为 Owner 亲自完成的厂商系统逆向分析报告
> （抓包 + 插件源码 + DOM 遍历，12,963 单实数据）。**附录 C/D/E 是 §07「采购插件契约与
> 成本口径」一节的一手来源**——实现时如对图纸转述有疑，以本文为准并回批注给审计侧。
> 姊妹篇（厂商面板字段级分析、状态实测分布、导入契约）在插件仓 `docs/vendor-panel-analysis.md`。

# 小蜜蜂AMZ采购系统 逆向分析报告

> 数据来源：浏览器实时抓包（API 响应体）+ 插件源码（v2.4.1 本地版 / v2.5.0 CRX 最新版对比）+ 页面 DOM 遍历
> 抓取时间：2026-07-30
> 总记录量：12,963 条采购订单

---

## 1. 页面清单

| # | URL | 页面名 | 解决什么问题 | 菜单入口 |
|---|-----|--------|-------------|---------|
| 1 | `/index` | 首页 | 公告通知 + 教程链接 + 插件下载入口 | 顶部导航"首页" |
| 2 | `/order/amazonOrder` | Amazon拍单 | 采购任务全生命周期管理（核心页） | 拍单管理 → Amazon拍单 |
| 3 | `/asinMonitor` | ASIN价格监控 | 监控 ASIN 在 Amazon 的实时库存与价格，计算盈亏 | 顶部导航"ASIN价格监控" |
| 4 | `/user/profile` | 个人中心 | 修改昵称/密码，查看所属角色和部门 | 右上角用户下拉菜单 |

**⚠️ 没有独立的买家账号管理页面。** 账号信息分散在每条订单的"买家号信息"列，账号健康状态只能从订单失败率推断。

---

## 2. 字段表

### 2.1 `/order/amazonOrder` — 筛选区（filter 请求体字段）

| 字段名 | UI 标签 | 类型 | 示例值 | 说明 |
|--------|---------|------|--------|------|
| `storeName` | 店铺名称 | string | `A085朱丽霖` | 模糊匹配 |
| `orderNoList` | 平台订单号 | string | `119121294870509...` | 内部系统单号 |
| `platformOrderNo` | AMZ订单号 | string | `111-5958998-9658617` | Amazon 订单号 |
| `buyerAccount` | 买家号 | string | `环境172民生` | 买家账号人工命名 |
| `remark` | 备注 | string | — | 模糊匹配 |
| `status` | 状态 | enum | 待审核/待拍单/已拍单/拍单异常 | 拍单任务状态 |
| `orderStatus` | 物流状态 | enum | 未同步/运输中/已签收/未发货/已取消 | 物流跟踪状态 |
| `country` | 国家 | enum | 美国 / 加拿大 / 日本 | |
| `createTimeStart` / `createTimeEnd` | 创建时间 | datetime | — | 范围筛选 |
| `purchaseTimeStart` / `purchaseTimeEnd` | 拍单时间 | datetime | — | 范围筛选 |

---

### 2.2 `/order/amazonOrder` — 列表数据（API 实际响应字段）

表格按列组呈现，括号内为 API 字段名。

#### 买家信息列

| 字段名 | 类型 | 示例值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `receivingName` | string | `CONSWELLA COLEMAN JONES` | ✓ | 收件人全名 |
| `receivingCountry` | string | `US` | ✓ | 国家代码 |
| `receivingState` | string | `AL` | ✓ | 州/省缩写 |
| `receivingCity` | string | `Marion Junction` | ✓ | 城市 |
| `receivingAddress` | string | `184 County Road 152` | ✓ | 街道地址 |
| `receivingPostCode` | string | `36759` | ✓ | 邮编 |
| `receivingPhone` | string | `3345099200` | ✓ | 电话 |
| `receivingDistrict` | string | null | 仅日本 | 区（JP 专用） |

#### 产品信息列

| 字段名 | 类型 | 示例值 | 说明 |
|--------|------|--------|------|
| `products[].asin` | string | `B0F8NB1T8Y` | 采购前由导入指定 |
| `products[].price` | number | `9.99` | 导入时的预估单价 |
| `products[].quantity` | integer | `1` | 采购数量 |
| `products[].imgUrl` | string | Amazon CDN URL | 商品图片 |
| `products[].status` | integer | `1` | 商品行状态 |
| `asinCheck` | boolean | `true` | 拍单后 ASIN 与订单是否一致 |
| `priceCheck` | boolean | `false` | 价格校验标志（超阈值时 true） |

#### 订单信息列

| 字段名 | 类型 | 示例值 | 说明 |
|--------|------|--------|------|
| `orderNo` | string | `119121294870509B0F8NB1T8Y` | 内部系统单号（导入时生成） |
| `platformOrderNo` | string | `111-5958998-9658617` | **Amazon 订单号，拍单后回填** |
| `asin` | string | `B0F8NB1T8Y` | 拍单后确认的 ASIN（可与预期不同） |
| `platformPostCode` | string | `36759` | 平台记录的邮编 |
| `postCodeCheck` | boolean | `true` | 邮编校验标志 |
| `deliveryTimeAbnormal` | integer | `0` / `1` | 预计送达是否超出限制天数 |

#### 费用信息列（拍单完成后回填）

| 字段名 | 类型 | 示例值 | 说明 |
|--------|------|--------|------|
| `shipping` | string | `$0.00` | 运费 |
| `totalBeforeTax` | string | `$9.99` | 税前总额（预估价对比基准） |
| `tax` | string | `$0.80` | 税费 |
| `total` | string | `$10.79` | **实付总额** ← price_delta_pct 的分母 |
| `subTotal` | string | null | 小计（部分订单为 null） |

> **价格护栏说明**：插件代码中判断 `priceDifference / orderTotal > 50%` 时取消下单，写入 `failContent`，
> UI 仅通过日志栏红色文字提示，**没有专门的涨价提示弹窗/标记列**。
> 你的 `price_delta_pct` 护栏需要自行设计预警展示层。

#### 物流信息列

| 字段名 | 类型 | 示例值 | 说明 |
|--------|------|--------|------|
| `platformTrackCarrier` | string | null → `UPS` | 承运商，物流同步后填入 |
| `platformTrackNo` | string | null → `1Z...` | 运单号，物流同步后填入 |
| `orderStatus` | integer | `0` | 物流状态枚举（见状态机） |
| `deliveryTime` | date | `2026-08-07` | 预计/实际签收日期 |

#### 买家号信息列

| 字段名 | 类型 | 示例值 | 说明 |
|--------|------|--------|------|
| `buyerAccount` | string | `环境172民生` | 买家账号人工命名 |
| `buyerCustomerId` | string | `A1NS3HIW4IC6P7` | Amazon Customer ID（customerId） |
| `paymentCard` | string | `7883` | 信用卡后4位，拍单后回填 |
| 同步时间（UI） | datetime | `--` | 最后一次物流同步时间，null 显示"--" |

#### 其他信息列

| 字段名 | 类型 | 示例值 | 说明 |
|--------|------|--------|------|
| `status` | integer | `1` | 拍单任务状态（见状态机） |
| `failContent` | string | null / `"商品无库存"` | 异常原因，成功时 null |
| `remark` | string | `""` | 人工备注，可在行内编辑 |
| `createName` | string | `bqhlwkj` | 创建人用户名 |
| `createTime` | datetime | `2026-07-30 10:13:18` | 导入/创建时间 |
| `updateTime` | datetime | null | 最后更新时间 |
| `purchaseTime` | datetime | `2026-07-30 11:19:01` | 插件实际下单时间 |

---

### 2.3 `/asinMonitor` — ASIN 价格监控

**筛选字段**：ASIN、国家（美国/加拿大/日本）、状态

| 列名 | 字段含义 | 说明 |
|------|---------|------|
| 店铺名称 | storeName | |
| ASIN | asin | |
| 国家 | country | |
| 售卖价格 | sellingPrice | 自己店铺的挂牌价 |
| AMZ价格 | amazonPrice | Amazon 实时采购价 |
| 盈亏 | profitLoss | `sellingPrice - amazonPrice` |
| 创建时间 | createTime | |
| 更新时间 | updateTime | 最后一次价格抓取时间 |

**库存状态枚举（原文）**：

| 枚举值 | 中文含义 |
|--------|---------|
| `AVAILABLE_DATE` | 预售 |
| `IN_STOCK` | 在售 |
| `IN_STOCK_SCARCE` | 低库存 |
| `OUT_OF_STOCK` | 缺货 |

---

### 2.4 `/user/profile` — 个人中心

当前账号角色：**拍单+价格**（同时具备拍单管理和价格监控权限）。

| 字段 | 只读/可编辑 | 说明 |
|------|------------|------|
| 用户名称 | 只读 | |
| 所属角色 | 只读 | `拍单+价格` |
| 所属部门 | 只读 | `外部` |
| 创建日期 | 只读 | |
| 用户昵称 | 可编辑 | |
| 手机号码 | 可编辑 | |
| 邮箱 | 可编辑 | |
| 性别 | 可编辑 | 男 / 女 |
| 旧密码 / 新密码 / 确认密码 | 可编辑 | 修改密码专区 |

---

## 3. 状态机

### 3.1 采购任务状态机

```
[导入订单 / 手动创建]
        │
        ▼
  ┌─【待审核】─────────────────────────────────┐
  │     │ [审批按钮]（人工触发）                │
  │     ▼                                    │
  │  【待拍单】← plugin: getNeedPurchaseOrders │
  │     │                                    │
  │   ┌─┴──────────────────────┐             │
  │   │ 拍单成功               │ 拍单失败      │
  │   │ purchaseOrderFinish    │ updateOrderStatus(99)
  │   ▼                        ▼             │
  │ 【已拍单】(status=1)  【拍单异常】(status=99)
  │     │                      │ [批量重置状态]│
  │     │                      └──────────────┘
  │     │                          (回到待拍单)
  │     │
  │     ├─ updateAmzOrderStatus(91) → 【Amazon已取消/退款】
  │     └─ updateAmzOrderStatus(92) → 【订单不存在】
  │
  └── 任意状态 ──[删除]──→ 消失（硬删除，不可恢复）
```

**状态值对照表：**

| 状态名 | status 数值 | 来源确认 |
|--------|------------|---------|
| 待审核 | 未知（推测 0） | UI 下拉枚举 |
| 待拍单 | 未知 | UI 下拉；plugin `getPurchaseOrders` 拉此状态 |
| 已拍单 | **1**（API 响应实测） | `"status":1` + UI 显示"已拍单" |
| 拍单异常 | **99**（代码实测） | `updateOrderStatus(id, 99, failReason)` |
| Amazon 取消/退款 | **91** | `updateAmzOrderStatus(id, orderNo, 91)` |
| 订单不存在 | **92** | `updateAmzOrderStatus(id, orderNo, 92)` |

**你的状态机映射对比：**

| 你的状态 | 他们的对应状态 | 差异/备注 |
|---------|--------------|---------|
| unassigned | 待审核 | 他们有"审批"人工卡点，你可以做成自动或配置 |
| assigned | 待拍单 | 他们的分配是按 customerId 拉取，你可主动派发 |
| claimed | — | **他们没有这个中间态**，插件拉到就直接开始执行 |
| purchased | 已拍单 | 一致 |
| shipped | 运输中/已签收 | 他们拆成 orderStatus 独立字段 |
| backfilled | 已签收 | 他们的"签收"= 物流同步到最终状态 |
| exception | 拍单异常 | 他们有 status=91/92 是你没有的两个细分 exception |

> **你没有的中间态 `claimed`** 说明他们没有设计"抢单"机制——插件拉到一批任务就全量执行，
> 不存在多实例竞争锁定的概念。这是潜在重复下单风险的根源。

---

### 3.2 物流状态机（orderStatus 字段）

```
拍单完成 → orderStatus = 0（未同步）
        │
        │ [plugin: 开始同步] 或 [Amz To Jc轨迹同步]
        │ updateTrackingInfo 写入 trackingNumber + events
        │
        ├──▶【未发货】  有运单号，物流事件为空或仅揽收
        │
        ├──▶【运输中】  有物流事件，未出现签收关键词
        │
        ├──▶【已签收】  物流事件含 delivered / 签收字样
        │
        └──▶【已取消】  Amazon 订单状态变为取消
```

**物流状态枚举（UI 原文，orderStatus 数值未确认）：**
`未同步(0已确认)` / `未发货` / `运输中` / `已签收` / `已取消`

---

### 3.3 买家账号"状态"（无独立状态机，从订单行为推断）

系统**没有独立的账号管理页面和账号状态字段**。健康信号全靠订单列失败率反推：

| 信号 | 推断含义 |
|------|---------|
| `paymentCard` 长期为 null，多条订单 | Cookie 未上传或账号登录态已失效 |
| `failContent` 反复出现"地址保存超时" | 账号可能触发了验证码拦截 |
| `failContent` 反复出现"未找到加入购物车按钮" | 账号可能被限购或已被封 |
| "同步时间" 长期显示 `--` | 该账号 Cookie 从未成功上传 |
| 同一 buyerCustomerId 下订单全部"拍单异常" | 账号整体失效，需人工介入 |

账号与插件绑定方式：**一个 Chrome 实例 = 一个 Amazon 登录态**。插件通过 `chrome.cookies.getAll()` 读取当前浏览器的 Amazon cookie，再用正则从页面 HTML 提取 `customerId`。官方文档未说明一个账号是否能挂多个浏览器，但多实例并发下单存在重复风险（**这正是你的"同一浏览器绝不同时开两个插件"红线的来源**）。

---

## 4. 操作清单

### 4.1 `/order/amazonOrder` — 工具栏按钮

| 按钮 | 触发 API | 前置条件 | 说明 |
|------|---------|---------|------|
| 搜索 | POST `/prod-api/system/amazonOrder/list` | 无 | 带当前筛选条件分页查询 |
| 重置 | 同上（清空参数） | 无 | 清空所有筛选条件 |
| 导入订单 | 上传接口（未抓到） | 有导入权限 | 批量导入 Excel/CSV 创建待审核订单 |
| 导出 | 导出接口（未抓到） | 无 | 下载当前筛选结果为文件 |
| 审批 | 审批接口（未抓到） | 勾选 ≥1 条待审核 | 将待审核批量流转为待拍单 |
| 批量重置状态 | 重置接口（未抓到） | 勾选 ≥1 条拍单异常 | 重置为待拍单，重新入队 |
| 删除 | 删除接口（未抓到） | 勾选 ≥1 条 | 硬删除，不可恢复 |

分页规格：**10 / 20 / 30 / 50 / 100 / 200 / 400 / 500 / 1000 / 2000 / 3000 / 4000 / 5000 条/页**（超大分页用于批量操作时一次性加载全部）。

### 4.2 每行操作（操作列图标）

| 图标类名 | 操作含义 | 触发条件 |
|---------|---------|---------|
| `el-icon-edit` | 编辑备注 | 任意状态可点击 |
| `el-icon-circle-check status-icon success` | ASIN/邮编验证通过标志（绿色 ✓） | `asinCheck=true` 或 `postCodeCheck=true` 时显示 |
| 前缀装饰图标（shop/user/location/house/message/phone） | 各信息字段的分组图标 | 仅展示，不可点击 |

### 4.3 插件控制面板（Chrome Extension Drawer）

| 按钮 | 触发函数 | 做什么 | 前置条件 |
|------|---------|--------|---------|
| 开始同步 | `handleOrderSync` | 1. GET `getNeedSyncOrders` 拉待同步列表<br>2. 上传当前账号 cookie<br>3. 逐单打开 Amazon 订单详情→抓物流链接→打开跟踪页→提取事件→POST 回服务端 | 已提取 customerId，当前页在 Amazon 订单列表 |
| 开始拍单 | `handlePurchase` | 1. GET `getNeedPurchaseOrders` 拉待拍单列表<br>2. 上传 cookie<br>3. 清空购物车<br>4. 逐单执行 加购→结账→填地址→验证→下单→回传订单号 | 同上；仅支持 US/CA/JP 站 |
| 提取 customerId | `handleExtractCustomerId` | 正则提取当前页面 `customerId:"..."` 并显示 | 当前页面含 customerId 信息 |
| Amz To Jc 轨迹同步 | `handleOrderSyncTracking` | 拉 `getNeedSyncOrdersTrack`，仅同步运单号和 firstEventTime，目标为 Jc 平台（`/amazonOrderPig/` 端点） | 已提取 customerId |
| 配置：只拍 N 天内送达 | `saveDeliveryDaysLimit` | 修改本地 localStorage 的送达天数上限（默认 7 天） | 无 |
| 清空日志 | `clearLogOutput` | 清空插件日志窗口显示 | 无 |

---

## 5. 异常类型与处置对照表

以下为插件 `failContent` 字段的所有已知取值（从源码提取），以及运营可执行的动作：

| # | failContent 原文 | 异常分类 | 运营可做动作 |
|---|-----------------|---------|------------|
| 1 | `地址保存超时` | 地址填写超时 | 批量重置 → 重试；多次出现考虑账号是否触发验证码 |
| 2 | `地址列表加载超时` | 地址列表超时 | 同上 |
| 3 | `地址信息不完整，缺少区相关信息` | 订单数据问题（JP） | 补充订单 receivingDistrict 字段后重置重试 |
| 4 | `未匹配到洲信息，请检查订单` | 州/省匹配失败 | 检查 receivingState 是否在 Amazon 下拉选项中，修正后重置重试 |
| 5 | `商品无库存` | Amazon 侧缺货 | 换 ASIN；转人工；取消订单 |
| 6 | `无法修改商品购买数量` | 数量选择器不存在 | 大概率 quantity > 1 但商品不支持多购；人工处理 |
| 7 | `商品库存不足或已售罄` | 库存不足 | 换 ASIN；转人工 |
| 8 | `未找到加入购物车按钮` | 商品页结构异常 | 检查 ASIN 是否有效；账号是否被限购 |
| 9 | `加入购物车失败：页面未跳转` | 加购后无跳转 | 重置重试；多次出现检查账号状态 |
| 10 | `商品 ${asin} 配送方式非 FBA` | 非 FBA 商品 | 换 ASIN（仅FBA可用）；取消 |
| 11 | `商品 ${asin} 属于捆绑商品（bundle），请手动拍单` | Bundle 商品 | 转人工下单；无法自动化 |
| 12 | `预计送达时间超过 ${N} 天` | 交期超限 | 调大 N 值配置（默认7天）；或取消订单 |
| 13 | `预计送达时间解析失败，用户主动跳过订单` | 用户主动跳过 | 人工确认送达时间后手动拍单 |
| 14 | `商品 ${asin} 验证失败，请确认商品状态或库存是否充足` | 购物车核验失败 | 检查库存；重置重试 |
| 15 | `下单验证超时` | 下单页面超时 | 重置重试；检查网络 |
| 16 | `回传订单失败，请手动复制AMZ订单号回填至系统` | 订单历史页未加载 | 人工到 Amazon 查新订单号，手动填写 platformOrderNo |
| 17 | `处理商品失败: ${error.message}` | 通用商品处理异常 | 查看具体错误信息；重置重试 |

**价格超限异常（未写入 failContent，代码层取消下单）：**
- 触发条件：`Math.abs(实付 - 预估) / 实付 > 50%`
- 处置：订单被取消，状态回到待拍单；运营需要检查 ASIN 当前价格后决定是否继续

**验证码/人工介入流程（`showVerificationOverlay`）：**
- 仅在 JP 站生效：检测到下单按钮后，插件会拦截点击，显示全屏遮罩"正在验证订单信息"，等待验证通过后自动点击
- US/CA 站：全自动，无弹窗介入
- **面板侧没有对应的"等待人工操作"状态**，只有日志栏会显示进度

---

## 6. 「我不用的功能」清单

| 功能 | 位置 | 原因 |
|------|------|------|
| Amz To Jc 轨迹同步 | 插件面板 | 仅用于另一个卖货平台（Jc系统）的物流同步，你只有一套 ERP |
| 导入订单 | 工具栏 | 你的订单从自己 ERP 生成，不需要通过 Excel 手动导入 |
| 导出 | 工具栏 | 你直接查数据库，不需要导出文件 |
| 我的积分 | 右上角 | 他们的计费/用量计量模块，你自建系统不计费 |
| 布局设置 | 右上角下拉 | 页面布局偏好，无业务价值 |
| ASIN 价格监控页 | `/asinMonitor` | 如果你在 ERP 里有价格监控模块则不需要；若没有可以考虑参考其字段设计 |
| pluginUpdateNotice 轮询 | 浏览器（每5-10秒一次） | 你版本化管理自己的自动化服务端，不需要给用户推送插件更新 |
| 个人中心 - 性别/昵称 | `/user/profile` | 对运营工作无任何价值 |

---

## 7. 「他们架构特有、我们不需要」清单

以下功能是因为他们的"订单在厂商云上、插件在用户本地"这一架构才产生的，**我们的订单本来就在自己库里**，中间少一跳，这些设计直接删掉：

| 设计 | 为什么他们有 | 为什么我们不需要 |
|------|------------|----------------|
| `updateBuyerCookie` 接口 | 他们的服务端需要存储买家 Cookie 才能做某些服务端操作 | 我们的自动化在自控服务器上运行，直接管理浏览器会话 |
| 提取 customerId 按钮 | 插件不知道当前登录的是哪个账号，需要从页面 HTML 实时提取 | 我们预配置账号 ID，系统知道每个任务绑定哪个账号 |
| `getNeedPurchaseOrders?customerId=xxx` 过滤模式 | 插件只能拉"属于当前登录账号的"任务，无法看到全部 | 我们的服务端直接按账号派发任务，不需要插件自己过滤 |
| pluginUpdateNotice 高频轮询（每5-10s） | 需要给数百用户推送插件更新 | 我们直接更新服务端部署，不存在"用户本地插件版本"问题 |
| layer.js iframe 弹层模式 | Chrome Content Script 无法直接请求 Amazon（跨域），必须用 iframe 模拟浏览器操作 | 我们的服务端 Playwright/自动化直接控制浏览器，不受跨域限制 |
| "同步"概念（开始同步/Amz To Jc同步） | 订单状态存在厂商云，需要插件定期从 Amazon 抓回来再推上去 | 我们的物流同步可以直接在服务端跑，不需要用户打开浏览器触发 |
| `asinCheck` / `postCodeCheck` / `priceCheck` 三个布尔标志 | 拍单前校验在插件端（客户端），结果回写给服务端 | 我们可以在服务端任务分发前完成所有前置校验，不需要这三个标志 |
| 日志窗口（插件 Drawer 内）| 运营需要实时看到插件执行进度 | 我们的日志在服务端，通过 ERP 后台统一展示 |

---

## 8. 截图附录

截图文件保存在当前目录下：

| 文件名 | 内容 |
|--------|------|
| `screenshot-order-list.png` | `/order/amazonOrder` 订单列表主页（默认视图，待拍单/已拍单混合） |
| `screenshot-asinMonitor.png` | `/asinMonitor` ASIN 价格监控页 |
| `screenshot-amazonOrder.png` | 导航栏展开状态（拍单管理子菜单） |

---

## 附录 A：后端 API 端点汇总

Base URL：`https://smallbee168.com/prod-api`（网页侧）/ 插件侧为 `CONFIG.API.baseUrl`（用户自配）

| 方法 | 路径 | 用途 | 调用方 |
|------|------|------|--------|
| POST | `/system/amazonOrder/list` | 查询订单列表（分页+筛选） | 网页 |
| GET | `/system/amazonOrder/getNeedSyncOrders` | 拉取待物流同步订单 | 插件 |
| GET | `/system/amazonOrder/getNeedPurchaseOrders` | 拉取待拍单任务列表 | 插件 |
| POST | `/system/amazonOrder/purchaseOrderFinishUpdate` | 拍单完成回填 | 插件 |
| POST | `/system/amazonOrder/updateTrackingInfo` | 物流轨迹同步（完整版） | 插件 |
| POST | `/system/amazonOrder/updateAmzOrderStatus` | 更新 Amazon 订单状态（取消/不存在） | 插件 |
| POST | `/system/amazonOrder/updateOrderStatus` | 更新拍单任务状态（异常等） | 插件 |
| POST | `/system/amazonOrder/updateBuyerCookie` | 上传买家账号 Cookie | 插件 |
| GET | `/system/amazonOrderPig/getNeedSyncOrders` | 拉取 Jc 平台待同步订单 | 插件 |
| POST | `/system/amazonOrderPig/updateTrackingInfo` | Jc 平台运单号同步 | 插件 |
| GET | `/system/notice/pluginUpdateNotice` | 插件更新通知（高频轮询） | 网页 |
| POST | `/system/notice/receiveNotice` | 确认已读通知 | 网页 |

---

## 附录 B：插件版本差异（v2.4.1 → v2.5.0）

| 变更项 | v2.4.1（本地） | v2.5.0（CRX 最新） |
|--------|--------------|-----------------|
| 版本号 | 2.4.1 | 2.5.0 |
| 日本站拍单 | `purchaseSupported: true`（已支持） | 同左 |
| JP 都道府县映射 | ❌ 无 | ✅ 新增 `JP_PREFECTURE_EN_TO_JA` 完整47都道府县 EN→JA 对照表 |
| Cookie 上传日志 | 含 `console.log` 调试输出 | 已清理 |

> v2.5.0 的核心新功能是 JP 地址的日文转换支持，说明日本站地址填写之前存在英文/日文匹配失败的问题。

---

## 附录 C：插件接口契约（`getNeedPurchaseOrders` + `purchaseOrderFinishUpdate`）

> 来源：popup.js 源码全量逻辑追踪，逐字段确认调用路径。

### C.1 `GET /system/amazonOrder/getNeedPurchaseOrders?customerId=xxx` 响应体

返回结构：`{ code: 200, data: Order[] }`，插件取 `data`。

以下是 Order 对象中**拍单流程实际访问的字段**（不含仅用于展示的字段）：

| 字段名 | 类型 | 必须 | 使用场景 |
|--------|------|------|---------|
| `id` | integer | ✓ | 全部 `updateOrderStatus` / `purchaseOrderFinishUpdate` 回调的主键 |
| `orderNo` | string | ✓ | 内部系统单号，用于日志和回调参数 |
| `receivingName` | string | ✓ | 填写 Amazon 收货地址表单：姓名字段 |
| `receivingPhone` | string | ✓ | 填写地址：手机号 |
| `receivingAddress` | string | ✓ | 填写地址：Address Line 1（US/CA）/ Line 2（JP） |
| `receivingCity` | string | ✓ | 填写地址：城市；JP 时拼接 city+district 作 Line 1 |
| `receivingDistrict` | string | JP必填 | JP 专用区字段，null 则报错中止 |
| `receivingPostCode` | string | ✓ | 填写地址：邮编；US 格式 `12345`，JP 格式 `123-4567` |
| `receivingCountry` | string | ✓ | "US" / "CA" / "JP"，驱动整个地址填写和验证分支 |
| `state` 或 `receivingState` | string | ✓ | 州/省，代码：`order.state \|\| order.receivingState`（两个字段名均需兼容） |
| `products` | array | ✓ | 见下方 |
| `products[].asin` | string | ✓ | 打开 `amazon.com/dp/{asin}` 商品页 |
| `products[].quantity` | integer | ✓ | 选择商品数量；为 1 时若无数量选择器则跳过 |

**`products` 数组中，拍单流程只访问 `asin` 和 `quantity`，`price` / `imgUrl` / `status` 等字段仅由网页端展示使用。**

> 注意：`state` 和 `receivingState` 两个字段名并存。你的 `procurement_order` 表只需保留一个，但对外 API 如果要兼容该插件，需要两个都返回（或都返回同一个值）。

---

### C.2 `POST /system/amazonOrder/purchaseOrderFinishUpdate` 请求体

拍单成功后，插件从 Amazon 结账页和订单历史页提取数据，组装后回填：

```json
{
  "id": 1121454,
  "platformOrderNo": "111-5958998-9658617",
  "asins": "B0F8NB1T8Y",
  "deliveryTime": "2026-08-07",
  "origDeliveryTime": "Thursday, August 7",
  "creditCardNumber": "7883",
  "mainPostCode": "36759",
  "extPostCode": "",
  "shipping": "$0.00",
  "totalBeforeTax": "$9.99",
  "tax": "$0.80",
  "total": "$10.79",
  "products": [
    {
      "asin": "B0F8NB1T8Y",
      "quantity": 1,
      "unitPrice": 9.99,
      "totalPrice": 9.99,
      "productImage": "https://m.media-amazon.com/images/I/..."
    }
  ]
}
```

| 字段 | 来源 | 说明 |
|------|------|------|
| `id` | `order.id` | 拍单前的任务 ID |
| `platformOrderNo` | Amazon 订单历史页 DOM | 新生成的 Amazon 订单号 |
| `asins` | Amazon 订单历史页，a 标签 href 正则提取 | 逗号分隔字符串（多 ASIN 用逗号） |
| `deliveryTime` | 结账页 h2 标签，解析后格式化为 YYYY-MM-DD | 预计送达日期 |
| `origDeliveryTime` | 结账页 h2 原文 | Amazon 原始字符串，如 "Thursday, August 7" |
| `creditCardNumber` | 结账页 `#payment-option-text-default` | 后4位字符串 |
| `mainPostCode` | 订单历史页地址弹窗解析 | 美国5位邮编 / 加拿大邮编 / 日本邮编 |
| `extPostCode` | 同上 | 美国 zip+4 扩展位，无则空字符串 `""` |
| `shipping` / `totalBeforeTax` / `tax` / `total` | 结账页 `#subtotals-marketplace-table` | 字符串带货币符号，如 `"$0.00"` |
| `products[].unitPrice` | 结账页 `span.lineitem-price-text.a-text-bold` | 数字类型 |
| `products[].totalPrice` | `unitPrice × quantity` 计算所得 | 数字类型 |
| `products[].productImage` | 结账页商品图片 src | URL 字符串 |

---

## 附录 D：导入订单模板列头

> 来源：`/smallBee.zip` → `amazon_order_template.xlsx`，实际下载并解析。

模板共 **15 列**，以下为列头原文 + 示例数据 + 与 API 字段的映射：

| # | 列头原文 | 示例值（行2） | API 字段 | 备注 |
|---|---------|-------------|---------|------|
| 1 | 店铺账号 | 示例数据 | `storeName` | 店铺名，非买家账号 |
| 2 | 平台订单号 | `123456789-123` | `orderNo` | **内部系统单号**（非 Amazon 订单号） |
| 3 | 收货人姓名 | `Garcia Isabel` | `receivingName` | |
| 4 | 收货人地址 | `2510 S Shelton St` | `receivingAddress` | |
| 5 | 收货人城市 | `Santa Ana` | `receivingCity` | |
| 6 | 收货人州/省 | `CA` | `receivingState` / `state` | 州缩写 |
| 7 | 邮编 | `92707` | `receivingPostCode` | 整数存储，导入时需转字符串 |
| 8 | 收货人电话 | `7143713557` | `receivingPhone` | 整数存储，导入时需转字符串 |
| 9 | AMZ订单号 | `999-9999999-9999999` | `platformOrderNo` | 可为空，拍单后回填；预填说明支持手工录入已有订单 |
| 10 | 买家号 | 示例数据 | `buyerAccount` | 账号人工命名，如"环境172民生" |
| 11 | 买家号ID | `A2OZOCIGK1VMB5` | `buyerCustomerId` | Amazon Customer ID |
| 12 | 收货人国家 | `US` | `receivingCountry` | US / CA / JP |
| 13 | ASIN | `B0FB3VS68J` 或 `B000,B001` | `products[].asin` | **多 ASIN 用英文逗号分隔** |
| 14 | 数量 | `1` 或 `2,1` | `products[].quantity` | **多数量与 ASIN 顺序对应，逗号分隔** |
| 15 | 收货人区 | 地区为日本时必填 | `receivingDistrict` | JP 专用，US/CA 留空 |

**用模板验你的 `procurement_order` 字段：**

模板 15 列 = 采购任务的最小输入集。对比你的字段表：
- ✅ 模板有、你应该有：id/orderNo/receivingName/Address/City/State/PostCode/Phone/Country/District、products(asin+quantity)、buyerAccount/buyerCustomerId
- ⚠️ 模板有、你的 ERP 里需要确认的：`storeName`（店铺）、`platformOrderNo`（导入时可能预填）
- ✅ 模板没有、你有的（正确）：代理出口绑定、店铺关联、三档档位护栏（这四项确为你业务特有）
- ℹ️ 模板没有 `price`：单价不在导入列，说明**价格不是输入，是拍单时从 Amazon 实时抓取**的

---

## 附录 E：物流 events 数据结构

> 来源：popup.js `extractTrackingEvents()` 函数 + `updateTrackingInfo` / `updateOrderTrackingInfoPig` 请求体。

### E.1 `trackingJson` 数组元素结构

```json
[
  {
    "day": "July 28, 2026",
    "time": "8:42 AM",
    "tracking_info": "Package arrived at carrier facility",
    "state": "CA",
    "city": "Los Angeles"
  },
  {
    "day": "July 26, 2026",
    "time": "11:15 PM",
    "tracking_info": "Package departed from facility",
    "state": "TX",
    "city": "Dallas"
  }
]
```

| 字段 | 类型 | Amazon DOM 来源 | 说明 |
|------|------|----------------|------|
| `day` | string | `.tracking-event-date`（日期组头） | 同一天多个事件共享同一 day 值 |
| `time` | string | `.tracking-event-time` | 12小时制，含 AM/PM |
| `tracking_info` | string | `.tracking-event-message` | 事件描述文本 |
| `state` | string | `.tracking-event-location` 解析 | 位置字符串逗号后半部分去掉邮编 |
| `city` | string | `.tracking-event-location` 解析 | 位置字符串逗号前半部分 |

**排列顺序：倒序（最新事件在 index 0）**，与 Amazon 页面显示一致。

### E.2 `updateTrackingInfo` 完整请求体（物流完整同步）

```json
{
  "orderId": 1121454,
  "trackingNumber": "1Z999AA10123456784",
  "trackingUrl": "https://www.amazon.com/progress-tracker/package/...",
  "trackingHtml": "<base64 编码的完整跟踪页 HTML>",
  "mainPostCode": "36759",
  "extPostCode": "",
  "card": "7883",
  "trackingJson": "[{\"day\":\"July 28\",\"time\":\"8:42 AM\",...}]",
  "asins": "B0F8NB1T8Y",
  "orderDate": "July 25, 2026",
  "subtotal": null,
  "shipping": "$0.00",
  "totalBeforeTax": "$9.99",
  "tax": "$0.80",
  "total": "$10.79",
  "carrier": "UPS",
  "receiptPicture": "https://m.media-amazon.com/images/delivery/...",
  "deliveryDate": "2026-08-07",
  "origDeliveryDate": "Thursday, August 7"
}
```

### E.3 `updateOrderTrackingInfoPig` 请求体（Jc 平台精简同步）

```json
{
  "orderId": 1121454,
  "trackingNumber": "1Z999AA10123456784",
  "trackingUrl": "https://...",
  "trackingHtml": "<base64>",
  "firstEventTime": "July 25, 2026 9:15 AM",
  "mainPostCode": "36759",
  "card": "7883",
  "trackingJson": "[...]"
}
```

> `firstEventTime` = 事件数组**最后一个元素**（即时间最早的事件）的 `day + " " + time` 拼接，
> 因为数组是倒序的，`index = length - 1` = 最早事件。

### E.4 对你建表的影响

```
logistics_event 表至少需要：
  order_id        FK
  event_date      date 或 varchar   ← day 字段，Amazon 返回英文，需标准化存储
  event_time      time 或 varchar   ← time 字段
  description     text              ← tracking_info
  city            varchar
  state_code      varchar(10)
  sort_order      integer           ← 0 = 最新，倒序索引；或存 created_at 反推

tracking_number / carrier / delivery_date 存在 order 主表，不用单独一张表。
trackingHtml 字段（base64 HTML）体积巨大——建议存 object storage，DB 只存 URL；
或者整体不存，按需重新抓取。
```


