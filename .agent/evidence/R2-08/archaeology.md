# R2-08 财务域考古

> **状态**：阶段一 + 阶段二完成（2026-07-27，R2-12 三日连测墙钟等待期间）。
> 只读，未改任何实现代码，未立项。
> **阶段一**（§0~§7）：闸门核实 · 现状基线 · 幂等键错位 · 渠道限额 · 初步拆单判断。
> **阶段二**（§8~§11）：KPI 归属缺口 · settlement 字段映射三处收缩 · 待裁清单汇总。
> **未做**（见 §10）：旧仓 820 行的**行为语义**（本轮只读了 schema 与端点，未读控制流）、
> 前端 `pages-finance.jsx` 形态、OpenAPI 契约缺口细目。

---

## 0 结论速览

| # | 结论 | 性质 |
|---|---|---|
| 1 | **工单写的「硬前置：等图纸」早已解除**——图纸 `421f83d`（2026-07-17）已按 immutable event ledger 修订完毕，commit 标题就叫「R2-08 闸门解除」，台账停在 07-16 未回写 | **台账失实**，已修 |
| 2 | 财务域**代码与表全仓为零**，真空地；`financial_event`/`ledger_entry`/`profit_ledger`/`settlement` 在 `backend/src/` 与全部 39 个迁移中零命中 | 现状核实 |
| 3 | 图纸提议的幂等自然键与渠道**实际提供的字段对不上**——这是「重拉不二次过账」这条硬保证的地基 | **已裁定并落笔**（main `b4f286f`，阻塞解除） |
| 4 | 旧仓实际用的两个 recon 端点**未列入官方限额表**，只能按同族类推 | 风险登记 |
| 5 | D-Q56 原文说「R4/R5 建域前改图纸」，而 007 把财务域排成 R2-08——顺序错位，图纸已按 R2-08 口径改了，此处只作记录 | 口径注记 |
| 6 | **图纸两处「沿用/保留原版」与原版实际不符**——原版既无 `matched` 对账态列、也无 headline 平账校验，两者都是**新设计**（§12.1） | **图纸表述失实**，批注待落笔 |
| 7 | 旧仓 `INSERT OR IGNORE` + 丢弃 skip 计数＝双重吞噬，是 §3「少计费用」风险的**实证机制**而非推测（§12.1） | 现状核实 |
| 8 | 契约 finance 零覆盖；顶层 `tags` 缺 4 个；两处 tag 大小写漂移（§12.3） | 欠账登记 |

---

## 1 闸门核实：工单说「等图纸」，图纸十天前就改完了

`review_list.json` 的 R2-08 条（`last_checked_at: 2026-07-16`）写：

> 硬前置：§08 图纸按 immutable event ledger 修订(D-Q56 裁定,审计工作区落笔)——**开发等图纸**

**实际**：`git log --follow specs/001-domain-model/08-finance.md` 头一条就是

```
421f83d docs(spec): §08 财务图纸 immutable event ledger 修订(D-Q56 C4)——R2-08 闸门解除
        2026-07-17 03:46:59 +0000
```

图纸正文 `08-finance.md:4` 亦自注「**D-Q56·评审 C4（2026-07-16 修订，审计工作区落笔，R2-08 建域前生效）**」。

**结论：R2-08 无前置阻塞，可立项。** 这是又一例「关账/解闸没回写 review_list」——与 2026-07-26 查出的 PR #29 那类同源（当时修的是 R2-11 accepted 却挂 in_progress 达 9 天）。本轮已修 R2-08 条并记入台账。

> 台账门禁（`tests/test_agent_ledger.py`）目前只校验结构不校验「与 specs 的一致性」，抓不到这类。
> 是否值得加一条「finding 里声称的前置与 specs 现状对拍」——**不建议**：那需要自然语言理解，
> 判据不可机械化，做出来必然是脆的。这类只能靠例行核对（如 2026-07-26 那次全面核对）兜。

## 2 现状基线：财务域是空地

