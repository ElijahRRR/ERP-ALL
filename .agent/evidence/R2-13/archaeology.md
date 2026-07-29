# R2-13 自动采购接入 —— 考古（2026-07-29）

> 工单：R2-13【L1→L2】（Amazon 采购插件，D-Q69，**MVP 内**）。
> 口径源：`specs/007-mvp-completion-plan/README.md` R2-13 节、
> `specs/001-domain-model/07-order-sourcing-aftersale.md`（`buyer_account` / `plugin_instance` 图纸）。
> **性质=从厂商 SaaS 迁移，非新建能力**——现产线在跑，切换有真金白银风险。

**本轮为只读考古**，不实现。007 给 R2-13 的 gate 写着「排 R2-09 之后」，
本文 §五 给出该 gate 是否真的卡住实现的判断。

---

## 〇、比 007 转述更具体的三条（**以插件源码为准**）

007 的插件描述来自审计侧解包 crx。本轮**直接读了 fork 源仓**
（`ElijahRRR/AMZ-Purchase-Assistant`，clone 于 `07282ed`，v2.4.1），得到三条 007 没写的事实。

### ① 9 个端点分属**两个路径前缀**，不是一组

`js/popup.js` 的 `CONFIG.API.endpoints` 原文：

| 插件内键名 | 真实路径 |
|---|---|
| `getPurchaseOrders` | `/system/amazonOrder/getNeedPurchaseOrders` |
| `purchaseOrderFinishUpdate` | `/system/amazonOrder/purchaseOrderFinishUpdate` |
| `updateOrderStatus` | `/system/amazonOrder/updateOrderStatus` |
| `updateAmzOrderStatus` | `/system/amazonOrder/updateAmzOrderStatus` |
| `updateBuyerCookie` | `/system/amazonOrder/updateBuyerCookie` |
| `getNeedSyncOrders` | `/system/amazonOrder/getNeedSyncOrders` |
| `updateOrderTracking` | `/system/amazonOrder/updateTrackingInfo` |
| **`getNeedSyncOrdersTrack`** | **`/system/amazonOrderPig/getNeedSyncOrders`** |
| **`updateOrderTrackingInfo`** | **`/system/amazonOrderPig/updateTrackingInfo`** |

007 记的是「`updateTrackingInfo`×2」，**没说那两条在不同命名空间**（`amazonOrder`
vs `amazonOrderPig`）。这不是命名细节——它意味着厂商 SaaS 那侧有**两个后端模块**，
物流链走的是另一套。**ERP 实现时必须先定：两组路径都实现，还是 fork 时把路径改写成一组。**
后者更干净，但属于改插件代码，与「只换 baseUrl」不是一个工作量。

### ② 插件的请求封装**默认不带任何认证头**

```
fetch(url,{headers:{"Content-Type":"application/json",...options.headers},...options}
```

全仓只有这一处 `fetch` 封装，基础头只有 `Content-Type`。

**推论：007 说的「fork 插件、baseUrl 指向 ERP」不足以完成 13a。** 图纸要求
`plugin_instance` **实例专属 token（禁全局共享密钥）**，而插件侧现在没有注入认证头的
地方——**必须改插件代码**加上头注入与 token 存取。这条落在 13a 的工作量估计里。

> 未查的一件：调用方是否在 `options.headers` 里逐次传了什么。抓取该细节的命令被安全
> 分类器拦下（模式形似翻凭证），**未绕过**。对本单结论无影响——无论现状如何，
> 认证机制都要按图纸重做。实施 13a 时在本机对照插件源码确认即可。

### ③ `manifest.json` 的权限面，比 007 的描述更值得警惕

```json
"permissions":      ["storage", "tabs", "scripting", "cookies"],
"host_permissions": ["*://*/*"]
```

而 `content_scripts.matches` **已经逐条枚举到 amazon 三站的订单页**。
**即通配符 host 权限并不服务于内容脚本，只服务于 `fetch` 与 `cookies` 那两条路径。**

