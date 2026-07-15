"""refdata.pt_spec.fields 规格访问层（R2-03 增量 2；spec 构建器/AI 属性填写/校验器共用）。

- fields = per-PT 官方 MP_ITEM v5 原始 schema 节点（0020 无损列，001 §03）。
- 共享 Orderable 段在伪行 '__orderable__'（0020 协议）。
- summarize_prop 语义移植源仓 auto_listing/pt_spec.py:157 _summarize_prop（拍平供
  LLM prompt/校验摘要），原始节点始终随 FieldSpec 保留（校验器用原始节点，零损）。
- 版本键 = refdata.dataset_revision('pt_spec')（0019 触发器，任何写入方都 bump）——
  进 build_hash，规格数据更新自动失效 spec 缓存。
"""

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ORDERABLE_PSEUDO_PT = "__orderable__"


@dataclass
class FieldSpec:
    """单字段规格：raw 节点零损 + 常用约束拍平。"""

    name: str
    required: bool
    raw: dict[str, Any] = field(repr=False)
    type: str | None = None
    title: str | None = None
    desc: str | None = None
    enum: list[Any] | None = None
    example: Any = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    multiple_of: float | None = None
    min_items: int | None = None
    max_items: int | None = None
    item_type: str | None = None
    item_enum: list[Any] | None = None
    object_props: list[str] | None = None
    object_required: list[str] | None = None


def summarize_prop(name: str, node: dict[str, Any], required: bool) -> FieldSpec:
    """schema 节点 → FieldSpec（语义移植源仓 _summarize_prop；raw 原样保留）。"""
    fs = FieldSpec(
        name=name,
        required=required,
        raw=node,
        type=node.get("type"),
        title=node.get("title"),
        desc=node.get("description"),
        enum=node.get("enum"),
        example=node.get("examples") or node.get("example"),
        min_length=node.get("minLength"),
        max_length=node.get("maxLength"),
        pattern=node.get("pattern"),
        minimum=node.get("minimum"),
        maximum=node.get("maximum"),
        multiple_of=node.get("multipleOf"),
        min_items=node.get("minItems"),
        max_items=node.get("maxItems"),
    )
    items = node.get("items")
    if isinstance(items, dict):
        fs.item_type = items.get("type")
        fs.item_enum = items.get("enum")
    props = node.get("properties")
    if isinstance(props, dict):
        fs.object_props = list(props)
        req = node.get("required")
        fs.object_required = [r for r in req if isinstance(r, str)] if isinstance(req, list) else []
    return fs


@dataclass
class PtSchema:
    """一个 PT（或 Orderable 段）的规格视图。"""

    name: str
    raw: dict[str, Any] = field(repr=False)

    @property
    def properties(self) -> dict[str, Any]:
        props = self.raw.get("properties")
        return props if isinstance(props, dict) else {}

    @property
    def required(self) -> list[str]:
        req = self.raw.get("required")
        return [r for r in req if isinstance(r, str)] if isinstance(req, list) else []

    @property
    def all_of(self) -> list[dict[str, Any]]:
        blocks = self.raw.get("allOf")
        if not isinstance(blocks, list):
            return []
        return [b for b in blocks if isinstance(b, dict)]

    def field_specs(self) -> list[FieldSpec]:
        req = set(self.required)
        return [
            summarize_prop(name, node, name in req)
            for name, node in self.properties.items()
            if isinstance(node, dict)
        ]


async def load_pt_schema(session: AsyncSession, wpt: str) -> PtSchema | None:
    """按 PT 名取原始 schema 节点（fields 为空/行不存在 → None，调用方决定降级）。"""
    row = (
        await session.execute(
            text(
                "SELECT fields FROM refdata.pt_spec"
                " WHERE walmart_product_type = :pt AND fields IS NOT NULL"
            ),
            {"pt": wpt},
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    raw = row if isinstance(row, dict) else json.loads(row)
    return PtSchema(name=wpt, raw=raw)


async def load_orderable_schema(session: AsyncSession) -> PtSchema | None:
    return await load_pt_schema(session, ORDERABLE_PSEUDO_PT)


async def pt_spec_revision(session: AsyncSession) -> int:
    """dataset_revision('pt_spec')——进 build_hash 使规格更新失效 spec 缓存。"""
    rev = (
        await session.execute(
            text("SELECT revision FROM refdata.dataset_revision WHERE dataset = 'pt_spec'")
        )
    ).scalar_one_or_none()
    return int(rev or 0)
