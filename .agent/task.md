# Task Definition
- Mode: build（R2 中段——R1 全 accepted；R2-01/02/03/04/05 + RS-03 主体 accepted；HF-0716 整单闭环）
- Task: 当前单 = **R2-06 定价引擎最小闭环**（策略配置 build/match 分套 D-Q3/23 →
  allocate/submit 自动算价 → 批量重定价预览 → PRICE_AND_PROMOTION 同步管道（6/day 聚合，
  outbox 三段式）→ 限价检联动 + 前端定价面板）。顺带收 SM-0716 小账三件。
- Acceptance（per review_list R2-06）：A152 真机改价经管道同步成功+影子对拍；
  新建 listing 自动带策略价；CI 全绿。
- 里程碑注记：2026-07-16 首件真实 UPC 商品全链上架 live（listing #46 M0002418，
  feed #37 自动轮询闭环）——采集→审核→构建→提交→轮询→live 全自动实证。
- 挂账（不阻本单）：R2-05 L2 发货补验（等 A152 真实新单）；R2-04 验收②模拟断连；
  钓鱼黑名单导入激活 phishing 检。
- Constraints: workflow discipline per CLAUDE.md；每增量 CI 绿（pytest/ruff[check+format]/mypy）；
  业务参数一律 system_config；不绕过 walmart_client 语义直连渠道；fail-closed 底线；
  PRICE_AND_PROMOTION 6/day 与 PUT /v3/price 100/hour 限额是硬约束（限流表已含）；
  migration 仅 ar 帽；specs 正文只由云端 AI 落笔。
- 本工作区环境：开发分支 claude/r2-03-launch-leg5n8（PR 按增量推），旧 erpAPI 仓挂载于 /home/user/erpAPI。
