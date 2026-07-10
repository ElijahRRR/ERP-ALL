# 业务规则总账（Business Rules Ledger）v1.2

- 日期：2026-07-08（v1.0）/ 2026-07-09（v1.1 外围五仓；v1.2 类目映射 + walmart-audit-system 考古）· 阶段：R0 需求考古 · 负责：PM 帽（工单 REF-R0-001/002）
- 来源：13 个零散模块 + erp-core 草稿 + CLAUDE.md/README 全量考古 + **外围五仓**（amazon-scraper-v3 / walmart-scraper / tro-scraper-matrix / trademark-data / walmart-trademark-sync）+ erpAPI main `a4a2999`（lark_io/scraper_client/store_status），每条规则标注源码位置
- 用途：从零重写的**唯一需求基准**。任何新系统行为与本账冲突时，要么改代码、要么改本账（经 Owner 批准），不允许静默偏离
- 标记：✅ 生产验证 · 🧪 已实现未上产 · 🚧 设计存在未启用 · ⚠️ 双版本冲突（见 §16）
- 编号：`BR-<域>-<序号>`，域缩写见各节标题

---

## 1. GW — 渠道网关（Walmart API 接入）

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-GW-001 | 所有 Walmart API 调用必经统一客户端；**禁止直连、禁止自实现认证/代理**。每个卖家账号绑定固定出口代理 IP，直连触发店铺关联风险（封店级后果） | ✅ | CLAUDE.md、walmart_client.py |
| BR-GW-002 | access_token 按 client_id 缓存，900s 内复用，提前 60s 过期；跨接口共用 | ✅ | walmart_client.py:213-255 |
| BR-GW-003 | 401 自愈：清缓存 → 用缓存的 secret 就地刷新 token → 重试 1 次（独立于重试配置） | ✅ | walmart_client.py:374-393 |
| BR-GW-004 | 代理 URL 中用户名/密码必须 URL 编码（防 @:/# 破坏 URL）；协议 socks5/http/https，未知回退 http | ✅ | walmart_client.py:187-194 |
| BR-GW-005 | GET 默认自动重试 2 次；**POST 默认不重试**（非幂等，防 feed/退款/发货重复提交），需要重试必须业务层保证幂等 | ✅ | walmart_client.py:472-497 |
| BR-GW-006 | 429 退避优先级：`Retry-After` > `X-Next-Replenishment-Time`（兼容 epoch-ms 与 ISO8601）> 默认 60s；等待上限 300s | ✅ | walmart_client.py:274-310 |
| BR-GW-007 | 传输层异常（TransportError/ProxyError）→ 主动废弃该代理的连接池，下次建新连接（SOCKS 半死自愈） | ✅ | walmart_client.py:111-125,351 |
| BR-GW-008 | 每次响应读 `x-current-token-count` / `x-next-replenishment-time` 做自适应限流微调 | ✅ | auto_listing/rate_limiter.py、erp-core rate_limiter.py |
| BR-GW-009 | **feed 提交遇 5xx/超时/429 绝不盲重试**：先反查最近 feed（按 itemsReceived+时间窗匹配）；三态判定 + 30s 二次确认；高置信"Walmart 未收到"→ 回收 UPC + 跳过回写 | ✅ | auto_listing/feed_submit.py（2026-05-04/05-29） |
| BR-GW-010 | 全局 socket 默认超时 90s，兜底 socks5 代理下 httpx timeout 失效（实测 SSL read 卡 2.5h） | ✅ | walmart_client.py:43 |
| BR-GW-011 | 速率限制硬表（写脚本前必查）：MP_ITEM feed **10/h**·DELETE_ITEM **10/h**·MP_MAINTENANCE 30/min·PRICE_AND_PROMOTION **6/day**·inventory feed 50/h·PUT /v3/price **100/h**·PUT /v3/inventory 200/min·GET /v3/items 带参 **60/min**(无参300)·catalog search 200/min·items/spec 3/min×20PT·GET /v3/returns **50/min**·Insights 全系 **1/min**·feeds 状态查询 5000/min 共享 | ✅ | docs/walmart_rate_limits.tsv、各模块 README |
| BR-GW-012 | feed 配额是**店铺级共享通道**：大批量 DELETE 当天避开价格批量同步 | 📖 | 沃尔玛批量下架/README |

## 2. ST — 店铺与凭证

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-ST-001 | 店铺表过滤：ClientId 为空或"0"→ 跳过；代理类型/IP/端口任一为"0"→ 跳过（无代理绝不放行直连） | ✅ | walmart_client.load_stores |
| BR-ST-002 | 生产店铺规模 ~57 家；店铺状态（sellerStatus）每小时同步 | ✅ | 各模块、commit 6ef1ec0 |
| BR-ST-003 | 仅 `sellerStatus == ACTIVE` 店铺参与自动清理等写操作；非 ACTIVE 店在日报中标"不可售" | ✅ | 问题商品清理/日报 README |
| BR-ST-004 | stockzero 特殊店（配置表"库存特殊要求=0"，当前 14 家）：跳过标题/价格维护；库存一律强制清零（已为 0 的行整行跳过）；名单每次运行时从配置表实时重读 | ✅ | 沃尔玛商品维护 README |
| BR-ST-005 | 库存清零属高危操作：必须显式 `--confirm-zeroing` 才执行 | ✅ | 沃尔玛商品维护 submit.py |
| BR-ST-006 | 新系统要求：凭证加密入库 + 全量轮换（历史上凭证曾入 git；xlsx 退役） | 决策已定 | PLAN.md Phase 1 |

## 3. CAT — 主数据与 SKU

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-CAT-001 | 现行 SKU = ASIN（大写），冲突时 `ASIN-N` 自增（2026-05-09 用户校正，弃用店铺前缀方案，理由：一一对应便于追踪） | ✅⚠️C2 | erp-core sku_generator.py |
| BR-CAT-002 | 新产品 D1 已拍板：内部身份 = master_sku（`M{seq}` 渠道中立终身不变）；ASIN 降级为 (source_channel, source_ref) 属性；新上架渠道 SKU=master_sku；**存量 SKU=ASIN 只映射不迁移**（Walmart SKU 不可改） | 决策已定 | PRODUCT-TEAM.md D1 |
| BR-CAT-003 | 全局 ASIN 去重：任何已在"在线产品总表"（148k+ 行）出现过的 ASIN 不重复上架，**不区分店铺**；集中缓存 1 次/天同步，缓存不健康（size=0 或 stale>30h）自动回退 (asin,store) 直读旧路径 | ✅ | auto_listing/main Layer2、erp_listing_server/dedup |
| BR-CAT-004 | 品牌唯一店铺策略:同一品牌绑定单店（brand assignment 占用/释放）→ 支撑"同 ASIN 只在一店"与 SKU 全局唯一假设 | ✅ | erp-core listings.py `_release_brand_assignment` |
| BR-CAT-005 | 上架商品强制品牌 = "Unbranded"（全店，无例外） | ✅ | auto_listing/config FORCE_BRAND |
| BR-CAT-006 | Amazon 类目 → Walmart PT 映射由「类目映射」模块产出（v5.5，5 阶段 pipeline）⚠️ 该模块被 gitignore，代码只在本机——考古前必须先入库（缺口 G1） | ⚠️ | README 模块矩阵、.gitignore:74 |

