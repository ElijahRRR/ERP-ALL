"""R2-11 增量2 段3 验收（构建器层）：spec 构建器变体段（D-Q63）。

照 test_spec_v5 的 psycopg 播种 pt_spec fixture + 直调 build_spec 模式：
- 分组 build：Visible 含映射维度值（color/size）；实例化 item 注入 variantGroupId="VG{组id}"
  与排序 variantAttributeNames；isPrimaryVariant 一律不注入；
- match 模式同一分组产品无任何变体字段（D-Q3 参数包差异，变体段仅 build）；
- 未分组产品行为/指纹与增量前一致（不含 "variant" 键，skip_variant 开关不改哈希）；
- 维度值变化 → build_hash 变（缓存失效）；组 id 变化不进指纹（VG 走实例化层，缓存仍命中）；
- 维度键映射不到 / PT 不支持变体 / broken 组 三重 fail-closed（ERP_SPEC_BUILD_FAILED）。

pt_spec fixture 手写 JSON schema（照 test_spec_v5 模式）：WPT 含
variantGroupId/variantAttributeNames/color/size 字段定义；WPT_NOVAR 缺变体字段（验 PT 不支持）。
"""

import asyncio
import json
from typing import Any

import psycopg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from erp.core.db import system_tx
from erp.core.errors import BusinessError
from erp.listing import spec as spec_builder

from .conftest import APP_URL, MIGRATOR_URL, _pg_dsn

PREFIX = "ZVAR"
WPT = f"{PREFIX}_Variant"  # 变体维度/组字段齐备的 PT
WPT_NOVAR = f"{PREFIX}_Plain"  # 缺 variantGroupId/variantAttributeNames 的 PT
EAN = "6901234567892"
TEAM_NAME = "变体spec测试团队"

# WPT 变体维度默认映射：color_name→color、size_name→size（spec._DEFAULT_THEME_MAP），
# 故 pt_spec 须含 color/size 取值字段 + variantGroupId/variantAttributeNames 组字段
_VARIANT_PT_FIELDS = {
    "type": "object",
    "required": ["shortDescription"],
    "properties": {
        "shortDescription": {"type": "string", "maxLength": 4000},
        "brand": {"type": "string"},
        "mainImageUrl": {"type": "string", "format": "uri"},
        "color": {"type": "string"},
        "size": {"type": "string"},
        "variantGroupId": {"type": "string", "maxLength": 300},
        "variantAttributeNames": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string"},
        },
    },
}
# 不支持变体的 PT：有维度取值字段但无组字段——_resolve_variant 应 fail-closed
_PLAIN_PT_FIELDS = {
    "type": "object",
    "required": ["shortDescription"],
    "properties": {
        "shortDescription": {"type": "string", "maxLength": 4000},
        "brand": {"type": "string"},
        "color": {"type": "string"},
        "size": {"type": "string"},
    },
}


def _sessions():  # type: ignore[no-untyped-def]
    return async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)


def _mk_product(conn, team_id: int, ref: str, *, wpt: str) -> int:  # type: ignore[no-untyped-def]
    attrs = json.dumps(
        {"wpt": wpt, "bullets": ["solid build", "easy clean", "food safe"],
         "description": f"nice variant item {ref}"}
    )  # fmt: skip
    return conn.execute(
        "INSERT INTO app.product (team_id, source_channel, source_ref, title, attrs, status)"
        " VALUES (%s, 'amazon', %s, %s, %s::jsonb, 'audit_passed') RETURNING id",
        (team_id, ref, f"Title {ref} " + "x" * 60, attrs),
    ).fetchone()[0]


def _mk_group(conn, team_id: int, parent: str, *, status: str, theme: str) -> int:  # type: ignore[no-untyped-def]
    return conn.execute(
        "INSERT INTO app.variant_group (team_id, source_parent_ref, variation_theme, status)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (team_id, parent, theme, status),
    ).fetchone()[0]


def _add_member(conn, group_id: int, product_id: int, attrs: dict[str, str]) -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        "INSERT INTO app.variant_member (group_id, product_id, variant_attrs)"
        " VALUES (%s, %s, %s::jsonb)",
        (group_id, product_id, json.dumps(attrs)),
    )
    conn.execute(
        "UPDATE app.product SET variant_group_id = %s WHERE id = %s", (group_id, product_id)
    )


