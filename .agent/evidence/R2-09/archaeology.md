# R2-09 三档自动化贯通 · 考古报告
> 2026-07-25 · 六路并行侦察 + 对抗性交叉核对（7 agents / 411 次工具调用 / 92 万 token / 41 min）。
> 侦察结论一律带 `file:line` 或图纸章节号；交叉核对阶段**逐条复核了侦察结论并推翻其中两条**，
> 复核结果以第 1 节「矛盾与裁定」为准，与第 5 节侦察详录冲突时**以第 1 节为准**。

## 0 结论速览
R2-09 明显大于 R2-12（R2-12=5增量+1补丁，主体是数据供给，只有 4b 一片碰真渠道写）。R2-09 横跨 audit/listing/pricing/aftersale/order 五个域，要新建一整层契约+权限点+前端页（002 里 automation 目前零 path、前端零入口），并至少接三条真渠道写路径（改价 PUT/feed、退款 POST、可能的取消 POST）。估算 6 个增量 + 1 个前置规划动作，量级约 R2-12 的 1.3~1.5 倍。最大不确定性不是技术而是范围口径未冻结：001§09 的 flow 清单明写要求「代码 Enum 对照 + CI 校验」，而工单点名的 listing_pricing 图纸叫 pricing_watch、scrape_to_audit 图纸根本没有、D-Q65②（宪法级，2026-07-23）又派生出一个未登记的档位消费点——清单不先冻结，Enum 写不出来，CI 第一天就红，整单不能合。第二不确定性：007 验收判据要求「采集→审核→上架→定价四环各自可停」，而 001 只供给了两环的 flow_code（audit_to_listing + pricing_watch），判据超出图纸供给 2 环，不裁定则验收天然不可达。第三：refund auto 是真实渠道退款（不可逆的钱），L2/L3 风险被【L1】标签盖住了。

## 1 六路交叉核对：矛盾与裁定
> 这一节是本次考古最有价值的部分——合成阶段不采信侦察结论，逐条回查代码/图纸后裁定。

### 1.1 半自动『定点停』是否需要新 DDL（路线1 vs 路线3，最贵的一条分歧）
**分歧**：路线1：『半自动定点停不是接线问题而是缺状态』——maintenance_task.status 无待确认态、audit_to_listing 批量待确认队列在 001 里无表无状态，因此 R2-09 必然动 migration（ar 帽），且必须在拆单时前置。evidence: 09-platform.md:160/164 vs 06-listing-pricing.md:151。
路线3：『零新表』——product.status='audit_passed' 就是待分配队列（有 ix_product(team_id,status)）、listing.status='draft' 是天然待放行位（allocate 只建 draft、submit 才出门，两步分离）、maintenance_task(price_sync, status='scheduled') + runner config.kinds 闸就是待确认位（0037:34 种子 kinds=[]，注释原文『[]=人工档，只积累不执行』）。
我实测复核：maintenance_task status CHECK IN (scheduled, running, done, failed, skipped)，确无待确认态，路线1 的事实陈述没错；但路线3 的推论成立——『不被 runner 认领的 scheduled』语义上就是停驻，不需要新状态列。

**裁定**：路线3 对，路线1 高估了 DDL 面。裁决：不为『停驻』新增任何状态列。但路线3 漏了粒度问题——kinds 闸是 kind 级全开全关、不是 task 级逐条放行，所以『半自动人工逐条确认』需要补 API 面（maintenance_task 单条/批量『立即执行』端点 + 批量送审端点），那是端点不是 DDL。真正确需 migration 的只有三件小事：ck_cc_action 扩退款/取消 action、automation.* 权限点种子、schedule 种子——都是 ar 帽小改，不构成前置阻塞。

### 1.2 『切档 60s 生效』要不要吃 R2-04 的 Redis pubsub（路线1 vs 路线2/4/5/6）
**分歧**：路线1：建议复用 erp:config:invalidate 把 automation_policy 纳入（『60s TTL 兜底天然满足验收』）。
路线4：判定『不需要复用，也不该复用』，理由是 ConfigService 生产零读者、pubsub 在失效一份没人读的缓存。
我实测复核：grep get_config_service 全仓仅 3 处命中——main.py:39、beat.py:155（都只是把单例喂给订阅循环）、config_service.py:123（定义本身）。路线4 的负面证据成立：没有任何业务代码调用 ConfigService.get()。automation_policy 现有两个读点（procurement.py:31-41 / refund.py:33-42）都是每次请求直连 SQL、无缓存，切档在下一个决策点即生效，延迟≈0。

**裁定**：路线4 对（路线2/5/6 同向，5 比 1 票）。裁决：不复用 pubsub，档位永不进缓存。但这与 007:79 已定案文字『档位变更即时生效（吃 R2-04 Redis pubsub 配置广播）』冲突——立单时的假设与现状不符，需 Owner 认可改为『档位每决策直读、不进缓存；Redis 全停复跑同一验收结果不变』。另：路线1 提出的 fail-open/fail-closed 分述仍要落笔（09:190 写的是 fail-open，而档位取值必须 fail-closed），这条是六路里唯一提出的，路线1 对。

### 1.3 maintenance_run 的 kinds 默认值到底是 fail-open 还是 fail-closed（路线2 vs 路线4）
**分歧**：路线2：『唯一现存 fail-open 缺陷』——maintenance.py:29 代码默认 config.get("kinds", ["delist"])，而 0037:34 种子给的是 []，schedule 行 config 若丢 kinds 键，runner 会自动开始真渠道下架。
路线4：引 maintenance.py:3-4『默认人工档 kinds=[]』当作已经 fail-closed，只把它当『第二个无缓存先例』一笔带过。
我实测复核：docstring 确写『**默认人工档 kinds=[]**』，但第 29 行代码是 kinds = [str(k) for k in config.get("kinds", ["delist"])]。路线4 读的是注释不是代码，路线2 对。

**裁定**：确认为现存 fail-open 缺陷，且与 D-Q65②『报错回收档位=人工闸』直接抵触（一个丢键就把 Owner 刚拍板的人工闸绕过去）。裁决：增量1 顺手改成 config.get("kinds", []) 并补一条测试。注意这是 R2-12 落地的 listing 域代码，按铁律3 属跨界——工单里显式注明『跨域清偿一行 + 一测』并知会 listing owner，不得扩成重构。

### 1.4 order_block / compliance_block 的 semi 档：认二元还是补语义（路线1 vs 路线2/6）
**分歧**：路线1：保留现行 semi≡auto，图纸注『本 flow 二元』、面板隐藏 semi，差异化留后续单——理由是 order_block 是唯一已上线消费点（R2-05 落地并有测试 test_procurement.py:269-288），动它就是改已上线的订单冻结行为。
路线2：『三档语义统一的第一件事是给 order_block 定义 semi≠auto』。
路线6：必须显式定义差异，建议 semi=冻结可一键放行 / auto=冻结且自动拒单。
复核：procurement.py:42 确为 if mode not in ("semi","auto"): return，两档同分支；09:161/162 图纸原文本身就是二元措辞（off/block、是否冻结）。另注意路线6 提议的『semi=可一键放行』其实已存在（order_check.resolved_at 放行机制对两档都生效），所以真正差异只能落在 auto=自动拒单/自动退单——那是新的渠道写，超出本单 L1 范围。

**裁定**：路线1 对。裁决：本单认二元，09:161/162 补注『semi≡auto，面板隐藏 semi』，差异化（auto 自动拒单）留 R2-08 或后续单。需 Owner 一句话确认，因为这是改图纸措辞。

### 1.5 工单等级，以及 refund auto 走哪个渠道端点
**分歧**：路线1 是唯一提出等级问题的（建议改标【L1（refund 片 L2）】对齐 D-Q54），其余五路均未质疑【L1】。
六路都没答的是 refund auto 调哪个端点。我核了 /home/user/erpAPI/docs/walmart_rate_limits.tsv：存在两个互不等价的退款入口——POST /v3/orders/{purchaseOrderId}/refund（Refund Order Lines，60/min）与 POST /v3/returns/{returnOrderId}/refund（Issue refund，60/min），cancel 则是第三个端点 POST /v3/orders/{purchaseOrderId}/cancel（60/min）。而 refund_request 按 order_id 建单，R2-07 又已建 channel_return 域——两条路都能走通，语义完全不同（卖家主动退款 vs 针对退货单发退款）。

**裁定**：等级按路线1 改标【L1（refund 执行片 L2）】。端点选择必须 Owner/渠道侧裁决，不能由实现侧默认——建议：有 channel_return 关联的走 /returns/{ro}/refund，无关联的主动退款走 /orders/{po}/refund，cancel 独立走 /orders/{po}/cancel。即 ck_cc_action 要扩的可能是 2~3 个 action，而不是路线2/3 说的 1 个 return_refund。

### 1.6 『只通 order_block』这句现状描述已过时
**分歧**：工单 check（.agent/review_list.json:547-553）与 007:72 都写『仅通 order_block 一档』，路线3 的接线矩阵基本沿用该口径；路线2/6 指出 R2-07 增量2 落地后 refund/cancel 已半通（manual→record / semi→approval 本地闭环有测试，auto 档 fail-closed）。复核 refund.py:1-13 头注与 _INITIAL_STATUS 双防线属实。

**裁定**：无实质分歧，但工单 check 文案必须随单改为『order_block 全通 + refund/cancel 半通（auto 待接）+ 其余零接线』，否则回写时对不上账。

## 2 必须 Owner 拍板（开工前）
> 前 4 条不裁定则 R2-09 无法开工（Enum 写不出 / 验收不可达 / 已定案文档与现状不符）。

**[1]** flow 清单 v2 一次性冻结（改 001§09 图纸，铁律1 需 Owner 批）：①删 gtin_alert / suspension_reminder 两行（阈值已落 team_config gtin.warn_pct/critical_pct、节奏已落 schedule 种子 remind_days=7，保留=同参数双落点『运营改了不生效』）；②listing_pricing 归一到 pricing_watch；③scrape_to_audit 登记为新码还是不做；④D-Q65②（DECISION-FORM.md:275，宪法级）要求的 maintenance runner 人工/半自动档是否登记为 flow（不接=静默偏离宪法）；⑤03-catalog.md:32『match 模式跳过 sourcing 由 automation_policy』归哪个码。必须一次拍完——09:156 明写『代码 Enum 对照 + CI 校验』，清单不冻结 Enum 就写不出来。

**[2]** 007 验收判据『采集→审核→上架→定价四环各自可停』与 001 只供给两环的矛盾：登记 scrape_to_audit(+listing_dispatch) 补足四环，还是把判据改成三环。不裁则验收天然不可达。

**[3]** order_block / compliance_block 认二元（图纸注 semi≡auto、面板隐藏 semi）还是补 semi 独立语义。建议认二元——order_block 是唯一已上线消费点，动它就是改已上线的订单冻结行为。

**[4]** 007:79『档位变更即时生效（吃 R2-04 Redis pubsub 配置广播）』的口径修正：实测 ConfigService 生产零读者、pubsub 在失效一份没人读的缓存，且 config bus 是 fail-open 而档位必须 fail-closed。改为『档位每决策直读、不进缓存』。这是修改已定案文档的措辞。

**[5]** audit_to_listing auto 档准入门槛：收紧为 ready-only（D-Q25 找到货源才上架）还是沿用 audit_passed。listing/service.py:260 注释自认『sourcing 域 R2 接入后收紧为 ready-only』，而 DECISION-FORM.md:151 的 Q43（该门是否对所有建品强制）仍是开放点。auto 档一开就等于系统批量绕过货源门。

**[6]** auto 档 guardrail 键集合与默认值：pricing_watch（改价幅度上限/单批上限/仅 live 非 locked——BR-MT-004『submit 必须人工触发，防误改在架商品』的动机转化）、audit_to_listing（每轮上限/default_store_id/offer_mode）、refund（amount_ceiling 超限时拒绝创建还是降 approval 档）。图纸只给了 amount_ceiling 一个键名、无默认值。

**[7]** 半自动停驻的超时兜底策略：定 SLA + 超时告警 / 超时自动降级 / 明确不管。三个停驻位当前全无扫描任务。

**[8]** refund auto 走哪个渠道端点（/v3/orders/{po}/refund vs /v3/returns/{ro}/refund，cancel 另走 /v3/orders/{po}/cancel），决定 ck_cc_action 要扩 1 个还是 3 个 action；灰度期仅 is_test 店真实执行（07:209 已有纪律）；工单等级改标【L1（refund 执行片 L2）】对齐 D-Q54。

**[9]** 权限点命名与授予矩阵：automation.policy_read / automation.policy_write（读写分离，对齐 channel.store_read/quota_write）还是单个 automation.policy_admin（对齐 audit.policy_admin）；module 取 'automation' 还是 'platform'；写权限给哪些角色（档位是团队决策 → 建议仅团队管理员）。

**[10]** 策略面板归属与超管形态：独立 /automation 页 vs 配置中心页第一个 Tab；单团队一张表 vs 全团队矩阵；新团队是否需要『默认档模板』继承。

## 3 盲区（六路都没覆盖，但 R2-09 必须知道）

**[1]** 【半自动停驻积压无兜底】三个停驻位（refund_request.pending_approval / maintenance_task.scheduled / product.audit_passed）全部没有超时扫描——我核了 automation/tasks.py:988-1007 的 TASKS 全表 19 个任务，没有任何一个扫过期停驻项，也没有 SLA 概念。验收只说『半自动在设定环节停』，停了之后堆积多久、谁被通知、超时降级还是告警，图纸零字。要问 Owner：每个 semi 停点是否定 SLA + 超时告警。要查：tasks.py 的 suspension_reminder（:798 附近）可作现成模板——R2-07 07b 已踩过『notify 24h 窗跨不过周期会架空 remind_days』的坑，别重犯。

**[2]** 【读档与动作的原子性，不是『多久生效』】六路把 60s 全部当成缓存/延迟问题，没人讨论『读档之后、动作提交之前切档』的竞态。procurement.py 因档位读与业务写共用同一 session/事务快照天然安全；但 auto 档 beat 推进器是『读档→长事务推进整批』，切档落在中间就以旧档跑完整批。要查：automation/tasks.py 各任务的 system_tx 边界（catalog/variant.py:684-721 的逐团队隔离写法是参照）。纪律要写进工单：档位读必须与被闸住的写同事务，且每条决策读一次——beat 任务级硬超时默认 900s（beat.py:36-37），任务级读一次的最坏陈旧是 900s+30s，直接击穿 60s。

**[3]** 【切档时在途对象归属未定】refund 是创建时快照 mode_applied（切档不影响在途，正确）；order_block 是实时求值（立即改变）；audit_to_listing / pricing_watch 未定。auto→manual 时，已被 auto 档 allocate 出来但没 submit 的 draft listing 归谁？已入 channel_command outbox 的命令要不要撤？D-Q64 批次原子性只覆盖变体组批次、不覆盖档位切换。要查 specs/001-domain-model/00-conventions.md 有无通用『口径切换』规则（路线1 已证实连 automation 边界纪律都没落笔）。必须逐 flow 声明『实时求值 vs 创建快照』做成表落进 09 图纸——路线2 提出了这个二分但六路没一路把表列出来，而『切档 60s 生效』在快照型 flow 上根本无法定义（在途单不变是正确行为）。

**[4]** 【auto 档会把号池和配额烧穿】路线3 提到 auto 档无人值守烧 GTIN + 扣 listing_create 配额（listing/service.py:322-338、:451），但六路都没核算量级。要查：listing/gtin.py 的池余量查询 + store_quota_config 现有配额值 + 0037 的 batch 默认 5。验收『同一商品跑一遍』是单件跑得通，但 auto 档一旦对全量 audit_passed 开闸，第一晚就可能烧穿号池。必须给每个 auto 档定『每轮上限』config 键 + 默认值（Owner 定），并复用现有 rejected/SAVEPOINT 隔离路径 fail-closed。

**[5]** 【验收本身不可重复执行】验收要求『同一商品在三档下各跑一遍全链』，但商品状态单向前进（ingested→audit_passed→listing draft→live），跑完 auto 档就回不去 manual 档。全仓无状态回退工具（tools/audit_replay.py:166 是离线重放，不改 product.status）。这条会在验收当天卡住。必须在拆单时就定取证口径：三档各用一件同 SKU 家族的不同商品（A/B/C），或写一个 is_test 店专用重置脚本，并写进 runbook。六路无人提。

**[6]** 【策略面板在超管视角的形态】面板是『当前团队一张表』还是『全团队矩阵』？超管代表团队改档走 X-Act-Team（frontend/src/api/client.ts:71-73 已自动带），audit_log 记的是超管还是被代表团队？D-Q30『共享开关仅超管』与档位什么关系？要查：backend/src/erp/core/deps.py 的 X-Act-Team 解析 + 0025 RLS 的 is_super 分支 + identity/router.py:120-141。六路都默认了『单团队一张表』，没人问。

**[7]** 【新团队的档位继承】automation_policy 无种子、create_team 不建 policy 行 → 新团队 0 行 = 全 manual（fail-closed，方向正确）。但是否需要『平台默认档模板』让新团队继承运营已调好的档位？图纸没这个概念。要问 Owner：每个新团队从零手调 6~8 个 flow，还是给 system_config 加一份默认模板。影响面板设计（要不要『应用默认模板』按钮）。

**[8]** 【enabled=false 是安全退化】两处读点 SQL 都带 AND enabled，即『停用策略行』静默等价于『无行』=manual。在 order_block 上这意味着误停用 = 拦截失效且无任何告警。面板必须显性区分『未配置 / 已停用 / manual』三种状态而非都显示为『人工』，停用时应记 warn 日志。路线2 提了一句但没人把它列成面板设计要求。

## 4 建议增量拆分
> 按铁律2「每个工单一个原子目标」，每个增量独立 CI 绿 + 独立验收。

### 增量0（前置，规划侧非 PR）：flow 清单 v2 冻结 + 图纸落笔
- **原子目标**：把 R2-09 的范围边界与三档语义从『可议』变成『已冻结』，让后续所有增量有唯一权威可对照
- **范围**：ar 帽改 001§09（flow 清单 v2：删 2 加 1~2；三档语义表 flow×{manual,semi,auto} 每档停在哪一步/谁推下一步/留什么痕；实时求值 vs 创建快照口径表；档位取值 fail-closed 与配置广播 fail-open 分述；automation_policy 不进缓存一句话）+ 00-conventions 补『automation 只编排不持业务状态』（external-review-round-1.md:28 已采纳的欠账清偿）+ 把 openapi-v0.yaml:2159 的 manual→record/semi→approval/auto→auto 映射提升为 09 通用映射表 + 007 验收判据按裁定改四环或三环
- **验收**：图纸 diff 经 Owner 批准合入；Owner 决策点 1/2/3/4/5 在文档里各有一处明确落笔（可 grep 到）；三档语义表覆盖冻结后全部 flow_code、无一行留白
- **风险**：不做这一步，增量1 的 Enum 写不出来、CI 校验第一天就红。整单唯一硬阻塞，必须先过。

### 增量1：policy 内核 + flow Enum + CI 一致性校验 + 两处旧代码回接（纯重构，行为零变化）
- **原子目标**：建立档位读取的唯一真相源，并交付 09:156 明写的『代码 Enum 对照 + CI 校验』
- **范围**：新建 backend/src/erp/automation/policy.py（FLOWS Enum、resolve_mode(session,*,team_id,flow_code) 无缓存直读、缺行=manual、beat 侧强制显式 WHERE team_id 防 is_super 绕 RLS 越权）；CI 加一步校验代码 Enum ⊆ 001§09 清单；procurement.py:31-58 与 refund.py:32-42 回接同一 helper；顺手清偿两笔：order_block 消费 config.check_kinds（09:162 规定但代码从不读，test_procurement.py:272 甚至写了却不生效）、maintenance.py:29 的 config.get("kinds", ["delist"]) 改默认 []（现存唯一 fail-open，与 D-Q65② 人工闸抵触）
- **验收**：CI 绿且现有 pytest 全过（行为零变化，既有测试即回归网）；负向证明：代码 Enum 里加一个图纸没有的码 → CI 必红；新增 check_kinds 过滤测试（空 config 保持现状拦全部 kind，配了则只拦配置内的）；新增 kinds 缺键默认 [] 不执行下架的测试；ruff/mypy strict 干净
- **风险**：低。唯一风险面是碰已上线的 order_block 冻结语义——check_kinds 过滤必须保证『空配置=拦全部』而非『拦零个』，否则是安全退化。maintenance.py 一行属跨域清偿，需知会 listing owner、不得扩成重构。

