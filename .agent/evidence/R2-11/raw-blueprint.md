# R2-11 考古原始报告：blueprint

## 摘要

R2-11 变体组图纸侦察完成：001 §03 的 variant_group/variant_member 列级图纸完备（含 status=broken 语义、variation_theme、source_parent_ref、is_primary 部分唯一约束），决策链 D-Q2「变体组进 MVP」+ D-Q3 补充「变体差异由 offer_mode 参数包承载」清晰，旧系统规则集中在 BR-LST-001/012/013（阶段序、full_set/PT 一致/跨店 leader、Anchor 锚定）。007 README 的 R2-11 验收判据为「≥3 成员真实变体组 A152 上架 live + 缺员构建拒绝可见原因」。发现若干图纸缺口需拍板：D-Q2 点名的 anchor 实体在新图纸无落点、06-listing 图纸零变体段落、variant_member 未注明公共列缺省、broken 状态的置位/恢复协议未定义。

# R2-11 变体组 · 图纸考古报告

## 1. 领域图纸：`variant_group` / `variant_member`（/home/user/ERP-ALL/specs/001-domain-model/03-catalog.md）

### 1.1 决策依据头（03-catalog.md:3）
> 决策依据：D1（master_sku）、**D-Q2（变体组进 MVP）**、D-Q21、D-Q25、D-Q31、D-Q39、D-Q41。

### 1.2 product 表上的变体触点

- `product.attrs JSONB NOT NULL DEFAULT '{}'` —— 「采集结构化属性（尺寸/材质/**变体维度值**…）」（03-catalog.md:21）。即成员维度取值的原始来源在采集属性里。
- `product.variant_group_id BIGINT NULL REFERENCES variant_group`（03-catalog.md:25）—— 007 已核实**此列在代码中也未建**（见 §4）。

### 1.3 variant_group 表（03-catalog.md:35-44）

章节标题：`## variant_group / variant_member 变体组（D-Q2 进 MVP）`（03-catalog.md:35）

| 列 | 类型 | 约束/默认 | 说明 | 行号 |
|---|---|---|---|---|
| id | BIGINT | PK identity | | 03-catalog.md:39 |
| team_id | BIGINT | NOT NULL | | 03-catalog.md:40 |
| source_parent_ref | TEXT | NULL | **Amazon parent ASIN** | 03-catalog.md:41 |
| variation_theme | TEXT | NULL | **Size / Color / Size-Color…（Walmart variant 属性名由 spec 构建时映射）** | 03-catalog.md:42 |
| status | TEXT | NOT NULL DEFAULT 'active' CHECK IN (active, broken) | **broken=成员不齐/主题冲突，spec 构建拒绝** | 03-catalog.md:43 |
| +公共列 | | | （即 00-conventions.md:23-31 的 team_id/created_at/updated_at/created_by 四件） | 03-catalog.md:44 |

**status 状态机全文即此一行**（03-catalog.md:43）：仅 active/broken 两态；broken 的组完整性语义 = 「成员不齐 **或** 主题冲突」，其唯一下游效应 = spec 构建拒绝。图纸**未定义**：谁置 broken、何时判定、broken→active 恢复条件（见开放问题）。

### 1.4 variant_member 表（03-catalog.md:46-55）

| 列 | 类型 | 约束/默认 | 说明 | 行号 |
|---|---|---|---|---|
| group_id | BIGINT | NOT NULL REFERENCES variant_group | | 03-catalog.md:48 |
| product_id | BIGINT | NOT NULL **UNIQUE** REFERENCES product | **一品最多属一组** | 03-catalog.md:49 |
| variant_attrs | JSONB | NOT NULL DEFAULT '{}' | 该成员的维度取值 {size:"L", color:"Red"} | 03-catalog.md:50 |
| is_primary | BOOLEAN | NOT NULL DEFAULT false | **组主图/主价成员** | 03-catalog.md:51 |
| PK | | **(group_id, product_id)** | | 03-catalog.md:52 |

约束与索引（03-catalog.md:54-55）：
- `uq_variant_member (group_id, product_id)` + **partial unique `(group_id) WHERE is_primary`**（每组至多一个 primary）（03-catalog.md:54）
- 「group 内 variant_attrs 组合唯一**由服务层校验**（JSONB 无法优雅建约束）」（03-catalog.md:55）

**图纸细读注记**：
1. `uq_variant_member (group_id, product_id)` 与 PK (group_id, product_id) 同键冗余；且 product_id 单列 UNIQUE 已使 (group_id, product_id) 必然唯一——迁移时按 PK+product_id UNIQUE 落即可，uq_ 可视为笔误性冗余。
2. variant_member 表**没有** `+公共列` 行，也未像 sku_mapping 那样注明缺省差异（对比 03-catalog.md:194 `+公共列（team_id 经 store）`）。按 00-conventions.md:33 规范「各表定义仅注明『+公共列』或指明缺省差异」，此处属图纸疏漏，需拍板（大概率 team_id 经 group 推导 + created_at）。
3. product.variant_group_id（03-catalog.md:25）与 variant_member 行构成**双向冗余关联**，两处一致性维护协议图纸未写。

## 2. 000-founding 决策原文

### 2.1 DECISION-FORM.md

- **D-Q2**（DECISION-FORM.md:10）：「**变体组进 MVP**」；架构影响：「领域模型第一版就要含 **variant_group / anchor 实体**」。
  - ⚠️ 注意：D-Q2 点名了 **anchor 实体**，但 03-catalog 图纸中不存在任何 anchor 列/表（全 000-founding 中 anchor 仅两处：DECISION-FORM.md:10 与 business-rules-ledger.md:118）。`is_primary`（主图/主价成员）≠ 旧系统 Anchor（店铺锚定）语义。
- **D-Q3**（DECISION-FORM.md:11）：跟卖=上架的一种运营模式，`offer_mode ∈ {build, match}` 单管道双模式。
- **D-Q3 补充说明**（DECISION-FORM.md:72）：build/match 共用配额、定价、feed 提交轮询、生命周期状态机与维护机器；「差异（输入/预检/内容构建/UPC 来源/**变体**/feed 类型）由 offer_mode 参数包承载」——即**变体是 build/match 的模式差异项之一**。
- 关联背景：D-Q42（DECISION-FORM.md:145）采集器源仓 amazon-scraper-v3 含 variant_offset 机制（变体侦测在采集端）。

