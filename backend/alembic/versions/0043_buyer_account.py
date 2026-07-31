"""R2-13 13b：buyer_account 亚马逊买家账号池 + 三个采购插件权限码

Revision ID: 0043
Revises: 0047
Create Date: 2026-07-31

口径 `specs/001-domain-model/07-order-sourcing-aftersale.md:249-268`（列表）
+ 同文 `:288-340`「身份从哪来」（2026-07-30 更正，本文件按更正后口径落地）。
迁移号 0043-0046 由 `007:592` 三线并行预分配给 R2-13；本文件是该号段第一件，
`down_revision` 指向 **0047**（R2-14 14b 先合入，是本文件落笔时的 head）——
号非单调但链线性，`tests/test_migrations_single_head.py` 只查单 head，合法。

## 本表承载什么

数十个亚马逊买家账号，**各自登录在一个独立指纹浏览器内**；代理与指纹由外部浏览器
管理，**ERP 不管代理**（区别于店铺侧 proxy 绑定）。`label` 的语义即「对应哪个指纹
浏览器」，`external_customer_id` 即插件侧 `customerId`、任务路由主键。

## 一、身份口径（本轮更正，写在这里防返工时再漂）

**令牌回答「这是不是团队 T 的一台授权浏览器」（`plugin_instance`，0044）；请求带的
`customerId` 回答「这台浏览器此刻登的是哪个买家号」。服务端只在团队 T 的范围内把
`customerId` 解析成本表的一行。** 越权边界＝**跨团队必败**，不是「实例只能取自己
账号的任务」——`customerId` 从「鉴权凭据」降为「路由参数」，它**不再是安全边界**。

## 二、首见自动登记 + 待认领 + 驳回

图纸 `07:330`：服务端遇到本团队没见过的 `customerId` → 落一条
`status='pending_claim'` 行并通知，运营补 `label`/`site`/`daily_cap` 后转 `active`；
**`pending_claim` 一律不派单**。故本轮三处改动：

1. `status` DEFAULT 改 `'pending_claim'`、词表加 `pending_claim` 与 `rejected`；
2. `label` / `site` **去 NOT NULL** —— 首见登记时服务端**只知道一个 `customerId`**：
   站点无从得知（`customerId` 不含站点信息），账号名更是人给的。硬留 NOT NULL 只有
   两条路，两条都更糟：编一个占位 `site='amazon_com'` 是**假话**（运营不改就点认领 ⇒
   美国号收到加拿大单）；编一个占位 `label` 会撞 `uq_buyer_account_label`，把首见登记
   变成可 500 的路径（该唯一索引不在 `ON CONFLICT (team_id, external_customer_id)` 的
   推断范围内，冲突会抛 `IntegrityError` 让整个事务作废 ⇒ 一次拉取变 500）。
   **与图纸 `07:258-259` 的 NOT NULL 冲突，已随 PR 列入批注回传的开放问题①。**
   > 附带的隐性依赖：`uq_buyer_account_label (team_id, label)` 在 PG 默认语义下
   > **NULL 互不相等**，故任意多条待认领行可同时 `label IS NULL` 而不撞唯一索引。
3. `ck_buyer_account_claimed` —— **认领闸（承重）**：只有待认领/已驳回可以缺
   `label`/`site`；一旦进入任何会被派单、或被运营当成真账号的状态，两列必须齐全。
   图纸那句「运营补 label/site/daily_cap 后转 active」的**库内执行者**就是它，
   使「active 但没站点」不可表示。

`rejected` 是**垃圾行治理的终态**（超出图纸五值词表，随 PR 批注回传，开放问题②）：
持有效令牌者可用伪造 `customerId` 灌 `pending_claim` 行，而本表**无 DELETE 授权**
⇒ 永久残留。驳回**不删行**，于是该 `(team_id, customerId)` 被 `uq_buyer_account`
**永久占住** ⇒ 同一个伪造 id 再灌一万次都只解析到那一行、零新增行、零新通知。
**粘性不是额外代码，是唯一索引的自然结果**——驳回必须粘住，否则伪造者换个时间再灌。
`ix_buyer_account_pending` 是洪水闸的支撑索引：每次「解析不到的 customerId」都要先
数一次本团队待认领行数（超上限则拒绝登记 + critical 告警），管理端「待认领」筛选同走它。
`rejected` **不占**该额度——否则驳回越多越容易把闸撑死，治理动作反成自伤。

## 三、uq_buyer_account 本轮升为承重

`(team_id, external_customer_id)` 唯一索引原先只是「同一 customerId 团队内唯一」的
声明；本轮它同时是首见登记 `INSERT ... ON CONFLICT DO NOTHING` 的**推断目标**，
即两路并发首见同一新 `customerId` 时「恰好一行、且不抛异常」的保证者。**改动它
（加列、换列序、改成部分索引）会静默破坏首见登记的并发安全**，勿动。

## 四、daily_cap 的唯一落点就是这一列

Owner 2026-07-30 裁定：「自动化策略**不应该**配置这个值，一切以帐号自己配置的属性
为准」（`.agent/evidence/R2-13/owner-rulings-20260730.md` §一）。**`automation_policy`
的 flow config 里不得出现同名键**——两处定义必然漂移，取严/取小方案已被明确否掉。

## 五、为什么不授 DELETE

`GRANT` 只到 SELECT/INSERT/UPDATE。买家账号的删除分支挂在 13b 之后另补
（`identity/delete.py:3-4` 的 docstring、`review_list.json:1097`；0047 头注亦记
「buyer_account 那类主体的删除路径随 13b 另补」）。一旦授了 DELETE 就**必须**同时
`CREATE POLICY buyer_account_del`，否则 `tests/db/test_baseline.py::TestRlsDeleteInvariant`
判红——RLS 下无 DELETE 策略的 DELETE 静默匹配零行、不报错（14b 实撞）。
本迁移选择不授，把这对绑定关系留给真正实现删除的那一单一并处置。
**注意这条与「二」互为因果**：正因为删不掉，驳回才必须是终态而不是删行。

## 六、三个权限码及授予角色

| 码 | 授予 |
|---|---|
| `procurement.buyer_account_read` | 团队管理员、订单员 |
| `procurement.buyer_account_admin` | 团队管理员 |
| `procurement.plugin_instance_admin` | 团队管理员 |

签发实例令牌单列一码的理由：签发 = 发一个**能代表本团队真下单**的凭证，
与「改个备注名」不是同一量级；分开让将来收紧到少数人不必改数据。
认领（`pending_claim→active`）与驳回（`→rejected`）都是账号写，归
`procurement.buyer_account_admin`，**本轮不新增权限码**。
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
          -- 运营可读名，语义=对应哪个指纹浏览器（07:258）。
          -- **可空**：首见自动登记时无从得知，认领时由运营补（见头注二）。
          label                text,
          -- 同上可空；三站词表不变。
          site                 text
                                 CONSTRAINT ck_buyer_account_site
                                 CHECK (site IS NULL OR
                                        site IN ('amazon_com','amazon_ca','amazon_co_jp')),
          -- 插件侧 customerId；**来源＝插件运行时从亚马逊页面现场提取并随请求带上，
          -- 由 ERP 首见自动登记**（图纸 07:288-340）。不是人工预录入——那会造成
          -- 「装插件前先要知道 ID、而 ID 只有装了插件才看得到」的死循环。
          external_customer_id text NOT NULL,
          -- DEFAULT=待认领（07:261）：新行的默认归宿是「发现了但还没人认」，
          -- 不是「可以派单」。**未认领一律不派单。**
          status               text NOT NULL DEFAULT 'pending_claim'
                                 CONSTRAINT ck_buyer_account_status
                                 CHECK (status IN ('pending_claim','active','paused',
                                                   'blocked','retired','rejected')),
          -- 单日采购上限＝该账号的物理承受力。NULL=不限。**唯一落点**（见头注四）。
          daily_cap            int,
          -- 该账号最近一次被插件请求解析到的时间（掉线可见）。
          -- 待认领行也 touch——管理端据此看出「这个未认领的号此刻真的在活动」。
          last_seen_at         timestamptz,
          note                 text,
          created_at           timestamptz NOT NULL DEFAULT now(),
          updated_at           timestamptz NOT NULL DEFAULT now(),
          -- 软引用 app_user.id（同 procurement_order.created_by）：建号人自己也可被删。
          -- 首见自动登记的行**留 NULL**：实例不是用户，写进去是事实错误；
          -- 那条路径的取证落点是通知行（含 created_at + 实例号）。
          created_by           bigint,
          -- 认领闸（承重，见头注二-3）：只有待认领/已驳回可以缺 label/site。
          CONSTRAINT ck_buyer_account_claimed CHECK (
            status IN ('pending_claim','rejected')
            OR (label IS NOT NULL AND site IS NOT NULL)
          )
        );
        -- **本轮升为承重**（头注三）：既是「团队内 customerId 唯一」，也是首见登记
        -- `ON CONFLICT` 的推断目标。勿改列、勿改序、勿改成部分索引。
        CREATE UNIQUE INDEX uq_buyer_account
          ON app.buyer_account (team_id, external_customer_id);
        -- label 可空后：PG 默认 NULL 互不相等，任意多条待认领行可同时 label IS NULL。
        CREATE UNIQUE INDEX uq_buyer_account_label ON app.buyer_account (team_id, label);
        -- 洪水闸的支撑索引（头注二）：登记前数一次本团队待认领行数；管理端筛选同走它。
        CREATE INDEX ix_buyer_account_pending
          ON app.buyer_account (team_id) WHERE status = 'pending_claim';
        CREATE INDEX ix_buyer_account_site ON app.buyer_account (team_id, site, status);
        """
    )
    op.execute(TOUCH.format(t="buyer_account"))
    op.execute(TEAM_RLS.format(t="buyer_account"))
    # 无 DELETE（见头注五）。
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
