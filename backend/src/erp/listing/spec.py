"""Item Spec v5 构建器（R2-03 真实化：官方规格接入 + 双模式差异化 payload）。

build（MP_ITEM v5，feed_kind=item_build）：
- WPT 链：product.attrs.wpt 显式 > audit L1 直判（run_l1——与审核同一可售语义，
  单一真相源）> system_config listing.default_wpt。
- Visible：产品基础字段 + 零认证覆盖（BR-AUD-006：只对该 PT spec 存在的字段强制、
  enum 不含目标值按安全序列回退、同步清掉文档字段防幻觉；覆盖记录进
  listing_spec.cert_overrides）。规格源=refdata.pt_spec.fields（0020 无损列）。
- Orderable：强制格式源仓 force_overrides 保真（BR-LST-007/008/011：
  productIdentifiers 单对象、price 裸 number、inventory=[{quantity,fulfillmentCenterID}]、
  endDate 必须 ISO DateTime——EXT_DATA_ERROR_00030257670757）。
- header 只能 3 字段 + version 完整时间戳（BR-LST-005；官方 sample 的
  sellingChannel/processMode/subset 会被拒——EXT_DATA_ERROR_60670554076755/74597363510508）。

match（MP_ITEM_MATCH v4.2，feed_kind=item_match，BR-LST-015）：
- 仅 offer 字段：sku/productIdentifiers/price/ShippingWeight/condition；
  identifier 预检 fail-closed（无标识符不可跟卖）。
- header = {sellingChannel: mpsetupbymatch, version: 4.2, locale: en}
  （walmart_official_specs/MP_ITEM_MATCH_v4.2.json required 三字段）。

缓存：listing_spec 模板 = 单个 MPItem 元素（header 是 feed 级不入缓存）；
build_hash 含 pt_spec dataset_revision 与配置——规格数据/配置更新自动失效。
SKU/GTIN/价格/库存/日期/PartnerID 是 listing/store 级参数，经 _instantiate 注入不进缓存键。
"""

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.audit.l1_category import run_l1
from erp.catalog import variant as variant_svc
from erp.core.errors import BusinessError
from erp.listing import coerce, validator, wpt_schema

# 实测校准常量（BR-LST-005/011；system_config 可覆盖，l1_rerank 配置模式）
_HEADER_DEFAULTS: dict[str, Any] = {
    "business_unit": "WALMART_US",
    "locale": "en",
    "version": "5.0.20260304-22_45_32-api",  # 完整时间戳，裸 "5.0" 拒收
}
_ORDERABLE_DEFAULTS: dict[str, Any] = {
    "fulfillment_lag_days": 1,
    "must_ship_alone": "No",
    "country_of_origin": "China",
    # D-Q9 统一远期；渠道对 2049 的唯一实测成功写法带毫秒（BR-RET-007：
    # '2049-12-31' 被拒、'2049-12-31T00:00:00.000Z' 被收）——若真机再拒可只改配置降级
    "end_date_default": "2049-12-31",
}
_FORCE_BRAND = "Unbranded"  # BR-CAT-005：上架商品强制品牌

# 系统后处理字段（源仓 mapper.py:14-29 逐字保真）：不喂 LLM、不采纳其输出——
# 文案/品牌/图片/SKU/价格/日期/库存由系统用 Amazon 原文与店铺配置决定。
# （定义放本模块供构建器合并过滤；attr_fill 从这里导入，避免循环依赖。）
SYSTEM_COPY_FIELDS = {"productName", "shortDescription", "keyFeatures"}
SYSTEM_BRAND_FIELDS = {"brand"}
SYSTEM_IMAGE_FIELDS = {"mainImageUrl", "productSecondaryImageURL", "swatchImageUrl"}
SYSTEM_VISIBLE_POSTPROCESS_FIELDS = SYSTEM_COPY_FIELDS | SYSTEM_BRAND_FIELDS | SYSTEM_IMAGE_FIELDS
# 变体控制字段唯一来源 = _instantiate 注入（D-Q63：isPrimaryVariant 一律不传）——
# LLM 填充/模板一律剥离，防幻觉组标识与主变体标记随 feed 出门（评审发现 R2-11 增量2）
VARIANT_CONTROL_FIELDS = {"variantGroupId", "variantAttributeNames", "isPrimaryVariant"}
SYSTEM_ORDERABLE_POSTPROCESS_FIELDS = {
    "sku",
    "productIdentifiers",
    "price",
    "country_of_origin_substantial_transformation",
    "specProductType",
    "inventory",
    "startDate",
    "endDate",
    "MustShipAlone",
    "fulfillmentLagTime",
}

