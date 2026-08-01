"""采购插件端点组的定向 CORS（R2-13 13e 前半，联调必需件）。

fork 后的插件是 **MV3 content script**：它的跨域 fetch 按**页面源**（amazon 站点）
执行 CORS 规则——MV3 起 `host_permissions` 不再豁免 content script 的跨域请求，
厂商后端必然开着 CORS 才跑得起来；ERP 不开，插件在真机上一条请求都发不出。
curl 与 CI 看不见这层（CORS 是浏览器端强制），所以它必须作为契约的一部分被
测试钉住，而不是等真机 401/血压才发现。

**只对 `/api/v1/purchase-plugin/` 前缀放行，绝不全局**：单人模式下 `/me` 等端点
免凭证即出数据，全局对 amazon 源放行等于把这些响应体开放给 amazon 页面上的
任意脚本读取。本组自身有 X-Plugin-Token 闸——放行的只是「浏览器允许读响应」，
不是「无凭证可取数」；401 响应也要带 CORS 头，否则插件侧只能看到一个
不可读的网络错误，分不清「token 错」与「服务不可达」。

不用 Starlette `CORSMiddleware` 的原因：它按源不按路径，要按路径就得挂子应用
或包两层 app，都比这几十行更重、更难在审查里看清放行面。
"""

import re

PLUGIN_PREFIX = "/api/v1/purchase-plugin/"

# https 且主机以 amazon.com / amazon.ca / amazon.co.jp 收尾（任意子域，含裸域）。
# 锚定写法防 `amazon.com.evil.io` 这类前缀仿冒；不放 http——amazon 全站 https。
_ORIGIN_RE = re.compile(r"^https://([a-z0-9-]+\.)*amazon\.(com|ca|co\.jp)$")

_ALLOW_METHODS = "GET, POST, OPTIONS"
# Content-Type 必须列上：插件 POST 带 application/json，属「非简单头」触发预检。
_ALLOW_HEADERS = "X-Plugin-Token, Content-Type"


def allow_origin(origin: str | None) -> bool:
    """该 Origin 是否在放行面内（None/空/非 amazon 三站一律否）。"""
    return origin is not None and _ORIGIN_RE.fullmatch(origin) is not None


def actual_headers(origin: str) -> dict[str, str]:
    """实际请求（GET/POST）响应上补的 CORS 头。回声具体 Origin 而非 `*`。"""
    return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}


def preflight_headers(origin: str, *, private_network: bool = False) -> dict[str, str]:
    """预检（OPTIONS）204 响应头。Max-Age 600：插件逐单循环里别每发必预检。

    `private_network`＝本次预检带了 `Access-Control-Request-Private-Network: true`。
    真机部署形态是 `https://www.amazon.com` 页面 → `http://127.0.0.1:8000`，落在
    Chrome 的 Private Network Access（公网上下文打私网/环回）口径里：Chrome 的预检会
    额外带该请求头，响应不回 `Access-Control-Allow-Private-Network: true` 就**整条被
    浏览器拦掉**——与「插件一条请求都发不出、还报不出为什么」同一现象。CI/curl 都看不见
    这层（浏览器端强制），只有真机 Chrome 会触发；故按「见到即回」放行本组、不主动声张。
    """
    headers = {
        **actual_headers(origin),
        "Access-Control-Allow-Methods": _ALLOW_METHODS,
        "Access-Control-Allow-Headers": _ALLOW_HEADERS,
        "Access-Control-Max-Age": "600",
    }
    if private_network:
        headers["Access-Control-Allow-Private-Network"] = "true"
    return headers
