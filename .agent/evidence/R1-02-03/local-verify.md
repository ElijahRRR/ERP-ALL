# R1-02 + R1-03 本地验证证据（沙盒真 PG 16.13，2026-07-10）

| 检查 | 结果 |
|---|---|
| `alembic upgrade head`（0001→0004） | ✅ app schema 20 表/分区/函数/策略全建 |
| `alembic downgrade base` → `upgrade head` 往返 | ✅ |
| RLS：团队A建代理→团队B查=0→超管可见 | ✅ test_team_isolation |
| RLS：无 GUC 查询=0 行 | ✅ test_no_guc_sees_nothing |
| RLS：跨团队 INSERT 被 WITH CHECK 拒绝 | ✅ test_cross_team_insert_rejected |
| audit_log：INSERT 可、UPDATE/DELETE 权限拒绝 | ✅ test_insert_ok_update_delete_denied |
| 认证通道：直查被 RLS 挡、SECURITY DEFINER 函数可查 | ✅ test_login_lookup_without_guc |
| 种子：36 权限点 / 7 模板角色 / walmart_us / 分区≥4 | ✅ test_permissions_roles_channel |
| ConfigService：default→system→team 优先级链 | ✅ test_priority_chain |
| ConfigService：TTL 缓存 + invalidate | ✅ test_cache_and_invalidate |
| ruff / mypy(strict) | ✅ |
| 幂等性：pytest 连续两跑 12 passed | ✅ |

偏离记录：portal_account 延后至 R2#6（purchaser 外键依赖，见 0002 文件头）。
