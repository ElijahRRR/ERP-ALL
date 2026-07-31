# PR #50 第三闸真机验证指令（R2-13 13a/13b/13d 自动采购：不花钱可证伪层）

> **给部署 AI（Win11 部署机）。整段可粘贴，逐步执行，每步贴回输出。**
>
> 被验代码：分支 **`claude/r2-03-launch-leg5n8` 的当前尖端**（判据=②的「你在分支尖端」+
> 「迁移清单恰为 0043-0046」，不写死 sha；实际 sha 记进回执）。
> 写法约定沿用 #48 v7 + #49 v6 全部教训：每条命令写死 `-f infra/docker-compose.yml`、
> 服务真名（db/redis/migrate/api/beat/scraper/frontend）、路径 `D:\项目文件\ERP-ALL`、
> SQL 载荷只有单引号、判据认状态不认日志、清理单事务、预期值写条件式。
> **编码问题从根上消掉（v6 教训的更强形式）：本指令全部测试数据一律 ASCII**
> （账号 label、订单号、原因串），中文不进任何 HTTP 判据；错误码/状态值本就是 ASCII。
> **承重步的实测值必须写进回执文件**（#49 十二轮建议：回执是归档物，数值不能只活在评论里）。

## 铁律（全程有效）

1. **不花钱**：本层全程用 `stop_before_payment` / `dry_run` 档。**绝不签发、绝不使用
   `live` 档实例**（⑥有一步专门验证「live 签不出来」，那是验证 422，不是要你签成）。
2. **不输出任何密钥、口令、Authorization/X-Plugin-Token 头的完整值**。实例 token 明文只在
   签发响应出现一次：截取**前 8 位 + 长度**记回执，完整值只存进 PowerShell 变量使用。
3. **不改码、不 push、不 merge。**
4. **③级表一行不删**：`audit_log`、`channel_order`/`order_line`/`order_check`、财务表。
   **唯一例外**：⑫清理里 `channel_order_no LIKE 'R13T-%'` 的**测试**渠道订单族
   （⑤造的，含其 order_line/order_check/procurement_order 连带），按该前缀**精确圈定**。
5. **本次唯一的写入是测试数据**：一律 `R13T` 前缀（渠道订单号 `R13T-…`、账号 label
   `R13T-ACC-…`、用户名 `r13t0731…`）。要动的对象不带该前缀 → **停下来问**。
6. 清理类 SQL 一律 `psql -1 -v ON_ERROR_STOP=1`。
7. 审查二轮点名的**重点位是 ⑨（插件回填的金额与状态机）**——那一步的每个数值判据
   都要逐字贴回执，不许写「与预期一致」。

## 本单验什么

13a（插件契约端点+实例认证）/ 13b（买家账号池+派发）/ 13d（回填异常物流）的
**不花钱可证伪层**：验收③（越权必败）、派发链（daily_cap/缺ASIN拦截/混派闸）、
⑨金额与状态机（审查重点位）、补录/释放互斥（Owner 异常#16 裁定）、③级表零伤害、
迁移 0043-0046 可逆。`live` 层（真实下单）由 Owner 之后拿真实订单收口，**不在本单**。

---

## ① 前置与基线（实测值全部写进回执）

```powershell
cd D:\项目文件\ERP-ALL
git status --porcelain
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.audit_log) AS audit_rows_before, (SELECT coalesce(max(id),0) FROM app.audit_log) AS audit_max_id_before, (SELECT count(*) FROM app.channel_order) AS channel_orders_before, (SELECT count(*) FROM app.order_line) AS order_lines_before, (SELECT count(*) FROM app.procurement_order) AS po_rows_before, (SELECT coalesce(max(id),0) FROM app.procurement_order) AS po_max_id_before, (SELECT count(*) FROM app.permission) AS perms_before;"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT md5(coalesce(string_agg(id || ':' || coalesce(purchaser_id::text, '-'), ',' ORDER BY id), '')) AS po_fingerprint_before FROM app.procurement_order;"
```

**判据**：`tracked_dirty=0`；全部计数 + 指纹逐字记入回执（⑪要逐项对拍；③的门要求本步先完成）。
`audit_max_id_before` 是⑪锚定等值判据的锚（审查三轮 G1）。
**本次运行特批**：①已跑过且当时未取该锚——部署机实测 `audit_rows_now=204` 与基线相等、
`audit_max_id_now=233`，**授权把 233 记为 audit_max_id_before**（无损补取，理由见 PR 评论）。

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

