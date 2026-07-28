# 00 全局建模约定

> 所有表定义遵守本文；migration 审查以本文为 checklist。违反即打回（qa 有权 block）。

## 1. 数据库与 schema

- PostgreSQL 16（阿里云 RDS，D-Q46/47：DB 与应用分离部署）。单库 `erp_all`。
- schema 划分：
  - **`app`**（默认）：全部 OLTP 业务表。
  - **`refdata`**：大体量只读参考数据（USPTO 商标 14.2M、封店案例库 75.7k、审核检索嵌入），只由导入器/同步管道写入，业务只读。
- 扩展：`pgcrypto`（凭证加密）、`pg_trgm`（品牌/商标模糊检索）、`vector`（pgvector，L1 检索与封店案例嵌入）、`pg_partman`（分区自动维护；RDS 不可用则由 beat 任务建分区，R1 确认）。

## 2. 命名

- 全部 snake_case；**表名单数**（`store`、`listing`、`channel_order`）。
- 主键一律 `id`；外键 `{目标表}_id`；时间列 `*_at`（timestamptz）/`*_date`（date）；布尔 `is_*` 或形容词；金额 `*_amount` 或语义名 + `currency`。
- PG 保留字回避：用户表命名 `app_user`（`user` 是保留字），订单表 `channel_order`（`order` 是保留字）。
- 索引 `ix_{表}_{列…}`，唯一 `uq_`，部分唯一注明 `WHERE` 条件；CHECK `ck_{表}_{语义}`。

## 3. 主键与公共列

- `id BIGINT GENERATED ALWAYS AS IDENTITY`；**分区表 PK = (id, 分区键)**（PG 要求分区键入 PK）。
- 团队域表标配四列：

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| team_id | BIGINT | NOT NULL REFERENCES team(id) | 租户键，见 §4 |
| created_at | timestamptz | NOT NULL DEFAULT now() | |
| updated_at | timestamptz | NOT NULL DEFAULT now() | 统一触发器维护 |
| created_by | BIGINT | NULL | app_user.id；系统写入为 NULL（actor 详情在 audit_log） |

- 事实/日志表（append-only）省略 updated_at。
- 各表定义中此四列不再重复列出，仅注明「+公共列」或指明缺省差异。

## 4. 多租户与隔离（D-Q30 / D-Q50）

三层防线，缺一不可：

1. **服务层强制 scope**：repository 层不提供无 team 条件的查询入口；跨团队只有超管代码路径。
2. **RLS**：所有团队域表 `ENABLE ROW LEVEL SECURITY`，policy 基于请求级 GUC：
   ```sql
   USING (team_id = current_setting('app.current_team')::bigint
          OR current_setting('app.is_super', true) = 'on')
   ```
   API 中间件在事务开始时 `SET LOCAL app.current_team / app.is_super`。
3. **门户物理隔离（D-Q50③）**：独立 DB 角色 `portal_app`，仅 GRANT 门户白名单视图（见 07 §portal 视图）；独立登录端点 `/portal`、独立 JWT audience、独立账号表 `portal_account`。内部应用角色 `erp_app`；migration 专用 `erp_migrator`。

**资源共享**（超管独占开关）：不放宽 RLS，而是授权表 `shared_resource`（01 号文档）显式插行；共享域 v1 仅 catalog / compliance / gtin 三域，policy 扩展为：
```sql
team_id = current_team OR EXISTS (SELECT 1 FROM shared_resource sr
  WHERE sr.resource_domain = '<域>' AND sr.owner_team_id = 表.team_id
    AND sr.grantee_team_id = current_team AND sr.revoked_at IS NULL)
```

**作用域三类**（每表注明）：
- 全局（无 team_id）：channel、category_map、listing_error_catalog、audit_policy、mail_rule、sys_dict、system_config、refdata.*
- 团队域：其余全部业务表
- 门户可见：portal_account + 07 号文档的门户视图，仅此而已

## 5. 枚举与可配置参数（D-Q11）

- **不用 PG native ENUM**（改值需锁表）。一律 `TEXT + CHECK`。
- 两类取值区别对待：
  - **系统状态机**（listing 状态、feed 状态等，代码分支依赖）→ CHECK 约束 + 服务层状态机模块。
  - **运营可维护取值**（错误处置、风险等级标签、退款原因等）→ 入 `sys_dict`（09 号文档），CHECK 不锁死。
- 业务参数（阈值/频率/开关）一律入 `system_config` / `team_config` / `automation_policy`，**禁止写死在代码**（CLAUDE.md 禁区）。

## 6. 金额、汇率、时间

- 金额 `NUMERIC(12,2)` + `currency CHAR(3)`；LLM 成本 `NUMERIC(12,6)`。
- 汇率 `NUMERIC(12,6)`；采购成本记 **CNY 原币 + 锁定汇率**（D-Q32：采购方汇率即成本），利润核算换算发生在 finance 物化，不回改原始记录。
- 时间全部 `timestamptz`（UTC 存储），前端按 Asia/Shanghai 展示；调度表显式存 timezone。

## 7. 软删除、硬删除与不可篡改

- 不做全局软删。主数据用 `status` 生命周期列；事实表只追加。
- `audit_log` 不可篡改：append-only，对 `erp_app` 角色 REVOKE UPDATE/DELETE，月分区永久保留（D-Q16）。

### 7.1 硬删除三级规则（Owner 2026-07-28 定，R2-14 落地）

