"""R2-15 验收：SKU 内外分离（D-Q72）——七条判据逐条测试化。

| 判据 | 用例 |
|---|---|
| ① channel_sku = ASIN | `TestChannelSku::test_new_listing_channel_sku_is_asin` |
| ② 同店重上取 -2/-3 且不撞唯一约束 | `TestChannelSku::test_suffix_scans_all_statuses` |
| ③ 跨店同 ASIN 互不冲突 | `TestChannelSku::test_cross_store_same_asin` |
| ④ master_sku 超 999,999,999 自动变长不截断 | `TestMasterSku::test_no_truncate_at_large_sequence` |
| ⑤ 既有号不动 + 新旧号永不相等 | `TestMasterSku::test_existing_untouched_and_never_collides` |
| ⑥ 订单回连在两种编码下均可用 | `TestOrderReconnect::test_reconnect_under_both_encodings` |
| ⑦ 回连失败不得静默 | `TestOrderReconnect::test_unlinked_line_is_signalled` |

另加一条判据外但必需的：**非 amazon 货源回落 master_sku**（D-Q72 只规定 amazon
取 ASIN，其余「另给规则」；规则落地前的回落行为必须被测试钉住，否则第二个货源
接进来时才发现是静默错误）。
"""

import time

import httpx
import psycopg
import pytest

from erp.channel.gateway import gateway
from erp.core.db import ctx_tx, get_session_factory
from erp.core.settings import get_settings
from erp.listing import service as listing_service
from erp.order import pull as order_pull

TEAM = "R2-15 SKU 分离测试团队"
ASIN_A = "B0R215AAA1"
ASIN_B = "B0R215BBB2"
ASIN_NON_AMZ = "1688-R215-XYZ"


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    """两个店（一个受去重、一个 dedup_exempt）+ 三个产品（amazon ×2、非 amazon ×1）。

    第二个店必须 `dedup_exempt`：团内去重按 **(product_id, team_id)** 判，不按店铺分维，
    故同团同产品上第二个店本会被 `LISTING_DUP_IN_TEAM` 挡掉——跨店同 ASIN 是
    **店铺豁免（D-Q31）下**才成立的场景。（本单踩点文里那张表把这一格写成
    「放行（店铺维度不同）」，是错的：去重 SQL 里没有 store_id 条件。）
    """
    key = get_settings().credential_key
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
                     (TEAM,))  # fmt: skip
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        conn.execute(
            "DELETE FROM app.store_credential WHERE store_id IN"
            " (SELECT id FROM app.store WHERE team_id = %s)",
            (team_id,),
        )
        for t in ("order_check", "order_line", "channel_order", "listing_state_history",
                  "listing", "product", "store"):  # fmt: skip
            conn.execute(f"DELETE FROM app.{t} WHERE team_id = %s", (team_id,))
        store_id = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, 'R215S1', 'SKU 分离测试店1', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
        store2_id = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test, dedup_exempt)"
            " VALUES (%s, %s, 'R215S2', 'SKU 分离测试店2（豁免去重）', true, true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO app.store_credential (store_id, client_id, client_secret_encrypted)"
            " VALUES (%s, 'r215-client', pgp_sym_encrypt('r215-secret', %s))",
            (store_id, key),
        )
        pids = {}
        for chan, ref in (("amazon", ASIN_A), ("amazon", ASIN_B), ("1688", ASIN_NON_AMZ)):
            pids[ref] = conn.execute(
                "INSERT INTO app.product (team_id, source_channel, source_ref, title, attrs,"
                " price_snapshot, status)"
                " VALUES (%s, %s, %s, %s,"
                '  \'{"wpt": "Drinkware", "bullets": ["solid"], "description": "nice"}\','
                "  '{\"list\": 19.99}', 'audit_passed') RETURNING id",
                (team_id, chan, ref, f"R2-15 测试品 {ref}"),
            ).fetchone()[0]
        conn.execute(
            "INSERT INTO app.system_config (key, value)"
            " VALUES ('channel.gateway_mode', '\"live_test\"'::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value"
        )
    return {"team": team_id, "store": store_id, "store2": store2_id, **pids}


