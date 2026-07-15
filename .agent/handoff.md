# Session Handoff（2026-07-15 工作区迁移收口版）

> 本文档为旧工作区（绑 elijahrrr/erpapi 会话，实际开发在 /workspace/erp-all）收口交接。
> 新工作区应直接绑定 ElijahRRR/ERP-ALL，本仓 CLAUDE.md 与 .agent/ 将自动加载。

## 当前状态

- Current mode: build → R2 中段。R1 全部 accepted（2026-07-11 关账）；R2-01 采集引擎 accepted；
  **R2-02 审核弹药 accepted（2026-07-15，D-Q60，调整口径 90.3%）**——十轮对拍收敛全史与
  14 项修复见 `.agent/evidence/R2-02/acceptance-report.md`（本项目至今最重的一单）。
- 数据：五类审核弹药真数据全部在库（商标 4.44M live / 政策 37 / 类目映射 ~16k+复排回写 /
  pt_meta 7,008 / pt_spec 6,942 / 黑名单品牌 41,992+类目 11,810+ASIN 18,772+卖家 1,308）。
  迁移方法论（dump→PG17 暂存→选择性单表还原→投影 csv/jsonl→导入 CLI→import_job 验证）已被
  十轮实战反复验证，runbook 散见 progress.md 2026-07-13 起各节。
- Alembic head=0019；标准模型 deepseek-v4-flash（D-Q58，deepseek-chat 官方 2026-07-24 弃用）；
  L4 视觉不进流程（D-Q58）；llm.pricing 含缓存命中价（v4-flash 0.14/0.0028/0.28）。

## 队列（下一单起点）

- **R2-03 上架真实化【L2】**（下一单）：MPSetup v5 官方规格+AI属性填写+category_map+本地校验器
  +错误码灌入。验收=①dry-run 过官方 spec 校验(≥5 WPT) ②A152 真调 1 SKU→PROCESSED→live→截图→delist。
  注意：refdata.pt_spec 0019 只导了审核子集列（fields 全量列被略——上架需要时经 import 通道补）；
  旧 erpAPI 的 pt_templates_full.json(293M) 与 walmart_official_specs/(4.3G) 在 Owner 的 T7 备份
  pt_metadata/ 目录（数据迁移时刻意未动，上架素材专用）。
- 之后：R2-04 worker/beat 底座（✅完码 2026-07-15，余 A152 实测两条——evidence/R2-04/runbook.md）
  → R2-05 订单履约 → RS 系列按闸门
  （RS-03b ✅已完 2026-07-15，A152 闸门解除；RS-01/02=多团队/门户对外前）。

## 协作模式（重要）

- **部署机（Win11）本地 AI**：只跑部署/数据/对拍，不改码不 push。给它的指令要可整段粘贴、
  含铁律（绝不 pg_restore 进 erp_all；暂存用一次性 pgvector/pg17 容器；用毕删容器+匿名卷；
  不输出密钥）。它的回报质量很高（聚类/时间戳核验都靠它）。
- **源仓依赖**：/workspace/walmart-audit-system（旧审核系统源码）是旧工作区独有资产，
  十轮保真移植全靠逐行比对它。**新工作区若做移植类工作（L4 视觉、beat 协调、seller 爬虫等）
  需重新挂载该仓**；已移植部分的对照结论沉淀在 evidence 各 archaeology.md。
  旧 erpAPI 仓（/home/user/erpAPI）同理（walmart_client/上架旧实现在里面，R2-03 会用到）。

## 决策链关键节点（specs/000-founding/DECISION-FORM.md）

D-Q55（L1=映射表+LLM复排，非嵌入）→ D-Q56/57（外部评审两轮 46 条全采纳，RS 工单体系）→
D-Q58（v4-flash 定标+L4 不进流程）→ D-Q59（R2-02 验收口径=重采样路径A）→ D-Q60（R2-02 收账）。

## Owner 侧待办（未变）

路由器固定 IP（团队接入前）；rclone 异地备份（红线）；R2-03 验收②需 A152 真调窗口。

## Read first（新会话顺序）

CLAUDE.md → .agent/progress.md（尾部 5 节=R2-02 十轮全史）→ .agent/review_list.json（RS 闸门）
→ .agent/evidence/R2-02/acceptance-report.md → specs/005-r2-plan/README.md（R2-03 定义）
→ backend/src/erp/listing/（R1-11 骨架，R2-03 的地基）。

## 铁律提醒（不变）

migration 仅 ar 帽可动；业务参数一律 system_config；worker/系统路径用 system_tx；
沙盒永不真调渠道/不真抓 Amazon；specs 正文只由云端 AI 落笔；fail-closed 是合规底线
（R2-02 十轮三次拒学源仓宽松行为的先例在案，不为任何分数让步）。
