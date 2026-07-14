"""审核管道 L0 / L2 / L3（R1-10 范围；L1/L4 留 R2）。

移植源：walmart-audit-system pipelines/*（考古对照表 .agent/evidence/R1-10/archaeology.md）。
保真要点：
- L0 只做 100% 确定性硬判断，四层顺序（卖家→类目→商标符号→品牌精准），任一命中即拒；
  占位符品牌白名单（n/a/unbranded/generic…）不当品牌拦——源仓实战教训。
- L2 双职责：硬拒 + 软证据收集（R1-10 仅软证据 R4/R5——penalty=0，detail 传给 L3）；
  不靠累积扣分自行 reject。
- L3 严格 JSON 输出 + _coerce 规范化：解析失败/非法 verdict → needs_review
  （fail-closed，评审 round-1 A4 修正，源仓原为保守 pass）；pass 强制 category=none；
  ⭐ verdict 合法为 pass 时任一 is_real_brand=true 强制翻案 reject（结构异常的响应
  不翻案——嵌套字段不可信，评审 round-2 R2-22）。
- system prompt 保持静态（吃 provider prompt cache——源仓 2026-04-28 成本设计），
  产品内容只进 user prompt。
"""

import json
import re
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.audit import l2_content, nice_class
from erp.audit.blacklist_index import scan_blacklist
from erp.audit.stopwords import is_stopword

log = structlog.get_logger()

# 源仓 phase0_brand._NON_BRAND_PLACEHOLDERS 原样移植
NON_BRAND_PLACEHOLDERS = {
    "n/a", "na", "n.a.", "n.a",
    "none", "null", "nil",
    "unbranded", "no brand", "no brand name", "no name",
    "generic", "oem", "various",
    "不详", "无品牌", "无",
    "-", "--", "---",
}  # fmt: skip

TRADEMARK_SYMBOLS = ("®", "™", "℠", "©")  # ® ™ ℠ ©


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


_CAT_WS_RE = re.compile(r"\s+")


def normalize_amazon_category(s: str | None) -> str:
    """源仓 phase0_lark_blacklist.normalize_amazon_category 原样：去全部空格 +
    '>'/'->'/'/' 统一为 '->'。旧 lark 类目黑名单存的是此形态（再经 _norm 小写入库），
    L0 类目查找需用同款派生键才能命中导入的旧数据。"""
    if not s:
        return ""
    raw = s.replace("->", "\x00").replace(">", "\x00").replace("/", "\x00")
    parts: list[str] = []
    for raw_seg in raw.split("\x00"):
        seg = _CAT_WS_RE.sub("", raw_seg).strip()
        if seg:
            parts.append(seg)
    return "->".join(parts)


def _product_text(product: dict[str, Any]) -> str:
    attrs = product.get("attrs") or {}
    bullets = attrs.get("bullets") or []
    parts = [
        product.get("title") or "",
        " ".join(map(str, bullets)),
        str(attrs.get("description") or ""),
    ]
    return "\n".join(p for p in parts if p)


# ── L0：确定性硬判断（命中即 reject，不进 L2/L3）──


async def run_l0(session: AsyncSession, product: dict[str, Any]) -> dict[str, Any] | None:
    """→ 命中 dict{rule_code, evidence} 或 None。查询直查四表（试点量级小；
    内存字典加载器带版本失效属 R2 优化，见 001 §04）。"""
    attrs = product.get("attrs") or {}
    team = product["team_id"]

    # 0. 卖家黑名单
    seller = _norm(str(attrs.get("seller_id") or ""))
    if seller:
        hit = await _blacklist_lookup(session, "blacklist_seller", "seller_ref", seller, team)
        if hit:
            return {
                "rule_code": "l0_blacklist_seller",
                "evidence": {"seller_ref": seller, "row_id": hit},
            }

    # 1. ASIN 黑名单
    asin = _norm(product.get("source_ref"))
    if asin:
        hit = await _blacklist_lookup(session, "blacklist_asin", "asin", asin, team)
        if hit:
            return {"rule_code": "l0_blacklist_asin", "evidence": {"asin": asin, "row_id": hit}}

    # 2. 类目黑名单（Amazon 类目路径/叶子）。双键：_norm 原文（本系统契约）+
    #    lower(normalize_amazon_category)（旧 lark 黑名单契约——去空格 '->' 分隔，
    #    导入的旧 phase0_blacklist_amazon_cats 数据以此形态命中）
    for cat in filter(None, (product.get("amazon_leaf_id"), product.get("category_path"))):
        for key in {_norm(str(cat)), _norm(normalize_amazon_category(str(cat)))}:
            hit = await _blacklist_lookup(
                session, "blacklist_category", "category_ref", key, team
            )
            if hit:
                return {
                    "rule_code": "l0_blacklist_category",
                    "evidence": {"category": cat, "matched_key": key, "row_id": hit},
                }

    # 3. 商标符号硬拦（title/bullets/desc 含 ®™℠©）
    full_text = _product_text(product)
    found = [s for s in TRADEMARK_SYMBOLS if s in full_text]
    if found:
        return {"rule_code": "l0_trademark_symbol", "evidence": {"symbols": found}}

    # 4. 品牌精准黑名单（等值；占位符白名单绕过）
    brand = _norm(product.get("brand"))
    if brand and brand not in NON_BRAND_PLACEHOLDERS:
        hit = await _blacklist_lookup(session, "blacklist_brand", "brand_norm", brand, team)
        if hit:
            return {"rule_code": "l0_blacklist_brand", "evidence": {"brand": brand, "row_id": hit}}
    return None


