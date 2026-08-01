# PR #50 挂起清场单 v2（十五轮 Z1 + 十六轮 W1/W2/W3；独立于已作废的 deploy-verify v14）

> **v2 变更**：W1——C5 运维资产检查改在**切换之前**对 `origin/main` 用 `git cat-file -e`
> 验（原 Test-Path 排在 checkout 之后且对已跟踪文件永真，连切换失败都测不出）；切换后
> HEAD 与 origin/main **锚定等值**。W2——buyer_account 删除补 id 卫（⓪-2 原则回归：
> 宁可漏删停下来问）。W3——本单用 C 编号，`xrefcheck` 无机检覆盖（其水印如实报
> 「一条都没查」）；C2→C4 顺序、C3 前置、回 C2 三处交叉引用已人工核并经十六轮独立复核。
> nit——C5 版本判据改「= main 链尖端」不写死快照值。

> **给部署 AI。整段可粘贴，逐步执行，每步贴回输出。**
> 背景：③闸挂起（Owner 单租户化裁定），⑫清理随之未跑——本单只做三件事：
> **清 R13T 测试族 → 降回 0047 → 切回 main 常驻**。把现场还原成「PR #50 从未来过」，
> 为后续按新图纸重写迁移（revision id 可能复用）扫清地基。
> 铁律照旧：**audit_log 一行不删**；只删 R13T 前缀自造数据；清理单事务
> `-1 -v ON_ERROR_STOP=1`；不改码不 push；不输出密钥。
> 现场已知量（v13/v14 回执钉下，现场不同以现场为准并记回执）：渠道订单 18-21、
> product 2494-2497、执行单 7-10、买家账号 id 1/2/3/4/5/6、实例 id 1-5（全 version=R13T）、
> `R13T-TEAM-B` id=14、`<TEAM_ID>`=⑤所用验收团队 id。

## C1 预检

```powershell
cd D:\项目文件\ERP-ALL
git log --oneline -1
git status --porcelain
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT version_num FROM alembic_version;"
```

**判据**：工作树无 tracked 脏（未跟踪项可留）；`version_num=0046`（新链，v8③所建）。
分支尖端 sha 记回执（迁移文件自 `e725c67` 后未变过，仅 `.agent/` 提交——降级用当前
检出文件即与现场所应用内容一致）。

## C2 清 R13T 族（单事务；顺序照删依赖；**必须在降级之前**——PO7 带
`backfill_actor_kind='plugin'`，不清则 0045 降级守卫硬失败，那是守卫在正确工作）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -1 -v ON_ERROR_STOP=1 -c "DELETE FROM app.procurement_logistics_event WHERE procurement_order_id IN (7,8,9,10); DELETE FROM app.procurement_order WHERE id IN (7,8,9,10) AND order_id IN (SELECT id FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%'); DELETE FROM app.order_check WHERE order_id IN (SELECT id FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%' AND id IN (18,19,20,21)); DELETE FROM app.order_line WHERE order_id IN (SELECT id FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%' AND id IN (18,19,20,21)); DELETE FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%' AND id IN (18,19,20,21); DELETE FROM app.product WHERE source_ref LIKE 'R13T%' AND id IN (2494,2495,2496,2497); DELETE FROM app.plugin_instance WHERE version = 'R13T'; DELETE FROM app.buyer_account WHERE (label LIKE 'R13T-%' OR external_customer_id LIKE 'R13T%') AND id IN (1,2,3,4,5,6); DELETE FROM app.notification WHERE dedupe_key LIKE 'plugin.%'; DELETE FROM app.team_config WHERE team_id = <TEAM_ID> AND key = 'procurement.plugin_dispatch'; DELETE FROM app.team WHERE name = 'R13T-TEAM-B';"
```

**判据（DELETE 行数逐条记回执并比对）**：`procurement_order=4`、`channel_order=4`、
`product=4`、`plugin_instance=5`、`buyer_account=6`、`notification>=5`（含 pending_claim
C/D/F/TB、flood、backfill_check、no_asin 等，照实记）、`team_config=1`、`team=1`；
logistics_event/order_check/order_line 照实记。
> notification 按 `plugin.%` 圈定的依据＝十/十一轮已核三条（发射点全前缀、其它生产者
> 冒号式 key、R-0 枚举实测无历史行），不依赖对象占位符。

## C3 残留核对（六清单全 0；在降级**之前**跑——降级后新表不存在没法查）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%') AS r13t_orders, (SELECT count(*) FROM app.buyer_account WHERE label LIKE 'R13T-%' OR external_customer_id LIKE 'R13T%') AS r13t_accounts, (SELECT count(*) FROM app.plugin_instance WHERE version = 'R13T') AS r13t_instances, (SELECT count(*) FROM app.team WHERE name = 'R13T-TEAM-B') AS r13t_team, (SELECT count(*) FROM app.team_config WHERE team_id = <TEAM_ID> AND key = 'procurement.plugin_dispatch') AS r13t_cfg, (SELECT count(*) FROM app.notification WHERE dedupe_key LIKE 'plugin.%') AS r13t_notifs;"
```

