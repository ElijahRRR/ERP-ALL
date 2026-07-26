"""权限漏授补齐（2026-07-26 Owner 裁定）：8 个无角色可达的权限码补授给模板角色。

**起因**：2026-07-26 上线的 CI 门禁 `tests/db/test_permission_reachability.py` 实测发现
**10 个 permission 码没有任何角色持有**——即那些能力只有超管（`authn.py` 的 `is_super`
无条件短路）能行使，任何非超管账号永远拿不到。Owner 逐条裁定后：2 条确认为设计上超管专属
（`identity.team_admin` 平台级建团队、`compliance.import_admin` 全局数据 bulk 需 system_tx），
其余 8 条属漏授，本迁移补齐。

**授予口径沿用 0031/0033/0035 的多点范式**：按角色名匹配，故同时覆盖全局模板角色
（`team_id IS NULL`）与既有团队的同名复制角色；`identity/router.py` 建团队时会从模板复制
角色连带权限映射，故本迁移之后新建的团队自动继承。

**三处授予对象与初版建议不同**（Owner 批的是描述、描述与权限官方名称不符，故按实际语义收敛，
方向一律取保守——授窄了将来好放宽，授宽了要回收就得动已在跑的账号）：

| 权限码 | 官方名称 | 初版建议 | 实际授予 | 收敛理由 |
|---|---|---|---|---|
| `listing.error_admin` | 错误字典维护 | 上架员+团管 | **仅团管** | 是平台级错误码字典调优（喂给提交前校验器与永久禁售判定），不是日常上架动作 |
| `catalog.source_write` | 货源录入 | 采集员+团管 | **仅团管** | 「货源」属采购/上架前置（D-Q25 找到货源才上架），非采集配置；归属角色图纸未定，先保守 |
| `catalog.category_write` | 类目映射修正 | 采集员+团管 | **审核员**+团管 | 审核员才是持 `catalog.product_write` 的数据编辑角色；采集员当前仅有 `catalog.product_read`（纯只读），跳到可改类目映射跨度过大 |

其余五条与初版一致，且都有既有映射佐证：订单员已持 `procurement.read` → 补 `execute` 顺理；
维护员已持 `pricing.read` → 补 `write` 对称；`catalog.import_read` 放宽给采集员/审核员是为了
合规中心「导入作业」Tab 的对账可见性（不授则运营看不到自己导入的结果）。
"""

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

# (角色名, 权限码)。按角色名匹配 → 模板角色 + 既有团队同名复制角色一并覆盖。
_GRANTS = [
    # 采购执行双入口的内侧（D-Q50）——不授则内侧等于只有超管能用
    ("订单员", "procurement.execute"),
    ("团队管理员", "procurement.execute"),
    ("团队管理员", "procurement.admin"),
    # 定价：read 已授 write 未授，形态不对称
    ("维护员", "pricing.write"),
    ("团队管理员", "pricing.write"),
    # 导入作业：read 放宽保对账可见性，write 收在团管
    ("采集员", "catalog.import_read"),
    ("审核员", "catalog.import_read"),
    ("团队管理员", "catalog.import_read"),
    ("团队管理员", "catalog.import_write"),
    # 类目映射修正 → 审核员（持 product_write 的数据编辑角色）
    ("审核员", "catalog.category_write"),
    ("团队管理员", "catalog.category_write"),
    # 以下两条保守收在团管（见文件头注表格）
    ("团队管理员", "catalog.source_write"),
    ("团队管理员", "listing.error_admin"),
]

_CODES = sorted({code for _, code in _GRANTS})


def upgrade() -> None:
    values = ",".join(f"('{role}','{code}')" for role, code in _GRANTS)
    op.execute(
        f"""
        INSERT INTO app.role_permission (role_id, permission_code)
        SELECT r.id, g.code FROM app.role r
        JOIN (VALUES {values}) AS g(role_name, code) ON r.name = g.role_name
        WHERE NOT EXISTS (SELECT 1 FROM app.role_permission rp
                          WHERE rp.role_id = r.id AND rp.permission_code = g.code);
        """
    )


def downgrade() -> None:
    """只回收本迁移授出的 (角色名, 权限码) 组合，不按权限码整列删。

    整列删会误伤运营在面板上手工授的同码权限——那是 identity 域的正常操作，不属本迁移产出。
    """
    pairs = ",".join(f"('{role}','{code}')" for role, code in _GRANTS)
    op.execute(
        f"""
        DELETE FROM app.role_permission rp
        USING app.role r, (VALUES {pairs}) AS g(role_name, code)
        WHERE rp.role_id = r.id AND r.name = g.role_name AND rp.permission_code = g.code;
        """
    )
