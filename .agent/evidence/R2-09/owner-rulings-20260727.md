# R2-09 立项三问：Owner 裁定回执（2026-07-27）

> 对应提问件：`.agent/evidence/R2-09/owner-questions-20260727.md`（云端 AI → Owner）。
> 本文是**裁定回执 + 落地方案 + 给审计侧的正文改动请求**。
>
> 按分工，云端 AI **不写** `specs/007-*` 与 `specs/001-domain-model/` 图纸正文。
> 下列标【审计侧落笔】的条目即改动请求，云端侧只在 `.agent/` 台账与代码侧落地。

| 问 | Owner 裁定（2026-07-27 原话） | 影响 | 落地位置 |
|---|---|---|---|
| Q1 | 「接受建议 (a)」【阻塞增量6】 | 验收判据改写 | 台账已改；007 正文待审计侧 |
| Q2 | 「要一份『平台默认档模板』让新团队继承运营调好的档位」【影响增量2 面板】 | 新增机制 | 增量2；§09 图纸待审计侧登记 |
| Q3 | 「接受建议」 | 面板三态 + warn + 闸类二次确认 | 增量1 已落 warn；其余入增量2 |

---

## Q1 —— 裁定 (a)：三件同族商品各跑一档

### 裁定内容

原判据「同一商品在三档下各跑一遍」改为**「同一 SKU 家族取 A/B/C 三件，分别在
manual / semi / auto 下各跑一档全链」**。

**明确不做 (b)**：不为验收往仓里引入商品状态回退能力，包括 `is_test` 店专用的状态重置
脚本。理由（提问件原文）：那是一条**能把商品状态往回拨、且仅靠一个 `is_test` 标志把关**
的通道，会长期存在而只用一次——收益一次性、风险长期。

### 为什么原判据跑不通（复核仍成立）

商品状态单向前进 `ingested → audit_passed → listing draft → live`，跑完 auto 档就回不到
manual 档。全仓无状态回退工具——`backend/src/erp/tools/audit_replay.py:166` 是离线重放，
不改 `product.status`。**这不是实现难度问题，是判据本身在当前状态机下无法执行**，会在验收
当天卡住。判据的真实目的是「三档在同一条流水线上都走得通」，三件等价输入同样证明这件事。

### 已落地（云端侧）

- `.agent/review_list.json` R2-09 的 `acceptance` 与 `note` 已按裁定改写，并在正文内写明
  改判据的**理由**与「明确不做状态回退」，避免后人只看到结论、不知道为什么放宽了字面。

### 【审计侧落笔】改动请求

`specs/007-mvp-completion-plan/README.md:88` 现文：

> **验收**：同一商品在三档下各跑一遍采集→审核→上架→定价全链：全自动零人工介入、
> 半自动在设定环节停、人工档每环节停。

请改为「三件同族商品各跑一档」并注明理由（单向状态机 + 不引入回退通道）。四环 flow 映射
与切档生效口径两段**不动**。

### 顺带核过、确认不受影响的一处

`review_list.json` 的 **R2-13** `acceptance` 里也有「②三档各跑一遍」字样，但它说的是采购
执行、且**未写「同一订单」**——三档本来就要三张不同的执行单，不存在同一实体状态回拨的问题。
**未改动**，特此说明，免得下次有人以为漏了。

---

## Q3 —— 裁定：接受建议（三条全收）

### 裁定内容

1. 面板**显性区分三态**——「未配置 / 已停用 / manual」，不要都渲染成「人工」；
2. 读点在命中「有行但 `enabled=false`」时记 warn 日志（区别于「本来就没配」）；
3. `order_block` / `compliance_block` 这两条**闸类** flow 停用时，面板给**显式二次确认**。

### 落地状态

| 条 | 状态 | 位置 |
|---|---|---|
| 2（warn 日志） | ✅ **增量1 已落** | `backend/src/erp/core/automation.py` 的 `automation.policy_disabled_treated_as_manual`，并与「本来就没配」（无行，静默回 MANUAL）区分开 |
| 1（面板三态） | 入**增量2** 范围 | 需要 API 把「无行 / 有行但停用 / 有行且 manual」三态如实传给前端——**不能在 API 层就把三态压成一个 `mode` 字段**，那样前端再想区分也区分不出来 |
| 3（闸类二次确认） | 入**增量2** 范围 | 面板对 `order_block` / `compliance_block` 的停用操作弹显式确认，文案须点明「停用后本 flow 退回 manual，而 manual 对本 flow 是**只软标记不冻结**」 |

