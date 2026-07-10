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