@pytest.fixture(scope="module", autouse=True)
def _seed(migrated_db: str):  # type: ignore[no-untyped-def]
    def _wipe(conn) -> None:  # type: ignore[no-untyped-def]
        team = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM_NAME,)).fetchone()
        if team:
            tid = team[0]
            conn.execute(
                "UPDATE app.product SET variant_group_id = NULL WHERE team_id = %s", (tid,)
            )
            conn.execute(
                "DELETE FROM app.variant_member WHERE group_id IN"
                " (SELECT id FROM app.variant_group WHERE team_id = %s)",
                (tid,),
            )
            conn.execute("DELETE FROM app.variant_group WHERE team_id = %s", (tid,))
            conn.execute("DELETE FROM app.listing_spec WHERE team_id = %s", (tid,))
            conn.execute("DELETE FROM app.product WHERE team_id = %s", (tid,))
        conn.execute(
            "DELETE FROM refdata.pt_spec WHERE walmart_product_type LIKE %s", (f"{PREFIX}%",)
        )
        conn.execute(
            "DELETE FROM refdata.pt_meta WHERE walmart_product_type LIKE %s", (f"{PREFIX}%",)
        )

    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _wipe(conn)
        conn.execute("INSERT INTO app.team (name) VALUES (%s) ON CONFLICT DO NOTHING", (TEAM_NAME,))
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM_NAME,)).fetchone()[
            0
        ]
        for pt in (WPT, WPT_NOVAR):
            conn.execute(
                "INSERT INTO refdata.pt_meta (walmart_product_type, walmart_category,"
                " zh_seller_forbidden, access_state, zh_can_do) VALUES (%s, 'Home', false,"
                " '普通商品', '是')",
                (pt,),
            )
        conn.execute(
            "INSERT INTO refdata.pt_spec (walmart_product_type, fields) VALUES (%s, %s::jsonb)",
            (WPT, json.dumps(_VARIANT_PT_FIELDS)),
        )
        conn.execute(
            "INSERT INTO refdata.pt_spec (walmart_product_type, fields) VALUES (%s, %s::jsonb)",
            (WPT_NOVAR, json.dumps(_PLAIN_PT_FIELDS)),
        )

        ids: dict[str, int] = {}
        # 正常组 GA（active，双成员）——基础 build + match 用例
        ga = _mk_group(conn, team_id, "VGPARENT_A", status="active", theme="color_name,size_name")
        ids["a1"] = _mk_product(conn, team_id, "VGP_A1", wpt=WPT)
        ids["a2"] = _mk_product(conn, team_id, "VGP_A2", wpt=WPT)
        _add_member(conn, ga, ids["a1"], {"color_name": "Red", "size_name": "L"})
        _add_member(conn, ga, ids["a2"], {"color_name": "Blue", "size_name": "M"})
        ids["ga"] = ga
        # 未分组
        ids["ung"] = _mk_product(conn, team_id, "VGP_UNG", wpt=WPT)
        # broken 组
        gbrk = _mk_group(conn, team_id, "VGPARENT_BRK", status="broken", theme="color_name")
        ids["brk"] = _mk_product(conn, team_id, "VGP_BRK", wpt=WPT)
        _add_member(conn, gbrk, ids["brk"], {"color_name": "Red"})
        ids["gbrk"] = gbrk
        # 维度键映射不到（flavor_name 不在 theme_map 也不在 WPT 字段集）
        gbad = _mk_group(conn, team_id, "VGPARENT_BAD", status="active", theme="flavor_name")
        ids["bad"] = _mk_product(conn, team_id, "VGP_BAD", wpt=WPT)
        _add_member(conn, gbad, ids["bad"], {"flavor_name": "Vanilla"})
        # PT 不支持变体（WPT_NOVAR 缺变体字段）
        gnov = _mk_group(conn, team_id, "VGPARENT_NOV", status="active", theme="color_name")
        ids["nov"] = _mk_product(conn, team_id, "VGP_NOV", wpt=WPT_NOVAR)
        _add_member(conn, gnov, ids["nov"], {"color_name": "Red"})
        # 维度值变化用例（专属组/产品，避免污染其它用例）
        gh = _mk_group(conn, team_id, "VGPARENT_H", status="active", theme="color_name,size_name")
        ids["h"] = _mk_product(conn, team_id, "VGP_H", wpt=WPT)
        _add_member(conn, gh, ids["h"], {"color_name": "Red", "size_name": "L"})
        ids["gh"] = gh
        # 组 id 变化用例：VGP_MOVE 从 GMA 迁到 GMB（维度值不变）
        gma = _mk_group(conn, team_id, "VGPARENT_MA", status="active", theme="color_name,size_name")
        gmb = _mk_group(conn, team_id, "VGPARENT_MB", status="active", theme="color_name,size_name")
        ids["move"] = _mk_product(conn, team_id, "VGP_MOVE", wpt=WPT)
        _add_member(conn, gma, ids["move"], {"color_name": "Red", "size_name": "L"})
        ids["gma"], ids["gmb"] = gma, gmb
        ids["team"] = team_id
    yield ids
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _wipe(conn)


