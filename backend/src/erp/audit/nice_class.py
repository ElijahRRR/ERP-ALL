"""Walmart Category → Nice Class 映射（源仓 nice_class_mapper 保真移植）。

数据：data/pt_nice_class.json（源仓 db/seed/pt_nice_class.yaml 机械转换，
类号 '006' → int 6）。供 L2 R5 商标类目过滤：只查产品类目相关 Nice 分类下的
LIVE 商标，压掉 GARDEN/CAR/BROWN 等通用词商标误报（它们多在 class 35 广告 /
41 娱乐等服务类）——源仓设计原文。round-4 对照：R5 无此过滤是 pass->reject
14 条同链分歧（R4→R5→L3 判真品牌拒）的实现级成因之一。

匹配顺序（源仓 classes_for 原样）：精确 → case-insensitive → 双向前缀 → default。
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_PATH = Path(__file__).parent / "data" / "pt_nice_class.json"


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    return data


def classes_for(walmart_category: str | None) -> list[int]:
    """→ 该类目相关的 Nice Class 清单（int）。未命中/空 → default（宽松兜底）。"""
    data = _load()
    mapping: dict[str, list[int]] = data.get("category_to_nice_class") or {}
    default: list[int] = data.get("default_classes") or []
    if not walmart_category:
        return list(default)
    cat = walmart_category.strip()
    if cat in mapping:
        return list(mapping[cat])
    for k, v in mapping.items():
        if k.lower() == cat.lower():
            return list(v)
    for k, v in mapping.items():
        if cat.startswith(k) or k.startswith(cat):
            return list(v)
    return list(default)
