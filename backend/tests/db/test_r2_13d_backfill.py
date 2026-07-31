"""R2-13 13d 验收：回填 / 异常 / 物流 / 对账接点（判据⑧ 的服务端半段）。

| 判据 / 定性决定 | 用例 |
|---|---|
| 金额「空值 ≠ 解析失败」，**失败绝不写 0** | `TestMoneyParsing` |
| 解析失败该列留 NULL 而不是 0 | `test_unparseable_amount_stays_null` |
| **负金额=抓错行**，走异常通道不入库（审查 B5） | `test_negative_amount_is_a_parse_error` |
| 同上端到端：负值该列留 NULL + `other` + 告警 | `test_negative_amount_stays_null_and_alerts` |
| **D2** 落异常 ≠ 不落数据（不平仍照实入库） | `test_self_check_mismatch_still_records_amounts` |
| **D1** 回填后核对不改 status（已拍单的事实不能抹） | `test_asin_mismatch_keeps_purchased` |
| 邮编核对不制造假阳（zip+4） | `test_postcode_zip4_is_not_a_mismatch` |
| 幂等键 = `platformOrderNo`（同值 no-op / 异值不覆盖 + 告警） | `TestIdempotency` 两例 |
| 汇率坑：`exchange_rate_locked` 必须是 1.0 不是 NULL | `test_live_backfill_writes_all_columns` |
| 三档：付款前停能抓回金额、dry_run 零写入、档不符 409 | `TestExecModes` |
| 演练痕迹重投不堆叠（marker 不带时间戳，审查 B3） | `test_replay_does_not_stack_note_markers` |
| 13c 停留位零出边：不推 purchased（审查 B7） | `test_pending_review_is_not_advanced_to_purchased` |
| 同上另一半：端点 3 也不许把它推成 exception | `test_pending_review_is_not_pushed_to_exception` |
| **验收⑧ 服务端半段**：拍单失败零金额列写入 | `test_failure_writes_zero_money_columns` |
| 17 条厂商原文归类表逐条对判 | `test_classify_covers_production_failures` |
| 91/92 → 两个 `exception_kind`，**status 一字不动** | `test_channel_status_keeps_status` |
| 轨迹 `seq` 保序（0=最新）、重投 upsert、解析失败仍入库 | `TestTracking` |
| 轨迹条数上限 200：超出截断尾部 + 告警（审查 B4） | `test_tracking_events_are_capped` |
| 明确不存的三个字段：列不存在 + 载荷不落库 | `test_discarded_fields_never_persisted` |
| **D6** 物流回填绝不动 `internal_status` | `test_tracking_does_not_touch_internal_status` |
| 对账接点：采购完成推 `purchasing` | `test_live_backfill_writes_all_columns` |

**不在本文件**：派发与唯一派发约束（`test_r2_13b_dispatch.py`，验收④ 已在那里以
「绕全部应用层注入违反」+ 并发两路覆盖）、认证与越权（`test_r2_13a_plugin_auth.py`）。
"""

import json
import uuid
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.core.security import hash_password
from erp.plugin import classify, parse

from .test_identity_api import PASSWORD, _login

ADMIN = "r13d_admin"
TEAM = "R2-13d 回填与异常测试团队"
PLUGIN = "/api/v1/purchase-plugin"


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        conn.execute("DELETE FROM app.procurement_logistics_event WHERE team_id = %s", (team_id,))
        for t in ("plugin_instance", "buyer_account", "procurement_order", "order_check",
                  "order_line", "channel_order", "product", "purchaser", "notification",
                  "store"):  # fmt: skip
            conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.app_user WHERE username LIKE 'r13d_%'")
        uid = conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '回填测试管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute("DELETE FROM app.role WHERE team_id = %s AND name = '13d测试角色'", (team_id,))
        role_id = conn.execute(
            "INSERT INTO app.role (team_id, name) VALUES (%s, '13d测试角色') RETURNING id",
            (team_id,),
        ).fetchone()[0]
        for code in ("order.read", "procurement.read", "procurement.execute",
                     "procurement.buyer_account_read", "procurement.buyer_account_admin",
                     "procurement.plugin_instance_admin"):  # fmt: skip
            conn.execute(
                "INSERT INTO app.role_permission (role_id, permission_code) VALUES (%s, %s)",
                (role_id, code),
            )
        conn.execute("INSERT INTO app.user_role (user_id, role_id) VALUES (%s, %s)", (uid, role_id))
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        store_id = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, 'PL13D', '回填测试店', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
    return {"team": team_id, "user": uid, "store": store_id}


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> TestClient:
    from erp.main import app

    return TestClient(app)


# ── 造数助手（形状同 13a 用例；测的是端点语义，不是建单链）──


def _reset(conn: psycopg.Connection, seeded: dict[str, int]) -> None:
    team = (seeded["team"],)
    conn.execute("DELETE FROM app.procurement_logistics_event WHERE team_id = %s", team)
    for t in ("plugin_instance", "buyer_account", "procurement_order", "order_check",
              "order_line", "channel_order", "product", "notification"):  # fmt: skip
        conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (seeded["team"],))


SHIP_TO = (
    '{"name": "Jane Roe", "phone": "+1-555-0100", "address1": "123 Main St",'
    ' "city": "Marion", "state": "AL", "postalCode": "%s", "country": "US"}'
)


