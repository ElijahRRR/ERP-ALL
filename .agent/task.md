# Task Definition
- Mode: build（R2 中段——R1 全 accepted；R2-01/02/03/05/06 + RS-03 + HF-0716 accepted；
  R2-04 余验收②挂账）
- Task: 当前单 = **INFRA-0716 生产前端改伺服构建产物**（根治 dev vite stale 模块事故：
  HF-0716①「无订单页」、FE-0716「翻页菜单消失」两起同源）。
- Acceptance（per review_list INFRA-0716）：生产 frontend 容器伺服构建产物；
  部署=重建镜像；不再出现 stale 模块类症状。
- 进度：**完码待部署机验证**（2026-07-16）。frontend/Dockerfile（多阶段 node→nginx）+
  nginx.conf（SPA 回退/API 反代/assets immutable/index no-store）+ compose 改造
  （frontend=生产静态默认启用，旧 vite→frontend-dev profile dev）+ 活文档同步
  （Makefile、windows.md）。本地 nginx 真实 E2E 冒烟四项全过；镜像构建留部署机。
- 挂账（不阻本单）：R2-05 L2 发货补验（等 A152 真实新单）；R2-06 验收②真机改价
  （Owner 择时）；R2-04 验收②模拟断连；钓鱼黑名单导入激活 phishing 检；
  前端 schema.d.ts codegen 重生成（SM-0716③注记）。
- Constraints: workflow discipline per CLAUDE.md；每增量 CI 绿（pytest/ruff[check+format]/mypy +
  pnpm lint/build）；业务参数一律 system_config；不绕过 walmart_client 语义直连渠道；
  fail-closed 底线；migration 仅 ar 帽；specs 正文只由云端 AI 落笔。
- 本工作区环境：开发分支 claude/r2-03-launch-leg5n8（PR 按增量推），旧 erpAPI 仓挂载于 /home/user/erpAPI。
