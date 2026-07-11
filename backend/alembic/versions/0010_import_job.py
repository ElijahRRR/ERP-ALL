"""import_job 标准导入作业（D-Q35 导入接口的 DB 侧）

R2-02 审核弹药灌入的数据载具：黑名单四表 / 商标 / 政策 / category_map 等全部
经此通道 dry-run 校验 → 幂等 upsert → 逐块行数核对 → 汇总报告（specs/001 §04）。
本单实现黑名单四域导入器；其余域表结构就位、导入器随各自工单补。

依据 specs/001-domain-model/04-compliance.md §import_job。
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# spec §import_job.domain 全集（表结构一次到位，避免每加一域改 CHECK）
_DOMAINS = (
    "blacklist_brand",
    "blacklist_seller",
    "blacklist_asin",
    "blacklist_category",
    "tro",
    "phishing",
    "category_map",
    "gtin",
    "trademark",
    "suspension_case",
    "product",
)


def upgrade() -> None:
    domains_sql = ", ".join(f"'{d}'" for d in _DOMAINS)
    op.execute(
        f"""
        CREATE TABLE app.import_job (
          id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id         bigint REFERENCES app.team(id),
          domain          text NOT NULL
                            CONSTRAINT ck_import_job_domain CHECK (domain IN ({domains_sql})),
          source_kind     text NOT NULL DEFAULT 'file'
                            CONSTRAINT ck_import_job_source_kind CHECK (source_kind IN ('file','api')),
          source_name     text NOT NULL,
          format          text NOT NULL DEFAULT 'csv'
                            CONSTRAINT ck_import_job_format CHECK (format IN ('csv','xlsx','jsonl')),
          total_rows      int NOT NULL DEFAULT 0,
          ok_rows         int NOT NULL DEFAULT 0,
          err_rows        int NOT NULL DEFAULT 0,
          skip_rows       int NOT NULL DEFAULT 0,
          status          text NOT NULL DEFAULT 'pending'
                            CONSTRAINT ck_import_job_status
                            CHECK (status IN ('pending','validating','running','done','failed','cancelled')),
          chunk_size      int NOT NULL DEFAULT 5000,
          verify          jsonb NOT NULL DEFAULT '{{}}'::jsonb,
          error_report_ref text,
          started_at      timestamptz,
          finished_at     timestamptz,
          created_by      bigint,
          created_at      timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_import_job_domain_status ON app.import_job (domain, status);
        CREATE INDEX ix_import_job_team ON app.import_job (COALESCE(team_id, 0), id DESC);
        """
    )
    op.execute(
        """
        ALTER TABLE app.import_job ENABLE ROW LEVEL SECURITY;
        CREATE POLICY import_job_sel ON app.import_job FOR SELECT
          USING (team_id IS NULL OR team_id = app.current_team() OR app.is_super());
        CREATE POLICY import_job_ins ON app.import_job FOR INSERT
          WITH CHECK (team_id = app.current_team() OR app.is_super());
        CREATE POLICY import_job_upd ON app.import_job FOR UPDATE
          USING (team_id = app.current_team() OR app.is_super())
          WITH CHECK (team_id = app.current_team() OR app.is_super());
        """
    )
    # 权限点：导入为全局管理动作，默认给超管；团队管理员可查看本团队导入历史
    op.execute(
        """
        INSERT INTO app.permission (code, module, name) VALUES
          ('compliance.import_read',  'compliance', '数据导入历史查看'),
          ('compliance.import_admin', 'compliance', '数据导入执行');
        INSERT INTO app.role_permission (role_id, permission_code)
        SELECT r.id, p.code
        FROM app.role r
        CROSS JOIN (VALUES ('compliance.import_read')) AS p(code)
        WHERE r.team_id IS NULL AND r.name = '团队管理员'
        ON CONFLICT DO NOTHING;
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON app.import_job TO erp_app;"
        "GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO erp_app;"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM app.role_permission WHERE permission_code LIKE 'compliance.import_%';"
        "DELETE FROM app.permission WHERE code LIKE 'compliance.import_%';"
    )
    op.execute("DROP TABLE IF EXISTS app.import_job CASCADE")
