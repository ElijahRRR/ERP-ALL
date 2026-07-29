#!/usr/bin/env python3
"""PowerShell 变量名大小写碰撞检查（#43 第 6 轮催生）。

PowerShell 的变量名**不区分大小写**：`$HEAD_SHA` 与 `$head_sha` 是同一个变量。
部署指令里一旦两种写法混用，后写的会悄悄覆盖前一个，而**两边看起来是两个变量**。

## 2026-07-29 补：本脚本自己带着它要防的那种缺陷

原版（`sys.argv[1]` 直读、无参数解析）有两个问题，都是 PR #46 回执里**声称它没有**的：

1. **零命中判绿**——文档里一个 ```powershell 块都没有时，循环不执行、`bad` 保持 0，
   于是打印「无大小写碰撞 ✅」并 exit 0。**它什么都没查，输出却是绿的。**
   这正是 `xrefcheck.py` / `payloadcheck.py` 明确防住、而回执误称三者都防住了的那一条。
2. **没有 `--self-test`**——回执写「三者都带 `--self-test`」，本脚本当时并没有。

两条都在 PR #47 补齐。**记在这里而不是悄悄修掉**：这个错是「我声称的检查覆盖面
比实际的宽」，与本单反复出现的那一类同形，删掉痕迹就等于把教训也删了。
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path

BLOCK_RE = re.compile(r"```powershell\n(.*?)```", re.S)
VAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def check(doc: str) -> tuple[list[str], int]:
    """→ (碰撞报文, 扫到的 powershell 块数)。"""
    blocks = BLOCK_RE.findall(doc)
    problems: list[str] = []
    for n, b in enumerate(blocks, 1):
        # 剥掉整行注释与行尾注释——注释里提到的变量名不是真的变量。
        # （第一版栽在这儿：把注释里的变量名算成变量。xrefcheck 后来踩了同一个坑。）
        code = "\n".join(re.sub(r"#.*$", "", ln) for ln in b.splitlines())
        names = VAR_RE.findall(code)
        if not names:
            continue
        groups: dict[str, set[str]] = collections.defaultdict(set)
        for v in names:
            groups[v.lower()].add(v)
        for _, variants in sorted(groups.items()):
            if len(variants) > 1:
                problems.append(
                    f"块 {n}：**大小写碰撞** {sorted(variants)} "
                    f"在 PowerShell 里是同一个变量"
                )
    return problems, len(blocks)


def report(path: Path) -> int:
    doc = path.read_text(encoding="utf-8")
    problems, n_blocks = check(doc)
    if n_blocks == 0:
        # **「零命中 + 通过」是最危险的输出**：它看起来是绿的，实际一条都没查。
        # 与 xrefcheck / payloadcheck 同一条道理。
        print(f"⚠️ {path.name} 里没有任何 ```powershell 代码块——**本检查器无事可查**。")
        print("   这不是「通过」。")
        return 2
    print(f"扫到 powershell 块 {n_blocks} 个")
    if problems:
        print("\n发现问题：")
        for p in problems:
            print(f"  {p}")
        return 1
    print("\n结论： 代码中无大小写碰撞 ✅")
    return 0


_BAD = """```powershell
$HEAD_SHA = git rev-parse HEAD
"$head_sha 就是它"
```"""
_GOOD = """```powershell
$HEAD_SHA = git rev-parse HEAD
"$HEAD_SHA 就是它"
```"""
# 注释里的「碰撞」不算数——剥注释这条也要被证伪一次
_COMMENT_ONLY = """```powershell
$HEAD_SHA = git rev-parse HEAD   # 不要写成 $head_sha
```"""


def self_test() -> int:
    bad, _ = check(_BAD)
    good, _ = check(_GOOD)
    comment, _ = check(_COMMENT_ONLY)
    _, empty_blocks = check("没有代码块的散文。")
    ok = len(bad) == 1 and not good and not comment and empty_blocks == 0
    print(f"self-test  碰撞判红={len(bad) == 1}  干净判绿={not good}")
    print(f"           注释内不误判={not comment}  零块可被识别={empty_blocks == 0}")
    for p in bad:
        print(f"  （应报）{p}")
    print("self-test", "通过 ✅" if ok else "失败 ❌")
    return 0 if ok else 1


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
