"""listing 域 + gtin_pool：上架最小闭环的全部表

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-10

依据 specs/001-domain-model/03-catalog.md §gtin_pool、06-listing-pricing.md
（listing / listing_state_history / feed / feed_item / listing_spec /
listing_error_catalog / maintenance_task；pricing 两表随 R2 定价引擎）。
总账铁律落位：feed.channel_feed_id NULL=verify_pending（永不盲重试）；
headline 仅展示、对账以 feed_item 为准；gtin used 永不回收。
"""

from alembic import op

revision = "0009"
down_revision = "0008"
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
    # ── gtin_pool ──
    op.execute(
        """
        CREATE TABLE app.gtin_pool (
          id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id         bigint NOT NULL REFERENCES app.team(id),
          gtin            text NOT NULL UNIQUE CHECK (gtin ~ '^[0-9]{12,13}$'),
          gtin_kind       text NOT NULL
                            CONSTRAINT ck_gtin_kind CHECK (gtin_kind IN ('upc_a','ean_13')),
          status          text NOT NULL DEFAULT 'free'
                            CONSTRAINT ck_gtin_status
                            CHECK (status IN ('free','held','used','conflict','invalid')),
          source          text NOT NULL
                            CONSTRAINT ck_gtin_source
                            CHECK (source IN ('generator_import','feishu_import','purchased')),
          held_listing_id bigint,
          held_at         timestamptz,
          used_listing_id bigint,
          used_at         timestamptz,
          last_check_at   timestamptz,
          check_result    jsonb,
          import_job_id   bigint,
          created_at      timestamptz NOT NULL DEFAULT now(),
          updated_at      timestamptz NOT NULL DEFAULT now(),
          created_by      bigint
        );
        CREATE INDEX ix_gtin_pool ON app.gtin_pool (team_id, gtin_kind, status);
        """
    )
    op.execute(TOUCH.format(t="gtin_pool"))
    op.execute(TEAM_RLS.format(t="gtin_pool"))

    # ── listing ──
    op.execute(
        """
        CREATE TABLE app.listing (
          id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id            bigint NOT NULL REFERENCES app.team(id),
          store_id           bigint NOT NULL REFERENCES app.store(id),
          product_id         bigint NOT NULL REFERENCES app.product(id),
          offer_mode         text NOT NULL
                               CONSTRAINT ck_listing_mode CHECK (offer_mode IN ('build','match')),
          channel_sku        text NOT NULL,
          gtin               text,
          status             text NOT NULL DEFAULT 'draft'
                               CONSTRAINT ck_listing_status CHECK (status IN
                               ('draft','queued','submitted','processing','published','live',
                                'degraded','delist_pending','delisted','failed','retired')),
          error_code         text,
          is_locked          boolean NOT NULL DEFAULT false,
          wpid               text,
          channel_item_id    text,
          end_date           date NOT NULL DEFAULT '2049-12-31',
          current_price      numeric(12,2),
          currency           char(3) NOT NULL DEFAULT 'USD',
          current_inventory  int NOT NULL DEFAULT 5,
          published_at       timestamptz,
          delisted_at        timestamptz,
          last_synced_at     timestamptz,
          last_maintained_at timestamptz,
          created_at         timestamptz NOT NULL DEFAULT now(),
          updated_at         timestamptz NOT NULL DEFAULT now(),
          created_by         bigint,
          CONSTRAINT uq_listing UNIQUE (store_id, channel_sku)
        );
        CREATE INDEX ix_listing_team ON app.listing (team_id, status);
        CREATE INDEX ix_listing_store ON app.listing (store_id, status);
        CREATE INDEX ix_listing_product ON app.listing (product_id, store_id);
        CREATE INDEX ix_listing_maintain ON app.listing (status, last_maintained_at)
          WHERE status = 'live';
        CREATE INDEX ix_listing_error ON app.listing (error_code)
          WHERE status IN ('failed','degraded');
        """
    )
    op.execute(TOUCH.format(t="listing"))
    op.execute(TEAM_RLS.format(t="listing"))

    # ── listing_state_history（月分区）──
    op.execute(
        """
        CREATE TABLE app.listing_state_history (
          id          bigint GENERATED ALWAYS AS IDENTITY,
          listing_id  bigint NOT NULL,
          team_id     bigint NOT NULL,
          from_status text NOT NULL,
          to_status   text NOT NULL,
          reason_code text,
          detail      jsonb NOT NULL DEFAULT '{}',
          actor_type  text NOT NULL
                        CONSTRAINT ck_lsh_actor CHECK (actor_type IN ('user','system')),
          actor_id    bigint,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (id, occurred_at)
        ) PARTITION BY RANGE (occurred_at);
        CREATE INDEX ix_lsh_listing ON app.listing_state_history (listing_id, occurred_at DESC);
        SELECT app.ensure_month_partitions('app.listing_state_history');
        ALTER TABLE app.listing_state_history ENABLE ROW LEVEL SECURITY;
        CREATE POLICY lsh_sel ON app.listing_state_history FOR SELECT
          USING (team_id = app.current_team() OR app.is_super());
        CREATE POLICY lsh_ins ON app.listing_state_history FOR INSERT
          WITH CHECK (team_id = app.current_team() OR app.is_super());
        """
    )

    # ── feed ──
    op.execute(
        """
        CREATE TABLE app.feed (
          id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id          bigint NOT NULL REFERENCES app.team(id),
          store_id         bigint NOT NULL REFERENCES app.store(id),
          channel_feed_id  text UNIQUE,
          feed_kind        text NOT NULL
                             CONSTRAINT ck_feed_kind CHECK (feed_kind IN
                             ('item_build','item_match','price','inventory','delete','lag_time')),
          status           text NOT NULL DEFAULT 'building'
                             CONSTRAINT ck_feed_status CHECK (status IN
                             ('building','submitting','verify_pending','submitted',
                              'processing','processed','partial','error','lost')),
          item_count       int NOT NULL DEFAULT 0,
          headline         jsonb,
          submitted_at     timestamptz,
          last_polled_at   timestamptz,
          completed_at     timestamptz,
          poll_attempts    int NOT NULL DEFAULT 0,
          raw_response_ref text,
          created_at       timestamptz NOT NULL DEFAULT now(),
          updated_at       timestamptz NOT NULL DEFAULT now(),
          created_by       bigint
        );
        CREATE INDEX ix_feed_store ON app.feed (store_id, feed_kind, created_at DESC);
        CREATE INDEX ix_feed_poll ON app.feed (status)
          WHERE status IN ('verify_pending','submitted','processing');
        """
    )
    op.execute(TOUCH.format(t="feed"))
    op.execute(TEAM_RLS.format(t="feed"))

    # ── feed_item（月分区）──
    op.execute(
        """
        CREATE TABLE app.feed_item (
          id          bigint GENERATED ALWAYS AS IDENTITY,
          feed_id     bigint NOT NULL,
          team_id     bigint NOT NULL,
          listing_id  bigint NOT NULL,
          channel_sku text NOT NULL,
          status      text NOT NULL DEFAULT 'pending'
                        CONSTRAINT ck_feed_item_status
                        CHECK (status IN ('pending','success','error')),
          error_code  text,
          error_msg   text,
          raw         jsonb,
          created_at  timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at);
        CREATE INDEX ix_feed_item_feed ON app.feed_item (feed_id);
        CREATE INDEX ix_feed_item_listing ON app.feed_item (listing_id, created_at DESC);
        CREATE INDEX ix_feed_item_error ON app.feed_item (error_code, created_at)
          WHERE status = 'error';
        SELECT app.ensure_month_partitions('app.feed_item');
        ALTER TABLE app.feed_item ENABLE ROW LEVEL SECURITY;
        CREATE POLICY feed_item_sel ON app.feed_item FOR SELECT
          USING (team_id = app.current_team() OR app.is_super());
        CREATE POLICY feed_item_ins ON app.feed_item FOR INSERT
          WITH CHECK (team_id = app.current_team() OR app.is_super());
        CREATE POLICY feed_item_upd ON app.feed_item FOR UPDATE
          USING (team_id = app.current_team() OR app.is_super())
          WITH CHECK (team_id = app.current_team() OR app.is_super());
        """
    )

    # ── listing_spec（可复算缓存）──
    op.execute(
        """
        CREATE TABLE app.listing_spec (
          id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id        bigint NOT NULL,
          product_id     bigint NOT NULL REFERENCES app.product(id),
          offer_mode     text NOT NULL
                           CONSTRAINT ck_spec_mode CHECK (offer_mode IN ('build','match')),
          wpt            text NOT NULL,
          spec_version   text NOT NULL DEFAULT 'v5',
          payload        jsonb NOT NULL,
          cert_overrides jsonb NOT NULL DEFAULT '{}',
          build_hash     text NOT NULL,
          built_at       timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_listing_spec UNIQUE (product_id, offer_mode, build_hash)
        );
        ALTER TABLE app.listing_spec ENABLE ROW LEVEL SECURITY;
        CREATE POLICY listing_spec_all ON app.listing_spec FOR ALL
          USING (team_id = app.current_team() OR app.is_super())
          WITH CHECK (team_id = app.current_team() OR app.is_super());
        """
    )

    # ── listing_error_catalog（全局字典）──
    op.execute(
        """
        CREATE TABLE app.listing_error_catalog (
          error_code  text PRIMARY KEY,
          category    text NOT NULL,
          title       text NOT NULL,
          disposition text NOT NULL
                        CONSTRAINT ck_error_disposition CHECK (disposition IN
                        ('auto_retry','backoff_retry','rebuild_spec','skip','manual','fatal')),
          max_retries int NOT NULL DEFAULT 3,
          notes       text,
          enabled     boolean NOT NULL DEFAULT true,
          updated_by  bigint,
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE TRIGGER listing_error_catalog_touch BEFORE UPDATE ON app.listing_error_catalog
          FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
        ALTER TABLE app.listing_error_catalog ENABLE ROW LEVEL SECURITY;
        CREATE POLICY error_catalog_sel ON app.listing_error_catalog FOR SELECT USING (true);
        CREATE POLICY error_catalog_write ON app.listing_error_catalog FOR ALL
          USING (app.is_super()) WITH CHECK (app.is_super());
        CREATE POLICY error_catalog_draft_ins ON app.listing_error_catalog FOR INSERT
          WITH CHECK (true);
        """
    )

    # ── maintenance_task ──
    op.execute(
        """
        CREATE TABLE app.maintenance_task (
          id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id      bigint NOT NULL REFERENCES app.team(id),
          store_id     bigint NOT NULL REFERENCES app.store(id),
          listing_id   bigint NOT NULL,
          task_kind    text NOT NULL
                         CONSTRAINT ck_mt_kind CHECK (task_kind IN
                         ('price_sync','inventory_sync','title_fix','end_date_renewal',
                          'delist','relist','unlock_probe')),
          status       text NOT NULL DEFAULT 'scheduled'
                         CONSTRAINT ck_mt_status CHECK (status IN
                         ('scheduled','running','done','failed','skipped')),
          priority     smallint NOT NULL DEFAULT 100,
          scheduled_at timestamptz NOT NULL,
          started_at   timestamptz,
          finished_at  timestamptz,
          result       jsonb,
          error        text,
          created_by   bigint,
          created_at   timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_mt_pick ON app.maintenance_task (status, priority, scheduled_at)
          WHERE status = 'scheduled';
        CREATE INDEX ix_mt_listing ON app.maintenance_task (listing_id, task_kind, created_at DESC);
        """
    )
    op.execute(TEAM_RLS.format(t="maintenance_task"))

    # ── 种子：错误字典基线（渠道实战码随 A152 真调补录；未登记码运行时自动插草稿）──
    op.execute(
        """
        INSERT INTO app.listing_error_catalog
          (error_code, category, title, disposition, max_retries, notes) VALUES
        ('ERP_SPEC_BUILD_FAILED', '系统', 'Spec 构建失败', 'manual', 0, '检查产品属性完整性'),
        ('ERP_QUOTA_EXHAUSTED', '限流', '店铺配额不足', 'backoff_retry', 5, '次日配额恢复自动重投'),
        ('ERP_GATEWAY_DRY_RUN', '系统', 'dry-run 模式（未真实提交）', 'skip', 0,
         '渠道网关处于 dry_run；切 live_test/live 后重投'),
        ('WM_ASYNC_REVIEW', '渠道审核', '渠道异步审核中(伪错误)', 'backoff_retry', 10,
         '总账规则: async-review fake errors——等待渠道复审, 禁止改 spec'),
        ('WM_SKU_LOCKED', '渠道锁定', 'SKU 被渠道锁定', 'manual', 0,
         '专用解锁流程(unlock_probe), 维护类操作全部跳过');
        """
    )

    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE ON app.gtin_pool, app.listing, app.feed,
          app.feed_item, app.listing_spec, app.maintenance_task,
          app.listing_error_catalog TO erp_app;
        GRANT SELECT, INSERT ON app.listing_state_history TO erp_app;
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO erp_app;
        """
    )


def downgrade() -> None:
    for t in (
        "maintenance_task",
        "listing_error_catalog",
        "listing_spec",
        "feed_item",
        "feed",
        "listing_state_history",
        "listing",
        "gtin_pool",
    ):
        op.execute(f"DROP TABLE IF EXISTS app.{t} CASCADE")
