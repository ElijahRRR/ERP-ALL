"""RS-04A 验收：商标 bulk 通道（COPY staging + set-based merge + manifest 断点续跑）。

小样本走全流程：基本导入+归一化 R5 对齐 / 幂等重导 / 批内重复 serial 后者胜 /
故障注入中断 → --resume 续跑补齐 / dataset_revision 触发器 bump（任意写入方失效缓存）。
100 万行性能演练不进 CI（.agent/evidence/RS-04A/ 记录）。
"""

import csv
import json
from pathlib import Path

import psycopg
import pytest

from erp.tools.bulk_import_trademark import run_bulk

from .conftest import APP_URL, MIGRATOR_URL, _pg_dsn

PREFIX = "ZBULK"  # 测试专用 serial 前缀，便于清理


@pytest.fixture(autouse=True)
def _clean(migrated_db: str):  # type: ignore[no-untyped-def]
    def _wipe() -> None:
        with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
            conn.execute("DELETE FROM refdata.trademark WHERE serial_no LIKE %s", (f"{PREFIX}%",))
            conn.execute("TRUNCATE refdata.trademark_staging")
            conn.execute(
                "DELETE FROM app.import_job WHERE source_name LIKE %s", (f"{PREFIX.lower()}%",)
            )

    _wipe()
    yield
    _wipe()


def _write_csv(path: Path, n: int, *, bad_every: int | None = None) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["serial_no", "mark_text", "status_code", "live", "nice_classes", "owner"])
        for i in range(n):
            if bad_every and i % bad_every == 0:
                w.writerow(["", f"NoSerial {i}", "", "", "", ""])  # err 行：缺 serial
            else:
                w.writerow([f"{PREFIX}{i:07d}", f"Mark  Brand {i}", "700", "LIVE", "9,25", "Acme"])


def _tm_count(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT count(*) FROM refdata.trademark WHERE serial_no LIKE %s", (f"{PREFIX}%",)
        ).fetchone()[0]


def _revision(dsn: str, dataset: str) -> int:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT revision FROM refdata.dataset_revision WHERE dataset = %s", (dataset,)
        ).fetchone()
        return row[0] if row else 0


def test_bulk_basic_and_norm_parity(migrated_db: str, tmp_path: Path) -> None:
    """基本导入 + err 行落 sidecar + mark_norm 与 R5 契约一致（小写压空格）。"""
    f = tmp_path / f"{PREFIX.lower()}-basic.csv"
    _write_csv(f, 100, bad_every=10)  # 10 行坏（i=0,10,...,90）
    dsn = _pg_dsn(APP_URL)
    s = run_bulk(f, batch_size=30, dsn=dsn)
    assert s["ok"] == 90
    assert s["err"] == 10
    assert s["total"] == 100
    assert _tm_count(migrated_db) == 90
    # 错误行 sidecar
    errors = [json.loads(x) for x in (tmp_path / f"{f.name}.errors.jsonl").read_text().splitlines()]
    assert len(errors) == 10
    assert all("缺失" in e["reason"] for e in errors)
    # 归一化契约：mark_norm = 小写 + 压空格（喂 L2-R5 反查）
    with psycopg.connect(migrated_db) as conn:
        norm = conn.execute(
            "SELECT mark_norm, nice_classes, is_live FROM refdata.trademark WHERE serial_no = %s",
            (f"{PREFIX}0000001",),
        ).fetchone()
    assert norm[0] == "mark brand 1"
    assert norm[1] == [9, 25]
    assert norm[2] is True
    # manifest 完结
    manifest = json.loads((tmp_path / f"{f.name}.manifest.json").read_text())
    assert manifest["finished"] is True
    assert len(manifest["batches"]) == 4  # 100 行 / 30 = 4 批


def test_bulk_idempotent_reimport(migrated_db: str, tmp_path: Path) -> None:
    """同文件重导（无 --resume=全新跑）：ON CONFLICT 恒更新，不增行。"""
    f = tmp_path / f"{PREFIX.lower()}-idem.csv"
    _write_csv(f, 50)
    dsn = _pg_dsn(APP_URL)
    run_bulk(f, batch_size=20, dsn=dsn)
    assert _tm_count(migrated_db) == 50
    s2 = run_bulk(f, batch_size=20, dsn=dsn)
    assert s2["ok"] == 50  # 全部按更新计
    assert _tm_count(migrated_db) == 50  # 不增行


