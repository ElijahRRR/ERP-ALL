# 003 R1 行走骨架任务分解（EA-003）

- 日期：2026-07-10 · 作者：ar+pm 帽 · 状态：任务清单已注册（.agent/review_list.json R1-01~R1-12）
- 依据：PRD §8 R1 定义（地基第一天就位 + 一条最小闭环全真实接线）+ 001 数据字典 + 002 契约
- 验收总纲（PRD/CLAUDE.md 验证纪律）：**每个工单 = CI 绿 + 证据落 `.agent/evidence/R1-xx/`**；渠道写路径必须先 dry-run 证据、真实调用只允许 A152（is_test 店）。

## 执行顺序与依赖

```
R1-01 工程化 ─→ R1-02 配置中心 ─→ R1-03 migration 基线 ─→ R1-04 认证/RBAC/审计出口
                                                            ├→ R1-05 identity API+前端
                                                            ├→ R1-06 通知中心骨架
                                                            └→ R1-08 店铺/代理/配额
R1-07 渠道网关移植（依赖 R1-02，可与 R1-05/06 并行）
R1-09 采集最小闭环（依赖 R1-03/07 不依赖前端）
R1-10 审核最小闭环（依赖 R1-09 产出的 product）
R1-11 上架最小闭环（依赖 R1-07/08/10）
R1-12 E2E 演示与失败路径（收口，依赖全部）
```

## 工单定义

### R1-01 仓库工程化与 CI（帽：ar，qa 验收）
- monorepo 布局：`backend/`（FastAPI src 布局 + alembic + tests）、`frontend/`（Vite+React+TS+AntD 中文）、`infra/`（docker-compose、部署脚本）、`workers/`（采集 worker 独立打包，本地运行）。
- 工具链：uv + ruff + mypy(strict 渐进) + pytest；pnpm + eslint + tsc。pre-commit 钩子。
- CI（GitHub Actions）：lint → typecheck → 单测 → migration 空库演练（`alembic upgrade head` 于 pg16 service container）→ 前端 build。主分支保护：CI 绿才可合。
- docker-compose：pg16（含 vector/pg_trgm/pgcrypto 初始化）+ redis7 + api + worker + beat。
- 验收：CI 全绿截图/日志入 evidence；`docker compose up` 一条命令起全栈。

### R1-02 配置与密钥（帽：be-domain）
- pydantic-settings 三层：环境变量 > .env > 默认值；密钥（DB/JWT/加密 key/LLM key）只走环境。
- ConfigService：system_config/team_config 读取（team > system > 代码默认），60s 进程缓存 + 失效广播（Redis pubsub）。
- 验收：单测覆盖优先级链；grep 全仓无魔法业务参数。

### R1-03 migration 基线（帽：ar 独占——唯一可动 migration 的角色）
- alembic 初始化 + 首批表：01-identity 全部 9 表 + 02-channel 7 表 + 09-system（sys_dict/system_config/team_config/schedule/task_run/notification 三表）。
- DB 角色三件套（erp_app/portal_app/erp_migrator）+ 全部团队域表 RLS policy + updated_at 触发器 + audit_log 分区骨架与 REVOKE。
- 种子：channel(walmart_us)、permission 首批清单（见附录 A）、全局模板角色 7 个（PRD §3）。
- 验收：升降级往返（upgrade head → downgrade base → upgrade head）无错；RLS 集成测试（两团队互查为空）。

### R1-04 认证 + RBAC 中间件 + 审计出口（帽：be-domain）
- JWT 双 audience（erp/portal）、argon2id、登录锁定、token_version 吊销。
- 权限点装饰器（读 002 契约 x-permission 同名码）；请求事务注入 GUC（app.current_team/app.is_super）。
- audit_log 唯一写出口（写操作装饰器：action/object/before/after 自动采集）。
- 验收：权限矩阵单测（有/无权限点 × 超管 ×门户 token 拒绝）；audit_log 对每类写操作断言成行。

### R1-05 identity API + 前端骨架（帽：be-domain + fe）
- 002 契约 Identity/Auth 段全部端点；前端：登录页、布局壳（菜单按权限点渲染）、成员/角色管理页、审计日志查询页。
- fe 铁律：只依赖 openapi-v0.yaml codegen 的 client，禁止 mock-first。
- 验收：Playwright E2E——开团队→开成员→赋角色→登录→越权 403。

### R1-06 通知中心骨架（帽：be-domain + fe）
- notification 三表 + 列表/已读 API + 前端铃铛与通知页；dedupe_key 抑制。
- 验收：task_run 失败→通知出现的集成链路。

