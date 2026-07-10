# 010 — AI 团队编成与任务编排（Team & Orchestration）

- 日期：2026-07-08 · 前置：PLAN.md（路线图）、WIRING-AUDIT.md（内部断线清单）
- 命题：把 erp-core 作为完整产品交付（满足现有需求 + 可持续扩展），需要哪些智能成员、
  各自职责、任务如何规划编排。

---

## 1. 编成原则

1. **按"文件所有权"分工，不按"功能名词"分工。** Agent 并行的最大风险是改同一批文件互相踩踏；
   所以每个成员对一片目录/表拥有独占写权，跨界需求走"提单"给对应 owner，而不是自己动手。
2. **小团队 + 强主控。** 每个 agent 冷启动都要重建上下文，成员越多、协调成本越高。
   6 个角色是上限，多数阶段同时活跃的只有 2-3 个。
3. **工单驱动。** 唯一事实源是 `.agent/review_list.json`（工单池）+ PLAN.md（路线图）。
   没有工单的代码改动一律不合并——防止"顺手改"把系统改回半接线状态。
4. **验证权独立。** 写代码的 agent 不给自己验收；QA 角色有 block 权。

## 2. 成员与职责

### T0 主控 / 架构（Tech Lead）——主会话（我）
- 持有全局上下文：PLAN、review_list、决策记录（D1-D5）、退役清单。
- 把 Phase 拆成任务卡（见 §3.2），派发给对应成员；评审并合并所有 PR。
- **独占权**：alembic migration 编号分配、endpoint 契约表冻结、CLAUDE.md/specs 修改。
- 对你（Owner）的接口：决策请示、阶段验收演示、风险上报。

### T1 数据平台工程师（DB & ETL）
- **管辖**：`alembic/versions/`、`app/models/`、`scripts/etl/`、新建 `app/services/lark_io.py`。
- Phase 1 主力：canonical schema 列级设计、master_sku 生成与映射、飞书/xlsx/SQLite ETL、
  每晚对账任务与差异报告、凭证加密入库。
- 交付物永远成对：migration + 可重放的回滚说明；ETL + 对账证据。

### T2 后端领域工程师（Domain Backend，可按模块开多个实例）
- **管辖**：`app/services/{listing,orders,aftersales,catalog,analytics}/`、`app/tasks/`（除 celery_app.py 路由表）。
- 消化两类工单：2A 接线补完（R-ERP-001/013/014/016、returns R-ERP-006）、2B 功能回收
  （九个零散模块的最新逻辑移植，按 PLAN 顺序）。
- **禁区**：不动 migrations（向 T1 提单）、不动 walmart_client/rate_limiter 语义（向 T3 提单）。

### T3 渠道集成工程师（Channel Integration）
- **管辖**：`app/services/walmart_client.py`、`walmart_reports.py`、`rate_limiter.py`、
  Phase 5 的 channel adapter 接口。
- 职责：所有"会打到 Walmart"的语义变更；feed 类型接入（MP_ITEM_MATCH 跟卖）；
  官方文档/速率限制核对（沿用"写脚本前必 grep rate_limits.tsv"纪律）。
- **团队里唯一允许触碰真实渠道写路径的角色**，且每次变更必须附 dry-run 证据。

### T4 前端工程师（Frontend）
- **管辖**：`handoff-design/project/src/`。
- 职责：修 3 个 404 调用、五个 mock 页面改页面级 fetch、F33-F37 对接（后端已就绪）、
  退役 `/v5/all` megablob 覆盖模式、Phase 3 同事上传入口。
- 输入是主控冻结的 **endpoint 契约表**（路径/参数/响应示例），不直接改后端；
  发现契约缺口→提单给 T2。

### T5 QA / 验证工程师（Verification）
- **管辖**：`backend/tests/`（新建）、CI 配置、接线审计脚本（把本次审计的三个交叉对照固化为可重跑的回归工具）。
- 职责：为每条合并做对抗式 code-review；维护 pytest + dry-run e2e；
  **每合并一批后重跑接线审计**——新增"端点无调用者/任务无派发者/表无读写方"即回归失败。
- 有权 block 合并；不写业务代码。

