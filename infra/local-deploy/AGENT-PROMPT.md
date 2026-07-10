# 本机部署智能体任务书（Windows 11，全权执行版）

> 你是在 Owner 的 Windows 11 台式机上执行 ERP-ALL 本地部署的 agent。
> 唯一操作依据：仓库 `infra/local-deploy/windows.md`（本任务书只补充约束与验收，不替代它）。
> 仓库已由 Owner 克隆在 `D:\项目文件\ERP-ALL`（Git Bash 路径 `/d/项目文件/ERP-ALL`），跳过 clone，
> 先 `git pull` 取最新 main 再开始。

## 硬约束（违反即停）

1. **密钥永不出机**：`backend/.env` 里的密钥、超管初始密码——只落在本机文件，
   不得贴进任何对话/日志/云端，不得 commit。`.env` 已在 .gitignore，确认后再动。
2. **只部署，不改码**：不修改仓库任何代码/配置文件（`backend/.env` 是本机新建文件，不算）；
   不向仓库 push 任何东西。
3. **系统操作最小化**：只执行 windows.md 列出的动作（装 Docker/Git、写 .wslconfig、
   电源与更新设置、防火墙单条规则、任务计划单条）。分区、删文件、改 BIOS、
   装任何未列出的软件——不做，遇到需要就停下问 Owner。
4. 卡住或报错：原样收集报错文本，停在当前步，报告 Owner（由 Owner 转给远端 agent），
   不要自行"创造性绕过"。

## 执行步骤

按 `infra/local-deploy/windows.md` 第 1→2→3→4 步顺序执行，外加一步：

**第 2.5 步（windows.md 起系统成功后）：创建初始超管**
```bash
# Git Bash，仓库根目录。密码：生成 16 位随机串，存入本机 D:\项目文件\erp-secrets.txt（仓库目录外）（连同 .env 里两个密钥的备份）
PW=$(openssl rand -base64 16)
echo "super 初始密码: $PW" >> /d/项目文件/erp-secrets.txt
docker compose -f infra/docker-compose.yml exec -e ERP_BOOTSTRAP_PASSWORD="$PW" api python -m erp.bootstrap admin
```
然后告诉 Owner：「超管账号 admin，初始密码在 D:\项目文件\erp-secrets.txt，请登录验证并妥善保管该文件」。
**不要把密码本身写进你与 Owner 的对话。**

## 验收清单（全部✓才算完成）

| # | 检查 | 证据 |
|---|---|---|
| 1 | `curl http://localhost:8000/healthz` 返回 ok | 命令输出 |
| 2 | 局域网另一台设备打开 `http://<固定IP>:5173` 能见登录页 | Owner 手机/其他电脑确认 |
| 3 | Owner 用 admin 登录成功、能看到工作台 | Owner 确认 |
| 4 | `bash infra/local-deploy/backup.sh` 产出 >10KB 的 dump 文件 | 文件路径+大小 |
| 5 | 任务计划程序存在每日 02:30 的备份任务 | 截图或 schtasks 查询输出 |
| 6 | 重启电脑后服务自动恢复（Docker 自启 + 容器 restart 策略） | 重启一次实测 healthz |
| 7 | （推荐）self-hosted runner 显示 Idle | 仓库 Settings→Runners 状态 |

## 完成后报告格式

向 Owner 汇报：验收清单逐项✓/✗ + 固定内网 IP + 偏离/遗留事项列表。
rclone 异地备份若 Owner 未提供网盘，标记为「待 Owner 配置」不阻塞验收。
