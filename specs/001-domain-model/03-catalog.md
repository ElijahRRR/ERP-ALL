# 03 catalog — 产品主数据 / 变体 / 品牌占用 / GTIN 池 / 类目映射 / 货源 / SKU 映射

> 决策依据：D1（master_sku）、D-Q2（变体组进 MVP）、D-Q21（类目映射入库）、D-Q25（默认库存 5 + 有货源才上架）、D-Q31（去重键 (team_id, asin) + 店铺豁免）、D-Q39（UPC 生成器导入）、D-Q41（1688 货源等 API）。
> 调研警报：UPC 池余量 19%、随机生成冲突率 67% → GTIN 域按「EAN-13 生成器导入为主供给 + 水位告警」设计。

## product 产品主数据（500 万级）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| master_sku | TEXT | NOT NULL UNIQUE | **内部身份，终身不变**（D1；**D-Q72 修订：不再默认发往渠道**，渠道编码见 §06 `listing.channel_sku`）。生成式 **`'M' \|\| to_char(nextval('app.master_sku_seq'), 'FM000000000')`**（9 位零填充）。⚠️ **原 `lpad(...,7,'0')` 有截断缺陷**：PostgreSQL `lpad` 在串长超出时**从右截断**，序号达 1000 万时 `lpad('10000000',7,'0')='1000000'` 与第 100 万号撞号 → 唯一约束报错、产品入库整体停摆；`to_char` 超出时自动变长而非截断，故改用之 |
| team_id | BIGINT | NOT NULL | |
| source_channel | TEXT | NOT NULL DEFAULT 'amazon' | 采集源（多源扩展点，D-Q4） |
| source_ref | TEXT | NOT NULL | ASIN（或未来其他源主键） |
| title | TEXT | NOT NULL | |
| brand | TEXT | NULL | 原始品牌串 |
| brand_norm | TEXT | GENERATED (lower(btrim(brand))) STORED | 归一化，供黑名单/占用/商标检索 |
| category_path | TEXT | NULL | Amazon 类目全路径 |
| amazon_leaf_id | TEXT | NULL | → category_map 查 WPT |
| images | JSONB | NOT NULL DEFAULT '[]' | URL 数组（图片本体不入库） |
| attrs | JSONB | NOT NULL DEFAULT '{}' | 采集结构化属性（尺寸/材质/变体维度值…） |
| price_snapshot | JSONB | NULL | 采集时价格快照 {list, deal, ts} |
| status | TEXT | NOT NULL DEFAULT 'ingested' CHECK IN (ingested, auditing, audit_passed, audit_rejected, sourcing, ready, listed, retired) | 主生命周期；listed=至少一个在架 listing |
| latest_audit_run_id | BIGINT | NULL | 最近一次审核（不建 FK，指向分区表） |
| variant_group_id | BIGINT | NULL REFERENCES variant_group | |
| +公共列 | | | |

约束与索引：
- **去重键** `uq_product (team_id, source_channel, source_ref)`（D-Q31：团队内去重；跨团队各自独立）。
- `ix_product (team_id, status)`；`ix_product_brand_trgm (brand_norm gin_trgm_ops)`；`ix_product_leaf (amazon_leaf_id)`。
- 采集重复入库协议：`ON CONFLICT (team_id, source_channel, source_ref) DO UPDATE`（刷新 title/price_snapshot/attrs，**不重置 status**）。
- 状态推进：ingested→auditing→(audit_passed|audit_rejected)；audit_passed→sourcing→ready（有货源才 ready，D-Q25）；build 模式必须 ready 才可分配上架，match 模式跳过 sourcing 由策略配置（automation_policy）。
- 共享域：catalog 可被 shared_resource 只读共享（D-Q30）。

## variant_group / variant_member 变体组（D-Q2 进 MVP）

| variant_group 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | |
| source_parent_ref | TEXT | NULL | Amazon parent ASIN |
| variation_theme | TEXT | NULL | Size / Color / Size-Color…（Walmart variant 属性名由 spec 构建时映射） |
| status | TEXT | NOT NULL DEFAULT 'active' CHECK IN (active, broken) | broken=成员不齐/主题冲突，spec 构建拒绝 |
| +公共列 | | | |