def _mk_order(
    conn: psycopg.Connection,
    seeded: dict[str, int],
    *,
    postcode: str = "36759",
    asins: tuple[str, ...] = ("B0PLUG13D",),
) -> dict[str, Any]:
    row = conn.execute(
        "INSERT INTO app.channel_order (team_id, store_id, channel_order_no, order_date,"
        " channel_status, customer, ship_to, order_total, item_count, pulled_at,"
        " internal_status, has_flag)"
        " VALUES (%s, %s, %s, now() - interval '1 day', 'Created', '{}',"
        "         %s::jsonb, 10, 1, now(), 'checked', false)"
        " RETURNING id, order_date, channel_order_no",
        (seeded["team"], seeded["store"], f"PL13D-{uuid.uuid4().hex[:8]}", SHIP_TO % postcode),
    ).fetchone()
    refs: list[str] = []
    for idx, asin in enumerate(asins, start=1):
        ref = f"{asin}-{uuid.uuid4().hex[:6]}"
        product_id = conn.execute(
            "INSERT INTO app.product (team_id, source_channel, source_ref, title)"
            " VALUES (%s, 'amazon', %s, '回填测试商品') RETURNING id",
            (seeded["team"], ref),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO app.order_line (order_id, order_date, team_id, channel_line_no,"
            " channel_sku, product_id, qty, unit_price, line_status)"
            " VALUES (%s, %s, %s, %s, 'M0000001', %s, 1, 9.99, 'created')",
            (row[0], row[1], seeded["team"], str(idx), product_id),
        )
        refs.append(ref)
    return {"id": int(row[0]), "date": row[1], "no": row[2], "asins": refs}


def _mk_account(conn: psycopg.Connection, seeded: dict[str, int]) -> dict[str, Any]:
    tag = uuid.uuid4().hex[:8]
    account_id = conn.execute(
        "INSERT INTO app.buyer_account (team_id, label, site, external_customer_id, status)"
        " VALUES (%s, %s, 'amazon_com', %s, 'active') RETURNING id",
        (seeded["team"], f"指纹浏览器-{tag}", f"A{tag.upper()}"),
    ).fetchone()[0]
    return {"id": int(account_id), "customerId": f"A{tag.upper()}"}


def _mk_po(
    conn: psycopg.Connection,
    seeded: dict[str, int],
    order: dict[str, Any],
    account_id: int,
    *,
    status: str = "assigned",
    purchase_order_ref: str | None = None,
) -> int:
    return int(
        conn.execute(
            "INSERT INTO app.procurement_order (team_id, store_id, order_id, order_date,"
            " status, buyer_account_id, assignee_kind, assigned_at, purchase_order_ref,"
            " purchased_at) VALUES (%s, %s, %s, %s, %s, %s, 'none', now(), %s::text,"
            " CASE WHEN %s::text IS NULL THEN NULL ELSE now() END) RETURNING id",
            (
                seeded["team"],
                seeded["store"],
                order["id"],
                order["date"],
                status,
                account_id,
                purchase_order_ref,
                purchase_order_ref,
            ),
        ).fetchone()[0]
    )


def _issue(
    client: TestClient, auth: dict[str, str], account_id: int, *, exec_mode: str = "live"
) -> dict[str, Any]:
    """签发一枚实例令牌。要 `live` 的话**必须签发后再 PATCH 升档**。

    签发端点只收两个演练档（审查 B2：一步直签 live 会打穿「升 live 须显式 PATCH」）。
    本用例文件要测的是 live 档的回填语义，故照真实运营路径走一遍升档——
    直接 `json={"exec_mode": "live"}` 会 422。
    """
    issue_mode = "stop_before_payment" if exec_mode == "live" else exec_mode
    r = client.post(
        f"/api/v1/buyer-accounts/{account_id}/plugin-instances",
        headers=auth,
        json={"exec_mode": issue_mode},
    )
    assert r.status_code == 201, r.text
    issued = dict(r.json())
    if exec_mode != issue_mode:
        up = client.patch(
            f"/api/v1/plugin-instances/{issued['id']}", headers=auth, json={"exec_mode": exec_mode}
        )
        assert up.status_code == 200, up.text
    return issued


def _h(instance: dict[str, Any]) -> dict[str, str]:
    return {"X-Plugin-Instance": str(instance["id"]), "X-Plugin-Token": instance["token"]}


def _setup(
    client: TestClient,
    migrated_db: str,
    seeded: dict[str, int],
    *,
    exec_mode: str = "live",
    status: str = "assigned",
    postcode: str = "36759",
    asins: tuple[str, ...] = ("B0PLUG13D",),
    purchase_order_ref: str | None = None,
) -> dict[str, Any]:
    """一张已派给本实例的执行单 + 一枚可用令牌。返回造数结果供断言。"""
    auth = _login(client, ADMIN, PASSWORD)
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        _reset(conn, seeded)
        account = _mk_account(conn, seeded)
        order = _mk_order(conn, seeded, postcode=postcode, asins=asins)
        po = _mk_po(
            conn, seeded, order, account["id"], status=status,
            purchase_order_ref=purchase_order_ref,
        )  # fmt: skip
    instance = _issue(client, auth, account["id"], exec_mode=exec_mode)
    return {"account": account, "order": order, "po": po, "instance": instance}


def _backfill_body(env: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": env["po"],
        "platformOrderNo": "111-5958998-9658617",
        "asins": ",".join(env["order"]["asins"]),
        "deliveryTime": "2026-08-07",
        "origDeliveryTime": "Thursday, August 7",
        "creditCardNumber": "7883",
        "mainPostCode": "36759",
        "extPostCode": "",
        "shipping": "$0.00",
        "totalBeforeTax": "$9.99",
        "tax": "$0.80",
        "total": "$10.79",
        "execMode": env["instance"].get("exec_mode", "live"),
    }
    body.update(overrides)
    return body


def _po_row(migrated_db: str, po_id: int) -> dict[str, Any]:
    with psycopg.connect(migrated_db, row_factory=psycopg.rows.dict_row) as conn:
        row = conn.execute("SELECT * FROM app.procurement_order WHERE id = %s", (po_id,)).fetchone()
    assert row is not None
    return dict(row)


def _notifications(migrated_db: str, po_id: int) -> list[tuple[str, str]]:
    with psycopg.connect(migrated_db) as conn:
        return [
            (r[0], r[1])
            for r in conn.execute(
                "SELECT severity, coalesce(body,'') FROM app.notification"
                " WHERE object_type = 'procurement_order' AND object_id = %s",
                (str(po_id),),
            ).fetchall()
        ]


# ── 金额解析：空值与解析失败是两件事 ──


class TestMoneyParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("$1,234.56", Decimal("1234.56")),
            ("$0.00", Decimal("0.00")),
            ("  $12.30  ", Decimal("12.30")),
            (9.99, Decimal("9.99")),
            ("", None),
            (None, None),
        ],
    )
    def test_parse_money_ok(self, raw: Any, expected: Decimal | None) -> None:
        got = parse.parse_money(raw)
        assert got == expected
        if expected is None:
            assert got is None, "空串/None 是「渠道没给」，不是 0"

    @pytest.mark.parametrize("raw", ["abc", "$", "12.3.4", "1,23", "9 99", True])
    def test_parse_money_rejects_garbage(self, raw: Any) -> None:
        """读不懂就抛——**绝不返回 0**（07:150-153 点名的那处坑）。"""
        with pytest.raises(parse.MoneyParseError):
            parse.parse_money(raw)

    def test_zero_and_missing_are_distinguishable(self) -> None:
        """`"$0.00"` 是「运费确实是 0」，`""` 是「渠道没给」。两者绝不能混为一谈。"""
        assert parse.parse_money("$0.00") == Decimal(0)
        assert parse.parse_money("") is None

    @pytest.mark.parametrize("raw", ["-$3.20", "-1,234.56", -0.01, -5])
    def test_negative_amount_is_a_parse_error(self, raw: Any) -> None:
        """负金额 = 抓错行，走异常通道而不是入库（审查 B5）。

        本文件解析的三项是成本 / 税 / 运费，业务上都非负；出现负数只可能是抓到了
        「优惠 -$5.00」那类行，或渠道换了版式。**此前它是照单全收的**（原
        `test_parse_money_ok` 里那条 `("-$3.20", Decimal("-3.20"))` 正是把 bug 写成了
        期望）——而一笔负成本会让利润核算凭空多出两倍利润，且 `purchase_cost` 列上
        没有 CHECK 兜底（0025 只是 numeric(12,2)）。
        """
        with pytest.raises(parse.MoneyParseError):
            parse.parse_money(raw)

    def test_zero_survives_the_negative_guard(self) -> None:
        """负值闸不许误伤 0：`"$0.00"` / `"-0.00"` 都是合法的「确实是零」。"""
        assert parse.parse_money("$0.00") == Decimal(0)
        assert parse.parse_money("-0.00") == Decimal(0)

    @pytest.mark.parametrize(
        ("day", "clock", "expected"),
        [
            ("July 28, 2026", "8:42 AM", (2026, 7, 28, 8, 42)),
            ("July 26, 2026", "11:15 PM", (2026, 7, 26, 23, 15)),
            ("January 1, 2026", "12:05 AM", (2026, 1, 1, 0, 5)),
            ("March 3, 2026", "12:30 PM", (2026, 3, 3, 12, 30)),
            ("April 9, 2026", None, (2026, 4, 9, 0, 0)),
        ],
    )
    def test_parse_event_time(self, day: str, clock: str | None, expected: tuple[int, ...]) -> None:
        got = parse.parse_event_time(day, clock)
        assert got is not None
        assert (got.year, got.month, got.day, got.hour, got.minute) == expected

    @pytest.mark.parametrize(("day", "clock"), [(None, "8:42 AM"), ("", "8:42 AM"), ("啊", "x")])
    def test_parse_event_time_failures_return_none(self, day: str | None, clock: str) -> None:
        """解析失败**不抛**——事件仍要入库，原文两列照存（0046 头注）。"""
        assert parse.parse_event_time(day, clock) is None

    def test_postcode_matching(self) -> None:
        assert parse.postcode_matches("36759", "36759-0000")
        assert parse.postcode_matches("36759-1234", "36759")
        assert not parse.postcode_matches("36759", "90210")
        assert not parse.postcode_matches("", "36759"), "空邮编不算匹配（fail-closed）"

    def test_split_asins(self) -> None:
        assert parse.split_asins(" B1 , B2 ,, ") == ["B1", "B2"]
        assert parse.split_asins(None) == []


