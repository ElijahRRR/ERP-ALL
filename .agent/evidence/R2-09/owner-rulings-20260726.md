# R2-09 开工前 Owner 裁定记录 + 批注回传（2026-07-26）

**性质**：本文件是**批注回传**，不是 specs 正文。按工单约束「specs 正文只由云端 AI 落笔
（007/图纸归审计侧，批注回传）」，开发侧不直接改 `specs/007-mvp-completion-plan/README.md`
与 `specs/001-domain-model/09-*.md`。下面每条都给出**逐字可套用的改动请求**，交审计侧落笔。

**来源**：`.agent/evidence/R2-09/archaeology.md`（1512 行，六路并行 + 对抗性交叉核对）
§2「必须 Owner 拍板（开工前）」的前 4 条。Owner 2026-07-26 全部按开发侧建议裁定。

**效力**：这 4 条裁定后 R2-09 具备开工条件。§2 余下 6 条（[5]~[10]：auto 档准入门槛、
guardrail 键集合与默认值、半自动停驻 SLA、refund 端点、权限点命名、面板归属）**未裁，
不阻塞开工**——按拆单建议它们分别落在增量 2/3/4/5，届时逐个提请。

---

## 裁定 1 · flow 清单 v2 一次性冻结（对应考古 §2[1]）

**Owner 裁定：五条子项全部按开发侧建议。**

**为什么必须一次拍完**：`specs/001-domain-model/09-*.md:156` 明写「代码 Enum 对照 + CI 校验」。
清单不冻结，`AutomationFlow` 枚举就写不出来，CI 一致性校验第一天就红。这是整单唯一硬阻塞。

### 给审计侧的改动请求（001§09 flow 清单表）

| # | 改动 | 裁定 | 依据 |
|---|---|---|---|
| ① | **删除** `gtin_alert` 行 | 删 | 阈值已落 `team_config` 的 `gtin.warn_pct` / `gtin.critical_pct`。保留=同一参数两个落点，运营改 A 处不生效 |
| ① | **删除** `suspension_reminder` 行 | 删 | 节奏已落 schedule 种子 `remind_days=7`。同上双落点问题 |
| ② | `listing_pricing` **归一为** `pricing_watch` | 改名 | 代码侧真名是 `pricing_watch`；清单用了另一个名字，Enum 对照必然对不上 |
| ③ | **新登记** `scrape_to_audit`（采集→审核） | 登记 | 图纸零出处但 007 验收判据要求「采集环节可停」。与裁定 2 联动 |
| ④ | **新登记** maintenance runner 的人工/半自动档为独立 flow | 登记 | `DECISION-FORM.md:275`（D-Q65②，**宪法级**）明确要求该 runner 有人工/半自动档。不登记 = 静默偏离宪法 |
| ⑤ | `03-catalog.md:32`「match 模式跳过 sourcing 由 automation_policy 决定」**归属到** `audit_to_listing` | 归属 | 该规则的生效点在 allocate→submit 链上，属 `audit_to_listing` 的作用域 |

**冻结后的 flow 全集**（供 Enum 落码，审计侧确认后即为 v2 基线）：
`order_block` · `compliance_block` · `refund` · `cancel` · `audit_to_listing` ·
`pricing_watch` · `scrape_to_audit` · `listing_dispatch`（见裁定 2）· `maintenance_run`

> 删掉的两行**不是能力被砍**——告警照旧工作，只是它们的档位不再由 automation_policy 管，
> 而由各自已有的配置落点管。这一点要在图纸里写明，否则将来有人会以为功能丢了。

---

## 裁定 2 · 验收判据「四环」与图纸供给「两环」的矛盾（对应考古 §2[2]）

**Owner 裁定：走「甲」——补登记两个 flow 把四环凑齐，不下调判据。**

**矛盾原文**：`specs/007-mvp-completion-plan/README.md` 的 R2-09 验收判据要求
「**采集→审核→上架→定价，四环各自可停**」，而 001§09 只供给了其中两环的 flow。
不裁定则该验收**天然不可达**——不是做不好，是判据要求的能力图纸没给。

### 给审计侧的改动请求

- **001§09**：随裁定 1③ 新登记 `scrape_to_audit`（采集→审核），**并另登记 `listing_dispatch`**
  （审核→上架的派发环节），四环补齐为：
  `scrape_to_audit`（采集）→ `audit_to_listing`（审核）→ `listing_dispatch`（上架）→ `pricing_watch`（定价）
