"""R2-13 13a：plugin_instance 采购插件实例（实例专属令牌 + 执行档位）

Revision ID: 0044
Revises: 0043
Create Date: 2026-07-31

口径 `specs/001-domain-model/07-order-sourcing-aftersale.md:274-286`
（2026-07-30 更正后的表定义；+ 本轮补一列 `exec_mode`，见下）。
**一实例 = 一台授权浏览器，不绑任何买家账号。**

## 一、为什么令牌绑浏览器、不绑账号

图纸 `07:288-340`「身份从哪来」（Owner 2026-07-30 质疑后更正）。插件源码实测三条硬事实：
`customerId` 从亚马逊页面 HTML **现场抓**、插件**零 per-install 配置**（同一个 build
装几十台浏览器装完即用）、「提取 customerId」按钮**纯展示不存不发**。故：

| 层 | 由什么承载 | 回答什么问题 |
|---|---|---|
| **授权** | 本表的令牌 | 这是不是我方团队 T 的一台授权浏览器？ |
| **身份** | 请求带上的 `customerId`（页面提取） | 这台浏览器**此刻**登的是哪个买家号？ |

于是装插件不需要先知道 `customerId`（死循环消失）、一台浏览器换登另一个买家号不用
换令牌（路由自然跟随）。越权边界仍在，但形状变了：**跨团队必败**——跨团队的
`customerId` 在本团队范围内解析不到。

## 二、`buyer_account_id` 绑定列与其复合外键：删除的论证

原先本表有 `buyer_account_id bigint NOT NULL` + 复合外键
`FOREIGN KEY (buyer_account_id, team_id) REFERENCES buyer_account (id, team_id)`
（审查 #50 首轮 F2）。**本轮随身份模型更正一并删除。这不是把 F2 回退掉。**

F2 挡的失效链是：

```
实例 team ≠ 账号 team
  → authenticate_instance 在 system_tx 下 JOIN buyer_account 成功（绕过 RLS）
  → 业务段换 ctx_tx(team=实例team)，账号行被 RLS 挡住
  → SELECT … FOR UPDATE 匹配零行、**不报错也不加锁**
  → daily_cap 串行化静默消失，回到 B1 修复前
```

**这条链的第一环（认证时 JOIN 账号表）在本轮被物理删除**：认证不再触碰
`buyer_account`；账号是在 `ctx_tx(team_id=principal.team_id)` 内部、以
`WHERE team_id = :t AND external_customer_id = :c` **解析出来**的。账号 team
**由查询谓词构造**，不再由外键约束校验——「实例 team ≠ 账号 team」这个形状
**不可被表达**，因为根本没有「实例与账号配对」这件事了。三条替代物逐一在位：

| 原 F2 承担 | 新承担者 | 落点 |
|---|---|---|
| 账号必须与实例同 team | 解析谓词 `team_id = :t` + RLS 双层 | `plugin/identity.py::_RESOLVE_SQL` |
| 行锁不得零行 | 保留 F2 修法 1，并**加强**：锁语句补 `AND team_id = :t` | `order/dispatch.py` 账号行锁 |
| 同一 customerId 团队内唯一 | `uq_buyer_account (team_id, external_customer_id)`（0043，本轮升为承重：同时是首见登记的 `ON CONFLICT` 目标） | `0043` |

配套删除的还有 `ix_plugin_instance_account (buyer_account_id, status)`（被索引的列
没了），改建 `ix_plugin_instance_team (team_id, status)` —— 实例列表本轮改为团队级。

**team 级不新增任何约束**：实例不再指向任何团队内实体，`team_id` 单列外键 + RLS 已足够。

## 三、token_hash 走散列不走加密

**不得使用全局共享密钥**——一台机器被盗即全量失守；实例级令牌让吊销粒度=单个浏览器
（图纸 07:281）。存储形态选**散列**（同 `scrape/service.py` 的 worker token 链：
`secrets.token_urlsafe(32)` 生成、只存 sha256、明文只在签发响应出现一次、校验用
`hmac.compare_digest`），**不是**加密（`channel/service.py` 的 `pgp_sym_encrypt` 是给
「我方要拿去用的凭证」；插件 token 是「用来验的」，我方永远不需要读回明文）。
图纸列名写的就是 `token_hash`。

## 四、无 token_hash 唯一索引

认证按 `id` 主键取行再 `compare_digest`，不按 hash 反查。载体是双头部
`X-Plugin-Instance`（实例 id 十进制串）+ `X-Plugin-Token`（明文令牌）——
签发方就是 ERP，`id` 即可索引；id 不是秘密，秘密只有 token。

## 五、exec_mode：比图纸多的一列（已列入批注回传）

来源 Owner 2026-07-30 补-4：「测试买家账号 + 花钱额度到时候前面停在最后一步付款就可以」
——该裁定引出一个图纸里没有的必需能力，即插件必须带一个「走到付款页即停并回报」的档位，
否则验收②⑥⑦⑧ 无法在不花钱的前提下执行。三档沿用本仓 `channel.gateway_mode` 同款思路。

**默认值的安全含义**：新签发的实例默认 `stop_before_payment`，**不会花钱**；
升到 `live` 是一次显式的写操作、有 audit 留痕。默认 `live` 会让「签发即可能扣款」，
不可接受。

## 六、last_seen_customer_id 是观察列，**不参与鉴权**

图纸 `07:283`。它记「该浏览器最近一次带上来的 `customerId`」，纯排障用（"这台机器
现在登的是哪个号"），换号即自然更新；只由两个拉取端点写。**任何鉴权/授权判定都
不得读它**——一读它就等于把「令牌绑账号」这条已被 Owner 推翻的模型偷偷接回来。
它也**不是**外键：那个字符串可能对应一条待认领行、也可能对应一条刚被驳回的行。

## 七、无 DELETE 授权

吊销是 UPDATE（`status='revoked'` + `revoked_at`），不是删行——留着才能回答
「这枚令牌什么时候被谁吊销的」。同 0043，授了 DELETE 就得配 DELETE 策略。
"""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

