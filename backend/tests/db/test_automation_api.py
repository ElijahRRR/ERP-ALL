"""档位面板 API 验收（R2-09 增量2）。

判据的重心在**三态不塌缩**与**零行团队有行可点**两件事上：
- 三态（unset / disabled / configured）在 API 层显性区分（Owner Q3），T1/T4 机器钉死；
- 零行团队 GET 恰好 10 条且可直接 PUT（PR#35 教训），T1 钉死；
- `effective_mode` 与内核 `resolve_mode` 同源，含「闸类存非法档位原样生效」，T5 钉死；
- 0040 必须回填**既有团队的角色副本**（模板授权不自动传播），T11 直查库钉死。
"""

from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.core.security import hash_password

from .conftest import APP_URL
from .test_identity_api import PASSWORD, _login
from .test_permission_reachability import _emitted_sql, _load_migration

TEAM_A = "档位面板测试团队A"
TEAM_B = "档位面板测试团队B"
ADMIN_A = "automation_admin_a"  # 团队管理员副本 → read + write
READER_A = "automation_reader_a"  # 自建角色，只授 automation.read
NONE_A = "automation_none_a"  # 订单员副本 → 无 automation.*
ADMIN_B = "automation_admin_b"
READER_ROLE = "档位只读"
MIGRATION_0040 = "0040_automation_policy_permissions.py"

FLOW_COUNT = 10


def _copy_template_role(conn: Any, team_id: int, role: str) -> None:
    """从全局模板复制角色 + 其权限映射（照 identity/router.py 建团队时的一次性快照）。"""
    conn.execute(
        """
        INSERT INTO app.role (team_id, name, description)
        SELECT %s, name, description FROM app.role tpl
        WHERE tpl.team_id IS NULL AND tpl.name = %s
          AND NOT EXISTS (SELECT 1 FROM app.role r WHERE r.team_id = %s AND r.name = %s)
        """,
        (team_id, role, team_id, role),
    )
    conn.execute(
        """
        INSERT INTO app.role_permission (role_id, permission_code)
        SELECT r.id, rp.permission_code FROM app.role r
        JOIN app.role tpl ON tpl.team_id IS NULL AND tpl.name = r.name
        JOIN app.role_permission rp ON rp.role_id = tpl.id
        WHERE r.team_id = %s AND r.name = %s
        ON CONFLICT DO NOTHING
        """,
        (team_id, role),
    )


def _make_user(conn: Any, team_id: int, username: str, role: str) -> int:
    conn.execute("DELETE FROM app.app_user WHERE username = %s", (username,))
    uid = int(
        conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (team_id, username, hash_password(PASSWORD), username),
        ).fetchone()[0]
    )
    conn.execute(
        """
        INSERT INTO app.user_role (user_id, role_id)
        SELECT %s, r.id FROM app.role r WHERE r.team_id = %s AND r.name = %s
        ON CONFLICT DO NOTHING
        """,
        (uid, team_id, role),
    )
    return uid


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    """两团队 + 四用户。团队管理员副本从模板复制（0040 跑过后自带 automation.*）。"""
    out: dict[str, int] = {}
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        for key, name in (("team_a", TEAM_A), ("team_b", TEAM_B)):
            conn.execute(
                "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (name,)
            )
            out[key] = int(
                conn.execute("SELECT id FROM app.team WHERE name = %s", (name,)).fetchone()[0]
            )
        for team in (out["team_a"], out["team_b"]):
            conn.execute("DELETE FROM app.automation_policy WHERE team_id = %s", (team,))
        for role in ("团队管理员", "订单员"):
            _copy_template_role(conn, out["team_a"], role)
        _copy_template_role(conn, out["team_b"], "团队管理员")

        # 只读角色：团队本地自建，只挂 automation.read（用于「有 read 无 write」那条判据）
        conn.execute(
            """
            INSERT INTO app.role (team_id, name, description)
            SELECT %s, %s, '仅 automation.read（测试夹具）'
            WHERE NOT EXISTS (SELECT 1 FROM app.role r WHERE r.team_id = %s AND r.name = %s)
            """,
            (out["team_a"], READER_ROLE, out["team_a"], READER_ROLE),
        )
        conn.execute(
            """
            INSERT INTO app.role_permission (role_id, permission_code)
            SELECT r.id, 'automation.read' FROM app.role r
            WHERE r.team_id = %s AND r.name = %s
            ON CONFLICT DO NOTHING
            """,
            (out["team_a"], READER_ROLE),
        )

        out[ADMIN_A] = _make_user(conn, out["team_a"], ADMIN_A, "团队管理员")
        out[READER_A] = _make_user(conn, out["team_a"], READER_A, READER_ROLE)
        out[NONE_A] = _make_user(conn, out["team_a"], NONE_A, "订单员")
        out[ADMIN_B] = _make_user(conn, out["team_b"], ADMIN_B, "团队管理员")
    return out


