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

## 故障处置速查

| 症状 | 动作 |
|---|---|
| 页面打不开 | `make logs` 看 api；`docker compose ps` 看容器状态 |
| 机器重启后服务没起 | compose 服务默认 `restart: unless-stopped`（deploy workflow 会补齐）；手动 `make up` |
| 磁盘告警 | 先清 `~/erp-backups` 过期文件与 docker 悬空镜像 `docker system prune` |
| 误删数据 | 立即停写，用最近备份 pg_restore；**不要**在原库上做实验 |

## 变体组运维（R2-11，D-Q63）

- **组哪来**：beat `variant_group_sync`（每小时 :40）从采集素材自动归组（只信 twister）；
  也可在产品页/接口人工建组、全量设成员。手动单跑：
  `docker compose -f infra/docker-compose.yml exec api python -m erp.tools.run_task variant_group_sync`。组状态 broken（成员不齐/维度冲突/超上限
  `variant.max_group_size`，默认 10）时 spec 构建与提交整组拒绝，修复后自动回 active。
- **整组上架**：组全体成员同店同批提交（缺员会整组拒绝并列出缺席成员；同店已在架/在途
  成员算在场——补投单个失败成员直接重投即可）。首次成功入列即锁定 anchor 店，之后只能
  在 anchor 店上架（不自动转移）。
- **anchor 处置（暂人工，增量3 复审口径）**：组首发即被渠道整体驳回、想换店重投时，
  确认组内无任何 live/在途成员后执行（一次性容器/只读核实先行）：
  `UPDATE app.variant_group SET anchor_store_id = NULL WHERE id = <组id>;`
- **验收演练（R2-11 增量3）**：①A152 采集一组带变体的真实 ASIN（≥3 成员）→ 等归组任务
  （或手动触发）→ 审核通过 → 同批分配+提交 → Walmart 后台确认 variant group live；
  ②故意少分配一个成员提交 → 应见 VARIANT_GROUP_INCOMPLETE 及缺席成员明细。

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
