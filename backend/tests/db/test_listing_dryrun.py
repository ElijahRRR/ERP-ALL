"""R2-03 验收① harness 自测：5 个不同 WPT 的 dry-run 产物过官方 spec 校验。

沙盒用 fixture 规格（真实形态：必填/枚举/allOf/图片约束）验证 harness 本身；
部署机用真数据（live spec / T7 monolith → import 通道）跑同一工具出验收证据。
"""

import asyncio
import json
from typing import Any

import psycopg
import pytest

from erp.listing import wpt_schema
from erp.tools.listing_dryrun import run_dryrun

from .conftest import MIGRATOR_URL, _pg_dsn

PREFIX = "ZDR"
TEAM = "dryrun验收测试团队"
WPTS = [f"{PREFIX}_{n}" for n in ("Mugs", "Boards", "Lamps", "Rugs", "Bottles")]


def _pt_fields(wpt: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["shortDescription", "keyFeatures", "material"],
        "properties": {
            "shortDescription": {"type": "string", "maxLength": 4000},
            "productName": {"type": "string", "maxLength": 199},
            "brand": {"type": "string"},
            "mainImageUrl": {"type": "string", "format": "uri"},
            "productSecondaryImageURL": {
                "type": "array",
                "minItems": 5,
                "items": {"type": "string", "format": "uri"},
            },
            "keyFeatures": {"type": "array", "minItems": 3, "items": {"type": "string"}},
            "material": {
                "type": "array",
                "items": {"type": "string", "enum": [f"{wpt}-Mat-A", f"{wpt}-Mat-B", "Metal"]},
            },
            "has_written_warranty": {"type": "string", "enum": ["Yes - Warranty Text", "No"]},
            "isAssemblyRequired": {"type": "string", "enum": ["Yes", "No"]},
        },
        "allOf": [
            {
                "if": {"properties": {"isAssemblyRequired": {"enum": ["Yes"]}}},
                "then": {"required": ["assemblyInstructions"]},
            }
        ],
    }


_ORDERABLE = {
    "type": "object",
    "required": ["sku", "productIdentifiers", "productName", "price", "ShippingWeight"],
    "properties": {
        "sku": {"type": "string", "maxLength": 50},
        "productIdentifiers": {
            "type": "object",
            "properties": {
                "productIdType": {"type": "string", "enum": ["UPC", "EAN", "GTIN", "ISBN"]},
                "productId": {"type": "string", "maxLength": 14},
            },
            "required": ["productIdType", "productId"],
        },
        "productName": {"type": "string", "maxLength": 199},
        "brand": {"type": "string"},
        "price": {"type": "number"},
        "ShippingWeight": {"type": "number"},
        "electronicsIndicator": {"type": "string", "enum": ["Yes", "No"]},
        "batteryTechnologyType": {"type": "string"},
        "chemicalAerosolPesticide": {"type": "string", "enum": ["Yes", "No"]},
        "shipsInOriginalPackaging": {"type": "string", "enum": ["Yes", "No"]},
        "specProductType": {"type": "string"},
        "country_of_origin_substantial_transformation": {"type": "string"},
        "MustShipAlone": {"type": "string", "enum": ["Yes", "No"]},
        "fulfillmentLagTime": {"type": "integer"},
        "startDate": {"type": "string"},
        "endDate": {"type": "string"},
        "inventory": {"type": "array"},
    },
}


@pytest.fixture(scope="module", autouse=True)
def _seed(migrated_db: str):  # type: ignore[no-untyped-def]
    def _wipe(conn) -> None:  # type: ignore[no-untyped-def]
        conn.execute(
            "DELETE FROM refdata.pt_spec WHERE walmart_product_type LIKE %s"
            " OR walmart_product_type = %s",
            (f"{PREFIX}%", wpt_schema.ORDERABLE_PSEUDO_PT),
        )
        team = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()
        if team:
            conn.execute("DELETE FROM app.listing_spec WHERE team_id = %s", (team[0],))
            conn.execute("DELETE FROM app.product WHERE team_id = %s", (team[0],))

    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _wipe(conn)
        conn.execute("INSERT INTO app.team (name) VALUES (%s) ON CONFLICT DO NOTHING", (TEAM,))
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        for wpt in WPTS:
            conn.execute(
                "INSERT INTO refdata.pt_spec (walmart_product_type, fields) VALUES (%s, %s::jsonb)",
                (wpt, json.dumps(_pt_fields(wpt))),
            )
        conn.execute(
            "INSERT INTO refdata.pt_spec (walmart_product_type, fields) VALUES (%s, %s::jsonb)",
            (wpt_schema.ORDERABLE_PSEUDO_PT, json.dumps(_ORDERABLE)),
        )
        pids = []
        for i, wpt in enumerate(WPTS):
            attrs = {
                "wpt": wpt,
                "bullets": [
                    "Premium quality build for daily use",
                    "Easy to clean and maintain",
                    "Large capacity fits every need",
                    "Food safe certified materials",
                ],
                "description": "A well made product",
                "shipping_weight": 1.5 + i,
            }
            pid = conn.execute(
                "INSERT INTO app.product (team_id, source_channel, source_ref, title, images,"
                " attrs, status) VALUES (%s, 'amazon', %s, %s, %s::jsonb, %s::jsonb,"
                " 'audit_passed') RETURNING id",
                (
                    team_id,
                    f"{PREFIX}ASIN{i:02d}",
                    f"Great {wpt.split('_')[1]} Product Number {i} For Home",
                    json.dumps([f"https://img.example/{i}/{k}.jpg" for k in range(7)]),
                    json.dumps(attrs),
                ),
            ).fetchone()[0]
            pids.append(pid)
    yield {"team": team_id, "pids": pids}
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _wipe(conn)


def test_dryrun_five_wpt_pass(_seed: dict) -> None:
    report = asyncio.run(run_dryrun(_seed["pids"]))
    s = report["summary"]
    assert s["products"] == 5
    assert s["validation_ok"] == 5, [r.get("validation") for r in report["results"]]
    assert s["validation_errors_total"] == 0
    assert s["distinct_wpt_count"] == 5
    assert s["pass"] is True
    # feed 封套：真 header 3 字段 + 5 个 item
    feed = report["feed"]
    assert set(feed["MPItemFeedHeader"]) == {"businessUnit", "locale", "version"}
    assert len(feed["MPItem"]) == 5
    # 逐产品：清洗链产物形态（必填 material 被 safe_default 补齐、零认证覆盖生效）
    for r in report["results"]:
        visible = r["item"]["Visible"][r["wpt"]]
        assert visible["material"]  # fill_missing_required 补了枚举安全值
        assert visible["has_written_warranty"] == "No"
        assert visible["isAssemblyRequired"] == "No"  # → allOf 不触发 assemblyInstructions
        assert "assemblyInstructions" not in visible
        orderable = r["item"]["Orderable"]
        assert orderable["productIdentifiers"]["productIdType"] == "EAN"
        assert "T" in orderable["endDate"]


def test_dryrun_below_min_wpt_fails(_seed: dict) -> None:
    report = asyncio.run(run_dryrun(_seed["pids"][:2]))
    s = report["summary"]
    assert s["validation_ok"] == 2 and s["distinct_wpt_count"] == 2
    assert s["pass"] is False  # 不足 5 个 WPT
