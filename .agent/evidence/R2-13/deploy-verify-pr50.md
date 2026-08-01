# PR #50 第三闸真机验证指令 v11（R2-13 自动采购：不花钱可证伪层，改形版 + 旧现场清场 + 续跑补丁）

> **给部署 AI（Win11 部署机）。整段可粘贴，逐步执行，每步贴回输出。**
>
> **v7 重写背景**：Owner 逆向工程实录更正了身份模型——插件实例绑「一台授权浏览器」
> 而非买家账号，`customerId` 从鉴权凭据降为路由参数，越权边界=跨团队。审查五轮
> （所审 `8ee9703`）对改形后代码**②闸重新放行**并要求本指令按新模型重写。
> v6 执行到③前停点（README 脏工作树）作废。
>
> **v8 增补背景**（六轮审查 I1 + 部署机 2026-07-31 停点评论，双源）：
> (a) **I1**——`plugin_instance` 库里**没有 `note` 列**，v7 却拿它当清理键：⑧-3 直插
> SQL 报错、⑫清理单事务整段回滚、终态残留核对不可用。v8 全部改用**真实存在且签发
> 即落库**的 `version` 列（值统一 `R13T`），⑥另加「列表回显 version」正向确认。
> (b) 部署机现场=旧 v5 授权造的⑤夹具残留 + **改形前旧链 schema**（alembic_version
> 已是 0046 但内容是旧模型；改形是对未合并迁移的**原地重写**，revision id 未变，
> 直接 upgrade 对 alembic 是空转，④结构判据必失败）。v8 新增**⓪**：精确清场旧夹具 →
> 用旧迁移内容回滚到 0047 → ②③再用新内容真正升级。
>
> **v9 续跑补丁背景**（部署机 v8 停点评论 + 审查八轮裁定，双源）：v8 实跑到⑧-2，
> **产品判据全过**；停在部署侧编排的本地取值层——psql 单行输出在 PowerShell 里是
> String 不是数组，`$rows[0]` 取到 Char，`.Split` 报错。IA 明文令牌只在已退出的进程里，
> 库内只有散列（设计而非事故），不可恢复。八轮裁定：**吊销 IA + 补签不损伤已录证据**。
> **本版续跑路径：⓪-⑧-2 前半的已录证据全部有效不重跑——「续跑入口 R」→ ⑧-2「续跑
> 补齐」→ ⑧-3 起按序走完**。全新重跑者仍从⓪走起（R 节跳过）。取值一律用「统一取值」
> 节的 `Get-SqlValue`，**禁 Split**（八轮点名同族陷阱下游还有五处）。
>
> **v10 补丁**（九轮 V1-V4，均指令层）：R 节补齐**跨会话三前置**（`git pull`／重新登录取
> `$TOKEN`／重贴 `Get-SqlValue`）并钉入停点回执值清单——续跑会话是全新进程，⑤不重跑
> 则 `$TOKEN` 无处产生，第一条 revoke 即 401（与 Split 停点同类：状态只活在死进程里）；
> ⑧-2 补齐改 `$BA_C` 插值、`c_audit` 钉恰 1；⑫ notification 清理加行数下界 ≥5 兜底
> （占位符漏替换删 0 行时终态五清单照样全 0，会假清场）。
>
> **v11 补丁**（十轮 N1/N2，③闸已随十轮放行，两条都在开跑前落齐）：N1——`≥5` 下界
> 盖不住 PO 支半替换恰好压线，终态残留查询加第六项 `dedupe_key LIKE 'plugin.%'` **锚定零**
> 作真兜底（`≥5` 降为正向信号）；N2——R-0 的 `git pull` 会让工作树超前于③所建镜像，
> 加改动路径守卫（仅限 `.agent/` 才可继续），回执 sha **分列两行**（产品代码 sha ≠ 指令版 sha）。
>
> 被验代码：分支 **`claude/r2-03-launch-leg5n8` 的当前尖端**（判据=②的「你在分支尖端」+
> 「迁移清单恰为 0043-0046」，不写死 sha；实际 sha 记进回执）。
> 写法约定沿用 #48 v7 + #49 v6 全部教训：每条命令写死 `-f infra/docker-compose.yml`、
> 服务真名（db/redis/migrate/api/beat/scraper/frontend）、路径 `D:\项目文件\ERP-ALL`、
> SQL 载荷只有单引号、判据认状态不认日志、清理单事务、预期值写条件式。
> **测试数据一律 ASCII**（账号 label、customerId、订单号、原因串），中文不进任何 HTTP 判据。
> **承重步的实测值必须写进回执文件**（#49 十二轮：回执是归档物，数值不能只活在评论里）。

## 铁律（全程有效）

1. **不花钱**：全程 `stop_before_payment` / `dry_run` 档。**绝不签发、绝不使用 `live` 档
   实例**（⑥有一步专门验证「live 签不出来」，那是验证 422，不是要你签成）。
2. **不输出任何密钥、口令、Authorization/X-Plugin-Token 头的完整值**。实例 token 明文只在
   签发响应出现一次：回执**只记长度（`token_len`），不记任何令牌片段**（v9 收紧，八轮
   卫生条：仓库 public，归档物不放令牌前缀），完整值只存进 PowerShell 变量使用。
   （⑧-3 跨团队探针的 SQL 里有一枚**测试造的**假 token 字面量，它不是凭证，可照贴。）
3. **不改码、不 push、不 merge。**
4. **③级表一行不删**：`audit_log`、`channel_order`/`order_line`/`order_check`、财务表。
   **唯一例外**：⑫清理里 `R13T` 前缀**精确圈定**的测试族（渠道订单/行/检查、执行单、
   product、buyer_account、plugin_instance、notification 对象圈定、R13T-TEAM-B 团队行）。
   **audit_log 无任何例外**——本轮登记/签发/补录写入的审计行**留着**，那是 append-only
   留存面该有的样子（⑪的判据是锚定等值，不是总数相等，新增行不影响）。
5. **本次唯一的写入是测试数据**：一律 `R13T` 前缀。要动的对象不带该前缀 → **停下来问**。
6. 清理类 SQL 一律 `psql -1 -v ON_ERROR_STOP=1`。
7. 审查五轮点名的**重点位有两处**：⑨（金额与状态机——直接碰钱的一块）与
   ⑧（**首见登记与认领流的真机形状**——新号进来→待认领→认领补站点→开始派单，
   这条链只有真机能看出运营走不走得通）。两处的数值判据逐字贴回执，不写「与预期一致」。