> ⚠️ v2 修订（③首停根因）：`migrate` 与 `api` 虽同一 build 上下文（`../backend`），但在
> compose 里是**两个独立镜像标签**——只 build `api frontend` 时 `run --rm migrate` 用的是
> **上一次的旧镜像**（迁移链停在旧 head），upgrade 原地 no-op。**build 必须带上 migrate**。

```powershell
docker compose -f infra/docker-compose.yml build api frontend migrate
docker compose -f infra/docker-compose.yml run --rm migrate
docker compose -f infra/docker-compose.yml up -d --force-recreate api beat frontend
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT version_num FROM alembic_version;"
Start-Sleep -Seconds 8
$r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/healthz" -UseBasicParsing -TimeoutSec 5
$r.StatusCode
```

**判据**：`version_num = 0046`（链 0047→0043→0044→0045→0046，head 是 0046）；healthz 200
（force-recreate 后容器要几秒起身，故先等 8 秒；仍失败再等 10 秒重试一次，
两次都失败才算判据不满足）。

## ④ 迁移结构核对（含审查二轮真库验过的复合外键）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'app' AND table_name IN ('buyer_account','plugin_instance','procurement_logistics_event')) AS new_tables, (SELECT count(*) FROM pg_constraint WHERE conname = 'uq_buyer_account_id_team') AS uq_id_team, (SELECT count(*) FROM pg_constraint WHERE conname = 'fk_plugin_instance_account') AS fk_composite, (SELECT count(*) FROM pg_indexes WHERE schemaname = 'app' AND indexname = 'uq_po_active_dispatch') AS uq_dispatch, (SELECT count(*) FROM app.permission) AS perms_after;"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS ck_has_pending_review FROM pg_constraint WHERE conname LIKE 'ck_procurement%status%' AND pg_get_constraintdef(oid) LIKE '%pending_review%';"
```

**判据**：`new_tables=3`、`uq_id_team=1`、`fk_composite=1`、`uq_dispatch=1`、
`perms_after = ①的 perms_before + 3`（条件式：0043 种子恰 +3 权限码）；
`ck_has_pending_review >= 1`。

## ⑤ 造数（全 ASCII；`<TEAM_ID>` 用你验收团队的 id）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -1 -v ON_ERROR_STOP=1 -c "INSERT INTO app.buyer_account (team_id, label, site, external_customer_id, status, daily_cap) VALUES (<TEAM_ID>, 'R13T-ACC-A', 'amazon_com', 'R13TCUSTA', 'active', 1), (<TEAM_ID>, 'R13T-ACC-B', 'amazon_com', 'R13TCUSTB', 'active', NULL) RETURNING id, label;"
```

再造**三张**可路由渠道订单（收货国 US、行带 amazon 产品 ASIN）与**一张缺 ASIN 单**。
订单/行/product 的造数 SQL 较长，按 `tests/db/test_r2_13b_dispatch.py::_mk_order` 的列清单写，
**product 的形状按下表钉死**（v3 修订：v2 的「O4 置空串 source_ref」与⑫清理的
`LIKE 'R13T%'` 自相矛盾——空串永远清不掉，部署机⑤前停点抓的就是它。改用「非 amazon
渠道」触发缺 ASIN：派发谓词认的是「有 `source_channel='amazon'` 且 source_ref 非空的
产品」，`walmart` 渠道的产品解不出 amazon ASIN，效果等价且可被前缀清理；
`product.source_channel` 无 CHECK 约束、去重键是 (team_id, source_channel, source_ref)，
此形状合法且不撞键）：

| 单 | product.source_channel | product.source_ref | 效果 |
|---|---|---|---|
| `R13T-O1`…`R13T-O3` | `amazon` | `R13T-O1-ASIN` / `R13T-O2-ASIN` / `R13T-O3-ASIN` | 可路由 |
| `R13T-O4` | `walmart` | `R13T-O4-NOASIN` | 缺 ASIN 拦截触发 |

每张订单 `internal_status='checked'`，并各建一张 `status='unassigned'` 的
procurement_order（`R13T-O4` 的也建）。全部 RETURNING id 记回执。
验收账号：确认你的团队账号有 `procurement.buyer_account_admin` /
`procurement.plugin_instance_admin` / `procurement.execute` / `order.assign` / `order.read` 五码，
登录取 `$TOKEN`（不回显）。

**判据**：账号 A（cap=1）/B（不限）建成；四单四执行单在册，id 全记回执。

## ⑥ 实例签发（live 必须签不出来）

