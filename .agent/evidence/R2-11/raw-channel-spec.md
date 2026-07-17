# R2-11 考古原始报告：channel-spec

## 摘要

MP_ITEM v5 规格本体（451MB，MPSetup/ 目录）不在本环境，CLAUDE.md 索引已过时；变体字段全集通过设计文档 spec 取证、pt_spec.py 字段路径和 items OpenAPI 三层证据还原。写路径变体字段为 Visible.{PT} 下的 variantGroupId（string 1-300 字符）、variantAttributeNames（array，minItems 1，enum 按 PT 而异）、isPrimaryVariant（可选 Yes/No，现行策略不传）加同名属性值字段和 swatchImageUrl；读路径为 variantGroupId + variantGroupInfo{isPrimary, groupingAttributes}。中文指南无变体专章（仅 2 处查询参数），同组要求/主变体/流程图全部沉淀在 auto_listing/docs 两份内部设计文档中；all_product_types.json 与 taxonomy_v5.json 均无 PT 变体支持标注，唯一判定口径是 per-PT spec 里 variantAttributeNames 是否存在。本地版本 5.0.20260304（2026-04-16 Recommended），按版本节奏 2026 Q3 大概率已有新版需在线核实。

# Walmart 变体组（Variant Group）上架规格考古报告

## 0. 前置发现：MPSetup 规格本体不在本环境（影响任务口径）

任务指定的 `grep -ril "variant" /home/user/erpAPI/walmart_official_specs/MPSetup/` **无法执行——该目录在本环境不存在**。证据链：

- `/home/user/erpAPI/auto_listing/config.py:72-78` 指向 `walmart_official_specs/MPSetup/5.0.20260304-22_45_32-api_MP_ITEM_0_0_en.json`（版本常量 `MP_ITEM_SPEC_VERSION = "5.0.20260304-22_45_32-api"`，config.py:78）
- `/home/user/erpAPI/walmart_spec_version_check.md:14` 记录该文件实际体量 **451MB**（MP_MAINTENANCE 424MB / MP_WFS_ITEM 447MB / OMNI_WFS 45MB，walmart_spec_version_check.md:15-17），显然未随仓库进入本环境
- 运行时按 PT 拆分目录 `MPSetup_by_pt/`（由 `/home/user/erpAPI/tools/split_mp_item_spec.py:12-17` 生成：`_pt_index.json` + `_orderable.json` + `_header.json` + 每 PT 一个 Visible schema 文件）在本环境同样为空
- `/home/user/erpAPI/CLAUDE.md` 的"商品规格文件"索引表仍写 `walmart_official_specs/MPSetup/`，**索引与磁盘现状不符**

因此变体字段全集由三层本地证据交叉还原：① 设计文档中的 spec 逐字取证；② `pt_spec.py` 的 JSON 路径代码；③ items OpenAPI YAML。所有摘录均标注来源。

---

## 1. 变体字段全集

### 1.1 写路径：MP_ITEM v5 上架 feed 的 `Visible.{ProductType}` 层

spec 内字段路径（`/home/user/erpAPI/auto_listing/docs/variant_groups_design.md:742-749` 附录逐字）：

```
properties.MPItem.items.properties.Visible.properties.<ProductType>.properties
  ├─ variantGroupId
  ├─ variantAttributeNames
  │    └─ items.enum  ← per-PT 允许的变体维度
  └─ isPrimaryVariant
```

