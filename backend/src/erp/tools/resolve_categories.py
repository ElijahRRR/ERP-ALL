"""L1-b 类目复排 CLI（module=category_map）：把无直判命中的 Amazon 类目用 LLM 复排
写回 refdata.category_map，之后审核 L1-a 直判即覆盖（0 LLM）。

用法：
  # 积压模式：扫 product 里无直判命中的类目，复排前 N 个
  docker compose -f infra/docker-compose.yml exec api \\
    python -m erp.tools.resolve_categories --backlog --limit 500

  # 单类目：复排指定 Amazon 类目路径
  python -m erp.tools.resolve_categories --category "Home & Kitchen/Kitchen/Drinkware/Mugs"

R2-02 对拍前置：先对样本商品类目跑本 CLI 填 map，再跑审核对拍（届时走 L1-a 直判）。
"""

import argparse
import asyncio
import sys

from sqlalchemy import text

from erp.audit import l1_rerank
from erp.core.db import get_session_factory, system_tx

# 无直判命中的 product 类目（category_path）——与 L1-a 直判同口径（INNER JOIN pt_meta，
# 排除 unmapped 标记）。跨团队全局（类目与团队无关），走 system_tx。
_BACKLOG_SQL = text(
    "SELECT DISTINCT p.category_path FROM app.product p"
    " WHERE p.category_path IS NOT NULL AND p.category_path <> ''"
    "   AND NOT EXISTS ("
    "     SELECT 1 FROM refdata.category_map cm"
    "     JOIN refdata.pt_meta pm ON pm.walmart_product_type = cm.walmart_product_type"
    "     WHERE cm.amazon_category = p.category_path"
    "       AND cm.walmart_product_type <> '无对应Walmart PT')"
    " LIMIT :lim"
)


async def _backlog_categories(limit: int) -> list[str]:
    sessions = get_session_factory()
    async with system_tx(sessions) as s:
        rows = (await s.execute(_BACKLOG_SQL, {"lim": limit})).scalars().all()
    return [r for r in rows if r]


async def _run(categories: list[str]) -> dict[str, int]:
    sessions = get_session_factory()
    tally = {"resolved": 0, "no_candidates": 0, "rejected": 0, "unavailable": 0}
    for cat in categories:
        out = await l1_rerank.resolve_category(sessions, cat)
        if out["resolved"]:
            tally["resolved"] += 1
            print(f"[✓] {cat} → {out['wpt']}")
        elif out.get("reason") == "no_candidates":
            tally["no_candidates"] += 1
            print(f"[无候选] {cat}")
        elif out.get("reason") == "llm_unavailable":
            tally["unavailable"] += 1
            print(f"[LLM失败] {cat}")
        else:
            tally["rejected"] += 1  # 复排非法/候选外 → fail-closed 未写回
            print(f"[复排未采纳] {cat}")
    return tally


def main() -> int:
    p = argparse.ArgumentParser(description="L1-b 类目复排（写回 refdata.category_map）")
    p.add_argument("--backlog", action="store_true", help="扫 product 无直判命中的类目")
    p.add_argument("--limit", type=int, default=500, help="积压模式上限（默认 500）")
    p.add_argument("--category", help="复排单个 Amazon 类目路径")
    args = p.parse_args()

    if args.category:
        categories = [args.category]
    elif args.backlog:
        categories = asyncio.run(_backlog_categories(args.limit))
    else:
        print("需指定 --backlog 或 --category", file=sys.stderr)
        return 2

    if not categories:
        print("无待复排类目")
        return 0
    tally = asyncio.run(_run(categories))
    print(
        f"完成：复排写回 {tally['resolved']} / 无候选 {tally['no_candidates']}"
        f" / 未采纳 {tally['rejected']} / LLM失败 {tally['unavailable']}（共 {len(categories)}）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
