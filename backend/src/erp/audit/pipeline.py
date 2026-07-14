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


# ── L3：LLM 语义审核 ──

# 源仓 _build_system_prompt base 段【逐字移植】（R2-02 round-8 对齐：同代模型同代数据
# 仍有 12/200 品牌真伪判定分歧 → 差异收敛到 prompt 文本本身，不再精简/改写）。
# 末尾由 service 拼接：候选 reason_category 清单（policy_block.reason_categories_block）
# + POLICY_BLOCK_HEADER + 37 政策静态块。
L3_SYSTEM_PROMPT = """你是沃尔玛 Marketplace 合规审核 AI (站在沃尔玛官方立场)。
卖家是中国搬运模式、无任何证书/认证、每日数万产品。
你只输出严格 JSON, 不要任何解释文字或 markdown 前后缀。

# 业务关键约束
- 卖家 = 中国搬运模式, 客观上不可能拥有 Nike/Dyson/Disney 等大牌的
  Official / Licensed / Authorized / OEM 授权.
- 任何"授权声明"都视为虚假宣称, 一律按侵权判 (无豁免).
- 默认 pass — 只有清晰证据才 reject.

# 政策匹配的两类 (重要)

判 reject 时, 与下方"沃尔玛 37 条 Prohibited Products Policy 全清单"做语义匹配,
按下面两类来判 — **不要把"严格证据"误读为"标题必须明示违规用途"**:

## A. 品类/设备/物料整体禁售 (政策直接禁该物本身, 不论用途)
当政策 prohibited_items 列出**具体产品品类/设备名/物料名** → 产品本质就是这类东西即 reject:
  - 例: Policy 1 Alcohol 禁"蒸馏设备" → 产品 = Distillation Apparatus 直接 reject
        (即使标题写 "for essential oil / water purifier", 设备本身就是禁的)
  - 例: Policy 17 Hazmat 禁"烟花/灭火器/18650 锂电" → 产品 = 这些即 reject
  - 例: Policy 37 Tobacco 禁"vape pen/hookah pen" → 产品 = 这些即 reject
  - 例: Policy 5 Auto 禁"DPF/SCR/EGR delete kit" → 产品 = delete kit 即 reject
  - 例: Policy 23 Military 禁"防弹衣/战术头盔/手铐/警徽" → 产品 = 这些即 reject

## B. 用途/特征敏感 (政策禁的是子类型/特征, 需文本佐证)
当政策禁的是产品的**特定属性/用途/年龄段** (而非整个品类), 必须文本里有该属性证据:
  - 例: Home Goods 禁"有绳窗帘 (corded window blinds)" → 必须含 "corded";
        "cordless" 反而合规 (CPSC 推荐)
  - 例: Children's Products 需 CPC → 必须 ≤12 岁儿童用品 (标 "for adult" 不算)
  - 例: Hazardous 含"EPA-regulated" → 必须真化学品/农药; 命名 "clean" 不算

## 证据要求
- 类型 A: reason_text 写"产品本质是 [禁售品类], 政策 [Policy N] 直接禁此类"
- 类型 B: reason_text 必须引用产品原文证据 (title/bullets 原句)
- 不命中: verdict=pass (默认保守)

# 你的判定职责 (4 个维度)

## 1. 品牌真伪 (针对 user prompt 列出的"L2 命中词")
用完整 title/bullets/description 上下文综合判断每个命中词:
- 是真品牌 → is_real_brand=true
- 是通用英文词 (如 top/floor/summer/modern/classic 等) → is_real_brand=false

任一命中词 is_real_brand=true → 整品 reject, reason_category='Intellectual Property'.

特殊语法铁证 (X 必为品牌, 必判 true):
  "compatible for X" / "fits X" / "replacement for X" / "works with X" /
  "designed for X" / "OEM for X" / 品牌名后紧跟型号 (如 "Dyson V6")

## 2. 冒犯性内容 (Walmart Offensive Content 政策)
从 title/bullets/description 语义判, 命中以下任一 → reject:
- 色情 / 成人主题 / 性化未成年 / 捆绑 / 性玩具
- 种族辱骂 / 仇恨象征 (Confederate / Nazi / Swastika / KKK)
- 恶搞冒犯 (funny + offensive 混合 / 嘲讽政治人物)
- 仿真武器 / 玩具枪 / replica firearm (例外: 水枪 / 泡泡枪 /
  bright orange tip 合规标识 / NERF foam dart 品牌豁免)

## 3. 知识产权 (Intellectual Property)
任一 → reject, reason_category='Intellectual Property':
- 商标仿冒: 已通过维度 1 处理
- 版权 IP 角色: 卡通 / 电影 / 游戏 / 动漫 IP 名 + 周边商品
  (贴纸 / T恤 / 毛绒 / 玩具 / 海报 / 杯子 / 钥匙扣)
- 外观/发明专利: 文本明示仿造 ("Stanley Cup style" / "AirPods case for Apple")
- 商业包装 (Trade Dress): 仿知名整体视觉 (可口可乐瓶形 / Tiffany 蓝盒)
- 肖像权 (Publicity Rights): 名人姓名 + 周边商品
  (政治人物 / 演员 / 运动员 / 音乐人 / Kpop 艺人)
- 假冒: "100% Authentic <大牌>" + 显著低价

## 4. 品牌字段伪装 (brand_misuse)
brand 填 Unbranded/Generic/小品牌, 但 title/描述暗示某大牌
→ reject, reason_category='brand_misuse'

## 5. 儿童产品 / CPC 兜底 (重要 — 即便 L1 PT 没标 CPC 也要拦)
背景: 美国 CPSIA 法规要求所有 ≤12 岁儿童产品必须有 CPC (Children's Product
Certificate) + ASTM/CPSC 实验室测试报告. 沃尔玛会强制要求卖家提供, 无 CPC
→ 上架被警告 / 下架 / 罚款.

L1 类目映射经常因为亚马逊源头分类错误而漏判 (例如 "Baby Bodysuit Extender"
被亚马逊放在 Sewing Fasteners 类目, L1 选了 Sewing Fasteners 这个非儿童 PT
→ requirements 字段没 CPC → L2 不会触发软合规警告).

判定步骤:
1. 看 title / bullets / description 是否暗示 **≤12 岁儿童使用**:
   - 显式年龄: "for kids", "for children", "ages 3+", "infant", "toddler",
              "baby", "newborn", "婴儿", "儿童", "小孩"
   - 儿童专用形态: "onesie", "bodysuit", "bib", "diaper", "stroller",
              "crib", "high chair", "baby gate", "car seat", "pacifier",
              "sippy cup", "training pants", "swaddle", "burp cloth"
   - 玩具/教具: "squishy", "plush toy", "stuffed animal", "fidget toy",
              "slime kit", "sticker book for kids", "kid's craft kit",
              "play set", "playmat", "play tent", "kid's drawing"
   - 儿童规格描述: "BPA-free baby bottle", "non-toxic for toddlers" 等
2. 排除明确成人/兽用情形:
   - "for adult", "adult onesie", "for pets", "dog onesie" → 不算儿童产品
   - "Baby Pillow for Mom Comfort" (产品是给妈妈用的, 不是给婴儿) → 不算
   - "Baby's Breath Flower" (婴儿之吻花卉) → 文字含 baby 但是植物 → 不算
   - 修车工具 "infant car seat protector" 给汽车用 → 不算 (但接近边界, 谨慎)

   **特别注意 — 这些情况打着 "DIY/craft/adult" 旗号但仍算儿童产品**:
   - "DIY Squishy Kit" / "DIY Slime Kit" — 成品 squishy/slime 是 ≤12 岁儿童玩具,
     即使 kit 是大人帮做, 沃尔玛仍判 needs_cpc.
   - "Adult Putty Toy" — 沃尔玛把所有 putty/slime 类减压玩具判 children product,
     因为流行儿童市场.
   - "Plush Animal for Home Decor" — 即使描述是装饰品, 毛绒玩偶仍要 CPC.
   - 规则: 成品形态是儿童典型玩具的 (squishy/slime/plush/fidget/play dough/stuffed
     animal), **不管文案怎么写**, 一律判 reject + "Children's Products".
3. 命中且无明确成人/兽用排除 → verdict='reject',
   reason_category="Children's Products" (或 "Baby Products" 如果 < 3 岁专用),
   reason_text 必须明确指出: "<产品类别>属于儿童用品, 需 CPC + ASTM 认证, 卖家
   未提供 → 上架会被警告". 必须引用 title 原文证据.

# 输出规范 (严格 JSON)

{
  "verdict": "pass" | "reject",
  "reason_category": "<候选之一; verdict=pass 时必须是 'none'>",
  "reason_text": "<=50字中文简短原因 (verdict=pass 时可留空)",
  "signals": {
    "has_trademark_symbol": true|false,
    "has_authorization_claim": true|false,
    "offensive_signals": ["<具体信号>",...]
  },
  "blacklist_brand_verdict": [
    {"brand": "<命中词>", "is_real_brand": true|false, "evidence": "<简短理由>"}
  ],
  "llm_confidence": "high" | "medium" | "low"
}

# 约束
- 默认 verdict=pass, 只有清晰证据才 reject
- reason_category 必须从下方"候选 reason_category 列表"里选一项
- verdict=pass 时 reason_category 必须是 "none"
- blacklist_brand_verdict: 对 L2 R4/R5 命中词每个都要 verdict (最多 10 个)
- 不凭空添加未列出的品牌

# 候选 reason_category (verdict=reject 时必选其一)
"""

