# R2-11 变体组考古综合（2026-07-17）

> 五路并行侦察（原始报告见本目录 raw-*.md）：图纸 / 采集与产品域现状 / spec 构建器扩展点 /
> 旧仓生产语义 / 渠道规格。本文是综合结论 + 增量拆分 + 拍板清单。

## 1. 修正 007 的现状论断（需批注回传审计侧）

007（README.md:96-97）称"代码零实现（含 product.variant_group_id 列亦未建）"——**后半句不成立**：
迁移 0007（2026-07-10）已把 `variant_group` / `variant_member` / `product.variant_group_id`
三者 DDL 完整落库（FK、TEAM_RLS、is_primary 部分唯一、授权齐全），与 001 §03 图纸对齐。
代码层（服务/端点/构建器/测试）确为零消费。**R2-11 范围收窄：不需建表迁移**，实际欠账 =
归组服务 + catalog 变体端点（openapi-v0.yaml:496-525 契约早已冻结）+ spec 构建器变体段 +
完整性守卫。

## 2. 已有底料（直接可用）

- **采集端素材已齐**（catalog-scrape 路）：workers parser 已解析 `parent_asin`、
  `variation_asins`（twister 变体矩阵，精确同族）、`variant_attributes`
  （"color_name=Red; size_name=L"），全部进 `product.attrs`，原始件留 `scrape_result.payload`，
  前端产品页已有展示标签。注意兜底语义：无父体时 parser 填 parent_asin=自身 ASIN
  （parser.py:1526-1531），归组必须排除 parent_asin==source_ref；旧回退路径 variation_asins
  为全页正则粗提（巨型伪组根因）——**只信任 twister 来源（variant_attributes 非空）**。
- **submit 管道天然支持一组多品**（spec-builder 路）：`_submit_tx1` 多 MPItem 同 feed 封套，
  一店一模式一 feed；listing:product 本就 N:1。变体段自然切口 = 维度值进 Visible 模板
  （进 build_hash 指纹）+ 组 ID/主变体标记走 `_instantiate` 注入。守卫落点 = `_submit_tx1`
  准入段组级整批判定 + `build_spec` 内兜底（fail-closed，照 ERP_SPEC_BUILD_FAILED 模式）。
- **旧仓有生产级实现与两份设计文档**（legacy 路）：`auto_listing/docs/variant_groups_design.md`
  + `variant_anchor_design.md`。关键语义：full_set = parent ∪ variation ∪ 自身；
  **variantGroupId 取 min(full_set)**（DMIT parent_asin 一律填自身，不能当组 ID）；Walmart 端
  无父体实体，纯靠同 variantGroupId + variantAttributeNames + 同名属性值归组。坑账：
  属性键是 snake_case（color_name=）、Art Sets 的 color 实为件数需重映射（硬编码表
  mapper.py:1452 + LLM 兜底，失败整组降级单 SKU）、巨型组阈值 ≥10、anchor 漂移。
- **渠道字段全集**（channel-spec 路）：写路径 Visible.{PT} 下 `variantGroupId`（string 1-300）、
  `variantAttributeNames`（array minItems 1，**enum 按 PT 而异，唯一判定口径 = per-PT spec**）、
  `isPrimaryVariant`（可选 Yes/No，旧仓策略不传=Walmart 自动选主）+ 同名属性值 + swatchImageUrl；
  读路径 variantGroupId + variantGroupInfo。本地 spec 版本 5.0.20260304 已入换版窗口，
  增量2 动工时在线核实 per-PT enum。

## 3. 图纸缺口与拍板清单

**P0（阻增量设计，需 Owner/审计侧拍板）**

1. **anchor 实体落点**：D-Q2 架构影响明文"variant_group / anchor 实体"（DECISION-FORM.md:10），
   BR-LST-012/013 跨店归 leader + anchor 锚定防漂移语义明确；但 001 §03 无 anchor 列，
   `is_primary`（主图/主价）≠ anchor（店铺锚定）。新模型中 anchor 由谁承载？
   开发侧建议：**variant_group 加 `anchor_store_id BIGINT NULL`**（首次上架时锁定；anchor 店
   暂停时成员上架拒绝并写明原因，不自动转移——保真 BR-LST-013），一次 0032 小迁移即可。
2. **variantGroupId 取值体系**：旧仓 min(full_set)（ASIN 系，渠道耦合）vs master_sku 体系
   （M{seq} 渠道中立，D1）。开发侧建议：**渠道中立派生 `VG{variant_group.id}`**——组 ID 是
   新系统自己的实体号，不依赖 Amazon ASIN 也不撞渠道既有值（Walmart 只要求组内一致的
   1-300 字符串）；source_parent_ref 仍存 parent ASIN 供溯源。
3. **组提交原子性**：007 验收"组员缺失时构建拒绝且可见原因"→ 开发侧按**整组拒绝**实现
   （一员失败整组不发，GTIN/配额不消耗），不做部分成功。请确认。

**P1（开发侧可自定，落笔注记即可）**

- broken 置位协议 v1：归组/重归组时判定（成员不齐=full_set 中有未入库成员？v1 从简：
  **主题冲突（组内 variation_theme 维度键不一致）或成员数<2 置 broken**；re-scrape 家族漂移
  时旧组置 broken + notification 人工处理，不自动拆组）；broken→active = 冲突消除后归组
  服务自动恢复。
- variation_theme→Walmart 属性名映射：**system_config**（铁律5），LLM 兜底重映射 v1 不移植
  （失败即组置 broken 可见原因，后续增量再评估）。
- match 模式豁免变体归组（旧仓 api_tasks.py:473 同款，D-Q3 参数包差异项）。
- isPrimaryVariant 不传（继承旧仓策略，Walmart 自动选主）——落台账批注。
- 巨型组护栏：组成员数上限走 system_config（旧仓 ≥10 经验值作默认），超限置 broken。

**批注回传审计侧**（不改 007 正文）：①上文 §1 现状论断更正；②06-listing 图纸零变体段落，
建议随本单"已落地"注记补齐；③variant_member 公共列缺省未注明 + uq 与 PK 同键冗余，建议勘误；
④erpAPI/CLAUDE.md 规格索引指向不存在的 MPSetup/ 目录（跨界，提单 erpAPI owner）。

## 4. 增量拆分

1. **增量1（归组闭环）**：归组服务（消费 product.attrs 的 twister 素材，建组/挂成员/双向
   同步 product.variant_group_id；排除自指 parent；只信 twister 源）+ 契约既有端点实现
   （建组/设成员，openapi-v0.yaml:496-525）+ broken 判定协议 v1 + beat `variant_group_sync`
   （或审核通过钩子，考古后定）+ db 测试。若 anchor 拍板加列 → 0032 迁移随本增量。
2. **增量2（构建器变体段 + 守卫）**：Visible 模板注入维度值（进 build_hash）+
   variantGroupId/variantAttributeNames 注入（_instantiate 层）+ 映射表 system_config +
   _submit_tx1 组级整批准入守卫（broken/缺员拒绝可见原因）+ listing_spec 缓存失效口径 +
   在线核实 per-PT variantAttributeNames enum + db 测试。
3. **增量3（L2 验收 + 收尾）**：A152 真实变体组（≥3 成员）上架 live（验收①）+ 缺员构建
   拒绝演练（验收②）+ specs 已落地注记 + 批注回传 + runbook + 工单回写。

## 5. 验收（007 原文）

一组真实变体（≥3 成员）A152 上架为 Walmart variant group 并 live；组员缺失时构建拒绝
且可见原因。
