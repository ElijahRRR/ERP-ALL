"""A152 实测回归（2026-07-15）：店铺出口代理为 SOCKS5（防关联铁律，一店一固定出口 IP），
httpx 走 SOCKS 需要 socks extra（socksio）——缺了会在网关建传输时就抛
ImportError（部署机 API 镜像实测，请求未发出即失败）。

沙盒测试全程 MockTransport 替身、从不构造真实代理传输，故此缺口只有真机暴露；
本用例用 socks5 代理 URL 实际构造 httpx 传输（httpx 在构造点强制 socksio），
把 httpx[socks] 钉死为正式依赖。
"""

import httpx

from erp.channel.gateway.client import WalmartGateway


def test_socks5_proxy_transport_constructible() -> None:
    gw = WalmartGateway()
    for scheme in ("socks5", "socks5h"):
        transport = gw._default_transport(f"{scheme}://user:pass@127.0.0.1:1080")
        assert isinstance(transport, httpx.AsyncBaseTransport)


def test_http_proxy_and_direct_still_fine() -> None:
    gw = WalmartGateway()
    assert isinstance(gw._default_transport("http://127.0.0.1:8888"), httpx.AsyncBaseTransport)
    assert isinstance(gw._default_transport(None), httpx.AsyncBaseTransport)
