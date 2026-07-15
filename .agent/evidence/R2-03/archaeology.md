# R2-03 上架真实化 — 考古报告（动工前）

日期：2026-07-15。方法：五路并行考古（旧 auto_listing 管线 / 新系统骨架 / 图纸核对 / spec 素材盘点 / 可复用基建），全部论断带 file:line 证据；本文件 §0 为执行摘要与增量计划，§1-§5 为五路原始报告（保留全部细节供实现时查证）。

源材料：`/home/user/erpAPI`（旧脚本仓，本工作区已挂载）。注意 walmart-audit-system 源仓未挂载（本单不需要——上架旧实现全部在 erpAPI 仓 auto_listing/）。

---

## §0 执行摘要

### 0.1 最重要的三个事实

1. **旧 auto_listing 是一套 14,400 行的实战系统，校验/coerce 函数群每一行都是错误码换来的**（§1 表格：12 个函数对应 10+ 个实测错误码）。R2-03 的本地校验器不是"写一个 JSON schema 校验器"，而是保真移植这套实战规则 + 通用 schema 校验。
2. **spec 规格数据源在本地不完整**（§4）：MPSetup v5 monolith（451MB）、MPSetup_by_pt/ 拆分目录、pt_templates_full.json（304MB）都只在 Owner T7 备份。本地只有 summary（字段名清单，无类型/枚举/约束）。**且旧审核库 walmart_pt_spec.fields 是压缩版**（sync_pt_specs.py:100-110：enum 截断到 10、丢 length/pattern/allOf）——直接导它做枚举校验会假拒。
3. **新系统骨架的接缝都在**（§2）：listing 六表齐（含 listing_error_catalog + listing_spec.cert_overrides 预留列）、状态机/verify-back/GTIN 池全落地、refdata.pt_spec 表已建（审核子集列）、import 通道模式成熟、LLM 三段原语可复用。缺的就是 R2-03 范围的四件套。

### 0.2 spec 规格数据方案（已定）

- **canonical 存储**：`refdata.pt_spec` 增 `fields JSONB` 列（无损 per-field schema：name/required/type/desc/enum 全量/minLength/maxLength/pattern/format/minItems/maxItems/items/allOf 条件必填）+ `spec_source`/`spec_synced_at` 溯源列。需 0020 迁移（ar 帽）+ 001 §03 图纸补正（图纸警报 §4-5a）。
- **数据生产**（部署机，两条路任一）：
  - **A（无需等 T7）**：live spec 拉取 `POST /v3/items/spec`（body `{feedType:"MP_ITEM", version:完整时间戳, productTypes:[≤20]}`，官方限流 10/min）→ 提取工具产 jsonl → import CLI。≥5 WPT 一次调用即齐，且天然是"官方 spec"。
  - **B（全量正解）**：Owner 从 T7 拷 MPSetup monolith → 提取工具流式产全量 6,942 PT jsonl → import CLI。
  - 两条路共用同一个**无损提取工具**（本单交付，替代旧 extract_pt_templates.py 的有损版）。
- **沙盒开发/测试**：fixture 级 per-WPT schema（真实形态、小体积）+ fake LLM；验收①在部署机用真数据跑（或 Owner 回传 ≥5 WPT jsonl 后沙盒跑）。

### 0.3 增量切分（每增量 CI 绿才提交）

| # | 增量 | 内容 | 关键源对照 |
|---|---|---|---|
| 1 | pt_spec fields 通道 | 0020 迁移（pt_spec 加 fields jsonb；ck_llm_usage_module 加 'listing'；ck_import_job_domain 加 'listing_error_catalog'）+ PT_SPEC_DOMAIN 扩列 + 无损提取工具 tools/extract_mp_item_spec.py（monolith 流式 + live-spec 响应两输入）+ 001 §03/§05 图纸补正 | extract_pt_templates.py（形态参照，去截断）、auto_listing/pt_spec.py（拆分加载教训）、sync_pt_specs.py:100-110（压缩版教训） |
| 2 | spec 构建器真实化 | listing/spec.py 重构：WPT 解析链补 category_map/L1 第三级；Visible 按 pt_spec.fields 出属性；Header 3 字段 + version 完整时间戳（system_config）；Orderable 强制格式（BR-LST-007/008/011）；零认证覆盖（BR-AUD-006）写 cert_overrides | mapper.py force_overrides(1161-1296)、feed_submit.py build_feed(88-119) |
| 3 | AI 属性填写 | listing/attr_fill.py：module=listing 记账；SYSTEM/USER_TEMPLATE prompt 保真移植（静态前缀 cache 友好结构保留）；LLM 只填结构化字段、系统后处理覆盖；fail-closed（输出不合规不进 payload） | mapper.py:14-106、245-273；audit/llm.py 三段原语 |
| 4 | 本地校验器 | listing/validator.py：required/enum/type/length/pattern/minItems + 条件必填求值器（_evaluate_if_condition 保真）+ 类型 coerce/枚举修复/未知字段剔除等 12 函数群语义移植；接线 submit 的 build_spec 之后、feed 之前；失败走既有 failed+配额返还分支 | mapper.py §1-4 表格全部函数 |
| 5 | 错误码灌入 | listing_error_catalog 数据文件（reconcile.py:58-73 + sync_listing_state.py:39-43 + BR-LC-* 全集）+ 导入通道；旧 8 处置 → 新 6 disposition 映射表（需要时扩枚举经 Owner） | §1-6 错误码知识库表 |
| 6 | 验收①harness | dry-run 产物落档工具 + 官方 spec 校验（validator 用真数据即官方校验）+ ≥5 WPT 证据入 evidence/；顺手把 request_snapshot 落 feed.raw_response_ref（列已预留） | test_gateway.py:252-276 证据落档模式 |

### 0.4 部署机/Owner 依赖（尽早给指令）

1. **pt_spec fields 数据**：增量 1 合入后给部署机可粘贴指令（提取→import CLI→import_job 验证）。路 A 用 A152 凭证 live 拉（≥5 WPT 起步）；路 B 需 Owner 从 T7 拷 MPSetup monolith 到部署机。
2. **验收②窗口**：A152 真调 1 SKU 全流程（runbook 已有 .agent/evidence/R1-11/a152-live-runbook.md），且 **RS-03b（channel outbox+幂等）闸门挂在真实写入前**——需在验收②之前完成 RS-03b。
3. 顺带修复项（移植时）：旧系统 DeepSeek/Qwen key 硬编码 config.py:62/66 →新系统一律配置中心；fulfillmentLagTime 硬编码→system_config。

### 0.5 specs 正文修订清单（随实现落笔，specs 只由云端 AI 写）

- **必改（本单范围内）**：001 §03 补 refdata.pt_spec 列级设计（a）；005 R2-03 范围修订——category_map 导入已由 DG1 完成，实际增量=pt_spec fields 接入（c）；04 import_job domain 枚举与 DB 实际（pt_meta/pt_spec 已在 0016/0019 加入 CHECK）对齐（h 一部分）；001 §06 listing_spec 段加 end_date ISO DateTime 注记（f）。
- **随增量 5 定**：disposition 新旧映射表（i）——若需扩枚举，001+002 同步改并提 Owner。
- **移交 RS-11（漂移清理单）**：openapi /category-map 旧设计（b）、005 R2-02 段文字过时（d）、GTIN 释放语义歧义+UPC 复用优先级缺失（g，BR-UPC-005/006 vs 001 §03）。
- **需 Owner 拍板**：dry-run 契约落点（e）——建议：验收①走工具脚本不进契约，正式 dry-run 端点随 R2-04 worker 化一并定。

---

## §1 旧系统上架管线考古（auto_listing/）

考古时间：2026-07-15。目录共 41 个 py 文件 + README + docs/，主链路约 14,400 行。README（`auto_listing/README.md`）本身是高质量一手资料，本节所有论断均已回源代码核实并给出 file:line。

---

## 1. 整体管线

单进程编排在 `main.py:635 main()`，Phase 0 → 3 顺序执行；调度聚合入口 `scheduler.py:214 COMMANDS`（10 个子命令，`scheduler.py:36 _run_module` 以子进程方式跑各模块），macOS launchd 4 条定时链（`launchd/*.plist`，README.md:219-224）。

一个待上架商品的完整旅程：

| 阶段 | 位置 | 内容 |
|---|---|---|
| 领任务 | `feishu_io.read_pending_rows` + `filter_pending`（main.py 内调用） | 飞书上架表 26 列，人工填 A(ASIN)/B(店铺)/D(WPT)/E(审核结果) |
| Phase 0 店铺门 | main.py:867（店铺状态非 ACTIVE 跳过）、main.py:882-892（`quota.get_remaining_list_quota` 日配额） | |
| Phase 0 (opt-in) | main.py:903-924 | `--live-spec` 时 `live_spec.fetch_spec` 按 20 PT/批拉实时 spec，`pt_spec.inject_live_spec` 覆盖本地快照 |
| Phase 0.5 廉价过滤 | `main.py:109 quick_filter_one` | 顺序：risk_gate.check_pt(main.py:122) → PT 本地 spec 存在性(main.py:128-131) → DMIT 拉取(main.py:135 `fetch_amazon_data`) → risk_gate.check_brand(main.py:141) → 库存≥5(main.py:148) → `pricing.compute_walmart_price`(main.py:153)。不领 UPC、不调 LLM |
| Phase 0.7/0.8 变体 | main.py:1044-1221 | 按 `mapper.full_variant_group_set`(mapper.py:1307) 分组 + PT 一致性 + 跨店归 leader；attr key 不合法时硬编码 remap(mapper.py:1492 `try_hardcoded_remap_variant_attrs`) + LLM 兜底(mapper.py:1548 `remap_variant_attrs_via_llm`) |
| Phase 1 UPC 预分配 | main.py:1223-1339 | 优先 ERP_SERVER 集中分配(main.py:1240)，否则本地文件锁(main.py:1305)；领取即标"已领"永不自动释放 |
| Phase 2 预处理 | `main.py:431 prepare_one_async`（sync 版 `main.py:238 prepare_one`），20 worker async pool(main.py:1399) | LLM 映射 → force_overrides → 十余步清洗 → validate（详见 §2-§4） |
| Phase 2.5 | main.py:1402-1426 → `mapper.differentiate_variant_titles_in_group`(mapper.py:1632) | 同组 productName 全同时加属性后缀 |
| Phase 2 提交 | main.py:1428-1509 → `main.py:578 submit_store_batch` → `feed_submit.build_feed`(feed_submit.py:88) + `submit_feed`(feed_submit.py:195) | 同店全部商品打包成**单个** MP_ITEM feed（因 10/小时配额），多店 ThreadPoolExecutor 并发(main.py:1467) |
| 提交后闭环 | feed_submit.py:331 `_build_success_result` → `pending_feed_tracker.register_feed`(pending_feed_tracker.py:57) + `notifier.notify_feed_submitted`；成功后 `upc_pool.mark_used_batch`(main.py:627) | |
| Phase 3 回写 | main.py:1529 起 | 批量写飞书 K=Yes/L=feedId/M=日期/N=失败原因；失败按 `retry_state.classify` 记账(main.py:1577-1578)；提交失败三态处理见 §5 |
| T+6h 对账 | launchd 每小时 → `scheduler.cmd_reconcile_due`(scheduler.py:67) → `auto_reconcile.run_once`(auto_reconcile.py:39) → `reconcile.reconcile`(reconcile.py:105) | 写飞书 O/P/Q，终态 `tracker.update_status(done=True)` 归档(auto_reconcile.py:100) |
| Live 状态回写 | `sync_status_track.py`（拉 `GET /v3/items` 5 轮 lifecycle 覆盖写 R-W 列）；`sync_listing_state.py` 按 feedId 批量修 K/O（sync_listing_state.py:124-134） | |
| SKU_LOCKED 自愈 | `retire_and_relist.py`：retire 提 RETIRE_ITEM feed + O=RETIRING → 24h 冷却 → relist 清飞书 K-P 让 main 重跑（README.md:107-108） | |

---

## 2. MPSetup v5 spec 构建

**spec 数据源（精确）**：
- 本地快照：`walmart_official_specs/MPSetup/5.0.20260304-22_45_32-api_MP_ITEM_0_0_en.json`（config.py:72-77 `MP_ITEM_SPEC_PATH`）。原始文件 451 MB，**按 PT 拆分到 `walmart_official_specs/MPSetup_by_pt/` 目录**（`tools/split_mp_item_spec.py` 生成；pt_spec.py:9-14 记载 OOM 事故动机）。加载链：`pt_spec.py:52 _pt_index()`（读 `_pt_index.json` ~10KB）→ `pt_spec.py:64 _load_pt_visible(pt_name)`（`lru_cache(512)`，单 PT 文件 10-100KB）→ `pt_spec.py:76 _load_orderable()`（共享 `_orderable.json` ~25KB）。
- `pt_templates_full.json`（307 MB，config.py:71 `PT_TEMPLATES_PATH`）仅 `update_listed.py` 用（pt_spec.py:42-43），主上架链**不用**。
- 实时拉取（opt-in）：`live_spec.py:24 fetch_spec` → `POST /v3/items/spec`，body `{"feedType": "MP_ITEM", "version": MP_ITEM_SPEC_VERSION, "productTypes": [...]}`（live_spec.py:41-45），限流 3/min × ≤20 PT/次；`pt_spec.py:85 inject_live_spec` 注入后 `load_mp_item_visible_schema`(pt_spec.py:107) 优先走 live 覆盖。

