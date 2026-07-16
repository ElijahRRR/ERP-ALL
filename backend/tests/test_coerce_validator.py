"""R2-03 增量 4：清洗链/校验器保真锚点（纯单元，无 DB）。

每组断言对应源仓一类实测拒绝：
- safe_default_for：安全序列/URL 数组禁占位/对象递归（unit/measure/netContent 特例）
- fill_known_walmart_required：allOf 条件必填 + 白名单 + 不动点级联（2026-06-20 教训）
- fix_type_mismatches：字符串包 array（50716566635066）/URL 占位删（49505365506868）/
  对象 enum 子字段修（IB.VALIDATION.DATA.001）
- fix_invalid_enums：替换/enum[0]/删；数组逐元素
- clean_state_restrictions / drop_min_items / enforce_copy_limits / round_decimals
- force_amazon_copy：bullets→keyFeatures 补齐 4 条 / 60 词 title 补 / 品牌全词剥除
- validator：必填空/枚举/长度/endDate ISO 报 error；PartnerID 缺失报 warning；match 五字段
"""

from typing import Any

from erp.listing import coerce, validator
from erp.listing.wpt_schema import PtSchema


def _schema(props: dict[str, Any], required: list[str], all_of: list | None = None) -> PtSchema:
    raw: dict[str, Any] = {"type": "object", "properties": props, "required": required}
    if all_of:
        raw["allOf"] = all_of
    return PtSchema(name="T", raw=raw)


class TestSafeDefault:
    def test_enum_safe_sequence(self) -> None:
        assert coerce.safe_default_for({"type": "string", "enum": ["A", "No", "B"]}) == "No"
        assert coerce.safe_default_for({"type": "string", "enum": ["A", "B"]}) == "A"

    def test_url_array_never_placeholder(self) -> None:
        node = {"type": "array", "items": {"type": "string", "format": "uri"}}
        assert coerce.safe_default_for(node) is None

    def test_array_items_enum_no_warning_pick(self) -> None:
        node = {
            "type": "array",
            "items": {"enum": ["3 - Contains small parts", "0 - No warning applicable"]},
        }
        assert coerce.safe_default_for(node) == ["0 - No warning applicable"]

    def test_object_recursion_unit_measure(self) -> None:
        node = {
            "type": "object",
            "properties": {
                "measure": {"type": "number"},
                "unit": {"type": "string", "enum": ["cm", "in"]},
            },
        }
        assert coerce.safe_default_for(node) == {"measure": 1, "unit": "cm"}


class TestConditionalRequired:
    def test_fixpoint_cascade(self) -> None:
        """白名单填 powerType 后才触发的第二层条件必填也要补上（不动点教训）。"""
        schema = _schema(
            {
                "powerType": {"type": "array", "items": {"enum": ["Battery", "Corded"]}},
                "batterySize": {"type": "string", "enum": ["AA", "AAA"]},
                "displayTechnology": {"type": "string", "enum": ["LCD", "LED"]},
            },
            [],
            all_of=[
                {
                    "if": {"properties": {"powerType": {"contains": {"enum": ["Battery"]}}}},
                    "then": {"required": ["displayTechnology"]},
                }
            ],
        )
        visible, filled = coerce.fill_known_walmart_required({}, schema)
        assert visible["powerType"] == ["Battery"]  # 白名单
        assert visible["displayTechnology"] == "LCD"  # 级联条件（第二轮才触发）
        assert visible["batterySize"] == "AA"  # 白名单
        assert any("条件触发" in f for f in filled)

    def test_evaluate_if_required_and_enum(self) -> None:
        assert coerce._evaluate_if_condition({"required": ["x"]}, {"x": "v"}) is True
        assert coerce._evaluate_if_condition({"required": ["x"]}, {"x": ""}) is False
        cond = {"properties": {"t": {"enum": ["Yes"]}}}
        assert coerce._evaluate_if_condition(cond, {"t": "Yes"}) is True
        assert coerce._evaluate_if_condition(cond, {"t": "No"}) is False


