"""R2-12 增量4a 验收（全店对账+报错回收，D-Q65②）。

- 13 类归类逐字保真（旧仓 feishu_sync 语义：严重性顺序 / J 特殊 / Z 兜底）；
- 三类差异落点（land_store_diff 纯 DB，无 HTTP）：后台有本地无→通知+样例；
  漂移→degraded+error_code+维护任务分派（E→delist / A→end_date_renewal）；
  永久禁售类→error_recycle pending 候选断言（asin 恒记 + C/E 品牌）；
- 幂等：重跑零新增（degraded 不再迁移、任务/断言去重）；
- runner：认领→执行失败落 failed+error（成功路径走真机——需渠道 HTTP）。
"""

import psycopg
import pytest

from erp.core.db import get_session_factory, system_tx
from erp.listing import item_pull, maintenance

TEAM = "对账测试团队"
PREFIX = "zrecon"


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        store_id = conn.execute(
            "SELECT id FROM app.store WHERE team_id = %s AND code = 'ZRECON1'", (team_id,)
        ).fetchone()
        if store_id is None:
            store_id = conn.execute(
                "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
                " VALUES (%s, %s, 'ZRECON1', '对账测试店', true) RETURNING id",
                (team_id, channel_id),
            ).fetchone()
        pid = {}
        for n, brand in ((1, "ZReconBrandX"), (2, "Unbranded"), (3, None)):
            asin = f"B0ZRECON{n:02d}"
            row = conn.execute(
                "SELECT id FROM app.product WHERE team_id = %s AND source_ref = %s",
                (team_id, asin),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "INSERT INTO app.product (team_id, source_channel, source_ref, title, brand)"
                    " VALUES (%s, 'amazon', %s, %s, %s) RETURNING id",
                    (team_id, asin, f"Recon Test Product {n}", brand),
                ).fetchone()
            pid[n] = row[0]
    products = {f"p{k}": int(v) for k, v in pid.items()}
    return {"team": int(team_id), "store": int(store_id[0]), **products}


@pytest.fixture(autouse=True)
def _clean(migrated_db: str, seeded: dict[str, int]):  # type: ignore[no-untyped-def]
    def _wipe() -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute("DELETE FROM app.maintenance_task WHERE team_id = %s", (seeded["team"],))
            conn.execute(
                "DELETE FROM app.blacklist_assertion WHERE team_id = %s", (seeded["team"],)
            )
            conn.execute("DELETE FROM app.blacklist_brand WHERE team_id = %s", (seeded["team"],))
            conn.execute("DELETE FROM app.blacklist_asin WHERE team_id = %s", (seeded["team"],))
            conn.execute("DELETE FROM app.notification WHERE category = 'item_recon'")
            conn.execute(
                "DELETE FROM app.listing_state_history WHERE team_id = %s", (seeded["team"],)
            )
            conn.execute("DELETE FROM app.listing WHERE team_id = %s", (seeded["team"],))

    _wipe()
    yield
    _wipe()


def _mk_listing(
    migrated_db: str, seeded: dict[str, int], sku: str, product: int, status: str = "live"
) -> int:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        return int(
            conn.execute(
                "INSERT INTO app.listing (team_id, store_id, product_id, offer_mode,"
                " channel_sku, status) VALUES (%s, %s, %s, 'build', %s, %s) RETURNING id",
                (seeded["team"], seeded["store"], product, sku, status),
            ).fetchone()[0]
        )


class TestCategorize:
    def test_verbatim_categories(self) -> None:
        assert item_pull.categorize("Your item's End Date has passed") == "A"
        assert item_pull.categorize("violates the Prohibited Product Policy") == "B"
        assert item_pull.categorize("Walmart has partnered with select brands") == "C"
        assert item_pull.categorize("an Intellectual Property claim was filed") == "E"
        assert item_pull.categorize("stays in stage status until you go live") == "J"
        assert item_pull.categorize("brand new unheard-of reason") == "Z"
        # 严重性顺序：具体归类（E）优先于兜底性 A
        both = "Intellectual Property issue | End Date has passed"
        assert item_pull.categorize(both) == "E"


async def _land(seeded: dict[str, int], remote: dict) -> dict:
    sf = get_session_factory()
    async with system_tx(sf) as s:
        return await item_pull.land_store_diff(
            s, team_id=seeded["team"], store_id=seeded["store"], remote=remote
        )


