"""测试进程的全局环境（RS-02a，2026-07-27）。

`Settings` 自 RS-02a 起会拒绝已知默认/弱密钥（见 `erp/core/settings.py` 头注）。
测试库与 CI 的 postgres/redis 都是一次性的、跑完就丢，用的正是那些弱口令，故在此
**显式声明放行**——这一行就是那份声明本身，不是绕过。

放在 `tests/conftest.py`（而非 `tests/db/`）：非 DB 测试也会构造 Settings。
`setdefault` 而非直接赋值：外部若已显式设了值（例如专门验证门禁真的会拦的用例），
以外部为准。

门禁本身由 `tests/test_settings_guard.py` 覆盖——那些用例直接构造 `Settings`
并把本开关关掉，避免出现「全局放行 → 门禁从此无人验证」的死角。
"""

import os

os.environ.setdefault("ERP_ALLOW_INSECURE_DEFAULTS", "1")
