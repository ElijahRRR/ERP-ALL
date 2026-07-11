"""ERP /worker/v1 客户端协议测试（httpx.MockTransport，无真实网络）。

覆盖：注册发令牌 + 本地持久化 + 重启复用；领任务/回传/归还/心跳的请求形态与认证头；
服务器错误 → None 语义；stale 回执透传。
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from erp_worker.erp_client import ErpWorkerClient

BASE = "http://erp.local/api/v1"


def _mock(routes: dict[tuple[str, str], Any]) -> httpx.MockTransport:
    """routes: {(method, path): (status, json_body) | callable(request)->(status, body)}。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        key = (request.method, request.url.path)
        spec = routes.get(key)
        if spec is None:
            return httpx.Response(404, json={"error": "no route"})
        if callable(spec):
            status, body = spec(request)
        else:
            status, body = spec
        return httpx.Response(status, json=body)

    transport = httpx.MockTransport(handler)
    transport.seen = seen  # type: ignore[attr-defined]
    return transport


async def test_register_saves_and_reuses_token(tmp_path: Path) -> None:
    transport = _mock(
        {("POST", "/api/v1/worker/v1/register"): (201, {"node_id": 7, "node_token": "secret-xyz"})}
    )
    c = ErpWorkerClient(BASE, "erp-node-a", state_dir=tmp_path, transport=transport)
    await c.ensure_registered("enroll-123")
    assert c._node_token == "secret-xyz"
    # 令牌落盘
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text())["node_token"] == "secret-xyz"
    # 第二个实例（模拟重启）复用本地令牌，不再触发注册请求
    transport2 = _mock({})  # 无 register 路由 → 若尝试注册会 404
    c2 = ErpWorkerClient(BASE, "erp-node-a", state_dir=tmp_path, transport=transport2)
    await c2.ensure_registered("enroll-123")
    assert c2._node_token == "secret-xyz"
    assert transport2.seen == []  # type: ignore[attr-defined]


async def test_register_failure_raises(tmp_path: Path) -> None:
    transport = _mock(
        {
            ("POST", "/api/v1/worker/v1/register"): (
                400,
                {"error": {"code": "SCRAPE_ENROLL_TOKEN_INVALID"}},
            )
        }
    )
    c = ErpWorkerClient(BASE, "erp-node-b", state_dir=tmp_path, transport=transport)
    with pytest.raises(RuntimeError, match="注册失败"):
        await c.ensure_registered("wrong-token")


async def test_pull_sends_auth_headers_and_parses_tasks(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def pull(request: httpx.Request) -> tuple[int, dict[str, Any]]:
        captured["key"] = request.headers.get("X-Node-Key")
        captured["token"] = request.headers.get("X-Node-Token")
        captured["count"] = request.url.params.get("count")
        return 200, {
            "tasks": [{"task_id": 1, "target_ref": "B0AAA", "attempt": 1, "max_attempts": 3}]
        }

    transport = _mock({("GET", "/api/v1/worker/v1/tasks/pull"): pull})
    c = ErpWorkerClient(BASE, "erp-node-c", state_dir=tmp_path, transport=transport)
    c._node_token = "tok-c"
    tasks = await c.pull_tasks(10)
    assert tasks is not None and tasks[0]["target_ref"] == "B0AAA"
    assert captured["key"] == "erp-node-c"
    assert captured["token"] == "tok-c"
    assert captured["count"] == "10"


async def test_pull_server_error_returns_none(tmp_path: Path) -> None:
    transport = _mock({("GET", "/api/v1/worker/v1/tasks/pull"): (500, {"error": "boom"})})
    c = ErpWorkerClient(BASE, "n", state_dir=tmp_path, transport=transport)
    c._node_token = "t"
    assert await c.pull_tasks(5) is None  # 服务器错误 ≠ 空任务


async def test_submit_success_payload_shape(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def result(request: httpx.Request) -> tuple[int, dict[str, Any]]:
        captured["body"] = json.loads(request.content)
        return 200, {"accepted": True, "stale": False, "product": {"master_sku": "M0000001"}}

    transport = _mock({("POST", "/api/v1/worker/v1/tasks/result"): result})
    c = ErpWorkerClient(BASE, "n", state_dir=tmp_path, transport=transport)
    c._node_token = "t"
    res = await c.submit_result(
        task_id=42, attempt=2, success=True, payload={"title": "X", "images": []}
    )
    assert res is not None and res["product"]["master_sku"] == "M0000001"
    body = captured["body"]
    assert body == {
        "task_id": 42,
        "attempt": 2,
        "success": True,
        "payload": {"title": "X", "images": []},
    }


async def test_submit_failure_omits_payload_includes_error(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def result(request: httpx.Request) -> tuple[int, dict[str, Any]]:
        captured["body"] = json.loads(request.content)
        return 200, {"accepted": True, "stale": False}

    transport = _mock({("POST", "/api/v1/worker/v1/tasks/result"): result})
    c = ErpWorkerClient(BASE, "n", state_dir=tmp_path, transport=transport)
    c._node_token = "t"
    await c.submit_result(
        task_id=9, attempt=1, success=False, error_type="blocked", error_detail="HTTP 503"
    )
    body = captured["body"]
    assert body["success"] is False
    assert "payload" not in body
    assert body["error_type"] == "blocked"
    assert body["error_detail"] == "HTTP 503"


async def test_stale_result_passthrough(tmp_path: Path) -> None:
    transport = _mock(
        {("POST", "/api/v1/worker/v1/tasks/result"): (200, {"accepted": False, "stale": True})}
    )
    c = ErpWorkerClient(BASE, "n", state_dir=tmp_path, transport=transport)
    c._node_token = "t"
    res = await c.submit_result(task_id=1, attempt=1, success=True, payload={"title": "x"})
    assert res == {"accepted": False, "stale": True}


async def test_release_returns_count(tmp_path: Path) -> None:
    transport = _mock({("POST", "/api/v1/worker/v1/tasks/release"): (200, {"released": 3})})
    c = ErpWorkerClient(BASE, "n", state_dir=tmp_path, transport=transport)
    c._node_token = "t"
    n = await c.release_tasks([{"task_id": 1, "attempt": 1}, {"task_id": 2, "attempt": 1}])
    assert n == 3


async def test_release_empty_short_circuits(tmp_path: Path) -> None:
    transport = _mock({})
    c = ErpWorkerClient(BASE, "n", state_dir=tmp_path, transport=transport)
    c._node_token = "t"
    assert await c.release_tasks([]) == 0
    assert transport.seen == []  # type: ignore[attr-defined]


async def test_sync_returns_settings_and_draining(tmp_path: Path) -> None:
    transport = _mock(
        {
            ("POST", "/api/v1/worker/v1/sync"): (
                200,
                {"settings": {"max_concurrency": 8}, "draining": True},
            )
        }
    )
    c = ErpWorkerClient(BASE, "n", state_dir=tmp_path, transport=transport)
    c._node_token = "t"
    res = await c.sync(metrics={"total": 1}, window_state={"concurrency": 4})
    assert res == {"settings": {"max_concurrency": 8}, "draining": True}


async def test_headers_require_registration(tmp_path: Path) -> None:
    c = ErpWorkerClient(BASE, "n", state_dir=tmp_path, transport=_mock({}))
    with pytest.raises(RuntimeError, match="未认证"):
        c._headers()
