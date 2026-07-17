# Task Definition
- Mode: build（R2 后半程——007 计划生效；动工顺序 2026-07-17 更新：
  **R2-11 → R2-07 → R2-12（与 RS-04D 同窗）→ R2-09 → R2-08 → R2-10**，
  FE-DESIGN Owner 触发制，R2-10 前置 RS-01/02；R2-12=合规数据供给持续化（007 4a57b68 立单））
- Task: **R2-11 变体组·增量1 归组闭环**（当前；07a 已收账 2026-07-17，验收①真机 36 单对账一致）。
  D-Q63 三拍板已入宪法表：anchor_store_id 列 / 渠道中立 VG{组id} / 整组拒绝。
  - 增量1（归组闭环）完码：0032 anchor 列 + 归组服务（twister 素材，broken 判定 v1）
    + 契约端点三件 + beat variant_group_sync + 测试。范围修正：变体三表 0007 已建（007 论断
    更正批注已被采纳），无需建表。
  - 待办：增量2 spec 构建器变体段+整组守卫+anchor 锁定（D-Q63）→ 增量3 A152 真组 L2 验收。
    R2-07 余 07b 封店 / 07c 邮箱，排 R2-11 后。
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
