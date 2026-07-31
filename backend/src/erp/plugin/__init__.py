"""采购插件机器面（R2-13）。

本包承载「插件来拉、插件来报」的第四认证域（`X-Plugin-Instance` + `X-Plugin-Token`
双头部，非 JWT）。**13b 只落 `auth.py` 的令牌原语**（签发端点要用），六个契约端点、
principal 解析与业务服务随 13a/13d 补进本包。
"""
