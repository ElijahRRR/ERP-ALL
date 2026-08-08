"""17 条渠道失败原文 → `exception_kind` 归类表（R2-13 13d）。

## 出处与性质

表内每一条子串都取自厂商插件**生产实测的 17 条 `failContent`**
（`.agent/evidence/R2-13/owner-reverse-engineering-20260730.md:305-325`），归类口径为
图纸 `07:154-166`（异常六类 + 三特例）。

**这是渠道原文的归类表，不是业务参数，故不进配置中心**（CLAUDE.md 禁区那条针对的是
「阈值、开关、口径」这类会随经营变的量）。它随插件版本变——插件改了文案就得同 PR 改
这张表，配置中心里的一份反而会悄悄与代码里的匹配逻辑脱节。

## 匹配规则

- **子串匹配**：原文含 `${asin}` / `${N}` 等模板变量（如 `商品 ${asin} 配送方式非 FBA`），
  等值比对必然全部落空。
- **有序，先匹配先赢**：靠前的更具体。调整顺序前先想清楚会不会把某条原文抢走。
- ⚠️ **匹配串逐字照抄一手来源，不要自行规范化空格**（`配送方式非 FBA` 的那个半角空格
  就是原文）。fork 版插件若改了文案，**同 PR 改这张表**——否则新文案静默落进 `other`，
  归类表看起来还在工作，实际已经失灵。

## 兜底 `other` 不是失败

第 17 条 `处理商品失败: ${error.message}` 本身就是厂商的通用兜底，落 `other` 是正解。
`exception_reason` 一律保留原文，运营看得到真正发生了什么；归类只决定**谁该被叫醒**。
"""

# (子串, exception_kind)。顺序即优先级。
_FAIL_PATTERNS: tuple[tuple[str, str], ...] = (
    # ── address：地址填写/匹配环节（07:156）──
    ("地址保存超时", "address"),
    ("地址列表加载超时", "address"),
    ("地址信息不完整", "address"),  # JP：缺少区相关信息
    ("未匹配到州信息", "address"),  # 13e fork：文案「洲→州」修正后的现行值
    ("未匹配到洲信息", "address"),  # 旧文案，留作向后兼容（在途/历史回传仍可能带它）
    # ── stock：渠道侧缺货（07:157）──
    ("商品无库存", "stock"),
    ("商品库存不足或已售罄", "stock"),
    ("未找到加入购物车按钮", "stock"),
    ("加入购物车失败", "stock"),
    # ── product：业务规则不可自动采购（07:158；非 FBA / bundle / 数量不可改）──
    ("配送方式非 FBA", "product"),
    ("捆绑商品", "product"),
    ("无法修改商品购买数量", "product"),
    # ── delivery：交期（07:159）──
    ("预计送达时间超过", "delivery"),
    ("预计送达时间解析失败", "delivery"),
    # ── checkout：结账/下单环节（07:160）──
    # 「验证失败」排在「下单验证超时」之前无妨——两条同类，先中哪条都是 checkout。
    ("验证失败", "checkout"),
    ("下单验证超时", "checkout"),
    ("回传订单失败", "checkout"),
)

# 落 critical 而不是 warn 的类：**业务规则失败**（非 FBA / bundle / 数量不可改）需要有人
# 换 ASIN 或转人工，光记一条 warn 会一直堆在那里，那张单永远不会被处置。
CRITICAL_KINDS = frozenset({"product"})


def classify_failure(reason: str | None) -> str:
    """渠道失败原文 → `exception_kind`（九值词表之一）。未命中回 `other`。"""
    text = reason or ""
    for needle, kind in _FAIL_PATTERNS:
        if needle in text:
            return kind
    return "other"