async def _allocate(team_id: int, store_id: int, product_ids: list[int]) -> dict:
    """直调服务层（offer_mode=match 跳过品牌占用与 GTIN 占号，聚焦 SKU 取值逻辑）。"""
    async with ctx_tx(get_session_factory(), team_id=team_id) as s:
        return await listing_service.allocate(
            s,
            team_id=team_id,
            store_id=store_id,
            product_ids=product_ids,
            offer_mode="match",
            actor_id=None,
        )


def _channel_sku_in_db(conn: psycopg.Connection, listing_id: int) -> str:
    row = conn.execute(
        "SELECT channel_sku FROM app.listing WHERE id = %s", (listing_id,)
    ).fetchone()
    assert row is not None
    return str(row[0])


class TestChannelSku:
    async def test_new_listing_channel_sku_is_asin(self, seeded: dict, migrated_db: str) -> None:
        """判据①：新上架 channel_sku 等于 ASIN，且**库里存的与 API 回报的一致**。

        后半句是承重的：D-Q72 与工单都把实现锚点指成响应回显那一行，只改那里会造成
        「库存 master_sku、API 回报 ASIN」的静默分叉，且分叉方向恰好是「看起来成功了」。
        故这里不只断言回显，还回库对一次。
        """
        res = await _allocate(seeded["team"], seeded["store"], [seeded[ASIN_A]])
        assert res["rejected"] == []
        created = res["created"][0]
        assert created["channel_sku"] == ASIN_A
        with psycopg.connect(migrated_db) as conn:
            assert _channel_sku_in_db(conn, created["id"]) == ASIN_A

    async def test_non_amazon_source_falls_back_to_master_sku(
        self, seeded: dict, migrated_db: str
    ) -> None:
        """非 amazon 货源显式回落 master_sku（D-Q72 对其余货源写「另给规则」）。"""
        res = await _allocate(seeded["team"], seeded["store"], [seeded[ASIN_NON_AMZ]])
        assert res["rejected"] == []
        sku = res["created"][0]["channel_sku"]
        assert sku.startswith("M") and sku[1:].isdigit()
        with psycopg.connect(migrated_db) as conn:
            master = conn.execute(
                "SELECT master_sku FROM app.product WHERE id = %s", (seeded[ASIN_NON_AMZ],)
            ).fetchone()[0]
        assert sku == master

    async def test_suffix_scans_all_statuses(self, seeded: dict, migrated_db: str) -> None:
        """判据②：同店重复上架同 ASIN 取 -2/-3，且**下架/retired 的旧 listing 照样占号**。

        这是后缀规则的**主场景**而不是边缘情形：`uq_listing` 不带状态过滤，而团内
        去重只挡在架的——两者叠起来就是「下架后重上：去重放行、唯一约束照撞」。

        ⚠️ **本用例证明的是「结果」而不是「手段」**（PR #48 审查侧 N2 实测指出）：
        给 `taken` 预扫加上状态过滤（只扫在架），本用例**仍然全绿**——因为真正的裁决在
        `uq_listing` + 退格重试，预扫只用来定起点。**这不是缺陷，恰恰说明设计是稳的**
        （手段可替换而结果不变）；但用例名容易被读成「钉住了扫全状态这个手段」，
        故在此写明：它钉住的是「下架后重上能拿到 -2/-3」，预扫范围属可替换的实现细节。
        """
        first = await _allocate(seeded["team"], seeded["store"], [seeded[ASIN_B]])
        assert first["created"][0]["channel_sku"] == ASIN_B
        first_id = first["created"][0]["id"]

        # 下架（delisted 不在团内去重的拦截集里，但仍占着 uq_listing）
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute("UPDATE app.listing SET status='delisted' WHERE id=%s", (first_id,))
        second = await _allocate(seeded["team"], seeded["store"], [seeded[ASIN_B]])
        assert second["rejected"] == [], second["rejected"]
        assert second["created"][0]["channel_sku"] == f"{ASIN_B}-2"
        second_id = second["created"][0]["id"]

        # 再把第二条置 retired，第三次应拿 -3（证明扫的是全状态，不是只跳过 delisted）
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute("UPDATE app.listing SET status='retired' WHERE id=%s", (second_id,))
        third = await _allocate(seeded["team"], seeded["store"], [seeded[ASIN_B]])
        assert third["rejected"] == [], third["rejected"]
        assert third["created"][0]["channel_sku"] == f"{ASIN_B}-3"

        # 三条各自持有不同 channel_sku——唯一约束因此永不被违反，
        # 且订单按成交时的那个 SKU 精确命中一行（不存在「多条命中取最新」的消歧问题）。
        with psycopg.connect(migrated_db) as conn:
            skus = [
                r[0]
                for r in conn.execute(
                    "SELECT channel_sku FROM app.listing WHERE store_id=%s AND product_id=%s"
                    " ORDER BY id",
                    (seeded["store"], seeded[ASIN_B]),
                ).fetchall()
            ]
        assert skus == [ASIN_B, f"{ASIN_B}-2", f"{ASIN_B}-3"]
        assert len(set(skus)) == len(skus)

    async def test_cross_store_same_asin(self, seeded: dict, migrated_db: str) -> None:
        """判据③：跨店同 ASIN 各自上架互不冲突，**且第二个店不带后缀**。

        `uq_listing` 是 (store_id, channel_sku)，故换店即不撞——第二个店应拿到裸 ASIN，
        不该因为「别的店已经用了这个 SKU」而莫名退到 -2。
        """
        res = await _allocate(seeded["team"], seeded["store2"], [seeded[ASIN_A]])
        assert res["rejected"] == [], res["rejected"]
        assert res["created"][0]["channel_sku"] == ASIN_A
        with psycopg.connect(migrated_db) as conn:
            rows = conn.execute(
                "SELECT store_id, channel_sku FROM app.listing"
                " WHERE product_id = %s AND channel_sku = %s ORDER BY store_id",
                (seeded[ASIN_A], ASIN_A),
            ).fetchall()
        assert {r[0] for r in rows} == {seeded["store"], seeded["store2"]}