| variant_member 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| group_id | BIGINT | NOT NULL REFERENCES variant_group | |
| product_id | BIGINT | NOT NULL UNIQUE REFERENCES product | 一品最多属一组 |
| variant_attrs | JSONB | NOT NULL DEFAULT '{}' | 该成员的维度取值 {size:"L", color:"Red"} |
| is_primary | BOOLEAN | NOT NULL DEFAULT false | 组主图/主价成员 |
| PK | | (group_id, product_id) | |

约束：`uq_variant_member (group_id, product_id)` + partial unique `(group_id) WHERE is_primary`。
唯一性：group 内 variant_attrs 组合唯一由服务层校验（JSONB 无法优雅建约束）。

已落地（R2-11 增量1/2，0032 + D-Q63）：
① variant_group 补列 `anchor_store_id BIGINT NULL REFERENCES store`——D-Q2 点名的 anchor 实体
落点（Owner 拍板 D-Q63①）：组首次提交入列时原子锁定（WHERE anchor IS NULL OR anchor=本店
RETURNING 判空，并发输家整组撤除）；anchor 店不匹配整组拒绝不自动转移（BR-LST-013 保真）；
首发即败解锁走 `POST /variant-groups/{id}/anchor/release`（R2-11 检修端点化，fail-closed：
锚定店有在途/在架成员拒绝；替代人工 SQL）。
② 自动归组（**D-Q64① 实时归组已落地**：采集入库 `product_upsert` 同事务 SAVEPOINT 内
`variant.sync_product` 单品即归、异常只记警不反噬入库；beat `variant_group_sync` 降为
兜底收敛——跨批解散重归/broken 复评/漏网扫描。维度键不在 `variant.theme_map` 时归组期
即发 warn 预警）：只信
twister 素材（variant_attributes 非空）、排除 parent_asin 自指、组身份=家族标识集连通分量
（增量2.5 full_set 语义，键存分量 min）；variation_theme 存排序维度键串
（如 color_name,size_name），Walmart variant 属性名由 spec 构建时经 system_config
`variant.theme_map` 映射。broken 判定 v2（D-Q64③ 仅真错误）：维度值缺失 / 组内维度键集
不一致——判定随归组/成员变更重跑，自动回 active；成员<2 与超上限不再 broken（超上限
`variant.max_group_size` 仅 oversize warn 观察；单批提交上限另由
`variant.max_batch_members` 保护，默认 200）。
③ 渠道组标识 = 渠道中立 `VG{variant_group.id}`（D-Q63②，同 D1 精神）；isPrimaryVariant
一律不传（渠道自动选主）——is_primary 仅承载主图/主价语义，不进 feed。
④ 勘误注记：uq_variant_member 与 PK 同键冗余（落库按 PK + product_id UNIQUE）；
variant_member 公共列缺省=team_id 经 group 推导（0007 实落无独立公共列）。

## brand_assignment 品牌店铺占用

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | |
| brand_norm | TEXT | NOT NULL | |
| brand_display | TEXT | NOT NULL | |
| store_id | BIGINT | NOT NULL REFERENCES store | |
| status | TEXT | NOT NULL DEFAULT 'occupied' CHECK IN (occupied, released) | |
| assigned_at | timestamptz | NOT NULL DEFAULT now() | |
| released_at | timestamptz | NULL | |
| release_reason | TEXT | NULL CHECK IN (suspension, manual, store_closed) | |
| incident_id | BIGINT | NULL REFERENCES store_incident | 封店释放回链（D-Q33） |
| +公共列 | | | |

约束：**`uq_brand_occupied (team_id, brand_norm) WHERE status='occupied'`** —— 一个品牌团队内同一时刻只占用一店（防跨店关联同品牌）；历史占用记录保留。
分配时机：build 上架分配店铺时自动 upsert（同店已占则通过、异店占用则拒绝并出 compliance_hit 类警示）；封店工作流批量 released。

## gtin_pool GTIN 池（UPC-A 12 + EAN-13 13，D-Q39）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | 默认团队隔离，可共享域（D-Q30） |
| gtin | TEXT | NOT NULL UNIQUE CHECK (gtin ~ '^[0-9]{12,13}$') | 全局唯一（跨团队也不允许重号） |
| gtin_kind | TEXT | NOT NULL CHECK IN (upc_a, ean_13) | |
| status | TEXT | NOT NULL DEFAULT 'free' CHECK IN (free, held, used, conflict, invalid) | conflict=渠道校验撞库（旧池 67% 教训） |
| source | TEXT | NOT NULL CHECK IN (generator_import, feishu_import, purchased) | 生成器本地生成后导入（D-Q39） |
| held_listing_id | BIGINT | NULL | 占用中的 listing（不建 FK） |
| held_at | timestamptz | NULL | |
| used_listing_id | BIGINT | NULL | 首次上架成功即终身绑定 |
| used_at | timestamptz | NULL | |
| last_check_at | timestamptz | NULL | 渠道校验时间 |
| check_result | JSONB | NULL | 校验详情 |
| import_job_id | BIGINT | NULL | → import_job（04） |
| +公共列 | | | |

