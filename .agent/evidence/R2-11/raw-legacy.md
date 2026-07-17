# R2-11 考古原始报告：legacy

## 摘要

旧仓有完整且生产级的变体实现，集中在 auto_listing/（worker 端 Phase 0.7/0.8/2.5 + inject_variant_fields）与 erp_listing_server/（server 端跨店 anchor 锚定），并配有两份详细设计文档（variant_groups_design.md、variant_anchor_design.md）。归组语义：full_variant_group_set = parent_asin ∪ variation_asins ∪ 自身 ASIN，variantGroupId 取 min(full_set)（因 DMIT 的 parent_asin 一律填自身，不能当组 ID）；Walmart 端无父体实体，纯靠同 variantGroupId + variantAttributeNames + 同名属性值归组。踩坑记录密集：DMIT 属性列实际是 snake_case（color_name=）而非设计假设的 Title Case、Art Sets 类 PT 的 color 实为套装件数需 LLM/硬编码重映射、变体组 per-seller 跨 batch 漂移需持久 anchor、2026-06-05 server 重启后店铺状态空导致整批误拒（已改 fail-open）。采集侧素材（parent_asin/variation_asins）在 erp-core scraper 本来就有，但正则全抓 ASIN 的实现是"巨型伪组"的可能根因；erp-core 仓内无 R-ERP 编号调研文档正文（README 仅引用 .agent/evidence/R-ERP-*.md，未随仓同步）。

# 旧仓（erpAPI）变体（variant/variation）语义考古报告

## 0. 结论速览

**旧仓有完整的变体实现**，且是经过多轮实战迭代（2026-05-30 初版 → 2026-06-03 大改 → 2026-06-05 事故修复）的生产级代码。分两层：

| 层 | 位置 | 职责 |
|---|---|---|
| Worker 端（Mac） | `auto_listing/` | 归组、PT 校验、属性重映射、feed 字段注入、title 差异化、飞书回写 |
| Server 端（DMiT） | `erp_listing_server/server/` | 跨 batch 持久 anchor（变体组锚定店铺）、非 active 店拒绝 |

两份设计文档是理解语义的最佳入口：
- `/home/user/erpAPI/auto_listing/docs/variant_groups_design.md`（变体组上架，草案 v1 2026-05-30，752 行）
- `/home/user/erpAPI/auto_listing/docs/variant_anchor_design.md`（跨 batch 锚定，2026-06-03 定稿）

---

## 1. 变体怎么归组

### 1.1 核心算法：full_variant_group_set（三源 union）

`auto_listing/mapper.py:1307-1329`：

```python
def full_variant_group_set(amazon: dict, self_asin: str) -> frozenset[str]:
    # union: parent_asin ∪ variation_asins ∪ self_asin
```

**DMIT 采集端的真实数据形态**（variant_groups_design.md:116-129，用户 2026-05-30 确认）：
- `父体 ASIN` 列 = **该行自身 ASIN**（DMIT 一律这么填，不是 Amazon 真正的虚拟 parent）
- `变体 ASIN 列表` = 同组兄弟，**不含自己**
- 三个兄弟行各自 union 后得到相同的完整集合 `{B0A, B0B, B0C}`，据此归组

### 1.2 variantGroupId = min(full_set)（关键决策）

`auto_listing/mapper.py:1430-1431`、variant_groups_design.md:322-335：

> **不能**用 parent_asin：DMIT 规则下每行 parent_asin = 自身 ASIN，用它当 group ID 会把同组切成 N 组。

`min(full_set)`（字母序最小 ASIN）的性质：跨兄弟稳定、跨 batch 一致（今天审 [B0A,B0B]、明天审 [B0C]，min 都是 B0A → 后续兄弟自动归入同组）、格式合规（≤300 字符）、人类可读。

### 1.3 Worker 端 Phase 0.7：分组 + PT 一致 + 跨店重定向

`auto_listing/main.py:1044-1123`：
- key = full_set（**不分店**，2026-06-03 从 `(full_set, store)` 改来）
- 组内 PT 不一致 → 整组淘汰 `VARIANT_PT_MISMATCH`（main.py:1074-1089）
- 跨店 → 全组重定向到 leader（min(row_index) 成员）的店（main.py:1091-1114）；`--strict-file-store` 可跳过（main.py:1098-1102, 650）
- 总开关 `ENABLE_VARIANT_LISTING`（默认 "1"，main.py:1058）