> **背景**：此前全仓**零 DELETE 端点**，产品与代理连出口都没有——清理 team 2 的 400 个
> 产品只能由部署 AI 手写 SQL 直连生产库、外加四道闸，**这本身就是缺口的证据**。
> Owner 裁定：**必须能真删**——商品下架后长期占体积（标题/图片/属性/原始 JSON 是大头）、
> 废弃的用户与采购方堆在列表里是真实使用负担。

| 级 | 适用 | 动作 |
|---|---|---|
| **① 无历史 → 直接硬删** | 从未上架的产品、从未操作过的用户、从未接单的采购方/买家账号 | 物理删除，无墓碑（本就没有历史可指向） |
| **② 有历史 → 硬删实体 + 留墓碑** | 上过架的产品、干过活的用户/采购方 | 实体行物理删除；最小信息写入墓碑表 |
| **③ 绝不删** | `audit_log`、`financial_event` / `ledger_entry`（§08 追加式）、订单与售后（D-Q18 永久保留） | 只追加；清理指令须**显式声明这些表一行不删**（§05 同款要求） |

**墓碑表（两张，均极小）**：

- `deleted_product (team_id, asin, reason, deleted_at, deleted_by)`——**几十字节 vs 完整
  商品行**，空间该省的省掉；同时**去重键 `(team_id, asin)` 仍认得该 ASIN，不会下次采集
  又抓回来**（无墓碑硬删 = 垃圾循环回流，这是硬删除唯一的技术陷阱）。
- `deleted_principal (kind, id, label, deleted_at, deleted_by)`——覆盖用户/角色/采购方/
  买家账号。**必要性实测**：`audit_log.actor_id` **无外键、且不冗余用户名**，删了用户后
  审计记录仍在但只剩一串认不出的数字；UI 解析操作人时先查主表、查不到再查墓碑，
  显示「张三（已删除）」——**历史保持可读，实体从列表彻底消失**。

**外键现状（实测，决定实现难度）**：产品被 `variant_member`/`listing`/`listing_spec`
三处 NOT NULL 外键引用（上过架的会被挡，走②级需先处置引用）；`user_role` 对
`app_user` 是 `ON DELETE CASCADE`（角色绑定跟着删，正确）；采购方被采购执行单可空引用。

**配套要求**：删除是权限点门控的动作 + 二次确认；**删除动作本身必须写 `audit_log`**
（谁在何时删了什么，before 快照留在日志里）；列表默认隐藏已停用/已归档项并提供
「显示已停用」开关——**折叠比删除更快见效，二者都要**。

## 8. 分区与保留策略（NFR 对账表）

| 表 | 分区键 | 粒度 | 保留 |
|---|---|---|---|
| audit_log | occurred_at | 月 | 永久（D-Q16） |
| audit_run / audit_hit | created_at | 月 | 永久 |
| llm_usage_log | occurred_at | 月 | 永久（成本追溯） |
| feed_item | created_at | 月 | 永久（开放点：24 个月归档） |
| listing_state_history | occurred_at | 月 | 永久 |
| price_history | occurred_at | 月 | 永久 |
| channel_order / order_line / order_check | order_date | 月 | 永久（D-Q18） |
| scrape_task | created_at | 月 | 12 个月 |
| scrape_result | created_at | 月 | 90 天（原始 payload 落 OSS/盘） |
| task_run | started_at | 月 | 12 个月 |
| notification | created_at | 月 | 12 个月 |

- 不分区大表：`listing`（500 万级）、`product`（500 万级）、`gtin_pool`（百万级）——B-tree + 覆盖索引可承受；R2#3 迭代设复评点，指标：单表 > 2 千万行或 p99 查询 > 100ms。
- 分区命名 `{表}_pYYYYMM`；提前 3 个月预建；建分区/清理由 beat 任务或 pg_partman 负责并有告警。

## 9. 外键纪律

- 主数据之间 FK 全开（team/store/product/listing 等）。
- **指向分区表不建 FK**（PG 限制 + 写放大），列名保持 `*_id` 语义 + 服务层校验 + 每日对账任务（automation 域）扫悬挂引用。
- 大事实表指向主数据的 FK：`feed_item.listing_id`、`order_line.listing_id` 等**不建 FK**（写入热路径），同样靠对账。

## 10. 加密与敏感数据

- 凭证列一律 `*_encrypted BYTEA`，pgcrypto 对称加密；密钥来自应用环境（KMS/挂载文件），**不入库不入 git**。
- 涉及：store_credential.client_secret、proxy.password、mailbox.password。
- 客户 PII 最小化：channel_order.customer/ship_to 只存履约必需字段；门户视图不暴露客户联系方式以外的 PII。
- 读取凭证的路径只有渠道网关/邮件轮询服务；前台展示一律打码，查看/修改动作记 audit_log（D-Q20：一切前台可操作）。

## 11. ID 与序列

- `master_sku`：独立 `SEQUENCE master_sku_seq START 1`，格式 `'M' || lpad(nextval::text, 7, '0')`，终身不变、不回收（D1）。7 位容量 999 万 > 库内 500 万 NFR。
- 对外 API 直接暴露 bigint id（内部系统，无混淆需求）；门户侧只暴露采购执行单号。

## 12. Redis 边界（哪些状态不进 PG）

- 渠道 token 缓存（900s 复用）、GCRA 限流桶、采集派发队列、上架任务队列 → Redis。
- **PG 是唯一事实源**：Redis 全丢可从 PG 重建；任何业务事实不允许只存 Redis。