**schema → LLM 摘要**：`pt_spec.py:205 field_summary(pt_name)` 返回 `{visible_fields, orderable_fields, visible_required, orderable_required}`；每字段由 `pt_spec.py:157 _summarize_prop` 拍平为 `{name, required, type, title, desc, enum, example, minItems/maxItems/minLength/maxLength, item_*, object_*}`。

**payload 顶层结构（feed_submit.py:106-119 原文）**：

```python
return {
    "MPItemFeedHeader": {
        "businessUnit": MP_ITEM_BUSINESS_UNIT,  # 必填，"WALMART_US"
        "locale":       MP_ITEM_LOCALE,
        "version":      MP_ITEM_SPEC_VERSION,    # 完整时间戳，不能 "5.0"
    },
    "MPItem": [
        {
            "Visible": {item["pt"]: item["visible"]},
            "Orderable": item["orderable"],
        }
        for item in items
    ],
}
```

关键实测知识（feed_submit.py:91-100 docstring）：**Header 只能 3 字段**；官方 sample 的 `sellingChannel/processMode/subset/subCategory` 会被拒（`EXT_DATA_ERROR_60670554076755`）；version 必须完整时间戳 `5.0.20260304-22_45_32-api`（config.py:78），`"5.0"` 被拒（`EXT_DATA_ERROR_74597363510508`）。

**Orderable 强制格式**（mapper.py:1177-1296 `force_overrides`，实测拒绝记录反推）：`price` 是**裸 number** 非对象(mapper.py:1272)；`productIdentifiers` 是**单对象** `{"productIdType":"UPC","productId":...}` 非数组(mapper.py:1274-1277)；`inventory[].fulfillmentCenterID` 必填 = Partner ID（`store_info.get_partner_id`，`GET /v3/settings/partnerprofile`）(mapper.py:1285-1288)；`endDate` 必须 ISO DateTime，纯 `yyyy-mm-dd` 被拒 `EXT_DATA_ERROR_00030257670757`(mapper.py:1291-1294, config.py:97-100)。

---

## 3. AI 属性填写

**模型**：DeepSeek `deepseek-v4-flash`（config.py:62-64，JSON 模式 + `thinking:{"type":"disabled"}`，llm_client.py:281-283），备选 Qwen turbo（config.py:66-68）。temperature 0.2、max_tokens 4096（mapper.py:30 `LLM_MAPPING_MAX_TOKENS`）。**API key 硬编码在 config.py:62/66 —— 移植时必须改配置中心（触碰 ERP-ALL 铁律 5）**。

**分工**：LLM **只填结构化字段**；文案（productName/shortDescription/keyFeatures）、品牌、图片、sku/price/UPC/inventory/日期等由系统后处理强制覆盖（mapper.py:14-29 `SYSTEM_*_POSTPROCESS_FIELDS`，字段列表从喂给 LLM 的 schema 中剔除：mapper.py:187-194）。

**System prompt 关键段原文**（mapper.py:40-77 `SYSTEM`）：

> ```
> 你是 Walmart Marketplace 上架专家。任务：把 Amazon 商品原始字段映射为 Walmart MP_ITEM v5 的 Visible 和 Orderable 字段。
> 【运营场景】Amazon → Walmart 搬运（跟卖），用户**没有任何认证/证书/文档**...
> 【关键铁律】**绝对不要选会触发"必填认证文档"的 enum 值**...
> 3. **枚举字段必须严格从给定 enum 列表里选一个原文值**；...**宁可填 enum[0] 也不要填非法值**。
> 4a. **数组类字段值必须是 JSON 数组**，绝不能是裸字符串。...
> 16. **isAssemblyRequired** 一律填 "No" ... 17. **has_written_warranty** 一律填 "No" ...
> 19. **certification_type** 一律填 "Neither of these applies" ... 20. **has_nrtl_listing_certification** 一律填 "No"
> ```

**User prompt 模板**（mapper.py:79-106 `USER_TEMPLATE`）：把 `field_summary` 拆成 5 个 block（visible 必填全量 / visible 可选前 20 / **allOf if-then 条件必填中文翻译**（mapper.py:109 `_format_conditional_block`）/ orderable 必填 / orderable 可选前 10），末尾附裁剪后的 Amazon JSON（mapper.py:231 `_amazon_for_llm`：剔除 image_urls 与 `__cached_*`，压缩空白）。静态 schema 块在前、`▼▼▼ 本次特定数据` 在后（mapper.py:96）——**刻意为 DeepSeek prefix cache 优化**。

**入口**：`mapper.py:245 map_amazon_to_walmart`（sync）/ `mapper.py:273 map_amazon_to_walmart_async`；输出必须含 `visible`+`orderable` 否则抛 LLMError(mapper.py:268-269)。

