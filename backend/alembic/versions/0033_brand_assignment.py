"""R2-07 07b：brand_assignment 品牌店铺占用表 + 封店提醒调度种子

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-17

- brand_assignment（specs/001 §03 图纸 :72-89 逐列）：build 上架分配店铺时自动
  upsert 占用，封店工作流批量 released（incident_id 回链）。部分唯一
  uq_brand_occupied (team_id, brand_norm) WHERE status='occupied' —— 同一品牌
  团队内同时刻只占一店（防跨店关联同品牌）；released 行不占唯一槽、历史保留。
- 权限点 catalog.brand_read / catalog.brand_release（0002 未预埋 catalog 品牌面）：
  种入 permission 并挂模板「团队管理员」+ 既有团队同名复制角色（同 0031 多点范式）。
- schedule 种子 suspension_reminder：封店后观察放款/写申诉信定时提醒（D-Q33/§02:186），
  config.remind_days（零硬编码，运营可编辑）；ON CONFLICT (code) DO NOTHING。
"""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

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
    # ── brand_assignment 品牌店铺占用（§03 :74-86 逐列）──
    op.execute(
        """
        CREATE TABLE app.brand_assignment (
          id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          team_id        bigint NOT NULL,
          brand_norm     text NOT NULL,
          brand_display  text NOT NULL,
          store_id       bigint NOT NULL REFERENCES app.store(id),
          status         text NOT NULL DEFAULT 'occupied'
                           CONSTRAINT ck_brand_assignment_status
                           CHECK (status IN ('occupied','released')),
          assigned_at    timestamptz NOT NULL DEFAULT now(),
          released_at    timestamptz,
          release_reason text CONSTRAINT ck_brand_assignment_reason
                           CHECK (release_reason IN ('suspension','manual','store_closed')),
          incident_id    bigint REFERENCES app.store_incident(id),
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now()
        );
        -- 部分唯一：一个品牌团队内同时刻只占一店；released 行不占唯一槽。
        CREATE UNIQUE INDEX uq_brand_occupied
          ON app.brand_assignment (team_id, brand_norm) WHERE status = 'occupied';
        -- 封店批量释放扫描（按 team+store 圈定 occupied 行）。
        CREATE INDEX ix_brand_assignment_release
          ON app.brand_assignment (team_id, store_id, status);
        """
    )
    op.execute(TOUCH.format(t="brand_assignment"))
    op.execute(TEAM_RLS.format(t="brand_assignment"))
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.brand_assignment TO erp_app;")

    # ── 权限点 catalog.brand_read / catalog.brand_release（0002 未预埋）──
    op.execute(
        """
        INSERT INTO app.permission (code, module, name) VALUES
          ('catalog.brand_read', 'catalog', '品牌占用查看'),
          ('catalog.brand_release', 'catalog', '品牌占用手动释放')
        ON CONFLICT (code) DO NOTHING;
        INSERT INTO app.role_permission (role_id, permission_code)
        SELECT r.id, p.code FROM app.role r
        JOIN (VALUES ('团队管理员', 'catalog.brand_read'),
                     ('团队管理员', 'catalog.brand_release')) AS p(role_name, code)
          ON r.name = p.role_name
        WHERE NOT EXISTS (SELECT 1 FROM app.role_permission rp
                          WHERE rp.role_id = r.id AND rp.permission_code = p.code);
        """
    )

    # ── 封店提醒调度种子（D-Q33/§02:186；每日一次，config.remind_days 运营可编辑）──
    op.execute(
        """
        INSERT INTO app.schedule (code, description, cron, config) VALUES
          ('suspension_reminder',
           '封店提醒节奏（未解封的 suspension 事件按 remind_days 周期提醒观察放款/写申诉信）',
           '0 9 * * *', '{"remind_days": 7}')
        ON CONFLICT (code) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM app.schedule WHERE code = 'suspension_reminder'")
    op.execute(
        "DELETE FROM app.role_permission"
        " WHERE permission_code IN ('catalog.brand_read','catalog.brand_release')"
    )
    op.execute(
        "DELETE FROM app.permission WHERE code IN ('catalog.brand_read','catalog.brand_release')"
    )
    op.execute("DROP TABLE IF EXISTS app.brand_assignment CASCADE")
