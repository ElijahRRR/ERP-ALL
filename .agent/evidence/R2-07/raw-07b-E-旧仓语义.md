## 方向 E 考古报告：旧仓（/home/user/erpAPI）封店语义

> 重要前置说明：`/home/user/erpAPI` 里其实混着两层——(a) 真·旧系统运维脚本（`auto_listing/`、`沃尔玛店铺日报/`、`沃尔玛批量下架/` 等），(b) `erp-core/` 一个 **Phase1b 原型快照**（V5 mock + seed，`alembic 0002`）。07b 相关的"旧表/旧代码"几乎全在 (b) 这个原型里，且多为 **只读 + mock seed，无真实写入方**。本报告逐条标注属于哪层。ERP-ALL 的当前 erp-core 可能已把其中一部分搬过去/改写，本报告只陈述 erpAPI 里的事实，落地前请与 ERP-ALL 现有代码交叉核对（属其它侦察方向）。

---

### 一、封店/暂停检测语义（真·旧系统脚本，genuine）

**1.1 上架侧封店闸门** `auto_listing/store_status.py`
- 状态枚举只有三档：`ACTIVE / SUSPENDED / TERMINATED`（`store_status.py:7`）。
- 数据源=飞书"店铺状态表"(店铺KPI总览)，B 列(idx1)=店铺名、F 列(idx5)=状态（`store_status.py:6-7`）。
- 检测机制：`@lru_cache(maxsize=1)` 进程级缓存，每次 main 运行读一次，**刻意不依赖 server 小时级同步**，理由写明"避免店铺被封后最长滞后半天才被上架侧感知"（`store_status.py:1-4`）。
- **fail-open 语义**：表读失败/某店不在表内 → 视为可上架放行，只有"明确非 ACTIVE"才判 False 跳过（`store_status.py:10-11,82-85`）。
- 消费点 `auto_listing/main.py:867-876`（Phase 0）：把非 ACTIVE 店铺从上架 pending 集里剔除，注释"封店当轮即生效"（`main.py:868`），fail-open 放行未知店（`main.py:869-870`）。
- 处置范围仅限**上架闸门**——检测到非 ACTIVE 只是"这轮不上架，留待复活后下次再上"，**不写 incident、不释放品牌、不停采集、不停订单**。

**1.2 状态的上游真实来源（API 侧）** `沃尔玛店铺日报/fetch_walmart_performance.py`
- 状态字段来自 Walmart API 的 `sellerStatus`（`fetch_walmart_performance.py:272`，`si.get("sellerStatus")` → "店铺状态"列）。
- 支付状态非 ACTIVE（HOLD/INACTIVE/SUSPENDED）→ 强制打款写 0（`:253-257`）；非 ACTIVE 店铺"销售状态"填"不可售"（`:734,749-750`）。
- 即：**封店检测链 = Walmart `sellerStatus` → 写飞书状态表 → store_status.py 读**。检测是 API 事实驱动的，可移植思路，但依赖飞书表这一中间层。

**1.3 状态旁路同步** `auto_listing/dedup_sync_to_server.py:120,155`：另读同一飞书表 B+F → `{store: ACTIVE/SUSPENDED/...}`，POST `/api/dedup/store-status`（小时级轻量），与 1.1 平行、不互相依赖。

---

### 二、品牌占用释放语义（erp-core 原型，per-listing 而非 per-store）

**2.1 模型** `erp-core/backend/app/models/brand.py:34-49` `BrandStoreAssignment`（表 `brand_store_assignments`）：
- 唯一键 `UniqueConstraint(brand_normalized, store_id)`（`brand.py:38`）；字段 `brand / brand_normalized / store_id(FK→stores, ondelete=CASCADE) / exclusive(默认True) / active_sku_count(默认0) / created_by / notes`。
- **无 `incident_id` 列**——07b 要的"brand_assignment.incident_id 回链"在旧仓完全不存在。

**2.2 释放原语** `erp-core/backend/app/api/v1/listings.py`
- `_record_brand_assignment`（`listings.py:287-300`）：上架时 `INSERT ... ON CONFLICT DO UPDATE active_sku_count += 1`。
- `_release_brand_assignment`（`listings.py:268-284`）：`active_sku_count = GREATEST(active_sku_count-1,0)`，**减到 0 就 DELETE 整行**，注释"品牌完全释放可分给其它店"。← 这是 07b 可复用的释放原语核心（delete-on-zero 思想）。
- 触发点全是**单 listing 级**：`release_listing`（`listings.py:686`，上架失败/取消的本地清理）、上架成功登记（`listings.py:562`）。
- 现有唯一"批量"入口 `release_failed_listings`（`listings.py:710-737`）：只按 `state='error'` + 可选 `store_id/asin` 过滤，`LIMIT 200` 循环逐行调 `release_listing`。**它按 listing 状态批量，不是按店铺封店批量**。

