"""R2-03 增量 2 验收：spec 构建器真实化（MP_ITEM v5 + MP_ITEM_MATCH v4.2）。

- header：build=3 字段+完整时间戳 version（BR-LST-005，多一个字段都拒）；match=v4.2 三字段
- Orderable 强制格式：productIdentifiers 单对象（UPC/EAN 按位数）、price 裸 number、
  inventory=[{quantity, fulfillmentCenterID}]、endDate ISO DateTime（BR-LST-006/007）
- Visible：brand=Unbranded（BR-CAT-005）、副图 ≥5 才写（schema minItems 实测规则）、
  零认证覆盖只对 PT spec 存在的字段强制 + enum 安全回退 + cert_overrides 记录（BR-AUD-006）
- WPT 链：attrs.wpt > L1 直判（与审核同语义）> 默认配置；类目硬拒 fail-closed
- match：identifier 预检 fail-closed（BR-LST-015）
- 缓存：同输入复用 spec 行；pt_spec dataset_revision 变更失效
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

PREFIX = "ZSPEC"
WPT = f"{PREFIX}_Drinkware"
EAN = "6901234567892"
UPC = "123456789012"


def _sessions():  # type: ignore[no-untyped-def]
    return async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)


@pytest.fixture(scope="module", autouse=True)
def _seed(migrated_db: str):  # type: ignore[no-untyped-def]
    def _wipe(conn) -> None:  # type: ignore[no-untyped-def]
        like = (f"{PREFIX}%",)
        conn.execute("DELETE FROM refdata.category_map WHERE amazon_category LIKE %s", like)
        conn.execute("DELETE FROM refdata.pt_meta WHERE walmart_product_type LIKE %s", like)
        conn.execute("DELETE FROM refdata.pt_spec WHERE walmart_product_type LIKE %s", like)
        team = conn.execute("SELECT id FROM app.team WHERE name = 'spec构建测试团队'").fetchone()
        if team:
            conn.execute("DELETE FROM app.listing_spec WHERE team_id = %s", (team[0],))
            conn.execute("DELETE FROM app.product WHERE team_id = %s", (team[0],))

    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _wipe(conn)
        conn.execute(
            "INSERT INTO app.team (name) VALUES ('spec构建测试团队') ON CONFLICT DO NOTHING"
        )
        team_id = conn.execute(
            "SELECT id FROM app.team WHERE name = 'spec构建测试团队'"
        ).fetchone()[0]
        # 可售 PT + 直判映射（L1 同语义）
        conn.execute(
            "INSERT INTO refdata.pt_meta (walmart_product_type, walmart_category,"
            " zh_seller_forbidden, access_state, zh_can_do) VALUES (%s, 'Home', false,"
            " '普通商品', '是')",
            (WPT,),
        )
        conn.execute(
            "INSERT INTO refdata.pt_meta (walmart_product_type, walmart_category,"
            " zh_seller_forbidden, access_state, zh_can_do) VALUES (%s, 'Home', true,"
            " '普通商品', '是')",
            (f"{PREFIX}_Forbidden",),
        )
        conn.execute(
            "INSERT INTO refdata.category_map (amazon_category, walmart_product_type)"
            " VALUES (%s, %s), (%s, %s)",
            (f"{PREFIX}/Home/Mugs", WPT, f"{PREFIX}/Bad/Ammo", f"{PREFIX}_Forbidden"),
        )
        # PT spec（fields 原始节点，含真实 PT 都有的文案/图片字段——清洗链 strip_unknown
        # 只保留 schema 内字段）：certification_type enum 含目标值；has_written_warranty
        # enum 不含 'No'（验安全序列回退）；has_nrtl_listing_certification 不在此 PT
        # （验"只对存在的字段强制"）
        pt_fields = {
            "type": "object",
            "required": ["shortDescription"],
            "properties": {
                "shortDescription": {"type": "string", "maxLength": 4000},
                "productName": {"type": "string", "maxLength": 199},
                "brand": {"type": "string"},
                "mainImageUrl": {"type": "string", "format": "uri"},
                "productSecondaryImageURL": {
                    "type": "array",
                    "minItems": 5,
                    "items": {"type": "string", "format": "uri"},
                },
                "keyFeatures": {"type": "array", "minItems": 3, "items": {"type": "string"}},
                "certification_type": {
                    "type": "string",
                    "enum": [
                        "Children's Product Certificate",
                        "General Certificate of Conformity",
                        "Neither of these applies",
                    ],
                },
                "has_written_warranty": {
                    "type": "string",
                    "enum": ["Yes - Warranty Text", "Skip for now"],
                },
                "isAssemblyRequired": {"type": "string", "enum": ["Yes", "No"]},
            },
        }
        conn.execute(
            "INSERT INTO refdata.pt_spec (walmart_product_type, fields) VALUES (%s, %s::jsonb)",
            (WPT, json.dumps(pt_fields)),
        )
        pids = {}
        attrs_by_ref: dict[str, dict[str, Any]] = {
            "SPEC001": {
                "wpt": WPT,
                "bullets": ["solid build", "easy clean", "large size", "food safe"],
                "description": "d",
                "shipping_weight": 2.5,
            },
            "SPEC002": {"bullets": ["x"]},
            "SPEC003": {"bullets": ["x"]},
            "SPEC004": {"upc": "123456789012"},
            "SPEC005": {},
        }
        for ref, cat in (
            ("SPEC001", None),
            ("SPEC002", f"{PREFIX}/Home/Mugs"),
            ("SPEC003", f"{PREFIX}/Bad/Ammo"),
            ("SPEC004", None),
            ("SPEC005", None),
        ):
            attrs = json.dumps(attrs_by_ref[ref])
            pids[ref] = conn.execute(
                "INSERT INTO app.product (team_id, source_channel, source_ref, title, attrs,"
                " category_path, status) VALUES (%s, 'amazon', %s, %s, %s::jsonb, %s,"
                " 'audit_passed') RETURNING id",
                (team_id, ref, f"Title {ref} " + "x" * 250, attrs, cat),
            ).fetchone()[0]
    yield {"team": team_id, **pids}
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _wipe(conn)


async def _product(ref: str) -> dict[str, Any]:
    sessions = _sessions()
    async with system_tx(sessions) as s:
        from sqlalchemy import text

        row = (
            (
                await s.execute(
                    text(
                        "SELECT id, team_id, title, brand, images, attrs, price_snapshot,"
                        " category_path, amazon_leaf_id FROM app.product WHERE source_ref = :r"
                    ),
                    {"r": ref},
                )
            )
            .mappings()
            .one()
        )
        return dict(row)


async def _build(ref: str, **kw: Any) -> dict[str, Any]:
    product = await _product(ref)
    if images := kw.pop("images", None):
        product["images"] = images
    sessions = _sessions()
    async with system_tx(sessions) as s:
        defaults: dict[str, Any] = {
            "offer_mode": "build",
            "gtin": EAN,
            "channel_sku": "M0000001",
            "price": 19.99,
            "inventory": 5,
        }
        defaults.update(kw)
        return await spec_builder.build_spec(s, product=product, **defaults)


class TestFeedHeader:
    def test_build_header_exactly_three_fields(self) -> None:
        async def _run() -> dict[str, Any]:
            async with system_tx(_sessions()) as s:
                return await spec_builder.feed_header(s, "build")

        h = asyncio.run(_run())
        assert set(h) == {"businessUnit", "locale", "version"}  # 多一个字段都拒（实测）
        assert h["businessUnit"] == "WALMART_US"
        assert h["version"].startswith("5.0.20") and h["version"].endswith("-api")

    def test_match_header_v42(self) -> None:
        async def _run() -> dict[str, Any]:
            async with system_tx(_sessions()) as s:
                return await spec_builder.feed_header(s, "match")

        h = asyncio.run(_run())
        assert h == {"sellingChannel": "mpsetupbymatch", "version": "4.2", "locale": "en"}


class TestBuildItem:
    def test_orderable_forced_formats(self, _seed: dict) -> None:
        imgs = [f"https://img/{i}.jpg" for i in range(7)]
        built = asyncio.run(_build("SPEC001", images=imgs, partner_id="10001234"))
        o = built["item"]["Orderable"]
        assert o["productIdentifiers"] == {"productIdType": "EAN", "productId": EAN}  # 单对象
        assert isinstance(o["price"], float) and o["price"] == 19.99  # 裸 number
        assert o["inventory"] == [{"quantity": 5, "fulfillmentCenterID": "10001234"}]
        # D-Q9 + ISO DateTime 带毫秒（BR-LST-006/BR-RET-007：2049 唯一实测成功写法）
        assert o["endDate"] == "2049-12-31T00:00:00.000Z"
        assert "T" in o["startDate"] and o["startDate"].endswith("Z")
        assert o["specProductType"] == WPT
        assert o["country_of_origin_substantial_transformation"] == "China"
        assert o["MustShipAlone"] == "No" and o["fulfillmentLagTime"] == 1
        assert o["brand"] == "Unbranded" and len(o["productName"]) <= 199
        assert o["ShippingWeight"] == 2.5

    def test_visible_base_and_zero_cert(self, _seed: dict) -> None:
        imgs = [f"https://img/{i}.jpg" for i in range(7)]
        built = asyncio.run(_build("SPEC001", images=imgs))
        v = built["item"]["Visible"][WPT]
        assert v["brand"] == "Unbranded"  # BR-CAT-005
        assert v["mainImageUrl"] == imgs[0]
        assert v["productSecondaryImageURL"] == imgs[1:7]  # 6 张 ≥5 写入
        # force_amazon_copy：keyFeatures=Amazon bullets 原文；shortDescription 不足 60 词
        # 拼 title 补（源仓文案链）
        assert v["keyFeatures"] == ["solid build", "easy clean", "large size", "food safe"]
        assert v["shortDescription"].startswith("Title SPEC001")
        assert "solid build" in v["shortDescription"]
        # 零认证覆盖（BR-AUD-006）：目标值在 enum → 原值；不在 → 安全序列回退
        assert v["certification_type"] == "Neither of these applies"
        assert v["has_written_warranty"] == "Skip for now"  # enum 无 'No' → 回退
        assert v["isAssemblyRequired"] == "No"
        assert "has_nrtl_listing_certification" not in v  # 不在该 PT spec，不强制
        # cert_overrides 落 listing_spec
        with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
            co = conn.execute(
                "SELECT cert_overrides FROM app.listing_spec WHERE id = %s",
                (built["spec_id"],),
            ).fetchone()[0]
        assert co["certification_type"] == "Neither of these applies"
        assert co["has_written_warranty"] == "Skip for now"

    def test_secondary_images_under_min_dropped(self, _seed: dict) -> None:
        built = asyncio.run(_build("SPEC001", images=[f"https://img/{i}.jpg" for i in range(4)]))
        v = built["item"]["Visible"][WPT]
        assert "productSecondaryImageURL" not in v  # 3 张副图 <5 不写（minItems 实测）

    def test_gtin_12_digit_is_upc(self, _seed: dict) -> None:
        built = asyncio.run(_build("SPEC001", gtin=UPC))
        pid = built["item"]["Orderable"]["productIdentifiers"]
        assert pid["productIdType"] == "UPC"

    def test_partner_id_absent_omitted(self, _seed: dict) -> None:
        built = asyncio.run(_build("SPEC001"))
        assert built["item"]["Orderable"]["inventory"] == [{"quantity": 5}]


class TestWptChain:
    def test_resolve_from_category_map(self, _seed: dict) -> None:
        built = asyncio.run(_build("SPEC002"))  # 无 attrs.wpt，category_path 直判
        assert built["wpt"] == WPT

    def test_forbidden_category_fail_closed(self, _seed: dict) -> None:
        with pytest.raises(BusinessError) as ei:
            asyncio.run(_build("SPEC003"))
        assert ei.value.code == "ERP_SPEC_BUILD_FAILED"

    def test_no_category_no_default_fail_closed(self, _seed: dict) -> None:
        with pytest.raises(BusinessError) as ei:
            asyncio.run(_build("SPEC005"))
        assert ei.value.code == "ERP_SPEC_BUILD_FAILED"


class TestMatchMode:
    def test_match_item_offer_fields(self, _seed: dict) -> None:
        built = asyncio.run(_build("SPEC004", offer_mode="match", gtin=None))
        item = built["item"]["Item"]
        assert set(item) == {"sku", "productIdentifiers", "price", "ShippingWeight", "condition"}
        assert item["condition"] == "New"
        assert item["productIdentifiers"] == {"productIdType": "UPC", "productId": UPC}
        assert built["spec_version"] == "4.2"

    def test_match_without_identifier_fail_closed(self, _seed: dict) -> None:
        with pytest.raises(BusinessError) as ei:
            asyncio.run(_build("SPEC005", offer_mode="match", gtin=None))
        assert ei.value.code == "ERP_SPEC_BUILD_FAILED"


class TestSpecCache:
    def test_reuse_and_revision_invalidation(self, _seed: dict) -> None:
        b1 = asyncio.run(_build("SPEC001"))
        b2 = asyncio.run(_build("SPEC001"))
        assert b1["spec_id"] == b2["spec_id"] and b1["build_hash"] == b2["build_hash"]
        # pt_spec 任何写入 bump dataset_revision → build_hash 变 → 新 spec 行
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            conn.execute(
                "INSERT INTO refdata.pt_spec (walmart_product_type, total_fields)"
                " VALUES (%s, 1) ON CONFLICT (walmart_product_type)"
                " DO UPDATE SET total_fields = 1",
                (f"{PREFIX}_Bump",),
            )
        b3 = asyncio.run(_build("SPEC001"))
        assert b3["build_hash"] != b1["build_hash"]
