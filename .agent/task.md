# Task Definition
- Mode: build（R2 后半程——007 MVP 补全计划已落 main：R2-07~11 + FE-DESIGN 补单，
  进度口径改按 PRD §8 九模块 + RS 闸门汇报）
- Task: **R2-07 售后与店铺事件域**（Owner 点名"推进售后"；定义以 specs/007 为准，三片：
  07a returns 只读闭环 / 07b 封店工作流 / 07c 邮箱最小闭环）。
  ✅ 07a 核心增量已完码（PR #18：0030 退货三表 + return_pull beat + 查询端点，CI 绿待合并）。
  待办：07a 收尾（refund_request 表落地 + A152 真机对账=验收①）→ 07b 封店
  （store_incident + 品牌占用批量释放 + beat 提醒=验收②）→ 07c 邮箱
  （IMAP 收件 + LLM 分类 mail_classify → incident+告警=验收③；完整客户端 MVP 后）。
  注意：退款三档 flow=refund 接线归 R2-09，本单 refund_request 只落图纸表结构。
- 撞号调和记录：开发侧在 007 落 main 前已按 Owner 口头点名立过本地 R2-07（退货/退款闭环），
  合并时以 007 定义为准；开发侧批注（旧仓独立 returns 脚本存在、台账 §13 出处）记于工单 finding。
- 挂账（触发条件到达时处理，不阻新单）：R2-05 L2 发货补验（等 A152 真实来单）；
  R2-04 验收②模拟断连；钓鱼黑名单导入激活 phishing 检；erpAPI PR #2 待合并授权；
  前端 schema.d.ts codegen 重生成（SM-0716③注记）。
- Constraints: workflow discipline per CLAUDE.md；每增量 CI 绿（pytest/ruff[check+format]/mypy +
  pnpm lint/build）；业务参数一律 system_config；不绕过 walmart_client 语义直连渠道；
  fail-closed 底线；migration 仅 ar 帽；specs 正文只由云端 AI 落笔（007 归审计侧，开发批注回传）。
- 本工作区环境：开发分支 claude/r2-03-launch-leg5n8（PR 按增量推），旧 erpAPI 仓挂载于 /home/user/erpAPI。
