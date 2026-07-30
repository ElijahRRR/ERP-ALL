"""R2-14 14b：deleted_principal 墓碑表 + 三类主体删除权限点 + purchaser 删除路径授权

Revision ID: 0047
Revises: 0042
Create Date: 2026-07-30

口径 `specs/001-domain-model/00-conventions.md §7.1`（硬删除三级规则，principal 侧）。
迁移号 0047 为三线并行预分配（0043-0046 归另两条线）；若它们先合入 main，
本文件的 `down_revision` 需一行 rebase 到届时的 head——本单不自行取号。

## 主键为什么必须是 (kind, id) 而不是单列 id

§7.1 写 `deleted_principal (kind, id, label, ...)`。四类主体（user/role/purchaser/
buyer_account）各有各的自增序列，**跨表 id 必然撞号**——单列 id 做 PK 会把
「用户 17」和「角色 17」写成同一行，第二个到达的墓碑被 ON CONFLICT 吞掉，
其历史解析随之静默失效（007「14b 的三个实现坑」第 2 条）。

## 比图纸多一列 team_id

与 0041 的 deleted_product 同由：本仓所有业务表按 team 隔离（RLS），墓碑的读取方
（audit-logs 的操作人解析）跑在团队会话里，无 team_id 则 RLS 无从落笔。
超管（is_super、team_id 可空）不在可删范围（服务层拒绝），故本列 NOT NULL 成立。

## kind 词表现在就含 buyer_account

该类主体的表由 R2-13 13b 建，现库中不存在，**删除路径也随 13b 另补**（007 坑 3：
不为凑齐四类阻塞本单）。词表先含它，13b 落地时无需再动本表。

## procurement_order.purchaser_id 的外键改软引用（本迁移唯一的结构变更）

`0025:232` 是 `bigint REFERENCES app.purchaser(id)`（可空、无 ON DELETE）。
采购执行单属 §7.1 ③级（订单族，D-Q18 永久保留）——**一行不删**；而②级要求
「干过活的采购方：实体行物理删除」。两者在该外键下不可同时成立：删主体被外键挡，
置 NULL 引用列则历史订单从此不知道「当时是谁采购的」，墓碑无从解析（id 都没了）。

解法与仓内既有取舍同形：`audit_log.actor_id` 无外键（§7.1 亲自引为 principal 墓碑
必要性的论据）、`order_line.product_id` 无外键（③级在产品删除下得以成立的原因）、
`deleted_product.deleted_by` 无外键（0041:84 明写「14b 之后用户本身也可被删」）。
③级表指向可删实体的列**本就应当是软引用**，0025 加这条外键时 §7.1 尚不存在。
UI 解析采购方时先查主表、查不到再查墓碑，显示「XX（已删除）」。

> **请审计侧确认或改判**（同 0041 多两列的处置方式）：若判定外键必须保留，
> 则②级对 purchaser 不可达，§7.1 需改写——两者必居其一。

## 追加式：只授 SELECT/INSERT（同 0041 / listing_state_history）

墓碑可改 = 「这个主体没被删过」可被伪造，历史解析随之失效。
撤销通道的缺口口径同 0041 头注：误删不可恢复，前端二次确认是唯一防线。

## 补授 purchaser 的 DELETE（本迁移唯一的权限放宽）

`0025:359` 只授了 SELECT/INSERT/UPDATE——不补则②级对采购方 100% 失败
（14a 在 0009:314 撞过同一形状的坑，0041:105 补的）。app_user / role /
role_permission / user_role 的 DELETE 在 `0002:251` 已有，无需再动。
"""

from alembic import op

revision = "0047"
down_revision = "0042"
branch_labels = None
depends_on = None

_PERMISSIONS = [
    ("identity.user_delete", "identity", "用户删除"),
    ("identity.role_delete", "identity", "角色删除"),
    ("procurement.purchaser_delete", "procurement", "采购方删除"),
]

