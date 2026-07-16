"""AI 属性填写（R2-03 增量 3）：Amazon 原始字段 → Walmart Visible/Orderable 结构化字段。

- prompt 逐字保真源仓 auto_listing/mapper.py（SYSTEM 零认证铁律 + USER_TEMPLATE 五块：
  visible 必填全量 / visible 可选前 20 / allOf 条件必填中文翻译 / orderable 必填 /
  orderable 可选前 10；静态 schema 块在前、产品数据在后——DeepSeek prefix cache 友好）。
- 分工不变：LLM 只填结构化字段；文案/品牌/图片/SKU/价格/日期/库存等系统后处理字段
  不喂给 LLM 也不采纳其输出（SYSTEM_*_POSTPROCESS_FIELDS，存储前再剥一层）。
- 三段纪律（RS-03a）：tx1 组 prompt+查缓存 → 无事务 HTTP → tx2 记账+coerce+写回；
  module='listing' 记账归因（0020）。
- fail-closed：非 JSON / 无 visible+orderable dict → 不写回（llm_cache cacheable 谓词
  同判，坏响应不入缓存，重试自愈——R2-21 纪律）。
- 结果落 product.attrs['walmart_fill'][wpt]，spec 构建器（增量 2）合并：
  系统字段 > LLM 填充 > 模板默认；零认证覆盖永远最后压轴。
"""

# ruff: noqa: E501  SYSTEM/USER_TEMPLATE 源仓逐字保真（mapper.py:40-106），不为行宽改写

import json
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.audit.llm import cache_key, llm_client
from erp.audit.pipeline import parse_json_object
from erp.core.db import system_tx
from erp.listing import wpt_schema
from erp.listing.spec import (
    SYSTEM_ORDERABLE_POSTPROCESS_FIELDS,
    SYSTEM_VISIBLE_POSTPROCESS_FIELDS,
    _resolve_wpt,
)

log = structlog.get_logger()

_AMAZON_DROP_FIELDS = {"image_urls", "images"}
_AMAZON_LIST_LIMITS = {"variation_asins": 20, "upc_list": 5, "ean_list": 5}
_DROP = object()

_VISIBLE_OPTIONAL_TOP = 20
_ORDERABLE_OPTIONAL_TOP = 10

# 模型参数：system_config 'listing.attr_fill'（{model, temperature, max_tokens}）逐键覆盖。
# 标准模型=deepseek-v4-flash（D-Q58）；temperature/max_tokens 源仓实测值。
_ATTR_FILL_DEFAULTS: dict[str, Any] = {
    "model": "deepseek-v4-flash",
    "temperature": 0.2,
    "max_tokens": 4096,
}

