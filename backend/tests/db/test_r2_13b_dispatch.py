"""R2-13 13b 验收：买家账号池 + 任务路由（判据④「同一订单不产生两个采购任务」）。

| 判据 / 铁律 | 用例（类见下） |
|---|---|
| DB 层唯一派发确实存在（绕全部应用层注入违反） | `test_constraint_violation_injected` |
| 验收④ 同一订单只派一个账号（批量 + 并发两路） | `test_batch_claims_only_one_po_per_order` |
| 同上，并发形态 | `test_concurrent_pull_dispatches_once` |
| 谓词正向：cancelled 不锁死重建单 | `test_cancelled_does_not_block_redispatch` |
| 谓词反向：exception 必须挡住重派（钱已花＝二次扣款） | `test_exception_does_block_redispatch` |
| 零回归：人工路径该列恒 NULL，唯一索引不触发 | `test_manual_path_unaffected` |
| `daily_cap` 唯一落点＝账号列，按**派发数**计 | `TestDailyCap` |
| 并发拉同一账号不得超 cap（账号行锁，审查 B1） | `test_concurrent_pulls_do_not_exceed_cap` |
| 签发端点**不收 `live`**（升实盘只能 PATCH，审查 B2） | `test_issue_refuses_live` |
| 站点路由 + 无收货国不猜（fail-closed + 告警） | `TestSiteRouting` 三例 |
| 账号闸只挡拉取（写路径不受影响见下注） | `TestAccountGate` |
| 应用层对偶：已派机器人的单不许再派人工/领单/回填 | `TestAssignInterlock` 前三例 |
| 混派：同一订单已有在途单则不许再建第二张 | `test_create_rejects_second_in_flight_po` |
| 插件认领镜像 `assign_po` 的订单联动 | `test_plugin_claim_advances_channel_order` |
| 插件异常单的显式出边（释放回池 → 可重新派发） | `TestPluginRelease` 四例 |
| 14b 耦合：退回池必须一并清 `buyer_account_id` | `test_pool_return_clears_buyer_account` |
| 令牌散列链：明文不入库、列表与审计不回显 | `test_issue_instance_token_only_once` |
| 日界与批量上限不写死（配置中心） | `TestDispatchConfig` 两例 |

**不在本文件**（属 13a/13d）：双头部认证与越权（A 实例取不到 B 账号的任务）、
六端点 401、插件侧回填/异常/物流语义。本文件只测服务层与管理面。

> **「账号闸只挡拉取、不挡写路径」的写侧证据在 13a**
> （`test_paused_account_pulls_nothing_but_writes_authenticate`）——那条纪律说的是**插件的**
> 写路径（回填/异常/物流三条端点），而本文件此前用「人工 `/backfill` 端点」当代理去测它。
> 审查 A1 之后人工写路径对插件单一律 409（理由见
> `procurement.py::_reject_if_plugin_dispatched`），那个代理不再成立：故本文件只留拉取侧
> （`TestAccountGate`），写侧由 13a 那条钉住，人工侧改由
> `test_backfill_po_rejects_plugin_dispatched` 反向钉住。
"""

import asyncio
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from erp.core.db import ctx_tx, get_session_factory
from erp.core.security import hash_password
from erp.order import dispatch

from .test_identity_api import PASSWORD, _login

ADMIN = "r13b_admin"
TEAM = "R2-13b 买家账号池测试团队"


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        # 先清依赖方再清被依赖方（plugin_instance → buyer_account 是硬外键）
        for t in ("plugin_instance", "buyer_account", "procurement_order", "order_check",
                  "order_line", "channel_order", "purchaser", "deleted_principal",
                  "automation_policy", "store"):  # fmt: skip
            conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.app_user WHERE username LIKE 'r13b_%'")
        uid = conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '账号池管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute("DELETE FROM app.role WHERE team_id = %s AND name = '13b测试角色'", (team_id,))
        role_id = conn.execute(
            "INSERT INTO app.role (team_id, name) VALUES (%s, '13b测试角色') RETURNING id",
            (team_id,),
        ).fetchone()[0]
        for code in (
            "order.read", "order.assign", "procurement.read", "procurement.execute",
            "procurement.admin", "procurement.purchaser_delete",
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
            " VALUES (%s, %s, 'BA13B', '账号池测试店', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
    return {"team": team_id, "user": uid, "store": store_id}


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> TestClient:
    from erp.main import app

    return TestClient(app)


# ── 造数助手 ──


def _reset(conn: psycopg.Connection, seeded: dict[str, int]) -> None:
    """每个用例开跑前清空本团队的单与账号——候选查询按 created_at 取最老的，
    上一个用例的残留会被下一个用例捞走，断言就不可复现了。"""
    for t in ("plugin_instance", "buyer_account", "procurement_order", "order_check",
              "order_line", "channel_order", "purchaser"):  # fmt: skip
        conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (seeded["team"],))


def _mk_order(
    conn: psycopg.Connection, seeded: dict[str, int], *, country: str | None = "US"
) -> tuple[int, Any]:
    ship_to = '{"name": "B", "postalCode": "36759"}'
    if country is not None:
        ship_to = f'{{"name": "B", "postalCode": "36759", "country": "{country}"}}'
    row = conn.execute(
        "INSERT INTO app.channel_order (team_id, store_id, channel_order_no, order_date,"
        " channel_status, customer, ship_to, order_total, item_count, pulled_at,"
        " internal_status, has_flag)"
        " VALUES (%s, %s, %s, now() - interval '1 day', 'Created', '{}',"
        "         %s::jsonb, 10, 1, now(), 'checked', false)"
        " RETURNING id, order_date",
        (seeded["team"], seeded["store"], f"BA13B-{uuid.uuid4().hex[:8]}", ship_to),
    ).fetchone()
    return int(row[0]), row[1]


