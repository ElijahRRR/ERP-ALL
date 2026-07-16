"""R2-06 增量 1：定价引擎纯函数保真锚点（纯单元，无 DB）。

每组断言对应考古移植清单（.agent/evidence/R2-06/archaeology.md）一条：
- parse_multiplier：'275%' / 千分位 / 垃圾值返回 None 绝不抛异常
  （2026-06-11 +cells-get 格式化值整批误淘汰事故修复语义，pricing.py:31-54）
- parse_money：Free→0.0 / N/A→None（pricing.py:57-69）
- source_total：current 优先于 buybox（BR-PR-002，2026-05-14 校正）；运费缺视 0；
  新系统 price_snapshot 'list' 形态直取；数字 0 不误判空（不带 `a or b` 旧债）
- compute_price：区间低含高不含（pricing.py:244-246）；out_of_band / below_min_price /
  min_price_required 三种不出价（fail-closed，001/06:173 min_price 硬底线必填）
- compute_price_clamped：区间外仍出价 + out_of_band 标记（pricing.py:253-302 展示/上架分离）
- price_changed：BR-PR-006 价差 < $0.01 跳过
- exceeds_confirm_threshold：BR-PR-008 30% 确认阈值（old=None/0 首次定价不需确认）
- guard_manual_price：match/manual 人工指定价只过 min_price 守护（D-Q23）
"""

import json
from typing import Any

from erp.pricing import (
    compute_price,
    compute_price_clamped,
    exceeds_confirm_threshold,
    guard_manual_price,
    parse_money,
    parse_multiplier,
    price_changed,
    source_total,
)

# legacy 实表默认模板（考古口径 7：FBA 0-30/30-80、FBM 15-80/80-1000）
PARAMS: dict[str, Any] = {
    "bands": {
        "FBA": [[0, 30, 2.75], [30, 80, 2.5]],
        "FBM": [[15, 80, 2.75], [80, 1000, 2.2]],
    },
    "min_price": 6.99,
}
PARAMS_NO_MIN: dict[str, Any] = {"bands": PARAMS["bands"]}


class TestParseMultiplier:
    def test_number(self) -> None:
        assert parse_multiplier(2.75) == 2.75
        assert parse_multiplier(2) == 2.0

    def test_numeric_string(self) -> None:
        assert parse_multiplier("2.75") == 2.75

    def test_percent_string(self) -> None:
        """'275%' → 2.75（+cells-get 返回格式化显示值，事故根因）。"""
        assert parse_multiplier("275%") == 2.75

    def test_thousand_separator_percent(self) -> None:
        assert parse_multiplier("1,000%") == 10.0

    def test_garbage_returns_none_never_raises(self) -> None:
        """不可解析一律 None（静默跳过语义），绝不抛 ValueError 整批误淘汰。"""
        assert parse_multiplier("abc") is None
        assert parse_multiplier("N/A") is None
        assert parse_multiplier("%") is None

    def test_empty_and_none(self) -> None:
        assert parse_multiplier(None) is None
        assert parse_multiplier("") is None
        assert parse_multiplier("   ") is None


class TestParseMoney:
    def test_number_and_string(self) -> None:
        assert parse_money(25.99) == 25.99
        assert parse_money(12) == 12.0
        assert parse_money("25.99") == 25.99
        assert parse_money("$25.99") == 25.99

    def test_free_means_zero(self) -> None:
        assert parse_money("Free") == 0.0
        assert parse_money("FREE SHIPPING") == 0.0
        assert parse_money("免运") == 0.0

    def test_na_none_empty(self) -> None:
        assert parse_money("N/A") is None
        assert parse_money("No Featured Offer") is None
        assert parse_money("None") is None
        assert parse_money("") is None
        assert parse_money(None) is None

    def test_thousand_separator(self) -> None:
        assert parse_money("$1,299.99") == 1299.99

    def test_no_digits(self) -> None:
        assert parse_money("abc") is None