**2.3 关键 GAP**：旧仓**没有任何代码在店铺状态变更时批量释放该店全部品牌占用**。释放严格由 listing 失败/取消驱动，与店铺封店/暂停解耦。`stores.py` 删店端点 `delete_store`（`erp-core/backend/app/api/v1/stores.py:134-143`）唯一封店动作是 `s.is_active=False; s.status='terminated'; s.terminated_at=now()`，**无任何级联**（不释放品牌、不下架、不写 incident、不停采集/订单）。而且旧仓连"暂停"端点都没有，唯一状态写路径就是这个 delete。

---

### 三、store_incidents 写入方（erp-core 原型 — 无真实写入方）

**3.1 表结构** `erp-core/backend/alembic/versions/0002_phase1b_v5_tables.py:193-215`：
- 列：`id`(String64, 形如 `inc-2025-12-15`) · `store_id`(FK→stores) · `store_name`(NN,index) · `cluster` · `tier` · `kind`(String16,NN,index，取值 `paused`/`banned`) · `reason_code` · `reason`(Text) · `poa_status`(默认 `none`) · `poa_text` · `next_appeal`(Date) · `fund_pending`(Bool默认F) · `fund_amount`(Float) · `fund_appeal_date`(Date) · `resolved`(Bool默认F,index) · `resolved_at` · `created_at`(NN) · `updated_at`。
- **不是 ORM 模型**：`models/__init__.py:1-8` 未导入 StoreIncident，全仓无 `class StoreIncident`。仅原生表。

**3.2 唯一写入方 = seed** `erp-core/scripts/seed_v5/seed_v5.py:208-238` `seed_store_incidents`：从 `v5_mock.json` 灌 mock，`ON CONFLICT(id) DO UPDATE` 只更新 `resolved/fund_pending/poa_status` 三列。**无生产写入方、无 API 创建、无状态流转代码**。

**3.3 只读端点** `erp-core/backend/app/api/v1/v5_data.py`：
- `GET /store-incidents`（`v5_data.py:1762-1787`，filter `kind`/`resolved`，raw SQL，`ORDER BY created_at DESC`）。
- `GET /store-incidents/{incident_id}`（`v5_data.py:1789-1801`）。
- **无 POST/PATCH/PUT**。前端聚合大 payload 里也只是 `"INCIDENTS": list_incidents(...)`（`v5_data.py:2387`）。

**3.4 mock 数据形状** `v5_mock.json` INCIDENTS（4 条）：`kind ∈ {paused, banned}`；`reason_code` 实例 `OTD_BELOW_THRESHOLD / INTELLECTUAL_PROPERTY / TRO_AFFECTED / COUNTERFEIT`；`poa_status ∈ {none, draft, submitted}`；banned 类常带 `fund_pending=true + fund_amount + fund_appeal_date`。字段形状定义清晰，但**无状态机、无写入、无品牌回链、无 beat**。

---

### 四、封店后处置清单（有文档为证，均"要求级"未实现）

来源 `参考资料/walmart_ops_knowledge_v4.docx.md`：