```bash
grep -rln 'financial_event|ledger_entry|profit_ledger|settlement' backend/src/ backend/alembic/versions/
# → 零命中
ls backend/src/erp/    # aftersale audit automation catalog channel compliance core identity
                       # listing notify order pricing scrape tools —— 无 finance
```

- **无 `erp/finance` 模块**，无任何财务相关服务/路由/任务。
- **迁移链 0001~0039 无一张财务表**。图纸里 5 张表（`financial_event` / `ledger_entry` /
  `settlement_snapshot` / `settlement_line` / `profit_ledger` / `exchange_rate_log`）全部待建。
- 结论：R2-08 是**纯新建**，不是移植改造，也没有「现状与图纸不符」的历史包袱要清。
  （对照 R2-11 当初「图纸说列亦未建、实际 0007 已建全套 DDL」那种错位，此处不存在。）

## 3 ⚠️ 幂等自然键：图纸提议 vs 渠道实际供给，对不上

这是本阶段最要紧的发现。**已于 2026-07-26 裁定并落笔（main `b4f286f`），阻塞解除**——详见本节末。

### 图纸怎么定的（`08-finance.md:49-52`）

> **幂等协议**：`uq_financial_event (source_kind, source_ref, event_kind)`。
> `source_ref` 用**来源自然键**而非行 id——结算重拉产生新 snapshot 版本时，同一笔结算行
> （**`store:period:line_kind:order_no:sku:seq`**）不会二次过账。

### 渠道实际给什么（旧仓 `fetch_walmart_settlement.py:192` 的唯一键）

```sql
UNIQUE(store, report_date, transaction_key, amount_type)
```

`recon_details` 的交易核心字段：`transaction_type` / **`transaction_key`** / `transaction_desc` /
`transaction_reason_desc` / `transaction_posted_ts` / **`amount_type`** / `amount` / `total_payable`；
订单维度另有 `purchase_order` / `purchase_order_line` / `customer_order` / `customer_order_line`；
商品维度 `partner_item_id` / `partner_gtin`。

### 错位在哪

| 图纸提议的组成 | 渠道是否直接提供 | 说明 |
|---|---|---|
| `store` | ✅ | |
| `period` | ⚠️ 半 | 渠道给的是 `report_date`（报告日）与 `period_start/end`；「period」指哪个需定死 |
| `line_kind` | ❌ | 渠道给 `transaction_type` + `amount_type` 两个维度，图纸的 `line_kind`（sale/refund/fee/adjustment）是**我方归类**，不是渠道字段——归类规则本身要版本化，否则规则一改，同一行会算出不同 `source_ref`，幂等失效 |
| `order_no` | ✅ | `purchase_order` / `customer_order` 二选一（需定死用哪个） |
| `sku` | ⚠️ | `partner_item_id`，但**费用类行无 SKU**（图纸自己也写了「费用类无单」） |
| `seq` | ❌ | 渠道无此概念；旧仓靠 `transaction_key` 区分 |
| — | | **渠道真正的唯一标识是 `transaction_key` + `amount_type`**，图纸没提到这两个字段 |

**风险**：若照图纸字面实现 `source_ref`，会出现两类失效——
①`line_kind` 归类规则变更 → 同一结算行生成不同 `source_ref` → 重拉时**二次过账**（重复计收入）；
②费用类行 `sku`/`order_no` 为空 → 多条不同费用行拼出**相同** `source_ref` → 后到的被幂等键**静默吞掉**（少计费用）。
两个方向都是钱算错，且都不会报错。

> ### ✅ 已裁定并落笔（2026-07-26，main `b4f286f`）
>
> 本条建议**已被规划/审查 AI 采纳并写进图纸**，且比原建议更进一步：
> - `source_ref` 改为 `{store_id}:{report_date}:{transaction_key}:{amount_type}`（渠道行身份，不含任何我方派生值）；
> - **另发现 `event_kind` 也是派生值且原本在唯一键里**（我这条没抓到），一并移出；
> - 新增 `posting_seq INT`——同一渠道行的第 n 次过账，**仅当前次已被 reversal 冲销后**才允许递增重过；
> - 唯一约束改为 `uq_financial_event (source_kind, source_ref, posting_seq)`；
> - `settlement_line` 补 `transaction_key` / `amount_type` 两列 **NOT NULL**，并写入「R2-08 建域预检：
>   拉取实现必须原样落库这两列，缺任一即无法构造幂等键」。
>
> **增量2 的阻塞已解除。** 下文保留原分析作为该修订的依据记录。