async def _blacklist_lookup(
    session: AsyncSession, table: str, col: str, value: str, team_id: int
) -> int | None:
    if not value:
        return None
    return (
        await session.execute(
            text(
                f"SELECT id FROM app.{table}"
                f" WHERE {col} = :v AND status = 'active'"
                "  AND (team_id IS NULL OR team_id = :t)"
                " LIMIT 1"
            ),
            {"v": value, "t": team_id},
        )
    ).scalar_one_or_none()


# ── L2：软证据收集（R4 黑名单词 / R5 USPTO LIVE）──

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'&.-]*")


async def run_l2(
    session: AsyncSession, product: dict[str, Any], *, walmart_category: str | None = None
) -> list[dict[str, Any]]:
    """walmart_category：L1 直判带出的 pt_meta.walmart_category（供 R5 Nice 过滤）。
    None（未跑 L1 / 未直判）→ R5 不过滤（保持既有行为，信号偏多交 L3 判）。"""
    hits: list[dict[str, Any]] = []
    full_text = _norm(_product_text(product))

    # R4：黑名单词词边界命中（Aho-Corasick + 版本失效内存加载器，见 blacklist_index）。
    #     命中按 brand_norm 升序返回（顺序确定性——命中词序影响 L3 缓存键）；
    #     全局+本 team 候选集与占位符/短单词跳过逻辑同旧实现，仅把 O(N) 正则换成一次 AC 扫描。
    matches = await scan_blacklist(session, team_id=product["team_id"], text=full_text)
    if matches:
        hits.append(
            {
                "rule_code": "l2_r4_title_desc_blacklist",
                "is_hard": False,
                "evidence": {"matches": matches[:10]},
            }
        )

    # R5：title 大写开头词反查 USPTO LIVE。walmart_category 已知时按 Nice Class 过滤
    # （源仓设计：只看产品类目相关分类下的商标，压掉通用词商标误报——GARDEN/CAR 等
    # 多注册在 35 广告/41 娱乐服务类）。Nice 未知的商标行在过滤态不命中（源仓同）。
    title = product.get("title") or ""
    candidates = list(
        dict.fromkeys(
            w.lower()
            for w in re.findall(r"\b[A-Z][A-Za-z]{3,}\b", title)
            if not is_stopword(w.lower())
        )
    )[:20]
    if candidates:
        allowed = nice_class.classes_for(walmart_category) if walmart_category else None
        sql = (
            "SELECT DISTINCT mark_norm FROM refdata.trademark"
            " WHERE mark_norm = ANY(:c) AND is_live"
        )
        params: dict[str, Any] = {"c": candidates}
        if allowed:
            sql += " AND nice_classes && CAST(:allowed AS smallint[])"
            params["allowed"] = allowed
        sql += " ORDER BY mark_norm LIMIT 10"
        marks = (await session.execute(text(sql), params)).scalars()
        matched = list(marks)
        if matched:
            hits.append(
                {
                    "rule_code": "l2_r5_trademark_live",
                    "is_hard": False,
                    "evidence": {"matched_marks": matched, "nice_filtered": bool(allowed)},
                }
            )

    # R7/R8：纯文本软证据（促销宣称 / 敏感内容），不否决，surface 到 L3 结合政策块判定
    attrs = product.get("attrs") or {}
    title = product.get("title") or ""
    bullets = [str(b) for b in (attrs.get("bullets") or [])]
    promo = l2_content.scan_promotional(title, bullets)
    if promo:
        hits.append({"rule_code": "l2_r7_content_promotional", "is_hard": False, "evidence": promo})
    sensitive = l2_content.scan_sensitive(title, bullets)
    if sensitive:
        hits.append({"rule_code": "l2_r8_sensitive", "is_hard": False, "evidence": sensitive})
    return hits


