"""Item Spec v5 构建器（build 模式最小版；match 模式 R2）。

- payload = MPItemFeedHeader + MPItem[]（v5 JSON 形态；字段随 A152 真调校准，
  walmart_official_specs/MPSetup 为权威——本构建器只落骨架与已有属性）。
- build_hash = sha256(product 关键属性 + wpt + mode)：输入未变直接复用缓存行。
- WPT 来源：product.attrs.wpt 显式指定 > system_config listing.default_wpt；
  category_map 自动映射随 R2 类目域接入。
"""

import hashlib
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.errors import BusinessError

SPEC_VERSION = "v5"


async def build_spec(
    session: AsyncSession,
    *,
    product: dict[str, Any],
    offer_mode: str,
    gtin: str | None,
    channel_sku: str,
    price: float | None,
    inventory: int,
) -> dict[str, Any]:
    """→ {spec_id, payload, wpt, build_hash}（命中缓存直接复用）。"""
    attrs = product.get("attrs") or {}
    wpt = attrs.get("wpt")
    if not wpt:
        wpt = (
            await session.execute(
                text(
                    "SELECT value #>> '{}' FROM app.system_config WHERE key = 'listing.default_wpt'"
                )
            )
        ).scalar_one_or_none()
    if not wpt:
        raise BusinessError(
            "ERP_SPEC_BUILD_FAILED",
            "无法确定 Walmart Product Type（product.attrs.wpt 未设且无默认配置）",
        )

    fingerprint = json.dumps(
        {
            "title": product.get("title"),
            "brand": product.get("brand"),
            "images": product.get("images"),
            "attrs": attrs,
            "wpt": wpt,
            "mode": offer_mode,
            "spec_version": SPEC_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    build_hash = hashlib.sha256(fingerprint.encode()).hexdigest()

    cached = (
        await session.execute(
            text(
                "SELECT id, payload, wpt FROM app.listing_spec"
                " WHERE product_id = :p AND offer_mode = :m AND build_hash = :h"
            ),
            {"p": product["id"], "m": offer_mode, "h": build_hash},
        )
    ).one_or_none()
    if cached is not None:
        payload = _instantiate(cached.payload, channel_sku, gtin, price, inventory)
        return {
            "spec_id": cached.id,
            "payload": payload,
            "wpt": cached.wpt,
            "build_hash": build_hash,
        }

    images = product.get("images") or []
    mp_item: dict[str, Any] = {
        "Orderable": {
            "sku": "{SKU}",
            "productIdentifiers": {"productIdType": "EAN", "productId": "{GTIN}"},
            "productName": (product.get("title") or "")[:199],
            "brand": "unbranded",  # 审核通过后统一 unbranded（源系统流程）
            "price": "{PRICE}",
            "ShippingWeight": float(attrs.get("shipping_weight") or 1),
            "electronicsIndicator": "No",
            "batteryTechnologyType": "Does Not Contain a Battery",
            "chemicalAerosolPesticide": "No",
            "shipsInOriginalPackaging": "No",
        },
        "Visible": {
            wpt: {
                "shortDescription": str(attrs.get("description") or product.get("title") or "")[
                    :4000
                ],
                "mainImageUrl": images[0] if images else None,
                "productSecondaryImageURL": images[1:5] or None,
                "keyFeatures": (attrs.get("bullets") or [])[:5] or None,
            }
        },
    }
    payload_template = {
        "MPItemFeedHeader": {
            "sellingChannel": "mpsetupbymatch" if offer_mode == "match" else "mpmaintenance",
            "processMode": "REPLACE",
            "subset": "EXTERNAL",
            "locale": "en",
            "version": "1.5",
        },
        "MPItem": [mp_item],
    }
    spec_id = (
        await session.execute(
            text(
                "INSERT INTO app.listing_spec"
                " (team_id, product_id, offer_mode, wpt, spec_version, payload, build_hash)"
                " VALUES (:t, :p, :m, :w, :v, cast(:pl AS jsonb), :h)"
                " ON CONFLICT (product_id, offer_mode, build_hash) DO UPDATE"
                "   SET built_at = now()"
                " RETURNING id"
            ),
            {
                "t": product["team_id"],
                "p": product["id"],
                "m": offer_mode,
                "w": wpt,
                "v": SPEC_VERSION,
                "pl": json.dumps(payload_template, ensure_ascii=False),
                "h": build_hash,
            },
        )
    ).scalar_one()
    payload = _instantiate(payload_template, channel_sku, gtin, price, inventory)
    return {"spec_id": spec_id, "payload": payload, "wpt": wpt, "build_hash": build_hash}


def _instantiate(
    template: dict[str, Any], sku: str, gtin: str | None, price: float | None, inventory: int
) -> dict[str, Any]:
    """把缓存模板实例化为具体 listing 的提交体（SKU/GTIN/价格是 listing 级，不进缓存键）。"""
    payload: dict[str, Any] = json.loads(json.dumps(template))
    item = payload["MPItem"][0]["Orderable"]
    item["sku"] = sku
    item["productIdentifiers"]["productId"] = gtin or ""
    item["price"] = float(price) if price is not None else 0.0
    payload["MPItem"][0]["Orderable"]["inventory"] = {"fulfillmentLagTime": 1, "amount": inventory}
    return payload