## 统一取值（v9 新增；八轮三号条件——同族陷阱一次性抹掉）

凡「从 psql 回读单值 → 存 PowerShell 变量」，一律先贴这个函数再取值：

```powershell
function Get-SqlValue([string]$Sql) {
  $raw = docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -tA -1 -v ON_ERROR_STOP=1 -c $Sql
  return ($raw | Out-String).Trim()
}
```

规则：数值用 `[long](Get-SqlValue "…")`，字符串直接用；`-tA` 保证无表头无对齐的裸值；
**禁止 `.Split` / 禁止对返回值做行数组索引**——psql 单行输出在 PowerShell 里是 String，
`[0]` 取到的是 Char（v8 停点实证）。需要多列时分两次取值，不做文本拆分。
下文 `<BA_C>`/`<TEAM_B>`/`<ITB>`/`<BA_D>`/`<BA_F>` 等尖括号占位符 = 用本函数取到并记进
回执的值。

## 本单验什么

13a/13b/13d 的**不花钱可证伪层**，验收③按新模型三判据：
(a) **跨团队必败**（且响应与「全新 customerId」同形——不可探测存在性）；
(b) **未认领只落待认领行**（不派单、不报错）；
(c) **同一实例换同团队另一已认领 customerId 正常路由**。
外加：首见登记+洪水闸、驳回粘性（含合并 PATCH 绕过探针，F3 承重）、派发链
（daily_cap/缺ASIN/混派闸）、⑨金额与状态机、补录/释放互斥（Owner 异常#16 裁定）、
③级表零伤害、迁移 0043-0046 可逆。`live` 层（真实下单）由 Owner 之后拿真实订单收口，
**不在本单**。

---

## ⓪ 旧现场清场与旧链回滚（v8 新增；对象=部署机停点评论的清点；全过才进①）

> **为什么必须有⓪**：现场 DB 是改形前的旧 0043-0046 建的，alembic_version 已是 0046。
> 改形（`5895982` 起）对这四个**未合并**迁移原地重写、revision id 未变，所以新代码下
> 直接 `run --rm migrate` 什么都不做——库停留在旧模型，④必失败。唯一干净路径：
> 用**与现场所应用内容一致的旧文件**降到 0047，再让②③用新文件升上来。
> **授权说明**：本步删除的行 = 部署机 2026-07-31 停点评论逐条清点的旧 v5 测试族
> （R13T 前缀：账号 2 / 订单 4 / 产品 4 / 执行单 4），且 DELETE 同时用**评论里的精确
> id** 双重圈定——Owner 转交本指令即为对该精确范围的清场授权。圈到任何非 R13T
> 对象 → 停下来问。

**⓪-1 预检**（工作树可检出；db 在跑）：

```powershell
cd D:\项目文件\ERP-ALL
git status --porcelain
docker compose -f infra/docker-compose.yml ps db
```

**判据**：无 tracked 脏文件（未跟踪项不阻检出，可留）；db `Up (healthy)`。
tracked 有脏 → 按①的 stash 规则处理，处理不了停下来问。

**⓪-2 旧夹具精确清场**（单事务；顺序照删依赖；前缀 + 清点 id 双条件——若前缀圈到
id 清单之外的行，它会**留下**并在⓪-3 暴露，宁可漏删停下来问，不多删一行。此刻仍是
旧 schema，旧 buyer_account 的 label/external_customer_id 均 NOT NULL，双条件成立）：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -1 -v ON_ERROR_STOP=1 -c "DELETE FROM app.procurement_logistics_event WHERE procurement_order_id IN (SELECT po.id FROM app.procurement_order po JOIN app.channel_order co ON co.id = po.order_id AND co.order_date = po.order_date WHERE co.channel_order_no LIKE 'R13T-%' AND co.id IN (14,15,16,17)); DELETE FROM app.procurement_order WHERE id IN (3,4,5,6) AND order_id IN (SELECT id FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%'); DELETE FROM app.order_check WHERE order_id IN (SELECT id FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%' AND id IN (14,15,16,17)); DELETE FROM app.order_line WHERE order_id IN (SELECT id FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%' AND id IN (14,15,16,17)); DELETE FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%' AND id IN (14,15,16,17); DELETE FROM app.product WHERE source_ref LIKE 'R13T%' AND id IN (2490,2491,2492,2493); DELETE FROM app.buyer_account WHERE (label LIKE 'R13T-%' OR external_customer_id LIKE 'R13T%') AND id IN (1,2);"
```

**判据**（DELETE 行数逐条比对清点）：`procurement_order=4`、`channel_order=4`、
`product=4`、`buyer_account=2`；logistics_event / order_check / order_line 行数照实
记回执（v5 造数的附带行，数量不钉死）。

**⓪-3 残留核对**（应全 0）：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%') AS r13t_orders, (SELECT count(*) FROM app.buyer_account WHERE label LIKE 'R13T-%' OR external_customer_id LIKE 'R13T%') AS r13t_accounts, (SELECT count(*) FROM app.product WHERE source_ref LIKE 'R13T%') AS r13t_products, (SELECT count(*) FROM app.procurement_order WHERE id IN (3,4,5,6)) AS r13t_pos, (SELECT count(*) FROM app.plugin_instance) AS pi_rows;"
```

**判据**：五项全 0（`pi_rows=0` 即停点评论的现状复核）。任一非 0 → **不进⓪-4**，
贴现场等修指令。

**⓪-4 旧链回滚到 0047**（检出改形前最后一个提交 `b0838bf`：0043-0046 的旧内容在
`644cbf7`（审查首轮修复）之后、改形起点 `5895982` 之前无任何改动，与 v5 ③当时应用的
内容一致；旧 downgrade 全为 `DROP TABLE IF EXISTS ... CASCADE` / 定名列删，链序
0046↓→0045↓→0044↓→0043↓，实例表先于账号表删，外键顺序安全）：

```powershell
git fetch origin claude/r2-03-launch-leg5n8
git checkout b0838bf
docker compose -f infra/docker-compose.yml build migrate
docker compose -f infra/docker-compose.yml run --rm migrate alembic downgrade 0047
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT version_num FROM alembic_version;"
```

