-- 本地开发库初始化（镜像 pgvector/pgvector:pg16，vector 已随镜像提供）。
-- 生产 RDS 上由 Owner 按 EA-004 §6 验证四扩展后、R1-03 migration 幂等执行同等语句。
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

-- 三角色（本地开发口令即角色名，仅限本地；生产口令走环境/Secrets）
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'erp_migrator') THEN
    CREATE ROLE erp_migrator LOGIN PASSWORD 'erp_migrator';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'erp_app') THEN
    CREATE ROLE erp_app LOGIN PASSWORD 'erp_app';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'portal_app') THEN
    CREATE ROLE portal_app LOGIN PASSWORD 'portal_app';
  END IF;
END
$$;

GRANT ALL ON DATABASE erp_all TO erp_migrator;
GRANT CREATE, USAGE ON SCHEMA public TO erp_migrator;
-- erp_app/portal_app 的对象级授权由 migration 逐表下发（R1-03），此处不放权
GRANT USAGE ON SCHEMA public TO erp_app, portal_app;
