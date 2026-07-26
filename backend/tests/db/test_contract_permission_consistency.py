"""RS-11 门禁：契约四向一致性（OpenAPI operation ↔ 实际路由 ↔ x-permission ↔ permission seed）。

RS-11 的 acceptance 原文头一句即本文判据。四个面：

| 面 | 来源 |
|---|---|
| 契约 operation | `specs/002-api-contract/openapi-v0.yaml` 的 `paths.*.{get,post,patch,...}` |
| `x-permission` | 上述 operation 的扩展字段 |
| 实际路由 | `erp.main.create_app()`，**须递归 `_IncludedRouter.original_router.routes`** |
| 路由权限码 | 依赖函数上的 `erp_permission` 属性（`core/authn.py` 挂的） |
| permission seed | 迁移建的 `app.permission` 表 |

**为什么要这条门禁**：`require_permission` 的 docstring 早就写着「权限码与 002 契约
x-permission **一字不差**」，但此前**从无强制手段**——2026-07-27 首次全量探测
（`.agent/evidence/RS-11/four-way-probe-20260727.md`）证实这句话当时确实成立，
本门禁把它钉住，防将来静默漂移。

**为什么不用 `__closure__` 反查权限码**：那依赖闭包变量顺序，`require_permission` 改个形参
就静默失效。见 `core/authn.py` 该处注释。
"""

import json
import re
from pathlib import Path
from typing import Any

import psycopg
import pytest

from erp.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT = REPO_ROOT / "specs/002-api-contract/openapi-v0.yaml"
LEDGER = REPO_ROOT / ".agent/review_list.json"
API_PREFIX = "/api/v1"
HTTP_METHODS = ("get", "post", "put", "patch", "delete")

# ── C 类白名单：契约已声明、代码未建 ──
#
# 契约超前于**未开工工单**是正常的（前置声明），不是漂移，故不判红。但每条**必须写明归属工单**，
# 并由 `test_contract_ahead_entries_have_open_owner` 施加反向不变量：**工单一旦 accepted/done，
# 其白名单条目必须清空**——否则「前置声明豁免」会退化成永久豁免（同 SUPER_ONLY 防僵尸的道理）。
CONTRACT_AHEAD_OF_CODE: dict[tuple[str, str], str] = {
    # D-Q50 采购方门户对外（双入口的外侧）——门户 router 全仓未挂载
    ("POST", f"{API_PREFIX}/portal/auth/login"): "R2-10",
    ("GET", f"{API_PREFIX}/portal/procurement-orders"): "R2-10",
    ("GET", f"{API_PREFIX}/portal/procurement-orders/{{}}"): "R2-10",
    ("POST", f"{API_PREFIX}/portal/procurement-orders/{{}}/claim"): "R2-10",
    ("POST", f"{API_PREFIX}/portal/procurement-orders/{{}}/backfill"): "R2-10",
    ("POST", f"{API_PREFIX}/portal/procurement-orders/{{}}/exception"): "R2-10",
    # 类目映射、货源录入、错误字典端点未建（catalog 5 + listing 2 = 7 条）。
    # 这 7 条首版被我分别挂在 R2-02 / R2-03 / R2-05 名下，下面那条反向不变量当场判红——
    # 三单已 accepted/done，已收账的工单不能给未建端点当归属。复核 `check` 后确认不是误关账，
    # 是归属判断错了（三单验收口径从无这些端点），故另立 **CT-0727** 认领无主欠账。
    # 对应权限码 catalog.category_write / catalog.source_write / listing.error_admin /
    # catalog.product_write 已由 0039 提前授给团队管理员——端点建成即可用（RS-11 报告 §3）。
    ("GET", f"{API_PREFIX}/category-map"): "CT-0727",
    ("PATCH", f"{API_PREFIX}/category-map/{{}}"): "CT-0727",
    ("GET", f"{API_PREFIX}/products/{{}}/sources"): "CT-0727",
    ("POST", f"{API_PREFIX}/products/{{}}/sources"): "CT-0727",
    ("PATCH", f"{API_PREFIX}/products/{{}}"): "CT-0727",
    ("GET", f"{API_PREFIX}/listing-errors"): "CT-0727",
    ("PATCH", f"{API_PREFIX}/listing-errors/{{}}"): "CT-0727",
    # 维护任务端点未建，属 R2-12（在做）本单范围。
    ("GET", f"{API_PREFIX}/maintenance-tasks"): "R2-12",
    ("POST", f"{API_PREFIX}/maintenance-tasks"): "R2-12",
}

