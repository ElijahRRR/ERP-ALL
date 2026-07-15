# Task Definition
- Mode: build（R2 中段——R1 全部 accepted；R2-01/R2-02 accepted；R2-03 验收①已批）
- Task: 当前单 = **RS-03b channel 写路径 outbox+幂等**（R2-03 验收② 的前置硬闸门）。
  R2-03 上架真实化【L2】主体已完：验收① 2026-07-15 Owner 批准（真数据 9/12 过、5 WPT）。
- Acceptance（RS-03b，per review_list RS-03）：同key同payload同结果/异payload 409；
  外部成功后DB回写前崩溃→verify-back不重复提交(故障注入)；lease/fencing 拒迟到 worker；
  同store/SKU命令有序；HTTP期间行锁已释放(实证)；outbox payload 凭证/PII 脱敏。
  —— 2026-07-15 全部测试化达成（evidence/RS-03b/acceptance.md），闸门解除。
- 余项（R2-03 收尾）：验收② A152 真调 1 SKU → PROCESSED → live → 截图 → delist
  ——需 Owner 窗口（runbook R1-11/a152-live-runbook.md + 部署机指令任务 4 已更新）。
- Constraints: workflow discipline per CLAUDE.md；每增量 CI 绿（pytest/ruff[check+format]/mypy）；
  业务参数一律 system_config；不绕过 walmart_client 语义直连渠道；fail-closed 底线（D-Q60 先例）；
  migration 仅 ar 帽；specs 正文只由云端 AI 落笔。
- 本工作区环境：开发分支 claude/r2-03-launch-leg5n8（PR 按增量推），旧 erpAPI 仓挂载于 /home/user/erpAPI。
