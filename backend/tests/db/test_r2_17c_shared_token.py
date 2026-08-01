"""R2-17 17c 验收④：插件带共享 token 可拉任务；不带/带错/未配置一律拒（唯一保留的闸）。

| 判据 | 用例 |
|---|---|
| 配置齐 + 对 token ⇒ 200 拉到已派任务（团队取自配置中心）
  | `test_configured_token_pulls_assigned_task` |
| 错 token / 空头 / 缺头 ⇒ **六端点**全 401 同码 `PLUGIN_AUTH`
  | `test_wrong_or_missing_token_all_endpoints_401` |
| `ERP_PLUGIN_SHARED_TOKEN` 未配置 ⇒ 通道整体关闭（fail-closed）
  | `test_unconfigured_channel_is_fail_closed` |
| 档位词表外/未配置回落 `stop_before_payment`，词表内逐值生效
  | `test_exec_mode_vocabulary_fallback` |
| 团队配置生效；缺配置回落最小活跃团队；哨兵 `instance_id=0`
  | `test_team_config_and_fallback` |

**不在本文件**：拉取/回填/异常/物流的业务语义（13a/13d 两文件已整体切到共享形态，
那边的每个用例同时都是共享认证的回归）；「散列不可当令牌」绊线在 13a 的
`test_stored_hash_is_not_a_usable_token`；免登录开关在 `test_single_user_mode.py`。

## 为什么六端点逐个打而不是抽一个代表

「唯一保留的闸」的价值恰好在**无一例外**：漏配一个端点的依赖（比如新端点抄错了
`Depends`），那个端点就是不设防的下单/回填通道。六条一起钉，加端点忘挂闸时这里红。
"""

import os
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.core.db import get_session_factory, system_tx
from erp.plugin import auth as plugin_auth

SHARED_TOKEN = "r17c-shared-token-fedcba9876543210"
TEAM = "R2-17c 共享 token 测试团队"
PLUGIN = "/api/v1/purchase-plugin"

# (method, path, 合法请求体)。体必须合法：认证与体校验同属依赖解析，体不合法时 422
# 可能先于 401 出来，那测的就不是闸了。
_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/getNeedPurchaseOrders", None),
    ("GET", "/getNeedSyncOrders", None),
    ("POST", "/purchaseOrderFinishUpdate", {"id": 1}),
    ("POST", "/updateOrderStatus", {"id": 1, "status": 99, "failReason": "x"}),
    ("POST", "/updateAmzOrderStatus", {"id": 1, "status": 91}),
    ("POST", "/updateTrackingInfo", {"orderId": 1}),
]


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        for t in ("procurement_order", "order_line", "channel_order", "product",
                  "buyer_account", "store"):  # fmt: skip
            conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (team_id,))
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        store_id = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, 'PL17C', '共享token测试店', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
        # 共享通道的团队归属：配置中心指定（铁律 5）。逐模块覆写，见 13a/13d 同款。
        conn.execute("DELETE FROM app.system_config WHERE key = 'procurement.plugin_team_id'")
        conn.execute(
            "INSERT INTO app.system_config (key, value) VALUES"
            " ('procurement.plugin_team_id', to_jsonb(%s::bigint))",
            (team_id,),
        )
    return {"team": team_id, "store": store_id}


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


SHIP_TO = (
    '{"name": "Jane Roe", "phone": "+1-555-0100", "address1": "123 Main St",'
    ' "city": "Marion", "state": "AL", "postalCode": "36759", "country": "US"}'
)