索引：`ix_gtin_pool (team_id, gtin_kind, status)`（水位统计主查询）。
状态机与并发协议：
- 分配：`UPDATE gtin_pool SET status='held', held_listing_id=$1, held_at=now() WHERE id = (SELECT id … WHERE team_id=$t AND gtin_kind=$k AND status='free' LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING gtin` —— 单语句防双占。占号 kind 按优先序逐池尝试：`gtin.kind_preference`（team_config > system_config > 默认 `["upc_a","ean_13"]`，真实购入 UPC 优先）——不写死单一池（真机缺陷教训 2026-07-15）。
- held → used：listing 首次 published；held → free：上架终态失败释放；used **永不回收**（防跨店重用关联）。
- 水位告警：automation 域按 (team, kind) 统计 free 占比，阈值进 team_config（默认 <15% warn、<5% critical）→ notification——已落地（R2-04 beat `gtin_watermark`，阈值键 `gtin.warn_pct`/`gtin.critical_pct`，dedupe 24h）。

## category_map 类目映射（全局参考数据 refdata，D-Q21 + D-Q55 修订）

> **D-Q55 修订（2026-07-11，随 L1 主路径落地）**：旧设计（`amazon_leaf_id` 唯一 → 单
> `wpt` + `map_source` + `risk` 5 维 + `pt_embedding` 向量召回）被"**映射表多候选 + LLM
> 语义复排**（Owner 原方法，非嵌入）"取代。主路径只需 category_map 数据 + 现有 LLM，
> **不引入 embedding API**；`refdata.pt_embedding` 向量召回降为可选后置增强。实际落库为
> `refdata.category_map`（多候选）+ `refdata.pt_meta`（PT 元数据主表），迁移 0015/0016。

**refdata.category_map（键 (amazon_category, walmart_product_type)，一个 Amazon 类目可映射多个 WPT 候选）**

| 列 | 类型 | 说明 |
|---|---|---|
| amazon_category | TEXT | Amazon 类目路径/叶子（原文，L1 精确/前缀匹配，不归一） |
| walmart_product_type | TEXT | Walmart PT 候选；`'无对应Walmart PT'`=合法 unmapped 标记 |
| confidence | TEXT | 匹配置信度（高/中/低，自由文本） |
| requires_certificate | BOOLEAN | 该映射是否需证书 |
| zh_seller_forbidden | BOOLEAN | 中国搬运卖家是否禁做 |
| requirements / notes | TEXT | 自由文本 |
| amazon_leaf / browse_node_id | TEXT | 飞书映射明细补源列（溯源） |
| rank_no | INT | PT 内候选排名（源 `rank_in_pt`），供复排先验 |
| match_type | TEXT | 匹配方式（leaf_exact/path_prefix…） |
| source_batch | TEXT | 来源批次 |
| updated_at | timestamptz | dataset_revision('category_map') 触发器随写 bump |

**refdata.pt_meta（键 walmart_product_type，PT 元数据主表）**：walmart_category / walmart_ptg /
access_state / zh_can_do / zh_seller_forbidden / requirements / notes / total_fields /
required_count / required_fields。**L1 候选必须 INNER JOIN pt_meta 过滤废弃 PT**（源仓
2026-05-09 教训：category_map 里残留已下线 PT，直接用会映射到不可上架类目）。

- 导入源：uspto.amazon_walmart_category_map + 飞书映射明细/沃尔玛类目（导入器幂等 upsert，
  别名列宽容）。真数据已落库（2026-07-13）：category_map 15,987 + pt_meta 7,008。
