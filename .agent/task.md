# Task Definition
- Mode: build（R2 后半程——007 计划生效；动工顺序 2026-07-17 更新：
  **R2-11 → R2-07 → R2-12（与 RS-04D 同窗）→ R2-09 → R2-08 → R2-10**，
  FE-DESIGN Owner 触发制，R2-10 前置 RS-01/02）
- Task: **R2-11 变体组——开发面全部完成，停在人工验收点**（Owner 指令：workflow 模式持续
  开发到人工验收为止）。
  - 增量1（归组闭环，PR #20）+ 增量2（构建器变体段+三闸整组守卫+anchor 原子锁，PR #21）
    均已合并；specs 注记与变体组运维 runbook 已入库。D-Q63 三拍板落地完毕。
  - **待 Owner（增量3 = L2 人工验收）**：①采集一组带变体的真实 Amazon ASIN（≥3 成员）；
    ②部署机 git pull + make up（0032 迁移自动跑）；③按 runbook「变体组运维·验收演练」：
    归组 → 审核 → 同批分配提交 → Walmart 后台确认 variant group live；④缺员拒绝演练
    （应见 VARIANT_GROUP_INCOMPLETE + 缺席成员明细）。回报后收账。
  - 验收后接续（按批准顺序）：R2-07 07b 封店 → 07c 邮箱 → R2-12（与 RS-04D 同窗）→ …
- R2-11 挂账（随验收/后续复审）：anchor 首发即败人工解锁口径（runbook 已载 SQL）；
  组上下文批量化（性能 minor）；维度值过 coerce enum 改写观察项（A152 实测关注）；
  spec 版本 5.0.20260304 换版窗口在线核实 per-PT variantAttributeNames。
- 全局挂账：R2-05 L2 发货补验（等 A152 真实来单）；R2-04 验收②模拟断连；钓鱼黑名单导入；
  erpAPI PR #2 待授权；前端 schema.d.ts codegen；售后前端页（随 07b/07c）。
- Constraints: workflow discipline per CLAUDE.md；每增量 CI 绿（pytest/ruff[check+format]/mypy +
  pnpm lint/build）；业务参数一律 system_config；不绕过 walmart_client 语义直连渠道；
  fail-closed 底线；migration 仅 ar 帽；specs 正文只由云端 AI 落笔（007/图纸归审计侧，批注回传）。
- 本工作区环境：开发分支 claude/r2-03-launch-leg5n8（PR 按增量推），旧 erpAPI 仓挂载于 /home/user/erpAPI。