**判据**：`version_num = 0047`。检出后处于分离 HEAD **属预期**（②会回到分支尖端）。
降级若在 ADD CONSTRAINT 硬失败 → 库里还有 `pending_review`/`backfill_actor_kind='plugin'`
残行（⓪-2 清过后不应发生，插件从未在生产跑过）→ 停下来贴现场。

**⓪-5 回滚结构核对**（旧模型对象应全部消失）：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'app' AND table_name IN ('buyer_account','plugin_instance','procurement_logistics_event')) AS old_tables_left, (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'app' AND table_name = 'procurement_order' AND column_name IN ('exception_kind','delivery_est_raw','delivery_est_date','payment_card_last4','tax_amount','buyer_account_id')) AS old_cols_left, (SELECT count(*) FROM app.permission WHERE code IN ('procurement.buyer_account_read','procurement.buyer_account_admin','procurement.plugin_instance_admin')) AS old_perms_left;"
```

**判据**：三项全 0。全过 → 进①（**基线此刻取才是真正的生产基线**——旧测试族已出账、
旧权限种子已回收，①的 `perms_before` 与④的 `+3` 判据由此对得上）。

## ① 前置与基线（实测值全部写进回执；v6 的旧基线作废）

```powershell
cd D:\项目文件\ERP-ALL
git status --porcelain
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.audit_log) AS audit_rows_before, (SELECT coalesce(max(id),0) FROM app.audit_log) AS audit_max_id_before, (SELECT count(*) FROM app.channel_order) AS channel_orders_before, (SELECT count(*) FROM app.order_line) AS order_lines_before, (SELECT count(*) FROM app.procurement_order) AS po_rows_before, (SELECT coalesce(max(id),0) FROM app.procurement_order) AS po_max_id_before, (SELECT count(*) FROM app.permission) AS perms_before;"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT md5(coalesce(string_agg(id || ':' || coalesce(purchaser_id::text, '-'), ',' ORDER BY id), '')) AS po_fingerprint_before FROM app.procurement_order;"
```

**判据**：`tracked_dirty=0`，**唯一豁免** `M infra/local-deploy/README.md`（Owner 指令
产物、裁定未出）——若只有它脏：`git stash push -m "R13T-keep-readme" infra/local-deploy/README.md`
暂存（**不是丢弃**），本单全部跑完后 `git stash pop` 还原并在回执记「已还原」。
除它之外任何 tracked 脏文件 → 停下来问。
全部计数 + 指纹逐字记入回执；`audit_max_id_before` 是⑪锚定等值判据的锚；
本步基线同时是③的进入前置（③开跑前会回看本步基线已贴回执）。

## ② 分支与迁移清单

```powershell
git fetch origin claude/r2-03-launch-leg5n8
git checkout claude/r2-03-launch-leg5n8
git pull
git log --oneline -1
git diff --name-only origin/main...HEAD -- backend/alembic/versions
```

**判据**：本地与 origin 尖端一致（sha 记回执）；迁移清单**恰四个文件** `0043_buyer_account.py`
/ `0044_plugin_instance.py` / `0045_procurement_plugin_columns.py` /
`0046_procurement_logistics_event.py`。过则进③。

## ③ 重建与迁移（进入本步前确认①②已完成且基线已贴回执）

> ⚠️ v2 修订保留（③首停根因）：`migrate` 与 `api` 同 build 上下文但**两个独立镜像标签**，
> 只 build `api frontend` 时 `run --rm migrate` 用的是旧镜像。**build 必须带上 migrate**。

```powershell
docker compose -f infra/docker-compose.yml build api frontend migrate
docker compose -f infra/docker-compose.yml run --rm migrate
docker compose -f infra/docker-compose.yml up -d --force-recreate api beat frontend
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT version_num FROM alembic_version;"
Start-Sleep -Seconds 8
$r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/healthz" -UseBasicParsing -TimeoutSec 5
$r.StatusCode
```

**判据**：`version_num = 0046`（链 0047→0043→0044→0045→0046）；healthz 200
（force-recreate 后先等 8 秒；仍失败再等 10 秒重试一次，两次都失败才算不满足）。

## ④ 迁移结构核对（判据按改形反转——v6 的复合外键判据已随模型撤销）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'app' AND table_name IN ('buyer_account','plugin_instance','procurement_logistics_event')) AS new_tables, (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'app' AND table_name = 'plugin_instance' AND column_name = 'buyer_account_id') AS pi_account_col, (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'app' AND table_name = 'plugin_instance' AND column_name = 'last_seen_customer_id') AS pi_last_seen_col, (SELECT count(*) FROM pg_constraint WHERE conname = 'fk_plugin_instance_account') AS fk_composite, (SELECT count(*) FROM pg_indexes WHERE schemaname = 'app' AND indexname = 'ix_plugin_instance_team') AS ix_pi_team, (SELECT count(*) FROM pg_indexes WHERE schemaname = 'app' AND indexname = 'ix_plugin_instance_account') AS ix_pi_account;"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM pg_indexes WHERE schemaname = 'app' AND indexname = 'uq_buyer_account') AS uq_customer, (SELECT count(*) FROM pg_indexes WHERE schemaname = 'app' AND indexname = 'ix_buyer_account_pending') AS ix_pending, (SELECT count(*) FROM pg_constraint WHERE conname = 'uq_buyer_account_id_team') AS uq_id_team, (SELECT count(*) FROM pg_constraint WHERE conname = 'ck_buyer_account_claimed') AS ck_claimed, (SELECT count(*) FROM pg_constraint WHERE conname = 'ck_buyer_account_status' AND pg_get_constraintdef(oid) LIKE '%pending_claim%' AND pg_get_constraintdef(oid) LIKE '%rejected%') AS ck_status_new, (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'app' AND table_name = 'buyer_account' AND column_name IN ('label','site') AND is_nullable = 'YES') AS nullable_pair, (SELECT count(*) FROM pg_indexes WHERE schemaname = 'app' AND indexname = 'uq_po_active_dispatch') AS uq_dispatch, (SELECT count(*) FROM app.permission) AS perms_after;"
```