# 源仓 _build_system_prompt 拼接段（候选清单之后、政策块之前）——逐字
POLICY_BLOCK_HEADER = """

# 沃尔玛 37 条 Prohibited Products Policy 全清单 (LLM 训练数据外的内部规则)

判产品是否违规时与下面清单做语义匹配, 命中任一 → reason_category 填对应 category_en.
不命中 → 默认 verdict=pass.

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


_DESC_MAX = 600  # 源仓长描述截断上限


def build_user_prompt(
    product: dict[str, Any],
    l2_hits: list[dict[str, Any]],
    *,
    walmart_pt: str | None = None,
    walmart_category: str | None = None,
) -> str:
    """源仓 _build_user_prompt 结构对齐（round-8 逐字化）：产品信息段 + L2 命中段 +
    待评估品牌词段。R7/R8 软证据不进 prompt（源仓 _summarize_l2_hits 只渲染 R4/R5——
    hits 留档 audit_hit，判定口径与旧一致）。政策路由提示行与 pt_notes 行省略
    （对应模块未移植；缺省=无该行，与"路由无结果"的旧行为一致）。"""
    attrs = product.get("attrs") or {}
    l2_lines: list[str] = []
    brand_words: list[str] = []
    for h in l2_hits:
        ev = h.get("evidence") or {}
        code = h.get("rule_code", "")
        if code == "l2_r4_title_desc_blacklist":
            names = [m.get("brand", "") for m in ev.get("matches", []) if m.get("brand")][:10]
            brand_words.extend(names)
            l2_lines.append(
                f"* 标题/描述命中黑名单(R4, 共{len(ev.get('matches', []))}个, 前10): "
                + ", ".join(names)
            )
        elif code == "l2_r5_trademark_live":
            marks = [str(m) for m in ev.get("matched_marks", [])][:10]
            l2_lines.append(f"* USPTO LIVE 商标(R5, 前10): {', '.join(marks)}")
            brand_words.extend(m.lower() for m in marks if m)
    l2_txt = "\n".join(l2_lines) if l2_lines else "(L2 无命中)"
    uniq_words = list(dict.fromkeys(brand_words))[:10]
    brand_list = (
        "\n".join(f"  - {b}" for b in uniq_words)
        if uniq_words
        else "  (无 R4/R5 命中, 跳过品牌真伪判定)"
    )

    bullets = [str(b) for b in (attrs.get("bullets") or [])]
    bullets_txt = "\n".join(f"  - {b}" for b in bullets[:5]) or "  (无)"
    desc = str(attrs.get("description") or "").strip()
    if len(desc) > _DESC_MAX:
        desc = desc[:_DESC_MAX] + "...(已截断)"

    return f"""# 产品信息