class TestLandDiff:
    async def test_three_diffs_and_idempotent(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        l_e = _mk_listing(migrated_db, seeded, f"{PREFIX}-E", seeded["p1"])
        l_a = _mk_listing(migrated_db, seeded, f"{PREFIX}-A", seeded["p3"])
        _mk_listing(migrated_db, seeded, f"{PREFIX}-OK", seeded["p2"])
        _mk_listing(migrated_db, seeded, f"{PREFIX}-GONE", seeded["p2"])
        remote = {
            f"{PREFIX}-MISS": {"published_status": "PUBLISHED", "lifecycle": "ACTIVE",
                               "reasons": []},
            f"{PREFIX}-E": {"published_status": "UNPUBLISHED", "lifecycle": "ACTIVE",
                            "reasons": ["An Intellectual Property claim was filed"]},
            f"{PREFIX}-A": {"published_status": "UNPUBLISHED", "lifecycle": "ACTIVE",
                            "reasons": ["Your item's End Date has passed"]},
            f"{PREFIX}-OK": {"published_status": "PUBLISHED", "lifecycle": "ACTIVE",
                             "reasons": []},
        }  # fmt: skip
        stats = await _land(seeded, remote)
        assert stats["missing_local"] == 1
        assert stats["gone_remote"] == 1
        assert stats["drift_degraded"] == 2
        assert stats["tasks"] == 2
        # 候选：E 类 → asin + 品牌（ZReconBrandX 非占位词）两条 pending
        assert stats["candidates"] == 2
        with psycopg.connect(migrated_db) as conn:
            st_e, err_e = conn.execute(
                "SELECT status, error_code FROM app.listing WHERE id = %s", (l_e,)
            ).fetchone()
            assert (st_e, err_e) == ("degraded", "ITEM_PULL_E")
            kinds = dict(
                conn.execute(
                    "SELECT listing_id, task_kind FROM app.maintenance_task"
                    " WHERE team_id = %s AND status = 'scheduled'",
                    (seeded["team"],),
                ).fetchall()
            )
            assert kinds == {l_e: "delist", l_a: "end_date_renewal"}
            cands = conn.execute(
                "SELECT domain, subject_norm, status FROM app.blacklist_assertion"
                " WHERE team_id = %s AND source = 'error_recycle' ORDER BY domain",
                (seeded["team"],),
            ).fetchall()
            assert [(c[0], c[2]) for c in cands] == [("asin", "pending"), ("brand", "pending")]
            assert ("brand", "zreconbrandx", "pending") in cands
            # pending 不投影：canonical 无行（人工闸）
            canon = conn.execute(
                "SELECT count(*) FROM app.blacklist_brand WHERE team_id = %s"
                " AND brand_norm = 'zreconbrandx'",
                (seeded["team"],),
            ).fetchone()[0]
            assert canon == 0
            notif = conn.execute(
                "SELECT count(*) FROM app.notification WHERE category = 'item_recon'"
            ).fetchone()[0]
            assert notif == 1
        # 幂等重跑：degraded 不再迁移，任务/断言零新增
        again = await _land(seeded, remote)
        assert again["drift_degraded"] == 0
        assert again["tasks"] == 0
        assert again["candidates"] == 0
        with psycopg.connect(migrated_db) as conn:
            n_tasks = conn.execute(
                "SELECT count(*) FROM app.maintenance_task WHERE team_id = %s",
                (seeded["team"],),
            ).fetchone()[0]
        assert n_tasks == 2

    async def test_stage_pending_j_untouched(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        l_j = _mk_listing(migrated_db, seeded, f"{PREFIX}-J", seeded["p3"])
        stats = await _land(
            seeded,
            {
                f"{PREFIX}-J": {
                    "published_status": "UNPUBLISHED",
                    "lifecycle": "ACTIVE",
                    "reasons": ["item remains in stage status until you go live"],
                }
            },
        )
        assert stats["drift_degraded"] == 0
        assert stats["by_category"] == {"J": 1}
        with psycopg.connect(migrated_db) as conn:
            st = conn.execute("SELECT status FROM app.listing WHERE id = %s", (l_j,)).fetchone()[0]
        assert st == "live"  # 特殊程序/待发布：不动


class TestMaintenanceRunner:
    async def test_claim_and_fail_cleanly(self, migrated_db: str, seeded: dict[str, int]) -> None:
        """draft 状态下架必失败（状态守卫）→ 任务 failed + error 留痕，不吞不重试。"""
        l_draft = _mk_listing(migrated_db, seeded, f"{PREFIX}-DRAFT", seeded["p3"], "draft")
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.maintenance_task (team_id, store_id, listing_id, task_kind,"
                " scheduled_at) VALUES (%s, %s, %s, 'delist', now())",
                (seeded["team"], seeded["store"], l_draft),
            )
        # kinds 必须显式给：本例意图是验 delist 的失败路径，与默认档无关。
        # 此前传 {"batch": 5} 靠 runner 的 ["delist"] fallback 才能认领——那个 fallback 本身
        # 是 fail-open 缺陷（已修为 []），本例曾是它的挡枪测试之一。
        stats = await maintenance.run(get_session_factory(), {"batch": 5, "kinds": ["delist"]})
        assert stats == {"claimed": 1, "done": 0, "failed": 1}
        with psycopg.connect(migrated_db) as conn:
            status, error = conn.execute(
                "SELECT status, error FROM app.maintenance_task WHERE listing_id = %s",
                (l_draft,),
            ).fetchone()
        assert status == "failed"
        assert error  # 留痕（LISTING_STATE_INVALID）

    async def test_unclaimed_kinds_stay_queued(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        """kinds 的选择性认领：配了 delist 档时，end_date_renewal 任务恒留队列。

        原措辞「不在默认 kinds」把 runner 的 ["delist"] fallback 追认成了「默认档」，与
        D-Q13/29、本文件 runner docstring、0037 种子（kinds=[]）三处相悖；fallback 已修为
        空表。这里改为显式配 kinds，避免退化成「空表当然认领不到」的空转断言。
        """
        l_a = _mk_listing(migrated_db, seeded, f"{PREFIX}-EDR", seeded["p3"], "degraded")
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.maintenance_task (team_id, store_id, listing_id, task_kind,"
                " scheduled_at) VALUES (%s, %s, %s, 'end_date_renewal', now())",
                (seeded["team"], seeded["store"], l_a),
            )
        stats = await maintenance.run(get_session_factory(), {"batch": 5, "kinds": ["delist"]})
        assert stats["claimed"] == 0
        with psycopg.connect(migrated_db) as conn:
            st = conn.execute(
                "SELECT status FROM app.maintenance_task WHERE listing_id = %s", (l_a,)
            ).fetchone()[0]
        assert st == "scheduled"
