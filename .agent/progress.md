### Session: 2026-07-10 (founding)
- Repo bootstrapped: founding docs migrated from erpAPI specs/011 + PRODUCT-TEAM/TEAM; CLAUDE.md/README/.agent/.claude-agents initialized.
- Next: EA-001 domain model (ar hat).
### Session: 2026-07-10 (EA-001)
- specs/001-domain-model/ delivered: 00-conventions（命名/公共列/RLS三层/分区保留表/枚举策略/金额汇率/FK纪律/加密/Redis边界）+ 01~09 九个上下文文件，80 表列级定义，决策依据 D-Qxx 内联可追溯。
- 关键落地：门户三件套隔离（portal_account+portal_app角色+portal_procurement_v视图）；GTIN 并发分配协议（FOR UPDATE SKIP LOCKED）；feed verify-back 建模（channel_feed_id NULL + verify_pending/lost 状态）；配额下发点原子扣减；品牌占用 partial unique；automation_policy flow_code 注册清单。
- Owner 开放点 4 项：feed_item 保留、邮件正文 30 天、scrape_result 90 天、上架去重服务层实现。
- Next: EA-002 OpenAPI 契约（等验收可并行起草）。
### Session: 2026-07-10 (EA-002/003/004)
- EA-002: specs/002-api-contract/ 契约草案 v0（README 全局约定 + openapi-v0.yaml，YAML/引用已校验）。
- EA-003: specs/003-r1-plan/ R1-01~R1-12 工单（依赖图/验收标准/permission 种子/不做清单），已注册 review_list。
- EA-004: specs/004-aliyun-provisioning/ 配置单（RDS PG16 HA 8C32G+500GB / ECS 8C16G / VPC / ICP 三选一 / 月费 ¥3.8k-5.5k / 四扩展验证优先）。
- R0 收尾四件套全部交付；等 Owner: 开放点拍板 + 云开通。R1-01~06 可先行（本地 compose 不依赖云）。
### Session: 2026-07-10 (R1-01)
- Owner 验收 R0（开放点默认值生效）→ R1 开工。
- R1-01 done: monorepo(backend FastAPI+alembic / frontend Vite+React+AntD / infra compose+pg-init 三角色) + CI 双 job 首跑绿(run 29077720593, 42s)。
- 沙盒限制记录：无 docker daemon → compose 实测靠 CI service 容器 + Owner 侧；本地无 PG → R1-02/03 的 DB 集成测试放 CI 跑（或沙盒 apt 装 PG，下 session 决定）。
- Next: R1-02 ConfigService + R1-03 migration 基线（ar 帽，注意先后依赖：ConfigService 的表在 R1-03 定义，两单联合交付）。
### Session: 2026-07-10 (R1-02/03)
- 沙盒 apt 装 PG16.13 → 真库全套验证。migration 0001 infra(schema app/角色容错/touch触发器/ensure_month_partitions/GUC函数) + 0002 identity(8表+RLS+审计不可篡改+auth SECURITY DEFINER通道+种子36权限点7模板角色) + 0003 channel(7表+代理独占部分唯一+store-join RLS) + 0004 system(5表+partition_maintain调度种子)。
- ConfigService(R1-02) 落地并真库测试。12 集成/单元测试全过×2（幂等）。
- 偏离：portal_account→R2#6（purchaser 依赖）。CI 补 ERP_DATABASE_URL。
- Next: R1-04 认证+RBAC中间件+审计出口（be-domain 帽）。
### Session: 2026-07-10 (D-Q52 + R1-04)
- D-Q52 定案：试点期全本地部署（MySQL 实例退订、EA-004 deferred）；infra/local-deploy/ runbook+backup.sh 交付。
- R1-04 done: 0005(locked_until+auth_record_login) + security/authn/audit + identity/service(login/refresh 自管事务) + main(request_id中间件+401/403信封)。24 测试×2 全过。
- 踩坑记录：SET LOCAL 不支持绑定参数 → set_config('key',:v,true)。
- Next: R1-05 identity API + 前端骨架（be-domain+fe 帽）；等 Owner 提供本地部署机配置以细化 runbook。
### Session: 2026-07-10 (R1-05)
- 后端: identity 全端点(8715c6a)。前端: 契约codegen客户端(refresh重试/信封解析) + AuthContext + 登录/成员/角色/审计四页 + 权限渲染菜单布局。
- E2E: 沙盒起真实全栈(PG+uvicorn+vite)，Playwright(预装Chromium executablePath)冒烟全过，6截图发Owner。
- 踩坑: AntD 双字按钮空格；vite dev proxy 即联调环境。
- Next: R1-06 通知中心骨架 或 R1-07 渠道网关移植（07 需 A152 凭证入库，依赖 R1-08 凭证维护——顺序上建议 06→08→07）。
### Session: 2026-07-10 (R1-06)
- 0006 通知三表(月分区+RLS) + notify()唯一入口(dedupe 24h) + run_tracked(task_run记账+失败→critical通知, 静默失败即缺陷) + 通知API(可见性=团队/本人/全局) + 前端铃铛/通知页。
- 关键新增: core/db.system_tx —— worker/beat 系统事务上下文(is_super GUC, 仅限非用户路径), 解决后台任务被 RLS 拒写。
- 33 测试×2 + 前端 build + E2E notify-smoke 截图×2 已发 Owner。
- Next: R1-08 店铺/代理/配额(界面录 A152 凭证) → R1-07 渠道网关。
### Session: 2026-07-10 (部署机确定)
- Owner 已退订 MySQL；部署机=Win11 Pro 台式机(Ultra7 265K 20核/48G/990PRO 2TB)——远超需求。
- infra/local-deploy/windows.md 交付（Docker Desktop+wslconfig 20G限额+电源/更新/固定IP/防火墙+Git Bash 备份挂任务计划+self-hosted runner）。
- 注意事项：可能是 Owner 主力机——WSL 已限 20G 互不干扰；正式团队切换时再评估专机。
### Session: 2026-07-10 (R1-08)
- channel 域完成：service(pgcrypto凭证/配额原子扣减+返还/代理独占) + router(契约全端点+封店联动) + 前端 店铺页(凭证/代理/配额三Tab抽屉)+代理页。
- 35 测试×2 + E2E channel-smoke 4 截图已发 Owner。踩坑: 可空绑定参数进 pgcrypto 需 cast。
- Next: R1-07 渠道网关移植（be-channel 帽，walmart_client+GCRA 考古；dry_run/live_test/live 三模式；A152 凭证待 Owner 部署后界面录入，真调冒烟可后补）。
### Session: 2026-07-10 (R1-07)
- 渠道网关移植完成：gateway/rate_limiter.py(GCRA async+实测限流表+响应头自适应) + gateway/client.py(三模式闸/按代理池化+半死自愈/token 900s+401自愈/五头/429退避)。
- 全离线验证 11 用例(MockTransport)；46 测试×2 全过。考古对照表+dry-run 快照入证据。
- A152 真调= Owner 部署机执行(live_test 模式)，沙盒宪法禁真调。
- Next: R1-09 采集最小闭环（v3 worker 协议移植）。
### Session: 2026-07-10 (部署支援 + R1-09 启动)
- Owner 本地 AI 部署报告：db/redis/api 健康+重启自愈实测过+备份/任务计划就位；前端 install 被 pnpm 11 卡死。
- 根因是仓库缺陷：corepack 无版本约束拉到 pnpm 11（不再读 package.json pnpm 字段）。修复=packageManager 钉死 10.33.0 + pnpm-workspace.yaml 批准 esbuild + compose 内置 restart 策略（452e8bf）；CI 又踩一坑：action-setup 只读仓库根 packageManager，需 package_json_file 指路（2555114，绿）。
- 教训入档：工具链版本必须钉死（corepack/CI/本地三处同源）；本地 AI 泄漏初始密码一次已自轮换（处置正确，无需追加）。
- R1-09 考古完成：v3 worker 协议 = pull(原子租约+lease_epoch)/release(校验+bump)/result(+batch, stale 检测)/worker.sync(心跳+指标+配额下发+重启标记)/回收(死worker+硬超时→bump epoch)+auto_retry(轮次上限+不可重试错误类型排除)。PG 移植要点：SQLite 写锁+BEGIN IMMEDIATE → FOR UPDATE SKIP LOCKED。
- Next: 0007 迁移(scrape_job/scrape_task/scrape_result) + worker 拨入 API + 采集结果→product upsert（去重协议见 001/03-catalog）。
### Session: 2026-07-10 (R1-09)
- 采集最小闭环完成：0007(product/variant/scrape三表+worker_node+scrape权限补种39码) + scrape/service(租约=attempt兼lease_epoch、FOR UPDATE SKIP LOCKED领取、断连回收、product upsert不重置status) + 双路由(UI契约4端点 + /worker/v1机器协议5端点, node token认证+enroll闸)。
- 契约补录 Scrape 段 + worker-protocol.md 机器协议规格；考古对照表入证据(舍弃项：prefer_zip/批量回传/auto_retry→R2)。
- 56 测试×2 全过(新增10)。真实 worker 引擎(curl_cffi/AIMD/session池)不在本单，随选品排期。
- Next: R1-10 审核最小闭环（be-domain[审核] 帽，源=walmart-audit-system，考古纪律：先抓策略/提示词对照表）。
### Session: 2026-07-10 (R1-10)
- 审核最小闭环完成：0008(audit五表+黑名单四表+refdata.trademark+种子: 策略9条/黑名单10行/audit权限3码→42) + audit/pipeline(L0四层含占位符白名单/L2 R4词边界+R5 LIVE反查软证据/L3单策略IP) + llm.py(sha256输入缓存+usage记账含命中0成本行+单价走system_config) + service(orchestrator语义: L0短路→L2证据→L3判定→product状态联动) + 契约Audit段3端点。
- 保真锁死: _coerce规范化(非法verdict→pass保守/旧标签映射/is_real_brand强制翻案)4单测; policy.version进prompt首行=配置变更自动新缓存键; L2查询ORDER BY保缓存键确定性。
- 67 测试×2 全过(新增11)。R2欠账在archaeology.md: L1/L4/R1-R3,R7-R8/37条政策全量(lark OJSrkV)/AC自动机。
- Next: R1-11 上架最小闭环（be-channel+be-domain 帽；listing六表+allocate/submit/轮询/回写；GTIN 20个EAN-13入池；A152真实上架1个SKU——需Owner部署机就绪+凭证入库后执行, dry-run部分沙盒可先行）。
### Session: 2026-07-10 (R1-11 沙盒范围)
- 上架最小闭环(沙盒部分)完成：0009(gtin_pool+listing七表+错误字典种子5条) + listing/gtin(GS1校验/单语句防双占/used永不回收) + spec(v5构建器+build_hash缓存, WPT=attrs.wpt>listing.default_wpt) + service(状态机唯一出口transition+allocate去重advisory lock+submit组批扣配额+poll item级权威回写+verify-back adopt/lost+delist+retry按处置策略) + 路由全契约端点+gtin导入。
- 自踩自修: 网关传输错误返回status=None不抛异常——必须当"结果未知"进verify_pending, 不能落"渠道拒绝"分支; 网关单例连接池缓存transport, 测试注入替身必须清池。
- 75 测试×2 全过(新增8)。A152 真调 runbook 交付(evidence/R1-11/a152-live-runbook.md)——等 Owner 部署+凭证。
- Next: R1-12 E2E 演示与失败路径(qa 收口)；A152 真调结果回来后补 R1-11 收尾。
### Session: 2026-07-10 (R1-12 沙盒演示)
- E2E 全链演示跑通：采集(真机器协议拨入)→审核(mock LLM)→分配→提交(live_test→mock渠道)→poll→live→delist；失败三路径(审核拒绝/配额耗尽/feed错误→字典→critical通知)；11 截图入证据。
- 支撑改动: 网关 BASE_URL→Settings.channel_base_url; notify 接入 poll 终态; catalog 最小路由(GET /products[/{id}]); 前端新三页(采集作业/产品库/上架管理)+菜单权限门控。
- 顺手修缺陷: transition 把 reason_code 误写 error_code(live 行挂红标)→仅 failed/degraded 落值; 提交0项不再弹 undefined 提示。
- 75 pytest×2 + 全静态检查 + 前端 build 绿。踩坑: Settings 字段→env 名对应(llm_api_base=ERP_LLM_API_BASE, 写错会真连 deepseek); AntD Drawer 用 .ant-drawer-close 关, Escape 不可靠。
- R1 收口状态: 01-10 ✅CI绿; 11 沙盒✅+A152待Owner; 12 沙盒✅+Owner验收待执行(owner-acceptance-runbook.md)。
- Next: 等 Owner 验收回执→R1 关账→R2 规划(选品/订单/邮件/定价/自动化)。期间可做: R2 规划草案/欠账清单整理。
### Session: 2026-07-11 (D-Q54 验收对齐重排)
- Owner 验收实测揭示错位：真任务卡 pending(无采集引擎)/审核缺弹药(黑名单10行/商标库空/政策1条/无L1)/spec骨架撑不起真上架——Owner 指示"测试验证要和开发进度相符"。
- 落档：D-Q54(数据真实性等级 L0模拟/L1真实只读/L2测试店写/L3正式店写, 验收必须匹配等级) + specs/005-r2-plan/README.md v2(R2-01采集引擎/R2-02审核弹药/R2-03上架真实化=Owner三缺口一一对应, A152真调挂R2-03) + owner-acceptance-runbook v2(R1=骨架验收, 含"明确不验"清单) + specs/003 修正注记。
- 教训: 工单验收判据必须与数据真实性等级绑定, 骨架单禁配真实验收。R1-11 转 done(修正范围)。
- 另: 超管作用团队切换(X-Act-Team+顶栏切换器) b11e03c 已交付(Owner 实测首报缺口)。
- Next: Owner 骨架验收回执 + R2 排期确认 → 注册 R2-01~05 工单开工。
### Session: 2026-07-11 (R1 关账 + R2 开工)
- Owner 回执：验收A ✅(重启自愈实测; 固定IP不懂→已给白话步骤, 列为团队接入前待办)、验收B ✅(重审缺口修复 3b1a8f7 后全过)、验收C ✅ →「R1 验收通过」。
- R1-01~12 全部 accepted 关账；R2-01~05 工单注册（specs/005-r2-plan v2 排序=Owner 三缺口）。
- R2-01 采集引擎移植开工：源 /workspace/amazon-scraper-v3/worker/，目标 workers/ 独立包。