async def _product(pid: int) -> dict[str, Any]:
    from sqlalchemy import text

    async with system_tx(_sessions()) as s:
        row = (
            (
                await s.execute(
                    text(
                        "SELECT id, team_id, title, brand, images, attrs, price_snapshot,"
                        " category_path, amazon_leaf_id FROM app.product WHERE id = :p"
                    ),
                    {"p": pid},
                )
            )
            .mappings()
            .one()
        )
        return dict(row)


async def _build(pid: int, **kw: Any) -> dict[str, Any]:
    product = await _product(pid)
    async with system_tx(_sessions()) as s:
        defaults: dict[str, Any] = {
            "offer_mode": "build",
            "gtin": EAN,
            "channel_sku": "M0000001",
            "price": 19.99,
            "inventory": 5,
        }
        defaults.update(kw)
        return await spec_builder.build_spec(s, product=product, **defaults)


class TestVariantBuild:
    def test_grouped_build_injects_variant_segment(self, _seed: dict) -> None:
        built = asyncio.run(_build(_seed["a1"]))
        v = built["item"]["Visible"][WPT]
        # 映射后维度值进 Visible（color_name→color、size_name→size）
        assert v["color"] == "Red" and v["size"] == "L"
        # 实例化注入：组中立引用 VG{组id} + 排序变体属性名；isPrimaryVariant 一律不注入
        assert v["variantGroupId"] == f"VG{_seed['ga']}"
        assert v["variantAttributeNames"] == ["color", "size"]
        assert "isPrimaryVariant" not in v
        assert built["validation"]["ok"] is True

    def test_match_mode_no_variant_segment(self, _seed: dict) -> None:
        built = asyncio.run(_build(_seed["a1"], offer_mode="match"))
        item = built["item"]
        assert "Visible" not in item  # match 只有 Item
        assert set(item["Item"]) == {
            "sku", "productIdentifiers", "price", "ShippingWeight", "condition",
        }  # fmt: skip
        assert "variantGroupId" not in item["Item"]
        assert "variantAttributeNames" not in item["Item"]

    def test_ungrouped_unchanged_and_skip_variant_hash_stable(self, _seed: dict) -> None:
        built = asyncio.run(_build(_seed["ung"]))
        v = built["item"]["Visible"][WPT]
        assert "variantGroupId" not in v  # 未分组不注入变体段
        assert "variantAttributeNames" not in v
        assert "color" not in v and "size" not in v
        # 未分组指纹不含 "variant" 键：skip_variant 开关不改哈希（增量前行为不变）
        with_skip = asyncio.run(_build(_seed["ung"], skip_variant=True))
        assert with_skip["build_hash"] == built["build_hash"]
        assert with_skip["spec_id"] == built["spec_id"]

    def test_dimension_change_invalidates_hash_group_id_does_not(
        self, _seed: dict, migrated_db: str
    ) -> None:
        # (a) 维度值变化 → build_hash 变（缓存失效）
        h1 = asyncio.run(_build(_seed["h"]))
        assert h1["item"]["Visible"][WPT]["color"] == "Red"
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            conn.execute(
                "UPDATE app.variant_member SET variant_attrs = %s::jsonb"
                " WHERE group_id = %s AND product_id = %s",
                (json.dumps({"color_name": "Green", "size_name": "L"}), _seed["gh"], _seed["h"]),
            )
        h2 = asyncio.run(_build(_seed["h"]))
        assert h2["build_hash"] != h1["build_hash"]  # 维度值进指纹
        assert h2["item"]["Visible"][WPT]["color"] == "Green"

        # (b) 组 id 变化（维度值不变）→ build_hash 不变（VG 走实例化层），缓存仍命中
        m1 = asyncio.run(_build(_seed["move"]))
        assert m1["item"]["Visible"][WPT]["variantGroupId"] == f"VG{_seed['gma']}"
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            conn.execute(
                "DELETE FROM app.variant_member WHERE group_id = %s AND product_id = %s",
                (_seed["gma"], _seed["move"]),
            )
            conn.execute(
                "INSERT INTO app.variant_member (group_id, product_id, variant_attrs)"
                " VALUES (%s, %s, %s::jsonb)",
                (_seed["gmb"], _seed["move"], json.dumps({"color_name": "Red", "size_name": "L"})),
            )
            conn.execute(
                "UPDATE app.product SET variant_group_id = %s WHERE id = %s",
                (_seed["gmb"], _seed["move"]),
            )
        m2 = asyncio.run(_build(_seed["move"]))
        assert m2["build_hash"] == m1["build_hash"]  # 组 id 不进指纹
        assert m2["spec_id"] == m1["spec_id"]  # 缓存命中同一模板行
        assert m2["item"]["Visible"][WPT]["variantGroupId"] == f"VG{_seed['gmb']}"


