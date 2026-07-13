"""L1-a 验收：类目判定同步直判 gate（refdata.category_map INNER JOIN pt_meta）。

直判命中非禁做→pass 带 wpt / 命中全禁做(map 或 pt 维度)→reject / 废弃 PT 被 INNER JOIN
滤除→needs_review / 无直判命中→needs_review / 无类目信息→needs_review。
一条 audit_one 集成：禁做类目 levels=[l0,l1] → reject_level=l1 + 商品 audit_rejected。
"""

import asyncio

import psycopg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from erp.audit import l1_category, service
from erp.core.db import system_tx

from .conftest import APP_URL, MIGRATOR_URL, _pg_dsn

PREFIX = "ZL1"  # 测试专用 amazon_category / walmart_product_type 前缀


def _sessions():  # type: ignore[no-untyped-def]
    return async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)


@pytest.fixture(scope="module", autouse=True)
def _seed(migrated_db: str):  # type: ignore[no-untyped-def]
    """播种 pt_meta（可售/禁做）+ category_map（多场景）。废弃 PT 故意不入 pt_meta。"""

    def _wipe(conn) -> None:  # type: ignore[no-untyped-def]
        like = (f"{PREFIX}%",)
        conn.execute("DELETE FROM refdata.category_map WHERE amazon_category LIKE %s", like)
        conn.execute("DELETE FROM refdata.pt_meta WHERE walmart_product_type LIKE %s", like)

    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _wipe(conn)
        # pt_meta：可售 PT + 禁做 PT（废弃 PT 'ZL1_Ghost' 故意不建，验 INNER JOIN 滤除）
        conn.execute(
            "INSERT INTO refdata.pt_meta"
            " (walmart_product_type, walmart_category, zh_seller_forbidden)"
            " VALUES (%s,%s,false),(%s,%s,true)",
            (f"{PREFIX}_Drinkware", "Home", f"{PREFIX}_Ammo", "Weapons"),
        )
        # category_map：各场景（第 3 元 = zh_seller_forbidden）
        rows = [
            (f"{PREFIX}/Home/Mugs", f"{PREFIX}_Drinkware", False),  # 可售
            (f"{PREFIX}/Weapons/Ammo", f"{PREFIX}_Ammo", False),  # pt 禁做 → reject
            (f"{PREFIX}/Restricted/X", f"{PREFIX}_Drinkware", True),  # map 禁做 → reject
            (f"{PREFIX}/Ghost/Deprecated", f"{PREFIX}_Ghost", False),  # 废弃 PT（不在 pt_meta）
            (f"{PREFIX}/Multi/Mixed", f"{PREFIX}_Ammo", False),  # 混合：禁
            (f"{PREFIX}/Multi/Mixed", f"{PREFIX}_Drinkware", False),  # 混合：可售
            (f"{PREFIX}/Unmapped/None", "无对应Walmart PT", False),  # unmapped 标记
        ]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO refdata.category_map"
                " (amazon_category, walmart_product_type, zh_seller_forbidden) VALUES (%s,%s,%s)",
                rows,
            )
    yield
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _wipe(conn)


async def _run_l1(product: dict) -> dict:
    async with system_tx(_sessions()) as s:
        return await l1_category.run_l1(s, product)


def _l1(category_path=None, amazon_leaf_id=None) -> dict:  # type: ignore[no-untyped-def]
    return asyncio.run(
        _run_l1({"category_path": category_path, "amazon_leaf_id": amazon_leaf_id})
    )


def test_mapped_sellable_pass() -> None:
    r = _l1(category_path=f"{PREFIX}/Home/Mugs")
    assert r["verdict"] == "pass"
    assert r["wpt"] == f"{PREFIX}_Drinkware"
    assert r["rule_code"] == "l1_category_mapped"
    assert r["evidence"]["walmart_category"] == "Home"


def test_pt_forbidden_reject() -> None:
    r = _l1(category_path=f"{PREFIX}/Weapons/Ammo")
    assert r["verdict"] == "reject"
    assert r["rule_code"] == "l1_category_forbidden"
    assert f"{PREFIX}_Ammo" in r["evidence"]["forbidden_wpts"]


def test_map_forbidden_reject() -> None:
    r = _l1(category_path=f"{PREFIX}/Restricted/X")
    assert r["verdict"] == "reject"  # map 行 zh_seller_forbidden=true，即便 PT 本身可售


def test_deprecated_pt_filtered_needs_review() -> None:
    """废弃 PT（不在 pt_meta）被 INNER JOIN 滤除 → 无有效直判 → needs_review。"""
    r = _l1(category_path=f"{PREFIX}/Ghost/Deprecated")
    assert r["verdict"] == "needs_review"
    assert r["rule_code"] == "l1_unmapped"


def test_mixed_candidates_sellable_wins() -> None:
    """同类目多候选：一禁一可售 → 存在可售通道即 pass（选可售的 WPT）。"""
    r = _l1(category_path=f"{PREFIX}/Multi/Mixed")
    assert r["verdict"] == "pass"
    assert r["wpt"] == f"{PREFIX}_Drinkware"


def test_unmapped_marker_and_no_hit_needs_review() -> None:
    # '无对应Walmart PT' 标记被排除，等价无直判
    assert _l1(category_path=f"{PREFIX}/Unmapped/None")["verdict"] == "needs_review"
    assert _l1(category_path=f"{PREFIX}/Does/Not/Exist")["rule_code"] == "l1_unmapped"


def test_leaf_id_key_matches() -> None:
    """amazon_leaf_id 也作为直判键（category_map.amazon_category 命中）。"""
    r = _l1(amazon_leaf_id=f"{PREFIX}/Home/Mugs")
    assert r["verdict"] == "pass"


def test_no_category_needs_review() -> None:
    r = _l1()
    assert r["verdict"] == "needs_review"
    assert r["rule_code"] == "l1_no_category"


def test_audit_one_forbidden_category_rejects_at_l1(team_ids: tuple[int, int]) -> None:
    """集成：禁做类目 levels=[l0,l1] → verdict reject / reject_level=l1 / 商品 audit_rejected。"""
    team = team_ids[0]
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        pid = conn.execute(
            "INSERT INTO app.product (team_id, source_channel, source_ref, title, category_path)"
            " VALUES (%s,'amazon','ZL1AUD001','Ammo Box', %s)"
            " ON CONFLICT (team_id, source_channel, source_ref) DO UPDATE SET"
            "  status='ingested', category_path=excluded.category_path RETURNING id",
            (team, f"{PREFIX}/Weapons/Ammo"),
        ).fetchone()[0]

    out = asyncio.run(
        service.audit_one(
            _sessions(),
            product_id=pid,
            team_id=team,
            is_super=False,
            levels=["l0", "l1"],
        )
    )
    assert out["verdict"] == "reject"
    assert out["reject_level"] == "l1"
    assert out["product_status"] == "audit_rejected"
    with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
        hit = conn.execute(
            "SELECT rule_code, is_hard FROM app.audit_hit WHERE product_id = %s AND level = 'l1'",
            (pid,),
        ).fetchone()
    assert hit == ("l1_category_forbidden", True)