# ── SYSTEM prompt（源仓 mapper.py:40-77 逐字保真）──
SYSTEM = """你是 Walmart Marketplace 上架专家。任务：把 Amazon 商品原始字段映射为 Walmart MP_ITEM v5 的 Visible 和 Orderable 字段。

【运营场景】Amazon → Walmart 搬运（跟卖），用户**没有任何认证/证书/文档**：
  - 无 CPC（儿童产品认证）/ 无 CPSC 测试报告 / 无 NRTL 安规认证
  - 无 General Certificate of Conformity / 无 Prop 65 警告文档
  - 无品牌 / 无品牌官方保修 / 无组装说明 PDF / 无任何法务签字文档

【关键铁律】**绝对不要选会触发"必填认证文档"的 enum 值**。即便商品确实是儿童玩具/有 Prop 65 化学品/真要组装，也**强制选不触发文档依赖的 enum 值**（如下面规则 16-21）。否则我们没法填 children_product_certificate_id 之类的字段，Walmart 必拒。

硬性规则：
1. **只输出严格 JSON**，结构 {"visible":{...},"orderable":{...}}，禁止任何 markdown / 注释。
1a. **不要输出系统后处理字段**：visible.brand / productName / shortDescription / keyFeatures / mainImageUrl / productSecondaryImageURL / swatchImageUrl，以及 orderable.sku / productIdentifiers / price / country_of_origin_substantial_transformation / specProductType / inventory / startDate / endDate / MustShipAlone / fulfillmentLagTime。这些字段由系统在 LLM 之后用 Amazon 原始数据、UPC、定价、库存和店铺配置自动填充；Amazon 文案只供你理解商品并填写其它结构化字段。
2. **所有必填字段必须给值**，无法直接从 Amazon 数据推断的，给一个合理的合规默认。
3. **枚举字段必须严格从给定 enum 列表里选一个原文值**；如果 Amazon 数据是 'Pinch Pleat' 但 enum 只有 ['Pleated','Tab Top','Grommet']，必须挑其中之一（语义最近的或第一个），**绝对不能直接输出 'Pinch Pleat'**。**宁可填 enum[0] 也不要填非法值**。
4. **数组字段必须满足 minItems 约束**：数组类字段宁缺勿空——**没有真实数据时直接不输出该字段**，不要写 `[]`、`""`、`null`。keyFeatures、图片、inventory 由系统后处理填充，不要输出。
4a. **数组类字段值必须是 JSON 数组**，绝不能是裸字符串。常见错例：`pattern`/`color`/`material`/`shape` 等许多看似单值字段在 spec 里是 array 类型 → 必须 `["Striped"]` 而不是 `"Striped"`。判断标准：spec.type == "array" → 永远用数组包；不确定就直接省略该字段。
4b. **URL 类数组字段** (productSecondaryImageURL, swatchImageUrl 等) 没有 ≥minItems 张真实 URL 时**直接不输出**，禁止填 `"No"` / `"Not Available"` / `[""]` 等占位（Walmart 拒收）。
5. **不要把不属于 schema 的字段写进输出**；不要造字段名变体（如 hasWrittenWarranty vs has_written_warranty，只用 schema 里的 snake_case 名）。
6. **数值字段用数字**，不要带单位字符串。维度对象 {"unit":"in","measure":数字}。
7. **长度严格遵守**：`manufacturer` 字符串 **maxLength=60 字符**（超出 Walmart 直接拒，EXT_DATA_ERROR_01076067496949）。productName / shortDescription / keyFeatures 由系统后处理填充，不要输出。
8. **不要把 Amazon 原品牌名写入任何输出字段**（如 FOTOSOK、Phueut 等）。品牌字段由系统强制写 "Unbranded"。
9. **绝对禁止编造任何 URL**（assemblyInstructions、warrantyURL 等）。Amazon 数据没给就不要这个字段。
10. visible.brand 由系统强制写 "Unbranded"，不要输出。

【搬运场景"零认证"强制规则 16-21】（违反会被 Walmart 拒收，调用方也会强制覆盖你的输出）：
16. **isAssemblyRequired** 一律填 "No"（即便橱柜需组装），避免 assemblyInstructions / suggested_number_of_people_for_assembly 链。
17. **has_written_warranty** 一律填 "No"（不是 'Yes - Warranty Text' 也不是 'Yes - Warranty URL'），避免 warrantyText / warrantyURL 必填。
18. **isProp65WarningRequired** 一律填 "No"（即便商品在加州 Prop 65 清单），避免 prop65WarningText 必填。
19. **certification_type** 一律填 "Neither of these applies"（不是 'Children's Product Certificate' 也不是 'General Certificate of Conformity'），避免 children_product_certificate_id / general_certificate_id 必填。
20. **has_nrtl_listing_certification** 一律填 "No"（即便是电器类），避免 nrtl_information 必填。
21. **儿童相关字段**（targetAudience / age_group / childrenProductCertificate*）：能不填就不填；如必须填且 enum 含 'Adult' / 'No' / 'Not for children'，**一律选这些**避开儿童 CPC 法规。

22. **危险品类避雷**：如果 Amazon 商品涉及以下，结构化字段不要主动强调这些卖点（让 Walmart 当成普通商品过审）：
    - 处方药/医疗器械（避免 PROHIBITED Medical Devices 触发）
    - 儿童玩具/婴儿用品（避免 CPC 必填）
    - 电器（除非确认 PT 不要 NRTL）
    - 任何枪支配件 / 军用品
"""

# ── USER 模板（源仓 mapper.py:79-106 逐字保真；{sku} 槽在新系统传 ASIN/source_ref）──
USER_TEMPLATE = """## walmart_product_type: {pt}

## Walmart Visible.{pt} 必填字段及定义
{visible_required_block}

## Walmart Visible.{pt} 可选字段（按需填）
{visible_optional_block}

## ⚠️ 条件必填依赖（spec allOf if-then；触发后必须同时填，否则 Walmart 拒收）
{conditional_block}

## Walmart Orderable 必填字段及定义
{orderable_required_block}

## Walmart Orderable 常用可选字段
{orderable_optional_block}

## ▼▼▼ 本次特定数据（以下内容每次变化）▼▼▼

## SKU: {sku}

## Amazon 原始数据
```json
{amazon_json}
```

请输出严格 JSON：{{"visible": {{...}}, "orderable": {{...}}}}
"""