class TestVariantFailClosed:
    def test_unmappable_dimension_key_fail_closed(self, _seed: dict) -> None:
        with pytest.raises(BusinessError) as ei:
            asyncio.run(_build(_seed["bad"]))
        assert ei.value.code == "ERP_SPEC_BUILD_FAILED"

    def test_pt_without_variant_fields_fail_closed(self, _seed: dict) -> None:
        with pytest.raises(BusinessError) as ei:
            asyncio.run(_build(_seed["nov"]))
        assert ei.value.code == "ERP_SPEC_BUILD_FAILED"

    def test_broken_group_fail_closed(self, _seed: dict) -> None:
        with pytest.raises(BusinessError) as ei:
            asyncio.run(_build(_seed["brk"]))
        assert ei.value.code == "ERP_SPEC_BUILD_FAILED"


class TestVariantControlFieldLeak:
    """评审修复回归：变体控制字段唯一来源 = _instantiate 注入（LLM 幻觉/isPrimaryVariant 剥离）。"""

    def test_llm_fill_variant_controls_stripped(self, _seed: dict) -> None:
        import psycopg

        from .conftest import MIGRATOR_URL, _pg_dsn

        # 未分组产品：LLM 填充里塞满幻觉变体控制字段 + 主变体标记
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            pid = _mk_product(conn, _seed["team"], "VLEAK_1", wpt=WPT)
            conn.execute(
                "UPDATE app.product SET attrs = attrs || %s::jsonb WHERE id = %s",
                (
                    json.dumps(
                        {
                            "walmart_fill": {
                                WPT: {
                                    "visible": {
                                        "variantGroupId": "HALLUCINATED",
                                        "variantAttributeNames": ["color"],
                                        "isPrimaryVariant": "Yes",
                                        "color": "Red",
                                    }
                                }
                            }
                        }
                    ),
                    pid,
                ),
            )
        built = asyncio.run(_build(pid))
        v = built["item"]["Visible"][WPT]
        assert "variantGroupId" not in v  # 幻觉组标识被剥离（未分组不得携带）
        assert "variantAttributeNames" not in v
        assert "isPrimaryVariant" not in v
        assert v.get("color") == "Red"  # 普通字段照常合并

    def test_grouped_llm_is_primary_stripped(self, _seed: dict) -> None:
        import psycopg

        from .conftest import MIGRATOR_URL, _pg_dsn

        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            conn.execute(
                "UPDATE app.product SET attrs = attrs || %s::jsonb WHERE id = %s",
                (
                    json.dumps({"walmart_fill": {WPT: {"visible": {"isPrimaryVariant": "Yes"}}}}),
                    _seed["a1"],
                ),
            )
            # 破坏性写 attrs 会变指纹 → 走新构建路径（正好验证模板层剥离）
        built = asyncio.run(_build(_seed["a1"]))
        v = built["item"]["Visible"][WPT]
        assert "isPrimaryVariant" not in v  # D-Q63 配套：一律不传
        assert v["variantGroupId"] == f"VG{_seed['ga']}"  # 正常注入不受影响