## 4. UPC — UPC 资产管理

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-UPC-001 | 生成标准：12 位 UPC-A，GS1 Mod10 校验位；熵源 OS 级随机 | 🧪 | UPC生成器/generator.py |
| BR-UPC-002 | 首位白名单 {0,1,6,7,8,9}：前缀 2(生鲜变重)/3(NDC药品)/4(店内卡)/5(优惠券) Walmart 以 `EXT_DATA_ERROR_54514906640101` 拒收 | 🧪 | UPC生成器 README |
| BR-UPC-003 | 拒绝 ≥4 位连续递增/递减段（含 9→0 环绕）；与已有 UPC 数值距离 ≥1000（防相邻批次连号） | 🧪 | UPC生成器 generator/storage |
| BR-UPC-004 | 新 UPC 必须过 Walmart 全站校验（`GET /v3/items/walmart/search?upc=`）：free/conflict/error 三态，仅 free 入池；error 下次重跑 | 🧪 | UPC生成器 walmart_check |
| BR-UPC-005 | 池释放语义（2026-06-13 修订，C1 已由生产裁决）：**提交前失败**（LLM 失败/预检拒绝，Walmart 从未见过该 UPC）→ 回收"已领→未用"（`unmark_used_batch`）；**提交后**（feed 已 POST，无论成败）→ 永不回收（Walmart 端可能已锁定） | ✅ | main.py:1513-1522（commit 67afa32）、feed_submit VerifyResult 回收 |
| BR-UPC-006 | UPC 复用（2026-05-09 用户校正）：Walmart 按卖家锁定 UPC，同 ASIN/同 SKU 重上架**必须复用历史同一 UPC**，不换新号；分配优先级：同 SKU 历史 > 同 ASIN 历史 > 池内新号 | ✅ | erp-core upc_allocator.allocate |
| BR-UPC-007 | feed 报 UPC 冲突 → 池内标"冲突"，不再分配；audit 周期扫池验证全站占用 | ✅ | auto_listing reconcile/upc_audit |
| BR-UPC-008 | 人工释放 listing 时释放的 UPC 标 `release_failed`，**不回可用池**（Walmart 端可能已锁定，复用必撞冲突） | ✅ | erp-core listings.py:671-683 |
| BR-UPC-009 | 上架成功应回填 verified_wpid 并升级 assigned（erp-core 设计，接线缺失=R-ERP-001）；新系统必须闭环此状态机 | 🚧 | upc_allocator.confirm |

## 5. COL — 选品采集

> ⚠️ v1.2 修订（D-Q42）：本域移植源 = **amazon-scraper-v3 独立仓**（BR-ASC 系列为准）；下表 erp-core 内嵌副本的行为仅作行为参考，代码弃用。

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-COL-001 | 采集器并发 AIMD 自适应（加性增/乘性减），控制器 5s 一拍；独立 worker 池（默认 6），与 API/任务进程隔离 | ✅ | erp-core scraper、celery_app |
| BR-COL-002 | 失败分类重试：transient（网络/代理/5xx）自动重入队，上限 5 轮 × 内部 3 次；permanent（404/解析失败/品牌限制）直接放弃标 gave_up；每 5min 扫一批 ≤200 | ✅ | pipeline_tasks.py |
| BR-COL-003 | DMIT 结果 404 = 尚未采集 → 自动推送 upload 任务；DMIT 每天凌晨 2:00 全量采集，下游早班需留 4h buffer | ✅ | auto_listing dmit_client、closed_loop.md |
| BR-COL-004 | 已上架商品源数据保鲜：每 4h 重采"有活跃 listing 且 last_scraped 过期"的产品（这是库存同步的核心驱动，没有它推送无意义——用户 2026-05-09 校正） | ✅ | listing_tasks_maint.rescrape_stale_listings |
| BR-COL-005 | 源库存变化 → 事件驱动立即推库存更新 + 6h 全库兜底扫描（双轨制，用户 2026-05-09 二次校正） | ✅ | async_runner.py:1210-1228、celery beat |

## 6. AUD — 合规审核与风险拦截

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-AUD-001 | 审核分层：Phase0（品牌黑名单/类目/商标图谱）→ L1 类目 → L2 规则引擎 → L3 LLM → L4 视觉；L3/L4 默认开启；产出 verdict + hit_codes + severity(hard_block/penalty/warning) | ✅ | erp-core audit/pipelines/orchestrator.py |
| BR-AUD-002 | 上架前风险门（独立于审核，零成本前置）：禁售 PT + 品牌黑名单双查；**放在 DMIT 采集之前**执行（省采集与 LLM 成本） | ✅ | auto_listing/main Phase0.5、risk_gate.py |
| BR-AUD-003 | 禁售 PT 判据：类目映射表 D=「禁售」或 E 列以「否」开头（当前 813 个 PT）；品牌黑名单 casefold 精确匹配（当前 1832 个） | ✅ | risk_gate.py 头注、commit 53a2789 |
| BR-AUD-004 | 风险门失败语义 = **fail-open**：飞书拉取失败优先用过期缓存，无缓存则放行 + WARNING（风控缺席不得阻塞生产）；缓存 TTL 24h | ✅ | risk_gate.py |
| BR-AUD-005 | 品牌黑名单自动回流：清理发现的 C 类(品牌限制)/E 类(知产) ASIN → DMIT 采品牌 → 写「禁止品牌收集」，来源标 `沃尔玛-品牌限制`/`沃尔玛-侵权`；DMIT 返回 `#`/`N/A` 占位符过滤 | ✅ | 问题商品清理 brand_collector |
| BR-AUD-006 | **零认证强制覆盖**（搬运场景核心约束 2026-04-28）：无法提供任何认证文档，凡"会触发文档必填"的字段一律强制安全值——certification_type=`Neither of these applies`、has_nrtl_listing_certification/isProp65WarningRequired/has_written_warranty/isAssemblyRequired=`No`；只对该 PT spec 存在的字段强制；目标值不在 enum 时按安全序列(No/Neither/Skip/None/首项)回退；同时**清空**对应文档字段（warrantyText、prop65WarningText、CPC 文档 id 等）防 LLM 幻觉填假值 | ✅ | mapper.force_overrides:972-1060 |
| BR-AUD-007 | LLM 不可信原则：身份/资金/合规字段（brand、price、UPC、库存、end date、认证）一律业务层覆盖，LLM 只做内容映射 | ✅ | mapper.py 设计 |
| BR-AUD-008 | 审核错误学习闭环：listing_errors 每周聚合反哺 audit 候选规则 | ✅ | cron.aggregate_listing_errors |

