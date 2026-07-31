"""R2-13 13a：plugin_instance 采购插件实例（实例专属令牌 + 执行档位）

Revision ID: 0044
Revises: 0043
Create Date: 2026-07-31

口径 `specs/001-domain-model/07-order-sourcing-aftersale.md:274-282`
（+ 本轮补一列 `exec_mode`，见下）。一实例绑定一买家账号 = 一个指纹浏览器。

## token_hash 走散列不走加密

**不得使用全局共享密钥**——一台机器被盗即全量失守；实例级令牌让吊销粒度=单个浏览器
（图纸 07:281）。存储形态选**散列**（同 `scrape/service.py` 的 worker token 链：
`secrets.token_urlsafe(32)` 生成、只存 sha256、明文只在签发响应出现一次、校验用
`hmac.compare_digest`），**不是**加密（`channel/service.py` 的 `pgp_sym_encrypt` 是给
「我方要拿去用的凭证」；插件 token 是「用来验的」，我方永远不需要读回明文）。
图纸列名写的就是 `token_hash`。

## 无 token_hash 唯一索引

认证按 `id` 主键取行再 `compare_digest`，不按 hash 反查。载体是双头部
`X-Plugin-Instance`（实例 id 十进制串）+ `X-Plugin-Token`（明文令牌）——
签发方就是 ERP，`id` 即可索引；id 不是秘密，秘密只有 token。

## 为什么 buyer_account_id 带硬外键，而 procurement_order.buyer_account_id（0045）不带

这不是随手，是 `0047` 头注那条规则的两侧：**③级表（订单族，D-Q18 永久保留）指向可删
实体的列应当是软引用**；而 `plugin_instance` 是运维态表、可随主体一并清理，**硬外键
正好逼将来的 buyer_account 删除路径显式处置实例**，而不是留下一枚仍能通过认证的
孤儿令牌。两列同名不同性质，勿按对称性统一。

## exec_mode：比图纸多的一列（已列入批注回传）

来源 Owner 2026-07-30 补-4：「测试买家账号 + 花钱额度到时候前面停在最后一步付款就可以」
——该裁定引出一个图纸里没有的必需能力，即插件必须带一个「走到付款页即停并回报」的档位，
否则验收②⑥⑦⑧ 无法在不花钱的前提下执行。三档沿用本仓 `channel.gateway_mode` 同款思路。

**默认值的安全含义**：新签发的实例默认 `stop_before_payment`，**不会花钱**；
升到 `live` 是一次显式的写操作、有 audit 留痕。默认 `live` 会让「签发即可能扣款」，
不可接受。

## 无 DELETE 授权

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
          team_id          bigint NOT NULL REFERENCES app.team(id),
          -- 硬外键（见头注）：孤儿令牌仍能通过认证，必须让删除路径显式处置。
          buyer_account_id bigint NOT NULL REFERENCES app.buyer_account(id),
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
          last_seen_at     timestamptz,
          revoked_at       timestamptz,
          -- 软引用 app_user.id：签发人自己也可被删（同 0043.created_by）。
          created_by       bigint,
          created_at       timestamptz NOT NULL DEFAULT now(),
          updated_at       timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_plugin_instance_account
          ON app.plugin_instance (buyer_account_id, status);
        """
    )
    op.execute(TOUCH.format(t="plugin_instance"))
    op.execute(TEAM_RLS.format(t="plugin_instance"))
    # 无 DELETE（见头注：吊销是 UPDATE）。
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.plugin_instance TO erp_app;")


def downgrade() -> None:
    """降级丢全部实例令牌（降级后须重新签发，明文本就不可从库中恢复）。"""
    op.execute("DROP TABLE IF EXISTS app.plugin_instance CASCADE;")