**原建议（已采纳）**：`source_ref` 直接采用渠道自然键
`store:report_date:transaction_key:amount_type`，与旧仓唯一键同构；`line_kind` 降级为**派生列**
（由 `transaction_type`+`amount_type` 按版本化规则映射，规则版本记在 `posting_rule_version` 上），
不参与幂等键。这样幂等只依赖渠道给的事实，不依赖我方可变的归类口径。

> 图纸 §「结构参考」本就写着「walmart_settlement.db 的 settlement_snapshots + recon_details
> ——**语义继承，字段按新模型规范化**」。本条即是把「语义继承」落到幂等键这一具体处。

## 4 渠道供给与限额（两条端点未列入官方表）

旧仓三条端点（`fetch_walmart_settlement.py:388-433`）：

| 端点 | 用途 | 官方限额表 |
|---|---|---|
| `GET /v3/report/payment/statement` | headline 结算摘要 | ✅ **15/min**（Payment Reports） |
| `GET /v3/report/reconreport/availableReconFiles?reportVersion=v1` | 列可用对账文件日期 | ❌ **未列** |
| `GET /v3/report/reconreport/reconFileJson?reportDate=…` | 对账明细 JSON | ❌ **未列** |

官方表里有同族的 `GET /v3/report/reconreport/reconFile` = **100/min**（非 JSON 变体）。
`reconFileJson` / `availableReconFiles` 只能按同族类推 100/min。

**这与 MP_MAINTENANCE 是同一类风险**（R2-12 已踩过）：`rate_limiter.py` 里给 MP_MAINTENANCE 配的
`10/3600` 就是从 MP_ITEM 实测值类推的，官方表没列。**处置口径应一致**——落 `rate_limiter.py`
时把这两条按 100/min 配上，并在注释里写明「类推值，非官方；真实上限未知」，
且**必须读响应头** `x-current-token-count` / `X-Next-Replenishment-Time` 做自适应退避。

> 另注：`/v3/report/payment/performance` 也是 15/min，日报 KPI 若要用得算进同一预算。

## 5 D-Q56 与 007 的顺序错位（记录，不阻塞）

D-Q56 原文（`DECISION-FORM.md:221`）：

> ④财务域按 immutable event ledger 设计（**R4/R5 建域前改图纸**）

而 007 把财务域排成 **R2-08**（R2 阶段）。图纸修订时已按 R2-08 口径落笔（`08-finance.md:4`
写的是「R2-08 建域前生效」，且 commit 标题写「R2-08 闸门解除」），**实质已对齐**，此处仅作
口径注记，避免将来有人拿 D-Q56 原文质疑「财务域为何提前到 R2」。

---

## 6 阶段二取证清单（下一轮做）

1. `fetch_walmart_settlement.py` 全文 820 行逐段——重点：增量拉取的断点续跑口径、
   `save_snapshot` 的 headline 字段全集（含二十余个 `adj_*` 列）、`--force` 全量重拉语义、
   `_to_float`/`_to_int` 的空值与脏值处理（图纸「headline 与明细求和不平 → 告警」需要它的口径）。
2. `settlement_snapshots` 全字段 → 图纸 `settlement_snapshot` 的列映射表，标出**旧有而图纸无**
   与**图纸有而旧无**两类缺口。
3. 旧 `store_kpi_snapshots` / `payout_accounts`（工单点名的日报 KPI 考古源）——**尚未定位到文件**，
   本轮 `find` 未命中，需在 erpAPI 仓与 T7 备份中继续找。