# stopwords 已全量移植至 erp.audit.stopwords（源仓 707 词 + 长度/数字规则，
# round-5 补齐——此前最小子集导致 R5 候选过滤不足、L3 商标信号偏多）


# ── L3：LLM 语义审核（R1-10 单策略=知识产权）──

# 保真移植：业务约束 + 品牌真伪/IP 维度 + 严格 JSON 输出（源仓 _build_system_prompt 精简版；
# 37 条政策全清单静态段随 R2 全量策略落库后拼接——结构位置已预留在末尾）
L3_SYSTEM_PROMPT = """你是沃尔玛 Marketplace 合规审核 AI (站在沃尔玛官方立场)。
卖家是中国搬运模式、无任何证书/认证、每日数万产品。
你只输出严格 JSON, 不要任何解释文字或 markdown 前后缀。

# 业务关键约束
- 卖家 = 中国搬运模式, 客观上不可能拥有 Nike/Dyson/Disney 等大牌的
  Official / Licensed / Authorized / OEM 授权.
- 任何"授权声明"都视为虚假宣称, 一律按侵权判 (无豁免).
- 默认 pass — 只有清晰证据才 reject.

# 你的判定职责

## 1. 品牌真伪 (针对 user prompt 列出的"L2 命中词")
用完整 title/bullets/description 上下文综合判断每个命中词:
- 是真品牌 → is_real_brand=true
- 是通用英文词 (如 top/floor/summer/modern/classic 等) → is_real_brand=false

任一命中词 is_real_brand=true → 整品 reject, reason_category='Intellectual Property'.

特殊语法铁证 (X 必为品牌, 必判 true):
  "compatible for X" / "fits X" / "replacement for X" / "works with X" /
  "designed for X" / "OEM for X" / 品牌名后紧跟型号 (如 "Dyson V6")

## 2. 冒犯性内容 (Walmart Offensive Content 政策)
从 title/bullets/description 语义判, 命中以下任一 → reject,
reason_category='Offensive Content':
- 色情 / 成人主题 / 性化未成年 / 捆绑 / 性玩具
- 种族辱骂 / 仇恨象征 (Confederate / Nazi / Swastika / KKK)
- 恶搞冒犯 (funny + offensive 混合 / 嘲讽政治人物)
- 仿真武器 / 玩具枪 / replica firearm (例外: 水枪 / 泡泡枪 /
  bright orange tip 合规标识 / NERF foam dart 品牌豁免)

## 3. 知识产权 (Intellectual Property)
任一 → reject, reason_category='Intellectual Property':
- 商标仿冒: 已通过维度 1 处理
- 版权 IP 角色: 卡通 / 电影 / 游戏 / 动漫 IP 名 + 周边商品
- 外观/发明专利: 文本明示仿造 ("Stanley Cup style" / "AirPods case for Apple")
- 商业包装 (Trade Dress): 仿知名整体视觉
- 肖像权: 名人姓名 + 周边商品
- 假冒: "100% Authentic <大牌>" + 显著低价

## 4. 品牌字段伪装 (brand_misuse)
brand 填 Unbranded/Generic/小品牌, 但 title/描述暗示某大牌
→ reject, reason_category='brand_misuse'

## 5. 儿童产品 / CPC 兜底 (重要 — 即便类目没标 CPC 也要拦)
美国 CPSIA 要求所有 ≤12 岁儿童产品必须有 CPC + ASTM/CPSC 实验室测试报告,
搬运卖家无法提供。判定步骤:
1. title/bullets/description 是否暗示 ≤12 岁儿童使用:
   - 显式年龄: "for kids" / "ages 3+" / "infant" / "toddler" / "baby" / "婴儿" / "儿童"
   - 儿童专用形态: onesie / bib / diaper / stroller / crib / high chair /
     car seat / pacifier / sippy cup / swaddle
   - 玩具/教具: squishy / plush toy / stuffed animal / fidget toy / slime kit /
     play set / playmat / play tent
2. 排除明确成人/兽用情形: "for adult" / "dog onesie" / "Baby's Breath Flower"(花卉) /
   给妈妈用的 "Baby Pillow for Mom" 等 → 不算儿童产品
   **特别注意**: 成品形态是儿童典型玩具的 (squishy/slime/plush/fidget/play dough/
   stuffed animal), 不管文案写 DIY/adult/decor, 一律算儿童产品.
3. 命中且无排除 → reject, reason_category="Children's Products"
   (< 3 岁专用则 "Baby Products"), reason_text 引用 title 原文证据.

# 输出规范 (严格 JSON)

{
  "verdict": "pass" | "reject",
  "reason_category": "<候选之一; verdict=pass 时必须是 'none'>",
  "reason_text": "<=50字中文简短原因 (verdict=pass 时可留空)",
  "signals": {
    "has_trademark_symbol": true|false,
    "has_authorization_claim": true|false,
    "offensive_signals": []
  },
  "blacklist_brand_verdict": [
    {"brand": "<命中词>", "is_real_brand": true|false, "evidence": "<简短理由>"}
  ],
  "llm_confidence": "high" | "medium" | "low"
}

# 约束
- 默认 verdict=pass, 只有清晰证据才 reject
- verdict=pass 时 reason_category 必须是 "none"
- blacklist_brand_verdict: 对 L2 命中词每个都要 verdict (最多 10 个)
- 不凭空添加未列出的品牌

# 候选 reason_category (verdict=reject 时必选其一)
Intellectual Property / brand_misuse / Offensive Content /
Children's Products / Baby Products / 或下方政策清单中的 category_en
"""