# ── 端点 2：live 档回填 ──


class TestLiveBackfill:
    def test_live_backfill_writes_all_columns(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """字段映射逐列 + 汇率坑 + 对账接点。

        `exchange_rate_locked` 必须是 **1.0 而不是 NULL**：`backfill_po` 那句
        `(SELECT exchange_rate FROM purchaser WHERE id = purchaser_id)` 在插件单上
        （`purchaser_id IS NULL`）取到的是 NULL，R2-08 过账时会静默取不到费率。
        """
        env = _setup(client, migrated_db, seeded)
        r = client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(env["instance"]),
            json=_backfill_body(env),
        )
        assert r.status_code == 200, r.text
        assert r.json()["code"] == 200

        row = _po_row(migrated_db, env["po"])
        assert row["purchase_order_ref"] == "111-5958998-9658617"
        assert row["purchase_cost"] == Decimal("9.99")
        assert row["tax_amount"] == Decimal("0.80")
        assert row["freight_cost"] == Decimal("0.00")
        assert row["payment_card_last4"] == "7883"
        assert str(row["delivery_est_date"]) == "2026-08-07"
        assert row["delivery_est_raw"] == "Thursday, August 7"
        assert row["purchase_platform"] == "amazon"
        assert row["purchase_currency"] == "USD"
        assert row["exchange_rate_locked"] == Decimal("1.0"), "同币种分支记 1.0，不能留 NULL"
        assert row["status"] == "purchased", "插件路径走 purchased，不写 backfilled（D5）"
        assert row["purchased_at"] is not None
        assert row["backfill_actor_kind"] == "plugin"
        assert row["backfill_actor_id"] == env["instance"]["id"]
        assert row["exception_kind"] is None, "全部核对通过时不该留异常分类"

        with psycopg.connect(migrated_db) as conn:
            internal = conn.execute(
                "SELECT internal_status FROM app.channel_order WHERE id = %s",
                (env["order"]["id"],),
            ).fetchone()[0]
        assert internal == "purchasing", "采购完成必须推渠道订单到 purchasing"

    def test_unparseable_amount_stays_null(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """**解析失败该列留 NULL，不是 0**，并落 `other` + critical 告警。

        写 0 会在利润核算里埋一个永远查不出来的错；留 NULL 加一条告警，人当天就看得见。
        """
        env = _setup(client, migrated_db, seeded)
        r = client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(env["instance"]),
            json=_backfill_body(env, tax="abc", total=None),
        )
        assert r.status_code == 200, r.text

        row = _po_row(migrated_db, env["po"])
        assert row["tax_amount"] is None, "解析失败的金额被写成了 0 或别的假数"
        assert row["purchase_cost"] == Decimal("9.99"), "一列读不懂不该拖累其余列"
        assert row["exception_kind"] == "other"
        assert "tax" in (row["exception_reason"] or "")
        assert any(sev == "critical" for sev, _ in _notifications(migrated_db, env["po"]))

    def test_negative_amount_stays_null_and_alerts(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """负金额走的是与「读不懂」同一条通道：留 NULL + `other` + 告警（审查 B5）。

        端到端这一半是承重的——只在 `parse` 层抛异常还不够，得确认调用方把它接住了
        （`_parse_amounts` 的 `except MoneyParseError`），而不是让它冒泡成 500 把整笔
        回填连同已经花出去的钱一起丢掉。
        """
        env = _setup(client, migrated_db, seeded)
        r = client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(env["instance"]),
            json=_backfill_body(env, shipping="-$4.00", total=None),
        )
        assert r.status_code == 200, r.text

        row = _po_row(migrated_db, env["po"])
        assert row["freight_cost"] is None, "负运费入库了"
        assert row["purchase_cost"] == Decimal("9.99"), "一列有问题不该拖累其余列"
        assert row["purchase_order_ref"] == "111-5958998-9658617", "钱花了照样记账（D2）"
        assert row["exception_kind"] == "other"
        assert "shipping" in (row["exception_reason"] or "")
        assert any(sev == "critical" for sev, _ in _notifications(migrated_db, env["po"]))

    def test_self_check_mismatch_still_records_amounts(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """**D2**：三项之和与 total 不平 → 落异常，但**金额与单号照实入库**。

        钱已经花了，不记账比记错更糟。
        """
        env = _setup(client, migrated_db, seeded)
        r = client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(env["instance"]),
            json=_backfill_body(env, total="$99.99"),
        )
        assert r.status_code == 200, r.text

        row = _po_row(migrated_db, env["po"])
        assert row["exception_kind"] == "other"
        assert row["purchase_cost"] == Decimal("9.99") and row["tax_amount"] == Decimal("0.80")
        assert row["purchase_order_ref"] == "111-5958998-9658617"
        assert row["status"] == "purchased", "自校验不平不改状态（D1）"

    def test_asin_mismatch_keeps_purchased(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """**D1 的直接证伪**：买错东西 → `product` 分类，但 status 仍是 `purchased`。

        改成 exception 会丢掉「已拍单」这个事实，运营看到会以为没拍然后重拍一次。
        """
        env = _setup(client, migrated_db, seeded)
        r = client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(env["instance"]),
            json=_backfill_body(env, asins="B0WRONGASIN"),
        )
        assert r.status_code == 200, r.text

        row = _po_row(migrated_db, env["po"])
        assert row["exception_kind"] == "product"
        assert row["status"] == "purchased"
        assert "B0WRONGASIN" in (row["exception_reason"] or "")
        assert any(sev == "critical" for sev, _ in _notifications(migrated_db, env["po"]))

    def test_postcode_mismatch_is_address(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        env = _setup(client, migrated_db, seeded)
        r = client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(env["instance"]),
            json=_backfill_body(env, mainPostCode="90210"),
        )
        assert r.status_code == 200, r.text
        row = _po_row(migrated_db, env["po"])
        assert row["exception_kind"] == "address"
        assert row["status"] == "purchased"

    def test_postcode_zip4_is_not_a_mismatch(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """`36759` 与 `36759-0000` 必须视为相同——假阳会让运营不再相信这类异常。"""
        env = _setup(client, migrated_db, seeded)
        r = client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(env["instance"]),
            json=_backfill_body(env, mainPostCode="36759", extPostCode="0000"),
        )
        assert r.status_code == 200, r.text
        assert _po_row(migrated_db, env["po"])["exception_kind"] is None

    def test_multiple_findings_pick_priority_kind_keep_all_text(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """同时命中多类：分类取优先序第一个（product），**原文全部保留**。"""
        env = _setup(client, migrated_db, seeded)
        r = client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(env["instance"]),
            json=_backfill_body(env, asins="B0WRONGASIN", mainPostCode="90210", total="$99.99"),
        )
        assert r.status_code == 200, r.text
        row = _po_row(migrated_db, env["po"])
        assert row["exception_kind"] == "product"
        reason = row["exception_reason"] or ""
        assert "B0WRONGASIN" in reason and "90210" in reason and "自校验" in reason


