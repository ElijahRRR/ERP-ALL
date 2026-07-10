"""scrape 域 + catalog 首批：product / variant_group / variant_member
/ scrape_job / scrape_task(月分区) / scrape_result(月分区) / worker_node + 权限种子

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-10

依据 specs/001-domain-model/03-catalog.md §product/variant、09-platform.md §scraping。
- product 去重键 uq_product(team_id, source_channel, source_ref)（D-Q31）；
  master_sku 由 master_sku_seq 生成 'M'||lpad(7)（D1，终身不变不回收）。
- scrape_task.attempt 兼作租约纪元（v3 lease_epoch 语义）：每次派发 +1，
  回传必须携带匹配 attempt，迟到结果（旧租约）拒收。
- worker_node 全局作用域（节点是基础设施）：SELECT 放开、写路径仅系统上下文
  （worker 拨入 API 走 system_tx，用户会话不可写）。
- 补漏：scrape.* 权限码当时未入 36 码种子，此处补种并同步「采集员」模板角色
  及既有团队复制角色（试点期团队少，按名同步可接受）。
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TEAM_RLS = """
ALTER TABLE app.{t} ENABLE ROW LEVEL SECURITY;
CREATE POLICY {t}_sel ON app.{t} FOR SELECT
  USING (team_id = app.current_team() OR app.is_super());
CREATE POLICY {t}_ins ON app.{t} FOR INSERT
  WITH CHECK (team_id = app.current_team() OR app.is_super());
CREATE POLICY {t}_upd ON app.{t} FOR UPDATE
  USING (team_id = app.current_team() OR app.is_super())
  WITH CHECK (team_id = app.current_team() OR app.is_super());
CREATE POLICY {t}_del ON app.{t} FOR DELETE
  USING (team_id = app.current_team() OR app.is_super());
