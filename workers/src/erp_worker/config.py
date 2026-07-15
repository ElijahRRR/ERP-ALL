"""采集 worker 运行配置（移植源 amazon-scraper-v3 common/config.py 的 worker 子集）。

只保留 worker 侧需要的调参常量与浏览器指纹；server 路径 / 导出映射 / DB 已剔除。
运行期这些值会被 ERP `/worker/v1/sync` 下发的 `scrape.worker_settings` 覆盖
（见 engine._apply_settings），此处仅作离线默认与启动兜底。

代理地址（PROXY_URL）优先级：CLI --proxy > 环境变量 PROXY_URL > ERP sync 下发。
"""

import os

# ── 采集基础 ──
DEFAULT_ZIP_CODE = os.environ.get("DEFAULT_ZIP_CODE", "10001")
MAX_CLIENTS = 32
# 单请求总超时（秒）。源仓默认 15 按直连/大带宽调校；TPS 代理链路整页抓取常态更慢
# ——部署侧必须按实际链路调（env REQUEST_TIMEOUT 或 scrape.worker_settings.request_timeout，
# 运行期下发变更会触发 session 轮换生效）。A152 真调实测 15s 全量超时的教训。
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "15"))
MAX_RETRIES = 3
SESSION_ROTATE_EVERY = 1000

# ── 中流停滞防御（2026-07-15 真机实测：TPS 坏出口 IP 收到部分响应后卡死；
#    且重试复用连接池同一隧道=钉死同一坏 IP）──
# 传输速率 < LIMIT 字节/秒 持续 TIME 秒 → 中止该请求（0 任一=关闭该防御）
LOW_SPEED_LIMIT_BPS = int(os.environ.get("LOW_SPEED_LIMIT_BPS", "1024"))
LOW_SPEED_TIME_S = int(os.environ.get("LOW_SPEED_TIME_S", "8"))
# 连续 N 次传输停滞（跨任务累计，成功即清零）→ 轮换 session（新连接=TPS 新出口 IP）
STALL_ROTATE_THRESHOLD = int(os.environ.get("STALL_ROTATE_THRESHOLD", "3"))

# ── 令牌桶限流（QPS）──
TOKEN_BUCKET_RATE = 64.0

# ── 自适应并发控制（AIMD）──
INITIAL_CONCURRENCY = 16
MIN_CONCURRENCY = 16
MAX_CONCURRENCY = 32

# 代理套餐带宽（Mbps）。0=不启用带宽软顶/分摊限速；声明真实值后 AIMD 会在
# 用量达 BANDWIDTH_SOFT_CAP 时背压（env PROXY_BANDWIDTH_MBPS 或 settings 下发）
PROXY_BANDWIDTH_MBPS = float(os.environ.get("PROXY_BANDWIDTH_MBPS", "0"))
ADJUST_INTERVAL_S = 3
TARGET_LATENCY_S = 5.0
MAX_LATENCY_S = 8.0
TARGET_SUCCESS_RATE = 0.95
MIN_SUCCESS_RATE = 0.85
BLOCK_RATE_THRESHOLD = 0.05
BANDWIDTH_SOFT_CAP = 0.80

TASK_QUEUE_SIZE = 64
TASK_PREFETCH_THRESHOLD = 0.5

# ── 代理（TPS 模式：每次请求经固定代理地址自动换出口 IP）──
PROXY_URL = os.environ.get("PROXY_URL", "")

# ── 浏览器指纹（UA + impersonate + sec-ch-ua 三者匹配，随机轮换）──
BROWSER_PROFILES = [
    {
        "impersonate": "chrome120",
        "sec_ch_ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "user_agents": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ],
    },
    {
        "impersonate": "chrome123",
        "sec_ch_ua": '"Google Chrome";v="123", "Not:A-Brand";v="8", "Chromium";v="123"',
        "user_agents": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        ],
    },
    {
        "impersonate": "chrome124",
        "sec_ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "user_agents": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        ],
    },
    {
        "impersonate": "chrome131",
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "user_agents": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        ],
    },
    {
        "impersonate": "chrome136",
        "sec_ch_ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
        "user_agents": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        ],
    },
]

# ── 启动校验（与源仓一致）──
assert MIN_CONCURRENCY <= INITIAL_CONCURRENCY <= MAX_CONCURRENCY
assert TARGET_LATENCY_S < MAX_LATENCY_S
