"""NRTL PT 整机 vs 小件分类器（源仓 nrtl_classifier 保真移植，R3b 降级逻辑）。

数据：data/nrtl_small_parts.json（源仓 db/seed/nrtl_small_parts.yaml 机械转换，
42 小件词 + 46 整机词）。规则（源仓原样）：
  - PT 名含 Replacement/Parts/Accessor → 强制小件（即使同时含整机词）
  - 整机词优先于小件词；未命中任一 → 整机（保守默认，保持硬拒）
R3b：pt_spec.has_real_cert=true 且分类=整机 → 硬拒（NRTL 实验室认证搬运做不了）；
小件 → 软证据不拒（交 L3 结合上下文）。
"""

import json
from functools import lru_cache
from pathlib import Path

_DATA_PATH = Path(__file__).parent / "data" / "nrtl_small_parts.json"


@lru_cache(maxsize=1)
def _keywords() -> tuple[list[str], list[str]]:
    data = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    return (
        [str(x).lower() for x in data.get("small_part_keywords") or []],
        [str(x).lower() for x in data.get("whole_unit_keywords") or []],
    )


def classify_nrtl_pt(pt_name: str | None) -> str:
    """→ 'small_part' | 'whole_unit'（未明确命中 → whole_unit，保守保持硬拒）。"""
    pt_low = (pt_name or "").strip().lower()
    if not pt_low:
        return "whole_unit"
    small_kws, whole_kws = _keywords()
    if any(kw in pt_low for kw in ("replacement", "parts", "accessor")):
        return "small_part"
    for wkw in whole_kws:
        if wkw in pt_low:
            return "whole_unit"
    for skw in small_kws:
        if skw in pt_low:
            return "small_part"
    return "whole_unit"