def _mk_task(conn: psycopg.Connection, seeded: dict[str, int]) -> tuple[str, int]:
    """一个 active 买家账号 + 一张已派给它的执行单（形状抄 13a，取最小集）。"""
    tag = uuid.uuid4().hex[:8]
    customer_id = f"A{tag.upper()}"
    account_id = conn.execute(
        "INSERT INTO app.buyer_account (team_id, label, site, external_customer_id, status)"
        " VALUES (%s, %s, 'amazon_com', %s, 'active') RETURNING id",
        (seeded["team"], f"指纹浏览器-{tag}", customer_id),
    ).fetchone()[0]
    order = conn.execute(
        "INSERT INTO app.channel_order (team_id, store_id, channel_order_no, order_date,"
        " channel_status, customer, ship_to, order_total, item_count, pulled_at,"
        " internal_status, has_flag)"
        " VALUES (%s, %s, %s, now() - interval '1 day', 'Created', '{}',"
        "         %s::jsonb, 10, 1, now(), 'checked', false) RETURNING id, order_date",
        (seeded["team"], seeded["store"], f"PL17C-{tag}", SHIP_TO),
    ).fetchone()
    product_id = conn.execute(
        "INSERT INTO app.product (team_id, source_channel, source_ref, title)"
        " VALUES (%s, 'amazon', %s, '17c测试商品') RETURNING id",
        (seeded["team"], f"B017C-{tag}"),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO app.order_line (order_id, order_date, team_id, channel_line_no,"
        " channel_sku, product_id, qty, unit_price, line_status)"
        " VALUES (%s, %s, %s, '1', 'M0000001', %s, 1, 9.99, 'created')",
        (order[0], order[1], seeded["team"], product_id),
    )
    po = int(
        conn.execute(
            "INSERT INTO app.procurement_order (team_id, store_id, order_id, order_date,"
            " status, buyer_account_id, assignee_kind, assigned_at)"
            " VALUES (%s, %s, %s, %s, 'assigned', %s, 'none', now()) RETURNING id",
            (seeded["team"], seeded["store"], order[0], order[1], account_id),
        ).fetchone()[0]
    )
    return customer_id, po


def _set_cfg(conn: psycopg.Connection, key: str, value_sql: str, *params: Any) -> None:
    conn.execute("DELETE FROM app.system_config WHERE key = %s", (key,))
    conn.execute(
        f"INSERT INTO app.system_config (key, value) VALUES (%s, {value_sql})", (key, *params)
    )


async def _authenticate(token: str = SHARED_TOKEN) -> plugin_auth.PluginPrincipal:
    """直调认证函数（与 router 同跑 `system_tx`）——档位/团队解析的判据在返回值上，
    走 HTTP 只能间接观测（409 与否），直调把映射钉成显式断言。"""
    async with system_tx(get_session_factory()) as s:
        return await plugin_auth.authenticate_shared(s, token)


class TestSharedGate:
    def test_configured_token_pulls_assigned_task(
        self, client: TestClient, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        """验收④正面：配置齐 + 对 token ⇒ 拉到已派任务。

        同时是「团队取自配置中心」的端到端证据：造数全落在本模块团队名下，
        能拉到就说明 `procurement.plugin_team_id` → principal.team_id → RLS/SQL 谓词
        整条链指对了团队。
        """
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            customer_id, po = _mk_task(conn, seeded)
        r = client.get(
            f"{PLUGIN}/getNeedPurchaseOrders",
            headers={"X-Plugin-Token": SHARED_TOKEN},
            params={"customerId": customer_id},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["code"] == 200
        assert po in [t["id"] for t in body["data"]], "对 token 拉不到已派任务——闸把自己人挡了"

    def test_wrong_or_missing_token_all_endpoints_401(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        """验收④反面：错 token / 空头 / 缺头 × 六端点 = 18 条全 401 同码。

        空头单列是因为它走的是 `if not shared or not token` 的另一半——头带了但值为空，
        与「压根没带」在 FastAPI 里是两个形状（None vs ""），都得掉进同一个 401。
        """
        for headers in ({"X-Plugin-Token": "not-the-shared-token"}, {"X-Plugin-Token": ""}, {}):
            for method, path, body in _ENDPOINTS:
                if method == "GET":
                    r = client.get(
                        f"{PLUGIN}{path}", headers=headers, params={"customerId": "A17CPROBE"}
                    )
                else:
                    r = client.post(f"{PLUGIN}{path}", headers=headers, json=body)
                assert r.status_code == 401, (headers, path, r.status_code, r.text)
                assert r.json()["error"]["code"] == "PLUGIN_AUTH", (headers, path)

    def test_unconfigured_channel_is_fail_closed(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        """`ERP_PLUGIN_SHARED_TOKEN` 未配置 ⇒ 插件通道整体关闭，**不存在「没配就裸奔」**。

        哪怕请求带着「将来会配的那个值」也进不来——服务端没有参照物，唯一 fail-closed
        的判定就是全拒。这条是 settings 注释里那句承诺的可执行形态。
        """
        from erp.core.settings import get_settings

        prev = os.environ.pop("ERP_PLUGIN_SHARED_TOKEN", None)
        get_settings.cache_clear()
        try:
            from erp.main import app

            with TestClient(app) as c:
                for token in (SHARED_TOKEN, "", None):
                    headers = {} if token is None else {"X-Plugin-Token": token}
                    r = c.get(
                        f"{PLUGIN}/getNeedPurchaseOrders",
                        headers=headers,
                        params={"customerId": "A17CPROBE"},
                    )
                    assert r.status_code == 401, (token, r.status_code, r.text)
                    assert r.json()["error"]["code"] == "PLUGIN_AUTH"
        finally:
            if prev is not None:
                os.environ["ERP_PLUGIN_SHARED_TOKEN"] = prev
            get_settings.cache_clear()


class TestConfigResolution:
    async def test_exec_mode_vocabulary_fallback(
        self, client: TestClient, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        """档位解析：词表内逐值生效；词表外/未配置一律回落 `stop_before_payment`。

        回落方向是承重的——配置错字滑进 `live` 就是真花钱，滑进
        `stop_before_payment` 只是多停一步（fail-safe 到不花钱档）。
        """
        cases = [
            ("'\"live\"'::jsonb", "live"),
            ("'\"dry_run\"'::jsonb", "dry_run"),
            ("'\"stop_before_payment\"'::jsonb", "stop_before_payment"),
            ("'\"prod\"'::jsonb", "stop_before_payment"),  # 词表外的错字
            (None, "stop_before_payment"),  # 压根没配
        ]
        for value_sql, expected in cases:
            with psycopg.connect(migrated_db, autocommit=True) as conn:
                if value_sql is None:
                    conn.execute(
                        "DELETE FROM app.system_config WHERE key = 'procurement.plugin_exec_mode'"
                    )
                else:
                    _set_cfg(conn, "procurement.plugin_exec_mode", value_sql)
            principal = await _authenticate()
            assert principal.exec_mode == expected, (value_sql, principal.exec_mode)

    async def test_team_config_and_fallback(
        self, client: TestClient, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        """团队解析：配置指定生效；缺配置回落最小活跃团队；哨兵 `instance_id=0`。

        末段还原配置——本模块后续用例（以及断言「团队取自配置」的正面例）依赖它在位。
        """
        principal = await _authenticate()
        assert principal.team_id == seeded["team"]
        assert principal.instance_id == 0, "共享通道必须是 0 哨兵（写路径 actor 落 NULL 靠它）"

        with psycopg.connect(migrated_db, autocommit=True) as conn:
            expected_fallback = conn.execute(
                "SELECT min(id) FROM app.team WHERE status = 'active'"
            ).fetchone()[0]
            conn.execute("DELETE FROM app.system_config WHERE key = 'procurement.plugin_team_id'")
        try:
            fallback = await _authenticate()
            assert fallback.team_id == expected_fallback, "缺配置该回落最小活跃团队"
        finally:
            with psycopg.connect(migrated_db, autocommit=True) as conn:
                _set_cfg(conn, "procurement.plugin_team_id", "to_jsonb(%s::bigint)", seeded["team"])