```powershell
$IA = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/buyer-accounts/<ACC_A>/plugin-instances" -Method Post -Headers @{ Authorization = "Bearer $TOKEN" } -Body '{"exec_mode":"stop_before_payment","note":"R13T-verify"}' -ContentType "application/json"
"token_len=$($IA.token.Length) token_head=$($IA.token.Substring(0,8))"
try { Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/buyer-accounts/<ACC_A>/plugin-instances" -Method Post -Headers @{ Authorization = "Bearer $TOKEN" } -Body '{"exec_mode":"live"}' -ContentType "application/json" } catch { $_.Exception.Response.StatusCode.value__ }
```

同法给账号 B 签一枚 `stop_before_payment` 实例 `$IB`。

**判据**：正常签发返回 token（回执只记 `token_len`/`token_head`）；**live 签发 = 422**
（审查 B2：live 只能事后 PATCH 显式升档）；列表端点不回显 token（抽查一次 GET）。

## ⑦ 认证与越权（验收③承重）

```powershell
$HA = @{ 'X-Plugin-Instance' = "$($IA.id)"; 'X-Plugin-Token' = $IA.token }
$HB = @{ 'X-Plugin-Instance' = "$($IB.id)"; 'X-Plugin-Token' = $IB.token }
(Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/purchase-plugin/getNeedPurchaseOrders" -Headers $HA -UseBasicParsing).StatusCode
try { Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/purchase-plugin/getNeedPurchaseOrders" -Headers @{ 'X-Plugin-Instance' = "$($IA.id)"; 'X-Plugin-Token' = 'R13T-wrong-token' } -UseBasicParsing } catch { $_.Exception.Response.StatusCode.value__ }
```

三条失败路同码（审查三轮 G3——auth 模块的承诺是「不存在的 id / 错 token / 已吊销
→ 全部 PLUGIN_AUTH + 401」，只测一条不算验过；顺带把吊销这条标准运维路径走一遍）：

```powershell
try { Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/purchase-plugin/getNeedPurchaseOrders" -Headers @{ 'X-Plugin-Instance' = '999999999'; 'X-Plugin-Token' = 'R13T-any' } -UseBasicParsing } catch { $_.Exception.Response.StatusCode.value__; $_.ErrorDetails.Message }
$IC = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/buyer-accounts/<ACC_A>/plugin-instances" -Method Post -Headers @{ Authorization = "Bearer $TOKEN" } -Body '{"exec_mode":"dry_run","note":"R13T-revoke-probe"}' -ContentType "application/json"
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/plugin-instances/$($IC.id)/revoke" -Method Post -Headers @{ Authorization = "Bearer $TOKEN" } | ConvertTo-Json
try { Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/purchase-plugin/getNeedPurchaseOrders" -Headers @{ 'X-Plugin-Instance' = "$($IC.id)"; 'X-Plugin-Token' = $IC.token } -UseBasicParsing } catch { $_.Exception.Response.StatusCode.value__; $_.ErrorDetails.Message }
```

**判据**：正确头 200；错 token / 不存在 id（999999999）/ 已吊销（IC）**三路全部 401 且
报文含同一错误码 `PLUGIN_AUTH`**（逐字相同——同码是 B6 时序抹平那条承诺的另一半）。
IC 是吊销探针专用实例（dry_run 档），吊销后不再使用、⑫无需清理（plugin_instance 随
buyer_account 删除连带）。越权承重在⑧-2（A 拉到的单拿给 B 的实例写）。

## ⑧ 派发链（13b 承重）

**⑧-1 daily_cap=1 只派一单 + 原因码**：A 实例拉取（`getNeedPurchaseOrders`，编码安全读法
取原始字节 UTF-8 解码后 ConvertFrom-Json）→ 应恰返回 **1** 张单（cap=1，池里 ≥2 张可派）；
再拉一次 → 返回同一张（续拉不耗额度）；记该单为 `<POA>`。B 实例拉取 → 返回**其余可派单**
（`R13T-O4` 缺 ASIN 的**不在其中**）。
库内核对：`SELECT id, status, buyer_account_id FROM app.procurement_order WHERE id IN (...)`
→ 被派单 `status='assigned'` 且挂对账号；对应 channel_order `internal_status='assigned'`（A2 对称）。

