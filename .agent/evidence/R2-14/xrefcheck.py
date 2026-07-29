#!/usr/bin/env python3
"""部署指令的步骤交叉引用检查（PR #46 审查侧 N1 的机器化）。

## 为什么要它

部署指令的步骤号在起草过程中会移位（插一步、并两步），而正文里的「见第 X 步」
不会跟着改。**这类错在部署指令里反复出现**（#43 那份连修五版），且它有个共同特征：
**部署机被铁律要求「对不上就停下来问」，所以后果不是跑错，是白停一轮。**

## 为什么只查「被引用的步骤存在吗」不够

PR #46 的 N1 就是反例：⑦ 写「这是⑪的基线」，而 ⑪ **确实存在**（只是它是别的事，
真正的对拍在 ⑬）。存在性检查全绿，错照样溜过去。

**抓得到它的是互引一致性**：真正成对的关系（基线↔对拍、快照↔比对）两头都会提到对方。
⑦→⑪ 是单向的，而 ⑬→⑦ 也是单向的——两条孤立的单向引用，正是「引用指错了对象」的
指纹。故本检查器：

- **硬失败**：引用了不存在的步骤；
- **硬失败**：引用中带成对语义关键词（基线/对拍/快照/与…比）却**不互引**；
- **只报告**：其余单向引用（「同⑤的做法」这类本就该单向，不判错）。

> 检查器自身也要被检查——见文末 `--self-test`：拿 PR #46 修复前的原句喂进去，
> 必须判红；喂修好的，必须判绿。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import sys
import tempfile
from pathlib import Path

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
# 成对语义：出现这些词的引用，两头必须互相提到对方。
# ⚠️ **不要把「比」这类常用字放进来**：第一版放了，于是「输出可逐字比对」这种
# 普通行文被当成步骤配对而误判。误判会让人开始整体忽略检查器，比漏判更伤。
# 这四个词在本类文档里几乎只用于「基线↔对拍」这层关系，而 PR #46 的 N1
# （「与⑪对拍」「这是⑪的基线」）靠前两个照样抓得到——**收紧没有削弱它**。
#
# 同一次收紧还去掉了「同一段」（审查侧 N3 指出这一处删除当时没有解释）。
# 理由与「比」不同：它不是误判源，实测对当时的文档零影响——纯粹是它描述的是
# **位置关系**而不是**成对的验证关系**，留着只会让这张表的语义变浑。
# 记在这里是因为：**未声明的删除，下一个人无从判断它是有意还是手滑。**
PAIRED_HINTS = ("基线", "对拍", "快照", "重跑")
HEADING_RE = re.compile(rf"^#{{1,4}}\s*([{CIRCLED}])")

# **同一批圈码在本项目文档里有三种含义**，只有第一种才是步骤引用：
#   1. 步骤号——「见第⑬步」「与⑬对拍」
#   2. §7.1 三级删除规则的「级」——「③级绝不删」
#   3. 工单验收判据编号——「验收②」
# 后两种带固定前后缀，对人无歧义；检查器必须先把它们剥掉，否则满屏误判，
# 而**误判多到一定程度，人就开始整体忽略这个检查器**——那比没有检查器更糟。
NOISE_RE = re.compile(rf"(?:验收|判据|acceptance)\s*[{CIRCLED}]+|[{CIRCLED}]+\s*级")


def _strip_noise(line: str) -> str:
    return NOISE_RE.sub("", line)


def parse(text: str) -> tuple[list[str], dict[str, list[str]]]:
    """→ (步骤顺序, {步骤: 它正文里引用到的其它步骤})。"""
    steps: list[str] = []
    refs: dict[str, list[str]] = {}
    current: str | None = None
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        # **围栏代码块必须跳过**：PowerShell 与 shell 的注释也以 `#` 开头，
        # 「# ① 某某用例」会被当成步骤标题。psvarcheck.py 第一版栽在同一处
        # （把注释里提到的变量名算成变量）——**检查器自身也需要被检查**。
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if m:
            current = m.group(1)
            steps.append(current)
            refs.setdefault(current, [])
            # 标题里除自身外的引用也算（如「与⑬对拍」写在标题上）
            body = line[m.end() :]
        else:
            body = line
        if current is None:
            continue
        for ch in _strip_noise(body):
            if ch in CIRCLED and ch != current:
                refs[current].append(ch)
    return steps, refs


def _context(text: str, src: str, dst: str) -> str:
    """取出 src 步骤里提到 dst 的那一行，用于判断是不是成对语义。"""
    current: str | None = None
    for line in text.splitlines():
        m = HEADING_RE.match(line)
        if m:
            current = m.group(1)
        if current == src and dst in line:
            return line.strip()
    return ""


def check(text: str) -> list[str]:
    steps, refs = parse(text)
    known = set(steps)
    problems: list[str] = []

    for src, targets in refs.items():
        for dst in dict.fromkeys(targets):
            if dst not in known:
                problems.append(f"[缺失] {src} 引用了不存在的步骤 {dst}")
                continue
            line = _context(text, src, dst)
            paired = any(h in line for h in PAIRED_HINTS)
            reciprocal = src in refs.get(dst, [])
            if paired and not reciprocal:
                who = [s for s in steps if src in refs.get(s, [])]
                hint = f"；真正提到 {src} 的是 {''.join(who)}" if who else ""
                problems.append(
                    f"[单向成对引用] {src} → {dst}：「{line[:70]}」"
                    f" 但 {dst} 从未提及 {src}{hint}"
                )
    return problems


def report(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    steps, refs = parse(text)
    if not steps:
        # **「0 步骤 + 通过」是最危险的输出**：它看起来是绿的，实际什么都没查。
        # 本检查器只适用于「用 `## ①` 这类圈码标题编步骤」的文档；不是这种编号方式的
        # （如 R2-09 的 pr43 指令用四段式 + 判据编号），必须明说不适用，而不是判绿。
        print(f"⚠️ 未找到任何圈码步骤标题（`## ①` 形式）——**本检查器不适用于 {path.name}**。")
        print("   这不是「通过」：一条都没查。")
        return 2
    print(f"步骤 {len(steps)} 个：{''.join(steps)}")
    for s in steps:
        if refs.get(s):
            print(f"  {s} → {''.join(dict.fromkeys(refs[s]))}")
    problems = check(text)
    if problems:
        print("\n发现问题：")
        for p in problems:
            print(f"  {p}")
        return 1
    print("\n结论： 交叉引用一致 ✅")
    return 0


_BAD = """## ⑦ 基线快照（与⑪对拍）
这是⑪的基线。
## ⑪ 守卫：在架的删不掉
无关内容。
## ⑬ 对拍
重跑⑦那段完全相同的 SQL，与基线逐行比。
"""
_GOOD = _BAD.replace("与⑪对拍", "与⑬对拍").replace("这是⑪的基线", "这是⑬的基线")


def _prose_exit_code() -> int:
    """把一份没有圈码步骤的文件真的喂给 `report()`，取它的退出码。

    **零命中守卫（「不适用」出口）住在 `report()` 里，`check()` 够不着它**——
    PR #47 审查侧实测：把 `report()` 的 `return 2` 改成 `return 0`，
    三个检查器的 self-test 全绿。守卫本身没有被任何 self-test 保护。
    """
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "prose-only.md"
        p.write_text("只有散文，没有任何圈码步骤标题。\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            return report(p)


def self_test() -> int:
    bad = check(_BAD)
    good = check(_GOOD)
    prose_exit = _prose_exit_code()
    ok = bool(bad) and not good and prose_exit == 2
    print(f"self-test  修复前判红={bool(bad)}  修复后判绿={not good}")
    print(f"           零命中出口 report()=={prose_exit}（须为 2，非 0）")
    for p in bad:
        print(f"  （修复前应报）{p}")
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
