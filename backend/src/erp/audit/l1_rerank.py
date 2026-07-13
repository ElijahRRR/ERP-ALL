"""L1-b：类目映射 LLM 语义复排（module=category_map，写回 refdata.category_map）。

D-Q55 + 001 §05（llm_usage_log.module=category_map，全局批量 team_id NULL）：无直判
命中的 Amazon 类目 → 召回候选 WPT（祖先前缀，INNER JOIN pt_meta 滤废弃）→ LLM 复排
选唯一最优 → 写回 category_map（match_type=ai_rerank）。这样审核的 L1-a 直判下次即
覆盖该类目（0 LLM）。故 L1 = L1-a 同步直判 gate（audit 内联）+ L1-b 类目级复排批量
（写回 map），映射表是两者的共享真相源——不改 twice-reviewed audit_one 的行锁编排。

分段（tx1 召回+查缓存 → HTTP → tx2 记账+写回）沿用 RS-03a 的"HTTP 不占事务"纪律，
但**无 product 行锁**（类目级作业，非 per-product），故无重锁/新者胜/遗孤清扫。

fail-closed：LLM 输出非法 / 选了召回候选外的 WPT / 无候选 → **不写回**——绝不写脏
映射污染 L1-a gate（脏映射会让后续该类目商品直判到错误/禁做 WPT）。
"""

import json
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.audit.llm import cache_key, llm_client
from erp.core.db import system_tx

log = structlog.get_logger()

_UNMAPPED_MARKER = "无对应Walmart PT"

# 召回：mapped 祖先前缀（starts_with 分隔符无关）→ 其 WPT 是本类目候选。DISTINCT ON 去重，
# 最近祖先（amazon_category 最长）优先。INNER JOIN pt_meta 滤废弃 PT，排除 unmapped 标记。
_RECALL_SQL = text(
    "SELECT wpt, walmart_category, walmart_ptg, pt_forbidden FROM ("
    "  SELECT DISTINCT ON (cm.walmart_product_type)"
    "         cm.walmart_product_type AS wpt, pm.walmart_category, pm.walmart_ptg,"
    "         pm.zh_seller_forbidden AS pt_forbidden, length(cm.amazon_category) AS anc_len"
    "  FROM refdata.category_map cm"
    "  JOIN refdata.pt_meta pm ON pm.walmart_product_type = cm.walmart_product_type"
    "  WHERE cm.amazon_category <> '' AND starts_with(:target, cm.amazon_category)"
    "    AND cm.walmart_product_type <> :unmapped"
    "  ORDER BY cm.walmart_product_type, length(cm.amazon_category) DESC"
    ") sub ORDER BY anc_len DESC LIMIT :lim"
)

_WRITEBACK_SQL = text(
    "INSERT INTO refdata.category_map"
    " (amazon_category, walmart_product_type, confidence, match_type, source_batch, updated_at)"
    " VALUES (:az, :wpt, :conf, 'ai_rerank', 'l1b', now())"
    " ON CONFLICT (amazon_category, walmart_product_type) DO UPDATE SET"
    "  confidence = EXCLUDED.confidence, match_type = 'ai_rerank', updated_at = now()"
)

RERANK_SYSTEM_PROMPT = """你是沃尔玛类目映射专家。给定一个 Amazon 类目路径和一组候选 \
Walmart Product Type，选出语义最匹配的唯一一个 WPT。
只能从候选列表里选，不得杜撰候选外的 WPT。只输出严格 JSON，无任何解释或 markdown：
{"wpt": "<候选之一>", "confidence": "高|中|低", "reason": "<=30字中文理由"}"""