| 字段 | 类型 | 必填性 | 约束 | 证据 |
|---|---|---|---|---|
| `variantGroupId` | string | spec 标**可选**，但是变体识别为同组的**唯一信号** | **1-300 字符**；同组所有 SKU 必须完全相同 | variant_groups_design.md:39,49（spec 取证表）；代码防御性截断 `group_id[:300]` mapper.py:1431 |
| `variantAttributeNames` | array[string] | 变体上架时必带 | **minItems: 1**；`items.enum` **因 PT 而异**，必须查该 PT 的 spec | variant_groups_design.md:40,50；读取路径 `Visible.{pt}.properties.variantAttributeNames.items.enum` 见 pt_spec.py:134-154 |
| `isPrimaryVariant` | enum {`Yes`, `No`} | 可选 | 决定搜索结果首图；不传时 Walmart 自动选一个；跨 batch 各设一个 Yes 时行为未定义 | variant_groups_design.md:41,51,338-347（§3.4 现行策略：**一律不传**） |
| 变体属性值字段（`color` / `size` / `pieceCount` …） | 按 PT 内同名字段定义（string/integer/number，可能带 enum） | 被列入 variantAttributeNames 的属性必须真实存在 | 值须通过该字段自身 enum/类型校验 | 存放位置说明 variant_groups_design.md:66-81（§2.3）；逐属性 enum/类型校验实现 mapper.py:1381-1425 |
| `swatchImageUrl` | URL 数组（Visible.{PT} 图片字段） | 可选 | 有 minItems 约束；无 ≥minItems 张真实 URL 时**整字段不输出**，禁止占位符（Walmart 拒收） | mapper.py:16（SYSTEM_IMAGE_FIELDS）、mapper.py:56（4b 规则）；ERP-ALL 侧同款定义 /home/user/ERP-ALL/backend/src/erp/listing/spec.py:59 |

**per-PT enum 实例**（grep 自 spec 本体，摘录于 variant_groups_design.md:57-62 与 pt_spec.py:141-143）：

| Product Type | variantAttributeNames 允许值 |
|---|---|
| Skateboard Risers | `[assembledProductWidth, color, multipackQuantity]` |
| Baby Play Yards | `[ageGroup, assembledProductHeight, assembledProductWidth, color, count, countPerPack, finish, gender, material, multipackQuantity, ...]` |
| Power Hedge Trimmers | `[amps, bladeLength, engineDisplacement, multipackQuantity, volts]` |
| Microphone Splitters | `[color, multipackQuantity, numberOfChannels]` |

### 1.2 读路径：items OpenAPI（`walmart_official_specs/openapi/walmart-marketplace-items-openapi-original.yml`）

**响应体字段**（GET /v3/items 系列，两处重复定义）：

| 字段 | 类型 | 说明 | 行号 |
|---|---|---|---|
| `variantGroupId` | string | "Variant Id if the item is of type Variant" | yml:483-485、2015-2017、2964-2966 |
| `variantGroupInfo` | object | "Additional variant group information if the item is of type Variant" | yml:486-508、2018-2040 |
| `variantGroupInfo.isPrimary` | boolean | "Returns true if the item is a primary variant" | yml:489-491、2021-2023 |
| `variantGroupInfo.groupingAttributes` | schema 写 **object** {name, value}，但官方示例是**数组** `[{name: actual_color, value: ...}]` | 创建变体所用属性列表 | schema yml:492-504 vs 示例 yml:547-552、587-592 —— **本地 YAML 自相矛盾，属已知质量问题** |
| `variantGroupInfo.primary` | boolean | 与 isPrimary 并存的冗余字段（无描述） | yml:505-506、2037-2038 |
| `additionalAttributes.nameValueAttribute[].isVariant` | boolean | 属性袋级变体标记 | yml:1976-1977、2069-2070 |
| `additionalAttributes.nameValueAttribute[].variantResourceType` | string | 变体资源类型 | yml:1978-1979、2071-2072 |
| `...value[].isVariant` | boolean | 属性值级变体标记 | yml:1996-1997、2089-2090 |

**查询参数 / 可搜索字段**：

- `GET /v3/items?variantGroupId=` query 参数："Variant Id to retrieve all items with the same variant id"（yml:1584-1589）；官方示例操作名 `get_all_item_with_variantGroupId`（yml:3519-3520）
- Catalog Search 可搜索字段 `variantGroupId`（yml:101-102）

### 1.3 其它 feed 规格中的变体（阴性结论）

