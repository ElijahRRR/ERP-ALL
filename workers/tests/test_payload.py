"""payload 适配器单测：parser 结果字典 → ERP product payload。"""

from erp_worker.payload import to_product_payload


def _parser_result(**over: object) -> dict[str, object]:
    """一份典型的 parser 成功结果（字段名对齐 parser._default_result）。"""
    base: dict[str, object] = {
        "asin": "B0TEST0001",
        "title": "Ceramic Coffee Mug 12oz",
        "brand": "Acme",
        "current_price": "$15.99",
        "buybox_price": "$15.99",
        "original_price": "$19.99",
        "buybox_shipping": "$0.00",
        "is_fba": "Yes",
        "stock_status": "In Stock",
        "stock_count": "12",
        "category_tree": "Home & Kitchen > Kitchen & Dining > Mugs",
        "category_ids": "1055398 > 289851 > 289855",
        "root_category_id": "1055398",
        "image_urls": "https://m.media-amazon.com/a.jpg\nhttps://m.media-amazon.com/b.jpg",
        "bullet_points": "12oz capacity\nDishwasher safe\nMicrowave safe",
        "upc_list": "012345678905,012345678912",
        "ean_list": "",
        "variation_asins": "B0TEST0002,B0TEST0003",
        "model_number": "MUG-12",
        "manufacturer": "Acme Inc",
        "rating": "4.5",
        "review_count": "1203",
        "seller_id": "A1SELLER",
        "seller_name": "Acme Store",
        "_page_asin": "B0TEST0001",
        "_sp_price": "15.99",
    }
    base.update(over)
    return base


def test_top_level_five_elements() -> None:
    p = to_product_payload(_parser_result())
    assert p["title"] == "Ceramic Coffee Mug 12oz"
    assert p["brand"] == "Acme"
    assert p["category_path"] == "Home & Kitchen > Kitchen & Dining > Mugs"
    assert p["amazon_leaf_id"] == "289855"  # category_ids 链末段
    assert p["images"] == [
        "https://m.media-amazon.com/a.jpg",
        "https://m.media-amazon.com/b.jpg",
    ]


def test_price_snapshot_collects_price_fields() -> None:
    ps = to_product_payload(_parser_result())["price_snapshot"]
    assert ps["current_price"] == "$15.99"
    assert ps["buybox_price"] == "$15.99"
    assert ps["original_price"] == "$19.99"
    assert ps["is_fba"] == "Yes"


def test_attrs_carry_remaining_fields_and_split_multivalue() -> None:
    attrs = to_product_payload(_parser_result())["attrs"]
    # 多值字段拆列表
    assert attrs["bullets"] == ["12oz capacity", "Dishwasher safe", "Microwave safe"]
    assert attrs["upc_list"] == ["012345678905", "012345678912"]
    assert attrs["variation_asins"] == ["B0TEST0002", "B0TEST0003"]
    # 其它字段保留
    assert attrs["model_number"] == "MUG-12"
    assert attrs["rating"] == "4.5"
    assert attrs["seller_name"] == "Acme Store"
    # 内部字段（下划线）不入库
    assert "_page_asin" not in attrs
    assert "_sp_price" not in attrs
    # 价格字段不重复进 attrs（已在 price_snapshot）
    assert "current_price" not in attrs


def test_na_and_empty_normalized_to_none() -> None:
    p = to_product_payload(
        _parser_result(brand="N/A", category_tree="", ean_list="", original_price="N/A")
    )
    assert p["brand"] is None
    assert p["category_path"] is None
    assert "ean_list" not in p["attrs"]  # 空串不入 attrs
    assert "original_price" not in p["price_snapshot"]


def test_leaf_falls_back_to_root_when_no_chain() -> None:
    p = to_product_payload(_parser_result(category_ids="", root_category_id="1055398"))
    assert p["amazon_leaf_id"] == "1055398"


def test_empty_price_snapshot_becomes_none() -> None:
    p = to_product_payload(
        _parser_result(
            current_price="N/A",
            buybox_price="N/A",
            original_price="N/A",
            buybox_shipping="N/A",
            is_fba="N/A",
        )
    )
    assert p["price_snapshot"] is None


def test_missing_product_placeholder_still_has_title() -> None:
    # 404 分支：engine 会塞 title="[商品不存在]"，payload 必须保留（ERP 要求 title 非空）
    p = to_product_payload({"asin": "B0MISSING1", "title": "[商品不存在]"})
    assert p["title"] == "[商品不存在]"
    assert p["images"] == []
