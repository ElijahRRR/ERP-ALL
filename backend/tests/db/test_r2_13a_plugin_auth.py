"""R2-13 13a 验收：插件契约端点组 + 实例认证（**验收③「A 实例取不到 B 账号的任务」**）。

| 判据 / 纪律 | 用例 |
|---|---|
| **路由正反两面**：带 A 的 customerId 只拿到 A 的单 | `test_pull_routes_by_customer_id` |
| **验收③(a)**：跨团队 customerId 必败**且不可探测**
  | `test_cross_team_customer_id_is_indistinguishable_from_unknown` |
| **验收③(b)**：未认领只落待认领行 + 通知 + **审计**，不派单
  | `test_unclaimed_customer_id_registers_and_never_dispatches` |
| **验收③(c)**：同一实例换号 ⇒ 路由跟随（令牌绑浏览器）
  | `test_same_instance_switches_customer_id_and_routing_follows` |
| 首见登记并发安全（ON CONFLICT，恰好一行） | `test_first_sight_registration_is_concurrency_safe` |
| 垃圾行治理：洪水闸 + 撞闸响应同形 + 告警带实例号与已驳回行数
  | `test_pending_claim_flood_is_capped` |
| 垃圾行治理：**并发下 cap 精确**（advisory 锁）
  | `test_pending_claim_flood_cap_is_exact_under_concurrency` |
| 垃圾行治理：驳回是终态且粘住 | `test_rejected_customer_id_is_sticky` |
| 垃圾行治理：粘性**不能被 PATCH 掉 customerId 解除**
  | `test_rejected_stickiness_cannot_be_lifted_by_patch` |
| **跨团队**的 po_id 回填必败且**一列未变** | `test_backfill_other_team_po_fails` |
| 不泄露存在性：不存在的 id 与他团队的 id 同码同状态 | `test_nonexistent_po_same_error_as_unowned` |
| 吊销即全域失效（六端点） | `test_revoked_instance_all_endpoints_401` |
| 认证失败一律 401 同码（含缺头/坏 id/超界 id） | `test_bad_credentials_all_401` |
| 三条失败路径工作量相同（不留时序信道，审查 B6） | `test_failure_paths_all_hash_and_compare` |
| 库里的 hash 不能当令牌用（证明不是裸比） | `test_stored_hash_is_not_a_usable_token` |
| **D4 承重**：插件路径的 RLS 没被 `system_tx` 短路 | `test_rls_still_on_for_plugin_path` |
| 任务取还幂等：重复拉不重复派、不重复耗额度 | `test_pull_is_idempotent` |
| 下发载荷逐字段对齐插件实读 | `test_pull_payload_shape` |
| 缺 ASIN 不猜、不派、有告警 | `test_missing_asin_task_not_dispatched` |
| 账号闸只挡拉取、不挡写路径认证 | `test_paused_account_pulls_nothing_but_writes_authenticate` |
| 拉取回写 last_seen_at / 版本 / **最近登录号** | `test_pull_touches_last_seen_and_version` |
| 端点 6 只列已拍单且属本次解析出的账号的 | `test_sync_orders_scope` |

**不在本文件**：派发算法本身（`test_r2_13b_dispatch.py`）、回填/异常/物流语义
（13d，本步端点 2/3/4/7 只通了通道，业务写入前显式 501）、cookies 删净
（`test_r2_13_no_cookie_chain.py`）。
"""

import asyncio
import hashlib
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from erp.core.db import ctx_tx, get_session_factory, system_tx
from erp.core.errors import BusinessError
from erp.core.security import hash_password
from erp.order import dispatch
from erp.plugin import auth as plugin_auth
from erp.plugin import service
from erp.plugin.auth import PluginPrincipal

from .test_identity_api import PASSWORD, _login

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
    return {
        "team": team_id,
        "user": uid,
        "store": store_id,
        "foreign": foreign_id,
        "foreign_store": foreign_store,
    }


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> TestClient:
    from erp.main import app

    return TestClient(app)


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