- **L1 判定主路径**（001 §05 audit，R2-02 对拍 round-1 修正）：① category_map 直判命中
  （键=category_path/amazon_leaf_id × amazon_category/amazon_leaf/browse_node_id）→
  0 LLM 短路，带出 wpt；② 禁做类目（map/pt 任一维度 `zh_seller_forbidden`，候选全禁）
  命中 → **L1 拒**（唯一 L1 硬拒）；③ 未直判/无类目 → **软标记放行**（l1_unmapped，
  L2/L3 照跑）——类目缺图=数据缺口非合规异常，A4 fail-closed 只适用于检查本身失败；
  旧系统 parity：类目硬拒仅 R1/R2/R3，unmapped 不拦审核。缺 WPT 只阻上架（listing 前置）。
  ④ 缺图类目由 **L1-b 批量复排**补：祖先召回（INNER JOIN pt_meta 滤废弃）→ LLM 选唯一
  候选 → 写回 map（match_type=ai_rerank；非法/候选外/无候选 → 不写回，绝不写脏映射）。
  命中即 0 LLM（PRD §9）。

## refdata.pt_spec 官方 PT 规格（R3b 引入，R2-03 补列级图纸与 fields 全量列）

> 迁移 0019（审核子集列）+ 0020（fields 全量列）。全局参考数据（无 team_id），
> dataset_revision('pt_spec') 触发器版本失效；溯源走 import_job（无 synced_at，
> 有意区别于源表 walmart_pt_spec）。

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| walmart_product_type | TEXT | PK | 官方 PT 名；伪行 `'__orderable__'` 见下 |
| has_real_cert | BOOLEAN | NOT NULL DEFAULT false | 必填字段含"真认证"集合（NRTL/CPSIA/FDA/OTC/危险品，集合定义在 extract_mp_item_spec.py，逐字保真源仓 sync_pt_specs）→ R3b：整机 PT 硬拒 |
| real_cert_fields | JSONB | NULL | 命中的真认证必填字段名数组 |
| has_soft_cert / soft_cert_fields | BOOLEAN/JSONB | | 软合规（警告/披露类），提醒不硬拒 |
| total_fields / required_count | INT | NULL | 统计列（审核/展示用） |
| required_fields | JSONB | NULL | 必填字段名数组 |
| fields | JSONB | NULL（0020） | **per-PT 官方 MP_ITEM v5 原始 schema 节点原样**（properties/required/allOf 零损）。上架 spec 构建器/本地校验器/AI 属性填写的唯一规格源。不用旧审核库压缩版（enum 截断 10、丢 length/pattern/allOf——会假拒） |

- **伪行协议**：`walmart_product_type='__orderable__'` 存共享 Orderable 段 schema
  （全 PT 共用一份，~25KB）。不与真实 PT 名冲突；`INNER JOIN pt_meta` 类消费方天然滤掉。
- **数据生产**（部署机）：`tools/extract_mp_item_spec.py`（输入=T7 MPSetup monolith 流式
  或 `POST /v3/items/spec` live 响应）→ jsonl → `tools/import_pt_spec.py --chunk-size 200`。
  fields 列 COALESCE 语义：行内缺省不清库中已有值（审核子集与上架全量两代数据可交替重导）。
- 消费方：R3b 认证 gate（audit）、上架 spec 构建器 + 本地校验器 + AI 属性填写（R2-03）。

## product_source 货源记录（占位设计，D-Q41）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | |
| product_id | BIGINT | NOT NULL REFERENCES product | |
| source_platform | TEXT | NOT NULL DEFAULT '1688' | |
| source_url | TEXT | NULL | |
| source_price_cny | NUMERIC(12,2) | NULL | 拿货价 |
| freight_cny | NUMERIC(12,2) | NULL | 运费 |
| total_cost_cny | NUMERIC(12,2) | NULL | 含运费成本 |
| risk_note | TEXT | NULL | 自由文本（源字段=沃尔玛WFS选品.xlsx） |
| status | TEXT | NOT NULL DEFAULT 'candidate' CHECK IN (candidate, confirmed, invalid) | confirmed 驱动 product.status→ready |
| found_by / found_at | | | |
| +公共列 | | | |

索引：`(product_id, status)`。
**占位声明**：1688 API 接入（R3+）后本表将扩列（供应商 ID/SKU 映射/起订量/时效），当前只承载「有货源才上架」闸门（D-Q25）与人工录入；旧 Excel 不导入（D-Q41 不可信）。

## sku_mapping 渠道 SKU 映射（存量桥）—— **R2-16 落地对象（2026-07-30 转正）**

