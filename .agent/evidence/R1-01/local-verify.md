# R1-01 本地验证证据（沙盒，2026-07-10）

| 检查 | 结果 |
|---|---|
| `uv sync`（backend，锁文件生成） | ✅ 深度 42 包 |
| `ruff check .` + `ruff format --check .` | ✅ All checks passed |
| `mypy`（strict） | ✅ no issues in 6 source files |
| `pytest` | ✅ 4 passed（healthz + 错误信封回归 + settings 优先级×2） |
| `pnpm install`（lockfile 生成）+ `pnpm lint` + `pnpm build` | ✅ eslint 通过；vite build 277KB(gzip 92KB) |
| `docker compose config -q` | ✅ 语法通过 |
| `alembic upgrade head --sql`（离线接线验证） | ✅ env.py/Settings 接线正常（0 revision 空跑） |
| `docker compose up`（容器实测） | ⚠️ 沙盒无 docker daemon——由 CI service 容器跑 migration 升降级演练；全栈 up 由 Owner 本机/ECS 首次部署时验证 |

CI 首跑结果见本目录 ci-first-run.md（推送后回填）。