### T6 采集/性能工程师（Scraper & Perf，按需激活）
- **管辖**：`app/services/scraper/`、`run_async_worker.py`、worker 池脚本。
- Phase 2B/3 激活：AIMD 参数与代理池调优、采集吞吐回归基准、rescrape 链路容量评估。

### Owner（你）——不可替代的三件事
1. 决策点拍板（D1-D5、促销下线 vs 修复、审核→上架自动化的风险偏好）；
2. 阶段验收（每个 Phase 的演示 + 退役清单确认）；
3. 生产钥匙：凭证轮换、live 放量审批（walmart_live_feed 开关、白名单店铺）、真实上架抽检。

## 3. 任务编排方法

### 3.1 节拍（每条工单的生命周期）

```
review_list 工单 → 任务卡(spec) → 独立分支/worktree → 实现
  → 自验(验收命令) → PR → T5 对抗评审 + CI → T0 合并
  → 退役清单/文档更新 → 接线审计回归
```

### 3.2 任务卡模板（主控派单时填写）

| 字段 | 内容 |
|---|---|
| 工单 | R-ERP-xxx / REF-xxxx |
| 目标 | 一句话 + 验收标准（可执行命令） |
| 管辖文件 | 允许改动的目录白名单 |
| 禁区 | 明确不许碰的文件/语义 |
| 依赖 | 前置工单 / 契约版本 |
| 证据要求 | dry-run 输出 / 对账报告 / 测试通过截图路径 |

### 3.3 并行规则

- **可并行**：文件所有权不相交的工单（如 T4 前端接线 ∥ T2 returns 实现 ∥ T1 ETL）。
- **必串行**：所有 migration（T1 单线）；celery_app.py 路由表变更（T0 审批）；同模块工单排队。
- **WIP 上限 3**：同时进行的实施工单不超过 3 个，避免 rebase 地狱和上下文分裂。
- 集成节奏：小步合并（工单粒度），禁止长寿分支；每天末主分支必须绿。

### 3.4 质量门禁（缺一不合并）

1. CI：ruff + pytest + compileall；
2. 涉渠道写路径 → dry-run 证据（`walmart_live_feed=False` 全程，live 放量单独走 Owner 审批）；
3. 涉数据迁移 → 对账零差异报告；
4. 接线审计回归无新增断线；
5. review_list 状态与 progress.md 会话记录已更新。

### 3.5 阶段 × 人力矩阵

| Phase | T1 数据 | T2 领域 | T3 渠道 | T4 前端 | T5 QA | T6 采集 |
|---|---|---|---|---|---|---|
| 0 奠基止血 | ◐ schema 草案 | ◐ UPC 闭环 | ○ | ○ | ● CI/回归工具 | ○ |
| 1 数据统一 | ● 主力 | ◐ 配合改读写 | ○ | ○ | ● 对账验证 | ○ |
| 2A 接线补完 | ◐ 表清杂 | ● 主力 | ◐ 死队列 | ● F33-37/mock | ● | ○ |
| 2B 功能回收 | ○ | ● 主力 | ● 跟卖 feed | ◐ | ● 影子双跑 | ◐ |
| 3 执行面统一 | ○ | ◐ | ◐ | ● 同事入口 | ● | ● 池化部署 |
| 4 闭环 | ○ | ● 策略引擎 | ◐ | ◐ 看板 | ● | ○ |
| 5 多平台 | ◐ | ◐ | ● adapter | ◐ | ● | ○ |

●主力 ◐参与 ○闲置（不实例化，省上下文成本）

### 3.6 在 Claude Code 中的落地形态

- 六个角色定义为 `.claude/agents/*.md`（各自的系统提示 = 上文"管辖/禁区/证据要求"），
  主会话用 Agent 工具派单，worktree 隔离并行工单；
- `.agent/` workflow（task/review_list/progress/handoff）继续作为跨会话记忆——任何成员的产出
  必须回写工单状态，主控换届（新会话）靠 handoff.md 十分钟接管；
- 每个 Phase 开工前，主控产出该 Phase 的 spec（specs/011、012…），工单全部登记后才动第一行代码。
