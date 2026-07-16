# Task Definition
- Mode: build（R2 中段——R1 全 accepted；R2-01/02/03/05/06 + RS-03 + HF-0716 + INFRA-0716
  全 accepted；R2-04 余验收②挂账）
- Task: **无活跃工单——等 Owner 点名下一单**。候选（云端已推荐顺序）：
  ①售后单（退货/退款域，旧仓有现成语义可考古）②选品 ③采购方门户（portal）。
- 最近关账：R2-06 定价引擎（2026-07-16 验收①②双过，真机改价 27.16→43.98 全链实证）；
  INFRA-0716 生产前端伺服构建产物（stale 模块事故根治，部署机+浏览器双验收）。
- 挂账（触发条件到达时处理，不阻新单）：R2-05 L2 发货补验（等 A152 真实来单）；
  R2-04 验收②模拟断连；钓鱼黑名单导入激活 phishing 检；erpAPI PR #2 待合并授权；
  前端 schema.d.ts codegen 重生成（SM-0716③注记）。
- Constraints: workflow discipline per CLAUDE.md；每增量 CI 绿（pytest/ruff[check+format]/mypy +
  pnpm lint/build）；业务参数一律 system_config；不绕过 walmart_client 语义直连渠道；
  fail-closed 底线；migration 仅 ar 帽；specs 正文只由云端 AI 落笔。
- 本工作区环境：开发分支 claude/r2-03-launch-leg5n8（PR 按增量推），旧 erpAPI 仓挂载于 /home/user/erpAPI。
