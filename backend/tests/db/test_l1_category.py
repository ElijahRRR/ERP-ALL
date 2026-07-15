"""L1-a 验收：类目判定同步直判 gate（refdata.category_map INNER JOIN pt_meta）。

直判命中非禁做→pass 带 wpt / 命中全禁做(map 或 pt 维度)→reject / 废弃 PT 被 INNER JOIN
滤除→软标记放行 / 无直判命中→软标记放行 / 无类目信息→软标记放行（对拍 round-1 教训：
类目缺图是数据缺口非合规异常，不阻审核）/ browse_node_id 数字键直判。
audit_one 集成：禁做类目→reject_level=l1+audit_rejected；unmapped→pass 放行。
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
        conn.execute("DELETE FROM refdata.pt_spec WHERE walmart_product_type LIKE %s", like)

    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _wipe(conn)
        # pt_meta（第 3-6 元 = zh_seller_forbidden / access_state / zh_can_do / requirements）
        # 可售 PT 必须过 R1 双白名单；'ZL1_Ghost' 故意不建（验 INNER JOIN 滤除）
        pt_rows = [
            (f"{PREFIX}_Drinkware", "Home", False, "普通商品", "是", None),  # 可售
            (f"{PREFIX}_Ammo", "Weapons", True, "普通商品", "是", None),  # zh_seller_forbidden
            (f"{PREFIX}_Approval", "Home", False, "需Walmart审批", "是", None),  # R1 access 拒
            (f"{PREFIX}_ZhNo", "Home", False, "普通商品", "否（进不去）", None),  # R1 zh 拒
            (f"{PREFIX}_Mega", "Electronics", False, "普通商品", "是", None),  # R0 大类硬禁
            (f"{PREFIX}_Cert", "Home", False, "附条件允许", "需评估上架", "需 FDA 注册"),  # R3a
            (f"{PREFIX}_Eval", "Home", False, "附条件允许", "需评估风险", None),  # 可售（需评估*）
            (f"{PREFIX}_SpecHeater", "Home", False, "普通商品", "是", None),  # R3b 整机
            (f"{PREFIX}_Fan Replacement Parts", "Home", False, "普通商品", "是", None),  # R3b 小件
        ]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO refdata.pt_meta (walmart_product_type, walmart_category,"
                " zh_seller_forbidden, access_state, zh_can_do, requirements)"
                " VALUES (%s,%s,%s,%s,%s,%s)",
                pt_rows,
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
            (f"{PREFIX}/Gate/Approval", f"{PREFIX}_Approval", False),  # R1 access
            (f"{PREFIX}/Gate/ZhNo", f"{PREFIX}_ZhNo", False),  # R1 zh_can_do
            (f"{PREFIX}/Gate/Mega", f"{PREFIX}_Mega", False),  # R0 mega
            (f"{PREFIX}/Gate/Cert", f"{PREFIX}_Cert", False),  # R3a cert
            (f"{PREFIX}/Gate/Eval", f"{PREFIX}_Eval", False),  # 需评估* 可售
            (f"{PREFIX}/Gate/SpecWhole", f"{PREFIX}_SpecHeater", False),  # R3b 整机拒
            (f"{PREFIX}/Gate/SpecSmall", f"{PREFIX}_Fan Replacement Parts", False),  # 小件放
        ]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO refdata.category_map"
                " (amazon_category, walmart_product_type, zh_seller_forbidden) VALUES (%s,%s,%s)",
                rows,
            )
        # browse node 数字 ID 直判键（旧库 amazon_leaf_id 多为数字 ID）
        conn.execute(
            "INSERT INTO refdata.category_map"
            " (amazon_category, walmart_product_type, browse_node_id) VALUES (%s,%s,%s)",
            (f"{PREFIX}/Node/Path", f"{PREFIX}_Drinkware", "998877"),
        )
        # R3b：官方 spec 认证层（has_real_cert=true；整机拒/小件放由 nrtl 分类器定）
        conn.execute(
            "INSERT INTO refdata.pt_spec (walmart_product_type, has_real_cert)"
            " VALUES (%s,true),(%s,true)",
            (f"{PREFIX}_SpecHeater", f"{PREFIX}_Fan Replacement Parts"),
        )
    yield
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        _wipe(conn)


async def _run_l1(product: dict) -> dict:
    async with system_tx(_sessions()) as s:
        return await l1_category.run_l1(s, product)


def _l1(category_path=None, amazon_leaf_id=None) -> dict:  # type: ignore[no-untyped-def]
    return asyncio.run(_run_l1({"category_path": category_path, "amazon_leaf_id": amazon_leaf_id}))


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
    assert r["evidence"]["candidates"][0]["wpt"] == f"{PREFIX}_Ammo"
    assert r["evidence"]["candidates"][0]["block"] == "l1_category_forbidden"


def test_map_forbidden_reject() -> None:
    r = _l1(category_path=f"{PREFIX}/Restricted/X")
    assert r["verdict"] == "reject"  # map 行 zh_seller_forbidden=true，即便 PT 本身可售


def test_deprecated_pt_filtered_soft_pass() -> None:
    """废弃 PT（不在 pt_meta）被 INNER JOIN 滤除 → 无有效直判 → 软标记放行。"""
    r = _l1(category_path=f"{PREFIX}/Ghost/Deprecated")
    assert r["verdict"] == "pass"
    assert r["wpt"] is None
    assert r["rule_code"] == "l1_unmapped"
    assert r["is_hard"] is False


def test_mixed_candidates_sellable_wins() -> None:
    """同类目多候选：一禁一可售 → 存在可售通道即 pass（选可售的 WPT）。"""
    r = _l1(category_path=f"{PREFIX}/Multi/Mixed")
    assert r["verdict"] == "pass"
    assert r["wpt"] == f"{PREFIX}_Drinkware"


def test_unmapped_marker_and_no_hit_soft_pass() -> None:
    # '无对应Walmart PT' 标记被排除，等价无直判 → 软标记放行（L2/L3 照跑）
    r1 = _l1(category_path=f"{PREFIX}/Unmapped/None")
    assert (r1["verdict"], r1["rule_code"]) == ("pass", "l1_unmapped")
    r2 = _l1(category_path=f"{PREFIX}/Does/Not/Exist")
    assert (r2["verdict"], r2["wpt"]) == ("pass", None)


def test_leaf_id_key_matches() -> None:
    """amazon_leaf_id 也作为直判键（category_map.amazon_category 命中）。"""
    r = _l1(amazon_leaf_id=f"{PREFIX}/Home/Mugs")
    assert r["verdict"] == "pass"


def test_browse_node_id_key_matches() -> None:
    """旧库 amazon_leaf_id=browse node 数字 ID → 命中 category_map.browse_node_id。"""
    r = _l1(amazon_leaf_id="998877")
    assert r["verdict"] == "pass"
    assert r["wpt"] == f"{PREFIX}_Drinkware"
    assert r["rule_code"] == "l1_category_mapped"


class TestCategoryHardGates:
    """源仓 L2 R0/R1/R3a 保真移植（R2-02 对照审查补齐）。"""

    def test_r1_access_blocked(self) -> None:
        r = _l1(category_path=f"{PREFIX}/Gate/Approval")
        assert r["verdict"] == "reject"
        assert r["rule_code"] == "cat_access_blocked"  # 需Walmart审批 不在白名单

    def test_r1_zh_can_do_blocked(self) -> None:
        r = _l1(category_path=f"{PREFIX}/Gate/ZhNo")
        assert r["verdict"] == "reject"
        assert r["rule_code"] == "cat_zh_blocked"  # zh_can_do 含'否'

    def test_r0_mega_category_blocked(self) -> None:
        r = _l1(category_path=f"{PREFIX}/Gate/Mega")
        assert r["verdict"] == "reject"
        assert r["rule_code"] == "zh_seller_mega_cat_forbidden"  # Electronics 硬禁

    def test_r3a_hard_cert_blocked(self) -> None:
        r = _l1(category_path=f"{PREFIX}/Gate/Cert")
        assert r["verdict"] == "reject"
        assert r["rule_code"] == "cat_requires_cert_hard"  # requirements 含 FDA

    def test_zh_can_do_eval_prefix_sellable(self) -> None:
        """zh_can_do='需评估*' 在白名单内（源仓规则），附条件允许 + 无硬认证 → 可售。"""
        r = _l1(category_path=f"{PREFIX}/Gate/Eval")
        assert r["verdict"] == "pass"
        assert r["wpt"] == f"{PREFIX}_Eval"

    def test_r3b_spec_whole_unit_blocked(self) -> None:
        """R3b：pt_spec.has_real_cert + 整机 PT（nrtl 保守默认）→ NRTL 硬拒。"""
        r = _l1(category_path=f"{PREFIX}/Gate/SpecWhole")
        assert r["verdict"] == "reject"
        assert r["rule_code"] == "cat_requires_cert_hard"

    def test_r3b_spec_small_part_sellable(self) -> None:
        """R3b 降级：PT 名含 'Replacement Parts' → 小件，不硬拒（源仓 R3b small）。"""
        r = _l1(category_path=f"{PREFIX}/Gate/SpecSmall")
        assert r["verdict"] == "pass"
        assert r["wpt"] == f"{PREFIX}_Fan Replacement Parts"


class TestSeedExcluded:
    """seed yaml 预拦截（源仓 L1 _check_seed_excluded 移植，round-3 补齐）。"""

    def test_amazon_path_excluded(self) -> None:
        """Amazon 路径含 'Consumer Electronics' → 直接拒（无需 map 命中）。"""
        r = _l1(category_path="Electronics > Consumer Electronics > Gadgets")
        assert r["verdict"] == "reject"
        assert r["rule_code"] == "l1_excluded_category"
        assert r["is_hard"] is True
        assert "3C" in r["evidence"]["reason"]

    def test_title_only_product_not_excluded(self) -> None:
        """无类目 + 干净 title → 仍软放行（排除检查不误伤）。"""
        r = asyncio.run(_run_l1({"category_path": None, "title": "Plain Ceramic Mug"}))
        assert r["verdict"] == "pass"
        assert r["rule_code"] == "l1_no_category"


def test_no_category_soft_pass() -> None:
    r = _l1()
    assert r["verdict"] == "pass"
    assert r["wpt"] is None
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


def test_audit_one_unmapped_category_passes_through(team_ids: tuple[int, int]) -> None:
    """集成：类目缺图不阻审核——levels=[l0,l1] unmapped → pass + 软命中留痕。"""
    team = team_ids[0]
    with psycopg.connect(_pg_dsn(MIGRATOR_URL), autocommit=True) as conn:
        pid = conn.execute(
            "INSERT INTO app.product (team_id, source_channel, source_ref, title, category_path)"
            " VALUES (%s,'amazon','ZL1AUD002','Plain Bowl', %s)"
            " ON CONFLICT (team_id, source_channel, source_ref) DO UPDATE SET"
            "  status='ingested', category_path=excluded.category_path RETURNING id",
            (team, f"{PREFIX}/Nowhere/ToBe/Found"),
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
    assert out["verdict"] == "pass"
    assert out["reject_level"] is None
    assert out["product_status"] == "audit_passed"
    with psycopg.connect(_pg_dsn(MIGRATOR_URL)) as conn:
        hit = conn.execute(
            "SELECT rule_code, is_hard FROM app.audit_hit WHERE product_id = %s AND level = 'l1'",
            (pid,),
        ).fetchone()
    assert hit == ("l1_unmapped", False)  # 软证据留痕，不否决
