#!/usr/bin/env python3
"""PowerShell 载荷内 `\\"` 转义检查（PR #48 部署机⑥-1 停机的机器化，第 4 类）。

## 失败机理

部署指令 v2 的⑥-1 把 JSON 字面量塞进 PowerShell 双引号字符串，用 `\\"` 转义内层
双引号——那是 **bash/C 的手**。PowerShell 的转义字符是反引号（`` ` ``），反斜杠
什么都不是：内层 `"` 直接终止参数，psql 收到的是碎块。真机实报：

    extra command-line argument "'{\\list\\:19.99}'::jsonb," ignored
    ERROR:  syntax error at end of input

**这类错的隐蔽处**：整条命令在 bash 里是完全正确的，写的人（我）手顺着 bash 肌肉
记忆往下写，人眼扫不出来——直到 Win11 上真跑。与 payloadcheck 抓的「语言换了」
同族，但方向相反：那边是载荷内容串了语言，这边是**外层引号的转义规则**串了语言。

## 判据

```powershell 块内（剥注释后）出现两字符序列 `\\"` 即判红。

- **不判「换转义能不能修好」**——正解从来不是把 `\\"` 换成 `` `" ``，而是**让内层
  双引号根本不出现**：SQL 里用 `jsonb_build_object`/`jsonb_build_array` 代替 JSON
  字面量；PowerShell 里用单引号字符串或 here-string。转义叠转义是下一次停机的温床。
- **已知误报面**：以反斜杠结尾的双引号路径（`"D:\\某目录\\"`）会被扫中。这在 PS 里
  确实合法，但在部署指令里同样是雷（下一层解析器未必这么想）——写成单引号或去掉
  尾反斜杠即可绕开本检查，代价可接受。误报进人眼会让检查器被整体忽略
  （xrefcheck 头注的教训），所以规则只认 `\\"` 这一个指纹，不扩到「引号数为奇数」
  之类的宽网。

剥注释是**引号感知**的：`#` 只有在不处于任何字符串内时才当注释起点——
psvarcheck 那种 `#.*$` 一刀切会把字符串里带 `#` 的载荷剥掉半截，在**专查引号**的
检查器里这不可接受。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import sys
import tempfile
from pathlib import Path

BLOCK_RE = re.compile(r"```powershell\n(.*?)```", re.S)


def _strip_comments(code: str) -> str:
    """按 PowerShell 的引号规则剥注释：字符串内的 `#` 不是注释。

    状态机只为「`#` 在不在字符串里」服务；对 BAD 输入（`\\"` 早终止字符串）
    的状态漂移不影响检测——检测是对剥完注释的全文找 `\\"`，与状态无关。
    """
    out: list[str] = []
    in_s = in_d = False
    i, n = 0, len(code)
    while i < n:
        c = code[i]
        if in_d:
            if c == "`" and i + 1 < n:  # 反引号才是转义
                out.append(code[i : i + 2])
                i += 2
                continue
            if c == '"':
                if i + 1 < n and code[i + 1] == '"':  # "" = 字符串内的引号
                    out.append('""')
                    i += 2
                    continue
                in_d = False
        elif in_s:
            if c == "'":
                if i + 1 < n and code[i + 1] == "'":  # '' = 字符串内的引号
                    out.append("''")
                    i += 2
                    continue
                in_s = False
        else:
            if c == '"':
                in_d = True
            elif c == "'":
                in_s = True
            elif c == "#":  # 真注释：吃到行尾
                j = code.find("\n", i)
                i = n if j == -1 else j
                continue
        out.append(c)
        i += 1
    return "".join(out)


def check(doc: str) -> tuple[list[str], int]:
    """→ (问题报文, 扫到的 powershell 块数)。"""
    blocks = BLOCK_RE.findall(doc)
    problems: list[str] = []
    for n, b in enumerate(blocks, 1):
        code = _strip_comments(b)
        for ln_no, ln in enumerate(code.splitlines(), 1):
            hits = ln.count('\\"')
            if hits:
                problems.append(
                    f"块 {n} 第 {ln_no} 行：发现 `\\\"` ×{hits}——"
                    f"PowerShell 的转义是反引号，`\\\"` 不转义，内层引号会当场终止参数。"
                    f"正解是让内层双引号根本不出现（jsonb_build_object / 单引号 / here-string）"
                )
    return problems, len(blocks)


def report(path: Path) -> int:
    doc = path.read_text(encoding="utf-8")
    problems, n_blocks = check(doc)
    if n_blocks == 0:
        # **「零命中 + 通过」是最危险的输出**——与另外三个检查器同一条守卫。
        print(f"⚠️ {path.name} 里没有任何 ```powershell 代码块——**本检查器无事可查**。")
        print("   这不是「通过」。")
        return 2
    print(f"扫到 powershell 块 {n_blocks} 个")
    if problems:
        print("\n发现问题：")
        for p in problems:
            print(f"  {p}")
        return 1
    print('\n结论： powershell 载荷内无 `\\"` 转义 ✅')
    return 0


# v2 ⑥-1 的原样形状（缩到最小）：bash 手写的 JSON 转义
_BAD = """```powershell
docker compose exec -T db psql -c "INSERT INTO t (a) VALUES ('{\\"wpt\\":\\"Drinkware\\"}'::jsonb);"
```"""
# v3 的形状：载荷内只有单引号
_GOOD = """```powershell
docker compose exec -T db psql -c "INSERT INTO t (a) SELECT jsonb_build_object('wpt','Drinkware');"
```"""
# 注释里提到 \\" 不算数——引号感知剥注释这条也要被证伪一次
_COMMENT_ONLY = """```powershell
Get-Content x.md   # 注意：不要在这里写 \\" 那种 bash 转义
```"""
# 字符串里带 # 时，# 后面的 \\" 仍要抓到——一刀切剥注释在这里会漏判
_HASH_IN_STRING = """```powershell
psql -c "SELECT '#1', '{\\"k\\":1}'::jsonb;"
```"""


def _prose_exit_code() -> int:
    """把一份只有散文的文件真的喂给 `report()`，取它的退出码。

    零命中守卫住在 `report()` 里，`check()` 够不着它——PR #47 审查侧证过：
    只测 `check()` 的 self-test 抓不住 `return 2` 被改成 `return 0`。
    """
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "prose-only.md"
        p.write_text("只有散文，没有任何 powershell 代码块。\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            return report(p)


def self_test() -> int:
    bad, _ = check(_BAD)
    good, _ = check(_GOOD)
    comment, _ = check(_COMMENT_ONLY)
    hash_in_string, _ = check(_HASH_IN_STRING)
    _, empty_blocks = check("没有代码块的散文。")
    prose_exit = _prose_exit_code()
    ok = (
        len(bad) == 1
        and not good
        and not comment
        and len(hash_in_string) == 1
        and empty_blocks == 0
        and prose_exit == 2
    )
    print(f"self-test  v2形状判红={len(bad) == 1}  v3形状判绿={not good}")
    print(f"           注释内不误判={not comment}  字符串带#仍抓到={len(hash_in_string) == 1}")
    print(f"           零块可被识别={empty_blocks == 0}  零命中出口 report()=={prose_exit}（须为 2，非 0）")
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
