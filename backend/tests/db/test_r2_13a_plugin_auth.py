"""R2-13 13a 验收（经 R2-17 17c/17d 改形）：插件契约端点组 + 共享 token 认证 + 人工指派拉取。

17c 共享 token 取代逐实例签发（休眠）；17d 拉取语义改「取本账号名下已指派单」、
customerId 首见即 active（pending_claim 认领仪式与洪水闸拆除）。原验收③的
「A 实例取不到 B 账号的任务」收敛为**跨团队必败**（团队边界不动）。

| 判据 / 纪律 | 用例 |
|---|---|
| **路由正反两面**：带 A 的 customerId 只拿到 A 名下已指派的单
  | `test_pull_routes_by_customer_id` |
| 跨团队 customerId 必败**且不可探测**
  | `test_cross_team_customer_id_is_indistinguishable_from_unknown` |
| 17d：首见即 active（空行）+ 通知 + **审计**，无指派即无任务
  | `test_first_sight_registers_active_and_pulls_nothing` |
| 同一浏览器换号 ⇒ 路由跟随（token 不绑账号）
  | `test_same_instance_switches_customer_id_and_routing_follows` |
| 首见登记并发安全（ON CONFLICT，恰好一行） | `test_first_sight_registration_is_concurrency_safe` |
| 休眠遗留 `rejected` 行仍粘住：不出任务、不复活、customerId 不可挪
  | `test_legacy_rejected_row_stays_sticky` |
| **跨团队**的 po_id 回填必败且**一列未变** | `test_backfill_other_team_po_fails` |
| 不泄露存在性：不存在的 id 与他团队的 id 同码同状态 | `test_nonexistent_po_same_error_as_unowned` |
| 认证失败一律 401 同码（错 token/空头/缺头） | `test_bad_credentials_all_401` |
| 散列不能当令牌用（共享摘要 + 库里 hash 双探针） | `test_stored_hash_is_not_a_usable_token` |
| **D4 承重**：插件路径的 RLS 没被 `system_tx` 短路 | `test_rls_still_on_for_plugin_path` |
| 拉取天然幂等：重复拉回同一批已指派单 | `test_pull_is_idempotent` |
| 下发载荷逐字段对齐插件实读 | `test_pull_payload_shape` |
| 缺 ASIN 已指派单不下发、不猜、有告警 | `test_missing_asin_task_not_dispatched` |
| 账号闸只挡拉取、不挡写路径认证 | `test_paused_account_pulls_nothing_but_writes_authenticate` |
| 拉取回写账号 last_seen_at；**实例观察列零回写**（17c 放弃机器归因）
  | `test_pull_touches_account_last_seen_but_not_instance` |
| 端点 6 只列已拍单且属本次解析出的账号的 | `test_sync_orders_scope` |

**不在本文件**：休眠的自动派发算法（`test_r2_13b_dispatch.py` 直调 dispatch 照测）、
回填/异常/物流语义（13d）、cookies 删净（`test_r2_13_no_cookie_chain.py`）、
共享 token 的验收④正反面（`test_r2_17c_shared_token.py`）、人工指派端点
（`test_r2_17d_manual_assign.py`）。
"""

import asyncio
import hashlib
import os
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from erp.core.db import ctx_tx, get_session_factory
from erp.core.security import hash_password
from erp.plugin import service
from erp.plugin.auth import PluginPrincipal

from .test_identity_api import PASSWORD, _login

# 17c（D-Q73 ③）：全文件经共享 token 过闸；实例签发端点保留休眠仍被 _issue 真调，
# 但请求头一律走共享形态（_h 不再读实例字段）。
SHARED_TOKEN = "r13a-shared-token-0123456789abcdef"
ADMIN = "r13a_admin"
TEAM = "R2-13a 插件认证测试团队"
FOREIGN_TEAM = "R2-13a 跨团队取证团队"
PLUGIN = "/api/v1/purchase-plugin"


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        for t in ("plugin_instance", "buyer_account", "procurement_order", "order_check",
                  "order_line", "channel_order", "product", "purchaser", "store"):  # fmt: skip
            conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.app_user WHERE username LIKE 'r13a_%'")
        uid = conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '插件实例管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute("DELETE FROM app.role WHERE team_id = %s AND name = '13a测试角色'", (team_id,))
        role_id = conn.execute(
            "INSERT INTO app.role (team_id, name) VALUES (%s, '13a测试角色') RETURNING id",
            (team_id,),
        ).fetchone()[0]
        for code in (
            "order.read", "procurement.read", "procurement.execute",
            "procurement.buyer_account_read", "procurement.buyer_account_admin",
            "procurement.plugin_instance_admin",
        ):  # fmt: skip
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
            " VALUES (%s, %s, 'PL13A', '插件测试店', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
        # 跨团队判据要造「别人的账号 / 别人的插件单」。**专用团队而不是共享的
        # 测试团队A/B**：那两个团队被全仓多个用例共用，为清理方便整团队清空会误伤邻居；
        # 本团队由本文件独占，`_reset_foreign` 可以放心清空。
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (FOREIGN_TEAM,)
        )
        foreign_id = conn.execute(
            "SELECT id FROM app.team WHERE name = %s", (FOREIGN_TEAM,)
        ).fetchone()[0]
        conn.execute("DELETE FROM app.procurement_order WHERE team_id = %s", (foreign_id,))
        conn.execute("DELETE FROM app.channel_order WHERE team_id = %s", (foreign_id,))
        conn.execute("DELETE FROM app.store WHERE team_id = %s AND code = 'PL13AF'", (foreign_id,))
        foreign_store = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, 'PL13AF', '他团队插件测试店', true) RETURNING id",
            (foreign_id, channel_id),
        ).fetchone()[0]
        conn.execute("DELETE FROM app.system_config WHERE key = 'procurement.plugin_team_id'")
        conn.execute(
            "INSERT INTO app.system_config (key, value) VALUES"
            " ('procurement.plugin_team_id', to_jsonb(%s::bigint))",
            (team_id,),
        )
        # 档位残值清除：13d/17c 的模块会写 procurement.plugin_exec_mode（可能是 live），
        # 而本文件的判据全部押在「未配置 ⇒ 回落 stop_before_payment」上。库跨 pytest
        # 进程持久，不清就是隐式模块序耦合。
        conn.execute("DELETE FROM app.system_config WHERE key = 'procurement.plugin_exec_mode'")
    return {
        "team": team_id,
        "user": uid,
        "store": store_id,
        "foreign": foreign_id,
        "foreign_store": foreign_store,
    }


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


