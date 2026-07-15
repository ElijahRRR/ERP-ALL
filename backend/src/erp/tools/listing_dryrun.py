"""R2-03 验收① harness：dry-run 产物过官方 spec 校验（≥5 个不同 WPT）。

用法（部署机，pt_spec.fields 数据就位后）：
  # 指定产品（建议覆盖 ≥5 个不同 WPT）
  python -m erp.tools.listing_dryrun --product-id 1 --product-id 2 ... \\
      --out .agent/evidence/R2-03/dryrun-report.json

  # 自动选品：审核通过的产品里按 WPT 去重取前 N 个
  python -m erp.tools.listing_dryrun --auto 8 --out ...

  # 先跑 AI 属性填写（需 LLM 凭证；缺规格/已填过自动跳过）
  python -m erp.tools.listing_dryrun --auto 8 --fill --out ...

产物：逐产品 {wpt, validation{ok,errors,warnings}, coerce_notes, item} + 完整
feed 封套（真 header + sanitize）+ 汇总（distinct WPT 数 / errors 总数 / PASS 判定）。
PASS = 全部产品 validation.ok 且 distinct WPT ≥ 5。

GTIN 用校验位合法的占位 EAN-13（dry-run 不占池）；SKU/价格/库存用演示值——
这些是 listing 级参数不影响 spec 校验语义。真实提交走 API（a152-live-runbook）。
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

from erp.core.db import get_session_factory, system_tx
from erp.listing import attr_fill
from erp.listing import spec as spec_builder
from erp.listing.coerce import sanitize_feed_numbers

MIN_DISTINCT_WPT = 5


def _ean13(seed: int) -> str:
    body = f"69{seed % 10**10:010d}"
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(body))
    return body + str((10 - total % 10) % 10)


async def _load_products(ids: list[int]) -> list[dict[str, Any]]:
    sessions = get_session_factory()
    async with system_tx(sessions) as s:
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT id, team_id, source_ref, title, brand, images, attrs,"
                        " category_path, amazon_leaf_id FROM app.product WHERE id = ANY(:ids)"
                        " ORDER BY id"
                    ),
                    {"ids": ids},
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


async def _auto_pick(limit: int) -> list[int]:
    """审核通过产品里选 attrs.wpt / 类目直判可得 WPT 的，尽量覆盖不同 WPT。"""
    sessions = get_session_factory()
    async with system_tx(sessions) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id FROM app.product"
                    " WHERE status IN ('audit_passed', 'ready') ORDER BY id LIMIT :n"
                ),
                {"n": limit * 5},
            )
        ).all()
    return [r[0] for r in rows][: limit * 5]


async def run_dryrun(
    product_ids: list[int], *, fill: bool = False, limit: int | None = None
) -> dict[str, Any]:
    """→ 验收报告 dict（可 json.dump）。"""
    sessions = get_session_factory()
    products = await _load_products(product_ids)
    results: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    seen_wpt: set[str] = set()

    for product in products:
        if limit and len(results) >= limit and len(seen_wpt) >= MIN_DISTINCT_WPT:
            break
        pid = int(product["id"])
        if fill:
            fill_result = await attr_fill.fill_product_attrs(sessions, product_id=pid)
            if fill_result.get("filled"):
                products_fresh = await _load_products([pid])  # 重载拿到 walmart_fill
                product = products_fresh[0]  # noqa: PLW2901
        entry: dict[str, Any] = {"product_id": pid, "source_ref": product.get("source_ref")}
        try:
            async with system_tx(sessions) as s:
                built = await spec_builder.build_spec(
                    s,
                    product=product,
                    offer_mode="build",
                    gtin=_ean13(pid),
                    channel_sku=f"DRYRUN{pid:07d}",
                    price=19.99,
                    inventory=5,
                )
        except Exception as e:
            entry.update({"wpt": None, "error": f"{type(e).__name__}: {e}"})
            results.append(entry)
            continue
        entry.update(
            {
                "wpt": built["wpt"],
                "spec_version": built["spec_version"],
                "validation": built["validation"],
                "coerce_notes": built.get("coerce_notes", []),
                "item": built["item"],
            }
        )
        if built["validation"]["ok"]:
            seen_wpt.add(built["wpt"])
        items.append(built["item"])
        results.append(entry)

    async with system_tx(sessions) as s:
        header = await spec_builder.feed_header(s, "build")
    feed, _fixes = sanitize_feed_numbers({"MPItemFeedHeader": header, "MPItem": items})

    n_ok = sum(1 for r in results if r.get("validation", {}).get("ok"))
    n_err = sum(len(r.get("validation", {}).get("errors", [])) for r in results)
    summary = {
        "products": len(results),
        "validation_ok": n_ok,
        "validation_errors_total": n_err,
        "distinct_wpt_ok": sorted(seen_wpt),
        "distinct_wpt_count": len(seen_wpt),
        "pass": n_ok == len(results) and len(results) > 0 and len(seen_wpt) >= MIN_DISTINCT_WPT,
        "criteria": f"全部 validation.ok 且 distinct WPT ≥ {MIN_DISTINCT_WPT}（验收①）",
    }
    return {"summary": summary, "feed": feed, "results": results}


def main() -> int:
    p = argparse.ArgumentParser(description="R2-03 验收①：dry-run 产物官方 spec 校验")
    p.add_argument("--product-id", type=int, action="append", default=[], help="产品 ID（可多个）")
    p.add_argument("--auto", type=int, default=0, help="自动选 N 个审核通过产品（覆盖不同 WPT）")
    p.add_argument("--fill", action="store_true", help="构建前先跑 AI 属性填写（需 LLM 凭证）")
    p.add_argument("--out", required=True, help="报告输出路径（json）")
    args = p.parse_args()
    ids: list[int] = list(args.product_id)
    if args.auto:
        ids.extend(asyncio.run(_auto_pick(args.auto)))
    ids = list(dict.fromkeys(ids))
    if not ids:
        print("--product-id 与 --auto 至少给一个", file=sys.stderr)
        return 2
    report = asyncio.run(run_dryrun(ids, fill=args.fill, limit=args.auto or None))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    s = report["summary"]
    print(
        f"dry-run 报告 → {out}\n"
        f"产品 {s['products']} / 校验通过 {s['validation_ok']}"
        f" / errors {s['validation_errors_total']}"
        f" / distinct WPT {s['distinct_wpt_count']} ({', '.join(s['distinct_wpt_ok'][:8])})\n"
        f"验收①判定：{'PASS ✅' if s['pass'] else 'FAIL ❌'}（{s['criteria']}）"
    )
    return 0 if s["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