- **1.1.4 暂停店铺**（`:63-67`）：立即动作=记录暂停原因/日期/保存通信记录/计划预申诉时间/AI 生成 POA。坑点=不同原因用不同 POA 模板、部分立即可申诉部分需等待；**产品原因暂停→立即关联产品、检索品牌→品牌所属公司→该公司旗下所有品牌、审查所有产品**；履约差异暂停→分析物流、与采购/物流沟通。ERP 需支持：持续记录暂停日期/原因、AI 生成 POA、**一段时间后向相关人员发提醒申诉**。
- **1.1.5 被封店铺**（`:70-78`）：立即动作=记录封店原因/日期/保存通信/**导出店铺数据**；资料隔离=营业执照/地址/IP 列入**「污染池」不再用于新店**；坑点=收款账户资金可能冻结，记录被封时间后期申诉。ERP 需支持：**封店资金申诉提醒**（显示被封日期，过一段时间提醒申诉资金）← 正是 07b 的 beat 提醒需求。
- **1.3.2 商品合规监控**（`:144-150`）：收到通知 **24h 内评估+下架**；ERP 需支持合规事件队列，部分类型**触发自动下架删除商品**。
- **侵权 SOP**（`:720`）：接收通知 → 24h 内下架 → **追溯品牌链** → 全量排查 → 更新黑名单 → 评估申诉。
- 前端意图 `erp-core/docs/frontend_prompt_v5_stores.md`：banned 事件描述"品牌…被起诉·受影响 listing 12 条·**已自动下架 + 加黑名单**"（`:574`）；paused"POA 提交 → 5 天后恢复·无资金冻结"（`:578`）；店铺合规监控 config"自动下架配置"（`:39`）、"暂停上架（默认）/ 仅告警不暂停"（`:298-299`）；事件中心是"所有 paused/suspended/terminated 状态店铺统一入口"（`:637`），tab=paused/banned/resolved/fund，资金 KPI 按 `fund_pending` 聚合（`:644-660`）。

**处置清单归纳**（文档要求，非实现）：封禁→下架受影响 listing + 加品牌黑名单 + 污染池标记(执照/地址/IP) + 资金冻结记录 + 定时提醒申诉；暂停→记录原因/日期 + AI 生成 POA + 计划申诉日 + 定时提醒；共性→24h 内下架、追溯品牌链、全量排查、更新黑名单。**旧仓无一行代码实现下架/停采集/停订单/污染池级联**——全是待建需求。

---

### 五、beat 定时提醒（erp-core 原型 — 无）

- `grep next_appeal|appeal|POA|incident` 于 `erp-core/backend/app/tasks/` **空命中**——旧仓无任何 incident/申诉提醒 beat 任务。
- store_incidents 表已备好 `next_appeal` / `fund_appeal_date` 列，但**无任何代码读它们做提醒**。ops 文档 1.1.5"过一段时间提醒申诉资金"要求在旧仓完全未落地。

---

### 六、端到端语义链（旧仓现状）

```
Walmart sellerStatus (API 事实)
      │  fetch_walmart_performance.py:272 写飞书状态表 F 列
      ▼
飞书"店铺状态表" (ACTIVE/SUSPENDED/TERMINATED)
      │  store_status.py:41-85 进程级读, fail-open
      ▼
auto_listing/main.py:867-876  ── 唯一处置：非 ACTIVE 店铺本轮不上架
      ✗ 不写 store_incidents
      ✗ 不释放 brand_store_assignments
      ✗ 不下架/停采集/停订单/污染池

（另一条并行、断裂的链）
erp-core stores.py:143 delete_store → status='terminated'
      ✗ 无任何级联
store_incidents 表：只有 seed 写、只读端点、无状态机、无 brand 回链、无 beat
brand_store_assignments：仅 per-listing 增减(listings.py:287/268)，无 incident_id、无按店批量释放
```
**结论：旧仓不存在"封店工作流"闭环。** 检测链（上架闸门）与 incident 表/品牌占用是三条互不相连的孤岛。07b 正是要把它们连成一条链。

---

### 七、可移植 / 不可移植判断

**可移植（直接复用/借鉴）：**
- `_release_brand_assignment` 的 **delete-on-zero** 释放思想（`listings.py:268-284`）——07b 批量释放可复用"减到 0 删行让品牌可再分配"这一语义，但见下方"需改造"。
- `release_failed_listings` 的**批量端点骨架**（`listings.py:710-737`：查行集→循环→收集 released/errors 返回）——07b 品牌批量释放端点可套同一 shape。
- `store_incidents` 表列集（`0002:193-215`）作为 07b 迁移的**基线字段**（kind/reason_code/poa_status/next_appeal/fund_* 已成熟），07b 只需增 `brand_assignment.incident_id` FK 回链 + 真实写入方/状态机。
- `store_status.py` 的**检测语义模式**（三档状态 + fail-open + 当轮生效）作为封店检测的行为参照。
- ops 文档 1.1.4/1.1.5/1.3.2 + 侵权 SOP 作为 07b **处置清单的权威需求来源**（带行号可引宪法）。

**不可移植 / 必须新建：**
- store_incidents **无任何写入方/状态流转**——07b 的创建 + 状态机（paused/banned → resolved）要从零建；现有端点纯只读 + mock seed。
- **`brand_store_assignments` 无 `incident_id`**——需新列 + 迁移；且现有释放是 `-1` per-SKU，而封店批量释放语义应是"该店全部品牌行一次性释放并盖 incident_id"，不能直接套 `-1` 循环，需改造为按 `store_id` 批量置 0/删行 + 回填 incident_id。
- **封店→品牌批量释放的触发接线为空**——`stores.py:143` 删店只置 `status='terminated'` 无级联，需 07b 新建"状态转 suspended/terminated 时触发 incident + 批量释放"。
- **beat 提醒任务不存在**——资金/POA 申诉提醒（读 `next_appeal`/`fund_appeal_date`）净新建。
- store_incidents **非 ORM 模型**——07b 若要 ORM 化需新增 model（现仅原生表 + raw SQL）。
- **污染池（执照/地址/IP 隔离）、自动下架级联、停采集/停订单**——旧仓仅文档要求、零实现。按 07b 任务范围（表+品牌释放+beat）这些应在 07b 之外，但作为"封店处置清单"的相邻未竟需求标注，规划时需明确划界。

**未核实**：ERP-ALL 当前 erp-core 是否已把 `store_incidents`/`brand_store_assignments` 从此原型搬入并改写（属其它侦察方向，本报告未查 ERP-ALL 侧代码）；飞书"店铺状态表"F 列的 TERMINATED 值是否由 `sellerStatus` 直接产出还是人工维护（`fetch_walmart_performance.py` 只见写入 sellerStatus 原值，未见 TERMINATED 归一化逻辑，未核实）。
