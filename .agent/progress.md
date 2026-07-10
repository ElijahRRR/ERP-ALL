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