**业务规则**（variant_groups_design.md:320）：变体组在 Walmart 是 **per-seller** 的，跨店拆分会让详情页 swatch 只显示本店子集 → 一组变体必须落同一店。

### 1.4 Server 端持久 anchor（跨 batch 防漂移）

问题（variant_anchor_design.md:9-27）：单 batch 内重定向不够——Batch1 上了 B0A 到 A152，Batch2 的 B0B 若按 min(row_index) 可能锚去别的店。2026-06-03 全量分析：22,596 个在线变体组，93.6% 部分上线会受漂移影响，31.5% 的 anchor 落在非 ACTIVE 店。

决策表（variant_anchor_design.md:42-55，实现在 `erp_listing_server/server/dedup_cache.py:209-277`）：

| 情形 | 归店 |
|---|---|
| 组 ≥10 兄弟（巨型伪组，DMIT bug）| 不重定向（`dedup_cache.py:231-236`，阈值 env `ERP_MAX_VARIANT_GROUP_SIZE`，`dedup_cache.py:70-71`）|
| 在线无兄弟（全新组）| leader = min(row_index)，校验 active（`api_tasks.py:414-435`）|
| 在线兄弟全在 1 店 / 多数派 / 平局 | unique / majority / tie（字母序最小店），`dedup_cache.py:252-261` |
| 最终目标店非 ACTIVE | **整组拒绝**，写 `_reject_reason = "重分配后{店}暂停，暂不上架"`（`api_tasks.py:444-448`）|

拒绝链路（server 判定 + worker 写飞书，因 DMiT server 无 lark-cli，variant_anchor_design.md:57-78）：server 在 task xlsx 追加 `_reject_reason` 列 → worker `excel_io.py:267-298` 读列、`excel_io.py:333-343` 拦截不进 pending、`excel_io.py:355-366` collect_rejected_excel 收集 → `main.py:672-675, 1656-1657` 写飞书 J=No / N=原因。

数据源同步：`auto_listing/dedup_sync_to_server.py:47,86,120-176`（在线产品总表 A+B 列每日 14:00 全量、店铺状态表小时级轻量），增量：worker 上架成功后主动 POST `/api/dedup/anchor-update`（`main.py:1881-1898`，防 24h 窗口漂移；server 端 `dedup_cache.py:292-320`、`api_dedup.py:165-177`）。

---

## 2. 变体怎么构建 feed / 父子关系怎么表达

### 2.1 Walmart 的父子表达：没有父体实体

MP_ITEM v5 spec 中变体只有三个字段（variant_groups_design.md:32-51，spec 路径见 :738-751）：
- `variantGroupId`（string 1-300，同组所有 SKU 完全相同 = 唯一归组信号）
- `variantAttributeNames`（array，**enum 因 PT 而异**）
- `isPrimaryVariant`（Yes/No，可选）

即：**不建父体 SKU，Amazon 的虚拟 parent 不上架**；父子关系纯靠"N 个平级子体共享同一 variantGroupId"表达。差异值写在 PT 的同名普通字段里（如 `color="Red"`，variant_groups_design.md:66-81）。

`isPrimaryVariant` 策略 = **不传**（variant_groups_design.md:338-347）：本批未必含真正主变体，跨 batch 各设一次 Yes 会出现双 primary、Walmart 行为未定义 → 让 Walmart 自动选。

### 2.2 注入实现：inject_variant_fields

`auto_listing/mapper.py:1332-1437`，调用点 `main.py:341-347`（sync）与 `main.py:503-512`（async，注释：在 force_amazon_copy 之后注入，Amazon 文案已落 visible 可共存）。5 个降级条件（任一不满足 → 按单 SKU 上架，不报错）：
1. full_set ≥ 2（mapper.py:1360-1362）
2. variation_attributes 非空 dict（:1364-1366）
3. PT spec 有 variantAttributeNames 定义（:1370-1372；`pt_spec.py:133-154` get_variant_attribute_enum 读 `Visible.{pt}.variantAttributeNames.items.enum`）
4. PT enum ∩ Amazon 属性 keys 非空（:1374-1379）
5. **Feature A**：每个属性值再过 PT 单属性 enum/类型校验（:1381-1428）——string 查 enum、integer/number 强转，非法值剔除。注释点明动机（:1352-1354）：防止"删了 color 但 variantAttributeNames 还说有 color"的不一致。

