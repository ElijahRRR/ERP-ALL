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

from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.audit.llm import cache_key, llm_client
from erp.audit.pipeline import parse_json_object
from erp.core.db import system_tx

log = structlog.get_logger()

# 复排模型参数：system_config 'category_map.rerank' JSON（{model, temperature, max_tokens}），
# 缺省回退此表。与 L3 的 audit_policy.config 同思路——模型选择归 DB 配置，不写死代码。
# 标准模型=deepseek-v4-flash（Owner 长期实测定标，D-Q58）。
_RERANK_DEFAULTS: dict[str, Any] = {
    "model": "deepseek-v4-flash",
    "temperature": 0.0,
    "max_tokens": 400,
}


async def _rerank_cfg(session: AsyncSession) -> dict[str, Any]:
    row = (
        await session.execute(
            text("SELECT value FROM app.system_config WHERE key = 'category_map.rerank'")
        )
    ).scalar_one_or_none()
    cfg = dict(_RERANK_DEFAULTS)
    if isinstance(row, dict):
        cfg.update({k: row[k] for k in ("model", "temperature", "max_tokens") if k in row})
    return cfg


_UNMAPPED_MARKER = "无对应Walmart PT"

# 召回 v2（R2-02 round-2 教训）：旧 starts_with 祖先召回在真数据上 90/189 无候选——
# map 存的是【完整叶子路径】，目标类目往往没有"恰为其前缀的祖先行"，但有大量
# 【同分支兄弟】（如 "…> Knitting & Crochet > Storage" 之于 "…> Knitting Needles"）。
# 现按首段 LIKE 拉回同顶级类目行，Python 侧做分段前缀匹配（分隔符/空格宽容），
# 从最深共同层往浅找，共同层 ≥2 段才算候选（仅同顶级过宽，噪声大不如无）。
_RECALL_SQL = text(
    "SELECT cm.walmart_product_type AS wpt, cm.amazon_category,"
    "       pm.walmart_category, pm.walmart_ptg, pm.zh_seller_forbidden AS pt_forbidden"
    " FROM refdata.category_map cm"
    " JOIN refdata.pt_meta pm ON pm.walmart_product_type = cm.walmart_product_type"
    " WHERE cm.amazon_category LIKE :pat ESCAPE '\\'"
    "   AND cm.walmart_product_type <> :unmapped"
    " LIMIT 5000"
)

_MIN_SHARED_SEGMENTS = 2  # 候选须与目标共享 ≥2 层路径


def _segments(path: str) -> list[str]:
    """路径 → 分段（'A > B > C' 或 'A/B/C' 皆可，段内空格修剪）。"""
    sep = ">" if ">" in path else "/"
    return [p.strip() for p in path.split(sep) if p.strip()]


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
    """同分支召回候选 WPT（INNER JOIN pt_meta）：首段 LIKE 拉回 + 分段前缀匹配，
    从最深共同层往浅找（≥2 段）。无 ≥2 段共同分支 → 空（不召仅同顶级的噪声）。"""
    target_segs = _segments(amazon_category or "")
    if not target_segs:
        return []
    rows = (
        (
            await session.execute(
                _RECALL_SQL,
                {"pat": _like_escape(target_segs[0]) + "%", "unmapped": _UNMAPPED_MARKER},
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        return []
    seg_rows = [(r, _segments(r["amazon_category"])) for r in rows]
    for k in range(len(target_segs), _MIN_SHARED_SEGMENTS - 1, -1):
        want = target_segs[:k]
        by_wpt: dict[str, dict[str, Any]] = {}
        for r, segs in seg_rows:
            if segs[:k] == want and r["wpt"] not in by_wpt:
                by_wpt[r["wpt"]] = {
                    "wpt": r["wpt"],
                    "walmart_category": r["walmart_category"],
                    "walmart_ptg": r["walmart_ptg"],
                    "pt_forbidden": r["pt_forbidden"],
                }
        if by_wpt:
            return list(by_wpt.values())[:20]
    return []


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
    raw = parse_json_object(raw_text)
    return isinstance(raw, dict) and bool(str(raw.get("wpt") or "").strip())


def coerce_rerank(raw_text: str, valid_wpts: set[str]) -> dict[str, Any] | None:
    """解析复排结果。fail-closed：非 JSON / 非 dict / wpt 不在召回候选内 → None（不写回）。"""
    raw = parse_json_object(raw_text)
    if not isinstance(raw, dict):
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
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """召回 → LLM 复排 → 写回 category_map。→ {amazon_category, resolved, wpt, ...}。

    模型参数：显式实参 > system_config 'category_map.rerank' > 默认。
    resolved=False 场景：no_candidates（无 ≥2 段同分支，不调 LLM）/ llm_unavailable
    （网络失败）/ 复排非法（coerce None，fail-closed 不写回）。
    """
    # ── tx1：召回 + 组 prompt + 查缓存（命中即写回终局）──
    async with system_tx(sessions) as s:
        cfg = await _rerank_cfg(s)
        model = model or str(cfg["model"])
        temperature = float(cfg["temperature"]) if temperature is None else temperature
        max_tokens = int(cfg["max_tokens"]) if max_tokens is None else max_tokens
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
        content, pt, ct, cached_toks = await llm_client.call_provider(
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
            cached_tokens=cached_toks,
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
