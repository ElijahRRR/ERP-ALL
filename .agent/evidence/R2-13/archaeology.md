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

> **Owner 2026-07-30 裁定：把插件改成一组。** 故 13a 含改插件路径。
> **下游后果**：fork 版路径与厂商后端从此不一致，**13e 的回切不能靠「改 baseUrl」，
> 必须写成「换回厂商原版插件」**，且回切演练要实测这条路。详见 `owner-rulings-20260730.md` §三。

### ② 插件的请求封装**默认不带任何认证头**

```
fetch(url,{headers:{"Content-Type":"application/json",...options.headers},...options}
```

全仓只有这一处 `fetch` 封装，基础头只有 `Content-Type`。

**推论：007 说的「fork 插件、baseUrl 指向 ERP」不足以完成 13a。** 图纸要求
`plugin_instance` **实例专属 token（禁全局共享密钥）**，而插件侧现在没有注入认证头的
地方——**必须改插件代码**加上头注入与 token 存取。这条落在 13a 的工作量估计里。

#### 补：这一格已查（原「未查」项，PR #47 审查侧 N-b 提出，本轮复算确认）

原先未查的是「调用方是否在 `options.headers` 里逐次传了什么」（抓取命令曾被安全分类器
拦下，未绕过）。本轮改用只读静态分析补上了，**结论不中性——它会改 13a 的做法**：

**`...options` 排在 `headers:` 之后，故调用方一旦传 `headers`，整个 headers 对象被整体
替换，base 全丢。** 这不是「合并」，是「覆盖」。node 实测：

```
不传 headers        → {"Content-Type":"application/json"}
传了 headers        → {"Content-Type":"application/json;charset=UTF-8"}   ← base 被整体替换
base 里加 token 后   → {"Content-Type":"application/json;charset=UTF-8"}   ← token 没了
```

**九个调用里恰好有一个这么传**：`purchaseOrderFinishUpdate`
（`headers:{"Content-Type":"application/json;charset=UTF-8"}`）。全文 `headers` 只出现
3 处，2 处是封装自身，第 3 处就是它。

今天无害（两边都只是 Content-Type）。但 13a 若按最自然的做法「在 base headers 里加实例
token」，**这一条调用会静默丢掉认证头 → 401，而它恰好是「采购完成回报」这条已经花过钱
的路径**。九分之一的间歇性失败，最难查的那种。

→ **13a 的工作量口径因此改变**：不是「加认证头注入」，而是「**先修 wrapper 的 headers
合并顺序，再加注入**」。见 §二 13a 行。

（本项为只读静态分析，未取任何凭证。）

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

> **但后半句已被 Owner 2026-07-30 裁定覆盖**：`buyer_session` **要启用**（用途=识别买家号），
> 故 `cookies` 与 `updateBuyerCookie` **保留**。收窄 `host_permissions` 那半句不变，
> 且因保留 cookies 而**更承重**（见下方「cookie 调用链」补节 ②）。

> 现状风险的具体形状：该扩展可读取**装它的那个浏览器访问过的任何站点**的 cookie——
> 包括 Walmart 卖家后台与飞书。这台机器同时是运营日常用的浏览器，故这不是理论风险。

#### 补：四个权限里**三个是零调用**（PR #47 审查侧 N-c 提出，本轮扩大核实）

审查侧指出 `scripting` 零调用可直接删。逐个核下来**范围比这更大**——扫的是**仓里全部
5 个 JS**：`js/popup.js`、`js/background.js`、`lib/jquery-3.3.1.min.js`、`layer/layer.js`、
`layer/mobile/layer.js`。

> 前 4 个是 manifest 引到的；第 5 个 `layer/mobile/layer.js` **manifest 并未引用**，
> 是审查侧第二轮补扫的——**「零命中」这种全称结论就该连没被引用的一起排除**，
> 我第一轮只扫了 manifest 引到的 4 个，口径偏窄。已复核：该文件全文无 `chrome.*`。
> 另核 `popup.html`，其 `<script src>` 只有 `js/popup.js`，无 manifest 之外的加载路径。