feed 打包无需特殊处理：build_feed 本来就接受 N 个 item 包成 MPItem 数组，同组同店自然进同一 feed（variant_groups_design.md:557-560）。

### 2.3 Phase 0.8：变体维度智能重映射（硬编码 + LLM 兜底）

`main.py:1125-1221` + `mapper.py:1440-1629`。动机（mapper.py:1443-1445）：**Art Sets 等文具 PT 不允许 color，但 DMIT 给 `color_name=48 Color`，语义其实是套装件数**。策略：
1. 硬编码表 `_HARDCODED_VARIANT_KEY_REMAP`（mapper.py:1452-1460）：(Art Sets/Markers/Pencils/Crayons/Colored Pencils, color) → [pieceCount, count, multipackQuantity]，要求全兄弟同 key 且 value 可解析为数字（正则 `_NUMERIC_VALUE_RE` 认 "48 Color"/"24 Pack"/"100 Pcs" 等，mapper.py:1462-1466）
2. LLM 兜底（mapper.py:1548-1629）：整组一次调用保证跨兄弟 key 一致，走 chat_json SQLite 缓存；输出严格校验（key ∈ enum、items 数量与 asin 集合全匹配，否则丢弃降级）
3. 都失败 → inject 自然降级单 SKU（main.py:1131）

⭐ 2026-06-03 修复（main.py:1148-1149）：**删掉 members<2 限制**——本 batch 只上 1 个变体时也要 remap，带 variantGroupId 占位，后续兄弟自动归组。

### 2.4 Phase 2.5：同组 title 差异化（Feature B）

`mapper.py:1632-1688`，调用 `main.py:1402-1426`：同 (store, variantGroupId) 组内 productName 全相同 → 追加 ` - Red, M` 后缀（按 variantAttributeNames 顺序）；总长超 spec maxLength 199 截 base 保 suffix；幂等（已有同后缀跳过）。

### 2.5 Feature C：AA 列写回

`feishu_io.py:558-567` + `main.py:1803-1825`：上架成功（J=Yes）的变体 SKU 把 variantGroupId 写飞书 AA 列，便于人工排查同组；单 SKU 不写。

### 2.6 采集列解析（excel_io）

- 列结构：`excel_io.py:182-183`（col 25 = 变体属性——⭐2026-06-03 DMIT 删 EAN 列表换的；父体 ASIN / 变体 ASIN 列表随后）；变体属性列按**列名**浮动查找（excel_io.py:266-289）
- `_parse_variation_asins`（excel_io.py:97-104）：ASIN 正则 `B0[A-Z0-9]{8}` 直接抓，容多空格/双逗号脏数据（2026-05-30 从 _split_comma 换来，excel_io.py:464）
- `_parse_variation_attrs`（excel_io.py:125-165+）：自动识别三种格式——DMIT 单属性 `color_name=48 Color`（70%）、多属性 `;` 分隔（30%）、旧 Title Case `Color: Red, Size: M`（兼容手填）
- key 映射表 `_AMAZON_TO_WALMART_VARIANT_ATTR_KEY`（excel_io.py:66-94）：DMIT snake_case（color_name/size_name/style_name/number_of_items/item_package_quantity/pattern_name/material_type/lens_color，注释标注 TOP4 频率 415/322/77/28）+ Title Case 兼容；未知 label 走 camelCase 退化，由 PT enum 交集自动过滤

Server 端 api_tasks 也有同源逻辑：`erp_listing_server/server/api_tasks.py:342-354`（_full_variant_group_set，带 ASIN 正则校验）、`api_tasks.py:357-450`（_cross_store_variant_redirect 完整 anchor 决策）。注意 **match（跟卖）任务明确跳过 anchor/变体**（api_tasks.py:473-474, 549）。

---

## 3. 踩过的坑（注释/文档中的实战记录）

