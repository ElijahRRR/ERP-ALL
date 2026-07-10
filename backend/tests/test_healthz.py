from fastapi.testclient import TestClient

from erp import __version__
from erp.main import BusinessError, create_app


def test_healthz() -> None:
    client = TestClient(create_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": __version__}


def test_business_error_envelope() -> None:
    """错误信封格式是契约的一部分，回归锁死。"""
    app = create_app()

    @app.get("/_boom")
    async def boom() -> None:
        raise BusinessError("DEMO_REJECTED", "演示拒绝", {"k": "v"})

    client = TestClient(app)
    resp = client.get("/_boom")
    assert resp.status_code == 422
    body = resp.json()["error"]
    assert body["code"] == "DEMO_REJECTED"
    assert body["message"] == "演示拒绝"
    assert body["detail"] == {"k": "v"}