class TestMasterSku:
    def test_no_truncate_at_large_sequence(self, migrated_db: str) -> None:
        """判据④：序号越过 999,999,999 后**自动变长**，不截断、不产出 `#`。

        判据明写「不得只测小序号」，故这里直接把序列推到边界与边界之后各取一次。
        """
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            # 保存原值，测完还原——本函数与产品入库共用同一序列
            original = conn.execute("SELECT last_value FROM app.master_sku_seq").fetchone()[0]
            try:
                got: dict[int, str] = {}
                for start in (1, 999_999_998, 999_999_999, 99_999_999_999):
                    conn.execute("SELECT setval('app.master_sku_seq', %s)", (start,))
                    got[start + 1] = conn.execute("SELECT app.next_master_sku()").fetchone()[0]
            finally:
                conn.execute("SELECT setval('app.master_sku_seq', %s)", (original,))

        assert got[2] == "M000000002"  # 小序号：左填零至 9 位
        assert got[999_999_999] == "M999999999"  # 边界：正好 9 位，不填不截
        assert got[1_000_000_000] == "M1000000000"  # **越界即变长**（10 位）
        assert got[100_000_000_000] == "M100000000000"  # 再越一档仍变长（12 位）

        for value in got.values():
            assert "#" not in value, f"{value} 含 '#'：说明用了会溢出的格式模板"
            assert value.startswith("M") and value[1:].isdigit()
            assert len(value[1:]) >= 9

        # 号码严格随序号单增（证明 nextval 只被调用一次、没有跳号或取到不同值）
        assert [int(got[k][1:]) for k in sorted(got)] == sorted(k for k in got)

    def test_existing_untouched_and_never_collides(self, seeded: dict, migrated_db: str) -> None:
        """判据⑤：存量一行不改；且新式号**位数不同故永不等于**任何旧式号。

        旧式 `M` + 7 位，新式 `M` + ≥9 位。这条不靠推理，直接构造一个旧式号断言。

        **必须依赖 `seeded`**（PR #48 审查侧 N1）：本用例按 `TEAM` 名查 team，而那个 team
        是 `seeded` 建的。此前签名漏了它，于是**只在脏库上能单独跑通**——干净库上单跑
        会 `fetchone()` 返回 None 再取 `[0]`，报 `TypeError` 而不是「存量被改动了」。
        整文件跑时靠同文件先跑的用例留下的副作用蒙对，属隐藏依赖。
        本仓要求「每条判据能被单独复算」，故补上。
        """
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
            conn.execute(
                "DELETE FROM app.product WHERE team_id = %s AND source_ref = 'B0R215LEGACY'",
                (team_id,),
            )
            legacy = "M1000000"  # 旧式（M+7）——原 lpad(7) 在序号 1e6 与 1e7 都会产出它
            conn.execute(
                "INSERT INTO app.product (team_id, master_sku, source_channel, source_ref,"
                " title, attrs, price_snapshot, status)"
                " VALUES (%s, %s, 'amazon', 'B0R215LEGACY', '存量旧式号产品',"
                " '{}'::jsonb, '{}'::jsonb, 'ingested')",
                (team_id, legacy),
            )
            # 生成若干新号并逐一对比
            fresh = [conn.execute("SELECT app.next_master_sku()").fetchone()[0] for _ in range(5)]
            after = conn.execute(
                "SELECT master_sku FROM app.product WHERE team_id = %s"
                " AND source_ref = 'B0R215LEGACY'",
                (team_id,),
            ).fetchone()[0]

        assert after == legacy, "存量 master_sku 被改动了——Walmart SKU 不可改"
        assert all(f != legacy for f in fresh)
        # 更强的一条：位数不同 ⇒ 不可能相等，与具体取值无关
        assert all(len(f) != len(legacy) for f in fresh)