1. **DMIT parent_asin = 自身**，不是 Amazon 真 parent → 用 parent_asin 当组 ID 会把同组切成 N 组，必须 min(full_set)（variant_groups_design.md:155-156, 324）。
2. **DMIT 属性格式与设计假设不符**：旧设计假设 Title Case `"Color: Red"`，2026-06-03 实测实际输出 snake_case `"color_name=48 Color"`，且 30% 用 `;` 分隔 → parser 重写为三格式自适应（excel_io.py:62-65, 128-146）。
3. **PT 变体维度错位**：Amazon `color_name` 在文具类目实为套装件数，Art Sets PT 根本不允许 color → 催生整个 Phase 0.8 remap 体系（mapper.py:1443-1445, main.py:1126-1131）。
4. **variantAttributeNames 与实际字段不一致会被拒**：下游 fix_invalid_enums 删了非法 color 值后 attrNames 还声明有 color → Feature A 前置 enum 校验（mapper.py:1352-1354）。
5. **变体组 per-seller，跨 batch 漂移**：单 batch 重定向不够 → server 持久 anchor + 上架成功增量推送（variant_anchor_design.md:13-20；main.py:1881-1898）。
6. **2026-06-05 A123蒋友志 误判事故**：server 重启后店铺状态表未同步（空 dict）→ 所有店被当暂停 → 所有 anchor 重定向被误拒"重分配后X暂停" → 修复为 **fail-open**（状态未知不拦截，`dedup_cache.py:263-270, 280-289`）。
7. **巨型伪组**：DMIT 误抓出 ≥10 兄弟的假变体组 → 不参与 anchor/重定向，各 SKU 留原店（dedup_cache.py:70-71, 231-236）。
8. **拒绝行必须放原店（active）task**：放 anchor（暂停）店的 task 会石沉大海没 worker 处理（variant_anchor_design.md:78）。
9. **同组同名 title**：Walmart 端难区分 → 后缀差异化 + 199 maxLength 截断（mapper.py:1647-1650, 1679-1685）。
10. **单成员 batch 也要带变体字段**：占位 groupId 让后续兄弟自动归组（main.py:1148-1149；边界见 variant_groups_design.md:588-592）。
11. **已知局限**（variant_anchor_design.md:261-266）：30,575 个在线 ASIN 从未被 DMIT 采过（盲区，当全新组）；31.5% anchor 在非 active 店（新变体会被拒到店恢复为止）。
12. **渠道侧字段命名坑**：Walmart 线上返回 camelCase（variantItemsNum）而本地 OpenAPI 写 snake_case → 双写兼容（`产品ID查询产品详情/query_product_detail.py:143-144, 243`）。
13. `tools/sync_online_products.py:26, 117` 有 `variant_offset` 错误类型：DMIT 采集"变体偏移"（详情页跳到别的变体）→ 该 ASIN 按不可售/库存 0 处理、O 列写"偏移"。
14. **仓内未见变体单元测试**：`grep inject_variant_fields|full_variant_group_set|check_anchor` 只命中 6 个实现文件（auto_listing/main.py、mapper.py、feishu_io.py、erp_listing_server 三件）；design doc §11 打了测试完成勾，但测试文件未随本仓副本同步。

---

## 4. 采集侧素材（mapper / erp-core scraper 是否本来就带变体字段）

**带，而且是采集端原生输出**：
- erp-core scraper 解析器：`erp-core/backend/app/services/scraper/parser_v3.py:1480-1493`——`_parse_parent_asin` 用正则 `"parentAsin":"(\w+)"` 抓（抓不到就填自身 ASIN，与 DMIT 行为一致：parser_v3.py:1338 兜底 `"parent_asin": asin`）；`_parse_variation_asins` = **全 HTML 里所有 `"asin":"..."` 的集合减去自身与父体**（parser_v3.py:1487-1491）。⚠️ 这个"全抓再减"的实现会把页面上非兄弟 ASIN（推荐位等）也收进来——很可能就是"巨型伪组 ≥10"的根因。
- 两个解析入口都挂了这对字段：parser_v3.py:190-192（引擎一）、parser_v3.py:912-914（引擎二）
- 旧 parser 同样透传：`erp-core/backend/app/services/scraper/parser.py:232-233`
- 落库/导出：`erp-core/backend/app/services/scraper/async_runner.py:1043-1044`（存 raw_extra）、`erp-core/backend/app/api/v1/collections.py:731-732`（导出列 `父 ASIN` / `变体 ASIN 列表`，取 `pm.extra->>'parent_asin'` / `pm.extra->>'variation_asins'`）
- listing 侧归一化：`erp-core/backend/app/services/listing/dmit_client.py:100-101`（variation_asins str→list）；`scraper_client.py:259-260` 同样归一化
- LLM 映射输入携带但截断：`auto_listing/mapper.py:34-38`（LLM_AMAZON_LIST_LIMITS：variation_asins 最多 20 个进 prompt）

