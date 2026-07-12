# 外部评审 Round-1 · Fable 逐条回应（2026-07-12）

> 评审方：部署机本地 AI（基于 main@083dcc8 全库只读通读，按 `specs/REVIEW-BRIEF.md` 执行）。
> 本文件 = 评审意见的压缩存档 + Fable 的逐条裁定 + 落点（工单 / 已修 / spec 修订）。
> 结论先行：**21 条意见，17 条采纳、4 条部分采纳、0 条驳回**。评审中全部"指控级"论断
> （A2/A3/A4/A5/A7/B4）已逐条到代码核实，**全部属实**。A4（L3 fail-open）当日已修复。

## 核实记录（指控级论断 → 代码证据）

| 论断 | 核实结果 |
|---|---|
| A4：非法 LLM 输出被强转 pass | ✅ `pipeline.py` coerce 两处 fail-open（bad JSON→pass、非法 verdict→pass）——**当日已修** |
| A2：erp_app 可自设 `app.is_super` | ✅ `db.py system_tx` 即 erp_app `set_config('app.is_super','on')`，第二道防线不独立 |
| A7：持行锁做网络调用 | ✅ `audit/service.py` 先 `FOR UPDATE` 锁 product 再等 LLM；listing submit 在单请求事务内做配额+组feed+网关提交 |
| B4：商标导入全量进内存+单大事务 | ✅ `import_trademark.py _read_rows` 返回完整 list，import 单 system_tx |
| A3：compose 未注入 JWT 密钥、DB/Redis 全接口发布 | ✅ `docker-compose.yml` backend_env 只有 DB/Redis URL；5432/6379/8000 发布到 0.0.0.0 |
| A5：pull_tasks 不按 kind 过滤、不限量 | ✅ `scrape/service.py pull_tasks` 无 kind/source 过滤、无 capacity-inflight 上限 |

---

## A 层 · 整体架构

### A1 限界上下文 —— 采纳
评审：缺数据摄取上下文；automation 有吞噬风险；缺持久化复核案件模型；库存不应只是 listing 附属字段。
**回应**：全部认同，且与 006 数据治理方向自然收敛。落点：① 001 增补 `data_ingestion`
逻辑边界（source registry / import / sync / staging / provenance，不拆服务）；② 各域自建
`review_case`，统一待办箱只做只读投影（→ RS-06）；③ 库存先抽成 listing 内独立
aggregate/interface，第二渠道前再定级；④ automation 边界纪律（只编排不持业务状态）写进 001 conventions。

### A2 RLS + GUC 多团队隔离 —— 采纳（FORCE RLS 一条部分采纳）
评审：is_super GUC 可被应用角色自设，防不了注入/盗号；单列 FK 可跨租户悬接；后台任务权限面过宽。
**回应**：属实。GUC 本质上连接内任何人都能 SET，"第二道防线"目前只防开发疏漏——文档措辞
按评审修正。落点（RS-01，**闸门=多团队正式使用**）：① system 通道改独立 DB 角色
（erp_system/erp_worker，最小授权），RLS 判超管改按 `current_user` 而非可自设 GUC；
② 父表 `(team_id,id)` 唯一键 + 团队域复合 FK（分区热表用约束触发器+日对账）；
③ 增加"跨团队 store/product/listing 组合必须失败"集成测试。
FORCE RLS 部分采纳：它只约束表 owner（运行时 erp_app 非 owner，本已受 RLS），加上无害、防未来漂移，一并做。

### A3 本地单机部署 —— 采纳
评审：默认密钥回落、端口全暴露、备份只警告不强制、迁云复杂度被低估。
**回应**：全部认同。落点（RS-02，**闸门=团队经营/门户对外 R2#6**）：prod 检测默认
JWT/DB 密钥硬失败；DB/Redis 不发布宿主端口或只绑 127.0.0.1；前端生产构建 + HTTPS 反代
（停 Vite dev server 对外）；异地备份失败=任务失败+告警，加密+校验+月度恢复演练；
R2#6 前做一次"本地→临时云→回切"迁移演练并以 RPO/RTO 为闸门。试点期（Owner 单人内网）
风险敞口有限，但按闸门推进不拖。

