# Task Definition
- Mode: build（R2 后半程——007 计划生效；动工顺序 2026-07-17 更新：
  **R2-11 → R2-07 → R2-12（与 RS-04D 同窗）→ R2-09 → R2-08 → R2-10**，
  FE-DESIGN Owner 触发制，R2-10 前置 RS-01/02）
- Task: **R2-12 合规数据供给持续化【L1】（与 RS-04D 断言账本同窗）——2026-07-23 立项开工
  （Owner 指令）**；R2-07 07b 开发面完成待验收②；R2-11 已整单关账（accepted）。
  - R2-12 范围（007 §R2-12 + review_list R2-12/RS-04D）：①RS-04D 断言账本（blacklist
    写路径，硬验收=blacklist_brand 真实跑通非空框架）②USPTO 日度自增量（部署机
    daily_update → RS-04A 通道 → refdata.trademark + revision 失效 + beat/告警）③TRO
    采集按日进 tro_case + 派生品牌断言 ④全店后台 SKU 拉取对账（GET /v3/items 翻页 ↔
    本地 listing）+ 报错回收黑名单候选（source 扩 error_recycle）⑤合规中心页。
    依赖：RS-04A ✅（14M 实测另行）；07c 邮件钩子后置不阻塞。
  - R2-11 ✅ **accepted（2026-07-23，PR #24/26/27/28 全合并）**：D-Q64 四点全落地——
    实时归组（入库即组，真机 B0DGTYRBZQ 实证）/ 自动路由与散品上架双档 / 批次原子性
    守卫 v2 / live 补挂成组（组 8 真机修复 feed#42 8/8）+ 组 6 子集全 live 成组。
    观察项遗留见 progress 2026-07-23 节（coerce enum 改写、spec 换版核实、
    自动降散品开关待 Owner 立单）。
  - 07b（本轮，PR 待开）：0033 brand_assignment 建表+占用/释放闭环+outbox 封店门控+
    suspension_reminder beat+店铺事件前端页+run_task 单跑工具+契约/runbook。评审 2 major
    全修（outbox drain 无门控、提醒 24h 窗架空 remind_days）。CI 全绿（457）。
  - **待 Owner（07b 验收②）**：按 runbook「封店工作流演练」——A152 造品牌占用 →
    前端登记 suspension（occurred_at 回填 ≥7 天前）→ 核对店铺 suspended/占用批量
    released → run_task suspension_reminder → 通知中心见提醒 → resolved 恢复。
  - 接续（按批准顺序）：07c 邮箱（需 Owner 提供 IMAP 凭证）→ R2-12（与 RS-04D 同窗）→ …
- R2-11 挂账：anchor 解锁通道、组上下文批量化 已随 2026-07-18 检修增量清偿（解锁端点
  POST /variant-groups/{id}/anchor/release + 批量 load_build_contexts + 同族历史分裂
  合并缺口修复 + 空维度 broken 判定增补）。余观察项：维度值过 coerce enum 改写
  （A152 实测关注）；spec 版本 5.0.20260304 换版窗口在线核实 per-PT variantAttributeNames。
- 全局挂账：R2-05 L2 发货补验（等 A152 真实来单）；R2-04 验收②模拟断连；钓鱼黑名单导入；
  erpAPI PR #2 待授权；售后前端页（returns/refund 部分随 07c；店铺事件页 07b 已交付）。
  已清偿：前端 schema.d.ts codegen（07b 随契约重生成，含 R2-05/06/07a/11 既往欠账）。
- Constraints: workflow discipline per CLAUDE.md；每增量 CI 绿（pytest/ruff[check+format]/mypy +
  pnpm lint/build）；业务参数一律 system_config；不绕过 walmart_client 语义直连渠道；
  fail-closed 底线；migration 仅 ar 帽；specs 正文只由云端 AI 落笔（007/图纸归审计侧，批注回传）。
- 真机验证流程（2026-07-18 Owner 拍板）：增量先在 PR 分支上由部署机验证（前置核验点
  分支 head），通过后 Owner 授权合并 main，合并后重建分支接着开发；部署机验完切回 main
  常驻；含迁移的增量若分支被弃须 alembic downgrade 归位。
- 本工作区环境：开发分支 claude/r2-03-launch-leg5n8（PR 按增量推），旧 erpAPI 仓挂载于 /home/user/erpAPI。
