#!/usr/bin/env python3
"""CI 只读门禁③：渠道写路径改动必须带 `.agent/evidence/` 变更（铁律 4 机器化）。

**为什么要这条门禁**——2026-07-26 Owner 审计指出铁律 4 被实质违反：R2-12 增量 1/3/4b
三个 PR 都在「待分支验证 → 合并」状态下直接合并，验证结果全仓零记录；其中 4b 是渠道
写路径，而 R2-12 全单**一份 dry-run 快照都没落仓**（对照 R1-07/R1-11/R2-03/R2-06 皆有），
真机图证据全挂 PR 评论、不在仓内。核实无误。

病根不是「没人看」，而是「把过程写在别处就当成过程有记录」——PR 评论和聊天记录都不是
仓内证据。这条门禁把它变成机器判据：**PR 若动了会打到真实渠道的代码，就必须同时动
`.agent/evidence/`**。

判定方式刻意从宽（宁可漏报不误伤）：
- 只看「本 PR 相对 base 的改动文件清单」；
- 命中条件 = 改动文件里有**调用网关**的（`gateway.request` / `gateway.request_prepared`
  / `gateway.prepare`）后端源码文件；
- 放行条件 = 同一份 diff 里有任何 `.agent/evidence/` 下的文件变更。

`ADVISORY=1` 时只告警不拦（首轮上车用），否则命中即非零退出。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 判定「这文件会打到真实渠道」的调用点。故意只认网关入口——绕过 walmart_client / 网关
# 直连渠道本就是 CLAUDE.md 铁律 5 禁区，另有审计手段，不在本门禁职责内。
GATEWAY_CALL_MARKERS = (
    "gateway.request_prepared",
    "gateway.request(",
    "gateway.prepare(",
)
EVIDENCE_PREFIX = ".agent/evidence/"
SOURCE_SUFFIXES = (".py",)


def _run(*args: str) -> str:
    return subprocess.run(
        args, cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def changed_files(base_ref: str) -> list[str]:
    """本 PR 相对 base 的改动文件。base 取不到时退回 HEAD~1（push 场景）。"""
    try:
        merge_base = _run("git", "merge-base", base_ref, "HEAD")
    except subprocess.CalledProcessError:
        merge_base = _run("git", "rev-parse", "HEAD~1")
    return [f for f in _run("git", "diff", "--name-only", merge_base, "HEAD").splitlines() if f]


def touches_channel_write(files: list[str]) -> list[str]:
    """改动文件里哪些含网关调用（按改动后的内容判定——删掉调用的 PR 不该被拦）。"""
    hits = []
    for f in files:
        if not f.endswith(SOURCE_SUFFIXES):
            continue
        path = REPO_ROOT / f
        if not path.exists():  # 本 PR 删除的文件
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(m in text for m in GATEWAY_CALL_MARKERS):
            hits.append(f)
    return hits


def main() -> int:
    base_ref = os.environ.get("GITHUB_BASE_REF") or "origin/main"
    if not base_ref.startswith("origin/") and "/" not in base_ref:
        base_ref = f"origin/{base_ref}"

    files = changed_files(base_ref)
    if not files:
        print("evidence-gate: 无改动文件，跳过。")
        return 0

    channel_hits = touches_channel_write(files)
    if not channel_hits:
        print(f"evidence-gate: 本次 {len(files)} 个改动文件均不含网关调用，跳过。")
        return 0

    evidence_hits = [f for f in files if f.startswith(EVIDENCE_PREFIX)]
    if evidence_hits:
        print("evidence-gate: 通过。")
        print(f"  渠道写路径改动 {len(channel_hits)} 个：" + ", ".join(channel_hits[:5]))
        print(f"  同 diff 内 evidence 变更 {len(evidence_hits)} 个：" + ", ".join(evidence_hits[:5]))
        return 0

    advisory = os.environ.get("ADVISORY") == "1"
    label = "警告（advisory）" if advisory else "失败"
    print(f"evidence-gate: {label}——改了渠道写路径但没有任何 {EVIDENCE_PREFIX} 变更。")
    print("  命中的渠道写路径文件：")
    for f in channel_hits:
        print(f"    {f}")
    print()
    print("  铁律 4：渠道写路径的增量必须有 dry-run 证据落仓。仓内已有三处可照抄的范式——")
    print("    tests/db/test_gateway.py / test_listing_api.py / test_price_push.py")
    print("  做法：dry_run 模式下断言请求形态，并把快照写进 .agent/evidence/<工单>/。")
    print("  参考 evidence/R2-12/dryrun-mp-maintenance.json（2026-07-26 补的那份）。")
    return 0 if advisory else 1


if __name__ == "__main__":
    sys.exit(main())
