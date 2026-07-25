"""USPTO 商标批量导入（RS-04A：流式读取 → COPY staging → set-based merge）。

旧通道（import_trademark.py，逐行 SQL + 全量进内存 + 单大事务）适用万级；14M 行用本工具：
  - **流式**读 csv/jsonl，内存占用与文件大小无关（单批 buffer）；
  - 每批 COPY 进 UNLOGGED staging（免 WAL），再一条 set-based upsert merge 进
    refdata.trademark，**按批提交**——不再有单大事务/WAL 洪峰；
  - **manifest 断点续跑**：`<file>.manifest.json` 记录 sha256 + 每批状态，中断后
    `--resume` 跳过已完成批次继续（源文件变更 sha256 不符则拒绝续跑）；
  - 错误行落 `<file>.errors.jsonl`（行号+原因），不阻断整体；
  - 归一化契约与旧通道逐字一致（mark_norm=_norm 派生，喂 L2-R5 反查；is_live
    显式列优先，否则查 tm_status_code 字典——bulk 预载成内存 dict）；
  - merge 触发 0014 统计触发器 → dataset_revision('trademark') 事务内递增。

用法（api 容器无 volume 挂载，先 `docker compose cp <文件> api:/tmp/` 再 exec；
日常链路见 infra/local-deploy/README.md「USPTO 商标供给链」）：
  python -m erp.tools.bulk_import_trademark --file /tmp/trademarks.csv
  python -m erp.tools.bulk_import_trademark --file /tmp/trademarks.csv --resume
"""

import argparse
import csv
import hashlib
import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg

from erp.audit.pipeline import _norm
from erp.compliance.import_service import (
    _TM_FILED_KEYS,
    _TM_MARK_KEYS,
    _TM_NORM_KEYS,
    _TM_OWNER_KEYS,
    _TM_REGISTERED_KEYS,
    _TM_SERIAL_KEYS,
    _TM_STATUS_KEYS,
    _first,
    _parse_is_live,
    _parse_nice_classes,
)
from erp.core.settings import get_settings

DEFAULT_BATCH = 50_000

_STAGING_COLS = (
    "batch_no", "serial_no", "mark_text", "mark_norm", "status_code",
    "is_live", "nice_classes", "owner_name", "filed_date", "registered_date",
)  # fmt: skip

_MERGE_SQL = """
INSERT INTO refdata.trademark
  (serial_no, mark_text, mark_norm, status_code, is_live, nice_classes,
   owner_name, filed_date, registered_date, updated_at)
SELECT DISTINCT ON (serial_no) serial_no, mark_text, mark_norm, status_code, is_live,
   nice_classes, owner_name, filed_date, registered_date, now()
FROM refdata.trademark_staging WHERE batch_no = %s
ORDER BY serial_no, ctid DESC
ON CONFLICT (serial_no) DO UPDATE SET
  mark_text = EXCLUDED.mark_text, mark_norm = EXCLUDED.mark_norm,
  status_code = EXCLUDED.status_code, is_live = EXCLUDED.is_live,
  nice_classes = EXCLUDED.nice_classes, owner_name = EXCLUDED.owner_name,
  filed_date = EXCLUDED.filed_date, registered_date = EXCLUDED.registered_date,
  updated_at = now()
"""


def _dsn() -> str:
    return get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _stream_rows(path: Path) -> Iterator[dict[str, Any]]:
    """流式产出原始行 dict（csv/jsonl；xlsx 不支持——14M 不该用 xlsx，转 csv）。"""
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)
    elif suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as f:
            yield from csv.DictReader(f)
    else:
        raise ValueError(f"bulk 通道仅支持 csv/jsonl（收到 {suffix}）；xlsx 请先转 csv")


def _normalize_row(
    row: dict[str, Any], status_map: dict[str, bool | None]
) -> tuple[Any, ...] | str:
    """原始行 → staging 元组（batch_no 之外的 9 列）；不合格返回错误原因串。

    契约与 import_service._apply_trademark_row 逐字一致（R5 归一化对齐）。
    """
    serial = _first(row, _TM_SERIAL_KEYS)
    mark_text = _first(row, _TM_MARK_KEYS)
    if not serial or not mark_text:
        return "serial_no 或 mark_text 缺失"
    provided_norm = _first(row, _TM_NORM_KEYS)
    mark_norm = _norm(provided_norm) if provided_norm else _norm(mark_text)
    status_code = _first(row, _TM_STATUS_KEYS)
    is_live = _parse_is_live(row)
    if is_live is None and status_code:
        is_live = status_map.get(status_code)
    return (
        serial,
        mark_text,
        mark_norm,
        status_code,
        is_live,
        _parse_nice_classes(row),
        _first(row, _TM_OWNER_KEYS),
        _first(row, _TM_FILED_KEYS) or None,
        _first(row, _TM_REGISTERED_KEYS) or None,
    )


