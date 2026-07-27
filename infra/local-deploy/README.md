# 本地部署 Runbook（D-Q52：试点期全本地）

> 目标机器：Owner 一台**常开**的机器（macOS / Windows+WSL2 / Linux 均可，建议 ≥16G 内存、SSD ≥100GB 空闲）。
> 告诉远端 agent 机器的系统与配置后，本文会按实际环境细化。

## 一次性安装（约 30 分钟）

1. 装 Docker：macOS/Windows 装 Docker Desktop；Linux 装 docker-ce + compose 插件。
2. 克隆仓库并配置环境：
   ```bash
   git clone https://github.com/ElijahRRR/ERP-ALL.git && cd ERP-ALL
   cp infra/.env.example infra/.env
   # 编辑 infra/.env：七个变量全部填强随机值（生成：openssl rand -hex 32，一个一串）
   ```
   **容器化部署只看 `infra/.env`**（`backend/.env` 是宿主机直跑后端时才用的，
   compose 不读它）。七个变量一个都不能空：RS-02a 起 compose 用 `${VAR:?}` 取值，
   缺任何一个都直接拒起——**不会**退回到默认口令。
   已有数据的库换口令**不是改这个文件就完事**（`POSTGRES_PASSWORD` 只在空卷首次
   initdb 时生效，凭证密钥换了还要重加密），照
   `.agent/evidence/RS-02a/deploy-rotate-secrets.md` 的顺序走。
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

### ⚠️ 切回 main 前必做：运维资产在位检查（fail-closed）

「切回 main」会把只存在于开发分支上的运维资产**从检出树里抹掉**。USPTO 日更链正是
踩过这个坑的活例：`uspto-daily.bat` 与强制 CRLF 的 `.gitattributes` 一度只在开发分支上，
一旦切回 main，修复版 bat 连同换行声明同时消失，**07-25 那次 LF-only 事故会原样复发**。
把 bat 纳入版控的初衷是「不能只存在于一台机器」，若它只存在于一条随时被 squash 的分支，
风险并没有消除。故切之前先跑这一段，**任一项缺失就不要切**：

```bash
git fetch origin main
git ls-tree -r origin/main --name-only | grep -E '^\.gitattributes$|^infra/local-deploy/automation/'
# 期望三行齐全：.gitattributes / automation/README.md / automation/uspto-daily.bat
# 少任何一行 → main 还没收到这些资产，别切；先让对应 PR 合并，或把当前分支保留常驻。
```

Windows 侧同时确认（切完再核一次，防 CRLF 被规范化掉）：

```bat
git -C <仓库根> check-attr text eol -- infra/local-deploy/automation/uspto-daily.bat
:: 期望 eol: crlf；若为 unset/lf，则 .gitattributes 未生效，bat 会重演 LF-only 事故
```

再确认 `infra/.env` 在位（**RS-02a 起任何 compose 命令都要它**，`make up`、`backup.sh`、
`uspto-daily.bat` 一律受影响；该文件不进版本库，切分支不会动它，故一般只需确认存在）：

```bash
test -f infra/.env && echo "ENV_OK" || echo "缺 infra/.env —— 见 .agent/evidence/RS-02a/deploy-rotate-secrets.md"
```

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

> 定位（D-Q65① 方案 A，2026-07-24 拍板）：抓取/解析/日度增量**整链驻部署机**
> （`ElijahRRR/walmart-trademark-sync` 仓的 `daily_update.py`，原驻 Owner Mac，迁入本机）；
> 新系统只做两件事——**导入**（`bulk_import_trademark`）与**新鲜度守卫**
> （beat `trademark_freshness` 告警）。新系统永不直连 USPTO。

### 一次性迁入（方案 A 初始化，做一遍）