def _mk_po(
    conn: psycopg.Connection,
    seeded: dict[str, int],
    order: tuple[int, Any],
    *,
    status: str = "unassigned",
    buyer_account_id: int | None = None,
    purchaser_id: int | None = None,
    exception_kind: str | None = None,
    exception_reason: str | None = None,
    purchase_order_ref: str | None = None,
) -> int:
    """直插 procurement_order（绕应用层）——用于造应用层不该产生的形状。"""
    assigned_at = "now()" if buyer_account_id is not None else "NULL"
    return int(
        conn.execute(
            "INSERT INTO app.procurement_order (team_id, store_id, order_id, order_date,"
            " status, buyer_account_id, purchaser_id, assignee_kind, assigned_at,"
            " exception_kind, exception_reason, purchase_order_ref)"
            f" VALUES (%s, %s, %s, %s, %s, %s, %s, %s, {assigned_at}, %s, %s, %s) RETURNING id",
            (
                seeded["team"],
                seeded["store"],
                order[0],
                order[1],
                status,
                buyer_account_id,
                purchaser_id,
                "internal" if purchaser_id else "none",
                exception_kind,
                exception_reason,
                purchase_order_ref,
            ),
        ).fetchone()[0]
    )


def _internal_status(conn: psycopg.Connection, order: tuple[int, Any]) -> str:
    return str(
        conn.execute(
            "SELECT internal_status FROM app.channel_order WHERE id = %s AND order_date = %s",
            (order[0], order[1]),
        ).fetchone()[0]
    )


def _mk_account(
    conn: psycopg.Connection,
    seeded: dict[str, int],
    *,
    site: str = "amazon_com",
    status: str = "active",
    daily_cap: int | None = None,
) -> int:
    tag = uuid.uuid4().hex[:8]
    return int(
        conn.execute(
            "INSERT INTO app.buyer_account (team_id, label, site, external_customer_id,"
            " status, daily_cap) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (seeded["team"], f"号-{tag}", site, f"A{tag.upper()}", status, daily_cap),
        ).fetchone()[0]
    )


async def _pull(team_id: int, account_id: int, *, limit: int = 20) -> tuple[list[int], str | None]:
    """按账号真实属性调派发内核（形状与 13a 的拉取端点将来要用的一致）。"""
    async with ctx_tx(get_session_factory(), team_id=team_id) as s:
        acc = (
            (
                await s.execute(
                    text("SELECT site, status, daily_cap FROM app.buyer_account WHERE id = :i"),
                    {"i": account_id},
                )
            )
            .mappings()
            .one()
        )
        return await dispatch.claim_tasks_for_account(
            s,
            team_id=team_id,
            buyer_account_id=account_id,
            site=acc["site"],
            account_status=acc["status"],
            daily_cap=acc["daily_cap"],
            limit=limit,
        )


def _dispatched(conn: psycopg.Connection, po_id: int) -> int | None:
    row = conn.execute(
        "SELECT buyer_account_id FROM app.procurement_order WHERE id = %s", (po_id,)
    ).fetchone()
    return None if row[0] is None else int(row[0])


# ── 验收④ + 铁律「同一渠道订单只派一个买家账号」 ──