| 声明的权限 | 实际调用 | 处置 |
|---|---|---|
| `storage` | **0** | 可删 |
| `tabs` | **0** | 可删 |
| `scripting` | **0** | 可删 |
| `cookies` | 1（`background.js` 的 `chrome.cookies.getAll`） | **保留**（Owner 2026-07-30 裁定：用于识别买家号） |

`chrome.runtime`（popup.js 的 `sendMessage`/`lastError`、layer.js 的 `chrome.runtime.id`）
**不需要声明权限**，故不构成对上表的反例。

> **这同时补上了 §〇③ 那句推断的最后一块。** `chrome.scripting.executeScript` 是需要
> host 权限的——若它存在且打到非 amazon 目标，收窄 `host_permissions` 就会打断它。
> 实测零调用，故收窄确实不打断任何东西。**核过之后那句才站得住。**
>
> 注：`incomplete_results: true` 的 GitHub 代码搜索**不能**用作「零命中」的证据，
> 上表是逐文件取回原文后计数得出的。

→ 随 13a 的安全收窄一并处理：删 `storage` / `tabs` / `scripting` 三条 + 收窄
`host_permissions`。**`cookies` 保留**——Owner 2026-07-30 裁定要用（识别买家号），故
三条零调用权限照删、收窄照做，但 `cookies` 不动。

> **删 `storage` 会不会让它没地方存东西？不会**：状态存在 `localStorage`（popup.js 里 4 处），
> 而 `chrome.storage` 零处。`localStorage` 不需要该权限，故那条是真闲置，删掉零影响。

#### 补：cookie 调用链——收窄 `host_permissions` 与保留 `cookies` **相容**，且收窄是承重的

（PR #47 审查侧第二轮提出前两条，本轮复算确认并补上第三条。三条都是给 13a 的。）

**① 那两条安全建议此前从没人验过它们相不相容，现在验了：相容。**

`background.js` 的 `chrome.cookies.getAll({url: request.url})`，`url` 取自消息载荷；
唯一调用方是 popup.js 的 `getCookiesAsJson()`，传的是 `window.location.href.split("?")[0]`
——**调用方自己所在页面的 URL**。故实际读到的只有 amazon 自己的 cookie。

**即：把 `host_permissions` 收窄到 amazon 三站是零行为变更**，不会打断 `buyer_session`
那条路径——而 Owner 2026-07-30 已裁定**保留**它，故这条相容性正是必需的前提，
不再是「若保留则……」的假设。收窄因此是个不需要回归测试的改动。

**② 但正因如此，收窄是承重的，不是清洁工作。**

`chrome.runtime.onMessage` 那个监听器**不校验 `sender`**，URL 直接取自消息。
今天安全只是因为**约束在调用方，不在被调方**。fork 之后一旦出现第二个调用点
（或注入面变化），它就是一个「任意域读 cookie」的原语，而**唯一还兜着它的就是
`host_permissions`**。

→ 13a 顺手把 `sender` 校验也加上：两道边界比一道稳，成本是一行。

**③ 而「约束在调用方」比 ② 说的还要散——`popup.js` 有两个执行上下文。**

它既是 manifest 的 `content_scripts.js`，又被 `popup.html` 以 `<script src>` 加载
（`popup.html` 只加载这一个脚本，无 manifest 之外的加载路径）。两个上下文里
`window.location.href` 完全不同：

| 上下文 | URL | 谁把它限制在 amazon |
|---|---|---|
| 内容脚本 | amazon 订单页 | manifest 的 `content_scripts.matches` |
| 扩展弹窗页 | `chrome-extension://<id>/popup.html` | `init()` 里的 `isAmazonOrderPage()`——不匹配就不建 UI，`getCookiesAsJson` 走不到 |

**还有第三道，独立于上面两道**（PR #47 审查侧第三轮补，已复核）：唯一调用
`getCookiesAsJson()` 的 `sendBuyerCookie()` **自己开头就早退**——