# ── D 类白名单：代码已建、契约未登记 ──
#
# 这些是**真欠账**（不是前置声明），2026-07-27 探测时的既有状态，先冻结防继续扩大。
# **新增路由必须同时登记契约**，否则 `test_no_new_undocumented_routes` 红。
# 清偿属 RS-11 后续增量：补登记进 002 契约后从本表删除。
CODE_AHEAD_OF_CONTRACT: dict[tuple[str, str], str] = {
    # 通知中心四端点——前端通知铃/通知中心页在用，契约里没有
    ("GET", f"{API_PREFIX}/notifications"): "RS-11 欠账：通知中心，前端在用",
    ("GET", f"{API_PREFIX}/notifications/unread-count"): "RS-11 欠账：通知中心",
    ("POST", f"{API_PREFIX}/notifications/read-all"): "RS-11 欠账：通知中心",
    ("POST", f"{API_PREFIX}/notifications/{{}}/read"): "RS-11 欠账：通知中心",
    # 采集 worker 五端点。注意路径含双版本号 /api/v1/worker/v1/——worker_router 自带
    # prefix="/worker/v1"（scrape/router.py:26）又被挂在 /api/v1 下所致。改路径是破坏性的
    # （采集 worker 在跑），故不动；补登记契约时须**如实写这个路径**，别写成 /worker/v1/...
    ("POST", f"{API_PREFIX}/worker/v1/register"): "RS-11 欠账：采集 worker（双版本号路径）",
    ("POST", f"{API_PREFIX}/worker/v1/sync"): "RS-11 欠账：采集 worker",
    ("GET", f"{API_PREFIX}/worker/v1/tasks/pull"): "RS-11 欠账：采集 worker",
    ("POST", f"{API_PREFIX}/worker/v1/tasks/release"): "RS-11 欠账：采集 worker",
    ("POST", f"{API_PREFIX}/worker/v1/tasks/result"): "RS-11 欠账：采集 worker",
}

CLOSED_TICKET_STATUS = {"done", "accepted", "accepted-l2-ship-deferred"}


def _norm(path: str) -> str:
    """路径参数占位符归一。

    契约用 camelCase（`{teamId}`）而代码用 snake_case（`{team_id}`）——功能等价但字面不同，
    不归一会造成满屏假阳性。命名风格不一致本身是另一处小漂移，已记入探测报告 §4。
    """
    return re.sub(r"\{[^}]+\}", "{}", path)


@pytest.fixture(scope="module")
def contract_ops() -> dict[tuple[str, str], str | None]:
    """(METHOD, 归一化路径) → x-permission（无则 None）。"""
    import yaml

    spec = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], str | None] = {}
    for path, ops in (spec.get("paths") or {}).items():
        for method, op in (ops or {}).items():
            if method.lower() in HTTP_METHODS:
                key = (method.upper(), _norm(API_PREFIX + path))
                out[key] = (op or {}).get("x-permission")
    assert out, "契约里没解析出任何 operation——解析逻辑或文件路径有问题"
    return out


@pytest.fixture(scope="module")
def route_perms() -> dict[tuple[str, str], str | None]:
    """(METHOD, 归一化路径) → 路由声明的权限码（无则 None）。仅取 /api/v1 下的。"""

    def walk(routes: list[Any], prefix: str = "") -> Any:
        for r in routes:
            if type(r).__name__ == "_IncludedRouter":
                # FastAPI 把 include_router 的结果包成该内部类；直接遍历 app.routes
                # 只能拿到 5 条，其余全是包装对象，须下钻并叠加挂载前缀。
                ctx = getattr(r, "include_context", None)
                sub = getattr(ctx, "prefix", "") if ctx is not None else ""
                yield from walk(r.original_router.routes, prefix + sub)
            elif hasattr(r, "dependant") and hasattr(r, "methods"):
                yield prefix + r.path, r

    app = create_app()
    out: dict[tuple[str, str], str | None] = {}
    for path, route in walk(app.routes):
        perm = next(
            (
                getattr(d.call, "erp_permission", None)
                for d in route.dependant.dependencies
                if getattr(d.call, "erp_permission", None)
            ),
            None,
        )
        for method in route.methods - {"HEAD", "OPTIONS"}:
            key = (method, _norm(path))
            if key[1].startswith(API_PREFIX):
                out[key] = perm
    assert len(out) > 50, f"只递归到 {len(out)} 条路由——_IncludedRouter 下钻逻辑可能失效"
    return out


@pytest.fixture(scope="module")
def seeded_codes(migrated_db: str) -> set[str]:
    with psycopg.connect(migrated_db.replace("postgresql+psycopg://", "postgresql://")) as conn:
        return {str(r[0]) for r in conn.execute("SELECT code FROM app.permission").fetchall()}


# ── A/B/E：硬断言（2026-07-27 探测时三项全清 0 例外，任何新增违例即真漏洞）──


def test_a_route_permissions_all_seeded(
    route_perms: dict[tuple[str, str], str | None], seeded_codes: set[str]
) -> None:
    """代码里 require_permission 的每个码，必须在 permission 种子里存在。

    不存在＝该端点的权限校验永远失败（非超管一律 403），且没有任何角色能被授予它。
    """
    missing = sorted({p for p in route_perms.values() if p} - seeded_codes)
    assert not missing, (
        "以下权限码在路由里被 require_permission 引用，但 permission 表里没有——"
        "非超管访问该端点必然 403 且无法授权：\n  " + "\n  ".join(missing)
    )