1. **常驻 uspto 库容器**（历史基线已在本机 `D:\erp-staging-backup` 的 dump 里，
   PG17.9 + pgvector 依赖——沿用当年暂存容器同款镜像，这次**常驻**）：
   ```bash
   docker run -d --name uspto-db --restart unless-stopped \
     -e POSTGRES_PASSWORD=<本机自设，存本地文件> -e POSTGRES_DB=uspto \
     -p 127.0.0.1:5433:5432 -v uspto_pgdata:/var/lib/postgresql/data \
     pgvector/pgvector:pg17
   # 选择性还原 7 张关系表（跳过 20G+ 向量 embedding 表；--no-owner -x 挡旧角色错）：
   for t in trademarks trademark_classes trademark_owners trademark_statements \
            trademark_design_codes etl_progress status_code_mapping; do
     pg_restore --no-owner -x -d "postgresql://postgres:<pw>@127.0.0.1:5433/uspto" \
       -t $t D:\\erp-staging-backup\\<dump文件>
   done
   ```
   🔴 **还原后必须逐项核对索引——本步骤实测会丢索引。**

   2026-07-26 实测：四张子表**全部缺失 `serial_number` 索引**（只剩 `id` 主键），
   代价是 ETL 掉到 **5.5 条/秒**、单条 `DELETE ... WHERE serial_number = ANY(...)`
   顺序扫子表耗时 **20~29 秒**，12 个日增量要跑 20 小时以上；补上索引后
   **1,500~2,300 条/秒**（约 300 倍），整链 5.5 分钟跑完。

   **机制未完全定论，不要照抄任何单一解释**——现场事实是：
   `trademarks` 的 5 个二级索引（含 GIN trgm）**全在**、四张子表的外键（`contype='f'`）
   **也全在**，唯独四张子表的二级索引**全丢**。最可信的猜想是大表
   （`trademark_statements` 7.2 GB / `trademark_owners` 3.2 GB）在还原期间建索引失败，
   而**下面这个 `for` 循环从不检查 `pg_restore` 退出码**，错误被静默吞掉。
   故：**循环要检查退出码，且完成后必须显式核验索引**（核验清单见本步末尾）。

   ```bash
   # 循环务必带退出码检查，别让 pg_restore 的失败被吞掉：
   #   pg_restore ... -t $t <dump> || echo "RESTORE FAILED: $t"
   ```

   **必须在还原后手工重建（缺哪个补哪个）**：

   ```sql
   -- P0：ETL 与 delta 导出都靠它，缺了整条链没法用
   CREATE INDEX IF NOT EXISTS idx_tm_classes_serial      ON trademark_classes      (serial_number);
   CREATE INDEX IF NOT EXISTS idx_tm_owners_serial       ON trademark_owners       (serial_number);
   CREATE INDEX IF NOT EXISTS idx_tm_statements_serial   ON trademark_statements   (serial_number);
   CREATE INDEX IF NOT EXISTS idx_tm_design_codes_serial ON trademark_design_codes (serial_number);
   -- P0：delta 导出按 source_file 取当轮增量（原 schema 无此索引，加了省 12 次全表扫）
   CREATE INDEX IF NOT EXISTS idx_trademarks_source_file ON trademarks (source_file);
   -- P1：daily_update 的 validate_data 要跑 mark_identification % 'NIKE'
   CREATE EXTENSION IF NOT EXISTS pg_trgm;
   CREATE INDEX IF NOT EXISTS idx_trademarks_mark_trgm ON trademarks USING gin (mark_identification gin_trgm_ops);
   -- 统计信息：pg_restore 后 reltuples 可能为 0，planner 会误选顺序扫
   ANALYZE trademarks; ANALYZE trademark_classes; ANALYZE trademark_owners;
   ANALYZE trademark_statements; ANALYZE trademark_design_codes;
   ```

   **核验必须查索引，不能只查行数**——行数与 `max(filing_date)` 在无索引时照样通过，
   2026-07-26 就是被这个盲区放过去的：

   ```sql
   SELECT tablename, indexname FROM pg_indexes
    WHERE schemaname='public'
      AND tablename IN ('trademarks','trademark_classes','trademark_owners',
                        'trademark_statements','trademark_design_codes')
    ORDER BY tablename, indexname;
   -- 至少要看到：trademarks_pkey + 上面五条 + 各子表 serial_number 索引
   SELECT count(*), max(filing_date) FROM trademarks;      -- 14.19M 级 / 2026-07 上旬
   SELECT count(*) FROM etl_progress WHERE data_type='trademark';  -- 有历史文件记录
   -- 冒烟：下面这条应是毫秒级；若要 20 秒以上，说明索引仍缺或统计信息未更新
   EXPLAIN (ANALYZE, BUFFERS)
     SELECT count(*) FROM trademark_classes WHERE serial_number = ANY(ARRAY[73000000,73000001]);
   ```

   ⚠ 铁律不变：绝不 pg_restore 进 erp_all；uspto 库只被本链读写。
