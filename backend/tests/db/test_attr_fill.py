"""R2-03 增量 3 验收：AI 属性填写（module=listing 记账 + fail-closed + 构建器合并）。

- 三段路径：填充写 attrs['walmart_fill'][wpt]；usage 行 module='listing'
- 缓存：同输入第二次 calls 不增；坏 JSON 不入缓存（重跑自愈 calls=2）+ 不写回
- 系统后处理字段剥除（LLM 输出 brand/price 之类不落库）
- prompt：五块结构 + allOf 条件必填中文翻译 + 静态前缀在产品数据之前
- 构建器合并序：LLM 填充打底 < 系统字段 < 零认证覆盖压轴
"""

import asyncio
import json
from typing import Any

import httpx
import psycopg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from erp.audit.llm import llm_client
from erp.core.db import system_tx
from erp.listing import attr_fill, wpt_schema
from erp.listing import spec as spec_builder

from .conftest import APP_URL, MIGRATOR_URL, _pg_dsn

PREFIX = "ZAF"
WPT = f"{PREFIX}_Lamps"
TEAM = "属性填写测试团队"


def _sessions():  # type: ignore[no-untyped-def]
    return async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)


class _FakeLlm:
    def __init__(self) -> None:
        self.calls = 0
        self.script: list[dict | str] = []
        self.last_messages: list[dict] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        payload = json.loads(request.content)
        self.last_messages = payload.get("messages")
        body = self.script.pop(0) if self.script else {"visible": {}, "orderable": {}}
        content = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 800, "completion_tokens": 120},
            },
        )


@pytest.fixture()
def fake_llm() -> _FakeLlm:
    fake = _FakeLlm()
    llm_client._transport_factory = lambda: httpx.MockTransport(fake.handler)
    yield fake
    llm_client._transport_factory = None


