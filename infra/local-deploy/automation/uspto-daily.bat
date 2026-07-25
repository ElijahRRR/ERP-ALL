@echo off
rem ============================================================================
rem USPTO 商标供给链日常编排（R2-12 增量3，D-Q65① 方案 A）
rem
rem 由部署机 Windows 任务计划每日 18:00 调用，串起 runbook「USPTO 商标供给链 ·
rem 日常链路」第 1-4 步：daily_update → delta 导出 → 拷入 api 容器导入 → 对账。
rem
rem 【本文件必须 CRLF】仓根 .gitattributes 已声明 `*.bat text eol=crlf` 强制检出。
rem   实证（2026-07-25）：本文件曾为 LF-only，cmd.exe 逐行吞掉前缀——
rem   `set "SECRET_FILE=..."` 被截成 `RET_FILE...` 从而变量从未赋值，
rem   `if not exist ""` 恒真 → 误报 secret 缺失 exit 10，白丢一个验收日。
rem
rem 【本文件必须纯 ASCII】机器相关的中文路径一律走 SECRET_FILE 的 ERP_COMPOSE 键，
rem   不要写进本文件。实证：硬编码的 `D:\项目文件\...` 在本文件里存成 UTF-8、
rem   被 cmd 按 GBK 读 → `D:\椤圭洰鏂囦欢\...`，路径不存在。
rem
rem 退出码：
rem   0  成功
rem   10 SECRET_FILE 不存在
rem   11 SECRET_FILE 缺 POSTGRES_PASSWORD 键
rem   12 SECRET_FILE 缺 ERP_COMPOSE 键，或该 compose 文件不存在
rem   20 delta 导出失败      21 拷入容器失败      22 ERP 导入失败
rem   其他 = 被调用命令的原始返回码
rem ============================================================================
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "SYNC_DIR=D:\walmart-trademark-sync"
set "PYTHON=%SYNC_DIR%\.venv\Scripts\python.exe"
set "SECRET_FILE=D:\erp-staging-backup\uspto-db.env"
set "OUT_DIR=%SYNC_DIR%\out"
set "LOG_DIR=D:\erp-staging-backup\logs"

rem RUN_ID 同时作为日志时间戳——不要用 %date% %time%，中文区域名在 chcp 65001 下
rem 会写成乱码（实证：`[鍛ㄦ棩 2026/07/26 0:59:35]`）。
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "RUN_ID=%%I"
set "LOG=%LOG_DIR%\uspto-daily-%RUN_ID%.log"
set "BEFORE_FILE=%TEMP%\uspto-completed-before-%RUN_ID%.txt"
set "AFTER_FILE=%TEMP%\uspto-completed-after-%RUN_ID%.txt"
set "NEW_FILE=%TEMP%\uspto-completed-new-%RUN_ID%.txt"

if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [%RUN_ID%] USPTO daily chain start>"%LOG%"

if not exist "%SECRET_FILE%" (
  echo ERROR: local secret file missing: %SECRET_FILE%>>"%LOG%"
  exit /b 10
)

rem SECRET_FILE 为 KEY=VALUE 文本，值一律不落日志。
rem   POSTGRES_PASSWORD = uspto-db 的 postgres 口令
rem   ERP_COMPOSE       = ERP-ALL 的 docker-compose.yml 绝对路径。**务必填 8.3 短路径**
rem                       （`for %%I in ("<长路径>") do @echo %%~sI` 取），纯 ASCII，
rem                       免疫任何代码页问题。
set "PGPASSWORD_LOCAL="
set "ERP_COMPOSE="
for /f "usebackq tokens=1,* delims==" %%A in ("%SECRET_FILE%") do (
  if /i "%%A"=="POSTGRES_PASSWORD" set "PGPASSWORD_LOCAL=%%B"
  if /i "%%A"=="ERP_COMPOSE" set "ERP_COMPOSE=%%B"
)
if not defined PGPASSWORD_LOCAL (
  echo ERROR: POSTGRES_PASSWORD entry missing>>"%LOG%"
  exit /b 11
)
if not defined ERP_COMPOSE (
  echo ERROR: ERP_COMPOSE entry missing in secret file>>"%LOG%"
  exit /b 12
)
if not exist "!ERP_COMPOSE!" (
  echo ERROR: compose file not found: !ERP_COMPOSE!>>"%LOG%"
  exit /b 12
)
set "COMPOSE=!ERP_COMPOSE!"

set "DB_CONN=dbname=uspto user=postgres password=!PGPASSWORD_LOCAL! host=127.0.0.1 port=5433"
set "PYTHONUTF8=1"

docker inspect -f "{{.State.Running}}" uspto-db 2>>"%LOG%" | findstr /i /x "true" >nul
if errorlevel 1 (
  echo ERROR: uspto-db is not running>>"%LOG%"
  goto :fail
)

docker exec uspto-db psql -U postgres -d uspto -Atc "SELECT source_file FROM etl_progress WHERE data_type='trademark' AND status='completed' ORDER BY source_file" >"%BEFORE_FILE%" 2>>"%LOG%"
if errorlevel 1 goto :fail

pushd "%SYNC_DIR%"
"%PYTHON%" "%SYNC_DIR%\daily_update.py" >>"%LOG%" 2>&1
set "DAILY_RC=!errorlevel!"
popd
if not "!DAILY_RC!"=="0" (
  echo ERROR: daily_update exited !DAILY_RC!>>"%LOG%"
  goto :fail
)

