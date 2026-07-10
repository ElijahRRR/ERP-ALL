"""初始化超管账号（部署引导，幂等）。

用法（在 api 容器内）：
    ERP_BOOTSTRAP_PASSWORD='<强密码>' python -m erp.bootstrap <用户名>

- 密码经环境变量传入（不进命令行历史）；
- 已存在任意超管则拒绝（防重复引导）；
- 走 migrator 连接（表属主，不受 RLS 影响）。
"""

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from erp.core.security import hash_password
from erp.core.settings import get_settings

MIN_PASSWORD_LEN = 12


async def main() -> int:
    username = (sys.argv[1] if len(sys.argv) > 1 else "admin").lower()
    password = os.environ.get("ERP_BOOTSTRAP_PASSWORD", "")
    if len(password) < MIN_PASSWORD_LEN:
        print(f"✗ 请以环境变量 ERP_BOOTSTRAP_PASSWORD 提供 ≥{MIN_PASSWORD_LEN} 位密码")
        return 1

    engine = create_async_engine(get_settings().migrator_database_url)
    async with engine.begin() as conn:
        existing = (
            await conn.execute(text("SELECT username FROM app.app_user WHERE is_super"))
        ).first()
        if existing is not None:
            print(f"✗ 已存在超管（{existing.username}），拒绝重复引导")
            return 2
        await conn.execute(
            text(
                "INSERT INTO app.app_user"
                " (team_id, username, password_hash, display_name, is_super)"
                " VALUES (NULL, :u, :p, '超级管理员', true)"
            ),
            {"u": username, "p": hash_password(password)},
        )
    await engine.dispose()
    print(f"✓ 超管 {username} 已创建，请立即登录验证")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
