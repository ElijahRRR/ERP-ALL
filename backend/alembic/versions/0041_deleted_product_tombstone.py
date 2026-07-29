"""R2-14 14a-1：deleted_product 墓碑表 + catalog.product_delete 权限点 + 删除路径授权补齐

Revision ID: 0041
Revises: 0040
Create Date: 2026-07-29

口径 `specs/001-domain-model/00-conventions.md §7.1`（硬删除三级规则）。

## 墓碑键为什么是 (team_id, source_channel, source_ref) 而不是图纸字面的 (team_id, asin)

`§7.1` 写 `deleted_product (team_id, asin, ...)`。**`asin` 是有定义的简写**，不是笔误：
`03-catalog.md:14` 定义 `source_ref` = 「ASIN（或未来其他源主键）」，宪法
`business-rules-ledger.md:44`（BR-CAT-002）定义「ASIN 降级为 `(source_channel, source_ref)`
属性」。本迁移即按 BR-CAT-002 把该简写展开成它的存储表示，**与 `uq_product` 严格同形**。

同形是硬要求，不是整洁癖：墓碑存在的唯一目的是让入库去重认得它，而入库去重认的键就是
`uq_product`（`scrape/service.py` 的 `ON CONFLICT (team_id, source_channel, source_ref)`）。
键不同形则墓碑查不中，**「删掉的商品下次采集又抓回来」照样发生**——那正是验收②唯一
要验的东西。展开依据与取舍详见 `.agent/evidence/R2-14/annotation-tombstone-key.md`。

## 比图纸多两列：product_id 与 master_sku（**请审计侧确认或改判**）

图纸列表是 `(team_id, asin, reason, deleted_at, deleted_by)`。本表多存原实体 id 与
master_sku，理由与 `deleted_principal` 存在的理由**同形**：

`audit_run.product_id` / `audit_hit.product_id`（`0008:91`、`0008:135`）是**裸 bigint、
无外键**——删产品既不会被挡也不会级联（这正是③级「审计三族一行不删」得以成立的原因），
但删完之后审计页拿着一个解析不到任何东西的 id。§7.1 为 principal 想到了这一层
（「审计记录仍在但只剩一串认不出的数字」），为 product 没有想到，**因为墓碑键选的是
去重键而不是实体 id**——按去重键根本反查不回 `product_id`。多这两列即可让审计历史
显示「M0000123（已删除）」而不是裸 id。仍是几十字节，§7.1 的空间论证不受影响。

## 追加式：只授 SELECT/INSERT，不授 UPDATE/DELETE

墓碑可改 = 「这个商品没被删过」可以被伪造，去重保护随之失效。故与
`listing_state_history`（`0009:317`）同款只授两权。

> **已知缺口（本增量不做，已在工单登记）**：因此也**没有「撤销墓碑」的通道**——
> 误删一个商品后，实体已物理消失（不可逆），重新采集又被墓碑挡住，即误删不可恢复。
> 前端二次确认是唯一防线。是否要开「恢复采集」入口属范围决策，留给 Owner 裁定。

## 补授 listing / listing_spec 的 DELETE（**这是本迁移唯一的权限放宽**）

`0009:314` 只授了 SELECT/INSERT/UPDATE。而②级（有历史 → 硬删实体）针对的**正是上过架
的产品**，`listing.product_id` / `listing_spec.product_id` 都是 NOT NULL 外键，不删这两张
表的行就删不掉产品——即不补授则②级在图纸要它生效的场景下 100% 失败。

放宽的边界靠代码守：删除服务只在 listing 处于**渠道侧非存活态**时才删
（`draft/delisted/failed/retired`），live/in-flight 一律拒绝——**渠道上还挂着的商品
被删掉本地记录 = 永远下不掉架的孤儿**，比不能删严重得多。判据在
`tests/db/test_product_lifecycle.py`。

**未加 ON DELETE CASCADE**（图纸也没要求）：CASCADE 会让将来任何一次误删静默扩散进
listing 域；显式按序清理在代码里看得见、在判据里挡得住。
"""

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

_PERMISSIONS = [("catalog.product_delete", "catalog", "产品删除")]

# 只授团队管理员：删除不可逆且会连带删掉 listing 行。沿用 0039/0040 头注的方向——
# 授窄了将来好放宽，授宽了要回收就得动已在跑的账号。
_GRANTS = [("团队管理员", "catalog.product_delete")]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.deleted_product (
          id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id        bigint NOT NULL REFERENCES app.team(id),
          source_channel text NOT NULL,
          source_ref     text NOT NULL,
          -- 原实体 id 与内部身份。**无外键**（实体已被物理删除，有外键就建不出这一行）。
          product_id     bigint NOT NULL,
          master_sku     text   NOT NULL,
          reason         text   NOT NULL,
          deleted_at     timestamptz NOT NULL DEFAULT now(),
          -- 无外键指向 app_user：14b 之后用户本身也可被删（同 audit_log.actor_id 的取舍）。
          deleted_by     bigint,
          -- **与 uq_product 严格同形**：墓碑要被入库去重查中，键必须一模一样。
          CONSTRAINT uq_deleted_product UNIQUE (team_id, source_channel, source_ref)
        );
        """
    )
    op.execute(
        """
        ALTER TABLE app.deleted_product ENABLE ROW LEVEL SECURITY;
        CREATE POLICY deleted_product_sel ON app.deleted_product FOR SELECT
          USING (team_id = app.current_team() OR app.is_super());
        CREATE POLICY deleted_product_ins ON app.deleted_product FOR INSERT
          WITH CHECK (team_id = app.current_team() OR app.is_super());
        """
    )
    # 追加式：无 UPDATE / DELETE（同 listing_state_history）。
    op.execute("GRANT SELECT, INSERT ON app.deleted_product TO erp_app;")

    # ②级删除的必要授权（见头注「唯一的权限放宽」）。product / variant_member 的
    # DELETE 早在 0007:260 就有，这里只补 0009 漏掉的两张。
    op.execute("GRANT DELETE ON app.listing, app.listing_spec TO erp_app;")

    perms = ",".join(f"('{code}','{module}','{name}')" for code, module, name in _PERMISSIONS)
    grants = ",".join(f"('{role}','{code}')" for role, code in _GRANTS)
    # 顺序不可换：role_permission.permission_code 有外键指向 permission.code。
    # 按角色名匹配、不带 team_id 谓词 → 模板角色与既有团队同名复制角色一并覆盖
    # （0040 头注的要害：identity/router.py 建团队时的复制是一次性快照，
    #  既有团队不会自动补上新码）。
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
    """**降级会丢掉墓碑**（表被 DROP），即降级后已删商品可被重新采集回来。

    这是降级固有的语义损失，不是本迁移的缺陷：墓碑本就是本迁移引入的保护。
    真机降级前须知悉这一点（同 R2-09 回执的口径：零迁移的单不降级、有迁移的单
    降级即失去该单带来的保护）。
    """
    codes = ",".join(f"'{code}'" for code, _, _ in _PERMISSIONS)
    op.execute(
        f"""
        DELETE FROM app.role_permission WHERE permission_code IN ({codes});
        DELETE FROM app.permission WHERE code IN ({codes});
        """
    )
    op.execute("REVOKE DELETE ON app.listing, app.listing_spec FROM erp_app;")
    op.execute("DROP TABLE IF EXISTS app.deleted_product CASCADE;")