### 语义仍未变（重要）

本轮只做「可见 + 留痕 + 拦一道手」，**没有改 `enabled=false` 的降级方向**——它仍然退回
manual。要不要改成「闸类停用需退回 auto」或「禁止停用闸类 flow」属语义变更，不在本次裁定
范围内，如需变更另行提请。

### 为什么这条值得单独较真

「看起来配着、实际没生效」是最难发现的一类故障。本会话已在别处栽过同形的坑：
compose 从未注入 `ERP_ENV`，导致一层保护从未生效，而**所有判据全绿**（RS-02a 审查 S1）。
把「未配置」和「已停用」渲染成同一个字，就是在制造同一类盲区。

---

## Q2 —— 裁定：要平台默认档模板

### 裁定内容

Owner 原话：**「要一份『平台默认档模板』让新团队继承运营调好的档位」**。

### ⛔ 但它现在开不了工——两道前置

裁定解决了「要不要做」，没有解决「能不能落码」。两道前置都不在云端侧手里：

1. **图纸里没有「模板」这个概念，且改图纸不归云端侧。**
   `09-platform.md` / `007 README.md` / `DECISION-FORM.md` 三份正文对「默认模板 / 平台默认 /
   继承」检索**零命中**（DECISION-FORM 有 4 处「模板/继承」字样，但分别属定价区间、审核 L4
   开关、变体主图，与档位无关）。「Owner 裁定要模板」**不等于**「图纸已有模板」——
   若直接落码，迁移头注写「Owner 2026-07-27 裁定」会读起来像图纸已认账，
   **那正是当初设立独立审查闸的那一类正文失实**。详见 §Q2-审计侧前置。
2. **两条业务口径未定**（模板写权归谁、非闸类 flow 能否在模板里设 auto），
   见 §Q2-仍需 Owner 定。两条都在 CLAUDE.md 升级清单内，云端侧不自行拍板。

因此 **增量2 拆成 2a / 2b**：2a（策略读写 API + 权限点 + 面板，含 Q3 三条）**无前置，可即刻开工**；
2b（模板）待上述两道前置解除。见 `.agent/task.md`。

### 方案：独立模板表 `app.automation_policy_template`

经三方案（表内哨兵行 / 独立模板表 / 塞进 `system_config`）× 三镜头对抗性审查
（fail-open 方向 / 运营现实 / 与既有约束一致性）后择定。**以下是设计提案，尚未落码、
尚未跑过测试**——判据与坏法都是纸面推演，落码时以实测为准。

**底座选独立表**，理由是另两条的代价不可嫁接：

- **表内哨兵行会打红三处正在跑的 upsert**。要把唯一索引换成 `(COALESCE(team_id,0), flow_code)`，
  而 PostgreSQL 的 `ON CONFLICT` 推断规范匹配不到它 → 42P10。三处活体已亲自 grep 确认：
  `tests/db/test_automation_resolve.py:35`（增量1 自己的判据）、`tests/db/test_procurement.py:271`、
  `tests/db/test_refund_request.py:128`。它还要改 §09 **已冻结**的两处正文
  （`:148` team_id NOT NULL、`:155` 唯一键）——那是设计变更不是附注。
- **塞进 `system_config`** 是全项目平台级配置里**唯一没开 RLS** 的表，DB 层对写入零抵抗。

**但两条落败方案的好点子全部嫁接过来**（它们与「表放哪」正交）：

| 来源 | 嫁接的点 |
|---|---|
| 哨兵行方案 | 把方向**焊进 DDL CHECK**——`CHECK` 绑表 owner，部署机手工 psql 也绕不过（而 owner 天然绕过 RLS）。这是本轮最锋利的一条论据。 |
| 哨兵行方案 | `automation_policy.origin` 列（`manual` / `template`）——显式区分「继承来的」与「自己配的」，不靠值 diff 推断（改一次模板会让全体团队集体误报「已偏离」）。 |
| system_config 方案 | apply 用 `ON CONFLICT DO NOTHING`——**单调性写在 SQL 层而不是纪律层**，模板通路在数学上无法下调任何已配置档位。 |
| system_config 方案 | 响应必须同时回显 `applied` 与 `skipped`（只显 applied 会让运营以为整团队已对齐）。 |