class _Manifest:
    """断点续跑账本（sidecar json）：sha256 锁源文件，逐批记录 staged/merged。"""

    def __init__(self, path: Path, file_sha: str, batch_size: int) -> None:
        self.path = path
        self.data: dict[str, Any] = {
            "sha256": file_sha,
            "batch_size": batch_size,
            "job_id": None,
            "batches": {},
            "finished": False,
        }

    @classmethod
    def load_for_resume(cls, path: Path, file_sha: str, batch_size: int) -> "_Manifest":
        m = cls(path, file_sha, batch_size)
        if not path.is_file():
            raise ValueError(f"--resume 但 manifest 不存在：{path}")
        loaded = json.loads(path.read_text())
        if loaded.get("sha256") != file_sha:
            raise ValueError(
                "源文件 sha256 与 manifest 不符——文件已变更，不能续跑（去掉 --resume 重导）"
            )
        if loaded.get("batch_size") != batch_size:
            raise ValueError("batch_size 与 manifest 不符——批次边界会错位，用原 batch_size 续跑")
        m.data = loaded
        return m

    def batch_done(self, batch_no: int) -> bool:
        return bool(self.data["batches"].get(str(batch_no), {}).get("done"))

    def record(self, batch_no: int, **fields: Any) -> None:
        self.data["batches"].setdefault(str(batch_no), {}).update(fields)
        self.save()

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1))
        tmp.replace(self.path)


