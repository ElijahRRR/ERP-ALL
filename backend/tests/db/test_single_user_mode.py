"""R2-17 17a 验收（D-Q73 单人模式）：免登录注入固定超管 + 休眠可逆 + fail-closed。

判据映射（review_list R2-17）：
- 验收①的后端半边：`SINGLE_USER_MODE` 开 → 无凭证请求注入固定超管（/me 200、
  权限点全放行——超管短路是既有一行，本开关只是把「无凭证」接到它前面）；
- 验收⑤：关掉开关 → 无凭证仍 401，登录流程原样（休眠可逆）；
- fail-closed 三条：指向不存在 / 停用 / 非超管用户 → 一律 401，不把降级身份
  静默当超管用；
- 回归：开关开着时带 token 的正常路径不受影响（token 身份优先，不被注入覆盖）。
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from erp.core.security import create_token, hash_password

from .conftest import APP_URL

SU_ADMIN = "sumode_admin"
SU_PLAIN = "sumode_plain"
SU_OFF = "sumode_disabled"
PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="module")
def seeded(migrated_db: str, team_ids: tuple[int, int]) -> dict[str, int]:
    """一枚超管（team NULL，同 bootstrap 形状）+ 一个普通用户 + 一个停用超管。"""
    a, _b = team_ids
    pw = hash_password(PASSWORD)
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        ids: dict[str, int] = {"team_a": a}
        rows = (
            (SU_ADMIN, True, None, "active"),
            (SU_PLAIN, False, a, "active"),
            (SU_OFF, True, None, "disabled"),
        )
        for uname, is_super, team, status in rows:
            conn.execute("DELETE FROM app.app_user WHERE username = %s", (uname,))
            ids[uname] = conn.execute(
                "INSERT INTO app.app_user"
                " (team_id, username, password_hash, display_name, is_super, status)"
                " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (team, uname, pw, uname, is_super, status),
            ).fetchone()[0]
    return ids


@contextmanager
def _client(*, mode: bool, admin: str = SU_ADMIN) -> Iterator[TestClient]:
    """按给定开关值构建应用。设置走环境变量 + cache_clear，用毕清缓存防串档。"""
    os.environ["ERP_DATABASE_URL"] = APP_URL
    if mode:
        os.environ["ERP_SINGLE_USER_MODE"] = "1"
    else:
        os.environ.pop("ERP_SINGLE_USER_MODE", None)
    os.environ["ERP_SINGLE_USER_ADMIN"] = admin

    from erp.core.db import get_session_factory
    from erp.core.settings import get_settings

    get_settings.cache_clear()
    get_session_factory.cache_clear()

    from erp.core.authn import CurrentUser, require_permission
    from erp.main import create_app

    app = create_app()

    @app.get("/t/gated")
    async def t_gated(  # pyright: ignore[reportUnusedFunction]
        user: CurrentUser = Depends(require_permission("listing.read")),
    ) -> dict[str, str]:
        return {"user": user.display_name}

    try:
        with TestClient(app) as c:
            yield c
    finally:
        os.environ.pop("ERP_SINGLE_USER_MODE", None)
        os.environ.pop("ERP_SINGLE_USER_ADMIN", None)
        get_settings.cache_clear()
        get_session_factory.cache_clear()


class TestSingleUserMode:
    def test_on_injects_fixed_super_and_passes_gates(self, seeded: dict[str, int]) -> None:
        with _client(mode=True) as c:
            me = c.get("/api/v1/me")
            assert me.status_code == 200, me.text
            body = me.json()
            assert body["user"]["username"] == SU_ADMIN
            assert body["user"]["is_super"] is True
            assert body["user"]["id"] == seeded[SU_ADMIN]
            # 权限点闸门被超管短路放行——注入身份走到 require_permission 为止全通
            gated = c.get("/t/gated")
            assert gated.status_code == 200, gated.text
            assert gated.json()["user"] == SU_ADMIN

    def test_off_keeps_login_flow(self, seeded: dict[str, int]) -> None:
        """验收⑤：休眠可逆——开关不在，无凭证一律 401，登录流程原样。"""
        with _client(mode=False) as c:
            assert c.get("/api/v1/me").status_code == 401
            assert c.get("/t/gated").status_code == 401

    @pytest.mark.parametrize(
        "admin_name",
        [
            pytest.param(SU_PLAIN, id="non-super"),
            pytest.param(SU_OFF, id="disabled"),
            pytest.param("sumode_ghost", id="missing"),
        ],
    )
    def test_fail_closed_on_bad_target(self, seeded: dict[str, int], admin_name: str) -> None:
        """配置指向非超管/停用/不存在用户 → 401，绝不注入降级身份。"""
        with _client(mode=True, admin=admin_name) as c:
            assert c.get("/api/v1/me").status_code == 401

    def test_token_path_unaffected_when_on(self, seeded: dict[str, int]) -> None:
        """回归：开关开着时带 token 请求仍按 token 解身份（不被注入覆盖）。"""
        with _client(mode=True) as c:
            token = create_token(
                subject=seeded[SU_PLAIN],
                audience="erp",
                kind="access",
                token_version=0,
                team_id=seeded["team_a"],
                is_super=False,
            )  # type: ignore[arg-type]
            me = c.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
            assert me.status_code == 200, me.text
            assert me.json()["user"]["username"] == SU_PLAIN