### 增量2：策略读写 API + 权限点 + 前端策略面板（不接新 flow，只管现有 order_block/refund/cancel）
- **原子目标**：让运营能在 UI 里看到并切换档位，并把『切档 60s 生效』这条硬指标在一个 flow 上先证明掉
- **范围**：002 契约新增 GET /automation-policies（后端做『flow 注册表 ∪ 库中行』合并，缺行返回虚拟行 mode=manual——避开 PR#35 那类 0 行无行可点陷阱）+ PUT /automation-policies/{flowCode}（upsert，表无 DELETE 权限）；migration 种 automation.policy_read/write 权限点 + role_permission（照抄 0031:83-99）；pnpm gen:api + /automation 页（三档 Segmented、enabled Switch、config 抽屉、per-flow 语义说明列、updated_by/updated_at + 审计日志入口、无写权限退化只读 Tag、『未配置/已停用/manual』三态显性区分）；写端点经 AuditWriter 留痕（60s 取证的 T0 锚点）
- **验收**：008§5 六项上车清单逐条过（契约与 schema.d.ts 同 PR、路由+菜单+权限三处注册、三态齐全、零手写响应 interface、runbook 同 PR、lint+build 绿），分页豁免理由写进 PR；新团队 0 行也能在 UI 设档（负向测试）；改档后 audit_log 有记录；60s 硬指标第一半：改 order_block 档 → 立刻调被闸端点 → 行为已变，T1−T0 秒级可核；Redis 全停复跑该验收结果不变（反证不依赖 pubsub）
- **风险**：中。前端是从 0 建的整层（契约+codegen+页+权限种子），是被工单一句『前端策略面板』严重低估的一块。注意 api.put 不带 Idempotency-Key（client.ts:130-131），若契约要求该头需先补 client。

### 增量3：audit_to_listing 三档（含 scrape_to_audit，若增量0 获批登记）
- **原子目标**：打通『审核→上架』这一段的三档（D-Q13 唯一直接授权的 flow）
- **范围**：auto 档推进器做成 beat 任务而非内联钩子（scrape 侧 submit_result 全程在一个 system_tx 大事务里，而 audit_one 是刻意的 tx1→LLM HTTP 零锁→tx2 三段式，内联会把 LLM 出网塞回持行锁事务，违反 RS-03a）；semi 档=批量送审端点 + 批量分配（前端 rowSelection 已有）；修 allocate 里 listing_state_history 的 actor_type 硬编码 'user'（listing/service.py:344-348）改传参，否则 auto 档把系统动作记成人工、可审计性破功（transition() 本身已支持 actor_type='system'）；config 扩 batch_size + default_store_id + offer_mode + 每轮上限；准入按 Owner 裁定 ready-only 或 audit_passed
- **验收**：三件同 SKU 家族商品各走一档，跑完 采集→审核→上架 段：auto 零人工介入、semi 停在约定环节（audit_passed 或 draft）且发通知、manual 每环节停；auto 档动作在 listing_state_history 里 actor_type='system'；GTIN 池空/配额不足走既有 rejected 路径不污染整批；每轮上限生效（超出不做）
- **风险**：中高。auto 档无人值守烧 GTIN 号池 + 扣 listing_create 配额，必须先有每轮上限才能开；D-Q25 货源门未裁定时 auto 档等于系统批量绕过货源门。

### 增量4：pricing_watch 三档 + 第二套档位语义收口
- **原子目标**：打通盯价改价三档，并把 30% 确认阈值这套体外档位语义收口进统一口径
- **范围**：新建 pricing_watch beat（生成 maintenance_task price_sync, status=scheduled）+ schedule 种子；maintenance.py 补 price_sync 执行分支（调 push_price，现状 else 抛 MAINT_KIND_UNSUPPORTED）；semi 档补 maintenance_task 单条/批量放行端点 + 前端维护任务列表（或并入上架页 Tab）；建议价不加列，runner 内按策略重算（cost_plus 对 product.price_snapshot 确定性，更新鲜且顺带解决 422 后建议丢失）；auto 档撞 30% 阈值（BR-PR-008，pricing/service.py:434-445）不许静默 force——标 skipped + 通知，下轮重新生成
- **验收**：三档各跑一遍定价段：manual 只生成任务不执行、semi 生成+人工放行后执行、auto 直接改价并在 A152 is_test 店真出门 + price_recon 归位；负向测试：auto 档撞 30% 阈值不静默 force（断言无 force=True 调用、任务落 skipped 并有通知）；改价批量走聚合而非高频单发
- **风险**：中高。真渠道价格写；PRICE_AND_PROMOTION feed 是 10/hour 的店铺级共享池（与 legacy price feed、批量促销 feed 三入口共享），auto 档批量必须聚合提交，否则触发 429。

### 增量5：refund/cancel auto 档渠道执行链【本单唯一 L2 片】
- **原子目标**：接通 R2-07 有意留下的 auto 档执行段，让 refund_request 的 executing/executed/failed 三态可达
- **范围**：migration 扩 ck_cc_action（按 Owner 裁定的端点，可能是 2~3 个 action 而非 1 个）+ outbox.ACTIONS 同步；删 refund.py:89-93 的 REFUND_AUTO_NOT_WIRED fail-closed 分支 + 补 _INITIAL_STATUS['auto']='executing' + approved→executing 执行入口；outbox 三段式 + verify-back + 新建 refund_recon beat（镜像 ship_recon，tasks.py:872-914）；amount_ceiling 闸；灰度期仅 is_test 店真实执行（07:209）；统一『档位拒绝』状态码为 409（现状 ORDER_BLOCKED 409 vs REFUND_AUTO_NOT_WIRED 走 BusinessError 默认 422）并同步 002；收口 openapi-v0.yaml:1287/:1307/:2160 与 aftersale/router.py:4/:152/:233 四处『随 R2-09』描述
- **验收**：A152 is_test 店真实退款执行到 executed + channel_ref 落库 + executed_at 非空；verify-back 对拍一致；超 amount_ceiling 按裁定行为有测试；非 is_test 店 auto 档 fail-closed 不出门（负向测试）；新 migration up/down/re-up 实测
- **风险**：高。全单唯一不可逆的钱路径，也是工单等级该改标的地方。建议单独排期、单独评审，不与其他增量并 PR。

### 增量6（收尾）：三档全链验收取证 + runbook + 图纸/契约/工单回写
- **原子目标**：把两条验收硬指标做成可复核的证据表，并清偿全部描述性欠账
- **范围**：三档全链取证表（flow × mode × T1−T0 + 语义正确性逐条断言，T0 取 audit_log.created_at、T1 取首条带新档位快照的决策记录 / task_run.stats.mode）；Redis 全停复跑同一验收（反证不依赖 pubsub，且实测 fail-closed 底线）；runbook（切档步骤、生效验证怎么看、回滚、每 flow 三档语义表、半自动停驻积压怎么查）；002/001 里所有『随 R2-09』描述改掉；review_list R2-09 的 check 文案改掉过时的『仅通 order_block』并回写状态
- **验收**：验收①同一商品三档各跑一遍全链，三份证据在档（.agent/evidence/R2-09/）；验收②切档 60s 生效在每个真三档 flow 上各有一条 T1−T0 记录且均 ≤60s；runbook 在部署机实测过一遍；CI 全绿；工单状态回写
- **风险**：低，但取证口径（T0 取哪个时刻、在途批次是否豁免）必须在增量0 就定好，否则收尾时返工。

---

## 5 六路侦察详录

## 路线1 · 决策链与图纸口径：三档语义与 flow 全集的权威定义

### 1. 决策原文：三档是什么、作用域到哪

| 决策 | 出处 | 原文要点 | 对 R2-09 的授权范围 |
|---|---|---|---|
| D-Q13 | `specs/000-founding/DECISION-FORM.md:31` | 「审核→上架三阶段演进：**前期人工逐批 → 中期半自动 → 后期全自动，模式可切换**」；架构影响=「自动化策略引擎从第一天按'三档开关'设计，**不做硬编码流程**」 | 只直接授权 `audit_to_listing` 一条；同时下了「引擎化、不硬编码」的架构硬要求 |
| D-Q29 | `DECISION-FORM.md:118`（原始问句 `:89`） | 「取消/退款现全人工；目标：记录 → 审批流 → 自动处理（三阶段，**同 D-Q13 模式**）」；架构影响=「售后动作也走'三档自动化开关'模型」 | 授权 `refund` / `cancel`；是三档语义最完整的一条 |
| D-Q14 | `DECISION-FORM.md:32` | 合规命中=**软标记**进复核队列 + 提供「自动拦截」开关；「拦截强度是租户/团队级配置项」 | `compliance_block` 的来源——注意原文是**开关（二元）**，不是三档 |
| D-Q26 | `DECISION-FORM.md:115` | 跟卖盯价现行 1 次/天，后期可配频率 + 手动触发；「维护频率=团队级配置项」 | `pricing_watch` 的 config（frequency）来源 |
| D-Q65② | `DECISION-FORM.md:275`（2026-07-23 拍板） | 「**报错回收档位 = 人工闸**（…对齐 D-Q13/29 三档纪律）——…DELETE/republish 执行走 **maintenance_task runner 人工/半自动档**」 | **新增一个尚未登记的档位消费点**（见 §3 缺口 D） |
| D-Q54 | `DECISION-FORM.md:209` | 数据真实性等级 L0/L1/L2/L3 与验收绑定 | R2-09 标【L1】，但 refund 执行片是真实渠道写（见 §5） |

**作用域结论（权威）**：三档是 **「团队 × flow」二维的流程级开关**，不是全局开关。

```
specs/001-domain-model/09-platform.md:143-155
| team_id  | BIGINT | NOT NULL | 团队级开关（档位是团队决策）
| flow_code| TEXT   | NOT NULL | 见下方注册清单
| mode     | TEXT   | NOT NULL DEFAULT 'manual' CHECK IN (manual, semi, auto) | **默认最保守档**
| config   | JSONB  | NOT NULL DEFAULT '{}'  | 流程私有参数（频率/阈值/批量大小）
| enabled  | BOOLEAN| NOT NULL DEFAULT true
约束：uq_automation_policy (team_id, flow_code)
```

旁证：`PRD-v1.md:60`「三档自动化策略引擎(人工/半自动/全自动, **各流程可切换**)」；
`PRD-v1.md:11`「自动化程度**可按团队调档**」；`00-conventions.md:66`「业务参数（阈值/频率/开关）一律入 `system_config`/`team_config`/`automation_policy`，禁止写死在代码」。

→ 前端面板必须是「团队维度 × flow 列表」矩阵；新团队/新 flow 缺省 `manual`（fail-closed，与 D-Q37 稳定优先一致）。

---

### 2. 权威 flow 清单表（R2-09 的范围边界）

图纸原文出处：`specs/001-domain-model/09-platform.md:156-166`，标题即「flow_code 注册清单（**v1，代码 Enum 对照 + CI 校验**）」——「Enum 对照 + CI 校验」是图纸明写的交付物。

清单是 **7 行 / 8 个 flow_code**（`refund / cancel` 同行但是两个独立码，见 `07-order-sourcing-aftersale.md:200`「从 automation_policy(flow=refund / flow=cancel) 快照」）。

| # | flow_code | 图纸出处 | 三档语义（原文） | 是否真三档 | 半自动定点停位置 | 停点是否已指定 |
|---|---|---|---|---|---|---|
| 1 | `audit_to_listing` | `09:160` + `05-audit.md:25` | 人工逐批确认 / 半自动（批量待确认队列）/ 全自动进分配（D-Q13）；config=`batch_size` | ✅ 真三档 | pass 之后、**进「分配」之前**（分配=店铺+GTIN；`05:25`「决定 pass 后是否自动进入分配」） | 🟡 环节已指定，**承载实体未指定**（"批量待确认队列"在 001 里无表无状态） |
| 2 | `compliance_block` | `09:161` + `04-compliance.md:76` | 「off(=manual 纯软标记) / block 拦截（D-Q14）」；config=按 severity 分档 | ❌ **二元** | 不适用（不是"停"，是"拦"）；落点=`compliance_hit.action_taken CHECK IN (flag, block)` | ❌ semi 无定义 |
| 3 | `order_block` | `09:162` + `07:83-84` | 「四检 flagged 单是否冻结分配」；off=纯软标记（默认），on=flagged 单冻结在 `checked` 不进分配 | ❌ **二元** | flagged 单**冻结在 `checked`**（已落地：`07:84` manual=软标记；**semi/auto=建执行单/分配 409 冻结**） | ✅ 停点明确，但 **semi≡auto**（唯一已上线消费点） |
| 4 | `refund` | `09:163` + `07:188-201` | 「记录 / 审批 / 自动执行（D-Q29）」；config=`amount_ceiling`（auto 档金额上限） | ✅ 真三档 | `refund_request.status='pending_approval'`（`07:201`）；`mode_applied CHECK IN (record, approval, auto)`（`07:200`） | ✅ **唯一完整指定**（含契约映射 `openapi-v0.yaml:2159`：manual→record / semi→approval / auto→auto） |
| 5 | `cancel` | 同上（`09:163` 合并行 / `07:200` 拆码） | 同 refund | ✅ 真三档 | 同 refund（`kind='cancel'`） | ✅ |
| 6 | `pricing_watch` | `09:164` + `06-listing-pricing.md:161` | 「盯价改价：仅报告 / 待确认 / 自动改价」；config=`frequency`（默认 1/日，店铺覆盖 D-Q26） | ✅ 真三档 | 应停在 `maintenance_task` 的「待确认」——但 **`06:151` status CHECK IN (scheduled, running, done, failed, skipped) 无该态** | ❌ **停点无处可落（缺状态）** |
| 7 | `gtin_alert` | `09:165` **vs** `03-catalog.md:121` | 「水位告警阈值」warn_pct/critical_pct | ❌ 无档位语义 | — | ❌ 且**已被取代**：实际落点=`team_config` 键 `gtin.warn_pct`/`gtin.critical_pct`（R2-04 beat `gtin_watermark` 已落地） |
| 8 | `suspension_reminder` | `09:166` **vs** `09:173/184-187` | 「封店提醒节奏（D-Q33）」remind_days | ❌ 无档位语义 | — | ❌ 且**已被取代**：R2-07 07b 落地把 `remind_days=7` 放进 **schedule 种子 config** |

**统计口径**：8 码中真三档 4 个（`audit_to_listing` / `refund` / `cancel` / `pricing_watch`）、二元 2 个（`compliance_block` / `order_block`）、纯参数寄生 2 个（`gtin_alert` / `suspension_reminder`，均已有别的落点）。

**建议动作（需 ar 帽落笔 + Owner 认可）**：
- 删 `gtin_alert`、`suspension_reminder` 两行 → 避免「同一参数双落点、运营改了不生效」的经典坑，R2-09 接线面直接收窄 25%；
- 给 `compliance_block`/`order_block` 在图纸注「本 flow 二元，semi≡auto」，面板隐藏 semi（**不要**在贯通单里改已上线的订单冻结语义）；
- 把 `openapi-v0.yaml:2159` 的映射（manual→record/semi→approval/auto→auto）提升为 09 图纸的**通用档位映射表**——这是全仓唯一权威的「三档语义统一」文本。

---

### 3. 图纸未登记、但被决策/图纸自身要求的 flow（4 处缺口）

| 缺口 | 出处 | 说明 |
|---|---|---|
| A. `scrape_to_audit`（采集完自动送审） | 仅 `007:77`（+ review_list R2-09.check 转抄）；`specs/` 全目录再无出处 | **工单发明、图纸未授权**。但 007 验收要求采集→审核这一环能停（见 §4），删不掉，须拍板 |
| B. 上架内部环节（分配→spec 构建→feed 提交） | 无任何 flow_code | `listing.status` 有 `draft → queued`（`06:17,36`），天然可作停点，但没有开关驱动 |
| C. match 模式跳过 sourcing | `03-catalog.md:32`「match 模式跳过 sourcing **由策略配置（automation_policy）**」 | 第 3 个未命名消费点；不显式化则 match 链在三档验收里行为不可解释 |
| D. maintenance runner 人工/半自动档 | `DECISION-FORM.md:275`（D-Q65②，**宪法级**） | 「DELETE/republish 执行走 maintenance_task runner 人工/半自动档」；`06-listing-pricing.md:145-161` 全表**无档位列、无 flow 关联** → D-Q65② 目前悬空 |

D 是最硬的一条：它出自 DECISION-FORM（最高优先级），而不是图纸外推。R2-09 若不接，等于静默偏离宪法。

---

### 4. 007 与 001 的口径不一致（逐条）

| # | 类型 | 007 措辞 | 001 图纸 | 裁决方向 |
|---|---|---|---|---|
| 1 | **命名错误** | `listing_pricing`（`007:76`） | `pricing_watch`（`09:164`，且 `06:161` 已用此名作 config 落点） | 归一到 `pricing_watch`（001 为图纸权威） |
| 2 | **无出处** | `scrape_to_audit`（`007:77`） | 全仓无 | 需 Owner 定：登记 v2 新码 / 或删 |
| 3 | **判据超出供给（最实质）** | 验收要求「采集→审核→上架→定价」**四环各自可停**（`007:81-82`） | 只供给两环：`audit_to_listing`（审核→上架衔接）+ `pricing_watch`（定价） | 二选一：①补登 `scrape_to_audit`(+`listing_dispatch`) 进清单 v2；②验收改为「审核→上架→定价 三环」。**否则工单验收天然不可达** |
| 4 | 措辞（无实质冲突） | 「补全 flow 枚举与消费点，**非建表**」（`007:73`）；用「等——**以 §09 automation 图纸 flow 清单为准**」（`007:77`） | `09:156`「v1，代码 Enum 对照 + CI 校验」 | 方向一致：001 优先，且 R2-09 须交付 Enum↔图纸的 CI 校验 |
| 5 | 「非建表」需打折 | `007:73` | `06:151` 缺待确认态；`audit_to_listing` 待确认队列无实体；refund 执行需 outbox 命令类型 | R2-09 **仍会动 migration（ar 帽）**，只是不动 `automation_policy` 本表 |

`007:44` 的原委（退款三档执行归 R2-09）：R2-07 只建申请表，档位**执行**留给 R2-09——与 001 落笔完全吻合：
```
specs/001-domain-model/07-order-sourcing-aftersale.md:211-219
已落地（R2-07 增量2，0031）：本表图纸原样落库；record/approval 两档本地闭环开通…
**auto 档在 R2-09 flow=refund 接线前 fail-closed 拒绝创建**（REFUND_AUTO_NOT_WIRED，不做静默降档）；
approved 为驻留态，渠道执行（executing→executed，outbox return_refund + verify-back）随 R2-09。
```
契约侧同步声明：`openapi-v0.yaml:1287`「创建退款/取消申请（档位随建快照 D-Q29；**auto 档 R2-09 接线前拒绝**）」。

---

### 5. PRD §8 模块 9 原文

```
specs/000-founding/PRD-v1.md:130  ## 8. 迭代切分（R1 行走骨架 → R2 领域纵深）
:137  ### R2 领域纵深（两周一迭代，每个模块影子对拍/A152 实测通过才算完成）
:138  建议顺序（低风险只读 → 高风险写操作）：
      …
:147  9. 三档自动化策略引擎全流程贯通
:148  - MVP 完成判据：上述 1-9 在 A152 + 一个试点团队跑通闭环。
```
配套：`PRD-v1.md:11`（愿景句「自动化程度可按团队调档」）、`:56`（aftersale 域「退款(记录→审批→自动 三档)」）、`:60`（automation 域一行）、`:73`（5.1 建品主流程图上「↑ 审核→上架衔接: 三档(人工逐批/半自动/全自动) 可切换 [D-Q13]」）。

PRD 对模块 9 只有四个字「全流程贯通」，**没有 flow 清单**——所以图纸口径的唯一权威确实是 `001§09:158-166`，007 也是这么声明的。PRD 把模块 9 排最后（高风险写操作总闸），与 `007:173` 定的动工顺序（…→R2-12→**R2-09**→R2-08→R2-10）一致。

