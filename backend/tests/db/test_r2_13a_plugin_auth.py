"""R2-13 13a 验收：插件契约端点组 + 实例认证（**验收③「A 实例取不到 B 账号的任务」**）。

| 判据 / 纪律 | 用例 |
|---|---|
| **验收③ 正向**：本实例拉得到自己账号的单 | `test_pull_returns_only_bound_account_tasks` |
| **验收③ 反向**：拉不到别的账号的单 | 同上（同一用例两个断言，缺一即空判据） |
| **验收③**：`customerId` 不符必败 | `test_pull_with_other_customer_id_fails` |
| **验收③**：拿别人的 po_id 回填必败且**一列未变** | `test_backfill_other_account_po_fails` |
| 不泄露存在性：不存在的 id 与他人的 id 同码同状态 | `test_nonexistent_po_same_error_as_unowned` |
| 吊销即全域失效（六端点） | `test_revoked_instance_all_endpoints_401` |
| 认证失败一律 401 同码（含缺头/坏 id/超界 id） | `test_bad_credentials_all_401` |
| 三条失败路径工作量相同（不留时序信道，审查 B6） | `test_failure_paths_all_hash_and_compare` |
| 库里的 hash 不能当令牌用（证明不是裸比） | `test_stored_hash_is_not_a_usable_token` |
| **D4 承重**：插件路径的 RLS 没被 `system_tx` 短路 | `test_rls_still_on_for_plugin_path` |
| 任务取还幂等：重复拉不重复派、不重复耗额度 | `test_pull_is_idempotent` |
| 下发载荷逐字段对齐插件实读 | `test_pull_payload_shape` |
| 缺 ASIN 不猜、不派、有告警 | `test_missing_asin_task_not_dispatched` |
| 账号闸只挡拉取、不挡写路径认证 | `test_paused_account_pulls_nothing_but_writes_authenticate` |
| 拉取回写 last_seen_at 与插件版本 | `test_pull_touches_last_seen_and_version` |
| 端点 6 只列已拍单且属本账号的 | `test_sync_orders_scope` |

**不在本文件**：派发算法本身（`test_r2_13b_dispatch.py`）、回填/异常/物流语义
（13d，本步端点 2/3/4/7 只通了通道，业务写入前显式 501）、cookies 删净
（`test_r2_13_no_cookie_chain.py`）。
"""

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
from erp.plugin import auth as plugin_auth
from erp.plugin import service
from erp.plugin.auth import PluginPrincipal

from .test_identity_api import PASSWORD, _login

ADMIN = "r13a_admin"
TEAM = "R2-13a 插件认证测试团队"
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
    return {"team": team_id, "user": uid, "store": store_id}


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
    tag = uuid.uuid4().hex[:8]
    customer_id = f"A{tag.upper()}"
    account_id = conn.execute(
        "INSERT INTO app.buyer_account (team_id, label, site, external_customer_id,"
        " status, daily_cap) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (seeded["team"], f"指纹浏览器-{tag}", site, customer_id, status, daily_cap),
    ).fetchone()[0]
    return {"id": int(account_id), "customerId": customer_id}


def _issue(client: TestClient, auth: dict[str, str], account_id: int) -> dict[str, Any]:
    """走 13b 的签发端点拿明文令牌（**明文只在这一次响应里出现**）。"""
    r = client.post(f"/api/v1/buyer-accounts/{account_id}/plugin-instances", headers=auth, json={})
    assert r.status_code == 201, r.text
    return dict(r.json())


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


# ── 验收③：实例只能看见/操作自己账号的任务 ──


