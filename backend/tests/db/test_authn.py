"""R1-04 验收：权限矩阵 / 双 audience / GUC 注入 / 登录锁定 / 审计出口。"""

import asyncio
import os
from collections.abc import Iterator

import psycopg
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.security import create_token, hash_password

from .conftest import APP_URL

USER_A = "authn_user_a"
USER_B = "authn_user_b"
PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="module")
def seeded(migrated_db: str, team_ids: tuple[int, int]) -> dict[str, int]:
    """建两个团队的测试用户（各挂一个含 listing.read 的团队角色）+ 团队A一条代理。"""
    a, b = team_ids
    pw = hash_password(PASSWORD)
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        ids: dict[str, int] = {"team_a": a, "team_b": b}
        for uname, team in ((USER_A, a), (USER_B, b)):
            conn.execute("DELETE FROM app.app_user WHERE username = %s", (uname,))
            uid = conn.execute(
                "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
                " VALUES (%s, %s, %s, %s) RETURNING id",
                (team, uname, pw, uname),
            ).fetchone()[0]
            rid = conn.execute(
                "INSERT INTO app.role (team_id, name) VALUES (%s, %s)"
                " ON CONFLICT DO NOTHING RETURNING id",
                (team, f"authn测试角色{team}"),
            ).fetchone()
            role_id = (
                rid[0]
                if rid
                else conn.execute(
                    "SELECT id FROM app.role WHERE team_id = %s AND name = %s",
                    (team, f"authn测试角色{team}"),
                ).fetchone()[0]
            )
            conn.execute(
                "INSERT INTO app.role_permission (role_id, permission_code)"
                " VALUES (%s, 'listing.read') ON CONFLICT DO NOTHING",
                (role_id,),
            )
            conn.execute(
                "INSERT INTO app.user_role (user_id, role_id) VALUES (%s, %s)"
                " ON CONFLICT DO NOTHING",
                (uid, role_id),
            )
            ids[uname] = uid
        conn.execute(
            "INSERT INTO app.proxy (team_id, kind, host, port)"
            " VALUES (%s,'http','10.9.9.9',9999) ON CONFLICT DO NOTHING",
            (a,),
        )
    return ids


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> Iterator[TestClient]:
    os.environ["ERP_DATABASE_URL"] = APP_URL
    from erp.core.db import get_session, get_session_factory
    from erp.core.settings import get_settings

    get_settings.cache_clear()
    get_session_factory.cache_clear()

    from erp.core.authn import CurrentUser, get_current_user, require_permission
    from erp.main import create_app

    app = create_app()

    @app.get("/t/read")
    async def t_read(  # pyright: ignore
        user: CurrentUser = Depends(require_permission("listing.read")),
    ) -> dict[str, str]:
        return {"user": user.display_name}

    @app.get("/t/admin")
    async def t_admin(  # pyright: ignore
        user: CurrentUser = Depends(require_permission("identity.role_write")),
    ) -> dict[str, str]:
        return {"user": user.display_name}

    @app.get("/t/proxies")
    async def t_proxies(  # pyright: ignore
        session: AsyncSession = Depends(get_session),
        user: CurrentUser = Depends(get_current_user),
    ) -> dict[str, int]:
        n = (await session.execute(text("SELECT count(*) FROM app.proxy"))).scalar_one()
        return {"count": n}

    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()
    get_session_factory.cache_clear()


