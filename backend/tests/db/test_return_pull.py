"""R2-07 增量1 验收：渠道退货全量拉取（L1「只读拉取入库对拍一致」的测试化）。

- 分页拉取入库：头/行字段映射与渠道响应一致（旧仓 27 列口径）；nextCursor 完整 URL
  parse_qs 协议（BR-AS-001：无时间过滤，不带 since 参数）。
- upsert by (RMA, line)（C6）：重拉不增行；同步列刷新、internal_status 不触碰。
- 行级状态变化写 channel_return_event 留痕（BR-AS-006 改进）。
- order 回连：行 purchaseOrderId 反查 channel_order。
- 页失败即抛：sync_state 不写（下轮整店重拉）。
- 查询端点：列表 / 详情（含 lines + events）+ 越权不可见（RETURN_NOT_FOUND）。
"""

import json
import urllib.parse

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.aftersale import pull as return_pull
from erp.channel.gateway import gateway
from erp.core.db import get_session_factory
from erp.core.security import hash_password
from erp.core.settings import get_settings

from .conftest import APP_URL
from .test_identity_api import PASSWORD, _login

ADMIN = "aftersale_admin"
ADMIN_TEAM = "退货拉取测试团队"


def _return_json(
    rma: str,
    *,
    po: str = "PO-RET-1",
    status: str = "INITIATED",
    refund_status: str | None = None,
    delivery_status: str = "IN_TRANSIT",
    refunded_qty: int = 0,
    extra_line: bool = False,
    tracking: str | None = "1Z777",
) -> dict:
    def line(no: str, sku: str) -> dict:
        return {
            "salesOrderLineNumber": no,
            "status": status,
            "currentRefundStatus": refund_status,
            "currentDeliveryStatus": delivery_status,
            "returnMethod": "CarrierPickup",
            "statusTime": "2026-07-10T08:30:00.000Z",
            "item": {"sku": sku, "productName": "Nice Blue Mug", "condition": "New"},
            "quantity": {"unitOfMeasurement": "EACH", "measurementValue": "2"},
            "refundedQty": refunded_qty,
            "returnReason": "DamagedItem",
            "returnDescription": "handle broken",
            "purchaseOrderId": po,
            "charges": [
                {
                    "chargeCategory": "PRODUCT",
                    "chargeName": "ItemPrice",
                    "chargePerUnit": {"currencyAmount": 10.50, "currencyUnit": "USD"},
                }
            ],
        }

    lines = [line("1", "M9000001")]
    if extra_line:
        lines.append(line("2", "M9000002"))
    labels = []
    if tracking:
        labels.append({"carrierInfoList": [{"carrierName": "FedEx", "trackingNo": tracking}]})
    return {
        "returnOrderId": rma,
        "customerOrderId": f"C{rma}",
        "customerEmailId": "buyer@example.com",
        "customerName": {"firstName": "Test", "lastName": "Buyer"},
        "returnOrderDate": "2026-07-09T18:00:00.000Z",
        "returnByDate": "2026-08-09T18:00:00.000Z",
        "refundMode": "REFUND_TO_PAYMENT_METHOD",
        "totalRefundAmount": {"currencyAmount": 21.00, "currencyUnit": "USD"},
        "returnLineGroups": [{"labels": labels}],
        "returnOrderLines": lines,
    }


def _page(orders: list[dict], next_cursor: str | None) -> httpx.Response:
    meta: dict = {"totalCount": len(orders), "limit": 200}
    if next_cursor:
        meta["nextCursor"] = next_cursor
    return httpx.Response(200, json={"returnOrders": orders, "meta": meta})