### 2.2 PRD-v1.md

- PRD-v1.md:45：catalog 域清单——「/ **变体组 variant_group** / 品牌占用 brand_assignment」。
- PRD-v1.md:68-73（§5.1 建品上架主流程 offer_mode=build）：
  「采集(Amazon) → 审核 L0-L4 → audit_passed → 货源匹配 → 分配店铺+GTIN → 定价(build策略) → **变体分组** → spec 构建(零认证覆盖) → feed 提交 → 轮询 → 状态机回写 → 在架」（变体分组位置：**定价之后、spec 构建之前**，PRD-v1.md:72）。
- PRD-v1.md:141（§8 R2 领域纵深模块 3）：「上架生命周期完整态机（build 模式 + UPC 闭环 + **变体组** + 错误分类处置）」——R2-11 归属的 PRD 模块。

## 3. business-rules-ledger.md 变体相关规则（旧系统语义，移植参照）

| 规则 | 原文要点 | 行号 |
|---|---|---|
| BR-LST-001 | 主链阶段序（fail-fast，行级）：…→ **0.7 变体分组 → 0.8 维度重映射** → 1 UPC 预分配 → 2 LLM 映射+同店打包提交 → **2.5 变体标题差异化** → 3 批量回写 | business-rules-ledger.md:106 |
| BR-LST-012 | 变体组核心规则四条：① `full_set` = 父 ASIN ∪ 变体 ASIN 列表 ∪ 自身（正则 `B0[A-Z0-9]{8}`）；② **组内 PT 必须一致**；③ **跨店同组归 leader**（min row_index 的店），其余成员重定向/拒绝；④ PT 不接受 Amazon 维度键时先硬编码映射后 LLM 兜底重映射（如 color_name→pieceCount）；⑤ 同组 productName 全同时追加变体属性后缀 | business-rules-ledger.md:117 |
| BR-LST-013 | **跨店 Anchor 锚定：变体组锚定到店后防漂移，anchor 店铺暂停时成员行拒绝并写明原因，不自动转移**（来源 docs/variant_anchor_design.md） | business-rules-ledger.md:118 |
| BR-PR-004 | 定价 clamp 变体：区间外用最近区间倍数计算并标 out_of_band（旁涉） | business-rules-ledger.md:96 |
| BR-ASC-003 | 采集器 **variant_offset 变动检测**（变体侦测来源在采集端，与 R2-11「采集端 parent ASIN 归组」呼应） | business-rules-ledger.md:232 |
| BR-WSC-003 | **公开页 GTIN 对多变体商品不准** → 权威 GTIN 旁路走卖家后台 isbm 接口（跟卖侧变体注意事项） | business-rules-ledger.md:243 |
| 缺口 G6 | 「erp_listing_server 任务拆分/**变体重定向**的 server 侧规则（api_tasks.py 跨店重定向已部分入账 BR-LST-012）」→ REF-R0-006 补全，**尚未完成考古** | business-rules-ledger.md:330 |

## 4. R2-11 工单原文与验收判据（/home/user/ERP-ALL/specs/007-mvp-completion-plan/README.md）

- 对账表：「3 上架完整态机 🟡 **变体组零实现** → R2-11」（README.md:20）；「5 跟卖模式 🟡 …货源占位链随 R2-11/09 复核」（README.md:22）。
- **工单定义**（README.md:94-104）原文：

  「### R2-11 变体组【L1→L2】（上架态机欠账，D-Q2 MVP 项）（README.md:94）
  **现状核实**：§03 `variant_group`/`variant_member` 列级图纸完备，代码零实现（含 `product.variant_group_id` 列亦未建）。（README.md:96-97）
  - 建表迁移 + 采集端 parent ASIN 归组（source_parent_ref 图纸已留）；（README.md:99）
  - spec 构建器变体段（R2-03 构建器扩展；variation_theme → Walmart variant 属性映射）；（README.md:100）
  - 组完整性守卫（status=broken 拒绝构建，图纸原文）。（README.md:101）

  **验收**：一组真实变体（≥3 成员）A152 上架为 Walmart variant group 并 live；组员缺失时构建拒绝且可见原因。」（README.md:103-104）

- 动工顺序：**R2-11 第一**（「R2-11（小，上架欠账）→ R2-07 → R2-09 → R2-08 → R2-10」，README.md:115）。

## 5. 交叉核实发现（对 R2-11 实现有直接影响）

1. **06-listing-pricing.md 零变体段落**：grep `变体|variant|variation` 在 /home/user/ERP-ALL/specs/001-domain-model/06-listing-pricing.md 中**无任何命中**。R2-11 范围第二条「spec 构建器变体段」在 06 图纸没有承接段落；listing/payload 图纸（06-listing-pricing.md:114，payload=单 MPItem 元素模板）未提及 Walmart variant group 所需的 spec 字段。
2. **PRD 主流程位置**：变体分组在「定价之后、spec 构建之前」（PRD-v1.md:72），而旧系统 BR-LST-001 是 Phase 0.7（廉价过滤后、UPC 之前）——两者顺序精神一致（都在 UPC/spec 消耗前），实现时以新主链态机为准。
3. **公共列规范**：00-conventions.md:23-31 定义四公共列（team_id/created_at/updated_at/created_by）；variant_group 带「+公共列」（03-catalog.md:44，注意与自列 team_id 重复列出），variant_member 未注明。

## 开放问题

- anchor 实体落点：D-Q2 架构影响明文要求「variant_group / anchor 实体」（DECISION-FORM.md:10），且旧规则 BR-LST-013 跨店 Anchor 锚定（锚定到店防漂移、anchor 店暂停成员拒绝不转移）语义明确，但 03-catalog 图纸的 variant_group 无 anchor_store_id 类列、is_primary（主图/主价）不承载店铺锚定语义——新模型中 anchor 由谁承载（variant_group 加列？经 listing 推导？），R2-11 建表迁移前需 Owner/架构拍板，否则与宪法 D-Q2 静默偏离
- status=broken 的置位与恢复协议未定义：图纸仅一句「broken=成员不齐/主题冲突，spec 构建拒绝」（03-catalog.md:43）——谁在何时判定（采集归组时？独立守卫任务？）、「成员不齐」判定基准是否沿用旧 full_set 语义（父 ASIN ∪ 变体 ASIN ∪ 自身，BR-LST-012）、broken→active 的恢复条件与是否自动恢复，需在 R2-11 设计中显式定义
- spec 构建器变体段无图纸：06-listing-pricing.md 对变体零提及，Walmart variant group 上架所需 spec 字段（variantGroupId / variantAttributeNames / isPrimaryVariant 等）与 variation_theme→Walmart variant 属性名的映射表存放位置（配置中心？pt_spec.fields 推导？铁律禁写死业务参数）需规划侧补图纸或确认随 R2-03 构建器扩展自带
- 组内 PT 必须一致（BR-LST-012 旧规则）在新图纸未落任何约束/守卫段落——是并入 broken 的「主题冲突」语义，还是独立校验点？跨店同组归 leader 规则在新多租户+listing 分配模型下是否仍适用（与 anchor 问题同源）
- variant_member 公共列缺省未注明（对比 sku_mapping 明注「team_id 经 store」，03-catalog.md:194）：team_id 是否经 group 推导、是否保留 created_at/created_by，建表迁移前需定；另 uq_variant_member (group_id, product_id) 与 PK 同键冗余（03-catalog.md:52,54），落库按 PK + product_id UNIQUE 即可，建议图纸勘误
- product.variant_group_id（03-catalog.md:25）与 variant_member 行双向冗余，一致性维护（服务层同写？触发器？以哪边为权威）未定义