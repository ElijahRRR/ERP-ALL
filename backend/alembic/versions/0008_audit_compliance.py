"""audit 域 + compliance 首批：blacklist 四表 / audit_run / audit_hit
/ audit_policy / llm_cache / llm_usage_log + refdata.trademark + 种子

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-10

依据 specs/001-domain-model/04-compliance.md §黑名单/refdata、05-audit.md 全部。
- 黑名单四表：team_id NULL=全局（超管维护）；active 内条件唯一；移除不删行。
- audit_run/audit_hit/llm_usage_log 月分区；llm_cache/audit_policy 全局。
- refdata schema：大参考数据，导入管道专写（system_tx 路径）、业务只读，无团队属性。
- 种子：audit_policy 登记 R1-10 实现的规则（L0×5/L2 R4,R5/L3 IP 一条）；
  全局黑名单品牌 10 行（验收要求的手工种子）；audit.* 权限 3 码 + 模板角色同步。
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

BLACKLIST_RLS = """
ALTER TABLE app.{t} ENABLE ROW LEVEL SECURITY;
CREATE POLICY {t}_sel ON app.{t} FOR SELECT
  USING (team_id IS NULL OR team_id = app.current_team() OR app.is_super());
CREATE POLICY {t}_ins ON app.{t} FOR INSERT
  WITH CHECK (team_id = app.current_team() OR app.is_super());
CREATE POLICY {t}_upd ON app.{t} FOR UPDATE
  USING (team_id = app.current_team() OR app.is_super())
  WITH CHECK (team_id = app.current_team() OR app.is_super());