**判据**（新模型的结构指纹，「不存在」与「存在」同等承重）：
`new_tables=3`；**`pi_account_col=0`**（buyer_account_id 列已随改形消失）；
**`pi_last_seen_col=1`**（观察列在位）；**`fk_composite=0`**、**`ix_pi_account=0`**、
**`uq_id_team=0`**（复合外键族已撤——认证不再触碰账号表，错配形状不可表达）；
`ix_pi_team=1`；`uq_customer=1`（首见登记 ON CONFLICT 的推断目标）；`ix_pending=1`
（洪水闸支撑索引）；`ck_claimed=1`；`ck_status_new=1`；`nullable_pair=2`
（label/site 已放松 NOT NULL）；`uq_dispatch=1`；`perms_after = ①的 perms_before + 3`。

## ⑤ 造数（全 ASCII；`<TEAM_ID>` 用你验收团队的 id）

**⑤-1 两个已认领账号**（active 必须带 label+site——`ck_buyer_account_claimed` 在挡）：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -1 -v ON_ERROR_STOP=1 -c "INSERT INTO app.buyer_account (team_id, label, site, external_customer_id, status, daily_cap) VALUES (<TEAM_ID>, 'R13T-ACC-A', 'amazon_com', 'R13TCUSTA', 'active', 1), (<TEAM_ID>, 'R13T-ACC-B', 'amazon_com', 'R13TCUSTB', 'active', NULL) RETURNING id, label;"
```

**⑤-2 洪水闸配置压低到 2**（默认 50，真机灌 50 个假 id 不现实；配置中心 team 级覆盖，
先确认无既有行——有则**停下来问**，不许覆盖生产配置）：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS existing_cfg FROM app.team_config WHERE team_id = <TEAM_ID> AND key = 'procurement.plugin_dispatch';"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -1 -v ON_ERROR_STOP=1 -c "INSERT INTO app.team_config (team_id, key, value) VALUES (<TEAM_ID>, 'procurement.plugin_dispatch', jsonb_build_object('pending_claim_cap', 2));"
```

**⑤-3 订单族**：三张可路由渠道订单（收货国 US、行带 amazon 产品 ASIN）+ 一张缺 ASIN 单。
造数 SQL 按 `tests/db/test_r2_13b_dispatch.py::_mk_order` 的列清单写，product 形状钉死
（v3 修订保留：「非 amazon 渠道」触发缺 ASIN，可被前缀清理）：

| 单 | product.source_channel | product.source_ref | 效果 |
|---|---|---|---|
| `R13T-O1`…`R13T-O3` | `amazon` | `R13T-O1-ASIN` / `R13T-O2-ASIN` / `R13T-O3-ASIN` | 可路由 |
| `R13T-O4` | `walmart` | `R13T-O4-NOASIN` | 缺 ASIN 拦截触发 |

每张 `internal_status='checked'`，各建一张 `status='unassigned'` 的 procurement_order
（O4 的也建）。全部 RETURNING id 记回执（下文 `<PO1>`-`<PO4>` 即此处四个执行单 id）。
验收账号：确认有 `procurement.buyer_account_admin` / `procurement.plugin_instance_admin` /
`procurement.execute` / `order.assign` / `order.read` 五码，登录取 `$TOKEN`（不回显）。

**判据**：`existing_cfg=0`（非 0 停）；账号 A（cap=1）/B（不限）建成；四单四执行单在册。

## ⑥ 实例签发（**团队级**——新模型下实例不挂账号；live 必须签不出来）

```powershell
$IA = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/plugin-instances" -Method Post -Headers @{ Authorization = "Bearer $TOKEN" } -Body '{"exec_mode":"stop_before_payment","version":"R13T"}' -ContentType "application/json"
"IA_id=$($IA.id) token_len=$($IA.token.Length)"
try { Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/plugin-instances" -Method Post -Headers @{ Authorization = "Bearer $TOKEN" } -Body '{"exec_mode":"live"}' -ContentType "application/json" } catch { $_.Exception.Response.StatusCode.value__ }
```

**判据**：签发 201 返回 token（回执只记 `IA_id`/`token_len`，**不记令牌片段**——铁律 2 v9 版）；**live 直签 = 422**
（签发词表只收两个演练档，live 只能事后 PATCH 显式升档）；`GET /plugin-instances`
不回显 token、含 `last_seen_customer_id` 列（此刻为空）、**IA 行 `version='R13T'`**
（清理键已真实落库的正向确认——⑫按它圈定；六轮 I1：v7 用的 note 从不入库）。
**只签这一枚工作实例**——
新模型一枚实例即可服务全团队所有账号（⑧-1 的 (c) 判据正要证明这一点）。

## ⑦ 认证三路同码（+吊销探针）

```powershell
$HA = @{ 'X-Plugin-Instance' = "$($IA.id)"; 'X-Plugin-Token' = $IA.token }
(Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/purchase-plugin/getNeedPurchaseOrders?customerId=R13TCUSTA" -Headers $HA -UseBasicParsing).StatusCode
try { Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/purchase-plugin/getNeedPurchaseOrders?customerId=R13TCUSTA" -Headers @{ 'X-Plugin-Instance' = "$($IA.id)"; 'X-Plugin-Token' = 'R13T-wrong-token' } -UseBasicParsing } catch { $_.Exception.Response.StatusCode.value__; $_.ErrorDetails.Message }
try { Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/purchase-plugin/getNeedPurchaseOrders?customerId=R13TCUSTA" -Headers @{ 'X-Plugin-Instance' = '999999999'; 'X-Plugin-Token' = 'R13T-any' } -UseBasicParsing } catch { $_.Exception.Response.StatusCode.value__; $_.ErrorDetails.Message }
$IC = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/plugin-instances" -Method Post -Headers @{ Authorization = "Bearer $TOKEN" } -Body '{"exec_mode":"dry_run","version":"R13T"}' -ContentType "application/json"
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/plugin-instances/$($IC.id)/revoke" -Method Post -Headers @{ Authorization = "Bearer $TOKEN" } | ConvertTo-Json
try { Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/purchase-plugin/getNeedPurchaseOrders?customerId=R13TCUSTA" -Headers @{ 'X-Plugin-Instance' = "$($IC.id)"; 'X-Plugin-Token' = $IC.token } -UseBasicParsing } catch { $_.Exception.Response.StatusCode.value__; $_.ErrorDetails.Message }
```