### Session: 2026-07-11 (R2-01 采集引擎移植)
- workers/ 独立 uv 包交付（拨入现有 /worker/v1 协议，不改后端）：
  - **爬虫核心逐字移植**（vendored，ruff/mypy 排除保对齐上游）：parser 2199行/session(curl_cffi TLS指纹+邮编+CAPTCHA)/adaptive(AIMD-Gradient2)/metrics/proxy。
  - **协议胶水（新写）**：erp_client(/worker/v1: register令牌持久化+复用/pull/result/release/sync, MockTransport 可测) + payload(parser扁平dict→ERP product五要素+attrs+price_snapshot)。
  - **编排壳（改写移植）**：engine(流水线+分级处置: 被封/404/降级/variant偏移/空标题, 租约attempt原样回传, 下线归还) + run(CLI, Windows信号兼容) + config(调参+BROWSER_PROFILES)。
  - Dockerfile + compose scraper profile(门控, 令牌/代理走env不落git) + CI workers job。
- 剔除R1协议不承载的v3机制(截图/seller发现/邮编切换/批次门控/全局配额协调→R2-04或后续)，考古对照入 evidence/R2-01/archaeology.md。
- 26 离线单测(payload/erp_client协议/engine分级处置) + ruff/format/mypy 全绿；CLI启动+parser离线解析冒烟过。沙盒不真抓(宪法禁)。
- CI 确认：run 29148288864（5c8e21c）三 job 全绿（backend/frontend/workers）。
- Next: L1 真抓验收待 Owner 机器+TPS 代理(runbook.md)；同时可开 R2-02 审核弹药灌入（已向 Owner 提议并行，待其回复）。

### Session: 2026-07-11 (R2-01 验收通过 + 前端详情 + R2-02 第一片)
- R2-01 Owner 机器真抓验收通过（真抓成功/variant偏移判失败/假ASIN标记不存在；未跑掐-worker回收测试）→ accepted 关账。
- 顺手补前端产品详情抽屉(e7bc6a0)：表格原只有 SKU/ASIN/标题/品牌/状态无处点开，采集的图片/五点/价格/类目一直在库(GET /products/{id})却看不到——加抽屉(图片画廊/价格/五点/全字段中文标签)，标题可点+操作列「详情」。
- R2-02 开工第一片=数据载具：migration 0010 import_job(表+RLS+compliance.import_read/admin 权限→44) + import_service(create_job/import_rows 分块核对+源截断守卫+幂等upsert) + 黑名单四域导入器 + CLI import_blacklist(csv/xlsx/jsonl→system_tx) + 只读路由 + main 注册。
- 归一化锁死：四域主体全走 audit.pipeline._norm，与 L0 _blacklist_lookup 字节一致(否则导进去查不到)；品牌占位符白名单跳过。
- 沙盒起 apt PG16(pgcrypto/pg_trgm；vector 缺=迁移未用)真库验证：0010 升降往返 + 84 pytest(新增6) + ruff/format/mypy 全绿。
- CI 确认：run 29149671287（3a847f7）三 job 全绿（backend 含 0010 迁移升降往返+84测试 / frontend / workers）。前端详情抽屉 e7bc6a0 亦已过 CI。
- Next: R2-02 后续片(黑名单/商标/37政策全量导入 + L1类目判定 + L2全规则 + AC自动机)，见 evidence/R2-02/archaeology.md 后续片清单。

### Session: 2026-07-11 (R2-02 并行片：workflow 模式 2×Opus 子代理)
- 用户启用 workflow 模式（Fable 统筹/决策/集成，Opus4.8 子代理做实现片）。
- 拆两片零文件重叠+各独立测试库(erp_all_a1/a2)：
  - 子代理A(audit)：R4 黑名单匹配→纯Python Aho-Corasick 自动机 + 版本失效内存加载器(count+max(added_at)为版本键，惰性重建)。词边界保真ERP regex，20k差分fuzz vs旧regex 0不符。commit e0dc4ae。
  - 子代理B(compliance)：import_job 加商标域→refdata.trademark upsert(ON CONFLICT serial_no)，mark_norm 走_norm小写(R5契约)、is_live派生、nice_classes解析。R5 parity 测试证明喂通。commit bd517d2。
- 环境坑：worktree 隔离建的是 erpAPI 仓的 worktree(非erp-all)，两子代理各自在/workspace/erp-all-{r4,a2}建 erp-all worktree(共享主 .git 对象库) → commit 可从 main cherry-pick。
- 集成：cherry-pick 两 commit(493d4f8/834048c)零冲突 → 主库全量 102 测试(84+10+8) + ruff/format/mypy 全绿。
- Fable 并行产出：L3 静态37政策 prompt 设计档(l3-policy-design.md，锁死 prefix-cache 不变量+数据依赖)——该片待本片集成后单独做(同碰 pipeline.py L3 + import_service)。
- 子代理B一处越界(justified)：test_unsupported_domain_rejected 例子域 trademark→category_map(因 trademark 现已支持)。子代理A一处有意分歧(safe)：全局+团队重复品牌的 evidence 去重(旧emit两次/新一次，build_user_prompt本就dedup，仅存储evidence差异)。
- Next: L3 静态37政策 prompt 代码片(表+domain=policy导入+拼接，空表退回单策略) + Owner 侧全量数据导入 + L1类目/L2全规则。
- CI 确认：run 29150618944（3e58344）三 job 全绿（backend 102测试+0010迁移往返 / frontend / workers）；AC 排序用 Python sorted()，pgvector/pg16 镜像下 collation 无碍。

### Session: 2026-07-11 (R2-02 第四片：L3 静态 37 政策 prompt，Fable 亲做)
- 由 Fable 在集成树上做（同碰 pipeline.py L3 + import_service，须待 A/B 落地）：
  - 0011 refdata.prohibited_policy 表(category_en PK + seq 排序) + grant；ALTER import_job.domain CHECK 加 'policy'(0010 依 spec 原域集未含)。
  - import_service 加 domain=policy 路径(显式分派替代「非黑名单即商标」隐式回退)+_apply_policy_row(幂等 upsert on category_en)。
  - audit/policy_block.py：版本失效(count+max(updated_at))内存加载 37 政策块，压 240 字/条拼 system prompt 末尾(吃 provider prefix cache，所有产品同一份=前缀稳定)；空表→空块 L3 退回单策略。valid_reason_categories 扩为静态两类+政策 category_en。
  - pipeline.coerce_l3_result 加 valid_categories 参数(默认静态集，向后兼容 4 旧测试)；service L3 拼块+传扩展类目集。
  - CLI import_policy.py + 5 DB 测试(幂等/空表退回/块含全清单+版本失效重建/coerce类目扩展/行数不符)。
- 关键成本不变量锁死：政策块在 system(所有产品同一份)、产品文本在 user → 前缀稳定=cache 命中；政策文本变→system 内容变→llm_cache 键自动失效(无需额外版本标记)。
- 沙盒真库：0011 升降往返 + 107 pytest(102+5) + ruff/format/mypy 全绿。
- Next: L1 类目判定(pt_embeddings+混合检索+LLM复排，需embed client+数据) + L2 R1/R2/R3/R7/R8；Owner 侧黑名单/商标/37政策全量数据导入。
- CI 确认：run 29151294258（7bf606a）三 job 全绿（backend 107测试+0010/0011迁移往返 / frontend / workers）。

### Session: 2026-07-11 (R2-02 第五片：L2 R7/R8 促销+敏感软证据)
- l2_content.py 移植 R7(促销宣称:强促销词表+全大写滥用去噪)/R8(八子类敏感:文化/宗教/政治/历史/武器装饰/成人/物质/卡通IP百余条正则)，penalty=0 软证据。
- run_l2 追加 R7/R8 命中；build_user_prompt surface「促销宣称词」「敏感内容命中」两行喂 L3。
- 纯文本无外部依赖无迁移；13 单测；120 pytest(107+13)+ruff/format/mypy 全绿(46555ed)。
- L2 R1/R2/R3(类目准入/禁售大类/认证)依赖 L1 类目判定，随 L1 交付。
- Next: L1 类目判定(pt_embeddings 向量表+混合检索+LLM复排)——需 pgvector(可apt装)+embedding API(Owner配)+6832嵌入数据(Owner导)。
- CI 确认：run 29151967354(a6e0344)含 46555ed(R7/R8) 三 job 全绿；R7/R8 未误伤既有审核测试(120 pytest 全过)。

### Session: 2026-07-11 (数据治理架构 D-Q55 + L1 方法修正)
- Owner 反馈：数据非一次性导入,需多来源持续维护(店铺后台/邮件/外部收集/USPTO几十G/错误记录/黑名单库),飞书只是表现形式非master；且原类目判定=映射表+LLM非嵌入。
- 诚实交代：已建地基(import_job+refdata+三导入器)但无体系化持续维护方案——现补。
- 交付 specs/006-data-governance/README.md：5核心原则+8数据域+来源×通道矩阵+7类摄取通道(批量/USPTO增量投影/飞书双向/beat定时/人工UI/邮件/反馈闭环)+主数据vs飞书方向规则+溯源版本冲突+落地工单(DG1 category_map/DG2 uspto投影/DG3错误记录反馈/DG4黑名单UI)。
- L1 修正：主路径=category_map+LLM(只卡category_map数据,不卡embedding API)；嵌入降为可选后置。l1-category-design.md 已改。
- D-Q55 入决策表。
- Next(待Owner)：①USPTO uspto库如何到部署机(定DG2形态) ②category_map首批导出(解锁L1) ③黑名单/商标/政策全量导入。我可先建 DG1 category_map导入域+表(不卡Owner的代码部分)。

### Session: 2026-07-12 (外部评审 Round-1 回应 + A4 fail-closed 修复)
- 部署机本地 AI 按 REVIEW-BRIEF 完成全项目评审(21条:A架构7/B治理8/C盲区6)，Owner 贴回聊天。
- 指控级论断逐条到代码核实(A2 GUC可自设/A3 compose缺密钥/A4 fail-open/A5 无kind过滤/A7 持锁调网络/B4 全量进内存)——全部属实。
- 裁定:17采纳/4部分采纳/0驳回，全文 specs/external-review-round-1.md；D-Q56 入决策表。
- **当日修 A4**：0012迁移(product.status+needs_review) + coerce fail-closed(badJSON/非法verdict→needs_review,不再默认pass;is_real_brand翻案覆盖needs_review) + audit_one写llm_needs_review软命中+状态联动 + 前端橙标可重审；测试3增2改，122 pytest+ruff/mypy/tsc 全绿。
- 自查次生问题：坏响应已进llm_cache会让同输入重审复现needs_review→RS-09一并处理(parse_error不落缓存)。
- 注册 RS-01~11 工单：P0三件绑闸门——RS-04摄取升级(立即,14M将至)/RS-03 outbox+幂等(真实写入前)/RS-01+02安全加固(多团队/门户前)。
- Next: RS-04 摄取升级动工(COPY staging+manifest+refdata_revision)；006 正文按 B1/B2/B8 修订；RS-11 漂移清理。

### Session: 2026-07-12 (外部评审 Round-2 收敛 + A4 尾项闭环)
- 本地 AI round-2 反馈 25 条：4 条分歧全收敛(A2/B5/B8 对方接受我方口径+补硬验收；A5 我接受对方坚持——poison/quarantine 不等多节点)；其抓出 A4 修复 3 个实质缺口，核实全属实。
- **当日修 A4 尾项**：①R2-20 chat() 异常兜底(llm_unavailable+needs_review 落痕,不再整体回滚)+policy 缺失→l3_policy_missing(不再静默 pass)；②R2-21 cacheable 谓词(坏响应不入缓存)+命中存量坏行 DELETE 驱逐自愈+0013 迁移补 DELETE 授权；③R2-22 收回"needs_review 也翻案"——仅结构合法 pass 响应可被 is_real_brand 翻案；④R2-23 needs_review 不占 reject_level；⑤R2-24 前端三态(通过/拒绝/待人工复核)。
- 测试：+5 用例(500 落痕/policy 禁用/坏响应不缓存重审自愈 calls=2/存量坏行驱逐/非法 verdict 不翻案)，126 pytest+ruff/mypy/tsc 全绿。测试工程教训：缓存键不含 ASIN,NR 用例需独立 title 防跨用例缓存短路。
- 工单修订：RS-03 拆双闸门(audit 异步前置 R2-02 对拍前)；RS-04 拆 A/B/C/D(B1/B2/B8 补注册)；RS-07 范围扩充(poison 现阶段做)；RS-01/02 闸门改机器可判定事件；各单补 acceptance 字段。
- specs/external-review-round-2.md + D-Q57。A4=待 round-3 复核关闭。
- Next: RS-04A 摄取升级动工(容量预算前置+COPY staging)；006 正文按 B1/B2/B8 修订(随 RS-11)。