# 逐字复制自 `0025_order_domain.py:25-39`（同 0043，不 import 跨迁移）。
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


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.plugin_instance (
          id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          -- **实例绑定的是「一台授权浏览器」，不是一个买家账号**（图纸 07:277）。
          -- 令牌回答「这是不是团队 T 的授权浏览器」；「此刻登的是哪个号」由请求带的
          -- customerId 在团队 T 内解析（见头注一/二）。故本表**无** buyer_account_id。
          team_id          bigint NOT NULL REFERENCES app.team(id),
          -- sha256 十六进制串；明文只在签发响应出现一次，任何列表端点都不返回。
          token_hash       text NOT NULL,
          status           text NOT NULL DEFAULT 'active'
                             CONSTRAINT ck_plugin_instance_status
                             CHECK (status IN ('active','revoked')),
          -- 执行档（补-4）：dry_run=不进结账页 / stop_before_payment=付款前停 / live=完整下单。
          exec_mode        text NOT NULL DEFAULT 'stop_before_payment'
                             CONSTRAINT ck_plugin_instance_exec_mode
                             CHECK (exec_mode IN ('dry_run','stop_before_payment','live')),
          -- 插件版本（便于灰度与排障），由拉取端点带 ?v= 回写。
          version          text,
          -- 观察列，**不参与鉴权**（图纸 07:283，见头注六）：该浏览器最近一次带上来的
          -- customerId。排障用（"这台机器现在登的是哪个号"），换号即自然更新。
          -- 只由两个拉取端点写；无外键（可能指向待认领行，也可能指向已驳回行）。
          last_seen_customer_id text,
          last_seen_at     timestamptz,
          revoked_at       timestamptz,
          -- 软引用 app_user.id：签发人自己也可被删（同 0043.created_by）。
          created_by       bigint,
          created_at       timestamptz NOT NULL DEFAULT now(),
          updated_at       timestamptz NOT NULL DEFAULT now()
        );
        -- 团队级索引：实例列表本轮由「某账号下的实例」改为「本团队的实例」。
        CREATE INDEX ix_plugin_instance_team ON app.plugin_instance (team_id, status);
        """
    )
    op.execute(TOUCH.format(t="plugin_instance"))
    op.execute(TEAM_RLS.format(t="plugin_instance"))
    # 无 DELETE（见头注七：吊销是 UPDATE）。
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.plugin_instance TO erp_app;")


def downgrade() -> None:
    """降级丢全部实例令牌（降级后须重新签发，明文本就不可从库中恢复）。"""
    op.execute("DROP TABLE IF EXISTS app.plugin_instance CASCADE;")