**判据**：六项全 0。非 0 → 停下来贴现场，不进 C4。

## C4 降回 0047（迁移可逆的实跑；migrate 镜像即 v8③所建，无需重建）

```powershell
docker compose -f infra/docker-compose.yml run --rm migrate alembic downgrade 0047
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT version_num FROM alembic_version) AS version_num, (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'app' AND table_name IN ('buyer_account','plugin_instance','procurement_logistics_event')) AS new_tables_left, (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'app' AND table_name = 'procurement_order' AND column_name IN ('exception_kind','buyer_account_id','tax_amount')) AS new_cols_left, (SELECT count(*) FROM app.permission) AS perms_now;"
```

**判据**：`version_num=0047`、`new_tables_left=0`、`new_cols_left=0`、`perms_now=59`
（=①基线值，+3 权限种子随降级回收）。若降级在 ADD CONSTRAINT 硬失败 → C2 有漏行，
回 C2 复查，**不许改词表硬闯**。

## C5 切回 main 常驻（部署机标准姿态；先做运维资产在位检查）

```powershell
git fetch origin main
git cat-file -e origin/main:infra/local-deploy/automation/uspto-daily.bat; "bat_ok=$?"
git cat-file -e origin/main:infra/local-deploy/automation/README.md; "readme_ok=$?"
git cat-file -e origin/main:.gitattributes; "attrs_ok=$?"
git checkout main
git rev-parse HEAD
git rev-parse origin/main
docker compose -f infra/docker-compose.yml build api frontend migrate
docker compose -f infra/docker-compose.yml run --rm migrate
docker compose -f infra/docker-compose.yml up -d --force-recreate api beat frontend
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT version_num FROM alembic_version;"
Start-Sleep -Seconds 8
$r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/healthz" -UseBasicParsing -TimeoutSec 5
$r.StatusCode
```

**判据**：三个 `_ok` 全 True（在**切换之前**对 `origin/main` 验——三行不齐**不许切**，
停下来问；此查的是仓库内运维资产，部署机上的 Windows 计划任务本体是否在位属另一项
检查、不在本单）；切换后两条 `git rev-parse` 输出**逐字相等**（锚定等值——切没切成、
切到哪，由它裁，不靠「记回执」）；migrate 为 no-op、`version_num` = **main 链尖端**
（当前为 0047；若届时 main 已带新迁移，以现值为准并记回执）；healthz 200
（失败等 10 秒重试一次）。

## C6 终态锚定（对 v8①基线的生产不变量）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.channel_order) AS channel_orders, (SELECT count(*) FROM app.order_line) AS order_lines, (SELECT count(*) FROM app.procurement_order) AS po_rows, (SELECT md5(coalesce(string_agg(id || ':' || coalesce(purchaser_id::text, '-'), ',' ORDER BY id), '')) FROM app.procurement_order) AS po_fingerprint, (SELECT count(*) FROM app.audit_log WHERE id <= 233) AS audit_baseline_rows;"
```

**判据**：`channel_orders=4`、`order_lines=4`、`po_rows=1`、
`po_fingerprint=d0bd571dc19c083d82f023c9666c5574`（与 v8①逐字同）、
`audit_baseline_rows=204`（锚定等值；总数大于基线属预期，审计行一行不删）。
全过 → 回执贴回 PR #50，写明：C2 各 DELETE 行数、六清单、版本链终态、main sha。