class TestSourceTotal:
    def test_list_shape_direct(self) -> None:
        """新系统 price_snapshot 现状仅 'list' 键：直接用，视作已含运费的源价。"""
        assert source_total({"list": 19.99}) == 19.99
        assert source_total({"list": "19.99"}) == 19.99

    def test_list_unparseable(self) -> None:
        assert source_total({"list": "N/A"}) is None

    def test_current_price_wins_over_buybox(self) -> None:
        """BR-PR-002：current 优先（2026-05-14 校正；buybox 优先为移植失真不带）。"""
        snap = {"current_price": "$10.00", "buybox_price": "$12.00", "buybox_shipping": "$2.50"}
        assert source_total(snap) == 12.50

    def test_buybox_fallback_when_current_missing(self) -> None:
        assert source_total({"current_price": "N/A", "buybox_price": 11.0}) == 11.0

    def test_zero_current_not_treated_as_missing(self) -> None:
        """数字 0 不误判空（不带 `a or b` 旧债）：current=0 不回落 buybox。"""
        assert source_total({"current_price": 0, "buybox_price": 12.0}) == 0.0

    def test_missing_or_free_shipping_is_zero(self) -> None:
        assert source_total({"current_price": 10.0}) == 10.0
        assert source_total({"current_price": 10.0, "buybox_shipping": "Free"}) == 10.0

    def test_unpriceable_returns_none(self) -> None:
        assert source_total({}) is None
        assert source_total({"current_price": "N/A", "buybox_price": None}) is None

    def test_rounds_to_cents(self) -> None:
        assert source_total({"current_price": 10.333, "buybox_shipping": 1.111}) == 11.44


class TestComputePrice:
    def test_in_band_fba(self) -> None:
        res = compute_price(25.0, fulfillment="FBA", params=PARAMS)
        assert res.ok and res.price == 68.75 and res.reason is None
        assert res.detail["band"] == [0.0, 30.0]
        assert res.detail["multiplier"] == 2.75
        assert res.detail["total"] == 25.0
        assert "68.75" in res.detail["formula"]

    def test_detail_is_json_serializable(self) -> None:
        """detail 进 price_history.detail（JSONB，001/06:179 契约）。"""
        res = compute_price(25.0, fulfillment="FBA", params=PARAMS)
        json.dumps(res.detail)

    def test_band_low_inclusive_high_exclusive(self) -> None:
        """low <= total < high：30 落第二段（低含），80 已出界（高不含）。"""
        res = compute_price(30.0, fulfillment="FBA", params=PARAMS)
        assert res.ok and res.price == 75.0 and res.detail["band"] == [30.0, 80.0]
        res = compute_price(80.0, fulfillment="FBA", params=PARAMS)
        assert not res.ok and res.reason == "out_of_band"

    def test_out_of_band_no_quote(self) -> None:
        """区间外不出价（上架链硬边界，pricing.py:249-250）。"""
        res = compute_price(100.0, fulfillment="FBA", params=PARAMS)
        assert not res.ok and res.price is None and res.reason == "out_of_band"
        assert res.detail["bands"] == [[0.0, 30.0], [30.0, 80.0]]

    def test_below_min_price_no_quote(self) -> None:
        """算出价 < min_price → 不出价（fail-closed），detail 保留被拒价格可追溯。"""
        res = compute_price(2.0, fulfillment="FBA", params=PARAMS)
        assert not res.ok and res.price is None and res.reason == "below_min_price"
        assert res.detail["price"] == 5.5
        assert res.detail["min_price"] == 6.99

    def test_min_price_required_fail_closed(self) -> None:
        """缺 min_price 即便区间内也不出价（001/06:173 硬底线必填）。"""
        res = compute_price(25.0, fulfillment="FBA", params=PARAMS_NO_MIN)
        assert not res.ok and res.price is None and res.reason == "min_price_required"

    def test_multiplier_percent_string_in_params(self) -> None:
        """params 里的倍数是 '275%' 字符串也能算（防御解析下沉到引擎）。"""
        params = {"bands": {"FBA": [[0, 30, "275%"]]}, "min_price": 6.99}
        res = compute_price(20.0, fulfillment="FBA", params=params)
        assert res.ok and res.price == 55.0

    def test_garbage_multiplier_band_skipped_not_raise(self) -> None:
        """垃圾倍数只跳过该段（2026-06-11 语义），其余段照常可用。"""
        params = {"bands": {"FBA": [[0, 30, "abc"], [30, 80, 2.5]]}, "min_price": 6.99}
        res = compute_price(10.0, fulfillment="FBA", params=params)
        assert not res.ok and res.reason == "out_of_band"
        res = compute_price(40.0, fulfillment="FBA", params=params)
        assert res.ok and res.price == 100.0

    def test_no_bands_for_fulfillment(self) -> None:
        res = compute_price(25.0, fulfillment="WFS", params=PARAMS)
        assert not res.ok and res.reason == "no_bands"

    def test_fulfillment_case_insensitive(self) -> None:
        res = compute_price(25.0, fulfillment="fba", params=PARAMS)
        assert res.ok and res.price == 68.75