# 静态合法类目（源仓 5 维判定的产出类目；37 政策 category_en 由 service 运行时扩入）
VALID_CATEGORIES = {
    "intellectual property", "brand_misuse", "offensive content",
    "children's products", "baby products", "none",
}  # fmt: skip
# 源仓 _coerce_result 的旧标签兼容映射
_LEGACY_CATEGORY_MAP = {
    "ip_infringement": "intellectual property",
    "offensive": "intellectual property",
    "forbidden_cat": "none",
    "needs_cert": "none",
}


def build_user_prompt(product: dict[str, Any], l2_hits: list[dict[str, Any]]) -> str:
    attrs = product.get("attrs") or {}
    hit_words: list[str] = []
    promo_phrases: list[str] = []
    sensitive_terms: list[str] = []
    for h in l2_hits:
        ev = h.get("evidence") or {}
        code = h.get("rule_code", "")
        if code == "l2_r7_content_promotional":
            promo_phrases += list(ev.get("strong_phrases", [])) + list(ev.get("allcaps_runs", []))
        elif code == "l2_r8_sensitive":
            sensitive_terms += list(ev.get("matched", []))
        else:
            hit_words += [m["brand"] for m in ev.get("matches", [])]
            hit_words += list(ev.get("matched_marks", []))
    lines = [
        f"brand: {product.get('brand') or '(空)'}",
        f"title: {product.get('title') or ''}",
    ]
    bullets = attrs.get("bullets") or []
    if bullets:
        lines.append("bullets: " + " | ".join(map(str, bullets[:8])))
    desc = str(attrs.get("description") or "")
    if desc:
        lines.append(f"description: {desc[:1500]}")
    lines.append("L2 命中词: " + (" / ".join(dict.fromkeys(hit_words)) if hit_words else "(无)"))
    if promo_phrases:
        lines.append("促销宣称词(判是否有事实依据): " + " / ".join(dict.fromkeys(promo_phrases)))
    if sensitive_terms:
        lines.append("敏感内容命中(判是否冒犯/违规): " + " / ".join(dict.fromkeys(sensitive_terms)))
    return "\n".join(lines)