class TestTypeAndEnumFixes:
    def test_string_wrapped_to_array(self) -> None:
        schema = _schema({"pattern": {"type": "array", "items": {"type": "string"}}}, [])
        v, fixes = coerce.fix_type_mismatches({"pattern": "Striped"}, schema)
        assert v["pattern"] == ["Striped"] and fixes

    def test_url_array_placeholder_dropped(self) -> None:
        schema = _schema(
            {"swatchImageUrl": {"type": "array", "items": {"type": "string", "format": "uri"}}}, []
        )
        v, _ = coerce.fix_type_mismatches({"swatchImageUrl": "No"}, schema)
        assert "swatchImageUrl" not in v
        v2, _ = coerce.fix_type_mismatches({"swatchImageUrl": ["No", "https://x/1.jpg"]}, schema)
        assert v2["swatchImageUrl"] == ["https://x/1.jpg"]

    def test_object_sub_enum_fixed(self) -> None:
        schema = _schema(
            {
                "rollLength": {
                    "type": "object",
                    "properties": {
                        "unit": {"type": "string", "enum": ["yd", "m"]},
                        "measure": {"type": "number"},
                    },
                }
            },
            [],
        )
        v, fixes = coerce.fix_type_mismatches({"rollLength": {"unit": "ft", "measure": 3}}, schema)
        assert v["rollLength"]["unit"] == "yd" and fixes

    def test_invalid_enum_replaced_with_safe(self) -> None:
        schema = _schema(
            {
                "pleat": {"type": "string", "enum": ["Pleated", "Tab Top"]},
                "extra": {"type": "string", "enum": ["A", "No"]},
            },
            ["pleat"],
        )
        v, _ = coerce.fix_invalid_enums({"pleat": "Pinch Pleat", "extra": "C"}, schema)
        assert v["pleat"] == "Pleated"  # safe_default 落 enum[0]
        assert v["extra"] == "No"  # 安全序列优先（源仓：非必填也替换，删除仅无可替换时）

    def test_array_enum_elements_cleaned(self) -> None:
        schema = _schema({"material": {"type": "array", "items": {"enum": ["Metal", "Glass"]}}}, [])
        v, _ = coerce.fix_invalid_enums({"material": ["Metal", "Vibranium"]}, schema)
        assert v["material"] == ["Metal"]


class TestCleanups:
    def test_state_restrictions_placeholder_dropped(self) -> None:
        o, notes = coerce.clean_state_restrictions(
            {
                "stateRestrictions": [
                    {"stateRestrictionsText": "None", "states": "AK", "zipCodes": ""},
                    {"stateRestrictionsText": "Illegal for Sale", "states": "CA", "zipCodes": ""},
                ]
            }
        )
        assert len(o["stateRestrictions"]) == 1 and notes

    def test_drop_min_items_optional_only(self) -> None:
        schema = _schema(
            {
                "productSecondaryImageURL": {"type": "array", "minItems": 5},
                "keyFeatures": {"type": "array", "minItems": 3},
            },
            ["keyFeatures"],
        )
        v, _o, dropped = coerce.drop_min_items_violations(
            {"productSecondaryImageURL": ["a", "b"], "keyFeatures": ["x"]}, {}, schema, None
        )
        assert "productSecondaryImageURL" not in v  # 非必填 <minItems 删
        assert v["keyFeatures"] == ["x"]  # 必填留给校验报错
        assert dropped

    def test_copy_limits_truncation(self) -> None:
        v, warnings = coerce.enforce_copy_limits(
            {
                "productName": "x" * 250,
                "manufacturer": "m" * 80,
                "keyFeatures": [f"f{i}" * 300 for i in range(9)],
            }
        )
        assert len(v["productName"]) <= 199
        assert len(v["manufacturer"]) <= 60
        assert len(v["keyFeatures"]) == 7
        assert all(len(k) <= 500 for k in v["keyFeatures"])
        assert any("EXT_DATA_ERROR_01076067496949" in w for w in warnings)

    def test_round_decimals(self) -> None:
        v, o, fixes = coerce.round_decimals_to_2(
            {"depth": {"measure": 1.2345}}, {"ShippingWeight": 12.500001}
        )
        assert v["depth"]["measure"] == 1.23
        assert o["ShippingWeight"] == 12.5
        assert len(fixes) == 2

    def test_sanitize_feed_numbers_boundary(self) -> None:
        feed, fixes = coerce.sanitize_feed_numbers({"MPItem": [{"Orderable": {"price": 19.999}}]})
        assert feed["MPItem"][0]["Orderable"]["price"] == 20.0 and fixes


