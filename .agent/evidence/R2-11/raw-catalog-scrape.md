# R2-11 考古原始报告：catalog-scrape

## 摘要

007 计划中 R2-11 的现状论断半对半错：后端 src/ 代码层确为零实现（无任何 variant 服务/路由/测试），但"product.variant_group_id 列亦未建"不成立——迁移 0007（2026-07-10）已把 product.variant_group_id、variant_group、variant_member 三者 DDL 完整落库，含 FK、RLS、is_primary 部分唯一索引和授权，与 specs/001 §03 图纸对齐。采集端数据基础意外充足：workers parser 已解析 parent_asin、variation_asins（twister 变体矩阵精确同族）和 variant_attributes（"color_name=Red; size_name=L"），经 payload 适配器全部进入 product.attrs 并留存 scrape_result.payload 原始件，前端产品页甚至已有展示标签。R2-11 实际欠账是归组服务逻辑、catalog 变体端点、spec 构建器变体段和完整性守卫，不需要新建表迁移。alembic 最新 revision 为 0031，下一号从 0032 开始。

# R2-11 变体组考古报告（采集/产品域现状盘点）

## 1. 核实 007 论断："代码零实现（含 product.variant_group_id 列亦未建）"

007 原文位置：`/home/user/ERP-ALL/specs/007-mvp-completion-plan/README.md:96-97`（"§03 variant_group/variant_member 列级图纸完备，代码零实现（含 product.variant_group_id 列亦未建）"）。

**结论：前半句成立，括号内不成立。**

### 1a. DDL 层：三件套已全部建好（007 说法过期）

迁移 `/home/user/ERP-ALL/backend/alembic/versions/0007_scrape_catalog.py`（Create Date 2026-07-10，README.md 的论断早于或未同步此迁移）：

| 对象 | 位置 |
|---|---|
| `product.variant_group_id bigint` 列 | 0007_scrape_catalog.py:70 |
| `app.variant_group` 表（id/team_id/**source_parent_ref**/variation_theme/status active|broken/审计列） | 0007_scrape_catalog.py:87-98 |
| `app.variant_member` 表（group_id/product_id UNIQUE/variant_attrs jsonb/is_primary，PK=(group_id,product_id)） | 0007_scrape_catalog.py:99-105 |
| 每组唯一主成员部分索引 `uq_variant_primary ... WHERE is_primary` | 0007_scrape_catalog.py:106-107 |
| FK `fk_product_variant_group` | 0007_scrape_catalog.py:108-109 |
| variant_member RLS（经 group JOIN team 校验） | 0007_scrape_catalog.py:110-115 |
| variant_group touch 触发器 + 标准 team RLS | 0007_scrape_catalog.py:118-119 |
| erp_app 授权（含 variant_group/variant_member） | 0007_scrape_catalog.py:260-261 |

与图纸 `/home/user/ERP-ALL/specs/001-domain-model/03-catalog.md:35-55` 逐列对齐（source_parent_ref、variation_theme、status broken、is_primary 部分唯一都在）；图纸 :54 的 `uq_variant_member (group_id, product_id)` 在 0007 用复合主键实现（0007_scrape_catalog.py:104，等效）。后续迁移对 product 无任何 ADD COLUMN（全量 grep 无命中）；仅 0012 改过 status 约束（`0012_product_needs_review.py:26-27`）。

### 1b. 代码层：确实零实现

`grep -rn "variant|parent_asin|source_parent_ref" backend/src/`（含大小写不敏感 variation）实质命中仅一处，且与变体组无关：
- `/home/user/ERP-ALL/backend/src/erp/listing/attr_fill.py:38` `_AMAZON_LIST_LIMITS = {"variation_asins": 20, ...}`——只是喂 LLM 前把 attrs 里的 variation_asins 截到 20 个（应用点 attr_fill.py:215），不做归组。
- 其余命中皆为 `Path(__file__).parent` 之类无关行。