**判据**：正确头 200；错 token / 不存在 id / 已吊销 **三路全部 401 且报文含同一错误码
`PLUGIN_AUTH`**（逐字相同——同码是 B6 时序抹平承诺的另一半）。`customerId` 是必填 query，
四条 URL 统一钉 `?customerId=R13TCUSTA` 保证失败只能来自认证层（v6 教训保留）。

## 续跑入口 R：吊销旧工作实例并补签（v9 新增；八轮裁定「允许」的落地；全新重跑跳过本节）

> 已录证据全部有效的依据（八轮逐条核过）：本指令无任何步骤断言 `plugin_instance` 行数
> （⓪-3 的 `pi_rows` 是升级前的、⑫按 version 圈定）；旧 IA 吊销后行仍在、
> `last_seen_customer_id='R13TCUSTC'` 观察值保留；已落派单在 `procurement_order` 上与实例
> 状态无关；明文只在签发响应出现一次、库内仅散列——**补签是唯一路径**。

**R-0 跨会话三前置（九轮 V1——续跑会话是全新进程，一样都不能省）**：
① `git pull` 取最新指令与代码；② **重新登录取 `$TOKEN`**（同⑤的方式：验收账号五码
在位，口令与 token 只入变量不回显）；③ 重贴 `Get-SqlValue`（下块已含——「统一取值」节
在文档前部，从本节接入不会经过它）。
**停点回执值清单**（⑧-3 起的手工占位符从这里填；现场复核不同以现场为准并记回执）：
`<PO1>`-`<PO4>` = **7/8/9/10**；`<POA>` = **7**（派给 A）；`<POB1>`/`<POB2>` = **8/9**
（派给 B）；`<BA_C>` = **3**（⑧-2 补齐会用统一取值重取核对）；`<TEAM_ID>` = ⑤所用
验收团队 id（现场已知）；订单 O1-O4 = 18-21、product = 2494-2497（⑪/⑫复核参照）。

```powershell
git pull
git log --oneline -1
git diff --name-only e725c67..HEAD
git stash list
function Get-SqlValue([string]$Sql) {
  $raw = docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -tA -1 -v ON_ERROR_STOP=1 -c $Sql
  return ($raw | Out-String).Trim()
}
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/plugin-instances/1/revoke" -Method Post -Headers @{ Authorization = "Bearer $TOKEN" } | ConvertTo-Json
$IA = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/plugin-instances" -Method Post -Headers @{ Authorization = "Bearer $TOKEN" } -Body '{"exec_mode":"stop_before_payment","version":"R13T"}' -ContentType "application/json"
"IA_R_id=$($IA.id) token_len=$($IA.token.Length)"
$HA = @{ 'X-Plugin-Instance' = "$($IA.id)"; 'X-Plugin-Token' = $IA.token }
```

**判据**：`git diff --name-only e725c67..HEAD` 输出路径**仅限 `.agent/`** → ③所建镜像
仍有效可继续；出现 `backend/`、`frontend/`、`infra/`、`workers/` 任一路径 → **回③重建
+重迁移**后再续。回执 sha **分列两行**：产品代码=`e725c67`（③建镜像所用）、指令版=
`git log` 所示 HEAD（十轮 N2——单写一个 sha 就是「没被镜像验证过的 sha」，正文失实
同族）。`git stash list` 照实记回执（①若做过 README 暂存它应还挂在栈上、⑫要 pop；
空则记「无暂存」——八轮点名的回执缺项）；吊销 200 且旧行仍在（revoked、last_seen 保留）；
补签 201，回执只记 `IA_R_id`/`token_len`；`GET /plugin-instances` 复核三行：id=1 revoked /
IC revoked / **IA-R 是唯一 active**，且 IA-R 行 **`version='R13T'`**（八轮一号条件：
body 漏这个字段则⑫清不掉、终态 `r13t_instances=0` 必红——签完立刻在这里确认，不拖到⑫）。
路由地址钉 id=1 是停点回执钉下的旧 IA id，现场若不同以现场为准并记回执。
变量沿用 `$IA`/`$HA`——⑧⑨⑩全部后续命令无需改写。
**续跑顺序：本节 → ⑧-2「续跑补齐」→ ⑧-3 起按序走完。**

## ⑧ 派发与身份链（验收③三判据 + 首见登记/洪水闸/驳回粘性；审查五轮点名重点位）

**⑧-1 daily_cap + 同实例换号路由（验收③判据 c）**：
IA 拉取 `?customerId=R13TCUSTA`（编码安全读法：取原始字节 UTF-8 解码后 ConvertFrom-Json）
→ 恰 **1** 张（cap=1，池里 3 张可派）；再拉一次 → 同一张（续拉不耗额度）；记 `<POA>`。
**同一实例**换 `?customerId=R13TCUSTB` → 其余 **2** 张全到手（cap NULL），记 `<POB1>`/`<POB2>`；
`R13T-O4` 不在任何返回里。库内核对：被派单 `status='assigned'` 挂对账号；
channel_order `internal_status='assigned'`（A2 对称）。
顺带记录（观察列，不承重）：`GET /plugin-instances` 里 IA 行的 `last_seen_customer_id`
实测值贴回执（预期=最近一次拉取带的 customerId；为空也照贴，继续）。