## 7. PR — 定价

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-PR-001 | 售价公式：`Walmart 价 = (Amazon 商品价 + 运费) × 店铺区间倍数`；四区间：FBA $0-30 / FBA $30-80 / FBM $30-100 / FBM $100-300，每店一行四倍数 | ✅ | auto_listing/pricing.py |
| BR-PR-002 | Amazon 价取值：`current_price` 优先，缺失才用 `buybox_price`（2026-05-14 用户校正，current 更贴近实际售卖价） | ✅ | pricing.amazon_total_price |
| BR-PR-003 | 运费解析：Free/免运 → 0；未显示运费视作 0；N/A → 无法计价 | ✅ | pricing._parse_money |
| BR-PR-004 | 总价不落任何区间 → **不上架**（主链行为）；clamp 变体：区间外用最近区间倍数计算并标 out_of_band（供在线总表 P/Q 建议下架） | ✅ | pricing.compute_walmart_price(_clamped) |
| BR-PR-005 | 倍数单元格可能是百分比格式（读出 `'275%'` 字符串），解析必须容忍——2026-06-11 事故：float() 失败静默跳过 → 全店误判"无倍数配置"整批淘汰 2355 行 | ✅ | pricing._parse_multiplier |
| BR-PR-006 | 价格同步：新旧价差 < $0.01 跳过 PUT（省 100/h 配额） | ✅ | config.PRICE_DIFF_THRESHOLD |
| BR-PR-007 | 价格批量永远走 PUT 单品（100/h）而非 PRICE_AND_PROMOTION feed（6/day 高危）；除非聚合到日级批次 | ✅ | closed_loop.md、CLAUDE.md |
| BR-PR-008 | 30% 价格变动阈值：超过需要额外确认（erp-core update_price 设计） | 🧪 | specs/008 F20-F23 |

## 8. LST — 上架（建品）

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-LST-001 | 主链阶段序（fail-fast，行级）：Phase0 配额 → 0.5 廉价过滤（风险门→DMIT→价格→库存，全部在消耗 UPC/LLM 之前）→ 0.7 变体分组 → 0.8 维度重映射 → 1 UPC 预分配 → 2 LLM 映射+同店打包提交 → 2.5 变体标题差异化 → 3 批量回写 | ✅ | auto_listing/main.py |
| BR-LST-002 | 库存 < 5 不上架；同步时库存 < 5 推 0（MIN_INVENTORY_THRESHOLD=5） | ✅ | config.py:99 |
| BR-LST-003 | 配送时长 > 12 天：**仍上架但库存写 0**（显示缺货不出单，保留 listing 资产） | ✅ | config.MAX_DELIVERY_LEAD_DAYS |
| BR-LST-004 | 同店全部商品打包为**单个** MP_ITEM feed（10/h 限额下的唯一可行策略） | ✅ | main Phase2 |
| BR-LST-005 | MP_ITEM v5 spec version 必须完整时间戳（如 `5.0.20260304-22_45_32-api`），裸 "5.0" 拒收；feed header 3 字段 | ✅ | config、feed_submit |
| BR-LST-006 | Site End Date 必须 ISO DateTime（`2028-12-31T00:00:00Z`）；纯日期 yyyy-mm-dd 被拒（EXT_DATA_ERROR_00030257670757，spec 文档描述是误导） | ✅ | config.SITE_END_DATE |
| BR-LST-007 | 格式实测三则：productIdentifiers=**单对象**非数组；price=**裸 number** 非对象；inventory[].fulfillmentCenterID **必填** = Partner ID（Virtual node，`GET /v3/settings/partnerprofile` 获取并缓存） | ✅ | mapper.force_overrides、store_info |
| BR-LST-008 | 数值字段 ≤2 位小数（尺寸超 2 位 → EXT_DATA_ERROR_68050064665065），提交边界统一 sanitize | ✅ | feed_submit（R-003 修复） |
| BR-LST-009 | 文案约束：keyFeatures 按 per-PT minItems 补齐；manufacturer ≤60 字符截断；类型修正（标量包 array/URL 占位删除/enum 单位修正） | ✅ | mapper enforce_copy_limits/fix_type_mismatches |
| BR-LST-010 | 图片顺序防御性排序（上游 set() 顺序随机时保 idempotent） | ✅ | config.SORT_IMAGES_DEFENSIVE |
| BR-LST-011 | 默认值：备货 1 天（fulfillmentLagTime）；mustShipAlone=No；原产国=China | ✅ | config.py:94-96 |
| BR-LST-012 | 变体组：`full_set` = 父 ASIN ∪ 变体 ASIN 列表 ∪ 自身（正则 `B0[A-Z0-9]{8}`）；组内 PT 必须一致；**跨店同组归 leader**（min row_index 的店），其余成员重定向/拒绝；PT 不接受 Amazon 维度键时先硬编码映射后 LLM 兜底重映射（如 color_name→pieceCount）；同组 productName 全同时追加变体属性后缀 | ✅ | main Phase0.7/0.8/2.5、api_tasks.py |
| BR-LST-013 | 跨店 Anchor 锚定：变体组锚定到店后防漂移，anchor 店铺暂停时成员行拒绝并写明原因，不自动转移 | ✅ | docs/variant_anchor_design.md |
| BR-LST-014 | Excel 输入模式防重：入队前剔除飞书 K=Yes/Unknown 的 (asin,store)（防外部文件缺列导致重复上架） | ✅ | main --xlsx |
| BR-LST-015 | 跟卖（match）通道：仅 5 个 offer 字段（sku/condition/productIdentifiers/ShippingWeight/price），feed 类型 MP_ITEM_MATCH v4.2；提交前 SPEC 预检（按 UPC 去重缓存）：返回 MP_ITEM_MATCH=可跟卖 / MP_ITEM=未在售 / {}=目录无；**feed 本身带不了库存**，PROCESSED 确认 item 建立后才推库存；默认 condition=New、重量 1 磅；no-match 仅记录不回退建品 | ✅ | match_listing/README |
| BR-LST-016 | 上架必须消耗当日店铺配额；重试型提交不占新增配额（下架同理） | ✅ | quota.py、批量下架 README |

