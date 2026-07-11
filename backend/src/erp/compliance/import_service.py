"""标准导入作业服务（specs/001 §import_job / D-Q35）。

契约：dry-run 校验 → 幂等 upsert（各表唯一键）→ 逐块行数核对 → 汇总报告；
**任何一块行数不符即 failed 并回滚该块**（lark 截断教训：逐块核对）。

本单实现黑名单四域（brand/seller/asin/category）。归一化与审核 L0 查表严格一致
（全部走 audit.pipeline._norm），否则导入的词审核时查不到——这是唯一必须锁死的不变量。
其余域（trademark/policy/category_map/gtin…）表结构与 job 通道已就位，导入器随各自工单补。
"""

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.audit.pipeline import NON_BRAND_PLACEHOLDERS, _norm
from erp.core.errors import BusinessError


@dataclass(frozen=True)
class _Domain:
    table: str
    subject_col: str
    display_col: str | None
    subject_keys: tuple[str, ...]  # 行中主体字段名（按序取第一个非空）
    display_keys: tuple[str, ...] = ()
    skip_placeholder: bool = False  # 仅品牌：占位符（unbranded/generic…）不入黑名单


# 归一化统一走 _norm（lowercase + 多空格压一），与 L0 查表一致
_DOMAINS: dict[str, _Domain] = {
    "blacklist_brand": _Domain(
        "blacklist_brand",
        "brand_norm",
        "brand_display",
        subject_keys=("brand", "brand_norm", "brand_display"),
        display_keys=("brand_display", "brand"),
        skip_placeholder=True,
    ),
    "blacklist_seller": _Domain(
        "blacklist_seller",
        "seller_ref",
        "seller_name",
        subject_keys=("seller_id", "seller_ref"),
        display_keys=("seller_name",),
    ),
    "blacklist_asin": _Domain(
        "blacklist_asin",
        "asin",
        None,
        subject_keys=("asin",),
    ),
    "blacklist_category": _Domain(
        "blacklist_category",
        "category_ref",
        None,
        subject_keys=("category", "category_ref"),
    ),
}

SUPPORTED_DOMAINS = tuple(_DOMAINS)


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v)
    return None


async def create_job(
    session: AsyncSession,
    *,
    domain: str,
    source_name: str,
    total_rows: int,
    fmt: str = "csv",
    source_kind: str = "file",
    chunk_size: int = 5000,
    team_id: int | None = None,
    created_by: int | None = None,
) -> dict[str, Any]:
    """建导入作业（pending）。domain 必须受支持；total_rows = 调用方声明的源行数（供核对）。"""
    if domain not in _DOMAINS:
        raise BusinessError(
            "IMPORT_DOMAIN_UNSUPPORTED",
            f"域 {domain} 导入器尚未实现",
            {"supported": list(SUPPORTED_DOMAINS)},
        )
    row = (
        await session.execute(
            text(
                "INSERT INTO app.import_job"
                " (team_id, domain, source_kind, source_name, format, total_rows,"
                "  chunk_size, created_by)"
                " VALUES (:t, :d, :sk, :sn, :f, :n, :cs, :u)"
                " RETURNING id, status, created_at"
            ),
            {
                "t": team_id,
                "d": domain,
                "sk": source_kind,
                "sn": source_name,
                "f": fmt,
                "n": total_rows,
                "cs": chunk_size,
                "u": created_by,
            },
        )
    ).one()
    return {"id": row.id, "status": row.status, "domain": domain, "total_rows": total_rows}


@dataclass
class _Counters:
    ok: int = 0
    skip: int = 0
    err: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


