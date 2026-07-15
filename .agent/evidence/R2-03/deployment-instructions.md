# R2-03 部署机指令（给本地 AI 整段粘贴）

> 铁律：绝不操作生产库 erp_all 的**结构**（本任务只走 ERP-ALL 自带 import CLI 写数据，
> 无 pg_restore/无 DDL）；任何暂存文件用完删除；不输出任何密钥/凭证明文；
> 沙盒/本机都不得绕过 walmart_client 直连渠道 API。

## 任务 1：灌 pt_spec.fields 全量规格（上架弹药，二选一路径）

先 `cd ERP-ALL && git pull`（需要 0020 迁移与新 CLI；`alembic upgrade head` 由部署流程照常执行）。

### 路 A（仅当本机有旧 erpAPI 仓时可走；2026-07-15 实测部署机没有 → 走路 B）：live spec 拉取

1. 在旧 erpAPI 仓用既有 live_spec 通道拉官方 spec（走 walmart_client + A152 凭证，
   限流 10/min、≤20 PT/次）。Python 一段（旧仓根目录跑）：

   ```python
   from auto_listing.live_spec import fetch_spec
   import json
   # 覆盖 ≥5 个将用于验收的 WPT + 各自变体常用 PT，一次 ≤20 个
   pts = ["Drinkware", "Cutting Boards", "Area Rugs", "Table Lamps",
          "Water Bottles", "Storage Baskets"]  # 按实际选品改
   resp = fetch_spec(pts)
   json.dump(resp, open("/tmp/live_spec_batch1.json", "w"))
   ```

2. 转换 + 导入（ERP-ALL/backend 下）：

   ```bash
   uv run python -m erp.tools.extract_mp_item_spec \
       --live-response /tmp/live_spec_batch1.json --out /tmp/pt_spec_fields.jsonl
   uv run python -m erp.tools.import_pt_spec --file /tmp/pt_spec_fields.jsonl --chunk-size 200
   rm /tmp/live_spec_batch1.json /tmp/pt_spec_fields.jsonl
   ```

3. 验证：`SELECT count(*) FROM refdata.pt_spec WHERE fields IS NOT NULL;` 应 ≥ 拉取 PT 数+1
   （含 `__orderable__` 伪行）；`SELECT status, ok_rows FROM app.import_job ORDER BY id DESC LIMIT 1;` 应 done/零错误。

### 路 B（全量，T7 monolith；2026-07-15 起为主路径）

1. Owner 从 T7 备份 pt_metadata/ 拷 `MPSetup/5.0.*_MP_ITEM_0_0_en.json`（~451MB）到部署机
   （已完成：`5.0.20260330-14_47_14-api` 快照，SHA-256 校验过，6,951 PT）。
2. `pip install ijson`（流式解析，451MB 不进内存——源仓 OOM 事故教训；≥3.1，
   实测 3.5.1）。**Decimal 序列化 bug 已修**（use_float=True + 写出兜底），
   需 main 含该修复 commit 再跑。
3. 同上 extract（`--monolith <路径>`）→ import（--chunk-size 200，预计几分钟）→ 验证 → 删临时文件。
4. **header version 说明**：T7 快照是 20260330，而 `listing.feed_header` 默认 version=
   `5.0.20260304-22_45_32-api`（旧系统实测被渠道接受的提交值）。**保持默认不用改**——
   version 串与本地规格快照不需要严格一致；A152 真调若遇 EXT_DATA_ERROR_74597363510508
   再改 system_config 对齐 20260330。

## 任务 2：灌错误码字典（一条命令）

```bash
uv run python -m erp.tools.import_error_catalog   # 随包实战种子 65 码
```

## 任务 3：验收① dry-run 真数据报告

前置：任务 1 完成；`ERP_LLM_API_KEY` 已在环境（AI 属性填写用，走 llm_cache 记账，
成本极低——v4-flash + 静态前缀缓存）。

```bash
uv run python -m erp.tools.listing_dryrun --auto 12 --fill \
    --out /tmp/dryrun-report.json
```

- 期望输出末行 `验收①判定：PASS ✅`。判定=005 验收①原文口径：**通过官方 spec 校验的
  产品覆盖 ≥5 个不同 WPT**（2026-07-15 修正——第 1 版误设为"全部产品过"，严于验收原文）。
- 个别产品源数据贫瘠（如仅 1 条卖点补不满 keyFeatures minItems）被本地校验拦下属正常
  ——旧系统会照发吃渠道拒，新系统本地拦截省配额；这类品列在 summary.failed，
  处置=补文案后重投，不改产品业务数据。
- 把 `/tmp/dryrun-report.json` 回传（贴 summary；有 failed 贴 errors 全文）。
- 其它 FAIL 因：某产品类目无直判（先跑 `uv run python -m erp.tools.resolve_categories
  --backlog` 填图）；某 WPT 的 fields 未拉（monolith 全量导入后不应出现）。
- 第 1 轮真数据结果（9/12 过、5 WPT，按原文口径已达标）见
  `.agent/evidence/R2-03/dryrun-real-data-run1.md`。

## 任务 4（Owner 窗口，验收②，先不做）

A152 真调 1 SKU → PROCESSED → live 回写 → 后台截图 → delist。
runbook：`.agent/evidence/R1-11/a152-live-runbook.md`。
**前置闸门：RS-03b（channel 写路径 outbox+幂等）必须先完成**——云端 AI 下一单做，
做完会更新本文件。届时再排窗口。