# 零认证覆盖（BR-AUD-006，源仓 NO_CERT_FORCES 逐字保真）
_NO_CERT_FORCES: dict[str, str] = {
    "certification_type": "Neither of these applies",
    "has_nrtl_listing_certification": "No",
    "isProp65WarningRequired": "No",
    "has_written_warranty": "No",
    "isAssemblyRequired": "No",
}
_SAFE_ENUM_SEQUENCE = ("No", "Neither of these applies", "Skip for now", "None")
_DANGEROUS_DOC_FIELDS = (
    "warrantyText",
    "warrantyURL",
    "prop65WarningText",
    "children_product_certificate_document_reference_id",
    "children_product_test_report_document_reference_id",
    "general_certificate_of_conformity_document_reference_id",
    "nrtl_information",
    "assemblyInstructions",
    "suggested_number_of_people_for_assembly",
)

# 副图 schema minItems=5：不足 5 张不写（源仓实测规则）
_SECONDARY_IMAGE_MIN = 5
_SECONDARY_IMAGE_MAX = 8
_PRODUCT_NAME_MAX = 199
_SHORT_DESC_MAX = 4000
_KEY_FEATURES_CAP = 10

_MATCH_HEADER = {"sellingChannel": "mpsetupbymatch", "version": "4.2", "locale": "en"}
_MATCH_CONDITION = "New"
SPEC_VERSION_MATCH = "4.2"

_UPC_LEN = 12
_EAN_LEN = 13

# 变体维度键 → Walmart 属性名映射：表迁居 catalog/variant（二期 B——归组期预警共用），
# 本模块经 variant_svc.theme_map 读取（默认表 + system_config variant.theme_map 并入）。


async def _cfg(session: AsyncSession, key: str, defaults: dict[str, Any]) -> dict[str, Any]:
    """system_config JSON 键逐字段覆盖默认（category_map.rerank 同款模式）。"""
    cfg = dict(defaults)
    row = (
        await session.execute(
            text("SELECT value FROM app.system_config WHERE key = :k"), {"k": key}
        )
    ).scalar_one_or_none()
    if isinstance(row, str):
        try:
            row = json.loads(row)
        except ValueError:
            row = None
    if isinstance(row, dict):
        cfg.update({k: row[k] for k in defaults if k in row})
    return cfg


def _map_variant_attrs(
    variant_attrs: dict[str, Any], theme_map: dict[str, str], pt_props: dict[str, Any]
) -> dict[str, str]:
    """成员维度值 → {映射后 Walmart 属性名: 维度值原文}（fail-closed，照 _resolve_wpt 模式）。

    维度键解析：theme_map 命中取映射名，否则键本身当 Walmart 属性名；解析后的名字必须
    在该 PT spec 字段集内——否则 coerce.strip_unknown 会删掉、渠道也无此属性，即"既不在
    theme_map 也不在该 PT spec 字段集"，无法映射，拒绝构建。
    """
    mapped: dict[str, str] = {}
    for dim_key, dim_val in variant_attrs.items():
        name = theme_map.get(dim_key, dim_key)
        if name not in pt_props:
            raise BusinessError(
                "ERP_SPEC_BUILD_FAILED",
                f"变体维度键无法映射：{dim_key}",
                {"dim_key": dim_key, "mapped": name},
            )
        if name in mapped:  # 两个维度键映到同名 → 维度静默丢失，fail-closed 拒绝
            raise BusinessError(
                "ERP_SPEC_BUILD_FAILED",
                f"变体维度键映射冲突：{dim_key} 与既有维度同映射到 {name}",
                {"dim_key": dim_key, "mapped": name},
            )
        mapped[name] = str(dim_val)
    return mapped