4. `erp-core/handoff-design/project/src/pages-finance.jsx`——既有前端形态，供 R2-08 页面参考
   （D-Q53 前端打磨是 Owner 触发制，但财务页的信息架构可先摸）。
5. 契约与权限点缺口：现有 `permission` 表**无任何 finance 模块权限码**（0002 的 perms 列表里
   `order`/`pricing`/`listing`/`catalog`/`channel`/`identity`/`compliance` 俱全，**无 finance**）——
   R2-08 需新增权限码，且按 2026-07-26 新上线的可达性门禁，**新增即须同时授给模板角色或声明超管专属**，
   否则 CI 直接红。这条是新门禁上线后第一个会撞上它的工单，正好验证门禁有效。

## 7 给立项的初步判断（非结论，供拆单参考）

- **量级**：图纸 5 张表 + 三层过账链 + 4 条验收补强（可复算 / 不可变实证 / 冲销演练 / 汇率审计），
  外加渠道拉取与对账工作台。粗估**接近 R2-12**，明显小于 R2-09（后者要接 9 条 flow）。
- **可拆点（初判）**：①结算文档层拉取入库（settlement_snapshot/line + 版本化重拉）
  ②事件层 + 幂等过账（含 §3 那条自然键裁定）③分录层 + 汇率块 + 过账规则版本化
  ④投影层 + rebuild_check 内建对账 ⑤对账工作台页 + 未匹配行人工核对 ⑥日报 KPI。
- **最该先裁的**：§3 的 `source_ref` 组成。它是增量2 的地基，且改起来是**破坏性**的
  （幂等键定错，已过账的事件要全量重算），不能边做边改。

---

# 阶段二（2026-07-27 续）：旧仓字段全貌 · 图纸覆盖缺口 · KPI 归属

## 8 ⚠️ 第二条硬缺口：007 要「KPI」，图纸 §08 零覆盖

007 对 R2-08 的标题原文（`007:58`）：

> ### R2-08 财务域【L1】（结算对账 + 利润账 + **KPI**）

工单 `check` 亦写「→ 日报 KPI（考古＝旧 `store_kpi_snapshots`/`payout_accounts`）」。

**但 `specs/001-domain-model/08-finance.md` 全文对 KPI 与收款账户零字**——
`grep -niE 'kpi|otd|vtr|payout_account|收款账户|绩效'` 在图纸里**零命中**。

### 旧 `store_kpi_snapshots` 到底是什么（`erp-core/backend/alembic/versions/0003_kpi_snapshots.py:21-41`）

| 列 | 含义（阈值来自注释） |
|---|---|
| `otd` | On-time Delivery ≥90% |
| `cancellation` | Cancellation Rate ≤2% |
| `vtr` | Valid Tracking Rate ≥99% |
| `srr` | Seller Response Rate ≥95% |
| `refund_rate` | Refund Rate ≤6% |
| `negative_review` | Negative Review Rate ≤2% |
| `return_rate` | Return Rate ≤6% |
| `inr` | Item Not Received ≤2% |
| `composite` | green / yellow / red 综合灯 |
| `source` | walmart_api / yingdao_rpa / manual |
| `raw_payload` | JSONB 原始响应 |
| 键 | `(store_id, store_name, snapshot_date)` |

**这是店铺健康/绩效 KPI，不是财务指标。** 八项全部对应 Walmart **Insights Performance** 系端点
（CLAUDE.md 已注明「Insights 性能指标类**全部 1/min**」，共 22 个端点；其中 `refunds/summary`
已被官方标 Deprecated、由 `returns/summary` 取代）。它与结算/利润/分录**没有任何数据关系**——
唯一的共同点是「都按店按日出一份报表」。

### 旧 `payout_accounts`（`0002_phase1b_v5_tables.py:231-245`）

| 列 | 说明 |
|---|---|
| `id`/`name`/`account_masked`/`type` | 收款账户（账号打码） |
| `stores` JSONB | 该账户绑定哪些店 |
| `kyc`/`status` | 认证与启用态 |
| `month_income`/`pending`/`frozen` | 月入账 / 待入账 / 冻结 |

