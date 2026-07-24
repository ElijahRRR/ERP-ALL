# 本地部署 Runbook（D-Q52：试点期全本地）

> 目标机器：Owner 一台**常开**的机器（macOS / Windows+WSL2 / Linux 均可，建议 ≥16G 内存、SSD ≥100GB 空闲）。
> 告诉远端 agent 机器的系统与配置后，本文会按实际环境细化。

## 一次性安装（约 30 分钟）

1. 装 Docker：macOS/Windows 装 Docker Desktop；Linux 装 docker-ce + compose 插件。
2. 克隆仓库并配置环境：
   ```bash
   git clone https://github.com/ElijahRRR/ERP-ALL.git && cd ERP-ALL
   cp backend/.env.example backend/.env
   # 编辑 backend/.env：把两个 dev-only-change-me 换成强随机串（生成：openssl rand -hex 32）
   ```
3. 起全栈：
   ```bash
   make up          # db + redis + migrate + api
   curl http://localhost:8000/healthz   # 应返回 {"status":"ok",...}
   ```
4. 配置每日备份（见下节）——**没配备份不算部署完成**。
5. （推荐）装 GitHub self-hosted runner 实现自动部署：
   仓库 Settings → Actions → Runners → New self-hosted runner，按页面命令安装为服务；
   之后 CI 绿会自动在本机拉新版重启（deploy workflow 由 R1 后续工单提供）。

## 每日备份（D-Q52 红线）

```bash
# 试跑一次
bash infra/local-deploy/backup.sh
# 挂定时：macOS/Linux 用 cron（crontab -e）：
# 30 2 * * * cd /path/to/ERP-ALL && bash infra/local-deploy/backup.sh >> ~/erp-backups/backup.log 2>&1
# Windows 用任务计划程序，触发器每日 02:30
```

