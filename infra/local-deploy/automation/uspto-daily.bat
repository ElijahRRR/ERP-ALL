@echo off
rem ============================================================================
rem USPTO trademark supply chain - daily orchestration (R2-12 / D-Q65 option A)
rem
rem Invoked by Windows Task Scheduler at 18:00 daily. Runs runbook section
rem "USPTO trademark supply chain / daily flow" steps 1-4:
rem   daily_update -> delta export -> copy into api container + import -> reconcile
rem
rem HARD RULES FOR THIS FILE (both were violated once and cost an acceptance day):
rem   1. CRLF ONLY. Repo .gitattributes pins "*.bat text eol=crlf". A LF-only
rem      .bat makes cmd.exe eat the prefix of every line, so
rem      set "SECRET_FILE=..." silently never runs and "if not exist" sees an
rem      empty path -> false "secret file missing" + exit 10.
rem   2. ASCII ONLY. This file is stored UTF-8 but cmd.exe reads it as the OEM
rem      codepage, so any non-ASCII path turns into mojibake and stops existing.
rem      Machine-specific paths belong in SECRET_FILE (key ERP_COMPOSE), never here.
rem   3. NEVER pipe Docker CLI output into "findstr /x". Docker ends its output
rem      with a bare LF and findstr exact-match fails on LF-only lines, so a
rem      healthy container reads as stopped. Capture with "for /f" and compare.
rem   4. ALWAYS wrap log appends as (echo ...)>>"%LOG%". Without the parens a
rem      value ending in a digit is parsed as a redirection descriptor, e.g.
rem      "rc=%RC%>>file" becomes "rc=" plus a stream-1 redirect and the code is
rem      lost -- exactly the diagnostic you need when something fails.
rem
rem See ./README.md for the full Chinese notes, field history and self-checks.
rem
rem Exit codes:
rem   0  ok
rem   10 SECRET_FILE not found
rem   11 SECRET_FILE has no POSTGRES_PASSWORD key
rem   12 SECRET_FILE has no ERP_COMPOSE key, or that compose file is missing
rem   20 delta export failed   21 copy into container failed   22 ERP import failed
rem   otherwise = raw return code of the failing command
rem ============================================================================
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "SYNC_DIR=D:\walmart-trademark-sync"
set "PYTHON=%SYNC_DIR%\.venv\Scripts\python.exe"
set "SECRET_FILE=D:\erp-staging-backup\uspto-db.env"
set "OUT_DIR=%SYNC_DIR%\out"
set "LOG_DIR=D:\erp-staging-backup\logs"

rem RUN_ID doubles as the log timestamp. Do NOT use %date% %time%: on a
rem Chinese locale the weekday name renders as mojibake under chcp 65001.
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "RUN_ID=%%I"
set "LOG=%LOG_DIR%\uspto-daily-%RUN_ID%.log"
set "BEFORE_FILE=%TEMP%\uspto-completed-before-%RUN_ID%.txt"
set "AFTER_FILE=%TEMP%\uspto-completed-after-%RUN_ID%.txt"
set "NEW_FILE=%TEMP%\uspto-completed-new-%RUN_ID%.txt"

if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

(echo [%RUN_ID%] USPTO daily chain start)>"%LOG%"

if not exist "%SECRET_FILE%" (
  (echo ERROR: local secret file missing: %SECRET_FILE%)>>"%LOG%"
  exit /b 10
)

rem SECRET_FILE is plain KEY=VALUE text. Values are never logged.
rem   POSTGRES_PASSWORD = postgres password of the uspto-db container
rem   ERP_COMPOSE       = absolute path of ERP-ALL docker-compose.yml.
rem                       MUST be the 8.3 short path (get it with:
rem                       for %%I in ("<long path>") do @echo %%~sI) so the
rem                       value stays pure ASCII and codepage-proof.
set "PGPASSWORD_LOCAL="
set "ERP_COMPOSE="
for /f "usebackq tokens=1,* delims==" %%A in ("%SECRET_FILE%") do (
  if /i "%%A"=="POSTGRES_PASSWORD" set "PGPASSWORD_LOCAL=%%B"
  if /i "%%A"=="ERP_COMPOSE" set "ERP_COMPOSE=%%B"
)
if not defined PGPASSWORD_LOCAL (
  (echo ERROR: POSTGRES_PASSWORD entry missing)>>"%LOG%"
  exit /b 11
)
if not defined ERP_COMPOSE (
  (echo ERROR: ERP_COMPOSE entry missing in secret file)>>"%LOG%"
  exit /b 12
)
if not exist "!ERP_COMPOSE!" (
  (echo ERROR: compose file not found: !ERP_COMPOSE!)>>"%LOG%"
  exit /b 12
)
set "COMPOSE=!ERP_COMPOSE!"

set "DB_CONN=dbname=uspto user=postgres password=!PGPASSWORD_LOCAL! host=127.0.0.1 port=5433"
set "PYTHONUTF8=1"

rem Do NOT pipe docker output into "findstr /x". The Docker CLI ends its output
rem with a bare LF, and findstr exact-line match does not match an LF-only line
rem on Windows -- it reports "not running" for a perfectly healthy container.
rem Field-proven 2026-07-26: docker emitted 74 72 75 65 0A ("true"+LF) yet the
rem piped findstr exited 1, while "echo true | findstr /i /x true" exited 0.
rem "for /f" tokenizes on both CR and LF, so capture the value and compare it.
set "USPTO_RUNNING="
for /f "usebackq delims=" %%S in (`docker inspect -f "{{.State.Running}}" uspto-db 2^>^>"%LOG%"`) do set "USPTO_RUNNING=%%S"
if /i not "!USPTO_RUNNING!"=="true" (
  (echo ERROR: uspto-db is not running - docker inspect returned "!USPTO_RUNNING!")>>"%LOG%"
  goto :fail
)

