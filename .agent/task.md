# Task Definition
- Mode: build（R2 中段——R1 全部 accepted；R2-01/R2-02 accepted；当前单 = R2-03）
- Task: R2-03 上架真实化【L2】per specs/005-r2-plan/README.md —— MPSetup v5 官方规格 + AI 属性填写
  + category_map 接线 + 提交前本地校验器 + listing_error_catalog 错误码灌入。
- Acceptance:
  - ①dry-run 产物通过官方 spec 校验（≥5 个不同 WPT 的产品）——沙盒可完成；
  - ②A152 真调 1 SKU → PROCESSED → live 回写 → Walmart 后台截图 → delist 收尾——需 Owner 窗口（部署机）。
  - 前置闸门：RS-03b（channel 写路径 outbox+幂等）挂"A152 L2 真实渠道写入之前"——验收②之前必须完成。
- Constraints: workflow discipline per CLAUDE.md；每增量 CI 绿（pytest/ruff[check+format]/mypy）；
  业务参数一律 system_config；不绕过 walmart_client 语义直连渠道；fail-closed 底线（D-Q60 先例）；
  migration 仅 ar 帽；specs 正文只由云端 AI 落笔。
- 数据注意（handoff）：refdata.pt_spec 0019 只导了审核子集列（fields 全量列需经 import 通道补）；
  pt_templates_full.json(293M) 与 walmart_official_specs/ 全量(4.3G) 在 Owner T7 备份 pt_metadata/。
- 本工作区环境：开发分支 claude/r2-03-launch-leg5n8（PR #1 draft），旧 erpAPI 仓挂载于 /home/user/erpAPI。