- **007 正文**：验收判据措辞**不改**（四环保留）。仅在判据后加一句注明四环对应的 flow 码，
  避免将来再次出现「判据说四环、图纸给两环」的错位。

**裁定理由（Owner 采纳）**：判据本身合理——四个环节都该能独立停；是图纸漏登记，
不该反过来降标准迁就图纸。

---

## 裁定 3 · order_block / compliance_block 的 semi 档（对应考古 §2[3]）

**Owner 裁定：认二元——不补 semi 独立语义。**

**现状**：这两条 flow 的 `semi` 档目前等价于 `auto`（图纸自注 `semi ≡ auto`，前端面板
隐藏 semi 选项）。

### 给审计侧的改动请求

- **001§09**：把「`semi ≡ auto`」从脚注提升为**显式声明**——`order_block` /
  `compliance_block` 两条 flow 的合法档位集合是 `{manual, auto}`，不含 `semi`。
  面板对这两条只渲染两个选项（不是隐藏第三个，是本来就只有两个）。
- 同时声明：**其余 flow 的档位集合是三档全集**，避免读者以为全局都是二元。

**裁定理由（Owner 采纳）**：`order_block` 是**唯一已上线在跑**的消费点。给它加 semi 语义
等于改已上线的订单冻结行为——收益是概念整齐，代价是可能弄坏正在工作的拦截逻辑，风险不对称。

---

## 裁定 4 · 「档位变更即时生效（吃 R2-04 Redis pubsub）」口径修正（对应考古 §2[4]）

**Owner 裁定：按开发侧建议改措辞为「档位每次决策直读数据库、不进缓存」。**

**`specs/007-mvp-completion-plan/README.md:79` 原文**大意：档位变更即时生效，吃 R2-04 的
Redis pubsub 配置广播。**实测三条与之不符**：

1. **那套配置缓存生产环境零读者**——`get_config_service` 全仓仅 3 处引用，且**没有任何业务
   代码真正调用 `ConfigService.get()` 取值**；
2. 所以 Redis 广播实际在做的事是「失效一份没人读的缓存」，即空转；
3. **方向是反的**：config bus 是 **fail-open**（异常时放行），而档位开关必须 **fail-closed**
   （异常时拦住）。用 fail-open 的载体承载 fail-closed 的语义，是照着矛盾写代码。

补充实测：档位两处读点均为每请求直连 SQL，延迟 ≈ 0——**直读比走缓存更准且不更慢**。

### 给审计侧的改动请求

- **007:79**：删除「吃 R2-04 Redis pubsub 配置广播」的实现指定，改为
  「**档位在每次决策时直读 `automation_policy`，不进缓存**」。
- 保留「即时生效」这个**目标**（直读天然满足），只删掉那个不成立的**实现手段**。

---

## 与裁定联动、必须写进 R2-09 工单纪律的两条（考古盲区 §3[2]）

这两条不是 Owner 待裁项，是裁定 4 的直接后果，落工单时不能漏：

1. **档位读必须与被闸住的写同事务，且每条决策读一次。** `procurement.py` 因档位读与业务写
   共用同一 session/事务快照，天然安全；但 auto 档 beat 推进器是「读档 → 长事务推进整批」，
   切档落在中间就会以旧档跑完整批。beat 任务级硬超时默认 900s（`beat.py:36-37`），
   任务级读一次的最坏陈旧是 900s+30s——**直接击穿原「60s 生效」的承诺**。
2. **逐 flow 声明「实时求值 vs 创建快照」并做成表落进图纸。** 现状已分化：`refund` 是创建时
   快照 `mode_applied`（切档不影响在途，正确）；`order_block` 是实时求值（立即改变）；
   `audit_to_listing` / `pricing_watch` **未定**。且「切档 60s 生效」在快照型 flow 上根本无法
   定义（在途单不变才是正确行为）。不逐条声明，验收时会撞上「auto→manual 时已 allocate
   未 submit 的 draft listing 归谁」这类无答案的问题。

---

## 待办与状态

- [x] Owner 裁定（2026-07-26）
- [ ] **审计侧按上述请求落笔 001§09 与 007 正文**（本文件即改动请求）
- [ ] 落笔后 R2-09 正式立项（注册进 review_list + task.md，按考古 §4 的 7 增量拆分）
- [ ] 考古 §2 余下 6 条（[5]~[10]）随对应增量逐个提请 Owner

**顺带说明**：考古时实锤的 `listing/maintenance.py:29` fail-open（kinds 默认值）已于
2026-07-26 随 PR #36/#37 修复，不再遗留给 R2-09。
