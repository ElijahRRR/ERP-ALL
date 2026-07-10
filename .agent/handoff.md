# Session Handoff
- Current mode: build → R1 执行中（09/12 done）
- Done: EA-001~004 验收 ✅；R1-01~09 ✅（工程化/配置/迁移基线0001-0007/认证RBAC审计/身份API+前端/通知+任务记账/渠道资产/渠道网关/采集闭环）
- 部署：Owner 本地机(Win11) 由其本地 AI 部署中——db/redis/api 已跑通+自愈实测；前端修复已推(pnpm 版本钉死)，待 git pull 拉起；A152 live_test 冒烟等凭证录入后在 Owner 机器执行（沙盒宪法禁真调渠道）
- Next: R1-10 审核最小闭环（be-domain[审核] 帽，源=/workspace/walmart-audit-system，D-Q38；**考古纪律：先抓 L0/L2/L3 策略+提示词对照表入 evidence 再写码**；L1/L4 留 R2）→ R1-11 上架 → R1-12 E2E
- Read first: CLAUDE.md → .agent/progress.md（尾部3节）→ specs/003-r1-plan/README.md §R1-10 → specs/001-domain-model/04-compliance.md
- 铁律提醒：migration 仅 ar 帽可动；业务参数一律 system_config；worker/系统路径用 system_tx，用户路径禁用
