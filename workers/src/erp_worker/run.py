"""采集 worker 启动入口。

用法（Owner 本地机器，需可出站访问 ERP api + 持有 TPS 代理）：

  python -m erp_worker.run \\
    --server http://<部署机IP>:8000 \\
    --enroll-token <system_config scrape.worker_enroll_token> \\
    --proxy http://user:pwd@tps-host:port

参数优先级：CLI > 环境变量。代理也可用环境变量 PROXY_URL；注册令牌用 ERP_WORKER_ENROLL_TOKEN。
node-key 缺省 = erp-<主机名>，重启复用同一身份（令牌存 ~/.erp_worker/）。
"""

import argparse
import asyncio
import logging
import os
import signal
import socket
import sys
from typing import Any

from erp_worker import config
from erp_worker.engine import ScrapeEngine
from erp_worker.erp_client import ErpWorkerClient


def _normalize_server(url: str) -> str:
    """worker_router 挂在 /api/v1；容忍用户只给到根，自动补全。"""
    url = url.rstrip("/")
    if url.endswith("/api/v1"):
        return url
    if url.endswith("/api"):
        return url + "/v1"
    return url + "/api/v1"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ERP Amazon 采集 worker")
    p.add_argument(
        "--server", default=os.environ.get("ERP_SERVER"), help="ERP api 地址，如 http://IP:8000"
    )
    p.add_argument(
        "--enroll-token",
        default=os.environ.get("ERP_WORKER_ENROLL_TOKEN"),
        help="注册令牌（= system_config scrape.worker_enroll_token）",
    )
    p.add_argument(
        "--node-key",
        default=os.environ.get("ERP_WORKER_NODE_KEY"),
        help="节点标识，缺省=erp-<主机名>",
    )
    p.add_argument(
        "--proxy",
        default=os.environ.get("PROXY_URL"),
        help="TPS 代理地址 http://user:pwd@host:port",
    )
    p.add_argument(
        "--allow-direct",
        action="store_true",
        help="显式允许无代理直连采集（仅开发调试；生产缺代理默认拒绝启动）",
    )
    p.add_argument("--zip-code", default=None, help="配送邮编（默认 10001）")
    p.add_argument(
        "--concurrency", type=int, default=None, help="并发上限（默认 32，受 server 配额约束）"
    )
    p.add_argument(
        "--kind", default="scraper_amazon", choices=["scraper_amazon", "scraper_walmart"]
    )
    p.add_argument("--log-level", default="INFO")
    return p


async def _run(args: argparse.Namespace) -> int:
    if not args.server:
        print("缺少 --server（或环境变量 ERP_SERVER）", file=sys.stderr)
        return 2
    if args.proxy:
        config.PROXY_URL = args.proxy
        os.environ["PROXY_URL"] = args.proxy

    node_key = args.node_key or f"erp-{socket.gethostname()}"
    client = ErpWorkerClient(_normalize_server(args.server), node_key, kind=args.kind)
    try:
        await client.ensure_registered(args.enroll_token or "", version="r2-01")
    except Exception as e:  # noqa: BLE001
        print(f"注册失败: {e}", file=sys.stderr)
        return 1

    engine = ScrapeEngine(
        client,
        zip_code=args.zip_code,
        max_concurrency=args.concurrency,
        allow_direct=args.allow_direct,
    )

    loop = asyncio.get_running_loop()
    stopping = False

    def _handle_signal(*_: Any) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        loop.create_task(engine.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:  # Windows 无 add_signal_handler
            signal.signal(sig, lambda *_: _handle_signal())

    await engine.start()
    return 0


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