历史旁证：`005-r2-plan/README.md:56` 把「automation 三档面板」列在「后续（顺序待 Owner 定）」——这正是 007 立单要补的断层；`003-r1-plan/README.md:87` R1 演示脚本已使用「(人工确认，**D-Q13 manual 档**)」措辞。

---

### 6. business-rules-ledger 相关规则条（与决策的张力）

grep 自动化/automation/三档/半自动 命中的与档位真正相关的两条，**都与 D-Q13/29 冲突**：

| 规则 | 出处 | 原文 | 裁决 |
|---|---|---|---|
| BR-AS-008 | `business-rules-ledger.md:200` | 「退款操作（POST refund）**明确不在自动化范围，人工执行**」（状态 ✅=旧系统现状） | **D-Q29 取代**（`DECISION-FORM.md:118`）。铁律 DECISION-FORM > ledger |
| BR-MT-004 | `business-rules-ledger.md:149` | 「`sync` 与 `poll` 自动化；**`submit` 必须人工触发**（防误改在架商品）」（✅） | **D-Q26 + `09:164` pricing_watch auto=自动改价 取代**；但其**动机必须转成 auto 档护栏** |

**建议**：为每个 auto 档在 `automation_policy.config` 定义一组 guardrail 键，与 refund 的 `amount_ceiling`（`09:163`）同构——`pricing_watch` auto 档至少需要「单次改价幅度上限 / 单批量上限 / 仅 `status=live` 且 `is_locked=false`」。并在图纸标注这两条 ledger 规则「已被 D-Q13/29 取代，动机转为 auto 档护栏」，避免后续审计当成静默偏离。

其他 grep 命中（BR-ORD-005 的"三档"= 钓鱼命中三档、BR-SCH-* 调度节律、C8/C10 冲突条）与档位无关，不构成 R2-09 约束。

---

### 7. 「切档 60s 生效」的图纸依据与一个 fail-open/fail-closed 矛盾

| 要素 | 出处 | 内容 |
|---|---|---|
| 60s 来源 | `09-platform.md:257` | 「配置读取契约：服务层统一 ConfigService（**带 60s 进程缓存 + 变更失效广播**）；代码里出现魔法数字即 CR 打回」 |
| 同款 | `003-r1-plan/README.md:32` | ConfigService…「60s 进程缓存 + 失效广播（Redis pubsub）」 |
| 广播频道 | `09-platform.md:190` | 「配置失效跨进程广播经 Redis pubsub `erp:config:invalidate`（**fail-open**，TTL 兜底）」 |
| 验收 | `007:82` | 「切档 **60s 内**生效」 |

→ 验收的 60s 与既有基建吻合（TTL 兜底天然满足）。**但两处待补**：
1. `09:190` 只覆盖 `system_config`/`team_config`，**`automation_policy` 是否走同频道图纸未写** —— 建议复用 `erp:config:invalidate` 并在图纸补一句；
2. `09:190` 写的是 **fail-open**，而档位是安全开关 —— 必须区分：「**配置广播 fail-open，档位取值 fail-closed**（读不到 policy 时按 `manual` 兜底，对齐 `09:150` DEFAULT 'manual' 与 refund 的 `REFUND_AUTO_NOT_WIRED` 不静默降档纪律）」。这一句落笔比接线更重要。

---

### 8. 两条会咬到 R2-09 的既有欠账

| 欠账 | 出处 | 对 R2-09 的约束 |
|---|---|---|
| **automation 边界纪律未落笔** | `external-review-round-1.md:28`「④ automation 边界纪律（**只编排不持业务状态**）写进 001 conventions」；`00-conventions.md` 全文无此句（automation 仅见 `:66`/`:101`） | R2-09 是第一个大写 automation 域的工单：档位判定只读 policy + 调各域服务；**业务停留态一律落在各域自己的表**（product/listing/maintenance_task/refund_request），automation 域不得新建「待确认商品表」。建议随单把这句补进 00-conventions（评审已采纳=清偿欠账，非新决策） |
| **通用待办箱 = RS-06 P1，图纸不存在** | `external-review-round-1.md:27,143,199`（「各域自建 `review_case`，统一待办箱只做只读投影 → RS-06」，P1）；`001-domain-model/` 下 `review_case` **零命中** | `audit_to_listing` 的「批量待确认队列」在 R2-09 期内**没有通用容器可用** → 各域就地实现（product 侧待确认标记 / listing 停在 `draft` / maintenance_task 加待确认态），并在图纸注「RS-06 落地后收敛为只读投影」，避免造一个将来要拆的通用待办箱 |

---

### 9. 契约与前端面板：被工单一句话严重低估的一块

`specs/002-api-contract/openapi-v0.yaml` 对 `automation_policy` **只有一处描述性引用**（`:2159` refund 的 `mode_applied` 映射），**没有任何 policy 读写路径**（grep `^  /` 无 automation/policies）。

→ 「前端策略面板」实际需要：一整组契约（policy 列表/更新，团队维度）+ codegen 类型重生成 + 权限点（`automation.read`/`automation.write`）+ AuditWriter 留痕（档位变更是高危操作，`automation_policy` 已有 `updated_by`，`09:153`）。且按 `CLAUDE.md` 前端规范与 `specs/008-frontend-conventions/README.md`，必须走统一 client + codegen 类型，禁手写响应 interface。

---

### 10. 给 R2-09 的范围建议（一句话版）

以 `001§09:156-166` 为边界，**删 2（gtin_alert/suspension_reminder）、认 2 为二元（compliance_block/order_block，semi≡auto 注图纸）、真做 4（audit_to_listing / refund / cancel / pricing_watch）**，另请 Owner 一次性拍板 4 个未登记 flow（`scrape_to_audit`、上架内部环节、match 跳 sourcing、**D-Q65② maintenance runner 档位**）是否进清单 v2；同时接受「本单必然动 migration」（pricing_watch 待确认态 + audit_to_listing 待确认落点 + refund 执行的 outbox 命令类型），并把工单等级修正为【L1（refund 执行片 L2）】以对齐 D-Q54。

## 路线2：automation_policy 现状接线盘点——"只通 order_block"到底什么意思

结论先给：**"只通 order_block"这句话在 2026-07-16 立单时准确，到今天（R2-07 增量2 落地后）已经不精确了。**
现状是 **1 个 flow 全通（order_block）+ 1 个 flow 半通（refund/cancel：manual/semi 本地闭环、auto fail-closed 拒绝）+ 5 个 flow 零接线 + 2 个 flow 用别的配置源实现（语义分裂）+ 1 套体外三档土办法（maintenance kinds）**。
R2-09 的实质工作量不是"补 4 个 flow"，而是**先统一档位读取入口与三档语义表，再逐 flow 接线**。

---

### 2.1 表结构（0025 迁移原文）与 ORM 现状

DDL 原文 `backend/alembic/versions/0025_order_domain.py:296-314`：

| 列 | 类型 | 约束/默认 | 语义 |
|---|---|---|---|
| id | bigint | GENERATED ALWAYS AS IDENTITY PK | |
| team_id | bigint | NOT NULL REFERENCES app.team(id) | **档位是团队级决策**（09:148），无 store 级覆盖 |
| flow_code | text | NOT NULL | 流程标识；无 CHECK 约束、无外键、无枚举表 |
| mode | text | NOT NULL DEFAULT `'manual'`，`ck_automation_mode CHECK (mode IN ('manual','semi','auto'))` | 三档；**默认最保守档**（09:150） |
| config | jsonb | NOT NULL DEFAULT `'{}'` | 流程私有参数（batch_size/check_kinds/amount_ceiling/frequency…），**无 schema 校验** |
| enabled | boolean | NOT NULL DEFAULT true | 停用开关 |
| created_at / updated_at | timestamptz | NOT NULL DEFAULT now() | updated_at 由触发器维护（`:313` TOUCH → `app.touch_updated_at()`） |
| updated_by | bigint | NULL | 无 FK，仅留痕 |

唯一键：`uq_automation_policy ON app.automation_policy (team_id, flow_code)`（`0025:310`）——保证任一 (团队, 流程) 至多一行，这是两处读取都敢用 `scalar_one_or_none()` 的前提。

**team_id 隔离怎么做的**（`0025:314` 套用 `:30-39` 的 `TEAM_RLS` 模板）：

```
ALTER TABLE app.automation_policy ENABLE ROW LEVEL SECURITY;
CREATE POLICY ..._sel FOR SELECT USING (team_id = app.current_team() OR app.is_super());
CREATE POLICY ..._ins FOR INSERT WITH CHECK (同上);
CREATE POLICY ..._upd FOR UPDATE USING/WITH CHECK (同上);
```

三点必须知道：
1. **没有 DELETE 策略**，且 `0025:359-361` 的 GRANT 只给 `SELECT, INSERT, UPDATE`（"最小面：无 DELETE"）→ **策略行建了就删不掉**。面板语义上"关闭自动化" = `mode='manual'` 或 `enabled=false`，不是删行。R2-09 的写端点必须是 upsert（`ON CONFLICT (team_id, flow_code) DO UPDATE`，可照抄 `backend/tests/db/test_refund_request.py:126-129`）。
2. GUC 由请求层注入（`backend/src/erp/core/db.py:43-50` `ctx_tx` / `:62-64` `system_tx`）。beat/worker 读策略必须走 `system_tx`（is_super 绕 RLS）并**显式带 team_id 过滤**——现有两处生产代码都是 RLS + 显式 team_id 双保险（`procurement.py:36-40`、`refund.py:36-40`），R2-09 照此纪律。
3. downgrade 直接 `DROP TABLE ... CASCADE`（`0025:388`）。

**ORM 模型：不存在。** 全仓无 SQLAlchemy declarative（`Mapped[` / `declarative_base` / `DeclarativeBase` 在 `backend/src` 零命中），项目统一 raw SQL + `text()`。因此：
- `09-platform.md:156` 写的"flow_code 注册清单（v1，**代码 Enum 对照 + CI 校验**）"**完全没落**——代码里没有 flow_code 常量，`.github/workflows/ci.yml` 没有对应校验。这是 R2-09 的显性欠账，也是最便宜的第一步增量。
- **无种子行**：全仓无 `INSERT INTO app.automation_policy`（只有两个测试 fixture 写）。即所有团队默认零策略行——缺省行为是常态路径，不是边角。

---

### 2.2 全仓触点清单（逐个 file:line + 干什么）

#### 生产代码（只有 2 处读，0 处写）

| # | file:line | 读什么 | 干什么 |
|---|---|---|---|
| 1 | `backend/src/erp/order/procurement.py:31-58` | `SELECT mode WHERE team_id=:t AND flow_code='order_block' AND enabled` | `_order_block_gate()`：semi/auto 档 + 存在未放行 flagged 四检 → 抛 `ORDER_BLOCKED` 409 冻结 |
| 1a | `backend/src/erp/order/procurement.py:126` | — | `create_po()` 建执行单前调闸 |
| 1b | `backend/src/erp/order/procurement.py:172` | — | `assign_po()` 分配采购方前调闸（`create_po` 带 purchaser 时会经 `:144` 二次过闸） |
| 2 | `backend/src/erp/aftersale/refund.py:32-42` | `SELECT mode WHERE team_id=:t AND flow_code=:f AND enabled`（f = kind ∈ refund/cancel） | `_resolve_mode()`：manual→record / semi→approval / auto→auto，无行→record |
| 2a | `backend/src/erp/aftersale/refund.py:88` | — | `create_request()` 建申请时**快照**档位落 `refund_request.mode_applied`（`:118`）并决定初始状态（`:119`） |

**没有任何 beat 任务读 automation_policy**——`backend/src/erp/automation/tasks.py:988-1007` 的 TASKS 注册表 19 个任务全部与它无关。这一点对 R2-09 关键：三档"全自动"档需要一个**驱动者**（beat 任务或实时钩子），而现在整个 automation 域只有"闸"没有"驱动"。

#### 测试触点（R2-09 可直接扩的现成 fixture）

| file:line | 内容 |
|---|---|
| `backend/tests/db/test_procurement.py:269-273` | 写 `order_block`=semi + `config={"check_kinds":["phishing"]}`，验 409 + 放行后 201 |
| `backend/tests/db/test_procurement.py:286-288` | finally 复位 manual（测试间隔离靠手工复位，无 fixture 化） |
| `backend/tests/db/test_refund_request.py:123-130` | `_set_policy(db, team, flow, mode)` upsert helper——**R2-09 应提升为共享 fixture** |
| `backend/tests/db/test_refund_request.py:134-141` | 无策略行 → record/recorded（缺省行为的唯一断言） |
| `backend/tests/db/test_refund_request.py:192` | auto 档 fail-closed 拒绝、不静默降档 |

#### 契约/前端触点（只有一处描述，零 CRUD）

| file:line | 内容 |
|---|---|
| `specs/002-api-contract/openapi-v0.yaml:2159` | `mode_applied` 字段描述提到"从 automation_policy(flow=kind) 快照" |
| `frontend/src/api/schema.d.ts:5029` | 上一行的 codegen 产物 |

→ **契约里没有任何 `/automation-policies` 路径，前端没有任何策略面板，全仓没有 `automation.*` 权限点**（对照既有权限种子写法 `backend/alembic/versions/0031_refund_request.py:87-94`，与 `require_permission` 用法 `backend/src/erp/order/router.py:295`）。R2-09 的"前端策略面板"是契约→codegen→页面→权限种子的整层新建，按 008 规范上车。

#### 文档触点（图纸口径来源）

- `specs/001-domain-model/09-platform.md:143-166`：表定义 + **flow_code 注册清单 7 行**（唯一权威词表）
- `specs/001-domain-model/07-order-sourcing-aftersale.md:83`：order_block 语义（**原文是 off/on 二值**："off=纯软标记（默认），on=flagged 单冻结在 checked 不进分配"）
- `specs/001-domain-model/07-order-sourcing-aftersale.md:200`、`:213-215`：refund mode_applied 快照 + auto 档 R2-09 前 fail-closed + 执行链归 R2-09
- `specs/001-domain-model/05-audit.md:25`：audit_to_listing 决定 pass 后是否自动进分配（D-Q13）
- `specs/001-domain-model/04-compliance.md:76`：compliance_block 决定 action_taken=flag/block（D-Q14）
- `specs/001-domain-model/06-listing-pricing.md:161`：盯价频率配置在 `pricing_watch` 的 config（D-Q26）
- `specs/000-founding/DECISION-FORM.md:31`（D-Q13）、`:118`（D-Q29）、`:32`（D-Q14）、`:115`（D-Q26）：宪法级三档原文

---

### 2.3 现状接线矩阵（flow × 是否接线 × 接线点）

以 `09-platform.md:158-166` 注册清单为行（+ 工单额外提到的两个）：

| flow_code | 图纸出处 | 接线状态 | 接线点 file:line | config 是否消费 | 三档是否真三分 |
|---|---|---|---|---|---|
| `audit_to_listing` | 09:160 / 05-audit:25 / D-Q13 | **零接线** | 无。审核落状态 `audit/service.py:167,172-175`；进分配靠用户点 API `listing/router.py:178` → `listing/service.py:206`（准入 `service.py:260-261`） | `batch_size` 未消费 | — |
| `compliance_block` | 09:161 / 04-compliance:76 / D-Q14 | **零接线** | 无（全仓无 `flow_code='compliance_block'` 读取）。合规裁决 verdict 默认常量 `compliance/assertion.py:61` | 未消费 | — |
| `order_block` | 09:162 / 07:83 | **已接线（唯一全通）** | `order/procurement.py:31-58` + 调用点 `:126` / `:172` | `check_kinds` **写了不生效**（`:44-52` 无过滤；词表 `order/checks.py:35`） | ❌ semi 与 auto 同分支（`:42`） |
| `refund` / `cancel` | 09:163 / 07:200,213 / D-Q29 | **半接线**：manual/semi 本地闭环；auto fail-closed 拒绝 | `aftersale/refund.py:32-42` + `:88`；审批 `:135-172`；拒绝桩 `:89-93` | `amount_ceiling` 未消费 | ⚠️ 两档真实现，第三档拒绝 |
| `pricing_watch` | 09:164 / 06:161 / D-Q26 | **零接线** | 无。改价只有用户触发（`pricing/router.py` reprice 批量、`listing/router.py:340`）；TASKS 无盯价任务（`automation/tasks.py:988-1007`）。`listing.pending_price` 是**在途标记**不是审批队列（`pricing/service.py:368-373`） | `frequency` 未消费 | — |
| `gtin_alert` | 09:165 | **语义分裂**：功能已上线但走别的配置源 | `automation/tasks.py:614-615`（schedule.config 默认）+ `:626-643`（team_config/system_config 的 `gtin.warn_pct`/`gtin.critical_pct`） | 走配置中心而非 policy.config | — |
| `suspension_reminder` | 09:166 / D-Q33 | **语义分裂**：同上 | `automation/tasks.py:798`（注释明写"remind_days 读 schedule.config"）+ `:800` | 走 schedule.config | — |
| `scrape_to_audit` | **不在图纸词表**（工单 review_list:550 / 007:77 提出） | **零接线且未注册** | 无。采集入库 product 落 `status='ingested'`（`scrape/service.py:520-548`，默认值 `0007_scrape_catalog.py:65`）；`audit_one` 仅用户 API（`audit/router.py:53`）与离线 replay（`tools/audit_replay.py:166`） | — | — |
| `listing_pricing` | **不在图纸词表**（工单用词，疑=pricing_watch） | 命名待裁 | — | — | — |
| （体外）maintenance runner | 无 flow 注册，注释自称 D-Q13/29 三档 | **土办法三档** | `listing/maintenance.py:3-5,:29`（`config.kinds`）；种子 `0037_item_pull_schedule.py:34` `kinds=[]` | schedule.config.kinds | ❌ 用空列表模拟人工档 |

---

### 2.4 order_block 样板：完整调用链与可抽象的模式

#### 调用链

```
POST /api/v1/procurement-orders            （order/router.py:295 require_permission("order.assign")）
  └─ procurement.create_po                （procurement.py:98）
       ├─ SELECT channel_order FOR UPDATE  （:106-118；状态须 checked/assigned，:121-125）
       ├─ _order_block_gate ★              （:126）
       │    ├─ SELECT mode FROM automation_policy WHERE team_id AND flow_code='order_block' AND enabled   （:36-41）
       │    ├─ if mode not in ("semi","auto"): return                      ← manual / 无行 / enabled=false 全部放行
       │    ├─ SELECT 1 FROM order_check WHERE result='flagged' AND resolved_at IS NULL LIMIT 1   （:47-51）
       │    └─ raise BusinessError("ORDER_BLOCKED", "…放行或调档后再分配", http_status=409)        （:54-58）
       ├─ INSERT procurement_order         （:130-142）
       └─ （若带 purchaser_id）assign_po → _order_block_gate 二次过闸      （:144 → :172）
```

三档实际行为（**注意只有两种**）：

| mode | 行为 | 代码 |
|---|---|---|
| 无行 / `manual` / `enabled=false` | 放行；四检 flagged 只是软标记（并有通知，`order/checks.py:277-278` 文案明写"软标记不拦截（order_block 档位可改）"） | `procumbent.py:42` 早返回 |
| `semi` | 未放行 flagged → 409 冻结 | `:53-58` |
| `auto` | **与 semi 完全相同** | 同一分支 |

#### 抽象出来的模式（R2-09 要照抄的部分）

**模式名：写入口前置闸（gate-at-transition）**，四要素：

1. **档位读取**：`(team_id, flow_code)` 单行 SELECT + `AND enabled` 过滤 + `scalar_one_or_none()`（唯一键保证）+ `None` 视作最保守档。
2. **求值时机在状态跃迁入口**，不是常驻状态机：策略只在"要往前走一步"时被问一次。这是最重要的可复用洞见——它让"切档即时生效"天然成立（无缓存、无快照），也让每个 flow 只需找到"那一步"的函数。
3. **拒绝形态**：`BusinessError(大写蛇形错误码, 中文可操作提示, http_status=409)`，提示里必须写清"怎么解开"（"放行或调档后再分配"）。
4. **同一 flow 的多个入口都要挂闸**（`:126` 和 `:172` 各挂一次），否则旁路进入。