class TestInstanceScope:
    def test_pull_returns_only_bound_account_tasks(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """**验收③ 的正反两面必须同时成立**：拉得到自己的（否则是空判据），拉不到别人的。"""
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
        inst_a = _issue(client, auth, acc_a["id"])

        r = _pull(client, inst_a, acc_a["customerId"])
        assert r.status_code == 200, r.text
        ids = [t["id"] for t in r.json()["data"]]
        assert po_a in ids, "本账号已派的单必须拉得到（续拉：插件重启后还要能看见）"
        assert po_b not in ids, "验收③：A 实例取到了 B 账号的任务"

    def test_pull_with_other_customer_id_fails(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """`customerId` 只做一致性校验：不符即 403（浏览器登错号的直接信号）。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            acc_a, acc_b = _mk_account(conn, seeded), _mk_account(conn, seeded)
        inst_a = _issue(client, auth, acc_a["id"])

        r = _pull(client, inst_a, acc_b["customerId"])
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "PLUGIN_CUSTOMER_MISMATCH"

    def test_backfill_other_account_po_fails(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """拿别人的 po_id 回填：403 **且那张单一列未变**（越权不得留下任何痕迹）。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            acc_a, acc_b = _mk_account(conn, seeded), _mk_account(conn, seeded)
            po_b = _mk_po(
                conn, seeded, _mk_order(conn, seeded), status="assigned",
                buyer_account_id=acc_b["id"],
            )  # fmt: skip
            before = _snapshot(conn, po_b)
        inst_a = _issue(client, auth, acc_a["id"])

        r = client.post(
            f"{PLUGIN}/purchaseOrderFinishUpdate",
            headers=_h(inst_a),
            json={
                "id": po_b,
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
            assert _snapshot(conn, po_b) == before

    def test_nonexistent_po_same_error_as_unowned(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """不存在的 id 与他人的 id **同码同状态**——越权可见于日志，存在性不外泄。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            acc_a, acc_b = _mk_account(conn, seeded), _mk_account(conn, seeded)
            po_b = _mk_po(
                conn, seeded, _mk_order(conn, seeded), status="assigned",
                buyer_account_id=acc_b["id"],
            )  # fmt: skip
        inst_a = _issue(client, auth, acc_a["id"])

        others = client.post(
            f"{PLUGIN}/updateOrderStatus",
            headers=_h(inst_a),
            json={"id": po_b, "status": 99, "failReason": "商品无库存"},
        )
        ghost = client.post(
            f"{PLUGIN}/updateOrderStatus",
            headers=_h(inst_a),
            json={"id": 2_000_000_111, "status": 99, "failReason": "商品无库存"},
        )
        assert others.status_code == ghost.status_code == 403
        assert others.json()["error"]["code"] == ghost.json()["error"]["code"]

    async def test_rls_still_on_for_plugin_path(
        self, migrated_db: str, seeded: dict, team_ids: tuple[int, int]
    ) -> None:
        """**D4 承重**：插件路径的业务查询走 `ctx_tx`，RLS 没被认证那步的 `system_tx` 短路。

        直接拿另一个团队的上下文跑同一个服务函数——账号行不可见 ⇒ fail-closed 回
        `PLUGIN_AUTH`，且那张待派单不会被认领。若哪天有人把业务段改回 `system_tx`，
        本用例立刻变红。
        """
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
            po = _mk_po(conn, seeded, _mk_order(conn, seeded))
        foreign = next(t for t in team_ids if t != seeded["team"])
        principal = PluginPrincipal(
            instance_id=-1,
            team_id=foreign,
            buyer_account_id=account["id"],
            exec_mode="stop_before_payment",
            account_site="amazon_com",
            account_status="active",
            external_customer_id=account["customerId"],
        )

        async with ctx_tx(get_session_factory(), team_id=foreign) as s:
            visible = (
                await s.execute(
                    text("SELECT count(*) FROM app.buyer_account WHERE id = :i"),
                    {"i": account["id"]},
                )
            ).scalar_one()
            assert visible == 0, "另一个团队的会话看得见本团队的买家账号——RLS 没生效"
            with pytest.raises(BusinessError) as exc:
                await service.pull_purchase_tasks(
                    s, principal, customer_id=account["customerId"], version=None
                )
        assert exc.value.code == "PLUGIN_AUTH"
        with psycopg.connect(migrated_db) as conn:
            assert conn.execute(
                "SELECT buyer_account_id FROM app.procurement_order WHERE id = %s", (po,)
            ).fetchone()[0] is None  # fmt: skip


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
        instance = _issue(client, auth, account["id"])
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
        instance = _issue(client, auth, account["id"])
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
            account = _mk_account(conn, seeded)
        live = _issue(client, auth, account["id"])
        revoked = _issue(client, auth, account["id"])
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
        instance = _issue(client, auth, account["id"])
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
        instance = _issue(client, auth, account["id"])

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
        instance = _issue(client, auth, account["id"])

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
        instance = _issue(client, auth, account["id"])

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
        instance = _issue(client, auth, account["id"])

        pulled = _pull(client, instance, account["customerId"])
        assert pulled.status_code == 200 and pulled.json()["data"] == []

        wrote = client.post(
            f"{PLUGIN}/updateOrderStatus",
            headers=_h(instance),
            json={"id": po, "status": 99, "failReason": "商品无库存"},
        )
        assert wrote.status_code not in (401, 403), (
            f"账号被停不得影响写路径的认证与归属判定（实得 {wrote.status_code}）"
        )

    def test_pull_touches_last_seen_and_version(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """拉取回写 `last_seen_at` 与插件版本——掉线判断与灰度期「谁还在跑旧版」都靠它。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
        instance = _issue(client, auth, account["id"])

        assert _pull(client, instance, account["customerId"], v="2.4.1").status_code == 200
        with psycopg.connect(migrated_db) as conn:
            seen = conn.execute(
                "SELECT last_seen_at FROM app.buyer_account WHERE id = %s", (account["id"],)
            ).fetchone()[0]
            inst = conn.execute(
                "SELECT last_seen_at, version FROM app.plugin_instance WHERE id = %s",
                (instance["id"],),
            ).fetchone()
        assert seen is not None and inst[0] is not None
        assert inst[1] == "2.4.1"


# ── 端点 6：待物流同步 ──


class TestSyncOrders:
    def test_sync_orders_scope(self, client: TestClient, migrated_db: str, seeded: dict) -> None:
        """只列**本账号**、**已拍单**、**有渠道单号**的；未拍的与别人的都不在内。"""
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
        inst_a = _issue(client, auth, acc_a["id"])

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
