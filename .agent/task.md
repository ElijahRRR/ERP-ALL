# Task Definition
- Mode: build（R2 后半程——007 计划生效；动工顺序 2026-07-17 更新：
  **R2-11 → R2-07 → R2-12（与 RS-04D 同窗）→ R2-09 → R2-08 → R2-10**，
  FE-DESIGN Owner 触发制，R2-10 前置 RS-01/02）
- Task: **R2-07 07b 封店工作流——开发面完成待验收②；R2-11 验收②已过、①等 Walmart 回执**
  （Owner 指令：workflow 模式持续开发到人工验收为止）。
  - R2-11：增量1/2/2.5 + 排障（worker 变体降级页防御，PR #24）均已合并；真机四 ASIN 收敛
    1 active 组（group 6）、缺员拒绝演练通过（验收②✅）；**验收① = 整组提交后等 Walmart
    variant group live 确认**，回执后整单收账。
  - 07b（本轮，PR 待开）：0033 brand_assignment 建表+占用/释放闭环+outbox 封店门控+
    suspension_reminder beat+店铺事件前端页+run_task 单跑工具+契约/runbook。评审 2 major
    全修（outbox drain 无门控、提醒 24h 窗架空 remind_days）。CI 全绿（457）。
  - **待 Owner（07b 验收②）**：按 runbook「封店工作流演练」——A152 造品牌占用 →
    前端登记 suspension（occurred_at 回填 ≥7 天前）→ 核对店铺 suspended/占用批量
    released → run_task suspension_reminder → 通知中心见提醒 → resolved 恢复。
  - 接续（按批准顺序）：07c 邮箱（需 Owner 提供 IMAP 凭证）→ R2-12（与 RS-04D 同窗）→ …
- R2-11 挂账（随验收/后续复审）：anchor 首发即败人工解锁口径（runbook 已载 SQL）；
  组上下文批量化（性能 minor）；维度值过 coerce enum 改写观察项（A152 实测关注）；
  spec 版本 5.0.20260304 换版窗口在线核实 per-PT variantAttributeNames。
- 全局挂账：R2-05 L2 发货补验（等 A152 真实来单）；R2-04 验收②模拟断连；钓鱼黑名单导入；
  erpAPI PR #2 待授权；售后前端页（returns/refund 部分随 07c；店铺事件页 07b 已交付）。
  已清偿：前端 schema.d.ts codegen（07b 随契约重生成，含 R2-05/06/07a/11 既往欠账）。
- Constraints: workflow discipline per CLAUDE.md；每增量 CI 绿（pytest/ruff[check+format]/mypy +
  pnpm lint/build）；业务参数一律 system_config；不绕过 walmart_client 语义直连渠道；
  fail-closed 底线；migration 仅 ar 帽；specs 正文只由云端 AI 落笔（007/图纸归审计侧，批注回传）。
- 本工作区环境：开发分支 claude/r2-03-launch-leg5n8（PR 按增量推），旧 erpAPI 仓挂载于 /home/user/erpAPI。