**没有可复用 helper。** `_order_block_gate` 是 `procurement.py` 的模块私有函数，`refund.py:32-42` 把同一段 SQL 又抄了一遍（差异只在 flow_code 是参数、以及多了一层词表映射 `_MODE_MAP`）。R2-09 第一个增量应该是：

```
erp/automation/policy.py（新）
  FLOWS: Final = {...}                                  # flow_code 注册 Enum（对齐 09:158-166，CI 校验）
  async def resolve_mode(session, *, team_id, flow_code) -> Literal["manual","semi","auto"]
  async def require_allows(session, *, team_id, flow_code, blocked_when, code, message)  # 可选的闸糖
```
并把 `procurement.py:31-58` 与 `refund.py:32-42` 回接到它——两处旧代码回接是"三档语义统一"最实的抓手，也顺带证明 helper 可用。

#### 样板的三个缺陷（**不要照抄**）

| 缺陷 | 证据 | R2-09 处置 |
|---|---|---|
| ① semi 与 auto 无差异 → 实为二值开关 | `procurement.py:42`；`07:83` 原文就是 off/on | 要么给 semi 定义"批量待放行队列"，要么承认 order_block 二值并回改 `09:162`。**这是"三档语义统一"的第一道裁定**，需 Owner |
| ② `config` 完全未读，写了不生效 | 09:162 规定 `check_kinds`；测试 `test_procurement.py:272` 真写了 `{"check_kinds":["phishing"]}`；gate `:44-52` 对任意 kind 的 flagged 都拦 | 消费 check_kinds（词表 `order/checks.py:35`），并给每个 flow 的 config 定 schema（面板表单也要它）。属静默偏离图纸，须清偿 |
| ③ 不经 ConfigService → 现有 pubsub 广播对它零作用 | `core/config_service.py:106`/`:109` 只查 system_config/team_config | 两条路：(a) 扩 ConfigService 第三来源；(b) 坚持 DB 直读（0 秒生效，"60s 生效"验收自动满足）。建议 (b) + 在 runbook 写明"policy 不缓存故无需广播"，把 pubsub 留给阈值类参数 |

#### 另一种口径：档位快照（refund 样板）

`refund.py:118` 把档位**写进 `refund_request.mode_applied`**（契约 `openapi-v0.yaml:2159`，DDL `0031:57-59`），即申请单一旦创建就锁定档位，事后切档不影响在途单。这与 order_block 的实时求值是**两种不同口径**：

- 实时求值：切档立刻改变下一次动作的行为（order_block、audit_to_listing、pricing_watch 适用）
- 创建快照：单据留痕档位，保证审批链前后一致（refund/cancel 适用）

R2-09 必须**显式给出每个 flow 用哪种口径并写进 09 图纸**，否则"切档 60s 生效"的验收判据在 refund 上无法定义（在途单不变是正确行为，不是 bug）。

---

### 2.5 埋桩、未使用枚举值、fail-closed 分支

**① R2-07 增量2 的 fail-closed 桩（工单点名要找的那段）——`backend/src/erp/aftersale/refund.py:88-93`：**

```python
mode = await _resolve_mode(session, team_id=team_id, kind=kind)
if mode == "auto":
    raise BusinessError(
        "REFUND_AUTO_NOT_WIRED",
        "auto 档渠道执行随 R2-09 接线后开放；请先将该流程档位调至 人工/半自动",
    )
```

模块头注 `refund.py:8-9` 写明设计意图："auto 档（auto）：接线前 fail-closed 拒绝创建（REFUND_AUTO_NOT_WIRED），**不做"静默降档"——档位语义不许偏离（铁律1）**"。
**第二道防线**：`refund.py:28-29` `_INITIAL_STATUS` 只有 `record`/`approval` 两键（注释："auto 在 R2-09 接线前到不了这里"）——即使有人删了 :89-93，`:119` 也会 KeyError 而不是静默落错状态。
**状态码不一致**：`REFUND_AUTO_NOT_WIRED` 走 `BusinessError` 默认 **422**（`core/errors.py:7-22`），而 `ORDER_BLOCKED` 显式 **409**（`procurement.py:57`）。两者都是"档位导致的拒绝"，R2-09 应统一口径并写进 002。
测试锚点：`backend/tests/db/test_refund_request.py:192`。

**② 已埋好、等 R2-09 用的表结构（`0031_refund_request.py`）：**
- `:57-59` `mode_applied` CHECK 含 **`'auto'`（当前不可达值）**
- `:60-63` `status` CHECK 含 **`executing` / `executed` / `failed`（当前全不可达）**
- `:70` `executed_at`（当前永远 NULL）
- 迁移头注 `0031:8` 自述"auto 档在 R2-09 flow=refund 接线前 fail-closed"
→ **表结构不用改**，R2-09 只需让这三个状态可达。

**③ 缺的东西（R2-09 必须新增）**：`channel_command.ck_cc_action` 现行集是
`('feed_submit','item_retire','order_ack','order_ship','price_push','item_maintenance')`（`0038_cc_action_maintenance.py:20-21`），**没有 `return_refund`**。而 `07:215` 明确"渠道执行（executing→executed，outbox return_refund + verify-back）随 R2-09"。→ 需要 0039+ 扩约束 + `outbox.ACTIONS` 同步扩（**migration 仅 ar 帽可动，开工前提单**）。

**④ 契约层已挂账的 R2-09 缺口**：`openapi-v0.yaml:1287`（"auto 档 R2-09 接线前拒绝"）、`:1307`（"approved 驻留待 R2-09 执行接线"）、`:2160`（status 描述）；路由层同注 `aftersale/router.py:4`、`:152`、`:233`。R2-09 收尾要把这四处描述改掉。

**⑤ 唯一的现存 fail-open 隐患（体外三档）**：`listing/maintenance.py:29`
```python
kinds = [str(k) for k in config.get("kinds", ["delist"])]
```
代码默认是 `["delist"]`（自动下架开着），而种子给的是 `[]`（`0037_item_pull_schedule.py:34`，人工档）。若 schedule 行的 config 丢了 `kinds` 键，runner 会**自动开始真渠道下架**。与 fail-closed 铁律相悖，R2-09 顺手清偿（改默认为 `[]`，或把该档位迁进 automation_policy）。

**⑥ 没找到的东西**（明确记录以免后人重复找）：全仓**无** `TODO`/`FIXME`/`XXX` 与 automation_policy 相关；无被注释掉的 flow 接线；无未使用的 flow_code 常量（因为根本没有常量）。埋桩全部集中在 refund 域与文档描述里。

---

### 2.6 默认值、缺省档位、缺策略时的行为

| 场景 | order_block（`procurement.py:42`） | refund/cancel（`refund.py:42`） |
|---|---|---|
| 无策略行 | 放行（不拦截） | `record`（只记账，不触达渠道） |
| `mode='manual'` | 放行 | `record` |
| `enabled=false`（任意 mode） | 放行（SQL `AND enabled` 过滤掉该行 ⇒ 等价无行） | `record`（同上） |
| `mode='semi'` | 拦截 409 | `approval`（pending_approval → 审批） |
| `mode='auto'` | 拦截 409（与 semi 同） | **422 拒绝创建**（未接线） |
| DB 层默认 | `mode DEFAULT 'manual'`（`0025:302`）、`enabled DEFAULT true`（`:305`）、`config DEFAULT '{}'`（`:304`） | 同一张表 |

**结论一：缺策略 = manual，两处一致，且都是 fail-closed 的"语义"实现**——但**行为方向相反**：
- order_block 的最保守 = **放行业务动作**（拦截是加码；`07:83` 原文 off 为默认，符合图纸）
- refund 的最保守 = **不执行渠道动作**（记账是降级）

这不是 bug，是"保守"在两个语境里指向相反操作。但它直接说明**"三档语义统一"不能靠一句"manual 最保守"**——R2-09 必须产出一张 `flow × {manual, semi, auto}` 的**语义表**（每档停在哪一步、谁来推下一步、留什么痕），落进 `09-platform.md`，然后代码照表实现。这张表就是工单验收"半自动在设定环节停、人工档每环节停"的判据来源。

**结论二：`enabled=false` 静默等价于 manual。** 对 order_block 意味着"误停用策略 = 拦截失效且无任何告警"。R2-09 面板应显性区分"未配置 / 已停用 / manual"三种视觉状态（而非都显示为"人工"），并考虑停用时记 warn 日志。

---

### 2.7 给 R2-09 的直接结论

**可以照抄的**（order_block 样板的骨架）：
1. `(team_id, flow_code)` 单行读 + `AND enabled` + `scalar_one_or_none()` + None→manual
2. 档位在**状态跃迁入口**求值，不建常驻状态机
3. 拒绝 = `BusinessError(码, 中文可操作提示, 409)`，同一 flow 的所有入口都挂闸
4. RLS + 显式 team_id 双保险；upsert-only（无 DELETE 权限）
5. 测试写法：`_set_policy` upsert helper（`test_refund_request.py:123-130`）+ finally 复位（`test_procurement.py:284-289`）
6. 若做实时钩子，隔离写法照抄 `scrape/service.py:551-554` 的 `begin_nested()`（自动化异常不反噬主链路）

**必须先裁定、不能照抄的**：
1. `semi` vs `auto` 在每个 flow 的差异（order_block 现状同分支）
2. 每个 flow 是"实时求值"还是"创建快照"（决定"60s 生效"验收判据）
3. flow_code 命名：`pricing_watch`(图纸) vs `listing_pricing`(工单)；`scrape_to_audit` 需先进图纸词表
4. `gtin_alert`/`suspension_reminder` 迁进 policy 还是从词表剔除（建议剔除+写清分界，别动已绿告警链）
5. automation_policy 是否纳入 ConfigService（建议不纳入，DB 直读=0 秒生效）

**第一个增量的建议形状（最小、可验证、不碰渠道）**：
`erp/automation/policy.py`（flow Enum + `resolve_mode` + CI 校验 Enum⊆09 词表）→ `procurement.py:31-58` 与 `refund.py:32-42` 回接同一 helper（行为零变化，纯重构，现有测试即回归网）→ 顺带消费 `order_block.config.check_kinds` 清偿缺陷② → 再逐 flow 接线。

## 路线3：四条待接 flow 的现有链路 —— 每条链在哪能插「停」点

### 0. 总纲结论（先看这段）

**现状 = 四条链全部是 API 端点驱动的人工链，没有任何 beat 任务推进这四条链的业务状态。**
换句话说：`人工档 = 现状`（零改动即满足验收「人工档每环节停」），R2-09 真正要造的是
**semi 的攒批确认位** 与 **auto 的推进器**。

唯一已在跑的自动段是「提交后的对账回写」——`feed_poll`（*/2 min，0023_beat_channel_seeds.py:26）
把 submitted→published/live，`price_recon`（*/15 min，0028_price_recon_seed.py:22）把改价命令归位。
这些是**结果回写**不是**流程推进**，不构成任何一档自动化。

`automation_policy` 表（0025_order_domain.py:295-314）现有唯一消费点：

| 消费点 | file:line | 读法 |
|---|---|---|
| order_block 冻结闸 | `backend/src/erp/order/procurement.py:31-58` | 直读 SQL，`mode not in (semi,auto)` 即放行 |
| refund 档位快照 | `backend/src/erp/aftersale/refund.py:32-42` | 直读 SQL，`_MODE_MAP` 翻译成 refund_request 词表 |

两处都是**每次请求直读、无缓存**——这意味着「切档 60s 生效」在 API 网关型消费点上天然成立，
`ConfigService`（`core/config_service.py:5-7` 的 60s TTL + `:30` pubsub 频道）**并不覆盖
automation_policy**，只覆盖 system_config / team_config。

---

### 1. scrape_to_audit（采集 → 审核）

#### 实际调用路径

```
POST /scrape-jobs                      scrape/router.py:68   → service.create_job (scrape/service.py:44)
   └─ 展开 scrape_task (status=pending)                        scrape/service.py:92-95
worker GET  /worker/tasks/pull          scrape/router.py:259  → pull_tasks (租约)
worker POST /worker/tasks/result        scrape/router.py:278  → submit_result (scrape/service.py:317)
   ├─ INSERT scrape_result                                     scrape/service.py:357-371
   ├─ scrape_task → done                                       scrape/service.py:372-378
   └─ job_kind == 'product_detail' → product_upsert            scrape/service.py:383-390
        └─ INSERT/UPDATE product (status 默认 'ingested')        scrape/service.py:509-548 / 0007:64
        └─ 变体实时归组钩子（SAVEPOINT 隔离）                     scrape/service.py:549-555
────────────────────── 链路在此断掉，无任何送审触发 ──────────────────────
POST /products/{id}/audit               audit/router.py:40    → audit_one (audit/service.py:194)
```

`audit_one` 的唯一生产调用者就是这个端点（另有离线工具 `tools/audit_replay.py:166`）——
grep 全仓确认无第二个调用点。

#### 环节 → 可停点 → 停驻落点

| 环节 | 边界（可插停点） | 停驻状态落点 | 现成/需新建 |
|---|---|---|---|
| 建作业 | `POST /scrape-jobs` | `scrape_job.status=queued` | 现成（本就人工发起，三档不涉及） |
| 任务派发/回传 | worker 租约协议 | `scrape_task.status` | 现成，**不建议插档**（worker 侧节流是采集域自己的事） |
| **入库 → 送审** | `product_upsert` 返回后（scrape/service.py:556） | **`product.status='ingested'`** | **现成队列**，有 `ix_product (team_id, status)`（0007:76） |
| 送审执行 | `audit_one` tx1 落 audit_run | `audit_run.status`（DDL 默认 `'queued'`，0008:96） | 现成但未用：代码直接插 `'running'`（audit/service.py:247） |
| 审核终局 | `_finalize` | `product.status` ∈ audit_passed / audit_rejected / **needs_review** | 现成（needs_review 由 0012 落，fail-closed 改造） |

#### 三档语义落法（不需要新表）

- **manual（现状）**：人工在产品页逐个点「审核」（`ProductsPage.tsx:164`）。
- **semi**：`ingested` 列表 + 前端批量勾选送审。`ProductsPage.tsx:255` 已有 `rowSelection`，
  但**契约只有单品端点**（`specs/002-api-contract/openapi-v0.yaml:429`），需补批量送审端点。
- **auto**：beat 任务扫 `product WHERE status='ingested'` → `audit_one(trigger_kind='auto')`。
  `trigger_kind='auto'` 枚举早已存在（0008_audit_compliance.py:92-94）。

⚠️ **auto 档禁止做成 submit_result 内联钩子**：worker 回传全程被一个 `system_tx` 大事务包住
（`scrape/router.py:282`），而 `audit_one` 是刻意的三段式（tx1 → LLM HTTP 零锁 → tx2，
`audit/service.py:194` 头注 + `audit/router.py:51-53`）。内联 = 把 LLM 出网塞回持行锁事务，
违反 RS-03a。正确形态是 beat 任务，逐团队隔离写法照抄 `catalog/variant.py:684-721`。

#### 现状档位判定
**本来就要人点**（人工档行为）。零自动。

---

### 2. audit_to_listing（审核 → 上架）

#### 实际调用路径

```
audit_one 终局 _finalize                audit/service.py:136-191
   └─ UPDATE product SET status=<audit_passed|audit_rejected|needs_review>,
                         latest_audit_run_id                  audit/service.py:167,172-175
────────────────────── 链路在此断掉，无自动分配 ──────────────────────
POST /listings/allocate                 listing/router.py:163 → service.allocate (listing/service.py:206)
   ├─ 准入闸：product.status ∈ (audit_passed, ready)            listing/service.py:260-269
   ├─ 团队内去重（advisory lock + dedup_exempt）                 listing/service.py:237-290
   ├─ 策略初价 _initial_price                                    listing/service.py:292-295
   ├─ INSERT listing (status=draft) + build 模式占品牌/GTIN       listing/service.py:301-338
   └─ listing_state_history: draft→draft 'allocated' actor='user' listing/service.py:342-349
POST /listings/submit                   listing/router.py:206 → service.submit (listing/service.py:379)
   ├─ tx1 准入 draft/queued + consume_quota('listing_create')    listing/service.py:435-456
   ├─ spec 构建 + 组 feed + 落 outbox 命令 → transition(queued)   listing/service.py:458-565
   ├─ HTTP（零锁）→ tx2 归位 transition(submitted)                listing/service.py:1054
beat feed_poll (*/2 min)                automation/tasks.py:238 → poll_feed (listing/service.py:1094)
   └─ transition(published) → transition(live)                   listing/service.py:1210-1212
```

#### 环节 → 可停点 → 停驻落点

| 环节 | 边界（可插停点） | 停驻状态落点 | 现成/需新建 |
|---|---|---|---|
| **审核 pass → 分配** | `_finalize` 之后（audit/service.py:175） | **`product.status='audit_passed'`** | **现成队列**（§09 的 D-Q13 停点就在这里） |
| 复核分流 | verdict=needs_review | `product.status='needs_review'` | 现成（准入闸自动挡住，不会误进 auto） |
| **分配 → 提交** | allocate 建完 draft（listing/service.py:373） | **`listing.status='draft'`** | **现成队列**——两步分离是天然的半自动停驻位 |
| 提交 → 渠道 | tx1 落 outbox 前 | `listing.status='queued'` + `channel_command` pending | 现成（配额闸/校验闸已在此） |
| 渠道回写 | feed_poll | submitted → published → live | 现成，**已自动**，不插档 |

#### 三档语义落法（不需要新表）

- **manual（现状）**：人工在产品页批量分配（`ProductsPage.tsx:247` 「批量分配上架」），
  再到上架页批量提交。
- **semi**：系统按 `config.batch_size` 攒批 → 通知 → 人工确认。两种落法都不用新表：
  ① 停在 `product.status='audit_passed'` 只发通知；② 更好：**auto allocate 到 draft 但不 submit**，
  人工批量 submit —— draft 就是待放行队列。
- **auto**：beat 扫 audit_passed → allocate → submit 一路到底。

⚠️ **两个必须先解决的问题**：
1. `listing_state_history` 的 `actor_type` 在 allocate 里被**写死 `'user'`**
   （`listing/service.py:344-348`），auto 档走这条会把系统动作记成人工，可审计性破功。
   `transition()` 本身支持 `actor_type='system'`（`listing/service.py:51-59`），
   `AuditWriter` 构造器默认也是 `actor_type='system'`（`core/audit.py:29`）——只需把 allocate
   的硬编码改成传参。
2. **auto 档缺配置**：allocate 必须要 `store_id` + `offer_mode`，而 §09:160 给 audit_to_listing
   的 config 只有 `batch_size`。且 build 模式会**无人值守烧 GTIN 号 + 占品牌 + 扣 listing_create
   配额**（`listing/service.py:322-338`、`:451`）。

#### 现状档位判定
**本来就要人点两次**（分配 + 提交）。渠道回写段已自动。

---

### 3. listing_pricing（上架 → 定价）＝ 001§09 的 `pricing_watch`

> 命名冲突见 gaps：§09 注册清单里叫 `pricing_watch`，工单 check 里叫 `listing_pricing`。

#### 实际调用路径

```
① 初价（上架时一次性，同步）
allocate → _initial_price(strategy, product)                 listing/service.py:292 / :118-149
   └─ 写 listing.current_price + record_price_history('initial') listing/service.py:350-362

② 在架改价（人工触发，唯一入口）
POST /pricing/reprice                   pricing/router.py:406 → _reprice_run (pricing/router.py:241)
   ├─ 逐条 _reprice_gate 预检（6 种 skip 原因）                 pricing/router.py:375-403
   │    not_live / locked / push_in_flight / no_strategy / manual / unchanged
   ├─ D-Q62 路由：单店 ≤ put_route_threshold(默认5) → 逐条 PUT   pricing/router.py:298-305
   │              超阈值 → 聚合 PRICE_AND_PROMOTION feed         pricing/router.py:306-317
   └─ push_price (pricing/service.py:379) 三段式
        ├─ min_price 守护（reason='manual' 时）                  pricing/service.py:416-431
        ├─ 30% 确认阈值 → 无 force 抛 PRICING_CONFIRM_REQUIRED    pricing/service.py:434-445
        └─ outbox price_push → HTTP → tx2 归位
beat price_recon (*/15 min)             automation/tasks.py:470  verify_pending 对账归位

③ 图纸设计的盯价链（未建）
pricing_watch beat → maintenance_task(price_sync) → maintenance_run 认领执行
   specs/001-domain-model/06-listing-pricing.md:161
```

