"""CI 只读门禁①：权限可达性不变量（2026-07-26 Owner 拍板）。

**为什么要这条门禁**——起因是一次误判。审计时看到现网两账号 `compliance_perms=0`，
一度判成「0035 按角色名字面授权至今一份都没发出去」。在干净库上实跑全量迁移后证伪：
`0002_identity.py` 本就把七个角色种成全局模板角色（`team_id IS NULL`），0035 的
`ON r.name = p.role_name` 匹配上了、权限确实发出去了（团队管理员 5 条 compliance、
审核员 3 条）；而 `identity/router.py` 建团队时会复制模板角色连带权限映射。那套
「多点范式」是自洽的。`compliance_perms=0` 的真因是**没有任何用户绑角色**（user_role 空）。

但同一次实测撞出真问题：**有 10 个 permission 码没有任何角色能拿到**，即那些能力
只有超管（`authn.py` 的 `is_super` 短路）能行使，非超管永远拿不到。本门禁把这件事
钉死，一次防住两类复发：

1. 将来某个迁移把角色名写错（改名 / 多语言 / 新建团队），授权静默不生效——名字匹配
   不上不会报错，只会「什么都没发生」；
2. 新增 permission 码时忘了授给任何角色——功能上线了但只有超管能用，没人会注意。

判据：每个 `app.permission` 码必须**要么**被至少一个全局模板角色持有，**要么**
显式列入下面的 `SUPER_ONLY`。新增权限时必须二选一，不许沉默。
"""

import psycopg
import pytest

# ── 超管专属权限白名单 ──
#
# 2026-07-26 首次实测出 10 条「无任何角色可达」，Owner 逐条裁定后：8 条属漏授、已由迁移
# 0039 补授给模板角色并从本白名单移除（下方 `test_super_only_whitelist_has_no_stale_entries`
# 会盯着——补授后忘了清理白名单就红）；仅剩这 2 条确认为设计上超管专属。
SUPER_ONLY = {
    # 建团队/改团队属平台级操作，identity/router.py 亦明确「超管请在目标团队上下文中建角色」
    "identity.team_admin",
    # D-Q65① 方案A + compliance/router.py CLI-only 铁律：全局黑名单/商标 bulk 写入需超管
    # system_tx（大文件不经 HTTP、全局数据跨团队），页面只负责看/核对/纠错，故不授普通角色
    "compliance.import_admin",
}


@pytest.fixture(scope="module")
def perm_rows(migrated_db: str) -> list[tuple[str, int]]:
    """(权限码, 持有它的全局模板角色数)。只看模板角色：团队副本由应用层从模板复制。"""
    with psycopg.connect(migrated_db) as conn:
        return [
            (str(code), int(n))
            for code, n in conn.execute(
                "SELECT p.code, count(r.id)"
                " FROM app.permission p"
                " LEFT JOIN app.role_permission rp ON rp.permission_code = p.code"
                " LEFT JOIN app.role r ON r.id = rp.role_id AND r.team_id IS NULL"
                " GROUP BY p.code ORDER BY p.code"
            ).fetchall()
        ]


def test_every_permission_is_reachable_or_declared_super_only(
    perm_rows: list[tuple[str, int]],
) -> None:
    orphans = sorted(code for code, n in perm_rows if n == 0)
    undeclared = [c for c in orphans if c not in SUPER_ONLY]
    assert not undeclared, (
        "以下 permission 码没有任何全局模板角色持有，且未列入 SUPER_ONLY——"
        "新增权限必须二选一（授给模板角色，或显式声明超管专属并写明理由）：\n  "
        + "\n  ".join(undeclared)
    )


def test_super_only_whitelist_has_no_stale_entries(perm_rows: list[tuple[str, int]]) -> None:
    """白名单不许养僵尸：已被授权的码要从 SUPER_ONLY 移除，否则白名单会越攒越松。"""
    reachable = {code for code, n in perm_rows if n > 0}
    stale = sorted(SUPER_ONLY & reachable)
    assert not stale, (
        "以下码已有模板角色持有，应从 SUPER_ONLY 移除（Owner 裁定后补授的记得清理白名单）：\n  "
        + "\n  ".join(stale)
    )