- **MP_ITEM_MATCH v4.2**（`walmart_official_specs/MP_ITEM_MATCH_v4.2.json`）：`grep -c -i variant` = **0**。match 模式无变体字段，`/home/user/erpAPI/match_listing/docs/match_listing_design.md:18` 明确："变体组：build 有 variantGroupId 逻辑；match **无**，继承沃尔玛目录的变体结构"
- **DELETE_ITEM**（`walmart_official_specs/DELETE_ITEM/5.0.20250919-16_45_47-api_DELETE_ITEM.json`）：0 处 variant
- **MP_MAINTENANCE**：本体不在环境（config.py:82-87 指向 424MB 文件），变体组维护能力未考古，variant_groups_design.md:24 记录"不改已有变体组（MP_MAINTENANCE 维护变体组功能，未来需要再做）"

---

## 2. Walmart_Marketplace_API_Guide.md 中的变体内容

**结论：中文指南没有变体专章。** 全文（含 variation/swimlane/主变体 等同义词扩展 grep）仅 2 处命中，均为查询侧参数：

| 行号 | 内容 |
|---|---|
| Walmart_Marketplace_API_Guide.md:547 | Catalog Search 可搜索字段表：`variantGroupId` \| 变体组 ID \| string（章节参考 URL 见 :559 `https://developer.walmart.com/us-marketplace/reference/getcatalogsearch`） |
| Walmart_Marketplace_API_Guide.md:582 | GET /v3/items 查询参数表：`variantGroupId` \| string \| 变体组 ID |

任务问的"同组要求 / 主变体 / 泳道图"**不在指南里**，而是沉淀在两份内部设计文档（标注"实测 + spec 取证"）：

### 2.1 同组要求（variant_groups_design.md）

- 同组所有 SKU 的 `variantGroupId` 必须完全相同（:49）
- 同组多个 SKU **不必在同一个 feed**，同 variantGroupId 即归组；但推荐同 feed 提交，避免短时 indexing 不一致；已上架组可后续追加新变体（:85-88，§2.4"实测/官方文档要点"）
- 同组应共用：`brand`、`productName` 主词（差异部分可作后缀）、`productType` **必须严格相同**、`keyFeatures` 大体一致；主图可不同（:90-97，§2.5）
- **变体组在 Walmart 是 per-seller**：跨店拆分会让详情页 swatch 残缺（variant_anchor_design.md:11；variant_groups_design.md:320）
- 巨型组防御：full_set ≥ 10 视为 DMIT 伪组不走变体路径（variant_anchor_design.md:48,55，env `ERP_MAX_VARIANT_GROUP_SIZE`）

### 2.2 主变体（variant_groups_design.md:338-347，§3.4）

- spec 标可选，不传时 Walmart 自动选一个（"通常按字母序或价格"）
- 现行策略：**所有变体一律不传 `isPrimaryVariant`**——跨 batch 补齐时若两个批次各设一个 Yes，"Walmart 行为未定义"

### 2.3 流程图（代替官方泳道图）

本地无官方泳道图；内部流程图见 variant_groups_design.md:369-400（§3.7 Phase 0→0.5→0.7 分组→1 UPC→2 注入→3 同店同 feed→4 回写）和 `/home/user/erpAPI/auto_listing/docs/listing_flow_full.md:91-123`（Phase 0.7 变体分组 / 0.8 维度重映射 / 2 变体注入 / 2.5 变体标题差异化）。ERP-ALL 侧规则已入账 `/home/user/ERP-ALL/specs/000-founding/business-rules-ledger.md:106,117,118`（BR-LST-001/012/013）。

---

## 3. PT 是否支持变体的标注：两个本地 JSON 均无此信息

