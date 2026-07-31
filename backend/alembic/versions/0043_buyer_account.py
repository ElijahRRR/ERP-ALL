"""R2-13 13b：buyer_account 亚马逊买家账号池 + 三个采购插件权限码

Revision ID: 0043
Revises: 0047
Create Date: 2026-07-31

口径 `specs/001-domain-model/07-order-sourcing-aftersale.md:249-268` 逐列落地。
迁移号 0043-0046 由 `007:592` 三线并行预分配给 R2-13；本文件是该号段第一件，
`down_revision` 指向 **0047**（R2-14 14b 先合入，是本文件落笔时的 head）——
号非单调但链线性，`tests/test_migrations_single_head.py` 只查单 head，合法。

## 本表承载什么

数十个亚马逊买家账号，**各自登录在一个独立指纹浏览器内**；代理与指纹由外部浏览器
管理，**ERP 不管代理**（区别于店铺侧 proxy 绑定）。`label` 的语义即「对应哪个指纹
浏览器」，`external_customer_id` 即插件侧 `customerId`、任务路由主键。

## daily_cap 的唯一落点就是这一列

Owner 2026-07-30 裁定：「自动化策略**不应该**配置这个值，一切以帐号自己配置的属性
为准」（`.agent/evidence/R2-13/owner-rulings-20260730.md` §一）。**`automation_policy`
的 flow config 里不得出现同名键**——两处定义必然漂移，取严/取小方案已被明确否掉。

## 为什么不授 DELETE

`GRANT` 只到 SELECT/INSERT/UPDATE。买家账号的删除分支挂在 13b 之后另补
（`identity/delete.py:3-4` 的 docstring、`review_list.json:1097`；0047 头注亦记
「buyer_account 那类主体的删除路径随 13b 另补」）。一旦授了 DELETE 就**必须**同时
`CREATE POLICY buyer_account_del`，否则 `tests/db/test_baseline.py::TestRlsDeleteInvariant`
判红——RLS 下无 DELETE 策略的 DELETE 静默匹配零行、不报错（14b 实撞）。
本迁移选择不授，把这对绑定关系留给真正实现删除的那一单一并处置。

## 三个权限码及授予角色

| 码 | 授予 |
|---|---|
| `procurement.buyer_account_read` | 团队管理员、订单员 |
| `procurement.buyer_account_admin` | 团队管理员 |
| `procurement.plugin_instance_admin` | 团队管理员 |

签发实例令牌单列一码的理由：签发 = 发一个**能代表该买家账号真下单**的凭证，
与「改个备注名」不是同一量级；分开让将来收紧到少数人不必改数据。
"""

from alembic import op

revision = "0043"
down_revision = "0047"
branch_labels = None
depends_on = None

# 两个模板逐字复制自 `0025_order_domain.py:25-39`——**不 import 跨迁移**
# （迁移是历史快照，跨文件引用会让旧迁移随新文件改动而改变行为）。
TOUCH = """
CREATE TRIGGER {t}_touch BEFORE UPDATE ON app.{t}
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
"""

TEAM_RLS = """
ALTER TABLE app.{t} ENABLE ROW LEVEL SECURITY;
CREATE POLICY {t}_sel ON app.{t} FOR SELECT
  USING (team_id = app.current_team() OR app.is_super());
CREATE POLICY {t}_ins ON app.{t} FOR INSERT
  WITH CHECK (team_id = app.current_team() OR app.is_super());
CREATE POLICY {t}_upd ON app.{t} FOR UPDATE
  USING (team_id = app.current_team() OR app.is_super())
  WITH CHECK (team_id = app.current_team() OR app.is_super());
"""

_PERMISSIONS = [
    ("procurement.buyer_account_read", "procurement", "买家账号池查看"),
    ("procurement.buyer_account_admin", "procurement", "买家账号池管理"),
    ("procurement.plugin_instance_admin", "procurement", "采购插件实例签发与吊销"),
]

_GRANTS = [
    ("团队管理员", "procurement.buyer_account_read"),
    ("团队管理员", "procurement.buyer_account_admin"),
    ("团队管理员", "procurement.plugin_instance_admin"),
    ("订单员", "procurement.buyer_account_read"),
]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.buyer_account (
          id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id              bigint NOT NULL REFERENCES app.team(id),
          -- 运营可读名，语义=对应哪个指纹浏览器（07:258）
          label                text NOT NULL,
          site                 text NOT NULL
                                 CONSTRAINT ck_buyer_account_site
                                 CHECK (site IN ('amazon_com','amazon_ca','amazon_co_jp')),
          -- 插件侧 customerId；来源=插件面板「提取 customerId」按钮取出后人工录入
          -- （Owner 2026-07-30 补-2 推翻一手来源 §7 的「不需要」）。
          external_customer_id text NOT NULL,
          status               text NOT NULL DEFAULT 'active'
                                 CONSTRAINT ck_buyer_account_status
                                 CHECK (status IN ('active','paused','blocked','retired')),
          -- 单日采购上限＝该账号的物理承受力。NULL=不限。**唯一落点**（见头注）。
          daily_cap            int,
          -- 该账号插件实例最近一次拉任务时间（掉线可见）
          last_seen_at         timestamptz,
          note                 text,
          created_at           timestamptz NOT NULL DEFAULT now(),
          updated_at           timestamptz NOT NULL DEFAULT now(),
          -- 软引用 app_user.id（同 procurement_order.created_by）：建号人自己也可被删。
          created_by           bigint
        );
        CREATE UNIQUE INDEX uq_buyer_account
          ON app.buyer_account (team_id, external_customer_id);
        CREATE UNIQUE INDEX uq_buyer_account_label ON app.buyer_account (team_id, label);
        CREATE INDEX ix_buyer_account_site ON app.buyer_account (team_id, site, status);
        """
    )
    op.execute(TOUCH.format(t="buyer_account"))
    op.execute(TEAM_RLS.format(t="buyer_account"))
    # 无 DELETE（见头注）。
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.buyer_account TO erp_app;")

    perms = ",".join(f"('{code}','{module}','{name}')" for code, module, name in _PERMISSIONS)
    grants = ",".join(f"('{role}','{code}')" for role, code in _GRANTS)
    # 顺序不可换：role_permission.permission_code 外键指向 permission.code。
    # 按角色名匹配、不带 team_id 谓词 → 模板角色与既有团队同名复制角色一并覆盖（同 0047）。
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
    """降级丢账号池数据（本表由本迁移引入，无前序内容可保）。"""
    codes = ",".join(f"'{code}'" for code, _, _ in _PERMISSIONS)
    op.execute(
        f"""
        DELETE FROM app.role_permission WHERE permission_code IN ({codes});
        DELETE FROM app.permission WHERE code IN ({codes});
        """
    )
    op.execute("DROP TABLE IF EXISTS app.buyer_account CASCADE;")
