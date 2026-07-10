# 001 领域模型与数据字典（EA-001）

- 日期：2026-07-10 · 作者：ar 帽 · 状态：**待 Owner 验收**
- 依据：specs/000-founding/（DECISION-FORM 50 项决策 > PRD-v1 §4 领域模型 > 业务规则总账 187 条 + data-survey/SYNTHESIS）
- 用途：**这是 migration 的唯一蓝本**。EA-002（OpenAPI 契约）与 R1 地基 migration 均从本 spec 派生；实现与本文冲突时，改实现或经 Owner 批准改本文。

## 文件索引

| 文件 | 上下文 | 表数 |
|---|---|---|
| [00-conventions.md](00-conventions.md) | 全局建模约定（命名/公共列/多租户RLS/分区/枚举/金额/FK/扩展） | — |
| [01-identity.md](01-identity.md) | identity：团队/用户/角色/权限/审计日志/共享授权/门户账号 | 9 |
| [02-channel.md](02-channel.md) | channel：渠道/店铺/凭证/代理/配额/店铺事件 | 7 |
| [03-catalog.md](03-catalog.md) | catalog：产品/变体/品牌占用/GTIN池/类目映射/货源/SKU映射 | 8 |
| [04-compliance.md](04-compliance.md) | compliance：黑名单四表/TRO/钓鱼/命中/导入作业 + refdata 大参考数据 | 13 |
| [05-audit.md](05-audit.md) | audit：审核运行/命中/策略/LLM缓存/用量 | 5 |
| [06-listing-pricing.md](06-listing-pricing.md) | listing + pricing：刊登/feed/spec/错误字典/维护任务/定价策略/价格历史 | 9 |
| [07-order-sourcing-aftersale.md](07-order-sourcing-aftersale.md) | sourcing + order + aftersale：采购方/订单/四检/采购执行单/发货/退货/退款 | 8 |
| [08-finance.md](08-finance.md) | finance：结算快照/明细/利润账/汇率 | 4 |
| [09-platform.md](09-platform.md) | scraping + mail + automation + notify + system | 17 |

**合计 80 张表**（app schema 76 + refdata schema 4）。

## 阅读方式

每张表给出：列级定义（列/类型/约束/说明）→ 索引 → 分区与保留 → 作用域（全局/团队/门户可见）→ 决策依据（D-Qxx）。
状态机只在 CHECK 里约束合法值，**迁移合法性在服务层**（一处状态机模块，禁止散落 UPDATE）。

## 与 NFR 的对账（PRD §6）

- 日 20 万上架 → `feed_item` / `audit_run` / `listing_state_history` 月分区（年增 7 千万行量级）
- 在线 200 万 / 库内 500 万 SKU → `listing`、`product` 不分区（B-tree 可承受），预留 R2#3 复评点
- 订单/审计日志永久保留（D-Q16/18）→ 月分区 + 永不 DROP
- 20-30 人多团队 → 全部团队域表 RLS + 服务层强制 scope（双保险），门户第三层独立 DB 角色

## 待 Owner 验收的开放点

1. **feed_item 保留**：默认永久；如接受 24 个月后归档冷存可省一半存储（不影响对账，feed 汇总永留）。
2. **邮件正文保留**：D-Q17 邮件不保留 → 落地为「元数据+分类永留，正文 30 天后清除，封店相关正文转存 store_incident 永留」。
3. **scrape_result 保留 90 天**（转化进 product 后原始 payload 可清；大原文本落盘/OSS 不进 DB）。
4. **上架去重的 DB 级约束边界**：店铺豁免（D-Q31）使「一品一店」无法用纯 DB 唯一约束表达，落地为服务层检查 + advisory lock + 支撑索引；接受此实现即可。

## 验收后动作

Owner 认可 → EA-002 以本 spec 为源生成 OpenAPI 契约草案；R1 首个 migration 按 00-conventions + 01/02 + 09(system) 起步。
