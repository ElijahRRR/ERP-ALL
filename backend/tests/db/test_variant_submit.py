"""R2-11 增量2 段3 验收（提交层）：变体组整组守卫 + anchor 首上锁定（D-Q63）。

照 test_listing_api 的 _FakeChannel + MockTransport 渠道替身；播种照 test_variant_group
的组/成员种子写法。经 allocate → submit API 驱动 service._submit_tx1 组级守卫：
- 整组/子集 → 一个 feed，每个 MPItem 含 VG 段（variantGroupId=VG{组id}），anchor 首上锁定
  本批店（D-Q64③：家族完整性检查废止，子集即可成组/追加）；
- broken 组（submit 兜底）→ 成组模式整组 skip（VARIANT_GROUP_BROKEN）；散品模式放行无 VG 段
  （D-Q64②，variant_mode=standalone）；allocate 不再拦 broken 组成员；
- 单批超上限 variant.max_batch_members → 整批 skip（VARIANT_BATCH_TOO_LARGE）；
- anchor 已锁 A 店、向 B 店提交 → 整组 skip（VARIANT_ANCHOR_MISMATCH）；
- 组内一员构建失败（坏价触发 ERP_SPEC_INVALID）→ 失败者 failed、其余撤出 feed 且状态不动
  （VARIANT_GROUP_MEMBER_FAILED）、feed 内不含该组任何 item（批次原子性）；
- regroup（D-Q64④）：live 成员补挂 VG 段重投，item_regroup 独立归位（失败不动状态/不清 GTIN）。
"""

import json
import uuid

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.channel.gateway import gateway
from erp.core.security import hash_password
from erp.core.settings import get_settings

from .conftest import APP_URL
from .test_identity_api import PASSWORD, _login

ADMIN = "variant_submit_admin"
TEAM = "变体提交测试团队"
PREFIX = "ZVSUB"
WPT = f"{PREFIX}_Variant"  # 变体字段齐备的 PT（否则组成员 build 直接 fail-closed）

_VARIANT_PT_FIELDS = {
    "type": "object",
    "required": ["shortDescription"],
    "properties": {
        "shortDescription": {"type": "string", "maxLength": 4000},
        "brand": {"type": "string"},
        "color": {"type": "string"},
        "size": {"type": "string"},
        "variantGroupId": {"type": "string", "maxLength": 300},
        "variantAttributeNames": {"type": "array", "minItems": 1, "items": {"type": "string"}},
    },
}


def _idem(auth: dict) -> dict:
    return {**auth, "Idempotency-Key": str(uuid.uuid4())}


class _FakeChannel:
    """渠道替身：/v3/token 发 token；业务路径按脚本队列响应（照 test_listing_api）。"""

    def __init__(self) -> None:
        self.script: list[httpx.Response | Exception] = []
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 900})
        self.requests.append(request)
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return httpx.Response(200, json={"ok": True})


def _mk_product(conn, team_id, ref, *, priced=True):  # type: ignore[no-untyped-def]
    attrs = json.dumps({"wpt": WPT, "bullets": ["solid"], "description": f"nice variant {ref}"})
    snap = '{"list": 19.99}' if priced else "{}"
    return conn.execute(
        "INSERT INTO app.product (team_id, source_channel, source_ref, title, attrs,"
        " price_snapshot, status)"
        " VALUES (%s, 'amazon', %s, %s, %s::jsonb, %s::jsonb, 'audit_passed') RETURNING id",
        (team_id, ref, f"Variant Item {ref}", attrs, snap),
    ).fetchone()[0]


def _mk_group(conn, team_id, parent, *, status, anchor=None):  # type: ignore[no-untyped-def]
    return conn.execute(
        "INSERT INTO app.variant_group (team_id, source_parent_ref, variation_theme, status,"
        " anchor_store_id) VALUES (%s, %s, 'color_name,size_name', %s, %s) RETURNING id",
        (team_id, parent, status, anchor),
    ).fetchone()[0]


