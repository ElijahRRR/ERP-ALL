"""ERP `/worker/v1` 拨入协议客户端（移植目标端点见 scrape/router.py worker_router）。

替换 v3 的 `/api/*` server 通信层：注册 / 领任务 / 回传 / 归还 / 心跳。
认证：注册一次拿高熵机器令牌，之后每请求带 X-Node-Key + X-Node-Token。

令牌持久化：注册返回的 node_token 仅此一次可见，写入本地状态文件
（默认 ~/.erp_worker/<server_host>.json）。重启复用同一 node_key + token；
令牌文件遗失需管理员删除节点后重新注册（server 端 SCRAPE_NODE_EXISTS 硬闸）。
"""

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ErpWorkerClient:
    """ERP worker 协议异步客户端。所有方法抛 httpx 异常由调用方按拨入语义处理。"""

    def __init__(
        self,
        base_url: str,
        node_key: str,
        *,
        kind: str = "scraper_amazon",
        timeout: float = 15.0,
        state_dir: Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # base_url 期望形如 http://host:8000/api/v1（worker_router 挂在 /api/v1 下）
        self.base_url = base_url.rstrip("/")
        self.node_key = node_key
        self.kind = kind
        self._timeout = timeout
        self._node_token: str | None = None
        self._state_dir = state_dir or (Path.home() / ".erp_worker")
        self._transport = transport  # 测试注入 httpx.MockTransport；生产为 None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url, timeout=self._timeout, transport=self._transport
        )

    # ── 令牌状态持久化 ──

    def _state_path(self) -> Path:
        digest = hashlib.sha256(f"{self.base_url}|{self.node_key}".encode()).hexdigest()[:16]
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", self.node_key)
        return self._state_dir / f"{safe}-{digest}.json"

    def _load_token(self) -> str | None:
        path = self._state_path()
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text())
            token = data.get("node_token")
            return token if isinstance(token, str) and token else None
        except (OSError, ValueError):
            return None

    def _save_token(self, token: str) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"node_key": self.node_key, "node_token": token}))
        # 令牌文件权限收紧（尽力，Windows 上 chmod 语义有限）
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _headers(self) -> dict[str, str]:
        if not self._node_token:
            raise RuntimeError("未认证：先 ensure_registered()")
        return {"X-Node-Key": self.node_key, "X-Node-Token": self._node_token}

    # ── 注册 ──

    async def ensure_registered(self, enroll_token: str, *, version: str | None = None) -> None:
        """确保已有可用令牌：优先复用本地状态；无则用 enroll_token 注册。"""
        cached = self._load_token()
        if cached:
            self._node_token = cached
            logger.info("复用本地机器令牌（node_key=%s）", self.node_key)
            return
        async with self._client() as client:
            resp = await client.post(
                "/worker/v1/register",
                json={
                    "node_key": self.node_key,
                    "kind": self.kind,
                    "enroll_token": enroll_token,
                    "version": version,
                },
            )
        if resp.status_code != 201:  # noqa: PLR2004
            raise RuntimeError(f"注册失败 HTTP {resp.status_code}: {resp.text[:300]}")
        token = resp.json()["node_token"]
        self._node_token = token
        self._save_token(token)
        logger.info("注册成功，机器令牌已保存到 %s", self._state_path())

    # ── 任务协议 ──

    async def pull_tasks(self, count: int) -> list[dict[str, Any]] | None:
        """领任务。返回任务列表；服务器错误/网络异常返回 None（区别于"无任务"的空列表）。"""
        try:
            async with self._client() as client:
                resp = await client.get(
                    "/worker/v1/tasks/pull",
                    params={"count": count},
                    headers=self._headers(),
                )
            if resp.status_code == 200:  # noqa: PLR2004
                tasks = resp.json().get("tasks", [])
                return list(tasks)
            logger.warning("领任务失败 HTTP %s", resp.status_code)
            return None
        except httpx.HTTPError as e:
            logger.error("领任务异常: %s", e)
            return None

    async def submit_result(
        self,
        *,
        task_id: int,
        attempt: int,
        success: bool,
        payload: dict[str, Any] | None = None,
        error_type: str | None = None,
        error_detail: str | None = None,
    ) -> dict[str, Any] | None:
        """回传结果。返回 {accepted, stale, product}；失败返回 None（不重投，靠 server 回收）。"""
        body: dict[str, Any] = {"task_id": task_id, "attempt": attempt, "success": success}
        if payload is not None:
            body["payload"] = payload
        if error_type is not None:
            body["error_type"] = error_type
        if error_detail is not None:
            body["error_detail"] = error_detail
        try:
            async with self._client() as client:
                resp = await client.post(
                    "/worker/v1/tasks/result", json=body, headers=self._headers()
                )
            if resp.status_code == 200:  # noqa: PLR2004
                return dict(resp.json())
            logger.warning("回传失败 HTTP %s: %s", resp.status_code, resp.text[:200])
            return None
        except httpx.HTTPError as e:
            logger.error("回传异常 task=%s: %s", task_id, e)
            return None

    async def release_tasks(self, tasks: list[dict[str, int]]) -> int:
        """归还未处理任务（优雅下线）。tasks=[{task_id, attempt}]。"""
        if not tasks:
            return 0
        try:
            async with self._client() as client:
                resp = await client.post(
                    "/worker/v1/tasks/release", json={"tasks": tasks}, headers=self._headers()
                )
            if resp.status_code == 200:  # noqa: PLR2004
                return int(resp.json().get("released", 0))
            logger.warning("归还失败 HTTP %s", resp.status_code)
            return 0
        except httpx.HTTPError as e:
            logger.error("归还异常: %s", e)
            return 0

    async def sync(
        self,
        *,
        metrics: dict[str, Any],
        window_state: dict[str, Any],
        version: str | None = None,
    ) -> dict[str, Any] | None:
        """心跳 + 指标上报 + 设置下发。返回 {settings, draining}；异常返回 None。"""
        try:
            async with self._client() as client:
                resp = await client.post(
                    "/worker/v1/sync",
                    json={"version": version, "metrics": metrics, "window_state": window_state},
                    headers=self._headers(),
                )
            if resp.status_code == 200:  # noqa: PLR2004
                return dict(resp.json())
            logger.warning("心跳失败 HTTP %s", resp.status_code)
            return None
        except httpx.HTTPError as e:
            logger.error("心跳异常: %s", e)
            return None