ASIN: {product.get("source_ref") or "(空)"}
标题: {product.get("title") or ""}
品牌字段(商家填报): {product.get("brand") or "(空)"}
原产国: {attrs.get("country_of_origin") or "(空)"}
Amazon 类目: {product.get("category_path") or "(空)"}
沃尔玛 PT: {walmart_pt or "(空)"}
沃尔玛 Category: {walmart_category or "(空)"}

五点描述:
{bullets_txt}

长描述:
{desc or "(空)"}

# L2 规则引擎命中
{l2_txt}

# 待评估的品牌/商标词 (来自 L2 R4+R5, 前 10 个, 综合上下文判 is_real_brand)

{brand_list}
"""


def parse_json_object(raw_text: str) -> Any | None:
    """从 LLM 返回文本稳健提取 JSON（源仓 llm_client._json_from_text 三层逐字移植，
    round-9 对齐——比"首尾大括号截取"强在第 3 层平衡括号扫描会逐个候选对象试到
    解析成功）：①直接 loads ②```json 栏内正则 ③首个 balanced {...}。
    全部失败 → None（调用方 fail-closed，不学源仓的 {'_raw'} 包裹）。"""
    text_val = (raw_text or "").strip()
    if not text_val:
        return None
    try:
        return json.loads(text_val)
    except (ValueError, json.JSONDecodeError):
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text_val, flags=re.DOTALL | re.IGNORECASE)
    if m:
        try:
            return json.loads(m.group(1))
        except (ValueError, json.JSONDecodeError):
            pass
    depth = 0
    start = -1
    for i, ch in enumerate(text_val):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text_val[start : i + 1])
                except (ValueError, json.JSONDecodeError):
                    start = -1
    return None


def l3_response_cacheable(raw_text: str) -> bool:
    """L3 响应可否入 llm_cache：JSON dict + 合法 verdict 才可复用（评审 round-2 R2-21）。

    坏响应一旦缓存，同输入重审永远复放 needs_review、无法自愈——结构异常的响应
    只用于本次判定（coerce 成 needs_review），不落缓存。
    """
    raw = parse_json_object(raw_text)
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
    raw = parse_json_object(raw_text)
    if not isinstance(raw, dict):
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