零实现具体指：无归组服务、catalog 无 variant 端点（见 §2）、`ProductOut` 不暴露 variant_group_id（`backend/src/erp/catalog/router.py:19-28`）、spec 构建器无变体段、`backend/tests/` 全目录 grep "variant" 零命中（无测试）。

## 2. product 表现有列全集 + catalog 域结构

**product 列全集**（唯一 DDL 来源 `0007_scrape_catalog.py:50-75`）：id、master_sku（`'M'||lpad(seq,7)` 默认，:52-53）、team_id、source_channel（默认 'amazon'）、source_ref、title、brand、brand_norm（generated）、category_path、amazon_leaf_id、images jsonb、attrs jsonb、price_snapshot jsonb、status（8 态 CHECK，:65-68）、latest_audit_run_id、**variant_group_id**、created_at/updated_at/created_by；去重键 `uq_product(team_id, source_channel, source_ref)`（:74，D-Q31）。索引：ix_product(team_id,status) / brand trgm / amazon_leaf_id（:76-78）。

**catalog 域结构**（`/home/user/ERP-ALL/backend/src/erp/catalog/`，仅 2 文件）：
- `__init__.py` — 空包标记。
- `router.py` — R1-12 最小只读路由（router.py:1 自述"完整 Catalog 段随 R2"）：`GET /products` 分页列表（:31-62）+ `GET /products/{id}` 详情（:65-88）。无写端点、无变体端点。

**scrape 域结构**（`/home/user/ERP-ALL/backend/src/erp/scrape/`）：
- `router.py` — 两组路由：scrape_router 作业管理（POST /scrape-jobs :67、列表 :94、详情 :128、cancel :162、GET /worker-nodes :176）+ worker_router `/worker/v1` 拨入协议（register :232、sync :245、tasks/pull :259、tasks/release :269、tasks/result :278）。
- `service.py` — 作业/任务生命周期 + 租约协议（attempt 兼 lease_epoch）+ 回收 + **product 入库**（自述 :1-16，移植自 amazon-scraper-v3）。

## 3. 采集端 → product 的完整链路与变体数据可用性

### 3a. 字段映射链（三段）

1. **worker 解析**（`/home/user/ERP-ALL/workers/src/erp_worker/parser.py`）：两条页面解析路径都产出变体三字段（:191-195 与 :896-900）——
   - `parent_asin`：正则 `"parentAsin":"(\w+)"`，**取不到时兜底=自身 ASIN**（:1526-1531；_default_result :1332 同）；
   - `variation_asins`：优先 twister 变体矩阵 `dimensionValuesDisplayData` 精确同族（`_parse_twister` :1580-1619+，单次请求即含全家族），twister 不可用回退全页 asin 正则粗提（:1533-1539，噪声大）；
   - `variant_attributes`：本 ASIN 自己的维度取值字符串 `"color_name=Red; size_name=L"`（:1583-1585），维度名有序列表来自 `dimensions`/`dimensionsDisplay` 键（:1598-1612）——**variation_theme 可由此推导**。
2. **payload 适配**（`/home/user/ERP-ALL/workers/src/erp_worker/payload.py:61-94`）：结构化五要素抽顶层；其余字段（含 parent_asin、variant_attributes）原样进 `attrs{}`（:64-69）；`variation_asins` 逗号串拆成列表覆盖（:75-78）。测试佐证 `/home/user/ERP-ALL/workers/tests/test_payload.py:65`。
3. **backend 入库**（`/home/user/ERP-ALL/backend/src/erp/scrape/service.py`）：
   - 原始 payload 全量落 `app.scrape_result.payload jsonb`（写入 :353-367；表 DDL 0007:214-232，月分区 append-only，另有 payload_ref 外置指针）；
   - `job_kind='product_detail'` 且成功时调 `product_upsert`（:379-386）；
   - `product_upsert`（:505-545）：title/brand/category_path/amazon_leaf_id/images/attrs/price_snapshot 写入 `app.product`（INSERT :519-522），冲突时 **只刷新 title/price_snapshot/attrs、不重置 status**（:523-526，D-Q31）。