2. **同步链代码**：`git clone https://github.com/ElijahRRR/walmart-trademark-sync D:\walmart-trademark-sync`
   → `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`。
3. **连接配置**：环境变量 `DB_CONN="dbname=uspto user=postgres password=<pw> host=127.0.0.1 port=5433"`
   （空格分隔 kv 格式**非 URI**，密码只落本机，不入仓不进对话）。
   ⚠ **必须是进程环境变量，且必须由 `.bat` 显式 `set`**——`etl_trademarks.py:24` 是
   `DB_CONFIG = _parse_db_conn(os.environ["DB_CONN"])`，**模块级、无默认值**，而
   `daily_update.py` 顶部就 `import etl_trademarks`，所以缺该变量时**在 import 阶段直接
   KeyError**。仓内**没有任何 dotenv 加载**，放一个 `.env` 在仓根 Python 也不会读
   （`.env.example` 只是键名模板：`DB_CONN` 是本链唯一必需，`LARK_*` 三个仅飞书同步脚本用）。
   本链唯一必需的就是 `DB_CONN`。
4. **挂定时**：Windows 任务计划每日一次（建议 18:00 本地）跑
   `D:\walmart-trademark-sync\.venv\Scripts\python.exe D:\walmart-trademark-sync\daily_update.py`
   ＋随后执行下方「日常链路」第 2-4 步（可合入同一 .bat）。
   - **.bat 已纳入版本管理**：`infra/local-deploy/automation/uspto-daily.bat`。部署机应从仓里
     检出使用，**不要在机器上另存一份**（原先只存在于 `D:\erp-staging-backup\automation\`，
     无备份、无评审）。仓根 `.gitattributes` 声明 `*.bat text eol=crlf` 强制 CRLF 检出。
   - **`Last Result=10` 有两种成因，先排第二种**：
     ①密钥文件（`D:\erp-staging-backup\uspto-db.env`）真的不存在；
     ②**`.bat` 是 LF-only** —— cmd 逐行吞前缀导致 `set "SECRET_FILE=..."` 从未执行，
     `if not exist ""` 恒真，**误报**成 ①。2026-07-25 就是栽在②上、白丢一个验收日。
     一秒自查：`[IO.File]::ReadAllBytes("<bat>")` 数 lone LF，非 0 即中招。
   - **.bat 里不许出现非 ASCII 路径**：本文件存 UTF-8 而 cmd 按 GBK 读，
     `D:\项目文件\...` 会变成 `D:\椤圭洰鏂囦欢\...`。机器相关路径一律走密钥文件的
     `ERP_COMPOSE` 键，**值填 8.3 短路径**（`for %%I in ("<长路径>") do @echo %%~sI`）。
   - 密钥文件是 `KEY=VALUE` 文本（**不是** `set "KEY=VALUE"` 片段），bat 读
     `POSTGRES_PASSWORD` 与 `ERP_COMPOSE` 两键，`DB_CONN` 由 bat 自行拼装。
     ⚠ 连接自检要连**宿主机**映射端口：容器内 PG 监听 5432，5433 是宿主机映射，
     在容器内用 `port=5433` 自检必然 `Connection refused`（自检姿势错，不是配置错）。
   - ⚠ **`Logon Mode` 必须能在无人登录时触发**。`schtasks /query /v` 若显示
     `Interactive only`，则**只有该账号处于登录态才会跑**——这与「无人值守」直接冲突，
     会让某天没人登录的验收日直接作废。两条路：①该账号常驻登录（锁屏即可，须写进运维约束、
     重启后要重新登录）；②`/RU <user> /RP <password>` 存储凭据（Logon Mode 变
     `Interactive/Background`）。**不要改成 `SYSTEM`**——日常链路第 3-4 步要
     `docker compose cp/exec`，Docker Desktop 按用户会话跑，SYSTEM 下通常不可用。
   - ⚠ **这个 `.bat` 是整条链的编排定义，必须纳入版本管理**（见
     `infra/local-deploy/automation/`），否则它只存在于那一台机器上，机器一坏链路定义即失传。
   - 🔒 **已知限制：不配 Windows 自动登录**（2026-07-26 Owner 定案）。自动登录须把口令写进
     注册表 `DefaultPassword` 或凭据管理器，等于给这台存有全部店铺 API 凭证的机器开一道
     明文后门，代价不值。**后果**：锁屏 / RDP 断开 / 长期空闲都照跑，但
     **机器重启到有人手工登录之前，链路完全不跑**（期间每天 18:00 档全部丢失）。
     故运维约束是长期的、不是待办：**部署机重启后须尽快人工登录桌面**。「无人值守」在本
     部署下的准确含义 = **无人干预，但需要有人保持登录态**。
     完整取舍与备选路（containerd 服务化 / WSL2+systemd / TPM 保护的自动登录）见
     `.agent/evidence/R2-12/uspto-3day-verification.md`「已知限制」节。

### 日常链路（部署机每日自动）

1. `daily_update.py`：下载缺失的 USPTO 日增量 zip（`apc{yymmdd}.zip`，404=当日无数据）
   → ETL 进 uspto 库（etl_progress 断点，重跑幂等）→ 完整性校验（孤儿/错误数/pg_trgm）。
2. **delta 导出**（对本轮 etl_progress 新转 completed 的每个 `apcYYMMDD.zip`）：
   ```bash
   psql "$DB_CONN_URI" -c "\copy (SELECT t.serial_number, t.mark_identification, \
     t.status_code, m.live_dead, \
     (SELECT string_agg(DISTINCT c.international_code, ' ') FROM trademark_classes c \
        WHERE c.serial_number = t.serial_number) AS nice_classes, \
     (SELECT o.party_name FROM trademark_owners o WHERE o.serial_number = t.serial_number \
        ORDER BY o.id LIMIT 1) AS owner_name, \
     t.filing_date, t.registration_date \
     FROM trademarks t LEFT JOIN status_code_mapping m ON m.status_code = t.status_code \
     WHERE t.source_file = 'apcYYMMDD.zip' AND t.mark_identification IS NOT NULL) \
     TO 'D:/walmart-trademark-sync/out/delta-YYMMDD.csv' WITH CSV HEADER"
   ```
   列名与导入器别名严格对应（serial_number/mark_identification/live_dead/filing_date…），
   **不筛 live**——DEAD 状态变化也要同步到 erp_all（is_live 翻转），R5 只查 LIVE 不受涨行影响。
3. 拷入 api 容器并导入（幂等，重导安全）：
   ```bash
   docker compose -f infra/docker-compose.yml cp D:/walmart-trademark-sync/out/delta-YYMMDD.csv api:/tmp/
   docker compose -f infra/docker-compose.yml exec api \
     python -m erp.tools.bulk_import_trademark --file /tmp/delta-YYMMDD.csv
   # 中断续跑（同文件同 batch_size）：追加 --resume；错误行看同目录 *.errors.jsonl
   ```
4. 对账口径（每次导入后）：
   - 工具输出 `total/merged/err` ↔ delta csv 行数一致，且 ≈ etl_progress 该文件的
     records_inserted（差值=无 mark 文本行）；err 行逐条看 errors.jsonl；
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

## 黑名单 / TRO bulk 导入（CLI 唯一入口 · #35 合并后的日常运维路径）

> 定位（Owner 2026-07-25 认现方案）：**bulk 导入只走 CLI，不做 HTTP 上传端点**。
> 理由三条：部署机直读本地文件（大文件不经 HTTP）；黑名单/TRO 是全局数据，写入
> 需超管 `system_tx`；合规中心页的职责是**看 / 核对 / 纠错**，不负责灌数据。
> 分工：**单主体**人工录入走页面「登记断言」（block/allow，带追溯）；**bulk** 走本节 CLI。

### 1 灌数据（写，CLI）

文件格式 csv / xlsx / jsonl（按扩展名识别）；列名按域，权威清单见
`backend/src/erp/tools/import_blacklist.py` 模块 docstring：

| `--domain` | 必需列 | 可选列 |
|---|---|---|
| `blacklist_brand` | `brand` | `brand_display`, `reason` |
| `blacklist_seller` | `seller_id` | `seller_name`, `reason` |
| `blacklist_asin` | `asin` | `reason` |
| `blacklist_category` | `category` | `reason` |
| `blacklist_address` | `street` / `address` / `地址` | `reason` |
| `blacklist_zip` | `zip` / `zipcode` / `邮编`（取前 5 位） | `reason` |
| `tro` | `case_no` | `plaintiff`, `court`, `filed_date`, `law_firm`, `brand_terms`, `status`, `raw_ref` |

```bash
# ① 拷进 api 容器：compose 给 api 没挂任何 volume（无 /data），一律 cp 到 /tmp
docker compose -f infra/docker-compose.yml cp D:/path/brands.csv api:/tmp/

