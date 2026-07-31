"""采购插件机器面（R2-13）。

本包承载「插件来拉、插件来报」的第四认证域（`X-Plugin-Instance` + `X-Plugin-Token`
双头部，非 JWT）。独立成包而不是塞进 `order/router.py`，是因为它有独立的认证域、
独立的事务形态（认证 `system_tx` / 业务 `ctx_tx`）与独立纪律（字段名保留厂商 camelCase、
响应带 `{code, data}` 兼容信封）——混进已 800+ 行的订单路由会把这三条负担压给每个读者。

| 文件 | 内容 |
|---|---|
| `auth.py` | 令牌散列链 + `PluginPrincipal` + 双头部校验 |
| `schemas.py` | 请求/响应模型（**字段名逐字保留厂商 camelCase**） |
| `service.py` | 六端点服务层（13a 通道与拉取语义；13d 补回填/异常/物流语义） |
| `router.py` | `plugin_router`（⛔ 故意不含 `updateBuyerCookie`，见该文件头注） |

派发算法**不在本包**：它是领域规则不是插件协议，落 `order/dispatch.py`；
买家账号与实例的管理面（UI 契约、JWT + 权限点）落 `order/buyer_account.py`。
"""