007 的安全要求（收窄 `host_permissions` 至 amazon 域；不启用 `buyer_session` 就一并删
`cookies` 权限与 `updateBuyerCookie`）**依据成立且可以更硬**：收窄不会影响内容脚本注入，
因为那部分本来就没依赖通配符。

> 现状风险的具体形状：该扩展可读取**装它的那个浏览器访问过的任何站点**的 cookie——
> 包括 Walmart 卖家后台与飞书。这台机器同时是运营日常用的浏览器，故这不是理论风险。

---

## 一、ERP 侧现状（实测）

### 已有，可直接用

| 件 | 位置 | 说明 |
|---|---|---|
| `procurement_order` | `0025_order_domain.py:218` | **13d 的回填列全部现成**：`purchase_order_ref` / `purchase_cost` / `purchase_currency` / `freight_cost` / `carrier` / `tracking_no` / `purchased_at` / `shipped_at` / `backfilled_at` / `exception_reason` |
| 状态机 | 同上 `ck_procurement_status` | `unassigned/assigned/claimed/purchased/shipped/backfilled/exception/cancelled`——**插件回填要走的状态流转已定义好** |
| 内部端点组 | 契约 002 `/procurement-orders/{assign,claim,backfill,exception}` | 权限点 `procurement.read/execute/admin`，R2-05 增量3 已落地 |
| 门户端点组 | 契约 002 `/portal/procurement-orders/*` | **契约已声明、代码未建**（R2-10，在 `CONTRACT_AHEAD_OF_CODE` 白名单）。**插件端点组与它是两个不同的外部入口**，隔离方式可对照 |

### 缺口

| 件 | 现状 | 图纸位置 |
|---|---|---|
| `buyer_account` | **全仓零建表** | `07-order-sourcing-aftersale.md:131-148`（含 `uq_buyer_account (team_id, external_customer_id)`、`uq_buyer_account_label`） |
| `plugin_instance` | **全仓零建表** | 同上 `:120, :161`（一浏览器一实例、实例专属 token） |
| `procurement_order.buyer_account_id` | **无此列** | 同上 `:149-150`（插件路径必填、人工/门户可空，索引 `(buyer_account_id, status)`） |
| `purchase_execute` flow | **只是个枚举值**（`core/automation.py:77`），**不在 `WIRED_FLOWS`**（现为 `{ORDER_BLOCK, REFUND, CANCEL}`，`automation.py:115`） | §09 v2.1 |

### **本单最要紧的一条：护栏是三件耦合，缺一件都等于没有**

007 已点出「护栏消费点须从零建」，本轮复核成立，**并发现它还牵着第三件**：

1. **消费点零存在**——`amount_ceiling` / `daily_cap` / `price_delta_pct` 在
   `backend/src` 的唯一命中是 `automation/router.py:24-25` 那段**说明它们零命中的注释**；
2. **`automation_policy.config` 现在写不进去**——`automation/router.py` 的 PUT
   **显式 422 `AUTOMATION_CONFIG_NOT_WRITABLE`**，理由原文：「开放写入只会造成
   『护栏已配』的错觉——真正的护栏要等消费点落地」；
3. 判据。

**所以 13c 必须同时交付这三件。** 只做 1 → 运营配不了值（PUT 被 422 拒）；
只做 2 → 配得进去但不生效，正是那条注释要防的错觉；缺 3 → 无人知道它到底生效没有。

> 这个耦合 007 没写。它是 R2-09 增量2 那次**主动收窄**留下的——当时收窄是对的
> （没有消费点就不该让人配），但**收窄的解除条件没写在任何地方**，只写在那段注释里。

---

## 二、增量拆分（沿用 007 的 13a–13e，按实测调整工作量口径）