**⑧-2 越权必败（验收③）**：用 **B 实例**的头对 `<POA>`（A 的单）调 `updateOrderStatus`，
载荷钉死（v4 修订：schema 的 `status` 是 `Literal[99]`，v3 那句「5=已领取之类的合法值」
过不了 Pydantic、422 到不了归属闸——部署机⑥前预检抓的）：

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/purchase-plugin/updateOrderStatus" -Method Post -Headers $HB -Body '{"id": <POA>, "status": 99, "failReason": "R13T-cross-account"}' -ContentType "application/json" -UseBasicParsing
```

**判据**：**HTTP 403** 且报文含 `PLUGIN_TASK_NOT_OWNED`（载荷合法所以拒绝只能来自
实例↔账号归属闸，这才是验收③要的证据）；库内 `<POA>` 一字未动
（status/exception_kind/exception_reason 三列复查为派发后原值）。

**⑧-3 缺 ASIN 拦截**：`R13T-O4` 的执行单始终 `unassigned`、`buyer_account_id IS NULL`；
`app.notification` 有 `plugin.no_asin.<该单id>` 的 dedupe_key 告警行（判据认 dedupe_key，ASCII）。

**⑧-4 混派闸（A2）**：对 `R13T-O1`（已被插件派出）经人工端点再建执行单
`POST /procurement-orders`（body 带该 order_id）→ **409 PROCUREMENT_ORDER_IN_FLIGHT**；
人工 claim `<POA>` → **409 PROCUREMENT_PLUGIN_DISPATCHED**（A1）。

## ⑨ 回填链：金额与状态机（审查二轮点名重点位，数值逐字入回执）

**⑨-1 stop_before_payment 档抓金额不落单**：A 实例对 `<POA>` 调
`purchaseOrderFinishUpdate`（totalBeforeTax `$9.99` / tax `$0.80` / shipping `$0.00` /
total `$10.79`，**不带** platformOrderNo 或带也一样——该档不写 ref）。
库内判据：`purchase_cost=9.99`、`tax_amount=0.80`、`freight_cost=0.00`、
`purchase_currency='USD'`、`exchange_rate_locked=1.0`、**`purchase_order_ref IS NULL`**、
**`purchased_at IS NULL`**、**`status='assigned'` 不动**、channel_order **不进 purchasing**。

**⑨-2 金额自校验不平 → 落异常但数据照实入库（D2/D1）**：再调一次，totalBeforeTax
`$5.00`、total `$10.79`（不平）→ 库内 `exception_kind` 落值、金额列**照实更新为 5.00**、
`status` 仍不动；notification 出现对应告警（dedupe_key ASCII）。

**⑨-3 负数拒收（B5）**：totalBeforeTax `-$3.20` → 该列**保持上一次值**（不写负数不写 0）、
告警/日志路径触发；HTTP 200（解析失败不断链）。

**⑨-4 人工补录与释放互斥（Owner 异常#16 裁定承重）**：
a. 把 `<POA>` 手工标异常：`POST /procurement-orders/<POA>/exception`（reason `R13T-fail-16`）
   → `status='exception'`；
b. **人工补录**：`POST /procurement-orders/<POA>/plugin-backfill`
   （body `{"purchase_order_ref":"113-R13T0001-0000001","purchase_cost":10.79}`）→ 200；
   库内 `status='purchased'`、`purchase_order_ref` 落、`backfill_actor_kind='op_direct'`、
   `exception_kind IS NULL`、channel_order → `purchasing`；
c. 再标异常（exception_po 允许）→ `POST /procurement-orders/<POA>/release` →
   **409 PROCUREMENT_ALREADY_PURCHASED**（补录后释放必被拒——误释放重买的洞闭合）；
d. **释放正路**：取 B 实例名下一张已派单 `<POB>`，标异常（不回填）→ release → 200，
   库内 `status='unassigned'`、`buyer_account_id IS NULL`、channel_order 退回 `checked`；
   B 再拉取 → `<POB>` **重新出现在候选**（释放后真能重派）。

**⑨-5 迟到重试幂等**：对 `<POA>`（已人工补录）用 A 实例再调 `purchaseOrderFinishUpdate`
带**同一** platformOrderNo `113-R13T0001-0000001` → 响应 `data.idempotent=true`，
金额不被覆盖；换一个**不同**单号再调 → 响应 conflict 形状、库内原 ref 一字不动、
notification 出现 `plugin.dup_ref.<POA>` critical 告警。

## ⑩ 物流链（13d，D6 承重）

对 `<POA>` 调 `updateTrackingInfo`（carrier `R13T-UPS`、trackingNo `1ZR13T000001`、
trackingJson 三条 ASCII 事件）→ 库内 `carrier`/`tracking_no` 落、`status='shipped'`、
`procurement_logistics_event` 恰 3 行 seq 0/1/2；**channel_order 的 `internal_status`
不因此变化**（D6：亚马逊包裹在途 ≠ 我方向 Walmart 发货）。重发同载荷 → 行数仍 3（upsert 幂等）。

## ⑪ ③级表承重（对①基线）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.audit_log) AS audit_rows_now, (SELECT count(*) FROM app.channel_order WHERE channel_order_no NOT LIKE 'R13T-%') AS channel_orders_now, (SELECT count(*) FROM app.order_line ol WHERE NOT EXISTS (SELECT 1 FROM app.channel_order co WHERE co.id = ol.order_id AND co.order_date = ol.order_date AND co.channel_order_no LIKE 'R13T-%')) AS order_lines_now;"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT md5(coalesce(string_agg(id || ':' || coalesce(purchaser_id::text, '-'), ',' ORDER BY id), '')) AS po_fingerprint_now FROM app.procurement_order WHERE id <= <PO_MAX_ID_BEFORE>;"
```