class TestUniqueDispatch:
    def test_constraint_violation_injected(self, migrated_db: str, seeded: dict) -> None:
        """**约束真的在 DB 里**：用 migrator 直连（绕 RLS 与全部应用层）注入违反。

        这条与其余用例的区别在于它不经过任何 Python 判断——没有它，「唯一派发」可能
        只是应用层的一句 if，将来任何新写入路径（beat / 运维 SQL / 另一条端点）都能绕开。
        """
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
            order = _mk_order(conn, seeded)
            po_a, po_b = _mk_po(conn, seeded, order), _mk_po(conn, seeded, order)
            conn.execute(
                "UPDATE app.procurement_order SET buyer_account_id = %s WHERE id = %s",
                (account, po_a),
            )
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "UPDATE app.procurement_order SET buyer_account_id = %s WHERE id = %s",
                    (account, po_b),
                )

    async def test_batch_claims_only_one_po_per_order(self, migrated_db: str, seeded: dict) -> None:
        """同一订单两张待派 PO，一次拉取只派得出一张——批量撞约束后逐单回退。

        同时证伪「一单撞车整批失败」：同批里另一张不同订单的 PO 必须照样派出去。
        """
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
            same = _mk_order(conn, seeded)
            po_a, po_b = _mk_po(conn, seeded, same), _mk_po(conn, seeded, same)
            po_c = _mk_po(conn, seeded, _mk_order(conn, seeded))

        claimed, reason = await _pull(seeded["team"], account)
        assert reason is None
        assert po_c in claimed, "撞车的那张不该拖累同批其余单"
        assert len({po_a, po_b} & set(claimed)) == 1, f"同一订单派了不止一张：{claimed}"
        with psycopg.connect(migrated_db) as conn:
            dispatched = [p for p in (po_a, po_b) if _dispatched(conn, p) is not None]
        assert len(dispatched) == 1

    async def test_concurrent_pull_dispatches_once(self, migrated_db: str, seeded: dict) -> None:
        """并发下发压测（验收④ 的原文形态）：两个账号四路并发抢同一订单的两张 PO。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            acc_x, acc_y = _mk_account(conn, seeded), _mk_account(conn, seeded)
            order = _mk_order(conn, seeded)
            pos = [_mk_po(conn, seeded, order) for _ in range(2)]

        results = await asyncio.gather(
            *(_pull(seeded["team"], a) for a in (acc_x, acc_y, acc_x, acc_y)),
            return_exceptions=True,
        )
        assert not [r for r in results if isinstance(r, BaseException)], results
        with psycopg.connect(migrated_db) as conn:
            owners = [_dispatched(conn, p) for p in pos]
        assert len([o for o in owners if o is not None]) == 1, owners

    async def test_cancelled_does_not_block_redispatch(
        self, migrated_db: str, seeded: dict
    ) -> None:
        """谓词正向：`cancelled` 是唯一明确的作废终态，不排除会锁死「取消后重建单」。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
            order = _mk_order(conn, seeded)
            _mk_po(conn, seeded, order, status="cancelled", buyer_account_id=account)
            fresh = _mk_po(conn, seeded, order)

        claimed, reason = await _pull(seeded["team"], account)
        assert claimed == [fresh] and reason is None

    async def test_exception_does_block_redispatch(self, migrated_db: str, seeded: dict) -> None:
        """谓词反向：`exception` **不排除**——17 条异常里 #16 是「回传订单失败」，
        那意味着单已真下出去、钱已经花了。此刻放行重派就是二次扣款。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
            order = _mk_order(conn, seeded)
            _mk_po(conn, seeded, order, status="exception", buyer_account_id=account)
            fresh = _mk_po(conn, seeded, order)

        claimed, reason = await _pull(seeded["team"], account)
        assert claimed == []
        assert reason == dispatch.REASON_ALREADY_DISPATCHED
        with psycopg.connect(migrated_db) as conn:
            assert _dispatched(conn, fresh) is None

    def test_manual_path_unaffected(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """零回归：人工路径 `buyer_account_id` 恒 NULL ⇒ 部分唯一索引整条谓词不成立，
        同一订单可以有多张人工执行单（R2-05 既有行为一条不变）。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            order = _mk_order(conn, seeded)
            po_a, po_b = _mk_po(conn, seeded, order), _mk_po(conn, seeded, order)
            purchaser = int(
                conn.execute(
                    "INSERT INTO app.purchaser (team_id, name, purchaser_kind, exchange_rate)"
                    " VALUES (%s, '人工采购方', 'external', 7.1) RETURNING id",
                    (seeded["team"],),
                ).fetchone()[0]
            )
        for po in (po_a, po_b):
            r = client.post(
                f"/api/v1/procurement-orders/{po}/assign",
                headers=auth,
                json={"purchaser_id": purchaser},
            )
            assert r.status_code == 200, r.text
        with psycopg.connect(migrated_db) as conn:
            assert [_dispatched(conn, p) for p in (po_a, po_b)] == [None, None]


# ── daily_cap（唯一落点＝账号列；口径＝派发数不是拍成数）──