class _FakeDiag:
    def __init__(self, name: str) -> None:
        self.constraint_name = name


class _FakeOrig(Exception):
    """冒充 psycopg3 的原始异常：只需要 `.diag.constraint_name`。"""

    def __init__(self, name: str) -> None:
        super().__init__(f'duplicate key value violates unique constraint "{name}"')
        self.diag = _FakeDiag(name)


class TestUqListingViolationGuard:
    """`_is_uq_listing_violation` 自己的守卫（PR #48 审查侧 N2）。

    它是**专门写来防**「把 FK/CHECK 错误吞进后缀重试循环、连吞 50 次后报一个误导性的
    `LISTING_SKU_SUFFIX_EXHAUSTED`」的。审查侧变异实测：把它改成恒真，九条判据**照样
    全绿**——即这个守卫自己没有被任何测试守住。这正是本仓反复出现的那一类形状
    （守卫补上了，守卫的守卫缺席），故补此用例。

    纯单元断言，不需要真造一个 FK 冲突：真正要钉住的是「按约束名分流」这个判断本身。
    """

    def test_only_uq_listing_is_treated_as_retryable(self) -> None:
        from sqlalchemy.exc import IntegrityError

        from erp.listing.service import _is_uq_listing_violation

        def _err(orig: Exception) -> IntegrityError:
            return IntegrityError("INSERT ...", {}, orig)

        # 带 diag 的正常路径（psycopg3）
        assert _is_uq_listing_violation(_err(_FakeOrig("uq_listing"))) is True
        # **其余约束一律不可重试，必须原样抛出**——这是本守卫存在的全部理由
        assert _is_uq_listing_violation(_err(_FakeOrig("fk_listing_product"))) is False
        assert _is_uq_listing_violation(_err(_FakeOrig("ck_listing_status"))) is False
        assert _is_uq_listing_violation(_err(_FakeOrig("uq_listing_spec"))) is False

    def test_falls_back_to_message_when_driver_gives_no_diag(self) -> None:
        """驱动未给出 `diag` 时走字符串兜底——兜底也必须只认 uq_listing。"""
        from sqlalchemy.exc import IntegrityError

        from erp.listing.service import _is_uq_listing_violation

        def _err(msg: str) -> IntegrityError:
            return IntegrityError("INSERT ...", {}, Exception(msg))

        assert _is_uq_listing_violation(_err('... unique constraint "uq_listing"')) is True
        assert _is_uq_listing_violation(_err('... foreign key constraint "fk_x"')) is False
        # uq_listing 是 uq_listing_spec 的前缀——兜底若用裸子串匹配会误判（审查 N4）
        assert _is_uq_listing_violation(_err('... unique constraint "uq_listing_spec"')) is False


