"""R2-14 生命周期出口验收（14c 列表折叠 / 14a 产品删除）。

口径：`specs/001-domain-model/00-conventions.md §7.1`（三级硬删除规则 + 墓碑表 +
列表默认折叠），分片见 `specs/007-mvp-completion-plan/README.md` R2-14。

本文件对应验收⑥（列表默认不显示已停用项且开关可切）。14a 的判据随后续增量补入同文件
——**折叠与删除是同一个「出口」的两半**，判据放一起才看得出「删掉的确实从列表消失了」。

夹具复用 `test_audit_api.py`（团队/管理员/造产品），范式同 `test_audit_batch.py`：
另造一套夹具等于各自维护一份「什么算一个产品」，漂了没人发现。
"""

import re
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.catalog.delete import _CHANNEL_ALIVE, _DELETABLE

from .test_audit_api import (  # noqa: F401  夹具跨文件复用（pyproject 已为 tests/* 豁免 F811）
    ADMIN,
    _mk_product,
    client,
    seeded,
)
from .test_identity_api import PASSWORD, _login

LIST_URL = "/api/v1/products"

# 本模块自造数据的命名前缀——清场按它圈定，不误伤别的模块。
_ASIN_PREFIX = "B0R214"
_SKU_PREFIX = "R214-SKU-"
_TEST_GTINS = ("210000000001", "210000000002")


@pytest.fixture(scope="module", autouse=True)
def _purge_residue(migrated_db: str) -> None:
    """开跑前清掉本模块上一轮的残留。

    **必须先于 `seeded` 跑**（autouse 模块级夹具的既定次序）：`seeded` 里有
    `DELETE FROM app.product WHERE team_id = ...`，而本模块会给产品挂 listing——
    上一轮中途失败留下的 listing 会让那句 DELETE 撞外键，于是**整个模块在 setup
    阶段全红，报的还是一个与被测逻辑毫无关系的外键错**，排查方向全被带偏。
    """
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "DELETE FROM app.maintenance_task WHERE listing_id IN"
            " (SELECT id FROM app.listing WHERE channel_sku LIKE %s)",
            (f"{_SKU_PREFIX}%",),
        )
        conn.execute("DELETE FROM app.gtin_pool WHERE gtin = ANY(%s)", (list(_TEST_GTINS),))
        conn.execute(
            "DELETE FROM app.listing_spec WHERE product_id IN"
            " (SELECT id FROM app.product WHERE source_ref LIKE %s)",
            (f"{_ASIN_PREFIX}%",),
        )
        conn.execute("DELETE FROM app.listing WHERE channel_sku LIKE %s", (f"{_SKU_PREFIX}%",))
        conn.execute(
            "DELETE FROM app.deleted_product WHERE source_ref LIKE %s", (f"{_ASIN_PREFIX}%",)
        )


def _set_status(migrated_db: str, pid: int, status: str) -> None:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("UPDATE app.product SET status = %s WHERE id = %s", (status, pid))


def _list(client: TestClient, auth: dict[str, str], **params: Any) -> dict[str, Any]:
    r = client.get(LIST_URL, headers=auth, params={"size": 100, **params})
    assert r.status_code == 200, r.text
    return dict(r.json())


def _ids(body: dict[str, Any]) -> set[int]:
    return {int(it["id"]) for it in body["items"]}


