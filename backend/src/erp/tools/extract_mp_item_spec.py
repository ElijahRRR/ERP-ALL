"""MP_ITEM v5 官方规格无损提取 → refdata.pt_spec fields 导入 jsonl（R2-03 增量 1）。

用法（部署机，两条数据路任一）：
  # 路 B：T7 备份的 MPSetup monolith（~451MB，流式解析需 pip install ijson）
  python -m erp.tools.extract_mp_item_spec --monolith MPSetup/5.0.xxx_MP_ITEM_0_0_en.json \\
      --out pt_spec_fields.jsonl

  # 路 A：POST /v3/items/spec 的 live 响应文件（≤20 PT/次，可多个文件合并）
  python -m erp.tools.extract_mp_item_spec --live-response resp1.json --live-response resp2.json \\
      --out pt_spec_fields.jsonl

  # 之后：python -m erp.tools.import_pt_spec --file pt_spec_fields.jsonl --chunk-size 200

为什么不用旧审核库 walmart_pt_spec.fields：那是压缩版（enum 截断 10、
丢 minLength/maxLength/pattern/allOf 条件必填，源 sync_pt_specs.py:100-110）——
用作本地校验依据会假拒。本工具输出 per-PT **原始 schema 节点原样**（零损），
校验器/LLM 摘要在消费侧解释。

行格式（与 import_pt_spec PT_SPEC_DOMAIN 对齐）：
  walmart_product_type · total_fields · required_count · required_fields ·
  has_real_cert · real_cert_fields · has_soft_cert · soft_cert_fields ·
  fields（原始 per-PT schema 节点：properties/required/allOf…）
共享 Orderable 段以伪行 walmart_product_type='__orderable__' 输出（0020 协议）。

cert 判定集合逐字保真源仓 erp-core sync_pt_specs.py（与 R3b 已导数据同一定义，
重导不改变 R3b 行为）。

monolith 451MB：优先 ijson 流式（峰值 KB 级；源仓 split_mp_item_spec 同法，
pt_spec.py:9-14 OOM 事故教训）；无 ijson 时回退 json.load（仅适合小文件/测试）。
"""

import argparse
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# 搬运模式做不了的"真认证"必填字段（逐字保真源仓 sync_pt_specs.py REAL_CERT_FIELDS）
REAL_CERT_FIELDS = {
    # UL/ETL 电气 NRTL 认证
    "has_nrtl_listing_certification",
    "nrtl_information",
    # 儿童产品 CPSIA
    "children_product_certificate_document_reference_id",
    "general_certificate_of_conformity_document_reference_id",
    "children_product_test_report_document_reference_id",
    # FDA / 食品接触
    "fda_registration_number",
    "fda_acronym",
    "food_facility_registration_number",
    # 药品 OTC
    "is_otc",
    "active_drug_ingredients",
    "contains_drug_facts",
    # 危险品 / 电池
    "is_hazardous_material",
    "battery_voltage",
    "contains_battery",
}

# 软合规字段（警告/披露，搬运模式通常可解决；逐字保真源仓 SOFT_CERT_FIELDS）
SOFT_CERT_FIELDS = {
    "smallPartsWarnings",
    "state_chemical_disclosure",
    "ingredients",
    "activeIngredients",
    "ingredientListImage",
    "hasIngredientList",
    "inactiveIngredients",
    "ingredient_properties",
    "law_label_disclosure",
    "foodAllergenStatements",
    "allergens_not_contained",
    "chemical_grade",
    "chemical_purity_percentage",
    "chemical_standard_type",
    "children_age_range_description",
    "minimum_age_recommended",
}

ORDERABLE_PSEUDO_PT = "__orderable__"

# 无 ijson 时允许整载的文件上限（超过则警告可能 OOM——源仓 451MB 整载事故教训）
_WHOLE_LOAD_WARN_MB = 64

_VISIBLE_PREFIX = "properties.MPItem.items.properties.Visible.properties"
_ORDERABLE_PREFIX = "properties.MPItem.items.properties.Orderable"


def build_row(pt_name: str, node: dict[str, Any]) -> dict[str, Any]:
    """一个 PT 的原始 schema 节点 → 导入行（节点原样进 fields，统计/cert 列同源派生）。"""
    properties = node.get("properties") or {}
    required = [r for r in (node.get("required") or []) if isinstance(r, str)]
    real_cert = sorted(set(required) & REAL_CERT_FIELDS)
    soft_cert = sorted(set(required) & SOFT_CERT_FIELDS)
    return {
        "walmart_product_type": pt_name,
        "total_fields": len(properties),
        "required_count": len(required),
        "required_fields": required,
        "has_real_cert": bool(real_cert),
        "real_cert_fields": real_cert,
        "has_soft_cert": bool(soft_cert),
        "soft_cert_fields": soft_cert,
        "fields": node,
    }