# ② 导入（缺省全局 team_id NULL；--team <id> 限定团队）
docker compose -f infra/docker-compose.yml exec api \
  python -m erp.tools.import_blacklist --domain blacklist_brand --file /tmp/brands.csv
```

要点：

- **幂等**：重复行 skip，占位符品牌（unbranded 等）skip——重跑安全。
- **TRO 域**（`--domain tro`）恒为全局，`--team` 对本域无效；`brand_terms` 为 JSON 数组或
  分号分隔串（词内逗号不拆）；`status ∈ active/dismissed/settled`（缺省 active）。
  active 案派生全局 `tro_sync` 品牌断言，dismissed/settled **撤销**该案断言。
- lark 钓鱼地址表表头在第 5 行，导出 csv 前先删表头前的噪声行。
- xlsx 需容器内有 openpyxl，否则改用 csv/jsonl。

### 2 核对（读，走合规中心页——增量5 起不再查库）

1. 合规中心 → **导入作业** Tab：找到刚生成的 job，看 `total / ok / err`（权限 `compliance.import_read`）。
2. `err > 0` → 点「报错报告」→ Drawer 透出**逐块核对**（chunk expected/loaded）+
   **报错样本**（≤50 条，行号 + 原因）。
3. 黑名单账本 Tab 按域查 canonical 生效面；商标库 / TRO 案件各自 Tab 查。

### 3 纠错（灌错了怎么撤，全在 UI 内）

- **误拉黑某主体**：黑名单账本 →「**按主体追溯**」输入归一化主体（不依赖列表命中，
  被压制/已撤销的主体也查得到）→ 抽屉列出各源断言 → 撤销那条 `import` 断言。
  若该主体还有其他源断言（tro_sync / trademark_sync 等），canonical **仍保持拉黑**
  ——多源并存语义，这是对的。
- **要压住所有自动源**：登记 `manual + allow`（人工 allow 压一切自动源，D-Q65 P1 优先级）。
  解白名单 = 按主体追溯 → 撤销该 allow → canonical 恢复拉黑。
- **整批灌错**且逐主体撤销不可接受：提单处理，**不要直改库**——canonical 由断言投影
  维护，直改 `blacklist_*` 表会与账本失同步。

### 4 对账口径

- job 的 `total` = 文件数据行数，`ok + skip + err = total`。
- **canonical 生效面 ≠ job 的 ok 数**：allow 压制、多源合并、占位符跳过都会造成差值。
  查名单条数以黑名单账本 / 按主体追溯为准，不要用 ok 数反推。

## 全店对账与报错回收（R2-12 增量4a，D-Q65②）

- **item_pull**（beat 每日 09:00 UTC）：全店 GET /v3/items 逐态扫描，三类差异**只发现
  不执行**：①后台有本地无→通知+`sync_state.stats` 样例；②状态漂移→listing 转
  degraded（error_code=ITEM_PULL_{类}）+ 维护任务生成；③永久禁售类报错→
  `blacklist_assertion` **pending** 候选（人工确认前不拉黑）。
- 查看差异：通知中心 `item_recon` 条目；明细
  `SELECT stats FROM app.sync_state WHERE scope='item_pull' AND ref_id=<store_id>;`
- 候选断言人工闸（**增量5 起走 UI**）：合规中心 → 黑名单账本 → 切「候选待裁决」→
  逐条「通过 / 驳回」（权限 `compliance.blacklist_write`）。通过=落 active 拉黑，
  驳回=留 revoked 行（不删，可追溯）。
  只读兜底查询（排障用）：
  `SELECT id, domain, subject_norm, reason FROM app.blacklist_assertion WHERE status='pending';`
- **maintenance_run**（beat 每小时）：默认**人工档**（`kinds: []`，任务只积累可见）。
  开半自动档（自动执行下架）需运营显式操作：
  `UPDATE app.schedule SET config = jsonb_set(config, '{kinds}', '["delist"]') WHERE code='maintenance_run';`
  关回人工档把 kinds 改回 `[]`。end_date_renewal 执行通道随增量4b。
- 手动单跑：`docker compose -f infra/docker-compose.yml exec api python -m erp.tools.run_task item_pull`
