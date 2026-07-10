# Session Handoff
- Current mode: build → R1 收口（沙盒全完成; 待 Owner 验收=完成判据）
- Done: EA-001~004 验收 ✅；R1-01~10 ✅（工程化/配置/迁移基线0001-0008/认证RBAC审计/身份API+前端/通知+任务记账/渠道资产/渠道网关/采集闭环/审核闭环）
- 部署：Owner 本地机(Win11) 由其本地 AI 部署中——db/redis/api 已跑通+自愈实测；前端修复已推(pnpm 版本钉死)，待 git pull 拉起；A152 live_test 冒烟等凭证录入后在 Owner 机器执行（沙盒宪法禁真调渠道）
- Next: 等 Owner 验收回执（runbook 在 evidence/R1-12/）→ R1 关账 → 用 specs/005-r2-plan/DRAFT.md 与 Owner 对齐 R2 排期后注册工单
- Read first: CLAUDE.md → .agent/progress.md（尾部3节）→ specs/003-r1-plan/README.md §R1-11 → specs/001-domain-model/06-listing-pricing.md
- 铁律提醒：migration 仅 ar 帽可动；业务参数一律 system_config；worker/系统路径用 system_tx，用户路径禁用