### Session: 2026-07-12 (A4 关单 + RS-04A 摄取升级落地)
- 本地 AI round-3 定点复查确认 **A4 可关闭**——正式关单（round-2 文档已记）。CI 995074f/8f4bcd8 双绿。
- **RS-04A 搬运通道建成**：0014 迁移（refdata.dataset_revision 事务内递增 + 统计触发器挂黑名单四表/政策/商标——任何写入方都 bump；trademark_staging UNLOGGED）+ tools/bulk_import_trademark.py（流式 csv/jsonl → COPY staging → DISTINCT ON merge → 按批提交；manifest sha256 断点续跑；错误行 sidecar；故障注入口）。
- blacklist 自动机 / L3 政策块缓存版本从 count+max(ts) 切到 dataset_revision（B5② 收口，消同秒碰撞窗口）。
- 测试 +6（基本+R5 归一对齐/幂等重导/批内重复后者胜/中断→resume 补齐/sha256 变更拒续/任意写入方 bump），132 pytest 全绿。
- **100 万行演练**：48s（20.8k 行/s）稳定无衰减；WAL 1.14GB/M、表+索引 257MB/M。14.18M 预算：~25-40min（部署机）/3.6GB/磁盘预留 8GB/当晚备份+2-3GB（.agent/evidence/RS-04A/rehearsal-1m.md）。
- 余项：14.18M 真实实测待 Owner USPTO 导出。Next: RS-03a audit 异步化（R2-02 百件对拍闸门）或 DG1 category_map 导入域。

### Session: 2026-07-12 (RS-03a audit 异步化落地)
- CI 664e729 绿（静默记）。
- **RS-03a 完成**：audit LLM 调用移出行锁/请求事务——llm.py 拆三段原语(check_cache/call_provider[纯网络零DB]/record_result，chat 保留为单事务组合)；audit_one 改三段式(tx1 短事务锁品+落run+L0/L2+组prompt+查缓存→缓存命中/L0拒/政策缺失同事务终局；无事务 HTTP 最长120s；tx2 重锁+记账+coerce+终局)。
- 配套：router 传 sessionmaker+用户 GUC 上下文(_ctx_tx 每段重放 SET LOCAL)；并发重审"新者胜"(tx2 检测更新 run 则不覆盖 product 状态)；崩溃恢复=懒清扫(>10min running→failed，无需 beat)。
- 验收实证：**FOR UPDATE NOWAIT 旁路探针在 provider 处理请求当口立即拿到行锁**(test_row_lock_released_during_provider_call)；遗孤清扫用例；全部 fail-closed 用例走新路径不变绿。134 pytest+ruff/mypy 全绿。
- R2-02 百件真实对拍的闸门(评审 R2-06)已解除。余 RS-03b channel outbox 挂 A152 前。
- Next: DG1 category_map 导入域(L1 前置，不卡 Owner) 或候审 Owner 数据到位后跑百件对拍。

### Session: 2026-07-12 (DG1 category_map 导入域落地)
- CI 066adb9 绿（RS-03a 收口，静默记）。
- **DG1 完成**：0015 迁移（refdata.category_map 列级保真移植源仓 walmart_category_map + PK(amazon_category,walmart_product_type) + 0014 revision 触发器）；import_service 增 CATEGORY_MAP_DOMAIN（别名列宽容/布尔宽容 _parse_flag/'无对应Walmart PT' unmapped 条目照导/amazon_category 保原文精确匹配语义）；tools/import_category_map.py CLI；4 db 测试。
- test_unsupported_domain_rejected 反例 category_map→gtin（域已支持）。138 pytest+ruff/mypy 全绿。
- **四类审核弹药通道全通**（黑名单/商标 bulk/政策/类目映射），全部只待 Owner 数据。
- 源仓考古备忘：L1 还需 walmart_pt_meta（PT 可用性元数据，源=walmart_specs/all_product_types.json+飞书），随 L1 实现单建表——candidates 必须 INNER JOIN pt_meta 过滤废弃 PT（源仓 2026-05-09 修复教训）。
- Next: L1 类目判定主路径（direct 短路+category_map 召回+LLM 复排+coerce，fake LLM 测试沙盒可全建）。

### Session: 2026-07-12 (CI 核对 + Owner 数据策略答复)
- CI 837adaf(DG1) 三 job 全绿（静默记）。
- Owner 提出数据策略问题（不导入数据对开发影响多大 / 导入不符预期调整麻烦否）——已答：不卡开发卡验收；调整绝大多数🟢(重跑幂等/列名别名)🟡(加列迁移)，仅"结构性猜错"🔴，可靠先导小样本消除。
- Owner 决定动工全量数据迁移，与本地 AI 协作用本项流程/代码——已交付迁移沟通包：破除"连库"误解(工具吃文件不连库)+三跳模型(Mac 抽取→局域网搬文件→部署机灌库)+局域网三方案(共享夹/U盘/scp)+可粘贴本地 AI 指令(四通道 CLR+列契约+docker 卷+import_job 验证+幂等安全网)。

### Session: 2026-07-12 (pt_meta 通道收尾——L1 弹药补全)
- **0016 收尾并测通提交**：category_map 补 5 列(amazon_leaf/browse_node_id/rank_no/match_type/source_batch)+ 新建 refdata.pt_meta(walmart_category/ptg/access_state/zh_can_do/zh_seller_forbidden/requirements/notes/total_fields/required_count/required_fields，PK=walmart_product_type)+ dataset_revision('pt_meta') 触发器 + import_job domain+=pt_meta。
- import_service 增 PT_META_DOMAIN + `_apply_pt_meta_row`(别名列/布尔宽容/`_parse_int` 含浮点串宽容)；category_map handler 扩写 5 源列(飞书映射明细无损导入)；`_parse_int` 复用助手。
- tools/import_pt_meta.py CLI；tests/db/test_import_pt_meta.py(4)+test_import_category_map 补 test_source_columns_imported(1)。
- 迁移 0016 downgrade/upgrade 往返验证通过(downgrade 真删表，re-upgrade CREATE 不冲突)。144 pytest + ruff + mypy(56 files) 全绿。
- **五类审核/上架弹药通道全通**（黑名单/商标 bulk/政策/类目映射/pt_meta），全部只待 Owner 数据。Owner 现可一次导全类目数据(映射明细→category_map，沃尔玛类目→pt_meta)。
- 001 §03 旧 category_map 设计(leaf 唯一→单 WPT)被 D-Q55 取代，待随 L1 主路径实现修订正文。
- Next: L1 类目判定主路径（direct 短路+category_map 召回 INNER JOIN pt_meta 滤废弃 PT+LLM 复排+coerce，fake LLM 测试沙盒可全建）。

### Session: 2026-07-13 (🎯 全量真数据迁移完成——五通道上线)
- **Owner + 部署 AI 用本项流程/代码完成全量迁移**（T7→D:\erp-staging-backup→PG17 staging→投影→ERP-ALL 导入 CLI）。9 个 import_job 全 done、错误 0、列全对齐、healthz ok、alembic 0016。
- 迁移路径实录（供复盘）：旧库 dump 是 PG17.9 且 uspto 依赖 pgvector →采用**一次性 pgvector/pgvector:pg17 暂存容器 + `-t` 选择性单表还原**（只拉需要的关系表，跳过 20G+ 向量 embedding 表，绕开 vector 依赖 + 省 C: 盘）；`--no-owner -x` 挡旧角色错。暂存用毕精确删容器+7GB 匿名卷，pt_metadata/ 未碰。
- **落库真数据量**：商标 4,439,478 live wordmark（14.19M 总行筛 ~31%）/ 政策 37（=L3 静态 37）/ 类目映射 15,987 / pt_meta 7,008 / 黑名单 41,992（4 符号品牌精确排除+大小写重复 ON CONFLICT 自动折叠）。
- **列映射对账定案**：`walmart_category_map.rank_in_pt→rank_no`、`walmart_prohibited_policy.id→seq`；黑名单 brand-only（source≠reason 不硬套）。源空值（商标 8565 无 Nice/36 无 owner/63 无 filed；政策 4 无 prohibited；PT 66 无字段统计）均源目一致=无损，非别名问题。
- **验收姿态变化**：R2-02（百件真实对拍 ≥90%）等"只待真数据"的门现在可在部署机跑；L0/L2/L3 三级均已真数据背书。
- Next: **建 L1 类目判定主路径**（流水线最后缺口，弹药已齐）——direct 短路+category_map 召回 INNER JOIN pt_meta+LLM 复排+coerce；随实现修订 001 §03 正文（D-Q55）。

### Session: 2026-07-13 (L1-a 类目判定同步直判 gate)
- **L1 拆两增量**（RS-03a 纪律）：L1-a 同步直判核心（本单，落 tx1 零重构 twice-reviewed audit_one）；L1-b 无直判命中的事务外 LLM 复排（另单，RS-03a 同款第二段 HTTP stall，值得独立评审 pass）。
- **spec §03 修订**（dfddd93）：旧单 WPT+pt_embedding 设计→D-Q55 映射表多候选+LLM 复排；正文对齐已落地 refdata.category_map/pt_meta。
- **l1_category.py**：`run_l1`——product 的 category_path/amazon_leaf_id 精确命中 category_map（INNER JOIN pt_meta 滤废弃 PT，排除 '无对应Walmart PT'）→ 存在非禁做有效候选=pass（带 resolved wpt）/ 候选全禁做(map 或 pt 维度)=reject(reject_level=l1) / 无直判/无类目=needs_review(fail-closed)。sellability=任一可售通道即算可售。
- **service.py**：L0 后 L2 前插 l1 级（`verdict==pass and 'l1' in levels`）；reject→reject_level=l1，needs_review→留 NULL(review 非否决)。**暂不进 DEFAULT_LEVELS**（无直判会大量落 needs_review，待 L1-b；R2-02 对拍显式 levels=[l0,l1,l2,l3]）——故不破坏既有 audit 测试。
- test_l1_category.py 9 例（pass/pt禁/map禁/废弃PT被INNER JOIN滤/混合可售胜/unmapped/leaf键/无类目/audit_one 集成 reject_level=l1）。153 pytest + ruff + mypy(57) 全绿。

### Session: 2026-07-13 (L1-b 类目复排——module=category_map 写回，L1 主路径完备)
- **架构据文档修正**：原计划"扩 audit_one 为两段 HTTP stall"；查 001 §05（llm_usage_log.module CHECK IN 含 category_map + team_id NULL "类目映射批量"）+ §03（复排结果回写 map）→ 文档指向**类目级批量复排写回**，非 per-product 内联。改用此法：**不改 twice-reviewed audit_one 行锁编排**，更符合文档。
- **l1_rerank.py**（module=category_map）：`resolve_category` 三段（tx1 召回+查缓存→HTTP→tx2 记账+写回，无 product 行锁故无重锁/新者胜）——祖先前缀召回(starts_with 分隔符无关) INNER JOIN pt_meta 滤废弃→LLM 复排选唯一候选→写回 category_map(match_type=ai_rerank)。之后 L1-a 直判即覆盖该类目(0 LLM)。**fail-closed**：非 JSON/候选外 WPT/无候选→不写回(绝不写脏映射污染 gate)。
- **llm.py**：check_cache/record_result/chat 加 `module` 参数(默认 audit，向后兼容)→ usage 归因 category_map。
- **tools/resolve_categories.py** CLI：--backlog(扫 product 无直判类目) / --category 单个；R2-02 对拍前置=先填 map 再对拍。
- test_l1_rerank.py 8 例(coerce 合法/候选外None/坏JSON/cacheable/祖先召回/resolve写回+usage归因+写回后L1-a直判命中/候选外不写/无候选不调LLM)。160 pytest + ruff + mypy(59) 全绿。
- **L1 主路径完备**：L1-a 同步直判 gate（audit 内联）+ L1-b 类目复排写回（批量作业），category_map 是共享真相源。DEFAULT_LEVELS 仍不含 l1（填 map 后由部署决定开启；R2-02 对拍显式 levels）。
- Next: **L2 R1/R2/R3 硬规则**（商标硬拒），补齐后 L1+L2 齐 → R2-02 部署机对拍验收（Owner）。

### Session: 2026-07-14 (R2-02 对拍 harness——最后一步开发，达验收闸门)
- **L2 R1/R2/R3 改"实测"不"盲建"**：查 archaeology（R1 cat_access_blocked/R2 forbidden_mega_cat 17类/R3a cat_requires_cert FDA/UL…）——三条都是**依赖 L1 类目产物的类目硬拒**，且 L1-a 已在 zh_seller_forbidden 拦禁做。是否已被导入数据覆盖，取决于飞书类目"禁做"标注范围（Owner 知识/真数据），我看不到源码与真数据 → 盲建=空框架（评审否过）。**正解=对拍实测**。
- **tools/audit_replay.py**（R2-02 验收工具）：groundtruth jsonl → upsert product（对拍专用 team）→ audit_one(levels=[l0,l1,l2,l3]) → verdict 对拍 → 一致率 + 混淆矩阵(old→new) + 分歧清单(带 reject_level)。verdict 归一(approved→pass/blocked→reject)。--resolve-categories 先 L1-b 填图；--out 导分歧供判 L2 类目硬规则是否需补。
- test_audit_replay 2 例(归一 + 端到端 3 品：L0 拒一致/L1 直判过+L3 过一致/旧拒新过=分歧；calls=2 证 L0 短路)。踩坑：黑名单 brand_norm 须归一小写(L0 查表用 _norm)；llm_cache 需清以定 LLM 调用数。162 pytest + ruff + mypy(60) 全绿。
- **R2-02 达【待验收运行】**：开发建齐（L0/L1/L2软证据/L3 + 对拍工具）。余 Owner 在部署机跑：导 groundtruth → resolve_categories 填图 → audit_replay 出一致率。≥90% 即通过；<90% 看分歧清单定 L2 硬规则（带真数据）。这是"持续推进到需要验收"的终点。
- Next（Owner 验收后按分歧决定）：或收 R2-02，或据分歧清单补 L2 R1/R2/R3（真数据驱动）。