**继承语义 = 建团队时快照复制，不是运行时回退。** `resolve_mode` **一个字不改**，
模板行永不出现在决策路径上——增量1 的 `test_no_row_is_manual` 继续成立且继续描述现实。

### 三处刀口比原方案更狠，逐条说明理由

**① 模板表不设 `config` 列。**
已复核：`core/automation.py:114` 的 SQL 是 `SELECT mode, enabled`，连 `config` 都不取；
全仓对 `automation_policy.config` 的引用**只有注释与 docstring，零处真读**，四个护栏键
（`amount_ceiling` / `daily_cap` / `price_delta_pct` / `check_kinds`）在 `backend/src` **零命中**。
所以今天往模板里写护栏 = 写进 `/dev/null`，**制造的是「护栏已配」的错觉而不是护栏**。
等 R2-13 13c 真把护栏消费点接上，再单独立单给模板加 config。

**② 模板表不设 `enabled` 列，下线 = 删行。**
一个标着「参与新团队继承」的无约束布尔，可以静默停掉闸类下发——与 Q3 里
「`enabled=false` 是静默安全退化」同形。「模板项 `enabled=false`」严格劣于「没有这一项」。

**③ 迁移不种任何模板行、不回填任何既有团队。**
候选方案里有一版要种 10 行（其中 8 行是 manual）。净收益严格为零
（`resolve_mode` 对 manual 行与无行都回 MANUAL），代价却是三条实的：考古结论
「零策略行是常态路径」当天失效；`test_no_row_is_manual` 从此测一个生产上不再发生的场景
（判据与现实脱钩，绿灯不再证明新团队 fail-closed）；这些行**永远删不掉**
（`automation_policy` 无 DELETE 授权）且无来源可查。

### 两条焊进 DDL 的方向约束

```sql
-- 闸类：auto 才是「拦截」，manual 是「只软标记不冻结」。模板写 manual 与「没有模板」
-- 运行时完全等价，却把「无人做过决策」洗成「有人决定不拦截」——面板会显示
-- 「已配置：人工」而拦截是关的，比今天的「未配置」更坏。
CONSTRAINT ck_apt_gate_tighten_only CHECK (
  flow_code NOT IN ('order_block','compliance_block') OR mode = 'auto')

-- purchase_execute 花真金白银（§09 v2.1「护栏缺失即禁止开 auto」）、
-- maintenance_run 的 DELETE/republish 是 D-Q65② 宪法级人工闸。两条今天都没有消费点
-- → 模板里写 auto 完全无声，R2-13 接线那天所有继承过它的团队同时开始自动执行。
CONSTRAINT ck_apt_no_inherited_auto CHECK (
  flow_code NOT IN ('purchase_execute','maintenance_run') OR mode <> 'auto')
```

判据须**按名断言约束存在**（不存在即红，不许 skip/短路），并把 DDL 里的 flow 名单与
`FLOWS[f].legal_modes == _TWO` 派生集比对——DDL 是 flow 注册表的**第三份副本**，
flow 改名后 `NOT IN` 恒真、约束静默失效而看不出来。

### 三条最可能的坏法与防线（摘要）

1. **模板配了却没继承，而判据全绿**（与 ERP_ENV 同构）：`INSERT...SELECT` 复制 0 行不报错；
   且 `tools/audit_replay.py` / `tests/db/conftest.py` / `frontend/e2e/seed-full-chain.sql`
   等**非 HTTP 建团队路径一律不继承**——本地与生产默认姿态不一致，「本地复现不了」从这长出来。
   防线：applied/skipped 回显 + 模板非空而 applied=0 记 warn + 面板顶部「平台模板未配置」banner
   （不能让它长得像「模板全 manual」）+ runbook 强制用抛弃型团队取证一次。
2. **有人把 `DO NOTHING` 改成 `DO UPDATE`**——那是本方案里**唯一**能把档位推给存量团队的路径。
   一次点击可把在跑团队的 `order_block` 从 manual 推成 auto，所有 flagged 单立刻 409，
   业务侧看起来像系统故障而非配置变更，现场最可能的处置是「先把这开关关掉」= **二阶 fail-open**。
   防线：单调性判据钉进 CI + 函数头注写死禁改 + **不提供任何「全平台一键应用」**。
3. **DDL 两条 CHECK 随 flow 改名静默失效**：防线见上（按名存在断言 + 名单派生比对）。

---

