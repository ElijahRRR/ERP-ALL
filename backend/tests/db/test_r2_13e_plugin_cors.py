"""R2-13 13e：插件端点组定向 CORS——放行面 = `/purchase-plugin/` 前缀 × amazon 三站源。

| 判据 | 用例 |
|---|---|
| amazon 三站源（含子域）预检 204 + 三件套响应头
  | `test_preflight_allows_amazon_origins` |
| 非 amazon 源预检零 CORS 头（含前缀仿冒 `amazon.com.evil.io`、http 降级）
  | `test_foreign_origin_gets_no_cors` |
| 非插件路径带 amazon 源也零 CORS 头（放行面不外溢——单人模式 `/me` 免凭证出数）
  | `test_non_plugin_path_gets_no_cors` |
| 实际请求回声 Origin；**401 也带**（插件侧要能读到错误体，分清坏 token 与不可达）
  | `test_actual_request_echoes_origin_even_on_401` |
| 带对 token + amazon 源 ⇒ 200 信封 + CORS 头（正向全链）
  | `test_actual_request_with_token_succeeds` |

为什么这层必须存在：fork 插件是 MV3 content script，跨域 fetch 按页面源走 CORS，
`host_permissions` 不豁免；curl/CI 都看不见浏览器端的这道闸，不钉住它，真机上
插件一条请求都发不出、还报不出为什么（跨域失败对 JS 只是不透明网络错误）。
"""

import os
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.plugin.cors import allow_origin

SHARED_TOKEN = "r13e-cors-token-0123456789abcdef"
TEAM = "R2-13e CORS 测试团队"
PULL = "/api/v1/purchase-plugin/getNeedPurchaseOrders"
AMZ = "https://www.amazon.com"


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        # 共享通道团队归属走配置中心（17c 同款）；本模块只拉空列表，无需订单素材。
        conn.execute("DELETE FROM app.system_config WHERE key = 'procurement.plugin_team_id'")
        conn.execute(
            "INSERT INTO app.system_config (key, value) VALUES"
            " ('procurement.plugin_team_id', to_jsonb(%s::bigint))",
            (team_id,),
        )
    return {"team": team_id}


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> Iterator[TestClient]:
    os.environ["ERP_PLUGIN_SHARED_TOKEN"] = SHARED_TOKEN
    from erp.core.settings import get_settings

    get_settings.cache_clear()
    from erp.main import app

    try:
        with TestClient(app) as c:
            yield c
    finally:
        os.environ.pop("ERP_PLUGIN_SHARED_TOKEN", None)
        get_settings.cache_clear()


@pytest.mark.parametrize(
    "origin",
    [
        "https://www.amazon.com",
        "https://www.amazon.ca",
        "https://www.amazon.co.jp",
        "https://smile.amazon.co.jp",  # 子域也是插件真实运行面
        "https://amazon.com",  # 裸域
    ],
)
def test_preflight_allows_amazon_origins(client: TestClient, origin: str) -> None:
    r = client.options(
        PULL,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Plugin-Token",
        },
    )
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == origin
    assert "X-Plugin-Token" in r.headers["access-control-allow-headers"]
    for method in ("GET", "POST"):
        assert method in r.headers["access-control-allow-methods"]


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "https://amazon.com.evil.io",  # 前缀仿冒
        "https://notamazon.com",  # 无点前界仿冒
        "http://www.amazon.com",  # http 降级不放行
        "https://amazon.de",  # 站外域
    ],
)
def test_foreign_origin_gets_no_cors(client: TestClient, origin: str) -> None:
    assert not allow_origin(origin)  # 匹配函数本体
    r = client.options(PULL, headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
    # 预检短路不触发（走到路由层 405），且全程零 CORS 头——浏览器侧读不了即闸住。
    assert "access-control-allow-origin" not in r.headers


def test_preflight_grants_private_network_when_requested(client: TestClient) -> None:
    """PNA：预检带 `Access-Control-Request-Private-Network: true` ⇒ 响应放行私网头。

    真机部署形态 = https amazon 页面 → http://127.0.0.1，落在 Chrome 的 Private
    Network Access 口径；不回 `Access-Control-Allow-Private-Network: true` 则整条请求
    被浏览器拦（CI/curl 看不见这层）。不带该请求头时不回该响应头（不主动声张）。
    """
    with_pna = client.options(
        PULL,
        headers={
            "Origin": AMZ,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert with_pna.status_code == 204
    assert with_pna.headers["access-control-allow-private-network"] == "true"

    without_pna = client.options(
        PULL, headers={"Origin": AMZ, "Access-Control-Request-Method": "GET"}
    )
    assert without_pna.status_code == 204
    assert "access-control-allow-private-network" not in without_pna.headers


def test_non_plugin_path_gets_no_cors(client: TestClient) -> None:
    preflight = client.options(
        "/api/v1/me", headers={"Origin": AMZ, "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in preflight.headers
    actual = client.get("/api/v1/me", headers={"Origin": AMZ})
    assert "access-control-allow-origin" not in actual.headers


def test_actual_request_echoes_origin_even_on_401(client: TestClient) -> None:
    r = client.get(
        f"{PULL}?customerId=A13ECORS401",
        headers={"Origin": AMZ, "X-Plugin-Token": "wrong-token"},
    )
    assert r.status_code == 401
    assert r.headers["access-control-allow-origin"] == AMZ
    assert "Origin" in r.headers.get("vary", "")


def test_actual_request_with_token_succeeds(client: TestClient) -> None:
    r = client.get(
        f"{PULL}?customerId=A13ECORSOK",
        headers={"Origin": AMZ, "X-Plugin-Token": SHARED_TOKEN},
    )
    assert r.status_code == 200
    assert r.json() == {"code": 200, "data": []}  # 新号首见登记，名下无派单
    assert r.headers["access-control-allow-origin"] == AMZ