### Session: 2026-07-14 (对拍 round-1 42% 根因修复——L1 缺图放行)
- **对拍 round-1 结果（部署机 200 ASIN，100pass+100reject）：42%，未过闸**。混淆：pass->pass 42/pass->reject 13/pass->NR 45/reject->reject 42/reject->pass 17/reject->NR 41。
- **根因**：needs_review 86/200(43%)=主因。旧系统 L3 二值输出从不产 needs_review（archaeology:89）→ 每个 NR 自动计分歧。86 个 NR 几乎全来自 L1-a「无直判命中→needs_review(fail-closed)」设计：189 类目仅 99 复排成功、90 无召回候选（疑旧库 category_path 与飞书 map 分隔符/格式不匹配），落在缺图类目的商品卡死 L1 连 L2/L3 都没跑。
- **定性**：我的设计错误——类目缺图是**数据缺口**非合规检查异常；A4 fail-closed 适用于"检查本身失败"(LLM 输出非法)，不适用"可选补充信息缺失"。把"能否上架(缺WPT)"和"是否合规"混进了一个闸门。旧系统 unmapped 照跑 L3，类目硬拒仅 R1/R2/R3。
- **修复（三件）**：①l1_category：unmapped/无类目 → verdict=pass + 软命中(l1_unmapped/l1_no_category, is_hard=False)，L2/L3 照跑；唯一 L1 硬拒=候选全禁做。run_l1 返回增 is_hard。②直判键增 browse_node_id（旧库 amazon_leaf_id 多为数字 browse node ID，0016 列已导数据但直判没用上）。③audit_replay diff 增强：每分歧带 category/hits(命中链)/old_reason(groundtruth 可选列)；unmapped 计数(缺图规模指标)；分歧类目 Top5 聚类（reject->pass 聚类=L2 类目硬规则缺口的直接证据）。
- spec §03 L1 主路径正文同步修正。test_l1_category 改 3 增 2（browse_node 键/unmapped 集成放行）；test_audit_replay 增 D 品（缺图→放行→L3 过=与旧一致）。164 pytest+ruff+mypy 全绿。
- **预期**：round-2 中 86 个 NR 桶消失（那些商品真正跑完 L2/L3）；残余分歧（17 reject->pass 等）由增强 diff 的 old_reason+hits+类目聚类定位——是否需补 L2 R1/R2/R3 届时以真数据裁决。
- Next：Owner/部署 AI round-2 重跑（groundtruth 加 old_reason；顺带发格式样例验证类目 join 匹配率；部署库补 llm.pricing）。

### Session: 2026-07-14 (round-2 64% 分析 + 召回 v2/栏剥离/模型参数化)
- **round-2（deepseek-chat）64%**：round-1 修复验证成立——NR 桶 86→0，42%→64%。residual：reject->pass 45（最大桶）/pass->reject 27。**v4-flash 对照 61%**：更漏旧拒(54)+9 条 markdown 栏破 JSON 契约→ **模型不是杠杆**，回退 deepseek-chat。
- **格式假设被数据否定**：两边路径都是 ' > ' 分隔，90 类目无候选=旧 map 里没有这些完整路径原文（15,744 条 map 是叶子级全路径，目标类目往往无"恰为其前缀的祖先行"，但有大量同分支兄弟叶子）。
- **修复四件（169 测试全绿已推）**：①L1-b 召回 v2——首段 LIKE 拉回+Python 分段前缀匹配（分隔符宽容），最深共同层往浅找、≥2 段共同才算（同顶级过宽不召）；兄弟叶子召回预计解 85/90 无候选。②strip_json_fences 全线（L3 coerce/cacheable + 复排 coerce/cacheable）——v4-flash 9/200 带 ```json 栏。③复排模型参数化：system_config 'category_map.rerank' {model,temperature,max_tokens}，显式实参>配置>默认；CLI --model。④对拍增强：console 分桶旧因 Top10 聚类（规则缺口直接证据）+diff 行带 l1 map 旗标(wpt/access_state/requires_certificate)。
- **待部署机数据（裁决 L2 R1/R2/R3 建法）**：existing diff-round2.jsonl 的 reject->pass 旧因 jq 聚类 + pt_meta.access_state / category_map.requires_certificate 值分布。假设：R1≈access_state 谓词、R3≈requires_certificate 谓词——数据已在库，缺的只是硬拒规则谓词，等分布+旧因确认再上（避免误杀 pass->reject 恶化）。
- Next：拿到聚类/分布 → 定 R1/R3 谓词 → round-3 重跑（召回 v2 应消灭 unmapped 33）。

### Session: 2026-07-14 (Owner 质疑触发对照审查——证实移植缺口并补齐)
- **Owner："我原本就有完善的审核系统…可能不止数据问题，还有实现问题"→ 逐行比对源仓**（/workspace/walmart-audit-system 就在沙盒）。**证实**：不止数据。
- **发现的实现缺口（按影响排序）**：①L2 R1 类目准入 gate 未移植——`access_state∈{普通商品,附条件允许} AND (zh_can_do='是'|'需评估*')`，源仓注释实测**拒 1223/7008 PT(17.5%)**，是 reject->pass 45 的最大嫌疑；②R0 八大 walmart_category 硬禁未移植；③R3a requirements 硬认证关键词(17 词)未移植；④L3 prompt 只移植了 5 维中的 3 维（缺"冒犯性内容"与"儿童产品/CPC 兜底"——源仓 813 行 l3_llm.py，我们当时标注'精简版'）；⑤R2 seed yaml(555 行细粒度 PT 禁售词)未移植；⑥旧 L1=映射候选+qwen-plus LLM 必走双确认+excluded_category_reason 预拦截 vs 我们 0-LLM 直判；⑦旧 L3=qwen-turbo/plus 混合路由+政策路由提示 vs 我们 deepseek 单模型；⑧**旧 L4 视觉默认开**（doubao）——groundtruth 拒样本可能含 L4 拒，纯文本管道结构性对不上。
- **本单补齐 ①②③④**：l1_category 增 `candidate_block_reason`（R0→R1→R3a 顺序保真，rule_code 保留源仓名 cat_access_blocked/cat_zh_blocked/zh_seller_mega_cat_forbidden/cat_requires_cert_hard），sellable=候选过全部谓词；reject evidence 带全候选拒因明细。L3_SYSTEM_PROMPT 补维度 2(冒犯性)/5(儿童CPC)（源仓原文保真），VALID_CATEGORIES 静态集扩 offensive content/children's products/baby products。数据前提已满足：pt_meta.access_state/zh_can_do/requirements 迁移时已导入。
- 测试：pt_meta 种子补 access/zh 列；TestCategoryHardGates 5 例(access 拒/zh 拒/mega 拒/cert 拒/需评估*可售)。174 pytest+ruff+mypy 全绿。
- **未移植项与处置**：R2 yaml→以 round-3 残余分歧裁决是否补；⑥⑦是有意的成本/架构差异（残余影响以对拍量化）；⑧需部署机查 groundtruth 里 L4 拒占比（从验收分母剔除或接受为已知差异）。
- Next：round-3 重跑（模型回退 deepseek-chat）；结果+L4 占比回来后收敛 R2-02。

### Session: 2026-07-14 (D-Q58：标准模型定标 v4-flash + L4 剔除对拍分母)
- **Owner 决策（D-Q58 已录）**：①标准模型=deepseek-v4-flash（Owner 长期实测，好用成本低）——撤回我"回退 deepseek-chat"的建议；模型不是一致率杠杆（64 vs 61 差 3 点 << 结构缺口），以 Owner 实测定标。②L4 视觉不进现阶段流程与验收（无视觉模型可接）。
- 落地：service.py L3 fallback + l1_rerank 默认 → deepseek-v4-flash；**0017 迁移**条件更新 0008 种子（仅改仍为 deepseek-chat 的行，部署侧已手工切 v4-flash 的配置不覆盖；version+1 自然失效缓存），downgrade/upgrade 往返验证过。v4-flash 栏输出兜底=已有 strip_json_fences。
- **对拍 L4 剔除**：groundtruth 行支持 old_stage/stage 字段，含 'l4' → 从分母剔除、单独计 excluded_l4 并在 console 报告。test_audit_replay 增 E 品（L4 拒→剔除）。
- 测试 pricing 种子补 deepseek-v4-flash 单价。174 pytest+ruff+mypy 全绿。
- Next：部署机 round-3——git pull(0017 会自动对齐模型) + groundtruth 导出带 old_stage 列 + llm.pricing 补 v4-flash 真实单价 + 重跑。

### Session: 2026-07-14 (DeepSeek 真实单价 + 缓存命中计价建模——RS-08 记账项提前落地)
- **官方单价（api-docs.deepseek.com 实查）**：deepseek-v4-flash 输入命中 $0.0028/1M、未命中 $0.14/1M、输出 $0.28/1M（**命中价=未命中 1/50**）；v4-pro 0.003625/0.435/0.87。⚠️ **deepseek-chat/deepseek-reasoner 模型名 2026-07-24 弃用**——Owner 定标 v4-flash 前瞻正确。
- **计费引擎补缓存命中建模**：`_price` 支持 `input_cache_hit_per_1m`（cost=命中×hit + 未命中×miss + 输出×out）；`call_provider` 返回 4 元组（+cached_tokens，DeepSeek 原生 `prompt_cache_hit_tokens`，OpenAI 兼容 `prompt_tokens_details.cached_tokens` 兜底）；`log_usage/record_result/chat` 全链路带 `cached_input_tokens`；**0018 迁移** llm_usage_log 加列（分区父表传播，往返验证过）。spec 001 §05 表随改。
- **架构复查（Owner"我以前这方面做得比较好"→比对）**：静态 system prompt 前缀（L3 policy_v+37 政策块 / L1-b 复排 system）本就是源仓 2026-04-28 的省钱设计、移植时保留——命中面已最大化，缺的只是**计价与记账**（今补齐）。v4-flash 下静态政策块的输入成本近乎免费（1/50），37 政策全量拼 prompt 的成本顾虑基本消除。
- 测试：TestCacheHitPricing（1000 输入 800 命中+200 输出=0.000086 vs 不建模 0.000196，56%↓；usage 行记 cached_input_tokens=800）。175 pytest+ruff+mypy 全绿。RS-08 的 cached_input_tokens 记账项就此提前完成（预算闸/pricing_version 仍留 RS-08）。
- Next：部署机 round-3（llm.pricing 用真实单价 SQL 已给）。

### Session: 2026-07-14 (round-3 72% + seed yaml 移植——最后一块拒绝机器)
- **round-3（部署机，be67efe/0017/v4-flash）：72%（+8）**。R0/R1/R3a gate 见效：旧拒侧一致 55→73；reject->pass 45→25；unmapped 33→14（召回 v2 生效）。excluded_l4=0（本批无 L4 拒）。残余 56：pass->reject 26（现最大桶）/ reject->pass 25 / NR 5（v4-flash 输出仍有 5 条不可解析，栏剥离外的形态待看样本）。
- **seed yaml 移植（对照清单最后一块拒绝机器）**：源仓 `forbidden_categories_zh_seller.yaml`（13 excluded + 18 mega）机械转换为 `audit/data/forbidden_categories_zh_seller.json`（stdlib json 加载，不引 pyyaml）。`zh_forbidden.py` 保真移植两匹配器：`check_excluded`（小写子串，amazon_category/walmart_pt/title_keyword 三 scope）+ `match_mega`（词边界+可选复数 's'，"bra"中"Bras"不中"Brackets"；category_prefix 小写前缀；首条命中优先）。
- **接线**：run_l1 最前做 excluded 预拦截（路径/title——无类目商品也能拦，rule=l1_excluded_category）；candidate_block_reason 增 excluded-PT 子串 + mega 词边界两谓词（rule=forbidden_mega_cat）。谓词链顺序≈源仓（forbidden→excluded→R0→R1→R2 yaml→R3a）。
- 测试：tests/test_zh_forbidden.py 8 例（数据量/子串/词边界/复数/无误报/前缀）+ TestSeedExcluded 集成 2 例。185 pytest+ruff+mypy(61) 全绿。
- 已知边界：旧系统三处吃 yaml（L1 excluded/R0 兜底/R2），现全数覆盖；旧 L1 的 title 关键词反查候选与 LLM 双确认仍未移植（有意）。规则数据后续入 refdata 治理（RS-04D 方向）。
- Next：round-4 重跑；同时要 pass->reject 26 的 reject_level/hits 聚类 + NR 5 的原始输出样本（api 日志 grep audit.l3_bad_json）。

### Session: 2026-07-14 (round-4 72.5% 平台期——R5 Nice 过滤 + 空响应重试)
- **round-4（2b1143b/0018）：72.5%（+0.5）**。yaml 移植净效果+1：旧拒侧 73→76（+3），但 pass->reject 26→31（+5，yaml/gate 对部分旧 pass 商品更严——数据/规则时点差异浮现）。unmapped 14→12。NR 5→1。**平台期确认：剩余分歧结构=①pass->reject 31（主链 14 条 R4→R5→L3 判真品牌拒）②reject->pass 23③数据演进与 LLM 散差**。
- **聚类证据（部署机回传）**：round-3 pass->reject 26 = L3 22/L1 3/L0 1；最大链 14 条 `l1_category_mapped→l2_r4→l2_r5→llm_intellectual_property`。NR 5 heads：3 空响应 + 2 截断 JSON 前缀。
- **R5 Nice Class 过滤移植**（源仓 pt_nice_class.yaml 30 映射+default 6 类→JSON+nice_class.py 保真 classes_for；R5 SQL 加 `nice_classes && allowed`）：只查产品类目相关分类的 LIVE 商标，压通用词商标误报（源仓设计原文——GARDEN/CAR 多注册在 35 广告/41 娱乐）。**只在 L1 直判出 walmart_category 时激活**（service 传递；未跑 L1/未直判=不过滤，非 L1 流不受影响）。Nice 未知(NULL)商标行过滤态不命中（源仓 brand_nice_class join 同语义，全库仅 0.2%）。
- **空响应重试**：call_provider 空 content 重试一次（round-3 NR 3/5 为空响应抖动），仍空抛 LLM_EMPTY_RESPONSE 走 fail-closed。截断 JSON（2/5）处置=部署侧 max_tokens 1200→2000（config SQL，无需代码）。
- 测试：test_r5_nice_filter 3 例（过滤命中/无关类滤除+NULL 滤除/未过滤全中）+ TestEmptyResponseRetry。189 pytest+ruff+mypy(62) 全绿。
- **战略判断（给 Owner）**：平台期残余大头疑似**groundtruth 时效**（黑名单/商标数据在旧判定之后增长——旧系统今天重跑也会拒）与 LLM 散差，需部署机做"品牌入库时间 vs 旧判定时间"核验后再谈验收口径（重采样近期判定 or 漂移行重分类）。
- Next：round-5（含 Nice 过滤+重试+max_tokens 2000）+ 漂移核验 + reject->pass 旧因 Top10（尚未见文本）。

### Session: 2026-07-14 (round-5 77% + 旧因 Top10 破案——lark 黑名单缺数据 + stopwords 全量)
- **round-5（9490ec4/0018/max_tokens=2000）：77%（+4.5）**。Nice 过滤+重试+max_tokens 全见效：NR 0、bad_json 0、pass->reject 31→22。残余 46=pass->reject 22 + reject->pass 24。
- **漂移假设被数据否定**：R4 命中的 86 对品牌**全部早于旧判定入库**——旧系统判定时看得见这些词仍放行=旧 L3 判了通用词而我们判真品牌。pass->reject 残余定性=LLM 判定散差 + R5 候选面偏大（stopwords 子集）。
- **reject->pass 旧因 Top10 破案**（首次拿到文本）：8× `phase0_lark_blacklist_amazon_cat` + 1× `_asin`——**旧系统飞书维护的类目/ASIN 黑名单，迁移时只导了品牌(41,992)，类目/ASIN/卖家三张全漏**（纯数据缺口）；4× cat_requires_cert_hard（含"整机电器必须 NRTL"=R3b walmart_pt_spec 驱动，表未导+分类器未移植）；1× history_shortcut（复放历史拒，不可复现）。
- **修二件**：①L0 类目黑名单双键查找——移植源仓 `normalize_amazon_category`（去空格+分隔符统一'->'），查 `_norm(原文)` + `_norm(normalize(原文))` 两键，使导入的旧 lark 归一形态数据可命中；②stopwords 全量移植（源仓 707 词+长度/数字规则 vs 此前 ~30 词子集，R5 候选过滤不足的实级缺口，erp/audit/stopwords.py 逐字保真+文件级 lint 豁免）。
- 测试：lark 归一形态 L0 命中集成 1 例（_mk_product 扩 category_path）。190 pytest+ruff+mypy(63) 全绿。
- **round-6 前置（部署机数据）**：从旧 dump 单表还原 phase0_blacklist_amazon_cats/asins/sellers → csv → import_blacklist 三域导入。**round-6 后仍开的口**：R3b NRTL（需 walmart_pt_spec 表+nrtl_classifier 移植，视 round-6 残余定）；LLM 散差与旧系统模型代际（groundtruth 跨新旧模型时代）→ 验收口径需 Owner 裁（重采样近期/散差类重分类）。
- Next：部署机导 lark 三表 → round-6。

### Session: 2026-07-14 (round-6 79%——代码保真收尾，验收语义决策点)
- **round-6（9d42a47，lark 三表已导：类目 11,810/ASIN 18,772/卖家 1,308，零错误）：79%（+2）**。结构：reject->pass 24→11（lark 导入按预测兑现 -13）；**pass->reject 22→31（+9）**——lark 类目黑名单今天命中 47/200，旧系统当年只在 ~9 条上拒过 → **类目黑名单时效漂移**（旧判定发生在类目拉黑之前；品牌漂移核验为 0 但类目是增长最快的名单）。旧系统同一 L0 规则今天重跑也会拒这些——新系统按今日数据判得更对。
- **残余 42 分解（估）**：pass->reject 31 ≈ 类目时效漂移大头 + LLM 散差 + R5 残余；reject->pass 11 ≈ R3b NRTL（walmart_pt_spec 驱动，表未导+分类器未移植，最后一块可移植）~4 + history_shortcut 1（结构性不可复现）+ LLM 散差 ~6。
- **定性：代码保真基本收尾**。八轮循环共修复：L1 缺图放行/browse_node 键/召回 v2/栏剥离/R0/R1/R3a gate/L3 双维度/seed yaml 双匹配器/R5 Nice 过滤/stopwords 全量/lark 双键/空响应重试——对照清单可移植项仅剩 R3b。**90% 问题已从工程问题变成验收语义问题**（groundtruth 与今日数据/模型时代不对齐）。
- 待部署机：①round-6 的 31 条 pass->reject reject_level 分布（jq）②phase0_blacklist_amazon_cats 是否有时间戳→类目漂移可证性 ③walmart_pt_spec 的 \d + 3 行样例（R3b 移植前置）。
- **Owner 决策点（三选一）**：A 重采样近期判定 groundtruth（数据/模型时代对齐，90% 门槛保真）/ B 漂移行重分类（时间戳可证时从分母剔）/ C 以"分歧类别全部溯源+文档化"为验收（79% + 已证成因清单）。

### Session: 2026-07-15 (D-Q59 路径A → round-7 84.5% + R3b 收尾)
- **Owner 选 A（D-Q59 已录）**：groundtruth 重采样旧系统最后 28 天判定（剔 SHORTCUT/L4），90% 门槛原样。
- **round-7（时代对齐 gt，d7bacd3）：84.5%**。结构单边化：**旧拒侧 93/100 对齐**（reject->pass 仅 7：3 L2-stage≈R3b、1 L0、3 商标词 bear/keller/utopia 散差）；缺口=pass->reject 21（旧 pass 无因，需我方 hits 聚类定位）+ bad_json 3。
- **R3b 全链落地**（85ff698，最后一块可移植缺口）：0019 refdata.pt_spec（源仓 walmart_pt_spec 6,942 行列级保真）+ nrtl.py 整机/小件分类器（42+46 词）+ L1 gate LEFT JOIN（has_real_cert 且整机→cat_requires_cert_hard，小件降级放行）+ PT_SPEC_DOMAIN 导入通道 + import_pt_spec CLI。
- **L3 输出容错升级**：strip_json_fences 增最外层大括号截取（round-7 3/200 JSON 混杂解释文字）；截断仍 fail-closed。199 pytest+ruff+mypy(65) 全绿。
- Next：部署机 ①导 pt_spec jsonl ②发 21 条 pass->reject 的 reject_level+hits 聚类（决定最后一步：若 l3 聚集→考虑 L3 prompt 与源仓逐字对齐；若 l1 聚集→PT 选择差异属 L1-LLM 双确认缺席，验收判读）③round-8。

### Session: 2026-07-15 (round-8 86% + L3 prompt 逐字对齐——最后一张牌)
- **round-8（pt_spec 6,942 导入，e552500）：86%（+1.5）**。旧拒侧 94/100（R3b 兑现）；NR 0（JSON 容错兑现）。**round-7 聚类破案**：pass->reject 21 = **13 l3**（12 条同链 R4/R5 命中→L3 判真品牌）+ 4 l1 cert（我方 map 候选被 gate 而旧 L1-LLM 选了别的 PT——架构取舍）+ 4 l0（lark ASIN/类目窗口内小漂移）。同代模型同代数据仍 12 条品牌真伪分歧 → 矛头=prompt 文本差异（我们此前是"精简版+补维度"非逐字）。
- **L3 prompt 逐字对齐（本单）**：①system prompt 换源仓 base 原文——补上此前缺失的整段「# 政策匹配的两类（A 品类整体禁售/B 需文本佐证）+ 证据要求」（塑造判定松紧的关键段）、IP 维度完整示例清单（贴纸/T恤/毛绒…；可口可乐瓶形/Tiffany 蓝盒；政治人物/演员…）、输出规范 offensive_signals 原格式；②候选 reason_category 由 policy_block.reason_categories_block 渲染（'  - Cat' 每行，seq 序 37 类+brand_misuse+none）+ POLICY_BLOCK_HEADER 源仓原文；③政策块渲染格式源仓逐字（## {seq}. {en} ({zh}) / 状态|中国卖家 / 禁 / 高风险备注——备注行此前遗漏）；④user prompt 结构源仓对齐：产品信息段（ASIN/标题/品牌字段/原产国/Amazon 类目/沃尔玛 PT/Category）+ 五点/长描述(600 截断) + L2 命中段 + 待评估品牌词段；**R7/R8 不再进 prompt**（源仓 _summarize_l2_hits 只渲染 R4/R5——判定口径与旧一致，hits 仍留档）。service 拼接顺序同步、传 l1 wpt/category。
- 测试：test_l2_content prompt 测试改口径（R7/R8 不进 prompt）+ 无命中占位；test_l3_policy 块格式断言更新。200 pytest+ruff+mypy 全绿。
- **诚实天花板**：4 l0（窗口内小漂移）+ 4 l1（0-LLM 直判 vs LLM 双确认架构差）≈ 8 条基本不可代码收敛 → 上限 ≈ 96%；本单瞄准 13 条 l3，需再收 ≥8 条即过线（86%→90%）。
- Next：round-9 重跑。若仍差 1-2 条 → 残余全部可溯源（漂移/架构/散差），建议以「已证成因清单」补充验收判读交 Owner。

### Session: 2026-07-15 (round-9 88% + 三层 JSON 提取移植——收官前最后代码增量)
- **round-9（prompt 逐字对齐生效）：88%（+2）**。pass->reject 22→16、pass->pass 78→83。残余 24 全部可命名：~8 l3 散差 + 4 l1 架构差 + 4 l0 窗口内小漂移 + 6 reject->pass 散差 + 2 bad_json。
- **三层 JSON 提取移植**（源仓 llm_client._json_from_text 逐字）：①直接 loads ②```json 栏内正则 ③**平衡括号扫描**（逐个候选对象试到解析成功——比此前"首尾大括号截取"强：坏候选/尾随含括号杂文本不干扰）。`parse_json_object` 统一供 L3 coerce/cacheable + L1-b 复排四处；全部失败 → None → fail-closed（**不学源仓 unparseable→pass 的旧行为**——A4 裁定 NR 保持）。瞄准 2 条 bad_json。
- 201 pytest+ruff+mypy 全绿。
- **收官账（若 round-10 达 89-90%）**：4 l0 需部署机验 lark 表时间戳（可证=groundtruth 缺陷，同 SHORTCUT/L4 剔除原则）；4 l1=架构取舍留档；l3 散差=同代模型固有非确定性。90% 若差 1-2 条，以 D-Q59 同款证据链交 Owner 裁定。