这是**收款通道账户主数据**（D-Q40 已定「收款仅 PingPong 标签」）。图纸 §08 只在
`settlement_snapshot.payment_processor` 留了一个**标签字段**，没有账户实体，
也没有 `pending`/`frozen` 这类资金状态。

### 结论与建议（待裁）

**R2-08 当前范围有三块，图纸只覆盖了一块半**：

| 子域 | 007 要求 | 图纸 §08 覆盖 | 缺口 |
|---|---|---|---|
| 结算对账 + 利润账 | ✅ | ✅ 完整（事件/分录/投影三层） | — |
| 收款账户 | 隐含（payout_accounts 考古锚点） | ⚠️ 只有一个 `payment_processor` 标签 | 无账户实体、无 pending/frozen 资金态 |
| 日报 KPI | ✅ 写进标题 | ❌ **零覆盖** | 整块无图纸 |

**建议（供 Owner/规划侧裁）**：**把「日报 KPI」从 R2-08 拆出去**，理由三条——
①它不是财务数据，与事件/分录/投影三层模型没有任何耦合，塞进财务域会污染域边界；
②它的数据源是 Insights 系 22 个端点（**全部 1/min**，且有 6 个未列入官方限额表、
1 个已 Deprecated），拉取节流与重试策略自成一套，与结算报告（15/min、100/min）不同量级；
③图纸零覆盖意味着**做它就要先补图纸**，而结算/利润那两块图纸已就绪、可以立刻开工——
捆在一起会让已就绪的部分陪着等。

若 Owner 认可拆分，建议新开 **R2-13 店铺健康 KPI**（或并入既有的店铺事件域 R2-07），
R2-08 收敛为「结算对账 + 利润账 + 收款账户」。**这条不裁不影响结算/利润部分开工**，
但会影响拆单粒度与验收判据，宜在立项时一并定。

## 9 settlement_snapshot 字段映射：图纸 5 个聚合数 vs 旧仓 ~75 列

旧 `settlement_snapshots`（`fetch_walmart_settlement.py:41-135`）分七组共约 75 列：

| 组 | 列数 | 代表字段 |
|---|---|---|
| 卖家信息 | 5 | `partner_id` / `seller_status` / `payment_status` / `tenure_days` |
| **账户摘要** | 15 | `opening_balance` / `order_activity` / `wfs_fees` / `reserve` / `hold_amount` / `hold_dates` / **`paid_to_you`** / `closing_balance` / `scheduled_settlement_date` / `settle_cycle` / `reserve_to_date` / `outstanding_mca` |
| 销售汇总 | 20 | `sale_product_price` / `sale_net_comm` / `sale_total_base_comm` / `sale_comm_savings` / `sale_wfs_shipping` / `sale_above_cap` / `sale_pricing_adjustment` / `sale_net_payable` … |
| 退款汇总 | 20 | `refund_*` 与销售组对称 |
| 调整项 | 5 | `adj_net_payable` / `adj_dispute_settlement` / `adj_return_ship_charge` / `adj_return_handling_charge` / `adj_fwd_shipping_fee` |
| WFS | 9 | `wfs_fulfillment_fee` / `wfs_storage_fee` / `wfs_return_shipping_fee` / `wfs_removal_fee` / `wfs_disposal_fee` / `wfs_prep_fee` / `wfs_adjustment` |
| 合作伙伴 | 2 | `partner_net_payable` / `partner_advance_payment` |
| 原始 | 1 | `raw_json` |

图纸 `settlement_snapshot`（`08-finance.md:97-102`）只有：
`team_id/store_id` · `period_start/end` · `currency` · **`gross_sales`/`refunds`/`channel_fees`/`adjustments`/`net_payout`**（5 个 headline 聚合） · `payment_processor` · `report_ref/pulled_at` · `version/status`。

### 三处值得裁的收缩