class _FakeReturnsChannel:
    """按请求序回放页；记录每次请求的 path+params 供协议断言。"""

    def __init__(self) -> None:
        self.pages: list[httpx.Response] = []
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 900})
        self.requests.append(request)
        if self.pages:
            return self.pages.pop(0)
        return _page([], None)


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    key = get_settings().credential_key
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (ADMIN_TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (ADMIN_TEAM,)).fetchone()[
            0
        ]
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        conn.execute(
            "DELETE FROM app.store_credential WHERE store_id IN"
            " (SELECT id FROM app.store WHERE team_id = %s)",
            (team_id,),
        )
        for t in ("channel_return_event", "channel_return_line", "channel_return",
                  "order_line", "channel_order", "store"):  # fmt: skip
            conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (team_id,))
        store_id = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, 'RETPULL1', '退货拉取测试店', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO app.store_credential (store_id, client_id, client_secret_encrypted)"
            " VALUES (%s, 'retpull-client-01', pgp_sym_encrypt('retpull-secret-01', %s))",
            (store_id, key),
        )
        conn.execute(
            "DELETE FROM app.sync_state WHERE scope='return_pull' AND ref_id=%s", (store_id,)
        )
        conn.execute(
            "INSERT INTO app.system_config (key, value)"
            " VALUES ('channel.gateway_mode', '\"live_test\"'::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value"
        )
        # 回连目标订单（行 purchaseOrderId = PO-RET-1）
        order_id = conn.execute(
            "INSERT INTO app.channel_order (team_id, store_id, channel_order_no, order_date,"
            " channel_status, ship_to, order_total, item_count, pulled_at)"
            " VALUES (%s, %s, 'PO-RET-1', now() - interval '10 day', 'Delivered',"
            "         '{}', 21.00, 1, now()) RETURNING id",
            (team_id, store_id),
        ).fetchone()[0]
    return {"team": team_id, "store": store_id, "order": order_id}


@pytest.fixture()
def fake(seeded: dict[str, int]) -> _FakeReturnsChannel:
    f = _FakeReturnsChannel()
    gateway._clients.clear()
    gateway._transport_factory = lambda proxy: httpx.MockTransport(f.handler)
    yield f
    gateway._clients.clear()
    gateway._transport_factory = gateway._default_transport