class TestIdempotency:
    def test_same_ref_is_a_noop(self, client: TestClient, migrated_db: str, seeded: dict) -> None:
        """同 `platformOrderNo` 重投：200 且**一列不动**（网络重试/插件重发）。"""
        env = _setup(client, migrated_db, seeded)
        body = _backfill_body(env)
        assert (
            client.post(
                f"{PLUGIN}/purchaseOrderFinishUpdate", headers=_h(env["instance"]), json=body
            ).status_code
            == 200
        )
        before = _po_row(migrated_db, env["po"])

        second = client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate", headers=_h(env["instance"]), json=body
        )
        assert second.status_code == 200, second.text
        assert second.json()["data"]["idempotent"] is True
        after = _po_row(migrated_db, env["po"])
        assert {k: v for k, v in after.items() if k != "updated_at"} == {
            k: v for k, v in before.items() if k != "updated_at"
        }

    def test_conflicting_ref_does_not_overwrite(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """异 `platformOrderNo`：**不覆盖原值** + `other` + critical 告警。

        这是重复下单的形状——静默覆盖等于把第一笔钱从账上抹掉。
        """
        env = _setup(client, migrated_db, seeded)
        client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(env["instance"]),
            json=_backfill_body(env),
        )
        r = client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(env["instance"]),
            json=_backfill_body(
                env, platformOrderNo="111-0000000-0000001", totalBeforeTax="$88.00"
            ),
        )
        assert r.status_code == 200, r.text
        row = _po_row(migrated_db, env["po"])
        assert row["purchase_order_ref"] == "111-5958998-9658617", "原单号被覆盖了"
        assert row["purchase_cost"] == Decimal("9.99"), "冲突时不得改写金额"
        assert row["exception_kind"] == "other"
        assert "111-0000000-0000001" in (row["exception_reason"] or "")
        assert any(sev == "critical" for sev, _ in _notifications(migrated_db, env["po"]))


