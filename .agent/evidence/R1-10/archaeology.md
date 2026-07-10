# R1-10 考古对照表：walmart-audit-system → ERP audit 域

源仓：/workspace/walmart-audit-system（L0→L4 全链路，已在全量 4326 ASIN 跑通）。
R1-10 范围 = L0（黑名单字典）+ L2（商标子集）+ L3（1 条策略）+ llm_cache + usage_log；L1/L4 留 R2。

## L0（源仓 Phase 0，"只做 100% 确定性硬判断"）

四层顺序，任一命中即 reject 不进后续（pipelines/phase0.py）：
1. `phase0_lark_blacklist` — seller_id / asin / amazon_cat 人工拉黑（最高优先）
2. `phase0_category` — Amazon 顶级 8 大类禁售（Books/Kindle/Clothing/Automotive…）
3. `phase0_trademark` — title/bullets/desc 含 ® ™ ℠ © 强 IP 信号（原 L2 R9 前置）
4. `phase0_brand` — brand 字段**精准等值**命中黑名单（36k 全量 + yaml 手工补 20+）

关键保真细节（phase0_brand.py）：
- 匹配 = lowercase + 多空格压一后完全相等；**不做子串**（子串归 L2 R4）
- `_NON_BRAND_PLACEHOLDERS` 白名单：n/a、none、unbranded、generic、oem、不详、无品牌等
  占位符不当品牌拦（否则 6/10 Amazon 产品被误挡）——必须移植
- stopword 过滤（通用英文词不算品牌）
- 设计红线：黑名单覆盖不足是数据侧问题，靠 sync 补数据，**不在 L0 加文本启发式**

R1-10 落地：ERP 已有 blacklist 四表规格（001 §04）→ 建表 + 手工种子 10 行；
L0 服务 = 占位符白名单 + 规整等值匹配 + 命中写 audit_hit(level=l0, is_hard=true)。

## L2（源仓 l2_rules.py v3，2026-04-27 重构原则）

**L2 双职责：硬拒（类目/认证级不可救）+ 证据收集（塞 detail 给 L3）；
不靠累积扣分自行 reject（会绕过 L3 语义判断，误伤率高）。**

| 规则 | 分值 | 语义 | R1-10 处置 |
|---|---|---|---|
| R1 cat_access_blocked | -100 硬拒 | PT 准入双字段白名单 | R2（依赖 L1 类目判定） |
| R2 forbidden_mega_cat | -100 硬拒 | 17 大禁售类目 | R2（依赖 L1） |
| R3a cat_requires_cert_hard | -100 硬拒 | FDA/UL/NRTL/CPSIA/EPA | R2（依赖 L1） |
| R3b/R3c 认证软合规 | 0 软证据 | 电气小件 NRTL / SDS/ASTM/RoHS | R2 |
| **R4 title_desc_blacklist** | 0 软证据 | Aho-Corasick 黑名单词命中 title/desc，交 L3 判真品牌/通用词 | **R1-10 移植**（词边界子串扫，stopword 过滤，detail.matches=[{brand, matched_phrase}]） |
| **R5 trademark_live** | 0 软证据 | USPTO LIVE 商标命中（按 Nice Class 过滤） | **R1-10 移植简化版**：refdata.trademark 1 万行子集，大写开头词反查 LIVE；Nice Class 过滤 R2（依赖 L1 PT） |
| R7 content_promotional | 0 软证据 | promotional claims | R2 |
| R8 walmart_strict_sensitive | 0 软证据 | 文化/宗教/政治/武器/成人 7 子类 | R2 |
| R6/R9 | — | R6 已删除；R9 已迁 L0 | 不存在 |

起始 100 分、<60 reject 的打分骨架保留（audit_run 记 score），R1 实际只有软证据 → 不会因分数拒。

## L3（源仓 l3_llm.py，2026-04-28 精简版设计）

**Prompt 架构（必须保真）**：
- system prompt = 业务约束（中国搬运卖家无授权，任何授权声明=虚假）+ 政策匹配两类法
  （A 品类整体禁售不论用途 / B 用途特征敏感需文本佐证）+ 4+1 判定维度
  （品牌真伪/冒犯性/IP/brand_misuse/儿童CPC兜底）+ 严格 JSON 输出规范
  + **37 条 Prohibited Policy 全清单静态附在 system prompt 末尾**
  （关键成本设计：静态段吃 provider 的 prompt cache；旧方案按 PT 路由 5-6 条放
  user prompt 导致 cache miss——移植时不得回退）
- user prompt 只放产品文本 + L2 命中词 + 路由提示一行
- 默认 pass 原则：只有明确证据才 reject
- 特殊语法铁证：compatible for X / fits X / replacement for X / works with X / OEM for X → X 必为品牌

**结果规范化 `_coerce_result`（必须逐条移植）**：
- verdict 非法 → 默认 pass（保守）
- reason_category 白名单校验 + 旧标签兼容映射（ip_infringement→intellectual property 等）
- pass 时强制 category=none
- ⭐ **强制翻案**：任一 blacklist_brand_verdict.is_real_brand=true → verdict 强制 reject
  （即使 LLM 自己说 pass）， category=intellectual property
- llm_confidence ∉ {high,medium,low} → medium
- reject → RuleHit(stage=L3, rule_code=llm_{category_slug}, penalty=0)

**37 条政策数据源**：飞书表 OJSrkV → sync_prohibited.py → walmart_prohibited_policy 表
（TRUNCATE+重灌）。ERP 侧 = audit_policy 表种子；R1-10 先落 1 条代表性策略
（选 Intellectual Property——L3 判定维度的主轴），37 条全量随 R2 L1 接入时经 lark-cli 拉取。

## llm_cache（integrations/llm_cache.py）

- key = sha256(model + messages(sort_keys) + temperature + max_tokens) **前 32 位**
- 命中路径 = 单条 UPDATE hit_count+1, last_hit_at=now() RETURNING response（原子）
- "不缓存产品，缓存输入"——产品内容变了 messages 自然变
- ERP 规格差异：001 §05 llm_cache.cache_key TEXT PK——用完整 sha256 hex（64 位）即可，
  截 32 位是源仓省空间习惯，无碰撞纪律价值

## usage_log（integrations/usage_logger.py + pricing.py）

- 每次调用一行（**缓存命中也记行 cost=0**，保真实调用画像——001 §05 明确继承）
- 从 OpenAI 兼容 response 提 usage；支持 deepseek prompt_cache_hit_tokens 分段计费
- 失败仅 warn 不影响审核流
- ERP：单价表进 system_config（llm.pricing），禁止写死

## 编排（orchestrator.audit_one → ERP audit service）

L0 命中 → verdict=reject, reject_level=l0，短路；否则 L2 收证据 → L3 判定 → verdict。
audit_run 记 llm_cost_usd / cache_hit_rate / duration_ms；audit_hit 每命中一行。
verdict 联动：pass → product.status=audit_passed；reject → audit_rejected；
needs_review → 人工队列（R1-10 不产 needs_review——L3 二值输出，保留状态位）。

## R1-10 明确不做（防越界）

- L1 类目判定（embedding 检索 + LLM 复排）→ R2
- L4 视觉审核 → R2（audit_policy 种子里 enabled=false 占位）
- L2 R1/R2/R3/R7/R8（依赖 L1 产物）→ R2
- 37 条政策全量种子 → R2（本单 1 条）
- worker 分布式审核队列 → 复用 R1-09 拨入协议的路子，R2 统一
