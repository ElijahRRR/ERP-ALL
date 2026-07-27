-- 本地开发库初始化（镜像 pgvector/pgvector:pg16，vector 已随镜像提供）。
-- 生产 RDS 上由 Owner 按 EA-004 §6 验证四扩展后、R1-03 migration 幂等执行同等语句。
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

-- 三业务角色的建号与授权见同目录 02-roles.sh：口令要从环境变量取，而
-- docker-entrypoint-initdb.d 下的 *.sql 是 psql 直接喂的、拿不到环境变量，
-- 故那段迁到 .sh（RS-02a，2026-07-27；此前口令写死＝角色名）。
