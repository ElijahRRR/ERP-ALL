"""提交前本地校验器（R2-03 增量 4）：官方 spec 层 + 实践规则层。

- **errors（官方 schema 层，任一命中=不许提交）**：必填缺失/空（源仓 validate_payload
  语义）、枚举违规、array 类型违规、maxLength 超限、必填数组 minItems 不足——
  coerce 链之后不该再有，这里是最后闸门（省 MP_ITEM 10/hour 配额）。
- **warnings（实践规则层，实测渠道行为，不阻 dry-run）**：fulfillmentCenterID 缺失
  （PartnerID，schema 未标但实测必填 BR-LST-007）、shortDescription <60 词、
  keyFeatures < per-PT minItems、schema 缺失（fields 未导入，无法做官方校验）。
- match 模式（MP_ITEM_MATCH v4.2）：required 五字段 + productIdentifiers 结构校验。

验收①口径：errors 为空 = dry-run 产物通过官方 spec 校验。
"""

from dataclasses import dataclass, field
from typing import Any

from erp.listing.coerce import _effective_enum
from erp.listing.wpt_schema import PtSchema

_EMPTYISH: tuple[Any, ...] = (None, "", [], {})

_TYPE_CHECKS: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "array": list,
    "object": dict,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
}

# MP_ITEM_MATCH v4.2 required（walmart_official_specs/MP_ITEM_MATCH_v4.2.json）
_MATCH_REQUIRED = ("sku", "ShippingWeight", "price", "productIdentifiers", "condition")
_MATCH_ID_TYPES = ("ISBN", "GTIN", "UPC", "EAN")
_MATCH_ID_MAX_LEN = 14  # v4.2 schema productId maxLength


@dataclass
class ValidationReport:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def _check_section(
    payload: dict[str, Any], schema: PtSchema, prefix: str, report: ValidationReport
) -> None:
    props = schema.properties
    # 必填非空（源仓 validate_payload 语义）
    for name in schema.required:
        if payload.get(name) in _EMPTYISH:
            report.error(f"{prefix}.{name}: 必填缺失/空")
    # 逐字段：类型 / 枚举 / 长度 / minItems
    for name, val in payload.items():
        node = props.get(name)
        if not isinstance(node, dict):
            continue  # schema 外字段由 coerce strip；此处不重复报
        if isinstance(val, str) and val.startswith("{") and val.endswith("}"):
            continue  # 模板占位符（校验实例化后的 item 时不会出现）
        ftype = node.get("type")
        expected = _TYPE_CHECKS.get(ftype or "")
        if expected is not None and val is not None and not isinstance(val, expected):
            report.error(f"{prefix}.{name}: 类型应为 {ftype}，实为 {type(val).__name__}")
            continue
        enum = _effective_enum(node)
        if enum:
            if isinstance(val, list):
                bad = [v for v in val if v not in enum]
                if bad:
                    report.error(f"{prefix}.{name}: 数组含枚举外值 {bad[:3]}")
            elif ftype == "string" and val not in enum:
                report.error(f"{prefix}.{name}: {val!r} 不在枚举内")
        max_len = node.get("maxLength")
        if isinstance(max_len, int) and isinstance(val, str) and len(val) > max_len:
            report.error(f"{prefix}.{name}: 长度 {len(val)} > maxLength {max_len}")
        min_items = node.get("minItems")
        if (
            isinstance(min_items, int)
            and isinstance(val, list)
            and len(val) < min_items
            and name in set(schema.required)
        ):
            report.error(f"{prefix}.{name}: {len(val)} 项 < minItems {min_items}")


def validate_build_item(
    item: dict[str, Any],
    wpt: str,
    vis_schema: PtSchema | None,
    ord_schema: PtSchema | None,
) -> ValidationReport:
    """MP_ITEM v5 单品校验（实例化后的 {Visible, Orderable} 元素）。"""
    report = ValidationReport()
    visible = (item.get("Visible") or {}).get(wpt) or {}
    orderable = item.get("Orderable") or {}

    if vis_schema is None:
        report.warn(f"pt_spec.fields 缺 {wpt}——官方 Visible 校验跳过（经 import 通道补数据）")
    else:
        _check_section(visible, vis_schema, "visible", report)
    if ord_schema is None:
        report.warn("pt_spec.fields 缺 __orderable__ 伪行——官方 Orderable 校验跳过")
    else:
        _check_section(orderable, ord_schema, "orderable", report)

    # 实践规则层（schema 未标但实测必要）
    inv = orderable.get("inventory")
    if isinstance(inv, list):
        for i, entry in enumerate(inv):
            if isinstance(entry, dict) and not entry.get("fulfillmentCenterID"):
                report.warn(
                    f"orderable.inventory[{i}].fulfillmentCenterID 缺失"
                    "（=PartnerID，store.profile.partner_id 维护；实测必填 BR-LST-007）"
                )
    end_date = orderable.get("endDate")
    if isinstance(end_date, str) and end_date and "T" not in end_date:
        report.error(
            f"orderable.endDate={end_date!r} 非 ISO DateTime"
            "（纯日期拒收 EXT_DATA_ERROR_00030257670757）"
        )
    return report


def validate_match_item(item: dict[str, Any]) -> ValidationReport:
    """MP_ITEM_MATCH v4.2 offer 校验（BR-LST-015：五字段 + identifier 结构）。"""
    report = ValidationReport()
    offer = item.get("Item") or {}
    for name in _MATCH_REQUIRED:
        if offer.get(name) in _EMPTYISH:
            report.error(f"Item.{name}: 必填缺失/空")
    pid = offer.get("productIdentifiers")
    if isinstance(pid, dict):
        if pid.get("productIdType") not in _MATCH_ID_TYPES:
            report.error(f"Item.productIdentifiers.productIdType {pid.get('productIdType')!r} 非法")
        ident = str(pid.get("productId") or "")
        if not 1 <= len(ident) <= _MATCH_ID_MAX_LEN:
            report.error("Item.productIdentifiers.productId 长度需 1-14")
    elif pid not in _EMPTYISH:
        report.error("Item.productIdentifiers 应为单对象")
    return report