class TestForceAmazonCopy:
    def test_bullets_and_padding_and_scrub(self) -> None:
        amazon = {
            "title": "FOTOSOK Camping Lantern Bright",
            "bullet_points": ["Great FOTOSOK lantern for camping trips and outdoor fun"],
            "description": (
                "This lantern is durable and waterproof. It runs for twenty hours straight. "
                "Perfect gift for hikers and campers everywhere."
            ),
        }
        v = coerce.force_amazon_copy({}, amazon, ["FOTOSOK"])
        assert "FOTOSOK" not in v["productName"]
        assert v["productName"].startswith("Camping Lantern")
        assert len(v["keyFeatures"]) >= 4  # 不足 4 条拆句补齐
        assert all("FOTOSOK" not in k for k in v["keyFeatures"])
        assert v["shortDescription"].startswith("Camping Lantern")  # <60 词拼 title


class TestValidator:
    def _schemas(self) -> tuple[PtSchema, PtSchema]:
        vis = _schema(
            {
                "shortDescription": {"type": "string", "maxLength": 20},
                "color": {"type": "string", "enum": ["Red", "Blue"]},
            },
            ["shortDescription"],
        )
        order = _schema({"sku": {"type": "string"}, "price": {"type": "number"}}, ["sku", "price"])
        return vis, order

    def test_errors_required_enum_length_enddate(self) -> None:
        vis, order = self._schemas()
        item = {
            "Visible": {"T": {"shortDescription": "x" * 30, "color": "Green"}},
            "Orderable": {"sku": "M1", "endDate": "2049-12-31"},
        }
        r = validator.validate_build_item(item, "T", vis, order)
        assert not r.ok
        joined = "\n".join(r.errors)
        assert "maxLength" in joined and "枚举" in joined
        assert "orderable.price: 必填缺失" in joined
        assert "EXT_DATA_ERROR_00030257670757" in joined  # endDate 纯日期

    def test_ok_with_practice_warnings(self) -> None:
        vis, order = self._schemas()
        item = {
            "Visible": {"T": {"shortDescription": "short desc", "color": "Red"}},
            "Orderable": {
                "sku": "M1",
                "price": 9.99,
                "endDate": "2049-12-31T00:00:00Z",
                "inventory": [{"quantity": 5}],
            },
        }
        r = validator.validate_build_item(item, "T", vis, order)
        assert r.ok
        assert any("fulfillmentCenterID" in w for w in r.warnings)

    def test_schema_missing_warns_not_errors(self) -> None:
        r = validator.validate_build_item(
            {"Visible": {"T": {}}, "Orderable": {"price": 9.99}}, "T", None, None
        )
        assert r.ok and len(r.warnings) == 2

    def test_zero_price_blocked(self) -> None:
        """HF-0716 真机：0/缺失价出门被渠道 CAP 拒（EXT_DATA_ERROR_66685355746773）
        ——本地必须拦截。"""
        for bad_price in (0.0, None, -1):
            r = validator.validate_build_item(
                {"Visible": {"T": {}}, "Orderable": {"price": bad_price}}, "T", None, None
            )
            assert not r.ok and any("CAP" in e for e in r.errors), bad_price
        bad_match = {
            "Item": {
                "sku": "M1",
                "ShippingWeight": 1.0,
                "price": 0.0,
                "condition": "New",
                "productIdentifiers": {"productIdType": "UPC", "productId": "123456789012"},
            }
        }
        r = validator.validate_match_item(bad_match)
        assert not r.ok and any("CAP" in e for e in r.errors)

    def test_match_validation(self) -> None:
        good = {
            "Item": {
                "sku": "M1",
                "ShippingWeight": 1.0,
                "price": 9.99,
                "condition": "New",
                "productIdentifiers": {"productIdType": "UPC", "productId": "123456789012"},
            }
        }
        assert validator.validate_match_item(good).ok
        bad = {"Item": {"sku": "M1", "productIdentifiers": {"productIdType": "ASIN"}}}
        r = validator.validate_match_item(bad)
        assert not r.ok
        assert any("productIdType" in e for e in r.errors)