# ── 造数助手 ──


def _reset(conn: psycopg.Connection, seeded: dict[str, int]) -> None:
    """清空本团队的单/账号/实例/商品——候选按 created_at 取最老的，残留会被下个用例捞走。"""
    for t in ("plugin_instance", "buyer_account", "procurement_order", "order_check",
              "order_line", "channel_order", "product"):  # fmt: skip
        conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (seeded["team"],))


SHIP_TO = (
    '{"name": "Jane Roe", "phone": "+1-555-0100", "address1": "123 Main St",'
    ' "address2": "Apt 4", "city": "Marion", "state": "AL",'
    ' "postalCode": "36759", "country": "%s"}'
)


def _mk_order(
    conn: psycopg.Connection,
    seeded: dict[str, int],
    *,
    country: str = "US",
    lines: tuple[tuple[str | None, int], ...] = (("B0PLUGIN01", 1),),
) -> dict[str, Any]:
    """建一张四检已过的渠道订单 + 订单行（行的 ASIN 走 product.source_ref）。

    `lines` 里 asin 传 None = 该行没有关联商品，用于「缺 ASIN 不猜」那条判据。
    """
    row = conn.execute(
        "INSERT INTO app.channel_order (team_id, store_id, channel_order_no, order_date,"
        " channel_status, customer, ship_to, order_total, item_count, pulled_at,"
        " internal_status, has_flag)"
        " VALUES (%s, %s, %s, now() - interval '1 day', 'Created', '{}',"
        "         %s::jsonb, 10, 1, now(), 'checked', false)"
        " RETURNING id, order_date, channel_order_no",
        (
            seeded["team"],
            seeded["store"],
            f"PL13A-{uuid.uuid4().hex[:8]}",
            SHIP_TO % country,
        ),
    ).fetchone()
    for idx, (asin, qty) in enumerate(lines, start=1):
        product_id = None
        if asin is not None:
            product_id = conn.execute(
                "INSERT INTO app.product (team_id, source_channel, source_ref, title)"
                " VALUES (%s, 'amazon', %s, '插件测试商品') RETURNING id",
                (seeded["team"], f"{asin}-{uuid.uuid4().hex[:6]}"),
            ).fetchone()[0]
        conn.execute(
            "INSERT INTO app.order_line (order_id, order_date, team_id, channel_line_no,"
            " channel_sku, product_id, qty, unit_price, line_status)"
            " VALUES (%s, %s, %s, %s, 'M0000001', %s, %s, 9.99, 'created')",
            (row[0], row[1], seeded["team"], str(idx), product_id, qty),
        )
    asins = [
        r[0]
        for r in conn.execute(
            "SELECT p.source_ref FROM app.order_line ol"
            " JOIN app.product p ON p.id = ol.product_id"
            " WHERE ol.order_id = %s ORDER BY ol.channel_line_no",
            (row[0],),
        ).fetchall()
    ]
    return {"id": int(row[0]), "date": row[1], "no": row[2], "asins": asins}


def _mk_po(
    conn: psycopg.Connection,
    seeded: dict[str, int],
    order: dict[str, Any],
    *,
    status: str = "unassigned",
    buyer_account_id: int | None = None,
    purchase_order_ref: str | None = None,
) -> int:
    """直插 procurement_order（绕应用层）——测的是端点，不是建单链。"""
    assigned_at = "now()" if buyer_account_id is not None else "NULL"
    return int(
        conn.execute(
            "INSERT INTO app.procurement_order (team_id, store_id, order_id, order_date,"
            " status, buyer_account_id, purchase_order_ref, purchased_at, assignee_kind,"
            f" assigned_at) VALUES (%s, %s, %s, %s, %s, %s, %s::text,"
            f" CASE WHEN %s::text IS NULL THEN NULL ELSE now() END, 'none', {assigned_at})"
            " RETURNING id",
            (
                seeded["team"],
                seeded["store"],
                order["id"],
                order["date"],
                status,
                buyer_account_id,
                purchase_order_ref,
                purchase_order_ref,
            ),
        ).fetchone()[0]
    )


def _mk_account(
    conn: psycopg.Connection,
    seeded: dict[str, int],
    *,
    site: str = "amazon_com",
    status: str = "active",
    daily_cap: int | None = None,
) -> dict[str, Any]:
    """造一个**已认领可派单**的买家账号。

    `status` 的默认值必须**显式写成 'active' 并显式传进 INSERT**：0043 本轮把列默认值改成
    了 `'pending_claim'`（首见自动登记的归宿）。依赖列默认值的话，本文件所有派发用例会
    静默变成「绿的空判据」——建出来的全是待认领账号，一律不派单，而断言只查「拿到了空」。
    """
    tag = uuid.uuid4().hex[:8]
    customer_id = f"A{tag.upper()}"
    account_id = conn.execute(
        "INSERT INTO app.buyer_account (team_id, label, site, external_customer_id,"
        " status, daily_cap) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (seeded["team"], f"指纹浏览器-{tag}", site, customer_id, status, daily_cap),
    ).fetchone()[0]
    return {"id": int(account_id), "customerId": customer_id}


