"""L2 R7（促销宣称）+ R8（敏感内容）纯函数单测 + L3 user prompt surface。"""

from erp.audit.l2_content import scan_promotional, scan_sensitive
from erp.audit.pipeline import build_user_prompt

# ── R7 促销宣称 ──


def test_r7_strong_phrase_fires() -> None:
    ev = scan_promotional("Premium Quality Stainless Steel Mug", [])
    assert ev is not None
    assert any("premium" in p.lower() for p in ev["strong_phrases"])  # type: ignore[operator]


def test_r7_soft_only_does_not_fire() -> None:
    # 只命中 soft（high-quality）不触发（避免过激）
    assert scan_promotional("High-Quality Cotton Shirt", []) is None


def test_r7_allcaps_run_fires() -> None:
    ev = scan_promotional("BEST SUPER STRONG DURABLE Widget", [])
    assert ev is not None
    assert ev["allcaps_runs"]  # 连续全大写词


def test_r7_noise_allcaps_not_flagged() -> None:
    # 全大写但都是噪声 token（USB/LED/HD）→ 不算滥用
    assert scan_promotional("USB LED HD Cable", []) is None


def test_r7_empty() -> None:
    assert scan_promotional("", []) is None


def test_r7_scans_bullets() -> None:
    ev = scan_promotional("Plain Mug", ["12oz", "commercial grade steel"])
    assert ev is not None
    assert any("commercial" in p.lower() for p in ev["strong_phrases"])  # type: ignore[operator]


# ── R8 敏感内容 ──


def test_r8_cartoon_ip() -> None:
    ev = scan_sensitive("Mickey Mouse Kids Backpack", [])
    assert ev is not None
    assert "cartoon_ip_character" in ev["subtypes"]  # type: ignore[operator]


def test_r8_political() -> None:
    ev = scan_sensitive("MAGA Trump 2024 Flag", [])
    assert ev is not None
    assert "political_sensitive" in ev["subtypes"]  # type: ignore[operator]


def test_r8_weapons_decorative() -> None:
    ev = scan_sensitive("Bullet Tumbler Cup for Hunting Gifts", [])
    assert ev is not None
    assert {"weapons_decorative"} <= set(ev["subtypes"])  # type: ignore[operator]


def test_r8_multiple_subtypes_flattened() -> None:
    ev = scan_sensitive("Nazi Confederate Flag Marijuana Leaf Sticker", [])
    assert ev is not None
    assert "historical_intolerance" in ev["subtypes"]  # type: ignore[operator]
    assert "substance_decorative" in ev["subtypes"]  # type: ignore[operator]
    assert len(ev["matched"]) >= 2  # type: ignore[arg-type]


def test_r8_benign_no_hit() -> None:
    assert scan_sensitive("Stainless Steel Water Bottle 32oz", []) is None


def test_r8_empty() -> None:
    assert scan_sensitive("", []) is None


# ── L3 user prompt surface ──


def test_build_user_prompt_source_aligned() -> None:
    """round-8 逐字对齐源仓 _build_user_prompt：R4/R5 进 prompt；R7/R8 只留档
    audit_hit 不进 prompt（源仓 _summarize_l2_hits 同）；产品信息段 + 待评估品牌词段。"""
    product = {"brand": "Generic", "title": "Mickey Mouse Premium Quality Mug", "attrs": {}}
    l2_hits = [
        {
            "rule_code": "l2_r7_content_promotional",
            "evidence": {"strong_phrases": ["Premium Quality"], "allcaps_runs": []},
        },
        {
            "rule_code": "l2_r8_sensitive",
            "evidence": {"matched": ["Mickey Mouse"]},
        },
        {"rule_code": "l2_r4_title_desc_blacklist", "evidence": {"matches": [{"brand": "acme"}]}},
    ]
    prompt = build_user_prompt(product, l2_hits, walmart_pt="Drinkware", walmart_category="Home")
    assert "# 产品信息" in prompt
    assert "沃尔玛 PT: Drinkware" in prompt
    assert "标题/描述命中黑名单(R4" in prompt and "acme" in prompt
    assert "# 待评估的品牌/商标词" in prompt
    assert "促销宣称词" not in prompt  # R7/R8 不进 prompt（源仓口径）
    assert "敏感内容命中" not in prompt


def test_build_user_prompt_no_hits_placeholder() -> None:
    prompt = build_user_prompt({"title": "Plain Mug", "attrs": {}}, [])
    assert "(L2 无命中)" in prompt
    assert "(无 R4/R5 命中, 跳过品牌真伪判定)" in prompt
