# Task Definition
- Mode: build（R2 中段——R1 全部 accepted；R2-01/02/03 + RS-03 accepted；R2-04 完码待实测）
- **插单 HF-0716（2026-07-16 生产三缺陷整改，完码待部署验证）**：①采集/beat 停摆
  （beat 任务级超时护栏 + scrape 乒乓判死/收口/双告警 + UI 节点横幅）②MP_ITEM
  Invalid Date（日期清洗/校验全链 + endDate .000Z + 远期值入配置）③无订单页
  （部署滞后，指令补前端项）。取证与部署指令见 evidence/hotfix-20260716/。
- Task: 当前单 = **R2-05 订单履约最小闭环【L1→L2】**（订单拉取真实只读起步 →
  四检（限价/钓鱼/黑名单/重复）→ 采购单 → 确认发货（测试单））。
  并入 RS-03b 尾账：ship/refund-execute 幂等接入（run_idempotent 同一助手）。
- Acceptance（per review_list R2-05）：L1=真实订单只读拉取入库对账一致；L2=测试单全流程流转。
- 进度：5 增量全部完码（2026-07-16，313 pytest + 前端 lint/build 绿）。
  **余项=人工验收：L1 部署机拉单对账 + L2 A152 测试单全流程（evidence/R2-05/runbook.md）**。
- R2-04 挂账（不阻本单）：验收①机制已证实（自动轮询/回写/零人工/零错误），feed #36
  等 Walmart 终态自然闭环（1-2 天属渠道常态）；验收②「模拟断连回收」部署机实测未跑
  ——beat runbook 步骤 5。Owner 指示持续推进至需人工验收再停。
- ✅ R2-03 整单 accepted（2026-07-15，D-Q61）：验收①②均过；live/delist 真调并入首次真实运营发布
  ——需 Owner 窗口（runbook R1-11/a152-live-runbook.md + 部署机指令任务 4 已更新）。
- Constraints: workflow discipline per CLAUDE.md；每增量 CI 绿（pytest/ruff[check+format]/mypy）；
  业务参数一律 system_config；不绕过 walmart_client 语义直连渠道；fail-closed 底线（D-Q60 先例）；
  migration 仅 ar 帽；specs 正文只由云端 AI 落笔。
- 本工作区环境：开发分支 claude/r2-03-launch-leg5n8（PR 按增量推），旧 erpAPI 仓挂载于 /home/user/erpAPI。
