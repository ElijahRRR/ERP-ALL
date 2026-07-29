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
    # 对应权限码 catalog.category_write / catalog.source_write / listing.error_admin
    # 已由 0039 提前授给团队管理员——端点建成即可用（RS-11 报告 §3）。
    # 〔2026-07-27 更正（审查 AI 的 F7）：此处原把 `catalog.product_write` 也算进这一串，
    #  **两处都错**——它不在 0039 的 8 码内（`_GRANTS` 里只在注释中被提及），且 0002:306
    #  把它授给的是**审核员**不是团队管理员，此后无任何迁移补授过。按错记去实现
    #  `PATCH /products/{productId}` 的人会以为团管开箱可用，上线即 403。
    #  该错记不会被任何门禁抓到：`product_write` 已被审核员持有故可达性判绿，
    #  `_EXPECTED_0039` 矩阵根本没列这个码。团管是否需要它已挂进 CT-0727 的 acceptance 待裁。〕
    ("GET", f"{API_PREFIX}/category-map"): "CT-0727",
    ("PATCH", f"{API_PREFIX}/category-map/{{}}"): "CT-0727",
    ("GET", f"{API_PREFIX}/products/{{}}/sources"): "CT-0727",
    ("POST", f"{API_PREFIX}/products/{{}}/sources"): "CT-0727",
    ("PATCH", f"{API_PREFIX}/products/{{}}"): "CT-0727",
    ("GET", f"{API_PREFIX}/listing-errors"): "CT-0727",
    ("PATCH", f"{API_PREFIX}/listing-errors/{{}}"): "CT-0727",
    # 维护任务端点（`GET/POST /maintenance-tasks`，x-permission: listing.read / listing.write）
    # 未建。原挂 R2-12 名下，2026-07-29 审计侧整单收账（`9e57e0f`）后 R2-12 转 accepted，
    # 下面那条反向不变量当场判红——**与上面那 7 条同形，处置也同**：复核 R2-12 的 acceptance
    # 四条（①USPTO 三日连测 ②TRO→L2 命中复现 ③全店对账 ④合规页四项）**从无这两个端点**，
    # 故不是误关账，是归属判断错了，改挂 CT-0727（无主欠账收容单，todo）。
    # 需要留意的是它与上面 7 条来历不同：那 7 条是一开始就挂错了单，这 2 条是**挂对了单、
    # 但那张单在端点没建成的情况下按另一套判据收了账**——功能域确属 R2-12（4b 的
    # end_date_renewal 执行通道就在里面，runner 已建、API 面没建），只是验收口径没要求它。
    # 〔若审计侧判定 R2-12 其实**不该**收账（即这两个端点本就属其交付范围），请把此处改回
    #  "R2-12" 并重开该单——本次改挂只是按既有先例解 main 的红，不代替验收判定。〕
    ("GET", f"{API_PREFIX}/maintenance-tasks"): "CT-0727",
    ("POST", f"{API_PREFIX}/maintenance-tasks"): "CT-0727",
}