"""


def _blacklist_table(name: str, subject_cols: str, unique_expr: str) -> str:
    return f"""
    CREATE TABLE app.{name} (
      id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      team_id    bigint REFERENCES app.team(id),
      {subject_cols},
      reason     text,
      source     text NOT NULL DEFAULT 'manual'
                   CONSTRAINT ck_{name}_source
                   CHECK (source IN ('import','manual','tro_sync','trademark_sync')),
      status     text NOT NULL DEFAULT 'active'
                   CONSTRAINT ck_{name}_status CHECK (status IN ('active','removed')),
      added_by   bigint,
      added_at   timestamptz NOT NULL DEFAULT now(),
      removed_at timestamptz
    );
    CREATE UNIQUE INDEX uq_{name}_active ON app.{name} ({unique_expr})
      WHERE status = 'active';
    """


def upgrade() -> None:
    # ── 黑名单四表（结构同构）──
    op.execute(
        _blacklist_table(
            "blacklist_brand",
            "brand_norm text NOT NULL, brand_display text",
            "COALESCE(team_id, 0), brand_norm",
        )
    )
    op.execute(
        _blacklist_table(
            "blacklist_seller",
            "seller_ref text NOT NULL, seller_name text",
            "COALESCE(team_id, 0), seller_ref",
        )
    )
    op.execute(
        _blacklist_table("blacklist_asin", "asin text NOT NULL", "COALESCE(team_id, 0), asin")
    )
    op.execute(
        _blacklist_table(
            "blacklist_category",
            "category_ref text NOT NULL",
            "COALESCE(team_id, 0), category_ref",
        )
    )
    for t in ("blacklist_brand", "blacklist_seller", "blacklist_asin", "blacklist_category"):
        op.execute(BLACKLIST_RLS.format(t=t))

    # ── audit_run（月分区）──
    op.execute(
        """
        CREATE TABLE app.audit_run (
          id               bigint GENERATED ALWAYS AS IDENTITY,
          team_id          bigint NOT NULL,
          product_id       bigint NOT NULL,
          trigger_kind     text NOT NULL
                             CONSTRAINT ck_audit_run_trigger
                             CHECK (trigger_kind IN ('auto','manual','batch','re_audit')),
          levels_requested text[] NOT NULL,
          status           text NOT NULL DEFAULT 'queued'
                             CONSTRAINT ck_audit_run_status
                             CHECK (status IN ('queued','running','done','failed')),
          verdict          text
                             CONSTRAINT ck_audit_run_verdict
                             CHECK (verdict IN ('pass','reject','needs_review')),
          reject_level     text,
          llm_cost_usd     numeric(12,6) NOT NULL DEFAULT 0,
          cache_hit_rate   numeric(4,3),
          started_at       timestamptz,
          finished_at      timestamptz,
          duration_ms      int,
          created_at       timestamptz NOT NULL DEFAULT now(),
          created_by       bigint,
          PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at);
        CREATE INDEX ix_audit_run_product ON app.audit_run (team_id, product_id, created_at DESC);
        CREATE INDEX ix_audit_run_verdict ON app.audit_run (team_id, verdict, created_at DESC);
        CREATE INDEX ix_audit_run_active ON app.audit_run (status)
          WHERE status IN ('queued','running');
        SELECT app.ensure_month_partitions('app.audit_run');
        ALTER TABLE app.audit_run ENABLE ROW LEVEL SECURITY;
        CREATE POLICY audit_run_sel ON app.audit_run FOR SELECT
          USING (team_id = app.current_team() OR app.is_super());
        CREATE POLICY audit_run_ins ON app.audit_run FOR INSERT
          WITH CHECK (team_id = app.current_team() OR app.is_super());
        CREATE POLICY audit_run_upd ON app.audit_run FOR UPDATE
          USING (team_id = app.current_team() OR app.is_super())
          WITH CHECK (team_id = app.current_team() OR app.is_super());
        """
    )

    # ── audit_hit（月分区，跟随 run）──
    op.execute(
        """
        CREATE TABLE app.audit_hit (
          id         bigint GENERATED ALWAYS AS IDENTITY,
          run_id     bigint NOT NULL,
          team_id    bigint NOT NULL,
          product_id bigint NOT NULL,
          level      text NOT NULL
                       CONSTRAINT ck_audit_hit_level
                       CHECK (level IN ('l0','l1','l2','l3','l4')),
          rule_code  text NOT NULL,
          is_hard    boolean NOT NULL,
          score      numeric(6,3),
          evidence   jsonb NOT NULL DEFAULT '{}',
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at);
        CREATE INDEX ix_audit_hit_run ON app.audit_hit (run_id);
        CREATE INDEX ix_audit_hit_rule ON app.audit_hit (team_id, rule_code, created_at DESC);
        SELECT app.ensure_month_partitions('app.audit_hit');
        ALTER TABLE app.audit_hit ENABLE ROW LEVEL SECURITY;
        CREATE POLICY audit_hit_sel ON app.audit_hit FOR SELECT
          USING (team_id = app.current_team() OR app.is_super());
        CREATE POLICY audit_hit_ins ON app.audit_hit FOR INSERT
          WITH CHECK (team_id = app.current_team() OR app.is_super());
        """
    )

    # ── audit_policy（全局：策略统一，避免审核标准漂移）──
    op.execute(
        """
        CREATE TABLE app.audit_policy (
          id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          code        text NOT NULL UNIQUE,
          level       text NOT NULL
                        CONSTRAINT ck_audit_policy_level
                        CHECK (level IN ('l0','l1','l2','l3','l4')),
          name        text NOT NULL,
          description text,
          is_hard     boolean NOT NULL DEFAULT false,
          enabled     boolean NOT NULL DEFAULT true,
          config      jsonb NOT NULL DEFAULT '{}',
          version     int NOT NULL DEFAULT 1,
          updated_by  bigint,
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE TRIGGER audit_policy_touch BEFORE UPDATE ON app.audit_policy
          FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
        ALTER TABLE app.audit_policy ENABLE ROW LEVEL SECURITY;
        CREATE POLICY audit_policy_sel ON app.audit_policy FOR SELECT USING (true);
        CREATE POLICY audit_policy_write ON app.audit_policy FOR ALL
          USING (app.is_super()) WITH CHECK (app.is_super());
        """
    )

    # ── llm_cache（全局共享：审核输入不含团队私有数据）──
    op.execute(
        """
        CREATE TABLE app.llm_cache (
          cache_key     text PRIMARY KEY,
          model         text NOT NULL,
          response_text text NOT NULL,
          response_meta jsonb NOT NULL DEFAULT '{}',
          hit_count     int NOT NULL DEFAULT 0,
          created_at    timestamptz NOT NULL DEFAULT now(),
          last_hit_at   timestamptz
        );
        ALTER TABLE app.llm_cache ENABLE ROW LEVEL SECURITY;
        CREATE POLICY llm_cache_all ON app.llm_cache FOR ALL USING (true) WITH CHECK (true);
        """
    )

    # ── llm_usage_log（月分区，成本引擎；缓存命中也记行 cost=0）──
    op.execute(
        """
        CREATE TABLE app.llm_usage_log (
          id                bigint GENERATED ALWAYS AS IDENTITY,
          team_id           bigint,
          module            text NOT NULL
                              CONSTRAINT ck_llm_usage_module
                              CHECK (module IN ('audit','category_map','mail_classify','other')),
          model             text NOT NULL,
          prompt_tokens     int NOT NULL,
          completion_tokens int NOT NULL,
          cost_usd          numeric(12,6) NOT NULL,
          cache_hit         boolean NOT NULL DEFAULT false,
          object_type       text,
          object_id         bigint,
          occurred_at       timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (id, occurred_at)
        ) PARTITION BY RANGE (occurred_at);
        CREATE INDEX ix_llm_usage ON app.llm_usage_log (team_id, module, occurred_at);
        SELECT app.ensure_month_partitions('app.llm_usage_log');
        ALTER TABLE app.llm_usage_log ENABLE ROW LEVEL SECURITY;
        CREATE POLICY llm_usage_sel ON app.llm_usage_log FOR SELECT
          USING (team_id IS NULL OR team_id = app.current_team() OR app.is_super());
        CREATE POLICY llm_usage_ins ON app.llm_usage_log FOR INSERT WITH CHECK (true);
        """
    )

    # ── refdata schema：大参考数据（导入管道专写、业务只读、无团队属性）──
    op.execute(
        """
        CREATE SCHEMA IF NOT EXISTS refdata;
        CREATE TABLE refdata.tm_status_code (
          code        text PRIMARY KEY,
          description text,
          is_live     boolean
        );
        CREATE TABLE refdata.trademark (
          serial_no       text PRIMARY KEY,
          mark_text       text,
          mark_norm       text,
          status_code     text,
          is_live         boolean,
          nice_classes    smallint[],
          owner_name      text,
          filed_date      date,
          registered_date date,
          updated_at      timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_tm_mark_trgm ON refdata.trademark USING gin (mark_norm gin_trgm_ops);
        CREATE INDEX ix_tm_status ON refdata.trademark (status_code);
        CREATE INDEX ix_tm_nice ON refdata.trademark USING gin (nice_classes);
        CREATE INDEX ix_tm_mark_norm ON refdata.trademark (mark_norm);
        GRANT USAGE ON SCHEMA refdata TO erp_app;
        GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA refdata TO erp_app;
        """
    )

    # ── 种子：audit_policy（R1-10 实现范围；L4 占位 enabled=false）──
    op.execute(
        """
        INSERT INTO app.audit_policy (code, level, name, is_hard, enabled, config) VALUES
        ('l0_blacklist_seller',   'l0', 'L0 卖家黑名单',        true,  true,  '{}'),
        ('l0_blacklist_asin',     'l0', 'L0 ASIN 黑名单',       true,  true,  '{}'),
        ('l0_blacklist_category', 'l0', 'L0 类目黑名单',        true,  true,  '{}'),
        ('l0_trademark_symbol',   'l0', 'L0 商标符号硬拦(®™℠©)', true,  true,  '{}'),
        ('l0_blacklist_brand',    'l0', 'L0 品牌精准黑名单',    true,  true,  '{}'),
        ('l2_r4_title_desc_blacklist', 'l2', 'L2-R4 标题描述黑名单词(软证据)', false, true,
         '{"min_word_len": 4}'),
        ('l2_r5_trademark_live', 'l2', 'L2-R5 USPTO LIVE 商标(软证据)', false, true,
         '{"max_marks": 10}'),
        ('l3_intellectual_property', 'l3', 'L3 知识产权语义审核(LLM)', true, true,
         '{"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 1200}'),
        ('l4_vision', 'l4', 'L4 视觉审核(默认关)', true, false, '{}');
        """
    )

    # ── 种子：全局黑名单品牌 10 行（R1-10 验收：手工种子）──
    op.execute(
        """
        INSERT INTO app.blacklist_brand (team_id, brand_norm, brand_display, source, reason)
        VALUES
        (NULL, 'nike', 'Nike', 'manual', '知名品牌-侵权高危'),
        (NULL, 'disney', 'Disney', 'manual', '知名IP-侵权高危'),
        (NULL, 'apple', 'Apple', 'manual', '知名品牌-侵权高危'),
        (NULL, 'adidas', 'Adidas', 'manual', '知名品牌-侵权高危'),
        (NULL, 'lego', 'LEGO', 'manual', '知名品牌-侵权高危'),
        (NULL, 'marvel', 'Marvel', 'manual', '知名IP-侵权高危'),
        (NULL, 'pokemon', 'Pokemon', 'manual', '知名IP-侵权高危'),
        (NULL, 'gucci', 'Gucci', 'manual', '奢侈品牌-假冒高危'),
        (NULL, 'louis vuitton', 'Louis Vuitton', 'manual', '奢侈品牌-假冒高危'),
        (NULL, 'dyson', 'Dyson', 'manual', '知名品牌-侵权高危');
        """
    )

    # ── 种子：audit.* 权限 + 模板角色同步 ──
    op.execute(
        """
        INSERT INTO app.permission (code, module, name) VALUES
          ('audit.run',          'audit', '发起审核'),
          ('audit.read',         'audit', '审核结果查看'),
          ('audit.policy_admin', 'audit', '审核策略管理');
        INSERT INTO app.role_permission (role_id, permission_code)
        SELECT r.id, p.code
        FROM app.role r
        CROSS JOIN (VALUES ('audit.run'), ('audit.read')) AS p(code)
        WHERE r.name = '审核员'
        ON CONFLICT DO NOTHING;
        INSERT INTO app.role_permission (role_id, permission_code)
        SELECT r.id, p.code
        FROM app.role r
        CROSS JOIN (VALUES ('audit.run'), ('audit.read'), ('audit.policy_admin')) AS p(code)
        WHERE r.name = '团队管理员'
        ON CONFLICT DO NOTHING;
        """
    )

    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE ON app.blacklist_brand, app.blacklist_seller,
          app.blacklist_asin, app.blacklist_category, app.audit_run, app.llm_cache TO erp_app;
        GRANT SELECT, INSERT ON app.audit_hit, app.llm_usage_log TO erp_app;
        GRANT SELECT ON app.audit_policy TO erp_app;
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO erp_app;
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM app.role_permission WHERE permission_code LIKE 'audit.%';"
        "DELETE FROM app.permission WHERE code LIKE 'audit.%';"
    )
    op.execute("DROP SCHEMA IF EXISTS refdata CASCADE")
    for t in (
        "llm_usage_log",
        "llm_cache",
        "audit_policy",
        "audit_hit",
        "audit_run",
        "blacklist_category",
        "blacklist_asin",
        "blacklist_seller",
        "blacklist_brand",
    ):
        op.execute(f"DROP TABLE IF EXISTS app.{t} CASCADE")