class TestDateFormats:
    """fix_date_formats + 日期校验（2026-07-16 真机 Invalid Date 拒收教训）。"""

    def _schemas(self) -> tuple[PtSchema, PtSchema]:
        vis = _schema(
            {
                "releaseDate": {"type": "string", "format": "date"},
                "warrantyStart": {"type": "string", "format": "date-time"},
                "mandatoryDate": {"type": "string", "format": "date"},
            },
            ["mandatoryDate"],
        )
        order = _schema({"startDate": {"type": "string", "format": "date-time"}}, [])
        return vis, order

    def test_parseable_normalized_unparseable_dropped_or_kept(self) -> None:
        vis, order = self._schemas()
        v, o, notes = coerce.fix_date_formats(
            {
                "releaseDate": "2024-05-03T10:00:00Z",  # datetime → 截断为 date
                "warrantyStart": "2024-05-03",  # date → 提升 .000Z（2049 实测成功写法）
                "mandatoryDate": "March 2024",  # 必填不可解析 → 保留交校验器
            },
            {"startDate": "{START_DATE}"},  # 模板占位符 → 原样跳过
            vis,
            order,
        )
        assert v["releaseDate"] == "2024-05-03"
        assert v["warrantyStart"] == "2024-05-03T00:00:00.000Z"
        assert v["mandatoryDate"] == "March 2024"
        assert o["startDate"] == "{START_DATE}"
        assert any("mandatoryDate" in n for n in notes)

    def test_optional_garbage_dropped(self) -> None:
        vis, order = self._schemas()
        v, _o, notes = coerce.fix_date_formats(
            {"releaseDate": "N/A", "mandatoryDate": "2024-01-01"}, {}, vis, order
        )
        assert "releaseDate" not in v
        assert v["mandatoryDate"] == "2024-01-01"
        assert any("releaseDate" in n for n in notes)

    def test_no_fabricated_date_defaults(self) -> None:
        """必填日期缺失：不塞 'Not Available'（渠道必拒），留给校验器报必填缺失。"""
        assert coerce.safe_default_for({"type": "string", "format": "date"}) is None
        vis, _ = self._schemas()
        filled, _notes = coerce.fill_missing_required({}, vis)
        assert "mandatoryDate" not in filled

    def test_validator_flags_bad_dates_and_placeholder_leak(self) -> None:
        vis, order = self._schemas()
        item = {
            "Visible": {"T": {"mandatoryDate": "March 2024", "warrantyStart": "2024"}},
            "Orderable": {
                "sku": "M1",
                "price": 9.99,
                "startDate": "{START_DATE}",
                "endDate": "2049-12-31T00:00:00.000Z",
            },
        }
        r = validator.validate_build_item(item, "T", vis, order)
        assert not r.ok
        joined = "\n".join(r.errors)
        assert "mandatoryDate" in joined and "warrantyStart" in joined  # 非法日期
        assert "占位符" in joined or "startDate" in joined  # 泄漏必须报出
        # 合法毫秒写法不误伤
        assert "endDate" not in joined

    def test_validator_accepts_both_iso_datetime_styles(self) -> None:
        vis = _schema({}, [])
        order = _schema({}, [])
        for good in ("2049-12-31T00:00:00Z", "2049-12-31T00:00:00.000Z"):
            item = {"Visible": {"T": {}}, "Orderable": {"price": 9.99, "endDate": good}}
            r = validator.validate_build_item(item, "T", vis, order)
            assert r.ok, (good, r.errors)
