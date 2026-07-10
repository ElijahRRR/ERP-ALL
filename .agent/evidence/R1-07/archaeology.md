# R1-07 考古对照表（移植前提取，2026-07-10）

## 源 1：erpAPI/walmart_client.py（497 行，生产实战版）
| 机制 | 行号 | 移植处置 |
|---|---|---|
| socket.setdefaulttimeout(90) 防 SOCKS hang（2026-05-12 实战：SSL read 卡 2.5h） | L40-43 | ✅ 保留注记；async httpx 用显式 Timeout |
| 按代理维度池化 httpx.Client（HTTP/2 + 连接级 retries=2 + limits） | L82-108 | ✅ 移植为 AsyncClient 池 |
| _invalidate_client 半死连接自愈（SOCKS 隧道半死/静默 RST/GOAWAY） | L111-125 | ✅ 移植 |
| token 900s 缓存-60s 提前过期 + 双检锁 | L213-255 | ✅ 移植（asyncio.Lock，按 store 键） |
| make_headers 五头 | L262-271 | ✅ 原样移植 |
| _parse_retry_after：Retry-After > X-Next-Replenishment-Time(epoch ms/ISO) > 60s，上限 300s | L274-310 | ✅ 原样移植 |
| 401 自愈：清缓存→就地刷新→重试1次（独立于 max_retries） | L374-393 | ✅ 移植 |
| 429/5xx opt-in 指数退避；POST 默认不重试（非幂等，feed 反查决定） | L395-414, L485-497 | ✅ 移植；写路径重试语义交上层 verify-back |

## 源 2：erp-core/backend/app/services/rate_limiter.py（GCRA）
| 机制 | 行号 | 移植处置 |
|---|---|---|
| WALMART_ENDPOINT_LIMITS 表（含用户实测校正：MP_ITEM=10/hour 非 20/min！L52-54） | L31-62 | ✅ 原样移植（注记保留） |
| GCRA tat/emission_interval 算法 + next_avail 自适应 | L65-133 | ✅ 移植为 async |
| update_from_walmart_headers：x-current-token-count=0 时按响应头推后 | L92-133 | ✅ 移植 |
| gate() max_wait 自适应（endpoint period×1.1，修 MP_ITEM 65s 即失败的坑，2026-05-07 用户校正） | L177-193 | ✅ 移植 |
| (store, endpoint) 桶注册表 | L136-165 | ✅ 移植；进程内实现，Redis backend 接口预留（多 worker 时 R2 接） |

## 新增（ERP-ALL 独有）
- 三模式 dry_run / live_test / live（验证纪律）：dry_run 只构造快照不发包；live_test 仅 is_test 店；live 需 system_config channel.live_enabled（Owner 放量开关）
- 凭证/代理从 DB 解密解析（R1-08 服务），不再读 xlsx