async def feed_header(session: AsyncSession, offer_mode: str) -> dict[str, Any]:
    """feed 级 header。build=v5 三字段（多一个都拒）；match=v4.2 required 三字段。"""
    if offer_mode == "match":
        return dict(_MATCH_HEADER)
    cfg = await _cfg(session, "listing.feed_header", _HEADER_DEFAULTS)
    return {
        "businessUnit": cfg["business_unit"],
        "locale": cfg["locale"],
        "version": cfg["version"],
    }


def _identifier_type(gtin: str) -> str:
    if len(gtin) == _UPC_LEN:
        return "UPC"
    if len(gtin) == _EAN_LEN:
        return "EAN"
    return "GTIN"


async def _resolve_wpt(session: AsyncSession, product: dict[str, Any]) -> tuple[str, str]:
    """→ (wpt, source)。链：attrs.wpt > L1 直判 > default 配置；全空 fail-closed。"""
    attrs = product.get("attrs") or {}
    if attrs.get("wpt"):
        return str(attrs["wpt"]), "attrs"
    l1 = await run_l1(session, product)
    if l1["verdict"] == "reject":
        raise BusinessError(
            "ERP_SPEC_BUILD_FAILED",
            f"类目硬拒不可上架（{l1['rule_code']}）",
            {"l1": l1["rule_code"]},
        )
    if l1.get("wpt"):
        return str(l1["wpt"]), "category_map"
    default = (
        await session.execute(
            text("SELECT value #>> '{}' FROM app.system_config WHERE key = 'listing.default_wpt'")
        )
    ).scalar_one_or_none()
    if default:
        return str(default), "config"
    raise BusinessError(
        "ERP_SPEC_BUILD_FAILED",
        "无法确定 Walmart Product Type（attrs.wpt 未设、类目无直判、无默认配置）",
        {"l1": l1["rule_code"]},
    )


