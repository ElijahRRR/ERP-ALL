"""迁移链单 head 不变量（PR #49 审查 N3）。

三线并行各持预分配号（0043-0046 / 0047），down_revision 都只能指当时的 head——
第二条线合入 main 即出现双 head，`alembic upgrade head` 直接报
`Multiple head revisions`、服务起不来。本测试让后合者在 CI 上当场看见，
而不是在部署机上看见。修法：把后合者的 down_revision 一行 rebase 到新 head。

放在 tests/ 根而非 tests/db/：纯脚本目录断言不需要 PG（审查 N10 口径——
纯单元断言放 db/ 会在无 PG 时随整目录静默 skip）。
"""

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migration_chain_has_single_head() -> None:
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert len(heads) == 1, (
        f"迁移链出现多个 head：{sorted(heads)}——并行线先后合入后，"
        "后合者须把 down_revision rebase 到新 head（一行改动）"
    )
