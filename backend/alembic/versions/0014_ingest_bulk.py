"""RS-04A 摄取升级基建：dataset_revision + 统计触发器 + trademark COPY staging

评审 round-1 B5②/round-2 采纳：
- refdata.dataset_revision：数据集修订号，**事务内递增**，替代 count+max(timestamp)
  缓存版本（后者同秒替换有碰撞窗口，且大表聚合贵）。
- 统计级触发器（FOR EACH STATEMENT）挂黑名单四表/政策/商标：**任何写入方**（导入通道、
  人工 SQL、未来 UI）都自动 bump，读侧缓存（blacklist 自动机 / L3 政策块）版本比对即失效，
  不依赖写入方自觉。
- refdata.trademark_staging：UNLOGGED（免 WAL，崩溃丢 staging 可重跑），承接 14M 商标
  流式 COPY → set-based merge（评审 B4：旧逐行 SQL 通道搬不动 14M）。
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

# 数据集 → 挂触发器的表（表 → dataset 名）
_TRACKED = (
    ("app.blacklist_brand", "blacklist"),
    ("app.blacklist_seller", "blacklist"),
    ("app.blacklist_asin", "blacklist"),
    ("app.blacklist_category", "blacklist"),
    ("refdata.prohibited_policy", "prohibited_policy"),
    ("refdata.trademark", "trademark"),
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE refdata.dataset_revision (
          dataset    text PRIMARY KEY,
          revision   bigint NOT NULL DEFAULT 1,
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        GRANT SELECT, INSERT, UPDATE ON refdata.dataset_revision TO erp_app;

        CREATE FUNCTION refdata.bump_dataset_revision() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          INSERT INTO refdata.dataset_revision (dataset) VALUES (TG_ARGV[0])
          ON CONFLICT (dataset) DO UPDATE
            SET revision = refdata.dataset_revision.revision + 1, updated_at = now();
          RETURN NULL;
        END $$;
        """
    )
    for table, dataset in _TRACKED:
        trig = f"trg_rev_{table.split('.')[1]}"
        op.execute(
            f"CREATE TRIGGER {trig} AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE"
            f" ON {table} FOR EACH STATEMENT"
            f" EXECUTE FUNCTION refdata.bump_dataset_revision('{dataset}')"
        )
    op.execute(
        """
        CREATE UNLOGGED TABLE refdata.trademark_staging (
          batch_no        int NOT NULL,
          serial_no       text NOT NULL,
          mark_text       text NOT NULL,
          mark_norm       text NOT NULL,
          status_code     text,
          is_live         boolean,
          nice_classes    smallint[],
          owner_name      text,
          filed_date      date,
          registered_date date
        );
        CREATE INDEX ix_tm_staging_batch ON refdata.trademark_staging (batch_no);
        GRANT SELECT, INSERT, DELETE, TRUNCATE ON refdata.trademark_staging TO erp_app;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS refdata.trademark_staging")
    for table, _dataset in _TRACKED:
        trig = f"trg_rev_{table.split('.')[1]}"
        op.execute(f"DROP TRIGGER IF EXISTS {trig} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS refdata.bump_dataset_revision()")
    op.execute("DROP TABLE IF EXISTS refdata.dataset_revision")
