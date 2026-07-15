"""R2-03 增量 1：MP_ITEM v5 规格无损提取工具（纯单元，无 DB）。

保真点：fields=原始节点原样（enum 全量/length/pattern/allOf 不丢）；cert 派生与
源仓 sync_pt_specs 同定义（required ∩ 集合）；live 响应 {"schema": …} 解包；
__orderable__ 伪行；--pt 过滤。
"""

import json
from pathlib import Path
from typing import Any

from erp.tools.extract_mp_item_spec import ORDERABLE_PSEUDO_PT, build_row, extract

# 缩微 monolith：形态与官方一致（properties.MPItem.items.properties.{Visible,Orderable}）
_DRINKWARE = {
    "type": "object",
    "required": ["shortDescription", "keyFeatures"],
    "properties": {
        "shortDescription": {"type": "string", "minLength": 1, "maxLength": 4000},
        "keyFeatures": {
            "type": "array",
            "minItems": 3,
            "items": {"type": "string", "maxLength": 500},
        },
        "material": {
            "type": "array",
            "items": {"type": "string", "enum": [f"M{i}" for i in range(30)]},  # >10 不得截断
        },
    },
    "allOf": [
        {
            "if": {"properties": {"material": {"contains": {"const": "M1"}}}},
            "then": {"required": ["shortDescription"]},
        }
    ],
}
_HEATER = {
    "type": "object",
    "required": ["has_nrtl_listing_certification", "smallPartsWarnings", "brandName"],
    "properties": {
        "has_nrtl_listing_certification": {"type": "string", "enum": ["Yes", "No"]},
        "smallPartsWarnings": {"type": "array", "items": {"type": "string"}},
        "brandName": {"type": "string", "pattern": "^[^<>]*$"},
    },
}
_ORDERABLE = {
    "type": "object",
    "required": ["sku", "price"],
    "properties": {
        "sku": {"type": "string", "maxLength": 50},
        "price": {"type": "number"},
    },
}


def _monolith() -> dict[str, Any]:
    return {
        "properties": {
            "MPItem": {
                "items": {
                    "properties": {
                        "Visible": {"properties": {"Drinkware": _DRINKWARE, "Heaters": _HEATER}},
                        "Orderable": _ORDERABLE,
                    }
                }
            }
        }
    }


def test_build_row_lossless_and_cert() -> None:
    row = build_row("Heaters", _HEATER)
    assert row["fields"] == _HEATER  # 节点原样零损
    assert row["total_fields"] == 3
    assert row["required_count"] == 3
    assert row["has_real_cert"] is True
    assert row["real_cert_fields"] == ["has_nrtl_listing_certification"]
    assert row["has_soft_cert"] is True
    assert row["soft_cert_fields"] == ["smallPartsWarnings"]

    row2 = build_row("Drinkware", _DRINKWARE)
    assert row2["has_real_cert"] is False
    # enum 全量与 allOf 不丢（对照旧压缩版教训）
    assert len(row2["fields"]["properties"]["material"]["items"]["enum"]) == 30
    assert "allOf" in row2["fields"]


def test_extract_monolith_with_orderable(tmp_path: Path) -> None:
    src = tmp_path / "mono.json"
    src.write_text(json.dumps(_monolith()), encoding="utf-8")
    out = tmp_path / "out.jsonl"
    stats = extract([("monolith", src)], out)
    assert stats == {"pts": 2, "orderable": 1}
    rows = {r["walmart_product_type"]: r for r in map(json.loads, out.read_text().splitlines())}
    assert set(rows) == {"Drinkware", "Heaters", ORDERABLE_PSEUDO_PT}
    assert rows[ORDERABLE_PSEUDO_PT]["fields"] == _ORDERABLE
    assert rows[ORDERABLE_PSEUDO_PT]["required_fields"] == ["sku", "price"]


def test_extract_live_response_wrapped_and_pt_filter(tmp_path: Path) -> None:
    src = tmp_path / "live.json"
    src.write_text(json.dumps({"schema": _monolith()}), encoding="utf-8")
    out = tmp_path / "out.jsonl"
    stats = extract([("live", src)], out, only_pts={"Heaters"}, include_orderable=False)
    assert stats == {"pts": 1, "orderable": 0}
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["walmart_product_type"] for r in rows] == ["Heaters"]


def test_extract_merge_later_wins(tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    a.write_text(json.dumps(_monolith()), encoding="utf-8")
    newer = _monolith()
    newer["properties"]["MPItem"]["items"]["properties"]["Visible"]["properties"]["Heaters"] = {
        "type": "object",
        "required": ["brandName"],
        "properties": {"brandName": {"type": "string"}},
    }
    b = tmp_path / "b.json"
    b.write_text(json.dumps(newer), encoding="utf-8")
    out = tmp_path / "out.jsonl"
    extract([("live", a), ("live", b)], out)
    rows = {r["walmart_product_type"]: r for r in map(json.loads, out.read_text().splitlines())}
    assert rows["Heaters"]["required_count"] == 1  # 后者胜
    assert rows["Drinkware"]["required_count"] == 2
