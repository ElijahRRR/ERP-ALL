"""业务异常（统一错误信封的来源之一；处理器在 main.create_app）。"""

from typing import Any


class BusinessError(Exception):
    """业务规则拒绝（默认 HTTP 422）。code 使用大写蛇形错误码，进错误信封。

    http_status 仅在契约明确非 422 时覆写（如幂等键冲突 409，002 §写操作幂等）。
    """

    def __init__(
        self,
        code: str,
        message: str,
        detail: dict[str, Any] | None = None,
        *,
        http_status: int = 422,
    ):
        self.code = code
        self.message = message
        self.detail = detail or {}
        self.http_status = http_status
        super().__init__(message)
