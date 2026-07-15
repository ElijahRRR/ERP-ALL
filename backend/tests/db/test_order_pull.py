"""R2-05 增量1 验收：渠道订单增量拉取（L1「只读拉取入库对账一致」的测试化）。

- 分页拉取入库：订单/行/金额/状态/地址映射与渠道响应一致（含 nextCursor 完整串协议）。
- upsert 幂等（BR-ORD-003）：重拉不增行；同步列刷新、内部列（internal_status）不触碰。
- 渠道 Cancelled 强制覆盖内部状态 + notification（001/07:45）。
- sync_state：成功推进 high-water mark；页失败不推进（下轮重拉同窗口）。
- createdStartDate 恒传（BR-ORD-002）与 lastModifiedStartDate 增量窗口。
"""

import json
import time
import urllib.parse

import httpx
import psycopg
import pytest

from erp.channel.gateway import gateway
from erp.core.db import get_session_factory
from erp.core.settings import get_settings
from erp.order import pull as order_pull

ADMIN_TEAM = "订单拉取测试团队"


def _order_json(
    po: str,
    *,
    order_ms: int,
    sku: str = "M9000001",
    status: str = "Created",
    qty: int = 2,
    price_each: float = 10.50,
    tracking: dict | None = None,
) -> dict:
    line_status: dict = {
        "status": status,
        "statusQuantity": {"unitOfMeasurement": "EACH", "amount": str(qty)},
    }
    if tracking:
        line_status["trackingInfo"] = tracking
    return {
        "purchaseOrderId": po,
        "customerOrderId": f"C{po}",
        "customerEmailId": "x@example.com",
        "orderDate": order_ms,
        "shippingInfo": {
            "phone": "5551234567",
            "estimatedShipDate": order_ms,
            "methodCode": "Standard",
            "postalAddress": {
                "name": "Test Buyer",
                "address1": "123 Fake Street",
                "city": "Austin",
                "state": "TX",
                "postalCode": "78701-1234",
                "country": "USA",
            },
        },
        "orderLines": {
            "orderLine": [
                {
                    "lineNumber": "1",
                    "item": {"productName": "Nice Blue Mug", "sku": sku},
                    "charges": {
                        "charge": [
                            {
                                "chargeType": "PRODUCT",
                                "chargeName": "ItemPrice",
                                "chargeAmount": {"currency": "USD", "amount": price_each * qty},
                            },
                            {
                                "chargeType": "SHIPPING",
                                "chargeName": "Shipping",
                                "chargeAmount": {"currency": "USD", "amount": 4.99},
                            },
                        ]
                    },
                    "orderLineQuantity": {"unitOfMeasurement": "EACH", "amount": str(qty)},
                    "orderLineStatuses": {"orderLineStatus": [line_status]},
                }
            ]
        },
    }


def _page(orders: list[dict], next_cursor: str | None) -> httpx.Response:
    meta: dict = {"totalCount": len(orders), "limit": 200}
    if next_cursor:
        meta["nextCursor"] = next_cursor
    return httpx.Response(200, json={"list": {"meta": meta, "elements": {"order": orders}}})


class _FakeOrdersChannel:
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
        for t in ("order_check", "order_line", "channel_order", "listing", "product",
                  "store"):  # fmt: skip
            conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (team_id,))
        conn.execute(
            "DELETE FROM app.sync_state WHERE scope = 'order_pull' AND ref_id NOT IN"
            " (SELECT id FROM app.store)"
        )
        store_id = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, 'ORDPULL1', '订单拉取测试店', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO app.store_credential (store_id, client_id, client_secret_encrypted)"
            " VALUES (%s, 'ordpull-client-01', pgp_sym_encrypt('ordpull-secret-01', %s))",
            (store_id, key),
        )
        conn.execute("DELETE FROM app.sync_state WHERE scope='order_pull' AND ref_id=%s",
                     (store_id,))  # fmt: skip
        conn.execute(
            "INSERT INTO app.system_config (key, value)"
            " VALUES ('channel.gateway_mode', '\"live_test\"'::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value"
        )
    return {"team": team_id, "store": store_id}