### A4 审核管道 —— 采纳，核心问题当日已修
评审：非法 LLM verdict 强转 pass 是合规 fail-open；审核运行未冻结数据集/prompt/模型版本，不可复现。
**回应**：fail-open 属实且是本轮评审最有价值的单点发现，**已修**（见文末"当日修复"）。
其余落点：① 决策 manifest（代码 commit / 输入 hash / 黑名单+category_map+政策版本 /
prompt hash / model+provider）→ RS-09；② 管道顺序固化为 输入质量门→L0→L1→L2→L3→
选择性 L4→统一决策器（L1 落地时接线）；③ L4 按风险+不确定度触发（原规划一致）。
**自查出的次生问题**：坏响应已进 llm_cache 会导致同输入重审复现 needs_review——parse_error
响应不落缓存/主动失效，并入 RS-09。

### A5 worker 租约协议 —— 采纳（分级排期）
评审：无 kind/source 过滤、无领取量上限、共享 enroll token 可囤积/污染；租约一刀切 10 分钟。
**回应**：属实。便宜三件先做（RS-07）：按 node kind/source 过滤派发、
`min(request_count, capacity-inflight)` 限量、按 job_kind 租约时长+续租。重三件
（mTLS、公平队列、毒任务隔离）挂**多节点/跨公网闸门**——当前现实为单 Owner 单节点内网拨入，
暴露面有限，先记不欠。

### A6 LLM 成本控制 —— 采纳
评审：输入 hash 缓存对 20 万新品/日命中有限；prefix cache 省钱需读供应商 cached-token 指标验证；预算是事后告警非事前闸门。
**回应**：全部认同。落点（RS-08）：调用前原子预算预留（日预算/并发/RPM/单任务 token 上限，
成功结算失败释放）；usage_log 增记 `cached_input_tokens`/`pricing_version`/重试/延迟——
用真实指标验证"静态政策块 95% 前缀命中"假设而非推断。模型分级路由待 A152 实测每千件成本后定
（与评审建议一致）。团队级 key 复用渠道凭证加密机制。

### A7 跨上下文异步与渠道双写 —— 采纳（P0）
评审：幂等契约无存储实现；持行锁调 Walmart/LLM 最长可占锁 120s；渠道已收但 DB commit 失败会连 verify-back 记录一起丢。
**回应**：全部属实，**认同这是 top-1 严重度**。落点（RS-03，**硬闸门=真实渠道写入
（A152 L2 测试店写）之前**）：PG transactional outbox/inbox——短事务写 command+幂等键+
payload hash 后提交；worker SKIP LOCKED 领取，**事务外**调渠道/LLM；新短事务回写，未知结果
进 verify-back；`(team, action, idempotency_key)` 唯一约束。audit 的 LLM 调用同模式移出行锁。
Idempotency-Key 消费（C2）并入本单。时机说明：当前唯一真实外呼是采集（已异步拨入）与
R1 dry-run，尚无实际暴露，但 A152 前必须落。

---

## B 层 · 数据治理

### B1 活数据迁移水位 —— 采纳
评审：单列 `updated_at > watermark` 漏同时戳跨批/回拨/历史修订/删除。
**回应**：认同。006 §9 修订：复合游标 `(updated_at, pk)` + 重叠窗口重扫；删除走 tombstone；
每日 key/count/hash 对账；按表分型——缺时间戳高变更表加源端 change-log trigger、纯追加表
用自增 ID、小表全量 hash-diff；logical decoding 只留给无法加 trigger 的少数高频表。
落进 DG2 同步框架工单验收标准。

### B2 飞书↔DB —— 采纳
评审：反对 TRUNCATE 重灌 canonical；列错位/删列/并发覆盖防不住。
**回应**：认同（006 原稿"拉入覆盖"表述确实与 DB=master 语义矛盾）。修订：飞书编辑一律
视为 command/candidate 先入 staging；行稳定 UUID+revision+last_source_hash；字段级 owner +
compare-and-set；冲突进 quarantine；DB→飞书回写记 writeback ledger；canonical 只做事务性
merge、永不 TRUNCATE。落 DG3。

