#!/usr/bin/env python3
"""跨 shell 边界检查：别把 PowerShell 的 cmdlet 写进容器里的 `sh -c`。

## 起因

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
                f"[跨 shell] 第 {line_no} 行附近：`{cmdlet}` 是 PowerShell cmdlet，"
                f"却出现在 sh/bash 的载荷里 → 容器内会报 `not found`\n"
                f"          载荷：{payload.strip()[:80]}"
            )
    return problems


_BAD = """```powershell
docker compose exec frontend sh -lc "ls /usr/share/nginx/html/assets | Select-String 'index-'"
```"""
_GOOD = """```powershell
docker compose exec frontend sh -c "ls /usr/share/nginx/html/assets/index-*.js"
```"""


def self_test() -> int:
    bad, good = check(_BAD), check(_GOOD)
    ok = bool(bad) and not good
    print(f"self-test  修复前判红={bool(bad)}  修复后判绿={not good}")
    for p in bad:
        print(f"  （修复前应报）{p.splitlines()[0]}")
    print("self-test", "通过 ✅" if ok else "失败 ❌")
    return 0 if ok else 1


def report(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    calls = SHELL_CALL_RE.findall(text)
    if not calls:
        # 与 xrefcheck 同一条道理：「零命中 + 通过」看起来是绿的，实际什么都没查。
        print(f"⚠️ {path.name} 里没有任何 `sh -c` / `bash -c` 调用——**本检查器无事可查**。")
        print("   这不是「通过」。")
        return 2
    print(f"扫到 {len(calls)} 处 sh/bash 载荷")
    problems = check(text)
    if problems:
        print("\n发现问题：")
        for p in problems:
            print(f"  {p}")
        return 1
    print("\n结论： 无 PowerShell cmdlet 越界进 sh ✅")
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