### R1-07 渠道网关移植（帽：be-channel 独占）
- 考古移植：erpAPI walmart_client（token 900s 复用、5 头、代理绑定）+ GCRA 限流（走 `docs/walmart_rate_limits.tsv` 数据）+ 自适应退避（读 x-current-token-count / X-Next-Replenishment-Time）。
- 网关三模式：`dry_run`（构造请求不发出，落证据）/ `live_test`（仅 is_test 店可发）/ `live`（Owner 放量开关，system_config）。
- 验收：**dry-run 证据**（items 读接口构造请求快照）+ **A152 真调**（GET /v3/items 冒烟）双证据入 evidence。

### R1-08 店铺/代理/配额（帽：be-domain + fe）
- 002 契约 Channel 段全部端点 + 前端店铺档案页（含凭证掩码维护、代理换绑、配额面板）。
- 配额原子扣减协议（001 §quota_usage）+ 单测并发夺额度。
- 验收：A152 入档、凭证经加密落库、UI 全流程可操作（D-Q20）。

### R1-09 采集最小闭环（帽：be-channel[采集] ）
- scrape_job/task/result 三表 + server 派发 API（worker 拨入协议：注册/心跳/领任务/回传，移植 v3 语义 D-Q42/47）。
- 单 worker 本地拨入云端（或本容器模拟）跑通：建 job(单 ASIN) → task 派发 → result 回传 → product upsert（03 §去重协议）。
- 验收：product 表出现该 ASIN、job 计数正确；worker 断连任务回收测试。

### R1-10 审核最小闭环（帽：be-domain[审核]，源=walmart-audit-system D-Q38）
- audit 5 表 migration + L0（黑名单内存字典，先用手工种子的 10 行黑名单）+ L2（refdata.trademark 先导入 1 万行子集）+ L3（1 条策略 + llm_cache + usage_log 记账）。L1/L4 留 R2。
- 验收：单 ASIN 走 L0→L2→L3 出 verdict；同输入二次运行 cache 命中 cost=0；usage_log 有行。
- ⚠️ 移植保真：先抓源仓策略/提示词对照表入 evidence，再写代码（考古纪律）。

> ⚠️ **修正注记（D-Q54，2026-07-11）**：R1-11/R1-12 原验收含 A152 真调（L2 动作），
> 与 R1 的 L0 骨架定级错位。修正后 R1 验收=骨架验收（evidence/R1-12/owner-acceptance-runbook.md v2），
> A152 真调挂 R2-03「上架真实化」验收②。等级定义见 specs/005-r2-plan/README.md。

### R1-11 上架最小闭环（帽：be-channel + be-domain）
- listing/feed/feed_item/listing_spec/listing_error_catalog/maintenance_task migration + allocate/submit/轮询/状态回写链路（002 契约 Listing 段核心端点）。
- GTIN：手工导入 20 个 EAN-13 入池（import-jobs 通道），走 held→used 全程。
- 提交路径：dry-run 全量证据 → A152 真实上架 **1 个 SKU** → 轮询 PROCESSED → live 状态回写 → 再走 delist 收尾。
- 验收：A152 渠道后台可见该品；feed verify-back 分支有单测（channel_feed_id=NULL 场景）；listing_state_history 完整链。

### R1-12 E2E 演示与失败路径（帽：qa 主导收口）
- 脚本化演示：采集(1 ASIN)→审核→(人工确认，D-Q13 manual 档)→上架(A152)→轮询回写→前端全程可见。
- 失败路径三演示：审核拒绝、配额耗尽拒绝、feed 错误→error catalog 处置→notification 告警。
- 验收：Owner 现场（或录屏）验收 = **R1 完成判据**（PRD §8）。

## 附录 A：permission 首批种子（R1 范围）

`identity.team_admin / identity.user_read / identity.user_write / identity.role_write / identity.audit_read`
`channel.store_read / channel.store_write / channel.credential_view / channel.credential_write / channel.proxy_read / channel.proxy_write / channel.quota_write / channel.incident_read / channel.incident_write`
`catalog.product_read / catalog.product_write / catalog.gtin_read / catalog.import_read / catalog.import_write / catalog.category_write / catalog.source_write`
`listing.read / listing.allocate / listing.submit / listing.delist / listing.maintain / listing.error_admin / pricing.read / pricing.write`
`order.read / order.check / order.assign / order.ship / procurement.read / procurement.execute / procurement.admin`
（R2 起随模块增补；代码 Enum 与种子一致性由 CI 校验，R1-03。）

## 附录 B：R1 明确不做

促销/WFS/手机端（非目标）；L1/L4 审核；变体组上架；跟卖 match 模式；订单/售后/财务全域（R2#6-8）；邮箱；三档自动化引擎（R1 全部流程固定 manual 档）——防骨架期范围蔓延。