**基建**（llm_client.py）：`chat_json`(llm_client.py:336) = SQLite 输入哈希缓存短路(llm_client.py:367-377) → tenacity AsyncRetrying 指数退避 5 次(llm_client.py:384-388，仅 LLMRetriableError：429/5xx/超时/空响应) → `_DynamicLimiter` AIMD 自适应并发 5-60(llm_client.py:118-202)。JSON 解析容错：剥 ``` 围栏 + 第一个 balanced `{...}`(llm_client.py:242-268)。缓存 key = `sha256(model+messages+temperature+max_tokens)[:32]`（llm_cache.py:62-68，SQLite WAL，`state/llm_cache.sqlite` 生产 ~462MB）。

**输出校验/coerce**：全部在 Python 侧（见 §4）。**失败处置**：LLM 网络类 → `retry_state` 不记账下次重试；输出不合规（必填缺失/结构错）→ `LLM_INVALID` 累计 3 次 terminate（retry_state.py:58），payload 落盘 `logs/llm_raw_{asin}_*.json` 供诊断(main.py:557-562)。

**第二处 LLM 用途**：变体维度 remap prompt（mapper.py:1573-1601，要求输出 `{"walmart_key":..., "rationale":..., "items":[{"asin","value"}]}`），输出校验极严：key 必须 ∈ enum、items 数量与 ASIN 集合必须精确匹配，否则整组丢弃降级单 SKU（mapper.py:1610-1629）。

---

## 4. 提交前本地校验（核心资产）

规则全部来自 **MP_ITEM v5 JSON schema 本身**（required/enum/type/minItems/allOf if-then）+ **实战错误码反推的白名单**。`prepare_one_async` 中的固定清洗流水线（main.py:485-563，sync 版 main.py:305-410 同构）：

| 步骤 | 函数 | 防的错误码 |
|---|---|---|
| 强制覆盖 | `mapper.force_overrides` mapper.py:1161 | 零认证 5 字段强制(mapper.py:1221-1241) + 危险文档字段清除(mapper.py:1245-1256) |
| 文案强制 Amazon 原文 | `force_amazon_copy` mapper.py:654（+ 去品牌 `scrub_brand_from_text` mapper.py:623） | keyFeatures 不足 4 条拆句补齐(mapper.py:731-748) |
| 条件必填解析 | `resolve_conditional_required` mapper.py:874（通用 JSON Schema if-then 求值器 `_evaluate_if_condition` mapper.py:828）+ `fill_known_walmart_required` mapper.py:909 **不动点迭代 ≤6 轮**(mapper.py:925-982，修级联条件) + 白名单 `KNOWN_WALMART_CONDITIONAL_REQUIRED` mapper.py:810 | EXT_DATA_ERROR_72600149546850 |
| 必填兜底 | `fill_missing_required` mapper.py:987 + `_safe_default_for` mapper.py:326（优先 'No'>'None'>'Not Applicable'>enum[0]；URL 数组禁占位） | |
| 类型 coerce | `fix_type_mismatches` mapper.py:1022（字符串包 array / URL 占位删除 / object.unit 非法 enum 替换） | EXT_DATA_ERROR_50716566635066 / 49505365506868 / IB.VALIDATION.DATA.001 |
| 枚举修复 | `fix_invalid_enums` mapper.py:1113（非法值→安全默认→enum[0]→非必填删） | IB.VALIDATION.DATA.001 |
| 未知字段剔除 | `strip_unknown_fields` mapper.py:780（additionalProperties=false） | |
| stateRestrictions 清理 | `clean_state_restrictions` mapper.py:396 | zipCodes 必填拒 |
| 空可选字段删 | `drop_empty_optional_fields` mapper.py:436 | |
| minItems 违规删 | `drop_min_items_violations` mapper.py:467（如 productSecondaryImageURL 需≥5） | |
| 长度约束 | `enforce_copy_limits` mapper.py:545（productName≤199 / shortDesc≤4000+60词 / keyFeatures per-PT minItems / manufacturer≤60） | EXT_DATA_ERROR_01076067496949 / 55506974520167 |
| 小数位 | `round_decimals_to_2` mapper.py:499 + 提交边界兜底 `feed_submit.sanitize_feed_numbers` feed_submit.py:64 | EXT_DATA_ERROR_68050064665065 |
| 终校验 | `validate_payload` mapper.py:301（visible/orderable required 非空检查，缺失即整行失败） | |

---

## 5. feed 提交与轮询

**提交**：`feed_submit.submit_feed`(feed_submit.py:195)，`POST {BASE_URL}/v3/feeds?feedType=MP_ITEM`(feed_submit.py:231,264)，timeout 90s，走 `walmart_client.safe_post_ex` + 代理。提交前 feed JSON 无条件落盘 `logs/feed_{store}_{sku}_{n}_{ts}.json`(feed_submit.py:219-221)——这是 reconcile 反查 SKU→UPC 的唯一依据。dry_run 只落盘不 POST(feed_submit.py:227-228)。

**防重复提交（最值钱的工程细节）**：4xx(≠408/429) 直接抛不重试(feed_submit.py:286-291)；5xx/408/429/超时 → 重试前先反查 `_verify_feed_submitted`(feed_submit.py:122)：`GET /v3/feeds?limit=20&feedType=MP_ITEM`，按 `itemsReceived==n_items` + feedDate ∈ [t_start-30s, +10min] 匹配(feed_submit.py:163-171)。三态 `VerifyResult`(feed_submit.py:39-48)：FOUND=当成功；NOT_FOUND → 30s 后二次确认（窗口拉宽 15min，feed_submit.py:311-323），仍空则高置信未收到 → main.py:1484-1494 **回收 UPC + 跳过飞书写入**（`unmark_used_batch` main.py:1518）；UNKNOWN → 保留"已领"+ K=Unknown 待 sync_status_track 自愈(main.py:1495-1502)。

**轮询**：`pending_feed_tracker.register_feed`(pending_feed_tracker.py:57) 写 `state/pending_feeds.json`；`list_due_feeds`(pending_feed_tracker.py:77) 规则 = 提交 ≥6h（`RECONCILE_DELAY_HOURS` config.py:132）且 <168h；launchd 每小时 :15 跑 `auto_reconcile.run_once`(auto_reconcile.py:39)，跨店并发 8 / 同店串行(auto_reconcile.py:75-94)，网络抖动进重试队列二轮串行(auto_reconcile.py:114-120)。终态判定 `_is_terminal` = stats.unknown==0(auto_reconcile.py:34-36)；超期 `gc_expired` 归档 `feed_history.json`(pending_feed_tracker.py:132)。

**itemDetails 解析**：`reconcile.reconcile`(reconcile.py:105)，`GET /v3/feeds/{feedId}?includeDetails=true&offset&limit=50` 分页(reconcile.py:123-151，单页 3 次退避重试；offset=0 失败抛错、中途失败截断)。每 item 取 `sku` / `ingestionStatus` / `ingestionErrors.ingestionError[].{code,description}`（code 要 strip 防 Walmart 末尾 `\t`，reconcile.py:191）。分类优先级（reconcile.py:224-264）：SKU_LOCKED 码 > SUCCESS 无码 > INPROGRESS > 剩余码全为 ASYNC 码→ASYNC_PENDING > SUCCESS+杂码→SUCCESS_WITH_WARNING > DATA_ERROR。所有错误码拼接写 P 列(reconcile.py:197-200)。`check_feed.py:38 get_error_report` 另可下 `GET /v3/feeds/{feedId}/errorReport` CSV。

---

## 6. 错误码知识库（散落四处，移植时应集中成错误码目录）

| 类别 | 码 | 出处 | 处置 |
|---|---|---|---|
| UPC 冲突 | `ERR_EXT_DATA_0101119` | reconcile.py:58 | `upc_pool.mark_conflict`，O=DATA_ERROR |
| SKU 锁死 | `ERR_EXT_DATA_0101211` | reconcile.py:73 | O=SKU_LOCKED → retire+24h 冷却+relist（**不能新 UPC 重发同 SKU**，README.md:366） |
| 异步审核假错误 | `EXT_DATA_ERROR_56026862530206` / `66547201695750` | reconcile.py:61-64 | O=ASYNC_PENDING，不动，几小时-几天自然 SUCCESS |
| 可重试 | `EXT_DATA_ERROR_72600149546850`（必填缺失）/ `IB.VALIDATION.DATA.001`（枚举）/ `ERR_OFFER_2020` / `ERR_INT_SYS_01010010` | reconcile.py:66-71 | 修 mapper 后 main 重跑（领新 UPC） |
| 政策违禁（标死） | `EXT_DATA_ERROR_71666506605865`(军警) / `61696573580701`(枪械配件) / `61020366035308`(通用禁售) | sync_listing_state.py:39-43 | 写 D=fail + O=PROHIBITED 永久淘汰 |
| 格式陷阱（已在校验器内固化） | header 拒收 `60670554076755`/`74597363510508`；endDate `00030257670757`；小数位 `68050064665065`；manufacturer 60 字 `01076067496949`；keyFeatures minItems `55506974520167`；array 类型 `50716566635066`；URL 占位 `49505365506868` | feed_submit.py:96-98, config.py:100, mapper.py 各处 | 提交前修复 |
| 上架前失败状态机 | `retry_state.THRESHOLDS`(retry_state.py:51-60)：DMIT_NOT_FOUND=3 / STOCK_LOW=30 / PT_INVALID=3 / PRICE_OUT_OF_BAND=30 / LLM_INVALID=3 / *_TRANSIENT 不记账 | retry_state.py:104 `classify` 正则分类 | 达阈值 terminate → 写 D=fail 永久淘汰（不可回滚，README.md:373） |

`risk_gate.py` 是**事前**拦截（非错误码）：禁售 PT（飞书类目映射表 D=禁售/E=否*，risk_gate.py:84-90）+ 品牌黑名单 casefold 匹配(risk_gate.py:93-100)；24h 缓存 + **fail-open**（飞书故障用过期缓存，无缓存放行并 WARNING，risk_gate.py:107-144）。

## 7. 配套机制一句话

- **quota.py**：从飞书定价表读每店每日 FBA/FBM 上架/下架配额（quota.py:57 `load_quotas`），北京 0 点重置；`get_remaining_list_quota`(quota.py:141) = 配额 − 今日 K=Yes 行数（quota.py:124 数飞书）。注意：不在表中的店铺默认 999 放行(quota.py:113)。**不是 GTIN/UPC 配额**——UPC 号段管理在 `upc_pool.py`（飞书池领号、已领/已用/冲突标记、文件锁）。
- **pricing.py**：Walmart 价 = (Amazon current_price 优先 + 运费) × 店铺 FBA/FBM 区间倍率（pricing.py:221 `compute_walmart_price`；区间外不上架 pricing.py:250）；倍率解析需处理 `'275%'` 格式化值（pricing.py:31 `_parse_multiplier`，6-11 全店误淘汰事故）；另有 clamp 版仅供展示(pricing.py:253)。
- **rate_limiter.py**：(店铺,端点) 滑动窗口（rate_limiter.py:46 `acquire` 阻塞式）+ 响应头自适应（rate_limiter.py:76 `update_from_response` 读 `x-current-token-count`==0 时等 `x-next-replenishment-time`）；配额表 `WALMART_RATE_LIMITS`(rate_limiter.py:123-133，MP_ITEM feed 10/h、items/spec 3/min)。
- **reconcile.py / auto_reconcile.py**：feed 终态对账写 O/P/Q（见 §5）；**列权责隔离**是硬约定（reconcile.py:6-11：reconcile 只写 O/P/Q，不碰 main 的 K-N、审核的 D-G、sync 的 R-W）。
- **sync_listing_state.py**：按 feedId 批量把 Walmart 当前 ingestion 状态修回飞书——SUCCESS 补 K=Yes+修 UPC 池标记(sync_listing_state.py:124-129,166-172)、PROHIBITED 写 D=fail(sync_listing_state.py:180-184)、其余留空重试；解决"昨天失败今天审核过了"的重复上架问题(sync_listing_state.py:3-7)。

## 8. 移植清单建议

**A. 必须逐行保真移植**（每一行都是错误码换来的，重写必然重踩坑）：
- `mapper.py` 全部校验/coerce 函数群（§4 表格 12 个函数，尤其 `force_overrides` 零认证矩阵、`fill_known_walmart_required` 不动点迭代、`_evaluate_if_condition` allOf 求值器、`_safe_default_for`）
- `feed_submit.py`：`build_feed` header 3 字段 + `_verify_feed_submitted` 三态反查 + `sanitize_feed_numbers`
- `reconcile.py:58-73` + `sync_listing_state.py:39-43` 错误码集合与分类优先级（reconcile.py:224-264）——建议灌入新系统 listing 错误码目录表
- `pt_spec.py` 按 PT 拆分加载策略（451MB OOM 教训）+ `field_summary`/`_summarize_prop`
- `mapper.py:40-106` SYSTEM/USER_TEMPLATE prompt（静态前缀 + 动态后缀的 cache 友好结构一并保留）
- config.py:78/97 `MP_ITEM_SPEC_VERSION` 完整时间戳、`SITE_END_DATE` DateTime 格式等"实测为准"常量

**B. 语义移植（逻辑保留，载体适配新架构）**：
- `main.py` Phase 0-3 编排 → 新系统任务流（飞书 26 列 → DB 状态机；行级 fail-fast、同店打包单 feed、多店并发骨架保留）
- `pending_feed_tracker.py`+`auto_reconcile.py` 闭环（JSON 文件 → DB 表；6h 延迟/168h 归档参数保留）
- `retry_state.py` 失败分类+阈值状态机（**SQLite → 新系统 DB**，铁律禁 SQLite 进生产路径；`classify` 的中文正则要换成结构化错误 kind）
- `llm_client.py`/`llm_cache.py`（AIMD+tenacity+输入哈希缓存设计保留；**API key 出 config 进配置中心**；模型选型按新系统决策）
- `rate_limiter.py`（设计保留，建议并入 walmart_client 层）
- `risk_gate.py`（fail-open 语义 + 24h 缓存保留；数据源从飞书表迁到新系统配置）
- `retire_and_relist.py` SKU_LOCKED 两阶段自愈、`quota.py`/`pricing.py` 业务规则（**参数进配置中心**，铁律 5）
- `sync_status_track.py`/`sync_listing_state.py` 状态对账语义（回写目标从飞书列改 DB 字段）
- `live_spec.py`（保留，作为本地快照过期的校准通道）

**C. 不移植**（一次性/旧载体专属）：
- `feishu_io.py`、`excel_io.py`、`dedup_sync_to_server.py`（飞书/Excel 载体层，新系统换 DB）
- `fix_end_date.py`、`fix_sheet_consistency.py`、`fix_upc_pool.py`、`mark_sku_locked_as_fail.py`、`resubmit_278.py`（一次性修复脚本；但其 docstring 里的事故记录值得进错误目录）
- `scheduler.py`+`launchd/`（macOS 专属，新系统用自己的调度）
- `dmit_client.py`（数据源按新系统选品/采集架构定）
- `notifier.py`（换新系统告警通道）
- `upc_pool.py` 飞书池实现（领号强一致语义保留——领取即标、失败按 VerifyResult 精确回收——但实现重写为 DB 事务）

**移植时须修复的已知债务**：DeepSeek/Qwen key 硬编码(config.py:62,66)；`quota.py:113` 无配置店铺默认放行 999；所有 state 文件假定单进程（README.md:374）；`rate_limiter` GET /v3/feeds/{feedId} 配置为 60/min 保守值非官方 5000/min（rate_limiter.py:132）。

---

## §2 新系统骨架现状（backend/src/erp/listing/ + R1-11 证据）

## 1. listing 模块现状（backend/src/erp/listing/）

`__init__.py` 为空（0 行）。四个实体文件共 1389 行，是**可跑通的最小闭环，但 spec 构建器是骨架级**。

### 1.1 spec.py（152 行）— 骨架/占位水平

- 头注自认骨架：`spec.py:1-8` "Item Spec v5 构建器（build 模式最小版；match 模式 R2）……本构建器只落骨架与已有属性"。
- WPT 来源链：`product.attrs.wpt` 显式指定 > `system_config 'listing.default_wpt'`（`spec.py:34-42`）；两者皆无 → `ERP_SPEC_BUILD_FAILED`（`spec.py:43-47`）。**category_map 自动映射未接**（`spec.py:6-7` 注明"随 R2 类目域接入"）。
- payload 结构硬编码（`spec.py:83-116`）：`MPItem = {Orderable: {sku/productIdentifiers(EAN)/productName[:199]/brand:"unbranded"/price/ShippingWeight/electronicsIndicator/batteryTechnologyType/chemicalAerosolPesticide/shipsInOriginalPackaging}, Visible: {<wpt>: {shortDescription[:4000]/mainImageUrl/productSecondaryImageURL[1:5]/keyFeatures[:5]}}}`——**Visible 段只有 4 个通用字段，完全不按 WPT schema 取必填属性**。
- Header 硬编码 `version: "1.5"`（`spec.py:107-114`）；`sellingChannel = "mpsetupbymatch" if match else "mpmaintenance"`（`spec.py:109`）——这是 offer_mode 在 payload 层的唯一体现。
- 缓存：`build_hash = sha256(title/brand/images/attrs/wpt/mode/spec_version)`（`spec.py:49-62`），命中 `app.listing_spec (product_id, offer_mode, build_hash)` 唯一键直接复用（`spec.py:64-80`）；SKU/GTIN/价格/库存是 listing 级参数经 `_instantiate()` 注入不进缓存键（`spec.py:142-152`，库存硬编码 `fulfillmentLagTime: 1`）。
- **无任何本地校验器**——payload 构完直接出门。

### 1.2 状态机（service.py，748 行）

- 状态枚举在 DB CHECK：`0009_listing.py:86-88` `('draft','queued','submitted','processing','published','live','degraded','delist_pending','delisted','failed','retired')`。
- 迁移唯一出口 `transition()`（`service.py:34-74`）：UPDATE listing + 同事务写 `listing_state_history`；published/delisted 顺带打时间戳（:46-49）；error_code 仅 failed/degraded 携带（:51）。
- 链路函数：
  - `allocate()` `service.py:100-237`：店铺 active 校验、pg_advisory_xact_lock 去重串行化（:127-129）、产品准入 `audit_passed/ready`（:150）、D-Q31 团队去重+店铺豁免（:159-179）、build 模式 GTIN 预占（:206-218）、初价取 `price_snapshot.list`（:181-184）。
  - `submit()` `service.py:243-447`：一 feed 一店一模式（:267-269 `FEED_MIXED_BATCH`）→ 配额 `listing_create` 原子扣减（:276-283）→ build_spec → 写 `feed`/`feed_item` → `gateway.request POST /v3/feeds`。分支：dry_run 返快照（:384-399）；传输异常/status=None → feed `verify_pending` 永不盲重试（:366-382, :401-415）；200+feedId → submitted（:417-435）；明确拒绝 → 配额返还+释放 GTIN+listing failed（:437-447）。
  - `poll_feed()` `service.py:461-583`：GET `/v3/feeds/{id}?includeDetails=true`，**item 级为权威**（headline 不可信，:506 注释）；SUCCESS → 回填 wpid → published → gtin mark_used → live（:527-542）；错误 → `_ensure_error_cataloged` 自动入字典草稿（:547, :586-601）→ 配额返还+GTIN 释放+failed（:562-564）。
  - `verify_back()` `service.py:607-685`：对账渠道近 10 条 feeds，feedType+itemsReceived 唯一匹配 → adopt（:633-664）；否则 lost → listing 回 queued+返配额（:665-685）。
  - `delist()` :691-721（RETIRE_ITEM feed）、`retry_failed()` :724-748（按 error_catalog disposition 闸，fatal/skip 拒重投 :738-739；build 重投重新占号 :740-745）。
- offer_mode 双模式体现：`FEED_TYPE_BY_KIND = {item_build: MP_ITEM, item_match: MP_ITEM_MATCH, delete: RETIRE_ITEM}`（`service.py:27-31`）；allocate 仅 build 占 GTIN（:206）；spec header sellingChannel 分流（`spec.py:109`）。**match 模式除 feedType/sellingChannel 外无差异化 payload（match 应为 identifier 匹配精简体，现在与 build 同构）**。

### 1.3 gtin.py（101 行）

GS1 校验位（:19-24）、批量导入逐条报告（:27-57）、`hold_one` 单语句 `FOR UPDATE SKIP LOCKED` 防双占（:60-78）、`mark_used` 终身绑定（:81-90）、`release` 仅 held 可归还（:93-101）。

### 1.4 router.py（388 行）

契约端点齐全：listings CRUD/allocate/submit/delist/retry、feeds list/get/items/poll/verify-back、gtin-pool stats/list/import。注记：submit/delist 契约 202 但试点期同步执行，worker 队列化随 R2（`router.py:3-4`）；poll 为手动触发，beat 自动轮询随 R2（:305）。

## 2. R1-11 证据（.agent/evidence/R1-11/）

目录只有 3 个文件：`local-verify.md`、`a152-live-runbook.md`、`dry-run-feed-snapshot.json`——**没有 archaeology.md**（网关的考古在 R1-07：`client.py:3-4` 引用 `.agent/evidence/R1-07/archaeology.md`）。

- **骨架范围**（local-verify.md:5-15）：六表 migration、GTIN 全生命周期、allocate/submit/poll/回写、verify-back、state_history 全链、未登记错误码自动入字典、failed 重投、dry-run 快照。75 tests passed ×2。
- **已知欠账 / 留给 R2 的钩子**（local-verify.md:26-28）：
  - WPT 来源链末端注明"category_map 映射 R2"；
  - 定价"R1 取 price_snapshot.list 原价直传；pricing_strategy 引擎 R2"；
  - A152 真实上架 1 SKU 验收 ⏸ 挂 Owner 机（= R2-03 验收②，specs/005 README:59 确认归属）。
- **关键教训**（local-verify.md:23-25）：gateway 吞传输异常返回 status=None，submit 必须当"结果未知"进 verify_pending，不能落"渠道拒绝"分支。
- dry-run-feed-snapshot.json：Drinkware 单品完整 MPItem v5 形态快照（GTIN/价格已实例化），是验收①的产物样例。
- a152-live-runbook.md：Owner 机 live_test 手册（is_test 勾选、gateway_mode 切换 SQL、Swagger 逐步操作、出错时错误码回传 agent 归类）。

## 3. listing 相关表（backend/alembic/versions/）

全部在 `0009_listing.py`（一单建齐 8 表）：

| 表 | 关键列 | 证据 |
|---|---|---|
| `app.gtin_pool` | gtin UNIQUE CHECK `^[0-9]{12,13}$`、gtin_kind(upc_a/ean_13)、status(free/held/used/conflict/invalid)、source、held_listing_id/held_at、used_listing_id/used_at、last_check_at、check_result jsonb、import_job_id | 0009:44-68 |
| `app.listing` | offer_mode(build/match)、channel_sku、gtin、status(11 态)、error_code、is_locked、wpid、channel_item_id、end_date default '2049-12-31'、current_price、currency、current_inventory default 5、published_at/delisted_at/last_synced_at/last_maintained_at、UNIQUE(store_id, channel_sku) | 0009:76-113 |
| `app.listing_state_history` | 月分区(occurred_at)、from_status/to_status/reason_code/detail jsonb/actor_type(user/system)/actor_id | 0009:121-142 |
| `app.feed` | channel_feed_id UNIQUE(可 NULL=verify_pending)、feed_kind(item_build/item_match/price/inventory/delete/lag_time)、status(building/submitting/verify_pending/submitted/processing/processed/partial/error/lost)、item_count、headline jsonb、submitted_at/last_polled_at/completed_at、poll_attempts、**raw_response_ref text（已预留、代码从未写入）** | 0009:148-174 |
| `app.feed_item` | 月分区(created_at)、feed_id/listing_id/channel_sku、status(pending/success/error)、error_code/error_msg、raw jsonb | 0009:182-210 |
| `app.listing_spec` | product_id/offer_mode/wpt/spec_version default 'v5'/payload jsonb/**cert_overrides jsonb（预留未用）**/build_hash、UNIQUE(product_id, offer_mode, build_hash) | 0009:216-234 |
| `app.listing_error_catalog` | **已建**：error_code PK、category、title、disposition CHECK(auto_retry/backoff_retry/rebuild_spec/skip/manual/fatal)、max_retries default 3、notes、enabled；RLS：任何人可读+可插草稿，改/删仅超管 | 0009:240-262 |
| `app.maintenance_task` | task_kind(price_sync/inventory_sync/title_fix/end_date_renewal/delist/relist/unlock_probe)、status、priority、scheduled_at | 0009:268-293 |

error_catalog 种子仅 5 条（0009:297-310）：ERP_SPEC_BUILD_FAILED、ERP_QUOTA_EXHAUSTED、ERP_GATEWAY_DRY_RUN、WM_ASYNC_REVIEW(backoff_retry×10)、WM_SKU_LOCKED。**渠道实战错误码尚未灌入**。

类目侧弹药表（R2-02 已建）：
- `refdata.category_map`（0015:22-32）：amazon_category+walmart_product_type PK、confidence、requires_certificate、zh_seller_forbidden、requirements；0016 补 amazon_leaf/browse_node_id/rank_no/match_type/source_batch 5 列（0016:45-48）。
- `refdata.pt_meta`（0016:52-66）：一行一 PT 元数据（access_state/zh_can_do/requirements/walmart_category 等）。
- `refdata.pt_spec`（0019:37-47）：**walmart_product_type PK、has_real_cert、real_cert_fields jsonb、has_soft_cert、soft_cert_fields jsonb、total_fields、required_count、required_fields jsonb**——官方 spec 认证/必填字段已有表位，是 R2-03 本地校验器的现成数据源。
- 导入器已存在：`backend/src/erp/tools/import_category_map.py`、`import_pt_meta.py`、`import_pt_spec.py`（+对应 tests/db/test_import_*.py）。

## 4. channel gateway 三模式（channel/gateway/client.py，333 行）

- 模式来源：`system_config 'channel.gateway_mode'`，非法值/缺省一律回落 `dry_run`（`client.py:186-198`）。可用 `mode_override` 参数强制（:235）。
- 闸门 `_enforce_mode`（:200-221）：`live_test` 仅 `store.is_test`（否则 `GATEWAY_STORE_NOT_TEST`）；`live` 还需 `system_config 'channel.live_enabled'="true"`（否则 `GATEWAY_LIVE_DISABLED`）。
- feed 提交唯一入口：`gateway.request(session, store_id, "POST", "/v3/feeds", endpoint_key="POST /v3/feeds:MP_ITEM|MP_ITEM_MATCH|RETIRE_ITEM", ...)`（`service.py:357-365`）。
- **dry-run 产物**：不发包，返回 `GatewayResponse(status=None, dry_run=True, request_snapshot={mode/store/method/url/endpoint_key/params/json_body/headers名单/proxy="<bound>"})`（`client.py:245-267`）。**快照只在响应体内联返回（submit 把它透传给 API 调用方，`service.py:398`），没有 `payload_ref` 落盘/落表机制**——feed.raw_response_ref 列存在但无人写。R1 验收的证据文件是测试代码手动写的（`tests/db/test_gateway.py:252-276` 写 `.agent/evidence/R1-07/dry-run-snapshot.json`；R1-11 的 snapshot 同理）。R2-03 dry-run 验收目前也只能走"调 submit → 收 request_snapshot → 人工/测试落档"这条路。
- 实战机制：按代理池化 AsyncClient+半死连接自愈（:128-141）、token 900s-60s 双检锁（:145-170）、401 就地刷新 1 次（:300-304）、429/5xx opt-in 退避（默认 max_retries=0，写路径不自动重试，:306-318）、限流闸 `registry.gate` + 响应头自适应（:270-272, :298；rate_limiter.py）。

## 5. 测试现状

listing 直接相关：
- `tests/db/test_listing_api.py`（458 行，8 例）：`test_import_20_ean13`(:154)、`test_allocate_dedup_and_gate`(:176)、`test_submit_live_test_then_poll_to_live`(:226)、`test_state_history_full_chain_and_delist`(:314)、`test_retry_failed_requires_disposition`(:347)、`test_no_response_never_blind_retry`(:364)、`test_verify_back_adopt`(:392)、`test_dry_run_snapshot`(:427)。渠道全程 MockTransport。
- `tests/db/test_gateway.py`（276 行，11 例）：三模式闸、token 复用、401 自愈、429 退避、GCRA 限流、header 自适应、dry-run 快照落证据。
- 弹药导入：`test_import_category_map.py`、`test_import_pt_meta.py`、`test_import_pt_spec.py`；L1 消费方 `test_l1_category.py`、`test_l1_rerank.py`。
- **零覆盖**：spec payload 字段级正确性（无按 WPT schema 的断言）、本地校验器（不存在）、AI 属性填写（不存在）、match 模式差异化 payload。

## 6. 缺口清单（对照 R2-03 范围，specs/005-r2-plan/README.md:36-43）

| R2-03 项 | 现状 | 缺什么 | 动刀位置 |
|---|---|---|---|
| **MPSetup v5 官方规格（按 WPT 取必填属性 schema）** | payload 硬编码 4 个 Visible 通用字段（spec.py:83-106）；`refdata.pt_spec.required_fields/required_count` 表已建（0019:37-47）且导入器齐（tools/import_pt_spec.py） | ① MPSetup v5 规格文件本体不在 ERP-ALL 仓（在 erpAPI 仓 `walmart_official_specs/MPSetup/`，需搬运/导入为 refdata）；② spec 构建器按 WPT 查 required_fields 动态出属性；③ header version "1.5" 需对照官方 v5 校准（spec.py:113） | `listing/spec.py` build_spec 重构；必要时新 `listing/wpt_schema.py`；migration 若 pt_spec 列不够（如属性类型/枚举约束）需增列 |
| **AI 属性填写** | 完全没有。LLM 基建现成：`audit/llm.py` LlmClient（chat/check_cache/call_provider/record_result，:137-）+ llm_cache/usage 记账 | 按 WPT 缺失必填属性组 prompt → LLM 补齐 → 写回 product.attrs 或 spec payload；走既有 cache_key/记账 | 新 `listing/attr_fill.py`（复用 `erp.audit.llm`——注意角色制，llm.py 在 audit 域，可能要提升到共享位置）；接线在 spec.py build_spec 前 |
| **category_map 接入 WPT 解析** | spec.py WPT 链只有 attrs.wpt > default_wpt（spec.py:34-42）；L1 已能直判出 wpt（l1_category.py:109- `run_l1` 返回 wpt），但 `l1_wpt` 仅在 audit run 内使用（audit/service.py:284,301,367），**不回写 product** | WPT 解析链加第三级：category_map/L1 结果；或 audit pass 时把 resolved wpt 持久化到 product.attrs.wpt | `listing/spec.py:34-47`（来源链）+ `audit/service.py:299-301`（回写 wpt，跨域需 audit owner 工单）；数据导入走 tools/import_category_map.py（Owner 机执行 6672+15771 行） |
| **提交前本地校验器** | 不存在；构完即提交（service.py:303-319），错误全靠渠道回报 | 按 pt_spec.required_fields + 类型/长度/枚举本地校验，缺项 → ERP_SPEC_BUILD_FAILED 类前置失败（省 MP_ITEM 10/hour 配额） | 新 `listing/validator.py`；调用点 `listing/service.py` submit 的 build_spec 之后（~:319）；失败复用既有 failed+配额返还分支（:313-318） |
| **listing_error_catalog 灌入渠道实战错误码** | 表+RLS+运行时自动草稿机制齐（0009:240-262；service.py:586-601）；种子仅 5 条（0009:297-310） | 渠道实战错误码批量灌入（源在 erpAPI 老系统/官方文档），含 category/disposition/max_retries 归类 | 新 `tools/import_error_catalog.py`（仿 import_pt_spec.py）或 seed migration；数据源需从 erpAPI 仓考古 |
| **验收①：dry-run ≥5 WPT 过官方 spec 校验** | dry-run 路可用但快照仅内联返回（client.py:245-267），无落盘位；仅 1 个 WPT（Drinkware）跑过 | ≥5 WPT 测试产品 + 官方 spec 校验脚本 + 证据落 `.agent/evidence/R2-03/`；可顺手把 request_snapshot 落 `feed.raw_response_ref`（列已预留，0009:166） | 测试 `tests/db/test_listing_api.py` 扩展或新 test_spec_v5.py；证据写入仿 test_gateway.py:252-276 |
| **验收②：A152 真调（R1-11 尾巴）** | runbook 已交付（a152-live-runbook.md），等 Owner 机执行 | 部署+凭证录入+is_test+live_test 切换后按 runbook 走 | 非代码项；错误码回传后补 error_catalog |

**附加风险注记**：① match 模式 payload 与 build 同构（spec.py:83-116 共用),官方 mpsetupbymatch 应为 identifier 精简体——R2-03 若只做 build 需在工单明确 match 豁免；② `listing_spec.cert_overrides` 列预留未用（0009:225），pt_spec.real_cert_fields 接入时是现成挂点；③ inventory `fulfillmentLagTime: 1` 硬编码（spec.py:151），违反"业务参数不写死"铁律，顺手配置化需提单。

---

## §3 图纸核对（specs/000/001/002/005/006）

核对范围：specs/000-founding、001-domain-model、002-api-contract、005-r2-plan、006-data-governance。所有行号为当前工作区文件实测。

---

## 1. 领域模型（specs/001-domain-model/）

listing 域集中在 `06-listing-pricing.md`，GTIN/类目/pt 在 `03-catalog.md`，import_job 在 `04-compliance.md`。

### 1.1 listing 刊登表 — `06-listing-pricing.md:6-42`

> `06-listing-pricing.md:3-4`：「决策依据：D-Q3（单管道双模式 offer_mode build|match）、D-Q9（End Date 统一 2049 + 自动续期）、D-Q23（跟卖定价独立成套）、D-Q25（默认库存 5）…总账铁律映射：feed 提交**永不盲重试**（verify-back）；headline 计数不可信（item 级权威）；SKU_LOCKED 单独处置；MP_ITEM 配额 10/h、PRICE_AND_PROMOTION 6/day（渠道网关 GCRA 层控制）。」

关键列（06:14-27）：
- `offer_mode TEXT NOT NULL CHECK IN (build, match)`「双模式共用本表与生命周期机器（D-Q3）」
- `channel_sku TEXT NOT NULL`「新品=master_sku（D1）；重拉存量=原 SKU」
- `gtin TEXT NULL`「build 必填（服务层校验）；match 跟卖无需自有 GTIN」
- `status` 11 态 CHECK：`draft, queued, submitted, processing, published, live, degraded, delist_pending, delisted, failed, retired`
- `error_code TEXT NULL`「终态 failed / degraded 的当前错误 → listing_error_catalog」
- `is_locked BOOLEAN DEFAULT false`「SKU_LOCKED：渠道锁定，维护类操作全部跳过」
- `end_date DATE NOT NULL DEFAULT '2049-12-31'`（D-Q9）；`current_inventory INT DEFAULT 5`（D-Q25）
- 唯一键 `uq_listing (store_id, channel_sku)`（06:30）；去重 D-Q31 服务层 advisory lock（06:32）

状态机原文（06:35-42）：
```
draft → queued → submitted → processing → published → live
processing → failed(error_code)          ← 渠道明确拒绝
live ⇄ degraded / live → delist_pending → delisted / delisted → retired（GTIN 不回收）
failed → queued                           ← 修复后重投（error 处置=auto_retry/manual）
```
全迁移写 `listing_state_history`（06:44-58，月分区，PK=(id, occurred_at)）。

### 1.2 feed / feed_item — `06-listing-pricing.md:60-93`

- feed（06:62-77）：`channel_feed_id TEXT NULL UNIQUE`「提交无响应时为 NULL → 走 verify-back，禁止直接重试」；`feed_kind CHECK IN (item_build, item_match, price, inventory, delete, lag_time)`「item_build=MP_ITEM(10/h)、item_match=MP_ITEM_MATCH、price=PRICE_AND_PROMOTION(6/day！必聚合)」；`status CHECK IN (building, submitting, verify_pending, submitted, processing, processed, partial, error, lost)`；`headline JSONB`「渠道汇总计数（**不可信**，仅展示；对账以 feed_item 为准）」；轮询节流 `/v3/feeds*` 5000/min（06:77）。
- feed_item（06:79-93）：月分区、年 7 千万行；`status CHECK IN (pending, success, error)`「item 级权威结果」；`error_code 入 listing_error_catalog 闭环`（06:89）；索引 `(error_code, created_at) WHERE status='error'`。

### 1.3 listing_spec 规格构建产物 — `06-listing-pricing.md:95-110`（R2-03 spec 构建器落点）

| 列 | 摘录 |
|---|---|
| offer_mode | 「双模式 spec 构建器不同（D-Q3 分叉点之一）」 |
| wpt | 「来自 category_map」 |
| spec_version | `TEXT NOT NULL DEFAULT 'v5'`「Item Spec v5」 |
| payload | `JSONB NOT NULL`「最终提交体（含变体组展开）」 |
| cert_overrides | `JSONB DEFAULT '{}'`「零认证覆盖记录（哪些合规字段被策略填充）」 |
| build_hash | 「输入指纹（product.attrs+映射+策略版本）；输入未变直接复用」 |

约束 `uq_listing_spec (product_id, offer_mode, build_hash)`（06:110）。

### 1.4 listing_error_catalog（R2-03 灌数据目标表）— `06-listing-pricing.md:112-125`

| 列 | 约束/说明（原文） |
|---|---|
| error_code | `TEXT PK`「渠道错误码或内部码（前缀区分 `WM_`/`ERP_`）」 |
| category | `NOT NULL`「内容/合规/GTIN/类目/限流/系统…」 |
| title | `NOT NULL`「中文名」 |
| disposition | `CHECK IN (auto_retry, backoff_retry, rebuild_spec, skip, manual, fatal)`「处置策略：worker 按此分派」 |
| max_retries | `INT DEFAULT 3` |
| notes / enabled / updated_by… | 「运营处置手册」 |

> `06:125`：「未登记错误码 → 默认 manual + 自动插入草稿行（category='未分类'）→ notification 提醒运营归类。**异步审核伪错误码**登记为 disposition=backoff_retry。」
> `00-conventions.md:56`：全局表（无 team_id）：「channel、category_map、**listing_error_catalog**、audit_policy、…、refdata.*」——错误码目录是全局字典，运营可维护（D-Q11，002:818）。

### 1.5 gtin_pool — `03-catalog.md:76-99`

- `gtin TEXT NOT NULL UNIQUE CHECK ('^[0-9]{12,13}$')`「全局唯一（跨团队也不允许重号）」；`gtin_kind IN (upc_a, ean_13)`；`status IN (free, held, used, conflict, invalid)`「conflict=渠道校验撞库（旧池 67% 教训）」；`source IN (generator_import, feishu_import, purchased)`（D-Q39）。
- 并发协议（03:97-99）：单语句 `FOR UPDATE SKIP LOCKED` 分配防双占；「held → used：listing 首次 published；held → free：上架终态失败释放；used **永不回收**（防跨店重用关联）」；水位告警默认 <15% warn、<5% critical。⚠️ 见 §5-g 警报。

### 1.6 category_map / pt_meta（D-Q55 修订版）— `03-catalog.md:101-141`

> `03:103-108` 修订注记：「旧设计（amazon_leaf_id 唯一 → 单 wpt + map_source + risk 5 维 + pt_embedding 向量召回）被"**映射表多候选 + LLM 语义复排**"取代…实际落库为 `refdata.category_map`（多候选）+ `refdata.pt_meta`（PT 元数据主表），迁移 0015/0016。」

- refdata.category_map（03:109-124）：键 `(amazon_category, walmart_product_type)`，列 confidence / requires_certificate / zh_seller_forbidden / requirements / notes / amazon_leaf / browse_node_id / rank_no / match_type / source_batch / updated_at（dataset_revision 触发器）。`'无对应Walmart PT'`=合法 unmapped 标记。
- refdata.pt_meta（03:125-128）：「键 walmart_product_type…walmart_category / walmart_ptg / access_state / zh_can_do / zh_seller_forbidden / requirements / notes / **total_fields / required_count / required_fields**。**L1 候选必须 INNER JOIN pt_meta 过滤废弃 PT**」。
- 03:130-131：「真数据已落库（2026-07-13）：category_map 15,987 + pt_meta 7,008。」
- 03:137：「**缺 WPT 只阻上架（listing 前置）**」，unmapped 不拦审核。

### 1.7 pt_spec 图纸现状（R2-03 核心缺口）

**001 领域模型中不存在 refdata.pt_spec 的列级设计。** 全 specs 内 pt_spec 仅出现于：
- `006-data-governance/README.md:36`：数据域清单一行「PT 元数据/规格 | `refdata.pt_meta` / `pt_specs` | 6832 / 变动 | 飞书 / 官方 MPSetup | 偶发 | L1 / 上架」——只有提名，无列设计。
- 旧系统 DDL（唯一列级参照）`000-founding/data-survey/out/pg_erp_core_schema.sql:1915-1928`：
```sql
CREATE TABLE public.walmart_pt_spec (
    walmart_product_type text NOT NULL,   -- PK (line 2617)
    total_fields integer,
    required_count integer,
    required_fields jsonb,
    real_cert_fields jsonb,
    has_real_cert boolean DEFAULT false,  -- partial index idx_pt_spec_cert (line 2911)
    soft_cert_fields jsonb,
    has_soft_cert boolean DEFAULT false,
    fields jsonb,                         -- ← 全量字段 schema，上架构建器需要的列
    synced_at timestamp with time zone DEFAULT now()
);
```
旧库实测 6,942 行（`pg_erp_core_rowcounts_exact.txt:52`）。即：审核子集列（total_fields/required_count/required_fields）图纸已并入 pt_meta；**`fields` 全量列、real_cert/soft_cert 四列（BR-AUD-006 零认证覆盖与 listing_spec.cert_overrides 的数据依赖）在新图纸中无家可归**——R2-03 必须补图纸（见 §5-a）。

### 1.8 import_job 通道 — `04-compliance.md:83-89`

> `04:89`：`domain TEXT NOT NULL CHECK IN (blacklist_brand, blacklist_seller, blacklist_asin, blacklist_category, tro, phishing, category_map, gtin, trademark, suspension_case, product)`「一 job 一目标域」

⚠️ 枚举无 `pt_spec`/`pt_meta`/`error_record`/`policy`，也无 listing 错误码域（见 §5-h）。

### 1.9 相关配套表（同文件，简列）

- maintenance_task（06:127-145）：task_kind 7 种含 `unlock_probe`（SKU_LOCKED 探测解锁）；skipped=is_locked 或配额不足。
- pricing_strategy（06:147-163，D-Q23）：`offer_mode` 双模式各配一套；`algo_code`：`cost_plus`（build 默认）/`manual`（match 现行）/未来 `follow_buybox`；活跃唯一 `uq (team_id, COALESCE(store_id,0), offer_mode) WHERE status='active'`。
- price_history（06:165-181）：「改价执行必须聚合成批」（06:180，PUT 100/h、feed 6/day）。

---

## 2. API 契约（specs/002-api-contract/openapi-v0.yaml）

### 2.1 Listing 段全部路径（632-836）与权限点

| 路径 | 方法 | x-permission | 要点（行号） |
|---|---|---|---|
| /listings | GET | listing.read | 筛选 store_id/status/offer_mode/error_code（633-644） |
| /listings/allocate | POST | listing.allocate | 「批量建 draft：产品×店铺×模式；含去重/品牌占用/GTIN 预占」；rejected code 示例 `LISTING_DUP_IN_TEAM, BRAND_OCCUPIED_OTHER_STORE, GTIN_POOL_EMPTY, PRODUCT_NOT_READY`（645-677） |
| /listings/{listingId} | GET | listing.read | 678-683 |
| /listings/submit | POST | listing.submit | 「批量提交上架（异步：构建 spec→组 feed→网关提交；消耗 listing_create 配额）」maxItems 500；422=配额不足（684-699） |
| /listings/{id}/delist | POST | listing.delist | 消耗 listing_delete 配额（700-706） |
| /listings/{id}/retry | POST | listing.submit | 「failed 重投（按 error catalog 处置校验）」（707-713） |
| /feeds、/feeds/{feedId} | GET | listing.read | 714-730 |
| /feeds/{feedId}/items | GET | listing.read | 「item 级权威结果（headline 不可信）」（731-741） |
| /feeds/{feedId}/poll | POST | listing.submit | 「手动轮询…beat 自动轮询 R2」（742-748） |
| /feeds/{feedId}/verify-back | POST | listing.submit | 「verify_pending 对账归位（adopt/lost）——提交无响应永不盲重试」（749-755） |
| /pricing-strategies (+/{id}) | GET/POST/PATCH | pricing.read / pricing.write | 「(team×store×offer_mode) 活跃唯一，D-Q23」（772-789） |
| /maintenance-tasks | GET/POST | listing.read / listing.maintain | task_kind 枚举与 001 一致（790-814） |
| /listing-errors | GET | listing.read | 「错误分类字典（运营可维护，D-Q11）」（815-820） |
| /listing-errors/{errorCode} | PATCH | **listing.error_admin** | 可改 category/disposition/max_retries/notes（821-836） |

配套：/gtin-pool GET（catalog.gtin_read，545-554）、/gtin-pool/import POST（catalog.import_write，「格式+GS1校验位校验；重复逐条报告」756-771）、/import-jobs（catalog.import_write/read，`dry_run` 参数在 569 行）、/category-map GET+PATCH（582-607，⚠️ 旧设计，见 §5-b）。

### 2.2 Schema（1499-1607）

`Listing`（1499-1513）、`ListingDetail`（+wpid/channel_item_id/end_date/state_history/price_history，1514-1539）、`Feed`（headline 注明「不可信，展示用」1553）、`FeedItem`（error_code/error_msg，1560-1568）、`ListingError`（=表结构 7 字段，1598-1607）、`PricingStrategy`（1573-1583）、`MaintenanceTask`（1584-1593）。

### 2.3 缺口

**契约中没有 listing dry-run / spec 预览端点**（全文件 `dry` 仅命中 import-jobs 的 dry_run:569）；也没有 pt_spec 查询端点。R2-03 验收①的 dry-run 形态无契约落点（见 §5-e）。

---

## 3. 决策约束（specs/000-founding/）

### 3.1 DECISION-FORM.md 相关决策原文

- **D-Q2**（:10）：「**变体组进 MVP**｜领域模型第一版就要含 variant_group / anchor 实体」
- **D-Q3**（:11）：「**跟卖 = 上架的一种运营模式**（参数不同，非独立系统）…listing 域设计为 `offer_mode ∈ {build(建品), match(跟卖)}` 单管道双模式；旧 match_listing 不再作为独立轨道移植。实现顺序：单品建品跑通后立即跟进」
- **D-Q3 补充**（:72）：「建品(build)/跟卖(match) 共用配额、定价、feed 提交轮询、生命周期状态机与维护机器；差异（输入/预检/内容构建/UPC 来源/变体/feed 类型）由 offer_mode 参数包承载；UI 双入口、数据带模式标记、权限可分授」
- **D-Q23**（:73）：「**跟卖定价规则与建品不同**：定价引擎共用，但策略集按 offer_mode 分套——build 模式默认策略 =（源价+运费）× 店铺区间倍数；match 模式独立策略集（现行 = 输入清单人工指定价…）」
- **D-Q9**（:27）：「End Date 统一远期 2049 + 到期自动巡检续期」；**D-Q10**（:28）：「配额中心三方向（上架/下架/维护）+ task_runs 精确计数」
- **D-Q25**（:114）：「跟卖默认库存 5…**找到货源才上架**」；**D-Q31**（:120）：「去重键 = (team_id, asin) + store 级豁免开关」
- **D-Q39**（:142）：「UPC 供给：Owner 有 **EAN-13 生成器**…本地生成后**导入 ERP**…条码域泛化为 GTIN 池（UPC-A 12 位与 EAN-13）…67% 冲突率问题由 EAN-13 号段缓解，校验流水线仍必须」
- **D-Q36**（:125）：「测试与验收店 = **A152**」
- **D-Q54**（:209）：「①每个工单必须标注「数据真实性等级」L0-L3…②R1 全部工单为 L0 骨架级…**A152 真调从 R1 验收移除**，挂到 R2「上架真实化」之后；③R2 前三单按 Owner 实测缺口排：采集引擎移植→审核弹药灌入→上架真实化…计划基线=specs/005-r2-plan/README.md」
- **D-Q55**（:215）：「③**L1 类目判定=映射表+LLM语义（Owner原方法），非向量嵌入**——主路径只需 category_map 数据+现有LLM，不引入embedding API；pt_embeddings向量召回降为可选后置增强」
- **D-Q58**（:233）：「L3 语义审核与 L1-b 类目复排的标准模型统一为 `deepseek-v4-flash`」——R2-03 AI 属性填写选模的既定基准。
- **D-Q59**（:239）：「同批完成 R3b NRTL 移植（**walmart_pt_spec** + nrtl 分类器，最后一块可移植缺口）」——即 R2-02 已把 walmart_pt_spec 以审核所需形态引入。
- **D1** 本表未单列：master_sku 决策记录在 PRODUCT-TEAM.md，由 ledger `BR-CAT-002`（business-rules-ledger.md:44）承载：「内部身份 = master_sku（`M{seq}` 渠道中立终身不变）…新上架渠道 SKU=master_sku；**存量 SKU=ASIN 只映射不迁移**（Walmart SKU 不可改）」。

### 3.2 business-rules-ledger.md 上架相关规则（R2-03 直接弹药）

- **spec 构建实测规则**：BR-LST-005（:110）「MP_ITEM v5 spec version 必须完整时间戳（`5.0.20260304-22_45_32-api`），裸 "5.0" 拒收」；BR-LST-006（:111）「Site End Date 必须 ISO DateTime…纯日期被拒（EXT_DATA_ERROR_00030257670757）」；BR-LST-007（:112）「productIdentifiers=单对象非数组；price=裸 number；inventory[].fulfillmentCenterID 必填=Partner ID（`GET /v3/settings/partnerprofile` 缓存）」；BR-LST-008（:113）「数值字段 ≤2 位小数（EXT_DATA_ERROR_68050064665065）」；BR-LST-009 keyFeatures per-PT minItems / manufacturer ≤60；BR-LST-011 备货 1 天 / mustShipAlone=No / 原产国=China；BR-LST-012 变体组 PT 一致+跨店归 leader；BR-CAT-005（:47）「上架商品强制品牌 = "Unbranded"」。
- **零认证覆盖** BR-AUD-006（:85）：「certification_type=`Neither of these applies`、has_nrtl_listing_certification/isProp65WarningRequired/has_written_warranty/isAssemblyRequired=`No`；**只对该 PT spec 存在的字段强制**；目标值不在 enum 时按安全序列回退；同时**清空**对应文档字段防 LLM 幻觉」——直接依赖 pt_spec.fields/cert 列。
- **错误码素材**（灌 listing_error_catalog 用）：BR-LC-004（:130）可重试集 `EXT_DATA_ERROR_72600149546850 / IB.VALIDATION.DATA.001 / ERR_OFFER_2020 / ERR_INT_SYS_01010010`；BR-LC-005（:131）SKU_LOCKED=`ERR_EXT_DATA_0101211`（24h 冷却重上链）；BR-UPC-002（:55）首位前缀拒收 `EXT_DATA_ERROR_54514906640101`；BR-LC-008（:134）「erp-core 错误分类体系：50+ errorCode → 8 类目×8 处置…未知码走关键词回退，最终 human_review」。
- **feed 纪律**：BR-GW-009（:23）feed 5xx/429 绝不盲重试三态判定；BR-LC-002（:128）「feed 提交后 **6h** 才首次 reconcile；**168h** 无终态归档」；BR-LC-006 headline 不可信；BR-LST-004 同店打包单 feed（10/h）；BR-LST-016 重试不占新增配额。
- **GTIN**：BR-UPC-005（:58）「**提交前失败**→回收；**提交后**（feed 已 POST，无论成败）→永不回收」；BR-UPC-006（:59）「同 ASIN/同 SKU 重上架**必须复用历史同一 UPC**；优先级：同 SKU 历史 > 同 ASIN 历史 > 池内新号」；BR-UPC-008 人工释放的 UPC 不回池。
- **match 模式**：BR-LST-015（:120）「仅 5 个 offer 字段…MP_ITEM_MATCH v4.2；提交前 SPEC 预检…**feed 本身带不了库存**，PROCESSED 确认后才推库存」。

---

## 4. R2 计划与数据治理

### 4.1 R2-03 原文 — `specs/005-r2-plan/README.md:36-42`

> 「### R2-03 上架真实化【L2】← 解决"spec 骨架撑不起真上架"
> - spec 构建器真实化：接 MPSetup v5 官方规格（按 WPT 取必填属性 schema）+ AI 属性填写（走 llm_cache/usage_log 既有记账）+ category_map 导入（6672+15771 映射）+ 提交前本地 schema 校验器；listing_error_catalog 灌入渠道实战错误码。
> - **验收（L2，分两档）**：①dry-run 产物通过官方 spec 校验（≥5 个不同 WPT 的产品）；②A152 真调 1 SKU → PROCESSED → live 回写 → Walmart 后台截图 → delist 收尾（即原 R1-11 尾巴，正式挂在此处）。」

另 `:59`：「A152 真调 → R2-03 验收②；sim_worker 保留为管道自检工具」。R1-11 原骨架范围见 `003-r1-plan/README.md:80-84`。

### 4.2 006 数据治理相关约定

- 数据域清单（006:35-38）：「类目映射 `refdata.category_map` 15771…消费方 审核 L1 / **上架 R2-03**」「PT 元数据/规格 `refdata.pt_meta` / `pt_specs` 6832/变动，来源 飞书 / **官方 MPSetup**，消费方 L1 / 上架」「错误商品记录 `app.error_product_record`…审核回测/反馈闭环」。
- 通道（006:57-59）：「批量文件导入 `import_job`（✅ 已建）…**待补域**：category_map、pt_meta/pt_specs、error_record。」（注：category_map 已由 DG1 补完，03-catalog:130-131。）
- 铁律（006:15-16、109）：「DB 是唯一 master，飞书是工作台/视图」「回写只动机器负责的列」；每行溯源 source/reason/added_by/added_at/import_job_id、软删不物删、版本失效缓存（006:115-118）。
- beat（006:79-81）为 R2-04 底座——R2-03 的 feed 自动轮询依赖它，R2-03 期间可用 /feeds/{id}/poll 手动轮询（002:742-748 注明「beat 自动轮询 R2」）。

---

## 5. 一致性警报（需随实现修订 specs 正文的点；specs 只由云端 AI 落笔）

- **(a) pt_spec 无列级图纸【R2-03 必须补】**：001 只设计了 pt_meta（审核子集列：total_fields/required_count/required_fields，03-catalog:125-128）；`fields` 全量列与 real_cert_fields/has_real_cert/soft_cert_fields/has_soft_cert 只存在于旧系统 DDL（data-survey pg_erp_core_schema.sql:1915-1928）和 006:36 的一行提名（`pt_specs`）。spec 构建器「按 WPT 取必填属性 schema」（005:37）与零认证覆盖（BR-AUD-006「只对该 PT spec 存在的字段强制」）都需要全量 fields。→ 修订 001 §03（或 refdata 章）补 `refdata.pt_spec` 列级设计。
- **(b) openapi /category-map 段是 pre-D-Q55 旧设计**：`582-607` 与 `CategoryMap` schema `1458-1468` 仍是 `amazon_leaf_id / wpt 单值 / risk / map_source / status(active|deprecated)`、PATCH「map_source=manual 优先级最高」——001 §03 已改为多候选 `(amazon_category, walmart_product_type)` 键 + pt_meta + match_type=ai_rerank 写回协议（03:103-141）。→ 修订 openapi-v0.yaml。
- **(c) 005 R2-03 范围含已完成项**：「category_map 导入（6672+15771 映射）」（005:38-39）已被 DG1 完成（03-catalog:130-131「真数据已落库（2026-07-13）：category_map 15,987 + pt_meta 7,008」，迁移 0015/0016）。→ R2-03 实际增量应改为 pt_spec fields 全量接入；005 正文待修订。
- **(d) 005 R2-02 段文字过时**（已验收关闭，仅正文陈旧）：「pt_embeddings 6832（L1 检索用）」「L1 类目判定（混合检索+LLM 复排）」（005:30-31）已被 D-Q55 取代（embedding 降级为可选后置增强）。
- **(e) dry-run 无契约落点**：R2-03 验收① 要求 dry-run 产物过官方 spec 校验，但 openapi Listing 段没有任何 dry-run/spec 预览端点（/listings/submit 直接消耗配额提交，002:684-699）。→ 需决定形态（如 POST /listings/dry-run、submit 加 dry_run 参数、或工具脚本不进契约）并修订 002。
- **(f) end_date 序列化陷阱**：001 `end_date DATE DEFAULT '2049-12-31'`（06:21）vs BR-LST-006「纯日期 yyyy-mm-dd 被拒（EXT_DATA_ERROR_00030257670757）」+ BR-RET-007 实战格式 `2049-12-31T00:00:00.000Z`。DB 列无冲突，但 spec 构建器必须转完整 ISO DateTime——建议 001 §06 listing_spec 段加注，防实现踩坑。
- **(g) GTIN 释放语义与总账裁决存在歧义**：001「held → free：上架终态失败释放」（03:98）未区分提交前/后；BR-UPC-005（生产裁决，C1 已收口）规定「feed 已 POST 后无论成败**永不回收**」、BR-UPC-008 人工释放标 release_failed 不回池。processing→failed 是提交后的终态失败，按 001 字面会误回收。→ 修订 001 §03 gtin_pool 状态协议：held→free 仅限「从未提交」的失败。另 BR-UPC-006「同 SKU/ASIN 重上必须复用历史同一 UPC（同 SKU>同 ASIN>新号）」在 001 分配协议中未体现（03:97 只有取 free 新号）→ 需补分配优先级。
- **(h) 错误码灌入通道无图纸**：import_job.domain 枚举（04:89）不含 listing 错误码域（也缺 pt_spec/pt_meta/error_record/policy——006:59 待补域）；005:39 只说「灌入渠道实战错误码」未指定通道（seed migration？import_job 扩域？运营 PATCH /listing-errors 逐条不现实）。→ 实现时定通道并同步修订 04 §import_job 枚举。
- **(i) disposition 枚举与旧系统处置体系需映射**：新 catalog 6 处置（auto_retry/backoff_retry/rebuild_spec/skip/manual/fatal，06:119）vs 旧 error_classifier 8 处置（BR-LC-008：retire/replace_image/reallocate_upc/resubmit_price/resubmit_inv/spec_rebuild/retry/human_review）。retire/replace_image/reallocate_upc/resubmit_* 无直接对应值（只能落 manual 丢失自动化语义，或扩枚举）。灌数据前需拍映射表；若扩枚举则 001+002 两处 enum 同步修订。SKU_LOCKED 的「RETIRE→24h 冷却→重上」链（BR-LC-005）在新模型由 is_locked+unlock_probe 承载（06:19、06:134），错误码 `ERR_EXT_DATA_0101211` 的 disposition 归属需明确。

**关键文件**：/home/user/ERP-ALL/specs/001-domain-model/06-listing-pricing.md、03-catalog.md、04-compliance.md、00-conventions.md、/home/user/ERP-ALL/specs/002-api-contract/openapi-v0.yaml、/home/user/ERP-ALL/specs/000-founding/DECISION-FORM.md、business-rules-ledger.md、data-survey/out/pg_erp_core_schema.sql、/home/user/ERP-ALL/specs/005-r2-plan/README.md、/home/user/ERP-ALL/specs/006-data-governance/README.md

---

## §4 上架规格素材盘点（/home/user/erpAPI）

## 1. walmart_official_specs/（本地 18M，确认为 T7 全量 4.3G 的**子集**）

**子目录结构**（关键结论：**本地没有 `MPSetup/`、没有 `MPMaintenance/`、没有 `MPSetup_by_pt/`** — 三个运行时依赖目录全部缺失）：

| 路径 | 大小 | 内容 |
|---|---|---|
| `walmart_official_specs/openapi/` | ~5.8M/20 文件 | 20 个模块 OpenAPI YAML（orders 2.7M、settings 723K、fulfillment 556K、items 334K…） |
| `walmart_official_specs/xsd_schemas/InventoryManagement/` | 3 文件 | Inventory/InventoryFeed/InventoryHeader.xsd（~1-1.7K 各） |
| `walmart_official_specs/xsd_schemas/OrderManagementV3/` | 5 文件 | PurchaseOrderV3.3.xsd (25K)、CommonComponentsV3.3.xsd (16K)、Cancel/Refund/ShipConfirm V3.3 |
| `walmart_official_specs/xsd_schemas/PriceManagement/` | 14 文件 | BulkPriceFeed.xsd (7.6K)、ItemFeedResponse.xsd (9.8K) 等 feed 响应 XSD |
| `walmart_official_specs/xsd_schemas/PriceJSON/` | 3 文件 | Price.json/PriceFeed.json/PriceHeader.json（JSON schema） |
| `walmart_official_specs/DELETE_ITEM/5.0.20250919-16_45_47-api_DELETE_ITEM.json` | 2.1K | 下架 feed JSON schema（draft-04，顶层 properties/required） |
| `walmart_official_specs/PricePromotion/Price&PromotionJSON/` | 5 文件 | 促销 feed JSON schema + curl/JSON 示例 |
| `walmart_official_specs/MP_ITEM_MATCH_v4.2.json` | 20K | 见下 |
| `walmart_official_specs/PT_Mapping.xlsx` | 493K | 见下 |
| `walmart_official_specs/MPSetup_FeedDiff.xlsx` | 289K | 见下 |
| `walmart_official_specs/Spec_5x_vs_4x_Diff.xlsx` | 11M | **文件损坏**：头是 PK（zip）但找不到 central directory，openpyxl/unzip 均报 BadZipFile，疑似截断——不可用，需从 T7 重拷 |

- **MP_ITEM_MATCH_v4.2.json**：完整可用的 JSON schema（draft-04）。顶层 `{$schema, type, title, properties:{MPItemFeedHeader, MPItem}, required, additionalProperties}`；`MPItem.items.required=["Item"]`，`Item.properties` 含 `sku`(1-50)、`productIdentifiers{productIdType∈[ISBN,GTIN,UPC,EAN], productId}`、`price`、`ShippingWeight`、`condition`(9 枚举)、`mainImageUrl`、`productSecondaryImageURL`、`productCategory`(枚举) 等 → **match 模式（offer_mode=match）校验器可直接用它**。
- **openapi 里 MP_ITEM 的定义**：`feeds` yml 只有 `MP_ITEM_PRICE_UPDATE`；`items` yml 的 `operationId: itemBulkUploads`（`POST /v3/feeds?feedType=...`）feedType enum 含 `MP_ITEM/MP_ITEM_MATCH/MP_MAINTENANCE/RETIRE_ITEM` 等，但 body 只是 `multipart file: binary` + **V4.8 示例**（header 还是 4.8 的 sellingChannel/processMode/subset 老格式）——**没有 v5 按 WPT 的属性 schema**。`POST /v3/items/spec` 在本地 20 个 YAML 里也**不存在**（grep 无命中）。
- **MPSetup v5 JSON schema（按 WPT 必填属性）**：**本地没有**。唯一存在于 T7：`MPSetup/5.0.20260304-22_45_32-api_MP_ITEM_0_0_en.json`（451MB monolith，split 脚本注释确认）。
- **PT_Mapping.xlsx**：sheet "PT Mapping" 6,681 行 × 16 列 —— Walmart 内部 Department 1-12 → Category → Sub Category(4.X Spec)（+疑似 PT 列）；Sheet1 是 101 行 PT 名单。是 4.x→部门映射参考，不是 v5 属性规格。
- **MPSetup_FeedDiff.xlsx**：sheet "Cover Page" + "Snapshot diff - 1"（列：Change#/Data Model/Change Type/Attribute Name/Old Value/New Value/Breaking Change?），记录 2025-11-18 → 2026-01-14 两个 spec 快照间各 PT Data Model 变更。是变更日志，非规格本体。

## 2. pt_templates/（836K，即 293M full 版的 summary）

| 文件 | 大小 | 结构 |
|---|---|---|
| `pt_templates/pt_templates_summary.xlsx` | 424K | Sheet1，**6,943 行**（6,942 PT + 表头）× 5 列：`Walmart Product Type / 字段总数 / 必填字段数 / 必填字段清单(管道分隔) / 核心字段(前20)` |
| `pt_templates/pt_templates_summary_sorted.xlsx` | 406K | 同上加 `Walmart Category / Walmart PTG` 两列（7 列，6,943 行） |

- **每行 = 一个 WPT**，含必填字段名清单（截断风险：单元格是 " | " 拼接的长字符串，但字段**名**列表完整，无类型/枚举/约束）。
- 生成链：`类目映射/active/extract_pt_templates.py` 从 T7 的 `MPSetup/5.0.20260330-14_47_14-api_MP_ITEM_0_0_en.json` 提取 → `pt_templates_full.json`（脚本注释 307MB，erp-core 注释 304MB，路径 `~/Downloads/pt_templates/`）→ summary。**full 版本地不存在**（`find` 全仓无命中）。
- summary 可回答"某 WPT 必填哪些字段名"，但**做不了类型/枚举/格式校验**。

## 3. 类目映射/（24M）

| 内容 | 大小 | 说明 |
|---|---|---|
| `data/mapping_detail_v5.5.xlsx` | 1.3M | sheet"映射明细" **15,771 行**（15,770 数据）× 11 列：Walmart Category/PTG/PT ← Amazon 叶子/路径/browse_node_id/排名/置信度/匹配方式/备注/来源批次；+"按Category汇总" |
| `archive_data/mapping_detail_v5.4.xlsx` / `v4.xlsx` | 1.2M / 623K | 历史版本回滚保险 |
| `data/PT风险5维度_v2.xlsx` | 21K | 6 sheets：预警清单/中国卖家禁售(61)/品牌锁定/禁售高发/受限需审批/知产高危，PT 粒度 |
| `data/pt_templates_summary.xlsx` | 425K | 与 pt_templates/ 同款副本 |
| `intermediate/policy_crawl_20260611/` | ~18M | 44 条禁售政策 HTML+TXT（合规素材，与规格无关） |
| `pipeline/` `active/` `legacy/` | ~250K py | 映射生成 5 阶段流水线脚本 |

**与 ERP-ALL category_map 15,987 行的关系**：ERP-ALL 0015 迁移注释写明源是 walmart-audit-system db 的 `walmart_category_map`（列级保真移植），即 v5.5 的 15,770 行 + 审计库额外 ~217 行（多版本/增补）。本目录是**上游生产地**，ERP-ALL 已导入的即其落库版；xlsx 是可复核的原始副本，行数差异需在验收时对账，非阻塞。

## 4. walmart_specs/（3.9M）

- `walmart_specs/all_product_types.json`（2.3M）：`{total:6942, unique:6942, product_types:[6942 个 PT 名字符串], category_map:{24 个 Category → [{productTypeGroup, productType, description}]}}`。**PT 名合法性校验 + Category/PTG 归属可直接用**。
- `walmart_specs/taxonomy_v5.json`（1.7M）：`{status:"OK", itemTaxonomy:[24 个 category → productTypeGroup[] → productType[{productTypeName, description}]]}`，即官方 taxonomy API 原始响应。**无属性定义**。

## 5. auto_listing 运行时 spec 依赖（旧系统实际加载什么）

`auto_listing/config.py:70-88` 硬编码三个数据源：

| 配置 | 指向 | 本地有？ |
|---|---|---|
| `PT_TEMPLATES_PATH` | `pt_templates/pt_templates_full.json`（307M） | **无**（仅 `update_listed.py` ad-hoc 用） |
| `MP_ITEM_SPEC_PATH` | `walmart_official_specs/MPSetup/5.0.20260304-22_45_32-api_MP_ITEM_0_0_en.json`（451M） | **无** |
| `MP_MAINTENANCE_SPEC_PATH` | `walmart_official_specs/MPMaintenance/5.0.20260304-22_45_32-api_MP_MAINTENANCE_0_0_en.json` | **无** |

关键机制（照抄价值高）：
- **`auto_listing/pt_spec.py`**：不直接 load 451M monolith，而是读 `walmart_official_specs/MPSetup_by_pt/` 拆分目录（`_pt_index.json` ~10K / `_orderable.json` ~25K / `{PT}.json` 10-100K each），由 `tools/split_mp_item_spec.py` 用 ijson 一次性拆出（451M→按 PT 小文件，内存 1.3G→KB 级）。**该拆分目录本地也不存在**。提供 `field_summary(pt)` 给 LLM、`get_variant_attribute_enum(pt)` 变体枚举。
- **`auto_listing/live_spec.py`**：`POST /v3/items/spec`，body `{feedType:"MP_ITEM", version:"5.0.20260304-22_45_32-api", productTypes:[≤20]}`，**限流 10/min（官方 rate_limits.tsv 第 82 行 "Get Spec 10/min"；脚本注释保守写 3/min）**，返回 `{"schema": <与本地 MP_ITEM 同构>}`。`main.py:904-921`：默认走本地 snapshot，`--live-spec` 时分 20 个/chunk 拉取并 merge 后 `inject_live_spec()` 覆盖。version 字符串是实测 Walmart 当前接受的值（listing_config.py:52 注释）。
- **`auto_listing/feed_submit.py:88-110`**：v5 feed header 实测只能 3 字段 `{businessUnit:"WALMART_US", locale:"en", version:完整时间戳}`——官方 sample 的 7 字段格式会被拒（错误码 EXT_DATA_ERROR_72600149546850/60670554076755/74597363510508），`submit_feed(dry_run=True)` 已有 dry-run 落盘不提交路径。
- **erp-core 新系统**：`erp-core/backend/app/services/audit/sync/sync_pt_specs.py` 也硬编码 `~/Downloads/pt_templates/pt_templates_full.json`（304M, 6942 PT）灌 `walmart_pt_spec` 表——同样卡 full 文件。

## 6. 结论：R2-03 本地校验器规格数据源方案排序

**本地素材不足以独立支撑 ≥5 WPT 的完整 v5 属性校验**（缺 MPSetup monolith / MPSetup_by_pt / pt_templates_full 三者任一），但**方案 A 无需等 Owner**：

1. **方案 A（推荐，可立即做）：Walmart Get Spec API 实时拉 + 落盘缓存**。照抄 `auto_listing/live_spec.py`（POST /v3/items/spec，官方限流 10/min、≤20 PT/次，version=`5.0.20260304-22_45_32-api`，A152 店铺凭证走 walmart_client）。5 个 WPT 一次调用就齐，返回结构与 MPSetup monolith 同构，拉下来即可按 `pt_spec.py` 的拆分格式落盘为本地校验器数据源，天然满足 D-Q37 的 A152 实测证据。旧系统 `--live-spec` 就是这条路，且旧系统实测本地 snapshot 常过期、live 才是权威。
2. **方案 B（并行请求，中期正解）：Owner 从 T7 补** `MPSetup/5.0.*_MP_ITEM_0_0_en.json`（451M）→ 本地跑 `tools/split_mp_item_spec.py` 生成 `MPSetup_by_pt/`；顺带补 `pt_templates_full.json`（304M，erp-core sync_pt_specs 也在等它）和完好的 `Spec_5x_vs_4x_Diff.xlsx`（本地副本已损坏）。全量 6,942 PT 离线校验 + pt_spec 表灌库都靠它。
3. **方案 C（兜底/辅助，纯本地）**：`pt_templates_summary_sorted.xlsx`（6,942 WPT 必填字段**名**）+ `all_product_types.json`（PT 名合法性）+ `MP_ITEM_MATCH_v4.2.json`（match 模式完整 schema）。build 模式只能做"必填字段是否齐"的弱校验（无类型/枚举/长度），match 模式反而可完整校验。可作为方案 A 拉取失败时的降级层。

另需带走的实测坑（校验器必须编码）：header 只 3 字段、version 必须完整时间戳、`Site End Date` 必须 ISO DateTime 而非 yyyy-mm-dd（EXT_DATA_ERROR_00030257670757）——见 `auto_listing/feed_submit.py:94-104`、`auto_listing/config.py:97-100`。

---

## §5 可复用基建接口速查（backend/）

## 1. LLM 基建 — `backend/src/erp/audit/llm.py`

### 三段原语（RS-03a：HTTP 期间不得持有事务/行锁）
单例：`llm_client = LlmClient()`（llm.py:341）。

**check_cache**（llm.py:148-159，事务内，短）：
```python
async def check_cache(self, session, *, key: str, model: str, team_id: int | None = None,
    object_type: str | None = None, object_id: int | None = None,
    cacheable: Callable[[str], bool] | None = None, module: str = "audit") -> str | None