def _token(uid: int, team: int | None, *, super_: bool = False, **kw: object) -> str:
    args: dict[str, object] = {
        "subject": uid,
        "audience": "erp",
        "kind": "access",
        "token_version": 0,
        "team_id": team,
        "is_super": super_,
    }
    args.update(kw)
    return create_token(**args)  # type: ignore[arg-type]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestPermissionMatrix:
    def test_no_token_401(self, client: TestClient) -> None:
        resp = client.get("/t/read")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHENTICATED"

    def test_garbage_token_401(self, client: TestClient) -> None:
        assert client.get("/t/read", headers=_auth("not-a-jwt")).status_code == 401

    def test_portal_audience_rejected(self, client: TestClient, seeded: dict[str, int]) -> None:
        t = _token(seeded[USER_A], seeded["team_a"], audience="portal")
        assert client.get("/t/read", headers=_auth(t)).status_code == 401

    def test_refresh_kind_rejected(self, client: TestClient, seeded: dict[str, int]) -> None:
        t = _token(seeded[USER_A], seeded["team_a"], kind="refresh")
        assert client.get("/t/read", headers=_auth(t)).status_code == 401

    def test_token_version_revoked(self, client: TestClient, seeded: dict[str, int]) -> None:
        t = _token(seeded[USER_A], seeded["team_a"], token_version=99)
        assert client.get("/t/read", headers=_auth(t)).status_code == 401

    def test_with_permission_200(self, client: TestClient, seeded: dict[str, int]) -> None:
        t = _token(seeded[USER_A], seeded["team_a"])
        resp = client.get("/t/read", headers=_auth(t))
        assert resp.status_code == 200
        assert resp.json()["user"] == USER_A

    def test_without_permission_403(self, client: TestClient, seeded: dict[str, int]) -> None:
        t = _token(seeded[USER_A], seeded["team_a"])
        resp = client.get("/t/admin", headers=_auth(t))
        assert resp.status_code == 403
        assert resp.json()["error"]["detail"]["permission"] == "identity.role_write"

    def test_request_id_header(self, client: TestClient) -> None:
        assert len(client.get("/healthz").headers["X-Request-Id"]) == 32


class TestGucScoping:
    def test_team_a_sees_proxy_team_b_zero(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        ta = _token(seeded[USER_A], seeded["team_a"])
        tb = _token(seeded[USER_B], seeded["team_b"])
        assert client.get("/t/proxies", headers=_auth(ta)).json()["count"] >= 1
        assert client.get("/t/proxies", headers=_auth(tb)).json()["count"] == 0


class TestLoginService:
    def _sessions(self):  # type: ignore[no-untyped-def]
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        return async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)

    def test_login_lockout_flow(self, client: TestClient, seeded: dict[str, int]) -> None:
        from erp.core.authn import AuthError
        from erp.identity import service

        sessions = self._sessions()

        async def flow() -> None:
            # 正确登录 → 拿到 token 对
            pair = await service.login(sessions, USER_B, PASSWORD)
            assert pair.access_token and pair.refresh_token
            # refresh 换新
            pair2 = await service.refresh(sessions, pair.refresh_token)
            assert pair2.access_token
            # 连错 5 次 → 锁定
            for _ in range(5):
                with pytest.raises(AuthError):
                    await service.login(sessions, USER_B, "wrong-password")
            # 锁定期内，正确密码也拒绝
            with pytest.raises(AuthError, match="锁定"):
                await service.login(sessions, USER_B, PASSWORD)

        asyncio.run(flow())

    def test_unknown_user(self, client: TestClient) -> None:
        from erp.core.authn import AuthError
        from erp.identity import service

        sessions = self._sessions()
        with pytest.raises(AuthError):
            asyncio.run(service.login(sessions, "no_such_user", "x"))


class TestAuditOutlet:
    def test_audit_writer_row(
        self, client: TestClient, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        from erp.core.audit import AuditWriter
        from erp.core.authn import CurrentUser

        sessions = TestLoginService()._sessions()
        user = CurrentUser(
            id=seeded[USER_A],
            team_id=seeded["team_a"],
            is_super=False,
            display_name=USER_A,
            permissions=frozenset(),
        )

        async def write() -> None:
            async with sessions() as s, s.begin():
                await s.execute(
                    text("SELECT set_config('app.current_team', :t, true)"),
                    {"t": str(user.team_id)},
                )
                await AuditWriter.for_user(s, user).log("authn.test", "proxy", 1, after={"k": "v"})

        asyncio.run(write())
        with psycopg.connect(migrated_db) as conn:
            n = conn.execute(
                "SELECT count(*) FROM app.audit_log WHERE action = 'authn.test' AND actor_id = %s",
                (user.id,),
            ).fetchone()[0]
        assert n == 1