@pytest.fixture()
def fake(seeded: dict[str, int]) -> _FakeOrdersChannel:
    f = _FakeOrdersChannel()
    gateway._clients.clear()
    gateway._transport_factory = lambda proxy: httpx.MockTransport(f.handler)
    yield f
    gateway._clients.clear()
    gateway._transport_factory = gateway._default_transport


def _now_ms(days_ago: float = 0) -> int:
    return int((time.time() - days_ago * 86400) * 1000)


# 同一渠道单的 order_date 恒定（真实渠道语义）——模块级固定，跨用例复用
MS_PO_1001 = _now_ms(1)
MS_PO_1002 = _now_ms(2)


class TestPullIngest:
    async def test_paged_pull_maps_and_ingests(
        self, seeded: dict, migrated_db: str, fake: _FakeOrdersChannel
    ) -> None:
        cursor = "?limit=200&nextCursor=abc123&hasMoreElements=true"
        fake.pages = [
            _page([_order_json("PO-1001", order_ms=MS_PO_1001)], cursor),
            _page(
                [
                    _order_json(
                        "PO-1002",
                        order_ms=MS_PO_1002,
                        status="Shipped",
                        tracking={
                            "shipDateTime": _now_ms(0.5),
                            "carrierName": {"carrier": "UPS"},
                            "trackingNumber": "1Z999",
                        },
                    )
                ],
                None,
            ),
        ]
        stats = await order_pull.pull_store_orders(
            get_session_factory(), seeded["store"], config={}
        )
        assert stats["pages"] == 2 and stats["orders"] == 2 and stats["lines"] == 2

        # 协议断言：首页恒带 createdStartDate + lastModifiedStartDate；次页走完整 cursor 串
        first = fake.requests[0]
        q = urllib.parse.parse_qs(str(first.url.query, "utf-8"))
        assert "createdStartDate" in q and "lastModifiedStartDate" in q  # BR-ORD-002/001
        second = fake.requests[1]
        assert "nextCursor=abc123" in str(second.url)

        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT channel_status, internal_status, order_total, item_count,"
                " ship_to->>'address1', customer->>'name'"
                " FROM app.channel_order WHERE channel_order_no = 'PO-1001'"
            ).fetchone()
            assert row == ("Created", "pulled", 21.00, 1, "123 Fake Street", "Test Buyer")
            line = conn.execute(
                "SELECT ol.line_status, ol.qty, ol.unit_price, ol.carrier, ol.tracking_no"
                " FROM app.order_line ol JOIN app.channel_order o ON o.id = ol.order_id"
                " WHERE o.channel_order_no = 'PO-1002'"
            ).fetchone()
            assert line == ("shipped", 2, 10.50, "UPS", "1Z999")
            hwm = conn.execute(
                "SELECT last_sync_at FROM app.sync_state WHERE scope='order_pull' AND ref_id=%s",
                (seeded["store"],),
            ).fetchone()[0]
            assert hwm is not None

    async def test_repull_idempotent_and_internal_columns_untouched(
        self, seeded: dict, migrated_db: str, fake: _FakeOrdersChannel
    ) -> None:
        """BR-ORD-003：重拉不增行；同步列刷新；内部列不被打回。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.channel_order SET internal_status = 'assigned'"
                " WHERE channel_order_no = 'PO-1001'"
            )
            before = conn.execute(
                "SELECT count(*) FROM app.channel_order WHERE team_id = %s", (seeded["team"],)
            ).fetchone()[0]
        # 同单重拉：渠道状态推进到 Acknowledged
        fake.pages = [
            _page([_order_json("PO-1001", order_ms=MS_PO_1001, status="Acknowledged")], None)
        ]
        await order_pull.pull_store_orders(get_session_factory(), seeded["store"], config={})
        with psycopg.connect(migrated_db) as conn:
            after = conn.execute(
                "SELECT count(*) FROM app.channel_order WHERE team_id = %s", (seeded["team"],)
            ).fetchone()[0]
            assert after == before  # 不增行
            row = conn.execute(
                "SELECT channel_status, internal_status FROM app.channel_order"
                " WHERE channel_order_no = 'PO-1001'"
            ).fetchone()
            assert row == ("Acknowledged", "assigned")  # 同步列刷新；内部列不触碰

    async def test_channel_cancelled_forces_internal_and_notifies(
        self, seeded: dict, migrated_db: str, fake: _FakeOrdersChannel
    ) -> None:
        fake.pages = [
            _page([_order_json("PO-1001", order_ms=MS_PO_1001, status="Cancelled")], None)
        ]
        stats = await order_pull.pull_store_orders(
            get_session_factory(), seeded["store"], config={}
        )
        assert stats["cancelled"] == 1
        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT id, channel_status, internal_status FROM app.channel_order"
                " WHERE channel_order_no = 'PO-1001'"
            ).fetchone()
            assert (row[1], row[2]) == ("Cancelled", "cancelled")
            assert (
                conn.execute(
                    "SELECT 1 FROM app.notification WHERE dedupe_key = %s",
                    (f"order_cancelled:{row[0]}",),
                ).fetchone()
                is not None
            )

    async def test_page_failure_keeps_watermark(
        self, seeded: dict, migrated_db: str, fake: _FakeOrdersChannel
    ) -> None:
        """页失败即抛：sync_state 不推进（下轮重拉同窗口）。"""
        with psycopg.connect(migrated_db) as conn:
            hwm_before = conn.execute(
                "SELECT last_sync_at FROM app.sync_state WHERE scope='order_pull' AND ref_id=%s",
                (seeded["store"],),
            ).fetchone()[0]
        fake.pages = [httpx.Response(500, json={"error": "boom"})]
        with pytest.raises(RuntimeError):
            await order_pull.pull_store_orders(get_session_factory(), seeded["store"], config={})
        with psycopg.connect(migrated_db) as conn:
            hwm_after = conn.execute(
                "SELECT last_sync_at FROM app.sync_state WHERE scope='order_pull' AND ref_id=%s",
                (seeded["store"],),
            ).fetchone()[0]
            assert hwm_after == hwm_before

    async def test_incremental_window_uses_watermark(
        self, seeded: dict, migrated_db: str, fake: _FakeOrdersChannel
    ) -> None:
        """有 high-water mark 后：lastModifiedStartDate = last_sync − overlap（非冷启动兜底）。"""
        fake.pages = [_page([], None)]
        await order_pull.pull_store_orders(
            get_session_factory(), seeded["store"], config={"overlap_hours": 1}
        )
        q = urllib.parse.parse_qs(str(fake.requests[-1].url.query, "utf-8"))
        since = q["lastModifiedStartDate"][0]
        # last_sync 是刚才的运行时间，减 1h 后应落在最近 2 小时内（而不是 30 天前）
        from datetime import UTC, datetime

        parsed = datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        assert (datetime.now(UTC) - parsed).total_seconds() < 2 * 3600


class TestMapOrder:
    def test_aggregate_status_least_advanced_and_all_cancelled(self) -> None:
        base = _order_json("PO-X", order_ms=_now_ms(1))
        two = _order_json("PO-X", order_ms=_now_ms(1))
        two["orderLines"]["orderLine"].append(
            json.loads(json.dumps(base["orderLines"]["orderLine"][0]))
        )
        two["orderLines"]["orderLine"][0]["orderLineStatuses"]["orderLineStatus"][0]["status"] = (
            "Shipped"
        )
        assert order_pull.map_order(two)["channel_status"] == "Created"  # 最落后未取消行
        for ln in two["orderLines"]["orderLine"]:
            ln["orderLineStatuses"]["orderLineStatus"][0]["status"] = "Cancelled"
        assert order_pull.map_order(two)["channel_status"] == "Cancelled"

    def test_line_status_takes_last_and_refund_maps(self) -> None:
        o = _order_json("PO-Y", order_ms=_now_ms(1))
        o["orderLines"]["orderLine"][0]["orderLineStatuses"]["orderLineStatus"] = [
            {"status": "Shipped", "statusQuantity": {"unitOfMeasurement": "EACH", "amount": "1"}},
            {"status": "Refund", "statusQuantity": {"unitOfMeasurement": "EACH", "amount": "1"}},
        ]
        mapped = order_pull.map_order(o)
        assert mapped["lines"][0]["line_status"] == "refunded"  # [-1] 语义 + Refund 映射