| 增量 | 内容 | 本轮实测带来的调整 |
|---|---|---|
| **13a** | 插件端点组 + `plugin_instance` 实例认证 | **含改插件代码**：加认证头注入（§〇②）；并定「两个路径前缀」的处置（§〇①） |
| **13b** | `buyer_account` 建表 + `procurement_order.buyer_account_id` + 任务路由 | 图纸 `:131-150` 已给全，含唯一约束与索引 |
| **13c** | `purchase_execute` 三档接线 + **三档护栏** | **三件耦合**：放开 `config` 写入 + 消费点 + 判据（§一） |
| **13d** | 回填与异常 | 回填列与状态机**全部现成**，主要是服务层与对账 |
| **13e** | 灰度切换（**最高风险片**） | 红线：同一浏览器配置内绝不同时启用两个插件——**会重复下单** |

**安全项随 13a 强制**：收窄 `host_permissions`、按 `buyer_session` 决定是否删 `cookies`
权限与 `updateBuyerCookie`、ERP 不可达时插件 **fail-closed 不自行采购**。

---

## 三、007 那条 gate 是否真的卡住实现

007 给 R2-13 的 gate：**「排 R2-09 之后（需 `purchase_execute` 三档护栏才敢开 auto）」**。

**实测结论：gate 卡的是「开 auto」，不是「开工」。**

- `purchase_execute` 的 flow 枚举、`resolve_mode` 内核、策略读写 API 与三档面板
  **都已随 R2-09 增量2 落地**（`core/automation.py`、`automation/router.py`）；
- 缺的只有**护栏消费点**，而那本来就是 **13c 自己的内容**，不是 R2-09 的欠账；
- 13a/13b/13d 与护栏无关。

**故 13a→13b→13d 可立即开工，13c 内部按「先放开 config 写入 + 消费点 + 判据」一次交齐，
13e 必须在 13c 验收②通过之后**。这与 gate 的**意图**一致（护栏缺失即禁止开 auto），
只是把「排在 R2-09 之后」精确成「auto 档排在 13c 之后」。

> **这一条建议由审计侧确认后再动工**——它改的是工单排期口径，属我不该自行拍板的那类。

---

## 四、待 Owner / 审计侧裁定

1. **两个路径前缀怎么处置**（§〇①）：ERP 两组都实现，还是 fork 时改写为一组？
   后者更干净但要改插件代码，且与厂商 SaaS 的回切路径会分叉——**而 13e 明确要求保留回切**。
   这两件事有张力，需要一并定。
2. **gate 口径**（§三）：是否接受「13a/13b/13d 先行，auto 档卡在 13c 之后」。
3. **`buyer_session` 用不用**——决定 `cookies` 权限与 `updateBuyerCookie` 的去留（007 已列，
   但没定）。这条影响 13a 的安全收窄范围。

## 五、开工前已确认的事实清单（供审查复算）

| 声称 | 复算命令 | 期望 |
|---|---|---|
| `buyer_account`/`plugin_instance` 未建 | `grep -rln "buyer_account\|plugin_instance" backend/alembic/versions/` | 空 |
| `procurement_order` 无 `buyer_account_id` | `grep -n "buyer_account_id" backend/alembic/versions/0025_order_domain.py` | 空 |
| `purchase_execute` 只是枚举 | `grep -rn "purchase_execute" backend/src/erp/ --include=*.py` | 仅 `core/automation.py:77` |
| 不在 WIRED_FLOWS | `sed -n '115,117p' backend/src/erp/core/automation.py` | 仅 ORDER_BLOCK/REFUND/CANCEL |
| 护栏键零消费点 | `grep -rn "amount_ceiling\|daily_cap\|price_delta_pct" backend/src/ --include=*.py` | 仅 `automation/router.py` 的说明注释 |
| config 写入被显式拒 | `grep -n "AUTOMATION_CONFIG_NOT_WRITABLE" backend/src/erp/automation/router.py` | 有 |
| 插件 9 端点与两个前缀 | `grep -o "endpoints:{[^}]*}" /workspace/amz-purchase-assistant/js/popup.js` | 见 §〇① |
| 插件权限面 | `cat /workspace/amz-purchase-assistant/manifest.json` | `*://*/*` + `cookies` |
