# R2-03 部署机指令（给本地 AI 整段粘贴）

> 铁律：绝不操作生产库 erp_all 的**结构**（本任务只走 ERP-ALL 自带 import CLI 写数据，
> 无 pg_restore/无 DDL）；任何暂存文件用完删除；不输出任何密钥/凭证明文；
> 沙盒/本机都不得绕过 walmart_client 直连渠道 API。

## 任务 1：灌 pt_spec.fields 全量规格（上架弹药，二选一路径）

先 `cd ERP-ALL && git pull`（需要 0020 迁移与新 CLI；`alembic upgrade head` 由部署流程照常执行）。

### 路 A（推荐先走，验收①够用，无需 T7）：live spec 拉取

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

### 路 B（全量 6,942 PT，需 Owner 从 T7 拷文件）

1. Owner 从 T7 备份 pt_metadata/ 拷 `MPSetup/5.0.*_MP_ITEM_0_0_en.json`（~451MB）到部署机。
2. `pip install ijson`（流式解析，451MB 不进内存——源仓 OOM 事故教训）。
3. 同上 extract（`--monolith <路径>`）→ import（--chunk-size 200，预计几分钟）→ 验证 → 删临时文件。

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

- 期望输出末行 `验收①判定：PASS ✅`（全部 validation.ok 且 distinct WPT ≥5）。
- 把 `/tmp/dryrun-report.json` 回传（贴 summary + 任一 FAIL 项的 validation.errors 全文）。
- FAIL 常见因：某产品类目无直判（先跑 `uv run python -m erp.tools.resolve_categories --backlog`
  填图）；某 WPT 的 fields 未拉（把该 WPT 补进任务 1 的 pts 列表重拉）。

## 任务 4（Owner 窗口，验收②，先不做）

A152 真调 1 SKU → PROCESSED → live 回写 → 后台截图 → delist。
runbook：`.agent/evidence/R1-11/a152-live-runbook.md`。
**前置闸门：RS-03b（channel 写路径 outbox+幂等）必须先完成**——云端 AI 下一单做，
做完会更新本文件。届时再排窗口。
