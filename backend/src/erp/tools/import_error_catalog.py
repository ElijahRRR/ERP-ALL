"""listing 错误码字典导入 CLI（R2-03 增量 5：app.listing_error_catalog 灌入）。

用法：
  # 默认灌随包实战种子（65 码：auto_listing reconcile 实战数字码 + erp-core
  # error_classifier 符号码；新旧处置映射见种子文件生成注释）
  python -m erp.tools.import_error_catalog

  # 或指定文件（运营后续增补）
  python -m erp.tools.import_error_catalog --file /data/error_codes.jsonl

列：error_code（必填）· category · title · disposition（六值：auto_retry/backoff_retry/
rebuild_spec/skip/manual/fatal，缺省 manual）· max_retries（缺省 3）· notes · enabled（缺省 true）

全局字典（无团队属性，D-Q11 运营可维护），走超管 system_tx。幂等：以 error_code 为键
ON CONFLICT 恒更新——可覆盖运行时自动插入的草稿行（_ensure_error_cataloged）。
"""

import argparse
import asyncio
import csv
import json
import sys
from importlib import resources
from pathlib import Path
from typing import Any

from erp.compliance import import_service
from erp.core.db import get_session_factory, system_tx


def _read_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as f:
            return [dict(r) for r in csv.DictReader(f)]
    raise ValueError(f"不支持的文件格式：{suffix}（用 csv/jsonl）")


def _seed_path() -> Path:
    return Path(str(resources.files("erp.listing") / "data" / "listing_error_catalog_seed.jsonl"))


async def _run(path: Path) -> dict[str, Any]:
    rows = _read_rows(path)
    if not rows:
        print("文件无数据行")
        return {"ok": 0, "skip": 0, "err": 0}
    sessions = get_session_factory()
    async with system_tx(sessions) as s:
        job = await import_service.create_job(
            s,
            domain=import_service.LISTING_ERROR_CATALOG_DOMAIN,
            source_name=path.name,
            total_rows=len(rows),
            fmt="jsonl" if path.suffix.lower() == ".jsonl" else "csv",
        )
    job_id = job["id"]
    try:
        async with system_tx(sessions) as s:
            summary = await import_service.import_rows(s, job_id=job_id, rows=rows)
    except Exception as e:
        async with system_tx(sessions) as s:
            await import_service.mark_failed(s, job_id=job_id, error=str(e))
        print(f"导入失败（job {job_id}）：{e}")
        raise
    print(
        f"导入完成 job {job_id} [listing_error_catalog]："
        f"新增/更新 {summary['ok']} / 跳过 {summary['skip']} / 错误 {summary['err']}"
        f"（共 {summary['total']} 行）"
    )
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description="listing 错误码字典导入（全局，D-Q11 运营可维护）")
    p.add_argument("--file", help="本地文件（csv/jsonl）；缺省=随包实战种子 65 码")
    args = p.parse_args()
    path = Path(args.file) if args.file else _seed_path()
    if not path.is_file():
        print(f"文件不存在：{path}", file=sys.stderr)
        return 2
    try:
        asyncio.run(_run(path))
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