def run_bulk(  # noqa: PLR0915, PLR0912  批量导入主流程：分批状态机，拆散反而割裂
    path: Path,
    *,
    batch_size: int = DEFAULT_BATCH,
    resume: bool = False,
    fail_after_batches: int | None = None,
    dsn: str | None = None,
) -> dict[str, Any]:
    """→ 汇总 dict（ok/err/batches/duration_s/rows_per_s）。fail_after_batches=故障注入。"""
    t0 = time.monotonic()
    file_sha = _sha256(path)
    manifest_path = path.with_name(path.name + ".manifest.json")
    errors_path = path.with_name(path.name + ".errors.jsonl")
    if resume:
        manifest = _Manifest.load_for_resume(manifest_path, file_sha, batch_size)
    else:
        manifest = _Manifest(manifest_path, file_sha, batch_size)

    conn = psycopg.connect(dsn or _dsn())
    try:
        conn.execute("SELECT set_config('app.is_super', 'on', false)")
        status_map: dict[str, bool | None] = {
            r[0]: r[1]
            for r in conn.execute("SELECT code, is_live FROM refdata.tm_status_code").fetchall()
        }
        if manifest.data["job_id"] is None:
            job_row = conn.execute(
                "INSERT INTO app.import_job"
                " (domain, source_name, format, total_rows, status, started_at)"
                " VALUES ('trademark', %s, %s, 0, 'running', now()) RETURNING id",
                (path.name, path.suffix.lstrip(".")),
            ).fetchone()
            assert job_row is not None
            job_id = job_row[0]
            manifest.data["job_id"] = job_id
        else:
            job_id = manifest.data["job_id"]
            conn.execute("UPDATE app.import_job SET status = 'running' WHERE id = %s", (job_id,))
        if not resume:
            conn.execute("TRUNCATE refdata.trademark_staging")
        conn.commit()
        manifest.save()

        total_rows = 0
        total_ok = 0
        total_err = 0
        merged_batches = 0
        err_f = errors_path.open("a" if resume else "w", encoding="utf-8")
        try:
            batch: list[tuple[Any, ...]] = []
            batch_no = 0
            batch_errs: list[str] = []

            def _flush(bno: int, rows: list[tuple[Any, ...]], errs: list[str]) -> None:
                nonlocal total_ok, total_err, merged_batches
                if manifest.batch_done(bno):  # 续跑：本批已完成，跳过（行已计入 total_rows）
                    return
                with (
                    conn.cursor() as cur,
                    cur.copy(
                        f"COPY refdata.trademark_staging ({', '.join(_STAGING_COLS)}) FROM STDIN"
                    ) as copy,
                ):
                    for r in rows:
                        copy.write_row((bno, *r))
                merged = conn.execute(_MERGE_SQL, (bno,)).rowcount
                conn.execute("DELETE FROM refdata.trademark_staging WHERE batch_no = %s", (bno,))
                staged_distinct = len({r[0] for r in rows})
                if merged != staged_distinct:  # 结构性守卫：merge 行数必须等于批内去重主键数
                    conn.rollback()
                    raise RuntimeError(f"批 {bno} merge 行数不符：{merged}/{staged_distinct}")
                conn.commit()
                for e in errs:
                    err_f.write(e + "\n")
                err_f.flush()
                total_ok += merged
                total_err += len(errs)
                merged_batches += 1
                manifest.record(bno, rows=len(rows), merged=merged, err=len(errs), done=True)
                elapsed = time.monotonic() - t0
                print(
                    f"批 {bno} 完成：staged {len(rows)} / merged {merged} / err {len(errs)}"
                    f" | 累计 {total_rows} 行 {elapsed:.0f}s ({total_rows / elapsed:.0f} 行/s)"
                )
                if fail_after_batches is not None and merged_batches >= fail_after_batches:
                    raise KeyboardInterrupt(f"[故障注入] 完成 {fail_after_batches} 批后中断")

            for line_no, raw in enumerate(_stream_rows(path)):
                total_rows += 1
                if not manifest.batch_done(batch_no):
                    out = _normalize_row(raw, status_map)
                    if isinstance(out, str):
                        batch_errs.append(
                            json.dumps(
                                {"line": line_no, "reason": out, "row": raw}, ensure_ascii=False
                            )
                        )
                    else:
                        batch.append(out)
                if total_rows % batch_size == 0:
                    _flush(batch_no, batch, batch_errs)
                    batch, batch_errs = [], []
                    batch_no += 1
            if batch or batch_errs:
                _flush(batch_no, batch, batch_errs)
        finally:
            err_f.close()

        duration = time.monotonic() - t0
        # 汇总从 manifest 累计（覆盖续跑场景：前次已完成批的 merged/err 也计入）
        all_batches = manifest.data["batches"].values()
        sum_ok = sum(int(b.get("merged", 0)) for b in all_batches)
        sum_err = sum(int(b.get("err", 0)) for b in all_batches)
        summary = {
            "ok": sum_ok,
            "err": sum_err,
            "total": total_rows,
            "batches": len(manifest.data["batches"]),
            "sha256": file_sha,
            "duration_s": round(duration, 1),
            "rows_per_s": round(total_rows / duration) if duration else 0,
        }
        manifest.data["finished"] = True
        manifest.save()
        conn.execute(
            "UPDATE app.import_job SET status = 'done', total_rows = %s, ok_rows = %s,"
            " err_rows = %s, finished_at = now(), verify = %s::jsonb WHERE id = %s",
            (total_rows, sum_ok, sum_err, json.dumps(summary, ensure_ascii=False), job_id),
        )
        conn.commit()
        print(f"导入完成 job {job_id}：{summary}")
        return summary
    except (Exception, KeyboardInterrupt):
        conn.rollback()
        conn.execute(
            "UPDATE app.import_job SET status = 'failed', finished_at = now(),"
            " error_report_ref = %s WHERE id = %s AND status <> 'done'",
            (
                f"中断于 {time.strftime('%F %T')}，manifest 可 --resume 续跑",
                manifest.data["job_id"],
            ),
        )
        conn.commit()
        raise
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description="USPTO 商标批量导入（COPY staging + 断点续跑）")
    p.add_argument("--file", required=True, help="csv/jsonl 文件路径")
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH)
    p.add_argument("--resume", action="store_true", help="按 manifest 跳过已完成批次续跑")
    args = p.parse_args()
    path = Path(args.file)
    if not path.is_file():
        print(f"文件不存在：{path}", file=sys.stderr)
        return 2
    try:
        run_bulk(path, batch_size=args.batch_size, resume=args.resume)
    except KeyboardInterrupt:
        print("已中断——用 --resume 续跑", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"导入失败：{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