**⑧-2 未认领只落待认领行（验收③判据 b）**：IA 拉 `?customerId=R13TCUSTC`（全新）→
**200 + 空数组**（不是 4xx——待认领是业务闸不是认证失败）。库内：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT id, status, label, site, daily_cap FROM app.buyer_account WHERE team_id = <TEAM_ID> AND external_customer_id = 'R13TCUSTC';"
```

→ 恰一行 `pending_claim`、label/site/daily_cap 全 NULL（记 `<BA_C>`）；notification 有
`dedupe_key='plugin.pending_claim.<BA_C>'` 的 warn 行；audit_log 有
`action='buyer_account.auto_register'` 且 `object_id='<BA_C>'` 的行。
**再拉一次同 id** → 仍 200+空数组，行数仍 1、通知仍 1 条（幂等，不重复告警）。
**任何执行单未因此变动**（`<PO1>`-`<PO4>` 状态复查与⑧-1 后一致）。

**⑧-2 续跑补齐（八轮点名的证据缺项；续跑者从这里接，全新重跑者随⑧-2 一并做）**：

```powershell
$BA_C = [long](Get-SqlValue "SELECT id FROM app.buyer_account WHERE team_id = <TEAM_ID> AND external_customer_id = 'R13TCUSTC';")
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.notification WHERE dedupe_key = 'plugin.pending_claim.$BA_C') AS c_alerts, (SELECT count(*) FROM app.audit_log WHERE action = 'buyer_account.auto_register' AND object_id = '$BA_C') AS c_audit;"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT channel_order_no, internal_status FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%' ORDER BY channel_order_no;"
```

**判据**：`c_alerts=1`（warn 行在）、**`c_audit=1`**（恰一行，九轮 V3——重复见号走解析
命中根本不进登记，审计留痕多于 1 行本身就是缺陷，`>=1` 对它是瞎的）；随后用 IA-R
再拉一次 `?customerId=R13TCUSTC` → 200+空数组，复查 C 行数仍 1、`c_alerts` **仍 1**
（幂等不重复告警——⑧-2 原判据的续跑复核）；channel_order 侧 `R13T-O1/O2/O3 = assigned`、
`R13T-O4 = checked`（⑧-1 漏报的 A2 对称项，补进回执）。

**⑧-3 跨团队必败（验收③判据 a）**：造对端团队与实例（直插 SQL——这是探针夹具，
不是签发通道；token 是测试字面量非凭证）：

```powershell
$TEAM_B = [long](Get-SqlValue "INSERT INTO app.team (name) VALUES ('R13T-TEAM-B') RETURNING id;")
$ITB = [long](Get-SqlValue "INSERT INTO app.plugin_instance (team_id, token_hash, exec_mode, version) VALUES ($TEAM_B, encode(sha256('R13T-TB-TOKEN-4f9a2c81'::bytea), 'hex'), 'dry_run', 'R13T') RETURNING id;")
"TEAM_B=$TEAM_B ITB=$ITB"
```

用 `<ITB>` + 明文 `R13T-TB-TOKEN-4f9a2c81` 作头，拉 `?customerId=R13TCUSTA`
（**团队 A 的已认领号**）：
- 响应 **200 + 空数组**，**与⑧-2 的响应同形**（同形即「不可探测存在性」判据：
  拿 B 的令牌问不出「R13TCUSTA 在别的团队存不存在」）；
- 库内：**团队 B 里**新落一行 `external_customer_id='R13TCUSTA'` 的 `pending_claim`
  （取值 `$BA_TB = [long](Get-SqlValue "SELECT id FROM app.buyer_account WHERE team_id = $TEAM_B AND external_customer_id = 'R13TCUSTA';")`
  ——跨团队在对方团队眼里就是个没见过的字符串，一视同仁登记）；
- **团队 A 一列不动**：A 的 R13TCUSTA 行、`<POA>` 等任务、A 的额度复查全部原值。
写路径必败：ITB 对 `<POA>` 调 `updateOrderStatus`（载荷同⑧-4）→ **403 且报文含
`PLUGIN_TASK_NOT_OWNED`**；库内 `<POA>` 一字未动。

**⑧-4 同团队跨账号写放行（图纸 07:338-341 有意放宽，正向承重）**：IA（团队 A 实例）对
**B 账号名下**的 `<POB1>` 调 `updateOrderStatus`：

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/purchase-plugin/updateOrderStatus" -Method Post -Headers $HA -Body '{"id": <POB1>, "status": 99, "failReason": "R13T-team-scope-probe"}' -ContentType "application/json" -UseBasicParsing
```

→ **200**（团队内换号报状态是操作常态不是越权——放宽是图纸背书的有意行为）；库内
`<POB1>` `status='exception'`、`exception_kind` 非空、`exception_reason` 含
`R13T-team-scope-probe`。（该单顺势成为⑨-4d 释放正路的输入。）

**⑧-5 缺 ASIN 拦截**：`<PO4>` 始终 `unassigned`、`buyer_account_id IS NULL`；
notification 有 `plugin.no_asin.<PO4>` 的 dedupe_key 行。

**⑧-6 混派闸**：对 `R13T-O1`（已被插件派出）人工 `POST /procurement-orders`（body 带该
order_id）→ **409 PROCUREMENT_ORDER_IN_FLIGHT**（A2）；人工 claim `<POA>` →
**409 PROCUREMENT_PLUGIN_DISPATCHED**（A1）。