## 9. LC — 生命周期与错误处置

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-LC-001 | legacy 9 状态流：登记 → 采集 → 审核(pass/fail) → 上架(Yes/No) → 异步审核(SUCCESS/INPROGRESS/ASYNC_PENDING/SUCCESS_WITH_WARNING/DATA_ERROR/SKU_LOCKED) + RETIRING/PROHIBITED；erp-core 扩展态：pending/spec_ready/submitted/processed/published/live/active/out_of_stock/updating/deleting/retiring/unpublished/error/deleted/retired | ✅ | closed_loop.md、erp-core models/listing.py |
| BR-LC-002 | feed 提交后 **6h** 才首次 reconcile（Walmart 异步审核期）；**168h(7天)** 无终态归档放弃跟踪 | ✅ | config RECONCILE_* |
| BR-LC-003 | 异步审核假错误：`EXT_DATA_ERROR_56026862530206`/`66547201695750` = "仍在合规审核"，标 ASYNC_PENDING **不清**审核列，等待自然转 SUCCESS（数小时~数天） | ✅ | reconcile.py、R-004 |
| BR-LC-004 | 可重试错误集：`EXT_DATA_ERROR_72600149546850`/`IB.VALIDATION.DATA.001`/`ERR_OFFER_2020`/`ERR_INT_SYS_01010010` → 清上架标记让主链重跑 | ✅ | reconcile.py |
| BR-LC-005 | **SKU_LOCKED**（`ERR_EXT_DATA_0101211`）= SKU 已绑死旧 UPC，不能换新 UPC 重发同 SKU；处置链：标 SKU_LOCKED → 当晚 RETIRE_ITEM → 标 RETIRING → **24h 冷却** → 清标记重新上架 | ✅ | auto_listing retire_and_relist |
| BR-LC-006 | **feed 顶层计数不可信**：以 item 级 ingestionStatus 为准（实测 headline itemsFailed=199 而 item 级 SUCCESS=155） | ✅ | R-004、feed_orchestrator real_success |
| BR-LC-007 | STALE_NO_OP 语义分型：对 DELETE/RETIRE/MAINTENANCE = "已达目标态"照终态处理；对上架/更新类**不可信**，交全量同步校验 | ✅ | feed_orchestrator.py:629-672 |
| BR-LC-008 | erp-core 错误分类体系：50+ errorCode → 8 类目（brand_block/category_block/image/price/inventory/gtin_upc/system/spec/other）× 8 处置（retire/replace_image/reallocate_upc/resubmit_price/resubmit_inv/spec_rebuild/retry/human_review）；未知码走关键词回退，最终 human_review | ✅ | error_classifier.py |
| BR-LC-009 | 失败累计状态机：按失败种类分阈值（DMIT_NOT_FOUND/STOCK_LOW/PT_INVALID/PRICE_OUT_OF_BAND/LLM_INVALID），达阈值标终态不再重试；**D=fail 是永久淘汰**，恢复必须人工改数据 | ✅ | retry_state.py |
| BR-LC-010 | feed 终态必须回填编排层：pipeline_run 收尾（pass/partial/reject）+ 计划任务真实成败数回填（不允许派发时乐观计数） | ✅ | feed_orchestrator.py:822-887 |
| BR-LC-011 | 价格/库存两段式：派发时只写 pending_price/pending_qty + updating 态；**Walmart SUCCESS 后才回填 current_***，失败清 pending 复位 | ✅ | listing_tasks_maint:290-371、feed_orchestrator:889-937 |
| BR-LC-012 | feed 整体 ERROR（spec 版本过期/格式错，无 itemDetails）→ 关联过渡态 listing 按 published_status/qty 推回稳定态，不许永久卡死；auto_relist 类错误重置待重派 | ✅ | feed_orchestrator.py:731-772 |
| BR-LC-013 | DELETE/RETIRE 终态且零失败时对未返回 itemDetails 的 SKU 做终态 sweep（Walmart 偶尔漏报 STALE 项）；MP_ITEM **不许 sweep**（上架必须逐 SKU 验证） | ✅ | feed_orchestrator.py:774-820 |
| BR-LC-014 | 单 ASIN 全链路必须可回放：每 stage 写事件（pipeline_events），feed 原始 JSON 永久留档（reconcile 反查 SKU→UPC 的唯一来源） | ✅ | pipeline/events、auto_listing logs |

## 10. MT — 在架维护

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-MT-001 | 维护三类：标题（MP_MAINTENANCE v5.0）、价格（PUT 或 PriceFeed v1.7）、库存（PUT 或 InventoryFeed v1.4） | ✅ | 沃尔玛商品维护 README |
| BR-MT-002 | 路由决策：单店 ≤5 SKU 改价走同步 PUT；≤10 SKU 改库存走 PUT；更大走 feed；feed 按 1000 SKU/片双约束切片（max_items+max_bytes） | ✅ | walmart_maintenance_common |
| BR-MT-003 | 维护前提 J=PUBLISHED；触发条件——标题：相似度≠"100" 且新标题非空非"[商品不存在]"占位；价格：`更新价格=="是"` 且数字合法；库存：`更新库存=="是"`→取值 / `=="库存调0"`→强制 0 | ✅ | 同上 |
| BR-MT-004 | `sync`（数据同步）与 `poll`（结果回查）自动化；**`submit` 必须人工触发**（防误改在架商品） | ✅ | 同上 |
| BR-MT-005 | 字段子集维护支持预设集（images/copy/attributes/price/inventory/shipping/all）+ 显式 overrides | ✅ | update_listed.py |
| BR-MT-006 | 维护 feed payload 按日期/批次落盘归档 | ✅ | payloads/ 目录 |