#### 环节 → 可停点 → 停驻落点

| 环节 | 边界（可插停点） | 停驻状态落点 | 现成/需新建 |
|---|---|---|---|
| 上架初价 | `_initial_price` | `listing.current_price` + `price_history(reason='initial')` | 现成，**不插档**（初价随 spec 一起出门，拆不开） |
| **盯价扫描 → 生成建议** | 新 `pricing_watch` beat | **`maintenance_task(task_kind='price_sync', status='scheduled')`** | **表现成**（0009_listing.py:273-276 枚举含 price_sync），**生成侧无 beat**（schedule 无该种子） |
| **建议 → 执行** | `maintenance_run` 的 `config.kinds` 闸 | 同上一行的 scheduled 行 | **执行位现成**（listing/maintenance.py:29-43），**price_sync 分支缺**（:56-59 抛 MAINT_KIND_UNSUPPORTED） |
| 改价出门 | `push_price` tx1 | `listing.pending_price` 非空（在途，不是待人工） | 现成，仅表示在途 |
| **30% 守护** | `push_price` 阈值闸 | 抛 422，**无驻留行**——调用方消失后建议就丢了 | 需新建落点（见下） |
| 渠道回写 | price_recon | `channel_command` verify_pending → 归位 | 现成，已自动 |

#### 三档语义落法（几乎不用新建存储）

§09:164 原文语义 = 「仅报告 / 待确认 / 自动改价」，与现有 `maintenance_task` + runner 一一对应：

| 档 | 落法 | 已有多少 |
|---|---|---|
| manual（仅报告） | pricing_watch beat 只生成 `maintenance_task(price_sync, scheduled)` + 通知，runner `kinds=[]` 不认领 | runner 默认就是 `kinds=[]`（0037_item_pull_schedule.py:31-34，注释原文「[]=人工档（默认，只积累不执行）」） |
| semi（待确认） | 同上 + 人工在维护任务列表逐条/批量放行 | 需前端维护任务列表页（当前无） |
| auto（自动改价） | `kinds` 加 `'price_sync'`，runner 内调 `push_price` | 需补 maintenance.py 的 price_sync 分支 |

⚠️ **`maintenance_task` 没有存「建议价」的列**（只有输出用的 `result` JSONB，0009:284）。
建议**不加列**，改为 runner 内按策略重算——`cost_plus` 对 `product.price_snapshot` 是确定性的，
重算还更新鲜（也顺带解决 30% 守护 422 后建议丢失的问题：下轮扫描会重新生成）。

⚠️ **30% 阈值是与 automation_policy 无关的第二套档位语义**，属「三档语义统一」必须收口的对象。
auto 档撞阈值时**不许静默 `force=True`**（那是绕过守护闸）——应落成「转人工确认」：
任务标 `skipped` + 通知，或生成一条待确认的 price_sync 任务。

#### 现状档位判定
**本来就要人点**（`POST /pricing/reprice` 手动调用）。无任何盯价自动扫描。

---

### 4. refund（退款执行）／cancel

#### 实际调用路径

```
POST /refund-requests                   aftersale/router.py:144 → create_request (refund.py:45)
   ├─ 校验 order / amount>0 / reason_code ∈ sys_dict            refund.py:59-86
   ├─ _resolve_mode: automation_policy(flow_code=kind) 直读       refund.py:32-42, :88
   │    manual→record / semi→approval / auto→auto，无行=manual（fail-closed）
   ├─ mode=='auto' → 抛 REFUND_AUTO_NOT_WIRED（明确不静默降档）    refund.py:89-93
   └─ INSERT refund_request (status = recorded | pending_approval) refund.py:95-127
POST /refund-requests/{id}/approve      aftersale/router.py:226 → decide_request (refund.py:135)
   └─ pending_approval → approved（**驻留态，不触渠道**）          refund.py:165-172
────────────────────── 链路在此断掉：approved 之后无执行段 ──────────────────────
（图纸期望：approved/auto → executing → outbox return_refund → verify-back → executed）
```

#### 环节 → 可停点 → 停驻落点

| 环节 | 边界（可插停点） | 停驻状态落点 | 现成/需新建 |
|---|---|---|---|
| 创建 | `create_request` | `status='recorded'`（record 档终态，只记账） | **现成** |
| 审批 | `decide_request` | `status='pending_approval'` → `approved` / `rejected` | **现成**，`ix_refund_request_status (team_id,status)`（0031:75） |
| **执行** | approved → 渠道 | `status='executing'` → `executed` / `failed` + `executed_at` / `channel_ref` | **状态位全现成**（0031:60-63 七态齐），**执行代码零** |
| 渠道对账 | executing 无响应 | `channel_command` verify_pending | 模式现成（镜像 ship_recon，tasks.py:872-914），**任务需新建** |

#### 三档语义（唯一一条已按三档实现的 flow）

`aftersale/refund.py:1-13` 头注就是标准答案：record（只记账）/ approval（审批后驻留）/
auto（接线前 fail-closed 拒绝创建）。R2-09 只需接**执行段**，**停驻状态位一个都不用新建**。

⚠️ **真实缺口在渠道写通道**：`channel_command.ck_cc_action` 枚举里**没有 `return_refund`**——
现集合 = `feed_submit / item_retire / order_ack / order_ship / price_push / item_maintenance`
（`0038_cc_action_maintenance.py:20-21`）。要扩枚举 = 新 migration（**ar 帽**）。
另需 `refund_recon` beat + schedule 种子。

`amount_ceiling`（§09:163 的 auto 档金额上限）目前无任何代码消费，config 里也无该键。

#### 现状档位判定
**已按三档实现，但 auto 档 fail-closed 拒绝**（`REFUND_AUTO_NOT_WIRED`）。
approved 单据会无限驻留——这是**设计意图**（R2-07 有意留给 R2-09），不是 bug。

---

### 5. 横切结论：R2-09 需要新建的东西（汇总）

| 类别 | 需新建 | 可复用（不用建） |
|---|---|---|
| 表 | **无** | product.status / listing.status+state_history / maintenance_task / refund_request 七态 / notification / audit_run |
| migration（**ar 帽**） | ① `ck_cc_action` 扩 `return_refund`；② 权限点 `automation.read/write`；③ schedule 种子（pricing_watch / audit_auto / scrape_auto）；④ automation_policy flow_code 若要 CHECK 约束 | 0025 的 automation_policy 表本体 |
| beat 任务 | 每条 flow 一个推进器 + `refund_recon` | beat tick 底座（tick 默认 30s，`beat.tick_seconds`，beat.py:138-145）、`run_tracked` 记账、逐团队隔离样板（`catalog/variant.py:684-721`）、按团队 group-by 样板（`tasks.py:616-671`） |
| 服务代码 | policy 解析器（统一三档读法）；`maintenance.py` 补 price_sync 分支；refund 执行段 | `procurement.py:31-58` 的直读样板；三段式 outbox 全套 |
| 端点 | GET/PUT `/automation-policies`；批量送审端点 | allocate / submit / reprice / approve 全在 |
| 前端 | 策略面板页；维护任务列表页 | ProductsPage 的 rowSelection 批量位（:255）；NotificationsPage |
| 语义收口 | 30% 阈值 vs auto 档；`maintenance_run.config.kinds` 这套 ad-hoc 三档要归口 automation_policy | — |

**「切档 60s 生效」的实现口径**：
API 网关型消费点（refund 创建、order_block）照 `procurement.py:33-43` 每次直读 → 天然即时。
beat 推进器每轮读一次 policy → tick 30s + 任务 cron 决定延迟，需保证相关 schedule cron ≤ 1min
或推进器每轮直读。**若引入带缓存的 policy 解析器，必须把 automation_policy 写面接进
`CONFIG_INVALIDATE_CHANNEL`（config_service.py:30）**，否则 60s 验收靠 TTL 兜底、边界会踩线。

## 路线4：切档 60s 生效的基建 —— R2-04 Redis pubsub 能否直接复用

> **判定先行：pubsub 不需要复用，也不该复用。**「切档 60s 生效」这条硬指标在现有代码形态下**默认就已满足**——`automation_policy` 的两个现役读点都是「每决策一次直连 SQL、零缓存」，切档在下一个决策点即时可见。R2-04 那条广播总线失效的是 `ConfigService` 的进程内缓存，而 `ConfigService` **在生产代码里一个读者都没有**，广播目前是空转基建。R2-09 的 60s 风险不在缓存，而在**批处理粒度**（beat 任务硬超时 900s）与**取证手段缺失**（没有策略写端点 ⇒ 没有 T0 时间戳）。

### 4.1 R2-04 增量4 的完整机制（谁发布 / 谁订阅 / 怎么热更新）

单通道、单一发布者、粗粒度整体失效：

| 环节 | 实现 | 位置 |
|---|---|---|
| 通道名 | `erp:config:invalidate` | `backend/src/erp/core/config_service.py:30` |
| 发布者 | **仅** `ConfigService.set_system` / `set_team`：提交后先 `invalidate()` 清本进程，再 `_publish(key)` | `config_service.py:56-84`（`:67-68`、`:83-84`） |
| 发布方式 | 按次建连（配置写是低频管理操作，不养长连接），`socket_connect_timeout=1.0` | `config_service.py:89-97` |
| payload | 配置 key 字符串 | `config_service.py:95` |
| 订阅者 | `run_invalidation_subscriber(service)`：`pubsub.listen()` 收到任意 `type=="message"` → `service.invalidate()`，**payload 被丢弃** | `config_service.py:128-154`（`:144-146`） |
| 热更新方式 | `invalidate()` = `self._cache.clear()`（整表清空，不做按键精细失效，模块注释明说「缓存条目少、重建便宜」） | `config_service.py:86-87`、`:131-132` |
| 订阅接线 | api：lifespan 起协程；beat：`run_forever` 起协程；两者都在退出时 `cancel()` | `backend/src/erp/main.py:36-45`（`:38-39`）、`backend/src/erp/automation/beat.py:154-155`（+`:173-176` 收尾） |
| 失败语义 | **fail-open**：publish 失败只 `log.warning("config_bus.publish_degraded")`；订阅断连指数退避 ≤30s 重连；一致性由 60s TTL 自然兜底 | `config_service.py:7-8`（头注）、`:98-99`、`:149-154` |
| 已验收 | fail-open（Redis 打坏地址写不受阻）+ 跨进程 round-trip（TTL 拉满 3600s，只有广播能让 reader 变）；Redis 不可达则 skip | `backend/tests/db/test_config_bus.py:31-37`、`:39-65` |

落笔与提交：`specs/001-domain-model/09-platform.md:190`、commit `8fc3e37`。Redis 在整个 backend 里**只有这一个用途**（`grep redis backend/src/erp` 除 `config_service.py` 外仅 `core/settings.py:24` 的 URL 定义）。

### 4.2 广播的是哪些配置？automation_policy 在不在范围内？

**在范围内的只有 `app.system_config` / `app.team_config` 的 ConfigService 读缓存**（`config_service.py:101-115` 的 `_lookup` 只查这两张表）。

- `automation_policy`：**不在**。它是 0025 建的独立表（`backend/alembic/versions/0025_order_domain.py:295-314`），列结构是 `(team_id, flow_code, mode, config, enabled)`，不是 k/v 配置键；按图纸它是「D-Q13/14/26/29 的唯一开关面板」（`specs/001-domain-model/09-platform.md:143-155`）——**不应**为了蹭广播被改造成 `team_config` 的键（那才是违反铁律1 的静默偏离）。
- `app.schedule`：**不在**，也不需要——beat 每 tick 重读整表（`beat.py:63-73`），`beat.tick_seconds` / `beat.task_timeout_seconds` 每轮直连 SQL 重读（`beat.py:138-145`、`:76-85`）。**注意 beat 自己都没走 ConfigService。**

#### 最关键的一条：ConfigService 零读者

全仓 `get_config_service()` 只有三处命中：`main.py:39`、`beat.py:155`（两处都只是把单例喂给订阅循环）、`config_service.py:123`（定义本身）。**没有任何业务代码调用 `ConfigService.get()`**。真实的配置读取一律是调用点直连 SQL：

```
channel/gateway/client.py:186-198   channel.gateway_mode（+ :209-216 channel.live_enabled）
channel/outbox.py:82                channel.outbox
automation/beat.py:79-85, 142       beat.task_timeout_seconds / beat.tick_seconds
listing/attr_fill.py:317            listing.attr_fill
listing/spec.py:122, 199            listing.* 逐字段覆盖 / listing.default_wpt
listing/gtin.py:46, 106             gtin.safe_prefixes / gtin.kind_preference
catalog/variant.py:59, 99           variant.* / variant.theme_map
audit/l1_rerank.py:41               category_map.rerank
audit/llm.py:84                     llm.pricing
automation/tasks.py:639, 740        水位阈值 / 商标新鲜度阈值
```

推论有两条，都直接影响 R2-09：

1. **广播现在在失效一份没人读的缓存**。`specs/007-mvp-completion-plan/README.md:79`「档位变更即时生效（吃 R2-04 Redis pubsub 配置广播）」若按字面执行，等于把档位接到一根空管上。
2. **反过来，正因为全仓无配置缓存，「即时生效」是当前架构的默认属性**，60s 指标不依赖任何广播。

> 顺带记一笔既存图纸偏离（**不属 R2-09 修复范围**）：`specs/001-domain-model/09-platform.md:257` 要求「服务层统一 ConfigService（带 60s 进程缓存 + 变更失效广播）」，实现是各点直连 SQL。这是 R1-02/R2-04 遗留的偏离，需 Owner 裁定单独立单，R2-09 不许顺手扩范围（铁律3）。

### 4.3 automation_policy 现状读法：为什么 60s 已经白送

两个现役读点，同一形态——**形参收 session、单条索引查、无缓存**：

```python
# backend/src/erp/order/procurement.py:31-41（order_block 网关）
mode = (await session.execute(text(
    "SELECT mode FROM app.automation_policy WHERE team_id = :t"
    " AND flow_code = 'order_block' AND enabled"), {"t": team_id})).scalar_one_or_none()
if mode not in ("semi", "auto"):
    return                      # 无行/manual = 放行（该 flow 的图纸语义：纯软标记）

# backend/src/erp/aftersale/refund.py:32-42（refund 档位快照）
row = (await session.execute(text(
    "SELECT mode FROM app.automation_policy"
    " WHERE team_id = :t AND flow_code = :f AND enabled"), {"t": team_id, "f": kind})).scalar_one_or_none()
return _MODE_MAP.get(row or "manual", "record")   # 无行 = manual（fail-closed 最保守档）
```

加上引擎未设 isolation（`backend/src/erp/core/db.py:20`，即 PG 默认 READ COMMITTED），每条语句看到的都是最新已提交数据 ⇒ **切档后下一个决策点即生效，延迟≈0**。

同时注意两点语义差异，`三档语义统一` 时必须逐 flow 对图纸（`09-platform.md:158-166`），不能一刀切：`order_block` 的 manual = 放行（软标记），`refund` 的 manual = record 档且缺行也走最保守档。方向相反。

### 4.4 进程覆盖面（切档要全进程生效，漏一个就有陈旧读）

| 进程 | 起法 | 是否订阅 config bus | 是否需要感知档位 |
|---|---|---|---|
| api | `uvicorn erp.main:app`，**无 `--workers`，单副本** | 是（`main.py:38-39`） | 是（audit_to_listing/refund/order_block/listing_pricing 的请求侧网关） |
| beat | `python -m erp.beat`（`backend/src/erp/beat.py`） | 是（`beat.py:154-155`） | 是（scrape_to_audit 等由周期任务推进的环节） |
| 通用 worker | compose 里**被注释的占位**（「代码中尚无任何队列生产者」） | — | 未上线 |
| scraper 采集 worker | 独立镜像 `../workers`，`profiles: ["scraper"]`，出站拨入 api | **不订阅、不连 DB**；运行期参数由 `/worker/v1/sync` 下发 `scrape.worker_settings` | 不应感知——采集结果的送审闸必须放在服务端 |
| `erp.tools.run_task` 等 CLI | 一次性短命进程，config 与 beat 同源读 `app.schedule` | 不需要 | 无陈旧问题 |

出处：`infra/docker-compose.yml:45-56 / 59-67 / 69-74 / 76-96`；`backend/src/erp/scrape/router.py:245-250`；`workers/src/erp_worker/config.py:4-6`；`backend/src/erp/tools/run_task.py:4,30`。

结论：**在「不缓存」路线下，进程覆盖面天然完整**（每个进程每次决策都读库）。若将来引缓存，两个长驻进程的订阅协程已就位、api 水平扩容时 lifespan 自动起订阅——但 scraper 永远吃不到 pubsub，任何档位判定都不能下发到采集 worker。

### 4.5 60s 生效的真实风险：批粒度，不是缓存

beat 的执行模型（`backend/src/erp/automation/beat.py:10-13` 头注 + `:36-37` + `:128-134`）：

- tick 默认 **30s**（`_DEFAULT_TICK_SECONDS = 30.0`）；
- 任务在 tick 内**串行**执行；
- 任务级硬超时默认 **900s**（`_DEFAULT_TASK_TIMEOUT_SECONDS`，可被 `schedule.config.task_timeout_seconds` / `system_config beat.task_timeout_seconds` 覆盖）。

⇒ 若某个 flow 网关写成「任务开始读一次 mode，然后处理整批」，最坏陈旧 = 该任务剩余运行时长（可达 900s）+ 一个 tick（30s），**直接击穿 60s 验收**。这与缓存无关，是粒度错误。

**实现纪律（必须写进工单）**：
1. 策略读取粒度 = **每条决策**（每商品 / 每单 / 每次改价判定），禁止任务级/批级读一次；
2. 验收口径声明清楚：60s 针对**新进入的决策**；已在飞行的批次按批次原子性走完（与 D-Q64 批次原子性一致，不算违规）；
3. 若某任务确需长跑，则在每 chunk 边界重读一次 mode，并把「本 chunk 生效档位」写进 `task_run.stats`。

### 4.6 复用可行性判定（结论 + 理由）

**判定：不复用。pubsub 侧零改动，`automation_policy` 保持「每决策读库」。**

三条理由，按强度排序：

1. **无缓存 ⇒ 无需失效**。现状读法本身就满足 60s（4.3），引入缓存 + 广播只是把「0 延迟」换成「≤60s 延迟 + Redis 依赖」，是纯负收益。
2. **语义冲突（合规底线）**。config bus 明确 fail-open（`config_service.py:7-8, 98-99`；`test_config_bus.py:31-37` 把 fail-open 当验收项），而档位是合规闸、现有代码坚持 fail-closed（`refund.py:8-9` auto 未接线直接拒绝、`:12` 无策略行=manual）。把档位生效寄托在 fail-open 通道上，Redis 一挂就会在 TTL 窗口内**继续按旧档执行**——`auto → manual` 方向就是「该停没停」，违反 fail-closed 底线。
3. **通道形态不匹配**。现在是「整体失效 + payload 丢弃」（`config_service.py:144-146`）。要让它按类型分发，得把订阅端改成注册表/分发器（core 改造，`ConfigService.invalidate` 之外再挂 invalidator），收益为零、风险落在 R1-02/R2-04 已验收的公共基建上。

**要改的清单（R2-09 与 60s 相关的实作项，pubsub 不在其中）**

