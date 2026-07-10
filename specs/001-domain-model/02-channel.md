# 02 channel — 渠道 / 店铺 / 凭证 / 代理 / 配额 / 店铺事件

> 决策依据：D-Q4（多渠道路线，首发 walmart_us）、D-Q10（三向配额）、D-Q20（凭证加密+前台可维护）、D-Q30（店铺团队独占）、D-Q33（封店工作流+店铺档案）、D-Q34（代理 IP 由 ERP 管理）、D-Q40（收款仅 PingPong 标签）。
> 铁律映射：每店绑定固定出口 IP（防关联）→ 本域用 DB 约束兜底。

## channel 渠道（全局种子）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| code | TEXT | NOT NULL UNIQUE | `walmart_us`（首发）；未来 `walmart_ca`/`amazon_us`… |
| name | TEXT | NOT NULL | |
| adapter | TEXT | NOT NULL | 渠道网关 adapter 注册名（多渠道扩展点，PRD §2） |
| status | TEXT | NOT NULL DEFAULT 'active' CHECK IN (active, disabled) | |
| created_at / updated_at | | | |

## store 店铺（团队独占）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | 独占，无共享（D-Q30） |
| channel_id | BIGINT | NOT NULL REFERENCES channel | |
| code | TEXT | NOT NULL | 店铺编号（A152…），渠道内唯一 |
| name | TEXT | NOT NULL | 店铺名（如 A085朱丽霖） |
| status | TEXT | NOT NULL DEFAULT 'active' CHECK IN (active, paused, suspended, closed) | paused=人工暂停投放；suspended 由 store_incident 联动 |
| operating_mode | TEXT | NOT NULL DEFAULT 'build' CHECK IN (build, match, mixed) | 运营模式（调研：飞书店铺表已有此列） |
| dedup_exempt | BOOLEAN | NOT NULL DEFAULT false | 上架去重豁免（D-Q31） |
| is_test | BOOLEAN | NOT NULL DEFAULT false | A152=true；渠道写路径灰度只允许 is_test 店（验证纪律） |
| proxy_id | BIGINT | NULL REFERENCES proxy | 当前绑定出口 |
| legal_entity | TEXT | NULL | 注册主体 |
| payment_label | TEXT | NULL | 收款标签，只显示 `PingPong`（D-Q40，不存卡号细节） |
| opened_at | DATE | NULL | |
| suspended_at / closed_at | timestamptz | NULL | 与 store_incident 联动回写 |
| profile | JSONB | NOT NULL DEFAULT '{}' | 档案扩展字段（D-Q33 字段清单中的低频项） |
| notes | TEXT | NULL | |
| +公共列 | | | |

约束与索引：
- `uq_store (channel_id, code)`；`ix_store (team_id, status)`。
- **代理独占**：`uq_store_proxy UNIQUE (proxy_id) WHERE proxy_id IS NOT NULL AND status <> 'closed'` —— 两店共用出口 IP 直接被 DB 拒绝（防关联铁律）。

## store_credential 店铺渠道凭证（加密）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| store_id | BIGINT | NOT NULL UNIQUE REFERENCES store | 1:1 |
| client_id | TEXT | NOT NULL | 明文（本身非秘密） |
| client_secret_encrypted | BYTEA | NOT NULL | pgcrypto，见 00 §10 |
| updated_by / updated_at / created_at | | | 前台可维护（D-Q20），每次查看/修改记 audit_log |

- 读取路径仅渠道网关服务；API 层永不回显 secret（只回显「已配置/更新时间」）。
- team_id 经 store 继承，不冗余；RLS 用 store JOIN policy。

## proxy 代理资产（ERP 管理，D-Q34）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | |
| kind | TEXT | NOT NULL CHECK IN (socks5, http) | |
| host | TEXT | NOT NULL | IP 或域名 |
| port | INT | NOT NULL CHECK (port BETWEEN 1 AND 65535) | |
| username | TEXT | NULL | |
| password_encrypted | BYTEA | NULL | |
| vendor | TEXT | NULL | 供应商（现况：易路，调研 Q4 无台账 → 字段从零建） |
| status | TEXT | NOT NULL DEFAULT 'active' CHECK IN (active, expired, disabled) | |
| expires_at | DATE | NULL | 到期提醒由 automation 域 schedule 扫描 |
| purchased_at | DATE | NULL | |
| monthly_cost | NUMERIC(12,2) | NULL | + `cost_currency CHAR(3)` |
| note | TEXT | NULL | |
| +公共列 | | | |

