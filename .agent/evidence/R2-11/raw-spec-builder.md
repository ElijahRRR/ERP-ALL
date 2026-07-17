# R2-11 考古原始报告：spec-builder

## 摘要

R2-03 spec 构建器（backend/src/erp/listing/spec.py）分为 WPT 解析→模板构建（LLM 填充打底+系统字段覆盖+零认证覆盖）→coerce 清洗链→listing_spec 缓存→_instantiate 参数注入→本地校验六段，变体段的自然切口是"维度值进 Visible 模板（进 build_hash 指纹）+ 组 ID/主变体标记走 _instantiate 注入"。submit 管道（service.py _submit_tx1）把多个 MPItem 打进同一 feed 封套，一店一模式一 feed，天然支持"一组多 listing"——DB 层 listing:product 本就是 N:1（无 product 唯一约束，去重是 allocate 层业务规则）。组完整性守卫无现成落点，最近似模式是 _resolve_wpt/_match_identifier 的 fail-closed（raise ERP_SPEC_BUILD_FAILED），建议落 _submit_tx1 准入段做组级整批判定 + build_spec 内兜底；variant_group/variant_member 表在 0007 迁移已建好（含 broken 状态），代码层零消费。测试照抄 test_spec_v5.py 的 psycopg 播种 pt_spec fixture + 直调 build_spec 模式即可，渠道/LLM 分别有 MockTransport 注入点。

# R2-11 变体组 · spec 构建器扩展点考古报告

基准目录 `/home/user/ERP-ALL/backend/`（下文相对路径均以此为根）。工单口径：`.agent/review_list.json:571-581`（R2-11：variant_group/variant_member 建表 + 采集 parent ASIN 归组 + **spec 构建器变体段（R2-03 扩展）** + **组完整性守卫（broken 拒构建）**；验收=一组 ≥3 成员在 A152 上架为 Walmart variant group 并 live）。

---

## 1. R2-03 spec 构建器现状

### 1.1 模块地图（src/erp/listing/）

| 文件 | 行数 | 职责 |
|---|---|---|
| `spec.py` | 513 | 构建器主体：build_spec / feed_header / _instantiate |
| `attr_fill.py` | 457 | AI 属性填写（R2-03 增量 3），写回 `product.attrs['walmart_fill'][wpt]` |
| `coerce.py` | 867 | 提交前清洗/coerce 管线（增量 4，源仓 mapper.py 保真移植） |
| `validator.py` | 195 | 提交前本地校验器（增量 4）：官方 spec 层 errors + 实践规则层 warnings |
| `wpt_schema.py` | 142 | `refdata.pt_spec.fields` 规格访问层（PtSchema/FieldSpec） |
| `service.py` | 1170 | listing 生命周期：allocate/submit/poll/verify-back/delist，状态机唯一模块 |
| `gtin.py` / `router.py` | | GTIN 池 / API 路由 |

### 1.2 构建流程分段（`build_spec`，src/erp/listing/spec.py:331-467）