## 11. RET — 下架与问题商品清理

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-RET-001 | **DELETE_ITEM = 永久删除不可恢复**，与 RETIRE_ITEM（可恢复停售）严格区分；DELETE 仅限 Seller-Fulfilled，WFS 调用报错 | ✅ | 批量下架 README |
| BR-RET-002 | 表驱动日删：每日 15:00；单 feed ≤95KB（官方 100KB 留 5KB 余量，≈1000 SKU）；同店重试+新增合并单 feed；手动 CLI 同店批间隔 360s（10/h） | ✅ | daily_retire_orchestrator |
| BR-RET-003 | 仅标准 ASIN（`^B0[A-Z0-9]{8}$`）可提交 DELETE，其他标"非ASIN"跳过 | ✅ | 同上（2026-05-26） |
| BR-RET-004 | 下架状态枚举：是 / 处理中 / 未查到 / 否N（累计失败次数）/ 非ASIN；SKU 级 DATA_ERROR/SYSTEM_ERROR/TIMEOUT → 入重试；重试不占单日上限 | ✅ | 同上 |
| BR-RET-005 | 单店单日下架上限默认 300（配置表 I 列，按店可调）；新增按上限截取 | ✅ | 同上 |
| BR-RET-006 | 问题商品清理每 6h（0/6/12/18）：拉 UNPUBLISHED + SYSTEM_PROBLEM（60/min 限速 1.1s/req） | ✅ | daily_cleanup.py |
| BR-RET-007 | **A 过期反补优先于删除**：错误原因含 "End Date has passed"（占 UNPUBLISHED ~46%）→ MP_MAINTENANCE 改 endDate=`2049-12-31T00:00:00.000Z` 促 republish 挽回流量；**≥2 次反补失败转 DELETE 兜底** | ✅ | relisting.py |
| BR-RET-008 | Stage 暂存商品（"Stage status until you go live"）不是错误，**不删除** | ✅ | daily_cleanup Step1.6 |
| BR-RET-009 | DELETE 提交 2 日去重（同 SKU 2 天内不重复提交，防 feed 未处理完反复提） | ✅ | submitted_skus.json |
| BR-RET-010 | 错误 13 类归类（A过期/B禁售/C品牌/D价格/E知产/F限类/G药品/H信息/I内容/J特殊/K审查/L系统/Z兜底），关键词判定；统计按 (SKU,归类) 全局去重累计 | ✅ | feishu_sync._CATEGORIES |
| BR-RET-011 | 监管合规删除表：人工录入的 SKU 每轮优先删除（Step 0，dry-run 时整体跳过） | ✅ | daily_cleanup Step0 |
| BR-RET-012 | 清理归档三通道：PostgreSQL(runs+error_items 幂等 (run_ts,store,sku)) + 邮件（临时附件发完即删）+ 飞书三表 | ✅ | db_store.py |

## 12. ORD — 订单与履约审核

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-ORD-001 | 增量窗口：`max(last_sync − 1h, now − 30d)`；重叠 1h 防边界漏单；`--days` 人工优先 | ✅ | 订单审核 状态.py |
| BR-ORD-002 | **必须显式传 `createdStartDate=179d_ago`**：不传时 Walmart 默认 7 天窗口，使 lastModifiedStartDate 失效（关键 workaround） | ✅ | 沃尔玛异步.py |
| BR-ORD-003 | upsert 语义：同步列（A:AD）覆盖刷新；人工列（AE+）永不触碰；本次未拉到的历史订单保留；全表按下单时间倒序重排 | ✅ | 飞书表.py |
| BR-ORD-004 | 拉单两轮重试：Round1 并发 10 → Round2 并发 3；4xx=永久错误不重试，5xx/超时=临时错误降并发重试 | ✅ | 沃尔玛异步.py |
| BR-ORD-005 | 钓鱼检测（同步时实时）：地址 A1+A2 拼接→去空格/大写/去标点标准化→与黑名单街道**双向 substring**（防 Suite 尾缀绕过）；邮编 zip+4 取前 5 位比对；黑名单条目 <8 字符跳过（防"123 Main St"误伤）；输出三档（地址+邮编双命中/地址/邮编） | ✅ | 钓鱼检测.py |
| BR-ORD-006 | **钓鱼标记优先级最高且不可覆盖**：一旦写入，后续任何综合审核不得覆盖；复审须人工清列 | ✅ | 审核决策.py |
| BR-ORD-007 | 采购方匹配：候选 = 启用=="是" ∧ 配送方式(FBA/FBM)相等 ∧ 区间起≤亚马逊总价≤区间止；多候选**取汇率最高**（卖家收益最大化） | 🚧 | 采购方匹配.py |
| BR-ORD-008 | 限价公式：`限价(USD) = 沃尔玛单价 × 0.85 × 6.8 ÷ 采购方汇率`（0.85=采购方留 15% 利润；6.8=USD→RMB 市场汇率；采购方汇率=协议折扣）；亚马逊总价 > 限价 → 建议拒绝 | 🚧 | 限价计算.py |
| BR-ORD-009 | 标题一致性：双方标题去标点全小写 `SequenceMatcher.ratio()` < **0.9** → 转人工 | 🚧 | 商品一致性.py |
| BR-ORD-010 | 综合决策优先级（命中即止）：采集失败 → 标题差异 → 无采购方 → 超限价 → ✓通过；钓鱼凌驾一切 | 🚧 | 审核决策.py |
| BR-ORD-011 | 审核截图以图片单元格嵌入（非 URL）；该列由审核服务专管，整表写入必须跳过 | 🚧 | 飞书表.py |
| BR-ORD-012 | ⚠️ V3 采集回调审核链（采购方/限价/一致性/截图）**代码就绪但生产未启用**（callback URL 未配置）——新系统按 R2 需求实现，非考古移植 | 🚧 | 订单审核 README 状态声明 |
| BR-ORD-013 | erp-core 订单侧扩展（已实现）：15min 多店同步（拉近 6h 变化单）、采购方 CRUD+match、系统订单、物流跟踪（17track 主/track123 备+承运商官网直链）、佣金按 PO 行核算、出库成本 CNY 折算 | ✅ | orders.py、carrier_tracking |
| BR-ORD-014 | 履约操作（acknowledge/ship/cancel、bulk purchase-status）后端就绪，前端未接线 | 🧪 | orders.py、WIRING-AUDIT |

## 13. AS — 售后

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-AS-001 | Walmart Returns API **不支持时间过滤**（无 since 参数）→ 只能全量拉取；limit=200 翻页，nextCursor 是完整 URL 需解析回参数 | ✅ | 售后订单同步 README |
| BR-AS-002 | 节流 1.3s/页 ≈46/min（限额 50/min 留余量）；8 路按店并发（每店独立 token 桶不互耗） | ✅ | fetch_walmart_returns |
| BR-AS-003 | 行级展开：一行 = returnOrderLine（同 RMA 多 SKU 拆多行，订单级字段重复）；27 列 schema（见 README 字段映射表）；按 returnOrderDate 倒序 | ✅ | 同上 |
| BR-AS-004 | 状态字段是**行级**的（status/refundStatus/deliveryStatus 同 RMA 各行可不同）；refundMode 枚举 COURTESY/REFUND_TO_PAYMENT_METHOD/MERCHANT_REFUND | ✅ | 同上 |
| BR-AS-005 | `replacementInfo=true` 拉取换货信息（预留，当前列未消费） | ✅ | 同上 |
| BR-AS-006 | 现行覆盖式写入的已知缺陷（无历史快照/尾部残留/非原子）→ **新系统必须 upsert by (RMA, line) + 保留历史**（改进型需求） | 决策已定 | README 风险节 |
| BR-AS-007 | 运维基线：≈3800 行；写入量骤降 >50% **不要重跑**，先排查代理/Walmart 故障 | ✅ | 同上 |
| BR-AS-008 | 退款操作（POST refund）明确**不在**自动化范围，人工执行 | ✅ | 同上 |