**(a) 缺「渠道自报的已付金额」这个对账锚点。** 图纸 `net_payout` 是我方口径的净额；
旧仓的 **`paid_to_you`** 与 `closing_balance` 是**渠道自报的实付**。图纸 §92 要求
「headline 与明细求和不平 → 对账异常 notification」——但真正该对的那个数（钱到底打了多少）
在图纸里没有独立字段。建议至少保留 `paid_to_you` / `opening_balance` / `closing_balance`
三列，让「期初 + 本期活动 = 期末 = 实付」成为可机械校验的恒等式。

**(b) WFS 与佣金明细被压成一个 `channel_fees`。** 旧仓把 WFS 拆成 7 项费用、佣金拆成
`net_comm`/`base_comm`/`comm_savings`/`above_cap` 四项。全压进 `channel_fee` 一个科目后，
**「这个月 WFS 费用为什么涨了」在系统里答不出来**，只能回去翻 `raw_json`。
考虑到 `ledger_entry.account` 的 CHECK 集合是图纸定死的六个（revenue/refund/channel_fee/
adjustment/purchase_cost/freight_cost），要么扩科目集合，要么在 `ledger_entry` 加一个
`fee_subtype` 维度列。建议后者——扩科目会动记账模型，加维度列只影响可分析性。

**(c) 图纸没有 `raw_json` 落点。** 旧仓每张快照都存原始响应。immutable ledger 设计尤其需要
原始凭证（审计要能复算、争议要能回溯、字段解析改了要能重放）。图纸只有 `report_ref`
（凭证**引用**）。建议 `settlement_snapshot` 增 `raw_payload JSONB`——它本就是「只进不改」
的文档层，存原文与该层定位一致。

> 注：(a)(b)(c) 三条都属**图纸修订**，按纪律归规划/审查 AI，本文件即批注素材。
> 但**不阻塞开工**——三条都是「加列/加维度」，不动事件/分录/投影三层骨架，可在增量1 落表时一并带上。

## 10 阶段二余下未做（✅ 三项均已于 2026-07-27 完成，详见 §12）

- `fetch_walmart_settlement.py` 的**行为语义**（增量拉取断点续跑口径、`--force` 全量重拉、
  `_to_float`/`_to_int` 脏值处理、headline 平账校验的实际实现）——本轮只读了 schema 与端点，
  未逐段读 820 行的控制流。
- `erp-core/handoff-design/project/src/pages-finance.jsx` 既有前端形态（D-Q53 触发制，可后置）。
- 契约缺口：`permission` 表**无任何 finance 模块权限码**（已在阶段一记）；OpenAPI 契约无
  Finance tag；前端无财务页路由。

## 11 截至阶段二的待裁清单（立项时一并提请）

| # | 事项 | 阻塞面 | 建议 |
|---|---|---|---|
| ~~1~~ | ~~`source_ref` 幂等键组成（阶段一 §3）~~ | ~~阻塞增量2~~ | ✅ **已裁定并落笔**（main `b4f286f`，另加 `posting_seq` 与 `event_kind` 移出唯一键；阻塞解除） |
| 2 | 「日报 KPI」是否拆出 R2-08（§8） | 影响拆单与验收判据 | 拆出（不是财务数据 / 限额量级不同 / 图纸零覆盖会拖累已就绪部分） |
| 3 | snapshot 保留 `paid_to_you` 等对账锚点（§9a） | 不阻塞，增量1 带 | 保留三列，让平账成为恒等式 |
| 4 | 费用明细维度 `fee_subtype`（§9b） | 不阻塞，增量1 带 | 加维度列而非扩科目集合 |
| 5 | `raw_payload` 落点（§9c） | 不阻塞，增量1 带 | 文档层存原文，与「只进不改」定位一致 |
| 6 | 收款账户实体是否建（§8） | 影响范围 | 待裁；D-Q40 只定了标签，未定实体 |
| 7 | **三方对账是否在 R2-08 范围**（§12.2） | 影响范围与验收判据 | 待裁；设计稿 `pages-finance.jsx` 做的是 Walmart 结算 × Amazon 采购 × **支付流水** 三方，图纸 §08 只有两方、第三方无对应实体 |
| 8 | **结算真值回填订单佣金是否 R2-08 承接**（§12.4） | 影响拆单 | 待裁；`erp-core` 已上线该行为并带 `commission_source: settlement\|estimated` 标记，图纸未覆盖此链路 |