class _FakeOrders:
    """最小 orders 替身：一页返回给定订单。"""

    def __init__(self) -> None:
        self.page: dict = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200, json={"access_token": "t", "token_type": "Bearer", "expires_in": 900}
            )
        return httpx.Response(200, json=self.page)


def _order(po: str, skus: list[str], *, order_ms: int) -> dict:
    return {
        "purchaseOrderId": po,
        "customerOrderId": f"C{po}",
        "customerEmailId": "x@example.com",
        "orderDate": order_ms,
        "shippingInfo": {"phone": "1", "postalAddress": {"name": "T"}},
        "orderLines": {
            "orderLine": [
                {
                    "lineNumber": str(i + 1),
                    "item": {"sku": sku, "productName": f"P{i}"},
                    "orderLineQuantity": {"unitOfMeasurement": "EACH", "amount": "1"},
                    "charges": {
                        "charge": [
                            {
                                "chargeType": "PRODUCT",
                                "chargeAmount": {"currency": "USD", "amount": 10.0},
                            }
                        ]
                    },
                    "orderLineStatuses": {
                        "orderLineStatus": [
                            {
                                "status": "Created",
                                "statusQuantity": {"unitOfMeasurement": "EACH", "amount": "1"},
                            }
                        ]
                    },
                }
                for i, sku in enumerate(skus)
            ]
        },
    }


def _page(orders: list[dict]) -> dict:
    return {
        "list": {
            "meta": {"totalCount": len(orders), "limit": 200, "nextCursor": None},
            "elements": {"order": orders},
        }
    }


@pytest.fixture()
def fake_orders(seeded: dict):  # type: ignore[no-untyped-def]
    f = _FakeOrders()
    gateway._clients.clear()
    gateway._transport_factory = lambda proxy: httpx.MockTransport(f.handler)
    yield f
    gateway._clients.clear()
    gateway._transport_factory = gateway._default_transport