- **`walmart_specs/all_product_types.json`**：结构为 `{total: 6942, unique: 6942, product_types: [PT名字符串...], category_map}`，无任何 per-PT 变体支持标注。全文仅 2 处 "variant" 命中（:21930、:36216），均为 PT 描述里的普通英文词（"Gore-Tex variants"、"some variants may not have a head"），**与变体支持无关**
- **`walmart_specs/taxonomy_v5.json`**：单行 minified 的 `itemTaxonomy` 树，节点仅含 `productTypeName` + `description` + department，无 variant 标注
- `pt_templates/` 下仅两个 xlsx 汇总（二进制，无法 grep），全量 `pt_templates_full.json`（307MB，pt_spec.py:42-43）不在本环境

**唯一权威判定口径**（现行代码即依此实现）：某 PT 支持变体 ⇔ MP_ITEM v5 spec 的 `Visible.{PT}.properties.variantAttributeNames` 存在且 `items.enum` 非空。`pt_spec.py:134-154` 的 `get_variant_attribute_enum(pt_name)` 即此逻辑，缺失时返回空 `frozenset()`，调用方降级单 SKU 路径（mapper.py:1370-1372；边界场景 variant_groups_design.md:615-618 §6.5，如 PT 只允许 `{multipackQuantity}` 时 color/material 维度全被剔除）。

---

## 4. 本地规格可能过时、需在线核实的点

| # | 疑点 | 本地证据 | 在线核实入口 |
|---|---|---|---|
| 1 | **spec 版本已到期风险**：本地 `5.0.20260304-22_45_32-api`（2026-04-16 官宣 Recommended），version_check 明言"下一季度 (2026 Q3，~7 月) 大概率再发一版"——今天是 2026-07-17，**正在预测窗口内** | walmart_spec_version_check.md:4-8,94 | `https://developer.walmart.com/us-marketplace/docs/item-spec-versioning-and-diff-reporting`（版本总览）；`https://developer.walmart.com/us-marketplace/page/whats-new`；spec JSON/diff xlsx 下载需登录 Developer Portal（version_check.md:83） |
| 2 | **变体字段恰是历次版本变更热区**：官方公告称 20260205/20260304 两版都是"改进上架、校验、**变体一致性**的 attribute + enum 更新"，per-PT `variantAttributeNames` enum 随版本增删 | walmart_spec_version_check.md:40-42,48 | 同上；完整字段级 diff 在 `MPITEM_FEED_TYPE_DIFF_2026-03-19.xlsx`（需登录，version_check.md:37,70） |
| 3 | **items OpenAPI 本地 YAML 已知过时/有错**：CLAUDE.md 明示"本地 YAML 可能过时…以在线官方文档为准"；实证：`groupingAttributes` schema 定义为 object（yml:492-504）而官方示例是数组（yml:547-552），且 `name` 字段 description 是复制粘贴错误（"Returns true if the item is a primary variant"，yml:497-498） | 见左 | `https://developer.walmart.com/us-marketplace/reference/getallitems`、`.../reference/getanitem`、`.../reference/getcatalogsearch`（URL 格式 `/reference/{operationId}`，CLAUDE.md 综合参考表） |
| 4 | **isPrimaryVariant 语义只有间接证据**："不传时自动选（通常按字母序或价格）"、"双 primary 行为未定义"均为设计文档推断，无官方文档行号背书 | variant_groups_design.md:343-345 | 官方变体指南（marketplacelearn.walmart.com 匿名可访问但老链接已改版，需从导航树进入，version_check.md:82）+ `/reference/{operationId}` 对应 item setup 页 |
| 5 | **同组提交时序规则是"实测/官方文档要点"而非 spec 硬约束**：同组不必同 feed、可后续追加——feed 校验行为可能随渠道侧调整 | variant_groups_design.md:85-88 | 官方 docs `https://developer.walmart.com/us-marketplace/docs/...`（item variations / bulk item setup 章节）+ A152 实测（D-Q37 验证纪律） |
| 6 | **swatch 字段形态不完整**：本地只见 `swatchImageUrl` 字段名（mapper.py:16）；v4 XML 时代为 `swatchImages{swatchVariantAttribute, swatchImageUrl}` 结构，v5 flat 后是否仍有 `swatchVariantAttribute` 本地无证据（`MPSetup_FeedDiff.xlsx` 为二进制未解析，spec 本体缺位） | 全仓 grep `swatchVariantAttribute` 零命中 | 拿到 MP_ITEM v5 本体后 grep，或查 Developer Portal item spec 下载页 |
| 7 | **CLAUDE.md 规格索引失效**：索引表指向的 `walmart_official_specs/MPSetup/` 目录不存在，后续 agent 会踩同一个坑 | CLAUDE.md 商品规格文件表 vs 磁盘现状（§0） | 修 CLAUDE.md 或补拆分文件，属 erpAPI owner 管辖 |

