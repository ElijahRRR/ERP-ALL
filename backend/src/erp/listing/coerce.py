"""提交前清洗/coerce 管线（R2-03 增量 4；源仓 auto_listing/mapper.py 函数群保真移植）。

每个函数对应一类实测渠道拒绝（错误码在各 docstring）——重写必然重踩坑，
故逻辑逐函数保真；仅数据载体从 field_summary 摘要换成 refdata.pt_spec.fields
的原始 schema 节点（wpt_schema.PtSchema，零损）。

链序（源仓 main.py prepare_one_async 保真）：
  force_overrides(增量2 已在构建器) → force_amazon_copy → fill_known_walmart_required
  → fill_missing_required → fix_type_mismatches → fix_invalid_enums
  → strip_unknown_fields → clean_state_restrictions → drop_empty_optional_fields
  → drop_min_items_violations → enforce_copy_limits → round_decimals_to_2
（终校验 validate 在 validator.py，提交边界另有 sanitize_feed_numbers。）
"""

# ruff: noqa: E501, PLR0911, PLR0912, PLR0915, PLR2004, PLR1714, SIM102  源仓函数群逐函数保真，不为 lint 重构

import copy
import re
from typing import Any

from erp.listing.wpt_schema import PtSchema

# Walmart 实际 reject 但 spec top-level required[] 没标的字段（conditional/allOf 链触发）
# 来源：实战 reconcile 错误 EXT_DATA_ERROR_72600149546850 统计（源仓白名单逐字保真）。
# 注：搬运场景下，warrantyText / nrtl_information / prop65WarningText 等"认证文档类"
# 不进白名单——零认证覆盖已把触发字段设为 No，根本不会触发。
KNOWN_WALMART_CONDITIONAL_REQUIRED: dict[str, tuple[str, Any]] = {
    "finish":             ("string",  "Polished"),
    "container_material": ("array",   ["Plastic"]),
    "leg_color":          ("string",  "Black"),
    "leg_material":       ("string",  "Metal"),
    "pump_type":          ("string",  "Hand Pump"),   # enum 因 PT 而异，fix_invalid_enums 兜底
    "powerType":          ("array",   ["Battery"]),   # 同上
    "batterySize":        ("string",  "AA"),
    "numberOfSheets":     ("integer", 1),
    "minimum_height":     ("object",  {"unit": "in", "measure": 0.1}),
    "maximum_height":     ("object",  {"unit": "in", "measure": 100.0}),
    "diameter":           ("object",  {"unit": "in", "measure": 1.0}),
    "core_diameter":      ("object",  {"unit": "mm", "measure": 1.0}),
}  # fmt: skip

_EMPTYISH: tuple[Any, ...] = (None, "", [], {})
_SCRUB_EXEMPT_BRANDS = ("unbranded", "n/a", "unknown", "generic")


def _effective_enum(node: dict[str, Any]) -> list[Any] | None:
    """字段有效枚举：顶层 enum；array 无顶层 enum 时提升 items.enum
    （源仓 _summarize_prop 的 hoist 语义——数组枚举校验/默认值都吃这个）。"""
    enum = node.get("enum")
    if isinstance(enum, list) and enum:
        return enum
    if node.get("type") == "array":
        items = node.get("items")
        if isinstance(items, dict):
            i_enum = items.get("enum")
            if isinstance(i_enum, list) and i_enum:
                return i_enum
    return None