def _pending_rows(conn: psycopg.Connection, seeded: dict[str, int]) -> list[Any]:
    return conn.execute(
        "SELECT id, external_customer_id, label, site, status FROM app.buyer_account"
        " WHERE team_id = %s AND status = 'pending_claim' ORDER BY id",
        (seeded["team"],),
    ).fetchall()


def _h(instance: dict[str, Any]) -> dict[str, str]:
    return {"X-Plugin-Instance": str(instance["id"]), "X-Plugin-Token": instance["token"]}


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
        """**判据 (a)**：团队 A 的实例带团队 B 的 customerId ⇒ 解析不到，**且不可探测**。

        「拿不到单」只是一半。真判据是**响应与「随便一个没见过的 customerId」逐字节相同**
        ——任何为跨团队单开的错误路径（403 / 特殊码 / 不登记）都会让这条当场红，
        而那种差异本身就是存在性探针：拿一把 A 的令牌扫 customerId，凡是「不给我落待认领
        行」的就是别的团队真实存在的号。

        另一半是「B 那边一列不动」：B 的账号行、B 的待派单都必须原样。
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
            # 跨团队那串在**本团队**落成待认领行（一视同仁），在**对方团队**零新增
            assert [r[1] for r in _pending_rows(conn, seeded)].count(foreign_customer) == 1
            assert conn.execute(
                "SELECT count(*) FROM app.buyer_account WHERE team_id = %s", (foreign,)
            ).fetchone()[0] == 1, "对方团队多出了行"  # fmt: skip

    def test_unclaimed_customer_id_registers_and_never_dispatches(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """**判据 (b)**：未认领的 customerId 不派单，只落一条 `pending_claim` 行 + 通知 + 审计。

        重跑一次必须**零新增行、零新增通知**（dedupe 命中）——否则一台跑着的浏览器会
        每隔几秒吵一条，运营很快把这类告警全部静音，真的那条也就没人看了。

        **审计那一腿是本轮新增（F6）**：通知面会被清理、被静音、被 dedupe 折叠，而
        `audit_log` 是 append-only（0002 无 UPDATE/DELETE 授权 + 无对应策略）。买家账号池里
        凭空多出来的行「什么时候来的、哪台实例带来的」，长期只有审计答得上。
        *修前红*：`_register` 不落审计时，`buyer_account.auto_register` 查不到行。
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
            rows = _pending_rows(conn, seeded)
            assert len(rows) == 1
            assert rows[0][1] == fresh
            assert rows[0][2] is None and rows[0][3] is None, "首见登记不许编造 label / site"
            assert conn.execute(
                "SELECT buyer_account_id, status FROM app.procurement_order WHERE id = %s",
                (waiting,),
            ).fetchone() == (None, "unassigned"), "未认领账号被派了单"  # fmt: skip
            notices = conn.execute(
                "SELECT count(*) FROM app.notification WHERE dedupe_key = %s",
                (f"plugin.pending_claim.{rows[0][0]}",),
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
        assert notices == 1, "首见登记必须留一条通知，否则那行永远没人认领"
        assert audit_after == audit_before + 1, "首见登记没落审计——通知被清掉后就再无来历可查"
        assert len(audits) == 1 and audits[0][0] == "system", (
            "机器侧动作的 actor_type 必须是既有词表内的 'system'"
            "（`ck_audit_actor` 只认 user/portal/system，本轮不为它扩 CHECK）"
        )
        assert audits[0][1]["external_customer_id"] == fresh
        assert audits[0][1]["instance_id"] == inst["id"], (
            "审计快照里没有实例号——「哪台浏览器带来的」正是这条审计存在的理由"
        )

        assert _pull(client, inst, fresh).json()["data"] == []
        with psycopg.connect(migrated_db) as conn:
            assert len(_pending_rows(conn, seeded)) == 1, "重跑产生了第二条待认领行"
            assert conn.execute(
                "SELECT count(*) FROM app.notification WHERE team_id = %s AND dedupe_key LIKE %s",
                (seeded["team"], "plugin.pending_claim.%"),
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
            assert len(_pending_rows(conn, seeded)) == 1, "并发首见落了不止一行"

    def test_pending_claim_flood_is_capped(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """洪水闸：待认领行到顶即**拒绝登记** + critical 告警，且响应仍与放行时同形。

        没有这道闸，持有效令牌者可以用伪造 customerId 无限灌行——而 `buyer_account`
        **无 DELETE 授权**，那些行永久残留。

        告警正文两腿是本轮新增（F7），它们是撞闸后**唯一**的可执行信息：
        - **实例号**——处置手段是吊销令牌，不知道是哪台就无从处置；
        - **本团队 rejected 行数**——驳回粘性只挡「同一个 id 再灌」，换个 id 照灌不误，
          而 rejected 行不占额度、平时零告警；这个数长期走高才是「有人在换 id 反复灌」
          的信号。*修前红*：正文里没有实例号、没有已驳回行数。
        """
        auth = _login(client, ADMIN, PASSWORD)
        tag = uuid.uuid4().hex[:8].upper()
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            conn.execute("DELETE FROM app.notification WHERE team_id = %s", (seeded["team"],))
            conn.execute(
                "DELETE FROM app.team_config WHERE team_id = %s AND key = %s",
                (seeded["team"], dispatch.PLUGIN_DISPATCH_CONFIG_KEY),
            )
            conn.execute(
                "INSERT INTO app.team_config (team_id, key, value) VALUES (%s, %s, %s::jsonb)",
                (seeded["team"], dispatch.PLUGIN_DISPATCH_CONFIG_KEY, '{"pending_claim_cap": 3}'),
            )
            # 两条已驳回残留：它们**不占额度**（闸只数 pending_claim），但必须出现在
            # 告警正文里——否则「换 id 反复灌」这一面在系统里没有任何观察点。
            for i in range(2):
                conn.execute(
                    "INSERT INTO app.buyer_account (team_id, external_customer_id, status)"
                    " VALUES (%s, %s, 'rejected')",
                    (seeded["team"], f"RJ{tag}{i}"),
                )
        inst = _issue(client, auth)
        try:
            bodies = [_pull(client, inst, _new_customer_id()).content for _ in range(5)]
            assert len(set(bodies)) == 1, "撞闸的响应与放行的响应不同形——那是可探测的信号"
            with psycopg.connect(migrated_db) as conn:
                assert len(_pending_rows(conn, seeded)) == 3, "洪水闸没拦住"
                alerts = conn.execute(
                    "SELECT body FROM app.notification WHERE dedupe_key = %s",
                    (f"plugin.pending_claim_flood.{seeded['team']}",),
                ).fetchall()
            assert len(alerts) == 1, "撞闸必须有 critical 告警"
            body = alerts[0][0]
            assert f"#{inst['id']}" in body, (
                f"critical 正文里没有实例号（处置＝吊销哪一把令牌，不写就得去翻日志）：{body}"
            )
            assert "2" in body and "rejected" in body, (
                f"critical 正文里没有本团队已驳回行数——「换 id 反复灌」失去唯一观察点：{body}"
            )
        finally:
            with psycopg.connect(migrated_db, autocommit=True) as conn:
                conn.execute(
                    "DELETE FROM app.team_config WHERE team_id = %s AND key = %s",
                    (seeded["team"], dispatch.PLUGIN_DISPATCH_CONFIG_KEY),
                )

    async def test_pending_claim_flood_cap_is_exact_under_concurrency(
        self, migrated_db: str, seeded: dict
    ) -> None:
        """**并发下 cap 必须精确**（审查 F2）：N 路同时首见 N 个新号，落地恰好 `cap` 条。

        「数一次 pending → 决定收不收 → INSERT」是典型的 read-then-decide。无锁时 N 路
        并发各自读到的都是对方写入之前的值，于是每一路都认为自己没到顶 ⇒ **全部放行**。
        `test_pending_claim_flood_is_capped` 是**顺序**形态，测不到这件事——它绿着的时候
        实测 `cap=3` 在并发下落地 **15** 条（＝连接池上限，即「越界幅度 = 并发度」，
        而头注当时写的是「允许小幅越界」）。修法：`_register` 在计数之前取一把按团队
        派生的 `pg_advisory_xact_lock`。

        *修前红*：去掉 `identity._REGISTER_LOCK_SQL` 那一行即刻红（落地条数 ≫ cap）。

        harness 同 `test_first_sight_registration_is_concurrency_safe`：**独立 `ctx_tx`
        会话**才是独立事务；`TestClient` 开线程会被它的 portal 串起来，测不到并发。
        并发度取 12（< 连接池 5+10=15，避免被连接饥饿掩盖成"看起来没超发"）。
        """
        cap, fanout = 3, 12
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            conn.execute(
                "DELETE FROM app.team_config WHERE team_id = %s AND key = %s",
                (seeded["team"], dispatch.PLUGIN_DISPATCH_CONFIG_KEY),
            )
            conn.execute(
                "INSERT INTO app.team_config (team_id, key, value) VALUES (%s, %s, %s::jsonb)",
                (
                    seeded["team"],
                    dispatch.PLUGIN_DISPATCH_CONFIG_KEY,
                    f'{{"pending_claim_cap": {cap}}}',
                ),
            )
        principal = PluginPrincipal(
            instance_id=-1, team_id=seeded["team"], exec_mode="stop_before_payment"
        )

        async def one(customer_id: str) -> list[dict[str, Any]]:
            async with ctx_tx(get_session_factory(), team_id=seeded["team"]) as s:
                return await service.pull_purchase_tasks(
                    s, principal, customer_id=customer_id, version=None
                )

        try:
            results = await asyncio.gather(
                *(one(_new_customer_id()) for _ in range(fanout)), return_exceptions=True
            )
            assert not [r for r in results if isinstance(r, BaseException)], results
            # 撞闸与放行的响应必须同形（空数组），并发下也不例外
            assert results == [[]] * fanout
            with psycopg.connect(migrated_db) as conn:
                landed = len(_pending_rows(conn, seeded))
            assert landed == cap, (
                f"并发下落地 {landed} 条待认领行，cap={cap} 被击穿"
                "（越界幅度 = 并发度，不是「小幅」）"
            )
        finally:
            with psycopg.connect(migrated_db, autocommit=True) as conn:
                conn.execute(
                    "DELETE FROM app.team_config WHERE team_id = %s AND key = %s",
                    (seeded["team"], dispatch.PLUGIN_DISPATCH_CONFIG_KEY),
                )

    def test_rejected_customer_id_is_sticky(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """驳回是**终态且粘住**：同一个 customerId 再灌 ⇒ 仍一行、仍 rejected、零新通知。

        粘性不是额外代码，是 `uq_buyer_account` 的自然结果——而它必须粘住，否则伪造者
        换个时间再灌一次，驳回就白做了（本表无 DELETE 授权，删不掉）。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            conn.execute("DELETE FROM app.notification WHERE team_id = %s", (seeded["team"],))
        inst = _issue(client, auth)
        fake = _new_customer_id()

        assert _pull(client, inst, fake).json()["data"] == []
        with psycopg.connect(migrated_db) as conn:
            account_id = _pending_rows(conn, seeded)[0][0]
        assert (
            client.patch(
                f"/api/v1/buyer-accounts/{account_id}", headers=auth, json={"status": "rejected"}
            ).status_code
            == 200
        )

        assert _pull(client, inst, fake).json()["data"] == []
        with psycopg.connect(migrated_db) as conn:
            rows = conn.execute(
                "SELECT id, status FROM app.buyer_account WHERE team_id = %s"
                " AND external_customer_id = %s",
                (seeded["team"], fake),
            ).fetchall()
            assert rows == [(account_id, "rejected")], "被驳回的 customerId 又被登记了一次"
            assert conn.execute(
                "SELECT count(*) FROM app.notification WHERE team_id = %s AND dedupe_key LIKE %s",
                (seeded["team"], "plugin.pending_claim.%"),
            ).fetchone()[0] == 1, "驳回后再灌又吵了一条"  # fmt: skip

    def test_rejected_stickiness_cannot_be_lifted_by_patch(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """粘性**不能被 PATCH 掉 `external_customer_id` 解除**（审查 F3，四步复现）。

        粘性靠「那一行永久占住那个 customerId」成立，而占用的载体就是这一列。此前
        `PATCH /buyer-accounts/{id}` 对它毫无守卫，于是有一条四步就能走通的解除序列：

        ① 灌一个伪造 customerId ⇒ 落待认领行；② 驳回 ⇒ `rejected`（占住）；
        ③ **把这条 rejected 行的 `external_customer_id` 改成别的串** ⇒ 占用当场释放；
        ④ 同一个伪造 id 再灌 ⇒ 又是「没见过」⇒ 新增行 + 新通知。
        「终态」于是退化成「到下一次有人改这行为止」。

        **状态机守卫拦不住第 ③ 步**：那个请求可以完全不带 `status`，
        `_transition_action` 连分支都不进——故守卫必须单独写
        （`buyer_account._guard_rejected_customer_id`）。
        *修前红*：去掉那个守卫，第 ③ 步回 200，第 ④ 步落出第二条行。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            conn.execute("DELETE FROM app.notification WHERE team_id = %s", (seeded["team"],))
        inst = _issue(client, auth)
        fake = _new_customer_id()

        # ① 灌
        assert _pull(client, inst, fake).json()["data"] == []
        with psycopg.connect(migrated_db) as conn:
            account_id = _pending_rows(conn, seeded)[0][0]
        url = f"/api/v1/buyer-accounts/{account_id}"

        # ② 驳回
        assert client.patch(url, headers=auth, json={"status": "rejected"}).status_code == 200

        # ③ 挪走 customerId —— 必须 409
        moved = client.patch(url, headers=auth, json={"external_customer_id": _new_customer_id()})
        assert moved.status_code == 409, moved.text
        assert moved.json()["error"]["code"] == "BUYER_ACCOUNT_REJECTED_IMMUTABLE"

        # ④ 再灌同一个 id：仍是那一行、仍 rejected、零新增行、零新通知
        assert _pull(client, inst, fake).json()["data"] == []
        with psycopg.connect(migrated_db) as conn:
            assert conn.execute(
                "SELECT id, status FROM app.buyer_account WHERE team_id = %s"
                " AND external_customer_id = %s",
                (seeded["team"], fake),
            ).fetchall() == [(account_id, "rejected")], "粘性被解除了"  # fmt: skip
            assert conn.execute(
                "SELECT count(*) FROM app.buyer_account WHERE team_id = %s", (seeded["team"],)
            ).fetchone()[0] == 1, "驳回行被挪走后又新增了一条"  # fmt: skip
            assert conn.execute(
                "SELECT count(*) FROM app.notification WHERE team_id = %s AND dedupe_key LIKE %s",
                (seeded["team"], "plugin.pending_claim.%"),
            ).fetchone()[0] == 1, "又吵了一条——粘性失效了"  # fmt: skip

        # 其余列照常可改（驳回行仍要能被批注「这是谁在什么时候灌的」），
        # 且把同一个值原样带回来是 no-op、不报错（前端整表单回填是常见形状）。
        keep = client.patch(
            url, headers=auth, json={"note": "确认非我方账号", "external_customer_id": fake}
        )
        assert keep.status_code == 200, keep.text

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
        于是那个 customerId 解析不到 ⇒ 落到**对方团队**的待认领行、**本团队的单一列未变**。
        若哪天有人把业务段改回 `system_tx`，账号立刻可见、单会被派出去，本用例当场红。
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
                "SELECT status FROM app.buyer_account WHERE team_id = %s"
                " AND external_customer_id = %s",
                (foreign, account["customerId"]),
            ).fetchone() == ("pending_claim",), "跨团队解析不到应落对方团队待认领行"  # fmt: skip


# ── 认证域本身 ──


class TestAuthDomain:
    @pytest.mark.parametrize(
        ("method", "path", "payload"),
        [
            ("get", "getNeedPurchaseOrders", None),
            ("get", "getNeedSyncOrders", None),
            ("post", "purchaseOrderFinishUpdate", {"id": 1}),
            ("post", "updateOrderStatus", {"id": 1, "status": 99}),
            ("post", "updateAmzOrderStatus", {"id": 1, "status": 91}),
            ("post", "updateTrackingInfo", {"orderId": 1}),
        ],
    )
    def test_revoked_instance_all_endpoints_401(
        self,
        client: TestClient,
        migrated_db: str,
        seeded: dict,
        method: str,
        path: str,
        payload: dict | None,
    ) -> None:
        """吊销 = 六个端点全域失效。**逐个端点参数化**：漏挂一个依赖就是一个后门。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
        instance = _issue(client, auth)
        assert (
            client.post(
                f"/api/v1/plugin-instances/{instance['id']}/revoke", headers=auth
            ).status_code
            == 200
        )

        kwargs: dict[str, Any] = {"headers": _h(instance)}
        if payload is None:
            kwargs["params"] = {"customerId": account["customerId"]}
        else:
            kwargs["json"] = payload
        r = getattr(client, method)(f"{PLUGIN}/{path}", **kwargs)
        assert r.status_code == 401, r.text
        assert r.json()["error"]["code"] == "PLUGIN_AUTH"

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

    async def test_failure_paths_all_hash_and_compare(
        self,
        client: TestClient,
        migrated_db: str,
        seeded: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """三条失败路径**做同样的活**：都要跑一次散列 + 比较（审查 B6）。

        同码同状态还不够。`row is None` 与「已吊销」原先靠 `or` 短路直接返回，比
        「实例存在但令牌不对」少一次 sha256 + 一次比较——那点时间差恰好把模块头注承诺
        不泄露的「这个实例存不存在 / 是不是已被吊销」漏到了**响应时间**上，探测者拿一把
        随便的 token 扫 id 就能画出实例地图。

        时序本身在单测里测不稳（几十微秒的差异会被调度噪声淹没），故这里钉**代码路径**：
        给 `token_digest` 挂一个计数器，三条路都必须调到它。短路一旦回来，计数立刻对不上。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
        live = _issue(client, auth)
        revoked = _issue(client, auth)
        assert (
            client.post(
                f"/api/v1/plugin-instances/{revoked['id']}/revoke", headers=auth
            ).status_code
            == 200
        )

        calls: list[str] = []
        real = plugin_auth.token_digest

        def counting_digest(token: str) -> str:
            calls.append(token)
            return real(token)

        monkeypatch.setattr(plugin_auth, "token_digest", counting_digest)

        cases = {
            "不存在的 id": ("2000000111", live["token"]),
            "已吊销的实例": (str(revoked["id"]), revoked["token"]),
            "令牌不对": (str(live["id"]), "not-the-token"),
        }
        for label, (instance_id, token) in cases.items():
            before = len(calls)
            async with system_tx(get_session_factory()) as s:
                with pytest.raises(BusinessError) as exc:
                    await plugin_auth.authenticate_instance(s, instance_id, token)
            assert exc.value.code == "PLUGIN_AUTH", label
            assert exc.value.http_status == 401, label
            assert len(calls) == before + 1, f"「{label}」这条路短路了，没跑散列比较"

    def test_stored_hash_is_not_a_usable_token(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """把库里的 `token_hash` 当令牌递进来必须 401——证明服务端比的是 sha256(明文)。

        若哪天有人图省事改成「直接比对头里的值和 token_hash」，一次库读权限就等于
        全部实例的下单能力。这条用例是那次改动的绊线。
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

        r = client.get(
            f"{PLUGIN}/getNeedPurchaseOrders",
            headers={"X-Plugin-Instance": str(instance["id"]), "X-Plugin-Token": stored},
            params={"customerId": account["customerId"]},
        )
        assert r.status_code == 401 and r.json()["error"]["code"] == "PLUGIN_AUTH"


# ── 端点 1：拉取语义（取还幂等 + 载荷形状 + 不猜 ASIN）──


class TestPullTasks:
    def test_pull_payload_shape(self, client: TestClient, migrated_db: str, seeded: dict) -> None:
        """载荷逐字段对齐插件实读（`07:216-224`）——字段名或语义写错，插件是静默买错。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
            order = _mk_order(conn, seeded, lines=(("B0PLUGIN01", 2),))
            po = _mk_po(conn, seeded, order)
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
        assert row[0] == account["id"] and row[1] == "assigned", "拉取即认领"

    def test_pull_is_idempotent(self, client: TestClient, migrated_db: str, seeded: dict) -> None:
        """取还幂等：重复拉回同一批单，不产生新派发、**不重复消耗 daily_cap**。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded, daily_cap=1)
            first = _mk_po(conn, seeded, _mk_order(conn, seeded))
            second = _mk_po(conn, seeded, _mk_order(conn, seeded))
        instance = _issue(client, auth)

        one = _pull(client, instance, account["customerId"]).json()["data"]
        two = _pull(client, instance, account["customerId"]).json()["data"]
        assert [t["id"] for t in one] == [first]
        assert [t["id"] for t in two] == [first], "重复拉必须回同一批（插件重启后还认得自己的单）"
        with psycopg.connect(migrated_db) as conn:
            assert conn.execute(
                "SELECT buyer_account_id FROM app.procurement_order WHERE id = %s", (second,)
            ).fetchone()[0] is None, "额度被重复消耗：第二张单不该被派出去"  # fmt: skip

    def test_missing_asin_task_not_dispatched(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """缺 ASIN 的单**不派、不猜、有告警**——少给一行插件会照着少买一件且无人发现。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
            broken = _mk_po(
                conn, seeded, _mk_order(conn, seeded, lines=(("B0PLUGIN01", 1), (None, 1)))
            )
        instance = _issue(client, auth)

        r = _pull(client, instance, account["customerId"])
        assert r.status_code == 200 and r.json()["data"] == []
        with psycopg.connect(migrated_db) as conn:
            assert conn.execute(
                "SELECT buyer_account_id FROM app.procurement_order WHERE id = %s", (broken,)
            ).fetchone()[0] is None, "缺 ASIN 的单被派出去了"  # fmt: skip
            assert conn.execute(
                "SELECT count(*) FROM app.notification WHERE dedupe_key = %s",
                (f"plugin.no_asin.{broken}",),
            ).fetchone()[0] == 1, "不派也要有人知道，否则症状只剩「插件拉不到单」"  # fmt: skip

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

    def test_pull_touches_last_seen_and_version(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """拉取回写 `last_seen_at` / 版本 / `last_seen_customer_id`。

        第三项是本轮新增的**观察列**（0044 头注六）：换号即自然更新，运营据此在实例列表里
        看出「这台机器现在登的是哪个号」。它**不参与鉴权**——第二段换号后仍能正常拉取，
        本身就是「它没被当成绑定」的证据。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account, other = _mk_account(conn, seeded), _mk_account(conn, seeded)
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
        assert seen is not None and inst[0] is not None
        assert inst[1] == "2.4.1"
        assert inst[2] == account["customerId"]

        assert _pull(client, instance, other["customerId"]).status_code == 200
        with psycopg.connect(migrated_db) as conn:
            assert conn.execute(
                "SELECT last_seen_customer_id FROM app.plugin_instance WHERE id = %s",
                (instance["id"],),
            ).fetchone()[0] == other["customerId"], "换号后观察列没跟着变"  # fmt: skip


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
