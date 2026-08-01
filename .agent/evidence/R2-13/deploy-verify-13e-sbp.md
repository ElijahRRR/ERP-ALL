# R2-13 13e 前半 ③闸真机联调单 v1（stop_before_payment 走一单到付款前；E 编号，与 R2-17 的 D、清场单的 C 不混）

> **给部署 AI。整段可粘贴，逐步执行，每步贴回输出。**
> 铁律照旧：不输出密钥（token 只记 `token_len=<N>`）；audit_log 一行不删；
> **本单唯一允许的本地改动 = fork 仓 `js/popup.js` 顶部 CONFIG 两个构建期常量**
> （baseUrl 与 token，装载浏览器用），**改完不 commit、不 push**；一步不过就停下来
> 贴现场，不自行补救。
>
> ⛔ **红线（先做后验，全单最高优先）：同一浏览器配置内绝不同时启用两个采购插件
> ——会重复下单、损失真金白银。** E3 装 fork 版之前，必须先在该指纹浏览器
> `chrome://extensions` 里把厂商原版「小蜜蜂AMZ采购助手」**停用或移除**，回执贴一句
> 「已确认原版已停用/移除」。fork 版名称为「AMZ采购助手（ERP fork）」，两者肉眼可辨。
>
> **本档不花钱**：stop_before_payment 走完结账页、抓到实付金额与预计送达后**停在
> 点付款之前**，不产生真实订单。服务端对未知/未配置档位也回落本档（fail-safe），
> 但 E2 仍要显式写配置——验收要的是「配置了本档且生效」，不是「碰巧回落」。
>
> **前置**：R2-17 已收口（单人模式常驻、`.env` 已有 `ERP_PLUGIN_SHARED_TOKEN`，
> ③闸 D5 实测过 401↔200）；现场 `main` 与 DB `0048`、healthz 200。

## E1 ERP 侧：检出验证分支、部署、CORS 双探针

```powershell
cd D:\项目文件\ERP-ALL
git status --porcelain
git fetch origin claude/r2-03-launch-leg5n8
git checkout claude/r2-03-launch-leg5n8
git merge --ff-only origin/claude/r2-03-launch-leg5n8
git rev-parse HEAD
git rev-parse origin/claude/r2-03-launch-leg5n8
docker compose -f infra/docker-compose.yml build api
docker compose -f infra/docker-compose.yml run --rm migrate
docker compose -f infra/docker-compose.yml up -d --force-recreate api
Start-Sleep -Seconds 8
(Invoke-WebRequest -Uri "http://127.0.0.1:8000/healthz" -UseBasicParsing -TimeoutSec 5).StatusCode
$ok = Invoke-WebRequest -Method Options -Uri "http://127.0.0.1:8000/api/v1/purchase-plugin/getNeedPurchaseOrders" -UseBasicParsing -TimeoutSec 5 -Headers @{"Origin"="https://www.amazon.com"; "Access-Control-Request-Method"="GET"; "Access-Control-Request-Headers"="X-Plugin-Token"}
$ok.StatusCode; $ok.Headers["Access-Control-Allow-Origin"]; $ok.Headers["Access-Control-Allow-Headers"]
$bad = Invoke-WebRequest -Method Options -Uri "http://127.0.0.1:8000/api/v1/purchase-plugin/getNeedPurchaseOrders" -UseBasicParsing -TimeoutSec 5 -SkipHttpErrorCheck -Headers @{"Origin"="https://evil.example"; "Access-Control-Request-Method"="GET"}
"bad_acao=[$($bad.Headers["Access-Control-Allow-Origin"])]"
```

**判据**：两条 `rev-parse` 逐字相等（锚定等值）；migrate **no-op**（本 PR 无迁移，
`version_num` 不变）；healthz 200；正探针 `204` + `Access-Control-Allow-Origin` 逐字
回声 `https://www.amazon.com` + Allow-Headers 含 `X-Plugin-Token`；反探针
`bad_acao=[]`（空——非 amazon 源零 CORS 头）。