async def recall_candidates(session: AsyncSession, amazon_category: str) -> list[dict[str, Any]]:
    """祖先前缀召回候选 WPT（INNER JOIN pt_meta）。无 mapped 祖先 → 空。"""
    if not amazon_category or not amazon_category.strip():
        return []
    rows = (
        await session.execute(
            _RECALL_SQL, {"target": amazon_category, "unmapped": _UNMAPPED_MARKER, "lim": 20}
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def build_rerank_messages(
    amazon_category: str, candidates: list[dict[str, Any]]
) -> list[dict[str, str]]:
    lines = [f"Amazon 类目: {amazon_category}", "", "候选 Walmart Product Type:"]
    for c in candidates:
        tag = "（该 PT 中国搬运卖家禁做）" if c["pt_forbidden"] else ""
        lines.append(
            f"- {c['wpt']}｜沃尔玛类目 {c['walmart_category'] or '?'}"
            f"｜组 {c['walmart_ptg'] or '?'}{tag}"
        )
    return [
        {"role": "system", "content": RERANK_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]


def rerank_cacheable(raw_text: str) -> bool:
    """可缓存谓词：JSON dict + 非空 wpt 串（坏响应不入缓存，同 L3 R2-21 纪律）。"""
    try:
        raw = json.loads(raw_text)
    except (ValueError, json.JSONDecodeError):
        return False
    return isinstance(raw, dict) and bool(str(raw.get("wpt") or "").strip())


def coerce_rerank(raw_text: str, valid_wpts: set[str]) -> dict[str, Any] | None:
    """解析复排结果。fail-closed：非 JSON / 非 dict / wpt 不在召回候选内 → None（不写回）。"""
    try:
        raw = json.loads(raw_text)
        if not isinstance(raw, dict):
            raise ValueError("非 dict")
    except (ValueError, json.JSONDecodeError):
        log.warning("l1b.rerank_bad_json", head=raw_text[:120])
        return None
    wpt = str(raw.get("wpt") or "").strip()
    if wpt not in valid_wpts:
        # 选了召回候选外的 WPT（含杜撰）→ 不可信，不写回
        log.warning("l1b.rerank_wpt_out_of_candidates", wpt=wpt)
        return None
    conf = str(raw.get("confidence") or "").strip()[:20] or None
    return {"wpt": wpt, "confidence": conf, "reason": str(raw.get("reason") or "")[:200]}


async def _apply_writeback(
    session: AsyncSession, amazon_category: str, result: dict[str, Any] | None
) -> tuple[bool, str | None]:
    if result is None:
        log.info("l1b.rerank_rejected", amazon_category=amazon_category)  # fail-closed
        return False, None
    await session.execute(
        _WRITEBACK_SQL,
        {"az": amazon_category, "wpt": result["wpt"], "conf": result["confidence"]},
    )
    log.info("l1b.category_resolved", amazon_category=amazon_category, wpt=result["wpt"])
    return True, result["wpt"]


async def resolve_category(
    sessions: async_sessionmaker[AsyncSession],
    amazon_category: str,
    *,
    model: str = "deepseek-chat",
    temperature: float = 0.0,
    max_tokens: int = 400,
) -> dict[str, Any]:
    """召回 → LLM 复排 → 写回 category_map。→ {amazon_category, resolved, wpt, ...}。

    resolved=False 场景：no_candidates（无 mapped 祖先，不调 LLM）/ llm_unavailable
    （网络失败）/ 复排非法（coerce None，fail-closed 不写回）。
    """
    # ── tx1：召回 + 组 prompt + 查缓存（命中即写回终局）──
    async with system_tx(sessions) as s:
        candidates = await recall_candidates(s, amazon_category)
        if not candidates:
            return {
                "amazon_category": amazon_category,
                "resolved": False,
                "reason": "no_candidates",
            }
        valid_wpts = {c["wpt"] for c in candidates}
        messages = build_rerank_messages(amazon_category, candidates)
        key = cache_key(model, messages, temperature, max_tokens)
        cached = await llm_client.check_cache(
            s,
            key=key,
            model=model,
            object_type="category_map",
            cacheable=rerank_cacheable,
            module="category_map",
        )
        if cached is not None:
            written, wpt = await _apply_writeback(
                s, amazon_category, coerce_rerank(cached, valid_wpts)
            )
            return {
                "amazon_category": amazon_category,
                "resolved": written,
                "wpt": wpt,
                "cache_hit": True,
                "candidates": len(candidates),
            }

    # ── HTTP（无事务）──
    try:
        content, pt, ct = await llm_client.call_provider(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens
        )
    except Exception as e:
        log.warning("l1b.rerank_unavailable", amazon_category=amazon_category, error=str(e)[:200])
        return {"amazon_category": amazon_category, "resolved": False, "reason": "llm_unavailable"}

    # ── tx2：记账 + coerce + 写回 ──
    async with system_tx(sessions) as s:
        cost = await llm_client.record_result(
            s,
            key=key,
            model=model,
            content=content,
            prompt_tokens=pt,
            completion_tokens=ct,
            object_type="category_map",
            cacheable=rerank_cacheable,
            module="category_map",
        )
        written, wpt = await _apply_writeback(
            s, amazon_category, coerce_rerank(content, valid_wpts)
        )
        return {
            "amazon_category": amazon_category,
            "resolved": written,
            "wpt": wpt,
            "cost_usd": cost,
            "candidates": len(candidates),
        }