def _issue(client: TestClient, auth: dict[str, str]) -> dict[str, Any]:
    """走 13b 的**团队级**签发端点拿明文令牌（**明文只在这一次响应里出现**）。

    本轮改形：签发不再挂在 `/buyer-accounts/{id}` 下——令牌绑一台授权浏览器不绑账号。
    """
    r = client.post("/api/v1/plugin-instances", headers=auth, json={})
    assert r.status_code == 201, r.text
    return dict(r.json())


def _reset_foreign(conn: psycopg.Connection, seeded: dict[str, int]) -> None:
    """清空**专用他团队**的单/账号/通知（本文件独占该团队，可以整团队清）。"""
    for t in ("procurement_order", "order_check", "order_line", "channel_order",
              "buyer_account", "notification"):  # fmt: skip
        conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (seeded["foreign"],))


def _foreign_plugin_po(conn: psycopg.Connection, seeded: dict[str, int]) -> tuple[int, str, Any]:
    """在他团队造一张**插件派发单**（`buyer_account_id` 非空）+ 它的买家账号。

    返回 (po_id, 该账号的 customerId, 该单的 to_jsonb 快照)。
    """
    _reset_foreign(conn, seeded)
    tag = uuid.uuid4().hex[:8]
    customer_id = f"FR{tag.upper()}"
    order = conn.execute(
        "INSERT INTO app.channel_order (team_id, store_id, channel_order_no, order_date,"
        " channel_status, customer, ship_to, order_total, item_count, pulled_at,"
        " internal_status, has_flag)"
        " VALUES (%s, %s, %s, now() - interval '1 day', 'Created', '{}',"
        "         %s::jsonb, 10, 1, now(), 'checked', false) RETURNING id, order_date",
        (seeded["foreign"], seeded["foreign_store"], f"FR13A-{tag}", SHIP_TO % "US"),
    ).fetchone()
    account = conn.execute(
        "INSERT INTO app.buyer_account (team_id, label, site, external_customer_id, status)"
        " VALUES (%s, %s, 'amazon_com', %s, 'active') RETURNING id",
        (seeded["foreign"], f"他团队号-{tag}", customer_id),
    ).fetchone()[0]
    po = int(
        conn.execute(
            "INSERT INTO app.procurement_order (team_id, store_id, order_id, order_date, status,"
            " buyer_account_id, assignee_kind, assigned_at)"
            " VALUES (%s, %s, %s, %s, 'assigned', %s, 'none', now()) RETURNING id",
            (seeded["foreign"], seeded["foreign_store"], order[0], order[1], account),
        ).fetchone()[0]
    )
    return po, customer_id, _snapshot(conn, po)


def _new_customer_id() -> str:
    """一个本团队从没见过的 customerId（形状同真号，内容随机）。"""
    return f"NEW{uuid.uuid4().hex[:10].upper()}"


def _auto_rows(conn: psycopg.Connection, seeded: dict[str, int]) -> list[Any]:
    """首见自动登记出的行（17d：`label IS NULL` 即自动登记——人工建号必带 label）。"""
    return conn.execute(
        "SELECT id, external_customer_id, label, site, status FROM app.buyer_account"
        " WHERE team_id = %s AND label IS NULL ORDER BY id",
        (seeded["team"],),
    ).fetchall()


def _h(instance: dict[str, Any]) -> dict[str, str]:
    """17c 共享形态：只带 X-Plugin-Token=共享值。参数保留是为了不动几十处调用点；
    实例字段不再参与认证（逐实例链休眠）。"""
    return {"X-Plugin-Token": SHARED_TOKEN}


def _pull(client: TestClient, instance: dict[str, Any], customer_id: str, **params: str):  # type: ignore[no-untyped-def]
    return client.get(
        f"{PLUGIN}/getNeedPurchaseOrders",
        headers=_h(instance),
        params={"customerId": customer_id, **params},
    )


def _snapshot(conn: psycopg.Connection, po_id: int) -> Any:
    return conn.execute(
        "SELECT to_jsonb(po) FROM app.procurement_order po WHERE id = %s", (po_id,)
    ).fetchone()[0]


# ── 验收③：跨团队必败 + customerId 路由（身份模型更正后的三条新判据）──


