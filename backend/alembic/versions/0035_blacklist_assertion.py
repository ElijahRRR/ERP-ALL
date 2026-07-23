"""R2-12 增量1（RS-04D，D-Q65）：blacklist_assertion 断言账本 + tro_case 建表

- blacklist_assertion（source assertion ledger，评审 B5① 硬验收承载）：
  一主体 N 条源断言（import/manual/tro_sync/trademark_sync/error_recycle）并存；
  verdict block/allow（allow=人工白名单裁决，仅 manual 源——压一切自动源）；
  status pending（候选，D-Q65② 报错回收人工闸）/active/revoked（撤销留行，append-only）。
  canonical（blacklist_* 表）由有效断言投影维护，账本可全量重建 canonical。
- 存量回填：六张黑名单表 active 行 → 各生成一条同 source 的 active 断言，
  使 canonical ≡ 断言投影从第一天成立（B5① 验收④ 前提）。
- tro_case：图纸 001 §04:30-48 逐列落地（此前 DDL 缺失，TRO 源断言上游）。
- source CHECK 扩 'error_recycle'（六张黑名单表重建约束；D-Q65 配套）。
- 权限种子：compliance.blacklist_read/write、compliance.trademark_read、
  compliance.tro_read（挂模板「团队管理员」全量 +「审核员」只读，0033 多点范式）。
"""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

_BL_TABLES = (
    "blacklist_brand",
    "blacklist_seller",
    "blacklist_asin",
    "blacklist_category",
    "blacklist_address",
    "blacklist_zip",
)

_SOURCES_NEW = "('import','manual','tro_sync','trademark_sync','error_recycle')"
_SOURCES_OLD = "('import','manual','tro_sync','trademark_sync')"

# 回填映射：表 → (domain, 主体列, 展示列或 NULL 字面量)
_BACKFILL = (
    ("blacklist_brand", "brand", "brand_norm", "brand_display"),
    ("blacklist_seller", "seller", "seller_ref", "seller_name"),
    ("blacklist_asin", "asin", "asin", "NULL"),
    ("blacklist_category", "category", "category_ref", "NULL"),
    ("blacklist_address", "address", "street_norm", "NULL"),
    ("blacklist_zip", "zip", "zip5", "NULL"),
)


