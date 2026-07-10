# R1 验收手册（Owner 部署机执行 = R1 完成判据）

> 前置：部署验收 7 项全过、前端可登录。整场约 30-45 分钟，建议录屏。
> 两段合一：①全链演示（本机 mock 不花钱不碰渠道）②A152 真调 1 SKU（真渠道）。

## 第一段：全链演示（照沙盒 E2E 重放，或直接看已交付截图）

沙盒已全自动跑通并出 11 张截图（.agent/evidence/R1-12/*.png）。你可以二选一：
- **省事**：看截图 + 在自己机器上手动点一遍主链（下面 5 步）
- **完整**：`git pull` 后在部署机重放 e2e 脚本（需 node；命令在 frontend/e2e/full-chain.mjs 头注）

手动主链 5 步（登录 admin）：
1. 采集作业 → 新建采集（填任意 ASIN）→ 看到作业与进度条
2. 产品库 → 等采集回传后（无真 worker 时可先跳过采集，直接看沙盒截图）
3. 产品行 → 审核 → 看 verdict 与「审核详情」抽屉（需配置 LLM key：backend/.env 加
   `ERP_LLM_API_KEY=<deepseek key>`——这是唯一新增密钥，写文件不发聊天）
4. 分配上架 → 上架管理提交（gateway_mode=dry_run 时只出快照，安全）
5. 通知中心 → 看告警样式

## 第二段：A152 真调 1 SKU（R1-11 收尾，照 evidence/R1-11/a152-live-runbook.md）

关键动作复述：录 A152 凭证+代理 → is_test 勾上 → gateway_mode 切 live_test →
dry_run 先验快照 → allocate → submit → poll → Walmart 后台见品截图 → delist 收尾。

## 验收判据（PRD §8）

- [ ] 主链全程前端可见（采集进度/审核判定/上架状态/通知）
- [ ] A152 渠道后台可见该品 + delist 后下架
- [ ] listing 状态历史完整链（draft→…→live→delisted）
- [ ] 三条失败路径行为符合预期（审核拒绝/配额拒绝/feed 错误告警）

全部勾上 → 回复"R1 验收通过"，我把 R1 收口、开 R2 规划（选品/订单/邮件/定价/自动化）。
任何一步卡住：截图+响应原文发我。