| # | 改动 | 位置/依据 | 目的 |
|---|---|---|---|
| 1 | 新建 `backend/src/erp/automation/policy.py`：统一 `resolve(session, *, team_id, flow_code) -> (mode, config)`，**无缓存**，签名沿用现有形参式 session；每 flow 的「缺行语义」按图纸逐条定义（不做一刀切默认） | 复用 `procurement.py:31-41` / `refund.py:32-42` 形态；语义源 `09-platform.md:158-166` | 语义统一 + 保持 0 延迟 |
| 2 | 把两个现役读点收敛到该模块（行为不变，只去重） | `procurement.py:33-41`、`refund.py:33-42` | 单一真相源 |
| 3 | 4 个新 flow 网关按「每决策读一次」接线 | 4.5 纪律 | 保 60s |
| 4 | beat 侧读点 SQL **必须显式 `WHERE team_id = :t`** | `system_tx` 以 `is_super` 绕 RLS（`core/db.py:53-64`），而 `automation_policy` 挂了 TEAM_RLS（`0025:314`） | 防跨团队越权读档（D-Q30） |
| 5 | 各 flow 决策落 `mode_applied` 快照（refund 已有；其余需新增列或落 `task_run.stats` / audit_log） | 先例 `0031_refund_request.py:57-59` + `refund.py:101-132`；`task_runner.py:63-70` | 60s 取证的 T1 锚点 |
| 6 | 新增策略读写端点（契约 + 权限点）并经 `AuditWriter` 记账；前端策略面板按 008 规范（统一 client + codegen 类型） | 契约缺口：`openapi-v0.yaml` 仅在 `:2159` 提及 automation_policy；模板 `pricing/router.py:224-227`、`core/audit.py:1-8` | 60s 取证的 T0 锚点 + 工单要求的面板 |
| 7 | （可选，仅当实测出热点才做）给 policy 读加缓存并复用 `erp:config:invalidate` | 需先解决 4.6-②的 fail-open/fail-closed 冲突 | 性能，非当前需要 |

### 4.7 60s 生效的取证方案

**现成观测点盘点（能证明什么 / 不能证明什么）**

| 观测点 | 位置 | 能否作为 60s 证据 |
|---|---|---|
| `config_bus.subscribed` / `publish_degraded` / `subscribe_retry` 日志 | `config_service.py:142, 99, 152` | ❌ 只证明总线活着；与档位生效无关（且档位不走这条总线） |
| `/healthz` | `main.py:101-104`（「进程在即 200，不查依赖」） | ❌ 存活探针，不含依赖/配置状态 |
| metrics 端点 | **不存在**（`main.py` 只挂 `/healthz` + 各业务 router） | ❌ |
| `refund_request.mode_applied` | `0031_refund_request.py:57-59`、`refund.py:101-132` | ✅ 决策时档位快照落库，先例可推广 |
| `task_run.stats` (jsonb) | `task_runner.py:49, 63-70` | ✅ beat 每轮 stats 落库，可塞「本轮生效档位」 |
| `audit_log`（`AuditWriter` 唯一写出口） | `core/audit.py:1-8`；模板 `pricing/router.py:224-227` | ✅ 提供切档时刻 T0（前提：先建策略写端点） |

**取证方案（纯 SQL 可核，可自动化成验收测试）**

1. T0 = 策略写端点经 `AuditWriter` 落的 `audit_log` 记录时刻（action 例：`automation.policy_update`，`after` 带 `flow_code` + 新 `mode`）；
2. T1 = 切档后**第一条**带新档位快照的决策记录：api 侧取业务表 `mode_applied`（refund 已有；其余 flow 新增同名列），beat 侧取 `task_run.stats.mode` 首次出现新值的 `started_at`；
3. 断言 `T1 - T0 ≤ 60s`，并同时断言**语义正确**（全自动零停 / 半自动定点停 / 人工每环节停），因为 60s 只是时限、不是正确性；
4. 三档各跑一遍时，附一条「档位快照与 audit_log 一一对拍」的记录表（同一商品三行，flow × mode × T1-T0）作为工单证据；
5. 补充健壮性用例：**Redis 全停**下重跑同一验收——由于档位不吃 pubsub，结果必须完全不变。这条同时反证「不复用 pubsub」的决策正确，也是 fail-closed 底线的实测。

未定义的口径（需 Owner 拍定，见 gaps）：T0 到底取「API 返回时刻 / audit_log.created_at / DB commit 时刻」——建议 `audit_log.created_at`（唯一写出口、与业务写同事务，无幽灵审计）。

### 4.8 退路：不缓存的代价（量化）

| 维度 | 事实 | 出处 |
|---|---|---|
| 表规模 | 行数 = 团队数 × flow 数；图纸 flow 清单 7 项 | `09-platform.md:158-166` |
| 索引 | `uq_automation_policy (team_id, flow_code)` 唯一索引，等值查必命中 | `0025_order_domain.py:310` |
| 连接开销 | **零**：读点复用调用方 session/事务，不新建连接、不额外往返；且与被闸住的业务写同事务 ⇒ 档位读与写事务一致 | `procurement.py:31`、`refund.py:32`；`core/db.py:24-27` |
| 频率上限（估算） | PRD 目标日上架 20 万 / 日采 20 万+ ASIN；若每件过 4 个 flow 网关 ≈ 80 万次/日 ≈ **10 次/s 均值**（此乘算为本人估算，非文档数字） | `specs/000-founding/PRD-v1.md:19`、`:101` |
| 结论 | 几十行的小表全驻 shared buffers，单次索引查 ~几十 µs 级；10 次/s（即便峰值 ×10）对 PG 完全无压 | — |

对照两个既有「无缓存也够用」的先例，进一步佐证：

- `app.schedule`：beat 每 tick 全表重读（`beat.py:63-73`），改表后 ≤30s 生效——`maintenance_run` 的人工/半自动档就是靠 `schedule.config.kinds` 实现的（`backend/src/erp/listing/maintenance.py:3-4, 29`）；
- `pricing_strategy`：每次算价从表读 params，无缓存（`backend/src/erp/pricing/service.py:83`、`pricing/router.py:171`）。

唯二有进程内缓存的模块也不靠 pubsub，而是**版本号令牌 + 每次读库校验**：`audit/policy_block.py:90-95`（版本不同即重建）、`audit/blacklist_index.py:138-176`（「每次都以刚读到的 DB 版本比对，版本变即重建，**绝不返回过期结果**」）。**这才是本仓「既要缓存又要即时生效」的现成通用模式**——如果 R2-09 日后真需要给档位加缓存，应抄这个模式（fail-closed、无外部依赖），而不是抄 fail-open 的 pubsub。

## 路线5：前端策略面板 —— 契约缺口与 008 规范约束

> 只读侦察，未改任何文件。结论均带 `file:line` 或文档章节。

### 5.1 现状：前端零自动化策略面（不是"页面简陋"，是完全不存在）

- 路由全集 `frontend/src/App.tsx:29-43`：15 条（scrape-jobs / products / listings / pricing /
  orders / purchasers / stores / incidents / compliance / proxies / users / notifications /
  roles / audit + index），**无 automation/policy**。
- 菜单全集 `frontend/src/layout/AppLayout.tsx:28-44`：15 项，**无自动化策略**。
- 全仓 `grep -i "automation|policy|自动化|策略|档" frontend/src` 的命中全部无关：
  `PricingPage.tsx` 系列是**定价策略**（pricing_strategy，另一个域）；
  `ListingsPage.tsx:86` 是 `strategy: '策略重定价'` 文案；
  `frontend/src/api/schema.d.ts:5029` 是 `RefundRequest.mode_applied` 的 description
  文本"创建时从 automation_policy(flow=kind) 快照"——**契约里 automation_policy 只以注释形式存在**。

结论：R2-09 的策略面板是**纯新增页**，无存量页可改造，必须整套走 008§5 上车清单。

### 5.2 后端/契约缺口：automation_policy 一个端点都没有

| 层 | 现状 | 证据 |
|---|---|---|
| 表 | 已建，7 列 + `uq (team_id, flow_code)` + TOUCH + TEAM_RLS + GRANT | `backend/alembic/versions/0025_order_domain.py:295-314`，`:359-361` |
| 服务 | 无。两处内联 SQL 直查 | `backend/src/erp/aftersale/refund.py:32-42`（flow_code = kind）、`backend/src/erp/order/procurement.py:30-41`（flow_code='order_block'） |
| router | **无**。`erp/automation/` 只有 `beat.py` / `task_runner.py` / `tasks.py` | `backend/src/erp/automation/`；`backend/src/erp/main.py:106-118` 注册 13 个 router，无 automation |
| 契约 | **零 path、零 schema**，仅一句 description | `specs/002-api-contract/openapi-v0.yaml:2159` |
| 权限点 | **无 automation/platform 模块任何权限点** | `backend/alembic/versions/0002_identity.py:262-299` |
| 种子 | **无**。建表不插行，`create_team` 只复制角色模板 | `0025:295-314`；`backend/src/erp/identity/router.py:120-141` |

表结构（照抄自 0025:298-310，面板字段的唯一真相）：

```sql
CREATE TABLE app.automation_policy (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  team_id bigint NOT NULL REFERENCES app.team(id),
  flow_code text NOT NULL,
  mode text NOT NULL DEFAULT 'manual'
    CONSTRAINT ck_automation_mode CHECK (mode IN ('manual','semi','auto')),
  config jsonb NOT NULL DEFAULT '{}',
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by bigint
);
CREATE UNIQUE INDEX uq_automation_policy ON app.automation_policy (team_id, flow_code);
```

RLS 已挂（`0025:314` TEAM_RLS）→ 端点不需要显式 team 过滤（同 `backend/src/erp/catalog/router.py:269`
的注释口径："RLS 经 current_team 隔离（require_permission 已 SET LOCAL）"）；
超管代表团队操作走 `X-Act-Team` 头，前端 client 已自动带（`frontend/src/api/client.ts:71-73`）。

### 5.3 端点缺口清单（含契约先例，不造新范式）

固定小枚举资源在本契约里已有两种成熟形态，**都不分页**：

- 「GET 裸数组 + PUT 全量数组」：`openapi-v0.yaml:302-317`
  `GET/PUT /stores/{storeId}/quota-configs`，读写权限分离（`channel.store_read` / `channel.quota_write`），
  schema `QuotaConfig`（`:1701-1706`，三字段 kind/limit/enabled——与 policy 行几乎同构）。
- 「按业务码 PATCH 单行」：`openapi-v0.yaml:1090-1111`
  `GET /listing-errors`（裸数组）+ `PATCH /listing-errors/{errorCode}`。
- 其他裸数组先例：`/permissions`（`:189-198`）、`/worker-nodes`（`:421-428`）、`/dicts/{dictType}`（`:214-226`）。

**建议 R2-09 新增（写进 002 契约）：**

| 端点 | 方法 | x-permission | 返回/入参 | 说明 |
|---|---|---|---|---|
| `/automation-policies` | GET | `automation.policy_read` | `array<AutomationPolicy>` | **返回 flow 注册表 ∪ 库中行**（缺行=虚拟行 mode=manual），面板零硬编码 flow 清单 |
| `/automation-policies/{flowCode}` | PUT | `automation.policy_write` | body `{mode, config, enabled}` → 200 | 单 flow upsert（`ON CONFLICT (team_id, flow_code)`）；带 `Idempotency-Key`（写端点惯例，`openapi-v0.yaml:1288` / `backend/src/erp/aftersale/router.py:27`） |

选"单 flow 写"而非 quota 式全量 PUT 的理由：① `audit_log` 的 entity 能落到 flow_code
（`AuditWriter.for_user(...).log(...)` 用法见 `backend/src/erp/aftersale/router.py:179,238,255`）；
② 一次误提交不会把所有 flow 的档位一起翻掉——这是"三档=风险姿态开关"应有的粒度。

schema 落点：`components.schemas`（`openapi-v0.yaml:1547` 起），字段照表结构 + `mode` enum
`[manual, semi, auto]` + `flow_code` enum（与后端 Enum、001§09 清单三处对齐，图纸原话"代码 Enum 对照 + CI 校验"）。
tag：现契约头部 `tags:`（`:15-22`）只声明了 7 个，但实际在用 11 个（Aftersale/Compliance/Scrape/Audit
未声明就用了）——建议本单顺手声明 `{name: Platform}` 或 `{name: Automation}` 并把新端点挂上，不要再添未声明 tag。

### 5.4 权限点提案（对齐既有命名法）

既有命名两派：
- 读写分离派：`channel.store_read` / `channel.store_write`、`pricing.read` / `pricing.write`、
  `compliance.blacklist_read` / `compliance.blacklist_write`（`0002_identity.py:268-296`、`0035:151-153`）。
- 单 admin 派：`audit.policy_admin`（`0008_audit_compliance.py:303`）、`scrape.node_admin`（`0007:241`）、
  `listing.error_admin`、`channel.quota_write`。

**建议**：`automation.policy_read`（module=`automation`，名"自动化策略查看"）+
`automation.policy_write`（"自动化策略调档"）。理由：菜单可见性需要一个 read 点（`AppLayout.tsx:58`
按 `has(permission)` 过滤菜单），页内写控件需要独立 write 点（`UsersPage.tsx:109-118` 模式）。
备选 `automation.policy_admin` 单点，但那样只能"能看即能改"，与档位=团队决策的风险等级不符。

种权限点的迁移写法照抄 `backend/alembic/versions/0031_refund_request.py:83-99`（`INSERT ... ON CONFLICT DO NOTHING`
\+ 按 role.name JOIN 挂 role_permission + downgrade 反向删）。角色映射建议：
`团队管理员` 拿 read+write；其余角色按需给 read（档位影响全链行为，D-Q13/D-Q29 定性为团队决策）。
**注意**：migration 只有 ar 帽能动，前端侧不能自己加。

### 5.5 008 六项上车清单逐条对照（原文 `specs/008-frontend-conventions/README.md:50-60`）

| # | 008 原文 | R2-09 面板要做什么 | 落点 |
|---|---|---|---|
| 1 | 契约先行（openapi 更新 + gen:api 产物同 PR） | 先加 2 个 path + `AutomationPolicy` schema，再 `pnpm gen:api`，`schema.d.ts` 与 yaml **同 PR** | `specs/002-api-contract/openapi-v0.yaml`（paths + `:1547` 起 schemas）、`frontend/src/api/schema.d.ts` |
| 2 | 路由 + 菜单 + 权限点三处注册 | ① `App.tsx` 加 `<Route path="/automation" element={<AutomationPage />} />`（import 按字母序，`App.tsx:5-20`）② `AppLayout.tsx` MENU 加一项 `permission: 'automation.policy_read'`（`:28-44`）③ migration 种权限点 + 页内 `has('automation.policy_write')` 门控写控件 | `App.tsx:29-43`、`AppLayout.tsx:28-44`、新 migration、页内 |
| 3 | 三态 + 服务端分页齐全 | **三态必须齐**（loading / error 透出 `ApiError.message` / 空态）；**分页正当豁免**：资源是固定小枚举（每团队 ≤7 行），先例 `/permissions`、`/worker-nodes`、`/listing-errors`、`/quota-configs` 全是裸数组。**必须在 PR 里写明豁免理由**，别默默不分页 | 见 5.6 范例 |
| 4 | 类型全部来自 codegen（零手写响应类型） | `export type AutomationPolicy = Schemas['AutomationPolicy']` 加到 `client.ts:12-19` 那个块；或页内 `type AutomationPolicy = Schemas['AutomationPolicy']`（`OrdersPage.tsx:24-25` 写法）。**一行 `interface` 都不许** | `frontend/src/api/client.ts:12-19` |
| 5 | 行为有运维面的，域 runbook 同 PR | 切档改变全链行为 → 必须有 runbook（切档步骤、生效验证怎么看、回滚、每 flow 三档语义表）。先例 `.agent/evidence/R2-05/runbook.md` | `.agent/evidence/R2-09/runbook.md` |
| 6 | CI lint + tsc + build 绿 | `cd /home/user/ERP-ALL/frontend && pnpm lint && pnpm build`；CI frontend 作业只跑这两步 | `.github/workflows/ci.yml:73-88` |

补充两条 008 硬约束（不在六项里但同样卡）：
- **§3.4 业务规则零前端**（`README.md:40-41`）：档位合法性、`auto` 档的前置（如 `amount_ceiling` 必填）
  一律后端校验 fail-closed，前端只做输入体验；**操作留痕靠后端 `audit_log`，前端不自造日志**。
- **§6 运维可见性**（`README.md:62-77`）：面板要显示 `updated_by` / `updated_at`（"谁什么时候改了档"），
  并给审计日志入口——运维不靠查库。§6 最后一条（PR #35 教训）与本面板高度相关，见 5.7。

### 5.6 数据层纪律怎么做 + 可照抄的正确范例

**唯一数据层**：`frontend/src/api/client.ts`
- 类型源：`:3` `import type { components } from './schema'` → `:5` `export type Schemas = components['schemas']` → `:6-19` 逐个具名导出。
- 错误信封：`:26-35` `ApiError(status, code, message, detail)`；`:102-118` 401 单次 refresh 重试 + 统一抛 `ApiError`。
- 动词：`:120-134` `api.get/post/put/patch`；`post` 自动带 `Idempotency-Key`（`:124-129`）。
  ⚠️ `api.put`（`:130-131`）**不带** Idempotency-Key——若契约把写端点定为 PUT 且要求该头，
  需要么改用 `api.post`，么在 client 里补（改 client 属跨页公共层，PR 里要说明）。

**照抄范例 = 合规中心黑名单页**（R2-12 增量5，全仓最新且唯一全程按 008 写的域）：

`frontend/src/pages/compliance/BlacklistTab.tsx`
- `:4-10` 类型全部从 client 取（`ApiError, api, type BlacklistAssertion, type BlacklistEntry, type PageOf`）——**零手写 interface**；
- `:41-43` `const { has } = useAuth(); const canWrite = has('compliance.blacklist_write')`；
- `:54-73` `load()` 三态标准骨架：`setLoading(true)` → `try` 取数 → `catch` `message.error(e instanceof ApiError ? e.message : '加载失败')` → `finally setLoading(false)`；
- `:93-112` 写操作骨架（`api.post` → `message.success` → `void load()` → catch 透出 `ApiError.message`）；
- `:160-164` 写按钮按 `canWrite` 门控；
- `:167-178` `PageOf<T>` 服务端分页接线；
- `:148-159` **008§6 最后一条的实证补丁**（不依赖列表命中的直查入口）。

页壳（多 Tab + 每 Tab 权限门控 + 全无权限时 `<Empty>`）：`frontend/src/pages/CompliancePage.tsx:12-36`。

**per-row 写控件门控**范例：`frontend/src/pages/UsersPage.tsx:105-118`——有权限渲染 `Switch`，
无权限退化为只读 `Tag`。这正是策略面板每行"三档 Segmented / 只读 Tag"该有的形状。

**结构最像但不要照抄数据层的**：`frontend/src/pages/StoresPage.tsx:303-380` QuotaTab
——"固定小枚举逐行编辑 + 全量保存"的形态先例，其中 `:330-337` 是"注册表 ∪ 库中行、缺行补默认"的
合并写法（策略面板同样需要，但**应放后端做**，否则 flow 清单硬编码进前端违 D-Q11 枚举不硬编码）。
反面：`:304-309` 内联匿名响应类型（属 FE-DEBT-01 同型）、`:311-314` `load()` 无 try/catch 无 loading
（违 008§3.2）——**只抄形状，不抄这两处**。

FE-DEBT-01 现状（新页绝不能添账）：全仓 29 处手写响应类型，见
`StoresPage.tsx:7-28`、`PricingPage.tsx:27-77`、`ListingsPage.tsx:20-54`、`ProductsPage.tsx:24-60`、
`OrdersPage.tsx:28-51`、`ScrapeJobsPage.tsx:9`、`ProxiesPage.tsx:7`、`IncidentsPage.tsx:11`、
`components/TeamSwitcher.tsx:6`、`compliance/ImportJobsTab.tsx:15`。

### 5.7 面板形态：无图纸，建议形态 + 三个必答设计点

**图纸检索结果（无）**：全仓 `grep "策略面板|开关面板|自动化面板"` 只三处文字：
`specs/001-domain-model/09-platform.md:143`（"D-Q13/14/26/29 的唯一开关面板"）、
`specs/007-mvp-completion-plan/README.md:78`（"三档语义统一（人工/半自动/全自动，团队级面板）+ 前端策略面板页"）、
`.agent/review_list.json:550`。**没有线框、没有 007 指定 UI、FE-DESIGN 未产出**（008 卷首说明：
正式前端在 FE-DESIGN，Owner 触发制，当前 13 页是开发期功能件）。形态由 R2-09 自定 + 回请 Owner 确认。

**建议形态：单页 `/automation`「自动化策略」= 一张 flow 表（无分页）**

