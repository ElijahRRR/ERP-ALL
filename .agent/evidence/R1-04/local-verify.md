# R1-04 本地验证证据（沙盒真 PG 16.13，2026-07-10）

| 检查 | 结果 |
|---|---|
| 权限矩阵：无token/坏token/门户aud/refresh-kind/tv吊销 → 401 | ✅ 5 用例 |
| 权限矩阵：有权限点 200 / 无权限点 403（信封含 permission） | ✅ |
| GUC 注入：团队A见代理≥1、团队B=0（同一 API 路由） | ✅ test_team_a_sees_proxy_team_b_zero |
| 登录服务：正确登录→refresh换新→连错5次锁定→锁定期正确密码也拒 | ✅ test_login_lockout_flow |
| 审计出口：AuditWriter 落行（actor/action/after 断言） | ✅ |
| X-Request-Id 中间件 | ✅ |
| migration 0005（locked_until + auth_record_login SECURITY DEFINER）升降级往返 | ✅ |
| ruff / mypy strict / pytest 24 全过 ×2（幂等） | ✅ |

实现要点：GUC 用 set_config(,,true)（SET LOCAL 不吃绑定参数——踩坑记录）；
login 自管事务（失败计数不随请求事务回滚）；用户查找仅经 SECURITY DEFINER 通道。