class TestReturnPullIngest:
    async def test_paged_pull_maps_and_ingests(
        self, seeded: dict, migrated_db: str, fake: _FakeReturnsChannel
    ) -> None:
        cursor = "https://marketplace.walmartapis.com/v3/returns?sellerId=SEL1&limit=200&offset=200"
        rma_1002 = _return_json(
            "RMA-1002", status="COMPLETED", refund_status="REFUNDED",
            refunded_qty=2, po="PO-NOMATCH",
        )  # fmt: skip
        fake.pages = [
            _page([_return_json("RMA-1001", extra_line=True)], cursor),
            _page([rma_1002], None),
        ]
        stats = await return_pull.pull_store_returns(
            get_session_factory(), seeded["store"], config={}
        )
        assert stats["pages"] == 2 and stats["returns"] == 2 and stats["lines"] == 3
        assert stats["events"] == 0  # 首次入库不记事件

        # 协议断言：首页无时间过滤（BR-AS-001）；次页 = cursor 完整 URL parse_qs 回参
        first = fake.requests[0]
        q1 = urllib.parse.parse_qs(str(first.url.query, "utf-8"))
        assert q1["limit"] == ["200"] and q1["replacementInfo"] == ["true"]  # BR-AS-005
        assert not any("Date" in k for k in q1)  # 无 since/时间参数
        q2 = urllib.parse.parse_qs(str(fake.requests[1].url.query, "utf-8"))
        assert q2["offset"] == ["200"] and q2["sellerId"] == ["SEL1"]

        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT customer_order_no, channel_status, internal_status, refund_mode,"
                " qty, refund_amount, currency, order_id, reason, customer->>'email'"
                " FROM app.channel_return WHERE channel_return_no = 'RMA-1001'"
            ).fetchone()
            assert row == ("CRMA-1001", "INITIATED", "pulled", "REFUND_TO_PAYMENT_METHOD",
                           4, 21.00, "USD", seeded["order"], "DamagedItem",
                           "buyer@example.com")  # fmt: skip
            line = conn.execute(
                "SELECT l.channel_sku, l.qty, l.refunded_qty, l.unit_price, l.line_status,"
                " l.delivery_status, l.carrier, l.tracking_no, l.purchase_order_no"
                " FROM app.channel_return_line l"
                " JOIN app.channel_return r ON r.id = l.return_id"
                " WHERE r.channel_return_no = 'RMA-1001' AND l.line_no = '1'"
            ).fetchone()
            assert line == ("M9000001", 2, 0, 10.50, "INITIATED", "IN_TRANSIT",
                            "FedEx", "1Z777", "PO-RET-1")  # fmt: skip
            # PO 不匹配的 RMA：order 回连留空，不误连
            unlinked = conn.execute(
                "SELECT order_id FROM app.channel_return WHERE channel_return_no = 'RMA-1002'"
            ).fetchone()[0]
            assert unlinked is None
            hwm = conn.execute(
                "SELECT last_sync_at FROM app.sync_state WHERE scope='return_pull' AND ref_id=%s",
                (seeded["store"],),
            ).fetchone()[0]
            assert hwm is not None

    async def test_repull_upserts_and_records_events(
        self, seeded: dict, migrated_db: str, fake: _FakeReturnsChannel
    ) -> None:
        """C6：重拉不增行；行级状态变化留痕；internal_status 不被打回。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.channel_return SET internal_status = 'reviewed'"
                " WHERE channel_return_no = 'RMA-1001'"
            )
            before = conn.execute(
                "SELECT count(*) FROM app.channel_return_line WHERE team_id = %s",
                (seeded["team"],),
            ).fetchone()[0]
        # 同 RMA 重拉：行状态推进 + 退款完成
        advanced = _return_json(
            "RMA-1001", extra_line=True, status="COMPLETED",
            refund_status="REFUNDED", refunded_qty=2,
        )  # fmt: skip
        fake.pages = [_page([advanced], None)]
        stats = await return_pull.pull_store_returns(
            get_session_factory(), seeded["store"], config={}
        )
        assert stats["events"] == 2  # 两行各一条变更事件
        with psycopg.connect(migrated_db) as conn:
            after = conn.execute(
                "SELECT count(*) FROM app.channel_return_line WHERE team_id = %s",
                (seeded["team"],),
            ).fetchone()[0]
            assert after == before  # 不增行
            row = conn.execute(
                "SELECT channel_status, internal_status FROM app.channel_return"
                " WHERE channel_return_no = 'RMA-1001'"
            ).fetchone()
            assert row == ("COMPLETED", "reviewed")  # 同步列刷新；内部列不触碰
            ev = conn.execute(
                "SELECT e.changes FROM app.channel_return_event e"
                " JOIN app.channel_return r ON r.id = e.return_id"
                " WHERE r.channel_return_no = 'RMA-1001' AND e.line_no = '1'"
            ).fetchone()[0]
            changes = ev if isinstance(ev, dict) else json.loads(ev)
            assert changes["line_status"] == {"old": "INITIATED", "new": "COMPLETED"}
            assert changes["refund_status"]["new"] == "REFUNDED"
            assert changes["refunded_qty"] == {"old": 0, "new": 2}

    async def test_page_failure_no_sync_state(
        self, seeded: dict, migrated_db: str, fake: _FakeReturnsChannel
    ) -> None:
        with psycopg.connect(migrated_db) as conn:
            hwm_before = conn.execute(
                "SELECT last_sync_at FROM app.sync_state WHERE scope='return_pull' AND ref_id=%s",
                (seeded["store"],),
            ).fetchone()[0]
        fake.pages = [httpx.Response(500, json={"error": "boom"})]
        with pytest.raises(RuntimeError):
            await return_pull.pull_store_returns(get_session_factory(), seeded["store"], config={})
        with psycopg.connect(migrated_db) as conn:
            hwm_after = conn.execute(
                "SELECT last_sync_at FROM app.sync_state WHERE scope='return_pull' AND ref_id=%s",
                (seeded["store"],),
            ).fetchone()[0]
            assert hwm_after == hwm_before

    async def test_drop_alert_notifies_not_blocks(
        self, seeded: dict, migrated_db: str, fake: _FakeReturnsChannel
    ) -> None:
        """BR-AS-007：见单量骤降 >50% 出 warn 告警，但照常入库不中断。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.sync_state SET stats = '{\"lines\": 100}'::jsonb"
                " WHERE scope='return_pull' AND ref_id=%s",
                (seeded["store"],),
            )
            conn.execute(
                "DELETE FROM app.notification WHERE dedupe_key = %s",
                (f"return_pull_drop:{seeded['store']}",),
            )
        fake.pages = [_page([_return_json("RMA-1001")], None)]
        stats = await return_pull.pull_store_returns(
            get_session_factory(), seeded["store"], config={}
        )
        assert stats["returns"] == 1  # 照常入库
        with psycopg.connect(migrated_db) as conn:
            assert (
                conn.execute(
                    "SELECT 1 FROM app.notification WHERE dedupe_key = %s",
                    (f"return_pull_drop:{seeded['store']}",),
                ).fetchone()
                is not None
            )