## E2 执行档配置（显式写 stop_before_payment）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -q -tA -c "INSERT INTO app.system_config (key, value) VALUES ('procurement.plugin_exec_mode', to_jsonb('stop_before_payment'::text)) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value RETURNING key || '=' || value;"
```

**判据**：回显 `procurement.plugin_exec_mode="stop_before_payment"`。

## E3 插件侧：检出 fork 分支、填常量、装载

```powershell
cd D:\项目文件\AMZ-Purchase-Assistant   # 没有则先 git clone 该仓到此路径
git fetch origin claude/r2-03-launch-leg5n8
git checkout claude/r2-03-launch-leg5n8
git merge --ff-only origin/claude/r2-03-launch-leg5n8
git rev-parse HEAD
git rev-parse origin/claude/r2-03-launch-leg5n8
```

然后**手工两步**（本单唯一允许的本地改动；不 commit 不 push）：
1. 编辑 `js/popup.js` 顶部 `CONFIG.API`：`baseUrl` 改为 `http://127.0.0.1:8000/api/v1`；
   `token` 改为 `.env` 里 `ERP_PLUGIN_SHARED_TOKEN` 的值。
   ⚠️ **指纹浏览器必须跑在部署机本机**：amazon 页面是 https，content script 的 fetch
   打到 `http://<内网IP>` 会被 Chrome 按混合内容拦死，唯一豁免是 loopback
   （`http://127.0.0.1`）。跨机形态等 RS-02b（HTTPS 反代）落地后另起验证单。
2. 选一个**测试用指纹浏览器**（登着真实买家号）：先停用厂商原版插件（红线，见上），
   再 `chrome://extensions` → 开发者模式 → 「加载已解压的扩展程序」→ 选本仓目录。

**判据**：两条 `rev-parse` 逐字相等；扩展列表出现「AMZ采购助手（ERP fork）」版本
`2.4.1.1`；回执贴「已确认原版已停用/移除」+ `token_len=<N>`（不贴明文）。

## E4 首见登记 + 指派 + 走单到付款前（本单核心）

1. 浏览器开 amazon.com 订单列表页（插件注入面板的页面）→ 插件面板点**开始拍单**。
   预期：空手而归（新号名下无派单），但 ERP 完成首见登记。
   ```powershell
   docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -q -tA -c "SELECT id || '|' || status || '|' || coalesce(label,'<NULL>') FROM app.buyer_account ORDER BY id DESC LIMIT 1;"
   ```
   **判据**：新行 `status=active`、`label=<NULL>`（17d 首见即 active）。
2. ERP 订单页：选一张**已核可（checked）**、行上 `source_ref` 为在售 ASIN、金额小的
   真实渠道订单 → 「选择采购账号（插件）」下拉选中刚登记的账号 → 点**指派**。
   （账号 label 为空时下拉按 customerId 显示；也可先在买家账号处补 label 再指派。）
3. 插件面板再点**开始拍单**。预期链路：拉到该单 → 清购物车 → 加购 → 填地址 →
   进结账页 → 抓金额/预计送达/卡后四位 → **停在点付款之前**，日志出现
   「已停在付款前一步」字样；浏览器停留在结账页，**未产生任何亚马逊订单**。
4. ERP 侧取证：
   ```powershell
   docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -q -tA -c "SELECT id || '|' || status || '|' || coalesce(purchase_order_ref,'<NULL>') || '|' || coalesce(purchase_cost::text,'<NULL>') || '|' || coalesce(tax_amount::text,'<NULL>') || '|' || coalesce(freight_cost::text,'<NULL>') || '|' || coalesce(delivery_est_date::text,'<NULL>') || '|' || coalesce(payment_card_last4,'<NULL>') FROM app.procurement_order WHERE buyer_account_id IS NOT NULL ORDER BY id DESC LIMIT 1;"
   docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -q -tA -c "SELECT note FROM app.procurement_order WHERE buyer_account_id IS NOT NULL ORDER BY id DESC LIMIT 1;"
   ```
   **判据**：`status` 仍为 `assigned`（演练档**不改状态**）；`purchase_order_ref=<NULL>`
   （没下单就没有单号）；`purchase_cost`/`tax_amount`/`freight_cost`/`delivery_est_date`/
   `payment_card_last4` **有值**（结账页抓回）；`note` 含
   `[stop_before_payment 演练 instance=0]` 痕迹行。
5. 亚马逊侧人工一步：该买家号订单历史里**没有**新订单——贴一句「已目检，零新单」。

## E5 反向探针（fail-closed 两条）

1. `js/popup.js` 里把 `token` 常量临时改错一位 → 刷新页面重点**开始拍单**。
   **判据**：插件日志出现 401/认证失败类可读报错（CORS 401 也带头，读得到错误体），
   **无任务拉回**。改回正确值。
2. 把 `token` 常量清成占位串 `YOUR_PLUGIN_SHARED_TOKEN` → 刷新重点。
   **判据**：插件日志明示「未配置，拒绝启动」类信息，**一条请求都不发**
   （F12 Network 面板零 purchase-plugin 请求）。改回正确值，刷新确认恢复可拉。

## E6 终态回执

全过 ⇒ 回执贴回 ERP-ALL 侧 PR：E1 两 sha + CORS 双探针输出、E2 回显、E3 装载与
红线确认 + token_len、E4 五步输出（psql 原文 + 两句目检）、E5 两条判据结论。
任何一步不过 ⇒ 停点 + 现场原文，等定性。
