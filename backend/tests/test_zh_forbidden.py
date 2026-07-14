"""seed 禁售规则匹配语义验收（源仓 yaml 保真移植，纯单元不碰 DB）。

excluded=小写子串（amazon_category/walmart_pt 两 scope）；
mega pt_contains=词边界+可选复数 's'（"bra" 中 "Bras" 不中 "Brackets"）；
category_prefix=小写前缀。数据=13 excluded + 18 mega（机械转换自源仓 yaml）。
"""

from erp.audit import zh_forbidden


def test_data_loaded() -> None:
    data = zh_forbidden._load()
    assert len(data["excluded_categories"]) == 13
    assert len(data["mega_forbidden_categories"]) == 18


def test_excluded_amazon_category_substring() -> None:
    # yaml: match="Consumer Electronics" scope=amazon_category reason=3C 禁售
    r = zh_forbidden.check_excluded(
        category_path="Electronics > Consumer Electronics > Gadgets", title=None
    )
    assert r == "3C 禁售"


def test_excluded_walmart_pt_substring() -> None:
    # yaml: match="Laptops" scope=walmart_pt
    assert zh_forbidden.check_excluded(
        category_path=None, title=None, walmart_pt="Gaming Laptops"
    ) is not None


def test_excluded_no_hit() -> None:
    assert zh_forbidden.check_excluded(
        category_path="Home & Kitchen > Mugs", title="Plain Ceramic Mug"
    ) is None


def test_mega_word_boundary_and_plural() -> None:
    hit = zh_forbidden.match_mega("Blood Pressure Monitors")  # 复数 s
    assert hit is not None
    assert hit["key"] == "medical"
    assert hit["matched_by"] == "pt_contains"


def test_mega_word_boundary_no_false_positive() -> None:
    # 源仓修过的 bug：'bra' 不得误中 'Brackets'
    hit = zh_forbidden.match_mega("Shelf Brackets")
    assert hit is None or hit["matched_term"] != "bra"


def test_mega_category_prefix() -> None:
    # 找一条带 category_prefix 的规则做前缀验证
    data = zh_forbidden._load()
    entry = next(
        (m for m in data["mega_forbidden_categories"] if m.get("walmart_category_prefix")),
        None,
    )
    if entry is None:  # 数据无前缀规则则跳过语义验证
        return
    pref = entry["walmart_category_prefix"][0]
    hit = zh_forbidden.match_mega(None, walmart_category=pref + " Something")
    assert hit is not None
    assert hit["matched_by"] == "walmart_category_prefix"


def test_mega_none_inputs() -> None:
    assert zh_forbidden.match_mega(None, None) is None
    assert zh_forbidden.match_mega("", "") is None