class TestDailyCap:
    async def test_blocks_beyond_cap(self, migrated_db: str, seeded: dict) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded, daily_cap=2)
            for _ in range(3):
                _mk_po(conn, seeded, _mk_order(conn, seeded))

        claimed, reason = await _pull(seeded["team"], account, limit=5)
        assert len(claimed) == 2 and reason is None

    async def test_null_cap_is_unlimited(self, migrated_db: str, seeded: dict) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded, daily_cap=None)
            for _ in range(3):
                _mk_po(conn, seeded, _mk_order(conn, seeded))

        claimed, reason = await _pull(seeded["team"], account, limit=5)
        assert len(claimed) == 3 and reason is None

    async def test_counts_dispatch_not_purchase(self, migrated_db: str, seeded: dict) -> None:
        """口径证伪：单派出去就算数，拍没拍成都一样。

        按「已拍成的单数」计的话，一个账号可被派 100 单再一口气拍完——帽子形同虚设。
        """
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded, daily_cap=2)
            for _ in range(4):
                _mk_po(conn, seeded, _mk_order(conn, seeded))

        first, _ = await _pull(seeded["team"], account, limit=2)
        assert len(first) == 2
        # 推到 purchased：既不在续拉可见范围（assigned/claimed）内，也不该退还额度
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.procurement_order SET status = 'purchased' WHERE id = ANY(%s)",
                (first,),
            )
        claimed, reason = await _pull(seeded["team"], account, limit=2)
        assert claimed == []
        assert reason == dispatch.REASON_DAILY_CAP_REACHED

    async def test_repull_does_not_consume_cap(self, migrated_db: str, seeded: dict) -> None:
        """续拉＝同一批单的重新可见（插件重启后要还看得见自己的单），不是新派发。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded, daily_cap=2)
            for _ in range(4):
                _mk_po(conn, seeded, _mk_order(conn, seeded))

        first, _ = await _pull(seeded["team"], account, limit=2)
        again, reason = await _pull(seeded["team"], account, limit=2)
        assert sorted(again) == sorted(first) and reason is None
        with psycopg.connect(migrated_db) as conn:
            total = conn.execute(
                "SELECT count(*) FROM app.procurement_order"
                " WHERE team_id = %s AND buyer_account_id IS NOT NULL",
                (seeded["team"],),
            ).fetchone()[0]
        assert total == 2, "续拉不得消耗新额度"

    async def test_concurrent_pulls_do_not_exceed_cap(self, migrated_db: str, seeded: dict) -> None:
        """并发拉同一账号，合计不得超过 `daily_cap`（审查 B1）。

        `daily_cap` 是 read-then-claim：先数今日已派、再按余量认领。没有账号级串行化时，
        N 路并发各自读到的 `used` 都是对方写入之前的值，于是能派出 **N×cap** 单。
        **`SKIP LOCKED` 在这里帮倒忙**——它让各路的候选集互斥，于是两边都不撞车、
        都成功，把超发放大而不是挡住（这正是「绿的并发测试没证明帽子还在」的形状：
        `test_concurrent_pull_dispatches_once` 断言的是唯一派发索引，与日限无关）。

        每张单挂在**不同订单**上：`uq_po_active_dispatch` 只挡「同一订单派两次」，
        本例要测的是日限，不能让那个索引替日限背书。
        """
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded, daily_cap=2)
            for _ in range(6):
                _mk_po(conn, seeded, _mk_order(conn, seeded))

        results = await asyncio.gather(
            *(_pull(seeded["team"], account, limit=2) for _ in range(3)),
            return_exceptions=True,
        )
        assert not [r for r in results if isinstance(r, BaseException)], results
        with psycopg.connect(migrated_db) as conn:
            dispatched = conn.execute(
                "SELECT count(*) FROM app.procurement_order"
                " WHERE team_id = %s AND buyer_account_id = %s",
                (seeded["team"], account),
            ).fetchone()[0]
        assert dispatched == 2, f"并发拉取派出了 {dispatched} 单，daily_cap=2 被击穿"

    async def test_cap_resets_across_day_boundary(self, migrated_db: str, seeded: dict) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded, daily_cap=2)
            for _ in range(4):
                _mk_po(conn, seeded, _mk_order(conn, seeded))

        first, _ = await _pull(seeded["team"], account, limit=2)
        assert len(first) == 2
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            # 把昨天派的单挪出今日窗口，并推到 purchased 免得续拉占满 limit
            conn.execute(
                "UPDATE app.procurement_order SET assigned_at = now() - interval '2 days',"
                " status = 'purchased' WHERE id = ANY(%s)",
                (first,),
            )
        claimed, reason = await _pull(seeded["team"], account, limit=2)
        assert len(claimed) == 2 and reason is None
        assert not set(claimed) & set(first)


# ── 站点路由 ──


class TestSiteRouting:
    async def test_routes_by_country_and_skips_unknown(
        self, migrated_db: str, seeded: dict
    ) -> None:
        """US 账号只拿 US 单；CA 单不碰；**无收货国的单不猜、不派**，落一条告警。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            conn.execute(
                "DELETE FROM app.notification WHERE team_id = %s AND category = 'procurement'",
                (seeded["team"],),
            )
            account = _mk_account(conn, seeded, site="amazon_com")
            us = _mk_po(conn, seeded, _mk_order(conn, seeded, country="US"))
            ca = _mk_po(conn, seeded, _mk_order(conn, seeded, country="CA"))
            blank = _mk_po(conn, seeded, _mk_order(conn, seeded, country=None))

        claimed, reason = await _pull(seeded["team"], account, limit=10)
        assert claimed == [us] and reason is None
        with psycopg.connect(migrated_db) as conn:
            assert _dispatched(conn, ca) is None
            assert _dispatched(conn, blank) is None
            warned = conn.execute(
                "SELECT count(*) FROM app.notification WHERE dedupe_key = %s",
                (f"plugin.site.{blank}",),
            ).fetchone()[0]
        assert warned == 1, "无收货国的单必须留告警，否则它会静默躺着没人管"

    async def test_ca_account_takes_ca_order(self, migrated_db: str, seeded: dict) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded, site="amazon_ca")
            _mk_po(conn, seeded, _mk_order(conn, seeded, country="US"))
            ca = _mk_po(conn, seeded, _mk_order(conn, seeded, country="CA"))

        claimed, _ = await _pull(seeded["team"], account, limit=10)
        assert claimed == [ca]

    def test_site_country_map_keeps_jp(self) -> None:
        """铁律 9：日本站 MVP 不做，但代码路径保留——将来开 JP 只补映射表。"""
        assert dispatch.site_country("amazon_co_jp") == "JP"
        assert dispatch.site_country("amazon_de") is None


# ── 账号闸：只挡拉取，不挡写路径 ──


class TestAccountGate:
    @pytest.mark.parametrize("status", ["paused", "blocked", "retired"])
    async def test_non_active_pulls_nothing(
        self, migrated_db: str, seeded: dict, status: str
    ) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded, status=status)
            _mk_po(conn, seeded, _mk_order(conn, seeded))

        claimed, reason = await _pull(seeded["team"], account)
        assert claimed == []
        assert reason == dispatch.REASON_ACCOUNT_NOT_ACTIVE


# ── 与人工路径的互锁（审查 A1/A2）──