```
- 命中记 usage(cost=0, cache_hit=true)；命中存量坏行（不过 `cacheable`）→ DELETE 驱逐返 None（llm.py:167-172）

**call_provider**（llm.py:189-196，**无任何 DB 交互**，必须在事务外调）：
```python
async def call_provider(self, *, model: str, messages: list[dict], temperature: float = 0.0,
    max_tokens: int = 1200) -> tuple[str, int, int, int]  # (content, prompt_tk, completion_tk, cached_tk)
```
- 强制 `response_format={"type":"json_object"}`（llm.py:224）；空响应重试 1 次后抛 `LLM_EMPTY_RESPONSE`；失败抛异常由调用方 fail-closed 落痕
- key 走 `Settings.llm_api_key`（环境 `ERP_LLM_API_KEY`），缺失抛 `LLM_KEY_MISSING`（llm.py:205-206）

**record_result**（llm.py:250-265，新事务）：
```python
async def record_result(self, session, *, key, model, content, prompt_tokens, completion_tokens,
    team_id=None, object_type=None, object_id=None, cacheable=None,
    module="audit", cached_tokens=0) -> float  # 返回 cost_usd
```
- 计价 + （过 `cacheable` 才）写缓存 + usage 行

**chat**（llm.py:293-306）= 三段单事务组合 → `(response_text, cost_usd, cache_hit)`，保留给非锁敏感调用方与测试。

### 缓存键（llm.py:28-41）
```python
cache_key = sha256(json.dumps({"model","messages","temperature","max_tokens"}, sort_keys=True))
```
「不缓存产品，缓存输入」——产品内容变了 messages 自然变。命中路径 = 单条 `UPDATE hit_count+1 RETURNING`（原子，llm.py:44-56）。

### module 机制 — **R2-03 需要新迁移扩 CHECK**
`0008_audit_compliance.py:208-210`：
```sql
module text NOT NULL CONSTRAINT ck_llm_usage_module
  CHECK (module IN ('audit','category_map','mail_classify','other'))