class TestMapReturn:
    def test_header_aggregates_and_fallback_line_no(self) -> None:
        raw = _return_json("RMA-X", extra_line=True)
        raw["returnOrderLines"][1]["status"] = "COMPLETED"
        del raw["returnOrderLines"][1]["salesOrderLineNumber"]
        mapped = return_pull.map_return(raw)
        assert mapped["channel_status"] == "MIXED"  # 行状态分歧
        assert mapped["qty"] == 4
        assert mapped["lines"][1]["line_no"] == "2"  # 缺销售行号回退序号

    def test_timestamp_tolerates_iso_and_epoch(self) -> None:
        assert return_pull._parse_ts("2026-07-09T18:00:00.000Z") is not None
        assert return_pull._parse_ts(1752084000000) is not None
        assert return_pull._parse_ts("") is None


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> TestClient:
    import os

    os.environ["ERP_DATABASE_URL"] = APP_URL
    from erp.core.db import get_session_factory as gsf
    from erp.core.settings import get_settings as gs

    gs.cache_clear()
    gsf.cache_clear()
    from erp.main import create_app

    return TestClient(create_app())


@pytest.fixture(scope="module")
def admin_user(seeded: dict[str, int], migrated_db: str) -> str:
    """团队内订单员（模板角色 0030 已挂 aftersale.read）。"""
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM app.app_user WHERE username = %s", (ADMIN,))
        uid = conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '售后测试员') RETURNING id",
            (seeded["team"], ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO app.role (team_id, name, description)
            SELECT %s, name, description FROM app.role tpl
            WHERE tpl.team_id IS NULL AND tpl.name = '订单员'
              AND NOT EXISTS (SELECT 1 FROM app.role r
                              WHERE r.team_id = %s AND r.name = '订单员')
            """,
            (seeded["team"], seeded["team"]),
        )
        conn.execute(
            """
            INSERT INTO app.role_permission (role_id, permission_code)
            SELECT r.id, rp.permission_code FROM app.role r
            JOIN app.role tpl ON tpl.team_id IS NULL AND tpl.name = r.name
            JOIN app.role_permission rp ON rp.role_id = tpl.id
            WHERE r.team_id = %s AND r.name = '订单员'
            ON CONFLICT DO NOTHING
            """,
            (seeded["team"],),
        )
        conn.execute(
            """
            INSERT INTO app.user_role (user_id, role_id)
            SELECT %s, r.id FROM app.role r WHERE r.team_id = %s AND r.name = '订单员'
            ON CONFLICT DO NOTHING
            """,
            (uid, seeded["team"]),
        )
    return ADMIN


class TestAftersaleApi:
    def test_list_and_detail(
        self, client: TestClient, seeded: dict, migrated_db: str, admin_user: str
    ) -> None:
        h = _login(client, admin_user, PASSWORD)
        page = client.get("/api/v1/returns", headers=h).json()
        assert page["total"] >= 1
        hit = next(i for i in page["items"] if i["channel_return_no"] == "RMA-1001")
        detail = client.get(f"/api/v1/returns/{hit['id']}", headers=h).json()
        assert {ln["line_no"] for ln in detail["lines"]} == {"1", "2"}
        assert detail["customer"]["email"] == "buyer@example.com"
        assert any(ev["line_no"] == "1" for ev in detail["events"])  # 变更留痕可见
        # 精确查（RMA / 客户订单号）
        assert client.get("/api/v1/returns?q=CRMA-1001", headers=h).json()["total"] >= 1

    def test_cross_team_isolated(
        self, client: TestClient, seeded: dict, migrated_db: str, admin_user: str
    ) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.team (name) VALUES ('退货隔离他队') ON CONFLICT (name) DO NOTHING"
            )
            other_team = conn.execute(
                "SELECT id FROM app.team WHERE name = '退货隔离他队'"
            ).fetchone()[0]
            other_ret = conn.execute(
                "INSERT INTO app.channel_return (team_id, store_id, channel_return_no,"
                " return_date, channel_status, qty, pulled_at)"
                " SELECT %s, store_id, 'RMA-OTHER', now(), 'INITIATED', 1, now()"
                " FROM app.channel_return LIMIT 1"
                " ON CONFLICT (store_id, channel_return_no)"
                " DO UPDATE SET team_id = excluded.team_id"
                " RETURNING id",
                (other_team,),
            ).fetchone()[0]
        h = _login(client, admin_user, PASSWORD)
        resp = client.get(f"/api/v1/returns/{other_ret}", headers=h)
        assert resp.status_code == 422  # BusinessError 统一信封（默认 422）
        assert resp.json()["error"]["code"] == "RETURN_NOT_FOUND"
        assert not any(
            i["channel_return_no"] == "RMA-OTHER"
            for i in client.get("/api/v1/returns", headers=h).json()["items"]
        )