**⑧-7 洪水闸（cap=2，⑤-2 所设）**：IA 拉 `?customerId=R13TCUSTD`（团队 A 第 2 条
pending：C+D）→ 200+空数组、落行，取值
`$BA_D = [long](Get-SqlValue "SELECT id FROM app.buyer_account WHERE team_id = <TEAM_ID> AND external_customer_id = 'R13TCUSTD';")`。
再拉 `?customerId=R13TCUSTE` → **200+空数组但不落行**：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.buyer_account WHERE team_id = <TEAM_ID> AND external_customer_id = 'R13TCUSTE') AS e_rows, (SELECT count(*) FROM app.buyer_account WHERE team_id = <TEAM_ID> AND status = 'pending_claim') AS pending_now, (SELECT count(*) FROM app.notification WHERE dedupe_key = 'plugin.pending_claim_flood.<TEAM_ID>') AS flood_alerts;"
```

**判据**：`e_rows=0`（拒绝登记）、`pending_now=2`（=cap）、`flood_alerts=1`（critical，
正文含实例号与 rejected 现值——治理面的两条可见性都在正文里，抽查贴回执）。
**实例号比对基准（八轮二号条件）**：通知正文记的是**发起当次请求的实例**
（`identity.py` 语义）。续跑后洪水闸/D/F 各条正文含 **IA-R 的 id**；⑧-2 已落的 BA_C
那条正文含旧 IA id=1——**两个 id 并存是正确行为**，既不是缺陷，也不许因「对不上」跳过比对。

**⑧-8 驳回粘性 + 合并 PATCH 探针（F3 承重）+ 认领链**：
a. 驳回 D：`PATCH /buyer-accounts/<BA_D>` body `'{"status":"rejected"}'` → 200；
   **rejected 不占额度**：再拉 `?customerId=R13TCUSTF` → **登记成功**（取值
   `$BA_F = [long](Get-SqlValue "SELECT id FROM app.buyer_account WHERE team_id = <TEAM_ID> AND external_customer_id = 'R13TCUSTF';")`；
   pending 回到 2——D 挪出额度后新号能进来，治理不自伤）。
b. 粘性（同 id 再灌零新增）：再拉 `?customerId=R13TCUSTD` → 200+空数组，
   D 行数仍 1、无新通知。
c. 驳回后挪 id 必拒：`PATCH /buyer-accounts/<BA_D>` body
   `'{"external_customer_id":"R13TCUSTZ"}'` → **409 BUYER_ACCOUNT_REJECTED_IMMUTABLE**。
d. **合并 PATCH 原子拒绝**（复验重开 F3 的那条绕过，判「结果态」）：对**仍待认领**的
   `<BA_C>` 一笔发 `'{"status":"rejected","external_customer_id":"R13TCUSTY"}'` →
   **整请求 409 BUYER_ACCOUNT_REJECTED_IMMUTABLE**；库内复查 `<BA_C>` **仍
   `pending_claim` 且 external_customer_id 仍 `R13TCUSTC`**（原子性：不存在
   「驳回生效了但 id 没挪」的部分放行）。
e. 认领正路（审查五轮点名的真机链，前半）：`PATCH /buyer-accounts/<BA_F>` body
   `'{"status":"active","label":"R13T-ACC-F","site":"amazon_com","daily_cap":1}'` → 200；
   随即拉 `?customerId=R13TCUSTF` → 200+空数组（池已派空——**空是对的**，F 的首单
   在⑨-4d 释放后回来，链在那里闭合）。
   反证一条：先只补 label 不补 site 的
   `'{"status":"active","label":"R13T-ACC-F"}'` → **422 BUYER_ACCOUNT_CLAIM_INCOMPLETE**
   （`ck_buyer_account_claimed` 在 DB 层挡「半认领」，先跑这条再跑上面的成功路）。

## ⑨ 回填链：金额与状态机（审查重点位，数值逐字入回执）

**⑨-1 stop_before_payment 档抓金额不落单**：IA 对 `<POA>` 调 `purchaseOrderFinishUpdate`
（totalBeforeTax `$9.99` / tax `$0.80` / shipping `$0.00` / total `$10.79`）。
库内判据：`purchase_cost=9.99`、`tax_amount=0.80`、`freight_cost=0.00`、
`purchase_currency='USD'`、`exchange_rate_locked=1.0`、**`purchase_order_ref IS NULL`**、
**`purchased_at IS NULL`**、**`status='assigned'` 不动**、channel_order 不进 purchasing。

**⑨-2 金额自校验不平 → 落异常但数据照实入库（D2/D1）**：再调一次，totalBeforeTax
`$5.00`、total `$10.79`（不平）→ `exception_kind` 落值、金额列**照实更新为 5.00**、
`status` 仍不动；notification 出现对应告警（dedupe_key ASCII）。

**⑨-3 负数拒收（B5）**：totalBeforeTax `-$3.20` → 该列**保持上一次值**（不写负不写 0）、
告警/日志触发；HTTP 200（解析失败不断链）。

**⑨-4 人工补录与释放互斥（Owner 异常#16 裁定承重）**：
a. `POST /procurement-orders/<POA>/exception`（reason `R13T-fail-16`）→ `status='exception'`；
b. **人工补录**：`POST /procurement-orders/<POA>/plugin-backfill`
   （body `'{"purchase_order_ref":"113-R13T0001-0000001","purchase_cost":10.79}'`）→ 200；
   库内 `status='purchased'`、ref 落、`backfill_actor_kind='op_direct'`、
   `exception_kind IS NULL`、channel_order → `purchasing`；
c. 再标异常（exception_po 允许）→ `POST /procurement-orders/<POA>/release` →
   **409 PROCUREMENT_ALREADY_PURCHASED**（补录后释放必被拒——误释放重买的洞闭合）；
d. **释放正路 + 认领链闭合（审查五轮点名链的后半）**：`<POB1>` 已在⑧-4 落 exception
   （未回填）→ `POST /procurement-orders/<POB1>/release` → 200，库内 `status='unassigned'`、
   `buyer_account_id IS NULL`、channel_order 退回 `checked`；随即 IA 拉
   `?customerId=R13TCUSTF` → **`<POB1>` 派给 F**（库内 `buyer_account_id=<BA_F>`）。
   这一步同时证明两件事：释放后真能重派；**新号进来→待认领→认领补站点→开始派单**
   整条运营链在真机走通。

**⑨-5 迟到重试幂等**：对 `<POA>`（已人工补录）用 IA 再调 `purchaseOrderFinishUpdate`
带**同一** platformOrderNo `113-R13T0001-0000001` → `data.idempotent=true`、金额不覆盖；
换**不同**单号再调 → conflict 形状、库内原 ref 一字不动、notification 出现
`plugin.dup_ref.<POA>` critical 告警。

## ⑩ 物流链（13d，D6 承重）

对 `<POA>` 调 `updateTrackingInfo`（carrier `R13T-UPS`、trackingNo `1ZR13T000001`、
trackingJson 三条 ASCII 事件）→ `carrier`/`tracking_no` 落、`status='shipped'`、
`procurement_logistics_event` 恰 3 行 seq 0/1/2；**channel_order 的 `internal_status`
不因此变化**（D6）。重发同载荷 → 行数仍 3（upsert 幂等）。

## ⑪ ③级表承重（对①基线）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.channel_order WHERE channel_order_no NOT LIKE 'R13T-%') AS channel_orders_now, (SELECT count(*) FROM app.order_line ol WHERE NOT EXISTS (SELECT 1 FROM app.channel_order co WHERE co.id = ol.order_id AND co.order_date = ol.order_date AND co.channel_order_no LIKE 'R13T-%')) AS order_lines_now;"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT md5(coalesce(string_agg(id || ':' || coalesce(purchaser_id::text, '-'), ',' ORDER BY id), '')) AS po_fingerprint_now FROM app.procurement_order WHERE id <= <PO_MAX_ID_BEFORE>;"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS audit_baseline_rows_now FROM app.audit_log WHERE id <= <AUDIT_MAX_ID_BEFORE>;"
```

**判据**：`channel_orders_now` / `order_lines_now` 与①同名项**相等**（排除 R13T 族后）；
`po_fingerprint_now` 与①**逐字相同**；**`audit_baseline_rows_now = ①的 audit_rows_before`**
（锚定等值：基线时刻已存在的审计行一行未删；本轮新增的签发/登记/补录/驳回留痕都在
锚之外，**总数大于基线属预期**，判据只认锚定等值）。