# 只授团队管理员（0041 同款方向：授窄了将来好放宽，授宽了要回收就得动在跑的账号）。
_GRANTS = [
    ("团队管理员", "identity.user_delete"),
    ("团队管理员", "identity.role_delete"),
    ("团队管理员", "procurement.purchaser_delete"),
]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.deleted_principal (
          kind       text NOT NULL
                       CONSTRAINT ck_deleted_principal_kind CHECK (kind IN
                       ('user','role','purchaser','buyer_account')),
          -- 原实体 id。无外键（实体已被物理删除，有外键就建不出这一行）。
          id         bigint NOT NULL,
          team_id    bigint NOT NULL REFERENCES app.team(id),
          -- 显示名：用户=display_name、角色/采购方=name。username 等身份细节
          -- 在删除动作的 audit_log before 快照里，墓碑只承担「历史可读」的最小面。
          label      text NOT NULL,
          deleted_at timestamptz NOT NULL DEFAULT now(),
          -- 无外键指向 app_user：删除人自己也可被删（同 audit_log.actor_id 的取舍）。
          deleted_by bigint,
          PRIMARY KEY (kind, id)
        );
        """
    )
    op.execute(
        """
        ALTER TABLE app.deleted_principal ENABLE ROW LEVEL SECURITY;
        CREATE POLICY deleted_principal_sel ON app.deleted_principal FOR SELECT
          USING (team_id = app.current_team() OR app.is_super());
        CREATE POLICY deleted_principal_ins ON app.deleted_principal FOR INSERT
          WITH CHECK (team_id = app.current_team() OR app.is_super());
        """
    )
    op.execute("GRANT SELECT, INSERT ON app.deleted_principal TO erp_app;")

    # ③级软引用化（头注「唯一的结构变更」）。约束名为 PG 自动命名的默认形态。
    op.execute(
        "ALTER TABLE app.procurement_order"
        " DROP CONSTRAINT procurement_order_purchaser_id_fkey;"
    )

    # ②级删除的必要授权（头注「唯一的权限放宽」）。
    op.execute("GRANT DELETE ON app.purchaser TO erp_app;")
    # RLS 的 DELETE 策略也得补：0025 的 TEAM_RLS 模板只有 SELECT/INSERT/UPDATE 三策略
    # （0002/_team_rls 与 0007 是四策略含 DELETE，两模板不同款）。**无策略的 DELETE
    # 不报错、静默匹配零行**——本单测试实撞：端点回 200、墓碑照写，purchaser 行还在。
    op.execute(
        """
        CREATE POLICY purchaser_del ON app.purchaser FOR DELETE
          USING (team_id = app.current_team() OR app.is_super());
        """
    )

    perms = ",".join(f"('{code}','{module}','{name}')" for code, module, name in _PERMISSIONS)
    grants = ",".join(f"('{role}','{code}')" for role, code in _GRANTS)
    # 顺序不可换：role_permission.permission_code 外键指向 permission.code。
    # 按角色名匹配、不带 team_id 谓词 → 模板角色与既有团队同名复制角色一并覆盖（同 0041）。
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
    """降级丢墓碑（同 0041 口径：墓碑本就是本迁移引入的保护）。

    外键以 NOT VALID 恢复：升级期间可能已删过采购方，procurement_order 里存在
    指向已删 id 的行，带校验的 ADD CONSTRAINT 会硬失败；NOT VALID 只约束新写入，
    与 0025 原语义的差异（存量不保证）是删除已发生后的固有事实，不是本降级的缺陷。
    """
    codes = ",".join(f"'{code}'" for code, _, _ in _PERMISSIONS)
    op.execute(
        f"""
        DELETE FROM app.role_permission WHERE permission_code IN ({codes});
        DELETE FROM app.permission WHERE code IN ({codes});
        """
    )
    op.execute("DROP POLICY IF EXISTS purchaser_del ON app.purchaser;")
    op.execute("REVOKE DELETE ON app.purchaser FROM erp_app;")
    op.execute(
        "ALTER TABLE app.procurement_order"
        " ADD CONSTRAINT procurement_order_purchaser_id_fkey"
        " FOREIGN KEY (purchaser_id) REFERENCES app.purchaser(id) NOT VALID;"
    )
    op.execute("DROP TABLE IF EXISTS app.deleted_principal CASCADE;")