async def import_rows(
    session: AsyncSession, *, job_id: int, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """把 rows 幂等灌入目标表，逐块核对行数，更新 job 计数与状态。

    返回 {status, total, ok, skip, err, verify}。行数与声明 total_rows 不符 → 抛
    IMPORT_ROW_COUNT_MISMATCH（调用方在独立事务标 failed）。
    """
    job = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, domain, chunk_size, total_rows, status"
                    " FROM app.import_job WHERE id = :j FOR UPDATE"
                ),
                {"j": job_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if job is None:
        raise BusinessError("IMPORT_JOB_NOT_FOUND", "导入作业不存在")
    if job["status"] not in ("pending", "validating"):
        raise BusinessError("IMPORT_JOB_STATE_INVALID", f"作业状态 {job['status']} 不可导入")
    dom = _DOMAINS[job["domain"]]
    team_id = job["team_id"]
    chunk_size = job["chunk_size"] or 5000

    # 源截断守卫：声明总数 > 0 时，实际收到行数必须相符（lark 截断教训）
    if job["total_rows"] and len(rows) != job["total_rows"]:
        await session.execute(
            text("UPDATE app.import_job SET status = 'failed', finished_at = now() WHERE id = :j"),
            {"j": job_id},
        )
        raise BusinessError(
            "IMPORT_ROW_COUNT_MISMATCH",
            f"源行数不符：声明 {job['total_rows']}，实到 {len(rows)}",
            {"declared": job["total_rows"], "received": len(rows)},
        )

    await session.execute(
        text("UPDATE app.import_job SET status = 'running', started_at = now() WHERE id = :j"),
        {"j": job_id},
    )

    total = _Counters()
    verify: list[dict[str, int]] = []
    for ci in range(0, len(rows), chunk_size):
        chunk = rows[ci : ci + chunk_size]
        c = _Counters()
        for idx, row in enumerate(chunk):
            await _apply_row(session, dom, team_id, row, ci + idx, c)
        # 逐块核对：处理数必须等于块行数（每行都被分类为 ok/skip/err）
        loaded = c.ok + c.skip + c.err
        verify.append({"chunk": ci // chunk_size, "expected": len(chunk), "loaded": loaded})
        if loaded != len(chunk):  # 结构性守卫：理论不触发，触发即代码缺陷
            await session.execute(
                text(
                    "UPDATE app.import_job SET status = 'failed', finished_at = now() WHERE id = :j"
                ),
                {"j": job_id},
            )
            raise BusinessError(
                "IMPORT_CHUNK_MISMATCH",
                f"块 {ci // chunk_size} 行数不符：{loaded}/{len(chunk)}",
            )
        total.ok += c.ok
        total.skip += c.skip
        total.err += c.err
        total.errors.extend(c.errors)

    await session.execute(
        text(
            "UPDATE app.import_job SET status = 'done', finished_at = now(),"
            " ok_rows = :ok, skip_rows = :sk, err_rows = :er,"
            " verify = cast(:v AS jsonb) WHERE id = :j"
        ),
        {
            "ok": total.ok,
            "sk": total.skip,
            "er": total.err,
            "v": _json({"chunks": verify, "sample_errors": total.errors[:50]}),
            "j": job_id,
        },
    )
    return {
        "status": "done",
        "total": len(rows),
        "ok": total.ok,
        "skip": total.skip,
        "err": total.err,
        "verify": verify,
    }


async def _apply_row(
    session: AsyncSession,
    dom: _Domain,
    team_id: int | None,
    row: dict[str, Any],
    line: int,
    c: _Counters,
) -> None:
    raw = _first(row, dom.subject_keys)
    subject = _norm(raw) if raw else ""
    if not subject:
        c.err += 1
        c.errors.append({"line": line, "reason": "主体字段为空", "row": row})
        return
    if dom.skip_placeholder and subject in NON_BRAND_PLACEHOLDERS:
        c.skip += 1  # 占位符品牌（unbranded/generic…）不入黑名单，否则误挡全站
        return
    display = _first(row, dom.display_keys) or (raw if dom.display_col else None)
    reason = row.get("reason")

    cols = ["team_id", dom.subject_col, "reason", "source"]
    vals = [":t", ":subj", ":reason", "'import'"]
    params: dict[str, Any] = {"t": team_id, "subj": subject, "reason": reason}
    if dom.display_col:
        cols.insert(2, dom.display_col)
        vals.insert(2, ":disp")
        params["disp"] = display
    inserted = (
        await session.execute(
            text(
                f"INSERT INTO app.{dom.table} ({', '.join(cols)})"
                f" VALUES ({', '.join(vals)})"
                f" ON CONFLICT (COALESCE(team_id, 0), {dom.subject_col})"
                "  WHERE status = 'active' DO NOTHING"
                " RETURNING id"
            ),
            params,
        )
    ).scalar_one_or_none()
    if inserted is not None:
        c.ok += 1
    else:
        c.skip += 1  # 幂等：已在黑名单 → 跳过


async def mark_failed(session: AsyncSession, *, job_id: int, error: str) -> None:
    await session.execute(
        text(
            "UPDATE app.import_job SET status = 'failed', finished_at = now(),"
            " error_report_ref = :e WHERE id = :j AND status NOT IN ('done','failed')"
        ),
        {"e": error[:500], "j": job_id},
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