def test_bulk_intra_batch_duplicate_last_wins(migrated_db: str, tmp_path: Path) -> None:
    """批内重复 serial：DISTINCT ON + ctid DESC 取后者，不报错。"""
    f = tmp_path / f"{PREFIX.lower()}-dup.csv"
    with f.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["serial_no", "mark_text"])
        w.writerow([f"{PREFIX}DUP", "First Version"])
        w.writerow([f"{PREFIX}DUP", "Second Version"])
    s = run_bulk(f, batch_size=10, dsn=_pg_dsn(APP_URL))
    assert s["ok"] == 1
    with psycopg.connect(migrated_db) as conn:
        mark = conn.execute(
            "SELECT mark_text FROM refdata.trademark WHERE serial_no = %s", (f"{PREFIX}DUP",)
        ).fetchone()[0]
    assert mark == "Second Version"


def test_bulk_interrupt_and_resume(migrated_db: str, tmp_path: Path) -> None:
    """故障注入：2 批后中断 → --resume 跳过已完成批续跑补齐，总量与直跑一致。"""
    f = tmp_path / f"{PREFIX.lower()}-resume.csv"
    _write_csv(f, 100)
    dsn = _pg_dsn(APP_URL)
    with pytest.raises(KeyboardInterrupt):
        run_bulk(f, batch_size=25, dsn=dsn, fail_after_batches=2)
    assert _tm_count(migrated_db) == 50  # 前 2 批已入
    with psycopg.connect(migrated_db) as conn:
        st = conn.execute(
            "SELECT status FROM app.import_job WHERE source_name = %s ORDER BY id DESC LIMIT 1",
            (f.name,),
        ).fetchone()[0]
    assert st == "failed"  # 中断落状态，可续跑
    s = run_bulk(f, batch_size=25, dsn=dsn, resume=True)
    assert _tm_count(migrated_db) == 100
    assert s["ok"] == 100  # manifest 累计（含前次已完成批）
    assert s["batches"] == 4
    with psycopg.connect(migrated_db) as conn:
        st2 = conn.execute(
            "SELECT status, ok_rows FROM app.import_job WHERE source_name = %s"
            " ORDER BY id DESC LIMIT 1",
            (f.name,),
        ).fetchone()
    assert st2[0] == "done"
    assert st2[1] == 100


def test_bulk_resume_rejects_changed_file(tmp_path: Path) -> None:
    """源文件变更后 --resume 必须拒绝（sha256 不符——批次边界不可信）。"""
    f = tmp_path / f"{PREFIX.lower()}-sha.csv"
    _write_csv(f, 60)
    dsn = _pg_dsn(APP_URL)
    with pytest.raises(KeyboardInterrupt):
        run_bulk(f, batch_size=20, dsn=dsn, fail_after_batches=1)
    _write_csv(f, 61)  # 文件变了
    with pytest.raises(ValueError, match="sha256"):
        run_bulk(f, batch_size=20, dsn=dsn, resume=True)


def test_dataset_revision_bumped_by_any_writer(migrated_db: str, tmp_path: Path) -> None:
    """0014 触发器：bulk merge 与直接 SQL 写都 bump revision（读侧缓存失效不靠写入方自觉）。"""
    dsn = _pg_dsn(APP_URL)
    r0 = _revision(migrated_db, "trademark")
    f = tmp_path / f"{PREFIX.lower()}-rev.csv"
    _write_csv(f, 10)
    run_bulk(f, batch_size=10, dsn=dsn)
    r1 = _revision(migrated_db, "trademark")
    assert r1 > r0  # bulk merge bump
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO refdata.trademark (serial_no, mark_text, mark_norm)"
            " VALUES (%s, 'Direct SQL', 'direct sql') ON CONFLICT (serial_no) DO NOTHING",
            (f"{PREFIX}RAWSQL",),
        )
    assert _revision(migrated_db, "trademark") > r1  # 人工 SQL 同样 bump

    # 黑名单触发器 + 自动机缓存联动：直接 SQL 增删品牌 → revision 变 → 缓存懒重建
    b0 = _revision(migrated_db, "blacklist")
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.blacklist_brand (team_id, brand_norm, source)"
            " VALUES (NULL, 'zbulk trigger probe', 'manual')"
            " ON CONFLICT DO NOTHING"
        )
        b1 = _revision(migrated_db, "blacklist")
        conn.execute("DELETE FROM app.blacklist_brand WHERE brand_norm = 'zbulk trigger probe'")
    assert b1 > b0
    assert _revision(migrated_db, "blacklist") > b1