**但 erp-core 后端没有任何变体 feed 构建/归组逻辑**——llm_mapper.py:28 的"变体"是"字段名变体"（无关），audit 管道的 variants 是关键词大小写变体（l2_rules.py:841-845，无关）。即 erp-core 只做了素材透传，变体上架语义全部只存在于 auto_listing + erp_listing_server。

---

## 5. erp-core 调研文档（R-ERP 编号）检索结果

- 全 erp-core 目录 grep `R-ERP` 仅命中 `erp-core/README.md:283`：review 模式约定"发现问题先记 `.agent/evidence/R-ERP-*.md`"，并提到最近一次 `R-ERP-004-full-readonly-review.md`（记了 5 个 P0/P1：UPC 反提交泄漏、Pipeline 终态未关闭、调度计数过早、价格库存提前落地、维护 feed 失去追溯——**均与变体无关**）。
- 但 `.agent/` 目录**未随本仓副本同步**（erp-core/ 下只有 Makefile、README、backend、docs、handoff-design、scripts、specs），R-ERP 文档正文不可得。
- `erp-core/specs/`（008-listing-lifecycle、009-purchaser-review）grep variant/变体/variation 零命中——**新核心 spec 尚未落笔变体**。
- `erp-core/docs/` 的命中全是前端按钮 `variant` prop（frontend_prompt 系列，无关）；唯一沾边的是 `erp-core/docs/frontend_prompt.md:253` 商品详情页 42 字段分组里列了"变体"分组（纯 UI 展示层）。

---

## 6. 对新核心（ERP-ALL）的可迁移语义清单

1. **归组键**：full_set = union(parent_asin, variation_asins, self) + groupId = min(full_set)——渠道中立、跨 batch 稳定，与 D-Q31 的 (team_id, asin) 去重键天然兼容。
2. **降级优先**（对应 D-Q37 稳定优先）：五级条件任一不满足即降单 SKU，不整组失败；PT 不允许维度 → 降级而非淘汰（旧仓已验证此取舍，variant_groups_design.md:711-712）。
3. **anchor 是有状态服务**：需要 asin→store 权威源（新核心 = PostgreSQL listings 表，比旧仓的飞书表+内存 cache 强得多）+ 店铺状态 + fail-open 语义。
4. **属性 key 归一化是脏活主战场**：显式映射表 + camelCase 退化 + per-PT enum 交集过滤 + 数值型强转，这四层缺一不可。

## 开放问题

- 新核心的 variantGroupId 用什么：沿用旧仓 min(full_set)（ASIN 系、渠道耦合）还是挂 master_sku 体系（M{seq} 渠道中立，D1/D-Q2）？涉及跨渠道扩展时组 ID 是否可复用，需 Owner 拍板。
- 跨店 anchor 重定向与多租户去重（D-Q31 (team_id, asin) + 店铺豁免）如何交互：旧仓允许把 B 店任务改写到 A 店上架（消耗原店审核额度，variant_groups_design.md §6.4 已知副作用），新核心是否保留改店语义，还是改为拒绝+人工改派？
- anchor 落在非 ACTIVE 店的组（旧数据 31.5%）策略：旧仓严格拒绝不迁移（决策 4），新核心是否提供人工迁移/整组换店工具？
- 巨型伪组根因在采集端：parser_v3._parse_variation_asins 全页正则抓 ASIN 再做差集（parser_v3.py:1487-1491），新采集器是否修复为只抓 twister/variation 区块，从源头消灭 ≥10 伪组和阈值 hack？
- _HARDCODED_VARIANT_KEY_REMAP（mapper.py:1452-1460）是写死的业务参数，违反 ERP-ALL 铁律 5（业务参数一律配置中心）——迁移时改为配置中心表还是保留 LLM-only？
- offer_mode=match（跟卖）路径旧仓明确跳过变体（api_tasks.py:473-474），新核心单管道双模式（D-Q3/23）是否同样规定 match 模式豁免变体归组？
- 变体单元测试未随仓同步（design doc §11 标已完成但仓内 grep 零命中），迁移前是否需要按 variant_groups_design.md §7.1 清单在新核心补齐测试？