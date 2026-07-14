"""R2-02 对拍 harness：旧系统判定 groundtruth → 新 ERP 流水线重跑 → 一致率 + 分歧拆解。

这是 R2-02 的验收工具（验收判据：旧系统 ≥100 ASIN 重跑 verdict 一致率 ≥90%）。
建完即最后一步开发；拿它在部署机上跑旧系统 groundtruth 就是 R2-02 验收。

用法：
  # 先填类目图（无直判命中的样本类目走 L1-b 复排），再对拍
  docker compose -f infra/docker-compose.yml exec api \\
    python -m erp.tools.audit_replay \\
      --file /data/groundtruth.jsonl --resolve-categories --out /data/diff.jsonl

groundtruth.jsonl 每行一个旧系统判过的商品（部署 AI 从旧 walmart_audit 导出）：
  {"asin": "...", "title": "...", "brand": "...", "category_path": "...",
   "amazon_leaf_id": "...", "seller_id": "...", "description": "...",
   "bullets": ["..."], "old_verdict": "pass|reject|needs_review"}
title 缺省用 asin；old_verdict 旧标签自动归一（approved→pass、blocked→reject…）。

输出：总数 / 一致数 / 一致率 / 混淆矩阵（old→new）/ 分歧样本（含新 reject_level，
供判断 L2 R1/R2/R3 是否需要——旧拒新过且集中某类目=需补类目硬规则）。
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.audit import l1_rerank, service
from erp.core.db import get_session_factory, system_tx

_ACCEPT_RATE = 0.9  # R2-02 验收闸门：一致率 ≥90%
_PASS = {"pass", "passed", "approved", "approve", "ok", "allow", "allowed"}
_REJECT = {"reject", "rejected", "blocked", "block", "fail", "failed", "denied", "deny"}
_REVIEW = {"needs_review", "review", "manual", "pending", "hold"}


def _norm_verdict(v: Any) -> str:
    t = str(v or "").strip().lower()
    if t in _PASS:
        return "pass"
    if t in _REJECT:
        return "reject"
    if t in _REVIEW:
        return "needs_review"
    return t or "(空)"


async def _ensure_team(session: AsyncSession, name: str) -> int:
    row = (
        await session.execute(
            text(
                "INSERT INTO app.team (name) VALUES (:n)"
                " ON CONFLICT (name) DO UPDATE SET name = excluded.name RETURNING id"
            ),
            {"n": name},
        )
    ).one()
    return int(row.id)


async def _upsert_product(session: AsyncSession, team_id: int, row: dict[str, Any]) -> int:
    attrs: dict[str, Any] = {}
    for k in ("seller_id", "description", "bullets"):
        if row.get(k):
            attrs[k] = row[k]
    r = (
        await session.execute(
            text(
                "INSERT INTO app.product"
                " (team_id, source_channel, source_ref, title, brand, category_path,"
                "  amazon_leaf_id, attrs)"
                " VALUES (:t,'amazon',:ref,:title,:brand,:cat,:leaf, cast(:attrs AS jsonb))"
                " ON CONFLICT (team_id, source_channel, source_ref) DO UPDATE SET"
                "  title = excluded.title, brand = excluded.brand,"
                "  category_path = excluded.category_path,"
                "  amazon_leaf_id = excluded.amazon_leaf_id,"
                "  attrs = excluded.attrs, status = 'ingested'"
                " RETURNING id"
            ),
            {
                "t": team_id,
                "ref": row["asin"],
                "title": row.get("title") or row["asin"],
                "brand": row.get("brand"),
                "cat": row.get("category_path"),
                "leaf": row.get("amazon_leaf_id"),
                "attrs": json.dumps(attrs, ensure_ascii=False),
            },
        )
    ).one()
    return int(r.id)


async def replay(
    sessions: async_sessionmaker[AsyncSession],
    *,
    team_name: str,
    rows: list[dict[str, Any]],
    levels: list[str],
    resolve_categories: bool = False,
) -> dict[str, Any]:
    """重跑并对拍。→ {total, agree, rate, confusion, disagreements}。"""
    async with system_tx(sessions) as s:
        team_id = await _ensure_team(s, team_name)

    if resolve_categories:
        cats = {str(r["category_path"]) for r in rows if r.get("category_path")}
        for c in cats:
            await l1_rerank.resolve_category(sessions, c)

    pairs: list[tuple[str, str, str | None, str]] = []
    for row in rows:
        async with system_tx(sessions) as s:
            pid = await _upsert_product(s, team_id, row)
        try:
            out = await service.audit_one(
                sessions,
                product_id=pid,
                team_id=team_id,
                is_super=False,
                levels=levels,
                trigger_kind="batch",
            )
            new_v, rl = out["verdict"], out["reject_level"]
        except Exception as e:
            new_v, rl = "error", str(e)[:120]
        pairs.append((_norm_verdict(row.get("old_verdict")), new_v, rl, str(row.get("asin"))))

    total = len(pairs)
    agree = sum(1 for o, n, _, _ in pairs if o == n)
    confusion: dict[str, int] = {}
    for o, n, _, _ in pairs:
        confusion[f"{o}->{n}"] = confusion.get(f"{o}->{n}", 0) + 1
    disagreements = [
        {"asin": a, "old": o, "new": n, "reject_level": rl}
        for o, n, rl, a in pairs
        if o != n
    ]
    return {
        "total": total,
        "agree": agree,
        "rate": round(agree / total, 4) if total else 0.0,
        "confusion": dict(sorted(confusion.items())),
        "disagreements": disagreements,
    }


def _read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def _run(
    path: Path, *, team: str, levels: list[str], resolve: bool, out: Path | None
) -> None:
    rows = _read_rows(path)
    if not rows:
        print("groundtruth 无数据行")
        return
    report = await replay(
        get_session_factory(),
        team_name=team,
        rows=rows,
        levels=levels,
        resolve_categories=resolve,
    )
    print(f"\n=== R2-02 对拍结果（levels={','.join(levels)}）===")
    print(f"总数 {report['total']} / 一致 {report['agree']} / 一致率 {report['rate']:.1%}")
    print("混淆矩阵 old→new:")
    for k, v in report["confusion"].items():
        print(f"  {k}: {v}")
    gate = "✅ 达标(≥90%)" if report["rate"] >= _ACCEPT_RATE else "❌ 未达标(<90%)"
    print(f"验收闸门：{gate}")
    if out is not None:
        out.write_text(
            "\n".join(json.dumps(d, ensure_ascii=False) for d in report["disagreements"])
        )
        n = len(report["disagreements"])
        print(f"分歧清单已写 {out}（{n} 条），供判 L2 类目硬规则是否需补")


def main() -> int:
    p = argparse.ArgumentParser(description="R2-02 对拍 harness（旧系统判定 vs 新 ERP 流水线）")
    p.add_argument("--file", required=True, help="groundtruth jsonl")
    p.add_argument("--team", default="R2-02对拍", help="对拍专用团队名")
    p.add_argument("--levels", default="l0,l1,l2,l3", help="审核级别，逗号分隔")
    p.add_argument("--resolve-categories", action="store_true", help="对拍前先 L1-b 填图")
    p.add_argument("--out", help="分歧清单输出 jsonl")
    args = p.parse_args()

    path = Path(args.file)
    if not path.is_file():
        print(f"文件不存在：{path}", file=sys.stderr)
        return 2
    levels = [x.strip() for x in args.levels.split(",") if x.strip()]
    asyncio.run(
        _run(
            path,
            team=args.team,
            levels=levels,
            resolve=args.resolve_categories,
            out=Path(args.out) if args.out else None,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
