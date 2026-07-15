# Task Definition
- Mode: build（R2 中段——R1 全部 accepted；R2-01/02/03 + RS-03 accepted）
- Task: 当前单 = **R2-04 worker/beat 底座【L0→L1】**（schedule 表驱动：feed 自动轮询/
  采集回收兜底/llm_cache LRU/GTIN 水位/预算闸告警版；Redis pubsub 配置广播；compose beat 启用）。
  并入 RS-03b 尾账：outbox drain 周期化、retire verify_pending 对账、api_idempotency 清扫。
- Acceptance（per review_list R2-04）：A152 提交后无人工点查自动轮询回写；模拟断连自动回收。
- 进度：4 增量完码全绿（2026-07-15，289 pytest）；两条验收已测试化锚定；
  **余项=部署机启 beat 后 A152 实测**（evidence/R2-04/runbook.md 步骤 4/5，指令可整段粘贴）。
- 考古：.agent/evidence/R2-04/archaeology.md（2026-07-15，4 增量拆分 + 设计决策 8 条）。
  范围注记：erp.worker 队列消费者无生产者暂不启用；RS-08 事前预算预留不并入本单。
- ✅ R2-03 整单 accepted（2026-07-15，D-Q61）：验收①②均过；live/delist 真调并入首次真实运营发布
  ——需 Owner 窗口（runbook R1-11/a152-live-runbook.md + 部署机指令任务 4 已更新）。
- Constraints: workflow discipline per CLAUDE.md；每增量 CI 绿（pytest/ruff[check+format]/mypy）；
  业务参数一律 system_config；不绕过 walmart_client 语义直连渠道；fail-closed 底线（D-Q60 先例）；
  migration 仅 ar 帽；specs 正文只由云端 AI 落笔。
- 本工作区环境：开发分支 claude/r2-03-launch-leg5n8（PR 按增量推），旧 erpAPI 仓挂载于 /home/user/erpAPI。
