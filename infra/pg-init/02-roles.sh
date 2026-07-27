#!/bin/sh
# 三业务角色建号 + 授权（RS-02a / D-Q68，2026-07-27）。
#
# 此前这段在 01-extensions.sql 里，口令写死成角色名（erp_app / erp_migrator /
# portal_app）——库一旦被暴露，这三个口令等于没有。改从环境变量取，缺一即拒绝初始化。
#
# ⚠️ **只在空数据卷首次 initdb 时执行**。卷已存在时本文件根本不会被调用，改 .env 里的
# 口令不会真的改密——既有库要改密码必须在运行库上 ALTER ROLE，见
# .agent/evidence/RS-02a/deploy-rotate-secrets.md 的执行顺序。
set -eu

: "${ERP_APP_DB_PASSWORD:?02-roles.sh: 缺 ERP_APP_DB_PASSWORD}"
: "${ERP_MIGRATOR_DB_PASSWORD:?02-roles.sh: 缺 ERP_MIGRATOR_DB_PASSWORD}"
: "${PORTAL_APP_DB_PASSWORD:?02-roles.sh: 缺 PORTAL_APP_DB_PASSWORD}"

# 口令经 psql 变量传入、再由 format(%L) 转义成 SQL 字面量 → 不做字符串拼接，
# 口令里含引号也不会拼出可执行的 SQL。
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_pw="$ERP_APP_DB_PASSWORD" \
  -v migrator_pw="$ERP_MIGRATOR_DB_PASSWORD" \
  -v portal_pw="$PORTAL_APP_DB_PASSWORD" <<'EOSQL'
SELECT format('CREATE ROLE erp_migrator LOGIN PASSWORD %L', :'migrator_pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'erp_migrator')
\gexec

SELECT format('CREATE ROLE erp_app LOGIN PASSWORD %L', :'app_pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'erp_app')
\gexec

SELECT format('CREATE ROLE portal_app LOGIN PASSWORD %L', :'portal_pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'portal_app')
\gexec

GRANT ALL ON DATABASE erp_all TO erp_migrator;
GRANT CREATE, USAGE ON SCHEMA public TO erp_migrator;
-- erp_app/portal_app 的对象级授权由 migration 逐表下发（R1-03），此处不放权
GRANT USAGE ON SCHEMA public TO erp_app, portal_app;
EOSQL