def _zero_cert_overrides(
    visible: dict[str, Any], pt_props: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """BR-AUD-006 零认证覆盖（源仓 force_overrides 保真）。→ (visible, 覆盖记录)。

    只对该 PT spec 存在的字段强制；目标值不在 enum 时按安全序列回退（最后 enum[0]）；
    同步清掉文档字段（LLM 幻觉的 fake 证书 ID 会被渠道拒）。
    """
    visible = dict(visible)
    overrides: dict[str, Any] = {}
    for fname, forced in _NO_CERT_FORCES.items():
        if fname not in pt_props:
            continue
        forced_val = forced
        f_def = pt_props[fname] if isinstance(pt_props[fname], dict) else {}
        f_enum = f_def.get("enum") or []
        if isinstance(f_enum, list) and f_enum and forced_val not in f_enum:
            forced_val = next((s for s in _SAFE_ENUM_SEQUENCE if s in f_enum), f_enum[0])
        visible[fname] = forced_val
        overrides[fname] = forced_val
    cleared = [f for f in _DANGEROUS_DOC_FIELDS if f in visible]
    for fname in cleared:
        visible.pop(fname, None)
    if cleared:
        overrides["__cleared__"] = cleared
    return visible, overrides


def _llm_fill(product: dict[str, Any], wpt: str) -> dict[str, Any]:
    """attrs['walmart_fill'][wpt]（attr_fill 增量 3 产物；无则空）。"""
    attrs = product.get("attrs") or {}
    fill = (attrs.get("walmart_fill") or {}).get(wpt)
    return fill if isinstance(fill, dict) else {}


def _build_visible(
    product: dict[str, Any],
    wpt: str,
    schema: wpt_schema.PtSchema | None,
    variant_attrs: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """build 模式 Visible：LLM 填充打底 → 系统字段覆盖 → 变体维度值 → 零认证覆盖压轴。"""
    attrs = product.get("attrs") or {}
    images = [u for u in (product.get("images") or []) if isinstance(u, str) and u.strip()]
    fill_visible = _llm_fill(product, wpt).get("visible") or {}
    visible: dict[str, Any] = {
        k: v
        for k, v in fill_visible.items()
        if k not in SYSTEM_VISIBLE_POSTPROCESS_FIELDS and k not in VARIANT_CONTROL_FIELDS
    }
    visible["brand"] = _FORCE_BRAND
    visible["shortDescription"] = str(attrs.get("description") or product.get("title") or "")[
        :_SHORT_DESC_MAX
    ]
    if images:
        visible["mainImageUrl"] = images[0]
        secondary = images[1 : 1 + _SECONDARY_IMAGE_MAX]
        if len(secondary) >= _SECONDARY_IMAGE_MIN:
            visible["productSecondaryImageURL"] = secondary
    bullets = [b for b in (attrs.get("bullets") or []) if isinstance(b, str) and b.strip()]
    if bullets:
        visible["keyFeatures"] = bullets[:_KEY_FEATURES_CAP]
    # 变体维度值并入（零认证覆盖之前；键=映射后 Walmart 属性名，值=维度值原文——进模板
    # 即进 build_hash 指纹，改维度/改映射自动失效缓存；未分组产品 variant_attrs 为空不动）
    for name, val in (variant_attrs or {}).items():
        visible[name] = val
    pt_props = schema.properties if schema is not None else {}
    return _zero_cert_overrides(visible, pt_props)


def _build_orderable_template(
    product: dict[str, Any], wpt: str, odefaults: dict[str, Any]
) -> dict[str, Any]:
    """build 模式 Orderable 模板（源仓 force_overrides Orderable 段保真）。

    合并序：模板默认 < LLM 填充（非后处理字段，如 electronicsIndicator/ShippingWeight
    推断）< 系统强制（后处理字段占位符，_instantiate 注入）。attrs.shipping_weight
    实抓值 > LLM 推断 > 默认 1。"""
    attrs = product.get("attrs") or {}
    orderable: dict[str, Any] = {
        "productName": (product.get("title") or "")[:_PRODUCT_NAME_MAX],
        "brand": _FORCE_BRAND,
        "ShippingWeight": 1.0,
        "electronicsIndicator": "No",
        "batteryTechnologyType": "Does Not Contain a Battery",
        "chemicalAerosolPesticide": "No",
        "shipsInOriginalPackaging": "No",
    }
    fill_orderable = _llm_fill(product, wpt).get("orderable") or {}
    orderable.update(
        {k: v for k, v in fill_orderable.items() if k not in SYSTEM_ORDERABLE_POSTPROCESS_FIELDS}
    )
    if attrs.get("shipping_weight"):
        orderable["ShippingWeight"] = float(attrs["shipping_weight"])
    orderable["productName"] = (product.get("title") or "")[:_PRODUCT_NAME_MAX]
    orderable["brand"] = _FORCE_BRAND
    orderable.update(
        {
            "sku": "{SKU}",
            "productIdentifiers": {"productIdType": "{GTIN_TYPE}", "productId": "{GTIN}"},
            "price": "{PRICE}",  # 裸 number（非对象），实例化时注入
            "specProductType": wpt,
            "country_of_origin_substantial_transformation": str(
                attrs.get("country_of_origin") or odefaults["country_of_origin"]
            ),
            "MustShipAlone": odefaults["must_ship_alone"],
            "fulfillmentLagTime": int(odefaults["fulfillment_lag_days"]),
            "startDate": "{START_DATE}",
            "endDate": "{END_DATE}",
            "inventory": "{INVENTORY}",
        }
    )
    return orderable


def _amazon_copy_view(product: dict[str, Any]) -> dict[str, Any]:
    """force_amazon_copy 的输入视图（旧 amazon_data 键名 → 新系统 product/attrs 映射）。"""
    attrs = product.get("attrs") or {}
    return {
        "title": product.get("title"),
        "bullet_points": attrs.get("bullets") or attrs.get("bullet_points"),
        "long_description": attrs.get("long_description"),
        "description": attrs.get("description"),
        "product_description": attrs.get("product_description"),
    }


def _brands_to_scrub(product: dict[str, Any]) -> list[str]:
    """文案去品牌词清单（源仓 main.py 过滤语义保真）。"""
    attrs = product.get("attrs") or {}
    out = []
    for b in (product.get("brand"), attrs.get("brand"), attrs.get("manufacturer")):
        s = str(b).strip() if b else ""
        if s and s not in ("N/A", "Unbranded", "Generic", "Unknown"):
            out.append(s)
    return out


def _match_identifier(product: dict[str, Any], gtin: str | None) -> tuple[str, str]:
    """match 模式标识符预检（BR-LST-015 fail-closed）：gtin 参数 > attrs.gtin/upc/ean。"""
    attrs = product.get("attrs") or {}
    candidate = gtin or attrs.get("gtin") or attrs.get("upc") or attrs.get("ean")
    if not candidate or not str(candidate).strip():
        raise BusinessError(
            "ERP_SPEC_BUILD_FAILED",
            "match 模式缺商品标识符（gtin/upc/ean 均空），无法跟卖",
        )
    ident = str(candidate).strip()
    return _identifier_type(ident), ident


def _build_match_item(product: dict[str, Any], gtin: str | None) -> dict[str, Any]:
    """MP_ITEM_MATCH v4.2 Item（required 五字段，BR-LST-015）。"""
    attrs = product.get("attrs") or {}
    id_type, ident = _match_identifier(product, gtin)
    return {
        "sku": "{SKU}",
        "productIdentifiers": {"productIdType": id_type, "productId": ident},
        "price": "{PRICE}",
        "ShippingWeight": float(attrs.get("shipping_weight") or 1),
        "condition": _MATCH_CONDITION,
    }


async def _resolve_variant(
    session: AsyncSession,
    product: dict[str, Any],
    *,
    offer_mode: str,
    wpt: str,
    vis_schema: wpt_schema.PtSchema | None,
    variant_ctx: dict[str, Any] | None,
    skip_variant: bool,
) -> tuple[dict[str, Any] | None, dict[str, str], str | None, list[str]]:
    """变体上下文解析（D-Q63）→ (variant_fp, mapped_attrs, group_ref, attr_names)。

    match/skip/未分组 → 全空（构建器走非变体路径，指纹/缓存不变）；broken 组、PT 不支持
    变体、维度键不可映射一律 fail-closed（ERP_SPEC_BUILD_FAILED，照 _resolve_wpt 模式）。
    variant_fp 非空时并入 fingerprint（新键 "variant"）——改维度/改映射自动失效缓存。
    """
    if offer_mode == "match" or skip_variant:
        return None, {}, None, []
    vctx = (
        variant_ctx
        if variant_ctx is not None
        else await variant_svc.load_build_context(session, int(product["id"]))
    )
    if vctx is None:  # 未分组
        return None, {}, None, []
    if str(vctx["status"]) == "broken":  # broken 组兜底拒绝（001 §03；D-Q63③ 配套）
        raise BusinessError(
            "ERP_SPEC_BUILD_FAILED",
            f"变体组 #{vctx['group_id']} broken：spec 构建拒绝",
            {"group_id": vctx["group_id"]},
        )
    vis_props = vis_schema.properties if vis_schema is not None else {}
    if "variantGroupId" not in vis_props or "variantAttributeNames" not in vis_props:
        raise BusinessError(  # PT 不支持变体
            "ERP_SPEC_BUILD_FAILED",
            f"PT {wpt} 不支持变体（spec 缺 variantGroupId/variantAttributeNames 字段）",
            {"wpt": wpt},
        )
    if not vctx["variant_attrs"]:  # 成员行缺失/维度值空——根因直说，别漂到 validator 才拦
        raise BusinessError(
            "ERP_SPEC_BUILD_FAILED",
            f"变体组 #{vctx['group_id']} 成员维度值缺失（product 无 variant_member 维度）",
            {"group_id": vctx["group_id"]},
        )
    theme_map = await variant_svc.theme_map(session)
    mapped = _map_variant_attrs(vctx["variant_attrs"], theme_map, vis_props)
    attr_names = sorted(mapped)
    variant_fp = {"mapped_attrs": mapped, "attr_names": attr_names}
    return variant_fp, mapped, str(vctx["group_ref"]), attr_names


async def build_spec(
    session: AsyncSession,
    *,
    product: dict[str, Any],
    offer_mode: str,
    gtin: str | None,
    channel_sku: str,
    price: float | None,
    inventory: int,
    end_date: date | None = None,
    partner_id: str | None = None,
    variant_ctx: dict[str, Any] | None = None,
    skip_variant: bool = False,
) -> dict[str, Any]:
    """→ {spec_id, item, wpt, build_hash, spec_version}（命中缓存直接复用模板）。

    item = 单个 MPItem 元素（build={Visible,Orderable}；match={Item}）；
    feed 级 header 由 feed_header() 供 service 组装。

    变体段（仅 build；match 豁免——D-Q3 参数包差异）：分组产品的成员维度值经 theme_map
    映射后并入 Visible 模板（进指纹），variantGroupId/variantAttributeNames 于 _instantiate
    注入（不进指纹）；broken 组/维度不可映射/PT 不支持变体一律 fail-closed（D-Q63）。
    variant_ctx 供 service 传入已加载上下文免重查；skip_variant 供豁免路径。
    """
    attrs = product.get("attrs") or {}
    header_cfg = await _cfg(session, "listing.feed_header", _HEADER_DEFAULTS)
    odefaults = await _cfg(session, "listing.orderable_defaults", _ORDERABLE_DEFAULTS)
    pt_rev = await wpt_schema.pt_spec_revision(session)

    if offer_mode == "match":
        wpt, wpt_source = attrs.get("wpt") or "", "match_na"  # match 不需要 WPT
        spec_version = SPEC_VERSION_MATCH
    else:
        wpt, wpt_source = await _resolve_wpt(session, product)
        spec_version = str(header_cfg["version"])

    vis_schema = None if offer_mode == "match" else await wpt_schema.load_pt_schema(session, wpt)
    ord_schema = None if offer_mode == "match" else await wpt_schema.load_orderable_schema(session)

    # 变体段解析（仅 build；match 豁免）——须在指纹前：映射后维度值/属性名进 build_hash
    variant_fp, variant_mapped, variant_group_ref, variant_attr_names = await _resolve_variant(
        session, product, offer_mode=offer_mode, wpt=wpt, vis_schema=vis_schema,
        variant_ctx=variant_ctx, skip_variant=skip_variant,
    )  # fmt: skip

    fp_dict: dict[str, Any] = {
        "title": product.get("title"),
        "brand": product.get("brand"),
        "images": product.get("images"),
        "attrs": attrs,
        "wpt": wpt,
        "wpt_source": wpt_source,
        "mode": offer_mode,
        "spec_version": spec_version,
        "odefaults": odefaults,
        "pt_spec_revision": pt_rev,
    }
    if variant_fp is not None:  # 未分组产品不加此键——指纹字节不变，缓存兼容勿全量失效
        fp_dict["variant"] = variant_fp
    fingerprint = json.dumps(fp_dict, ensure_ascii=False, sort_keys=True)
    build_hash = hashlib.sha256(fingerprint.encode()).hexdigest()

    def _validated(item: dict[str, Any]) -> dict[str, Any]:
        report = (
            validator.validate_match_item(item)
            if offer_mode == "match"
            else validator.validate_build_item(item, wpt, vis_schema, ord_schema)
        )
        return {"ok": report.ok, "errors": report.errors, "warnings": report.warnings}

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
        item = _instantiate(
            cached.payload, channel_sku, gtin, price, inventory,
            end_date=end_date, partner_id=partner_id,
            end_date_default=str(odefaults["end_date_default"]),
            variant_group_ref=variant_group_ref, variant_attr_names=variant_attr_names,
        )  # fmt: skip
        return {
            "spec_id": cached.id,
            "item": item,
            "wpt": cached.wpt,
            "build_hash": build_hash,
            "spec_version": spec_version,
            "validation": _validated(item),
        }

    cert_overrides: dict[str, Any] = {}
    coerce_notes: list[str] = []
    if offer_mode == "match":
        item_template: dict[str, Any] = {"Item": _build_match_item(product, gtin)}
    else:
        visible, cert_overrides = _build_visible(product, wpt, vis_schema, variant_mapped)
        orderable = _build_orderable_template(product, wpt, odefaults)
        # 清洗/coerce 链（增量 4，源仓 prepare_one_async 链序保真）
        visible, orderable, coerce_notes = coerce.run_coerce_chain(
            visible,
            orderable,
            amazon=_amazon_copy_view(product),
            brands_to_remove=_brands_to_scrub(product),
            vis_schema=vis_schema,
            ord_schema=ord_schema,
        )
        item_template = {"Orderable": orderable, "Visible": {wpt: visible}}

    spec_id = (
        await session.execute(
            text(
                "INSERT INTO app.listing_spec"
                " (team_id, product_id, offer_mode, wpt, spec_version, payload,"
                "  cert_overrides, build_hash)"
                " VALUES (:t, :p, :m, :w, :v, cast(:pl AS jsonb), cast(:co AS jsonb), :h)"
                " ON CONFLICT (product_id, offer_mode, build_hash) DO UPDATE"
                "   SET built_at = now()"
                " RETURNING id"
            ),
            {
                "t": product["team_id"],
                "p": product["id"],
                "m": offer_mode,
                "w": wpt,  # match 模式无 WPT 存 ''（列 NOT NULL；build 恒非空）
                "v": spec_version,
                "pl": json.dumps(item_template, ensure_ascii=False),
                "co": json.dumps(cert_overrides, ensure_ascii=False),
                "h": build_hash,
            },
        )
    ).scalar_one()
    item = _instantiate(
        item_template, channel_sku, gtin, price, inventory,
        end_date=end_date, partner_id=partner_id,
        end_date_default=str(odefaults["end_date_default"]),
        variant_group_ref=variant_group_ref, variant_attr_names=variant_attr_names,
    )  # fmt: skip
    return {
        "spec_id": spec_id,
        "item": item,
        "wpt": wpt,
        "build_hash": build_hash,
        "spec_version": spec_version,
        "coerce_notes": coerce_notes,
        "validation": _validated(item),
    }


def _instantiate(
    template: dict[str, Any],
    sku: str,
    gtin: str | None,
    price: float | None,
    inventory: int,
    *,
    end_date: date | None = None,
    partner_id: str | None = None,
    end_date_default: str = "2049-12-31",
    variant_group_ref: str | None = None,
    variant_attr_names: list[str] | None = None,
) -> dict[str, Any]:
    """缓存模板 → 具体 listing 提交体（listing/store 级参数注入，不进缓存键）。

    变体段（D-Q63）：variant_group_ref 非空时向 Visible.{wpt} 注入 variantGroupId=组中立
    引用、variantAttributeNames=排序映射属性名；isPrimaryVariant 一律不注入（Walmart 自动选主）。

    实测格式（BR-LST-006/007）：endDate 必须 ISO DateTime（纯日期拒收，
    EXT_DATA_ERROR_00030257670757），且带毫秒 .000Z——2049 远期值的唯一实测
    成功写法（BR-RET-007，2026-05-08 校正）；inventory=[{quantity,
    fulfillmentCenterID=PartnerID}]——PartnerID 缺失时省略该键（本地校验器
    实践规则层负责提示，官方 schema 不含它）。
    """
    payload: dict[str, Any] = json.loads(json.dumps(template))
    if "Item" in payload:  # match
        item = payload["Item"]
        item["sku"] = sku
        item["price"] = float(price) if price is not None else 0.0
        return payload

    orderable = payload["Orderable"]
    orderable["sku"] = sku
    if gtin:
        orderable["productIdentifiers"] = {
            "productIdType": _identifier_type(gtin),
            "productId": gtin,
        }
    else:
        orderable.pop("productIdentifiers", None)
    orderable["price"] = float(price) if price is not None else 0.0
    orderable["startDate"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = end_date or date.fromisoformat(end_date_default)  # D-Q9 统一远期（配置中心可调）
    orderable["endDate"] = f"{end.isoformat()}T00:00:00.000Z"
    inv_entry: dict[str, Any] = {"quantity": int(inventory)}
    if partner_id:
        inv_entry["fulfillmentCenterID"] = str(partner_id)
    orderable["inventory"] = [inv_entry]
    # 变体控制字段收口（D-Q63）：isPrimaryVariant 一律剥离（渠道自动选主）；
    # 组标识仅对分组产品注入，未分组时兜底清除（防旧缓存模板/幻觉残留）
    for vis in (payload.get("Visible") or {}).values():
        vis.pop("isPrimaryVariant", None)
        if variant_group_ref is not None:
            vis["variantGroupId"] = variant_group_ref
            vis["variantAttributeNames"] = list(variant_attr_names or [])
        else:
            vis.pop("variantGroupId", None)
            vis.pop("variantAttributeNames", None)
    return payload
