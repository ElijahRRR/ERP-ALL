"""凭证加密密钥轮换（RS-02a / D-Q68，2026-07-27）。

`ERP_CREDENTIAL_KEY` 是 pgcrypto 的对称密钥，两张表的密文靠它打开：

| 表 | 列 | 内容 |
|---|---|---|
| `app.store_credential` | `client_secret_encrypted` | 各店铺 Walmart `client_secret` |
| `app.proxy` | `password_encrypted` | 出口代理口令（可空） |

**换密钥不能只改环境变量**——改完旧密文就再也解不开，全部店铺的渠道调用与代理拨号
一起失效，而且没有「解错了」的信号，只会表现为渠道 401/连不上。本工具在**一个事务**里
用旧密钥解、新密钥加，并在提交前逐行回读比对摘要，比对不上就整体回滚。

    # 推荐：旧密钥走环境变量，不进命令行历史
    ERP_CREDENTIAL_KEY_OLD='<旧密钥>' python -m erp.tools.rotate_credential_key --dry-run
    ERP_CREDENTIAL_KEY_OLD='<旧密钥>' python -m erp.tools.rotate_credential_key

    # 轮换后的验收：不给旧密钥的 dry-run ＝「用当前密钥自查」，报出能解开多少行
    python -m erp.tools.rotate_credential_key --dry-run

新密钥默认取 `Settings.credential_key`（即已经填进 `infra/.env` 的那个），
故本工具**必须在新 env 生效的环境里跑**；`--new` 仅供特殊场景显式覆盖。

前后顺序（详见 `.agent/evidence/RS-02a/deploy-rotate-secrets.md`）：先改库角色口令，
再跑本工具，最后 `make up`。**在 `make up` 之前跑完**——否则 api/beat 会带着新密钥去
解旧密文，窗口期内所有渠道调用失败。

不打印任何密钥或明文：过程只输出行数与「摘要是否一致」。
"""

import argparse
import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.db import get_session_factory, system_tx
from erp.core.settings import get_settings

_OLD_KEY_ENV = "ERP_CREDENTIAL_KEY_OLD"

# (表, 主键列, 密文列)——密文列可空的表靠 WHERE ... IS NOT NULL 跳过
_TARGETS = (
    ("app.store_credential", "store_id", "client_secret_encrypted"),
    ("app.proxy", "id", "password_encrypted"),
)


async def _digests(s: AsyncSession, table: str, pk: str, col: str, key: str) -> dict[int, str]:
    """→ {主键: md5(明文)}。解不开会抛 psycopg 错误——那正是「密钥不对」的硬信号。"""
    rows = (
        await s.execute(
            text(
                f"SELECT {pk} AS pk, md5(pgp_sym_decrypt({col}, :key)) AS d"
                f" FROM {table} WHERE {col} IS NOT NULL ORDER BY {pk}"
            ),
            {"key": key},
        )
    ).all()
    return {int(r.pk): str(r.d) for r in rows}


async def rotate(*, old_key: str, new_key: str, dry_run: bool) -> dict[str, int]:
    """整体重加密；任一表比对不上即抛错（事务随之回滚，一行都不会落）。"""
    sessions = get_session_factory()
    stats: dict[str, int] = {}
    async with system_tx(sessions) as s:
        for table, pk, col in _TARGETS:
            before = await _digests(s, table, pk, col, old_key)
            stats[table] = len(before)
            if dry_run or not before:
                continue
            await s.execute(
                text(
                    f"UPDATE {table} SET {col} ="
                    f" pgp_sym_encrypt(pgp_sym_decrypt({col}, :old), :new)"
                    f" WHERE {col} IS NOT NULL"
                ),
                {"old": old_key, "new": new_key},
            )
            after = await _digests(s, table, pk, col, new_key)
            if after != before:
                # 抛出即回滚：宁可这次没换成，也不能留下一半解不开的密文
                raise RuntimeError(
                    f"{table} 重加密后回读比对不一致（{len(before)} 行 → {len(after)} 行匹配）"
                    "，已回滚，库未改动"
                )
    # dry-run 路径一条 UPDATE 都没发，事务提交等同空提交——不必显式回滚
    return stats


def main() -> int:
    p = argparse.ArgumentParser(
        description="凭证加密密钥轮换（旧密钥解 → 新密钥加，单事务 + 回读比对）"
    )
    p.add_argument("--old", help=f"旧密钥；不给则读环境变量 {_OLD_KEY_ENV}")
    p.add_argument("--new", help="新密钥；不给则取 Settings.credential_key（即当前 env 的值）")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只试解并报行数，不写库。不给 --old 时用当前密钥自查——"
        "轮换后拿它验收：所有密文都还打得开",
    )
    args = p.parse_args()

    current = get_settings().credential_key
    # dry-run 不给旧密钥＝「用当前密钥自查」，这是轮换后的验收姿势；
    # 真轮换必须显式给旧密钥，不许猜。
    old_key = args.old or os.environ.get(_OLD_KEY_ENV) or (current if args.dry_run else None)
    if not old_key:
        print(f"缺旧密钥：给 --old 或设环境变量 {_OLD_KEY_ENV}", file=sys.stderr)
        return 2
    new_key = args.new or current
    if old_key == new_key and not args.dry_run:
        print("新旧密钥相同，无需轮换（若这是意外，检查 ERP_CREDENTIAL_KEY 是否真的换了）")
        return 2

    try:
        stats = asyncio.run(rotate(old_key=old_key, new_key=new_key, dry_run=args.dry_run))
    except Exception as exc:  # 明确报「没换成、库没动」，避免误以为换了一半
        print(f"轮换失败，已回滚，库未改动：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    verb = "可解开" if args.dry_run else "已重加密"
    for table, count in stats.items():
        print(f"{table}: {count} 行{verb}")
    if args.dry_run:
        print("dry-run：未写库。去掉 --dry-run 执行真正轮换。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