def strip_json_fences(raw_text: str) -> str:
    """剥掉模型偶发的 markdown 代码栏（```json … ```）+ 前后缀杂文本容错。

    v4-flash 实测两类形态（对拍 round-2 栏 9/200、round-7 杂文本 3/200）：
    ①代码栏包裹；②JSON 前/后混入解释文字。处理=剥外层栏 + 截取最外层大括号；
    截断（缺闭合括号）仍解析失败 → fail-closed NR（不猜补）。"""
    t = raw_text.strip()
    if t.startswith("```"):
        t = t[3:]
        if t[:4].lower() == "json":
            t = t[4:]
        t = t.strip()
        if t.endswith("```"):
            t = t[:-3].strip()
    if not t.startswith("{"):
        i = t.find("{")
        if i >= 0:
            t = t[i:]
    if not t.endswith("}"):
        j = t.rfind("}")
        if j >= 0:
            t = t[: j + 1]
    return t


def l3_response_cacheable(raw_text: str) -> bool:
    """L3 响应可否入 llm_cache：JSON dict + 合法 verdict 才可复用（评审 round-2 R2-21）。

    坏响应一旦缓存，同输入重审永远复放 needs_review、无法自愈——结构异常的响应
    只用于本次判定（coerce 成 needs_review），不落缓存。
    """
    try:
        raw = json.loads(strip_json_fences(raw_text))
    except (ValueError, json.JSONDecodeError):
        return False
    if not isinstance(raw, dict):
        return False
    return str(raw.get("verdict") or "").strip().lower() in ("pass", "reject")


def coerce_l3_result(raw_text: str, valid_categories: set[str] | None = None) -> dict[str, Any]:
    """源仓 _coerce_result 移植：保守规范化 + is_real_brand 强制翻案。

    fail-closed（外部评审 round-1 A4 修正）：JSON 解析失败 / verdict 非法 → needs_review
    进人工复核，绝不默认 pass——审核是合规闸门，模型输出异常时放行等于闸门失效。

    valid_categories：reason_category 合法候选（小写）。缺省=静态两类；灌入 37 政策后
    由 service 传入扩展集（静态 + 政策 category_en），否则政策类目会被误判为非法回退 IP。
    """
    categories = valid_categories or VALID_CATEGORIES
    try:
        raw = json.loads(strip_json_fences(raw_text))
        if not isinstance(raw, dict):
            raise ValueError("非 dict")
    except (ValueError, json.JSONDecodeError):
        log.warning("audit.l3_bad_json", head=raw_text[:120])
        return {"verdict": "needs_review", "reason_category": "none",
                "reason_text": "[L3] 模型输出无法解析，转人工复核",
                "signals": {}, "blacklist_brand_verdict": [], "llm_confidence": "low",
                "parse_error": True}  # fmt: skip

    verdict = str(raw.get("verdict") or "").strip().lower()
    if verdict not in ("pass", "reject"):
        log.warning("audit.l3_bad_verdict", verdict=verdict)
        verdict = "needs_review"  # 非法 verdict → 人工复核（fail-closed）

    category = str(raw.get("reason_category") or "").strip().lower()
    if category not in categories:
        category = _LEGACY_CATEGORY_MAP.get(category) or (
            "intellectual property" if verdict == "reject" else "none"
        )
    if verdict == "pass":
        category = "none"

    reason_text = (str(raw.get("reason_text") or "").strip()[:500]) or None

    brand_verdict = raw.get("blacklist_brand_verdict") or []
    if not isinstance(brand_verdict, list):
        brand_verdict = []

    # ⭐ 强制翻案：verdict 合法为 pass 且任一 is_real_brand=true → reject。
    # 仅限结构合法的响应——顶层 verdict 非法时嵌套字段同样不可信，保持 needs_review
    # 交人工（避免坏输出误升硬拒进而污染反馈闭环，评审 round-2 R2-22）。
    real_hits = [v for v in brand_verdict if isinstance(v, dict) and v.get("is_real_brand") is True]
    if verdict == "pass" and real_hits:
        verdict = "reject"
        category = "intellectual property"
        if not reason_text:
            reason_text = f"[Trademark] 未授权引用品牌名 {real_hits[0].get('brand') or '?'}"
        log.info("audit.l3_override_reject", brands=[v.get("brand") for v in real_hits])

    signals = raw.get("signals") if isinstance(raw.get("signals"), dict) else {}
    confidence = str(raw.get("llm_confidence") or "medium").strip().lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"

    return {
        "verdict": verdict,
        "reason_category": category,
        "reason_text": reason_text,
        "signals": signals,
        "blacklist_brand_verdict": brand_verdict,
        "llm_confidence": confidence,
    }
