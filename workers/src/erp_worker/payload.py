"""解析结果 → ERP product 入库 payload 适配器。

v3 parser 产出一个扁平字典（字段名见 parser._default_result）。ERP
`/worker/v1/tasks/result` 的 payload 期望结构化字段（见 scrape.service.product_upsert）：

    {title, brand, category_path, amazon_leaf_id, images[], attrs{}, price_snapshot{}}

映射原则：
- 结构化五要素（标题/品牌/类目/图片/价格）抽到顶层，供上架 spec 直接取用；
- 采集到的其余全部字段原样收进 attrs（审核 L2/L3、上架属性填写都可能用到），
  不丢数据；下划线前缀的内部字段（_page_asin/_sp_price 等）不入库。
- "N/A" / 空串一律归一为 None / 空，避免脏值污染下游判定。
"""

from typing import Any

_NA = {"", "N/A", "N/a", "n/a", "None", None}


def _clean(value: Any) -> str | None:
    """归一空占位：'N/A'/空串 → None，其余 strip 后返回。"""
    if value is None:
        return None
    s = str(value).strip()
    return None if s in _NA else s


def _split(value: Any, sep: str) -> list[str]:
    """按分隔符拆成非空列表（parser 用 \\n / , 拼接多值字段）。"""
    s = _clean(value)
    if not s:
        return []
    return [part.strip() for part in s.split(sep) if part.strip()]


def _leaf_id(result: dict[str, Any]) -> str | None:
    """类目链末段 = 叶子类目 id（category_ids 逗号/大于号分隔，取最后一段）。"""
    chain = _clean(result.get("category_ids"))
    if not chain:
        return _clean(result.get("root_category_id"))
    for sep in (">", ","):
        if sep in chain:
            parts = [p.strip() for p in chain.split(sep) if p.strip()]
            if parts:
                return parts[-1]
    return chain


# 顶层已单独处理 / 内部字段：不再重复进 attrs
_TOP_LEVEL = {"title", "brand", "category_tree", "category_ids", "root_category_id", "image_urls"}
_PRICE_FIELDS = {
    "current_price",
    "buybox_price",
    "original_price",
    "buybox_shipping",
    "is_fba",
    "total_price",
}


def to_product_payload(result: dict[str, Any]) -> dict[str, Any]:
    """把 parser 结果字典转成 ERP product upsert payload。"""
    attrs: dict[str, Any] = {}
    for k, v in result.items():
        if k.startswith("_") or k in _TOP_LEVEL or k in _PRICE_FIELDS:
            continue
        cleaned = _clean(v)
        if cleaned is not None:
            attrs[k] = cleaned

    # 多值字段拆成列表，覆盖上面的字符串版本，供下游直接消费
    bullets = _split(result.get("bullet_points"), "\n")
    if bullets:
        attrs["bullets"] = bullets
    for field, sep in (("upc_list", ","), ("ean_list", ","), ("variation_asins", ",")):
        items = _split(result.get(field), sep)
        if items:
            attrs[field] = items

    price_snapshot: dict[str, Any] = {}
    for f in _PRICE_FIELDS:
        cleaned = _clean(result.get(f))
        if cleaned is not None:
            price_snapshot[f] = cleaned

    return {
        "title": _clean(result.get("title")),
        "brand": _clean(result.get("brand")),
        "category_path": _clean(result.get("category_tree")),
        "amazon_leaf_id": _leaf_id(result),
        "images": _split(result.get("image_urls"), "\n"),
        "attrs": attrs,
        "price_snapshot": price_snapshot or None,
    }