- 本地保留 14 天；**异地一份**：装 [rclone](https://rclone.org) 配置任意云盘/OSS 后，设置环境变量
  `RCLONE_REMOTE=<remote>:<bucket>/erp-backups`，脚本会自动上传。
- 恢复演练（每月一次，进 automation 提醒）：
  ```bash
  docker compose -f infra/docker-compose.yml exec -T db pg_restore -U postgres -d erp_all_restore_test --create <备份文件>
  ```

## 团队访问

- 同办公室/内网：浏览器访问 `http://<这台机器内网IP>:5173`（前端）——R1-05 后可用。
- 异地成员/外部门户：**暂不开放公网**；R2#6 门户上线前按 D-Q52 检查点评估迁云或穿透方案。

## 增量验证流程（2026-07-18 起：先分支后合并）

增量一律先在 PR 分支上验证，通过后 Owner 授权合并 main，再继续开发（main 恒为
「CI 绿 + 真机验过」）：

```bash
# 注意：开发分支每次合并后会从 main 强制重建（历史重写），pull 无法 fast-forward——
# 分支对齐一律 fetch + reset --hard（部署机铁律不改码，本地恒无自产提交，重置零丢失）
git fetch origin <PR分支> && git checkout <PR分支> && git reset --hard origin/<PR分支>
git log -1 --oneline        # 前置核验：应含该增量标题/短哈希（以指令块为准）
make up                     # 重建 api+beat+frontend；migrate 退出码应为 0
# …按该增量的核验指令块执行，回报结果；验证通过、Owner 合并后：
git checkout main && git pull && make up   # 部署机切回 main 常驻
```

- 含迁移的增量：分支验证会把库 schema 推前；若增量最终被弃，须先 `alembic downgrade`
  归位再切回 main（增量门槛本就要求迁移 up/down 实测过）。

## 故障处置速查

| 症状 | 动作 |
|---|---|
| 页面打不开 | `make logs` 看 api；`docker compose ps` 看容器状态 |
| 机器重启后服务没起 | compose 服务默认 `restart: unless-stopped`（deploy workflow 会补齐）；手动 `make up` |
| 磁盘告警 | 先清 `~/erp-backups` 过期文件与 docker 悬空镜像 `docker system prune` |
| 误删数据 | 立即停写，用最近备份 pg_restore；**不要**在原库上做实验 |

## 变体组运维（R2-11，D-Q63/D-Q64）

- **组哪来（D-Q64① 实时归组已落地）**：采集入库即时归组（product_upsert 同事务，只信
  twister；维度键不在映射表会当场发 warn 预警）；beat `variant_group_sync`（每小时 :40）
  降为兜底收敛（跨批解散重归/broken 复评/漏网扫描）；也可在产品页/接口人工建组、
  全量设成员。手动单跑：
  `docker compose -f infra/docker-compose.yml exec api python -m erp.tools.run_task variant_group_sync`。组状态 broken 仅剩真错误（维度冲突/维度值缺失，
  判定 v2 D-Q64③）；成员<2 与超上限不再 broken（超 `variant.max_group_size` 只发
  oversize warn 观察）。旧 v1 误置 broken 的组每轮归组自动复评回 active（healed 计数）。
- **自动路由上架（默认档，D-Q64②③）**：刊登页默认「自动路由」（`variant_mode=group`）——
  混批一 feed：已归组成员自动携 VG 段成组/追加（**子集即可，不要求全家齐**；首个子集
  成功入列即锁定 anchor 店，之后该组只能在 anchor 店追加不自动转移），未归组产品自动
  散品路径，与旧系统混上语义一致。本批同组任一成员构建失败该组整批拒绝（批次原子性，
  可见原因）；单批上限 `variant.max_batch_members`（默认 200）。
- **散品上架（覆盖开关，D-Q64②）**：切「散品上架」（`variant_mode=standalone`）——
  整批强制散品：不带 VG 段、不锁 anchor、不受组守卫限制（broken 组成员也能散着上）。
- **散转组补挂（D-Q64④）**：已 live 的组成员补挂 VG 段成组：
  `POST /api/v1/listings/variant-regroup`，body `{"group_id": 组id, "store_id": 店id}`
  （需 listing.submit + Idempotency-Key 头）。按 SKU 更新 MP_ITEM feed 重投，成员保持
  live；失败只返还配额、不动 listing 状态、不释放 GTIN。组 broken/anchor 异店/无在架
  成员/超单批上限 → 422 整批拒绝。
- **anchor 处置（端点化，不再手工 SQL）**：组首发即被渠道整体驳回、想换店
  重投时，调 `POST /api/v1/variant-groups/{组id}/anchor/release`（需 catalog.product_write，
  审计留痕）。端点自带 fail-closed 核实：锚定店仍有在途/在架成员（queued/submitted/
  published/live）会 409 拒绝，须先撤除/下架整组。
- **验收演练（D-Q64 版）**：①组 8 现场修复——`variant-regroup` 补挂后等 feed processed，
  Walmart 后台确认 9 员并成一个 variant group（`SELECT anchor_store_id FROM
  app.variant_group WHERE id=8;` 应为 1）；②组 6 子集上架——不摘审核不过成员，直接把
  可上的 3 员同批分配+提交，应正常出门并锁 anchor（不再见 VARIANT_GROUP_INCOMPLETE）；
  ③散品模式——选组内成员用「散品上架」提交，feed 条目应无 variantGroupId。

## 封店工作流演练（R2-07 07b，D-Q33）

验收②：登记封店事件 → 品牌占用批量释放 → 定时提醒送达 → resolved 恢复。测试验收店 = **A152**。
下述 SQL 全为**只读核对**（SELECT），不含任何改库操作；连库统一走：

```bash
PSQL="docker compose -f infra/docker-compose.yml exec -T db psql -U postgres -d erp_all -c"
```

1. **前置：制造品牌占用**（build 模式已分配产品才会占用品牌）。
   前端「上架管理 → 分配上架」对 **A152** 分配若干 build 模式、带品牌的产品；成功后
   `app.brand_assignment` 新增 `status='occupied'` 行。只读核对当前占用：
   ```bash
   $PSQL "SELECT ba.id, ba.brand_display, ba.status FROM app.brand_assignment ba
          JOIN app.store s ON s.id = ba.store_id
          WHERE s.code = 'A152' AND ba.status = 'occupied' ORDER BY ba.id;"
   ```
   期望 ≥1 行 occupied；若为空，先回上一步分配产品。

2. **登记封店事件**（前端）。「店铺事件」页 → 「登记事件」→ 店铺选 A152、类型选「封店
   suspension」、原因随填 → 提交。表单会红字提示"将立即置店铺为
   suspended 并批量释放品牌占用"。
   **发生时间请回填 ≥ remind_days（默认 7）天前**——提醒任务按"已封天数 ≥ remind_days"
   触发，发生时间填现在则当日不会产生提醒（需等满一个周期）。

3. **核对联动结果**（只读）。
   ```bash
   # ① 店铺置 suspended（期望 status=suspended、suspended_at 非空）
   $PSQL "SELECT id, code, name, status, suspended_at FROM app.store WHERE code = 'A152';"
   # ② 新事件行 + 品牌释放回填（期望最新一行 incident_kind=suspension、brand_released_at 非空）
   $PSQL "SELECT id, store_id, incident_kind, status, occurred_at, brand_released_at, sku_released_at
          FROM app.store_incident ORDER BY id DESC LIMIT 3;"
   # ③ 品牌占用批量释放（期望先前 occupied 行全部 status=released、release_reason=suspension、
   #    incident_id=新事件 id、released_at 非空）
   $PSQL "SELECT ba.id, ba.brand_display, ba.status, ba.released_at, ba.release_reason, ba.incident_id
          FROM app.brand_assignment ba JOIN app.store s ON s.id = ba.store_id
          WHERE s.code = 'A152' ORDER BY ba.id;"
   ```
   前端「店铺事件」页下半「品牌占用」表按 A152 过滤，应同样看到这些行已 released。

4. **触发/等待封店提醒**（beat `suspension_reminder` → notification）。
   - 自动：beat 调度器按 `app.schedule` 中 `suspension_reminder` 的 `remind_days` 周期自动派发，
     命中未闭合的 suspension 事件后写 notification（前端右上角通知铃 / 「通知中心」可见）。
   - 手动一次性（`erp.tools.run_task` 通用单跑工具，config 与 beat 同源读 app.schedule）：
     ```bash
     docker compose -f infra/docker-compose.yml exec api python -m erp.tools.run_task suspension_reminder
     ```
   - 只读核对提醒已生成：
     ```bash
     $PSQL "SELECT id, category, title, created_at FROM app.notification
            ORDER BY created_at DESC LIMIT 5;"
     ```
     期望出现封店/申诉提醒条目。

5. **resolved 流转恢复**。「店铺事件」页 → 该 suspension 行 → 「推进状态」→ 目标选「已解决
   resolved」（弹窗提示店铺将恢复 active）→ 确认。只读核对：
   ```bash
   $PSQL "SELECT id, code, status FROM app.store WHERE code = 'A152';"
   ```
   期望 `status=active`。注意：resolved 只恢复店铺 active（人工确认，§02:187）；已释放的品牌
   占用不因 resolved 自动回占，需要时在「品牌占用」重新分配。

## USPTO 商标供给链（R2-12 增量3，D-Q65①）

> 定位：抓取/解析/日度增量**整链驻部署机**（旧仓 walmart-trademark-sync 的
> `daily_update` 保持本机定时跑）；新系统只做两件事——**导入**（`bulk_import_trademark`）
> 与**新鲜度守卫**（beat `trademark_freshness` 告警）。新系统永不直连 USPTO。

### 日常链路（部署机每日）

1. 旧仓 `daily_update` 定时产出日增量文件（csv/jsonl，列含 serial_number /
   mark_identification / status_code / filing_date …，列名兼容旧仓导出）。
2. 拷入 api 容器并导入（幂等，重导安全）：
   ```bash
   docker compose -f infra/docker-compose.yml cp /path/to/daily-YYYYMMDD.csv api:/tmp/
   docker compose -f infra/docker-compose.yml exec api \
     python -m erp.tools.bulk_import_trademark --file /tmp/daily-YYYYMMDD.csv
   # 中断续跑（同文件同 batch_size）：追加 --resume；错误行看同目录 *.errors.jsonl
   ```
3. 对账口径（每次导入后）：
   - 工具输出 `total/merged/err` 与旧仓 daily_update 报告的行数一致（err 行逐条看
     errors.jsonl，常见为 serial/mark 缺失）；
   - 只读 SQL 复核总量与最新申请日、revision 递增（审核 R5 反查即时生效，无缓存）：
     ```bash
     $PSQL "SELECT count(*) AS total, max(filed_date) AS newest FROM refdata.trademark;"
     $PSQL "SELECT revision FROM refdata.dataset_revision WHERE dataset = 'trademark';"
     ```

### 新鲜度守卫（新系统侧自动）

- beat `trademark_freshness`（默认每日 10:00 UTC，`app.schedule` 可改）检查
  `max(filed_date)` 距今滞后：> 7 天 warn / > 14 天 critical / 库空恒 critical
  → 通知中心告警（每日至多一条）。
- **告警出现 = 部署机链路断了**：依次检查本机 daily_update 定时是否在跑、
  导出文件是否生成、第 2 步导入是否执行成功。
- 阈值运营可改（零硬编码）：`app.schedule.config`（warn_days/critical_days）或
  `app.system_config`（`trademark.freshness_warn_days` / `trademark.freshness_critical_days`，
  后者优先）。
- 手动单跑核验：
  ```bash
  docker compose -f infra/docker-compose.yml exec api \
    python -m erp.tools.run_task trademark_freshness
  ```