## 14. RPT — 报表与 KPI

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-RPT-001 | 8 项绩效指标与达标线：OTD ≥90% · 取消率 ≤2% · VTR ≥99% · 卖家回复率 ≥95% · 退款率 ≤6% · 差评率 ≤2% · 退货率 ≤6% · 未收到商品 ≤2% | ✅ | 日报 KPI 列结构 |
| BR-RPT-002 | Insights 端点全系 1/min；`refunds/summary` 官方已废弃 → 一律用 `returns` 系列；negativeFeedback/returns/itemNotReceived 6 端点不在官方限速表，按 1/min 保守 | ✅ | CLAUDE.md |
| BR-RPT-003 | 24h 销售快照窗口 = 中国时间昨日 06:30 ~ 今日 06:30 | ✅ | fetch_walmart_performance |
| BR-RPT-004 | 双跑节奏：08:00 完整（API+前台抓取+问题订单+日报推送）；14:00 轻量补刷（保留前台抓取列不动） | ✅ | 日报 README |
| BR-RPT-005 | 问题订单永久累积：去重 key=(Sales Order#, 指标, 子分类, 物流单号, 商品)，只保留首次发现日期 | ✅ | fetch_walmart_problem_orders |
| BR-RPT-006 | 前台数据（卖家名/可售状态）走 RPA 抓取，失败自动回退旧快照不阻塞；非 ACTIVE 店补"不可售" | ✅ | 同上 |
| BR-RPT-007 | 财务：账期销售额/佣金/退款/期末余额/备用金/回款（statement + reconreport）；WFS Recon Report 1/h 必须缓存 | ✅ | 同上、CLAUDE.md |

## 15. SCH — 调度与配额

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-SCH-001 | 每日店铺配额：上架 FBA/FBM 与下架分开配置（飞书定价表），**北京时间 0 点重置**；读取当日已用后返回剩余 | ✅ | quota.py |
| BR-SCH-002 | 生产节律总表：06:00 早班链(库存同步→回写→relist→上新)；每小时:15 reconcile；15:00 批量下架；23:30 retire SKU_LOCKED；0/6/12/18 清理；08/14 日报；每 4h 订单；08:00 售后；14:06 去重同步；08/12/16/20 健康报告 | ✅ | 各模块 cron/launchd |
| BR-SCH-003 | 计划任务六型：bulk_delete/bulk_retire/bulk_extend_end_date/bulk_publish/sync_orders/sync_returns；每 5min tick 推进；配额满推迟到次日 00:30 | ✅ | erp-core scheduler.py |
| BR-SCH-004 | 配额计数必须精确（task_runs 级），派发时不得乐观累计（R-ERP-009/P0-3 教训） | 决策已定 | listing_tasks.py 注释 |
| BR-SCH-005 | 队列隔离：审核（IO 密集 20 并发）/上架提交（按店限流 8 并发）/轮询/巡检/下架/cron 分队列；采集独立 worker 池不走队列 | ✅ | celery_app.py |

## 15-EXT. 外围系统（v1.1 新增 — 五个独立仓库 + erpAPI main 更新）

### ASC — Amazon 采集器（amazon-scraper-v3，DMIT :8899 生产）

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-ASC-001 | Server/Worker 分离：Server 单机轻量（1C/2GB，SQLite WAL+FTS5 读写分离连接池）负责任务分发/结果收集/定时调度/全局并发配额/webhook 回调；Worker 可多机水平扩展 | ✅ | amazon-scraper-v3 README |
| BR-ASC-002 | 反侦测三件套：curl_cffi TLS 指纹模拟 + TPS 代理每请求自动换 IP + Session 热备轮换 | ✅ | 同上 |
| BR-ASC-003 | Worker 端 AIMD 自适应并发；lease_epoch 租约防任务重复消费；variant_offset 变动检测；3 并行 batch submitter | ✅ | 同上 |
| BR-ASC-004 | 截图 Playwright 可选（`--no-screenshot` worker 只领非截图任务）；`--auto-restart-hours` 定时自愈防内存泄漏 | ✅ | 同上 |
| BR-ASC-005 | Worker 接入需 API Key（WORKER_API_KEY）；batch 完成走 webhook 回调通知下游 | ✅ | 同上 |
| BR-ASC-006 | erpAPI 侧唯一入口 = `scraper_client.py`（同步 + 异步孪生 API，ScraperError 统一失败约定），**禁止直连 :8899**；与 erp_listing_server(:9080) 是同机不同服务，严禁混淆 | ✅ | erpAPI scraper_client.py（2026-07-09） |

### WSC — 沃尔玛采集器（walmart-scraper，跟卖选品数据源）

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-WSC-001 | 单进程 FastAPI + BackgroundTasks（无 Celery/Redis）；LanePool 架构：每 Lane 独占一个代理 IP，Lane 内串行 + 限速 | ✅ | walmart-scraper README |
| BR-WSC-002 | 封控语义：**默认关自动换 IP**——被封时 Lane 置 BLOCKED 停机报警，人工确认后换 IP（防止自动重试烧光 IP 池） | ✅ | 同上 |
| BR-WSC-003 | **权威 GTIN 旁路**：公开页 GTIN 对多变体商品不准 → 走卖家后台 isbm 接口取目录权威码；后台会话由本地 BitBrowser 导出 cookie+账号代理经 `/seller-session` 上报服务器复用 | ✅ | 同上、GTIN_UPC_采集对比与改进.md |
| BR-WSC-004 | 三种采集模式：ids / keyword / seller；支持变动检测与断点续采；启动时 ALTER TABLE 自动补列（增量迁移） | ✅ | 同上 |

### TRO — TRO 案件采集（tro-scraper-matrix）

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-TRO-001 | 5 站点矩阵（123tro/61tro/ipsebe/saibeiip/worldtro）各自独立 SQLite → merge → merged.db → 清洗 → Excel/飞书/tro_cases | ✅ | tro-scraper-matrix README |
| BR-TRO-002 | 清洗规则：全角→半角字符翻译表；律所别名归一（GBC/HSP/SMG/Keith 等 casefold 映射）；entity key = 小写去非字母数字汉字 | ✅ | cleaning_engine.py |
| BR-TRO-003 | 61tro 用 last_page 增量游标自动维护；HTML 站走 httpx+bs4，JS/滑块站走 Playwright | ✅ | README |
| BR-TRO-004 | 每日定时 pipeline 已注册（daily-tro-pipeline / tro-daily-scrape SKILL）；下游消费方 = erp-core audit 的 tro_cases 表 | ✅ | erpAPI scheduled-tasks、erp-core etl_tro |

### TMK — USPTO 商标与黑名单（walmart-trademark-sync 生产 · trademark-data 研究）

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-TMK-001 | USPTO 官方公开 dataset ETL（无付费 API）：zip→XML→PostgreSQL `uspto` 库 5 张关系表——trademarks ~12M（主键 serial_number）/ classes ~30M / statements ~5M / owners ~15M / design_codes ~3M | ✅ | walmart-trademark-sync README+schema.sql |
| BR-TMK-002 | 日度增量 06:00（daily_update）；`etl_progress` 按 (data_type, source_file) 断点续传，只重跑失败/未完成 | ✅ | 同上 |
| BR-TMK-003 | 飞书→DB 单向同步（TRUNCATE+全量重灌）：黑名单品牌 ~36k 行（08:00，uniq brand_upper）+ 品牌×Nice类目映射 ~126k 行（08:05） | ✅ | sync_blacklist_from_lark / sync_brand_nice_class |
| BR-TMK-004 | 未匹配增强（retry_not_found）4 策略：标点剥离 / name flip / pg_trgm 相似度 / 品牌反查；依赖 matched_companies + tro_cases 两张表（生产脚本在别仓） | ✅ | 同上 |
| BR-TMK-005 | LIVE/DEAD 状态经 status_code_mapping（~70 码）字典判定 | ✅ | schema.sql |
| BR-TMK-006 | trademark-data 仓 = 研究性质（专利库重建 + pgvector + SafeSellAI 逆向 + captcha_solver）；**生产商标链路以 walmart-trademark-sync 为准** | ✅ | trademark-data progress.md |
| BR-TMK-007 | 消费方：erp-core audit Phase0/商标层/nice_class_mapper 读 `uspto` 库（USPTO_DATABASE_URL 只读）——注意黑名单双份：PG 36k（审核用）vs 飞书 1832（risk_gate 用），口径需在新系统统一 | ✅⚠️ | erp-core config、risk_gate.py |

### EAI — erpAPI main 更新（a4a2999，2026-07-09）

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-EAI-001 | `lark_io/` 统一飞书模块已落地（_core 1367 行 + 989 行 pytest）：批量切块三重约束取先触发者、90217/90235 瞬时错误双轨判定（int code + lowercase 子串）、统一 LarkError + 各包兼容别名、order-audit 的 dict-error 契约在边界翻译 | ✅ | lark_io/、tests/test_lark_io.py |
| BR-EAI-002 | `docs/feishu_sheets_registry.md` = 9 个物理 workbook 的语义命名注册表（LISTING/ONLINE/ERROR_PRODUCTS/PRICING/CATEGORY/RETURNS/ORDER_AUDIT/PERF/KPI + 全部 sheet_id 用途）；**PRICING 双 token 是同一 workbook 的两个 locator**（X4vMwQ… ≡ E1p9sy…）——Phase 1 数据统一的事实地图 | ✅ | docs/feishu_sheets_registry.md |
| BR-EAI-003 | 飞书身份统一决策：全部 10 workbook bot 可读已探针验证；迁移到 bot 的方案与割接清单已成文；**写探针尚未执行**（割接前置条件） | 🧪 | docs/feishu_migration_plan.md、cutover_checklist.md |
| BR-EAI-004 | 店铺状态门控：上架侧每次运行直读店铺状态表（不依赖小时级缓存，防封店滞后感知半天）；fail-open——读失败或店不在表视为可上架 + WARNING，仅明确非 ACTIVE 才跳过 | ✅ | auto_listing/store_status.py |

## 15-CAT. 类目映射（REF-R0-002，v1.2 新增，代码入库 main 5ac847c）

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-CAT-101 | 映射规模 v5.5：15,770 Amazon 叶子 → ~4,860 Walmart PT（全量 ~7,000 PT）；置信度分布 高81.9%/中10%/低3.8%/无4.4%（695 叶子无对应 PT） | ✅ | 类目映射 README |
| BR-CAT-102 | 5 阶段 pipeline：①Amazon 类目准备（爬取→提取→15,770 叶子含 browse_node_id+L1-Ln 路径）②A→W 初始映射 ③v5 迭代审核（置信度 73%→82%）④风险/合规标签 ⑤飞书同步 6 表 | ✅ | pipeline/01-05 |
| BR-CAT-103 | **5 维度风险模型**（PT 级标注）：中国卖家禁售 55 PT / 品牌锁定 22 PT / 禁售高发 47 PT / 受限需审批 22 PT / 知产高危 19 PT | ✅ | README 风险表 |
| BR-CAT-104 | 中国卖家可做判据来自**真实上架失败回测**（BIZ-CN 数据），不是拍脑袋——「中国卖家可做=否」列由上架记录回测得出 | ✅ | README、risk_gate BR-AUD-003 消费此列 |
| BR-CAT-105 | 每 PT 标注必需认证（PT 模板字段 + 合规知识库交叉）；37 条政策清单（沃尔玛禁止表，含 BIZ-CN）+ 2026-06-11 政策 v2 重爬（Prohibited-Products-Policy 各品类 HTML/TXT 归档） | ✅ | 沃尔玛禁止 sheet、intermediate/policy_crawl |
| BR-CAT-106 | Amazon 类目重跑触发：Amazon 大改类目（约年 1-2 次）或新增子树；PT 模板随 Walmart spec 版本更新重抽 | ✅ | README 何时重跑 |
| BR-CAT-107 | 内嵌独立 git 仓（remote amazon-walmart-category-mapping，领先 7 未推 commit）；入 erpAPI 时内层 .git→.git-archive 保留 | ✅ | 本机 agent 处理记录 |

## 15-AUD2. 审核系统深化（REF-R0-002，v1.2，源=walmart-audit-system 在产仓）

> 替代此前 BR-AUD-001 的粗粒度描述；新系统审核域以本节为移植基准（非 erp-core 副本）。

| # | 规则 | 状态 | 来源 |
|---|---|---|---|
| BR-AUD-101 | 定位：站在**沃尔玛官方审核视角预审** Amazon 搬运品能否过审；通过后品牌统一改 unbranded 交下游上架。已在全量 4326 ASIN 跑通 | ✅ | walmart-audit-system README |
| BR-AUD-102 | L0 预过滤（phase0）：品牌黑名单 + 商标符号(®™) + 大类硬禁，零 LLM 成本前置 | ✅ | pipelines/phase0*.py |
| BR-AUD-103 | **L1 类目判定 = 混合检索**：`walmart_category_map` 精确匹配优先 → 未命中走 text-embedding-v3(1024维) Top-N 候选 + 关键词召回 → LLM 复排；PT embedding 预计算 6,832 个（pt_embeddings.npz ~27MB） | ✅ | l1_category.py、data/ |
| BR-AUD-104 | **L2 硬规则分层**：R1 类目准入/R2 禁售大类/R3 强制证书 = **硬拒**；R4-R8 = 软证据（0 分，不拒，交 L3 判断）——硬拒与软证据分离是关键设计 | ✅ | l2_rules.py |
| BR-AUD-105 | L3 多任务语义：全 **37 条政策**，文本模型 provider 可选（dashscope→qwen-turbo / deepseek→deepseek-v4-flash），单进程 Semaphore(100) + 3 次指数退避 | ✅ | l3_llm.py、l3_policy_router.py |
| BR-AUD-106 | L4 视觉审核：默认 Volcengine doubao-seed-1-6-flash（多图+中文），可降级 qwen-vl-plus，每商品≤5 图；**线上 worker 默认关 L4**（run_l4=False，需 RUN_L4=true/--run-l4 显式开），单机 CLI 默认开 | ✅ | l4_vision.py、README |
| BR-AUD-107 | 多 Provider 路由：文本 DashScope/DeepSeek，视觉 Volcengine/Qwen，按场景分独立 key（对应 D-Q19 LLM 抽象层） | ✅ | integrations/ |
| BR-AUD-108 | 成本工程（20万/日的关键）：llm_cache 按 sha256(system+user+model) 复用；usage_log 逐次记 provider/model/token/美元；cost_report 生成报表 | ✅ | llm_cache.py、usage_logger.py、cli/cost_report.py |
| BR-AUD-109 | 分布式：server(FastAPI+SQLite 任务表)切片派发，worker 心跳+增量日志回传，支持按层 --filter-stage 重跑；线上 server + 10 worker | ✅ | server/、worker/ |
| BR-AUD-110 | 辅助分类器：nice_class_mapper（商标 Nice 类目）、nrtl_classifier（NRTL 认证判定）、aggressive_offensive（攻击性内容）、forbidden_mega_categories（大类硬禁）、reason_mapper（拒因归一） | ✅ | pipelines/ |
| BR-AUD-111 | 黑名单卖家（第 10 飞书表）：飞书 QNIpwrRW…/8280e8 三列独立（卖家ID/ASIN/Amazon类目）→ 每日 07:05 TRUNCATE 重灌 walmart_audit 三表（sellers 1,308/asins 18,458/cats 11,810）→ Phase0 frozenset 内存拦截 | ✅ | sync/、SYNTHESIS.md |

## 16. ⚠️ 冲突与待决清单（新系统开工前 Owner 逐条拍板）

| # | 冲突 | 两个版本 | 建议 |
|---|---|---|---|
| C1 | ~~UPC 释放语义~~ **已裁决**（2026-06-13 生产实践 = 提交前失败回收、提交后永不回收，见 BR-UPC-005 修订） | — | 新系统照此实现，外加 reserved 超期 sweep（仅限从未提交过的） |
| C2 | SKU 标准 | SKU=ASIN（现行）vs master_sku（D1 已拍板） | 已决：D1 方案，存量只映射 |
| C3 | UPC 校验限速 | upc_audit 200/min vs UPC 生成器自留 180/min | 统一 180/min（10% 余量原则推广到所有端点） |
| C4 | End Date 值 | 上架用 2028-12-31 vs 反补用 2049-12-31 | 统一策略：新系统全部用远期(2049)+到期前自动巡检续期，消灭"A 过期"这一类问题的根源 |
| C5 | 配额体系 | legacy 飞书定价表（生效）vs erp-core store_quota_config（表存在无写入，从未生效） | 新系统配额中心必须覆盖上架/下架/维护三方向 + 精确计数（BR-SCH-004） |
| C6 | 售后写入 | 全量覆盖 vs upsert | 已决：upsert by (RMA,line) + 历史快照（BR-AS-006） |
| C7 | 订单四检链路 | 钓鱼已上线 vs 采购方/限价/一致性代码就绪未启用 | 新系统按 R2 全新实现四检（公式沿用 BR-ORD-007/008/009），不移植未验证代码 |
| C8 | 限流器双实现 | auto_listing/rate_limiter（滑动窗）vs erp-core GCRA | 考古移植 erp-core GCRA（与 walmart_client 已集成） |
| C9 | 飞书身份 | 多数 bot vs quota.py `--as user`（会过期） | 新系统统一 bot 身份；user 依赖清零 |
| C10 | 汇率常数 6.8 | 硬编码在限价公式 | 改为可配置参数 + 定期核对（写死汇率是财务风险） |

## 17. 缺口登记（本账未覆盖、需后续工单补全）

| # | 缺口 | 处置 |
|---|---|---|
| G1 | **`类目映射/` 模块零版本控制**（.gitignore:74 整目录忽略），v5.5 五阶段映射规则无法考古 | 用户在本机执行：删 .gitignore 该行 → 提交模块代码 → 开 REF-R0-002 考古工单 |
| G2 | 飞书侧活数据未快照：定价表实际倍数/配额值、stockzero 配置、黑名单当前规模 | Phase 1 ETL 时一并落库并附对账快照 |
| G3 | 审核 L2 规则引擎 1392 行的逐条规则清单 | REF-R0-003 专项考古（结构已入账 BR-AUD-001） |
| G4 | mapper.py 字段级映射规则全集（22 条规则、枚举回退表、截断表） | REF-R0-004 专项考古（核心已入账 BR-AUD-006/BR-LST-007~011） |
| G5 | 订单审核 docs/ 三份设计文档、日报影刀应用逻辑 | REF-R0-005 补读 |
| G6 | erp_listing_server 任务拆分/变体重定向的 server 侧规则（api_tasks.py 跨店重定向已部分入账 BR-LST-012） | REF-R0-006 补全 |

### v1.1 缺口状态更新

- G1（类目映射零版本控制）**仍未解决**——main a4a2999 后 `.gitignore:79` 仍整目录忽略，仓库内 0 文件。
- 新增 G7：外围五仓与 erpAPI 的**契约面**未入账（scraper_client ↔ v3 API 字段契约、uspto 库 ↔ audit 查询契约、TRO lark 表 schema）——REF-R0-007。
- 新增 C11：品牌黑名单**双份口径**（PG uspto.blacklist_brands 36k 供审核 vs 飞书 WvPTz2 1832 供 risk_gate）——新系统必须单一来源（建议 PG 为准、飞书为镜像）。
- lark_io 落地消解了旧发现"lark-cli 包装重复 20 处"的技术债（Phase 1 的 lark_io 统一服务已由用户提前完成第一步）。

---

**统计（v1.1）**：16 域 + 5 外围域 · 158 条规则 · 冲突 C1 已裁决/C2 已决/其余 8+C11 待拍板 · 缺口 G1 未解 + G2-G7。
下一步（R0 剩余）：Owner 过账签字 → C 系列拍板 → **G1 类目映射入库（仍卡在你本机）** → REF-R0-002~007 专项考古 → PRD v1 → 领域模型与数据字典。