def _add_member(conn, group_id, product_id, attrs):  # type: ignore[no-untyped-def]
    conn.execute(
        "INSERT INTO app.variant_member (group_id, product_id, variant_attrs)"
        " VALUES (%s, %s, %s::jsonb)",
        (group_id, product_id, json.dumps(attrs)),
    )
    conn.execute(
        "UPDATE app.product SET variant_group_id = %s WHERE id = %s", (group_id, product_id)
    )


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:  # noqa: PLR0915 单块播种（清场+组/成员种子），拆散反失真
    key = get_settings().credential_key
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM app.app_user WHERE username = %s", (ADMIN,))
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        # ── 清场（FK 安全序）──
        conn.execute("DELETE FROM app.listing_state_history WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.channel_command WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.api_idempotency WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.feed_item WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.feed WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.listing_spec WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.listing WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.gtin_pool WHERE team_id = %s", (team_id,))
        conn.execute(
            "UPDATE app.product SET variant_group_id = NULL WHERE team_id = %s", (team_id,)
        )
        conn.execute(
            "DELETE FROM app.variant_member WHERE group_id IN"
            " (SELECT id FROM app.variant_group WHERE team_id = %s)",
            (team_id,),
        )
        conn.execute("DELETE FROM app.variant_group WHERE team_id = %s", (team_id,))
        conn.execute("DELETE FROM app.product WHERE team_id = %s", (team_id,))
        conn.execute(
            "DELETE FROM app.store_credential WHERE store_id IN"
            " (SELECT id FROM app.store WHERE team_id = %s)",
            (team_id,),
        )
        conn.execute("DELETE FROM app.store WHERE team_id = %s", (team_id,))
        conn.execute(
            "DELETE FROM refdata.pt_spec WHERE walmart_product_type LIKE %s", (f"{PREFIX}%",)
        )
        conn.execute(
            "DELETE FROM refdata.pt_meta WHERE walmart_product_type LIKE %s", (f"{PREFIX}%",)
        )

        # ── 用户 / 角色 / 权限 ──
        uid = conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, '变体提交管理员') RETURNING id",
            (team_id, ADMIN, hash_password(PASSWORD)),
        ).fetchone()[0]
        conn.execute(
            "DELETE FROM app.role WHERE team_id = %s AND name = '变体提交测试角色'", (team_id,)
        )
        role_id = conn.execute(
            "INSERT INTO app.role (team_id, name) VALUES (%s, '变体提交测试角色') RETURNING id",
            (team_id,),
        ).fetchone()[0]
        # catalog.product_write：TestAnchorRelease 走 /variant-groups/{id}/anchor/release
        for code in ("listing.read", "listing.allocate", "listing.submit",
                     "catalog.product_write"):  # fmt: skip
            conn.execute(
                "INSERT INTO app.role_permission (role_id, permission_code) VALUES (%s, %s)",
                (role_id, code),
            )
        conn.execute("INSERT INTO app.user_role (user_id, role_id) VALUES (%s, %s)", (uid, role_id))

        # ── PT spec（变体字段齐备）──
        conn.execute(
            "INSERT INTO refdata.pt_meta (walmart_product_type, walmart_category,"
            " zh_seller_forbidden, access_state, zh_can_do) VALUES (%s, 'Home', false,"
            " '普通商品', '是')",
            (WPT,),
        )
        conn.execute(
            "INSERT INTO refdata.pt_spec (walmart_product_type, fields) VALUES (%s, %s::jsonb)",
            (WPT, json.dumps(_VARIANT_PT_FIELDS)),
        )

        # ── 两店：SB 主提交店（is_test → live_test 走替身）+ SA anchor-其它店 ──
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        store_b = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, 'ZVSUB_B', '变体提交店B', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
        store_a = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, 'ZVSUB_A', '变体锚定店A', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO app.store_credential (store_id, client_id, client_secret_encrypted)"
            " VALUES (%s, 'zvsub-client', pgp_sym_encrypt('zvsub-secret', %s))",
            (store_b, key),
        )

        # ── GTIN 池（ean_13 free，充足）——直接播种，绕导入端点 ──
        for i in range(30):
            conn.execute(
                "INSERT INTO app.gtin_pool (team_id, gtin, gtin_kind, source)"
                " VALUES (%s, %s, 'ean_13', 'generator_import')",
                (team_id, f"78{i:011d}"),
            )

        # ── 变体组 + 成员 ──
        ids: dict[str, int] = {"team": team_id, "user": uid, "sb": store_b, "sa": store_a}
        theme = [{"color_name": "Red", "size_name": "L"},
                 {"color_name": "Blue", "size_name": "M"},
                 {"color_name": "Green", "size_name": "S"}]  # fmt: skip
        # G1 齐全组
        g1 = _mk_group(conn, team_id, "VGSUB_1", status="active")
        for n, at in enumerate(theme):
            pid = _mk_product(conn, team_id, f"VGSUB_1{n}")
            _add_member(conn, g1, pid, at)
            ids[f"g1_{n}"] = pid
        ids["g1"] = g1
        # G2 提交期置 broken（allocate 时仍 active）
        g2 = _mk_group(conn, team_id, "VGSUB_2", status="active")
        for n, at in enumerate(theme):
            pid = _mk_product(conn, team_id, f"VGSUB_2{n}")
            _add_member(conn, g2, pid, at)
            ids[f"g2_{n}"] = pid
        ids["g2"] = g2
        # G3 组不齐（提交只带 2/3）
        g3 = _mk_group(conn, team_id, "VGSUB_3", status="active")
        for n, at in enumerate(theme):
            pid = _mk_product(conn, team_id, f"VGSUB_3{n}")
            _add_member(conn, g3, pid, at)
            ids[f"g3_{n}"] = pid
        ids["g3"] = g3
        # G4 anchor 已锁 SA
        g4 = _mk_group(conn, team_id, "VGSUB_4", status="active", anchor=store_a)
        for n, at in enumerate(theme):
            pid = _mk_product(conn, team_id, f"VGSUB_4{n}")
            _add_member(conn, g4, pid, at)
            ids[f"g4_{n}"] = pid
        ids["g4"] = g4
        # G5 一员坏价（g5_2 无 price_snapshot.list → build ERP_SPEC_INVALID）
        g5 = _mk_group(conn, team_id, "VGSUB_5", status="active")
        for n, at in enumerate(theme):
            pid = _mk_product(conn, team_id, f"VGSUB_5{n}", priced=(n != 2))
            _add_member(conn, g5, pid, at)
            ids[f"g5_{n}"] = pid
        ids["g5"] = g5
        # G6 broken（allocate 拒）
        g6 = _mk_group(conn, team_id, "VGSUB_6", status="broken")
        ids["g6_0"] = _mk_product(conn, team_id, "VGSUB_60")
        _add_member(conn, g6, ids["g6_0"], {"color_name": "Red"})
        ids["g6"] = g6

        # 网关模式：live_test
        conn.execute(
            "INSERT INTO app.system_config (key, value)"
            " VALUES ('channel.gateway_mode', '\"live_test\"'::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value"
        )
    return ids


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