## Q2-审计侧前置【审计侧落笔】

模板落码**前置**，五条：

1. **§09 新增小节「automation_policy_template 平台默认档模板」**：表定义、继承语义
   （建团队快照复制，非运行时回退）、闸类只能 auto、`purchase_execute`/`maintenance_run` 禁 auto、
   **模板无 config 无 enabled 的理由**，以及「**继承只发生在 `POST /teams`**」——
   非 HTTP 建团队路径一律不继承，**任何「团队必有 policy 行」的假设都不成立**。
2. **§09 `automation_policy` 列表追加 `origin` 列**（纯追加，不动 `:148` team_id 与 `:155` 唯一键正文）。
3. **`00-conventions.md:56` 的「全局（无 team_id）」表清单加 `automation_policy_template`。**
4. **⚠️ 硬提醒（会当场打红 CI）**：`backend/tests/test_automation_flow_contract.py` 是个
   **脆弱的 markdown 解析器**——它扫 §09 全文所有 `|` 行，第 1 列匹配 `[a-z][a-z_]+` 且
   **第 3 列是纯斜杠档位清单**就当 flow 注册行收进，且后出现的覆盖先出现的。
   所以新章节里**绝不能出现第 3 列是档位清单的表**（例如 `| order_block | 闸类 | auto | … |`），
   否则 `test_legal_modes_match_spec[order_block]` 立刻红。这不是审计侧的错，是判据的形状——
   若嫌它太脆，云端侧可另行加固（提单即可）。
5. **007 R2-09 增量2 的验收判据补一条模板继承。**

---

## Q2-仍需 Owner 定（两条阻塞 + 三条报备）

### 阻塞（不定则 2b 开不了工）

**B1｜模板写权归谁。** 方案把 `automation.template_admin` 钉成**超管专属**，但 Owner 原话里
那位「调好了档位的运营」**不是超管**——于是每次模板微调都要找超管，而 CLAUDE.md 明写
「不要默认丢回 Owner」。唯一绕法是给运营发超管账号（= 全团队数据 + 绕 RLS），
**为改一张模板授出一个上帝账号**。
选项：**(a)** 维持超管专属（保守，代价是运营每次找人）；
**(b)** 新建「平台运营」角色持有 `automation.template_admin`——那是**权限边界与角色模型变更**。
云端侧**按 (a) 保守落码**，但这个两难必须让 Owner 知道。

**B2｜非闸类 flow 能否在模板里设 auto。** DDL 只禁了闸类的非 auto、以及两条花钱/宪法级 flow 的 auto。
`scrape_to_audit` / `audit_to_listing` / `listing_dispatch` / `pricing_watch` **可以**在模板里设 auto，
那意味着**每个新建团队从第 0 秒起自动采集→审核→上架→改价，没有任何人看过一眼**——
方向上是 D-Q13「前期人工逐批」的反面。
这是有意留的敞口（堵死等于取消 Owner 要的功能本身），但属业务口径：**允许，还是也一并 DDL 禁掉**？
（禁掉则模板实际只能下发 manual/semi，功能价值大减。）

### 报备（不阻塞，但按升级清单必须让 Owner 知道）

**R1｜模板行下线用 DELETE**，与 `automation_policy`「最小面：无 DELETE」不同。
删的是平台配置行、方向保守（新团队回落 0 行 = manual = 今天行为）、带完整 before 审计镜像，
但属升级清单「删数据」。选它是为了不引入那个不受约束的 `enabled` 布尔。

**R2｜平台级审计事件在 `audit_log` 里没有归属。** `core/audit.py` 的 `AuditWriter.for_user`
取 `user.team_id`，而超管的 `team_id` 是 `None`（带 `X-Act-Team` 时等于他当时代表的那个团队），
`audit_sel` 又是 `team_id = app.current_team() OR app.is_super()`。合起来：
**超管改模板的审计行，要么非超管永远读不到，要么被错误归档进某个无关团队的审计流**。
Owner 点名要模板留痕，而这条留痕对受影响的团队管理员不可见。修它超出 R2-09 范围——
本单只写审计行并接受不可见，还是另立工单补平台级审计可见性？

**R3｜既有团队一律不回填。** 若要既有团队也继承，那是**另一支迁移、另一张单、单独真机验证**
——它改的是正在运营的团队的自动化行为，与本单「只影响未来新团队」不是一个风险等级。
**不会顺手塞进同一支迁移。**
