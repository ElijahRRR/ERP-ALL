"""AI 属性填写 CLI（R2-03 增量 3：product.attrs['walmart_fill'][wpt]）。

用法：
  # 指定产品（可多个）
  python -m erp.tools.fill_listing_attrs --product-id 12 --product-id 34

  # 全部待上架（audit_passed/ready 且尚无该 WPT 填充）——A152 验收前置批量
  python -m erp.tools.fill_listing_attrs --pending --limit 50

走 llm_cache/usage_log 既有记账（module=listing）；同输入命中缓存零成本。
fail-closed：输出非法不写回（重跑自愈）。模型参数 system_config 'listing.attr_fill'。
"""

import argparse
import asyncio
import sys
from typing import Any

from sqlalchemy import text

from erp.core.db import get_session_factory, system_tx
from erp.listing.attr_fill import fill_product_attrs


async def _pending_ids(limit: int) -> list[int]:
    sessions = get_session_factory()
    async with system_tx(sessions) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT id FROM app.product"
                    " WHERE status IN ('audit_passed', 'ready')"
                    " ORDER BY id LIMIT :n"
                ),
                {"n": limit},
            )
        ).all()
    return [r[0] for r in rows]


async def _run(product_ids: list[int], model: str | None) -> int:
    sessions = get_session_factory()
    filled = skipped = failed = 0
    for pid in product_ids:
        result: dict[str, Any] = await fill_product_attrs(sessions, product_id=pid, model=model)
        if result.get("filled"):
            filled += 1
            hit = "（缓存）" if result.get("cache_hit") else f"（${result.get('cost_usd', 0):.6f}）"
            print(f"[{pid}] 填充完成 wpt={result.get('wpt')} {hit}")
        elif result.get("reason") in ("no_wpt", "no_pt_schema"):
            skipped += 1
            print(f"[{pid}] 跳过：{result['reason']}")
        else:
            failed += 1
            print(f"[{pid}] 失败：{result.get('reason', '输出非法(fail-closed)')}")
    print(f"完成：filled={filled} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 1


def main() -> int:
    p = argparse.ArgumentParser(description="AI 属性填写（module=listing 记账）")
    p.add_argument("--product-id", type=int, action="append", default=[], help="产品 ID（可多个）")
    p.add_argument("--pending", action="store_true", help="扫全部待上架产品")
    p.add_argument("--limit", type=int, default=50, help="--pending 上限")
    p.add_argument("--model", help="覆盖模型（默认走 system_config listing.attr_fill）")
    args = p.parse_args()
    ids: list[int] = list(args.product_id)
    if args.pending:
        ids.extend(asyncio.run(_pending_ids(args.limit)))
    if not ids:
        print("--product-id 与 --pending 至少给一个", file=sys.stderr)
        return 2
    return asyncio.run(_run(list(dict.fromkeys(ids)), args.model))


if __name__ == "__main__":
    sys.exit(main())