| 列 | 内容 | 组件先例 |
|---|---|---|
| 流程 | flow 中文名 + `flow_code`（文案随 GET 下发，不前端硬编码） | — |
| 档位 | 三档 `Segmented`（人工/半自动/全自动）；无写权限退化只读 `Tag` | `BlacklistTab.tsx:126-136`、`ListingsPage.tsx:201-208`、`UsersPage.tsx:105-118` |
| 启用 | `Switch`（须警示：拦截型 flow 上"停用"=放宽，见下） | `StoresPage.tsx:355-360`、`UsersPage.tsx:110` |
| 本档含义 | **per-flow 语义说明**（必需，见下） | — |
| 参数 | `config` 抽屉/行内编辑（键位随 flow 不同） | `StoresPage.tsx:341-370` |
| 最近变更 | `updated_by` / `updated_at` + 审计日志入口 | 008§6 |

**不建议**做"flow × 档位"打勾矩阵：`config` 每 flow 键位不同（batch_size / check_kinds /
amount_ceiling / frequency / warn_pct,critical_pct / remind_days，`09-platform.md:158-166`），
矩阵放不下参数编辑，最终还得开抽屉——不如一开始就用表。

**必答设计点 ①：三档语义在既有代码里方向相反，面板必须逐 flow 解释**
- `refund`：`manual` = 最保守（只记账不执行）→ `backend/src/erp/aftersale/refund.py:26-29,32-42`；
- `order_block`：`manual`（默认）= **最宽松**（flagged 单纯软标记放行），`semi/auto` 才冻结
  → `backend/src/erp/order/procurement.py:30-41` 原注释"semi/auto 档 + 未放行 flagged → 冻结（409）。manual（默认）纯软标记放行"；
- 图纸自身也这么写：`compliance_block` = "off(=manual 纯软标记) / block 拦截"（`09-platform.md:161`）。
- 且两处查询都带 `AND enabled` → `enabled=false` 退化成"无行"→ 在 `order_block` 上等于**放行**。
  **面板上"停用"不等于"安全"，必须显式警示。**

**必答设计点 ②：0 行陷阱（与 008§6 最后一条 PR #35 教训同型）**
表无种子、`create_team` 不建 policy 行（`identity/router.py:120-141` 只复制角色）。
若 GET 直读表，新团队面板 0 行 → 运营**无行可点、设不了档**，只能退回调 API/改库——
008§6 明令禁止的形态。修法：GET 返回 flow 注册表与库中行的合并结果（缺行=虚拟行 `mode=manual`），
写端点 upsert。合并放后端做（前端合并=硬编码 flow 清单，违 D-Q11）。

**必答设计点 ③：面板行集依赖 flow 词表冻结**
`001§09:156-166` 列 7 行且 `refund / cancel` 合写一行，但代码按 `flow_code = kind` 读
（`refund.py:37-39`）→ 实际需 `refund`、`cancel` **两行**；007 点名的 `listing_pricing`、
`scrape_to_audit` 都不在 §09（§09 是 `pricing_watch`，且无 `scrape_to_audit`）。
前端只渲染 GET 返回的行即可对齐，但**契约 enum / 后端 Enum / 001§09 三处必须先由 R2-09 统一**。

### 5.8 schema.d.ts codegen 流程

```bash
# 1) 先改契约（唯一入口，禁止先改前端）
#    specs/002-api-contract/openapi-v0.yaml：加 paths + components.schemas.AutomationPolicy
# 2) 生成类型（必须在 frontend/ 目录内跑；命令见 frontend/package.json:12）
cd /home/user/ERP-ALL/frontend && pnpm gen:api
#    等价于 openapi-typescript ../specs/002-api-contract/openapi-v0.yaml -o src/api/schema.d.ts
# 3) 在 client.ts 具名导出新类型（:12-19 块），页面 import
# 4) 本地验（= CI frontend 作业全部内容，ci.yml:73-88）
pnpm lint && pnpm build      # build = tsc -b && vite build（package.json:9）
# 5) yaml + schema.d.ts + 页面 同一个 PR 提交（008§5 第1条）
```

何时跑：**每次改 openapi-v0.yaml 之后、写页面之前**。tsc 会在编译期暴露契约不兼容（008§2.3）。

⚠️ CI 缺一道闸：`.github/workflows/ci.yml:73-88` 只跑 `pnpm lint` + `pnpm build`，
**没有** `pnpm gen:api && git diff --exit-code src/api/schema.d.ts` 的漂移校验——
即"schema.d.ts 与 yaml 不同步"目前 CI 抓不到，靠 008§2.3 人守。R2-09 可顺带补这道闸（属 fe/ci 帽，宜另单）。

### 5.9 "切档 60s 生效"在前端侧的含义

- 生效机制在后端：`backend/src/erp/core/config_service.py:1-9,:30,:37,:103,:131-146`
  （进程内缓存 TTL 60s + Redis pubsub `erp:config:invalidate`，fail-open）。
  但该服务只覆盖 `system_config` / `team_config`；**automation_policy 目前无服务层、无缓存**
  （两个读点每次直查库，`refund.py:36-41`、`procurement.py:33-40`）→ 现状"切档即时生效"天然成立。
- 因此前端**不需要**为 60s 做任何事；面板只需展示 `updated_at`/`updated_by` 供验收取证。
- 反向风险提醒（给后端路线）：若 R2-09 为 policy 加缓存以降 QPS，**必须复用同一 pubsub 失效通道**，
  否则 60s 验收反而是被本单新加的缓存破坏的。

## 路线6：旧仓/旧系统的三档语义考据

**结论先行：有旧语义可考，但没有旧「档位」可移植。**

具体分三层：

| 层次 | 旧仓有无 | 可复用度 |
|---|---|---|
| 档位表 / 档位枚举 / 档位配置键 | **完全没有**（grep 全仓 0 命中；旧生产库 system_config 恰好 12 行=基础设施种子） | 0 —— 建表确属新建（R2-05 0025 已建对） |
| 三档的**行为语义**（哪里该停、停给谁、怎么放行） | **有**，且成文 | 高 —— 逐 flow 有对照点，见下 §6.3 |
| 三档的**运行经验**（semi/auto 跑通过） | **只有 auto 档在少数链路跑过**（价格同步、问题商品清理） | 中 —— auto 档有实战参数可抄，semi 档几乎零经验 |

> 教训对齐：R2-07 曾出现「无旧语义可考」误判（`.agent/review_list.json` R2-07 finding 开发侧批注：erpAPI 根目录另有独立生产脚本 `售后订单同步/fetch_walmart_returns.py`）。本路线因此按「包内代码 + 根目录独立脚本 + 上一代后端 erp-core + 前端设计稿 + 旧生产库 schema/rowcount」五处交叉搜，结论如下。

---

### 6.1 侦察范围与手法（可复核）

搜索面：
- `/home/user/erpAPI/` 根目录 9 个中文业务目录 + 5 个根级 .py 生产脚本；
- `/home/user/erpAPI/auto_listing/`（旧上架管线，46 文件）、`match_listing/`（跟卖）；
- `/home/user/erpAPI/erp-core/`（**上一代 FastAPI 后端，本项目直系前身**，222 文件，含 alembic 全量迁移）；
- `/home/user/erpAPI/erp-core/handoff-design/`（旧前端设计稿 + 原型 jsx）；
- `/home/user/ERP-ALL/specs/000-founding/data-survey/out/`（旧生产库 schema dump + **精确行数**）。

关键 grep（均为 0 有效命中，构成负面证据）：
```
三档|档位|automation_(policy|level|mode)|full_auto|semi_auto|manual_mode|人工档
  → 仅 3 处无关命中：walmart_prohibited_detailed.md:916（半自动折刀）、
    沃尔玛订单审核/README.md:106（钓鱼三档命中）、
    erp-core/.../audit/pipelines/orchestrator.py:291（severity 三档）
auto_audit|autoAudit|采集后自动审核
  → 仅 1 处：handoff-design/COLLECTION_REFACTOR_PROMPT.md:171（设计稿，未实现）
```
环境变量全量枚举（`os.environ` 扫描）里唯一的流程开关是 `ENABLE_VARIANT_LISTING`（二元 feature flag，`auto_listing/main.py:342,507,1058,1132,1405`）——不是档位。

---

### 6.2 硬证据：旧系统的「配置中心」从未管过业务流程

`erp-core` 的 system_config 与新系统同名同形（key/value JSONB/description/updated_by/updated_at）：

- 建表 + 种子：`/home/user/erpAPI/erp-core/backend/alembic/versions/0006_system_config.py:19-44`
  —— 12 条：`auto_retry.{enabled,interval_minutes,max_cycles,collect_window_hours}`、
  `worker_restart.{enabled,interval_hours}`、`async_pool.{workers,initial_c,max_c,qps_rate,stagger_sec,fetch_max_retries}`
- 读取：`/home/user/erpAPI/erp-core/backend/app/services/system_config.py:40-51`，**每次调用直接 SELECT，无缓存无广播**
- 全仓消费点只有 3 行：`app/tasks/pipeline_tasks.py:74,76,77`（自动重试失败采集）
- **旧生产库实测行数 = 12**（`specs/000-founding/data-survey/out/pg_erp_core_rowcounts_exact.txt:41`）

→ 与种子完全一致，说明**上线后没有人往里加过任何一条业务配置**。旧系统的配置中心只调基础设施参数（重试/worker/并发池），R2-09 把它用于业务流程编排是第一次，无旧运行经验可继承。

同理，最接近 automation_policy 形状的 `store_rules`（`app/models/store.py:71-92`：rule_type / config JSONB / is_active / priority）**生产 0 行、代码零消费**（rowcounts:38）。这既是「表形状先例」，也是**反面教训**：建了不接线就是死表——正是 R2-09 要避免 automation_policy 重蹈的命运。

---

### 6.3 逐 flow 对照点（旧语义 → 图纸三档）

#### (a) audit_to_listing（D-Q13）

**旧语义 = 硬人工闸，逐行放行，无批量队列。**

```
/home/user/erpAPI/auto_listing/feishu_io.py:6     E 审核结果  F 理由  G 审核日期  ← 人工审核
/home/user/erpAPI/auto_listing/feishu_io.py:20    写入分工：人工 E / F / G
/home/user/erpAPI/auto_listing/feishu_io.py:423   if r["audit_result"] != "pass": continue
```

`filter_pending` 是整条上架链的入口闸：ASIN/店铺/WPT 齐全 **且 E=pass** 才进 main.py。

**不对称纪律（重要）**：机器可以自动「拒」，但**永不自动「放行」**——
`/home/user/erpAPI/auto_listing/main.py:1731-1755`：某 ASIN 累计失败达阈值（`retry_state.classify/record_failure`，
`auto_listing/retry_state.py:104-116`）时机器写 `E=fail` 永久淘汰；而 `pass` 只能人工填。

> 对 R2-09 的含义：manual 档 1:1 有旧参照；semi 档的「批量待确认队列 + batch_size」在旧仓**无对应**（旧仓是逐行人工），需新建。
> 建议保留旧的不对称性：semi 档允许自动拒/自动淘汰，自动放行只在 auto 档开放。

上一代 erp-core 的对应闸在 DB 里而非表格里：
```
/home/user/erpAPI/erp-core/backend/app/services/audit/pipelines/orchestrator.py:178-190
   verdict=pass → lifecycle_state='audit_passed'
   verdict=reject（L0 命中）→ 'blacklisted'；其它 reject → 'audit_rejected'
   verdict=pending → 'audit_review'          ← 人工复核态
/home/user/erpAPI/erp-core/backend/app/api/v1/listings.py:336-339, 451-453
   提交前校验 lifecycle_state ∈ (audit_passed, listed[, collected])，否则 400
```
`Verdict = Literal["pass","reject","pending"]`（`app/services/audit/pipelines/models.py:8`）——
**pending/audit_review 就是旧系统的人工停点**，新系统 manual 档的 gate 位置可原样照抄。

#### (b) scrape_to_audit（采集完自动送审）

**旧语义 = 只有设计稿，从未实现。**

```
/home/user/erpAPI/erp-core/handoff-design/COLLECTION_REFACTOR_PROMPT.md:67
   4 个 tab 共享一个表单容器，复用 QPS / 重试 / 自动审核 这三个通用配置
/home/user/erpAPI/erp-core/handoff-design/COLLECTION_REFACTOR_PROMPT.md:171
   采集后自动审核: [✓]   命中验证码自动切代理: [✓]
```
全仓 `auto_audit|autoAudit` 零代码命中；后端 `app/api/v1/collections.py` 里也无 audit 相关分支。
审核只能由显式入口触发：
```
/home/user/erpAPI/erp-core/backend/app/api/v1/pipelines.py:79-97   POST /{run_id}/audit（须 run.status=='completed'）
/home/user/erpAPI/erp-core/backend/app/api/v1/audit.py:110-139     POST /audit/runs/batch → dispatch_audit_batch
```
且 13 条 celery beat（`app/tasks/celery_app.py:23-106`）全部是**下游**巡检/同步/重试
（poll feeds / verify listings / full sync / orders sync / auto-retry / unstick / AIMD…），
**没有一条把 collected 推进 audit、或把 audit_passed 推进 publish**。

> 对 R2-09 的含义：该 flow 无旧实现可移植，但设计意图与图纸一致 → 是老需求的欠账，不是新臆想。
> 另注意 flow_code 命名问题，见 §6.6。

#### (c) listing_pricing → 图纸正名 `pricing_watch`（D-Q26）

**旧语义 = 只有「全自动」一档，且同域两套相反口径。**

```
/home/user/erpAPI/auto_listing/sync_price_inventory.py:1-32
   飞书取已上架行 → DMIT 拉最新 → PUT /v3/price + PUT /v3/inventory
   cron: 0 5 * * *（DMIT 2:00 全量采，留 3h 缓冲）；唯一闸门 --dry-run
   节流思路：PRICE_DIFF_THRESHOLD=0.01 价格变动小于阈值跳过 PUT 省配额（auto_listing/config.py）
```
对照同一个域的另一条写路径：
```
/home/user/erpAPI/沃尔玛商品维护/README.md:51   `sync` 和 `poll` 可放 cron；`submit` 由人工触发（避免误改）
/home/user/erpAPI/沃尔玛商品维护/README.md:17   安全闸门：默认 dry-run；库存清零必须显式 --confirm-zeroing
/home/user/erpAPI/沃尔玛商品维护/README.md:167  灰度试运行：当前仅单店 A093陈兴勇，人工确认稳定后再扩 --stores
```

> 对 R2-09 的含义：「自动改价」档有可对拍的旧实现（含跳变阈值节流）；「仅报告 / 待确认」两档需新建。
> **两套相反口径正是 D-Q13 要统一的病灶**——R2-09 应在 specs 里把统一口径写死，别让新系统再长出第二套。

#### (d) refund / cancel（D-Q29）

**旧语义 = 纯建议不执行，永不调渠道 API；且人工介入分两类。**

```
/home/user/erpAPI/沃尔玛订单审核/docs/审核服务架构设计.md:34
   建议而非自动执行：所有判断写入「审核结果」列，**永不调 Walmart API 拒单**（高风险）
/home/user/erpAPI/沃尔玛订单审核/docs/审核服务架构设计.md:43
   非目标：不自动拒单（卖家自己看「建议拒绝」决定）
/home/user/erpAPI/沃尔玛订单审核/审核决策.py:1-21, 44-84
   11 条优先级 → 三值输出：「✓ 通过」/「建议拒绝（N 类原因）」/「待人工（M 类原因）」
   钓鱼命中优先级最高且永不被覆盖（是钓鱼标记() / 钓鱼检测.py:144-148）
```

> 对 R2-09 的含义：与 D-Q29「现全人工」完全一致，新系统 refund.py 的映射无冲突。
> 旧的**三值分流**值得抄进 semi 档面板：「建议 X」=带默认动作的一键确认；「待人工」=缺数据需人补，
> 两类混在一个队列里会让运营无法批量处理。

#### (e) order_block（已接线，作为语义统一的基准）

旧仓依据同上（钓鱼命中只写表格不动渠道）→ 新系统 manual=软标记的默认值有旧依据。
但当前实现里 semi 与 auto **行为完全相同**：
```
/home/user/ERP-ALL/backend/src/erp/order/procurement.py:41   if mode not in ("semi","auto"): return
```
→ 三档在该 flow 实际是二元。语义统一时必须显式定义 order_block 的 semi/auto 差异
（例如 semi=冻结但可一键放行、auto=冻结且自动走退单/拒单），否则「三档」名不副实。

---

### 6.4 旧仓唯一的「半自动」真实实现：scheduled_tasks 目标任务

```
/home/user/erpAPI/erp-core/backend/alembic/versions/0011_orders_returns_tasks.py:111-135
   scheduled_tasks(title, task_type, store_id, scope JSONB, total/processed/succeeded/failed,
                   daily_quota, status ∈ pending|running|paused|completed|cancelled|failed,
                   priority, scheduled_at, next_action_at, …)
   task_type: bulk_delete / bulk_retire / bulk_extend_end_date / bulk_publish / sync_orders / sync_returns
/home/user/erpAPI/erp-core/backend/app/tasks/listing_tasks.py:1095-1130
   cron.tick_scheduled_tasks 每 5 分钟推进一小步：查配额 → 取未处理 SKU → 派任务 → 回填计数
```
形态 = **人工定范围+配额 → 机器按配额分批推进 → 可暂停**，正是「半自动」的可用骨架；
图纸 `audit_to_listing.config.batch_size` 语义 ≈ 这里的 `daily_quota`。

但旧生产库 `scheduled_tasks|1`、`daily_quota_usage|1`、`store_quota_config|0`
（rowcounts:33/15/37）——**建了几乎没用起来**。骨架可抄，运行经验没有。

---

### 6.5 前端策略面板 & 「即时生效」的旧参照

旧前端原型里确有一块可直接照搬的配置面板：
```
/home/user/erpAPI/erp-core/handoff-design/project/src/pages-collection.jsx:1566   GET  /api/v1/system/config
/home/user/erpAPI/erp-core/handoff-design/project/src/pages-collection.jsx:1582   PATCH /api/v1/system/config
/home/user/erpAPI/erp-core/handoff-design/project/src/pages-collection.jsx:1620   BoolToggle（开关直绑 config key）
/home/user/erpAPI/erp-core/handoff-design/project/src/pages-collection.jsx:1633   卡片标题挂 Tag「提交即时生效」
/home/user/erpAPI/erp-core/handoff-design/project/src/pages-collection.jsx:1644-1673  「自动化开关」区（enabled + 间隔/轮数数字输入）
后端：/home/user/erpAPI/erp-core/backend/app/api/v1/system.py:82-103（GET 全量 / PATCH 批量 + 回读 current）
```
交互模式（读全量 → 局部改 → PATCH → 回读 → 成功/失败横幅）可 1:1 沿用，
把 key/value 换成 (flow_code, mode, config) 三元组 + 团队维度即可；
三档需从「开关」换成 segmented 三选一，并展示 `updated_by/updated_at`（automation_policy 有这两列，09:153）。

**「即时生效」的实现差异是本路线最有价值的技术提醒**：
- 旧：`system_config.get_config` **每次读库**（`app/services/system_config.py:40-51`，全文件无 cache/TTL/pubsub）→ 60s 生效天然满足，代价是每次一条 SELECT；
- 新：TTL 缓存 + Redis pubsub `erp:config:invalidate`，**fail-open + TTL 兜底**（`specs/001-domain-model/09-platform.md:190`）。

→ 若 automation_policy 走缓存，TTL 必须 ≤60s，否则 pubsub 故障时「切档 60s 生效」验收不成立。
最保守且有旧依据的做法：**档位读取不进缓存**（每次读库，档位读 QPS 极低），pubsub 只服务其它配置。
旧仓另一处印证同样口径：`沃尔玛商品维护/README.md:89` +
`walmart_maintenance_common.py:477-497`（stockzero 店名单「实时来源于配置表，每次启动时重读」）。

---

### 6.6 唯一的旧语义 vs 图纸冲突（已被 Owner 修订）

```
/home/user/erpAPI/沃尔玛问题商品清理/daily_cleanup.py:585      唯一闸门 --dry-run
/home/user/erpAPI/沃尔玛问题商品清理/daily_cleanup.py:630-634   Step 2：DELETE_ITEM 批量删除
/home/user/erpAPI/沃尔玛问题商品清理/daily_cleanup.py:669-672   Step 7：永久禁售 ASIN 入黑名单
/home/user/erpAPI/定时任务skill/walmart-daily-cleanup/SKILL.md   每 6 小时无人值守跑
```
= 旧系统在「报错回收/清理」链上**无人工闸全自动删除 + 自动入黑名单**。

