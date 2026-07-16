# Task Definition
- Mode: build（R2 中段——R1 全 accepted；R2-01/02/03/05/06 + RS-03 + HF-0716 + INFRA-0716
  全 accepted；R2-04 余验收②挂账）
- Task: **R2-07 售后域（退货/退款）最小闭环**（Owner 2026-07-16 点名"下一步推进开发售后"）。
  考古已完成（.agent/evidence/R2-07/archaeology.md）。增量拆四：
  ①读闭环（0030 迁移 channel_return 头+行+状态历史 / return_pull beat / 查询端点）
  ②refund_request 三档记账/审批（D-Q29）③渠道退款执行（outbox return_refund + verify-back +
  is_test 灰度）④前端售后页 + specs 注记 + 收尾。当前推进：增量1。
- 验收：①L1 真机拉回退货单头行落库对拍旧仓 27 列口径；②三档流转测试全绿 + 渠道执行
  dry-run 证据（真实退款执行挂账等 A152 真实退货单）；③CI 全绿。
- 挂账（触发条件到达时处理，不阻新单）：R2-05 L2 发货补验（等 A152 真实来单）；
  R2-04 验收②模拟断连；钓鱼黑名单导入激活 phishing 检；erpAPI PR #2 待合并授权；
  前端 schema.d.ts codegen 重生成（SM-0716③注记）。
- Constraints: workflow discipline per CLAUDE.md；每增量 CI 绿（pytest/ruff[check+format]/mypy +
  pnpm lint/build）；业务参数一律 system_config；不绕过 walmart_client 语义直连渠道；
  fail-closed 底线；migration 仅 ar 帽；specs 正文只由云端 AI 落笔。
- 本工作区环境：开发分支 claude/r2-03-launch-leg5n8（PR 按增量推），旧 erpAPI 仓挂载于 /home/user/erpAPI。
