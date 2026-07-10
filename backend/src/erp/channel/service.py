"""channel 域服务：凭证加密存取 / 配额原子扣减 / 代理独占换绑。

- 凭证：pgcrypto 对称加密（密钥来自 Settings.credential_key，永不入库）；
  解密函数只允许渠道网关调用（R1-07），API 层永不回显。
- 配额：下发点单语句原子扣减（00-conventions §quota 协议），拿不到额度即拒绝。
"""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.errors import BusinessError
from erp.core.settings import get_settings

_MASK_VISIBLE = 4


async def set_credential(
    session: AsyncSession, store_id: int, client_id: str, client_secret: str, updated_by: int
) -> None:
    await session.execute(
        text(
            "INSERT INTO app.store_credential (store_id, client_id, client_secret_encrypted,"
            " updated_by)"
            " VALUES (:sid, :cid, pgp_sym_encrypt(:sec, :key), :by)"
            " ON CONFLICT (store_id) DO UPDATE SET client_id = excluded.client_id,"
            " client_secret_encrypted = excluded.client_secret_encrypted,"
            " updated_by = excluded.updated_by"
        ),
        {
            "sid": store_id,
            "cid": client_id,
            "sec": client_secret,
            "key": get_settings().credential_key,
            "by": updated_by,
        },
    )


async def credential_status(session: AsyncSession, store_id: int) -> dict[str, object]:
    row = (
        await session.execute(
            text("SELECT client_id, updated_at FROM app.store_credential WHERE store_id = :sid"),
            {"sid": store_id},
        )
    ).first()
    if row is None:
        return {"configured": False, "client_id_masked": None, "updated_at": None}
    cid: str = row.client_id
    masked = cid[:_MASK_VISIBLE] + "****" if len(cid) > _MASK_VISIBLE else "****"
    return {"configured": True, "client_id_masked": masked, "updated_at": row.updated_at}


async def decrypt_credential(session: AsyncSession, store_id: int) -> tuple[str, str]:
    """→ (client_id, client_secret)。仅渠道网关（R1-07）调用；调用方必须记 audit。"""
    row = (
        await session.execute(
            text(
                "SELECT client_id,"
                " pgp_sym_decrypt(client_secret_encrypted, :key) AS secret"
                " FROM app.store_credential WHERE store_id = :sid"
            ),
            {"sid": store_id, "key": get_settings().credential_key},
        )
    ).first()
    if row is None:
        raise BusinessError("CREDENTIAL_MISSING", "店铺未配置渠道凭证")
    return row.client_id, row.secret


async def bind_proxy(session: AsyncSession, store_id: int, proxy_id: int) -> None:
    """换绑出口代理；独占约束由 uq_store_proxy 兜底，这里转译为业务错误。"""
    occupied = (
        await session.execute(
            text(
                "SELECT code FROM app.store WHERE proxy_id = :pid"
                " AND id <> :sid AND status <> 'closed'"
            ),
            {"pid": proxy_id, "sid": store_id},
        )
    ).first()
    if occupied is not None:
        raise BusinessError("PROXY_OCCUPIED", f"该代理已被店铺 {occupied.code} 独占（防关联铁律）")
    updated = (
        await session.execute(
            text("UPDATE app.store SET proxy_id = :pid WHERE id = :sid RETURNING id"),
            {"pid": proxy_id, "sid": store_id},
        )
    ).first()
    if updated is None:
        raise BusinessError("STORE_NOT_FOUND", "店铺不存在")


async def consume_quota(
    session: AsyncSession, store_id: int, quota_kind: str, *, amount: int = 1
) -> bool:
    """任务下发点原子扣减；True=拿到额度。未配置该向配额 = 不设限（返回 True）。"""
    cfg = (
        await session.execute(
            text(
                "SELECT daily_limit, enabled FROM app.quota_config"
                " WHERE store_id = :sid AND quota_kind = :k"
            ),
            {"sid": store_id, "k": quota_kind},
        )
    ).first()
    if cfg is None or not cfg.enabled:
        return True
    row = (
        await session.execute(
            text(
                "INSERT INTO app.quota_usage (store_id, quota_kind, window_date, used)"
                " VALUES (:sid, :k, :d, :a)"
                " ON CONFLICT (store_id, quota_kind, window_date) DO UPDATE"
                " SET used = app.quota_usage.used + :a, updated_at = now()"
                " WHERE app.quota_usage.used + :a <= :limit"
                " RETURNING used"
            ),
            {
                "sid": store_id,
                "k": quota_kind,
                "d": _business_date(),
                "a": amount,
                "limit": cfg.daily_limit,
            },
        )
    ).first()
    # 首次插入超限的边界：INSERT 分支不走 WHERE，需二次校验
    if row is not None and row.used > cfg.daily_limit:
        await session.execute(
            text(
                "UPDATE app.quota_usage SET used = used - :a"
                " WHERE store_id = :sid AND quota_kind = :k AND window_date = :d"
            ),
            {"a": amount, "sid": store_id, "k": quota_kind, "d": _business_date()},
        )
        return False
    return row is not None


async def release_quota(
    session: AsyncSession, store_id: int, quota_kind: str, *, amount: int = 1
) -> None:
    """失败返还（create/delete 返还、maintenance 不返还——调用方守规则）。"""
    await session.execute(
        text(
            "UPDATE app.quota_usage SET used = greatest(used - :a, 0)"
            " WHERE store_id = :sid AND quota_kind = :k AND window_date = :d"
        ),
        {"a": amount, "sid": store_id, "k": quota_kind, "d": _business_date()},
    )


def _business_date() -> date:
    """业务日按 Asia/Shanghai 口径（00-conventions §quota_usage）。"""
    return (datetime.now(UTC) + timedelta(hours=8)).date()