@pytest.fixture()
def fake(seeded: dict[str, int]) -> _FakeChannel:
    f = _FakeChannel()
    gateway._clients.clear()
    gateway._transport_factory = lambda proxy: httpx.MockTransport(f.handler)
    yield f
    gateway._clients.clear()
    gateway._transport_factory = gateway._default_transport


def _allocate(client: TestClient, auth: dict, store_id: int, product_ids: list[int]) -> dict:
    return client.post(
        "/api/v1/listings/allocate",
        headers=_idem(auth),
        json={"product_ids": product_ids, "store_id": store_id, "offer_mode": "build"},
    ).json()


def _by_pid(created: list[dict]) -> dict[int, int]:
    """created 项 → {product_id: listing_id}。"""
    return {c["product_id"]: c["id"] for c in created}


def _status(migrated_db: str, listing_id: int) -> str:
    with psycopg.connect(migrated_db) as conn:
        return conn.execute(
            "SELECT status FROM app.listing WHERE id = %s", (listing_id,)
        ).fetchone()[0]


class TestVariantGroupSubmit:
    def test_full_group_one_feed_vg_segment_and_anchor_lock(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        pids = [seeded["g1_0"], seeded["g1_1"], seeded["g1_2"]]
        created = _allocate(client, auth, seeded["sb"], pids)["created"]
        assert len(created) == 3
        listing_ids = [c["id"] for c in created]
        fake.script = [httpx.Response(200, json={"feedId": "F-VG-1"})]
        r = client.post(
            "/api/v1/listings/submit", headers=_idem(auth), json={"listing_ids": listing_ids}
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["queued"] == 3
        assert body["feed_status"] == "submitted"
        assert body["channel_feed_id"] == "F-VG-1"
        # 一个 feed 三个 MPItem，各含 VG 段（组中立引用 VG{组id}）；isPrimaryVariant 不注入
        sent = json.loads(fake.requests[0].content)
        assert len(sent["MPItem"]) == 3
        vg_ref = f"VG{seeded['g1']}"
        for it in sent["MPItem"]:
            vis = it["Visible"][WPT]
            assert vis["variantGroupId"] == vg_ref
            assert vis["variantAttributeNames"] == ["color", "size"]
            assert "isPrimaryVariant" not in vis
        # anchor 首上锁定本批店 SB（tx1 内、WHERE anchor IS NULL 保证只锁一次）
        with psycopg.connect(migrated_db) as conn:
            anchor = conn.execute(
                "SELECT anchor_store_id FROM app.variant_group WHERE id = %s", (seeded["g1"],)
            ).fetchone()[0]
        assert anchor == seeded["sb"]

    def test_broken_group_whole_skip(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        pids = [seeded["g2_0"], seeded["g2_1"], seeded["g2_2"]]
        created = _allocate(client, auth, seeded["sb"], pids)["created"]  # allocate 时组 active
        assert len(created) == 3
        listing_ids = [c["id"] for c in created]
        # 提交前置 broken —— 验 submit 兜底守卫（防绕过分配入口）
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.variant_group SET status = 'broken' WHERE id = %s", (seeded["g2"],)
            )
        r = client.post(
            "/api/v1/listings/submit", headers=_idem(auth), json={"listing_ids": listing_ids}
        )
        body = r.json()
        assert body["queued"] == 0
        assert body["feed_id"] is None
        codes = {s["code"] for s in body["skipped"]}
        assert codes == {"VARIANT_GROUP_BROKEN"}
        assert len(body["skipped"]) == 3
        assert all("broken" in s["message"] for s in body["skipped"])  # 原因可见
        # 成员状态不动（仍 draft）
        for lid in listing_ids:
            assert _status(migrated_db, lid) == "draft"

    def test_subset_submit_forms_group(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """D-Q64③：家族完整性检查废止——本批子集（2/3）即可成组上架并锁 anchor。"""
        auth = _login(client, ADMIN, PASSWORD)
        pids = [seeded["g3_0"], seeded["g3_1"], seeded["g3_2"]]
        created = _allocate(client, auth, seeded["sb"], pids)["created"]
        by_pid = _by_pid(created)
        present = [by_pid[seeded["g3_0"]], by_pid[seeded["g3_1"]]]  # 只带 2/3
        fake.script = [httpx.Response(200, json={"feedId": "F-VG-3"})]
        r = client.post(
            "/api/v1/listings/submit", headers=_idem(auth), json={"listing_ids": present}
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["queued"] == 2
        assert not any(s.get("code") == "VARIANT_GROUP_INCOMPLETE" for s in body.get("skipped", []))
        sent = json.loads(fake.requests[-1].content)
        assert len(sent["MPItem"]) == 2
        for it in sent["MPItem"]:
            assert it["Visible"][WPT]["variantGroupId"] == f"VG{seeded['g3']}"
        with psycopg.connect(migrated_db) as conn:
            anchor = conn.execute(
                "SELECT anchor_store_id FROM app.variant_group WHERE id = %s", (seeded["g3"],)
            ).fetchone()[0]
        assert anchor == seeded["sb"]  # 首个子集即锁店
        # 未在批的第三员不受影响（draft 可后续同店追加）
        assert _status(migrated_db, by_pid[seeded["g3_2"]]) == "draft"

    def test_anchor_mismatch_whole_skip(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        pids = [seeded["g4_0"], seeded["g4_1"], seeded["g4_2"]]
        # G4 anchor 已锁 SA，向 SB 提交
        created = _allocate(client, auth, seeded["sb"], pids)["created"]
        listing_ids = [c["id"] for c in created]
        r = client.post(
            "/api/v1/listings/submit", headers=_idem(auth), json={"listing_ids": listing_ids}
        )
        body = r.json()
        assert body["queued"] == 0
        assert body["feed_id"] is None
        codes = {s["code"] for s in body["skipped"]}
        assert codes == {"VARIANT_ANCHOR_MISMATCH"}
        assert all(str(seeded["sa"]) in s["message"] for s in body["skipped"])
        for lid in listing_ids:
            assert _status(migrated_db, lid) == "draft"
        # anchor 不转移：仍是 SA
        with psycopg.connect(migrated_db) as conn:
            anchor = conn.execute(
                "SELECT anchor_store_id FROM app.variant_group WHERE id = %s", (seeded["g4"],)
            ).fetchone()[0]
        assert anchor == seeded["sa"]

    def test_member_build_failure_rejects_whole_group(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        pids = [seeded["g5_0"], seeded["g5_1"], seeded["g5_2"]]  # g5_2 无价 → build 失败
        created = _allocate(client, auth, seeded["sb"], pids)["created"]
        by_pid = _by_pid(created)
        assert len(created) == 3
        listing_ids = [c["id"] for c in created]
        r = client.post(
            "/api/v1/listings/submit", headers=_idem(auth), json={"listing_ids": listing_ids}
        )
        body = r.json()
        assert body["queued"] == 0
        assert body["feed_id"] is None  # feed 内不含该组任何 item（整组拒，无 feed）
        skip_by_lid = {s["listing_id"]: s["code"] for s in body["skipped"]}
        failed_lid = by_pid[seeded["g5_2"]]
        ok_lids = [by_pid[seeded["g5_0"]], by_pid[seeded["g5_1"]]]
        # 失败者走 ERP_SPEC_INVALID + failed；其余撤出并记 VARIANT_GROUP_MEMBER_FAILED、状态不动
        assert skip_by_lid[failed_lid] == "ERP_SPEC_INVALID"
        assert _status(migrated_db, failed_lid) == "failed"
        for lid in ok_lids:
            assert skip_by_lid[lid] == "VARIANT_GROUP_MEMBER_FAILED"
            assert _status(migrated_db, lid) == "draft"
        # anchor 从未锁定（整组未入列）
        with psycopg.connect(migrated_db) as conn:
            anchor = conn.execute(
                "SELECT anchor_store_id FROM app.variant_group WHERE id = %s", (seeded["g5"],)
            ).fetchone()[0]
        assert anchor is None

    def test_broken_member_allocate_ok_group_gated_standalone_passes(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """D-Q64②：allocate 不再拦 broken 组成员；成组提交闸①仍拒；散品模式放行无 VG 段。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            g6_1 = _mk_product(conn, seeded["team"], "VGSUB_61")
            _add_member(conn, seeded["g6"], g6_1, {"color_name": "Blue"})
        resp = _allocate(client, auth, seeded["sb"], [g6_1])
        assert len(resp["created"]) == 1  # 分配放行（分配期不知道上架模式）
        lid = resp["created"][0]["id"]
        # 成组提交 → 闸① broken 拒，状态不动
        r = client.post(
            "/api/v1/listings/submit",
            headers=_idem(auth),
            json={"listing_ids": [lid], "variant_mode": "group"},
        )
        body = r.json()
        assert body["queued"] == 0
        assert {s["code"] for s in body["skipped"]} == {"VARIANT_GROUP_BROKEN"}
        assert _status(migrated_db, lid) == "draft"
        # 散品提交 → 放行，feed 条目无 VG 段，anchor 不锁
        fake.script = [httpx.Response(200, json={"feedId": "F-VG-6S"})]
        r = client.post(
            "/api/v1/listings/submit",
            headers=_idem(auth),
            json={"listing_ids": [lid], "variant_mode": "standalone"},
        )
        assert r.status_code == 202, r.text
        assert r.json()["queued"] == 1
        sent = json.loads(fake.requests[-1].content)
        assert "variantGroupId" not in sent["MPItem"][0]["Visible"][WPT]
        with psycopg.connect(migrated_db) as conn:
            anchor = conn.execute(
                "SELECT anchor_store_id FROM app.variant_group WHERE id = %s", (seeded["g6"],)
            ).fetchone()[0]
        assert anchor is None

    def test_batch_cap_rejects_oversize_batch(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """闸③新语义（D-Q64③）：单批成员数超 variant.max_batch_members → 整批拒绝。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            g10 = _mk_group(conn, seeded["team"], "VG_PARENT_10", status="active")
            pids = []
            for n, at in enumerate(
                [{"color_name": "Red", "size_name": "L"}, {"color_name": "Blue", "size_name": "M"}]
            ):
                pid = _mk_product(conn, seeded["team"], f"VGSUB_A{n}")
                _add_member(conn, g10, pid, at)
                pids.append(pid)
            conn.execute(
                "INSERT INTO app.team_config (team_id, key, value)"
                " VALUES (%s, 'variant.max_batch_members', '1'::jsonb)"
                " ON CONFLICT (team_id, key) DO UPDATE SET value = excluded.value",
                (seeded["team"],),
            )
        try:
            created = _allocate(client, auth, seeded["sb"], pids)["created"]
            assert len(created) == 2
            r = client.post(
                "/api/v1/listings/submit",
                headers=_idem(auth),
                json={"listing_ids": [c["id"] for c in created]},
            )
            body = r.json()
            assert body["queued"] == 0
            assert {s["code"] for s in body["skipped"]} == {"VARIANT_BATCH_TOO_LARGE"}
            for c in created:
                assert _status(migrated_db, c["id"]) == "draft"
        finally:
            with psycopg.connect(migrated_db, autocommit=True) as conn:
                conn.execute(
                    "DELETE FROM app.team_config"
                    " WHERE team_id = %s AND key = 'variant.max_batch_members'",
                    (seeded["team"],),
                )

    # ── 评审修复回归（fable 终审后落码）──

    def test_allocate_match_exempt_from_broken_group(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """D-Q63 配套：match 不携带变体段，broken 组不得阻断 match 分配（评审 blocker）。"""
        auth = _login(client, ADMIN, PASSWORD)
        resp = client.post(
            "/api/v1/listings/allocate",
            headers=_idem(auth),
            json={
                "product_ids": [seeded["g6_0"]],
                "store_id": seeded["sb"],
                "offer_mode": "match",
            },
        ).json()
        assert not any(r.get("code") == "VARIANT_GROUP_BROKEN" for r in resp.get("rejected", []))
        assert len(resp["created"]) == 1

    def test_absent_member_on_channel_counts_present(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        """ "在场"扩展（评审 major）：同店已在架成员视为在场——渠道部分失败补投/追加成员不死锁。"""
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            g7 = _mk_group(conn, seeded["team"], "VG_PARENT_7", status="active",
                           anchor=seeded["sb"])  # fmt: skip
            p_live = _mk_product(conn, seeded["team"], "VGSUB_70")
            p_new = _mk_product(conn, seeded["team"], "VGSUB_71")
            _add_member(conn, g7, p_live, {"color_name": "Red", "size_name": "L"})
            _add_member(conn, g7, p_new, {"color_name": "Blue", "size_name": "M"})
            # p_live 已在本店在架（模拟渠道侧既成事实）
            conn.execute(
                "INSERT INTO app.listing (team_id, store_id, product_id, channel_sku,"
                " offer_mode, status) VALUES (%s, %s, %s, 'VGSUB70SKU', 'build', 'live')",
                (seeded["team"], seeded["sb"], p_live),
            )
        created = _allocate(client, auth, seeded["sb"], [p_new])["created"]
        assert len(created) == 1
        fake.script = [httpx.Response(200, json={"feedId": "F-VG-7"})]
        r = client.post(
            "/api/v1/listings/submit",
            headers=_idem(auth),
            json={"listing_ids": [created[0]["id"]]},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["queued"] == 1  # 组员在架=在场，单成员补投放行
        assert not any(s.get("code") == "VARIANT_GROUP_INCOMPLETE" for s in body.get("skipped", []))


class TestAnchorRelease:
    """R2-11 检修：anchor 首发即败解锁通道（挂账清偿；替代 runbook 手工 SQL）。"""

    def test_release_paths(self, client: TestClient, seeded: dict, migrated_db: str) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            g8 = _mk_group(conn, seeded["team"], "VG_PARENT_8", status="active",
                           anchor=seeded["sb"])  # fmt: skip
            p1 = _mk_product(conn, seeded["team"], "VGSUB_80")
            p2 = _mk_product(conn, seeded["team"], "VGSUB_81")
            _add_member(conn, g8, p1, {"color_name": "Red", "size_name": "L"})
            _add_member(conn, g8, p2, {"color_name": "Blue", "size_name": "M"})
            # 首发即败现场：锚定店一员 failed（不在场）+ 一员在途 queued（在场）
            conn.execute(
                "INSERT INTO app.listing (team_id, store_id, product_id, channel_sku,"
                " offer_mode, status) VALUES (%s, %s, %s, 'VGSUB80SKU', 'build', 'failed')",
                (seeded["team"], seeded["sb"], p1),
            )
            lid = conn.execute(
                "INSERT INTO app.listing (team_id, store_id, product_id, channel_sku,"
                " offer_mode, status) VALUES (%s, %s, %s, 'VGSUB81SKU', 'build', 'queued')"
                " RETURNING id",
                (seeded["team"], seeded["sb"], p2),
            ).fetchone()[0]
        # 在途成员在场 → fail-closed 409
        r = client.post(f"/api/v1/variant-groups/{g8}/anchor/release", headers=auth)
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "VARIANT_ANCHOR_IN_USE"
        # 在途成员归位 failed（整组首发即败）→ 解锁放行 + 审计留痕
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute("UPDATE app.listing SET status = 'failed' WHERE id = %s", (lid,))
        r = client.post(f"/api/v1/variant-groups/{g8}/anchor/release", headers=auth)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["released_store_id"] == seeded["sb"]
        assert body["detail"]["anchor_store_id"] is None
        with psycopg.connect(migrated_db) as conn:
            assert (
                conn.execute(
                    "SELECT anchor_store_id FROM app.variant_group WHERE id = %s", (g8,)
                ).fetchone()[0]
                is None
            )
            assert (
                conn.execute(
                    "SELECT 1 FROM app.audit_log WHERE action = 'catalog.variant_anchor_release'"
                    " AND object_type = 'variant_group' AND object_id = %s",
                    (str(g8),),
                ).fetchone()
                is not None
            )
        # 未锚定 → 409 VARIANT_ANCHOR_NOT_SET（重复解锁幂等拒绝）
        r = client.post(f"/api/v1/variant-groups/{g8}/anchor/release", headers=auth)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "VARIANT_ANCHOR_NOT_SET"


class TestRegroup:
    """D-Q64④ live 散品补挂成组：item_regroup feed 独立归位（组 8 现场修复的通用能力）。"""

    def test_regroup_happy_path_locks_anchor_keeps_live(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            g11 = _mk_group(conn, seeded["team"], "VG_PARENT_11", status="active")
            lids = []
            for n, at in enumerate(
                [{"color_name": "Red", "size_name": "L"}, {"color_name": "Blue", "size_name": "M"}]
            ):
                pid = _mk_product(conn, seeded["team"], f"VGSUB_B{n}")
                _add_member(conn, g11, pid, at)
                lids.append(
                    conn.execute(
                        "INSERT INTO app.listing (team_id, store_id, product_id, channel_sku,"
                        " offer_mode, status, gtin, current_price, current_inventory)"
                        " VALUES (%s, %s, %s, %s, 'build', 'live', %s, 19.99, 5) RETURNING id",
                        (seeded["team"], seeded["sb"], pid, f"VGSUBB{n}SKU", f"7899{n:09d}"),
                    ).fetchone()[0]
                )
        seeded["g11"], seeded["g11_lids"] = g11, lids
        fake.script = [httpx.Response(200, json={"feedId": "F-RG-1"})]
        r = client.post(
            "/api/v1/listings/variant-regroup",
            headers=_idem(auth),
            json={"group_id": g11, "store_id": seeded["sb"]},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["queued"] == 2 and body["feed_status"] == "submitted"
        # feed 条目带 VG 段（散品转成组的关键差异）
        sent = json.loads(fake.requests[-1].content)
        assert len(sent["MPItem"]) == 2
        for it in sent["MPItem"]:
            assert it["Visible"][WPT]["variantGroupId"] == f"VG{g11}"
        with psycopg.connect(migrated_db) as conn:
            kind, anchor = conn.execute(
                "SELECT f.feed_kind, g.anchor_store_id FROM app.feed f, app.variant_group g"
                " WHERE f.id = %s AND g.id = %s",
                (body["feed_id"], g11),
            ).fetchone()
            assert kind == "item_regroup"
            assert anchor == seeded["sb"]  # 补挂即锁 anchor
            for lid in lids:  # 成员保持 live（不迁 queued）
                assert (
                    conn.execute("SELECT status FROM app.listing WHERE id = %s", (lid,)).fetchone()[
                        0
                    ]
                    == "live"
                )

    def test_regroup_guards(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        # anchor 已锁 SB → 向 SA 补挂拒绝（不自动转移）
        r = client.post(
            "/api/v1/listings/variant-regroup",
            headers=_idem(auth),
            json={"group_id": seeded["g11"], "store_id": seeded["sa"]},
        )
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "VARIANT_ANCHOR_MISMATCH"
        # 组无在架成员（G5 成员 draft/failed）→ 拒绝
        r = client.post(
            "/api/v1/listings/variant-regroup",
            headers=_idem(auth),
            json={"group_id": seeded["g5"], "store_id": seeded["sb"]},
        )
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "ERP_REGROUP_NO_TARGETS"

    async def test_regroup_poll_error_keeps_members_live(
        self, seeded: dict, migrated_db: str
    ) -> None:
        """独立归位的安全底线：条目失败不打 failed、不释放 GTIN（成员照常在架）。"""
        from erp.core.db import get_session_factory, system_tx
        from erp.listing import service as listing_service

        with psycopg.connect(migrated_db) as conn:
            feed = conn.execute(
                "SELECT id, team_id, store_id FROM app.feed"
                " WHERE feed_kind = 'item_regroup' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            rows = conn.execute(
                "SELECT fi.channel_sku, fi.listing_id, l.gtin FROM app.feed_item fi"
                " JOIN app.listing l ON l.id = fi.listing_id"
                " WHERE fi.feed_id = %s ORDER BY fi.listing_id",
                (feed[0],),
            ).fetchall()
        assert len(rows) == 2
        data = {
            "feedStatus": "PROCESSED",
            "itemDetails": {
                "itemIngestionStatus": [
                    {"sku": rows[0][0], "ingestionStatus": "SUCCESS", "wpid": "W-RG-1"},
                    {
                        "sku": rows[1][0],
                        "ingestionStatus": "DATA_ERROR",
                        "ingestionErrors": {
                            "ingestionError": [{"code": "WM_VG_TEST", "description": "boom"}]
                        },
                    },
                ]
            },
        }
        feed_dict = {
            "id": feed[0], "team_id": feed[1], "store_id": feed[2],
            "feed_kind": "item_regroup",
        }  # fmt: skip
        async with system_tx(get_session_factory()) as session:
            await listing_service._apply_poll_result(session, feed_dict, data)
        with psycopg.connect(migrated_db) as conn:
            for _sku, lid, _g in rows:  # 成败两员都保持 live
                assert (
                    conn.execute("SELECT status FROM app.listing WHERE id = %s", (lid,)).fetchone()[
                        0
                    ]
                    == "live"
                )
            gtin_now = conn.execute(
                "SELECT gtin FROM app.listing WHERE id = %s", (rows[1][1],)
            ).fetchone()[0]
            assert gtin_now == rows[1][2] and gtin_now is not None  # GTIN 未清
            assert (
                conn.execute("SELECT status FROM app.feed WHERE id = %s", (feed[0],)).fetchone()[0]
                == "partial"
            )
            item_err = conn.execute(
                "SELECT status, error_code FROM app.feed_item"
                " WHERE feed_id = %s AND channel_sku = %s",
                (feed[0], rows[1][0]),
            ).fetchone()
            assert item_err[0] == "error" and item_err[1] == "WM_VG_TEST"