### Session: 2026-07-15 (round-10 88.5%——收口点，4 l0 漂移证成，交 Owner 终裁)
- **round-10（a6891ca）：88.5%（177/200）**。bad_json 2→1（平衡括号扫描救回 1）；旧拒侧 94/100。十轮全史：42→64→72→72.5→77→79→84.5→86→88→88.5。
- **4 条 l0 漂移证成（双重证据）**：①synced_at 口径 4/4 晚于旧判定 12-22 天（部署机核验，注：synced_at=最近同步时间非首次入库，弱证据）；②**决定性逻辑证据**——旧系统 L0 lark 黑名单是确定性等值集合匹配且硬拒：旧系统当时判了 pass ⟺ 该条目当时不在黑名单；今天在 → 条目在判定后加入。旧系统自己的 pass 判定就是"当时不在名单"的证明，比任何时间戳都硬。
- **调整口径**：4 条与 SHORTCUT/L4 同性质（groundtruth 数据时点缺陷，非判定能力差异），按 D-Q59 已立原则剔除分母 → **177/196 = 90.3% ≥ 90%**。
- **残余 19 条最终分类**：~8 l3 品牌真伪散差 + 6 reject->pass 商标词散差（同代模型固有非确定性）+ 4 l1 cert（0-LLM 直判 vs LLM 双确认架构取舍，D-Q55）+ 1 bad_json。代码收敛已到底（十轮共 14 项修复/移植全部落地）。
- **交 Owner 终裁**：A 批准剔除口径 → R2-02 以 90.3% 过线收账（D-Q60 记录+验收报告归档）；B 坚持未调整口径 → 88.5% + 全残余可溯源清单，验收判读由 Owner 定夺。

### Session: 2026-07-15 (🏁 R2-02 收账 + 工作区迁移收口)
- **Owner："批准A" + "工作区将移动到写erp项目那里，本工作区做好收口"**。
- **R2-02 正式收账**：D-Q60 录入决策表；验收报告归档 `.agent/evidence/R2-02/acceptance-report.md`（十轮全史/终局混淆/4 条剔除双重证据/残余 19 条分类/纪律记录）；review_list R2-02 → **accepted**。
- **工作区收口**：`.agent/handoff.md` 重写为迁移交接版——新工作区直接绑 ERP-ALL；含当前状态/队列（下一单 R2-03 上架真实化）/部署机协作模式/源仓依赖提示（walmart-audit-system 与旧 erpAPI 仓是旧工作区独有资产，移植类工作需重挂）/决策链/Read first 顺序。
- 本工作区历史贡献归档：R2-02 全程（数据迁移方法论+十轮对拍+14 项修复移植）、D-Q55~60 六轮决策、迁移 0012~0019、审核全链路（L0/L1-a/L1-b/L2/L3）真数据化。
- **新工作区第一单：R2-03 上架真实化**（handoff 已给起点与注意事项）。