def upgrade() -> None:
    # ── 断言账本 ──
    op.execute(
        """
        CREATE TABLE app.blacklist_assertion (
          id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id         bigint REFERENCES app.team(id),
          domain          text NOT NULL CONSTRAINT ck_bla_domain
                            CHECK (domain IN ('brand','seller','asin','category',
                                              'address','zip')),
          subject_norm    text NOT NULL,
          subject_display text,
          verdict         text NOT NULL DEFAULT 'block'
                            CONSTRAINT ck_bla_verdict CHECK (verdict IN ('block','allow')),
          source          text NOT NULL CONSTRAINT ck_bla_source
                            CHECK (source IN """
        + _SOURCES_NEW
        + """),
          source_ref      text,
          reason          text,
          status          text NOT NULL DEFAULT 'active'
                            CONSTRAINT ck_bla_status
                            CHECK (status IN ('pending','active','revoked')),
          created_by      bigint,
          created_at      timestamptz NOT NULL DEFAULT now(),
          decided_by      bigint,
          decided_at      timestamptz,
          revoked_by      bigint,
          revoked_at      timestamptz,
          revoke_reason   text,
          CONSTRAINT ck_bla_allow_manual CHECK (verdict = 'block' OR source = 'manual')
        );
        CREATE UNIQUE INDEX uq_bla_live ON app.blacklist_assertion
          (COALESCE(team_id, 0), domain, subject_norm, source, verdict,
           COALESCE(source_ref, ''))
          WHERE status IN ('pending','active');
        CREATE INDEX ix_bla_subject ON app.blacklist_assertion (domain, subject_norm);
        CREATE INDEX ix_bla_pending ON app.blacklist_assertion (domain, created_at)
          WHERE status = 'pending';

        ALTER TABLE app.blacklist_assertion ENABLE ROW LEVEL SECURITY;
        CREATE POLICY blacklist_assertion_sel ON app.blacklist_assertion FOR SELECT
          USING (team_id IS NULL OR team_id = app.current_team() OR app.is_super());
        CREATE POLICY blacklist_assertion_ins ON app.blacklist_assertion FOR INSERT
          WITH CHECK (team_id = app.current_team() OR app.is_super());
        CREATE POLICY blacklist_assertion_upd ON app.blacklist_assertion FOR UPDATE
          USING (team_id = app.current_team() OR app.is_super())
          WITH CHECK (team_id = app.current_team() OR app.is_super());
        GRANT SELECT, INSERT, UPDATE ON app.blacklist_assertion TO erp_app;
        """
    )

    # ── source CHECK 扩 error_recycle（六表重建约束）──
    for t in _BL_TABLES:
        op.execute(
            f"ALTER TABLE app.{t} DROP CONSTRAINT ck_{t}_source;"
            f" ALTER TABLE app.{t} ADD CONSTRAINT ck_{t}_source"
            f" CHECK (source IN {_SOURCES_NEW})"
        )

    # ── 存量回填：active 黑名单行 → 同 source active 断言（幂等）──
    for table, domain, subj, disp in _BACKFILL:
        op.execute(
            f"""
            INSERT INTO app.blacklist_assertion
              (team_id, domain, subject_norm, subject_display, verdict, source,
               reason, status, created_by, created_at)
            SELECT team_id, '{domain}', {subj}, {disp}, 'block', source,
                   reason, 'active', added_by, added_at
            FROM app.{table} WHERE status = 'active'
            ON CONFLICT (COALESCE(team_id, 0), domain, subject_norm, source, verdict,
                         COALESCE(source_ref, ''))
              WHERE status IN ('pending','active') DO NOTHING
            """
        )

    # ── tro_case（图纸 001 §04:30-48；全局作用域无 team_id）──
    op.execute(
        """
        CREATE TABLE app.tro_case (
          id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          case_no       text NOT NULL,
          court         text,
          filed_date    date,
          plaintiff     text,
          law_firm      text,
          brand_terms   jsonb NOT NULL DEFAULT '[]',
          source        text NOT NULL DEFAULT 'tro-scraper-matrix',
          raw_ref       text,
          status        text NOT NULL DEFAULT 'active'
                          CONSTRAINT ck_tro_status
                          CHECK (status IN ('active','dismissed','settled')),
          imported_at   timestamptz NOT NULL DEFAULT now(),
          import_job_id bigint
        );
        CREATE UNIQUE INDEX uq_tro_case ON app.tro_case (case_no, COALESCE(plaintiff, ''));
        CREATE INDEX ix_tro_brand_terms ON app.tro_case USING gin (brand_terms);
        GRANT SELECT, INSERT, UPDATE ON app.tro_case TO erp_app;
        """
    )

    # ── 合规权限种子（0033 多点范式：模板角色 + 既有团队同名复制角色由应用层继承）──
    op.execute(
        """
        INSERT INTO app.permission (code, module, name) VALUES
          ('compliance.blacklist_read', 'compliance', '黑名单查看（含断言追溯）'),
          ('compliance.blacklist_write', 'compliance', '黑名单维护（断言/裁决/候选确认）'),
          ('compliance.trademark_read', 'compliance', '商标库查询'),
          ('compliance.tro_read', 'compliance', 'TRO 案件查询')
        ON CONFLICT (code) DO NOTHING;
        INSERT INTO app.role_permission (role_id, permission_code)
        SELECT r.id, p.code FROM app.role r
        JOIN (VALUES ('团队管理员', 'compliance.blacklist_read'),
                     ('团队管理员', 'compliance.blacklist_write'),
                     ('团队管理员', 'compliance.trademark_read'),
                     ('团队管理员', 'compliance.tro_read'),
                     ('审核员', 'compliance.blacklist_read'),
                     ('审核员', 'compliance.trademark_read'),
                     ('审核员', 'compliance.tro_read')) AS p(role_name, code)
          ON r.name = p.role_name
        WHERE NOT EXISTS (SELECT 1 FROM app.role_permission rp
                          WHERE rp.role_id = r.id AND rp.permission_code = p.code);
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM app.role_permission WHERE permission_code IN"
        " ('compliance.blacklist_read','compliance.blacklist_write',"
        "  'compliance.trademark_read','compliance.tro_read')"
    )
    op.execute(
        "DELETE FROM app.permission WHERE code IN"
        " ('compliance.blacklist_read','compliance.blacklist_write',"
        "  'compliance.trademark_read','compliance.tro_read')"
    )
    op.execute("DROP TABLE IF EXISTS app.tro_case CASCADE")
    # source 收窄前先清 error_recycle 行（黑名单表与断言账本一起废弃该来源）
    for t in _BL_TABLES:
        op.execute(f"DELETE FROM app.{t} WHERE source = 'error_recycle'")
        op.execute(
            f"ALTER TABLE app.{t} DROP CONSTRAINT ck_{t}_source;"
            f" ALTER TABLE app.{t} ADD CONSTRAINT ck_{t}_source"
            f" CHECK (source IN {_SOURCES_OLD})"
        )
    op.execute("DROP TABLE IF EXISTS app.blacklist_assertion CASCADE")