### B3 反馈闭环污染 —— 采纳
评审：审核 reject 直接当 groundtruth 会自证循环、误判固化。
**回应**：完全认同，这是自动化合规系统的经典陷阱。落点（RS-05，升级原 DG4）：
observation → candidate → adjudication → effective_rule 四层；模型输出只能是 observation；
自动生效需独立渠道证据或人工双审；新规则 shadow 统计误杀率→canary→生效；
带置信度/有效期/撤销原因/一键回滚。

### B4 USPTO 14M 搬运 —— 采纳（时间上最紧迫）
评审：现 CLI 全量进内存+逐行 SQL+单大事务，搬不动 14M。
**回应**：属实——现通道为 3.6 万级黑名单设计，14M 必须换弹药库。落点（RS-04，**最先动工**，
因为数据马上要来）：流式读取 → `COPY` staging → set-based merge → 分批提交 +
manifest/checksum；水位用 USPTO 源文件日期/ETL manifest/serial（而非未经证明的行级
updated_at）；首次快照与增量起点同一致性边界。

### B5 溯源/软删/多源冲突 —— 部分采纳
评审：单行+固定优先级不够，要 source assertion ledger；缓存版本 count+max(ts) 有碰撞，改事务内递增 revision。
**回应**：① 断言账本**方向认同、分级落地**——多源冲突真实存在的域（黑名单/TRO/商标豁免）
上 assertion ledger（canonical=有效断言投影，人工裁决可压自动源、优先级按域/字段配置）；
单源域保留 canonical+provenance 列不加账本层——全域立即上账本对当前规模是过度设计，
账本作为通用能力在 RS-04 建一次、按域启用。② 缓存版本改 `refdata_revision` 事务内递增：
**全盘采纳**（并入 RS-04），现 count+max(added_at) 确有同秒替换碰撞窗口，且免大表聚合。

### B6 L1 映射+LLM —— 采纳（与现设计一致）
评审：精确命中短路、多候选才 LLM 复排、勿全量 6832 塞 LLM、冻结评测集量化。
**回应**：与已定设计（`.agent/evidence/R2-02/l1-category-design.md`）一致：direct 高置信
短路 → category_map+关键词召回候选 → LLM 只做候选复排。补充采纳：冻结评测集 = 旧系统
4326 ASIN 子集，验收统计覆盖率/Top-1/Top-k/千件成本/下游错误率。向量增强维持可选（D-Q55）。

### B7 数据资产管理缺失 —— 采纳
评审：缺 source registry/data contract/DQ+quarantine/责任人 SLA/保留策略/许可记录。
**回应**：确为遗漏。006 新增 §10"管理数据的数据"；source registry + 导入 manifest 随
RS-04 先落，其余（数据契约版本、DQ 规则、保留策略、责任人）随各域数据实际接入渐进补齐，
不一次性造全套空架子。

### B8 边建边追与切主 —— 部分采纳
评审：追平≠无缝切主；要 backfill→shadow_read→reconciled→write_fenced→master 状态机+写栅栏。
**回应**：状态机与写栅栏采纳，写进 006 §9。修正一处量级判断：多数域的"旧系统"是
**脚本+飞书表**而非活跃 OLTP——写栅栏=停旧脚本+记录最后游标，成本很低；只有 Mac PG 上
持续产出的域需要完整栅栏/LSN/回滚窗口。"不做无约束双写"本就是 006 共识。

---

## C 层 · 盲区

### C1 人工复核工作流缺失 —— 采纳
**回应**：属实（本轮 A4 修复后 needs_review 产品已在产品列表可见可重审，但认领/SLA/裁决
留痕/并发仲裁全无）。落点 RS-06：各域 review_case/decision 模型 + 统一待办箱只读投影 +
裁决幂等留理由可撤销。审核 needs_review 是第一个接入方。

### C2 契约与实现漂移 —— 采纳
**回应**：Idempotency-Key 未消费属实（并入 RS-03 幂等存储）；契约-路由-权限码-前端调用
四向一致性检查加进 CI（随 RS-11 docs pass 一起处理 README/YAML 范围漂移与门户基路径疑点）。

### C3 大表分页与容量 —— 采纳（排期至数据到量）
**回应**：认同 offset+COUNT 在 500 万/7000 万行会线性恶化。落点 RS-10：product/listing/
audit/order/feed_item 改 keyset cursor、total 可选/估算——**触发闸门=真实数据大规模迁入前**
（当前全部千行级，先记账不空转）；容量模型用 RS-04 导入 manifest 的真实 row width 数据来算。

