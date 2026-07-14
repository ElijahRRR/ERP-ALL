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
   "bullets": ["..."], "old_verdict": "pass|reject|needs_review",
   "old_reason": "旧系统拒绝原因(可选,强烈建议带上——分歧诊断的关键)",
   "old_stage": "旧系统 stage_stopped_at(可选;含 'L4' 的行从分母剔除,D-Q58)"}
title 缺省用 asin；old_verdict 旧标签自动归一（approved→pass、blocked→reject…）。

输出：总数 / 一致数 / 一致率 / 混淆矩阵（old→new）/ 分歧样本（含新 reject_level，
供判断 L2 R1/R2/R3 是否需要——旧拒新过且集中某类目=需补类目硬规则）。
"""

import argparse
import asyncio
import json
import sys
from collections import Counter
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


_L1_INFO_KEYS = ("wpt", "access_state", "requires_certificate", "confidence", "match_type")


async def _run_hits(
    sessions: async_sessionmaker[AsyncSession], run_id: int
) -> tuple[list[str], dict[str, Any] | None]:
    """本次 run 的命中链 + L1 证据摘要（map 旗标，判 R1/R3 类目规则缺口用）。"""
    async with system_tx(sessions) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT level, rule_code, evidence FROM app.audit_hit"
                    " WHERE run_id = :r ORDER BY id"
                ),
                {"r": run_id},
            )
        ).all()
    codes = [f"{r.level}:{r.rule_code}" for r in rows]
    l1_info: dict[str, Any] | None = None
    for r in rows:
        if r.level == "l1" and isinstance(r.evidence, dict):
            l1_info = {k: r.evidence[k] for k in _L1_INFO_KEYS if k in r.evidence}
    return codes, l1_info


async def replay(
    sessions: async_sessionmaker[AsyncSession],
    *,
    team_name: str,
    rows: list[dict[str, Any]],
    levels: list[str],
    resolve_categories: bool = False,
) -> dict[str, Any]:
    """重跑并对拍。→ {total, agree, rate, confusion, unmapped, disagreements}。

    disagreements 每条带 category / hits / old_reason——分歧可按类目聚类、按命中层
    定位（判 L2 类目硬规则是否需补的实测证据）。unmapped=命中 l1_unmapped 的产品数
    （类目缺图规模指标；高=旧库 category_path 与 map 格式不匹配的信号）。

    L4 剔除（D-Q58）：旧系统 L4 视觉默认开、本系统现阶段无视觉模型不接入——
    groundtruth 行带 old_stage/stage 含 'l4' 的（旧系统看图拒的）纯文本管道结构性
    对不上，从对拍分母剔除，单独计 excluded_l4。
    """
    l4_rows = [
        r
        for r in rows
        if "l4" in str(r.get("old_stage") or r.get("stage") or "").strip().lower()
    ]
    if l4_rows:
        excluded = {id(r) for r in l4_rows}
        rows = [r for r in rows if id(r) not in excluded]

    async with system_tx(sessions) as s:
        team_id = await _ensure_team(s, team_name)

    if resolve_categories:
        cats = {str(r["category_path"]) for r in rows if r.get("category_path")}
        for c in cats:
            await l1_rerank.resolve_category(sessions, c)

    recs: list[dict[str, Any]] = []
    for row in rows:
        async with system_tx(sessions) as s:
            pid = await _upsert_product(s, team_id, row)
        hits: list[str] = []
        l1_info: dict[str, Any] | None = None
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
            hits, l1_info = await _run_hits(sessions, out["run_id"])
        except Exception as e:  # 单品失败不终止整批对拍
            new_v, rl = "error", str(e)[:120]
        recs.append(
            {
                "asin": str(row.get("asin")),
                "old": _norm_verdict(row.get("old_verdict")),
                "new": new_v,
                "reject_level": rl,
                "category": row.get("category_path"),
                "hits": hits,
                "l1": l1_info,
                "old_reason": row.get("old_reason") or row.get("reason"),
            }
        )

    total = len(recs)
    agree = sum(1 for r in recs if r["old"] == r["new"])
    confusion: dict[str, int] = {}
    for r in recs:
        k = f"{r['old']}->{r['new']}"
        confusion[k] = confusion.get(k, 0) + 1
    unmapped = sum(1 for r in recs if any(h.endswith(":l1_unmapped") for h in r["hits"]))
    return {
        "total": total,
        "agree": agree,
        "rate": round(agree / total, 4) if total else 0.0,
        "confusion": dict(sorted(confusion.items())),
        "unmapped": unmapped,
        "excluded_l4": len(l4_rows),
        "disagreements": [r for r in recs if r["old"] != r["new"]],
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
    if report["excluded_l4"]:
        print(f"已剔除旧系统 L4(视觉)拒样本：{report['excluded_l4']}（现阶段无视觉模型，D-Q58）")
    print(f"类目缺图产品数(l1_unmapped)：{report['unmapped']}（高=旧库路径与 map 格式不匹配信号）")
    print("混淆矩阵 old→new:")
    for k, v in report["confusion"].items():
        print(f"  {k}: {v}")
    gate = "✅ 达标(≥90%)" if report["rate"] >= _ACCEPT_RATE else "❌ 未达标(<90%)"
    print(f"验收闸门：{gate}")
    for bucket in ("reject->pass", "pass->reject"):
        rows = [d for d in report["disagreements"] if f"{d['old']}->{d['new']}" == bucket]
        if not rows:
            continue
        reasons = Counter(str(d.get("old_reason") or "(无)") for d in rows)
        print(f"分歧 {bucket} 旧因 Top10（规则缺口的直接证据）:")
        for c, n in reasons.most_common(10):
            print(f"  {n}× {c}")
        cats = Counter(str(d.get("category") or "(无类目)") for d in rows)
        print(f"分歧 {bucket} 类目 Top5:")
        for c, n in cats.most_common(5):
            print(f"  {n}× {c}")
    if out is not None:
        out.write_text(
            "\n".join(json.dumps(d, ensure_ascii=False) for d in report["disagreements"])
        )
        n = len(report["disagreements"])
        print(f"分歧清单已写 {out}（{n} 条，含 category/hits/old_reason），供定位规则缺口")


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