约束：`uq_proxy (host, port, COALESCE(username,''))`；索引 `(team_id, status)`、`(expires_at) WHERE status='active'`。
换绑流程：store.proxy_id 更新 = 服务层动作（记 audit_log + 通知），老 proxy 不自动 disabled。

## quota_config 配额配置（三向，D-Q10）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| store_id | BIGINT | NOT NULL REFERENCES store | |
| quota_kind | TEXT | NOT NULL CHECK IN (listing_create, listing_delete, maintenance) | 上架/下架/维护 |
| daily_limit | INT | NOT NULL CHECK (daily_limit >= 0) | 0=停用该方向 |
| enabled | BOOLEAN | NOT NULL DEFAULT true | |
| updated_by / updated_at / created_at | | | 运营可维护（D-Q11） |

约束：`uq_quota_config (store_id, quota_kind)`。
注意与渠道限流的关系：quota 是**业务额度**（店铺运营节奏），GCRA（Redis）是**渠道 API 限流**（`docs/walmart_rate_limits.tsv`），两层独立、都必须过。

## quota_usage 配额消耗计数

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| store_id | BIGINT | NOT NULL | |
| quota_kind | TEXT | NOT NULL | 同上 CHECK |
| window_date | DATE | NOT NULL | 业务日（Asia/Shanghai 口径，服务层换算） |
| used | INT | NOT NULL DEFAULT 0 | |
| updated_at | timestamptz | NOT NULL DEFAULT now() | |

约束：`uq_quota_usage (store_id, quota_kind, window_date)`。
消耗协议：任务**下发点**原子 `INSERT … ON CONFLICT DO UPDATE SET used = used + 1 WHERE used < limit`（拿不到额度即不下发）——杜绝草稿系统里「配额闸空转」的旧病（WIRING-AUDIT 教训）。失败任务是否返还额度：create/delete 返还、maintenance 不返还（服务层规则，进总账）。

## store_incident 店铺事件（封店工作流，D-Q33）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | |
| store_id | BIGINT | NOT NULL REFERENCES store | |
| incident_kind | TEXT | NOT NULL CHECK IN (suspension, warning, listing_block, other) | |
| source | TEXT | NOT NULL CHECK IN (mail, manual) | 邮件识别 or 人工标记 |
| mail_message_id | BIGINT | NULL | → mail_message（09），邮件识别时回链 |
| occurred_at | timestamptz | NOT NULL | 封店时间 |
| reason | TEXT | NULL | 封店原因（邮件抽取或人工填） |
| mail_body_snapshot | TEXT | NULL | 封店邮件正文转存（正文本体 30 天清，此处永留） |
| status | TEXT | NOT NULL DEFAULT 'open' CHECK IN (open, observing, appealing, resolved, closed) | observing=观察放款 |
| sku_released_at | timestamptz | NULL | 该店 SKU 全量释放完成时间（catalog 域动作回填） |
| brand_released_at | timestamptz | NULL | 品牌占用释放完成时间 |
| appeal_notes | TEXT | NULL | 申诉记录（refdata.suspension_case 75k 案例库辅助写信） |
| closed_at | timestamptz | NULL | |
| +公共列 | | | |

索引：`(team_id, status)`、`(store_id, occurred_at DESC)`。
工作流联动（服务层编排，R2#7 落地）：
1. 创建 suspension 事件 → store.status=suspended + suspended_at 回写；
2. 触发释放作业：brand_assignment 释放 + listing 停止维护 + gtin 保持 used（不回收，防重用关联）；
3. automation 域 schedule 定时提醒（观察放款/写申诉信）→ notification；
4. resolved → store.status 恢复由人工确认，不自动。

## 渠道网关运行时（不建表，声明边界）

- token 缓存（900s 复用、跨接口共用）与 GCRA 限流桶 → **Redis**；响应头 `x-current-token-count` / `X-Next-Replenishment-Time` 驱动自适应退避（移植 erpAPI walmart_client + GCRA，PRD 移植白名单）。
- 渠道请求明细不落 DB（量级不允许），走结构化日志；业务结果落各域事实表（feed/order/…）。