> **实现现状**：本表在 `backend/alembic/versions/` 与 `backend/src/erp/` 中仍**零命中**，
> 但**已有落地工单**：R2-16 存量接管（后台在线产品补挂）以本表为核心表。
>
> **转正说明（撤回 2026-07-29 那句）**：昨日曾写「本表保留为设计意图，**不作为任何工单的
> 验收对象**」——那是为 R2-15 判据⑥（误验图纸表）写的止损，**现已过期并撤回**。次日 Owner
> 追问「沃尔玛在线产品与本地产品库怎么对应、**有可能亚马逊端还没入库而沃尔玛端先进来**」
> 时才发现：本表原文用途正是「从 Walmart 重拉在线商品时（D-Q35）批量生成 legacy 行」，
> 且 `product_id` 明写**可空——「重拉的历史在线品可能未入产品库」**，恰是该场景的设计。
>
> **本表与 `listing` 的分工**（它存在的理由）：
>
> | | 承载什么 | `product_id` |
> |---|---|---|
> | `listing` | **我们上架的东西** | `NOT NULL`——没有产品就不该有上架单 |
> | `sku_mapping` | **渠道上存在这个 SKU** | **NULL 可**——渠道上有，本地未必有 |
>
> 故「后台有本地无」不需要单独的工作队列表，它就是 `WHERE product_id IS NULL` 这个查询。
> 建第二张表会让「渠道 SKU 的存在性」有两个真相。
>
> **订单回连仍不经本表**：现役实现是 `order/pull.py` 以 `(store_id, channel_sku)` 直查
> `listing`。**该路径的失败是静默的**（子查询落空返回 NULL 而 INSERT 仍成功，订单失去产品
> 关联，下游仅由订单四检 `price_limit` 以 `source_missing` 间接暴露）——R2-15 判据⑦已要求
> 显式信号。**涉及订单回连的判据一律仍按现役 listing 直查路径书写**，不因本表转正而改。

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | **显式冗余列（2026-07-30 补）**。原写「team_id 经 store」，但全仓 RLS 策略一律是 `team_id = app.current_team() OR app.is_super()` **长在表自己身上**（`0009_listing.py:23-31` 模板），无此列则套不上。与 `listing`/`maintenance_task` 同形 |
| store_id | BIGINT | NOT NULL REFERENCES store | |
| channel_sku | TEXT | NOT NULL | 渠道侧 SKU |
| product_id | BIGINT | NULL REFERENCES product | **可空是本表的核心性质**：重拉的历史在线品可能未入产品库（渠道先于本地） |
| origin | TEXT | NOT NULL CHECK IN (legacy, new) | legacy=存量（重拉在线品）；new=本系统上架。**D-Q72 后渠道侧统一为 ASIN 系**（`source_channel='amazon'` 时 `channel_sku`=ASIN，同店冲突加 `-N` 后缀），二者不再是两套编码体系，本列仅表来源不表编码规则 |
| link_state | TEXT | NOT NULL DEFAULT 'pending' CHECK IN (pending, adopting, linked, source_dead, unresolvable, ignored) | **R2-16 补**。对应关系阶梯的落点：pending=待处理；adopting=采集在途；linked=已关联产品；source_dead=形如 ASIN 但亚马逊侧采不回（级 3）；unresolvable=不形如 ASIN（级 4）；ignored=人工判定不接管 |
| first_seen_at / last_seen_at | timestamptz | NOT NULL | **R2-16 补**。首次发现 / 最近一轮仍在渠道侧——连续多轮不再出现即可判定渠道侧已消失 |
| published_status / lifecycle | TEXT | NULL | **R2-16 补**。渠道侧状态原值 |
| wpid / gtin / title / price | | NULL | **R2-16 补**。渠道侧原始信息。⚠️ **`item_pull` 现在把这些全丢了**——`remote` 字典只留 `published_status`/`lifecycle`/`reasons`，16a 须扩采 |
| +公共列 | | | |

约束：`uq_sku_mapping (store_id, channel_sku)`。
用途：订单行/结算明细回连产品；从 Walmart 重拉在线商品时（D-Q35）批量生成 legacy 行。

**对应关系阶梯**（R2-16 核心设计，详见 007 同名节）：级 0 无条件 upsert 本表（**永不失败、
不依赖亚马逊侧**）→ 级 1 本地已有该 ASIN 的产品则直接回填 `product_id`（**零采集**）→
级 2 走 scrape 建产品 → 级 3 亚马逊侧已死则建 `source_channel='walmart'` 占位产品
（**不能就此丢弃：还在 Walmart 卖但没货源的品，正是最该被发现的**）→ 级 4 不形如 ASIN 同级 3。
