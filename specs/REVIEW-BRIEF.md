# ERP-ALL 整体方案 · 外部 AI 评审交接包（全项目版）

> **给 Owner**：你要引入的是**部署机上那个本地 AI**——它手里有完整仓库克隆，所以**不用你
> 复制粘贴任何文件**。你只需让它先 `git pull` 拉到最新，然后把本文件路径 `specs/REVIEW-BRIEF.md`
> 指给它，它照着"阅读地图"自己读全项目，再按"评审任务"产出意见。它的意见你原样转我
> （贴聊天 / 或让它开 draft PR），我逐条消化。

---

## 你（本地评审 AI）的角色

你是一名资深系统架构 / 数据工程 / 电商合规评审。你**不是来改代码或改文档的**——你的产出是
**架构层面的质疑、风险、更优解**。另一个 AI（Fable，云端开发）负责落码与整合方案；你负责
从不同角度把方案挑硬。**仓库里的 specs/*.md 是唯一事实源，只由 Fable 落笔定稿，你只提建议。**

## 第一步：拉最新 + 按此地图通读（你有完整仓库）

```bash
git pull origin main          # 先拉到最新
```

**阅读顺序（从"是什么"到"怎么做"到"现状"）**：

1. `README.md` + `CLAUDE.md` — 项目是什么、铁律（Walmart API 只走 walmart_client、飞书 lark-cli）
2. `specs/000-founding/` — 立项愿景 + `DECISION-FORM.md`（D-Q1~D-Q55 全部关键决策与理由，**必读**）
3. `specs/001-domain-model/` — 领域模型（80 表列级设计，15 限界上下文；架构核心，重点看
   README + 00-conventions + 03-catalog + 04-compliance + 05-audit + 06-listing）
4. `specs/002-api-contract/openapi-v0.yaml` — API 契约草案
5. `specs/003-r1-plan/` + `specs/005-r2-plan/` — R1（已完成）/ R2（进行中）工单与验收
6. `specs/006-data-governance/` — **当前最热**：数据入库与持续维护架构（含 §9 活数据迁移、
   附录 L1 方法）+ 该目录 `REVIEW-BRIEF.md` 里更细的 8 个数据治理挑战点
7. `.agent/review_list.json` + `.agent/progress.md` — 已建什么、每单状态、逐 session 进展
8. 代码骨架（可选深入）：`backend/src/erp/`（audit / compliance / scrape / listing / channel 域）、
   `workers/`（采集引擎）

## 评审任务：分两层

### A 层 — 整体架构（务必覆盖）
1. **限界上下文划分**（001 的 15 上下文：identity/channel/catalog/scrape/audit/listing/order/
   notify/automation/…）合理吗？有无该拆该并、职责错位？
2. **多团队隔离用 PostgreSQL RLS + GUC**（team_id 行级安全）——这个隔离模型在"多店铺多团队 +
   超管跨团队"场景下够稳吗？有无绕过风险 / 性能隐患？
3. **本地单机部署（D-Q52）** vs 迁云的时机与代价——试点期全本地、上量前迁云，判断对吗？
4. **审核四层管道 L0→L2→L3（→L1 类目→L4 视觉）** 的分层与"确定性硬判断在前、LLM 语义在后 +
   provider prompt cache 省成本"设计——有无更优结构 / 漏判风险？
5. **采集 worker 拨入协议**（本地 worker 出站拨云端领任务、租约 attempt 兼 lease_epoch、
   FOR UPDATE SKIP LOCKED）——分布式任务分发的健壮性？
6. **成本控制**（LLM 输入哈希缓存 + usage 记账 + prompt 静态段吃 provider cache）够不够？

### B 层 — 数据治理（当前最热，见 specs/006-data-governance/REVIEW-BRIEF.md 的 8 点）
活数据迁移（水位增量追平散乱 Mac 源库）、飞书↔DB 双向、反馈闭环防误判固化、USPTO 几十G
取舍、溯源/软删/多源冲突、L1 映射+LLM 是否够。

### C 层 — 盲区
你认为**整个项目漏掉、或想当然、或将来会痛**的东西（技术债、扩展性、合规风险、运维单点）。

## 输出格式（便于 Fable 逐条回应）

按 A/B/C 分区、每条编号：**① 判断（认同/存疑/反对）② 理由 ③ 具体改法（增量，别重写方案）**。
最后给一份"如果只改 3 件事，改哪 3 件"的优先级排序。

---

## 回传方式（给 Owner）

**铁律**：仓库 specs/*.md 只由 Fable 一人编辑定稿；本地 AI 与你都只"提议"，避免两个 AI 并发
改同一文档打架。两种回传任选：

- **A. 贴聊天（推荐做讨论迭代）**：把本地 AI 的评审意见原样贴给 Fable。Fable 逐条回应（认同
  就改方案、存疑就摆理由让你裁决），一致的落进 specs。适合快速来回。
- **B. draft PR（推荐做定稿留痕）**：本地 AI 有仓库权限，可对某文档提 **draft PR** 或新增
  `specs/external-review-round-N.md`（**不要直接改 README.md 正文**，避免与 Fable 的编辑冲突）。
  Fable 看 diff、inline 逐条回复、把认可的整合进正文。

> 提醒：本地 AI 此前的部署纪律是"只部署不改码不 push 到 main"。评审是新角色，允许它**读全库 +
> 提 draft PR/评审文件**，但**仍不直接改 main 上的代码与 specs 正文**——它提议，Fable 整合。