### Session: 2026-07-15 (🚀 新工作区接手 + R2-03 上架真实化六增量全部落地)
- **新工作区就位**（绑 ERP-ALL，旧 erpAPI 仓挂载于 /home/user/erpAPI）；开发分支 claude/r2-03-launch-leg5n8（PR #1 draft）——本工作区环境要求分支开发+PR，不直推 main；每增量四关绿（pytest/ruff check+**format**/mypy）后提交推送。
- **接手第一笔欠账**：远端 main CI 自 85ff698 (07-14) 起红——backend job 挂 `ruff format --check`（round-7~10 期间本地只跑了 ruff check）。纯格式化 10 文件修复（2ad1663）。教训入纪律：本地四关必须含 format --check，且 format 改动后要**重跑 check**（RUF100 又踩一次，fee98d7）。
- **考古**：五路并行（旧 auto_listing 管线 14,400 行 / 新骨架 / 图纸 / spec 素材盘点 / 可复用基建）→ `.agent/evidence/R2-03/archaeology.md`（812 行，file:line 全）。关键结论：①旧校验/coerce 12 函数群每行都是错误码换来的，保真移植不重写；②本地无 MPSetup monolith/pt_templates_full（T7 独有），且旧审核库 walmart_pt_spec.fields 是**压缩版**（enum 截 10/丢 length/pattern/allOf）直接用会假拒→定案 pt_spec 加无损 fields 列+提取工具；③图纸警报 9 条（pt_spec 无列级图纸/dry-run 无契约落点/GTIN 释放语义歧义等，部分移交 RS-11）。
- **增量1（9f31658）pt_spec fields 通道**：0020 迁移（fields jsonb + ck_llm_usage_module+='listing' + ck_import_job_domain+='listing_error_catalog'）；extract_mp_item_spec.py 无损提取（T7 monolith ijson 流式 / live spec 响应两输入；cert 集合逐字保真 sync_pt_specs 重导不改 R3b）；import fields 列 COALESCE（子集行重导不清全量）；'__orderable__' 伪行协议；specs 001 §03/§05、04、005 随改。extract→import→查回 E2E 实跑通过。
- **CI 踩坑修复（8bba194）**：CI 迁移演练在 pytest 之后跑同一库——0020 downgrade 恢复旧 CHECK 被测试遗留 module='listing' 行打爆。修：downgrade 先重映射 'listing'→'other' + 删新域 job 行；测试夹具补 usage 清理。带杂行降级+全程 base 往返实测过。
- **增量2（e91e4e8）spec 构建器真实化**：v5 header 只 3 字段+完整时间戳（原骨架 v4.8 式 "1.5" 废弃，BR-LST-005）；WPT 链 attrs.wpt > run_l1 直判（与审核同一可售语义）> 配置；Orderable 强制格式源仓 force_overrides 保真（identifiers 单对象 UPC/EAN 按位数、price 裸 number、inventory=[{quantity,fulfillmentCenterID=store.profile.partner_id}]、endDate ISO）；零认证覆盖记 cert_overrides；match=v4.2 五字段+identifier 预检 fail-closed；缓存=单 MPItem 模板，build_hash 含 pt_spec dataset_revision。
- **增量3（b11158a）AI 属性填写**：attr_fill.py——SYSTEM 零认证铁律+USER 五块 prompt 逐字保真（静态前缀 cache 友好）；三段纪律（RS-03a）tx1 缓存→HTTP→tx2 记账+写 attrs['walmart_fill'][wpt]；module='listing' 归因；fail-closed 坏输出不写回不入缓存；合并序 LLM打底<系统字段<零认证压轴；fill_listing_attrs CLI。
- **增量4（341726e）清洗链+本地校验器**：coerce.py 源仓 12 函数群逐函数保真（safe_default/force_amazon_copy/条件必填不动点/类型枚举修复/strip_unknown/stateRestrictions/minItems/copy_limits/round/sanitize_feed_numbers，链序=prepare_one_async）；validator.py 官方 schema 层 errors+实践规则层 warnings；submit errors→ERP_SPEC_INVALID failed（配额返还，省 10/hour）。
- **增量5（e079f2d）错误码灌入**：import 域 listing_error_catalog+CLI+实战种子 65 码（auto_listing 数字码 20+erp-core error_classifier 符号码 44+ERP_SPEC_INVALID）；新旧处置映射拍定不扩枚举（retire→fatal 注可申诉/reallocate_upc→rebuild_spec 重投自动领新号/resubmit_*→manual 待 R2-04 承接），映射入 notes 运营可改（D-Q11）。CLI 实跑 65/65。
- **增量6 验收① harness**：listing_dryrun.py（build_spec 产物+官方校验+feed 封套+PASS 判定=全 ok 且 distinct WPT≥5）；沙盒 5 WPT fixture 自测 PASS（evidence/dryrun-harness-selftest.json）；部署机真数据指令 evidence/deployment-instructions.md（路A live spec 拉取不等 T7 / 路B monolith 全量；错误码一条命令；dry-run --auto --fill 出验收报告）。
- 终态：249 pytest + ruff(check+format) + mypy 全绿；PR #1 全部增量已推送。
- **Next（需 Owner/部署机）**：①部署机按 deployment-instructions 跑任务 1-3 回传 dry-run 报告（验收①真数据版）②验收② A152 真调前必须先做 **RS-03b**（channel outbox+幂等，闸门在案）——云端下一单直接开工 RS-03b，不等验收①回传。

### Session: 2026-07-15 (R2-03 部署机真数据闭环：Decimal 修复 + 验收①判定口径修正)
- **PR #1 已按 Owner 指示合入 main**（rebase，新 head 820a2a7）——部署机拉不到分支代码的阻塞解除；工作分支按规程从 main 重建。
- **部署机路 B 实测抓出 extract 真 bug**：ijson 默认产 decimal.Decimal，json.dumps 写 jsonl 报 TypeError。修复=iter_monolith 两处 use_float=True + 写出层 _json_default 兜底（双保险）；与部署机同版 ijson 3.5.1 真流式实测（小数约束零损）+ 回归单测（bff03e6，PR #2）。路 A 在部署机不可用（无旧 erpAPI 仓）→ 指令改路 B 为主路径；header version 保持默认 20260304（与 T7 快照 20260330 不需严格一致，遇 74597363510508 再对齐）。
- **部署机真数据全链完成**：pt_spec.fields 6,952（6,951 PT + __orderable__，job 14 零错误）；错误码 65/65（job 15，字典 70 码）；dry-run --auto 12 --fill：9/12 过官方 spec 校验、覆盖 5 个不同 WPT、llm_unavailable=0。
- **3 条失败判读=源数据贫瘠非缺陷**（各只有 1 条卖点，文案链补不满 keyFeatures minItems 3；旧系统会照发吃渠道拒 55506974520167，新系统本地拦截省配额=校验器本职）。证据 evidence/R2-03/dryrun-real-data-run1.md。
- **harness 判定口径修正**：第 1 版 pass 要求全部产品过——严于 005 验收①原文（"≥5 个不同 WPT 的产品"）。改为原文口径（通过品覆盖 ≥5 WPT 即 PASS），failed 完整列报不隐藏；+回归测试（贫瘠品被拦不拖垮判定、errors 列报）。按原文口径**第 1 轮真数据已达标**（9 过/5 WPT），待部署机重跑出 PASS 报告归档、Owner 签字。
- 环境注：沙盒 PG 会随容器闲置停机（stale pid），跑 db 测试前 pg_ctlcluster start。

### Session: 2026-07-15 (RS-03b channel outbox+幂等：A152 闸门解除)
- **前置**：Owner 批准 R2-03 验收①（真数据 9/12 过官方 spec、5 WPT，按 005 原文口径达标；review_list 已记账）+ "开始下一单"。PR #2 已合 main（head 2be1c88），工作分支按规程重建。
- **考古**（evidence/RS-03b/archaeology.md）：A7 论断逐条对码全属实——submit/delist/poll/verify_back 均在请求事务内持 FOR UPDATE 行锁跨渠道 HTTP；最狠的崩溃窗口=渠道已收+请求事务未提交→feed 行整体回滚（DB 全失忆，连对账线索都没有）；Idempotency-Key 契约 required 但服务端零消费+前端零发送（C2 属实）。
- **增量1-3（提交1）**：0021 channel_command（UNIQUE(team,action,idem_key)+payload_hash/fence/lease/同店FIFO）+ api_idempotency；outbox.py 全套原语+三段式执行器；gateway 拆 prepare（事务内 DB 读）/request_prepared（纯网络零 session）；submit/delist 改 tx1 落 feed+命令 COMMIT→HTTP→tx2 fence 归位，poll/verify_back 行锁不跨 HTTP；verify_back 归位同步终局命令解车道。模式闸 tx1 预检回滚=API 行为不变。**既有 8 用例零断言改动通过=语义保真**（永不盲重试/headline 不可信/配额/GTIN/状态链）。
- **增量4（提交2）**：core/idempotency.run_idempotent（占位→执行→回填；同载荷重放/异载荷409/并发409/错误不缓存/残留占位超时失效）；allocate/submit/delist 三端点头转必填；前端 api.post 自动 crypto.randomUUID()；**契约内漂移顺手修**：002 YAML delist 漏 idempotencyKey 参数（README §6 明明覆盖 delist）→ 补参数+三端点 409 响应+schema.d.ts 再生。
- **增量5（提交3）**：test_channel_outbox.py 9 用例=验收单 6 项逐条对拍（acceptance.md 对拍表）：API 重放零再发包单feed行/409 两层/故障注入 POST 后崩→verify-back 采认且请求序列断言 ["POST","GET"] 零重复提交/fence 拒迟到回写/同店 FIFO+跨店独立/HTTP 当口 listing+feed+command 三行 NOWAIT 探针全可锁/payload 敏感键拒收+真实命令全文扫描零凭证。specs 落笔：001 §02 channel_command+api_idempotency 两节、§06 feed 提交拓扑注。
- 范围钉死：inbox 缓办（进站仅主动轮询，无重复消费面）至 R2-04 webhook；retire verify_pending 对账+api_idempotency 全表清扫+drain beat 化=R2-04 维护任务；ship/refund 幂等接入=R2-05。
- 终态：**262 pytest + ruff(check+format) + mypy + 迁移 base↔head 实跑 + 前端 lint/build（契约再生）全绿**。RS-03 工单整单 done（a+b 双闸门齐）。
- **Next**：①A152 验收②窗口可排（部署机指令任务 4 已更新：必带 Idempotency-Key 头/断连只走 verify-back/drain 工具）②R2-04 worker/beat 底座（outbox drain 周期化+retire 对账维护任务自然并入）。

### Session: 2026-07-15 (A152 验收②护航：真机暴露的两笔缺陷修复)
- **socksio 缺依赖（PR #4，已合 92fc7e9）**：网关经店铺 SOCKS5 代理发包需 httpx[socks]，镜像缺 socksio 在传输构造点即抛错。修=正式依赖 + socks5 URL 构造级回归测试。沙盒 MockTransport 从不建真实传输→只有真机能暴露。
- **采集 worker 超时写死 15s（真机全量超时）**：config.REQUEST_TIMEOUT 源仓直连口径，TPS 代理链路整页常态更慢；且 _apply_settings 文档串声称支持"超时"下发但代码未接（配置中心铁律漏网）。修=env 兜底（REQUEST_TIMEOUT/PROXY_BANDWIDTH_MBPS）+ scrape.worker_settings 运行期下发 request_timeout/proxy_bandwidth_mbps；超时变更丢弃旧超时热备、清节流戳强制冷轮换生效；R2-01 runbook 补链路调参节（支持键全列）。workers 28 用例四关全绿。
- **Owner 反证推翻超时判因**（同代理同机、R2-01 时跑得通）→ 复查实锤真根因：compose `--proxy "${PROXY_URL:-}"` shell 插值，容器不带前缀重建即空代理；proxy.py 空代理仅 warning 后**静默直连** Amazon → 全量 15s 超时装成"采集坏了"。修=engine 启动 `_enforce_proxy_policy` fail-closed（缺代理拒启，--allow-direct 显式放行）+ settings 下发 proxy_url 补域名预解析（c-ares 坑，下发路径原漏接）+ runbook 补"代理必带/写配置中心防丢"节。workers 31 用例四关全绿。超时可配置化保留（配置中心铁律本就欠账）。

### Session: 2026-07-15 (验收② A152 真调通过 · R2-03 整单收账 · D-Q61)
- **A152 真调全链首跑成功**（部署机+Owner）：HEAD 92fc7e9 部署 → socksio ok → partnerprofile 经网关+SOCKS 代理 HTTP 200（partner_id=10003098102 回填）→ live_test 档真实提交 1 SKU → **Walmart 后台可见 feed** → 轮询成功 → item 级 error 回写（测试 UPC 随机编造被渠道驳回=预期）→ listing failed+错误码入字典+配额返还+GTIN 归还。**RS-03b outbox 三段式在真渠道首跑即工作**。
- **D-Q61**：验收②口径调整并通过——live→截图→delist 分支因无真实购入 UPC 不可达成（Owner 确认），渠道写路径全链真调（提交/确收/轮询/权威回写/错误处置闭环）即达标；live/delist 真调并入首次真实运营发布。**R2-03 整单 accepted**。
- 采集器插曲收束：TPS 停滞间歇自愈（"刚才又可以了"），Owner 指示稳定性项搁置——中流停滞防御（低速中止+停滞连击轮换）已完码存档 PR #5（draft，含代理 fail-closed+超时可配置化，共 3 commit 全绿），待后续窗口再验再合。
- Next：R2-04 worker/beat 底座（outbox drain 周期化/feed 自动轮询/retire 对账维护任务/api_idempotency 清扫自然并入）；真 UPC 到位后灌 GTIN 池即可上真品。