def _unwrap(doc: dict[str, Any]) -> dict[str, Any]:
    """live 响应 {"schema": <monolith 同构>} → 内层；已是 monolith 形态则原样。"""
    inner = doc.get("schema")
    if isinstance(inner, dict) and "properties" in inner:
        return inner
    return doc


def _navigate(doc: dict[str, Any], dotted: str) -> Any:
    node: Any = doc
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _iter_loaded(doc: dict[str, Any]) -> tuple[Iterator[tuple[str, dict[str, Any]]], Any]:
    doc = _unwrap(doc)
    visible = _navigate(doc, _VISIBLE_PREFIX) or {}
    orderable = _navigate(doc, _ORDERABLE_PREFIX)
    pairs = ((pt, node) for pt, node in visible.items() if isinstance(node, dict))
    return pairs, orderable


def iter_monolith(path: Path) -> tuple[Iterator[tuple[str, dict[str, Any]]], Any]:
    """monolith → (per-PT 节点迭代器, Orderable 节点)。优先 ijson 流式。"""
    try:
        import ijson  # type: ignore[import-not-found]  # noqa: PLC0415 可选依赖延迟导入
    except ImportError:
        size_mb = path.stat().st_size / 1024 / 1024
        if size_mb > _WHOLE_LOAD_WARN_MB:
            print(
                f"警告：{path.name} {size_mb:.0f}MB 且无 ijson，整载可能 OOM"
                "（源仓事故教训）；建议 pip install ijson",
                file=sys.stderr,
            )
        with path.open(encoding="utf-8") as f:
            return _iter_loaded(json.load(f))

    def _pairs() -> Iterator[tuple[str, dict[str, Any]]]:
        with path.open("rb") as fb:
            yield from ijson.kvitems(fb, _VISIBLE_PREFIX)

    orderable = None
    with path.open("rb") as fb:
        for node in ijson.items(fb, _ORDERABLE_PREFIX):
            orderable = node
            break
    return _pairs(), orderable


def extract(
    sources: list[tuple[str, Path]],
    out: Path,
    only_pts: set[str] | None = None,
    include_orderable: bool = True,
) -> dict[str, int]:
    """多源提取合并写 jsonl。同名 PT 后者胜（多 live 响应合并语义）。"""
    rows: dict[str, dict[str, Any]] = {}
    orderable_node: Any = None
    for kind, path in sources:
        if kind == "monolith":
            pairs, orderable = iter_monolith(path)
        else:
            with path.open(encoding="utf-8") as f:
                pairs, orderable = _iter_loaded(json.load(f))
        n_before = len(rows)
        for pt, node in pairs:
            if only_pts is not None and pt not in only_pts:
                continue
            rows[pt] = build_row(pt, node)
        if orderable is not None:
            orderable_node = orderable
        print(f"[{path.name}] 提取 {len(rows) - n_before} 个 PT")
    if include_orderable and isinstance(orderable_node, dict):
        rows[ORDERABLE_PSEUDO_PT] = build_row(ORDERABLE_PSEUDO_PT, orderable_node)
    with out.open("w", encoding="utf-8") as f:
        for row in rows.values():
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    stats = {
        "pts": len(rows) - (1 if ORDERABLE_PSEUDO_PT in rows else 0),
        "orderable": 1 if ORDERABLE_PSEUDO_PT in rows else 0,
    }
    print(f"输出 {out}：{stats['pts']} 个 PT + {stats['orderable']} 个 Orderable 伪行")
    return stats


def main() -> int:
    p = argparse.ArgumentParser(description="MP_ITEM v5 官方规格无损提取（→ import_pt_spec jsonl）")
    p.add_argument("--monolith", help="MPSetup monolith JSON（T7 备份，~451MB）")
    p.add_argument(
        "--live-response",
        action="append",
        default=[],
        help="POST /v3/items/spec 响应文件（可多个，合并；同 PT 后者胜）",
    )
    p.add_argument("--out", required=True, help="输出 jsonl 路径")
    p.add_argument("--pt", action="append", default=[], help="只提取指定 PT（可多个；默认全量）")
    p.add_argument("--no-orderable", action="store_true", help="不输出 __orderable__ 伪行")
    args = p.parse_args()

    sources: list[tuple[str, Path]] = []
    if args.monolith:
        sources.append(("monolith", Path(args.monolith)))
    sources.extend(("live", Path(x)) for x in args.live_response)
    if not sources:
        print("--monolith 与 --live-response 至少给一个", file=sys.stderr)
        return 2
    for _, path in sources:
        if not path.is_file():
            print(f"文件不存在：{path}", file=sys.stderr)
            return 2
    extract(
        sources,
        Path(args.out),
        only_pts=set(args.pt) or None,
        include_orderable=not args.no_orderable,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
