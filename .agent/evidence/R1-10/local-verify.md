# R1-10 本地验证记录（2026-07-10）

## 验收对照（specs/003 §R1-10）

| 验收项 | 结果 | 证据 |
|---|---|---|
| audit 5 表 migration | ✅ | 0008：audit_run/audit_hit（月分区）+ audit_policy + llm_cache + llm_usage_log（月分区）；另建黑名单四表 + refdata.trademark（升→降→升演练通过） |
| L0 黑名单（手工种子 10 行） | ✅ | 0008 全局种子 10 品牌；四层顺序（卖家→ASIN→类目→符号→品牌）+ 占位符白名单保真移植 |
| L2（refdata.trademark 子集） | ✅ | R4 词边界黑名单扫 + R5 USPTO LIVE 反查（软证据 penalty=0 传 L3）；表就绪，1 万行子集导入待 Owner 机器（源数据 uspto 库在其本地） |
| L3（1 条策略 + llm_cache + usage_log） | ✅ | l3_intellectual_property 策略种子；缓存键 sha256(model+messages+温度+max_tokens)；命中也记 usage 行 cost=0 |
| 单 ASIN 走 L0→L2→L3 出 verdict | ✅ | test_full_reject_with_evidence_and_cost：R4+R5 证据 + L3 reject + product→audit_rejected |
| 同输入二次运行 cache 命中 cost=0 | ✅ | test_second_run_cache_hit_cost_zero：cost=0、无真调用、cache_hit_rate=1.0、hit_count+1 |
| usage_log 有行 | ✅ | 真调用行（cost>0，单价来自 system_config llm.pricing）+ 缓存行（cache_hit=true, cost=0） |
| 移植保真（考古纪律） | ✅ | archaeology.md 先行入库；_coerce 逐条移植含 ⭐is_real_brand 强制翻案（4 个单测锁死） |

## 测试

- `uv run pytest` → **67 passed**（新增 test_audit_api.py 11 例）×2 稳定。
- ruff check / ruff format --check（全目录，含 tests——上单教训）/ mypy strict 全过。
- LLM 全程 MockTransport 替身，零真调用；密钥走 ERP_LLM_API_KEY 环境变量。

## 关键设计（后续单据依赖）

- audit_policy.version 注入 system prompt 首行 → 策略 config 变更自动新缓存键（无需清缓存）。
- L2 查询全部 ORDER BY——命中词顺序进 L3 缓存键，顺序不定=缓存失效（自踩预防）。
- verdict 联动：pass→audit_passed / reject→audit_rejected + latest_audit_run_id 回写（R1-11 上架单据以 audit_passed 为准入）。

## R2 欠账（已在 archaeology.md 标注）

L1 类目判定、L2 R1/R2/R3/R7/R8、37 条政策全量种子（lark-cli 拉 OJSrkV）、
L4 视觉（策略占位 enabled=false）、Aho-Corasick 替换 regex、内存字典加载器。