class TestRetiredFold:
    """验收⑥：默认折叠 `retired`，开关可切，显式状态筛选优先。"""

    def test_default_hides_retired(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        live = _mk_product(migrated_db, seeded["team"], asin="B0R214F001")
        gone = _mk_product(migrated_db, seeded["team"], asin="B0R214F002")
        _set_status(migrated_db, gone, "retired")
        auth = _login(client, ADMIN, PASSWORD)

        body = _list(client, auth)
        ids = _ids(body)
        assert live in ids
        assert gone not in ids
        # **total 必须跟着折叠走**：若只过滤了行而 total 仍按全量算，分页会出现
        # 「共 N 条」却翻不到的空页——用户看到的是「系统丢了商品」。
        assert body["total"] == len(ids)

    def test_switch_reveals_retired(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        live = _mk_product(migrated_db, seeded["team"], asin="B0R214F003")
        gone = _mk_product(migrated_db, seeded["team"], asin="B0R214F004")
        _set_status(migrated_db, gone, "retired")
        auth = _login(client, ADMIN, PASSWORD)

        ids = _ids(_list(client, auth, include_retired="true"))
        assert {live, gone} <= ids

    def test_explicit_status_retired_wins_over_fold(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """**这条是本增量最容易写错的方向**：默认折叠若与 status 筛选叠成 AND，
        运营在下拉里选「已下架」会得到空列表——一个明确要求看某状态、系统却按默认
        把它过滤掉的行为，在用户眼里就是功能坏了。故两者互斥，不是两个 AND 条件。
        """
        gone = _mk_product(migrated_db, seeded["team"], asin="B0R214F005")
        _set_status(migrated_db, gone, "retired")
        auth = _login(client, ADMIN, PASSWORD)

        body = _list(client, auth, status="retired")  # 不传 include_retired
        assert gone in _ids(body)
        assert {it["status"] for it in body["items"]} == {"retired"}

    def test_explicit_status_not_widened_by_switch(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """反方向同样要钉住：开关打开也不能把 `retired` 掺进按状态筛选的结果里。"""
        live = _mk_product(migrated_db, seeded["team"], asin="B0R214F006")
        gone = _mk_product(migrated_db, seeded["team"], asin="B0R214F007")
        _set_status(migrated_db, gone, "retired")
        auth = _login(client, ADMIN, PASSWORD)

        body = _list(client, auth, status="ingested", include_retired="true")
        ids = _ids(body)
        assert live in ids
        assert gone not in ids


def test_listing_status_classification_is_exhaustive(migrated_db: str) -> None:
    """**反向不变量**：`delete.py` 的两张 listing 状态表并集必须 == 0009 的 CHECK 全集。

    删除路径按状态决定「能不能删」。将来有人给 `ck_listing_status` 加一个新状态而忘了
    在这里分类，本测试当场判红——否则这个新状态会走进 `PRODUCT_DELETE_LISTING_UNCLASSIFIED`
    分支，运行期变成「某些产品莫名其妙删不掉」，而没人知道为什么。

    运行期那条 fail-closed 守卫与本测试是配套的两层：**测试保证开发期发现，守卫保证
    万一没发现也不会误删**。少任何一层都不够——只有守卫，问题会拖到运营报障；
    只有测试，改约束的人绕过测试就直接放行了。
    """
    with psycopg.connect(migrated_db) as conn:
        src = conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'ck_listing_status'"
        ).fetchone()[0]
    declared = set(re.findall(r"'([a-z_]+)'::text", src))
    assert declared, f"未能从约束定义解析出状态集合：{src}"
    assert _CHANNEL_ALIVE | _DELETABLE == declared, (
        f"delete.py 的状态分类与 ck_listing_status 不一致："
        f"约束多出 {sorted(declared - (_CHANNEL_ALIVE | _DELETABLE))}，"
        f"代码多出 {sorted((_CHANNEL_ALIVE | _DELETABLE) - declared)}"
    )
    assert not (_CHANNEL_ALIVE & _DELETABLE), "同一状态不能既算存活又算可删"


# ══════════════════════════════════ 14a 产品删除 ══════════════════════════════════

DEL_URL = "/api/v1/products/{}"

# ③级「绝不删」清单（§7.1）。删除路径对这些表**一个 DELETE 都不发**，
# 判据的做法是**显式断言行数不变**，而不是「我们没写删它的代码所以应该没事」——
# 后者是一句关于意图的话，前者是一条关于事实的断言。
_NEVER_DELETED = (
    # 审计三族（D-Q16 append-only）
    "app.audit_log",
    "app.audit_run",
    "app.audit_hit",
    # 订单（D-Q18 永久保留）
    "app.channel_order",
    "app.order_line",
    "app.order_check",
    # 售后
    "app.channel_return",
    "app.channel_return_line",
    "app.channel_return_event",
    "app.refund_request",
    # 财务（§08 追加式）——**R2-08 尚未开工，这两张现在还不存在**。
    # 保留在清单里而不是删掉：建成的那天判据自动覆盖它们，不必有人记得回来补。
    "app.financial_event",
    "app.ledger_entry",
)

# 允许缺席的表（仅未开工的域）。**任何其它名字缺席都判红**——过滤式断言最危险的
# 失败模式是「静默地什么都没测」：表名写错一个字母，count 就跳过它，判据全绿而
# 那张表根本没被看过一眼。
_NOT_YET_BUILT = frozenset({"app.financial_event", "app.ledger_entry"})


def _existing_never_deleted(migrated_db: str) -> tuple[str, ...]:
    with psycopg.connect(migrated_db) as conn:
        rows = conn.execute(
            "SELECT t FROM unnest(%s::text[]) t WHERE to_regclass(t) IS NOT NULL",
            (list(_NEVER_DELETED),),
        ).fetchall()
    return tuple(r[0] for r in rows)


def test_never_deleted_census_is_not_silently_empty(migrated_db: str) -> None:
    """③级清单里除「未开工的域」外，每张表都必须真实存在——否则判据在空跑。"""
    missing = set(_NEVER_DELETED) - set(_existing_never_deleted(migrated_db))
    assert missing <= _NOT_YET_BUILT, (
        f"③级『绝不删』清单里的表不存在：{sorted(missing)}。"
        "要么表名写错（判据会静默跳过它），要么表被改名/删除（那本身就是事故）。"
    )


def _counts(migrated_db: str) -> dict[str, int]:
    tables = _existing_never_deleted(migrated_db)
    with psycopg.connect(migrated_db) as conn:
        return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}


def _assert_never_deleted(
    before: dict[str, int], after: dict[str, int], *, audit_log_delta: int
) -> None:
    """③级不变量：**只追加，绝不减少**。

    注意这不是「行数不变」——`audit_log` 在一次成功删除后必然 **+1**（删除留痕本身
    就写进它）。把它写成「不变」曾让本判据判红，而那是判据错了不是代码错了：
    §7.1 对这三族的约束是 append-only，方向是「不许少」，不是「不许多」。

    `audit_log_delta` 精确到条：写 0 条（该删除被拒）或多于 1 条都判红——
    多写说明有别的路径顺手记了账，那属于没预期到的副作用。
    """
    for table, n_before in before.items():
        n_after = after[table]
        if table == "app.audit_log":
            assert n_after == n_before + audit_log_delta, (
                f"audit_log 增量不符：期望 +{audit_log_delta}，实际 {n_after - n_before}"
            )
        else:
            assert n_after == n_before, (
                f"{table} 行数变了（{n_before} → {n_after}）——③级『绝不删』被击穿"
            )


def _row_exists(migrated_db: str, table: str, where: str, params: tuple) -> bool:
    with psycopg.connect(migrated_db) as conn:
        return conn.execute(f"SELECT 1 FROM {table} WHERE {where}", params).fetchone() is not None


def _tombstones(migrated_db: str, team: int) -> list[dict[str, Any]]:
    with psycopg.connect(migrated_db) as conn:
        rows = conn.execute(
            "SELECT source_channel, source_ref, product_id, master_sku, reason, deleted_by"
            " FROM app.deleted_product WHERE team_id = %s ORDER BY id",
            (team,),
        ).fetchall()
    cols = ("source_channel", "source_ref", "product_id", "master_sku", "reason", "deleted_by")
    return [dict(zip(cols, r, strict=True)) for r in rows]


def _mk_listing(migrated_db: str, team: int, product_id: int, status: str, sku: str) -> int:
    """造一条 listing（store 取该团队任意一家，没有就建一家）。"""
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        store = conn.execute(
            "SELECT id FROM app.store WHERE team_id = %s LIMIT 1", (team,)
        ).fetchone()
        if store is None:
            store = conn.execute(
                "INSERT INTO app.store (team_id, channel_id, code, name)"
                " SELECT %s, c.id, 'R214TEST', 'R2-14 判据店' FROM app.channel c"
                " ORDER BY c.id LIMIT 1 RETURNING id",
                (team,),
            ).fetchone()
        return conn.execute(
            "INSERT INTO app.listing (team_id, store_id, product_id, offer_mode,"
            " channel_sku, status) VALUES (%s, %s, %s, 'build', %s, %s) RETURNING id",
            (team, store[0], product_id, sku, status),
        ).fetchone()[0]


@pytest.fixture()
def other_team(migrated_db: str) -> dict[str, int]:
    """他团队 + 一件他团队的产品（越权判据用）。"""
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES ('R2-14 他团队') ON CONFLICT (name) DO NOTHING"
        )
        tid = conn.execute("SELECT id FROM app.team WHERE name = 'R2-14 他团队'").fetchone()[0]
        pid = conn.execute(
            "INSERT INTO app.product (team_id, source_channel, source_ref, title, attrs)"
            " VALUES (%s, 'amazon', 'B0R214OTHER', 'Other Team Mug', '{}'::jsonb)"
            " ON CONFLICT (team_id, source_channel, source_ref) DO UPDATE"
            "   SET status = 'ingested' RETURNING id",
            (tid,),
        ).fetchone()[0]
    return {"team": tid, "product": pid}


class TestDeleteLevels:
    def test_level1_no_history_leaves_no_tombstone(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """验收①：删从未上架的产品 → 物理行消失、**无墓碑**、列表不再出现。"""
        pid = _mk_product(migrated_db, seeded["team"], asin="B0R214D001")
        auth = _login(client, ADMIN, PASSWORD)
        before = _counts(migrated_db)

        r = client.delete(DEL_URL.format(pid), headers=auth, params={"reason": "判据①"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["level"] == "no_history"
        assert body["tombstoned"] is False
        assert body["listings_deleted"] == 0

        assert not _row_exists(migrated_db, "app.product", "id = %s", (pid,))
        assert [t for t in _tombstones(migrated_db, seeded["team"]) if t["product_id"] == pid] == []
        assert pid not in _ids(_list(client, auth, include_retired="true"))
        _assert_never_deleted(before, _counts(migrated_db), audit_log_delta=1)

    def test_level2_with_history_writes_tombstone_and_blocks_reingest(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """**验收②，本单唯一不可省的判据**：删上过架的产品 → 实体消失、墓碑一行，
        且随后用同 `(team_id, source_channel, source_ref)` 重新采集 → **不入库**。

        重采这一步直接调 `product_upsert`（入库点本身），不经采集作业——作业侧另有
        预筛，若从作业入口验，绿灯可能来自预筛而**根本没验到承重的那一层**。
        """
        import asyncio

        from erp.core.db import get_session_factory
        from erp.scrape import service as scrape_service

        asin = "B0R214D002"
        pid = _mk_product(migrated_db, seeded["team"], asin=asin)
        _mk_listing(migrated_db, seeded["team"], pid, "delisted", "R214-SKU-002")
        auth = _login(client, ADMIN, PASSWORD)
        before = _counts(migrated_db)

        r = client.delete(DEL_URL.format(pid), headers=auth, params={"reason": "判据②"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["level"] == "with_history"
        assert body["tombstoned"] is True
        assert body["listings_deleted"] == 1

        assert not _row_exists(migrated_db, "app.product", "id = %s", (pid,))
        assert not _row_exists(migrated_db, "app.listing", "product_id = %s", (pid,))
        tomb = [t for t in _tombstones(migrated_db, seeded["team"]) if t["source_ref"] == asin]
        assert len(tomb) == 1
        assert tomb[0]["product_id"] == pid
        assert tomb[0]["reason"] == "判据②"
        _assert_never_deleted(before, _counts(migrated_db), audit_log_delta=1)

        async def _reingest() -> dict[str, Any]:
            async with get_session_factory()() as s, s.begin():
                await s.execute(
                    __import__("sqlalchemy").text(
                        "SELECT set_config('app.current_team', :t, true)"
                    ),
                    {"t": str(seeded["team"])},
                )
                return await scrape_service.product_upsert(
                    s,
                    team_id=seeded["team"],
                    source="amazon",
                    source_ref=asin,
                    payload={"title": "Plain Ceramic Mug 12oz"},
                )

        result = asyncio.run(_reingest())
        assert result == {
            "skipped": True,
            "reason": "deleted_tombstone",
            "source_ref": asin,
        }, "墓碑没挡住重采——删掉的商品又回流了，这正是 §7.1 说的硬删除唯一技术陷阱"
        assert not _row_exists(
            migrated_db, "app.product", "team_id = %s AND source_ref = %s", (seeded["team"], asin)
        )

    def test_live_listing_blocks_delete(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """在架/在途一律 409——**删了本地行，渠道上那条商品就再也下不掉架**。"""
        pid = _mk_product(migrated_db, seeded["team"], asin="B0R214D003")
        lid = _mk_listing(migrated_db, seeded["team"], pid, "live", "R214-SKU-003")
        auth = _login(client, ADMIN, PASSWORD)

        r = client.delete(DEL_URL.format(pid), headers=auth, params={"reason": "应被拒"})
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "PRODUCT_DELETE_LISTING_ACTIVE"
        # 拒绝必须是整体回滚：产品与 listing 都还在。
        assert _row_exists(migrated_db, "app.product", "id = %s", (pid,))
        assert _row_exists(migrated_db, "app.listing", "id = %s", (lid,))

    def test_running_maintenance_task_blocks_delete(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """维护任务执行中 → 409。删掉它脚下的 listing 会让 runner 跑向不存在的行。"""
        pid = _mk_product(migrated_db, seeded["team"], asin="B0R214D004")
        lid = _mk_listing(migrated_db, seeded["team"], pid, "delisted", "R214-SKU-004")
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            store = conn.execute(
                "SELECT store_id FROM app.listing WHERE id = %s", (lid,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO app.maintenance_task (team_id, store_id, listing_id, task_kind,"
                " status, scheduled_at) VALUES (%s, %s, %s, 'price_sync', 'running', now())",
                (seeded["team"], store, lid),
            )
        auth = _login(client, ADMIN, PASSWORD)

        r = client.delete(DEL_URL.format(pid), headers=auth, params={"reason": "应被拒"})
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "PRODUCT_DELETE_TASK_RUNNING"
        assert _row_exists(migrated_db, "app.product", "id = %s", (pid,))

    def test_scheduled_task_skipped_and_held_gtin_returned(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """待办维护任务作废；**held GTIN 归还池，used 不动**。

        不归还 held 就是 GTIN 池静默泄漏——池空了才会发现，那时已查不出是谁漏的。
        used 反过来必须**不**归还：它已发到渠道，回收会造成渠道侧号码复用。
        """
        pid = _mk_product(migrated_db, seeded["team"], asin="B0R214D005")
        lid = _mk_listing(migrated_db, seeded["team"], pid, "failed", "R214-SKU-005")
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            store = conn.execute(
                "SELECT store_id FROM app.listing WHERE id = %s", (lid,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO app.maintenance_task (team_id, store_id, listing_id, task_kind,"
                " status, scheduled_at) VALUES (%s, %s, %s, 'price_sync', 'scheduled', now())",
                (seeded["team"], store, lid),
            )
            conn.execute(
                "INSERT INTO app.gtin_pool (team_id, gtin, gtin_kind, status, source,"
                " held_listing_id, held_at) VALUES"
                " (%s, '210000000001', 'upc_a', 'held', 'generator_import', %s, now()),"
                " (%s, '210000000002', 'upc_a', 'used', 'generator_import', %s, now())",
                (seeded["team"], lid, seeded["team"], lid),
            )
        auth = _login(client, ADMIN, PASSWORD)

        r = client.delete(DEL_URL.format(pid), headers=auth, params={"reason": "判据 GTIN"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["gtins_released"] == 1
        assert body["maintenance_tasks_skipped"] == 1

        with psycopg.connect(migrated_db) as conn:
            held = conn.execute(
                "SELECT status, held_listing_id FROM app.gtin_pool WHERE gtin = '210000000001'"
            ).fetchone()
            used = conn.execute(
                "SELECT status, used_listing_id FROM app.gtin_pool WHERE gtin = '210000000002'"
            ).fetchone()
            task = conn.execute(
                "SELECT status FROM app.maintenance_task WHERE listing_id = %s", (lid,)
            ).fetchone()
        assert held == ("free", None), "held GTIN 没归还 → 池静默泄漏"
        assert used[0] == "used", "used GTIN 被回收 → 渠道侧号码会复用"
        assert task[0] == "skipped"


class TestTombstonePrefilterOnJobCreate:
    """采集作业侧的墓碑预筛（14a-3 的**优化层**，不是承重层）。

    承重层是 `product_upsert` 里的那一句（判据在 `test_level2_*`）。本类验的是
    「省掉白抓」与「运营当场看得见」，**故意与承重判据分开写**：两处若合成一条，
    某天预筛坏了而入库检查还在，判据照样绿——反过来更糟，入库检查坏了而预筛还在，
    绿灯会让人以为回流保护还在，而实际上只剩一层能被绕过的优化。
    """

    def _tombstone(self, migrated_db: str, team: int, asin: str) -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.deleted_product (team_id, source_channel, source_ref,"
                " product_id, master_sku, reason) VALUES (%s, 'amazon', %s, 0, 'M0000000', '判据')"
                " ON CONFLICT DO NOTHING",
                (team, asin),
            )

    def test_partially_blocked_job_shrinks_and_records_what_was_skipped(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        self._tombstone(migrated_db, seeded["team"], "B0R214J001")
        auth = _login(client, ADMIN, PASSWORD)

        r = client.post(
            "/api/v1/scrape-jobs",
            headers=auth,
            json={
                "source": "amazon",
                "job_kind": "product_detail",
                "input": {"targets": ["B0R214J001", "B0R214J002"]},
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["total_tasks"] == 1, "被墓碑挡下的目标仍然建了任务 → 白抓"

        with psycopg.connect(migrated_db) as conn:
            job_input, n_tasks = conn.execute(
                "SELECT j.input, (SELECT count(*) FROM app.scrape_task t WHERE t.job_id = j.id)"
                " FROM app.scrape_job j WHERE j.id = %s",
                (r.json()["id"],),
            ).fetchone()
        assert n_tasks == 1
        assert job_input["targets"] == ["B0R214J002"]
        # 留痕：作业里记着「谁被挡了」，否则运营只看到目标数对不上，无从解释。
        assert job_input["skipped_deleted"] == ["B0R214J001"]

    def test_all_blocked_raises_instead_of_creating_an_empty_job(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """全被挡 → 报错，**不建空作业**。

        0 任务的作业永远等不到 `_finish_job_if_complete`，会以 pending 僵在列表里，
        看起来像卡住的采集——而真相是没什么可采。宁可当场说清楚。
        """
        self._tombstone(migrated_db, seeded["team"], "B0R214J003")
        auth = _login(client, ADMIN, PASSWORD)
        before = _job_count(migrated_db, seeded["team"])

        r = client.post(
            "/api/v1/scrape-jobs",
            headers=auth,
            json={
                "source": "amazon",
                "job_kind": "product_detail",
                "input": {"targets": ["B0R214J003"]},
            },
        )
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "SCRAPE_ALL_TARGETS_DELETED"
        assert _job_count(migrated_db, seeded["team"]) == before, "报错了却还是建了作业"


def _job_count(migrated_db: str, team: int) -> int:
    with psycopg.connect(migrated_db) as conn:
        return conn.execute(
            "SELECT count(*) FROM app.scrape_job WHERE team_id = %s", (team,)
        ).fetchone()[0]


class TestDeleteTrailAndIsolation:
    def test_audit_log_records_who_what_and_a_lean_snapshot(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """验收④：删除动作在 `audit_log` 可查（谁/何时/删了什么/before 快照）。

        外加一条**反向**判据：快照**不含** attrs / images / price_snapshot。
        那三样正是 §7.1 说的「占体积的大头」，而 `audit_log` 永久保留——把整行塞进
        快照，删除就只是把字节从 product 搬进一张永不清理的表，**空间不但没省，还更糟**。
        """
        pid = _mk_product(
            migrated_db, seeded["team"], asin="B0R214D006", attrs={"model_number": "X-1"}
        )
        auth = _login(client, ADMIN, PASSWORD)
        r = client.delete(DEL_URL.format(pid), headers=auth, params={"reason": "留痕判据"})
        assert r.status_code == 200, r.text

        with psycopg.connect(migrated_db) as conn:
            row = conn.execute(
                "SELECT actor_type, actor_id, team_id, before, after FROM app.audit_log"
                " WHERE action = 'catalog.product_delete' AND object_id = %s",
                (str(pid),),
            ).fetchone()
        assert row is not None, (
            "删除没有留痕——先上线一个「谁都能删、删了查不到」的出口比没有出口更糟"
        )
        actor_type, actor_id, team_id, before, after = row
        assert actor_type == "user"
        assert actor_id == seeded["user"]
        assert team_id == seeded["team"]
        assert before["source_ref"] == "B0R214D006"
        assert before["master_sku"].startswith("M")
        assert after == {"reason": "留痕判据", "tombstoned": False}
        assert set(before) & {"attrs", "images", "price_snapshot"} == set(), (
            "before 快照塞进了大字段——删除变成把字节搬进永久保留的 audit_log，空间不减反增"
        )

    def test_cross_team_delete_is_404_not_403(
        self, client: TestClient, seeded: dict, other_team: dict, migrated_db: str
    ) -> None:
        """越权删 → **404 而非 403**，且与「纯粹不存在的 id」响应完全一致。

        403 等于确认「这个 id 存在，只是不属于你」——攻击者可用连号 id 扫出别人家的
        产品规模。同时断言他团队那行**一根毫毛都没动**。
        """
        auth = _login(client, ADMIN, PASSWORD)
        ghost = 9_999_999

        cross = client.delete(
            DEL_URL.format(other_team["product"]), headers=auth, params={"reason": "越权"}
        )
        fake = client.delete(DEL_URL.format(ghost), headers=auth, params={"reason": "越权"})
        assert cross.status_code == 404 and fake.status_code == 404
        # request_id 每请求不同，剥掉后比其余全部字段。
        cross_err = {k: v for k, v in cross.json()["error"].items() if k != "request_id"}
        fake_err = {k: v for k, v in fake.json()["error"].items() if k != "request_id"}
        assert cross_err == fake_err, "越权与不存在的响应可区分（可用于探测）"
        assert _row_exists(migrated_db, "app.product", "id = %s", (other_team["product"],))
        assert _tombstones(migrated_db, other_team["team"]) == []

    def test_reason_is_required(self, client: TestClient, seeded: dict, migrated_db: str) -> None:
        """`reason` 必填：不可逆操作不接受「没有理由」。"""
        pid = _mk_product(migrated_db, seeded["team"], asin="B0R214D007")
        auth = _login(client, ADMIN, PASSWORD)
        assert client.delete(DEL_URL.format(pid), headers=auth).status_code == 422
        assert _row_exists(migrated_db, "app.product", "id = %s", (pid,))
