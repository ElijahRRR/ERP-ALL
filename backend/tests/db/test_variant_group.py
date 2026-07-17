"""R2-11 增量1 验收：变体自动归组 + broken 判定 v1 + 契约端点（D-Q2/D-Q63）。

- 自动归组：只信 twister（variant_attributes 非空）；排除 parent_asin 自指；
  组身份 (team_id, source_parent_ref)；双向同步 product.variant_group_id；幂等重跑不增组。
- broken 判定 v1：成员<2 / 组内维度键集不一致 / 超上限（variant.max_group_size 配置中心）；
  broken 置位出 warn 通知；修复后自动回 active。
- 端点：人工建组 + PUT 全量设成员（主变体唯一闸/一品一组闸/摘除双向清理）+ 列表过滤。
"""

import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.catalog import variant as variant_service
from erp.core.db import get_session_factory
from erp.core.security import hash_password

from .conftest import APP_URL
from .test_identity_api import PASSWORD, _login

ADMIN = "variant_admin"
TEAM = "变体归组测试团队"


def _seed_product(
    conn: psycopg.Connection,
    team_id: int,
    asin: str,
    *,
    parent: str | None,
    vattrs: str | None,
) -> int:
    import json as _json

    attrs: dict = {}
    if parent is not None:
        attrs["parent_asin"] = parent
    if vattrs is not None:
        attrs["variant_attributes"] = vattrs
    return conn.execute(
        "INSERT INTO app.product (team_id, source_ref, title, attrs)"
        " VALUES (%s, %s, %s, %s::jsonb) RETURNING id",
        (team_id, asin, f"测试品 {asin}", _json.dumps(attrs)),
    ).fetchone()[0]


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        conn.execute(
            "UPDATE app.product SET variant_group_id = NULL WHERE team_id = %s", (team_id,)
        )
        for t in ("variant_member",):
            conn.execute(
                f"DELETE FROM app.{t} WHERE group_id IN"
                " (SELECT id FROM app.variant_group WHERE team_id = %s)",
                (team_id,),
            )
        conn.execute("DELETE FROM app.variant_group WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.product WHERE team_id = %s", (team_id,))
        conn.execute(
            "DELETE FROM app.team_config WHERE team_id = %s AND key = 'variant.max_group_size'",
            (team_id,),
        )
        # 正常两人组（同维度键集）
        p1 = _seed_product(conn, team_id, "B0AAAAAA01",
                           parent="B0PARENT01", vattrs="color_name=Red; size_name=L")  # fmt: skip
        p2 = _seed_product(conn, team_id, "B0AAAAAA02",
                           parent="B0PARENT01", vattrs="color_name=Blue; size_name=M")  # fmt: skip
        # 自指 parent（parser 无父体填自身）→ 不归组
        p3 = _seed_product(conn, team_id, "B0SELF0001",
                           parent="B0SELF0001", vattrs="color_name=Red")  # fmt: skip
        # 无 twister 素材（旧全页正则来源不可信）→ 不归组
        p4 = _seed_product(conn, team_id, "B0NOTWIST1", parent="B0PARENT09", vattrs=None)
        # 单成员组 → broken（成员不齐）
        p5 = _seed_product(conn, team_id, "B0LONELY01",
                           parent="B0PARENT02", vattrs="color_name=Red")  # fmt: skip
        # 主题冲突组（维度键集不一致）→ broken
        p6 = _seed_product(conn, team_id, "B0CONFL001",
                           parent="B0PARENT03", vattrs="color_name=Red")  # fmt: skip
        p7 = _seed_product(conn, team_id, "B0CONFL002",
                           parent="B0PARENT03", vattrs="size_name=L")  # fmt: skip
    return {"team": team_id, "p1": p1, "p2": p2, "p3": p3, "p4": p4, "p5": p5,
            "p6": p6, "p7": p7}  # fmt: skip