def _enum_str(enum: Any) -> str:
    if isinstance(enum, list):
        return ",".join(str(x) for x in enum[:30])
    return str(enum)[:300]


def _sub_props(node: dict[str, Any]) -> str:
    props = node.get("properties")
    if not isinstance(props, dict):
        return ""
    return "; ".join(
        f"{k}({v.get('type', '?') if isinstance(v, dict) else '?'}"
        + (f", enum=[{_enum_str(v['enum'])}]" if isinstance(v, dict) and v.get("enum") else "")
        + ")"
        for k, v in props.items()
    )


def format_field_block(fields: list[wpt_schema.FieldSpec], max_fields: int | None = None) -> str:
    """字段块渲染（源仓 _format_field_block 语义保真；子结构从 raw 节点派生）。"""
    if max_fields:
        fields = fields[:max_fields]
    lines = []
    for f in fields:
        bits = [f"- **{f.name}** ({f.type})"]
        if f.title:
            bits.append(f"标题={f.title}")
        if f.desc:
            bits.append(f"说明={f.desc[:300]}")
        if f.format in ("date", "date-time"):
            fmt_hint = (
                "YYYY-MM-DD" if f.format == "date" else "ISO 8601 含 T（如 2026-01-31T00:00:00Z）"
            )
            bits.append(f"日期格式必须 {fmt_hint}；不确定具体日期就省略本字段，禁止 'N/A'/纯年份")
        if f.enum:
            bits.append(f"枚举=[{_enum_str(f.enum)}]")
        if f.item_type:
            bits.append(f"数组元素类型={f.item_type}")
        items = f.raw.get("items")
        if isinstance(items, dict) and (sub := _sub_props(items)):
            bits.append(f"数组元素结构: {{{sub}}}")
        if sub := _sub_props(f.raw):
            bits.append(f"对象字段: {{{sub}}}")
        if f.object_required:
            bits.append(f"对象必填={f.object_required}")
        if f.example:
            bits.append(f"示例={str(f.example)[:120]}")
        lines.append("  ".join(bits))
    return "\n".join(lines) if lines else "（无）"


def format_conditional_block(schema: wpt_schema.PtSchema) -> str:
    """allOf if-then → 中文条件依赖列表（源仓 _format_conditional_block 保真）。"""
    lines = []
    for cond in schema.all_of:
        if "if" not in cond or "then" not in cond:
            continue
        if_props = cond["if"].get("properties", {})
        then_req = cond.get("then", {}).get("required", [])
        if not then_req or not isinstance(if_props, dict):
            continue
        if_parts = []
        for fname, fcond in if_props.items():
            if not isinstance(fcond, dict):
                continue
            if fcond.get("enum"):
                if_parts.append(f"{fname} ∈ {fcond['enum']}")
            elif fcond.get("contains", {}).get("enum"):
                if_parts.append(f"{fname} 数组含 {fcond['contains']['enum']} 任一值")
            elif fcond.get("type"):
                if_parts.append(f"{fname} 是 {fcond['type']} 类型")
            else:
                if_parts.append(f"{fname} 已填")
        if not if_parts:
            continue
        lines.append(f"- 若 {' AND '.join(if_parts)} → 必须同时填: {then_req}")
    return "\n".join(lines) if lines else "（无）"


def _compact_text(value: str) -> str:
    return " ".join(str(value).split())


def _trim_amazon_value(key: str, value: Any) -> Any:
    """源仓 _trim_amazon_value_for_llm 保真：剔图片/缓存键/空值，压空白，限长列表。"""
    if key in _AMAZON_DROP_FIELDS or key.startswith("__cached_"):
        return _DROP
    if value in (None, "", "N/A"):
        return _DROP
    if isinstance(value, str):
        return _compact_text(value)
    if isinstance(value, list):
        limit = _AMAZON_LIST_LIMITS.get(key)
        items = value[:limit] if limit is not None else value
        out = [t for t in (_trim_amazon_value(key, i) for i in items) if t is not _DROP]
        return out if out else _DROP
    if isinstance(value, dict):
        outd = {}
        for k in sorted(value):
            t = _trim_amazon_value(str(k), value[k])
            if t is not _DROP:
                outd[str(k)] = t
        return outd if outd else _DROP
    return value