class TestOrderReconnect:
    """判据⑥⑦：订单回连按**现役实现**验——`(store_id, channel_sku)` 直查 listing。

    图纸表 `sku_mapping` 全仓零建表（001/03-catalog.md 该节已标注为图纸态），
    本单不要求建；回连的真实路径就是 `order/pull.py` 的那两个子查询。
    """

    async def test_reconnect_under_both_encodings(
        self, seeded: dict, migrated_db: str, fake_orders: _FakeOrders
    ) -> None:
        """两种编码各拉一单：M 号（改造前）与 ASIN（改造后）都要接上非空 listing/product。"""
        legacy_sku = "M0000777"  # 改造前编码的存量 listing
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM app.listing WHERE store_id = %s AND channel_sku = %s",
                (seeded["store"], legacy_sku),
            )
            conn.execute(
                "INSERT INTO app.listing (team_id, store_id, product_id, offer_mode,"
                " channel_sku, status) VALUES (%s, %s, %s, 'match', %s, 'live')",
                (seeded["team"], seeded["store"], seeded[ASIN_A], legacy_sku),
            )

        po = f"PO-R215-{int(time.time())}"
        fake_orders.page = _page(
            [_order(po, [ASIN_A, legacy_sku], order_ms=int(time.time() * 1000))]
        )
        stats = await order_pull.pull_store_orders(
            get_session_factory(), seeded["store"], config={}
        )
        assert stats["orders"] == 1 and stats["lines"] == 2
        assert stats["unlinked_lines"] == 0, "两种编码都该接上，却有行没接上"

        with psycopg.connect(migrated_db) as conn:
            rows = conn.execute(
                "SELECT ol.channel_sku, ol.listing_id, ol.product_id"
                " FROM app.order_line ol JOIN app.channel_order co ON co.id = ol.order_id"
                " WHERE co.channel_order_no = %s ORDER BY ol.channel_line_no",
                (po,),
            ).fetchall()
        assert len(rows) == 2
        for sku, listing_id, product_id in rows:
            assert listing_id is not None, f"{sku} 未接上 listing_id"
            assert product_id == seeded[ASIN_A], f"{sku} 接错了 product_id"

    async def test_unlinked_line_is_signalled(
        self, seeded: dict, migrated_db: str, fake_orders: _FakeOrders
    ) -> None:
        """判据⑦：构造一个查不到 listing 的订单行——**必须有显式信号，不得静默**。

        原实现是完全静默的：子查询落空返回 NULL 而 INSERT 仍成功，订单就此失去
        产品关联，下游只有四检 `price_limit` 以 `source_missing` 间接暴露。
        """
        ghost = "B0GHOST-NO-LISTING"
        po = f"PO-R215-GHOST-{int(time.time())}"
        fake_orders.page = _page([_order(po, [ghost], order_ms=int(time.time() * 1000))])
        stats = await order_pull.pull_store_orders(
            get_session_factory(), seeded["store"], config={}
        )
        # 信号一：计数（落进 sync_state.stats，不只是一条会滚走的日志）
        assert stats["unlinked_lines"] == 1

        with psycopg.connect(migrated_db) as conn:
            listing_id = conn.execute(
                "SELECT ol.listing_id FROM app.order_line ol"
                " JOIN app.channel_order co ON co.id = ol.order_id"
                " WHERE co.channel_order_no = %s",
                (po,),
            ).fetchone()[0]
            # 信号二：一条 warn 级通知（运营看得见）
            note = conn.execute(
                "SELECT severity, title FROM app.notification"
                " WHERE team_id = %s AND dedupe_key LIKE 'order_line_unlinked:%%'"
                " ORDER BY id DESC LIMIT 1",
                (seeded["team"],),
            ).fetchone()
        assert listing_id is None, "本用例的前提是这一行确实接不上"
        assert note is not None, "回连失败没有产生任何通知——即仍是静默失败"
        assert note[0] == "warn"

    async def test_repull_heals_link_once_listing_exists(
        self, seeded: dict, migrated_db: str, fake_orders: _FakeOrders
    ) -> None:
        """补回连自愈：先接不上 → 补建 listing → 重拉即补上，且**不覆盖已接好的链接**。

        原实现里 `listing_id`/`product_id` 不在 ON CONFLICT 的 SET 列表里，故首次没接上
        的订单行即便 listing 后来建好也永远补不上——那会让判据⑦ 的告警变成
        「只能人工改库」的死信。
        """
        late_sku = "B0R215LATE1"
        po = f"PO-R215-LATE-{int(time.time())}"
        order_ms = int(time.time() * 1000)
        fake_orders.page = _page([_order(po, [late_sku], order_ms=order_ms)])
        first = await order_pull.pull_store_orders(
            get_session_factory(), seeded["store"], config={}
        )
        assert first["unlinked_lines"] == 1

        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.listing (team_id, store_id, product_id, offer_mode,"
                " channel_sku, status) VALUES (%s, %s, %s, 'match', %s, 'live')",
                (seeded["team"], seeded["store"], seeded[ASIN_B], late_sku),
            )
        fake_orders.page = _page([_order(po, [late_sku], order_ms=order_ms)])
        second = await order_pull.pull_store_orders(
            get_session_factory(), seeded["store"], config={}
        )
        assert second["unlinked_lines"] == 0, "listing 已补建，重拉却仍未接上"

        with psycopg.connect(migrated_db) as conn:
            listing_id, product_id = conn.execute(
                "SELECT ol.listing_id, ol.product_id FROM app.order_line ol"
                " JOIN app.channel_order co ON co.id = ol.order_id"
                " WHERE co.channel_order_no = %s",
                (po,),
            ).fetchone()
        assert listing_id is not None and product_id == seeded[ASIN_B]