class TestAssignInterlock:
    """插件派发单 ⇒ 人工侧三条写路径全 409；同一订单 ⇒ 只允许一张在途执行单。

    三条端点共用 `procurement.py::_reject_if_plugin_dispatched`，但**必须一条一条测**：
    审查 A1 的实测绕过序列正是「assign 有守卫、claim 没有」——共用一个 helper 不等于
    三个调用点都接了它，接没接只有逐个调用点断言才证得出来。
    """

    def _plugin_po(
        self, conn: psycopg.Connection, seeded: dict, *, status: str = "assigned"
    ) -> tuple[int, tuple[int, Any]]:
        """造一张「插件已派发」的单：`status='assigned'` + `buyer_account_id` 非空。

        这正是 `dispatch.py::_CLAIM_SQL` 落地后的形状，不是编出来的形状。
        """
        _reset(conn, seeded)
        account = _mk_account(conn, seeded)
        order = _mk_order(conn, seeded)
        return _mk_po(conn, seeded, order, status=status, buyer_account_id=account), order

    def test_assign_po_rejects_plugin_dispatched(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """不加这条守卫，一张已派给买家账号的单还能被分配给 purchaser——
        同一订单既派机器人又派人，两边都会真下单。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            po, _ = self._plugin_po(conn, seeded)
            purchaser = int(
                conn.execute(
                    "INSERT INTO app.purchaser (team_id, name, purchaser_kind, exchange_rate)"
                    " VALUES (%s, '抢单采购方', 'external', 7.1) RETURNING id",
                    (seeded["team"],),
                ).fetchone()[0]
            )
        r = client.post(
            f"/api/v1/procurement-orders/{po}/assign",
            headers=auth,
            json={"purchaser_id": purchaser},
        )
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "PROCUREMENT_PLUGIN_DISPATCHED"

    def test_claim_po_rejects_plugin_dispatched(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """审查 A1 实测的绕过序列：插件派发落 `assigned`，而 `claim_po` 只判
        `status == 'assigned'` ⇒ 人工 `POST /claim` 200 ⇒ 机器人与人各下一次单。

        修前这里回 200 并把 `status` 推成 `claimed`（把单从插件手里抢走）。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            po, _ = self._plugin_po(conn, seeded)
        r = client.post(f"/api/v1/procurement-orders/{po}/claim", headers=auth)
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "PROCUREMENT_PLUGIN_DISPATCHED"
        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT status, buyer_account_id FROM app.procurement_order WHERE id = %s", (po,)
            ).fetchone()
        assert row[0] == "assigned" and row[1] is not None, "守卫必须连一个字段都不改"

    @pytest.mark.parametrize("status", ["assigned", "purchased"])
    def test_backfill_po_rejects_plugin_dispatched(
        self, client: TestClient, migrated_db: str, seeded: dict, status: str
    ) -> None:
        """人工回填插件单会把 `status` 推成 `backfilled`，而插件侧的白名单
        （`plugin/service.py::_PURCHASABLE_FROM`）**不含** `backfilled` ⇒ 插件真下出去的
        金额此后永远写不进来。两个参数化状态是插件单实际会停的两个位置。

        修前这里回 200。**写侧「账号闸不挡写路径」那条纪律不受影响**：它说的是插件的
        写路径，证据在 13a `test_paused_account_pulls_nothing_but_writes_authenticate`。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            po, _ = self._plugin_po(conn, seeded, status=status)
        r = client.post(
            f"/api/v1/procurement-orders/{po}/backfill",
            headers=auth,
            json={"purchase_order_ref": "111-2223334-4445556", "purchase_cost": 12.34},
        )
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "PROCUREMENT_PLUGIN_DISPATCHED"
        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT status, purchase_order_ref FROM app.procurement_order WHERE id = %s", (po,)
            ).fetchone()
        assert row == (status, None), "守卫必须连一个字段都不改"

    def test_create_rejects_second_in_flight_po(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """审查 A2 的混派路径：`uq_po_active_dispatch` 只约束插件侧（谓词带
        `buyer_account_id IS NOT NULL`），人工**新建**一张单时该列是 NULL ⇒ DB 放行 ⇒
        同一渠道订单可以同时挂着「插件在拍」和「人工另开的单」，两边都真下单。

        修前 `POST /procurement-orders` 回 201（造出第二张在途单）。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _, order = self._plugin_po(conn, seeded)
        r = client.post("/api/v1/procurement-orders", headers=auth, json={"order_id": order[0]})
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "PROCUREMENT_ORDER_IN_FLIGHT"
        with psycopg.connect(migrated_db) as conn:
            n = conn.execute(
                "SELECT count(*) FROM app.procurement_order WHERE order_id = %s", (order[0],)
            ).fetchone()[0]
        assert n == 1, "409 之后不许留下半张单"

    @pytest.mark.parametrize("done", ["cancelled", "backfilled"])
    def test_create_allowed_after_terminal_po(
        self, client: TestClient, migrated_db: str, seeded: dict, done: str
    ) -> None:
        """A2 的闸不能顺手锁死既有人工路径：`cancelled`（作废）与 `backfilled`（办完）
        都是终态，之后重新建单是 R2-05 就有的合法动作。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            order = _mk_order(conn, seeded)
            _mk_po(conn, seeded, order, status=done)
        r = client.post("/api/v1/procurement-orders", headers=auth, json={"order_id": order[0]})
        assert r.status_code == 201, r.text

    async def test_plugin_claim_advances_channel_order(
        self, migrated_db: str, seeded: dict
    ) -> None:
        """审查 A2 的不对称加重项：人工 `assign_po` 把渠道订单推到 `assigned`，插件认领
        此前**只改执行单不动订单** ⇒ 订单列表里那单仍显示 `checked`（＝「等着派」），
        运营据此再建一张人工单去下单，正是混派的上游诱因。

        修前这里断言 `'assigned'` 会拿到 `'checked'`。
        """
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
            order = _mk_order(conn, seeded)
            po = _mk_po(conn, seeded, order)
            assert _internal_status(conn, order) == "checked"

        claimed, _ = await _pull(seeded["team"], account)
        assert claimed == [po]
        with psycopg.connect(migrated_db) as conn:
            assert _internal_status(conn, order) == "assigned"

    async def test_plugin_claim_does_not_pull_back_later_states(
        self, migrated_db: str, seeded: dict
    ) -> None:
        """条件推进（`from_statuses=('checked',)`）与 `assign_po` 逐字同款：已经走到
        `purchasing` 的订单不许被认领动作拉回 `assigned`。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
            order = _mk_order(conn, seeded)
            po = _mk_po(conn, seeded, order)
            conn.execute(
                "UPDATE app.channel_order SET internal_status = 'purchasing'"
                " WHERE id = %s AND order_date = %s",
                (order[0], order[1]),
            )

        claimed, _ = await _pull(seeded["team"], account)
        assert claimed == [po]
        with psycopg.connect(migrated_db) as conn:
            assert _internal_status(conn, order) == "purchasing"

    def test_pool_return_clears_buyer_account(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """14b 耦合：删采购方把未锁汇率的单退回池时，**必须一并清 `buyer_account_id`**
        ——否则那张单顶着唯一派发索引回到待派池，从此永远派不出去。

        本形状（同时有 purchaser_id 与 buyer_account_id）在当前不变量下不该出现，
        故绕应用层直插造出来：这条测的正是「不变量被破时失败在安全的一侧」。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
            purchaser = int(
                conn.execute(
                    "INSERT INTO app.purchaser (team_id, name, purchaser_kind, exchange_rate)"
                    " VALUES (%s, '待删采购方', 'external', 7.2) RETURNING id",
                    (seeded["team"],),
                ).fetchone()[0]
            )
            po = _mk_po(
                conn,
                seeded,
                _mk_order(conn, seeded),
                status="assigned",
                buyer_account_id=account,
                purchaser_id=purchaser,
            )
        r = client.delete(f"/api/v1/purchasers/{purchaser}?reason=13b耦合测试", headers=auth)
        assert r.status_code == 200, r.text
        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT status, purchaser_id, buyer_account_id FROM app.procurement_order"
                " WHERE id = %s",
                (po,),
            ).fetchone()
        assert row == ("unassigned", None, None)


# ── 插件异常单的显式出边（审查 A3）──


class TestPluginRelease:
    """`POST /procurement-orders/{id}/release`：0045 头注二承诺、此前代码里不存在的出边。

    没有它，插件把单标成 `exception` 之后 `buyer_account_id` 永远非空，
    `uq_po_active_dispatch` 长期占着该 `order_id`（谓词故意不排除 `exception`），
    该渠道订单再也派不出去；人工侧又被 `PROCUREMENT_PLUGIN_DISPATCHED` 挡住 —— 单卡死。
    """

    def _exception_po(
        self, conn: psycopg.Connection, seeded: dict, **kw: Any
    ) -> tuple[int, tuple[int, Any], int]:
        _reset(conn, seeded)
        account = _mk_account(conn, seeded)
        order = _mk_order(conn, seeded)
        conn.execute(
            "UPDATE app.channel_order SET internal_status = 'assigned'"
            " WHERE id = %s AND order_date = %s",
            (order[0], order[1]),
        )
        po = _mk_po(
            conn,
            seeded,
            order,
            status="exception",
            buyer_account_id=account,
            exception_kind="stock",
            exception_reason="商品无库存",
            **kw,
        )
        return po, order, account

    async def test_release_returns_to_pool_and_redispatches(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """承重全链：释放 → 五个字段清干净 + 订单对称退回 `checked` + 审计留 before
        → **再跑一次派发能重新捞到这单**（出边不通的话最后这步永远拿不到）。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            po, order, account = self._exception_po(conn, seeded)

        r = client.post(f"/api/v1/procurement-orders/{po}/release", headers=auth)
        assert r.status_code == 200, r.text
        assert r.json() == {"id": po, "status": "unassigned"}

        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT status, buyer_account_id, exception_kind, exception_reason, assigned_at"
                " FROM app.procurement_order WHERE id = %s",
                (po,),
            ).fetchone()
            assert row == ("unassigned", None, None, None, None)
            # 14b 先例：assign 推上去的 internal_status，退回就对称退回来
            assert _internal_status(conn, order) == "checked"
            before = conn.execute(
                "SELECT before FROM app.audit_log WHERE action = 'order.po_release'"
                " AND object_id = %s ORDER BY occurred_at DESC LIMIT 1",
                (str(po),),
            ).fetchone()[0]
        assert before["exception_kind"] == "stock"
        assert before["exception_reason"] == "商品无库存"
        assert before["status"] == "exception"

        # 出边真的通了：同一账号重新拉取能捞回这张单
        claimed, reason = await _pull(seeded["team"], account)
        assert claimed == [po] and reason is None

    def test_release_refuses_manual_purchaser_order(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """人工采购方的 `exception` 单属 FX-0730B 既有缺口，出边是「删采购方时退回池」，
        本次不扩——守卫必须把两者分开，不能顺手给人工单开一条新出边。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            purchaser = int(
                conn.execute(
                    "INSERT INTO app.purchaser (team_id, name, purchaser_kind, exchange_rate)"
                    " VALUES (%s, '人工异常方', 'external', 7.3) RETURNING id",
                    (seeded["team"],),
                ).fetchone()[0]
            )
            po = _mk_po(
                conn,
                seeded,
                _mk_order(conn, seeded),
                status="exception",
                purchaser_id=purchaser,
                exception_reason="人工侧异常",
            )
        r = client.post(f"/api/v1/procurement-orders/{po}/release", headers=auth)
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "PROCUREMENT_NOT_PLUGIN_DISPATCHED"

    @pytest.mark.parametrize("status", ["assigned", "claimed"])
    def test_release_refuses_in_execution(
        self, client: TestClient, migrated_db: str, seeded: dict, status: str
    ) -> None:
        """插件仍在执行的单不许释放——那是把活从它手里抽走再派给别人，才是真的双买。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            po = _mk_po(
                conn,
                seeded,
                _mk_order(conn, seeded),
                status=status,
                buyer_account_id=_mk_account(conn, seeded),
            )
        r = client.post(f"/api/v1/procurement-orders/{po}/release", headers=auth)
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "PROCUREMENT_STATE_INVALID"

    def test_release_refuses_already_purchased(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """0045 头注二那句「钱已经花了」的代码落点：`exception_po` 只排除
        backfilled/cancelled，所以「已拍单 + 被人标成 exception」这个形状是可达的；
        对它释放回池 ⇒ 重新派发 ⇒ **二次扣款**。fail-closed 挡掉。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            po, _, _ = self._exception_po(conn, seeded, purchase_order_ref="111-2223334-4445556")
        r = client.post(f"/api/v1/procurement-orders/{po}/release", headers=auth)
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "PROCUREMENT_ALREADY_PURCHASED"
        with psycopg.connect(migrated_db) as conn:
            assert _dispatched(conn, po) is not None, "被拒之后买家账号必须还挂着"


# ── 管理面 CRUD + 令牌散列链 ──


class TestBuyerAccountCrud:
    def test_create_patch_and_duplicates(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
        tag = uuid.uuid4().hex[:6]
        body = {
            "label": f"指纹浏览器-{tag}",
            "site": "amazon_com",
            "external_customer_id": f"A{tag.upper()}",
            "daily_cap": 5,
        }
        r = client.post("/api/v1/buyer-accounts", headers=auth, json=body)
        assert r.status_code == 201, r.text
        account_id = r.json()["id"]

        # 缺 external_customer_id（人工录入通道，必填）
        r = client.post(
            "/api/v1/buyer-accounts",
            headers=auth,
            json={"label": f"另一个-{tag}", "site": "amazon_com"},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "BUYER_ACCOUNT_FIELDS_REQUIRED"

        # 撞 customerId / 撞 label 各自回 409 且指明字段
        r = client.post(
            "/api/v1/buyer-accounts", headers=auth, json={**body, "label": f"换名-{tag}"}
        )
        assert r.status_code == 409
        assert r.json()["error"]["detail"]["field"] == "external_customer_id"
        r = client.post(
            "/api/v1/buyer-accounts",
            headers=auth,
            json={**body, "external_customer_id": f"B{tag.upper()}"},
        )
        assert r.status_code == 409
        assert r.json()["error"]["detail"]["field"] == "label"

        # daily_cap 显式传 null = 改成不限（与「不传」区分开）
        assert (
            client.patch(
                f"/api/v1/buyer-accounts/{account_id}", headers=auth, json={"daily_cap": None}
            ).status_code
            == 200
        )
        page = client.get("/api/v1/buyer-accounts", headers=auth, params={"q": tag}).json()
        assert page["total"] == 1
        assert page["items"][0]["daily_cap"] is None
        assert page["items"][0]["active_instance_count"] == 0
        # 改了不该动的列没被顺手改掉
        assert page["items"][0]["external_customer_id"] == f"A{tag.upper()}"

    def test_issue_instance_token_only_once(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        """令牌散列链：库里只有 sha256、明文只在签发响应出现一次、列表零回显。"""
        import hashlib

        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)

        r = client.post(f"/api/v1/buyer-accounts/{account}/plugin-instances", headers=auth, json={})
        assert r.status_code == 201, r.text
        issued = r.json()
        token = issued["token"]
        assert token and len(token) >= 32

        with psycopg.connect(migrated_db) as conn:
            stored = conn.execute(
                "SELECT token_hash, exec_mode, status FROM app.plugin_instance WHERE id = %s",
                (issued["id"],),
            ).fetchone()
        assert stored[0] != token
        assert stored[0] == hashlib.sha256(token.encode()).hexdigest()
        # 默认档不会花钱（安全边界即默认值）
        assert stored[1] == "stop_before_payment" and stored[2] == "active"

        listed = client.get(f"/api/v1/buyer-accounts/{account}/plugin-instances", headers=auth)
        assert listed.status_code == 200
        blob = listed.text
        assert token not in blob and stored[0] not in blob, "列表端点不得回显 token 任何形态"
        assert listed.json()[0]["exec_mode"] == "stop_before_payment"

        # 审计快照同样不得含凭证（audit_log 是长期留存面）
        with psycopg.connect(migrated_db) as conn:
            audits = conn.execute(
                "SELECT coalesce(after::text, '') FROM app.audit_log"
                " WHERE action = 'plugin_instance.issue' AND object_id = %s",
                (str(issued["id"]),),
            ).fetchall()
        assert audits, "签发必须留审计"
        assert all(token not in a[0] and stored[0] not in a[0] for a in audits)

    def test_issue_refuses_live(self, client: TestClient, migrated_db: str, seeded: dict) -> None:
        """签发端点**不接受 `live`**（审查 B2）：升实盘只能走 PATCH。

        「新签发的实例不会花钱」此前只由**默认值**保证——而默认值挡不住显式传 live 的
        一步直签，于是「升 live 须显式 PATCH（有 audit、有前端二次确认）」这条不变量
        （openapi 该端点白纸黑字）等于没有。两档演练照常放行，PATCH 三档不受影响
        （`test_exec_mode_patch_and_revoke` 钉住）。
        """
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)

        url = f"/api/v1/buyer-accounts/{account}/plugin-instances"
        r = client.post(url, headers=auth, json={"exec_mode": "live"})
        assert r.status_code == 422, r.text
        for mode in ("dry_run", "stop_before_payment"):
            ok = client.post(url, headers=auth, json={"exec_mode": mode})
            assert ok.status_code == 201, ok.text
        with psycopg.connect(migrated_db) as conn:
            modes = [
                r[0]
                for r in conn.execute(
                    "SELECT exec_mode FROM app.plugin_instance WHERE buyer_account_id = %s",
                    (account,),
                ).fetchall()
            ]
        assert "live" not in modes, "签发路径落库了 live 实例"

    def test_exec_mode_patch_and_revoke(
        self, client: TestClient, migrated_db: str, seeded: dict
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            _reset(conn, seeded)
            account = _mk_account(conn, seeded)
        instance = client.post(
            f"/api/v1/buyer-accounts/{account}/plugin-instances", headers=auth, json={}
        ).json()["id"]

        r = client.patch(
            f"/api/v1/plugin-instances/{instance}", headers=auth, json={"exec_mode": "live"}
        )
        assert r.status_code == 200 and r.json()["exec_mode"] == "live"
        assert (
            client.patch(
                f"/api/v1/plugin-instances/{instance}", headers=auth, json={"exec_mode": "nope"}
            ).status_code
            == 422
        )

        assert (
            client.post(f"/api/v1/plugin-instances/{instance}/revoke", headers=auth).status_code
            == 200
        )
        # 幂等：再吊销一次仍 200
        assert (
            client.post(f"/api/v1/plugin-instances/{instance}/revoke", headers=auth).status_code
            == 200
        )
        r = client.patch(
            f"/api/v1/plugin-instances/{instance}", headers=auth, json={"exec_mode": "dry_run"}
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "PLUGIN_INSTANCE_REVOKED"
        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT status, revoked_at, exec_mode FROM app.plugin_instance WHERE id = %s",
                (instance,),
            ).fetchone()
        assert row[0] == "revoked" and row[1] is not None and row[2] == "live"

    def test_cross_team_account_is_invisible(
        self, client: TestClient, migrated_db: str, seeded: dict, team_ids: tuple[int, int]
    ) -> None:
        """RLS + 显式 team 谓词双保险：别的团队的账号一律 404（不是 403，不泄露存在性）。"""
        auth = _login(client, ADMIN, PASSWORD)
        tag = uuid.uuid4().hex[:6]
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            other = int(
                conn.execute(
                    "INSERT INTO app.buyer_account (team_id, label, site, external_customer_id)"
                    " VALUES (%s, %s, 'amazon_com', %s) RETURNING id",
                    (team_ids[0], f"他团队-{tag}", f"X{tag.upper()}"),
                ).fetchone()[0]
            )
        assert (
            client.get(f"/api/v1/buyer-accounts/{other}/plugin-instances", headers=auth).status_code
            == 404
        )
        assert (
            client.patch(
                f"/api/v1/buyer-accounts/{other}", headers=auth, json={"note": "越权"}
            ).status_code
            == 404
        )
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute("DELETE FROM app.buyer_account WHERE id = %s", (other,))


# ── 配置中心：日界与批量上限不写死 ──


class TestDispatchConfig:
    async def test_defaults_and_team_override(self, migrated_db: str, seeded: dict) -> None:
        async def read() -> tuple[str, int]:
            async with ctx_tx(get_session_factory(), team_id=seeded["team"]) as s:
                return await dispatch.dispatch_config(s, seeded["team"])

        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM app.team_config WHERE team_id = %s AND key = %s",
                (seeded["team"], dispatch.PLUGIN_DISPATCH_CONFIG_KEY),
            )
        assert await read() == (dispatch.DEFAULT_DAY_BOUNDARY_TZ, dispatch.DEFAULT_PULL_BATCH_MAX)

        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.team_config (team_id, key, value) VALUES (%s, %s, %s::jsonb)",
                (
                    seeded["team"],
                    dispatch.PLUGIN_DISPATCH_CONFIG_KEY,
                    '{"day_boundary_tz": "America/Los_Angeles", "pull_batch_max": 999}',
                ),
            )
        tz, batch = await read()
        assert tz == "America/Los_Angeles"
        assert batch == dispatch.PULL_BATCH_HARD_MAX, "配置写多大都不许越过硬上限"
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM app.team_config WHERE team_id = %s AND key = %s",
                (seeded["team"], dispatch.PLUGIN_DISPATCH_CONFIG_KEY),
            )

    def test_bad_timezone_degrades_not_breaks(self) -> None:
        """坏时区名只降级回 UTC + 告警，**不抛**——一个配错的值不该让整条拉取链 500。"""
        assert dispatch.day_start("Mars/Olympus").utcoffset().total_seconds() == 0  # type: ignore[union-attr]
        assert dispatch.day_start("UTC").hour == 0