def amazon_view_for_llm(product: dict[str, Any]) -> dict[str, Any]:
    """产品 → 只给 LLM 看的稳定 Amazon 视图（源仓 _amazon_for_llm 语义）。"""
    attrs = product.get("attrs") or {}
    base: dict[str, Any] = {
        "asin": product.get("source_ref"),
        "title": product.get("title"),
        "brand": product.get("brand"),
        "category_path": product.get("category_path"),
    }
    base.update({k: v for k, v in attrs.items() if k != "walmart_fill"})
    out = {}
    for k in sorted(base):
        t = _trim_amazon_value(str(k), base[k])
        if t is not _DROP:
            out[str(k)] = t
    return out


def build_messages(
    product: dict[str, Any],
    wpt: str,
    pt_schema: wpt_schema.PtSchema,
    orderable_schema: wpt_schema.PtSchema | None,
) -> list[dict[str, str]]:
    """五块 schema（静态前缀）+ 产品数据（动态后缀）——prefix cache 友好结构保留。"""
    vis_fields = [
        f for f in pt_schema.field_specs() if f.name not in SYSTEM_VISIBLE_POSTPROCESS_FIELDS
    ]
    vis_req = [f for f in vis_fields if f.required]
    vis_opt = [f for f in vis_fields if not f.required]
    ord_fields = (
        [
            f
            for f in orderable_schema.field_specs()
            if f.name not in SYSTEM_ORDERABLE_POSTPROCESS_FIELDS
        ]
        if orderable_schema is not None
        else []
    )
    ord_req = [f for f in ord_fields if f.required]
    ord_opt = [f for f in ord_fields if not f.required]
    user = USER_TEMPLATE.format(
        pt=wpt,
        visible_required_block=format_field_block(vis_req),
        visible_optional_block=format_field_block(vis_opt, _VISIBLE_OPTIONAL_TOP),
        conditional_block=format_conditional_block(pt_schema),
        orderable_required_block=format_field_block(ord_req),
        orderable_optional_block=format_field_block(ord_opt, _ORDERABLE_OPTIONAL_TOP),
        sku=str(product.get("source_ref") or product.get("id")),
        amazon_json=json.dumps(amazon_view_for_llm(product), ensure_ascii=False),
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def fill_cacheable(raw_text: str) -> bool:
    """可缓存谓词：JSON dict 且 visible/orderable 均为 dict（坏响应不入缓存）。"""
    raw = parse_json_object(raw_text)
    return (
        isinstance(raw, dict)
        and isinstance(raw.get("visible"), dict)
        and isinstance(raw.get("orderable"), dict)
    )


def coerce_fill(raw_text: str) -> dict[str, Any] | None:
    """解析填充结果。fail-closed：非 JSON / visible/orderable 非 dict → None（不写回）。

    系统后处理字段无论 LLM 输出与否一律剥除（构建器还会再压一层零认证覆盖）。
    """
    raw = parse_json_object(raw_text)
    if not isinstance(raw, dict):
        log.warning("listing.attr_fill_bad_json", head=raw_text[:120])
        return None
    visible, orderable = raw.get("visible"), raw.get("orderable")
    if not isinstance(visible, dict) or not isinstance(orderable, dict):
        log.warning("listing.attr_fill_bad_shape", keys=sorted(raw)[:8])
        return None
    return {
        "visible": {k: v for k, v in visible.items() if k not in SYSTEM_VISIBLE_POSTPROCESS_FIELDS},
        "orderable": {
            k: v for k, v in orderable.items() if k not in SYSTEM_ORDERABLE_POSTPROCESS_FIELDS
        },
    }


async def _fill_cfg(session: AsyncSession) -> dict[str, Any]:
    row = (
        await session.execute(
            text("SELECT value FROM app.system_config WHERE key = 'listing.attr_fill'")
        )
    ).scalar_one_or_none()
    cfg = dict(_ATTR_FILL_DEFAULTS)
    if isinstance(row, dict):
        cfg.update({k: row[k] for k in ("model", "temperature", "max_tokens") if k in row})
    return cfg


async def _load_product(session: AsyncSession, product_id: int) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, source_ref, title, brand, images, attrs,"
                    " category_path, amazon_leaf_id FROM app.product WHERE id = :p"
                ),
                {"p": product_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


_WRITE_FILL_SQL = text(
    "UPDATE app.product SET attrs = jsonb_set("
    " CASE WHEN attrs ? 'walmart_fill' THEN attrs"
    "      ELSE attrs || '{\"walmart_fill\": {}}'::jsonb END,"
    " ARRAY['walmart_fill', :wpt], cast(:val AS jsonb), true),"
    " updated_at = now()"
    " WHERE id = :p"
)


async def _apply_fill(
    session: AsyncSession, product_id: int, wpt: str, result: dict[str, Any] | None, model: str
) -> bool:
    if result is None:
        log.info("listing.attr_fill_rejected", product_id=product_id, wpt=wpt)  # fail-closed
        return False
    await session.execute(
        _WRITE_FILL_SQL,
        {
            "p": product_id,
            "wpt": wpt,
            "val": json.dumps({**result, "model": model}, ensure_ascii=False),
        },
    )
    log.info("listing.attrs_filled", product_id=product_id, wpt=wpt)
    return True


async def fill_product_attrs(
    sessions: async_sessionmaker[AsyncSession],
    *,
    product_id: int,
    wpt: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """组 prompt → LLM 填充 → 写回 product.attrs['walmart_fill'][wpt]。

    三段纪律（RS-03a）：HTTP 期间不持任何事务。模型参数：显式实参 >
    system_config 'listing.attr_fill' > 默认。filled=False 场景：
    product_not_found / no_wpt（解析失败）/ no_pt_schema（fields 未导入，不调 LLM）/
    llm_unavailable / 输出非法（coerce None，fail-closed 不写回）。
    """
    # ── tx1：载产品 + 定 WPT + 载 schema + 组 prompt + 查缓存（命中同事务终局）──
    async with system_tx(sessions) as s:
        cfg = await _fill_cfg(s)
        model = model or str(cfg["model"])
        temperature = float(cfg["temperature"]) if temperature is None else temperature
        max_tokens = int(cfg["max_tokens"]) if max_tokens is None else max_tokens
        product = await _load_product(s, product_id)
        if product is None:
            return {"product_id": product_id, "filled": False, "reason": "product_not_found"}
        if wpt is None:
            try:
                wpt, _src = await _resolve_wpt(s, product)
            except Exception:
                return {"product_id": product_id, "filled": False, "reason": "no_wpt"}
        pt_schema = await wpt_schema.load_pt_schema(s, wpt)
        if pt_schema is None:
            return {
                "product_id": product_id,
                "wpt": wpt,
                "filled": False,
                "reason": "no_pt_schema",
            }
        orderable_schema = await wpt_schema.load_orderable_schema(s)
        messages = build_messages(product, wpt, pt_schema, orderable_schema)
        key = cache_key(model, messages, temperature, max_tokens)
        cached = await llm_client.check_cache(
            s,
            key=key,
            model=model,
            team_id=product["team_id"],
            object_type="product",
            object_id=product_id,
            cacheable=fill_cacheable,
            module="listing",
        )
        if cached is not None:
            written = await _apply_fill(s, product_id, wpt, coerce_fill(cached), model)
            return {"product_id": product_id, "wpt": wpt, "filled": written, "cache_hit": True}

    # ── HTTP（无事务）──
    try:
        content, pt_toks, ct_toks, cached_toks = await llm_client.call_provider(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens
        )
    except Exception as e:
        log.warning("listing.attr_fill_unavailable", product_id=product_id, error=str(e)[:200])
        return {"product_id": product_id, "wpt": wpt, "filled": False, "reason": "llm_unavailable"}

    # ── tx2：记账 + coerce + 写回 ──
    async with system_tx(sessions) as s:
        cost = await llm_client.record_result(
            s,
            key=key,
            model=model,
            content=content,
            prompt_tokens=pt_toks,
            completion_tokens=ct_toks,
            team_id=product["team_id"],
            object_type="product",
            object_id=product_id,
            cacheable=fill_cacheable,
            module="listing",
            cached_tokens=cached_toks,
        )
        written = await _apply_fill(s, product_id, wpt, coerce_fill(content), model)
        return {
            "product_id": product_id,
            "wpt": wpt,
            "filled": written,
            "cost_usd": cost,
        }
