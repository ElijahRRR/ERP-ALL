# R2-02 L3 静态 37 政策 prompt 设计（下一片，待两 Opus 片集成后由 Fable 实现）

源：`/workspace/walmart-audit-system/pipelines/l3_llm.py`（2026-04-28 精简版）+ `l3_policy_router.py`。
本片有**数据依赖**（37 政策文本只在 lark OJSrkV + 旧库，沙盒无），故拆两半：
- **代码（沙盒可建）**：政策参考表 + import_job `domain=policy` 导入路径 + L3 prompt 静态段拼接。
- **数据（Owner 导）**：37 条政策经 import 通道灌入（或 lark-cli 拉）。空表时 L3 退回当前单策略行为。

## 必须保真的成本设计（易回退，务必锁死）

源仓 l3_llm.py 血泪教训（注释 line 195, 209-213, 281-287）：
- **37 条政策全清单静态拼在 system prompt 末尾**，所有产品共享同一份 → 吃 DeepSeek/OpenAI
  prefix cache（前缀完全匹配）。cache 命中率从旧方案（按 PT 路由 5-6 条塞 user prompt）
  的 ~63% 提到 ~95%+。
- **绝不能按 PT 动态把不同政策拼进 prompt** —— 动态段会让每个产品的 system prefix 不同 →
  cache miss → 成本爆炸。ERP 现有 L3 已用 `policy.version` 进 system 首行做失效（R1-10），
  拼 37 政策后：**版本号 + 静态政策块都在 system，产品文本在 user**，前缀稳定。
- 每条政策压到 ~240 字（header: category_en/zh + zh_seller_risk；overall_status；
  prohibited_items 摘要），37 条 ≈ 5500 字 ≈ 4-5K token，可接受。
- `reason_category` 候选 = 37 个 category_en + `brand_misuse` + `none`（coerce 白名单要同步扩）。

## 数据模型（拟）

ERP `audit_policy`（0008）是规则配置表（code/level/enabled/config/version），**不是**这 37 条。
37 条是参考数据，另立表（无 team）：
```
refdata.prohibited_policy (
  id int PK, category_en text, category_zh text, overall_status text,
  prohibited_items text, conditional_items text, preapproval_items text,
  zh_seller_risk text, zh_seller_notes text, updated_at timestamptz
)
```
（放 refdata，与 trademark 同域：大参考数据、导入专写、业务只读。migration 0011+，
取决于两 Opus 片是否已占用迁移号——集成时确认。）

## 落地清单（集成后实现）

1. migration：`refdata.prohibited_policy` 表 + grant erp_app SELECT + import_read 无需新权限。
2. import_service：加 `domain='policy'` 路径（upsert on id，refdata schema，同 trademark 结构）
   —— 注意：Agent B 已在 import_service 加了 trademark 路径，本片在其基础上加 policy，避免重复重构。
3. pipeline.py L3：`_format_full_policy_block()` 等价物——读全部政策 ORDER BY id 压 240 字拼
   `L3_SYSTEM_PROMPT` 末尾；空表 → 返回空串（退回当前单策略行为，不报错）。
   —— 注意：Agent A 已改 pipeline.py 的 R4 段，本片改 L3 段，集成后在合并树上做。
4. audit/service.py：L3 调用处 system prompt = `[policy_v{ver}]` + L3_SYSTEM_PROMPT + 政策块；
   coerce_l3_result 的 reason_category 白名单扩为动态（读 refdata.prohibited_policy.category_en）。
5. 测试：空表退回单策略；N 条时 system prompt 含政策块且 user prompt 不含政策（prefix 稳定）；
   同一产品重复审核 cache_hit=1 成本 0（证明 prefix cache 生效）。

## Owner 侧

37 政策数据导入：`import_trademark`/`import_blacklist` 同款 CLI（domain=policy）读 lark 导出的
csv/jsonl；或直接 lark-cli 拉 OJSrkV。属 R2-02 数据灌入的一部分，与黑名单/商标全量同批。