class TestComputePriceClamped:
    def test_in_band_same_as_compute_price(self) -> None:
        res = compute_price_clamped(40.0, fulfillment="FBM", params=PARAMS)
        strict = compute_price(40.0, fulfillment="FBM", params=PARAMS)
        assert res.ok and res.price == strict.price == 110.0
        assert res.detail["out_of_band"] is False

    def test_clamp_low_uses_lowest_band(self) -> None:
        """区间外(低)：用最低区间倍数照常出价 + out_of_band 标记（展示用）。"""
        res = compute_price_clamped(10.0, fulfillment="FBM", params=PARAMS)
        assert res.ok and res.price == 27.5
        assert res.detail["out_of_band"] is True and res.detail["clamp"] == "low"
        assert "区间外(低)" in res.detail["formula"]

    def test_clamp_high_uses_highest_band(self) -> None:
        res = compute_price_clamped(2000.0, fulfillment="FBM", params=PARAMS)
        assert res.ok and res.price == 4400.0
        assert res.detail["out_of_band"] is True and res.detail["clamp"] == "high"

    def test_strict_version_rejects_same_input(self) -> None:
        """双函数分工对照：同一区间外输入，上架链 compute_price 必须不出价。"""
        assert not compute_price(10.0, fulfillment="FBM", params=PARAMS).ok
        assert not compute_price(2000.0, fulfillment="FBM", params=PARAMS).ok

    def test_no_bands_cannot_clamp(self) -> None:
        res = compute_price_clamped(25.0, fulfillment="WFS", params=PARAMS)
        assert not res.ok and res.reason == "no_bands"

    def test_below_min_price_only_flagged_not_blocked(self) -> None:
        """展示链不拦截低于 min_price 的价，仅 detail 打标供前端提示。"""
        params = {"bands": PARAMS["bands"], "min_price": 100.0}
        res = compute_price_clamped(15.0, fulfillment="FBM", params=params)
        assert res.ok and res.price == 41.25
        assert res.detail["below_min_price"] is True


class TestPriceChanged:
    def test_equal_price_not_changed(self) -> None:
        assert price_changed(10.00, 10.00) is False

    def test_sub_cent_diff_skipped(self) -> None:
        """BR-PR-006：价差 < $0.01 跳过省配额。"""
        assert price_changed(10.00, 10.005) is False

    def test_one_cent_boundary_is_changed(self) -> None:
        """恰好 1 美分必须判变——含浮点陷阱（19.99-19.98 二进制 ≈ 0.00999…）。"""
        assert price_changed(10.00, 10.01) is True
        assert price_changed(19.99, 19.98) is True

    def test_first_price_always_changed(self) -> None:
        assert price_changed(None, 9.99) is True

    def test_custom_threshold(self) -> None:
        assert price_changed(10.0, 10.05, threshold=0.1) is False
        assert price_changed(10.0, 10.10, threshold=0.1) is True


class TestExceedsConfirmThreshold:
    def test_exactly_30_percent_not_exceeded(self) -> None:
        """严格「超过」：恰好 30% 不触发确认。"""
        assert exceeds_confirm_threshold(10.0, 13.0) is False
        assert exceeds_confirm_threshold(10.0, 7.0) is False

    def test_over_30_percent_exceeded(self) -> None:
        assert exceeds_confirm_threshold(10.0, 13.01) is True
        assert exceeds_confirm_threshold(10.0, 6.99) is True

    def test_first_pricing_never_needs_confirm(self) -> None:
        """old=None/0（首次定价）不需确认（BR-PR-008）。"""
        assert exceeds_confirm_threshold(None, 99.0) is False
        assert exceeds_confirm_threshold(0.0, 99.0) is False

    def test_custom_pct(self) -> None:
        assert exceeds_confirm_threshold(10.0, 11.5, pct=0.10) is True
        assert exceeds_confirm_threshold(10.0, 11.0, pct=0.10) is False


class TestGuardManualPrice:
    def test_manual_price_passes_guard(self) -> None:
        """match/manual：人工指定价不经 compute_price，原样过 min_price 守护。"""
        res = guard_manual_price(19.99, PARAMS)
        assert res.ok and res.price == 19.99
        assert res.detail["algo"] == "manual"

    def test_manual_below_min_price_rejected(self) -> None:
        res = guard_manual_price(5.0, PARAMS)
        assert not res.ok and res.price is None and res.reason == "below_min_price"

    def test_manual_min_price_required(self) -> None:
        res = guard_manual_price(5.0, PARAMS_NO_MIN)
        assert not res.ok and res.reason == "min_price_required"

    def test_manual_at_min_price_boundary_ok(self) -> None:
        res = guard_manual_price(6.99, PARAMS)
        assert res.ok and res.price == 6.99
