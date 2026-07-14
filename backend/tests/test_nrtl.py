"""NRTL 整机/小件分类器语义验收（源仓 nrtl_classifier 保真移植，纯单元）。"""

from erp.audit.nrtl import classify_nrtl_pt


def test_replacement_forces_small_part() -> None:
    # Replacement/Parts/Accessor 覆盖性关键词 → 强制小件（即使含整机词）
    assert classify_nrtl_pt("Ceiling Fan Replacement Parts") == "small_part"


def test_default_whole_unit() -> None:
    # 未命中任一关键词 → 保守整机（保持硬拒）
    assert classify_nrtl_pt("Totally Unknown Widget") == "whole_unit"
    assert classify_nrtl_pt("") == "whole_unit"
    assert classify_nrtl_pt(None) == "whole_unit"


def test_keywords_loaded() -> None:
    from erp.audit.nrtl import _keywords

    small, whole = _keywords()
    assert len(small) == 42
    assert len(whole) == 46