### Session: 2026-07-15 (R2-04 worker/beat 底座：4 增量完码全绿)
- **考古**（evidence/R2-04/archaeology.md，四路并行）：底座存储层早已就位（0004 schedule/task_run 表+partition_maintain 种子、run_tracked 记账、compose redis 服务+redis 依赖），缺的只是执行体。设计拍板 8 条：cronsim（croniter 上游已归档）、单语句乐观领取、任务注册表显式化、pubsub fail-open、erp.worker 队列消费者因无生产者暂不启用（compose 保留占位）、RS-08 事前预算预留不并入。
- **增量1（2a33190）**：erp.beat 调度循环（NULL 初始化防重启风暴/坏 cron 1h 兜底并记失败/run_tracked 记账）+ 0022（种子3条+ensure_month_partitions SECURITY DEFINER 提权，search_path 首位坑：pg_catalog 在前会成为 CREATE TABLE 落点）+ 低风险任务四件。验收②锚点测试化：死心跳节点+硬超时任务，仅 beat tick 即回收。发现并修测试跨模块污染：回收用例遗留 pending 采集任务会被 scrape 套件节点领走（pull 队列全局）——收尾终局化。
- **增量2（78453e7）**：run_tracked 契约演进为 fn 自管事务（渠道任务三段式不挂外层连接）；渠道任务四件全复用既有函数（poll_feed/verify_back/drain/resolve_verify）零新渠道调用面。retire_recon 收 RS-03b 尾账：商品实况权威（404/RETIRED→delisted；在架超 grace→failed 归位配额返还回 live；未过 grace 维持背压）。验收①锚点测试化：提交后无人工点查 beat tick 自动轮询回写至 live（假渠道按 method+path 路由防批扫顺序依赖）。
- **增量3（a02e90c）**：gtin_watermark（team_config 阈值覆盖 15/5 默认，dedupe 24h）+ llm_budget_check（北京时区日聚合 vs llm_budget_daily_usd，超限 critical 含降级建议，不自动停）。
- **增量4**：ConfigService 接 Redis pubsub（写后 PUBLISH erp:config:invalidate，api/beat lifespan 各起订阅循环，fail-open=TTL 兜底）；compose beat 启用+make up 并入；CI 加 redis 服务；round-trip 真 redis 测试+fail-open 测试。specs 落笔（02/03/06/09 四处）；runbook（evidence/R2-04/runbook.md，含部署机整段指令+任务节奏速查）。
- 终态：289 pytest + ruff + mypy + 迁移 base↔head 演练全绿。**工单余项=部署机启 beat 后 A152 实测两条验收**（无人工点查自动轮询回写；模拟断连自动回收）——runbook 步骤 4/5。
- 注：R2-04 全部增量压在 PR #5 分支（单分支纪律），PR 标题/描述已更新反映实际内容。

### Session: 2026-07-15 (真机缺陷修复：真实 UPC 入池后 allocate 500)
- **背景**：PR #5 已合（main 5ccbe37，R2-04 完码待实测），Owner 导入 30 枚真实 UPC（upc_a 池）试真品分配 → API 500。部署机定位两问题叠加，GitHub 端修复。
- **缺陷①**：gtin.hold_one 默认 kind='ean_13' 写死——upc_a 池 30 free 永远取不到 → GTIN_POOL_EMPTY。修=占号优先序进配置中心 `gtin.kind_preference`（team>system>默认 [upc_a, ean_13]，真实购入 UPC 优先），按序逐池单语句尝试；allocate/retry_failed 两调用点自动受益；spec.py 本就按长度判 UPC/EAN 无需改。
- **缺陷②**：allocate 池空补偿走 `DELETE FROM app.listing`，而 erp_app 无 DELETE 权限（0009 最小授权，listing 本就不许物理删）——权限错误把池空业务提示覆盖成 500。修=INSERT+占号包进 SAVEPOINT（begin_nested），失败回滚本品插入不影响批内其它产品，**不扩权限、不加迁移**。
- 回归测试 test_gtin_allocation.py ×3（仅 upc_a 有号可占到/池空干净 rejected 零残留行/配置覆盖优先序），291 pytest + ruff + mypy 绿（redis round-trip 沙箱跳过，CI 有服务）。03-catalog 分配协议补占号优先序一句。

### Session: 2026-07-16 (R2-05 订单履约最小闭环：考古 + 增量1-4 完码 + 5a 文档)
- **考古**（evidence/R2-05/archaeology.md，四路并行）：订单域全零起建，基座全齐。口径裁定 5 条：四检以 001+002 冻结契约为准（phishing/purchaser/price_limit/consistency——005 一句话「黑名单/重复」无 BR 依据）；refund/returns 随售后单（契约未冻结）；portal③=R2#6；ack=内部自动步骤（FIFO 保序）；BR-ORD-007 候选匹配降档（purchaser 表无区间/配送方式列，扩列待决）。
- **增量1（4527c71）**：0025 订单域 6 表+automation_policy+blacklist_address/zip+sync_state+ck_cc_action 扩展；分区预建 [-7..+3]+DEFAULT 兜底（order_date 外部数据）。order_pull beat（15min）：lastModified 增量（重叠1h/成功才推 sync_state）+createdStartDate=179d 恒传+nextCursor 完整串翻页+upsert 同步列/内部列分离+行状态[-1]+Cancelled 强制覆盖+通知。
- **增量2（83ce01b）**：四检引擎（钓鱼双向 substring+前5位邮编+<8跳过；BR-ORD-006 flagged 粘滞人工 resolve 才清；限价 0.85×6.8÷汇率进 order.checks 配置；一致性 ratio<0.9 品名缓存 detail）；拉单即检 pulled→checked；order_flag 通知；GET /orders 列表/详情+rerun/resolve 契约端点；import_blacklist 扩 address/zip 域（_Domain.normalizer）。
- **增量3（86b57a6）**：采购执行单双入口（建单/分配/领单锁汇率/回填 op_direct/异常+mine=我的单）+purchaser CRUD（internal 1:1 绑成员；portal 字段 422 拒）；order_block 档位闸（semi/auto+flagged 未放行→409 冻结）。
- **增量4（6aae9d7）**：POST /orders/{id}/ship（Idempotency-Key+run_idempotent——RS-03b「ship 幂等」尾账收账）；outbox 扩 order_ack/order_ship（Created 单自动先 ack）；applier 200→落账/明确拒→failed+notify+换键重推/未知→verify_pending；ship_recon beat 渠道实况对账（0026）。drain 注册表合并 listing+order。
- **增量5a**：L1 对账 harness（erp.tools.order_pull_verify，与拉单共用 map_order 口径）；specs 落笔（07 三处/02 actions/09 种子清单）；runbook（部署机 L1 指令+Owner L2 步骤+调参表）。
- 终态：313 pytest + 迁移 base↔head 演练 + ruff + mypy 全绿。余：增量5b 前端订单页 → 更新 PR #7 → **停在人工验收节点（L1 部署机对账 / L2 A152 测试单）**。
- **增量5b（fe agent 交付，主线复验 lint/build 绿）**：OrdersPage（过滤/列表/详情抽屉/四检卡片放行重跑/采购执行操作/发货 Modal）+ PurchasersPage（建档/编辑，门户字段不提供）+ 路由与权限菜单。R2-05 全部增量完码，停在人工验收节点（L1/L2）。

## HF-0716 生产三缺陷整改（2026-07-16 插单，完码待部署验证）
- Owner 报告：无订单页 / 采集器卡住未真抓无审核输入 / 真实 UPC 上架 Invalid Date（feed #36）。
- 三路考古定因：①部署滞后（R2-05 未部署）+部署指令缺前端项；②beat 单进程串行全链无超时
  （一个挂起渠道调用顶死全部周期任务）+采集管线无告警无收口（无 worker 静默等、乒乓任务永不终态）；
  ③管线日期零感知（format 摘要即丢、LLM 坏日期直达渠道、必填兜底塞 'Not Available'）
  +endDate 2049 无毫秒组合未实测（旧仓 2049 唯一成功写法 .000Z）。
- 修复九件：beat 任务级超时+启动回收 / scrape 乒乓判死+收口兜底+无worker/零进展双告警
  / UI 采集节点横幅（fe agent）/ FieldSpec 透出 format+prompt 日期指令 / coerce fix_date_formats
  / validator 日期真解析+startDate+占位符泄漏报错 / endDate .000Z+远期值入配置（D-Q9 值不变）。
- 证据：evidence/hotfix-20260716/analysis-and-runbook.md（含部署机取证 SQL：feed_item.error_msg
  拿 Invalid Date 具体字段、采集/beat 卡点判读、升级部署含前端步骤）。
- 余项：部署机取证回报 → 确认 Invalid Date 字段归因 → listing #46 重提交；采集卡点按取证收尾。
- **HF-0716 归因闭环（部署机取证回报）**：①渠道拒收实为 `EXT_DATA_ERROR_66685355746773`
  field=CAP「Invalid Data」= **0 价出门**（price_snapshot 无 list 价→current_price NULL→
  构建器兜底 0.0；「Invalid Date」系误读）——补修：validator 拦 0/缺失价、错误码入字典、
  PATCH /listings/{id} 改价端点 + UI 改价入口/无价红标；②采集卡点=scraper 容器已停
  （job#9 建单前 4 分钟），beat 健康——双告警+横幅正对症，恢复=起 scraper；③前端=vite
  内存缓存，需 force-recreate。日期加固与超时护栏保留（真实缺口，预防性根治）。
- **HF-0716 部署核验通过（2026-07-16，HEAD 17f1a6d）**：全项绿——订单页可见、节点横幅生效、
  作业#9 done 8/8、beat 正常、停摆双告警按预期触发（scraper 恢复前的存量状态）；listing#46
  改价 39.99 重投 feed#37，自动轮询未复现 CAP 拒收，终态等渠道（beat 闭环）。
  部署惯例：scraper 代理经进程环境注入（本机密码文件），不入库明文——PROXY_REQUIRED
  fail-closed 属预期。工单 HF-0716 → deployed-verified；余=feed#37 终态 + R2-05 L1/L2 验收。
- **R2-05 L1 验收通过（2026-07-16 部署机）**：A152 store_id=1，渠道 2/DB 2 对账一致✅退出码 0
  （本轮拉取 orders=0 属正常——窗口内无新单）。L2 前四环真机走通（看单/四检重跑/采购执行
  分配领单回填）；发货留待真实新单（历史单不得推 ship，Owner 判断正确）。
  待核对：限价命中证据是否错位显示 no_active_purchaser（代码上该理由仅采购方检可写；
  限价证据=over/source_missing。只读 SQL 已交，若 DB 行错位则立缺陷单）。
- **四检真机核对闭环（order_id=1）**：price_limit 证据确为 source_missing=["1"]（历史单行
  无产品回连，预期 fail-closed），无错位缺陷；purchaser pass（采购方#1 汇率 6.85）；
  phishing/consistency pass。Owner 已放行限价命中。R2-05 仅余 L2 发货闭环（等真实新单）。
  注：purchaser 表的 kind 列实名 purchaser_kind（诊断 SQL 模板留意）。

## R2-06 定价引擎最小闭环（2026-07-16 立单）
- **里程碑**：首件真实 UPC 商品全链上架 live（listing #46 M0002418 / feed #37 / WPID
  4MBVJZD6I1FT，beat 自动轮询收终态）——R2-04 验收①完整闭环、HF-0716 CAP 修复真机实证、
  R2-03 live 分支真数据首验。HF-0716 → accepted；R2-05 → accepted（L2 发货 Owner 挂账后补）。
- 真机连带发现：#38 撞 EXT_DATA_ERROR_54514906640101（UPC 首位前缀拒收 BR-UPC-002——
  测试假 EAN 2000000000xxx 以 2 开头全部不可真实上架）；无成功通知属 R1-12 口径但值得补
  ——两件+PATCH 契约冻结入 SM-0716 小账随本单收。
- Owner 授权：「收掉 feed#37 后走 R2-06 定价引擎」。考古四路并行启动。
- **R2-06 考古完成（四路并行汇总 → evidence/R2-06/archaeology.md）**：范围=cost_plus+min_price
  （005:56）；九条口径裁定（成本价 current 优先/feed 格式收敛 canonical/限额修正 6/day→10/hour
  共享池/路由 ≤5 PUT 否则聚合/两段式回填/CAP 计划与拒收无关/区间属 params 数据/min_price 绝对值
  底线/30% 阈值参数化）；保真移植八件；必修缺陷=rate_limiter 价格桶键前缀不齐（限额被架空）；
  增量拆分 4+验收；3 项拟 D-Q 待 Owner（限额路由更新 ledger/默认区间取实表值/min_price 必填）。
- **R2-06 增量3（价格同步管道）完成**：PUT /v3/price 单品通道 + PRICE_AND_PROMOTION 聚合
  feed 双通道路由（D-Q62：单店 ≤5 条 PUT、更多聚合 feed，阈值配置中心可覆盖）；outbox
  price_push 三段式 + 幂等键带轮次（episode，retire 房例）；pending_price 两段式在途标记
  （0029，兼作改价并发闸；updating 语义=pending_price 非空，不入状态机枚举）；price_recon
  对账收敛（非 200 亦入 grace 判败通道）+ 0028 种子；限流闸拒绝归还 pending（零字节出门
  非未知结果）+ 429 同类处置 + drain 按店轮转防跨店饿死；rate_limiter 价格桶键修正
  （10/hour 官方现行）；dry-run 证据（PUT 快照无促销字段）。经工作流三镜头评审+对抗核实：
  6 项确认发现（1 critical）全部修复。min_price 可选落码（D-Q62 补充）。402 pytest 全绿。
- **R2-06 增量4 完成（全单完码）**：前端定价页（策略 CRUD/试算/批量重定价/改价 force 确认，
  区间模板 D-Q62 定值预填、min_price 留空即不设防）+ SM-0716 三件（上架成功 info 通知/
  GTIN 首位白名单 gtin.safe_prefixes/PATCH listings 契约冻结）+ erpAPI 速查修正（erpAPI PR#2）。
  合并树复验：403 pytest + ruff + mypy + 前端 lint/build 全绿。验收 runbook 就绪
  （evidence/R2-06/runbook.md）——**停在人工验收节点**：①新 listing 自动带策略价
  ②A152 真机改价 listing#46（渠道价变 + 两段式回填闭环）。