1. **配置与版本**：`_cfg` 读 system_config 逐键覆盖默认（spec.py:110-125）；header/orderable 默认在 spec.py:39-51；`pt_spec_revision` 取规格数据集版本（wpt_schema.py:135-142）。
2. **WPT 解析**（build 模式）：`_resolve_wpt`（spec.py:148-173），链 = `product.attrs.wpt` 显式 > audit `run_l1` 直判（与审核同一可售语义，硬拒 fail-closed raise `ERP_SPEC_BUILD_FAILED`，spec.py:154-159）> system_config `listing.default_wpt`；全空 fail-closed（spec.py:169-173）。match 模式跳过 WPT（spec.py:353-355）。
3. **指纹与缓存命中**：fingerprint = title/brand/images/attrs/wpt/mode/spec_version/odefaults/**pt_spec_revision** 的 JSON → sha256 `build_hash`（spec.py:360-376）；命中 `app.listing_spec (product_id, offer_mode, build_hash)` 直接 `_instantiate` 复用模板（spec.py:389-411）。**SKU/GTIN/价格/库存/日期/PartnerID 是 listing/store 级参数，经 _instantiate 注入不进缓存键**（spec.py:21-23 头注）。
4. **模板构建**（未命中时）：
   - Visible：`_build_visible`（spec.py:211-234）——**LLM 填充打底 → 系统字段覆盖 → 零认证覆盖压轴**。LLM 产物从 `attrs['walmart_fill'][wpt]` 取（`_llm_fill`，spec.py:204-208）；系统后处理字段（文案/品牌/图片）定义在 spec.py:57-72（`SYSTEM_VISIBLE_POSTPROCESS_FIELDS` 含 **swatchImageUrl**，spec.py:59）；零认证覆盖 `_zero_cert_overrides`（spec.py:176-201，BR-AUD-006：只对该 PT spec 存在的字段强制 + enum 安全序列回退 + 清文档字段）。
   - Orderable：`_build_orderable_template`（spec.py:237-279）——合并序：模板默认 < LLM 填充（非后处理字段）< 系统强制占位符（`{SKU}`/`{GTIN}`/`{PRICE}` 等，spec.py:263-278）。
5. **coerce 清洗链**：`coerce.run_coerce_chain`（spec.py:421-428 调用；coerce.py:818-867 实现）。链序（coerce.py:7-14 头注）：force_amazon_copy → fill_known_walmart_required → fill_missing_required → fix_type_mismatches → fix_invalid_enums → fix_date_formats → strip_unknown_fields → clean_state_restrictions → drop_empty_optional_fields → drop_min_items_violations → enforce_copy_limits → round_decimals_to_2。每步对应一类实测渠道拒绝码。
6. **落缓存**：INSERT `app.listing_spec`，payload=单个 MPItem 模板元素（`{Orderable, Visible:{wpt:...}}`），cert_overrides 同存（spec.py:431-453）。
7. **实例化**：`_instantiate`（spec.py:470-513）——注入 sku/productIdentifiers/price/startDate/endDate（ISO DateTime 带毫秒 .000Z，BR-RET-007）/inventory=[{quantity, fulfillmentCenterID=partner_id}]。
8. **本地校验**：`_validated` 闭包（spec.py:381-387）在**实例化后的 item** 上跑 `validator.validate_build_item` / `validate_match_item`，结果随返回值 `validation:{ok,errors,warnings}` 带出。

### 1.3 属性来源双通道

- **AI 填写**（增量 3）：`attr_fill.fill_product_attrs`（attr_fill.py:371-457），三段纪律 tx1 组 prompt+查缓存 → 无事务 HTTP → tx2 记账+coerce+写回；产物落 `product.attrs['walmart_fill'][wpt]`（`_WRITE_FILL_SQL`，attr_fill.py:343-350）；fail-closed（非 JSON/形状错不写回，attr_fill.py:293-311）。系统后处理字段不喂 LLM 也不采纳（prompt 规则 1a，attr_fill.py:64）。
- **规格导入**：`refdata.pt_spec.fields`（0020 无损列）经 `wpt_schema.load_pt_schema`（wpt_schema.py:114-128）取 per-PT 原始 schema 节点；共享 Orderable 段在伪行 `__orderable__`（wpt_schema.py:18, 131-132）。规格数据任何写入 bump `dataset_revision('pt_spec')` → build_hash 变 → spec 缓存自动失效（wpt_schema.py:7-8、spec.py:371）。

### 1.4 本地校验器怎么接（注意：是增量 4，非增量 3）

任务描述中"R2-03 增量3 的本地校验器"与任务台账不符：**增量 3 = AI 属性填写（attr_fill.py），增量 4 = 提交前本地校验器（validator.py）**（validator.py:1 头注"R2-03 增量 4"；coerce.py:1 同）。校验器两处消费：

- **构建时**：`build_spec._validated`（spec.py:381-387）→ `validate_build_item`（validator.py:125-162：`_check_section` 对 Visible/Orderable 各跑必填/类型/枚举/maxLength/minItems/日期格式，validator.py:73-122；外加实践规则层——fulfillmentCenterID 缺失警告 validator.py:146-153、startDate/endDate ISO DateTime 强校验 validator.py:154-160、正价校验 `_check_positive_price` validator.py:165-174；**模板占位符未实例化直接报 error** validator.py:86-89）。
- **提交闸门**：`service._submit_tx1` 消费 `validation["ok"]`——errors 非空 → 返还配额 + listing 迁 failed（`ERP_SPEC_INVALID`）+ 跳过，**不许出门吃渠道拒**（service.py:426-434，省 MP_ITEM 10/hour 配额）。

### 1.5 变体段应切在哪（核心结论）

Walmart v5 的变体字段（`variantGroupId` / `variantAttributeNames` / `isPrimaryVariant` / `swatchImageUrl`）是 **Visible 段的 per-PT 属性**（erpAPI 参考：`/home/user/erpAPI/Walmart_Marketplace_API_Guide.md:547,582` 确认 variantGroupId 为渠道一等字段；旧仓 `pt_spec.get_variant_attribute_enum(pt)` 提供 per-PT 变体维度枚举，见 `.agent/evidence/R2-03/archaeology.md:616`）。切点分两层：

1. **模板层（进缓存指纹）**：成员自身的**维度取值**（`variant_member.variant_attrs`，如 {size:"L", color:"Red"}）是产品固有属性 → 应并入 `_build_visible` 产出的 Visible 模板，并把 variant_attrs（或其来源）纳入 fingerprint（spec.py:360-375）——归组/改维度自动失效缓存。天然挂点：`_build_visible`（spec.py:211-234）在零认证覆盖之前插一段"变体维度映射"（variation_theme → Walmart variant 属性名；旧仓有硬编码 remap + LLM 兜底两级，mapper.py:1492/1548，见 archaeology.md:70）。
2. **实例化层（不进缓存键）**：`variantGroupId`（组内一致的组标识）与 `isPrimaryVariant`（组内唯一主变体）是**组/listing 级参数**，与 sku/gtin/price 同类 → 走 `build_spec` keyword-only 形参（spec.py:331-342 处加 `variant_group: dict | None = None` 之类）透传 `_instantiate`（spec.py:470-513）注入。`swatchImageUrl` 已在 `SYSTEM_IMAGE_FIELDS`（spec.py:59）被划为系统后处理字段——现成钩子，只需在 `_build_visible` 图片段（spec.py:225-229）按 variant_attrs 写入。
3. **校验层**：单 item 校验在 `validate_build_item` 加变体字段检查即可；但**组内一致性（PT 一致/维度键一致/主变体唯一）是跨 item 校验**，validator.py 的单 item 契约装不下，须新增组级校验函数（见 §3）。
4. **match 模式无变体段**：MP_ITEM_MATCH v4.2 仅 5 个 offer 字段（spec.py:318-328、validator.py:54），变体段仅 build 模式适用——与 D-Q3"差异由 offer_mode 参数包承载"一致（specs/000-founding/DECISION-FORM.md:72）。

---

## 2. submit 管道与"一组多 listing"

### 2.1 提交管道怎么消费构建产物

`service.submit`（service.py:308-336）→ RS-03b 三段式：`_submit_tx1`（service.py:339-495）完成准入/配额/spec/组 feed/落 outbox 后 COMMIT → HTTP 零事务 → `_apply_feed_submit` tx2 归位（service.py:516-607）。关键消费链：

- 逐 listing 载 product → `spec_builder.build_spec(...)`（service.py:408-419），BusinessError → 返还配额+迁 failed（service.py:420-425）；validation errors → 同上（service.py:426-434）。
- 通过者 `items_payload.append(built["item"])`（service.py:436）→ 封套 `{"MPItemFeedHeader": feed_header(), "MPItem": items_payload}`（service.py:441-444）→ `sanitize_feed_numbers` 最后一道小数位兜底（service.py:446；coerce.py:797-815）→ 落 `app.feed`/`app.feed_item` + listing 迁 queued（service.py:450-468）→ outbox 命令 `POST /v3/feeds?feedType=MP_ITEM`（service.py:475-491）。
- **一 feed 一店一模式**：批内混店/混模式项被 `FEED_MIXED_BATCH` 跳过（service.py:361-365）。
- 回写：`poll_feed` item 级权威回写（headline 不可信），SUCCESS → published→live + GTIN mark_used；error → 返还配额+释放 GTIN+failed（service.py:699-756）。

### 2.2 listing 与 product：现状是 1:N（可多）

- DDL：`app.listing` 唯一约束仅 `uq_listing UNIQUE (store_id, channel_sku)`（alembic/versions/0009_listing.py:104）；`product_id` 无唯一约束（0009_listing.py:80），索引 `ix_listing_product (product_id, store_id)`（0009_listing.py:108）。**同一 product 可有多条 listing（跨店），DB 层不阻**。
- 团队内"一品一在架"是 **allocate 层业务规则**而非约束：D-Q31 去重协议（advisory lock 串行化 + 非豁免店查重 `LISTING_DUP_IN_TEAM`，service.py:176-228），店铺 `dedup_exempt` 可豁免。
- 变体组维度：`app.variant_group` / `app.variant_member` 表**已在 0007 迁移建好**（alembic/versions/0007_scrape_catalog.py:84-119：group.status ∈ active/broken，member.product_id UNIQUE=一品最多一组，`uq_variant_primary (group_id) WHERE is_primary`；`product.variant_group_id` 列 0007:70 + FK 0007:108-109）——与图纸 specs/001-domain-model/03-catalog.md:35-54 一致；**代码层（src/erp/ 下）对这两表零读写**（全仓 grep 无命中），API 契约有 `POST /variant-groups`、`PUT /variant-groups/{groupId}/members`（specs/002-api-contract/openapi-v0.yaml:496-525）但 router 未实现。
- **"一组多 listing"与单 listing 管道的关系**：组 → N 个 member product → 各自 allocate 出各自 listing（每个成员独立 GTIN/SKU/状态机），同组成员打进**同一个 MP_ITEM feed 的多个 MPItem 元素**（封套 MPItem 本就是数组，service.py:443），靠 Visible 段 variantGroupId 在渠道侧关联成组——**管道形态天然兼容，不需要"组 listing"新实体**。缺的是组级编排：①同组必须同店同模式（一 feed 一店约束 service.py:363-364 决定变体组不可跨店提交，呼应 BR-LST-012/013 跨店归 leader/anchor 语义）；②组级提交入口（按 group 展开成员 listing_ids）；③组级状态聚合（现状态机是 per-listing 的 `transition`，service.py:49-89）。
- **语义冲突点**：现有批内是"逐品跳过、余员照发"（spec 失败/校验失败单品 failed，service.py:420-435），而 R2-11 验收要求"组员缺失构建拒绝"（review_list.json:581）——组内一员构建失败时应整组拒，这需要在 `_submit_tx1` 把"批"再分"组"，组内任一失败则整组回滚（含配额返还，模式参考 SAVEPOINT 用法 service.py:236-267）。

---

## 3. 组完整性守卫落点

图纸依据：`variant_group.status`：**broken = 成员不齐/主题冲突，spec 构建拒绝**（specs/001-domain-model/03-catalog.md:43；DDL 0007_scrape_catalog.py:92-94）。

现状**没有**任何"构建前组校验"函数——R2-03 构建前校验的既有模式是 build_spec 内的两个 fail-closed 前置检查，是新守卫的模板：

- `_resolve_wpt`（spec.py:148-173）：类目硬拒/WPT 不可得 → `raise BusinessError("ERP_SPEC_BUILD_FAILED", ...)`，由 `_submit_tx1` 捕获转 failed（service.py:420-425）。
- `_match_identifier`（spec.py:305-315）：match 无标识符 fail-closed。

（澄清：任务描述里的"增量3 本地校验器"实为增量 4 的 `validator.py`——但它跑在**实例化后的单 item** 上（spec.py:381-387），管的是字段合法性，做不了跨成员的组完整性；组守卫是新函数。）

建议三级落点（由外向内）：

1. **allocate 准入**（service.py:199-207 产品状态检查旁）：product 属 broken 组 → 拒绝分配（rejected code 如 `VARIANT_GROUP_BROKEN`），最早拦截、不浪费 GTIN 预占。
2. **_submit_tx1 准入段**（service.py:350-366，与 is_locked/状态/FEED_MIXED_BATCH 并列）：**主落点**——按 listing→product→variant_member 反查组，校验 ①组 status=active；②组全体成员的 listing 都在本批同店同模式（成员缺席=组不齐 → 整组 skip，code 如 `VARIANT_GROUP_INCOMPLETE`，含缺席成员明细满足"可见原因"验收）；③维度值组合唯一/主变体存在（图纸要求服务层校验 03-catalog.md:53-54）。
3. **build_spec 内兜底**（spec.py:349-358 WPT 解析旁）：单独走 dry-run/工具路径时同样 fail-closed（`ERP_SPEC_BUILD_FAILED` 或新码），保证守卫不因入口绕过失效。新错误码需入 `listing_error_catalog` 种子（0009_listing.py:297-310 模式；数据文件 src/erp/listing/data/listing_error_catalog_seed.jsonl 已有变体相关渠道码 `EXT_DATA_ERROR_66547201695750`"异步审核伪错误（变体关联处理中）"→ backoff_retry）。

---

## 4. db 测试与 mock 模式

基座 `tests/db/conftest.py`：双 DSN（`MIGRATOR_URL` DDL 超级用户 / `APP_URL` erp_app 受 RLS，conftest.py:23-30），PG 不可达整目录 skip（conftest.py:41-42），`migrated_db` session fixture 跑 alembic 到 head + 建测试团队（conftest.py:45-60）。

| 文件 | 覆盖 | mock 模式 |
|---|---|---|
| `tests/db/test_spec_v5.py` | 构建器全量：header 3 字段/Orderable 强制格式/零认证覆盖/WPT 链 fail-closed/match 五字段/缓存复用与失效 | **无 HTTP mock，纯 DB**：module 级 `_seed` 用 psycopg 直连 migrator 播种 `refdata.pt_spec.fields` 手写 JSON schema fixture + pt_meta/category_map/product（test_spec_v5.py:37-141），测试体直调 `spec_builder.build_spec`（`_build` helper，165-179）；缓存失效靠向 pt_spec 写一行 bump dataset_revision（297-305） |
| `tests/db/test_listing_dryrun.py` | R2-03 验收① harness：5 个 WPT fixture 规格（含 allOf 条件必填、`__orderable__` 伪行播种，87-141）过 `run_dryrun`（src/erp/tools/listing_dryrun.py:81-161）；贫瘠品被拦不拖垮判定（175-195） | 同上纯 DB |
| `tests/db/test_listing_api.py` | 上架闭环 API 级：GTIN→allocate→submit→poll→delist | **渠道 mock**：`_FakeChannel` 脚本队列 handler（46-62，/v3/token 自动发 token）+ `gateway._transport_factory = lambda proxy: httpx.MockTransport(f.handler)` 注入、用后还原并清 `gateway._clients` 连接池（157-164）；gateway 模式经 system_config `channel.gateway_mode` 播种 live_test / 切 dry_run（142-146、454-491） |
| `tests/db/test_attr_fill.py` | AI 填写：写回/缓存命中/坏 JSON fail-closed 自愈/无 schema 不调 LLM/合并序 | **LLM mock**：`_FakeLlm` 脚本队列 + `llm_client._transport_factory = lambda: httpx.MockTransport(fake.handler)`（35-61），断言调用次数与 last_messages prompt 内容 |

R2-11 测试可直接复用：pt_spec fixture 里加变体字段（variantGroupId/isPrimaryVariant/swatch 及维度属性 enum）→ psycopg 播种 variant_group/variant_member → 直调 build_spec 断言 item 变体段与组守卫 fail-closed（test_spec_v5 模式）；组级 submit/整组拒走 test_listing_api 的 _FakeChannel 模式。

---

## 5. 扩展点清单（落码位速查）

| 扩展项 | 切点 | 引用 |
|---|---|---|
| 变体维度值入 Visible 模板 | `_build_visible` 内、零认证覆盖前 | spec.py:211-234 |
| variation_theme→Walmart 属性名映射 | 新模块（旧仓 remap 语义），或并入 coerce 链新步骤 | coerce.py:818-867；archaeology.md:70,143 |
| variant_attrs 进缓存指纹 | fingerprint dict 加键 | spec.py:360-376 |
| variantGroupId/isPrimaryVariant 注入 | `build_spec` keyword 形参 + `_instantiate` | spec.py:331-342, 470-513 |
| swatchImageUrl | 已是系统字段，图片段写入 | spec.py:59, 225-229 |
| 组完整性守卫 | allocate 准入 + `_submit_tx1` 准入段 + build_spec 兜底 | service.py:199-207, 350-366；spec.py:148-173（模式） |
| 单 item 变体字段校验 | `validate_build_item` | validator.py:125-162 |
| 组级错误码 | listing_error_catalog 种子 | 0009_listing.py:297-310；listing/data/listing_error_catalog_seed.jsonl:4 |
| 建组/设成员 API | 契约已有、router 未实现 | specs/002-api-contract/openapi-v0.yaml:496-525 |
| DB 表 | 已就绪（active/broken、is_primary 唯一、一品一组） | 0007_scrape_catalog.py:84-119；03-catalog.md:35-54 |

## 开放问题

- variantGroupId 取值与注入层级：组标识用 variant_group.id 派生串还是渠道/自定义值？走 _instantiate 注入（不进缓存键）还是进 build_hash？同一产品被重新归组后 listing_spec 缓存（唯一键 product_id+offer_mode+build_hash，0009_listing.py:228）如何失效——若仅 variant_attrs 进指纹，组 ID 变更不会失效缓存，需拍板。
- 组提交原子性口径：现有 _submit_tx1 是逐品跳过、余员照发（service.py:420-435），R2-11 验收要求组员缺失整组拒——组内一员 spec 构建/校验失败时整组回滚（含 GTIN/配额返还）还是允许部分成功？部分成功后的组重投语义（渠道侧半组 live）如何定义？
- 变体组与店铺的关系：一 feed 一店约束（service.py:363-364）意味着组必须整组同店提交；BR-LST-012/013 的跨店归 leader/anchor 语义在新系统 variant_group（无 store 维度，0007 迁移）如何承载——是否给组加 anchor_store 列，D-Q31 团队去重与组成员同店要求如何协调，需 Owner 拍板。
- variation_theme → Walmart variant 属性名映射的实现档位：旧仓是硬编码 remap（mapper.py:1492）+ LLM 兜底（mapper.py:1548，失败整组降级单 SKU）两级——R2-11 首增量是否移植 LLM remap，还是先只支持直映射+失败标 broken？
- 建组/设成员端点（openapi-v0.yaml:496-525 契约已有）归属：catalog 域（产品关系）还是 listing 域（上架编排）？按角色制管辖目录需先定 owner；采集端 parent ASIN 自动归组与人工建组的优先级/冲突解决（同 ASIN 两来源）也需口径。