## 12 阶段二收尾：余下三项已做完（2026-07-27，只读）

### 12.1 `fetch_walmart_settlement.py` 控制流（820 行逐段读完）

**⚠️ 两条图纸表述与原版实际不符（批注素材，归规划/审查 AI 落笔）**

| 图纸原文（`08-finance.md`） | 原版实际 | 核实方式 |
|---|---|---|
| :108「结构与原版一致（headline 汇总 + 明细行 + **matched 对账态**）」、:117「沿用原版语义（按 `channel_order_no+sku` 匹配→回填 `order_id`/`matched`、未匹配行进人工核对页）」 | **原版没有 `matched`/`matched_at`/`order_id` 任何一列**。`recon_details` 全仓只在 `fetch_walmart_settlement.py:136` 定义一处；全仓 grep `matched` 在结算语境下零命中；`erp-core/backend/alembic/versions/` 无任何结算表 | `grep -rn "CREATE TABLE.*recon"` + 全仓 `matched` 扫描 + erp-core 迁移目录扫描 |
| :115「headline 与明细求和不平 → 对账异常 notification（不静默吞，**原版保留**）」 | **原版没有任何平账校验**。全文只有 `cmd_query` 里 `pending_payment = closing - hold` 一处展示算术，不是校验 | 全文 grep `平账/balance/校验/assert` 后逐处确认 |

**危害不在措辞而在后果**：「沿用原版语义」这类写法会让实现方以为有现成参照、细节已定，于是不写详细规格；而匹配规则（一对多怎么办、部分匹配算什么态）、平账容差（浮点求和允许差多少）**原版根本不存在，全都没定义**。这与本周修的几处 fail-open 同形——**看起来有依据，实际是空的**。建议图纸把这两处从「沿用/保留原版」改写为「**新增设计**」并补规格。

**其余控制流要点**

- **增量断点续跑口径**：`get_existing_recon_dates` 只查 `SELECT DISTINCT report_date FROM recon_details WHERE store=?`——**靠「行是否存在」推断账期是否拉全**。逐层核实后**当前实现下是安全的**，但这份安全依赖三个细节同时成立：①`fetch_recon_json` 累积完所有页才整体返回，任一页抛异常则整个账期不落库；②`save_recon_records` 每账期一次 `commit()`；③sqlite 隐式事务保证进程被杀时回滚。**任一处改动就会退化**——比如改成流式逐页写库，就变成「部分落库 → 永久跳过 → 静默缺数」。R2-08 若沿用「按账期判增量」，必须**显式记录账期级完成态**，不能靠行存在性推断。
- **⚠️ `INSERT OR IGNORE` + 丢弃 skip 计数＝双重吞噬**（`:309` / `:361` / `:572`）：`INSERT OR IGNORE` 已经吞掉约束冲突，外层还 `except sqlite3.IntegrityError`（因而基本永不触发、`skipped` 恒为 0）；调用方 `ins, skip = save_recon_records(...)` 把 `skip` **直接丢弃**，只累加并打印 `ins`。→ **幂等键一旦选错造成碰撞，少计费用且计数器不报**。这就是阶段一 §3「多条费用行拼出相同 `source_ref` → 被幂等键静默吞掉」那条风险在旧仓里的**实证机制，不是推测**。
- **`--force` 是「重拉但不覆盖」**（`:536`）：force 只是不预加载 `existing_map`，所有行仍走 `INSERT OR IGNORE`。→ **渠道事后修订过的行不会更新，静默保持旧值**；旧仓没有「渠道数据可被修订」的处理路径。这印证了图纸修订②「重拉协议版本化」的必要性，但图纸只处理了 snapshot 层——**明细行层（`settlement_line`）的重拉语义图纸没说**，建议一并补。
- **`_to_float`/`_to_int` 脏值→`None`**（`:367`）：金额字段解析失败静默变 NULL。NULL 与 0 在后续 `SUM` 里表现不同但都不告警。
- **限额量级**（补齐阶段一 §4 那条只说了「未列入官方表」的风险）：`cmd_fetch` 用 `ThreadPoolExecutor(max_workers=8)` **8 店并发**，每店多账期、每账期 `fetch_recon_json` 分页循环（`page_size=1000`）**无节流、无退避、无页数上限**。而 `reconFileJson` 未列入官方限额表。突发请求量不可控。
- **`settlement_snapshots` 无任何唯一键**（`:42` 仅 `AUTOINCREMENT`）：每次 fetch 插一行新的 → 它是**抓取日志，不是账期记录**。图纸给的 `uq_settlement (store_id, period_start, period_end)` 同样是**新增约束而非沿用原版**。