- **R2-06 验收缺陷修复（Owner 真机反馈）**：①履约判定读错键——接线读不存在的
  attrs.fulfillment 致全部静默落 FBM；修为旧仓保真 attrs.is_fba（Yes/No/N/A，
  采集器 parser.py 实际写入键），判不出→拒绝 PRICING_FULFILLMENT_UNKNOWN
  （fail-closed，params.default_fulfillment 显式兜底）；②initial 价史不可见——
  GET /listings/{id} 未按契约返回 price_history；补端点字段 + 前端「历史」抽屉
  价格历史小节（公式明细悬浮）。404 pytest + 前端 lint/build 全绿。
- **R2-06 验收缺陷二次修复**：is_fba 真实落点在 price_snapshot（worker payload 适配器
  _PRICE_FIELDS 归类），首修只读 attrs 仍全判不出——改为 price_snapshot 优先、attrs 兜底；
  wiring 用例 FBA 夹具改真实 worker 形态钉死。404 pytest 全绿。
- **R2-06 整单收官（Owner 2026-07-16）**：验收①真机通过（FBA 41/FBM 5 正确分流+价史公式；
  无标记 401 个拒绝出价属预期）；验收②真机改价挂账择时。定价引擎全链入 main（1a0c90f）。
- **插单 FE-0716**：产品库翻页致侧栏新菜单项消失（Owner 报告）——fe agent 本地 E2E 复现排查中。
- **FE-0716 诊断闭环**：翻页菜单消失=浏览器跑旧 AppLayout 模块（vite dev HMR stale，
  与 HF-0716①同病），非代码缺陷——四轮对抗矩阵全阴性+HMR 热替换正向复现全部症状。
  顺手加固：refresh 单飞 + 跨标签身份对齐/reload 竞态守卫（E2E 实测）。
  根治提单 INFRA-0716（生产改伺服 build 产物）；当下缓解=部署必 --force-recreate frontend。
- **INFRA-0716 完码（生产前端改伺服构建产物）**：frontend/Dockerfile 多阶段
  （node:22-alpine corepack pnpm build → nginx:1.27-alpine）+ nginx.conf（SPA try_files
  回退；/api、/portal/v1 反代 http://api:8000；hashed 资产 immutable 永久缓存；
  index.html no-store——换版本=换资产指纹，浏览器不可能再跑旧模块）。compose：frontend=
  生产静态默认随 up 启动（5173:80 端口不变，restart unless-stopped），旧 vite 挪名
  frontend-dev 留 profile dev（与生产端口互斥）。活文档同步：Makefile（up 含 frontend；
  up-full 废除改 fe-dev——旧写法 --profile dev up 会双前端抢 5173）、windows.md
  （步骤2 改生产启动 + 排障记录补 stale 模块条目）。验证：pnpm build ✓、compose 解析 ✓、
  本机 nginx 1.24 同配置真实 E2E 冒烟四项全过（nginx -t / SPA 回退且 index no-store /
  资产 immutable / API 反代 422==直连 422）；镜像构建留部署机（沙盒无 docker daemon）。
- **INFRA-0716 部署验证跟进**：部署机切换成功（镜像 erp-all-frontend/index no-store ✓），
  核验 3c 首测 404——定性=runbook 命令路径缺陷非部署缺陷（后端健康端点在根 /healthz，
  FastAPI 404 报文经 5173 返回恰证反代链路通）。补 nginx 精确映射
  location = /api/healthz → proxy_pass /healthz（前门全链健康检查可用，不动后端 API 面）。
  本地 nginx+路径回显桩实证四项：/api/healthz→/healthz ✓、/api/v1 原样直通 ✓、
  /portal/v1 原样直通 ✓、index no-store 回归 ✓。
- **INFRA-0716 收账（Owner 2026-07-16）**：部署机切换实测通过（镜像 erp-all-frontend、
  index no-store、/api/healthz 经反代 200）+ 浏览器验收通过（侧栏齐全/翻页不掉菜单）。
  跟进单 PR#15（/api/healthz 精确映射）同日合入（a521bd7）。stale 模块类事故根治闭环。
- **R2-06 验收②缺陷③（Owner 真机启动验收发现）**：live listing 无「改价」按钮——
  后端 PATCH /listings/{id} 早已按状态分流（draft/failed 直改、live/published 转
  push_price 管道三段式），前端按钮门控 REPRICEABLE=['draft','failed'] 是 HF-0716
  直改时代旧值、R2-06 增量3 后端扩容时漏放宽。修复：门控放宽至 live/published +
  按返回分流提示（live 非 succeeded 提示"已提交渠道确认后回填"而非谎报改价成功）。
  前端 lint/build 绿。
- **R2-06 验收②真机闭环（Owner 2026-07-16「可以收账」）**：PR#16（live 补改价按钮）
  部署后，listing #48 真机改价 27.16→43.98 全链走通——channel_command #9 price_push
  succeeded（attempts=1，2 秒完成）、渠道官方响应「Thank you. Your price has been
  updated…」(200)、manual 价史落账、Owner Seller Center 实见价变。取证澄清：PUT 单品
  通道不产生 feed 属预期（D-Q62 路由：单店 ≤5 条走 PUT /v3/price，Feed 面板只显示
  批量聚合 PRICE_AND_PROMOTION）。**R2-06 整单关账**（验收①②双过）。挂账清单更新：
  余 R2-05 L2 发货 / R2-04 验收②断连 / 钓鱼黑名单导入 / erpAPI PR#2 / schema codegen。

## 2026-07-16 R2-07 售后域立项 + 考古
- Owner 点名下一单：售后。五路考古完成（宪法决策/冻结契约/旧仓生产语义/渠道 API/基建模式），
  落 .agent/evidence/R2-07/archaeology.md。（workflow 并行考古两轮全体 529 过载，改内联完成。）
- 关键结论：BR-AS-001~008 全保真移植；契约张力（冻结 channel_return 为 RMA 级 vs C6 已决行级
  upsert）处置 = 头表不动 + 新增 channel_return_line 行表；三档映射 manual→record/semi→approval/
  auto→auto，缺省 record fail-closed；退款执行走 outbox return_refund + verify-back，灰度仅 is_test 店。
- 工单注册 review_list（P0 feature，in_progress），增量拆四，先推增量1（读闭环）。

## 2026-07-16 R2-07 增量1 完码（读闭环）
- 0030 迁移：channel_return（头，07 冻结列+扩展列）/ channel_return_line（行，uq (return_id,line_no)）
  / channel_return_event（行级状态变更留痕）三表 + aftersale.read 权限点补种（挂 订单员/财务/团队管理员，
  含既有团队同名角色）+ return_pull 调度种子（0 8 * * *）。
- erp.aftersale.pull：全量拉取（BR-AS-001 无时间过滤；nextCursor 完整 URL parse_qs 回参——与订单域
  整串拼路径协议不同，旧仓实战语义）；行级 upsert 同步列刷新 internal_status 不触碰；变更 diff 写
  event（C6/BR-AS-006）；BR-AS-007 见单量骤降 >50% warn 告警不重跑；店间失败隔离；50/min 由网关
  GCRA 管（限流表补 GET /v3/returns）。
- GET /returns 列表（store/状态/时间/RMA 精确查）+ 详情（lines+events）；openapi-v0 补 Aftersale 契约；
  07 文档注记行级落地。
- 本地 CI：pytest 411 passed（新增 8 项：分页协议/upsert 幂等/event 留痕/页失败水位/骤降告警/
  map 聚合/API 越权隔离）+ ruff check/format + mypy strict 全绿；permission 基线 44→45。

## 2026-07-16 007 计划落 main 与 R2-07 撞号调和
- 审计工作区推 007 MVP 补全计划（R2-07~11 + FE-DESIGN 补单；进度口径改 PRD §8 九模块）。
  main 前进致 PR #18 冲突（review_list 双方各注册了 R2-07）。
- 调和：review_list 以 main（46 单）为基；R2-07 采 007 三片定义（07a returns 只读/07b 封店/
  07c 邮箱），我方增量1 = 07a 核心，进展与考古并入其 finding；退款三档执行划归 R2-09。
- 开发侧批注（记 finding 不改 007 正文）：R-ERP-006 实证的是 erp-core 缺 returns，erpAPI 根
  另有独立生产脚本（台账 §13 出处），已用作实现语义与对拍口径；结论（代码新建）不变。

## 2026-07-17 R2-07 增量2 完码（07a 收尾：refund_request 两档）
- PR #18 合并（增量1 读闭环入 main）。0031 迁移：refund_request 图纸原样落库 + 权限点
  refund.request（订单员/团队管理员）/refund.approve（团队管理员）+ sys_dict refund_reason 七码。
- record/approval 两档本地闭环：POST /refund-requests（Idempotency-Key 幂等 + 档位随建快照
  manual→record/semi→approval，缺省 manual fail-closed）+ approve/reject + 列表；审计三动作。
  auto 档 R2-09 flow=refund 接线前拒绝创建（REFUND_AUTO_NOT_WIRED），不做静默降档；
  approved 为驻留态，渠道执行（outbox return_refund + verify-back）归 R2-09。
- 契约：openapi-v0 /refund-requests 四端点 + schema；07 文档"已落地（增量2）"注记。
- 本地 CI：pytest 418 passed（新测 7 项：三档快照/审批流/重复裁决/字典闸/幂等重放/越权隔离）
  + ruff + mypy strict；permission 基线 45→47。

## 2026-07-17 审计侧三情报入账 + return_pull_verify 对账 harness
- 情报：①动工顺序 Owner 批 R2-11→R2-07→R2-09→R2-08→R2-10；②§08 财务图纸 immutable event
  ledger 重写完成（421f83d），R2-08 闸门解除；③进度口径统一 PRD §8 九模块。开发侧批注被
  核实采纳（11443af，fetch_walmart_returns.py 确认为旧语义源）——批注流转通道首跑成功。
  核实：两笔提交在 PR #18 合并前已进 main，当前分支与 PR #19 均已包含，无需补拉。
- 补 erp.tools.return_pull_verify（照 order_pull_verify 模板）：渠道为准全量重拉（只读）
  对比 DB 头/行（RMA 集合/聚合状态/金额/行数/行级退款状态），--pull-first 免等 08:00 beat。
  这是 07a 验收① 的一条命令化。CI 全绿（418 passed/ruff/mypy strict）。

## 2026-07-17 R2-07 07a 整片收账（验收①真机通过）+ R2-11 考古启动
- PR #19 合并（refund_request 两档 + 对账 harness 入 main）。部署机升级 HEAD=176da4c、
  alembic 0031、migrate 退出码 0。
- 验收①：A152（store_id=1）拉回 36 张真实退货单（36 头 36 行 1 页），return_pull_verify
  全口径对账一致 ✅（evidence/R2-07/a152-recon-20260717.md）。07a 整片收账。
- 按 Owner 批准顺序转 R2-11 变体组：五路考古已启动（图纸/采集现状/构建器扩展点/旧仓/渠道规格）。

## 2026-07-17 R2-11 考古完成（五路并行，全绿）
- 修正 007 论断：0007 迁移已建变体三件套 DDL，范围收窄（无需建表）；采集素材已齐；
  旧仓有生产级实现与两份设计文档；渠道字段全集三层取证（设计文档/pt_spec/items OpenAPI）。
- 综合报告 evidence/R2-11/archaeology.md（增量拆三 + P0 拍板三项 + P1 自定清单 + 批注四条）。
- P0 待 Owner：anchor 落点 / variantGroupId 体系 / 组提交原子性。拍板后开增量1。

## 2026-07-17 R2-11 增量1 完码（归组闭环）+ D-Q63 + R2-12 立单入账
- D-Q63 三拍板入宪法表（anchor_store_id / VG{组id} / 整组拒绝，Owner 全按建议）。
- 0032：variant_group.anchor_store_id + variant_group_sync 调度种子（40 * * * *）。
- erp.catalog.variant：自动归组（只信 twister 素材、排除自指 parent、(team, parent_ref) 组身份、
  member+product.variant_group_id 双向同步）；broken 判定 v1（成员<2/维度键集冲突/超上限
  variant.max_group_size 配置中心默认 10）+ warn 通知 + 自动回 active；团队间失败隔离。
- 契约端点：POST /variant-groups / PUT members（主变体唯一闸/一品一组闸/摘员双向清理）/
  GET 列表（status/q 过滤，契约增补注记）。beat TASKS 注册 variant_group_sync。
- 本地 CI：pytest 423 passed（新测 5 项：归组规则/幂等重跑/巨型组护栏/端点闸/列表过滤）
  + ruff + mypy strict。main 4a57b68（R2-12 立单）已并入，顺序更新入 task.md。

## 2026-07-17 R2-11 增量2 完码（Workflow 编排：Opus 执行 / fable 规划验收）
- 编排实录：三段串行实现（变体段/守卫/测试）+ 三路并行评审（D-Q63 契合/fail-closed/回归）。
  故障两起均恢复：段2 结构化输出五连败（Opus 漏传必填空数组）→ 改主控核实常量绕行；
  实现本体未损失（守卫 281 行已落盘）。
- 交付：spec.py 变体段（theme_map 配置中心+加性合并、维度值进指纹、VG{组id} 实例化注入、
  broken/PT 不支持/键不可映射/维度缺失/映射冲突 五重 fail-closed）；service.py 三闸整组守卫
  + anchor 首上原子锁（RETURNING 判空防并发 TOCTOU）；变体控制字段全链剥离（isPrimaryVariant
  一律不传，LLM 幻觉防注入）；组不齐"在场"扩展（同店在途/在架算在场）。
- 评审验收：1 blocker（match 被 broken 组误拦）+3 major 全修，2 minor 修复，2 挂账（anchor
  解锁通道、组上下文批量化）。本地 CI：pytest 440 passed + ruff + mypy strict 全绿（新测 17 项）。
