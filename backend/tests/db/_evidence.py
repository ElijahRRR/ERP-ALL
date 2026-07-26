"""dry-run 证据落盘：把**易变但无证据价值**的字段规范化，使文件 diff 只反映请求形态变化。

**为什么需要这层**（2026-07-27 自查 evidence-gate 时发现的它自己的弱点）：
`scripts/ci_evidence_gate.py` 的判据是「改了含网关调用的源码 → 同 diff 内必须有
`.agent/evidence/` 变更」。但 `evidence/R1-11/dry-run-feed-snapshot.json` 每跑一遍测试都会变
两行——`sku` 是库里序号分配的 master_sku（跟前面测试建了多少产品有关）、`startDate` 是构包
时刻的 `now()`。**请求形态一个字没变，文件却变了**。于是「同 diff 有 evidence 变更」这个信号
**可以被纯 churn 满足**：跑一遍测试就能过闸，并不代表真补了证据。这是那道闸自己的 fail-open。

根因在证据文件不稳定，所以在这里治：落盘前把这些字段替换成固定占位符。

**替换必须留痕**——文件里写 `"sku": "M0000000"` 而不说明，读者会以为我们真的发这个值，
那是拿假象换稳定，比 churn 更坏。故规范化后在文件里写一个 `_normalized` 块，逐条记
「哪个路径、替换成什么、为什么」。断言仍然跑在**原始快照**上，不受规范化影响。

**残留局限（不掩盖）**：本模块只处理调用方**显式声明**的路径。将来新增的证据写入点若忘了声明
某个易变字段，churn 会重新出现，闸也就重新可被 churn 满足。之所以不做「按模式自动识别时间戳/
序号」：那会在无人察觉时把真正有意义的值也抹掉——用静默的错换静默的错，不划算。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# 固定占位符：保留原值的**格式**（位宽、时区后缀），只抹掉取值，
# 这样「字段在不在、长什么样」这类证据信息仍然可读。
PLACEHOLDER_SEQ_SKU = "M0000000"
PLACEHOLDER_TIMESTAMP = "2026-01-01T00:00:00Z"


def _dig(obj: Any, path: tuple[str | int, ...]) -> tuple[Any, bool]:
    """按路径取值；取不到返回 (None, False)，**不抛异常也不静默当成功**。"""
    cur = obj
    for key in path:
        if isinstance(key, int):
            if not isinstance(cur, list) or not (-len(cur) <= key < len(cur)):
                return None, False
            cur = cur[key]
        else:
            if not isinstance(cur, dict) or key not in cur:
                return None, False
            cur = cur[key]
    return cur, True


def _put(obj: Any, path: tuple[str | int, ...], value: Any) -> None:
    parent, ok = _dig(obj, path[:-1])
    assert ok, f"路径不可达：{path}"
    parent[path[-1]] = value  # type: ignore[index]


def write_dry_run_evidence(
    out: Path,
    snapshot: dict[str, Any],
    *,
    volatile: dict[tuple[str | int, ...], tuple[str, str]],
) -> dict[str, Any]:
    """规范化后落盘，返回实际写出的对象（供调用方自查）。

    `volatile` 形如 `{("json_body", "MPItem", 0, "Orderable", "sku"): (占位符, 理由)}`。
    **声明了却不存在的路径会硬失败**——否则字段改名后这里静默失效、churn 悄悄回来，
    正是本模块要防的那类问题。
    """
    doc = json.loads(json.dumps(snapshot))  # 深拷贝，别改调用方手里的对象
    notes: list[dict[str, str]] = []
    for path, (placeholder, reason) in sorted(volatile.items(), key=lambda kv: str(kv[0])):
        _, ok = _dig(doc, path)
        assert ok, (
            f"声明为易变的路径在快照里不存在：{'.'.join(map(str, path))}。"
            "字段可能已改名——请同步更新声明，不要留着不管（留着就等于没规范化，churn 会回来）。"
        )
        _put(doc, path, placeholder)
        # **不要在这里记 sample_original**：首版记了原值，结果 `_normalized` 块自己每跑一遍就变，
        # 等于把要消除的 churn 原样搬了个位置——与本模块要修的是同一个形状，两跑对比时当场露馅。
        # 真实取值长什么样由 `reason` 用静态文字描述。
        notes.append(
            {"path": ".".join(map(str, path)), "replaced_with": placeholder, "reason": reason}
        )

    doc["_normalized"] = {
        "why": (
            "以下字段每次跑都不同但无证据价值，落盘前替换为固定占位符，"
            "使本文件的 diff 只反映请求形态变化（见 tests/db/_evidence.py 文件头）。"
            "断言跑在原始快照上，不受此处影响。"
        ),
        "fields": notes,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return doc