# ── D 类白名单：代码已建、契约未登记 ──
#
# 这些是**真欠账**（不是前置声明），2026-07-27 探测时的既有状态，先冻结防继续扩大。
# **新增路由必须同时登记契约**，否则 `test_no_new_undocumented_routes` 红。
# 清偿属 RS-11 后续增量：补登记进 002 契约后从本表删除。
CODE_AHEAD_OF_CONTRACT: dict[tuple[str, str], str] = {
    # 2026-07-27 全部清偿完毕：通知中心四端点 + 采集 worker 五端点已补登记进 002 契约。
    # 空表＝任何新增未登记路由都会被 `test_d_no_new_undocumented_routes` 当场判红。
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


# ── F：tag 一致性（2026-07-27 补）──
#
# 起因是 R2-08 考古坐实的两笔欠账：契约顶层 `tags` 只声明 7 个而 paths 实际用了 11 个；
# 代码里 `aftersale`/`order` 用小写而契约用 `Aftersale`/`Order`。两处都已修，加这组判据防复发。
#
# **一条更正记在这里**：当时我判「改 tag 会动 codegen 产物命名」，据此把大小写统一列为
# 「有影响面、需先查前端」。**错的**——本项目 codegen 是 `openapi-typescript`，产出的是
# 按 path 键控的 `schema.d.ts`，**根本不输出 tag**（`Aftersale`/`Catalog`/`Compliance`
# 在产物里零命中）。改完重跑 `pnpm gen:api`，产物**逐字节相同**。tag 只影响 Swagger UI
# 的分组展示。原判断把一个安全改动说成了有风险的改动。
CODE_ONLY_TAGS = {
    # ① 设计上就不进契约，**不是欠账**：探活端点在 /api/v1 之外，不属业务 API 面。
    #    （本条是这组判据首跑抓出来的——D 类那条判据只看 /api/v1，`/healthz` 从没露过面。）
    "ops": "设计如此：`main.py:101` 的 `/healthz` 探活端点，在 /api/v1 之外，不属业务契约面",
    # ② 曾有 Notify / ScrapeWorker 两条真欠账，2026-07-27 补登记进契约后已摘牌
    #    （摘牌是门禁强制的：`stale` 那条断言会在 tag 进了契约后要求删除白名单条目）。
}


@pytest.fixture(scope="module")
def contract_tags() -> tuple[set[str], set[str]]:
    """(顶层已声明的 tag, paths 里实际用到的 tag)。"""
    import yaml

    spec = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    declared = {t["name"] for t in (spec.get("tags") or [])}
    used: set[str] = set()
    for ops in (spec.get("paths") or {}).values():
        for method, op in (ops or {}).items():
            if method.lower() in HTTP_METHODS:
                used.update((op or {}).get("tags") or [])
    assert declared and used, "契约 tag 没解析出来——解析逻辑有问题"
    return declared, used


def test_f_contract_tags_all_declared(contract_tags: tuple[set[str], set[str]]) -> None:
    """paths 用到的 tag 必须在顶层声明，且顶层不得有无人使用的废声明。"""
    declared, used = contract_tags
    assert not (used - declared), (
        f"以下 tag 在 paths 里用了却未在顶层 `tags` 声明：{sorted(used - declared)}"
    )
    assert not (declared - used), (
        f"以下 tag 顶层声明了却没有任何 operation 使用（废声明，请删）：{sorted(declared - used)}"
    )


def test_f_route_tags_match_contract(contract_tags: tuple[set[str], set[str]]) -> None:
    """代码路由用的 tag 必须与契约一致——**含大小写**，否则 Swagger 分组会裂成两半。

    未登记进契约的 tag 须显式列入 `CODE_ONLY_TAGS`，不许默默存在。
    """
    declared, _ = contract_tags

    def walk(routes: list[Any]) -> Any:
        for r in routes:
            if type(r).__name__ == "_IncludedRouter":
                yield from walk(r.original_router.routes)
            elif hasattr(r, "dependant") and hasattr(r, "methods"):
                yield r

    route_tags = {t for r in walk(create_app().routes) for t in (getattr(r, "tags", None) or [])}
    assert route_tags, "没取到任何路由 tag——遍历逻辑可能失效"

    unknown = sorted(route_tags - declared - CODE_ONLY_TAGS.keys())
    assert not unknown, (
        f"以下 tag 只存在于代码、契约未声明：{unknown}。"
        "若属未登记端点请补进 CODE_ONLY_TAGS 并写明归属；若是大小写写错了请改代码"
        "（契约侧 9 个 tag 一律首字母大写）"
    )
    stale = sorted(CODE_ONLY_TAGS.keys() & declared)
    assert not stale, f"以下 tag 已补进契约，请从 CODE_ONLY_TAGS 删除：{stale}"


# ── G：保护存在性（2026-07-27 由独立审查 AI 的 F3 逼出）──
#
# **这是本门禁自身的方向性 fail-open，实测坐实的**：A/B 用 `if p` 把 None 滤掉、E 判
# `None == None` 相等、C/D 只比 (method, path) 集合、F 只比 tag——**六组判据没有一条问
# 「这个 operation 到底有没有保护」**。于是把 `order/router.py` 的
# `require_permission("order.ship")` 换成 `get_current_user`、同时在契约里删掉那行
# `x-permission: order.ship`，两侧一起归零后**六组全绿通过**（我按报告描述实跑复现，
# 非静态推导）。结果是任何登录用户——包括只持 `catalog.product_read` 的采集员——
# 都能对任意订单发货回传，而门禁说没问题。**把保护删干净反而最容易过闸。**
#
# None 在两边都是合法取值（下表 15 条），所以做不成「None 即报警」，只能显式登记。
PERMISSIONLESS_OPS: dict[tuple[str, str], str] = {
    # 认证入口：拿不到 token 之前谈不上权限点
    ("POST", f"{API_PREFIX}/auth/login"): "登录本身是取证入口",
    ("POST", f"{API_PREFIX}/auth/refresh"): "续期只认 refresh token",
    ("POST", f"{API_PREFIX}/auth/logout"): "登出只吊销自己的凭证",
    # 自我信息与静态字典：任何登录用户可读，读的是「我自己」或无租户数据
    ("GET", f"{API_PREFIX}/me"): "读自己的身份与权限集合",
    ("GET", f"{API_PREFIX}/permissions"): "权限码字典本身（供角色配置页渲染）",
    ("GET", f"{API_PREFIX}/dicts/{{}}"): "静态枚举字典，无租户数据",
    # 通知中心：`notify/router.py:1` docstring 即此口径「任何登录用户可用（无权限点）」，
    # 可见性由 RLS + notification_target 决定，不由权限点决定
    ("GET", f"{API_PREFIX}/notifications"): "通知列表，可见性走 RLS 与投递目标",
    ("GET", f"{API_PREFIX}/notifications/unread-count"): "未读数，同上",
    ("POST", f"{API_PREFIX}/notifications/read-all"): "标记自己已读",
    ("POST", f"{API_PREFIX}/notifications/{{}}/read"): "标记自己已读",
    # 采集节点回调面：走**第三认证域**（X-Node-Key + X-Node-Token 双头部 `_node_auth`），
    # 不是 JWT 用户，天然没有权限点。**它们不是没保护，是保护在另一条道上。**
    ("POST", f"{API_PREFIX}/worker/v1/register"): "node 认证域；以一次性 enroll_token 换长期凭证",
    ("POST", f"{API_PREFIX}/worker/v1/sync"): "node 认证域（_node_auth 双头部）",
    ("GET", f"{API_PREFIX}/worker/v1/tasks/pull"): "node 认证域",
    ("POST", f"{API_PREFIX}/worker/v1/tasks/release"): "node 认证域",
    ("POST", f"{API_PREFIX}/worker/v1/tasks/result"): "node 认证域",
}


def test_g_no_route_silently_loses_its_permission(
    route_perms: dict[tuple[str, str], str | None],
) -> None:
    """**没有权限点的路由必须显式登记**——否则删保护就是静默通过。

    这条是 A/B/C/D/E/F 都不管的那一面：它们保证「两边一致」，本条保证「保护存在」。
    """
    unguarded = sorted(k for k, v in route_perms.items() if v is None)
    unregistered = [k for k in unguarded if k not in PERMISSIONLESS_OPS]
    assert not unregistered, (
        "以下路由没有 require_permission 且未登记进 PERMISSIONLESS_OPS：\n  "
        + "\n  ".join(f"{m} {p}" for m, p in unregistered)
        + "\n若确属无需权限点（认证入口/自我信息/另一条认证道），请登记并写明理由；"
        "若是保护被误删，请把 require_permission 加回去。"
    )


def test_g_permissionless_registry_has_no_zombies(
    route_perms: dict[tuple[str, str], str | None],
) -> None:
    """反向不变量：登记表里的条目必须**当前确实无权限点**。

    端点后来加上了权限点、或路径改了名，登记就得撤——否则这张表会慢慢变成
    「谁都能进的永久豁免名单」。与 `SUPER_ONLY`、C 类白名单是同一条道理。
    """
    stale: list[str] = []
    for key, reason in sorted(PERMISSIONLESS_OPS.items()):
        m, p = key
        if key not in route_perms:
            stale.append(f"{m} {p}: 登记了但该路由已不存在（改名或删除？）——请从表里删")
        elif route_perms[key] is not None:
            stale.append(
                f"{m} {p}: 现已有权限点 {route_perms[key]!r}，登记（{reason}）已过时——请从表里删"
            )
    assert not stale, "PERMISSIONLESS_OPS 有陈旧条目：\n  " + "\n  ".join(stale)
