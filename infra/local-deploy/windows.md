# Windows 11 部署手册（Owner 机器实配版）

> 目标机：Win11 Pro 24H2 · Ultra 7 265K (20核) · 48GB DDR5 · 990 PRO 2TB —— 远超需求，直接用。
> 本手册是 README.md 的 Windows 落地版，照抄执行即可；遇到卡点把报错发给远端 agent。

## 第 1 步：一次性系统准备（约 20 分钟）

1. **装 Docker Desktop**：https://www.docker.com/products/docker-desktop/ 下载安装，
   安装时勾选 WSL2 backend；装完在 Settings → General 勾选 **Start Docker Desktop when you sign in**。
2. **限制 WSL 内存**（防止 Docker 吃满 48G 影响你日常使用）：
   在 `C:\Users\<你>\.wslconfig` 写入：
   ```ini
   [wsl2]
   memory=20GB
   processors=12
   ```
   然后 PowerShell 执行 `wsl --shutdown`（Docker Desktop 会自动重启）。
3. **电源设置**：设置 → 系统 → 电源 → 屏幕和睡眠 → **睡眠：从不**（睡眠 = 整套系统对团队下线）。
4. **Windows 更新缓冲**：设置 → Windows 更新 → 高级选项 → 使用时段设为你们的工作时间，
   避免工作时间自动重启（更新重启后 Docker 自启、服务自动恢复，但会中断几分钟）。
5. **固定内网 IP**：路由器管理页 → DHCP 静态绑定，把本机 MAC 绑一个固定 IP（如 192.168.1.100）。
6. **防火墙放行**（管理员 PowerShell）：
   ```powershell
   New-NetFirewallRule -DisplayName "ERP-ALL" -Direction Inbound -Protocol TCP -LocalPort 8000,5173 -Action Allow -Profile Private
   ```
7. **装 Git**：https://git-scm.com/download/win （附带 Git Bash，备份脚本要用）。

## 第 2 步：起系统（约 10 分钟）

Git Bash 里执行：
```bash
# 仓库已克隆在 D:\项目文件\ERP-ALL（Git Bash 路径写法 /d/项目文件/ERP-ALL）
cd /d/项目文件/ERP-ALL
cp backend/.env.example backend/.env
# 用记事本打开 backend/.env，把两处 dev-only-change-me 换成强随机串
# （Git Bash 里生成：openssl rand -hex 32，跑两次各取一个）
make up          # 没有 make 就执行: docker compose -f infra/docker-compose.yml up -d --build db redis migrate api
curl http://localhost:8000/healthz   # 期望 {"status":"ok",...}
```
前端（dev profile，团队试用期直接跑 dev server 即可）：
```bash
docker compose -f infra/docker-compose.yml --profile dev up -d frontend
```
团队访问：浏览器打开 `http://<固定内网IP>:5173`（前端随 R1 迭代由部署工作流接管构建）。

### 第 2.5 步：创建初始超管（首次部署必做）

```bash
PW=$(openssl rand -base64 16) && echo "super 初始密码: $PW" >> /d/项目文件/erp-secrets.txt  # 注意：放仓库目录外，防误提交
docker compose -f infra/docker-compose.yml exec -e ERP_BOOTSTRAP_PASSWORD="$PW" api python -m erp.bootstrap admin
```
登录名 `admin`，密码在 `D:\项目文件\erp-secrets.txt`；系统内已有超管时该命令会拒绝（幂等）。

## 第 3 步：每日备份（红线，没配不算部署完成）

1. 试跑：Git Bash 里 `bash infra/local-deploy/backup.sh`，确认 `~/erp-backups` 出现 dump 文件。
2. 挂定时（任务计划程序）：
   - 打开「任务计划程序」→ 创建基本任务 → 每天 02:30
   - 操作=启动程序：程序填 bash.exe 的**实际安装路径**（默认 `"C:\Program Files\Git\bin\bash.exe"`，装在别处按实际改），
     参数填 `-lc "cd /d/项目文件/ERP-ALL && bash infra/local-deploy/backup.sh >> ~/erp-backups/backup.log 2>&1"`
   - 属性里勾选「只在用户登录时运行」+「使用最高权限」。
     （不要选「不管用户是否登录都要运行」：那需要存 Windows 密码，且 SYSTEM 账户访问不到用户态的
     Docker Desktop，备份反而会失败。机器常开常登录即满足。）
3. 异地副本：装 rclone（https://rclone.org/downloads/），`rclone config` 配一个网盘/OSS，
   然后在任务计划的参数前加 `RCLONE_REMOTE=<remote>:erp-backups `（脚本自动上传）。
4. 每月一次恢复演练（README §备份 有命令）。

## 第 4 步（推荐）：自动部署 runner

仓库 GitHub 页面 → Settings → Actions → Runners → New self-hosted runner → Windows，
照页面命令装成服务。装好后告诉远端 agent，deploy workflow 会接管「CI 绿 → 本机自动拉新版重启」。

## 排障记录（部署实战）

- **前端 `pnpm install` 退出码 1（build scripts: esbuild）**：corepack 拉到 pnpm 11，
  它不再读 `package.json` 的 `pnpm.onlyBuiltDependencies`。已在仓库根治
  （`packageManager` 钉死 pnpm 版本 + `frontend/pnpm-workspace.yaml` 批准 esbuild）。
  处置：删掉本机被 pnpm 11 自动生成的 `frontend/pnpm-workspace.yaml`（占位符无效文件），
  `git pull` 后重启 frontend 容器即可。
- **`wsl --shutdown` 后主机连不上 8000**：可能残留孤儿 `wslrelay.exe` 占端口；
  重启 Docker Desktop（或真机重启）即恢复，容器本身是健康的。
- **restart 策略**：compose 已内置 `restart: unless-stopped`（db/redis/api/frontend），
  `git pull` 后无需再手动 `docker update`；migrate 是一次性任务，Exited(0) 属正常。

## 注意

- 这台若同时是你的日常主力机：跑 ERP 与日常使用互不冲突（资源占用已限制在 WSL 20G 内），
  但**重装系统/长时间关机前先说一声**；正式团队全员切换使用时可再评估要不要专机。
- RTX 5080 当前用不上（审核 LLM 走 API）；未来若想本地跑小模型做初筛，这卡是现成的富余。