docker exec uspto-db psql -U postgres -d uspto -Atc "SELECT source_file FROM etl_progress WHERE data_type='trademark' AND status='completed' ORDER BY source_file" >"%BEFORE_FILE%" 2>>"%LOG%"
if errorlevel 1 goto :fail

pushd "%SYNC_DIR%"
"%PYTHON%" "%SYNC_DIR%\daily_update.py" >>"%LOG%" 2>&1
set "DAILY_RC=!errorlevel!"
popd
if not "!DAILY_RC!"=="0" (
  (echo ERROR: daily_update exited !DAILY_RC!)>>"%LOG%"
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

(echo [RECONCILE] USPTO source)>>"%LOG%"
docker exec uspto-db psql -U postgres -d uspto -P pager=off -c "SELECT count(*) AS total, max(filing_date) AS newest FROM trademarks; SELECT count(*) AS completed_files, max(source_file) AS latest_file FROM etl_progress WHERE data_type='trademark' AND status='completed';" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail

(echo [RECONCILE] ERP target)>>"%LOG%"
docker compose -f "%COMPOSE%" exec -T db psql -U postgres -d erp_all -P pager=off -c "SELECT count(*) AS total, max(filed_date) AS newest FROM refdata.trademark; SELECT revision FROM refdata.dataset_revision WHERE dataset='trademark';" >>"%LOG%" 2>&1
if errorlevel 1 goto :fail

(echo [%RUN_ID%] USPTO daily chain done)>>"%LOG%"
call :cleanup
exit /b 0

:process_file
setlocal EnableDelayedExpansion
set "SOURCE_FILE=%~1"
set "BASE=%~n1"
set "YYMMDD=!BASE:~3!"
set "DELTA_NAME=delta-!YYMMDD!.csv"
set "DELTA=%OUT_DIR%\!DELTA_NAME!"

(echo [DELTA] !SOURCE_FILE!)>>"%LOG%"
docker exec uspto-db psql -U postgres -d uspto -v ON_ERROR_STOP=1 -c "\copy (SELECT t.serial_number, t.mark_identification, t.status_code, m.live_dead, (SELECT string_agg(DISTINCT c.international_code, ' ') FROM trademark_classes c WHERE c.serial_number = t.serial_number) AS nice_classes, (SELECT o.party_name FROM trademark_owners o WHERE o.serial_number = t.serial_number ORDER BY o.id LIMIT 1) AS owner_name, t.filing_date, t.registration_date FROM trademarks t LEFT JOIN status_code_mapping m ON m.status_code = t.status_code WHERE t.source_file = '!SOURCE_FILE!' AND t.mark_identification IS NOT NULL) TO STDOUT WITH CSV HEADER" >"!DELTA!" 2>>"%LOG%"
if errorlevel 1 (
  (echo ERROR: delta export failed for !SOURCE_FILE!)>>"%LOG%"
  endlocal & exit /b 20
)

rem Pass the path via the environment: nesting single quotes inside a for/f
rem '...' command is ambiguous to cmd, and a bare "|" inside it needs escaping.
rem Counting via @(...).Count avoids the pipe entirely.
set "DELTA_PATH=!DELTA!"
for /f %%N in ('powershell -NoProfile -Command "$n=@(Get-Content -LiteralPath $env:DELTA_PATH).Count-1; [Math]::Max($n,0)"') do set "DELTA_ROWS=%%N"
(echo [DELTA] rows=!DELTA_ROWS! file=!DELTA_NAME!)>>"%LOG%"

docker compose -f "%COMPOSE%" cp "!DELTA!" "api:/tmp/!DELTA_NAME!" >>"%LOG%" 2>&1
if errorlevel 1 (
  (echo ERROR: docker copy failed for !SOURCE_FILE!)>>"%LOG%"
  endlocal & exit /b 21
)

rem Never pass --resume here: bulk_import_trademark hard-fails when the
rem manifest is absent (tools/bulk_import_trademark.py:145). --resume is only
rem for resuming an interrupted run and needs the same sha256 + batch_size.
rem This chain imports each delta exactly once.
docker compose -f "%COMPOSE%" exec -T api python -m erp.tools.bulk_import_trademark --file "/tmp/!DELTA_NAME!" >>"%LOG%" 2>&1
if errorlevel 1 (
  (echo ERROR: ERP import failed for !SOURCE_FILE!)>>"%LOG%"
  endlocal & exit /b 22
)

docker compose -f "%COMPOSE%" exec -T api rm -f "/tmp/!DELTA_NAME!" >>"%LOG%" 2>&1
rem Keep deltas 14 days for post-hoc checks (the original deleted them
rem immediately, leaving no source to audit when counts disagreed).
forfiles /p "%OUT_DIR%" /m "delta-*.csv" /d -14 /c "cmd /c del /q @path" >nul 2>&1
(echo [IMPORT] completed !SOURCE_FILE!)>>"%LOG%"
endlocal & exit /b 0

:cleanup
del /q "%BEFORE_FILE%" "%AFTER_FILE%" "%NEW_FILE%" >nul 2>&1
set "DB_CONN="
set "PGPASSWORD_LOCAL="
exit /b 0

:fail
set "RC=%errorlevel%"
if "%RC%"=="0" set "RC=1"
(echo [%RUN_ID%] USPTO daily chain failed rc=%RC%)>>"%LOG%"
call :cleanup
exit /b %RC%