_PT_FIELDS = {
    "type": "object",
    "required": ["shortDescription", "light_bulb_type", "material"],
    "properties": {
        "shortDescription": {"type": "string", "maxLength": 4000},
        "light_bulb_type": {
            "type": "string",
            "title": "Light Bulb Type",
            "enum": ["LED", "Incandescent", "Halogen"],
        },
        "material": {"type": "array", "items": {"type": "string", "enum": ["Metal", "Glass"]}},
        "has_written_warranty": {"type": "string", "enum": ["Yes - Warranty Text", "No"]},
        "shade_color": {"type": "string"},
    },
    "allOf": [
        {
            "if": {"properties": {"light_bulb_type": {"enum": ["LED"]}}},
            "then": {"required": ["wattage"]},
        }
    ],
}
_ORDERABLE_FIELDS = {
    "type": "object",
    "required": ["sku", "price"],
    "properties": {
        "sku": {"type": "string"},
        "price": {"type": "number"},
        "electronicsIndicator": {"type": "string", "enum": ["Yes", "No"]},
        "ShippingWeight": {"type": "number"},
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
            # usage 行必须清：CI 迁移演练在 pytest 后跑，遗留 module='listing' 行
            # 会让 0020 downgrade 的 CHECK 收窄验证失败（本文件是唯一写入方）
            conn.execute("DELETE FROM app.llm_usage_log WHERE team_id = %s", (team[0],))
            conn.execute("DELETE FROM app.product WHERE team_id = %s", (team[0],))

    pricing = json.dumps(
        {"deepseek-v4-flash": {"input_per_1m": 0.14, "input_cache_hit_per_1m": 0.0028,
                               "output_per_1m": 0.28}}
    )  # fmt: skip
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _wipe(conn)
        conn.execute("INSERT INTO app.team (name) VALUES (%s) ON CONFLICT DO NOTHING", (TEAM,))
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        conn.execute(
            "INSERT INTO refdata.pt_spec (walmart_product_type, fields)"
            " VALUES (%s, %s::jsonb), (%s, %s::jsonb)",
            (WPT, json.dumps(_PT_FIELDS), wpt_schema.ORDERABLE_PSEUDO_PT,
             json.dumps(_ORDERABLE_FIELDS)),
        )  # fmt: skip
        conn.execute(
            "INSERT INTO app.system_config (key, value) VALUES ('llm.pricing', %s)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (pricing,),
        )
        pid = conn.execute(
            "INSERT INTO app.product (team_id, source_channel, source_ref, title, attrs, status)"
            " VALUES (%s, 'amazon', 'ZAFASIN01', 'Modern LED Desk Lamp',"
            '  \'{"wpt": "' + WPT + '", "description": "nice lamp",'
            '    "bullets": ["bright"], "shipping_weight": 3.0}\', \'audit_passed\')'
            " RETURNING id",
            (team_id,),
        ).fetchone()[0]
        conn.execute("DELETE FROM app.llm_cache")  # 确定性调用数
    yield {"team": team_id, "pid": pid}
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _wipe(conn)


def _fill(pid: int) -> dict[str, Any]:
    return asyncio.run(attr_fill.fill_product_attrs(_sessions(), product_id=pid))


def _stored_fill(pid: int) -> dict[str, Any] | None:
    with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
        attrs = conn.execute("SELECT attrs FROM app.product WHERE id = %s", (pid,)).fetchone()[0]
    return (attrs.get("walmart_fill") or {}).get(WPT)


class TestFill:
    def test_fill_writes_attrs_and_usage(self, _seed: dict, fake_llm: _FakeLlm) -> None:
        fake_llm.script = [
            {
                "visible": {
                    "light_bulb_type": "LED",
                    "material": ["Metal"],
                    "brand": "EvilBrand",  # 后处理字段必须被剥
                },
                "orderable": {"electronicsIndicator": "Yes", "price": 999},  # price 被剥
            }
        ]
        r = _fill(_seed["pid"])
        assert r["filled"] is True and r["wpt"] == WPT
        stored = _stored_fill(_seed["pid"])
        assert stored is not None
        assert stored["visible"] == {"light_bulb_type": "LED", "material": ["Metal"]}
        assert stored["orderable"] == {"electronicsIndicator": "Yes"}
        assert stored["model"] == "deepseek-v4-flash"
        # prompt：五块 + 条件必填翻译 + 静态前缀在产品数据前
        user = fake_llm.last_messages[1]["content"]
        assert "## Walmart Visible." + WPT + " 必填字段及定义" in user
        assert "light_bulb_type" in user and "枚举=[LED,Incandescent,Halogen]" in user
        assert "若 light_bulb_type ∈ ['LED'] → 必须同时填: ['wattage']" in user
        assert user.index("必填字段及定义") < user.index("▼▼▼ 本次特定数据")
        assert "brand" not in json.loads(user.split("```json\n")[1].split("\n```")[0])
        # usage 归因 module=listing
        with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
            n = conn.execute(
                "SELECT count(*) FROM app.llm_usage_log WHERE module = 'listing'"
                " AND object_type = 'product' AND object_id = %s",
                (_seed["pid"],),
            ).fetchone()[0]
        assert n >= 1

    def test_cache_hit_no_second_call(self, _seed: dict, fake_llm: _FakeLlm) -> None:
        fake_llm.script = [
            {"visible": {"light_bulb_type": "LED", "material": ["Metal"]}, "orderable": {}}
        ]
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            conn.execute("DELETE FROM app.llm_cache")
        r1 = _fill(_seed["pid"])
        r2 = _fill(_seed["pid"])
        assert r1["filled"] and r2["filled"]
        assert r2.get("cache_hit") is True
        assert fake_llm.calls == 1

    def test_bad_json_fail_closed_and_self_heal(self, _seed: dict, fake_llm: _FakeLlm) -> None:
        fake_llm.script = [
            "这不是JSON",
            {"visible": {"light_bulb_type": "Halogen", "material": ["Glass"]}, "orderable": {}},
        ]
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            conn.execute("DELETE FROM app.llm_cache")
            conn.execute(
                "UPDATE app.product SET attrs = attrs - 'walmart_fill' WHERE id = %s",
                (_seed["pid"],),
            )
        r1 = _fill(_seed["pid"])
        assert r1["filled"] is False
        assert _stored_fill(_seed["pid"]) is None  # fail-closed 不写回
        r2 = _fill(_seed["pid"])  # 坏响应未入缓存 → 重跑真调自愈
        assert r2["filled"] is True and fake_llm.calls == 2

    def test_no_pt_schema_no_llm_call(self, _seed: dict, fake_llm: _FakeLlm) -> None:
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            pid = conn.execute(
                "INSERT INTO app.product (team_id, source_channel, source_ref, title, attrs,"
                " status) VALUES (%s, 'amazon', 'ZAFASIN02', 'No Schema Item',"
                " '{\"wpt\": \"ZAF_NoSchema\"}', 'audit_passed') RETURNING id",
                (_seed["team"],),
            ).fetchone()[0]
        r = _fill(pid)
        assert r == {"product_id": pid, "wpt": "ZAF_NoSchema", "filled": False,
                     "reason": "no_pt_schema"}  # fmt: skip
        assert fake_llm.calls == 0


class TestBuilderMerge:
    def test_merge_order_llm_below_system_and_cert(self, _seed: dict, fake_llm: _FakeLlm) -> None:
        fake_llm.script = [
            {
                "visible": {
                    "light_bulb_type": "LED",
                    "material": ["Metal"],
                    "shade_color": "White",
                    "has_written_warranty": "Yes - Warranty Text",  # 零认证必须压掉
                    "shortDescription": "llm text",  # 系统字段必须压掉
                },
                "orderable": {"electronicsIndicator": "Yes", "ShippingWeight": 9.9},
            }
        ]
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            conn.execute("DELETE FROM app.llm_cache")
            conn.execute(
                "UPDATE app.product SET attrs = attrs - 'walmart_fill' WHERE id = %s",
                (_seed["pid"],),
            )
        assert _fill(_seed["pid"])["filled"] is True

        async def _build() -> dict[str, Any]:
            async with system_tx(_sessions()) as s:
                from sqlalchemy import text

                product = dict(
                    (
                        await s.execute(
                            text(
                                "SELECT id, team_id, title, brand, images, attrs,"
                                " category_path, amazon_leaf_id"
                                " FROM app.product WHERE id = :p"
                            ),
                            {"p": _seed["pid"]},
                        )
                    )
                    .mappings()
                    .one()
                )
                return await spec_builder.build_spec(
                    s, product=product, offer_mode="build", gtin="6901234567892",
                    channel_sku="M0000009", price=29.99, inventory=5,
                )  # fmt: skip

        built = asyncio.run(_build())
        v = built["item"]["Visible"][WPT]
        assert v["light_bulb_type"] == "LED" and v["shade_color"] == "White"  # LLM 保留
        # 系统文案压过 LLM：force_amazon_copy 用 bullets 拼段落，<60 词拼 title 补（源仓链）
        assert v["shortDescription"] == "Modern LED Desk Lamp. bright"
        assert v["has_written_warranty"] == "No"  # 零认证覆盖压轴（enum 含 No）
        o = built["item"]["Orderable"]
        assert o["electronicsIndicator"] == "Yes"  # LLM 非后处理字段生效
        assert o["ShippingWeight"] == 3.0  # attrs 实抓值 > LLM 推断