### 12.2 `pages-finance.jsx`（532 行）：设计稿，不是实现

硬编码数组（`:12` `items`、`:326` `weeks`、`:348` `wf`），**零 API 调用**，位于 `handoff-design/` 下，属 UI 设计原型。

但它定义了一个**图纸没有的产品概念**：`FinanceReconPage` 做的是**三方对账**——Walmart 结算 × Amazon 采购成本 × 支付流水（`:10` 注释「Every order has 3 legs」），三态 `matched` / `partial` / `unmatched`，`partial` 的实例是「支付金额 $32.80 ≠ 采购金额 $30.50，差 $2.30（疑似 tip / 加急）」。而图纸 §08 的对账只有**两方**（渠道明细 ↔ 本地订单），**第三方「支付流水」在图纸里没有对应实体**。→ 列入待裁（是否在 R2-08 范围）。

### 12.3 契约缺口（三条，均已实测）

1. **finance 零覆盖**：契约无 `Finance` tag，无任何 settlement/profit 路径（grep 零命中）。与阶段一记的「`permission` 表无任何 finance 权限码」构成同一个空白面——R2-08 建端点时**契约、权限码、tag 三样都要新建**。
2. **顶层 `tags` 块缺 4 个**（坐实 Owner 2026-07-26 的次要项报告）：已声明 7 个 `[Auth, Identity, Channel, Catalog, Listing, Order, Portal]`，而 paths 实际用到 11 个，缺 **`Scrape`(5) / `Audit`(3) / `Compliance`(11) / `Aftersale`(6)**。反向无废声明（声明了却无 operation 使用的为 0）。
3. **两处 tag 大小写漂移**（本轮新发现）：代码 `aftersale/router.py:23` 用 `tags=["aftersale"]`、`order/router.py:25` 用 `tags=["order"]`，**均为小写**，而契约写 `Aftersale` / `Order`。codegen 按 tag 分组生成客户端命名空间，大小写不一致会产出两套命名。另 `Notify` 与 `ScrapeWorker` 两个 tag 只存在于代码、契约完全没有——与 RS-11 D 类欠账 9 条同源。

### 12.4 一条既有产品行为，图纸未覆盖（供拆单参考）

`erp-core/backend/app/api/v1/orders.py` **已经在用结算数据回填订单佣金真值**：`commission_source: "settlement" | "estimated"`（`:309`），取不到真值才估算 15%（`:584`），真值来源写明是 `walmart_settlement.db.recon_details` 里 `amount_type='Commission on Product'`（`:618`）。这是**已上线的产品行为且带真值/估算标记**，图纸 §08 未覆盖「结算真值回填订单」这条链路。→ 列入待裁（R2-08 是否承接）。

> 附一条与 R2-08 无关的旧仓事实：该文件用**硬编码 macOS 绝对路径** `/Users/nextderboy/Projects/erpAPI/walmart_settlement.db`（`:89`）连 SQLite。仅作旧仓现状记录，新仓不涉及（禁 SQLite 进生产路径是铁律 5）。
