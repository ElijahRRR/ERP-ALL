"""插件实例令牌的散列链（R2-13 13a 认证域；13b 先落签发侧要用的两个原语）。

## 为什么走散列链而不是加密链

仓内有两条凭证链，用途相反：

| 链 | 代表 | 存的是什么 | 用途 |
|---|---|---|---|
| **散列** | 本文件、`scrape/service.py:37` | 只存 sha256 | 我方要拿来**验**的凭证 |
| 加密 | `channel/service.py` 的 `pgp_sym_encrypt` | 可解回明文 | 我方要拿去**用**的凭证 |

插件令牌属前者——ERP 只需判断「来者是不是它」，永远不需要把它取回来用。
图纸 `07:280` 也写的是 `token_hash`。**任何时候都不要把这一列改成可解密存储**，
那会让一次库泄漏直接等于「以买家身份下单」的能力泄漏。

## 明文的生命周期

`mint_token()` 生成 → 写入库的只有 `token_digest()` 的结果 → 明文**只在签发端点的
响应体里出现一次**，此后 ERP 侧任何地方（列表端点、审计快照、日志）都取不到。
遗失只能吊销后重新签发。这条纪律的落点在 `order/buyer_account.py::issue_plugin_instance`。

> 13a 补进本文件：`PluginPrincipal` 数据类与 `authenticate_instance()`
> （按 `id` 取行 + `hmac.compare_digest`，失败一律同一错误码 401 不区分原因）。
"""

import hashlib
import secrets


def token_digest(token: str) -> str:
    """令牌 → sha256 十六进制串（入库形态）。与 `scrape/service.py::_token_digest` 同算法。"""
    return hashlib.sha256(token.encode()).hexdigest()


def mint_token() -> str:
    """生成一个新的实例令牌明文（32 字节熵，URL 安全）。同 `worker_node` 注册。"""
    return secrets.token_urlsafe(32)