## ⑫ 清理 + 迁移可逆 + 终态

**清理（单事务，前缀/对象精确圈定；顺序照删依赖）**：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -1 -v ON_ERROR_STOP=1 -c "DELETE FROM app.procurement_logistics_event WHERE procurement_order_id IN (SELECT po.id FROM app.procurement_order po JOIN app.channel_order co ON co.id = po.order_id AND co.order_date = po.order_date WHERE co.channel_order_no LIKE 'R13T-%'); DELETE FROM app.procurement_order WHERE order_id IN (SELECT id FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%'); DELETE FROM app.order_check WHERE order_id IN (SELECT id FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%'); DELETE FROM app.order_line WHERE order_id IN (SELECT id FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%'); DELETE FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%'; DELETE FROM app.product WHERE source_ref LIKE 'R13T%'; DELETE FROM app.plugin_instance WHERE version = 'R13T'; DELETE FROM app.buyer_account WHERE label LIKE 'R13T-%' OR external_customer_id LIKE 'R13T%'; DELETE FROM app.notification WHERE (object_type = 'procurement_order' AND object_id IN ('<PO1>','<PO2>','<PO3>','<PO4>')) OR (object_type = 'buyer_account' AND object_id IN ('<BA_C>','<BA_D>','<BA_F>','<BA_TB>')) OR dedupe_key = 'plugin.pending_claim_flood.<TEAM_ID>'; DELETE FROM app.team_config WHERE team_id = <TEAM_ID> AND key = 'procurement.plugin_dispatch'; DELETE FROM app.team WHERE name = 'R13T-TEAM-B';"
```

> 圈定说明：plugin_instance 按 `version = 'R13T'`（**续跑现场为四枚全中**：旧 IA revoked /
> IC revoked / IA-R active / ITB——⑥、续跑入口 R 的签发 body 与⑧-3 直插同值，落库已在
> ⑥/R 正向确认；全新重跑则三枚。六轮 I1：库里从来没有 note 列，团队级模型下
> 也不再有「随账号连带删」）；buyer_account 双条件——已认领的按 label 前缀、待认领/驳回的
> label 为 NULL 按 customerId 前缀（含 TB 团队里那条 `R13TCUSTA` 探针行）；notification
> 按**对象圈定** + 洪水闸那条按**本团队 dedupe_key**（cap 是⑤-2 人为压低造出的告警，
> 插件未上线不存在真实同类行）；team_config 删的正是⑤-2 造的行（前提=⑤-2 的
> `existing_cfg=0` 判据过了）；`R13T-TEAM-B` 团队行最后删（其 plugin_instance/
> buyer_account 子行已先删；audit/notification 的 team_id 是软引用不阻删——TB 的审计行
> **留着**，append-only 铁律对测试团队的行同样有效）。

**清理判据（九轮 V4 兜底——终态五清单不含 notification，这里不拦会假清场）**：
`-1` 单事务下 psql 逐条打印 `DELETE n`，各条行数**照实记回执**；其中 **notification
那条必须 ≥ 5**（确定性下界：C/D/F/TB 四条 pending_claim warn + 1 条洪水闸；no_asin、
金额自校验、dup_ref 等按实际发生数再往上加）。**该条为 0 = 占位符漏替换的信号，停**，
不得继续走迁移可逆演练。
> 十轮 N1 修正：`≥5` 只是**正向信号**——半替换（PO 支占位符漏、BA 支已替）会恰好删 5 条
> 压线通过。**真兜底是终态残留查询的第六项 `r13t_notifs`（锚定零）**，见下。

**迁移可逆**（migrate 服务默认命令是 upgrade head，降级要覆盖命令跑）：

```powershell
docker compose -f infra/docker-compose.yml run --rm migrate alembic downgrade 0047
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT version_num FROM alembic_version;"
docker compose -f infra/docker-compose.yml run --rm migrate
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT version_num FROM alembic_version;"
docker compose -f infra/docker-compose.yml restart beat
```

判据：降级后 `version_num=0047` + 三张新表消失 + procurement_order 新列消失 + perms 回①值；
回升后 `version_num=0046` + ④的结构判据复核全过；beat 重启恢复 Up。
> ⚠️ 若降级在 ADD CONSTRAINT 上硬失败（`CheckViolation ck_procurement_backfill_actor`
> 或 status CHECK），说明库里还有 `backfill_actor_kind='plugin'` 或 `status='pending_review'`
> 残行——那是 0045 **有意的**降级守卫（人工处置前不许降，仓内测试
> `TestDowngradeDataGuard` 钉着它），**回⑫清理核对漏了哪行，不许改词表硬闯**。

**终态**：⑪三项复核相等/等值；六服务 Up（db/redis healthy）；`git stash pop` 还原 README
暂存（若①做过）并在回执记「已还原」；残留核对**六清单全 0**：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%') AS r13t_orders, (SELECT count(*) FROM app.buyer_account WHERE label LIKE 'R13T-%' OR external_customer_id LIKE 'R13T%') AS r13t_accounts, (SELECT count(*) FROM app.plugin_instance WHERE version = 'R13T') AS r13t_instances, (SELECT count(*) FROM app.team WHERE name = 'R13T-TEAM-B') AS r13t_team, (SELECT count(*) FROM app.team_config WHERE team_id = <TEAM_ID> AND key = 'procurement.plugin_dispatch') AS r13t_cfg, (SELECT count(*) FROM app.notification WHERE dedupe_key LIKE 'plugin.%') AS r13t_notifs;"
```

> 第六项 `r13t_notifs`（十轮 N1，notification 清理的真兜底）判 **0** 的三条依据（审查侧
> 已逐一核过）：本轮全部通知发射点的 dedupe_key 一律 `plugin.` 前缀；插件从未在生产
> 跑过（⓪-3 实测 `pi_rows=0`）；库内其它通知生产者全用冒号式 key（`ship_failed:` 等），
> 不会命中 `plugin.%`。它不依赖任何手工占位符，「查询与 DELETE 一起瞎」在这里不可能。

---

**回执格式**：逐步贴判据实测值（⑧⑨两个重点位全部数值逐字）；结尾写明被验代码 sha、
指令版本 sha、停机次数与性质。任何一步判据不满足：**停手贴现场**，等修指令，不自行变通。