class TestAutoGrouping:
    async def test_sync_groups_and_broken_rules(self, seeded: dict, migrated_db: str) -> None:
        totals = await variant_service.run(get_session_factory(), {})
        assert totals["failed"] == 0 and totals["grouped"] == 5  # p1 p2 p5 p6 p7
        with psycopg.connect(migrated_db) as conn:
            # 正常组：active + theme=排序维度键串 + 双向关联（组键=分量 min，经成员反查）
            g1 = conn.execute(
                "SELECT g.id, g.variation_theme, g.status FROM app.variant_group g"
                " JOIN app.product p ON p.variant_group_id = g.id WHERE p.id = %s",
                (seeded["p1"],),
            ).fetchone()
            assert g1[1] == "color_name,size_name" and g1[2] == "active"
            member = conn.execute(
                "SELECT variant_attrs FROM app.variant_member"
                " WHERE group_id = %s AND product_id = %s",
                (g1[0], seeded["p1"]),
            ).fetchone()[0]
            assert member == {"color_name": "Red", "size_name": "L"}
            assert (
                conn.execute(
                    "SELECT variant_group_id FROM app.product WHERE id = %s", (seeded["p1"],)
                ).fetchone()[0]
                == g1[0]
            )
            # 自指/无 twister：不归组
            for pid in (seeded["p3"], seeded["p4"]):
                assert (
                    conn.execute(
                        "SELECT variant_group_id FROM app.product WHERE id = %s", (pid,)
                    ).fetchone()[0]
                    is None
                )
            # 单成员 → broken + warn 通知
            g2 = conn.execute(
                "SELECT g.id, g.status FROM app.variant_group g"
                " JOIN app.product p ON p.variant_group_id = g.id WHERE p.id = %s",
                (seeded["p5"],),
            ).fetchone()
            assert g2[1] == "broken"
            assert (
                conn.execute(
                    "SELECT 1 FROM app.notification WHERE dedupe_key = %s",
                    (f"variant_broken:{g2[0]}",),
                ).fetchone()
                is not None
            )
            # 主题冲突 → broken
            assert (
                conn.execute(
                    "SELECT g.status FROM app.variant_group g"
                    " JOIN app.product p ON p.variant_group_id = g.id WHERE p.id = %s",
                    (seeded["p6"],),
                ).fetchone()[0]
                == "broken"
            )

    async def test_rerun_idempotent(self, seeded: dict, migrated_db: str) -> None:
        with psycopg.connect(migrated_db) as conn:
            before = conn.execute(
                "SELECT count(*) FROM app.variant_group WHERE team_id = %s", (seeded["team"],)
            ).fetchone()[0]
        totals = await variant_service.run(get_session_factory(), {})
        assert totals["grouped"] == 0  # 已归组者不再扫描
        with psycopg.connect(migrated_db) as conn:
            after = conn.execute(
                "SELECT count(*) FROM app.variant_group WHERE team_id = %s", (seeded["team"],)
            ).fetchone()[0]
            assert after == before

    async def test_max_group_size_guard(self, seeded: dict, migrated_db: str) -> None:
        """超上限 → broken（巨型伪组护栏，配置中心生效）。"""
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.team_config (team_id, key, value)"
                " VALUES (%s, 'variant.max_group_size', '2'::jsonb)"
                " ON CONFLICT (team_id, key) DO UPDATE SET value = excluded.value",
                (seeded["team"],),
            )
            for i in range(3):
                _seed_product(conn, seeded["team"], f"B0BIGFAM{i:02d}",
                              parent="B0PARENT04", vattrs=f"color_name=C{i}")  # fmt: skip
        await variant_service.run(get_session_factory(), {})
        with psycopg.connect(migrated_db) as conn:
            assert (
                conn.execute(
                    "SELECT g.status FROM app.variant_group g"
                    " JOIN app.product p ON p.variant_group_id = g.id"
                    " WHERE p.source_ref = 'B0BIGFAM00' AND p.team_id = %s",
                    (seeded["team"],),
                ).fetchone()[0]
                == "broken"
            )

    # ── 增量2.5 回归：家族连通分量归组 + 错裂解散（真机缺陷修复）──

    async def test_split_parents_family_converges_one_group(
        self, seeded: dict, migrated_db: str
    ) -> None:
        """真机缺陷复现：同家族各页 parentAsin 不一致（甚至自指），兄弟列表互指 → 应归一组。"""
        import json as _json

        asins = [f"B0SPLIT00{i}" for i in range(4)]
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            # 复位前一用例设下的巨型组上限（team_config 残留会误伤本用例）
            conn.execute(
                "DELETE FROM app.team_config WHERE team_id = %s AND key = 'variant.max_group_size'",
                (seeded["team"],),
            )
            for i, a in enumerate(asins):
                attrs = {
                    # 各页 parent 互不一致：两个自指、两个各指不同值（真机观察形态）
                    "parent_asin": a if i < 2 else f"B0FAKEPAR{i}",
                    "variant_attributes": f"color_name=C{i}",
                    "variation_asins": [x for x in asins if x != a],
                }
                conn.execute(
                    "INSERT INTO app.product (team_id, source_ref, title, attrs)"
                    " VALUES (%s, %s, %s, %s::jsonb)",
                    (seeded["team"], a, f"裂组复现 {a}", _json.dumps(attrs)),
                )
        totals = await variant_service.run(get_session_factory(), {})
        assert totals["failed"] == 0
        with psycopg.connect(migrated_db) as conn:
            rows = conn.execute(
                "SELECT variant_group_id FROM app.product"
                " WHERE team_id = %s AND source_ref = ANY(%s)",
                (seeded["team"], asins),
            ).fetchall()
            gids = {r[0] for r in rows}
            assert len(gids) == 1 and None not in gids  # 一家一组，不裂
            gid = gids.pop()
            status, key = conn.execute(
                "SELECT status, source_parent_ref FROM app.variant_group WHERE id = %s", (gid,)
            ).fetchone()
            assert status == "active"
            assert key == "B0FAKEPAR2"  # 分量 min（含假 parent 标识，确定性）

    async def test_orphan_singletons_dissolved_and_merged(
        self, seeded: dict, migrated_db: str
    ) -> None:
        """老键错裂的 broken 单员组：解散重归为一组 active（A152 真机现场的修复路径）。"""
        import json as _json

        asins = ["B0ORPH0001", "B0ORPH0002", "B0ORPH0003"]
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            pids = {}
            for i, a in enumerate(asins):
                attrs = {
                    "parent_asin": f"B0OLDPAR0{i}",  # 各自不同的旧 parent（裂组根因）
                    "variant_attributes": f"size_name=S{i}",
                    "variation_asins": [x for x in asins if x != a],
                }
                pids[a] = conn.execute(
                    "INSERT INTO app.product (team_id, source_ref, title, attrs)"
                    " VALUES (%s, %s, %s, %s::jsonb) RETURNING id",
                    (seeded["team"], a, f"错裂 {a}", _json.dumps(attrs)),
                ).fetchone()[0]
                # 模拟旧逻辑产物：每品一个以旧 parent 为键的 broken 单员组
                gid = conn.execute(
                    "INSERT INTO app.variant_group (team_id, source_parent_ref, status)"
                    " VALUES (%s, %s, 'broken') RETURNING id",
                    (seeded["team"], f"B0OLDPAR0{i}"),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO app.variant_member (group_id, product_id, variant_attrs)"
                    " VALUES (%s, %s, %s::jsonb)",
                    (gid, pids[a], _json.dumps({"size_name": f"S{i}"})),
                )
                conn.execute(
                    "UPDATE app.product SET variant_group_id = %s WHERE id = %s",
                    (gid, pids[a]),
                )
        totals = await variant_service.run(get_session_factory(), {})
        assert totals["dissolved"] >= 2  # 错裂组被解散（幸存者收编其余成员）
        with psycopg.connect(migrated_db) as conn:
            gids = {
                r[0]
                for r in conn.execute(
                    "SELECT variant_group_id FROM app.product"
                    " WHERE team_id = %s AND source_ref = ANY(%s)",
                    (seeded["team"], asins),
                ).fetchall()
            }
            assert len(gids) == 1 and None not in gids
            assert (
                conn.execute(
                    "SELECT status FROM app.variant_group WHERE id = %s", (gids.pop(),)
                ).fetchone()[0]
                == "active"
            )

    async def test_lonely_singleton_stable_across_runs(
        self, seeded: dict, migrated_db: str
    ) -> None:
        """真孤品单员组不解散不换号（防每轮重建 + broken 通知随新组号刷屏）。"""
        with psycopg.connect(migrated_db) as conn:
            before = conn.execute(
                "SELECT variant_group_id FROM app.product WHERE id = %s", (seeded["p5"],)
            ).fetchone()[0]
        assert before is not None
        await variant_service.run(get_session_factory(), {})
        with psycopg.connect(migrated_db) as conn:
            after = conn.execute(
                "SELECT variant_group_id FROM app.product WHERE id = %s", (seeded["p5"],)
            ).fetchone()[0]
            assert after == before  # 组号稳定


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
    """审核员模板角色含 catalog.product_read/write。"""
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM app.app_user WHERE username = %s", (ADMIN,))
        uid = conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '变体测试员') RETURNING id",
            (seeded["team"], ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO app.role (team_id, name, description)
            SELECT %s, name, description FROM app.role tpl
            WHERE tpl.team_id IS NULL AND tpl.name = '审核员'
              AND NOT EXISTS (SELECT 1 FROM app.role r
                              WHERE r.team_id = %s AND r.name = '审核员')
            """,
            (seeded["team"], seeded["team"]),
        )
        conn.execute(
            """
            INSERT INTO app.role_permission (role_id, permission_code)
            SELECT r.id, rp.permission_code FROM app.role r
            JOIN app.role tpl ON tpl.team_id IS NULL AND tpl.name = r.name
            JOIN app.role_permission rp ON rp.role_id = tpl.id
            WHERE r.team_id = %s AND r.name = '审核员'
            ON CONFLICT DO NOTHING
            """,
            (seeded["team"],),
        )
        conn.execute(
            """
            INSERT INTO app.user_role (user_id, role_id)
            SELECT %s, r.id FROM app.role r WHERE r.team_id = %s AND r.name = '审核员'
            ON CONFLICT DO NOTHING
            """,
            (uid, seeded["team"]),
        )
    return ADMIN


class TestVariantEndpoints:
    def test_manual_group_and_set_members(
        self, client: TestClient, seeded: dict, migrated_db: str, admin_user: str
    ) -> None:
        h = _login(client, admin_user, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            pa = _seed_product(conn, seeded["team"], "B0MANUAL01", parent=None, vattrs=None)
            pb = _seed_product(conn, seeded["team"], "B0MANUAL02", parent=None, vattrs=None)
        g = client.post(
            "/api/v1/variant-groups",
            json={"source_parent_ref": "B0MANPAR01", "variation_theme": "color_name"},
            headers=h,
        )
        assert g.status_code == 201, g.text
        gid = g.json()["id"]
        r = client.put(
            f"/api/v1/variant-groups/{gid}/members",
            json=[
                {"product_id": pa, "variant_attrs": {"color_name": "Red"}, "is_primary": True},
                {"product_id": pb, "variant_attrs": {"color_name": "Blue"}},
            ],
            headers=h,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "active"
        # 主变体唯一闸
        dup = client.put(
            f"/api/v1/variant-groups/{gid}/members",
            json=[
                {"product_id": pa, "variant_attrs": {"color_name": "Red"}, "is_primary": True},
                {"product_id": pb, "variant_attrs": {"color_name": "Blue"}, "is_primary": True},
            ],
            headers=h,
        )
        assert dup.status_code == 422
        assert dup.json()["error"]["code"] == "VARIANT_PRIMARY_CONFLICT"
        # 一品一组闸：p1 已属自动归组的组
        taken = client.put(
            f"/api/v1/variant-groups/{gid}/members",
            json=[{"product_id": seeded["p1"], "variant_attrs": {}}],
            headers=h,
        )
        assert taken.status_code == 422
        assert taken.json()["error"]["code"] == "VARIANT_MEMBER_TAKEN"
        # 摘员：全量 PUT 单成员 → 另一成员双向清理 + 组置 broken（成员<2）
        shrink = client.put(
            f"/api/v1/variant-groups/{gid}/members",
            json=[{"product_id": pa, "variant_attrs": {"color_name": "Red"}}],
            headers=h,
        )
        assert shrink.status_code == 200 and shrink.json()["status"] == "broken"
        with psycopg.connect(migrated_db) as conn:
            assert (
                conn.execute(
                    "SELECT variant_group_id FROM app.product WHERE id = %s", (pb,)
                ).fetchone()[0]
                is None
            )

    def test_list_filters(
        self, client: TestClient, seeded: dict, migrated_db: str, admin_user: str
    ) -> None:
        h = _login(client, admin_user, PASSWORD)
        page = client.get("/api/v1/variant-groups?status=active", headers=h).json()
        assert page["total"] >= 1
        assert all(i["status"] == "active" for i in page["items"])
        # 组键=分量 min（增量2.5）：先取 p1 所在组的键再精确查
        with psycopg.connect(migrated_db) as conn:
            g1_key = conn.execute(
                "SELECT g.source_parent_ref FROM app.variant_group g"
                " JOIN app.product p ON p.variant_group_id = g.id WHERE p.id = %s",
                (seeded["p1"],),
            ).fetchone()[0]
        hit = client.get(f"/api/v1/variant-groups?q={g1_key}", headers=h).json()
        assert hit["total"] == 1
        members = hit["items"][0]["members"]
        assert {m["product_id"] for m in members} == {seeded["p1"], seeded["p2"]}
        assert all("master_sku" in m for m in members)
