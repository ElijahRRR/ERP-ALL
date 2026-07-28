"""自动化档位面板权限点（R2-09 增量2）：`automation.read` / `automation.write`。

**为什么要新码**：增量2 上 `GET /automation-policies` 与
`PUT /automation-policies/{flow_code}`。全仓零个以 `automation.` 开头的权限码
（0002 基线未预埋平台档位面），复用现有码都不对——档位面板是**跨域**的流程开关
（采集/审核/上架/定价/订单闸/合规闸/售后/采购/维护九类全在一张表上），挂在任何
单域的码下都会给出错误的授权面。

**授予口径沿用 0031/0033/0035/0039 的多点范式**：按角色名匹配、**不带 `team_id`
谓词**，故同一条 INSERT 同时覆盖全局模板角色（`team_id IS NULL`）与既有团队的
同名复制角色。**这一点是本迁移的要害**：`identity/router.py:121-141` 建团队时从
模板复制角色连带权限映射，但那是**一次性快照**——本迁移之后新建的团队自动继承，
之前已建的团队不会自动更新。漏了团队副本 = 现网既有团队永远看不到这个面板，
且没有任何报错，只是菜单不出现（判据见
`backend/tests/db/test_automation_api.py::test_0040_backfills_existing_team_role_copies`）。

**只授团队管理员**：档位切换是团队级流程开关，误改的影响面覆盖全域——把
`order_block` 关掉即等于该团队订单拦截静默失效（`core/automation.py` 模块头注）。
方向沿用 0039 头注：授窄了将来好放宽，授宽了要回收就得动已在跑的账号。
`read` 一并只授团管：面板的「读」本身带着「谁能看到全域自动化态势」的含义；且
`test_pricing_read_write_symmetry` 的形态规则要求同模块 read/write 成对可达，
分开授反而制造出它要抓的形态。

**不引入 `_CO_OWNED_NOT_REVOKED`**：`automation.*` 是全新码，与任何前序迁移零重叠
（0039 那张豁免表存在的唯一原因是它与 0010 撞了 (团管, `compliance.import_read`)）。
故 downgrade 照 0031:117-124 按码整列删即可——本迁移是这两个码的唯一出处。
"""

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

# (code, module, name)。`app.permission.module` 无 CHECK 约束，新增取值 `automation` 合法；
# 前端 RolesPage 按 module 原样分组渲染（无中文映射表，本增量不引入）。
_PERMISSIONS = [
    ("automation.read", "automation", "自动化档位查看"),
    ("automation.write", "automation", "自动化档位配置"),
]

# (角色名, 权限码)。按角色名匹配 → 模板角色 + 既有团队同名复制角色一并覆盖。
_GRANTS = [
    ("团队管理员", "automation.read"),
    ("团队管理员", "automation.write"),
]


def upgrade() -> None:
    perms = ",".join(f"('{code}','{module}','{name}')" for code, module, name in _PERMISSIONS)
    grants = ",".join(f"('{role}','{code}')" for role, code in _GRANTS)
    # 顺序不可换：role_permission.permission_code 有外键指向 permission.code
    op.execute(
        f"""
        INSERT INTO app.permission (code, module, name) VALUES {perms}
        ON CONFLICT (code) DO NOTHING;
        INSERT INTO app.role_permission (role_id, permission_code)
        SELECT r.id, g.code FROM app.role r
        JOIN (VALUES {grants}) AS g(role_name, code) ON r.name = g.role_name
        WHERE NOT EXISTS (SELECT 1 FROM app.role_permission rp
                          WHERE rp.role_id = r.id AND rp.permission_code = g.code);
        """
    )


def downgrade() -> None:
    """按码整列删（先 role_permission 后 permission，FK 方向）。

    与 0039 的细粒度 (角色, 码) 回收不同：那里必须细，是因为它与 0010 共有一对，
    整列删会替 0010 把线上功能删掉。本迁移是 `automation.*` 两码的**唯一出处**，
    整列删不会误伤任何前序迁移。会一并删掉运维事后手工授出的同码权限——但那些授予
    本就依赖本迁移种的码，码没了它们也无处可挂。
    """
    codes = ",".join(f"'{code}'" for code, _, _ in _PERMISSIONS)
    op.execute(
        f"""
        DELETE FROM app.role_permission WHERE permission_code IN ({codes});
        DELETE FROM app.permission WHERE code IN ({codes});
        """
    )
