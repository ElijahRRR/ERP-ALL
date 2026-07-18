"""R2-11 增量2 段3 验收（提交层）：变体组整组守卫 + anchor 首上锁定（D-Q63）。

照 test_listing_api 的 _FakeChannel + MockTransport 渠道替身；播种照 test_variant_group
的组/成员种子写法。经 allocate → submit API 驱动 service._submit_tx1 组级守卫：
- 整组齐 → 一个 feed，每个 MPItem 含 VG 段（variantGroupId=VG{组id}），anchor 首上锁定本批店；
- broken 组（submit 兜底，防绕过分配入口）→ 整组 skip（VARIANT_GROUP_BROKEN）；
- 组不齐 → 整组 skip、消息含缺席成员、成员状态不动（VARIANT_GROUP_INCOMPLETE）；
- anchor 已锁 A 店、向 B 店提交 → 整组 skip（VARIANT_ANCHOR_MISMATCH）；
- 组内一员构建失败（坏价触发 ERP_SPEC_INVALID）→ 失败者 failed、其余撤出 feed 且状态不动
  （VARIANT_GROUP_MEMBER_FAILED）、feed 内不含该组任何 item；
- allocate 对 broken 组成员拒绝（VARIANT_GROUP_BROKEN，最早拦截）。
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

    def test_incomplete_group_skip_with_absent_member(
        self, client: TestClient, seeded: dict, migrated_db: str, fake: _FakeChannel
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        pids = [seeded["g3_0"], seeded["g3_1"], seeded["g3_2"]]
        created = _allocate(client, auth, seeded["sb"], pids)["created"]
        by_pid = _by_pid(created)
        present = [by_pid[seeded["g3_0"]], by_pid[seeded["g3_1"]]]  # 只带 2/3
        absent_pid = seeded["g3_2"]
        r = client.post(
            "/api/v1/listings/submit", headers=_idem(auth), json={"listing_ids": present}
        )
        body = r.json()
        assert body["queued"] == 0
        assert body["feed_id"] is None
        codes = {s["code"] for s in body["skipped"]}
        assert codes == {"VARIANT_GROUP_INCOMPLETE"}
        # 缺席成员 product_id 在消息里可见
        assert all(str(absent_pid) in s["message"] for s in body["skipped"])
        # 在批成员状态不动
        for lid in present:
            assert _status(migrated_db, lid) == "draft"

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

    def test_allocate_rejects_broken_group_member(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        auth = _login(client, ADMIN, PASSWORD)
        resp = _allocate(client, auth, seeded["sb"], [seeded["g6_0"]])
        assert resp["created"] == []
        assert resp["rejected"][0]["code"] == "VARIANT_GROUP_BROKEN"

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