```
文档同源：`specs/001-domain-model/05-audit.md:82`。**无 `listing` 值**——R2-03 AI 属性填写要么暂用 `other`（不推荐，破坏归因），要么加 `'listing'`：新迁移 DROP/ADD `ck_llm_usage_module` + 同步改 05-audit.md。迁移归 **ar 帽**（`.claude/agents/ar.md:5`「全队唯一有权动 migration 的角色」）。

### JSON 容错
- `parse_json_object(raw_text) -> Any | None`（`audit/pipeline.py:461`）：①直接 loads ②\`\`\`json 栏内正则 ③首个 balanced `{...}` 逐候选试解析；全败 → None（调用方 fail-closed）。
- **不存在独立的 `strip_json_fences` 函数**（仅 service.py:373 注释提及），一律用 `parse_json_object`。

### 模型配置模式（复制 category_map.rerank 的做法）
`audit/l1_rerank.py:31-47`：
```python
_RERANK_DEFAULTS = {"model": "deepseek-v4-flash", "temperature": 0.0, "max_tokens": 400}
# system_config 'category_map.rerank' JSON {model, temperature, max_tokens}，逐键覆盖默认
row = SELECT value FROM app.system_config WHERE key = 'category_map.rerank'
cfg.update({k: row[k] for k in ("model","temperature","max_tokens") if k in row})
```
标准模型=deepseek-v4-flash（D-Q58）。R2-03 建议对称加 `listing.attr_fill` 键。单价表 `llm.pricing`：`{model: {input_per_1m, input_cache_hit_per_1m, output_per_1m}}`（llm.py:83-98）。

## 2. refdata.pt_spec 现状 vs 源表

**0019 迁移建表**（`alembic/versions/0019_pt_spec_r3b.py:37-47`）9 列：
`walmart_product_type PK · has_real_cert · real_cert_fields jsonb · has_soft_cert · soft_cert_fields jsonb · total_fields · required_count · required_fields jsonb · updated_at`

**源表 walmart_pt_spec 全量**（`specs/000-founding/data-survey/out/pg_erp_core_schema.sql:1915-1926`，6,942 行）：上述之外还有 **`fields jsonb`（全字段定义——R2-03 spec 构建器/AI 属性填写正需要的列）** 和 `synced_at`（有意去掉，溯源走 import_job）。

**补 `fields` 列 = 需要新迁移（ar 帽）**，模式抄 0019：CREATE/ALTER + `_domain_check()` 无需改（domain 'pt_spec' 已在）。import 侧改 `_apply_pt_spec_row` 的 INSERT 列表即可——当前明确忽略 fields（import_pt_spec.py:13 注释「源表 fields/synced_at 列忽略——审核只需 cert 与统计列」；import_service.py:648）。

## 3. import_service 通道 — `backend/src/erp/compliance/import_service.py`

**已支持 domain**（:75-82）：`blacklist_brand/seller/asin/category`（`_DOMAINS` 表驱动）+ 独立代码路径常量 `TRADEMARK_DOMAIN / POLICY_DOMAIN / CATEGORY_MAP_DOMAIN / PT_META_DOMAIN / PT_SPEC_DOMAIN`。

**核心 API**：
```python
create_job(session, *, domain, source_name, total_rows, fmt="csv", source_kind="file",
           chunk_size=5000, team_id=None, created_by=None) -> dict   # :121
