#!/usr/bin/env python3
"""载荷语言错配检查：**把 X 的语法写进了只认 Y 的载荷里**。

> 原名 `crossshellcheck.py`，只查 PS→sh 一种。部署机第五次停机撞出的
> 「psql 元命令混进 `-c` 的 SQL 载荷」是**同一形状的错**，故改名并合并规则：
> 都是「引号一进去，语言就换了，而两边在同一个代码块里，人眼扫不出来」。

## 两条规则

| 规则 | 载荷属于 | 判红条件 |
|---|---|---|
| R1 | `sh -c` / `bash -c` | 出现 PowerShell 的 `Verb-Noun` cmdlet |
| R2 | `psql ... -c` | 含 `\\d` 这类元命令，**且载荷不是「恰好一条元命令」** |

R2 的边界要说清楚：`psql -c '\\d foo'` **是合法的**——`-c` 在载荷恰好是单条元命令时
会把它当元命令跑。一旦带上换行或与 SQL 混排，整段按 SQL 发送，于是
`syntax error at or near "\\"`。所以判据不是「有没有反斜杠」，是「有反斜杠**而且**不是单条」。

## R1 的起因

PR #46 第三闸第④步 v1 写的是：

    docker compose exec frontend sh -lc "ls /usr/share/nginx/html/assets | Select-String 'index-'"

`Select-String` 是 PowerShell cmdlet，而 `sh -lc` 里跑的是容器内的 Alpine sh，
于是 `sh: Select-String: not found`，部署机停机。

**这类错的隐蔽处在于：整份文档标着 ```powershell，外层确实是 PowerShell，
所以写的时候手会顺着往下写**——但引号一进 `sh -c`，语言就换了。
人眼扫过去看不出来，因为两边都在同一个代码块里。

## 判据

在 `sh -c` / `sh -lc` / `bash -c` / `bash -lc` 的**引号载荷**里，出现 PowerShell 的
`Verb-Noun` 形态标识符即判红。用形态而不是白名单：白名单永远漏掉没想到的那个，
而 `Get-`/`Select-`/`Where-` 这种大写连字符命名在 POSIX 工具里本就极罕见。

> 反向（把 `grep`/`awk` 写进 PowerShell）不查——那在 Windows 上多半直接就报错了，
> 而且 PowerShell 有不少 Unix 别名，误判率会很高。**只查会静默写错的那个方向。**
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# `sh -c "..."`、`bash -lc '...'`：抓引号里的载荷
SHELL_CALL_RE = re.compile(r"""\b(?:ba)?sh\s+-[a-z]*c\s+(["'])(?P<payload>.*?)\1""", re.DOTALL)
# PowerShell 的 Verb-Noun：Select-String / Get-Content / ConvertFrom-Json …
CMDLET_RE = re.compile(r"\b[A-Z][A-Za-z]+-[A-Z][A-Za-z]+\b")
# `psql ... -c "..."`；注意要兼容 `-tAc` 这类合并短选项
PSQL_CALL_RE = re.compile(r"""psql\b[^"']*?-[a-zA-Z]*c\s+(["'])(?P<payload>.*?)\1""", re.DOTALL)


def _fenced_only(text: str) -> str:
    """只保留围栏代码块里的内容，其余行清空（保留行数，报错行号才准）。

    **必须这么做**：修订说明里会**引用**当初写错的那条命令当反面教材，
    那是散文不是命令，扫到它就是误报——而误报会让人开始整体忽略检查器。
    与 `xrefcheck.py` 是同一个教训的镜像：那边要**跳过**围栏（代码注释不是标题），
    这边要**只看**围栏（真命令都在围栏里，围栏外的都是说明）。
    """
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append("")
            continue
        out.append(line if in_fence else "")
    return "\n".join(out)


def check(text: str) -> list[str]:
    problems: list[str] = []
    text = _fenced_only(text)
    for m in SHELL_CALL_RE.finditer(text):
        payload = m.group("payload")
        for cmdlet in dict.fromkeys(CMDLET_RE.findall(payload)):
            line_no = text[: m.start()].count("\n") + 1
            problems.append(
                f"[R1 跨 shell] 第 {line_no} 行附近：`{cmdlet}` 是 PowerShell cmdlet，"
                f"却出现在 sh/bash 的载荷里 → 容器内会报 `not found`\n"
                f"          载荷：{payload.strip()[:80]}"
            )

    for m in PSQL_CALL_RE.finditer(text):
        payload = m.group("payload")
        metas = [ln.strip() for ln in payload.splitlines() if ln.strip().startswith("\\")]
        if not metas:
            continue
        # 合法形态：**原始载荷**恰好就是一条元命令，前后不带任何空白。
        # ⚠️ 这里**不能先 strip**——带前导换行的载荷 strip 完也是单行，
        # 而那个换行正是让 psql 按 SQL 发送的原因（部署机第五次停机的实证）。
        # 第一版就是 strip 之后再判，于是把真正的错误样本当成合法放过了，self-test 判红才发现。
        if len(payload.splitlines()) == 1 and payload.startswith("\\"):
            continue
        line_no = text[: m.start()].count("\n") + 1
        problems.append(
            f"[R2 psql 元命令] 第 {line_no} 行附近：`{metas[0]}` 是 psql 元命令，"
            f"但载荷不是「恰好一条元命令」→ 整段按 SQL 发送，报 syntax error at or near \\\n"
            f"          修法：改成 catalog 查询（输出可逐字比对），或单独一条 -c '{metas[0]}'"
        )
    return problems


_BAD = """```powershell
docker compose exec frontend sh -lc "ls /usr/share/nginx/html/assets | Select-String 'index-'"
```"""
_GOOD = """```powershell
docker compose exec frontend sh -c "ls /usr/share/nginx/html/assets/index-*.js"
```"""

# R2 的两个样本：带换行的元命令（错）vs 单条元命令（合法，不该判红）
_BAD_PSQL = '''```powershell
docker compose exec db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "
\\d app.deleted_product
"
```'''
_OK_PSQL = """```powershell
docker compose exec db psql -U erp_migrator -d erp_all -c '\\d app.deleted_product'
```"""


def self_test() -> int:
    bad, good = check(_BAD) + check(_BAD_PSQL), check(_GOOD) + check(_OK_PSQL)
    ok = len(bad) == 2 and not good
    print(f"self-test  修复前判红={bool(bad)}  修复后判绿={not good}")
    for p in bad:
        print(f"  （修复前应报）{p.splitlines()[0]}")
    print("self-test", "通过 ✅" if ok else "失败 ❌")
    return 0 if ok else 1


def report(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    fenced = _fenced_only(text)
    n_sh = len(SHELL_CALL_RE.findall(fenced))
    n_psql = len(PSQL_CALL_RE.findall(fenced))
    if n_sh + n_psql == 0:
        # 与 xrefcheck 同一条道理：「零命中 + 通过」看起来是绿的，实际什么都没查。
        print(f"⚠️ {path.name} 里没有任何 `sh -c` / `psql -c` 载荷——**本检查器无事可查**。")
        print("   这不是「通过」。")
        return 2
    print(f"扫到载荷：sh/bash {n_sh} 处、psql {n_psql} 处")
    problems = check(text)
    if problems:
        print("\n发现问题：")
        for p in problems:
            print(f"  {p}")
        return 1
    print("\n结论： 无载荷语言错配（R1 cmdlet 越界 / R2 psql 元命令混排）✅")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", type=Path)
    ap.add_argument("--self-test", action="store_true")
    ns = ap.parse_args()
    if ns.self_test:
        sys.exit(self_test())
    if ns.path is None:
        ap.error("需要一个文档路径，或 --self-test")
    sys.exit(report(ns.path))