```js
const country = getCurrentCountryCode();   // ← 由 detectAmazonSite() 按 hostname 推
if (!country) { ...; return }              // ← 非 amazon 域返回 null，走不到取 cookie
```

即使前两道被绕过，这一道还在。另核 popup.js 里 `onMessage` **0 处**——没有消息驱动的
入口能从外部触发上述处理函数。

**所以 amazon-only 这个性质靠的是两个上下文里的三道各自独立的调用方约束，而
`background.js` 里一道都没有。** 这让 ② 的结论更硬：不是「有一个调用方恰好传了安全的
值」，而是「有多道各自独立的调用方约束，改任何一道都会破，而被调方对此一无所知」。

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
| **13a** | 插件端点组 + `plugin_instance` 实例认证 | **含改插件代码**：**先修 wrapper 的 headers 合并顺序，再加认证头注入**——顺序反了会让 `purchaseOrderFinishUpdate` 静默丢认证头（§〇② 补）；并定「两个路径前缀」的处置（§〇①） |
| **13b** | `buyer_account` 建表 + `procurement_order.buyer_account_id` + 任务路由 | 图纸 `:131-150` 已给全，含唯一约束与索引 |
| **13c** | `purchase_execute` 三档接线 + **三档护栏** | **三件耦合**：放开 `config` 写入 + 消费点 + 判据（§一） |
| **13d** | 回填与异常 | 回填列与状态机**全部现成**，主要是服务层与对账 |
| **13e** | 灰度切换（**最高风险片**） | 红线：同一浏览器配置内绝不同时启用两个插件——**会重复下单** |

**安全项随 13a 强制**：收窄 `host_permissions`（**零行为变更，且它同时是那个未校验
`sender` 的读 cookie 原语的边界**）、**删掉 `storage`/`tabs`/`scripting` 三条零调用权限**、
**给 `chrome.runtime.onMessage` 加 `sender` 校验**（成本一行，见 §〇③ 补）、
**保留 `cookies` 权限与 `updateBuyerCookie`**（Owner 2026-07-30 裁定；细节待其本地分析
结果，在此之前 13a 不动 cookie 相关代码）、
ERP 不可达时插件 **fail-closed 不自行采购**。

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

## 四、四条待裁定 —— **Owner 2026-07-30 已全部裁定**

> 裁定原文与逐条影响见 **`owner-rulings-20260730.md`**（含需审计侧改图纸正文的清单）。
> 本节只留结论，避免两处各写一份而漂移。

| # | 议题 | 裁定 |
|---|---|---|
| 1 | 两个路径前缀 | **把插件改成一组**（推翻本文原推荐的「ERP 两组都实现」）。→ 下游后果：**13e 的回切方式必须写成「换回厂商原版插件」而非「改 baseUrl」**，因 fork 版路径已与厂商后端不一致 |
| 2 | gate 口径 | **接受**「13a/13b/13d 先行，auto 档锁在 13c 交付并验收之后」 |
| 3 | `buyer_session` / cookies | **要，保留**（推翻本文原推荐的「删掉」）。用途限定为**识别买家号**；**细节待 Owner 的本地分析结果，在此之前 13a 不动 cookie 相关代码**。三条零调用权限该删照删、`host_permissions` 收窄照做、`sender` 校验**因保留 cookies 而更必要** |
| 4 | `daily_cap` 权威方 | **账号列唯一权威；自动化策略不配这个值**——`automation_policy.config.daily_cap` **取消**，不进护栏键清单。→ **13c 护栏从三键降为两键**（`amount_ceiling` + `price_delta_pct`） |

> **本文提的三个 `daily_cap` 口径（列为权威 / 护栏为权威 / 取较小值）均未被采纳**，
> Owner 选的是更简的第四种：护栏键根本不存在。这比「取较小值」更彻底——取较小值仍要求
> 两处都被正确查询，而「只有一处」连查漏的可能都没有。

## 五、开工前已确认的事实清单（供审查复算）