import_rows(session, *, job_id, rows) -> {status,total,ok,skip,err,verify}  # :176
mark_failed(session, *, job_id, error)                                # :724
```
守卫：声明 total_rows ≠ 实收 → `IMPORT_ROW_COUNT_MISMATCH`（:205-214）；逐块处理数≠块行数 → 该块 failed（:290-300）。

**加新 domain 标准四步**（照 PT_SPEC 抄）：
1. 迁移扩 `ck_import_job_domain` CHECK（模式：0019 `_domain_check()` :24-31，DROP+ADD 全量枚举）——ar 帽
2. `import_service.py` 加常量 + 入 `SUPPORTED_DOMAINS` + 写 `_apply_xxx_row(session,row,line,c)`（幂等 ON CONFLICT 恒更新；err 仅键缺失）+ `import_rows` 内 apply_row 分派（:225-239，显式 elif，勿隐式回退）
3. CLI：`erp/tools/import_xxx.py` 抄 `import_pt_spec.py`（jsonl/csv/xlsx 读取 `_read_rows` → `system_tx` 内 create_job → import_rows → 异常 mark_failed；`python -m erp.tools.import_xxx --file …`）
4. `tests/db/test_import_xxx.py`

注意：`listing_error_catalog` 表已存在（0009_listing.py:240-262，列 error_code PK/category/title/disposition CHECK IN ('auto_retry','backoff_retry','rebuild_spec','skip','manual','fatal')/max_retries/enabled），且运行时有自动草稿插入 `_ensure_error_cataloged`（listing/service.py:586-601，未登记码→disposition=manual）；错误码字典批量灌入走 import 新 domain 即可，表不用动。

## 4. ConfigService — `backend/src/erp/core/config_service.py`

```python
ConfigService(session_factory, ttl_seconds=60.0)
await get(key, *, team_id=None, default=None)   # team_config > system_config > default（D-Q11，:31-37）
await set_system(key, value, *, updated_by=None)
await set_team(team_id, key, value, *, updated_by=None)
invalidate()
```
- 进程内缓存 TTL 60s；写立即失效本进程
- **现存两种读法**：ConfigService（带 team 覆盖）与服务层直读 `SELECT value FROM app.system_config WHERE key=…`（gateway、l1_rerank、spec.py 均直读——全局无 team 语义的键这样也合规）。R2-03 定价/开关：团队可覆盖的走 ConfigService；全局的两者皆可。既有键参照：`listing.default_wpt`（spec.py:38-41）、`channel.gateway_mode` / `channel.live_enabled`、`category_map.rerank`、`llm.pricing`、`llm_budget_daily_usd`（team_config）。

## 5. channel gateway — `backend/src/erp/channel/gateway/`

**唯一入口**（client.py:224-237）：
```python
await gateway.request(session, store_id, method, path, *, endpoint_key=None,
    json_body=None, params=None, max_retries=0, mode_override=None) -> GatewayResponse
