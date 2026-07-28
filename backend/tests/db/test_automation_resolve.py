"""三档内核 `resolve_mode` 的真库判据（R2-09 增量1）。

内核的价值全在**三条边角**上（无行 / `enabled=false` / 档位对本 flow 非法），
这三条此前散在两处读点里各写各的、无人对账。判据逐条钉死，尤其钉住
**「非法档位只告警不改写」**——那是本增量「行为零变化」的承诺所在：
`order_block` 上若存着 `semi`，现行代码是拦截的，归 manual 会让该团队静默失去订单拦截。
"""

import psycopg
import pytest

from erp.core.automation import AutomationFlow, Mode, resolve_mode
from erp.core.db import get_session_factory, system_tx

_TEAM = "三档内核测试团队"


@pytest.fixture(scope="module")
def team_id(migrated_db: str) -> int:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        return int(
            conn.execute(
                "INSERT INTO app.team (name) VALUES (%s)"
                " ON CONFLICT (name) DO UPDATE SET name = excluded.name RETURNING id",
                (_TEAM,),
            ).fetchone()[0]
        )


def _set_policy(dsn: str, team: int, flow: str, mode: str, *, enabled: bool = True) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.automation_policy (team_id, flow_code, mode, enabled)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (team_id, flow_code) DO UPDATE"
            " SET mode = excluded.mode, enabled = excluded.enabled",
            (team, flow, mode, enabled),
        )


def _clear(dsn: str, team: int, flow: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "DELETE FROM app.automation_policy WHERE team_id = %s AND flow_code = %s",
            (team, flow),
        )


async def _resolve(team: int, flow: AutomationFlow) -> Mode:
    async with system_tx(get_session_factory()) as s:
        return await resolve_mode(s, team_id=team, flow=flow)


async def test_no_row_is_manual(migrated_db: str, team_id: int) -> None:
    """新团队 0 行 = 全人工。fail-closed，不会一上来就自动跑。"""
    _clear(migrated_db, team_id, AutomationFlow.PRICING_WATCH.value)
    assert await _resolve(team_id, AutomationFlow.PRICING_WATCH) is Mode.MANUAL


@pytest.mark.parametrize("stored", ["manual", "semi", "auto"])
async def test_enabled_row_is_returned_verbatim(
    migrated_db: str, team_id: int, stored: str
) -> None:
    _set_policy(migrated_db, team_id, AutomationFlow.PRICING_WATCH.value, stored)
    assert (await _resolve(team_id, AutomationFlow.PRICING_WATCH)).value == stored


async def test_disabled_row_is_manual(migrated_db: str, team_id: int) -> None:
    """`enabled=false` 等价于无行——与两处旧读点 `AND enabled` 的行为一致。

    注意这在 `order_block` 上是**放松**（manual = 只软标记不冻结），
    即「误停用 = 订单拦截静默失效」。本增量只补 warn 不改语义，
    是否要改归 Owner 裁定（批注回传 Q3）。
    """
    _set_policy(migrated_db, team_id, AutomationFlow.PRICING_WATCH.value, "auto", enabled=False)
    assert await _resolve(team_id, AutomationFlow.PRICING_WATCH) is Mode.MANUAL


async def test_illegal_mode_for_gate_flow_is_not_rewritten(migrated_db: str, team_id: int) -> None:
    """**行为零变化的核心判据**：`order_block` 存 `semi`（§09 上非法）时原样返回。

    DB 的 `ck_automation_mode` 只约束 `mode ∈ {manual,semi,auto}`、**不区分 flow**，
    所以这一行在库层完全合法、现网可能真的存在。内核若把它归 manual，
    `procurement._order_block_gate` 的 `mode in (SEMI, AUTO)` 就不再成立
    ——该团队的订单拦截会**静默消失**。故只告警、不改写。
    """
    _set_policy(migrated_db, team_id, AutomationFlow.ORDER_BLOCK.value, "semi")
    assert await _resolve(team_id, AutomationFlow.ORDER_BLOCK) is Mode.SEMI


async def test_gate_flow_legal_modes_still_work(migrated_db: str, team_id: int) -> None:
    _set_policy(migrated_db, team_id, AutomationFlow.ORDER_BLOCK.value, "auto")
    assert await _resolve(team_id, AutomationFlow.ORDER_BLOCK) is Mode.AUTO
    _set_policy(migrated_db, team_id, AutomationFlow.ORDER_BLOCK.value, "manual")
    assert await _resolve(team_id, AutomationFlow.ORDER_BLOCK) is Mode.MANUAL


async def test_policy_is_per_team(migrated_db: str, team_id: int) -> None:
    """档位按团队隔离——别的团队调档不影响本团队（多租户底线）。"""
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        other = int(
            conn.execute(
                "INSERT INTO app.team (name) VALUES ('三档内核测试团队B')"
                " ON CONFLICT (name) DO UPDATE SET name = excluded.name RETURNING id"
            ).fetchone()[0]
        )
    _set_policy(migrated_db, other, AutomationFlow.PRICING_WATCH.value, "auto")
    _clear(migrated_db, team_id, AutomationFlow.PRICING_WATCH.value)
    assert await _resolve(team_id, AutomationFlow.PRICING_WATCH) is Mode.MANUAL
    assert await _resolve(other, AutomationFlow.PRICING_WATCH) is Mode.AUTO