@pytest.fixture(scope="module")
def client(seeded: dict[str, int]) -> TestClient:
    import os

    os.environ["ERP_DATABASE_URL"] = APP_URL
    from erp.core.db import get_session_factory as gsf
    from erp.core.settings import get_settings as gs

    gs.cache_clear()
    gsf.cache_clear()
    from erp.main import create_app

    return TestClient(create_app())


def _clear(dsn: str, team: int) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM app.automation_policy WHERE team_id = %s", (team,))


def _set_policy(dsn: str, team: int, flow: str, mode: str, *, enabled: bool = True) -> None:
    """直连库造数（含库层合法但 flow 层非法的取值）——照 test_automation_resolve.py:30-38。"""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.automation_policy (team_id, flow_code, mode, enabled)"
            " VALUES (%s,%s,%s,%s) ON CONFLICT (team_id, flow_code) DO UPDATE"
            " SET mode = excluded.mode, enabled = excluded.enabled",
            (team, flow, mode, enabled),
        )


def _get(client: TestClient, h: dict[str, str]) -> dict[str, Any]:
    r = client.get("/api/v1/automation-policies", headers=h)
    assert r.status_code == 200, r.text
    return {row["flow_code"]: row for row in r.json()}


def _codes(conn: Any, team_id: int) -> set[str]:
    return {
        str(c)
        for (c,) in conn.execute(
            "SELECT rp.permission_code FROM app.role_permission rp"
            " JOIN app.role r ON r.id = rp.role_id"
            " WHERE r.team_id = %s AND r.name = '团队管理员'"
            "   AND rp.permission_code LIKE 'automation.%%'",
            (team_id,),
        ).fetchall()
    }


def test_zero_row_team_gets_all_ten_and_can_put(
    client: TestClient, seeded: dict[str, int], migrated_db: str
) -> None:
    """T1：零行团队 → 恰好 10 条、全 unset、legal_modes 正确，且能直接 PUT。"""
    _clear(migrated_db, seeded["team_a"])
    h = _login(client, ADMIN_A, PASSWORD)
    rows = _get(client, h)
    assert len(rows) == FLOW_COUNT
    assert {r["state"] for r in rows.values()} == {"unset"}
    assert all(r["mode"] is None and r["enabled"] is None for r in rows.values())
    assert all(r["effective_mode"] == "manual" for r in rows.values())
    assert all(r["illegal_for_flow"] is False for r in rows.values())
    assert all(r["config"] is None and r["updated_at"] is None for r in rows.values())
    # 闸类只有两档，且顺序是 manual 在前（保守档不在中间）
    assert rows["order_block"]["legal_modes"] == ["manual", "auto"]
    assert rows["compliance_block"]["legal_modes"] == ["manual", "auto"]
    assert rows["pricing_watch"]["legal_modes"] == ["manual", "semi", "auto"]
    # gate/wired 由服务端派生（前端不许硬编码闸类清单——审查 F4）
    assert {f for f, r in rows.items() if r["gate"]} == {"order_block", "compliance_block"}
    # wired 是代码事实：当前只有 order_block/refund/cancel 真有消费点（审查 F1）。
    # 增量3-5 接线时此断言随 WIRED_FLOWS 同步扩——它红了就说明接线 PR 忘了更新注册表。
    assert {f for f, r in rows.items() if r["wired"]} == {"order_block", "refund", "cancel"}
    assert rows["refund"]["evaluation"] == "snapshot"
    assert rows["pricing_watch"]["evaluation"] == "realtime"
    # 0 行也有行可设（PR#35 教训的机器判据）
    r = client.put(
        "/api/v1/automation-policies/pricing_watch",
        json={"mode": "semi", "enabled": True},
        headers=h,
    )
    assert r.status_code == 200, r.text


