"""凭证密钥轮换工具的实测（RS-02a / D-Q68）。

这个工具动的是**全部店铺的 Walmart client_secret 与代理口令**——搞砸了不是报错，
是渠道调用集体 401 而库里再也解不回来。故不满足于「跑通不报错」，四条都要验到：

1. 轮换后新密钥解得开，且明文与轮换前**逐行一致**（不是只对行数）
2. 轮换后旧密钥**解不开**（真的换了，不是没动）
3. 旧密钥给错时整体回滚，库一行未改（比对错了宁可不换）
4. `--dry-run` 不写库

用例跑完把密钥转回去（`finally`）：本套 DB 测试共用一个库，留在新密钥上会让后面
所有解密的用例连坐。
"""

import psycopg
import pytest

from erp.core.settings import get_settings
from erp.tools.rotate_credential_key import rotate

_TMP_KEY = "rotate-test-key-4e6f7a8b9c0d1e2f"
_SECRET = "rotate-secret-plaintext-01"
_PROXY_PW = "rotate-proxy-password-01"


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    """一店一代理，均按当前 settings 密钥加密。"""
    key = get_settings().credential_key
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        team_id = conn.execute(
            "INSERT INTO app.team (name) VALUES ('轮换测试团队')"
            " ON CONFLICT (name) DO UPDATE SET name = excluded.name RETURNING id"
        ).fetchone()[0]
        channel_id = conn.execute(
            "SELECT id FROM app.channel WHERE code = 'walmart_us'"
        ).fetchone()[0]
        conn.execute(
            "DELETE FROM app.store_credential WHERE store_id IN"
            " (SELECT id FROM app.store WHERE code = 'ROTATE01')"
        )
        conn.execute("DELETE FROM app.store WHERE code = 'ROTATE01'")
        store_id = conn.execute(
            "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
            " VALUES (%s, %s, 'ROTATE01', '轮换测试店', true) RETURNING id",
            (team_id, channel_id),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO app.store_credential (store_id, client_id, client_secret_encrypted)"
            " VALUES (%s, 'rotate-client-01', pgp_sym_encrypt(%s, %s))",
            (store_id, _SECRET, key),
        )
        conn.execute("DELETE FROM app.proxy WHERE host = '203.0.113.9' AND port = 1080")
        proxy_id = conn.execute(
            "INSERT INTO app.proxy (team_id, kind, host, port, username, password_encrypted)"
            " VALUES (%s, 'socks5', '203.0.113.9', 1080, 'rotate-u',"
            " pgp_sym_encrypt(%s, %s)) RETURNING id",
            (team_id, _PROXY_PW, key),
        ).fetchone()[0]
    return {"store_id": store_id, "proxy_id": proxy_id}


def _decrypt(dsn: str, sql: str, args: tuple[object, ...]) -> str | None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        try:
            row = conn.execute(sql, args).fetchone()
        except psycopg.errors.DatabaseError:  # 密钥不对 → pgcrypto 直接报错
            return None
    return None if row is None else str(row[0])


def _read_secret(dsn: str, store_id: int, key: str) -> str | None:
    return _decrypt(
        dsn,
        "SELECT pgp_sym_decrypt(client_secret_encrypted, %s)"
        " FROM app.store_credential WHERE store_id = %s",
        (key, store_id),
    )


def _read_proxy_pw(dsn: str, proxy_id: int, key: str) -> str | None:
    return _decrypt(
        dsn,
        "SELECT pgp_sym_decrypt(password_encrypted, %s) FROM app.proxy WHERE id = %s",
        (key, proxy_id),
    )


async def test_dry_run_does_not_write(migrated_db: str, seeded: dict[str, int]) -> None:
    key = get_settings().credential_key
    stats = await rotate(old_key=key, new_key=_TMP_KEY, dry_run=True)
    assert stats["app.store_credential"] >= 1
    assert _read_secret(migrated_db, seeded["store_id"], key) == _SECRET


async def test_wrong_old_key_rolls_back(migrated_db: str, seeded: dict[str, int]) -> None:
    key = get_settings().credential_key
    with pytest.raises(Exception):  # noqa: B017 —— pgcrypto 抛的具体类型不是判据
        await rotate(old_key="definitely-not-the-key", new_key=_TMP_KEY, dry_run=False)
    # 关键：失败之后库必须原样——**这条才是「宁可不换」的实证**
    assert _read_secret(migrated_db, seeded["store_id"], key) == _SECRET
    assert _read_proxy_pw(migrated_db, seeded["proxy_id"], key) == _PROXY_PW


async def test_round_trip_preserves_plaintext(migrated_db: str, seeded: dict[str, int]) -> None:
    key = get_settings().credential_key
    try:
        await rotate(old_key=key, new_key=_TMP_KEY, dry_run=False)
        # 新密钥解得开，明文一字不差
        assert _read_secret(migrated_db, seeded["store_id"], _TMP_KEY) == _SECRET
        assert _read_proxy_pw(migrated_db, seeded["proxy_id"], _TMP_KEY) == _PROXY_PW
        # 旧密钥解不开 —— 否则「换了」只是错觉
        assert _read_secret(migrated_db, seeded["store_id"], key) is None
    finally:
        # 共用库：无论上面成没成，都要把密钥转回去，别让后面的用例连坐
        await rotate(old_key=_TMP_KEY, new_key=key, dry_run=False)
    assert _read_secret(migrated_db, seeded["store_id"], key) == _SECRET