已由 D-Q65②（`/home/user/ERP-ALL/specs/000-founding/DECISION-FORM.md:275`）拍板修订为**人工闸**，
既有考古 `/home/user/ERP-ALL/.agent/evidence/R2-12/archaeology.md:53-56` 已提出该问题。

> 对 R2-09 的含义：凡涉及「删除 / 下架 / 入黑名单」的自动动作，默认档必须 manual，
> 不得以「旧系统就是全自动」为由回归旧行为。**这是本路线找到的唯一一处旧语义与图纸正面冲突，且已有裁决。**

---

### 6.7 顺带核出的两处工单文本欠账（属规划输入，动工前须定）

1. **flow_code 名字对不上**：工单/007 写 `listing_pricing`、`scrape_to_audit`
   （`.agent/review_list.json:550`、`specs/007-mvp-completion-plan/README.md:76-77`），
   但 001§09 注册清单（`specs/001-domain-model/09-platform.md:158-166`）只有 7 项：
   `audit_to_listing / compliance_block / order_block / refund·cancel / pricing_watch / gtin_alert / suspension_reminder`
   —— `pricing_watch` 才是正名，**`scrape_to_audit` 图纸里根本没有**。
   而 09:156 要求 flow_code「代码 Enum 对照 + CI 校验」，Enum 不含它时接线会被 CI 拦。
   → 需 Owner 批准修订 001§09 增列，或把采集送审并入 audit_to_listing。旧仓两者都没实现，无第三方证据可裁。

2. **「仅通 order_block」已过时**：refund/cancel 在 R2-07 增量2 已半接线——
   `/home/user/ERP-ALL/backend/src/erp/aftersale/refund.py:33-44`（读 automation_policy，flow_code=kind）、
   `:88-93`（auto 档 `REFUND_AUTO_NOT_WIRED` fail-closed，注释明写「随 R2-09 接线后开放」）。
   R2-09 在 refund 上的真实缺口只有 auto 档的渠道执行链（approved/auto → executing → outbox return_refund + verify-back）。
   这也是「未接线档位应 fail-closed 而非静默降档」的既有样板，其它 flow 照此办理。

3. **档位读取无统一 helper**：两处已接线都是内联裸 SQL + 各自映射词表
   （procurement.py:33-42 二元拦截 vs refund.py:33-44 `_MODE_MAP` 三值映射）。
   「三档语义统一」的第一步应是抽 `resolve_mode(team_id, flow_code)` 统一读取点
   （含无策略行=manual 的 fail-closed 缺省、缓存/失效口径、审计留痕），再把两处改造过去。

---

## 6 各路未查清项（gaps）
> 侦察方自报的未查清项。已被第 1~3 节吸收的不再重复处理，其余留作实现期核实点。

**路线1：决策链与图纸口径 —— 三档语义与 flow 全集的权威定义**
- `scrape_to_audit` 是否作为 v2 新增 flow_code 登记（007:77 提名，001 全无出处；但 007:81-82 验收要求采集→审核这一环能停）—— Owner 拍板：补登 flow 还是把验收改成三环
- D-Q65②（DECISION-FORM.md:275，宪法级）要求的 maintenance_task runner「人工/半自动档」是否注册为 flow_code —— 001§06 maintenance_task 全表（06-listing-pricing.md:145-161）无档位列、无 flow 关联，该决策目前悬空，R2-09 不接就是静默偏离
- `gtin_alert` / `suspension_reminder` 两行是否从 flow_code 清单删除 —— 阈值/节奏的实际落点已分别是 team_config（03-catalog.md:121，R2-04 落地）与 schedule 种子 config（R2-07 07b 落地），保留会造成双落点
- `compliance_block` / `order_block` 的 semi 档如何定义 —— 图纸原文均为二元（09:161/09:162），且 order_block 已上线为 semi≡auto（07:84）。是补出 semi 独立语义（改已上线订单冻结行为，有回归风险）还是图纸注明二元、面板隐藏 semi
- `pricing_watch` semi 档「待确认」的停留态落在哪 —— maintenance_task.status 枚举（06-listing-pricing.md:151）无待确认态。加新状态 / 加 approved_by 门 / 复用 skipped + 人工放行，需定
- `audit_to_listing` 半自动「批量待确认队列」（09:160，config=batch_size）的承载实体未指定 —— review_case/统一待办箱是 RS-06 P1（仅见 external-review-round-1.md:27/143/199），001 里不存在。是各域就地实现还是等 RS-06
- 03-catalog.md:32「match 模式跳过 sourcing 由策略配置（automation_policy）」用哪个 flow_code / config 键 —— 图纸未命名
- automation_policy 是否纳入 Redis pubsub `erp:config:invalidate` 频道 —— 09:190 只写了 system_config/team_config；且该处写 fail-open，与「档位取值须 fail-closed（读不到=manual）」的合规底线需在图纸显式分开表述
- auto 档 guardrail 键集合待定 —— 仅 refund 有 amount_ceiling（09:163）；BR-MT-004（ledger:149「submit 必须人工触发，防误改在架商品」）的动机应转为 pricing_watch auto 档护栏（改价幅度/批量上限/仅 live 非 locked），具体键名与默认值需 Owner 定
- 002 契约完全没有 automation policy 端点（openapi-v0.yaml 仅 :2159 一处描述性引用）—— 前端策略面板需新增整组路径 + 权限点（automation.read/write）+ codegen，工单文只写了「前端策略面板页」一句，工作量口径需与 Owner 对齐
- 「automation 边界纪律（只编排不持业务状态）」评审已采纳（external-review-round-1.md:28）但从未写进 00-conventions —— 是否随 R2-09 清偿
- R2-09 标【L1】但 refund 执行片是真实渠道写（POST refund，07:209 执行纪律要求灰度期仅 is_test 店）—— 工单等级是否按 D-Q54（DECISION-FORM.md:209）修正为【L1（refund 片 L2）】
- 本路线未核代码：automation_policy 实际迁移（0025）落库列是否与 09:143-155 图纸一致、order_block 消费点位置、flow_code Enum 是否已存在 CI 校验（09:156 明写要求）—— 归路线2/3

**路线2：automation_policy 现状接线盘点（"只通 order_block"到底什么意思）**
- `listing_pricing`（工单/007 用词）与 `pricing_watch`（09:164 图纸用词）是同一 flow 还是两个？需 Owner 裁定命名，且若取图纸名要同步改 review_list 的 check 文案。
- `scrape_to_audit` 不在 09:158-166 注册清单里——是补进图纸（改宪法级文档需 Owner 批）还是本单不做？工单验收明确要求「采集→审核」自动，倾向补进图纸。
- order_block 的 semi 与 auto 语义差异未定义（现状同分支，07:83 原文本就是 off/on 二值）。是给 semi 补「批量待放行队列」语义，还是承认二值并改 09:162 的三档表述？需 Owner 裁。
- gtin_alert / suspension_reminder 已用 team_config/system_config/schedule.config 实现（tasks.py:614-643 / :798-800）。R2-09 是把它们迁进 automation_policy（动已绿告警链），还是在 09 图纸剔除并写明「阈值类走配置中心/档位类走 automation_policy」的分界？建议后者，需 Owner 拍。
- 「切档 60s 生效」的口径未定：procurement 是每次调用实时读表（0 秒生效），refund 是创建时快照落 mode_applied（refund.py:118、openapi-v0.yaml:2159，切档不影响已建单）。两种口径都合理但验收判据不同——需明确「哪些 flow 实时求值、哪些快照」，写进 09 图纸。
- automation_policy 是否纳入 ConfigService 未定。现状 ConfigService 只认 system_config/team_config（config_service.py:106/:109），policy 走裸 SQL；若要吃 R2-04 的 pubsub 失效广播就得扩第三来源（新键空间设计），若坚持 DB 直读则「pubsub 即时生效」这条验收改为「无缓存故 0 秒生效」——需 Owner/审计确认这样算不算满足工单。
- 策略面板的权限点命名与角色授予矩阵未定（全仓无 automation.* 权限码）。建议 automation.policy.read/write + 仅团队管理员，但需与 R1-03 角色矩阵对齐后落 migration（ar 帽）。
- refund auto 档执行需要 channel_command 新 action（return_refund，0038:20-21 现行集没有）+ outbox.ACTIONS 同步扩 —— migration 归 ar 帽，本单开工前需先提单排期。
- 09:163 的 config 关键项 amount_ceiling（auto 档金额上限）与 09:162 的 check_kinds、09:160 的 batch_size 三个 config 语义都尚无任何代码消费，且 config jsonb 无 schema 校验。R2-09 是否为每个 flow 定 config schema（pydantic 模型 + 面板表单校验）？范围需 Owner 确认。

**路线3：四条待接 flow 的现有链路 —— 每条链在哪能插"停"点**
- flow_code 定名冲突（必须 Owner 定）：001§09:156-166 注册清单是 audit_to_listing / compliance_block / order_block / refund / cancel / pricing_watch / gtin_alert / suspension_reminder；工单 check 里的 listing_pricing 与 scrape_to_audit 都不在清单内。listing_pricing 建议并入 pricing_watch（语义重合）；scrape_to_audit 是新增 flow，需批准补进 §09 图纸再落 Enum，否则「代码 Enum 对照 + CI 校验」自相矛盾。
- audit_to_listing 的 auto 档缺配置项（图纸修订，需 Owner）：allocate 必须要 store_id + offer_mode，而 §09:160 的 config 只有 batch_size。要不要扩 default_store_id / offer_mode / 按店轮转规则？
- D-Q25 货源前置门与 auto 档的冲突未裁定：allocate 现放行 audit_passed（listing/service.py:260 注释自认「sourcing 域 R2 接入后收紧为 ready-only」），sourcing/ready 两态无人写；DECISION-FORM.md:151 的 Q43（该门是否对所有建品强制）仍是开放点。auto 档一开就等于系统批量绕过货源门——建议 auto 档准入收紧为 ready-only，但需 Owner 确认。
- auto 档撞 30% 确认阈值（BR-PR-008）时的正确行为未定：静默 force 是绕过守护闸（违合规底线），转人工确认则需要一个驻留落点。我倾向「标记 skipped + 通知 + 下轮盯价重新生成」，但这属语义决策。
- refund auto 档的 amount_ceiling（§09:163）无任何代码消费、config 无该键；超上限时是拒绝创建、还是自动降 approval 档等人批？未查到任何裁定。
- refund/cancel 的渠道 API 具体端点与幂等语义未在本次侦察范围内核实（属路线「渠道 API」）：只确认了 ck_cc_action 枚举缺 return_refund，未核实 Walmart 侧退款/取消端点的配额与回执字段（channel_ref 存什么）。
- maintenance_run 的 config.kinds 这套 ad-hoc 三档（0037:31-34 注释自称 D-Q13/29 三档）与 automation_policy 是两套开关。归口方式（kinds 改由 automation_policy(pricing_watch).config 派生？还是 maintenance 系单独一个 flow_code？）需要定，涉及 R2-12 已落地行为的兼容。
- semi 档「批量待确认队列」的 UI 归属未定：audit_to_listing 的待确认可以停在 product.status='audit_passed'（产品页现有列表）或停在 listing draft（上架页现有列表），两者都不用新建页；但 pricing_watch 的待确认需要一个「维护任务列表」页，当前前端 14 个页面里没有。这是 FE 工作量估算的关键分歧点。
- 本次未验证 audit_run.status='queued' 是否值得启用（DDL 默认 queued + ix_audit_run_active 部分索引已建，0008:96/:114-115，但代码直接插 running）。若 scrape_to_audit auto 档要做「排队送审 + 限流」而非「beat 直接扫 product」，这个队列位是现成的——两种设计需选一。
- 未实际跑起来验证（只读侦察）：beat 是否已在部署机运行、automation_policy 表是否有 A152 团队的实际行、schedule 表当前 enabled 状态。验收前需在部署机核实。

**路线4：切档 60s 生效的基建 —— R2-04 Redis pubsub 配置广播复用可行性**
- ConfigService 零读者（全仓配置读取都是调用点直连 SQL）与 001 §09-platform.md:257「服务层统一 ConfigService」是既存图纸偏离——需 Owner/审计裁定：单独立技术债工单，还是改图纸承认现状。R2-09 不应顺手扩范围（铁律3），但工单里要显式记一句「本单不吃 pubsub」，否则与 007:79 的字面表述冲突。
- flow_code 命名与图纸不一致：工单 check 写的是 listing_pricing / scrape_to_audit，而 001 §09:158-166 的注册清单里是 pricing_watch，且**没有 scrape_to_audit 这一项**（清单为 audit_to_listing / compliance_block / order_block / refund·cancel / pricing_watch / gtin_alert / suspension_reminder）。按铁律1 需以图纸为准或经 Owner 批准改图纸——同时图纸注明「代码 Enum 对照 + CI 校验」，该 Enum 与 CI 校验目前也不存在（未在仓内找到）。
- maintenance_run 的三档走 schedule.config.kinds（listing/maintenance.py:3-4,29），不在 automation_policy 里，且 001 flow 清单无 maintenance 项——「三档语义统一」是否要把它收归 automation_policy，需 Owner 定 flow 清单是否扩项。
- 「切档 60s 生效」的计时起点未定义（API 返回时刻 / audit_log.created_at / DB commit 时刻），且未定义验收对「已在飞行批次」的处理口径——建议 T0 取 audit_log.created_at、飞行批次按 D-Q64 批次原子性豁免，但需 Owner 拍定后写进工单验收条。
- 生产是否存在多 api 副本未核实：compose 为单副本单 worker（infra/docker-compose.yml:45-56，uvicorn 无 --workers），部署机实际起法（infra/local-deploy/README.md 只给 make up）未逐项确认。当前「不缓存」路线下副本数无影响；仅当日后引缓存时才需要复核每副本订阅。
- 各 flow 的 mode_applied 快照落点尚未设计：refund 有专列（0031:57-59），audit_to_listing / listing_pricing / scrape_to_audit 落在哪张表的哪一列（或是否统一落 audit_log/task_run.stats）需在增量设计时定——涉及是否需要新 migration（仅 ar 帽可动）。

**路线5：前端策略面板 —— 契约缺口与 008 规范约束**
- flow_code 最终行集未冻结（Owner/契约定）：001§09:156-166 的 7 行 vs 代码按 kind 读 refund/cancel 两行 vs 007 提的 listing_pricing、scrape_to_audit 不在图纸——面板行集与标签取决于此，前端无法先行
- config(jsonb) 每 flow 键位如何在契约里表达未定：图纸只给"config 关键项"（batch_size / check_kinds / amount_ceiling / frequency / warn_pct,critical_pct / remind_days，09-platform.md:158-166），契约里是 typed per-flow schema（oneOf by flow_code）、还是自由 object + 后端校验，直接决定面板是结构化表单还是 JSON 文本框
- 权限点命名需 Owner/ar 拍板：automation.policy_read + automation.policy_write（对齐 channel.store_read/channel.quota_write 读写分离）还是单个 automation.policy_admin（对齐 audit.policy_admin / scrape.node_admin）；module 取 'automation'（代码包名）还是 'platform'（001 文档名）
- 店铺级覆盖缺口：D-Q26（DECISION-FORM.md:115）说盯价频率"后期同事可配"、09-platform.md:164 写 pricing_watch frequency"店铺覆盖 D-Q26"，但 automation_policy 只有 team_id 无 store_id（0025:298-310）。面板要不要店铺维度？加列属 migration（ar 帽），本单是否做需 Owner 定
- 面板归属未定：D-Q11 的"业务参数配置中心 UI 化"（DECISION-FORM.md:59 第7条）目前 system_config/team_config 也无任何前端页（frontend/src/pages/ 无 ConfigPage）。自动化面板是独立 /automation 页，还是作为"配置中心"页的第一个 Tab（CompliancePage.tsx:12-36 的 Tabs 壳），未有决策
- 008§6 第三条"后台任务（beat/维护任务/导入作业）状态页面可见"仍缺 beat 侧：前端零 task_run/schedule 视图（grep frontend/src 无命中），契约也无 /schedules、/task-runs（openapi 路径全集里没有）。R2-09 的"即时生效"验收若要在 UI 内自证，需要这块——是否顺带做需 Owner/编排定
- 008§7 FE-TEST-01（api/client.ts 单测 + ≥1 交互密集页单测）尚未落地，前端测试仍为 0（frontend/package.json 无 test 脚本、devDeps 无 vitest；ci.yml:73-88 无测试步骤）。新面板是否随单补测试，未有工单口径
- 无 FE-DEBT 台账文件：FE-DEBT-01 只写在 specs/008-frontend-conventions/README.md:28-31 正文里，没有独立台账（grep FE-DEBT 全仓仅命中该文件），审计侧按 §5 查 PR 后记账到哪未定

**路线6：旧仓/旧系统的三档语义有无可考**
- 【未挂载仓】/workspace/walmart-audit-system（旧「新审核系统」源码，erp-core 内嵌 audit 引擎的上游）本工作区不可达。需查三件事：① 该仓是否有 verdict=pending → 人工复核队列的**处理入口与 SLA/超时自动放行**语义（erp-core 内只落了 lifecycle_state='audit_review'，没有队列消费端）；② 是否存在 audit→listing 的自动送审/自动放行开关（erp-core 侧确认没有，但引擎源仓可能有）；③ L0-L4 各层的 run_l3/run_l4 开关是否曾被做成配置而非 API 参数（erp-core 是 API Query 参数，pipelines.py:79-84）。建议 grep 关键词：auto_audit / 自动送审 / review_queue / 待复核 / pending 队列 / flow 开关。若查不到，R2-09 的 semi 档「审核后待确认队列」按纯新建处理，风险可控。
- 【须 Owner/规划拍板】flow_code 全集口径：001§09 注册清单无 scrape_to_audit、且 listing_pricing 的图纸正名是 pricing_watch。是修订 001§09 增列 scrape_to_audit（并统一 pricing_watch 命名），还是把采集送审并入 audit_to_listing？09:156 要求「代码 Enum 对照 + CI 校验」，不定就没法写 Enum。旧仓两者都无实现，无外部证据可裁。
- 【无旧语义可考，须图纸/Owner 定义】semi 档「在设定环节停」的**停点全集**：旧仓的 scheduled_tasks 只有「配额节流 + 可暂停」，没有「按环节停」的概念。采集→审核→上架→定价四环里，semi 档到底在哪几个点停（分配前？feed 提交前？改价 PUT 前？），以及停点是否可 per-flow 配置（config JSONB 里放 stop_at 列表？），本路线找不到任何旧参照。
- 【无旧语义可考】多租户/多层覆盖的合并优先级：旧 system_config 无 team_id 维度（单租户单表），旧仓的店铺覆盖是脚本各自读配置表（TEST_STORES 白名单、stockzero 名单），没有「团队档位 + 店铺覆盖」的合并规则先例。automation_policy 是 (team_id, flow_code) 唯一，D-Q26 又要求 pricing_watch 支持店铺覆盖——覆盖放 config JSONB 还是新增行，需实现侧定口径（本路线建议放 config，理由见 §6.3(c)/§6.5，但无旧证据支撑）。
- 【本路线未核实，属其它路线范围】新系统 ConfigService 的 TTL 具体秒数、以及 automation_policy 是否已纳入 erp:config:invalidate 广播通道——本路线只从旧系统侧给出「旧是每次读库、天然满足 60s」的对照结论，新系统缓存现状需由现有代码侧路线（R2-04 pubsub 实现）核实后才能判定 60s 验收是否成立。
- 【本路线未展开】旧仓 auto 档的**实战节流参数**只抽查到价格链（PRICE_DIFF_THRESHOLD=0.01、每日 5:00 单次全量、单店批次间 sleep 360s 满足 10/hour）。若 R2-09 的 auto 档要真跑全链，上架侧的配额口径（auto_listing/quota.py 从飞书『定价』表读单店单日上架/下架上限、北京 0 点重置）还需按 flow 逐条搬进 automation_policy.config 或 system_config，本路线未逐项列全。