### 3b. 变体数据现状结论

**parent ASIN / variation 信息已经在库里可用**：每个采集入库的 product，`attrs['parent_asin']`（字符串）、`attrs['variation_asins']`（列表，twister 来源时精确）、`attrs['variant_attributes']`（维度串，可直接解析为 variant_member.variant_attrs jsonb）三件齐备；原始件在 scrape_result.payload 可回溯。前端产品页已有这两字段的展示标签（`/home/user/ERP-ALL/frontend/src/pages/ProductsPage.tsx:107-108`）。契约侧 `specs/002-api-contract/openapi-v0.yaml:1326,1590` 已含 variation_theme 字段（前端 schema.d.ts:3715 已生成）——契约先行，端点未建。

注意两个语义坑：parser 无父体时 parent_asin 兜底=自身 ASIN（归组逻辑必须把 `parent_asin == source_ref` 判为单品，否则人人自成一组）；旧回退路径的 variation_asins 是全页正则粗提，归组前应校验（如仅信任 twister 来源或用 parent_asin 相等约束）。

另有旁路写 product：`/home/user/ERP-ALL/backend/src/erp/tools/audit_replay.py:76-85`（审计回放工具 upsert，attrs 仅 seller_id/description/bullets，不含变体字段——R2-11 不需理会但归组任务需容忍此类无 parent_asin 的行）。

## 4. alembic 最新 revision

`/home/user/ERP-ALL/backend/alembic/versions/` 顺序编号 0001-0031，最新 `0031_refund_request.py`（revision="0031"，down_revision="0030"，Create Date 2026-07-17）。**下一号从 0032 开始。**

## 5. R2-11 实际欠账清单（相对 007 三条计划）

| 007 计划项 | 现状 |
|---|---|
| 建表迁移 | **不需要**——0007 已建齐（007 此项可划掉）；仅可能补服务层校验相关索引 |
| 采集端 parent ASIN 归组 | 零实现，但输入数据齐备（attrs 三字段），只欠归组服务（source_parent_ref 列已留 0007:90） |
| spec 构建器变体段（variation_theme → Walmart variant 属性映射，007 README.md:100） | 零实现（listing/ 下无 variant 命中） |
| 组完整性守卫（status=broken 拒绝构建，007 README.md:101） | 零实现（图纸 03-catalog.md:42-55 已定义） |
| 另欠：catalog 变体查询端点 + ProductOut 补 variant_group_id + 测试 | 契约 openapi-v0.yaml:1326 已留 variation_theme，端点/测试全无 |

## 开放问题

- 归组执行位置：在 product_upsert 事务内联归组（简单但每次回传都要锁 variant_group），还是独立 beat 任务批量扫 attrs.parent_asin 归组（backend/src/erp/scrape/service.py:505 vs erp/beat.py 模式）？需架构拍板。
- 归组键与兜底语义：parser 无父体时 parent_asin=自身 ASIN（workers/src/erp_worker/parser.py:1526-1531），归组必须排除 parent_asin==source_ref；且旧回退路径 variation_asins 为全页正则粗提（parser.py:1533-1539）——是否只信任 twister 来源（variant_attributes 非空）作为归组依据？
- variation_theme 映射表（color_name/size_name → Walmart variant 属性名）放配置中心还是 spec 构建器常量？铁律 5 禁止写死业务参数，建议 system_config，需 Owner 确认。
- 家族漂移处理：product_upsert 冲突时会刷新 attrs（service.py:523-526），re-scrape 后 parent_asin 变化（家族拆分/重组）时旧组如何处置——自动置 status=broken 还是仅告警人工处理？图纸只定义了 broken 拒绝构建，未定义进入 broken 的触发器。
- 007 计划文档更正：specs/007-mvp-completion-plan/README.md:96-97 的"product.variant_group_id 列亦未建"与 0007 迁移事实不符，按铁律 1 需经 Owner 批准更正文档（R2-11 范围从"建表+归组"收窄为"归组+构建器+守卫"）。