# GatewayResponse(status, headers, data, dry_run, request_snapshot)  :47-53
```
（无 safe_get/safe_post 之名——源仓那对函数合并为 `request()`；失败传输错误返回 status=None，429/5xx 退避是 opt-in `max_retries`，写路径默认不自动重试。）

**三模式闸**（client.py:186-220）：`system_config channel.gateway_mode ∈ {dry_run, live_test, live}`，默认 dry_run；live_test 仅 `store.is_test`（A152）；live 需 `channel.live_enabled='true'`。dry_run 返回 `request_snapshot`（=验证纪律的 dry-run 证据）。

**feed 提交现状**（listing/service.py）：`submit()` :243 → spec 构建 → `gateway.request(POST /v3/feeds, endpoint_key=f"POST /v3/feeds:{feed_type}")` :356-365；feed_type 映射 `FEED_TYPE_BY_KIND = {"item_build":"MP_ITEM","item_match":"MP_ITEM_MATCH","delete":"RETIRE_ITEM"}` :27-31；无响应→verify_pending 走 `verify_back()` :607，**永不盲重试**。

**GCRA 限流**（rate_limiter.py）：配置在**代码内字典** `WALMART_ENDPOINT_LIMITS`（:31-50），键=endpoint_key：
```python
"feeds:PRICE_AND_PROMOTION": RateLimit(6, 86400),   # 6/day
"POST /v3/feeds:MP_ITEM": RateLimit(10, 3600),      # 实测校正，非官方 20/min
"GET /v3/insights/performance/*": RateLimit(1, 60), # 通配前缀
"_default": RateLimit(120, 60),
```
维度 (store, endpoint) 每桶独立；`registry.gate(store_key, endpoint, max_wait=None)`（默认 period×1.1，至少 60s）；响应头自适应 `x-current-token-count=0` + `X-Next-Replenishment-Time` 推后（:78-106）。进程内实现，多 worker 换 Redis backend 预留 R2。

**凭证**：`channel/service.py:55-69 decrypt_credential(session, store_id) -> (client_id, client_secret)`，pgcrypto `pgp_sym_decrypt(client_secret_encrypted, get_settings().credential_key)`；「仅渠道网关调用；调用方必须记 audit」。代理密码同法（client.py:88-90）。

## 6. 审计 / 权限

**audit_log 唯一出口**（`core/audit.py`，D-Q16，禁止直接 INSERT）：
```python
writer = AuditWriter.for_user(session, user, request)   # :47  或 AuditWriter(session, actor_type="system", ...)
await writer.log(action, object_type, object_id, *, before=None, after=None)  # :59
```
与业务写同事务（回滚同回滚）。listing 状态迁移另有专用 `transition()`（listing/service.py:34-74，写 listing_state_history）。

**权限点**（`core/authn.py:128-136`）：
```python
user: Annotated[CurrentUser, Depends(require_permission("listing.submit"))]
```
listing 权限种子已有（0002_identity.py:284-289）：`listing.read / listing.allocate / listing.submit / listing.delist / listing.maintain / listing.error_admin`——错误字典维护点现成。

## 7. 测试基建

**真库测试**（`tests/db/conftest.py`）：
- PG 不可达 → 整目录 module-level skip（:41-42）；环境变量 `ERP_MIGRATOR_DATABASE_URL`（DDL）/ `ERP_DATABASE_URL`（erp_app 受 RLS）
- fixtures：`migrated_db`（session 级 alembic upgrade head + 建测试团队A/B）→ `team_ids` → `app_conn`（erp_app 连接，「用例内自行 SET app.current_team」）
- 数据用 PREFIX 前缀隔离 + module 级 seed/wipe（见 test_l1_rerank.py:56-61）

**fake LLM 注入**（test_l1_rerank.py:48-53，R2-02 模式）：
```python
@pytest.fixture()
def fake_llm() -> _FakeLlm:
    fake = _FakeLlm()
    llm_client._transport_factory = lambda: httpx.MockTransport(fake.handler)
    yield fake
    llm_client._transport_factory = None