def test_super_only_entries_all_exist(perm_rows: list[tuple[str, int]]) -> None:
    """白名单里的码必须真实存在，防权限码改名后白名单变成哑条目、把真漏网放过去。"""
    known = {code for code, _ in perm_rows}
    ghosts = sorted(SUPER_ONLY - known)
    assert not ghosts, "SUPER_ONLY 含不存在的权限码（改名或删除后未同步）：\n  " + "\n  ".join(
        ghosts
    )


def test_template_roles_seeded(migrated_db: str) -> None:
    """回归本次误判：0002 的模板角色必须真实存在——它们是按名匹配授权范式的地基。

    若这条红了，说明模板角色被改名/删除，则 0031/0033/0035 那三个按角色名授权的迁移
    会静默失效（名字匹配不上，不报错、什么都不发生）。
    """
    with psycopg.connect(migrated_db) as conn:
        names = {
            str(r[0])
            for r in conn.execute("SELECT name FROM app.role WHERE team_id IS NULL").fetchall()
        }
    # 这两个是 0031/0033/0035 按名授权时点到的角色，缺一个就有迁移静默失效
    assert {"团队管理员", "审核员"} <= names, f"模板角色缺失：{{'团队管理员', '审核员'}} - {names}"


# ── 0039 授予矩阵锁定 ──
#
# 逐条钉死「哪个角色拿到哪个码」。没有这层，将来谁动了 0039 的 _GRANTS 不会有任何信号——
# 上面的可达性不变量只管「至少一个角色持有」，不管持有者是谁；授给错的角色照样能过。
_EXPECTED_0039 = {
    "procurement.execute": {"订单员", "团队管理员"},
    "procurement.admin": {"团队管理员"},
    "pricing.write": {"维护员", "团队管理员"},
    "catalog.import_read": {"采集员", "审核员", "团队管理员"},
    "catalog.import_write": {"团队管理员"},
    "catalog.category_write": {"审核员", "团队管理员"},
    # 以下两条 Owner 裁定后按实际语义收敛为仅团管（见 0039 头注表格）：
    # source_write=货源录入（采购/上架前置，非采集配置）、error_admin=错误字典维护（平台级调优）
    "catalog.source_write": {"团队管理员"},
    "listing.error_admin": {"团队管理员"},
}


def test_0039_grant_matrix_exact(migrated_db: str) -> None:
    """0039 补授的 8 个码，其模板角色持有者集合必须与裁定完全一致（多一个少一个都红）。"""
    with psycopg.connect(migrated_db) as conn:
        rows = conn.execute(
            "SELECT rp.permission_code, r.name FROM app.role_permission rp"
            " JOIN app.role r ON r.id = rp.role_id AND r.team_id IS NULL"
            " WHERE rp.permission_code = ANY(%s)",
            (list(_EXPECTED_0039),),
        ).fetchall()
    actual: dict[str, set[str]] = {code: set() for code in _EXPECTED_0039}
    for code, role in rows:
        actual[str(code)].add(str(role))
    diffs = [
        f"{code}: 期望 {sorted(want)}，实得 {sorted(actual[code])}"
        for code, want in _EXPECTED_0039.items()
        if actual[code] != want
    ]
    assert not diffs, "0039 授予矩阵与裁定不符：\n  " + "\n  ".join(diffs)


def test_pricing_read_write_symmetry(migrated_db: str) -> None:
    """回归本次漏授的形态特征：read 授了 write 没授。

    `pricing.write` 当初就是这么漏的（`pricing.read` 已授维护员/上架员/团管）。这条把
    read/write 成对的模块钉住——凡持 *.read 的角色，若该模块存在同名 *.write 且该 write
    无任何角色持有，即视为同类漏授。
    """
    with psycopg.connect(migrated_db) as conn:
        holders = {
            f"{code}": n
            for code, n in conn.execute(
                "SELECT p.code, count(r.id) FROM app.permission p"
                " LEFT JOIN app.role_permission rp ON rp.permission_code = p.code"
                " LEFT JOIN app.role r ON r.id = rp.role_id AND r.team_id IS NULL"
                " GROUP BY p.code"
            ).fetchall()
        }
    orphan_writes = [
        w
        for r, w in ((c, c[: -len("read")] + "write") for c in holders if c.endswith(".read"))
        if w in holders and holders[w] == 0 and holders[r] > 0 and w not in SUPER_ONLY
    ]
    assert not orphan_writes, (
        "以下 *.write 权限无任何角色持有，而同模块 *.read 已授——与 pricing.write 当初的漏授"
        "形态相同：\n  " + "\n  ".join(sorted(orphan_writes))
    )
