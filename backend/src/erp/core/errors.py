"""业务异常（统一错误信封的来源之一；处理器在 main.create_app）。"""

from typing import Any


class BusinessError(Exception):
    """业务规则拒绝（HTTP 422）。code 使用大写蛇形错误码，进错误信封。"""

    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None):
        self.code = code
        self.message = message
        self.detail = detail or {}
        super().__init__(message)
