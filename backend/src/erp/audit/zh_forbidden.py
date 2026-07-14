"""中国搬运卖家禁售种子规则（源仓 seed yaml 保真移植，R2-02 round-3 补齐）。

数据：data/forbidden_categories_zh_seller.json（由源仓 db/seed/
forbidden_categories_zh_seller.yaml 机械转换，13 条 excluded + 18 条 mega）。
源仓吃它的两处拒绝点，此前均未移植——round-3 残余 reject->pass 25 的主嫌疑：
  1. L1 excluded（`_check_seed_excluded`）：amazon 路径 / title / PT 名子串命中
     （3C/服饰/汽配/带电…）→ 预拦截拒；
  2. L2 R2 mega（`match_mega`）：PT 名 word-boundary（+可选复数 's'）或
     walmart_category 前缀命中 18 个细粒度禁售大类（医疗/烟草/武器/农药…）→ 硬拒。

匹配语义与源仓逐行对齐：excluded=小写子串；mega pt_contains=词边界正则
`\\b<term>s?\\b`（"bra" 中 "Bras" 不中 "Brackets"）；category_prefix=小写前缀。
多条命中取首条（数据顺序=优先级）。规则数据后续入 refdata 治理（RS-04D 断言账本
方向）；现阶段随代码走保证与源仓行为一致。
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_PATH = Path(__file__).parent / "data" / "forbidden_categories_zh_seller.json"


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    return data


@lru_cache(maxsize=512)
def _kw_pattern(term: str) -> re.Pattern[str]:
    """词边界 + 可选尾 's'（源仓 _compile_kw_pattern 原样）。"""
    return re.compile(rf"\b{re.escape(term.lower())}s?\b", re.IGNORECASE)


def check_excluded(
    *, category_path: str | None, title: str | None, walmart_pt: str | None = None
) -> str | None:
    """L1 排除检查（源仓 _check_seed_excluded 原样）：子串命中即返回 reason。"""
    haystacks = {
        "walmart_pt": (walmart_pt or "").lower(),
        "amazon_category": (category_path or "").lower(),
        "title_keyword": (title or "").lower(),
    }
    for rule in _load().get("excluded_categories", []):
        m = str(rule.get("match") or "").lower()
        if not m:
            continue
        hay = haystacks.get(rule.get("scope") or "walmart_pt", "")
        if m in hay:
            return str(rule.get("reason") or f"seed-{rule.get('scope')}")
    return None


def match_mega(
    walmart_pt: str | None, walmart_category: str | None = None
) -> dict[str, Any] | None:
    """R2 禁售大类（源仓 match_mega 原样）→ 命中 dict{key, reason, matched_by,
    matched_term, walmart_policy} 或 None。"""
    pt_norm = (walmart_pt or "").strip()
    cat_norm = (walmart_category or "").strip().lower()
    if not pt_norm and not cat_norm:
        return None
    for mc in _load().get("mega_forbidden_categories", []):
        if pt_norm:
            for raw_term in mc.get("pt_contains") or []:
                term = str(raw_term).strip()
                if term and _kw_pattern(term).search(pt_norm):
                    return {
                        "key": mc.get("key"),
                        "reason": mc.get("reason"),
                        "matched_by": "pt_contains",
                        "matched_term": term,
                        "walmart_policy": mc.get("walmart_policy"),
                    }
        if cat_norm:
            for raw_pref in mc.get("walmart_category_prefix") or []:
                pref = str(raw_pref).strip().lower()
                if pref and cat_norm.startswith(pref):
                    return {
                        "key": mc.get("key"),
                        "reason": mc.get("reason"),
                        "matched_by": "walmart_category_prefix",
                        "matched_term": pref,
                        "walmart_policy": mc.get("walmart_policy"),
                    }
    return None