def test_semi_on_gate_flow_is_422_with_legal_list(
    client: TestClient, seeded: dict[str, int]
) -> None:
    """T2：闸类 flow 置 semi → 422，报文列出合法档位。"""
    h = _login(client, ADMIN_A, PASSWORD)
    r = client.put("/api/v1/automation-policies/order_block", json={"mode": "semi"}, headers=h)
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "AUTOMATION_MODE_ILLEGAL"
    assert "manual" in err["message"] and "auto" in err["message"]
    assert err["detail"]["legal_modes"] == ["manual", "auto"]
    # 空串与任意坏串走**同一个码、同一个统一信封**——不落 FastAPI 默认 {"detail":[...]}
    # （审查 F2：初版的 min/max_length 恰好把要规避的那个信封请了回来）
    for bad in ("", "x" * 30, "bogus"):
        r = client.put("/api/v1/automation-policies/pricing_watch", json={"mode": bad}, headers=h)
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "AUTOMATION_MODE_ILLEGAL", r.text


def test_put_legal_then_get_reflects_configured(
    client: TestClient, seeded: dict[str, int], migrated_db: str
) -> None:
    """T3：PUT 合法值 → GET 反映 configured + mode/effective_mode 正确。"""
    _clear(migrated_db, seeded["team_a"])
    h = _login(client, ADMIN_A, PASSWORD)
    for flow, mode in (("order_block", "auto"), ("pricing_watch", "semi")):
        r = client.put(f"/api/v1/automation-policies/{flow}", json={"mode": mode}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "configured"
    rows = _get(client, h)
    for flow, mode in (("order_block", "auto"), ("pricing_watch", "semi")):
        assert rows[flow]["state"] == "configured"
        assert rows[flow]["mode"] == mode
        assert rows[flow]["effective_mode"] == mode
        assert rows[flow]["enabled"] is True
        assert rows[flow]["illegal_for_flow"] is False
        assert rows[flow]["updated_by"] == seeded[ADMIN_A]
        assert rows[flow]["updated_at"] is not None
    # 未被 PUT 的 flow 仍是 unset——PUT 一条不会顺手给别的 flow 建行
    assert rows["cancel"]["state"] == "unset"


def test_disabled_row_is_state_disabled_not_manual(
    client: TestClient, seeded: dict[str, int], migrated_db: str
) -> None:
    """T4：**三态不塌缩的机器判据**。enabled=false 的行不许渲染成「配了 manual」。"""
    _set_policy(migrated_db, seeded["team_a"], "pricing_watch", "auto", enabled=False)
    row = _get(client, _login(client, ADMIN_A, PASSWORD))["pricing_watch"]
    assert row["state"] == "disabled"  # 不是 unset、也不是 configured
    assert row["mode"] == "auto"  # 存的还是 auto
    assert row["enabled"] is False
    assert row["effective_mode"] == "manual"  # 但实际按 manual 跑


def test_illegal_stored_mode_on_gate_flow_is_surfaced_not_rewritten(
    client: TestClient, seeded: dict[str, int], migrated_db: str
) -> None:
    """T5：闸类存 semi（库层合法、flow 层非法）→ illegal_for_flow=true 且原样生效。

    与 `test_automation_resolve.py::test_illegal_mode_for_gate_flow_is_not_rewritten`
    同一件事的 API 侧投影——面板显示的必须与订单闸实际执行的一致。
    """
    _set_policy(migrated_db, seeded["team_a"], "order_block", "semi", enabled=True)
    row = _get(client, _login(client, ADMIN_A, PASSWORD))["order_block"]
    assert row["illegal_for_flow"] is True
    assert row["effective_mode"] == "semi"  # **不是** manual：归 manual 是放松
    assert row["state"] == "configured"


def test_permission_gates(client: TestClient, seeded: dict[str, int]) -> None:
    """T6：无 automation.read → GET 403；有 read 无 write → PUT 403。"""
    none_h = _login(client, NONE_A, PASSWORD)
    assert client.get("/api/v1/automation-policies", headers=none_h).status_code == 403
    reader_h = _login(client, READER_A, PASSWORD)
    assert client.get("/api/v1/automation-policies", headers=reader_h).status_code == 200
    r = client.put(
        "/api/v1/automation-policies/pricing_watch", json={"mode": "manual"}, headers=reader_h
    )
    assert r.status_code == 403
    assert r.json()["error"]["detail"]["permission"] == "automation.write"


def test_audit_records_before_and_after(
    client: TestClient, seeded: dict[str, int], migrated_db: str
) -> None:
    """T7：审计行落库，before/after 如实（首次写 before=null，再写 before=前值）。

    **必须带水位线**。初版查询无任何「本次运行」的界定、直接取尾部两行，在持久库上
    只要 audit_log 尾部恰好已是匹配的一对（把本测试连跑两次即可），「审计写入被整个
    删掉」它都判绿——变异证伪 2026-07-28 M4 实测坐实了这个出口。CI 每轮起新库所以
    没被骗过，但**守住这条线的是那个一次性数据库，不是判据本身**。
    水位线 + 恰好 2 行，两个口子一起堵。
    """
    _clear(migrated_db, seeded["team_a"])
    with psycopg.connect(migrated_db) as conn:
        watermark = conn.execute("SELECT coalesce(max(id), 0) FROM app.audit_log").fetchone()[0]
    h = _login(client, ADMIN_A, PASSWORD)
    client.put("/api/v1/automation-policies/cancel", json={"mode": "semi"}, headers=h)
    client.put(
        "/api/v1/automation-policies/cancel", json={"mode": "auto", "enabled": False}, headers=h
    )
    with psycopg.connect(migrated_db) as conn:
        rows = conn.execute(
            "SELECT before, after FROM app.audit_log"
            " WHERE action = 'automation.policy_set' AND team_id = %s AND id > %s"
            " ORDER BY id",
            (seeded["team_a"], watermark),
        ).fetchall()
    assert len(rows) == 2, f"水位线之后应恰好 2 条审计行，实得 {len(rows)}"
    first, second = rows
    assert first[0] is None
    assert first[1] == {"flow_code": "cancel", "mode": "semi", "enabled": True}
    assert second[0] == {"flow_code": "cancel", "mode": "semi", "enabled": True}
    assert second[1] == {"flow_code": "cancel", "mode": "auto", "enabled": False}


def test_cross_team_isolation(client: TestClient, seeded: dict[str, int], migrated_db: str) -> None:
    """T8：A 团队 PUT 不影响 B 团队 GET（多租户底线）。"""
    _clear(migrated_db, seeded["team_b"])
    r = client.put(
        "/api/v1/automation-policies/listing_dispatch",
        json={"mode": "auto"},
        headers=_login(client, ADMIN_A, PASSWORD),
    )
    assert r.status_code == 200, r.text
    row_b = _get(client, _login(client, ADMIN_B, PASSWORD))["listing_dispatch"]
    assert row_b["state"] == "unset" and row_b["effective_mode"] == "manual"


def test_unknown_flow_code_is_404(client: TestClient, seeded: dict[str, int]) -> None:
    """T9：flow_code 不在枚举 → 404 AUTOMATION_FLOW_UNKNOWN。"""
    r = client.put(
        "/api/v1/automation-policies/no_such_flow",
        json={"mode": "manual"},
        headers=_login(client, ADMIN_A, PASSWORD),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "AUTOMATION_FLOW_UNKNOWN"


def test_config_in_body_is_rejected_not_silently_dropped(
    client: TestClient, seeded: dict[str, int], migrated_db: str
) -> None:
    """T10：PUT 带 config → 422，且库里连行都不建。

    选「拒绝」而不是「忽略」：静默丢弃恰恰会造成「护栏已配」的错觉——而
    `automation_policy.config` 全仓零消费点，那正是本增量不开放它的原因。

    **必须断言统一信封码**，不只是状态码：初版用 `extra="forbid"` 时这里返回的是
    FastAPI 默认的 `{"detail": [...]}`（无错误码、绕过 002 统一信封），而 T10 只看
    422 抓不到——审查 2026-07-28 指出后改为显式 `AUTOMATION_CONFIG_NOT_WRITABLE`。
    """
    _clear(migrated_db, seeded["team_a"])
    h = _login(client, ADMIN_A, PASSWORD)
    r = client.put(
        "/api/v1/automation-policies/purchase_execute",
        json={"mode": "auto", "config": {"amount_ceiling": 100}},
        headers=h,
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "AUTOMATION_CONFIG_NOT_WRITABLE"
    with psycopg.connect(migrated_db) as conn:
        n = conn.execute(
            "SELECT count(*) FROM app.automation_policy"
            " WHERE team_id = %s AND flow_code = 'purchase_execute'",
            (seeded["team_a"],),
        ).fetchone()[0]
    assert n == 0  # 连行都不该建


def test_disable_row_with_illegal_stored_mode_succeeds(
    client: TestClient, seeded: dict[str, int], migrated_db: str
) -> None:
    """T12：停用一条已存非法档位的行必须走得通（审查 R5）。

    场景：order_block 上遗留 semi（库层合法、flow 层非法，增量2 之前只能手写 SQL 造出）。
    运维在面板上关「启用」开关，前端会把库中 mode 原样回传。初版 PUT 无条件先验
    flow 级合法性 → 恒 422，停用这条路在唯一需要它的状态下是死路。
    现规则：enabled=false 时只验「三档之一」；重新启用时 flow 级校验照旧拦住。
    """
    _set_policy(migrated_db, seeded["team_a"], "order_block", "semi", enabled=True)
    h = _login(client, ADMIN_A, PASSWORD)
    r = client.put(
        "/api/v1/automation-policies/order_block",
        json={"mode": "semi", "enabled": False},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "disabled"
    assert body["effective_mode"] == "manual"  # 停用即收紧
    assert body["illegal_for_flow"] is True  # 坏值仍如实标出，不洗白
    # 带着非法档位重新启用 → 仍被拦（洗不进启用态）
    r = client.put(
        "/api/v1/automation-policies/order_block",
        json={"mode": "semi", "enabled": True},
        headers=h,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "AUTOMATION_MODE_ILLEGAL"


def test_orphan_flow_code_row_does_not_break_get(
    client: TestClient, seeded: dict[str, int], migrated_db: str
) -> None:
    """T13：库中存在枚举外的 flow_code 行（改名/历史遗留）时 GET 不炸、仍恒 10 条。

    `flow_code` 列无 CHECK 无 FK（考古实锤），这种行造得出来。面板不渲染它（不是
    §09 注册的 flow），但也不该 500——它由 GET 侧 log.warning 留痕（审查 F9）。
    """
    _clear(migrated_db, seeded["team_a"])
    _set_policy(migrated_db, seeded["team_a"], "legacy_renamed_flow", "auto", enabled=True)
    try:
        rows = _get(client, _login(client, ADMIN_A, PASSWORD))
        assert len(rows) == FLOW_COUNT
        assert "legacy_renamed_flow" not in rows
    finally:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM app.automation_policy WHERE flow_code = 'legacy_renamed_flow'"
            )


def test_teamless_super_gets_422_not_fake_unset_screen(
    client: TestClient, seeded: dict[str, int], migrated_db: str
) -> None:
    """T14：超管未带 X-Act-Team → GET 422 AUTOMATION_TEAM_REQUIRED（与 PUT 同口径）。

    初版 GET 按 team_id=NULL 静默查 → 恒 0 行 → 返回一屏「全 unset + 拦截不生效」，
    **它描述的不是任何一个团队，而是「没有团队」**（审查 F3/P1-1，三镜头同报）。
    对全平台最高权限账号显示假话，比报错糟得多。
    """
    username = "automation_super_t14"
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM app.app_user WHERE username = %s", (username,))
        conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name, is_super)"
            " VALUES (NULL, %s, %s, %s, TRUE)",
            (username, hash_password(PASSWORD), username),
        )
    h = _login(client, username, PASSWORD)  # 不带 X-Act-Team
    r = client.get("/api/v1/automation-policies", headers=h)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "AUTOMATION_TEAM_REQUIRED"


def test_0040_backfills_existing_team_role_copies(migrated_db: str) -> None:
    """T11：0040 必须把两个新码回填到**既有团队**的团管副本，不只是模板角色。

    模板角色的授权**不会**自动传播到既有团队副本（`identity/router.py:121-141` 的
    复制是建团队时的一次性快照）。漏回填 = 迁移前已存在的团队永远看不到面板，
    且没有任何报错，只是菜单不出现。

    做法：造一个「0040 之前建的团队」（复制模板角色后把两个 automation 码摘掉），
    再跑 0040 `upgrade()` **真正发出的** SQL，查完整体 rollback、不改库
    （形态同 `test_permission_reachability.py::test_0039_downgrade_spares_predecessor_grants`）。
    """
    stmts = _emitted_sql(_load_migration(MIGRATION_0040), "upgrade")
    assert stmts, "upgrade() 一条 SQL 都没发——判据会永远绿"
    with psycopg.connect(migrated_db) as conn:
        try:
            tid = conn.execute(
                "INSERT INTO app.team (name) VALUES ('0040回填判据团队') RETURNING id"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO app.role (team_id, name, description)"
                " SELECT %s, name, description FROM app.role WHERE team_id IS NULL",
                (tid,),
            )
            conn.execute(
                "INSERT INTO app.role_permission (role_id, permission_code)"
                " SELECT nr.id, rp.permission_code FROM app.role nr"
                " JOIN app.role tpl ON tpl.team_id IS NULL AND tpl.name = nr.name"
                " JOIN app.role_permission rp ON rp.role_id = tpl.id"
                " WHERE nr.team_id = %s",
                (tid,),
            )
            # 摘掉 automation.*，模拟迁移之前建的团队
            conn.execute(
                "DELETE FROM app.role_permission rp USING app.role r"
                " WHERE rp.role_id = r.id AND r.team_id = %s"
                "   AND rp.permission_code LIKE 'automation.%%'",
                (tid,),
            )
            assert _codes(conn, tid) == set()
            for sql in stmts:
                conn.execute(sql)  # type: ignore[arg-type]
            assert _codes(conn, tid) == {"automation.read", "automation.write"}, (
                "0040 只授到了模板角色，既有团队的团管副本没拿到——现网既有团队看不到面板"
            )
        finally:
            conn.rollback()
