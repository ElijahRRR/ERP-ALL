"""R2-13 铁律 1 的结构性防线：**cookies 整条链删净**（不是「这次没做」，是不变量）。

| 判据 | 用例 |
|---|---|
| ERP 侧没有任何收 cookie 的端点 | `test_no_cookie_endpoint_in_any_route` |
| 后端代码里没有 cookie/会话 jar 的落点或搬运 | `test_no_cookie_identifier_in_backend_code` |
| 库里没有放 cookie 的表或列（`buyer_session` 不存在） | `test_no_cookie_landing_place_in_schema` |

**为什么值得一个测试文件**：Owner 2026-07-30 二次裁定「先不收 cookie」，理由不是
「暂时不用」而是**用途已被证伪**——那份 jar 的字段整形（`hostOnly` /
`sameSite:'no_restriction'` / `storeId:'1'` / `id:index+1`）正是 Cookie-Editor 类插件的
导出格式，唯一目的是被导入回浏览器：它是整套**可搬运的登录态**而非标识，
「哪个买家号」由同一调用的独立参数 `customerId` 承载。jar 一旦落库，持有者即可以
买家身份下单。一次性的「这版没实现」会被下一个照厂商协议补齐 9 个端点的人抹掉；
写成不变量才拦得住。出处：`007:308-315`、`owner-rulings-20260730.md` 补-1、
`plugin/router.py` 模块头注（三处留档，缺一即将来有人「补齐」它）。

**为什么 needle 不是裸的 `cookie`**：`audit/l2_content.py` 的儿童 IP 正则里有
`cookie monster sesame`（芝麻街）——那是 L2 内容审核的合法词表，与本条毫无关系。
用裸词会逼下一个人去加豁免，而豁免清单一旦开头就会长。故只钉真正的形状。
"""

import ast
from pathlib import Path
from typing import Any

import psycopg

from erp.main import create_app

BACKEND = Path(__file__).resolve().parents[2]
SOURCE_DIRS = (BACKEND / "src", BACKEND / "alembic" / "versions")

# 形状而非词：表名 / 端点名 / 变量名 / 浏览器 API。命中任一即整条链有人在往回接。
FORBIDDEN = ("buyer_session", "buyercookie", "cookie_jar", "cookiejar", "chrome.cookies")


def _code_text(path: Path) -> str:
    """取一个文件里**真正会执行的部分**：标识符 + 非 docstring 字符串。

    注释与 docstring 一律排除——本仓的纪律是「把不做的理由写清楚」，
    `plugin/router.py` 那段「⛔ 故意不含 `updateBuyerCookie`」正是遵守纪律的产物，
    不该被自己的门禁判红。**扫代码不扫文档**，否则结果是逼人删掉解释。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doc_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                doc_nodes.add(id(first.value))

    parts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str) and id(node) not in doc_nodes:
                parts.append(node.value)
        elif isinstance(node, ast.Name):
            parts.append(node.id)
        elif isinstance(node, ast.Attribute):
            parts.append(node.attr)
        elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            parts.append(node.name)
        elif isinstance(node, ast.arg) or (isinstance(node, ast.keyword) and node.arg):
            parts.append(node.arg)  # type: ignore[arg-type]
    return "\n".join(parts).lower()


def test_no_cookie_endpoint_in_any_route() -> None:
    """全应用零个路径带 cookie —— `updateBuyerCookie` 没有被任何人「补齐」。"""

    def walk(routes: list[Any], prefix: str = "") -> Any:
        for r in routes:
            if type(r).__name__ == "_IncludedRouter":
                ctx = getattr(r, "include_context", None)
                sub = getattr(ctx, "prefix", "") if ctx is not None else ""
                yield from walk(r.original_router.routes, prefix + sub)
            elif hasattr(r, "dependant") and hasattr(r, "methods"):
                yield prefix + r.path

    paths = list(walk(create_app().routes))
    assert paths, "没取到任何路由——遍历逻辑可能失效（本条判据会因此变成空判据）"
    offenders = [p for p in paths if "cookie" in p.lower()]
    assert not offenders, (
        f"出现了收 cookie 的端点：{offenders}。"
        "Owner 2026-07-30 裁定不收 cookie（用途已被证伪，见本文件头注）——"
        "买家号身份由 customerId 承载，不需要整套可搬运的登录态。"
    )


def test_no_cookie_identifier_in_backend_code() -> None:
    """后端代码里没有 cookie jar 的落点、搬运或 `buyer_session` 这张表。"""
    hits: list[str] = []
    for root in SOURCE_DIRS:
        for path in sorted(root.rglob("*.py")):
            text = _code_text(path)
            hits += [
                f"{path.relative_to(BACKEND)}: {needle}" for needle in FORBIDDEN if needle in text
            ]
    assert not hits, (
        "以下位置在往回接 cookie 链：\n  "
        + "\n  ".join(hits)
        + "\n（注释与 docstring 不在扫描范围内，命中的是真代码）"
    )


def test_no_cookie_landing_place_in_schema(migrated_db: str) -> None:
    """库里没有能放下 cookie jar 的表或列——**这条比源码扫描硬**：

    源码可以改写、绕过、换个变量名，而「落不落库」是结构性的。`buyer_session` 表不建
    这条裁定的全部要害就在这里：没有落点，就没有「持有者以买家身份下单」的能力泄漏面。
    """
    with psycopg.connect(migrated_db) as conn:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'app'"
                "   AND (table_name ILIKE '%%cookie%%' OR table_name ILIKE '%%session%%')"
            ).fetchall()
        ]
        columns = [
            f"{r[0]}.{r[1]}"
            for r in conn.execute(
                "SELECT table_name, column_name FROM information_schema.columns"
                " WHERE table_schema = 'app' AND column_name ILIKE '%%cookie%%'"
            ).fetchall()
        ]
    assert not tables, f"出现了 cookie/会话 表：{tables}（`buyer_session` 明确不建）"
    assert not columns, f"出现了能放 cookie 的列：{columns}"