class TestInstanceScope:
    """令牌绑「一台授权浏览器」，`customerId` 只做**团队内**路由。

    旧判据「A 实例取不到 B 账号的任务」已被 Owner 2026-07-30 推翻
    （图纸 `07:288-340`）：同团队内换号是**正常运营动作**，不是越权。
    越权边界改为**跨团队必败**，三条新判据 (a)/(b)/(c) 各有一个承重用例，便于验收对表。
    """

    def test_pull_routes_by_customer_id(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """路由的正反两面必须同时成立：带 A 的 customerId 拿到 A 的单，拿不到 B 的。

        **同一把令牌**——这条与 `test_same_instance_switches_customer_id_and_routing_follows`
        的区别是：那条证明「换号即换路由」，本条证明「一次请求只看见一个号的单」。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            acc_a, acc_b = _mk_account(conn, seeded), _mk_account(conn, seeded)
            po_a = _mk_po(
                conn, seeded, _mk_order(conn, seeded), status="assigned",
                buyer_account_id=acc_a["id"],
            )  # fmt: skip
            po_b = _mk_po(
                conn, seeded, _mk_order(conn, seeded), status="assigned",
                buyer_account_id=acc_b["id"],
            )  # fmt: skip
        inst = _issue(client, auth)

        r = _pull(client, inst, acc_a["customerId"])
        assert r.status_code == 200, r.text
        ids = [t["id"] for t in r.json()["data"]]
        assert po_a in ids, "本次解析到的账号已派的单必须拉得到（续拉：插件重启后还要能看见）"
        assert po_b not in ids, "带 A 的 customerId 却拿到了 B 的单——路由没按 customerId 走"

    def test_cross_team_customer_id_is_indistinguishable_from_unknown(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """跨团队 customerId ⇒ 解析不到，**且不可探测**。

        「拿不到单」只是一半。真判据是**响应与「随便一个没见过的 customerId」逐字节相同**
        ——任何为跨团队单开的错误路径（403 / 特殊码 / 不登记）都会让这条当场红，
        而那种差异本身就是存在性探针：拿共享 token 扫 customerId，凡是「不给我落自动
        登记行」的就是别的团队真实存在的号。

        另一半是「B 那边一列不动」：B 的账号行、B 的单都必须原样。17d 首见即 active
        不改这条的实质——登记出的是**空行**，名下无指派单，拉不走任何东西。
        """
        auth = _login(client, ADMIN, PASSWORD)
        foreign = seeded["foreign"]
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            foreign_po, foreign_customer, foreign_po_before = _foreign_plugin_po(conn, seeded)
            foreign_account_before = conn.execute(
                "SELECT to_jsonb(ba) FROM app.buyer_account ba WHERE team_id = %s", (foreign,)
            ).fetchone()[0]
            mine = _mk_po(conn, seeded, _mk_order(conn, seeded))
        inst = _issue(client, auth)

        cross = _pull(client, inst, foreign_customer)
        unknown = _pull(client, inst, _new_customer_id())
        assert cross.status_code == unknown.status_code == 200, cross.text
        assert cross.content == unknown.content, (
            "跨团队 customerId 的响应与「没见过的 customerId」不同——那就是一个存在性探针"
        )
        assert cross.json()["data"] == []

        with psycopg.connect(migrated_db) as conn:
            assert (
                conn.execute(
                    "SELECT to_jsonb(ba) FROM app.buyer_account ba WHERE team_id = %s", (foreign,)
                ).fetchone()[0]
                == foreign_account_before
            ), "另一个团队的账号行被动了"
            assert _snapshot(conn, foreign_po) == foreign_po_before, "另一个团队的单被动了"
            assert conn.execute(
                "SELECT buyer_account_id, status FROM app.procurement_order WHERE id = %s", (mine,)
            ).fetchone() == (None, "unassigned"), "解析不到的请求不得派出任何单"  # fmt: skip
            # 跨团队那串在**本团队**落成自动登记行（一视同仁），在**对方团队**零新增
            assert [r[1] for r in _auto_rows(conn, seeded)].count(foreign_customer) == 1
            assert conn.execute(
                "SELECT count(*) FROM app.buyer_account WHERE team_id = %s", (foreign,)
            ).fetchone()[0] == 1, "对方团队多出了行"  # fmt: skip

    def test_first_sight_registers_active_and_pulls_nothing(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """17d：首见的 customerId 直接登记为 `active`（空行）+ 通知 + 审计；无指派即无任务。

        「首见即用」不等于「首见即拿单」——拉取只回本账号名下**已指派**的单，新登记的
        空行名下什么都没有，团队里等着的未指派单一列不动。这正是拆掉 pending_claim 闸
        后垃圾行无害化的机制本体，必须钉住。

        重跑一次必须**零新增行、零新增通知**（dedupe 命中）——否则一台跑着的浏览器会
        每隔几秒吵一条，运营很快把这类告警全部静音，真的那条也就没人看了。

        **审计那一腿保留**（原 F6）：通知面会被清理/静音/折叠，`audit_log` 是
        append-only——账号池里凭空多出来的行「什么时候、怎么来的」长期只有审计答得上。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            conn.execute("DELETE FROM app.notification WHERE team_id = %s", (seeded["team"],))
            waiting = _mk_po(conn, seeded, _mk_order(conn, seeded))
            audit_before = conn.execute(
                "SELECT count(*) FROM app.audit_log WHERE team_id = %s"
                " AND action = 'buyer_account.auto_register'",
                (seeded["team"],),
            ).fetchone()[0]
        inst = _issue(client, auth)
        fresh = _new_customer_id()

        r = _pull(client, inst, fresh)
        assert r.status_code == 200 and r.json()["data"] == []
        with psycopg.connect(migrated_db) as conn:
            rows = _auto_rows(conn, seeded)
            assert len(rows) == 1
            assert rows[0][1] == fresh
            assert rows[0][2] is None and rows[0][3] is None, "首见登记不许编造 label / site"
            assert rows[0][4] == "active", "17d：首见即 active（无认领仪式）"
            assert conn.execute(
                "SELECT buyer_account_id, status FROM app.procurement_order WHERE id = %s",
                (waiting,),
            ).fetchone() == (None, "unassigned"), "未指派的单被首见空行拉走了"  # fmt: skip
            notices = conn.execute(
                "SELECT count(*) FROM app.notification WHERE dedupe_key = %s",
                (f"plugin.first_sight.{rows[0][0]}",),
            ).fetchone()[0]
            audits = conn.execute(
                "SELECT actor_type, after FROM app.audit_log WHERE team_id = %s"
                " AND action = 'buyer_account.auto_register' AND object_id = %s",
                (seeded["team"], str(rows[0][0])),
            ).fetchall()
            audit_after = conn.execute(
                "SELECT count(*) FROM app.audit_log WHERE team_id = %s"
                " AND action = 'buyer_account.auto_register'",
                (seeded["team"],),
            ).fetchone()[0]
        assert notices == 1, "首见登记必须留一条通知，否则没人知道该补账号名"
        assert audit_after == audit_before + 1, "首见登记没落审计——通知被清掉后就再无来历可查"
        assert len(audits) == 1 and audits[0][0] == "system", (
            "机器侧动作的 actor_type 必须是既有词表内的 'system'"
            "（`ck_audit_actor` 只认 user/portal/system，本轮不为它扩 CHECK）"
        )
        assert audits[0][1]["external_customer_id"] == fresh
        assert audits[0][1]["status"] == "active", "审计快照要如实记 17d 的落库状态"
        # 17c：共享通道无机器归因，快照如实记哨兵 0（写 None 反而分不清「老数据没记」
        # 与「共享通道记不了」）。多机归因重启后此断言翻回真实实例号。
        assert audits[0][1]["instance_id"] == 0, "共享通道的审计快照该记哨兵 0"

        assert _pull(client, inst, fresh).json()["data"] == []
        with psycopg.connect(migrated_db) as conn:
            assert len(_auto_rows(conn, seeded)) == 1, "重跑产生了第二条自动登记行"
            assert conn.execute(
                "SELECT count(*) FROM app.notification WHERE team_id = %s AND dedupe_key LIKE %s",
                (seeded["team"], "plugin.first_sight.%"),
            ).fetchone()[0] == 1, "重跑又吵了一条——dedupe 没生效"  # fmt: skip

    def test_same_instance_switches_customer_id_and_routing_follows(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """**判据 (c)**：同一把令牌换带另一个已认领 customerId ⇒ 路由跟随。

        这条直接证明「令牌绑浏览器不绑账号」：一台指纹浏览器换登另一个买家号，**不换令牌**
        也能正常取到那个号的单。旧模型下第二次拉取会 403。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            acc_a, acc_b = _mk_account(conn, seeded), _mk_account(conn, seeded)
            po_a = _mk_po(
                conn, seeded, _mk_order(conn, seeded), status="assigned",
                buyer_account_id=acc_a["id"],
            )  # fmt: skip
            po_b = _mk_po(
                conn, seeded, _mk_order(conn, seeded), status="assigned",
                buyer_account_id=acc_b["id"],
            )  # fmt: skip
        inst = _issue(client, auth)

        first = _pull(client, inst, acc_a["customerId"])
        second = _pull(client, inst, acc_b["customerId"])
        assert first.status_code == second.status_code == 200, second.text
        assert [t["id"] for t in first.json()["data"]] == [po_a]
        assert [t["id"] for t in second.json()["data"]] == [po_b]

    async def test_first_sight_registration_is_concurrency_safe(
        self, migrated_db: str, seeded: dict
    ) -> None:
        """两路**真并发**首见同一个新 customerId ⇒ 恰好一行、零异常。

        承重的是 `identity._REGISTER_SQL` 的 `ON CONFLICT (team_id, external_customer_id)
        DO NOTHING` + 零行回头重解析。不用 `ON CONFLICT` 的实现会抛 `UniqueViolation`，
        让那一次拉取变 500（且整个事务作废）。

        harness 形状抄 13b `test_concurrent_pulls_do_not_exceed_cap`：**两个独立
        `ctx_tx` 会话**才是两个真事务；用 `TestClient` 开线程只会被它的 portal 串起来，
        测不到并发。
        """
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
        fresh = _new_customer_id()
        principal = PluginPrincipal(
            instance_id=-1, team_id=seeded["team"], exec_mode="stop_before_payment"
        )

        async def one() -> list[dict[str, Any]]:
            async with ctx_tx(get_session_factory(), team_id=seeded["team"]) as s:
                return await service.pull_purchase_tasks(
                    s, principal, customer_id=fresh, version=None
                )

        results = await asyncio.gather(one(), one(), return_exceptions=True)
        assert not [r for r in results if isinstance(r, BaseException)], results
        assert results == [[], []]
        with psycopg.connect(migrated_db) as conn:
            rows = _auto_rows(conn, seeded)
            assert len(rows) == 1, "并发首见落了不止一行"
            assert rows[0][4] == "active"

    # 17d：test_pending_claim_flood_is_capped / test_pending_claim_flood_cap_is_exact_under_
    # concurrency / test_rejected_customer_id_is_sticky / test_rejected_stickiness_cannot_be_
    # lifted_by_patch / test_rejected_stickiness_merged_patch_also_blocked 五条随 pending_claim
    # 闸与洪水闸拆除移除（首见即 active 后不存在「待认领额度」这个被保护的资源；
    # 垃圾行无害化机制换成「空行无指派单可拉」，由 test_first_sight_registers_active_and_
    # pulls_nothing 钉住）。原实现见 git 历史（de61fc8 之前）；多机多人形态重启时随
    # identity.py 的洪水闸一起恢复。遗留 rejected 行的粘性纪律收敛为下面这一条。

    def test_legacy_rejected_row_stays_sticky(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """休眠遗留的 `rejected` 行仍粘住：不出任务、不复活、customerId 不可挪。

        17d 后 `rejected` 无新增来源（首见即 active，而 `_ALLOWED_TRANSITIONS` 里
        active 不可转 rejected），但**存量行的三条纪律必须继续成立**：
        ① 该 customerId 再来 ⇒ 解析到同一行、零新增行、零通知（`uq_buyer_account` 粘性）；
        ② 账号闸挡拉取（`status != 'active'` 不出任务）；
        ③ `external_customer_id` 不可挪（`_guard_rejected_customer_id`，挪走即释放占用）、
           状态不可离开终态——两条守卫都是休眠词表继续在岗的证据。
        """
        auth = _login(client, ADMIN, PASSWORD)
        fake = _new_customer_id()
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            conn.execute("DELETE FROM app.notification WHERE team_id = %s", (seeded["team"],))
            legacy = int(
                conn.execute(
                    "INSERT INTO app.buyer_account (team_id, external_customer_id, status)"
                    " VALUES (%s, %s, 'rejected') RETURNING id",
                    (seeded["team"], fake),
                ).fetchone()[0]
            )
        inst = _issue(client, auth)

        # ①② 再来即解析到同一行：无任务、零新增行、零通知
        assert _pull(client, inst, fake).json()["data"] == []
        with psycopg.connect(migrated_db) as conn:
            assert conn.execute(
                "SELECT count(*) FROM app.buyer_account WHERE team_id = %s"
                " AND external_customer_id = %s",
                (seeded["team"], fake),
            ).fetchone()[0] == 1, "遗留 rejected 行被重复登记"  # fmt: skip
            assert conn.execute(
                "SELECT count(*) FROM app.notification WHERE team_id = %s", (seeded["team"],)
            ).fetchone()[0] == 0, "解析到已有行不该发任何通知"  # fmt: skip

        # ③ customerId 不可挪；状态不可离开终态（rejected 只能转 rejected）
        url = f"/api/v1/buyer-accounts/{legacy}"
        moved = client.patch(url, headers=auth, json={"external_customer_id": _new_customer_id()})
        assert moved.status_code == 409, moved.text
        assert moved.json()["error"]["code"] == "BUYER_ACCOUNT_REJECTED_IMMUTABLE"
        revived = client.patch(url, headers=auth, json={"status": "active"})
        assert revived.status_code == 409, revived.text

    def test_backfill_other_team_po_fails(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """拿**另一个团队**的 po_id 回填：403 **且那张单一列未变**（越权不留任何痕迹）。

        修前红的落点是 `_TEAM_PLUGIN_PO_SQL` 的 `team_id = :t`——删掉它，本用例当场绿转红。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            foreign_po, _, before = _foreign_plugin_po(conn, seeded)
        inst = _issue(client, auth)

        r = client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(inst),
            json={
                "id": foreign_po,
                "platformOrderNo": "111-0000000-0000000",
                "totalBeforeTax": "$9.99",
                "tax": "$0.80",
                "shipping": "$0.00",
                "total": "$10.79",
            },
        )
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "PLUGIN_TASK_NOT_OWNED"
        with psycopg.connect(migrated_db) as conn:
            assert _snapshot(conn, foreign_po) == before

    def test_nonexistent_po_same_error_as_unowned(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """不存在的 id 与**另一团队**的 id **同码同状态**——越权可见于日志，存在性不外泄。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            foreign_po, _, _ = _foreign_plugin_po(conn, seeded)
        inst = _issue(client, auth)

        others = client.post(
            f"{PLUGIN}/updateOrderStatus",
            headers=_h(inst),
            json={"id": foreign_po, "status": 99, "failReason": "商品无库存"},
        )
        ghost = client.post(
            f"{PLUGIN}/updateOrderStatus",
            headers=_h(inst),
            json={"id": 2_000_000_111, "status": 99, "failReason": "商品无库存"},
        )
        assert others.status_code == ghost.status_code == 403
        assert others.json()["error"]["code"] == ghost.json()["error"]["code"]

    async def test_rls_still_on_for_plugin_path(self, migrated_db: str, seeded: dict) -> None:
        """**D4 承重**：插件路径的业务查询走 `ctx_tx`，RLS 没被认证那步的 `system_tx` 短路。

        直接拿另一个团队的上下文跑同一个服务函数：本团队的账号行在那个上下文里不可见，
        于是那个 customerId 解析不到 ⇒ 在**对方团队**落一条自动登记空行（17d 首见即
        active）、**本团队的单一列未变**。若哪天有人把业务段改回 `system_tx`，
        `visible == 0` 那一腿当场红（RLS 兜底失效的直接绊线；显式 `team_id = :t`
        谓词那一腿由 13b 的 is_super 用例单独钉）。
        """
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            _reset_foreign(conn, seeded)
            account = _mk_account(conn, seeded)
            po = _mk_po(conn, seeded, _mk_order(conn, seeded))
        foreign = seeded["foreign"]
        # principal 只剩三字段——账号信息刻意不在里面（身份由请求带的 customerId 解析）。
        principal = PluginPrincipal(
            instance_id=-1, team_id=foreign, exec_mode="stop_before_payment"
        )

        async with ctx_tx(get_session_factory(), team_id=foreign) as s:
            visible = (
                await s.execute(
                    text("SELECT count(*) FROM app.buyer_account WHERE id = :i"),
                    {"i": account["id"]},
                )
            ).scalar_one()
            assert visible == 0, "另一个团队的会话看得见本团队的买家账号——RLS 没生效"
            tasks = await service.pull_purchase_tasks(
                s, principal, customer_id=account["customerId"], version=None
            )
        assert tasks == []
        with psycopg.connect(migrated_db) as conn:
            assert conn.execute(
                "SELECT buyer_account_id FROM app.procurement_order WHERE id = %s", (po,)
            ).fetchone()[0] is None  # fmt: skip
            assert conn.execute(
                "SELECT status, label FROM app.buyer_account WHERE team_id = %s"
                " AND external_customer_id = %s",
                (foreign, account["customerId"]),
            ).fetchone() == ("active", None), "对方团队该落一条自动登记空行"  # fmt: skip

    # ── 认证域本身 ──

    # 17c：test_revoked_instance_all_endpoints_401 已随逐实例认证链休眠移除（吊销/时序纪律属
    # authenticate_instance，重启多机归因时随链恢复；authenticate_shared 头注有价签）。

    def test_bad_credentials_all_401(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """坏令牌 / 不存在的 id / 非数字 id / 超 bigint 的 id / 干脆没带头——**全部同码 401**。

        超界那条不是凑数：十进制串直接进 SQL 会让 Postgres 抛
        `NumericValueOutOfRange`（500 且整事务作废），认证失败本就该是 401。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
        instance = _issue(client, auth)
        params = {"customerId": account["customerId"]}

        cases = [
            {"X-Plugin-Instance": str(instance["id"]), "X-Plugin-Token": "not-the-token"},
            {"X-Plugin-Instance": "2000000111", "X-Plugin-Token": instance["token"]},
            {"X-Plugin-Instance": "abc", "X-Plugin-Token": instance["token"]},
            {"X-Plugin-Instance": "9" * 25, "X-Plugin-Token": instance["token"]},
            {"X-Plugin-Instance": str(instance["id"])},  # 缺令牌头
            {},  # 两个头都没带
        ]
        for headers in cases:
            r = client.get(f"{PLUGIN}/getNeedPurchaseOrders", headers=headers, params=params)
            assert r.status_code == 401, (headers, r.status_code, r.text)
            assert r.json()["error"]["code"] == "PLUGIN_AUTH"

    # 17c：test_failure_paths_all_hash_and_compare 已随逐实例认证链休眠移除（吊销/时序纪律属
    # authenticate_instance，重启多机归因时随链恢复；authenticate_shared 头注有价签）。

    def test_stored_hash_is_not_a_usable_token(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """把散列当令牌递进来必须 401——证明服务端比的是 sha256(明文) 对 sha256(明文)。

        17c 共享形态下的绊线：`authenticate_shared` 若被图省事改成
        `compare_digest(token_digest(shared), token)`（拿头里的原始值直接比摘要），
        那么 sha256(共享明文) 就成了等价令牌——而那个十六进制串会出现在任何一处
        「按老习惯存 hash」的地方。补一条实例休眠链的同款（库里的 `token_hash`
        当令牌），两条都得 401。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
        instance = _issue(client, auth)
        with psycopg.connect(migrated_db) as conn:
            stored = conn.execute(
                "SELECT token_hash FROM app.plugin_instance WHERE id = %s", (instance["id"],)
            ).fetchone()[0]
        assert stored == hashlib.sha256(instance["token"].encode()).hexdigest()

        shared_digest = hashlib.sha256(SHARED_TOKEN.encode()).hexdigest()
        for probe in (shared_digest, stored):
            r = client.get(
                f"{PLUGIN}/getNeedPurchaseOrders",
                headers={"X-Plugin-Token": probe},
                params={"customerId": account["customerId"]},
            )
            assert r.status_code == 401 and r.json()["error"]["code"] == "PLUGIN_AUTH", probe


# ── 端点 1：拉取语义（取还幂等 + 载荷形状 + 不猜 ASIN）──


class TestPullTasks:
    def test_pull_payload_shape(self, client: TestClient, migrated_db: str, seeded: dict) -> None:
        """载荷逐字段对齐插件实读（`07:216-224`）——字段名或语义写错，插件是静默买错。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
            order = _mk_order(conn, seeded, lines=(("B0PLUGIN01", 2),))
            # 17d：拉取只读已指派单——载荷用例的单直接指派到账号名下（人工指派的落库形状）
            po = _mk_po(conn, seeded, order, status="assigned", buyer_account_id=account["id"])
        instance = _issue(client, auth)

        r = _pull(client, instance, account["customerId"])
        assert r.status_code == 200, r.text
        assert r.json()["code"] == 200, "厂商兼容信封：插件读的是 response.data"
        tasks = r.json()["data"]
        assert [t["id"] for t in tasks] == [po]
        task = tasks[0]
        assert task["orderNo"] == order["no"], "orderNo 取渠道单号，插件日志才能与后台对上"
        assert task["state"] == task["receivingState"] == "AL", "两个字段名都要给且同值"
        assert task["receivingAddress"] == "123 Main St\nApt 4"
        assert task["receivingCity"] == "Marion"
        assert task["receivingPostCode"] == "36759"
        assert task["receivingCountry"] == "US"
        assert task["receivingName"] == "Jane Roe"
        assert task["receivingPhone"] == "+1-555-0100"
        # JP 专用字段：本轮不做日本站，但字段与分支保留（不做 ≠ 删掉）
        assert task["receivingDistrict"] is None
        assert task["products"] == [{"asin": order["asins"][0], "quantity": 2}]
        assert task["execMode"] == "stop_before_payment", "默认档不花钱"

        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT buyer_account_id, status FROM app.procurement_order WHERE id = %s", (po,)
            ).fetchone()
        assert row[0] == account["id"] and row[1] == "assigned", "拉取是只读，不得改指派与状态"

    def test_pull_is_idempotent(self, client: TestClient, migrated_db: str, seeded: dict) -> None:
        """拉取天然幂等（17d 只读语义）：重复拉回同一批已指派单；未指派的单永远不出现。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
            first = _mk_po(
                conn, seeded, _mk_order(conn, seeded), status="assigned",
                buyer_account_id=account["id"],
            )  # fmt: skip
            unassigned = _mk_po(conn, seeded, _mk_order(conn, seeded))
        instance = _issue(client, auth)

        one = _pull(client, instance, account["customerId"]).json()["data"]
        two = _pull(client, instance, account["customerId"]).json()["data"]
        assert [t["id"] for t in one] == [first]
        assert [t["id"] for t in two] == [first], "重复拉必须回同一批（插件重启后还认得自己的单）"
        with psycopg.connect(migrated_db) as conn:
            assert conn.execute(
                "SELECT buyer_account_id FROM app.procurement_order WHERE id = %s", (unassigned,)
            ).fetchone()[0] is None, "拉取是只读——未指派的单不得被拉取悄悄认领"  # fmt: skip

    def test_missing_asin_task_not_dispatched(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """已指派但缺 ASIN 的单**不下发、不猜、有告警**——少给一行插件会照着少买一件。

        17d 后指派是人做的、不看商品来源，「指派后商品被删（14a 硬删）」也会造出这种单
        ——下发侧的兜底因此从「双保险」变成**唯一一道闸**，本用例的承重只增不减。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
            broken = _mk_po(
                conn, seeded, _mk_order(conn, seeded, lines=(("B0PLUGIN01", 1), (None, 1))),
                status="assigned", buyer_account_id=account["id"],
            )  # fmt: skip
        instance = _issue(client, auth)

        r = _pull(client, instance, account["customerId"])
        assert r.status_code == 200 and r.json()["data"] == []
        with psycopg.connect(migrated_db) as conn:
            assert conn.execute(
                "SELECT buyer_account_id, status FROM app.procurement_order WHERE id = %s",
                (broken,),
            ).fetchone() == (account["id"], "assigned"), "跳过下发不该动指派与状态"  # fmt: skip
            assert conn.execute(
                "SELECT count(*) FROM app.notification WHERE dedupe_key = %s",
                (f"plugin.no_asin.{broken}",),
            ).fetchone()[0] == 1, "不下发也要有人知道，否则症状只剩「插件拉不到单」"  # fmt: skip

    def test_paused_account_pulls_nothing_but_writes_authenticate(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """账号闸只挡拉取。**写路径的认证必须照常通过**——钱可能已经花出去了，
        账号被停就收不到回填 = 花了钱系统不知道，那比「账号被风控」严重得多。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded, status="paused")
            po = _mk_po(
                conn, seeded, _mk_order(conn, seeded), status="assigned",
                buyer_account_id=account["id"],
            )  # fmt: skip
        instance = _issue(client, auth)

        pulled = _pull(client, instance, account["customerId"])
        assert pulled.status_code == 200 and pulled.json()["data"] == []

        wrote = client.post(
            f"{PLUGIN}/updateOrderStatus",
            headers=_h(instance),
            json={"id": po, "status": 99, "failReason": "商品无库存"},
        )
        # **本轮加强**：从「不是 401/403」提到「必须 200」。身份模型更正后写路径压根不解析
        # 账号（`load_team_plugin_po` 只看团队 + 是不是插件单），可以给强判据了。
        assert wrote.status_code == 200, (
            f"账号被停不得影响写路径的认证与归属判定（实得 {wrote.status_code}：{wrote.text}）"
        )

    def test_pull_touches_account_last_seen_but_not_instance(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """拉取仍回写账号 `last_seen_at`；**实例观察列一列不动**（17c 放弃机器归因）。

        账号侧那半继续承重——掉线判断看「这个号还在不在拉单」。实例侧翻转成反向断言：
        共享通道的 principal 是 `instance_id=0` 哨兵，`touch_last_seen` 那句
        `UPDATE app.plugin_instance WHERE id = :i` 零行命中，签发出的休眠实例行必须
        保持全 NULL——这正是 D-Q73「放弃机器归因」的可测形状。重启多机归因时连同
        `_h` 一起换回，本用例再翻回原断言（原文见 git 历史）。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
        instance = _issue(client, auth)

        assert _pull(client, instance, account["customerId"], v="2.4.1").status_code == 200
        with psycopg.connect(migrated_db) as conn:
            seen = conn.execute(
                "SELECT last_seen_at FROM app.buyer_account WHERE id = %s", (account["id"],)
            ).fetchone()[0]
            inst = conn.execute(
                "SELECT last_seen_at, version, last_seen_customer_id FROM app.plugin_instance"
                " WHERE id = %s",
                (instance["id"],),
            ).fetchone()
        assert seen is not None, "账号 last_seen_at 仍要回写（掉线判断的依据）"
        assert inst == (None, None, None), "共享通道不得回写实例观察列（哨兵 0 应零行命中）"


# ── 端点 6：待物流同步 ──


class TestSyncOrders:
    def test_sync_orders_scope(self, client: TestClient, migrated_db: str, seeded: dict) -> None:
        """只列**本次解析出的账号**、**已拍单**、**有渠道单号**的；未拍的与别的号的都不在内。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            acc_a, acc_b = _mk_account(conn, seeded), _mk_account(conn, seeded)
            order = _mk_order(conn, seeded)
            mine = _mk_po(
                conn, seeded, order, status="purchased", buyer_account_id=acc_a["id"],
                purchase_order_ref="111-2223334-5556667",
            )  # fmt: skip
            not_purchased = _mk_po(
                conn, seeded, _mk_order(conn, seeded), status="assigned",
                buyer_account_id=acc_a["id"],
            )  # fmt: skip
            others = _mk_po(
                conn, seeded, _mk_order(conn, seeded), status="purchased",
                buyer_account_id=acc_b["id"], purchase_order_ref="111-9998887-6665554",
            )  # fmt: skip
        inst_a = _issue(client, auth)

        r = client.get(
            f"{PLUGIN}/getNeedSyncOrders",
            headers=_h(inst_a),
            params={"customerId": acc_a["customerId"]},
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert [d["id"] for d in data] == [mine]
        assert data[0]["platformOrderNo"] == "111-2223334-5556667"
        assert data[0]["orderNo"] == order["no"]
        assert not_purchased not in [d["id"] for d in data]
        assert others not in [d["id"] for d in data]