def safe_default_for(node: dict[str, Any]) -> Any:
    """对必填字段给最保守默认值（源仓 _safe_default_for 保真）。

    优先级：'No' > 'None' > 'Not Applicable' > enum[0]。
    URL 类数组禁占位 → None（drop_min_items_violations 兜底删，
    EXT_DATA_ERROR_49505365506868）。对象类型递归填子字段（netContent/measure/unit 特例）。
    """
    if node.get("type") == "array":
        items = node.get("items") or {}
        if items.get("format") in ("uri", "url"):
            return None

    enum = _effective_enum(node)
    if isinstance(enum, list) and enum:
        if node.get("type") == "array":
            # smallPartsWarnings 类：选 "0 - No warning applicable" 这种
            for v in enum:
                low = str(v).lower()
                if "no" in low and ("applicable" in low or "warning" in low):
                    return [v]
            return [enum[0]]
        for safe in ("No", "None", "Not Applicable", "Unbranded"):
            if safe in enum:
                return safe
        return enum[0]

    if node.get("type") == "object":
        sub_props = node.get("properties") or {}
        if sub_props:
            obj: dict[str, Any] = {}
            for sub_name, sub_def in sub_props.items():
                if not isinstance(sub_def, dict):
                    continue
                sub_val = safe_default_for(
                    {"type": sub_def.get("type", "string"), "enum": sub_def.get("enum")}
                )
                if sub_val is not None:
                    obj[sub_name] = sub_val
                else:
                    t = sub_def.get("type")
                    if t in ("number", "integer"):
                        obj[sub_name] = 1
                    elif t == "string":
                        obj[sub_name] = ""
                    elif t == "array":
                        obj[sub_name] = []
            if "productNetContentUnit" in obj and not obj.get("productNetContentUnit"):
                obj["productNetContentUnit"] = "Each"
            if "productNetContentMeasure" in obj and not obj.get("productNetContentMeasure"):
                obj["productNetContentMeasure"] = 1
            if "measure" in obj and not obj.get("measure"):
                obj["measure"] = 1
            if "unit" in obj and not obj.get("unit"):
                u_def = sub_props.get("unit", {})
                u_enum = u_def.get("enum") or [] if isinstance(u_def, dict) else []
                obj["unit"] = u_enum[0] if u_enum else "in"
            return obj if obj else None
    return None