---

## 5. 关键文件索引（全部为绝对路径）

| 文件 | 角色 |
|---|---|
| `/home/user/erpAPI/auto_listing/docs/variant_groups_design.md` | 变体组设计（spec 取证 §2、字段表 :47-51、附录路径 :742-749） |
| `/home/user/erpAPI/auto_listing/docs/variant_anchor_design.md` | 跨店 anchor 锚定（per-seller :11、决策表 :44-55） |
| `/home/user/erpAPI/auto_listing/docs/listing_flow_full.md` | 全流程含变体 Phase 图（:31-51,91-123） |
| `/home/user/erpAPI/auto_listing/pt_spec.py` | spec 加载器 + `get_variant_attribute_enum`（:134-154） |
| `/home/user/erpAPI/auto_listing/mapper.py` | `full_variant_group_set`（:1307-1329）、`inject_variant_fields`（:1332-1437）、维度重映射（:1440+）、变体标题差异化（:1632+） |
| `/home/user/erpAPI/auto_listing/config.py` | spec 路径与版本常量（:71-88） |
| `/home/user/erpAPI/walmart_spec_version_check.md` | 版本时间线与升级建议（全文） |
| `/home/user/erpAPI/walmart_official_specs/openapi/walmart-marketplace-items-openapi-original.yml` | 读路径变体 schema（:483-508、:1584-1589、:1976-1997、:2015-2040） |
| `/home/user/ERP-ALL/specs/001-domain-model/03-catalog.md` | ERP-ALL 侧 variant_group/variant_member 实体（:35-50） |
| `/home/user/ERP-ALL/specs/000-founding/business-rules-ledger.md` | BR-LST-012/013 变体规则入账（:117-118） |

## 开放问题

- MP_ITEM v5 spec 本体（451MB）与 MPSetup_by_pt 拆分文件均不在本环境/仓库，ERP-ALL spec 构建器的变体字段权威来源如何供给：纳入版本管理拆分文件、生产机共享挂载、还是走 Get Spec API（pt_spec.py inject_live_spec 通道）在线拉取？需架构拍板
- 本地 spec 版本 5.0.20260304 已进入预测的 2026 Q3 换版窗口（version_check.md:94），且变体一致性正是历次版本变更热区——是否立即安排一次 Developer Portal 登录核实 + diff xlsx 下载，确认 per-PT variantAttributeNames enum 是否有增删？
- isPrimaryVariant 一律不传（Walmart 自动选主变体）是旧仓 auto_listing 的策略（variant_groups_design.md §3.4），ERP-ALL 变体组实现是否继承该策略并落笔 DECISION-FORM/business-rules-ledger？（D-Q2 只定了变体组进 MVP，主变体策略未见落笔）
- 巨型组阈值 ≥10（ERP_MAX_VARIANT_GROUP_SIZE）与跨店 anchor 非 ACTIVE 店严格拒绝（BR-LST-013）在 ERP-ALL 多租户模型下如何与 D-Q31 去重键 (team_id, asin) + 店铺豁免交互——anchor 是 per-team 还是全局？需 Owner 拍板
- erpAPI/CLAUDE.md 规格索引指向不存在的 walmart_official_specs/MPSetup/ 目录，是否授权 erpAPI owner 修正索引（属跨界改动，按角色制需提单）？