"""

TOUCH = """
CREATE TRIGGER {t}_touch BEFORE UPDATE ON app.{t}
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
"""


def upgrade() -> None:
    # ── catalog: master_sku 序列 + product ──
    op.execute(
        """
        CREATE SEQUENCE app.master_sku_seq START 1;
        CREATE TABLE app.product (
          id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          master_sku          text NOT NULL UNIQUE
                                DEFAULT 'M' || lpad(nextval('app.master_sku_seq')::text, 7, '0'),
          team_id             bigint NOT NULL REFERENCES app.team(id),
          source_channel      text NOT NULL DEFAULT 'amazon',
          source_ref          text NOT NULL,
          title               text NOT NULL,
          brand               text,
          brand_norm          text GENERATED ALWAYS AS (lower(btrim(brand))) STORED,
          category_path       text,
          amazon_leaf_id      text,
          images              jsonb NOT NULL DEFAULT '[]',
          attrs               jsonb NOT NULL DEFAULT '{}',
          price_snapshot      jsonb,
          status              text NOT NULL DEFAULT 'ingested'
                                CONSTRAINT ck_product_status CHECK (status IN
                                ('ingested','auditing','audit_passed','audit_rejected',
                                 'sourcing','ready','listed','retired')),
          latest_audit_run_id bigint,
          variant_group_id    bigint,
          created_at          timestamptz NOT NULL DEFAULT now(),
          updated_at          timestamptz NOT NULL DEFAULT now(),
          created_by          bigint,
          CONSTRAINT uq_product UNIQUE (team_id, source_channel, source_ref)
        );
        CREATE INDEX ix_product ON app.product (team_id, status);
        CREATE INDEX ix_product_brand_trgm ON app.product USING gin (brand_norm gin_trgm_ops);
        CREATE INDEX ix_product_leaf ON app.product (amazon_leaf_id);
        """
    )
    op.execute(TOUCH.format(t="product"))
    op.execute(TEAM_RLS.format(t="product"))

    # ── catalog: 变体组 ──
    op.execute(
        """
        CREATE TABLE app.variant_group (
          id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id           bigint NOT NULL REFERENCES app.team(id),
          source_parent_ref text,
          variation_theme   text,
          status            text NOT NULL DEFAULT 'active'
                              CONSTRAINT ck_variant_group_status
                              CHECK (status IN ('active','broken')),
          created_at        timestamptz NOT NULL DEFAULT now(),
          updated_at        timestamptz NOT NULL DEFAULT now(),
          created_by        bigint
        );
        CREATE TABLE app.variant_member (
          group_id      bigint NOT NULL REFERENCES app.variant_group(id),
          product_id    bigint NOT NULL UNIQUE REFERENCES app.product(id),
          variant_attrs jsonb NOT NULL DEFAULT '{}',
          is_primary    boolean NOT NULL DEFAULT false,
          PRIMARY KEY (group_id, product_id)
        );
        CREATE UNIQUE INDEX uq_variant_primary ON app.variant_member (group_id)
          WHERE is_primary;
        ALTER TABLE app.product ADD CONSTRAINT fk_product_variant_group
          FOREIGN KEY (variant_group_id) REFERENCES app.variant_group(id);
        ALTER TABLE app.variant_member ENABLE ROW LEVEL SECURITY;
        CREATE POLICY variant_member_all ON app.variant_member FOR ALL
          USING (EXISTS (SELECT 1 FROM app.variant_group g WHERE g.id = group_id
                 AND (g.team_id = app.current_team() OR app.is_super())))
          WITH CHECK (EXISTS (SELECT 1 FROM app.variant_group g WHERE g.id = group_id
                 AND (g.team_id = app.current_team() OR app.is_super())));
        """
    )
    op.execute(TOUCH.format(t="variant_group"))
    op.execute(TEAM_RLS.format(t="variant_group"))

    # ── scraping: worker_node（全局基础设施）──
    op.execute(
        """
        CREATE TABLE app.worker_node (
          id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          node_key          text NOT NULL UNIQUE,
          token_hash        text NOT NULL,
          kind              text NOT NULL
                              CONSTRAINT ck_worker_kind
                              CHECK (kind IN ('scraper_amazon','scraper_walmart')),
          version           text,
          status            text NOT NULL DEFAULT 'offline'
                              CONSTRAINT ck_worker_status
                              CHECK (status IN ('online','offline','draining')),
          capacity          int NOT NULL DEFAULT 1,
          window_state      jsonb NOT NULL DEFAULT '{}',
          last_heartbeat_at timestamptz,
          stats             jsonb NOT NULL DEFAULT '{}',
          registered_at     timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE app.worker_node ENABLE ROW LEVEL SECURITY;
        CREATE POLICY worker_node_sel ON app.worker_node FOR SELECT USING (true);
        CREATE POLICY worker_node_write ON app.worker_node FOR ALL
          USING (app.is_super()) WITH CHECK (app.is_super());
        """
    )

    # ── scraping: scrape_job ──
    op.execute(
        """
        CREATE TABLE app.scrape_job (
          id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id      bigint NOT NULL REFERENCES app.team(id),
          source       text NOT NULL
                         CONSTRAINT ck_scrape_job_source
                         CHECK (source IN ('amazon','walmart')),
          job_kind     text NOT NULL
                         CONSTRAINT ck_scrape_job_kind
                         CHECK (job_kind IN
                         ('product_detail','keyword','seller','bestseller','price_watch')),
          input        jsonb NOT NULL,
          status       text NOT NULL DEFAULT 'pending'
                         CONSTRAINT ck_scrape_job_status
                         CHECK (status IN
                         ('pending','running','done','partial','failed','cancelled')),
          priority     smallint NOT NULL DEFAULT 100,
          total_tasks  int NOT NULL DEFAULT 0,
          done_tasks   int NOT NULL DEFAULT 0,
          failed_tasks int NOT NULL DEFAULT 0,
          requested_by bigint,
          finished_at  timestamptz,
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now(),
          created_by   bigint
        );
        CREATE INDEX ix_scrape_job ON app.scrape_job (team_id, status, created_at DESC);
        """
    )
    op.execute(TOUCH.format(t="scrape_job"))
    op.execute(TEAM_RLS.format(t="scrape_job"))

    # ── scraping: scrape_task（月分区；attempt 兼作租约纪元）──
    op.execute(
        """
        CREATE TABLE app.scrape_task (
          id            bigint GENERATED ALWAYS AS IDENTITY,
          job_id        bigint NOT NULL REFERENCES app.scrape_job(id),
          team_id       bigint NOT NULL,
          target_ref    text NOT NULL,
          status        text NOT NULL DEFAULT 'pending'
                          CONSTRAINT ck_scrape_task_status
                          CHECK (status IN
                          ('pending','dispatched','running','done','failed','dead')),
          attempt       smallint NOT NULL DEFAULT 0,
          max_attempts  smallint NOT NULL DEFAULT 3,
          worker_id     bigint REFERENCES app.worker_node(id),
          dispatched_at timestamptz,
          finished_at   timestamptz,
          error         text,
          created_at    timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at);
        CREATE INDEX ix_scrape_task_job ON app.scrape_task (job_id, status);
        CREATE INDEX ix_scrape_task_queue ON app.scrape_task (status, created_at)
          WHERE status IN ('pending','dispatched');
        SELECT app.ensure_month_partitions('app.scrape_task');
        """
    )
    op.execute(TEAM_RLS.format(t="scrape_task"))

    # ── scraping: scrape_result（月分区，append-only）──
    op.execute(
        """
        CREATE TABLE app.scrape_result (
          id          bigint GENERATED ALWAYS AS IDENTITY,
          task_id     bigint NOT NULL,
          team_id     bigint NOT NULL,
          target_ref  text NOT NULL,
          payload     jsonb,
          payload_ref text,
          fetched_at  timestamptz NOT NULL,
          created_at  timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at);
        CREATE INDEX ix_scrape_result_task ON app.scrape_result (task_id);
        SELECT app.ensure_month_partitions('app.scrape_result');
        ALTER TABLE app.scrape_result ENABLE ROW LEVEL SECURITY;
        CREATE POLICY scrape_result_sel ON app.scrape_result FOR SELECT
          USING (team_id = app.current_team() OR app.is_super());
        CREATE POLICY scrape_result_ins ON app.scrape_result FOR INSERT
          WITH CHECK (team_id = app.current_team() OR app.is_super());
        """
    )

    # ── 权限种子补漏 + 模板角色同步 ──
    op.execute(
        """
        INSERT INTO app.permission (code, module, name) VALUES
          ('scrape.job_read',  'scrape', '采集作业查看'),
          ('scrape.job_write', 'scrape', '采集作业管理'),
          ('scrape.node_admin','scrape', '采集节点管理');
        INSERT INTO app.role_permission (role_id, permission_code)
        SELECT r.id, p.code
        FROM app.role r
        CROSS JOIN (VALUES ('scrape.job_read'), ('scrape.job_write')) AS p(code)
        WHERE r.name = '采集员'
        ON CONFLICT DO NOTHING;
        INSERT INTO app.role_permission (role_id, permission_code)
        SELECT r.id, p.code
        FROM app.role r
        CROSS JOIN (VALUES ('scrape.job_read'), ('scrape.job_write'),
                           ('scrape.node_admin')) AS p(code)
        WHERE r.name = '团队管理员'
        ON CONFLICT DO NOTHING;
        """
    )

    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.product, app.variant_group,
          app.variant_member, app.scrape_job TO erp_app;
        GRANT SELECT, INSERT, UPDATE ON app.scrape_task, app.worker_node TO erp_app;
        GRANT SELECT, INSERT ON app.scrape_result TO erp_app;
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO erp_app;
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM app.role_permission WHERE permission_code LIKE 'scrape.%';"
        "DELETE FROM app.permission WHERE code LIKE 'scrape.%';"
    )
    for t in (
        "scrape_result",
        "scrape_task",
        "scrape_job",
        "worker_node",
        "variant_member",
        "variant_group",
        "product",
    ):
        op.execute(f"DROP TABLE IF EXISTS app.{t} CASCADE")
    op.execute("DROP SEQUENCE IF EXISTS app.master_sku_seq")