def scrub_brand_from_text(text: str, brands_to_remove: list[str]) -> str:
    """从文本中去除品牌名（不区分大小写、全词匹配），保持空格整洁（源仓保真）。"""
    if not text or not brands_to_remove:
        return text
    out = text
    for b in brands_to_remove:
        if not b or b.lower() in _SCRUB_EXEMPT_BRANDS:
            continue
        out = re.sub(rf"\b{re.escape(b)}\b", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s{2,}", " ", out).strip(" ,;-")
    return re.sub(r"\s+([,.;:])", r"\1", out)


def force_amazon_copy(
    visible: dict[str, Any], amazon: dict[str, Any], brands_to_remove: list[str]
) -> dict[str, Any]:
    """**强制**用 Amazon 原文填 productName/shortDescription/keyFeatures（源仓保真）。

    LLM 不重写文案，只做结构化字段映射；文案与 Amazon 一致（仅去品牌、截长度）。
    keyFeatures 不够 4 条时拆句补齐（EXT_DATA_ERROR_55506974520167——部分 PT
    minItems 提到 4/5/6）；shortDescription 不足 60 词拼 title 补。
    """
    visible = dict(visible)

    def _clean_text(value: Any) -> str:
        if value is None:
            return ""
        cleaned = scrub_brand_from_text(str(value), brands_to_remove).strip()
        cleaned = re.sub(r"[•·▪▫]+", "", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    def _sentences(text: str) -> list[str]:
        text = _clean_text(text)
        if not text:
            return []
        parts = [
            p.strip() for p in re.split(r"(?:\n+|[.;。；!?！？]\s+)", text) if len(p.strip()) >= 25
        ]
        if parts:
            return parts
        words = text.split()
        if len(words) >= 24:
            chunks: list[str] = []
            cur: list[str] = []
            for w in words:
                cur.append(w)
                if len(" ".join(cur)) >= 120:
                    chunks.append(" ".join(cur))
                    cur = []
                    if len(chunks) >= 4:
                        break
            if cur and len(chunks) < 4:
                chunks.append(" ".join(cur))
            if chunks:
                return chunks
        return [text] if len(text) >= 10 else []

    title = (amazon.get("title") or "").strip()
    if title:
        clean = _clean_text(title)
        if clean:
            visible["productName"] = clean[:199]
    else:
        for fallback in _sentences(
            amazon.get("long_description")
            or amazon.get("description")
            or amazon.get("product_description")
            or ""
        ):
            visible["productName"] = fallback[:199]
            break

    bullets = amazon.get("bullet_points") or []
    if isinstance(bullets, str):
        bullets = [b.strip() for b in bullets.split("\n") if b.strip()]
    cleaned: list[str] = []
    if isinstance(bullets, list) and bullets:
        for b in bullets:
            if not isinstance(b, str):
                continue
            c = _clean_text(b)
            if c:
                cleaned.append(c[:500])

    if len(cleaned) < 4:
        extras: list[str] = []
        source_texts = [
            *cleaned,
            amazon.get("long_description", ""),
            amazon.get("description", ""),
            amazon.get("product_description", ""),
            title,
        ]
        for text in source_texts:
            for p in _sentences(text):
                if p not in cleaned and p not in extras:
                    extras.append(p[:500])
                if len(cleaned) + len(extras) >= 4:
                    break
            if len(cleaned) + len(extras) >= 4:
                break
        cleaned.extend(extras)

    if len(cleaned) >= 4:
        visible["keyFeatures"] = cleaned[:7]
    elif cleaned:
        visible["keyFeatures"] = cleaned  # 仍不足 4：原样输出，enforce_copy_limits 报警

    if isinstance(bullets, list) and bullets:
        paragraph = " ".join(_clean_text(b) for b in bullets if isinstance(b, str))
    else:
        paragraph = _clean_text(
            amazon.get("long_description")
            or amazon.get("description")
            or amazon.get("product_description")
            or ""
        )
    if len(paragraph.split()) < 60 and title:
        tcl = _clean_text(title)
        paragraph = (tcl + ". " + paragraph) if tcl and paragraph else (tcl or paragraph)
    if paragraph:
        visible["shortDescription"] = paragraph[:4000]

    return visible


def _evaluate_if_condition(if_clause: dict[str, Any], visible: dict[str, Any]) -> bool:
    """评估 JSON Schema if 条件（Walmart spec 实际用到的子集；源仓保真）。"""
    for r in if_clause.get("required", []):
        if visible.get(r) in _EMPTYISH:
            return False
    for fname, fcond in if_clause.get("properties", {}).items():
        if not isinstance(fcond, dict):
            continue
        v = visible.get(fname)
        enum = fcond.get("enum")
        if enum is not None and v not in enum:
            return False
        ftype = fcond.get("type")
        if ftype == "string" and not isinstance(v, str):
            return False
        if ftype == "array" and not isinstance(v, list):
            return False
        if ftype == "object" and not isinstance(v, dict):
            return False
        if ftype in ("number", "integer") and not isinstance(v, int | float):
            return False
        contains = fcond.get("contains", {})
        if contains:
            if not isinstance(v, list):
                return False
            sub_enum = contains.get("enum")
            if sub_enum and not any(item in sub_enum for item in v):
                return False
    return True


def resolve_conditional_required(
    visible: dict[str, Any], schema: PtSchema
) -> tuple[set[str], list[str]]:
    """解析 allOf if-then，返回当前 visible 触发的额外 required 集合（源仓保真）。"""
    extra_required: set[str] = set()
    triggers: list[str] = []
    for cond in schema.all_of:
        if "if" not in cond or "then" not in cond:
            continue
        if not _evaluate_if_condition(cond["if"], visible):
            continue
        for f in cond.get("then", {}).get("required", []):
            if f not in extra_required:
                extra_required.add(f)
                if_props = cond["if"].get("properties", {})
                trigger_desc = ", ".join(f"{k}={visible.get(k)!r}" for k in if_props) or "条件成立"
                triggers.append(f"{f} (因 {trigger_desc})")
    return extra_required, triggers


def fill_known_walmart_required(
    visible: dict[str, Any], schema: PtSchema
) -> tuple[dict[str, Any], list[str]]:
    """补条件必填（通用 allOf 解析 + 实战白名单兜底）；不动点迭代 ≤6 轮修级联触发
    （源仓 2026-06-20 教训：白名单填的 powerType 又触发新条件，单遍会漏）。"""
    pt_props = schema.properties
    visible = dict(visible)
    filled: list[str] = []

    for _ in range(6):
        round_filled: list[str] = []
        extra_required, _triggers = resolve_conditional_required(visible, schema)
        for fname in extra_required:
            if fname not in pt_props or visible.get(fname) not in _EMPTYISH:
                continue
            node = pt_props[fname] if isinstance(pt_props[fname], dict) else {}
            default = safe_default_for(node)
            if default is None:
                ft = node.get("type", "string")
                if ft == "string":
                    default = "Not Available"
                elif ft in ("number", "integer"):
                    default = 1
                elif ft == "array":
                    default = ["Not Available"]
                elif ft == "object":
                    default = {}
            if default is not None:
                visible[fname] = default
                round_filled.append(f"{fname}={default!r} (条件触发)")

        for fname, (ftype, default) in KNOWN_WALMART_CONDITIONAL_REQUIRED.items():
            if fname not in pt_props or visible.get(fname) not in _EMPTYISH:
                continue
            node = pt_props[fname] if isinstance(pt_props[fname], dict) else {}
            spec_type = node.get("type", ftype)
            if spec_type != ftype:
                if spec_type == "string":
                    visible[fname] = str(default if not isinstance(default, list | dict) else "")
                elif spec_type == "array":
                    visible[fname] = default if isinstance(default, list) else [str(default)]
                elif spec_type == "object":
                    visible[fname] = default if isinstance(default, dict) else {}
                else:
                    visible[fname] = default
            else:
                visible[fname] = default
            round_filled.append(f"{fname}={visible[fname]!r} (白名单)")

        if not round_filled:
            break  # 不动点收敛
        filled.extend(round_filled)

    return visible, filled


def fill_missing_required(
    visible: dict[str, Any], schema: PtSchema
) -> tuple[dict[str, Any], list[str]]:
    """缺失必填填安全默认（源仓保真）：闭枚举安全序列 > 对象递归 >
    'Not Available'/1/['Not Available'] 类型兜底。"""
    props = schema.properties
    visible = dict(visible)
    filled: list[str] = []
    for name in schema.required:
        if visible.get(name) not in _EMPTYISH:
            continue
        node = props.get(name, {}) if isinstance(props.get(name), dict) else {}
        default = safe_default_for(node)
        if default is None:
            ft = node.get("type", "string")
            if ft == "string":
                default = "Not Available"
            elif ft in ("number", "integer"):
                default = 1
            elif ft == "array":
                default = ["Not Available"]
        if default is not None:
            visible[name] = default
            filled.append(f"{name}={default!r}")
    return visible, filled


_URL_PLACEHOLDER = {"no", "none", "not applicable", "not available", "n/a", "yes"}
_URL_SCHEMES = ("http://", "https://", "ftp://")


def fix_type_mismatches(
    visible: dict[str, Any], schema: PtSchema
) -> tuple[dict[str, Any], list[str]]:
    """类型 coerce（源仓保真）：spec=array 值非 list → 包/删（EXT_DATA_ERROR_50716566635066）；
    URL 数组占位清除（49505365506868）；对象 enum 子字段修复（IB.VALIDATION.DATA.001）。"""
    props = schema.properties
    visible = dict(visible)
    fixes: list[str] = []

    for name, val in list(visible.items()):
        node = props.get(name, {}) if isinstance(props.get(name), dict) else {}
        ftype = node.get("type")

        if ftype == "array" and not isinstance(val, list):
            if val is None or val == "":
                del visible[name]
                fixes.append(f"{name}: 空值删除（spec 要求 array）")
            elif isinstance(val, str):
                items = node.get("items") or {}
                items_format = items.get("format") or ""
                if items_format in ("uri", "url") or "url" in name.lower():
                    if not val.lower().startswith(_URL_SCHEMES):
                        del visible[name]
                        fixes.append(f"{name}: 非 URL 字符串 {val!r} 删除（spec 要求 URL 数组）")
                        continue
                if val.strip().lower() in _URL_PLACEHOLDER:
                    del visible[name]
                    fixes.append(f"{name}: 占位字符串 {val!r} 删除")
                else:
                    visible[name] = [val]
                    fixes.append(
                        f"{name}: 字符串 {val!r} 包成 array (EXT_DATA_ERROR_50716566635066)"
                    )
            else:
                visible[name] = [val]
                fixes.append(f"{name}: 标量 {val!r} 包成 array")

        elif ftype == "array" and isinstance(val, list):
            items = node.get("items") or {}
            items_format = items.get("format") or ""
            if items_format in ("uri", "url") or "url" in name.lower():
                clean = [
                    v for v in val if isinstance(v, str) and v.lower().startswith(_URL_SCHEMES)
                ]
                if len(clean) != len(val):
                    if clean:
                        visible[name] = clean
                        fixes.append(f"{name}: URL 数组清掉 {len(val) - len(clean)} 个非 URL 元素")
                    else:
                        del visible[name]
                        fixes.append(
                            f"{name}: URL 数组全是占位，整字段删除 (EXT_DATA_ERROR_49505365506868)"
                        )
                        continue

        if ftype == "object" and isinstance(val, dict):
            sub_props = node.get("properties") or {}
            for sub_name, sub_def in sub_props.items():
                if not isinstance(sub_def, dict):
                    continue
                sub_enum = sub_def.get("enum")
                sub_val = val.get(sub_name)
                if (
                    isinstance(sub_enum, list)
                    and sub_enum
                    and sub_val is not None
                    and sub_val not in sub_enum
                ):
                    repl = safe_default_for(
                        {"enum": sub_enum, "type": sub_def.get("type", "string")}
                    )
                    if repl is None or repl not in sub_enum:
                        repl = sub_enum[0]
                    val[sub_name] = repl
                    fixes.append(
                        f"{name}.{sub_name}: {sub_val!r}→{repl!r} (IB.VALIDATION.DATA.001 修复)"
                    )

    return visible, fixes


def fix_invalid_enums(
    visible: dict[str, Any], schema: PtSchema
) -> tuple[dict[str, Any], list[str]]:
    """枚举修复（源仓保真）：非法值→安全默认→enum[0]（必填）→删（非必填）；
    数组按 items 枚举逐元素清洗。防 IB.VALIDATION.DATA.001。"""
    props = schema.properties
    required = set(schema.required)
    visible = dict(visible)
    fixed: list[str] = []

    for name, val in list(visible.items()):
        node = props.get(name, {}) if isinstance(props.get(name), dict) else {}
        enum = _effective_enum(node)
        ftype = node.get("type", "string")

        if ftype == "string" and isinstance(enum, list) and enum and val not in enum:
            replacement = safe_default_for(node)
            if replacement and replacement in enum:
                visible[name] = replacement
                fixed.append(f"{name}: {val!r}→{replacement!r}")
            elif name in required:
                visible[name] = enum[0]
                fixed.append(f"{name}: {val!r}→{enum[0]!r} (enum 第 1 个)")
            else:
                del visible[name]
                fixed.append(f"{name}: {val!r}→删 (非必填)")

        elif ftype == "array" and isinstance(enum, list) and enum and isinstance(val, list):
            cleaned = [v for v in val if v in enum]
            if len(cleaned) != len(val):
                dropped = [v for v in val if v not in enum]
                if cleaned:
                    visible[name] = cleaned
                    fixed.append(f"{name}: 删非法 {dropped}")
                elif name in required:
                    safe = safe_default_for(node)
                    visible[name] = safe if isinstance(safe, list) else [enum[0]]
                    fixed.append(f"{name}: 全非法，回退 {visible[name]}")
                else:
                    del visible[name]
                    fixed.append(f"{name}: 全非法且非必填，删")

    return visible, fixed


def strip_unknown_fields(
    visible: dict[str, Any],
    orderable: dict[str, Any],
    vis_schema: PtSchema,
    ord_schema: PtSchema | None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """删 schema 外字段（additionalProperties=false 会拒；源仓保真）。
    Orderable schema 缺失（伪行未导）时不动 orderable——宁保守不误删。"""
    visible_keys = set(vis_schema.properties)
    dropped: list[str] = []
    new_visible = {}
    for k, v in visible.items():
        if k in visible_keys:
            new_visible[k] = v
        else:
            dropped.append(f"visible.{k}")
    if ord_schema is None:
        return new_visible, dict(orderable), dropped
    orderable_keys = set(ord_schema.properties)
    new_orderable = {}
    for k, v in orderable.items():
        if k in orderable_keys:
            new_orderable[k] = v
        else:
            dropped.append(f"orderable.{k}")
    return new_visible, new_orderable, dropped


def clean_state_restrictions(orderable: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """清 LLM 幻觉的无效 stateRestrictions 条目（zipCodes 必填被拒；源仓保真）。
    丢弃 reason 为 'None'/空、或既无 states 又无 zipCodes 的条目；清空后删 key。"""
    sr = orderable.get("stateRestrictions")
    if not isinstance(sr, list) or not sr:
        return orderable, []
    kept = []
    for e in sr:
        if not isinstance(e, dict):
            continue
        reason = str(e.get("stateRestrictionsText") or "").strip()
        states = str(e.get("states") or "").strip()
        zips = str(e.get("zipCodes") or "").strip()
        if reason in ("", "None"):
            continue
        if not states and not zips:
            continue
        kept.append(e)
    if len(kept) == len(sr):
        return orderable, []
    orderable = dict(orderable)
    if kept:
        orderable["stateRestrictions"] = kept
        note = f"stateRestrictions 清理 {len(sr)}→{len(kept)} 条有效"
    else:
        orderable.pop("stateRestrictions", None)
        note = f"stateRestrictions 全无效(None/空)→ 删除({len(sr)} 条)"
    return orderable, [note]


def _is_empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def drop_empty_optional_fields(
    visible: dict[str, Any],
    orderable: dict[str, Any],
    vis_schema: PtSchema,
    ord_schema: PtSchema | None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """删非必填空字段；必填留给校验报错（源仓保真）。"""
    visible_required = set(vis_schema.required)
    orderable_required = set(ord_schema.required) if ord_schema else set()
    dropped: list[str] = []
    new_visible = {}
    for k, v in visible.items():
        if _is_empty(v) and k not in visible_required:
            dropped.append(f"visible.{k}")
            continue
        new_visible[k] = v
    new_orderable = {}
    for k, v in orderable.items():
        if _is_empty(v) and k not in orderable_required:
            dropped.append(f"orderable.{k}")
            continue
        new_orderable[k] = v
    return new_visible, new_orderable, dropped


def drop_min_items_violations(
    visible: dict[str, Any],
    orderable: dict[str, Any],
    vis_schema: PtSchema,
    ord_schema: PtSchema | None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """删数组长度 < minItems 的非必填字段（如副图必须 ≥5 否则省略）；必填留给校验（源仓保真）。"""

    def _check(
        payload: dict[str, Any], schema: PtSchema, prefix: str
    ) -> tuple[dict[str, Any], list[str]]:
        required = set(schema.required)
        properties = schema.properties
        out = {}
        dropped_local = []
        for k, v in payload.items():
            prop = properties.get(k, {}) if isinstance(properties.get(k), dict) else {}
            min_items = prop.get("minItems")
            if (
                prop.get("type") == "array"
                and isinstance(v, list)
                and min_items
                and len(v) < min_items
                and k not in required
            ):
                dropped_local.append(f"{prefix}.{k} (有 {len(v)} 项 < minItems {min_items})")
                continue
            out[k] = v
        return out, dropped_local

    new_visible, d1 = _check(visible, vis_schema, "visible")
    if ord_schema is None:
        return new_visible, dict(orderable), d1
    new_orderable, d2 = _check(orderable, ord_schema, "orderable")
    return new_visible, new_orderable, d1 + d2


def enforce_copy_limits(
    visible: dict[str, Any], vis_schema: PtSchema | None = None
) -> tuple[dict[str, Any], list[str]]:
    """文案长度/数量约束（源仓保真）：productName≤199、shortDescription≤4000（<60 词警告）、
    manufacturer≤60（EXT_DATA_ERROR_01076067496949）、keyFeatures≤7 条每条≤500
    （< per-PT minItems 警告，EXT_DATA_ERROR_55506974520167）。"""
    visible = dict(visible)
    warnings: list[str] = []

    name = visible.get("productName", "")
    if isinstance(name, str):
        if len(name) > 199:
            visible["productName"] = name[:199].rstrip()
            warnings.append(f"productName 截断 {len(name)}→199")
        if len(visible.get("productName", "")) < 10:
            warnings.append(f"productName 过短 ({len(visible.get('productName', ''))} 字符，<10)")

    desc = visible.get("shortDescription", "")
    if isinstance(desc, str):
        if len(desc) > 4000:
            visible["shortDescription"] = desc[:3997].rstrip() + "..."
            warnings.append(f"shortDescription 截断 {len(desc)}→4000")
        word_count = len(desc.split())
        if word_count < 60:
            warnings.append(f"shortDescription 词数 {word_count} < 60 (Walmart 会警告但可发布)")

    mfg = visible.get("manufacturer", "")
    if isinstance(mfg, str) and len(mfg) > 60:
        visible["manufacturer"] = mfg[:60].rstrip()
        warnings.append(f"manufacturer 截断 {len(mfg)}→60 (EXT_DATA_ERROR_01076067496949)")

    kf_min = 4  # 默认 4——比源仓旧值 3 保守，覆盖 95% PT
    if vis_schema is not None:
        kf_node = vis_schema.properties.get("keyFeatures")
        if isinstance(kf_node, dict):
            spec_min = kf_node.get("minItems")
            if isinstance(spec_min, int) and spec_min >= 1:
                kf_min = spec_min
    kf = visible.get("keyFeatures")
    if isinstance(kf, list):
        if len(kf) > 7:
            visible["keyFeatures"] = kf[:7]
            warnings.append(f"keyFeatures 截断 {len(kf)}→7 条")
        new_kf = []
        for i, item in enumerate(visible["keyFeatures"]):
            if isinstance(item, str) and len(item) > 500:
                new_kf.append(item[:497].rstrip() + "...")
                warnings.append(f"keyFeatures[{i}] 截断 {len(item)}→500")
            else:
                new_kf.append(item)
        visible["keyFeatures"] = new_kf
        if len(visible["keyFeatures"]) < kf_min:
            warnings.append(
                f"keyFeatures 只有 {len(visible['keyFeatures'])} 条，<{kf_min} minItems"
                f"（EXT_DATA_ERROR_55506974520167 风险）"
            )

    return visible, warnings


def round_decimals_to_2(
    visible: dict[str, Any], orderable: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """递归修数值小数位 ≤2（EXT_DATA_ERROR_68050064665065；源仓保真）。"""
    fixes: list[str] = []

    def _round_dict(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in d.items():
            if isinstance(v, bool):
                out[k] = v
            elif isinstance(v, float):
                rounded = round(v, 2)
                if rounded != v:
                    fixes.append(f"{prefix}{k}={v}→{rounded}")
                out[k] = rounded
            elif isinstance(v, dict):
                out[k] = _round_dict(v, f"{prefix}{k}.")
            elif isinstance(v, list):
                out[k] = [
                    _round_dict(item, f"{prefix}{k}[{i}].")
                    if isinstance(item, dict)
                    else (round(item, 2) if isinstance(item, float) else item)
                    for i, item in enumerate(v)
                ]
            else:
                out[k] = v
        return out

    return _round_dict(visible, "visible."), _round_dict(orderable, "orderable."), fixes


def sanitize_feed_numbers(feed: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """提交边界最后一道小数位兜底（源仓 feed_submit.sanitize_feed_numbers 保真）。"""
    fixes: list[str] = []

    def _walk(value: Any, path: str) -> Any:
        if isinstance(value, bool):
            return value
        if isinstance(value, float):
            rounded = round(value, 2)
            if rounded != value:
                fixes.append(f"{path}={value}->{rounded}")
            return rounded
        if isinstance(value, dict):
            return {k: _walk(v, f"{path}.{k}" if path else k) for k, v in value.items()}
        if isinstance(value, list):
            return [_walk(v, f"{path}[{i}]") for i, v in enumerate(value)]
        return value

    return _walk(copy.deepcopy(feed), ""), fixes


def run_coerce_chain(
    visible: dict[str, Any],
    orderable: dict[str, Any],
    *,
    amazon: dict[str, Any],
    brands_to_remove: list[str],
    vis_schema: PtSchema | None,
    ord_schema: PtSchema | None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """完整清洗链（源仓 prepare_one_async 链序保真）。→ (visible, orderable, 变更日志)。

    vis_schema 缺失（pt_spec.fields 未导入该 PT）时只跑 schema 无关步骤
    （force_amazon_copy / clean_state_restrictions / enforce_copy_limits / round）
    ——宁保守不瞎猜；官方 spec 校验（validator）会把缺 schema 记为警告。
    """
    notes: list[str] = []
    visible = force_amazon_copy(visible, amazon, brands_to_remove)
    if vis_schema is not None:
        visible, filled_known = fill_known_walmart_required(visible, vis_schema)
        notes.extend(f"fill_known: {x}" for x in filled_known)
        visible, filled = fill_missing_required(visible, vis_schema)
        notes.extend(f"fill_required: {x}" for x in filled)
        visible, type_fixes = fix_type_mismatches(visible, vis_schema)
        notes.extend(f"fix_type: {x}" for x in type_fixes)
        visible, enum_fixes = fix_invalid_enums(visible, vis_schema)
        notes.extend(f"fix_enum: {x}" for x in enum_fixes)
        visible, orderable, dropped = strip_unknown_fields(
            visible, orderable, vis_schema, ord_schema
        )
        notes.extend(f"strip_unknown: {x}" for x in dropped)
    orderable, sr_notes = clean_state_restrictions(orderable)
    notes.extend(sr_notes)
    if vis_schema is not None:
        visible, orderable, emptied = drop_empty_optional_fields(
            visible, orderable, vis_schema, ord_schema
        )
        notes.extend(f"drop_empty: {x}" for x in emptied)
        visible, orderable, shorty = drop_min_items_violations(
            visible, orderable, vis_schema, ord_schema
        )
        notes.extend(f"drop_min_items: {x}" for x in shorty)
    visible, copy_warnings = enforce_copy_limits(visible, vis_schema)
    notes.extend(f"copy_limits: {x}" for x in copy_warnings)
    visible, orderable, rounded = round_decimals_to_2(visible, orderable)
    notes.extend(f"round: {x}" for x in rounded)
    return visible, orderable, notes
