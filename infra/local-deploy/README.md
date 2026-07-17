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
  也可在产品页/接口人工建组、全量设成员。组状态 broken（成员不齐/维度冲突/超上限
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