| 声称 | 复算命令 | 期望 |
|---|---|---|
| `buyer_account`/`plugin_instance` 未建 | `grep -rln "buyer_account\|plugin_instance" backend/alembic/versions/` | 空 |
| `procurement_order` 无 `buyer_account_id` | `grep -n "buyer_account_id" backend/alembic/versions/0025_order_domain.py` | 空 |
| `purchase_execute` 只是枚举 | `grep -rn "purchase_execute" backend/src/erp/ --include=*.py` | 仅 `core/automation.py:77` |
| 不在 WIRED_FLOWS | `sed -n '115,117p' backend/src/erp/core/automation.py` | 仅 ORDER_BLOCK/REFUND/CANCEL |
| 护栏键零消费点 | `grep -rn "amount_ceiling\|daily_cap\|price_delta_pct" backend/src/ --include=*.py` | 仅 `automation/router.py` 的说明注释 |
| config 写入被显式拒 | `grep -n "AUTOMATION_CONFIG_NOT_WRITABLE" backend/src/erp/automation/router.py` | 有 |
| 插件 9 端点与两个前缀 | `grep -o "endpoints:{[^}]*}" <插件仓>/js/popup.js` | 见 §〇① |
| 插件权限面 | `cat <插件仓>/manifest.json` | `*://*/*` + `cookies` |

### 本轮（PR #47 审查侧 N-a/N-b/N-c）新增的主张

外部仓一律以 `ElijahRRR/AMZ-Purchase-Assistant@07282ed`（v2.4.1）为准。

| 声称 | 复算命令 | 期望 |
|---|---|---|
| `headers` 全文只 3 处，第 3 处即唯一传 headers 的调用 | `grep -o "headers" <插件仓>/js/popup.js \| wc -l`，再看每处上下文 | 3；第 3 处在 `purchaseOrderFinishUpdate` |
| 传了 headers 则 base 被整体替换 | 用 `{headers:{...base,...o.headers},...o}` 跑一次 node | 传 headers 时 base 键全丢 |
| 四权限里三个零调用 | 对**仓里全部 5 个** JS 各数 `chrome.storage` / `chrome.tabs` / `chrome.scripting` / `chrome.cookies` | 前三个 0；`cookies` 仅 `background.js` 1 处 |
| 插件自有 JS 就是两个文件 | `cat <插件仓>/manifest.json` 看 `content_scripts.js` 与 `background.service_worker` | `js/popup.js` + `js/background.js`（其余为 jquery/layer 库） |
| 仓里共 5 个 JS，第 5 个未被 manifest 引用 | 列仓内 `*.js` 并与 manifest + `popup.html` 的加载项比对 | 多出 `layer/mobile/layer.js`；其全文无 `chrome.*` |
| 状态存 `localStorage` 而非 `chrome.storage` | 各数一次 `localStorage` / `chrome.storage` | 4 / 0 |
| `cookies` 的 url 来自调用方所在页 | 读 `getCookiesAsJson()` 与 `background.js` 的监听器 | `window.location.href.split("?")[0]`；监听器不校验 `sender` |
| `popup.js` 有两个执行上下文 | manifest 的 `content_scripts.js` + `popup.html` 的 `<script src>` | 两处都指向 `js/popup.js` |
| 非 amazon 上下文有**三道**独立约束 | ①`matches` ②`init()` 的 `isAmazonOrderPage()` ③`sendBuyerCookie()` 开头的 `getCurrentCountryCode()` 早退 | 三道各自成立；另 popup.js 内 `onMessage` 为 0，无外部消息入口 |
| `daily_cap` 两个存储位置 | `grep -rn "daily_cap" specs/` | `07:144` 建列、`09:208` 护栏键，措辞同义。⚠️ **本行是截至 2026-07-29 的图纸态**；Owner 已裁定取消护栏键，审计侧改完 `09:208`/`007:280` 后本行的期望值变为「仅 `07:144`/`07:152`/`007:278` 的账号列」 |

> ⚠️ **复算这几条时不要用 GitHub 代码搜索**：它对本仓返回 `incomplete_results: true`，
> **不能用作「零命中」的证据**。上表全部是逐文件取回原文后计数得出的。
