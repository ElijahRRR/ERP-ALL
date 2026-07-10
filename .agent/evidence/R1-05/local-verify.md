# R1-05 验证证据（沙盒真实全栈：PG16.13 + uvicorn + vite dev，2026-07-10）

| 检查 | 结果 |
|---|---|
| 后端 29 测试×2（验收流：建团队→模板角色复制→建成员→赋角色→登录→越权403→审计留痕） | ✅ |
| 前端 eslint + tsc + vite build | ✅ |
| 契约 codegen（openapi-typescript → src/api/schema.d.ts，pnpm gen:api） | ✅ 禁 mock-first 达成 |
| **E2E 冒烟（Playwright + 预装 Chromium，真实 HTTP 全链路）** | ✅ e2e/smoke.mjs 全过 |
| — 登录页/超管工作台/成员管理/角色权限抽屉/审计日志 可用并截图 | ✅ shots/01~05 |
| — 普通成员菜单按权限裁剪（无管理项）+ 越权直调 /users=403 | ✅ shots/06 + 断言 |
| 截图已发送 Owner | ✅ 6 张 |

踩坑记录：AntD 双汉字按钮自动插空格（"登 录"/"保 存"）→ Playwright 匹配须用 /登\s*录/；
CTE 同语句快照对 RLS 子查询不可见（建团队复制角色拆两条语句）；TestClient 伪 IP 不入 inet 列。