```
handler 返回 OpenAI 兼容 `{"choices":[{"message":{"content":…}}],"usage":{…}}`；`_FakeLlm.script` 按序出脚本、记 `calls` 数。gateway 同样有 `_transport_factory` 注入点（client.py:75）。

## 8. CI 门禁

- **Makefile**：`make lint` = `cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy`；`make test` = `uv run pytest`
- **CI**（`.github/workflows/ci.yml` backend job）：pg16 service（pgvector/pgvector:pg16，postgres/postgres/erp_all）+ env 两个 DB URL → `uv sync --frozen` → ruff check + format --check → `uv run mypy`（**strict**，pyproject:55-61）→ `uv run pytest` → **migration 空库演练 upgrade head / downgrade base / upgrade head**（新迁移必须可降级）
- pytest：`asyncio_mode = "auto"`（pyproject:63-66）；ruff line-length 100，per-file-ignores 见 pyproject:50-53
- 本地 db 测试：起 compose db（`make up`）或任何可达 PG，角色 erp_migrator/erp_app 由 pg-init 预建

## R2-03 落地要点（结论）

| 事项 | 结论 |
|---|---|
| AI 属性填写 module | 需 ar 帽新迁移扩 `ck_llm_usage_module` 加 `'listing'`（现仅 audit/category_map/mail_classify/other），同步改 specs/001-domain-model/05-audit.md:82 |
| pt_spec 补 `fields` 列 | **需新迁移**（ar 帽，列不存在，import 通道改不了 DDL）+ 改 `_apply_pt_spec_row` INSERT 列表 + import_pt_spec.py 文档注释 |
| listing_error_catalog 灌入 | 表已在（0009），加 import domain `listing_error_catalog`（四步法）即可，权限点 `listing.error_admin` 已种 |
| 模型/定价参数 | 抄 `category_map.rerank` 模式落 `system_config`（如 `listing.attr_fill` = {model,temperature,max_tokens}），团队可覆盖的走 ConfigService.get |
| feed/LLM 写路径纪律 | LLM 走三段原语（HTTP 不持锁）；渠道走 gateway.request（默认 dry_run 出证据快照）；PRICE_AND_PROMOTION 6/day 已在 `WALMART_ENDPOint_LIMITS`（rate_limiter.py:33），新端点在该字典加键即可 |

---

