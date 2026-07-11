# R2-01 考古对照表：amazon-scraper-v3 worker 引擎 → ERP workers/

源仓：`/workspace/amazon-scraper-v3/worker/`（engine 2183 行 + parser 2199 行 +
session/adaptive/metrics/proxy）。目标：`workers/src/erp_worker/`，拨入 ERP 现有
`/worker/v1` 协议（R1-09 已建，见 evidence/R1-09/archaeology.md）。

## 移植策略：分三层

| 层 | 模块 | 处置 | 理由 |
|---|---|---|---|
| **爬虫核心（vendored 逐字移植）** | `parser.py` `session.py` `adaptive.py` `metrics.py` `proxy.py` | 逐字复制，仅改 import 路径（`common`/`worker` → `erp_worker`）。ruff/mypy 排除，保持与上游对齐 | 这是源仓运维沉淀的护城河：Amazon 选择器多层 fallback、curl_cffi TLS 指纹轮换、AIMD-Gradient2 自适应并发、TPS 换 IP——改一行都可能破反爬。零改动=零回归 |
| **协议胶水（新写）** | `erp_client.py` `payload.py` | 全新，替换 v3 的 `/api/*` server 通信层 | v3 说 `/api/tasks/*`，ERP 说 `/worker/v1/*` + node token 认证；数据落库结构也不同 |
| **编排壳（改写移植）** | `engine.py` `run.py` `config.py` | 保留流水线骨架与分级处置语义，剔除越界机制，换 server 通信 | 见下 |

## 协议层映射（v3 /api → ERP /worker/v1）

| v3 | ERP | 差异处置 |
|---|---|---|
| `POST /api/worker/register`（无，v3 用 X-Worker-Api-Key 静态密钥） | `POST /worker/v1/register`（enroll_token 换高熵机器令牌，一次性下发） | 令牌持久化到 `~/.erp_worker/<key>-<hash>.json`，重启复用；遗失需管理员删节点重注册 |
| `GET /api/tasks/pull`（worker_id + prefer_zip） | `GET /worker/v1/tasks/pull`（node token 认证，count） | prefer_zip 不移植（R1 协议无 zip 派发偏好，任务不带 zip） |
| `POST /api/tasks/result`（lease_epoch 校验） | `POST /worker/v1/tasks/result`（attempt 兼租约纪元） | **回传必须原样带回 pull 给的 attempt**——server 端租约校验 `(worker_id, attempt, status)`，不符即 stale 拒收 |
| `POST /api/tasks/release`（lease_epoch） | `POST /worker/v1/tasks/release`（task_id + attempt） | 下线归还本机未完成租约（engine `_cleanup` → `_leased` 表） |
| `POST /api/worker/sync`（心跳+指标+配额+restart） | `POST /worker/v1/sync`（metrics + window_state → settings + draining） | 配额/全局封锁/软重启的复杂协调下沉到 R2-04 beat；本单只做心跳+指标上报+settings 下发 |

## 数据落库映射（parser 扁平 dict → ERP product payload）

`payload.to_product_payload`：v3 parser 产出 45+ 字段的扁平 dict（字段名见
`parser._default_result`）→ ERP `product_upsert` 的结构化 payload：

- 五要素抽顶层：`title` / `brand` / `category_tree`→`category_path` /
  `category_ids`末段→`amazon_leaf_id` / `image_urls`(\n)→`images[]`
- 价格聚 `price_snapshot`：current/buybox/original_price + shipping + is_fba
- 其余全字段进 `attrs`（审核 L2/L3 + 上架属性填写都可能用）；多值字段
  (bullets/upc/ean/variation) 拆列表；`_` 前缀内部字段（`_page_asin` 等）不入库
- `N/A`/空串统一归一 None，杜绝脏值下游污染

## R1 协议不承载、故 engine 剔除的 v3 机制（挂后续工单）

| 剔除项 | v3 位置 | 归属 |
|---|---|---|
| 截图子进程存证（screenshot.py + gate monitor + HTML dump） | engine `_enqueue_screenshot_html` 等 | 独立能力，ERP R1 协议无 needs_screenshot 字段；未排期 |
| 卖家店铺发现 `discover_seller`（翻页扫 /s?me=） | engine `_process_seller_task` | R2-01 或选品单（三入口展开） |
| per-ASIN 邮编切换（drain 在飞→change_zip） | engine `_switch_session_zip` | ERP 任务不带 zip；多站点/多邮编采集未排期 |
| 批次进度门控（截图未完不拉新批） | engine task_feeder 前半段 | 依赖截图，随之剔除 |
| prefer_zip 派发偏好 / 优先级抢占清队列 | engine `_pull_tasks` 排序 | ERP pull 已 `ORDER BY priority DESC`；worker 侧不再二次排序 |
| 全局并发协调器（AIMD 窗跨 worker）/ 配额下发 / 软重启 | engine `_settings_sync` 后半段 | R2-04 worker/beat 底座 |
| 定时 auto-restart（execv 重载） | run_worker.py execv | 可选运维，Owner 用 Docker/任务计划重启替代 |

## 保真锁死的关键不变量（移植时逐条核对）

1. **租约纪元原样回传**：`_process_task` 里 `attempt_lease = task["attempt"]`，成功/失败
   回传都带这个值，绝不本地自增。（v3 lease_epoch 语义）
2. **传输错误 ≠ 渠道拒绝**：v3 `_pull_tasks`/`_submit_result` 网络异常返回 None 而非抛，
   `erp_client` 保真：pull 服务器错误→None（区别于"无任务"空列表），result 失败→None
   （不本地重投，靠 server 超时回收）。
3. **variant 偏移直接终态失败**：请求 A 拿到兄弟 variant B → 不轮换不重试（v3 2026-05-20
   复盘：轮换重试结果一样且打爆代理配额），直接 `error_type=variant_offset` 回传。
4. **降级页 vs 有效空状态**：核心字段全缺=降级页要轮换重试；但 `No Featured Offer` /
   `不可售` 是有效终态，不算降级（v3 `_is_degraded` 判定保真）。
5. **被封先轮换再回传失败**：`is_blocked` → `_rotate_session` → 回传 failed，让 server
   决定是否再派发（本地不占用 attempt）。

## 验收（L1）

见 `.agent/evidence/R2-01/runbook.md`：Owner 机器 worker 真抓 ≥10 真实 ASIN →
产品库字段完整（标题/品牌/图片/五点/价格）+ 与旧系统同 ASIN 对照一致 + 失败任务正确回收。
沙盒不真抓（宪法禁），本单沙盒交付=离线全绿（26 单测：payload 适配 / 协议 MockTransport /
引擎分级处置）+ CLI 可启动 + parser 离线解析冒烟。
