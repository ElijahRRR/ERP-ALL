# 05 audit — 审核运行 / 命中 / 策略 / LLM 缓存 / 用量

> 移植源：**walmart-audit-system 独立仓**（D-Q38，erp-core 内嵌副本弃用）。L0 内存字典 → L1 混合检索 → L2 商标（硬拒 R1-R3 / 软证据 R4-R8）→ L3 37 条策略（LLM）→ L4 视觉（默认关）。
> 容量：日 20 万审核（NFR）→ audit_run/audit_hit 月分区；成本控制 = flash 优先 + sha256 缓存 + L4 默认关（PRD §9）。

## audit_run 审核运行（月分区）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | identity | PK=(id, created_at) |
| team_id | BIGINT | NOT NULL | |
| product_id | BIGINT | NOT NULL | 不建 FK（本表分区、写入热路径） |
| trigger_kind | TEXT | NOT NULL CHECK IN (auto, manual, batch, re_audit) | |
| levels_requested | TEXT[] | NOT NULL | 如 {l0,l1,l2,l3}；L4 需显式开（默认关） |
| status | TEXT | NOT NULL DEFAULT 'queued' CHECK IN (queued, running, done, failed) | |
| verdict | TEXT | NULL CHECK IN (pass, reject, needs_review) | done 时必填 |
| reject_level | TEXT | NULL | 首个否决层（l0/l1/l2/l3/l4） |
| llm_cost_usd | NUMERIC(12,6) | NOT NULL DEFAULT 0 | 本次运行合计（含缓存命中 0 成本项） |
| cache_hit_rate | NUMERIC(4,3) | NULL | 观测指标 |
| started_at / finished_at | timestamptz | NULL | |
| duration_ms | INT | NULL | |
| created_at / created_by | | 分区键=created_at | |

索引：`(team_id, product_id, created_at DESC)`、`(team_id, verdict, created_at DESC)`、`(status) WHERE status IN ('queued','running')`。
verdict 语义：pass → product.status=audit_passed；reject → audit_rejected；needs_review → 人工队列（前端审核工作台）。审核→上架衔接三档由 automation_policy(flow=audit_to_listing) 决定 pass 后是否自动进入分配（D-Q13）。

## audit_hit 审核命中（月分区，跟随 run）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | identity | PK=(id, created_at) |
| run_id | BIGINT | NOT NULL | 逻辑关联 audit_run（同月分区，不建 FK） |
| team_id | BIGINT | NOT NULL | 冗余，RLS 与直查用 |
| product_id | BIGINT | NOT NULL | 冗余 |
| level | TEXT | NOT NULL CHECK IN (l0, l1, l2, l3, l4) | |
| rule_code | TEXT | NOT NULL | L2: R1..R8；L3: 策略 code；L0: 字典名 |
| is_hard | BOOLEAN | NOT NULL | L2 语义：R1-R3 硬拒 true / R4-R8 软证据 false |
| score | NUMERIC(6,3) | NULL | 检索/模型得分 |
| evidence | JSONB | NOT NULL DEFAULT '{}' | 命中证据（商标号/黑名单行/LLM 判词摘要） |
| created_at | | 分区键 | |

索引：`(run_id)`、`(team_id, rule_code, created_at DESC)`（规则命中率分析）。

## audit_policy 审核策略登记（全局作用域）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| code | TEXT | NOT NULL UNIQUE | 策略编码（移植时保留源仓 code） |
| level | TEXT | NOT NULL CHECK IN (l0, l1, l2, l3, l4) | |
| name | TEXT | NOT NULL | 中文名 |
| description | TEXT | NULL | |
| is_hard | BOOLEAN | NOT NULL DEFAULT false | 命中即否决 vs 累积证据 |
| enabled | BOOLEAN | NOT NULL DEFAULT true | 全局开关；**L4 策略默认 false** |
| config | JSONB | NOT NULL DEFAULT '{}' | 阈值/提示词版本/模型名（运营可调，D-Q11） |
| version | INT | NOT NULL DEFAULT 1 | config 每改 +1；audit_hit.evidence 记录命中时 version |
| updated_by / updated_at / created_at | | | |

基线：walmart-audit-system 的 L3 37 条策略 + L2 R1-R8 全部登记为种子数据（移植工单产出对照表）。
团队级差异不在本表——团队只有三档衔接开关（automation_policy），策略本身全局统一（避免审核标准漂移）。

## llm_cache LLM 响应缓存（全局）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| cache_key | TEXT | PK | sha256(model + 规范化 prompt)（移植源仓语义） |
| model | TEXT | NOT NULL | |
| response_text | TEXT | NOT NULL | |
| response_meta | JSONB | NOT NULL DEFAULT '{}' | tokens/finish_reason |
| hit_count | INT | NOT NULL DEFAULT 0 | |
| created_at / last_hit_at | timestamptz | | |

- 全局共享（跨团队命中同 ASIN 同策略即复用——审核输入不含团队私有数据，安全）。
- 失效：策略 config.version 变更进 cache_key 派生（prompt 变即新 key），无需主动清理；容量治理=LRU 清理任务（automation 域，last_hit_at > 180 天）。

## llm_usage_log LLM 用量（月分区，成本引擎）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | identity | PK=(id, occurred_at) |
| team_id | BIGINT | NULL | 全局任务（类目映射批量）为 NULL |
| module | TEXT | NOT NULL CHECK IN (audit, category_map, mail_classify, listing, other) | listing=上架 AI 属性填写（0020，R2-03） |
| model | TEXT | NOT NULL | v4flash/v4pro…（团队自管模型选择，D-Q19）；标准模型=deepseek-v4-flash（D-Q58） |
| prompt_tokens / completion_tokens | INT | NOT NULL | |
| cached_input_tokens | INT | NOT NULL DEFAULT 0 | 输入中命中 provider prefix-cache 的 token 数（0018；DeepSeek 命中价≈未命中 1/50，计价与 RS-08 实测用） |
| cost_usd | NUMERIC(12,6) | NOT NULL | 单价表在 system_config `llm.pricing`（{model: {input_per_1m, input_cache_hit_per_1m, output_per_1m}}，运营可更新） |
| cache_hit | BOOLEAN | NOT NULL DEFAULT false | 命中缓存也记行（cost=0），保真实调用画像 |
| object_type / object_id | TEXT/BIGINT | NULL | 归因（product/audit_run…） |
| occurred_at | timestamptz | NOT NULL DEFAULT now() | 分区键 |

索引：`(team_id, module, occurred_at)`。
预算闸：automation 域每小时聚合本表 → team_config 预算阈值（`llm_budget_daily_usd`）超限 → notification critical + 三档策略降级建议（不自动停，人决策）。

**保留口径（2026-07-28 补记，源自 team 2 验证残留清理实践）**：本表**无永久保留要求**
（D-Q18 的永久保留只覆盖订单与售后），团队注销或验证残留清理时可随 `team_id` 一并删除；
预算闸只吃近窗聚合，删历史行不影响其正确性；财务域（§08）的分录科目**不含 LLM 成本**，
故亦无依赖。
> ⚠️ **与审计三族的不对称（结构事实，不是疏漏）**：`audit_run`/`audit_hit`/`audit_log`
> **无 team 外键**（分区热路径不建 FK，见本文 audit_run 列注），因而**无法按团队选择性
> 删除**——清理团队时它们必然整族留存。结果是"审计发生过"的证据保留、而其成本流水可被
> 清走。这个不对称是可接受的：审计留痕是合规底线，成本流水只是运营指标。
> **任何清理指令须显式声明审计三族一行不删**，不得为"清干净"去动它们。