另跑锚定等值（审查三轮 G1——`>=` 是单边判据，「删 5 插 20」也能过；锚定后对新增免疫、
对删除敏感，才是「一行不删」的精确判据）：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS audit_baseline_rows_now FROM app.audit_log WHERE id <= <AUDIT_MAX_ID_BEFORE>;"
```

**判据**：`channel_orders_now` / `order_lines_now` 与①同名项**相等**（排除 R13T 测试族后）；
`po_fingerprint_now` 与①**逐字相同**（存量执行单的 id:purchaser_id 无一变化）；
**`audit_baseline_rows_now = ①的 audit_rows_before`**（锚定等值：基线时刻已存在的审计行
一行未删；本轮新增的签发/补录/释放留痕都在锚之外，不影响该判据）。

## ⑫ 清理 + 迁移可逆 + 终态

**清理（单事务，前缀精确圈定；顺序：物流事件→执行单→order_check→order_line→channel_order→
product(R13T 的 source_ref 族)→plugin_instance→buyer_account→notification(R13T/plugin.% 且
object 指向本次测试单)）**：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -1 -v ON_ERROR_STOP=1 -c "DELETE FROM app.procurement_logistics_event WHERE procurement_order_id IN (SELECT po.id FROM app.procurement_order po JOIN app.channel_order co ON co.id = po.order_id AND co.order_date = po.order_date WHERE co.channel_order_no LIKE 'R13T-%'); DELETE FROM app.procurement_order WHERE order_id IN (SELECT id FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%'); DELETE FROM app.order_check WHERE order_id IN (SELECT id FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%'); DELETE FROM app.order_line WHERE order_id IN (SELECT id FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%'); DELETE FROM app.channel_order WHERE channel_order_no LIKE 'R13T-%'; DELETE FROM app.product WHERE source_ref LIKE 'R13T%'; DELETE FROM app.plugin_instance WHERE buyer_account_id IN (SELECT id FROM app.buyer_account WHERE label LIKE 'R13T-%'); DELETE FROM app.buyer_account WHERE label LIKE 'R13T-%'; DELETE FROM app.notification WHERE object_type = 'procurement_order' AND object_id = ANY (ARRAY['<PO1>','<PO2>','<PO3>','<PO4>']);"
```

> buyer_account 无 DELETE 授权是应用角色（erp_app）的限制；本清理走 **erp_migrator**（DDL 角色），
> 可删。⚠️ 铁律 4 的三级表例外仅限 R13T 前缀族——上面每条 WHERE 都已带前缀圈定，
> **不许放宽**。notification 那条按**对象圈定**（object_id = 本次四张测试执行单的 id，⑤记下的值代入
`<PO1>`-`<PO4>`）——审查三轮 G2：`plugin.%` 是产品自己的 dedupe 前缀，拿它清会在插件
真上线后连真实告警一起删；对象圈定与其余各条的前缀圈定同一精神——只删本次测试造出来的。

**迁移可逆**（v2 补具体命令——migrate 服务的默认命令是 upgrade head，降级要**覆盖命令**跑）：

```powershell
docker compose -f infra/docker-compose.yml run --rm migrate alembic downgrade 0047
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT version_num FROM alembic_version;"
docker compose -f infra/docker-compose.yml run --rm migrate
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT version_num FROM alembic_version;"
docker compose -f infra/docker-compose.yml restart beat
```

判据：降级后 `version_num=0047` + 三张新表消失 + procurement_order 新列消失 + perms 回到①值；
回升后 `version_num=0046` + ④的结构判据复核全过；beat 重启恢复 Up。

**终态**：①的三个③级计数复核相等；六服务 Up（db/redis healthy）；工作树无已跟踪改动；
残留 `R13T` 行数全 0。

---

**回执格式**：逐步贴判据实测值（承重步⑨全部数值逐字）；结尾写明被验代码 sha、指令版本 sha、
停机次数与性质。验证过程中任何一步判据不满足：**停手贴现场**，等修指令，不自行变通。
