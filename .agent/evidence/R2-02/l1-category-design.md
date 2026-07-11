# R2-02 L1 类目判定设计（下一片，卡 Owner 的 embedding API + 数据）

源：`walmart-audit-system/pipelines/l1_category.py`（1032 行）+ `integrations/embed_client.py`
+ `sync/build_pt_embeddings.py`。L1 = 判断产品属于哪个沃尔玛 Product Type（WPT），
L2 的 R1/R2/R3（类目准入 / 禁售大类 / 认证要求）全依赖它，上架 spec 也依赖它。

## 方法修正（Owner 澄清 2026-07-11）：映射表 + LLM，非嵌入

Owner 原系统类目判定 = **映射表（category_map）+ LLM 语义理解**，**不是**向量嵌入。
故 ERP L1 主路径不需 embedding API：
- **主路径（可立即建，只卡 category_map 数据）**：category_map（Amazon 路径→WPT 候选）
  + LLM 语义复排选最终 WPT + `_coerce_llm` 规范化 + direct 高置信短路。复用现有
  llm_cache/usage_log，**不引入新外部服务**。
- **可选增强（后置，非必需）**：pt_embeddings 向量语义召回，补映射表未覆盖的品。要它才需
  embedding API + 6832 嵌入数据；不要它 L1 也能跑（覆盖率略低）。

**因此 L1 的唯一 Owner 依赖降为 category_map 数据**（走 import_job domain=category_map，
DG1 通道）。沙盒可全建主路径 + fake LLM 测试；真验收（旧系统 4326 ASIN 子集 ≥90% 一致）
待 category_map + 黑名单/商标/政策数据到位。embedding 增强作为独立后续，卡 Owner 选嵌入模型。

## 源仓 classify() 架构（移植蓝图）

1. **direct high-confidence 短路**（`_try_direct_high_confidence`）：amazon 类目/PT
   在字典里精确命中 → 直接给 WPT，不调 LLM（省钱）。
2. **hybrid 候选**（`_fetch_candidates_hybrid`）：
   - embedding 召回：产品文本嵌入 → 与 6832 pt 向量 cosine 相似度 top-40
     （源仓 numpy 内存算；**ERP 改用 pgvector `embedding <=> :q` cosine 距离检索**）。
   - 关键词反查：category_map 命中 top-15。
   - 两路合并去重。
3. **LLM rerank**：候选列表 + 产品文本 → LLM 选最终 WPT（复用 llm_cache/usage_log 记账）。
4. **spec override + unmapped-amazon-path 守卫**（`_apply_spec_override` /
   `_check_unmapped_amazon_path`）：某些 PT 有强制 spec 映射；amazon 路径未映射时降级处理。
5. **`_coerce_llm` 规范化**：非法/幻觉 WPT → 回退；置信度归一（同 L3 coerce 纪律）。

## ERP 落地清单（下一 session）

- migration 0012：`refdata.pt_embeddings (pt_name text PK, embedding vector(N), dims int,
  updated_at)` + `CREATE EXTENSION vector`（CI pgvector 镜像有；沙盒 apt 已装
  postgresql-16-pgvector 可测）+ ivfflat/hnsw 索引 + grant。可能另需 category_map 表。
- settings：`embed_api_base` / `embed_api_key`（→ ERP_EMBED_API_BASE / ERP_EMBED_API_KEY），
  同 llm 三件套模式。
- `audit/embed_client.py`：嵌入客户端抽象（真 API via httpx + 确定性 fake 供测试注入）。
- `audit/l1_category.py`：pgvector cosine 检索 + 关键词候选 + LLM rerank + coerce + 短路。
- import：domain=pt_embeddings（向量灌入）+ domain=category_map（走既有 import_job 通道）。
- 一个生成工具 `tools/build_pt_embeddings.py`（读 walmart_specs/all_product_types.json，
  调 embed API 逐条嵌入 → 灌 refdata.pt_embeddings）——Owner 机器执行（需 #1）。
- 测试：fake embedder + 手插向量 + pgvector 检索 + mock LLM rerank + coerce。
- 接线：L2 R1/R2/R3 用 L1 输出的 WPT 判类目准入/禁售大类/认证（源仓 l2_rules R1-R3）。

## Owner TODO（解锁 L1）

1. 提供 embedding API（服务地址 + key）——决定用哪个嵌入模型（volcengine / OpenAI /
   deepseek 是否有 embeddings / 本地模型）。
2. category_map 数据导出（amazon→walmart，走 import 通道）。
3. 黑名单 3.6万 / 商标 14.18M / 37 政策全量导入（前几片通道已就绪）——这三样也是
   R2-02 整单 L1 验收（≥90% 一致率）的前提。