### C4 财务模型 —— 采纳（设计期修正，零现码成本）
**回应**：认同"覆盖式月物化不是账"。订单/财务域尚未开建（R4/R5），趁未写码把
immutable `financial_event`/`ledger_entry`（利润=投影）+ 汇率显式 base/quote/公式/锁定时刻/
舍入规则写进 001 对应章节修订——建在图纸上比返工便宜一百倍，这条来得正是时候。

### C5 安全与灾备 —— 采纳
**回应**：并入 RS-02：refresh token localStorage→HttpOnly Secure cookie（门户/团队上线闸门）、
TLS、备份加密+恢复演练纳入运维工单、RPO/RTO/断电/磁盘满演练定义。

### C6 决策与规格漂移 —— 采纳（我来，本周）
**回应**：属实——D-Q55 已把向量降级但 001/R2-02 文本仍留硬依赖表述。落点 RS-11（Fable
亲自做，specs 单一编辑权）：漂移文本修正 + 历史文档加 `superseded_by` 标注 + DECISION-FORM
增加"决策→被修订文档→工单"追踪列；"CI 检测已被修订仍标 active"先做轻量清单核对，不上工具。

---

## Top-3 排序：评审 vs Fable

评审 top-3：① outbox+幂等 ② 租户+部署安全 ③ 摄取重构+裁决门。
**Fable 排序（按暴露时间而非纯严重度）**：
1. **RS-04 摄取升级**——数据导入是 Owner 下一个实际动作，14M 商标现通道搬不动，暴露面在本周；
2. **RS-03 outbox+幂等**——严重度最高，但暴露面在真实渠道写入（A152 L2）打开时，设为该验收的硬闸门；
3. **RS-01/02 安全加固**——设为多团队上线/门户对外的硬闸门。
三者都注册 P0 并绑定明确闸门，殊途同归，无实质分歧。

## 当日修复（A4 fail-closed）

- `0012_product_needs_review.py`：product.status 增 `needs_review`（audit_run.verdict 原生已支持）
- `pipeline.coerce_l3_result`：bad JSON / 非法 verdict → `needs_review`（不再默认 pass）；
  is_real_brand 强制翻案同时覆盖 pass 与 needs_review
- `audit/service.audit_one`：needs_review 写 `llm_needs_review` 软命中（含 parse_error/政策版本），
  product.status 联动 `needs_review`
- 前端：needs_review 橙色标签 + 可重审
- 测试 3 新增/2 改写，后端 122 全绿

## 新注册工单（RS 系列 = Review-Sourced Round-1）

| 工单 | 内容 | 优先级 / 闸门 |
|---|---|---|
| RS-01 | 租户隔离加固（独立 DB 角色替代可自设 GUC、复合 FK、FORCE RLS、跨团队必败测试） | P0 / 多团队上线 |
| RS-02 | 部署与灾备加固（默认密钥硬失败、端口收敛、HTTPS 反代、备份加密+恢复演练、迁云演练） | P0 / 团队经营·R2#6 |
| RS-03 | transactional outbox + 幂等命令（含 Idempotency-Key 消费、audit LLM 出行锁） | P0 / 真实渠道写入 |
| RS-04 | 摄取通道升级（COPY staging、set-based merge、manifest/checksum、refdata_revision、source registry、断言账本能力） | P0 / 立即（数据将至） |
| RS-05 | 反馈裁决门（observation→candidate→adjudication→effective_rule + shadow/canary，升级 DG4） | P1 |
| RS-06 | 人工复核工作流（review_case/decision + 待办箱投影 + needs_review 承接 UI） | P1 |
| RS-07 | worker 信任边界（kind/source 过滤、capacity 限量、租约续租分级） | P1 |
| RS-08 | LLM 预算闸门（事前原子预留 + cached_tokens/pricing_version 实测） | P1 |
| RS-09 | 审核决策 manifest + 坏响应缓存失效 | P1 |
| RS-10 | keyset 分页 + 容量模型 | P2 / 大数据迁入前 |
| RS-11 | 决策-规格漂移清理（superseded_by、追踪矩阵、契约四向检查） | P1 / Fable 本周 |