docker exec uspto-db psql -U postgres -d uspto -Atc "SELECT source_file FROM etl_progress WHERE data_type='trademark' AND status='completed' ORDER BY source_file" >"%AFTER_FILE%" 2>>"%LOG%"
if errorlevel 1 goto :fail

powershell -NoProfile -Command "$before=Get-Content -LiteralPath $env:BEFORE_FILE; Get-Content -LiteralPath $env:AFTER_FILE | Where-Object { $_ -notin $before -and $_ -match '^apc\d{6}\.zip$' } | Set-Content -LiteralPath $env:NEW_FILE -Encoding ASCII"
if errorlevel 1 goto :fail

for /f "usebackq delims=" %%F in ("%NEW_FILE%") do (
  call :process_file "%%F"
  if errorlevel 1 goto :fail
)

echo [RECONCILE] USPTO source>>"%LOG%"
docker exec uspto-db psql -U postgres -d uspto -P pager=off -c "SELECT count(*) AS total, max(filing_date) AS newest FROM trademarks; SELECT count(*) AS completed_files, max(source_file) AS latest_file FROM etl_progress WHERE data_type='trademark' AND status='completed';" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail

echo [RECONCILE] ERP target>>"%LOG%"
docker compose -f "%COMPOSE%" exec -T db psql -U postgres -d erp_all -P pager=off -c "SELECT count(*) AS total, max(filed_date) AS newest FROM refdata.trademark; SELECT revision FROM refdata.dataset_revision WHERE dataset='trademark';" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail

echo [%RUN_ID%] USPTO daily chain done>>"%LOG%"
call :cleanup
exit /b 0

:process_file
setlocal EnableDelayedExpansion
set "SOURCE_FILE=%~1"
set "BASE=%~n1"
set "YYMMDD=!BASE:~3!"
set "DELTA_NAME=delta-!YYMMDD!.csv"
set "DELTA=%OUT_DIR%\!DELTA_NAME!"

echo [DELTA] !SOURCE_FILE!>>"%LOG%"
docker exec uspto-db psql -U postgres -d uspto -v ON_ERROR_STOP=1 -c "\copy (SELECT t.serial_number, t.mark_identification, t.status_code, m.live_dead, (SELECT string_agg(DISTINCT c.international_code, ' ') FROM trademark_classes c WHERE c.serial_number = t.serial_number) AS nice_classes, (SELECT o.party_name FROM trademark_owners o WHERE o.serial_number = t.serial_number ORDER BY o.id LIMIT 1) AS owner_name, t.filing_date, t.registration_date FROM trademarks t LEFT JOIN status_code_mapping m ON m.status_code = t.status_code WHERE t.source_file = '!SOURCE_FILE!' AND t.mark_identification IS NOT NULL) TO STDOUT WITH CSV HEADER" >"!DELTA!" 2>>"%LOG%"
if errorlevel 1 (
  echo ERROR: delta export failed for !SOURCE_FILE!>>"%LOG%"
  endlocal & exit /b 20
)

for /f %%N in ('powershell -NoProfile -Command "$n=(Get-Content -LiteralPath '!DELTA!' | Measure-Object -Line).Lines-1; [Math]::Max($n,0)"') do set "DELTA_ROWS=%%N"
echo [DELTA] rows=!DELTA_ROWS! file=!DELTA_NAME!>>"%LOG%"

docker compose -f "%COMPOSE%" cp "!DELTA!" "api:/tmp/!DELTA_NAME!" >>"%LOG%" 2>&1
if errorlevel 1 (
  echo ERROR: docker copy failed for !SOURCE_FILE!>>"%LOG%"
  endlocal & exit /b 21
)

rem 首跑不加 --resume：manifest 不存在时 bulk_import_trademark 会硬失败
rem （tools/bulk_import_trademark.py:145 raise）。--resume 仅用于中断续跑，
rem 且须同文件 sha256 + 同 batch_size。本链每个 delta 只导一次，故永不加。
docker compose -f "%COMPOSE%" exec -T api python -m erp.tools.bulk_import_trademark --file "/tmp/!DELTA_NAME!" >>"%LOG%" 2>&1
if errorlevel 1 (
  echo ERROR: ERP import failed for !SOURCE_FILE!>>"%LOG%"
  endlocal & exit /b 22
)

docker compose -f "%COMPOSE%" exec -T api rm -f "/tmp/!DELTA_NAME!" >>"%LOG%" 2>&1
rem delta 保留 14 天供事后核对（原实现导完即删，出账对不上时无源可查）
forfiles /p "%OUT_DIR%" /m "delta-*.csv" /d -14 /c "cmd /c del /q @path" >nul 2>&1
echo [IMPORT] completed !SOURCE_FILE!>>"%LOG%"
endlocal & exit /b 0

:cleanup
del /q "%BEFORE_FILE%" "%AFTER_FILE%" "%NEW_FILE%" >nul 2>&1
set "DB_CONN="
set "PGPASSWORD_LOCAL="
exit /b 0

:fail
set "RC=%errorlevel%"
if "%RC%"=="0" set "RC=1"
echo [%RUN_ID%] USPTO daily chain failed rc=%RC%>>"%LOG%"
call :cleanup
exit /b %RC%