def test_b_contract_permissions_all_seeded(
    contract_ops: dict[tuple[str, str], str | None], seeded_codes: set[str]
) -> None:
    """契约里每个 x-permission，必须在 permission 种子里存在。"""
    missing = sorted({p for p in contract_ops.values() if p} - seeded_codes)
    assert not missing, (
        "以下 x-permission 出现在 002 契约里，但 permission 表没有该码：\n  " + "\n  ".join(missing)
    )


def test_e_contract_and_route_permissions_identical(
    contract_ops: dict[tuple[str, str], str | None], route_perms: dict[tuple[str, str], str | None]
) -> None:
    """两边都存在的 operation，权限码必须一字不差。

    这正是 `require_permission` docstring 那句承诺的机器化判据。
    """
    diffs = [
        f"{m} {p}: 契约={contract_ops[(m, p)]!r} 路由={route_perms[(m, p)]!r}"
        for (m, p) in sorted(contract_ops.keys() & route_perms.keys())
        if contract_ops[(m, p)] != route_perms[(m, p)]
    ]
    assert not diffs, "契约 x-permission 与路由 require_permission 不一致：\n  " + "\n  ".join(
        diffs
    )


# ── C/D：带白名单的硬断言 ──


def test_c_no_new_contract_ahead_of_code(
    contract_ops: dict[tuple[str, str], str | None], route_perms: dict[tuple[str, str], str | None]
) -> None:
    """契约声明而代码未建的 operation，必须在白名单里且写明归属工单。"""
    undeclared = sorted(set(contract_ops) - set(route_perms) - set(CONTRACT_AHEAD_OF_CODE))
    assert not undeclared, (
        "以下 operation 在 002 契约里声明但代码里没有对应路由，且未列入 "
        "CONTRACT_AHEAD_OF_CODE——若属未开工工单的前置声明，请补进白名单并写明归属工单；"
        "若属误写，请从契约删除：\n  " + "\n  ".join(f"{m} {p}" for m, p in undeclared)
    )


def test_d_no_new_undocumented_routes(
    contract_ops: dict[tuple[str, str], str | None], route_perms: dict[tuple[str, str], str | None]
) -> None:
    """代码里有而契约未登记的路由，必须在欠账白名单里。

    **新增路由必须同时登记 002 契约**——这是本门禁的主要防线。
    """
    undeclared = sorted(set(route_perms) - set(contract_ops) - set(CODE_AHEAD_OF_CONTRACT))
    assert not undeclared, (
        "以下路由存在于代码但 002 契约未登记（新增端点必须同步登记契约）：\n  "
        + "\n  ".join(f"{m} {p}" for m, p in undeclared)
    )


# ── 白名单自身的不变量（防豁免变永久）──


def test_whitelists_have_no_stale_entries(
    contract_ops: dict[tuple[str, str], str | None], route_perms: dict[tuple[str, str], str | None]
) -> None:
    """白名单不许养僵尸：情况已消失的条目必须删掉，否则白名单越攒越松。"""
    stale_c = sorted(k for k in CONTRACT_AHEAD_OF_CODE if k in route_perms)
    stale_d = sorted(k for k in CODE_AHEAD_OF_CONTRACT if k in contract_ops)
    problems = [f"CONTRACT_AHEAD_OF_CODE 里 {m} {p} 的路由已建成，应移除" for m, p in stale_c] + [
        f"CODE_AHEAD_OF_CONTRACT 里 {m} {p} 已登记进契约，应移除" for m, p in stale_d
    ]
    assert not problems, "白名单含已失效条目：\n  " + "\n  ".join(problems)


def test_contract_ahead_entries_have_open_owner() -> None:
    """**反向不变量**：C 类白名单每条的归属工单必须仍未收账。

    工单一旦 accepted/done，其端点理应已建成——白名单条目还在，说明要么工单被误判关账、
    要么契约声明是废的。不加这条，「前置声明豁免」会退化成永久豁免（同 SUPER_ONLY 防僵尸）。
    """
    entries: list[dict[str, Any]] = json.loads(LEDGER.read_text(encoding="utf-8"))
    status = {str(e["id"]): str(e.get("status")) for e in entries}
    problems: list[str] = []
    for (method, path), ticket in sorted(CONTRACT_AHEAD_OF_CODE.items()):
        if ticket not in status:
            problems.append(f"{method} {path}: 归属工单 {ticket} 在 review_list 里不存在")
        elif status[ticket] in CLOSED_TICKET_STATUS:
            problems.append(
                f"{method} {path}: 归属工单 {ticket} 已 {status[ticket]}，"
                "但该 operation 仍无对应路由——工单误关账，或契约声明是废的"
            )
    assert not problems, "C 类白名单的归属工单状态不合规：\n  " + "\n  ".join(problems)
