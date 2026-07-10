# 01 identity — 团队 / 用户 / 角色 / 权限 / 审计 / 共享授权 / 门户账号

> 决策依据：D-Q16（20-30 人、功能级 RBAC、团队隔离、全量审计）、D-Q30（共享超管独占）、D-Q50（采购双入口+物理隔离）。
> R1 地基第一批表：本文件全部表在首个 migration 落地。

## team 团队

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| name | TEXT | NOT NULL UNIQUE | |
| status | TEXT | NOT NULL DEFAULT 'active' CHECK IN (active, disabled) | |
| settings | JSONB | NOT NULL DEFAULT '{}' | 团队级杂项（展示偏好等，业务开关不放这，放 automation_policy/team_config） |
| created_at / updated_at | | 公共列 | 本表无 team_id/created_by |

作用域：全局（租户根）。

## app_user 用户

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NULL REFERENCES team | **超管为 NULL**，普通成员必属一团队（服务层校验非超管必填） |
| username | TEXT | NOT NULL UNIQUE | 登录名，小写规范化后存储 |
| password_hash | TEXT | NOT NULL | argon2id |
| display_name | TEXT | NOT NULL | |
| is_super | BOOLEAN | NOT NULL DEFAULT false | Owner 唯一超管（约定不设约束，保留未来多超管可能） |
| status | TEXT | NOT NULL DEFAULT 'active' CHECK IN (active, disabled, locked) | locked=连续失败锁定 |
| token_version | INT | NOT NULL DEFAULT 0 | +1 即吊销全部已发 refresh token |
| failed_attempts | SMALLINT | NOT NULL DEFAULT 0 | 登录失败计数，成功清零 |
| last_login_at | timestamptz | NULL | |
| created_at / updated_at / created_by | | 公共列 | |

索引：`uq_app_user_username`；`ix_app_user_team (team_id, status)`。
认证机制（供 R1 实现）：JWT access（15min，audience=erp）+ refresh（7d，校验 token_version）；密码 argon2id；连续 5 次失败锁定 15 分钟；全部登录/登出/失败事件入 audit_log。

## role 角色

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NULL REFERENCES team | NULL=全局模板角色（超管维护，建团队时复制） |
| name | TEXT | NOT NULL | 采集员/审核员/上架员/维护员/订单员/财务/团队管理员…（PRD §3） |
| description | TEXT | NULL | |
| created_at / updated_at / created_by | | 公共列 | |

约束：`uq_role (COALESCE(team_id,0), name)`。

## permission 权限点（种子数据，代码即真源）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| code | TEXT | PK | `模块.动作`，如 `listing.submit`、`order.assign`、**`procurement.execute`**（D-Q50① 内部采购执行入口） |
| module | TEXT | NOT NULL | 15 上下文之一 |
| name | TEXT | NOT NULL | 中文名（前端权限勾选界面用） |
| description | TEXT | NULL | |

作用域：全局。migration 种子 + 代码内 Enum 对照，CI 校验两者一致。
首批权限点清单在 EA-003 的 R1 分解中冻结；命名规则先立：查询 `*.read`、写 `*.write`、敏感动作独立点（`store.credential_view`、`refund.approve`、`listing.live_submit`）。

## role_permission / user_role 关联

| 表 | 列 | 说明 |
|---|---|---|
| role_permission | role_id FK, permission_code FK, PK(role_id, permission_code) | |
| user_role | user_id FK, role_id FK, granted_by BIGINT, granted_at, PK(user_id, role_id) | 服务层校验 role 与 user 同团队（或全局模板不可直挂用户） |

## audit_log 审计日志（不可篡改）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | identity | PK = (id, occurred_at) |
| occurred_at | timestamptz | NOT NULL DEFAULT now() | 分区键 |
| actor_type | TEXT | NOT NULL CHECK IN (user, portal, system) | 三类主体（D-Q50 门户主体独立记账） |
| actor_id | BIGINT | NULL | app_user.id / portal_account.id；system 为 NULL |
| team_id | BIGINT | NULL | 冗余记录，跨团队动作（超管）为 NULL |
| action | TEXT | NOT NULL | `模块.动词`，与 permission.code 同规 |
| object_type | TEXT | NOT NULL | 表名/聚合名 |
| object_id | TEXT | NOT NULL | 统一 TEXT（兼容复合键对象） |
| before / after | JSONB | NULL | 变更快照（写操作必填 after；查看敏感数据只记 action） |
| ip | INET | NULL | |
| user_agent | TEXT | NULL | |
| request_id | TEXT | NULL | 关联结构化日志 |

- append-only：对 erp_app/portal_app REVOKE UPDATE/DELETE；月分区永久保留（D-Q16）。
- 索引：`(team_id, occurred_at)`、`(object_type, object_id, occurred_at)`、`(actor_type, actor_id, occurred_at)`。
- 写入方式：服务层写模型统一出口（写操作装饰器），禁止业务代码绕开。

## shared_resource 跨团队共享授权（超管独占，D-Q30）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| resource_domain | TEXT | NOT NULL CHECK IN (catalog, compliance, gtin) | v1 只开三域 |
| owner_team_id | BIGINT | NOT NULL REFERENCES team | 资源属主 |
| grantee_team_id | BIGINT | NOT NULL REFERENCES team | 受益团队 |
| granted_by | BIGINT | NOT NULL | 必须是超管（服务层校验） |
| granted_at | timestamptz | NOT NULL DEFAULT now() | |
| revoked_at | timestamptz | NULL | 撤销即失效，不删行（留审计） |

约束：`uq_shared_resource (resource_domain, owner_team_id, grantee_team_id) WHERE revoked_at IS NULL`。
RLS policy 通过本表 EXISTS 判定（见 00 §4）；共享是**只读共享**，写回属主资源不开放。

## portal_account 采购方门户账号（物理隔离，D-Q50③）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| purchaser_id | BIGINT | NOT NULL UNIQUE | → purchaser（07 号文档）；1:1 |
| username | TEXT | NOT NULL UNIQUE | 与 app_user.username **不同命名空间**（独立表天然隔离） |
| password_hash | TEXT | NOT NULL | argon2id |
| status | TEXT | NOT NULL DEFAULT 'active' CHECK IN (active, disabled) | |
| token_version | INT | NOT NULL DEFAULT 0 | |
| last_login_at | timestamptz | NULL | |
| created_at / updated_at / created_by | | 公共列（created_by=开户运营） | |

隔离实现（三件套，缺一即违宪）：
1. 独立登录端点 `/portal/auth/login`，签发 JWT `audience=portal`；内部 API 全部拒绝 portal audience。
2. DB 角色 `portal_app` 仅 GRANT：本表（自身行）+ 07 号文档的 `portal_procurement_v` 视图；无任何其他表权限。
3. 前端独立路由树 `/portal/*`，构建时物理排除内部页面代码。

内部采购（D-Q50①②）不用本表：内部成员走 `procurement.execute` 权限点，在内部界面领单；或运营在订单页直接代填（07 号文档 backfill_actor_kind=op_direct）。
