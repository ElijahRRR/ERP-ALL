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