class TestExecModes:
    def test_stop_before_payment_records_amounts_only(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """付款前停：金额抓得回来（验收⑥⑦），但**不写单号、不改状态、不推渠道订单**。"""
        env = _setup(client, migrated_db, seeded, exec_mode="stop_before_payment")
        r = client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(env["instance"]),
            json=_backfill_body(env, platformOrderNo=None, execMode="stop_before_payment"),
        )
        assert r.status_code == 200, r.text

        row = _po_row(migrated_db, env["po"])
        assert row["purchase_cost"] == Decimal("9.99") and row["tax_amount"] == Decimal("0.80")
        assert row["payment_card_last4"] == "7883"
        assert row["purchase_currency"] == "USD", "金额是 USD，币种列不能留默认的 CNY"
        assert row["purchase_order_ref"] is None and row["purchased_at"] is None
        assert row["status"] == "assigned", "演练不改状态——那笔钱确实没花"
        assert "stop_before_payment 演练" in (row["note"] or "")
        with psycopg.connect(migrated_db) as conn:
            internal = conn.execute(
                "SELECT internal_status FROM app.channel_order WHERE id = %s",
                (env["order"]["id"],),
            ).fetchone()[0]
        assert internal == "checked", "演练不得推渠道订单"

    def test_dry_run_writes_no_money_columns(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """只校验档：**一个金额列都不写**，只在 note 留一行痕迹。"""
        env = _setup(client, migrated_db, seeded, exec_mode="dry_run")
        r = client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(env["instance"]),
            json=_backfill_body(env, platformOrderNo=None, execMode="dry_run"),
        )
        assert r.status_code == 200, r.text

        row = _po_row(migrated_db, env["po"])
        for col in ("purchase_cost", "tax_amount", "freight_cost", "payment_card_last4",
                    "delivery_est_date", "delivery_est_raw", "purchase_order_ref"):  # fmt: skip
            assert row[col] is None, f"dry_run 写了 {col}"
        assert row["status"] == "assigned"
        assert "dry_run" in (row["note"] or "")

    def test_exec_mode_mismatch_is_rejected(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """插件回报的档与实例当前档不符 → 409，且**一列未写**。

        挡的是「实例已降档、插件仍按旧档在真下单」：放行就等于默许它继续按错档买。
        """
        env = _setup(client, migrated_db, seeded, exec_mode="stop_before_payment")
        r = client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(env["instance"]),
            json=_backfill_body(env, execMode="live"),
        )
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "PLUGIN_EXEC_MODE_MISMATCH"
        row = _po_row(migrated_db, env["po"])
        assert row["purchase_order_ref"] is None and row["purchase_cost"] is None

    @pytest.mark.parametrize(
        ("mode", "tag"),
        [("stop_before_payment", "stop_before_payment 演练"), ("dry_run", "dry_run 校验回传")],
    )
    def test_replay_does_not_stack_note_markers(
        self, client: TestClient, migrated_db: str, seeded: dict, mode: str, tag: str
    ) -> None:
        """同一请求重投两次，`note` 只留一行痕迹（审查 B3）。

        两个演练档都往 `note` 追加一行「谁跑的、核对结论是什么」。原先那行带秒级时间戳，
        于是**每次重投都追加一行**——而重投恰是插件侧最常见的形态（网络重试、页面刷新），
        一个卡在重试循环里的实例能把这一列刷成一堵墙，墙里的信息量与一行完全相同。
        """
        env = _setup(client, migrated_db, seeded, exec_mode=mode)
        body = _backfill_body(env, platformOrderNo=None, execMode=mode)
        for _ in range(2):
            r = client.post(
                f"{PLUGIN}/purchaseOrderFinishUpdate", headers=_h(env["instance"]), json=body
            )
            assert r.status_code == 200, r.text

        note = _po_row(migrated_db, env["po"])["note"] or ""
        assert note.count(tag) == 1, f"重投把 note 堆成了 {note.count(tag)} 行：{note!r}"
        assert len([ln for ln in note.splitlines() if ln.strip()]) == 1

    def test_pending_review_is_not_advanced_to_purchased(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """`pending_review` **不在**回填的可推进白名单里（审查 B7）。

        它是 13c 护栏拦下的专用停留位，本轮零转入路径（0045 只扩词表）。预先把它写进
        白名单等于替 13c 开好一条出边——而护栏拦下的单该怎么放行正是 13c 要定的事，
        不能由「插件说它买了」自动生效。金额与单号仍照实入库（D2：钱花了不记账更糟），
        停住的只有状态。
        """
        env = _setup(client, migrated_db, seeded, status="pending_review")
        r = client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(env["instance"]),
            json=_backfill_body(env),
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "pending_review"

        row = _po_row(migrated_db, env["po"])
        assert row["status"] == "pending_review", "护栏停留位被插件回填自动推走了"
        assert row["purchased_at"] is None
        assert row["purchase_order_ref"] == "111-5958998-9658617", "钱花了照样记账"
        assert row["purchase_cost"] == Decimal("9.99")

    def test_drill_with_channel_ref_is_recorded_as_purchase(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """演练档却带回了真实渠道单号 = **钱已经花出去了的实证**。

        比实例上配的档位更硬：按已下单入账（D2）并升 critical 告警。当成演练处理会把
        这张单留在 `assigned`，下一轮派发再派一次——那就是二次扣款。
        """
        env = _setup(client, migrated_db, seeded, exec_mode="stop_before_payment")
        r = client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(env["instance"]),
            json=_backfill_body(env, execMode="stop_before_payment"),
        )
        assert r.status_code == 200, r.text
        row = _po_row(migrated_db, env["po"])
        assert row["purchase_order_ref"] == "111-5958998-9658617"
        assert row["status"] == "purchased"
        assert row["exception_kind"] == "other"
        assert any(sev == "critical" for sev, _ in _notifications(migrated_db, env["po"]))


# ── 端点 3：拍单失败（验收⑧ 服务端半段）──


class TestTaskFailure:
    def test_failure_writes_zero_money_columns(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """**验收⑧ 服务端半段**：拍单失败 → 转异常，**全部金额列仍为 NULL**。"""
        env = _setup(client, migrated_db, seeded)
        r = client.post(
            f"{PLUGIN}/updateOrderStatus",
            headers=_h(env["instance"]),
            json={"id": env["po"], "status": 99, "failReason": "商品无库存"},
        )
        assert r.status_code == 200, r.text

        row = _po_row(migrated_db, env["po"])
        assert row["status"] == "exception"
        assert row["exception_kind"] == "stock"
        assert row["exception_reason"] == "商品无库存", "原文一字不改"
        for col in ("purchase_cost", "tax_amount", "freight_cost", "purchase_order_ref",
                    "payment_card_last4", "purchased_at"):  # fmt: skip
            assert row[col] is None, f"拍单失败却写了 {col}"

    def test_pending_review_is_not_pushed_to_exception(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """`_FAILABLE_FROM` 同样不含 `pending_review`（审查 B7 的另一半）。

        两个白名单要一起收窄：只收窄采购那条、留着失败这条，等于把 13c 的停留位换成
        `exception` 再放回自动派发的视野之外——出边仍然是被这一轮悄悄定死的。
        分类与原文照落（那是给人看的线索），状态留给 13c 处置。
        """
        env = _setup(client, migrated_db, seeded, status="pending_review")
        r = client.post(
            f"{PLUGIN}/updateOrderStatus",
            headers=_h(env["instance"]),
            json={"id": env["po"], "status": 99, "failReason": "商品无库存"},
        )
        assert r.status_code == 200, r.text
        row = _po_row(migrated_db, env["po"])
        assert row["status"] == "pending_review", "护栏停留位被端点 3 推成了 exception"
        assert row["exception_kind"] == "stock" and row["exception_reason"] == "商品无库存"

    def test_fail_content_alias_is_accepted(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """请求体键名未逐字取证，`failContent` 与 `failReason` 同义——两个都收。"""
        env = _setup(client, migrated_db, seeded)
        r = client.post(
            f"{PLUGIN}/updateOrderStatus",
            headers=_h(env["instance"]),
            json={"id": env["po"], "status": 99, "failContent": "下单验证超时"},
        )
        assert r.status_code == 200, r.text
        row = _po_row(migrated_db, env["po"])
        assert row["exception_kind"] == "checkout" and row["exception_reason"] == "下单验证超时"

    def test_failure_after_purchase_keeps_status(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """已拍单的单收到 99：**status 不动**，只落分类 + 升 critical 告警。

        把它改成 exception 会抹掉「已拍单」这个事实，运营据此重拍就是二次扣款。
        """
        env = _setup(client, migrated_db, seeded, status="purchased", purchase_order_ref="111-1-1")
        r = client.post(
            f"{PLUGIN}/updateOrderStatus",
            headers=_h(env["instance"]),
            json={"id": env["po"], "status": 99, "failReason": "回传订单失败"},
        )
        assert r.status_code == 200, r.text
        row = _po_row(migrated_db, env["po"])
        assert row["status"] == "purchased"
        assert row["exception_kind"] == "checkout"
        assert row["purchase_order_ref"] == "111-1-1"
        assert any(sev == "critical" for sev, _ in _notifications(migrated_db, env["po"]))

    def test_status_other_than_99_is_rejected(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """厂商的数字状态机不进我方模型：只认 99，其余 422。"""
        env = _setup(client, migrated_db, seeded)
        r = client.post(
            f"{PLUGIN}/updateOrderStatus",
            headers=_h(env["instance"]),
            json={"id": env["po"], "status": 88, "failReason": "x"},
        )
        assert r.status_code == 422, r.text

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("地址保存超时", "address"),
            ("地址列表加载超时", "address"),
            ("地址信息不完整，缺少区相关信息", "address"),
            ("未匹配到洲信息，请检查订单", "address"),
            ("商品无库存", "stock"),
            ("商品库存不足或已售罄", "stock"),
            ("未找到加入购物车按钮", "stock"),
            ("加入购物车失败：页面未跳转", "stock"),
            ("商品 B0F8NB1T8Y 配送方式非 FBA", "product"),
            ("商品 B0F8NB1T8Y 属于捆绑商品（bundle），请手动拍单", "product"),
            ("无法修改商品购买数量", "product"),
            ("预计送达时间超过 7 天", "delivery"),
            ("预计送达时间解析失败，用户主动跳过订单", "delivery"),
            ("商品 B0F8NB1T8Y 验证失败，请确认商品状态或库存是否充足", "checkout"),
            ("下单验证超时", "checkout"),
            ("回传订单失败，请手动复制AMZ订单号回填至系统", "checkout"),
            ("处理商品失败: TypeError", "other"),
            # 表外三条 → 兜底 other（新文案静默落 other 是设计内的行为，不是失灵）
            ("Amazon 服务器返回 503", "other"),
            ("", "other"),
            (None, "other"),
        ],
    )
    def test_classify_covers_production_failures(self, raw: str | None, expected: str) -> None:
        """17 条厂商生产实测原文逐条对判图纸 `07:154-166` 的归类口径。"""
        assert classify.classify_failure(raw) == expected


# ── 端点 4：渠道回报 91/92 ──


class TestChannelStatus:
    @pytest.mark.parametrize(("code", "kind"), [(91, "channel_cancelled"), (92, "order_not_found")])
    def test_channel_status_keeps_status(
        self, client: TestClient, migrated_db: str, seeded: dict, code: int, kind: str
    ) -> None:
        """**D1**：91/92 发生在已拍单之后 → 只落 `exception_kind`，`status` 一字不动。"""
        env = _setup(client, migrated_db, seeded, status="purchased", purchase_order_ref="111-2-2")
        r = client.post(
            f"{PLUGIN}/updateAmzOrderStatus",
            headers=_h(env["instance"]),
            json={"id": env["po"], "orderNo": env["order"]["no"], "status": code},
        )
        assert r.status_code == 200, r.text
        row = _po_row(migrated_db, env["po"])
        assert row["exception_kind"] == kind
        assert row["status"] == "purchased", "改状态会抹掉「已拍单」这个事实"
        assert str(code) in (row["exception_reason"] or "")
        assert any(sev == "critical" for sev, _ in _notifications(migrated_db, env["po"]))

    def test_order_no_mismatch_does_not_block(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """`orderNo` 只做一致性校验：不符仅记 warn 不拒——渠道单号漂移不该挡住
        「渠道那边把这单取消了」这个要发起退款的信号入库。"""
        env = _setup(client, migrated_db, seeded, status="purchased", purchase_order_ref="111-3-3")
        r = client.post(
            f"{PLUGIN}/updateAmzOrderStatus",
            headers=_h(env["instance"]),
            json={"id": env["po"], "orderNo": "WM-DRIFTED-0001", "status": 91},
        )
        assert r.status_code == 200, r.text
        assert _po_row(migrated_db, env["po"])["exception_kind"] == "channel_cancelled"

    def test_replay_does_not_pile_up_reason(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """重投同一条回报不该把 `exception_reason` 堆成一堵墙（原文追加是幂等的）。"""
        env = _setup(client, migrated_db, seeded, status="purchased", purchase_order_ref="111-4-4")
        payload = {"id": env["po"], "status": 91}
        for _ in range(3):
            assert (
                client.post(
                    f"{PLUGIN}/updateAmzOrderStatus", headers=_h(env["instance"]), json=payload
                ).status_code
                == 200
            )
        assert (_po_row(migrated_db, env["po"])["exception_reason"] or "").count("91=已取消") == 1


# ── 端点 7：物流回填 + 轨迹事件 ──


TRACKING_JSON = (
    '[{"day":"July 28, 2026","time":"8:42 AM","tracking_info":"Package arrived at carrier'
    ' facility","state":"CA","city":"Los Angeles"},'
    '{"day":"July 26, 2026","time":"11:15 PM","tracking_info":"Package departed from facility",'
    '"state":"TX","city":"Dallas"}]'
)


def _tracking_body(po_id: int, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "orderId": po_id,
        "trackingNumber": "1Z999AA10123456784",
        "carrier": "UPS",
        "card": "7883",
        "trackingJson": TRACKING_JSON,
        "deliveryDate": "2026-08-07",
        "origDeliveryDate": "Thursday, August 7",
        "shipping": "$0.00",
        "totalBeforeTax": "$9.99",
        "tax": "$0.80",
        "total": "$10.79",
    }
    body.update(overrides)
    return body


def _events(migrated_db: str, po_id: int) -> list[dict[str, Any]]:
    with psycopg.connect(migrated_db, row_factory=psycopg.rows.dict_row) as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM app.procurement_logistics_event"
                " WHERE procurement_order_id = %s ORDER BY seq",
                (po_id,),
            ).fetchall()
        ]


class TestTracking:
    def test_tracking_sets_shipped_and_writes_events(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """`purchased → shipped` + 轨迹入库。**`seq` = 渠道下标，0 = 最新**。"""
        env = _setup(client, migrated_db, seeded, status="purchased", purchase_order_ref="111-5-5")
        r = client.post(
            f"{PLUGIN}/updateTrackingInfo",
            headers=_h(env["instance"]),
            json=_tracking_body(env["po"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["events"] == 2

        row = _po_row(migrated_db, env["po"])
        assert row["status"] == "shipped" and row["shipped_at"] is not None
        assert row["tracking_no"] == "1Z999AA10123456784" and row["carrier"] == "UPS"
        # 端点 2 没落过的金额洞由本端点补上
        assert row["purchase_cost"] == Decimal("9.99") and row["tax_amount"] == Decimal("0.80")

        events = _events(migrated_db, env["po"])
        assert [e["seq"] for e in events] == [0, 1]
        assert events[0]["description"].startswith("Package arrived")
        assert events[0]["city"] == "Los Angeles" and events[0]["state_code"] == "CA"
        assert events[0]["occurred_at"] is not None
        assert events[0]["occurred_at"] > events[1]["occurred_at"], "seq 0 是最新的那条"

    def test_amounts_do_not_overwrite_endpoint2(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """端点 2 落过的金额是权威，物流回填**只补空洞不覆盖**。"""
        env = _setup(client, migrated_db, seeded)
        client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(env["instance"]),
            json=_backfill_body(env),
        )
        r = client.post(
            f"{PLUGIN}/updateTrackingInfo",
            headers=_h(env["instance"]),
            json=_tracking_body(env["po"], totalBeforeTax="$77.00", tax="$7.00"),
        )
        assert r.status_code == 200, r.text
        row = _po_row(migrated_db, env["po"])
        assert row["purchase_cost"] == Decimal("9.99") and row["tax_amount"] == Decimal("0.80")

    def test_replay_upserts_events(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """重投同一批轨迹 → upsert，不堆重复行（唯一键 `(po_id, seq)`）。"""
        env = _setup(client, migrated_db, seeded, status="purchased", purchase_order_ref="111-6-6")
        body = _tracking_body(env["po"])
        client.post(f"{PLUGIN}/updateTrackingInfo", headers=_h(env["instance"]), json=body)
        updated = TRACKING_JSON.replace("Package arrived at carrier facility", "Out for delivery")
        client.post(
            f"{PLUGIN}/updateTrackingInfo",
            headers=_h(env["instance"]),
            json=_tracking_body(env["po"], trackingJson=updated),
        )
        events = _events(migrated_db, env["po"])
        assert len(events) == 2, "重投把轨迹堆成了重复行"
        assert events[0]["description"] == "Out for delivery"

    def test_unparseable_event_time_is_still_stored(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """`occurred_at` 解析失败**不丢事件**，原文两列照存（丢事件等于丢轨迹）。"""
        env = _setup(client, migrated_db, seeded, status="purchased", purchase_order_ref="111-7-7")
        weird = '[{"day":"昨天","time":"稍早","tracking_info":"Label created"}]'
        r = client.post(
            f"{PLUGIN}/updateTrackingInfo",
            headers=_h(env["instance"]),
            json=_tracking_body(env["po"], trackingJson=weird),
        )
        assert r.status_code == 200, r.text
        events = _events(migrated_db, env["po"])
        assert len(events) == 1
        assert events[0]["occurred_at"] is None
        assert events[0]["raw_day"] == "昨天" and events[0]["raw_time"] == "稍早"

    def test_tracking_events_are_capped(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """`trackingJson` 条数有上限：201 条只落 200 条 + warn 告警（审查 B4）。

        这是**外部输入且无上界**的字段——一条畸形/恶意载荷能在一个请求里塞进几万条，
        每条一次 upsert，把一次回填变成长事务并撑爆表。截断丢的是尾部（`seq` 0 是最新，
        丢的是最老的那截），且轨迹本就是可重抓的投影（0046 头注）；运单号照常回填。
        """
        env = _setup(client, migrated_db, seeded, status="purchased", purchase_order_ref="111-C-C")
        flood = json.dumps(
            [
                {"day": "July 28, 2026", "time": "8:42 AM", "tracking_info": f"事件 {i}"}
                for i in range(201)
            ]
        )
        r = client.post(
            f"{PLUGIN}/updateTrackingInfo",
            headers=_h(env["instance"]),
            json=_tracking_body(env["po"], trackingJson=flood),
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["events"] == 200

        events = _events(migrated_db, env["po"])
        assert len(events) == 200, f"落库 {len(events)} 条，上限没生效"
        assert [e["seq"] for e in events][:2] == [0, 1]
        assert events[0]["description"] == "事件 0", "截断丢的应是尾部（最老），不是头部"
        assert _po_row(migrated_db, env["po"])["tracking_no"] == "1Z999AA10123456784"
        assert any(sev == "warn" for sev, _ in _notifications(migrated_db, env["po"]))

    def test_bad_tracking_json_keeps_tracking_no(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """`trackingJson` 整体解析失败 → 主表照写 + warn 告警。

        运单号是承重的，事件是可重抓的投影——不能因为轨迹解析不出来就把运单号一起丢掉。
        """
        env = _setup(client, migrated_db, seeded, status="purchased", purchase_order_ref="111-8-8")
        r = client.post(
            f"{PLUGIN}/updateTrackingInfo",
            headers=_h(env["instance"]),
            json=_tracking_body(env["po"], trackingJson="{不是数组"),
        )
        assert r.status_code == 200, r.text
        assert _po_row(migrated_db, env["po"])["tracking_no"] == "1Z999AA10123456784"
        assert _events(migrated_db, env["po"]) == []
        assert any(sev == "warn" for sev, _ in _notifications(migrated_db, env["po"]))

    def test_no_jump_from_assigned_to_shipped(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """没拍单哪来的运单：`assigned` 收到运单号也**不许**跳 `shipped`。"""
        env = _setup(client, migrated_db, seeded, status="assigned")
        r = client.post(
            f"{PLUGIN}/updateTrackingInfo",
            headers=_h(env["instance"]),
            json=_tracking_body(env["po"]),
        )
        assert r.status_code == 200, r.text
        row = _po_row(migrated_db, env["po"])
        assert row["status"] == "assigned" and row["shipped_at"] is None
        assert row["tracking_no"] == "1Z999AA10123456784", "运单号还是要收下"

    def test_tracking_does_not_touch_internal_status(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """**D6 的直接证伪**：`channel_order.internal_status` 一字未动。

        那一列是「我方已发货给 Walmart 买家」，亚马逊侧包裹在路上 ≠ 我方已发货。
        """
        env = _setup(client, migrated_db, seeded, status="purchased", purchase_order_ref="111-9-9")
        with psycopg.connect(migrated_db) as conn:
            before = conn.execute(
                "SELECT internal_status FROM app.channel_order WHERE id = %s",
                (env["order"]["id"],),
            ).fetchone()[0]
        client.post(
            f"{PLUGIN}/updateTrackingInfo",
            headers=_h(env["instance"]),
            json=_tracking_body(env["po"]),
        )
        with psycopg.connect(migrated_db) as conn:
            after = conn.execute(
                "SELECT internal_status FROM app.channel_order WHERE id = %s",
                (env["order"]["id"],),
            ).fetchone()[0]
        assert after == before

    def test_discarded_fields_never_persisted(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """`trackingHtml` / `trackingUrl` / `receiptPicture`：**列不存在 + 载荷不落库**。

        没有这条，下一个照抄厂商字段表的人会顺手把整页 base64 HTML 加成一列
        （单条数十至数百 KB、万单即 GB 级）。
        """
        env = _setup(
            client, migrated_db, seeded, status="purchased", purchase_order_ref="111-10-10"
        )
        needle = "QkFTRTY0LUhUTUwtU05JUFBFVA"
        r = client.post(
            f"{PLUGIN}/updateTrackingInfo",
            headers=_h(env["instance"]),
            json=_tracking_body(
                env["po"],
                trackingHtml=needle,
                trackingUrl="https://www.amazon.com/progress-tracker/package/x",
                receiptPicture="https://m.media-amazon.com/images/delivery/x.jpg",
            ),
        )
        assert r.status_code == 200, r.text

        with psycopg.connect(migrated_db) as conn:
            cols = {
                c[0]
                for c in conn.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = 'app' AND table_name = 'procurement_logistics_event'"
                ).fetchall()
            }
            assert cols.isdisjoint({"tracking_html", "tracking_url", "receipt_picture"})
            for table in ("procurement_order", "procurement_logistics_event"):
                hits = conn.execute(
                    f"SELECT count(*) FROM app.{table} t WHERE to_jsonb(t)::text LIKE %s",
                    (f"%{needle}%",),
                ).fetchone()[0]
                assert hits == 0, f"{table} 里落下了本该丢弃的整页 HTML"
