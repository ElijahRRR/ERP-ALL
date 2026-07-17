# Task Definition
- Mode: build（R2 后半程——007 计划生效；动工顺序 Owner 已批：
  **R2-11 → R2-07 → R2-09 → R2-08 → R2-10**，FE-DESIGN Owner 触发制，R2-10 前置 RS-01/02）
- Task: **R2-07 售后与店铺事件域·07a 收尾**（当前）→ 收尾后**先开 R2-11 变体组**，再回 07b/07c。
  - 07a 代码面已齐：增量1 读闭环（PR #18 已合并）+ 增量2 refund_request 两档（PR #19 在途）
    + return_pull_verify 对账 harness（随 PR #19）。
  - 07a 余项 = A152 真机对账（验收①）：部署机升级后
    `exec api python -m erp.tools.return_pull_verify --store <A152_id> --pull-first`。
  - R2-11 变体组【L1→L2】：§03 variant_group/variant_member 图纸完备、代码零实现；
    建表迁移 + 采集端 parent ASIN 归组（source_parent_ref）+ spec 构建器变体段 + 组完整性守卫
    （broken 拒构建）。开工前照例先考古。
- 新情报入账（2026-07-17 审计工作区）：①动工顺序如上；②R2-08 闸门解除——§08 财务图纸已按
  immutable event ledger 重写（421f83d，financial_event/ledger_entry 追加式两层+显式汇率块+
  自然键过账幂等+冲销协议），建财务域以此为唯一图纸；③进度汇报统一 PRD §8 九模块分母
  （007 有对账表）；007 异议继续走批注（本轮批注已被核实采纳，11443af）。
- 挂账（触发条件到达时处理，不阻新单）：R2-05 L2 发货补验（等 A152 真实来单）；
  R2-04 验收②模拟断连；钓鱼黑名单导入激活 phishing 检；erpAPI PR #2 待合并授权；
  前端 schema.d.ts codegen 重生成；售后前端页（随 07b/07c 一并做或单列）。
- Constraints: workflow discipline per CLAUDE.md；每增量 CI 绿（pytest/ruff[check+format]/mypy +
  pnpm lint/build）；业务参数一律 system_config；不绕过 walmart_client 语义直连渠道；
  fail-closed 底线；migration 仅 ar 帽；specs 正文只由云端 AI 落笔（007/图纸归审计侧，开发批注回传）。
- 本工作区环境：开发分支 claude/r2-03-launch-leg5n8（PR 按增量推），旧 erpAPI 仓挂载于 /home/user/erpAPI。
