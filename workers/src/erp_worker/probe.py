"""变体解析探针（R2-11 排障）：用 worker 真实抓取栈拉指定 ASIN，报告变体数据块形态指纹。

背景：真机出现「parentAsin 解析成功但 variant_attributes/variation_asins 全空」的页面形态
（twister 矩阵缺失或换壳），本探针用于定位 Amazon 页面到底长什么样——只打印统计、标记
存在性与首次出现处的短上下文切片（repr，防转义形态看不见），不打印整页；原始 HTML gzip
落容器 /tmp/probe-{asin}.html.gz 供后续 zgrep 切片取证。

用法（scraper 容器内）：
  python -m erp_worker.probe B0H2YXQRRX [更多 ASIN...]
"""

from __future__ import annotations

import asyncio
import gzip
import json
import re
import sys

from erp_worker.parser import AmazonParser
from erp_worker.proxy import ProxyManager
from erp_worker.session import AmazonSession

# 形态标记：twister 各已知载体 + 兜底正则素材 + 反爬页指纹
_MARKERS = [
    "dimensionValuesDisplayData",
    "dimensionValuesData",
    "dimensionsDisplay",
    '"dimensions"',
    "variationValues",
    "asinVariationValues",
    "twister-js-init",
    "inline-twister",
    "parentAsin",
    '"asin":"',
    "dimensionToAsinMap",
    "api-services-support@amazon.com",  # Robot Check 页指纹
    "Type the characters you see",  # captcha 页指纹
]
_CONTEXT = 150


def _report(asin: str, html: str) -> dict[str, object]:
    parser = AmazonParser()
    attrs, family = parser._parse_twister(html, asin)
    fallback = parser._parse_variation_asins(html, asin, parser._parse_parent_asin(html, asin))
    marks: dict[str, object] = {}
    for m in _MARKERS:
        n = html.count(m)
        entry: dict[str, object] = {"count": n}
        if n:
            i = html.index(m)
            entry["ctx"] = repr(html[max(0, i - 20) : i + _CONTEXT])
        marks[m] = entry
    title = re.search(r"<title>(.{0,80})", html)
    return {
        "asin": asin,
        "html_len": len(html),
        "title": title.group(1).strip() if title else None,
        "parse": {
            "parent_asin": parser._parse_parent_asin(html, asin),
            "variant_attributes": attrs,
            "twister_family": family,
            "fallback_variation_asins": fallback[:200],
        },
        "markers": marks,
    }


async def _main(asins: list[str]) -> int:
    session = AmazonSession(ProxyManager())
    ok = await session.initialize()
    if not ok:
        print("会话初始化失败（代理/首页不可达）", file=sys.stderr)
        return 2
    rc = 0
    for asin in asins:
        resp = await session.fetch_product_page(asin)
        if resp is None or resp.status_code != 200:
            code = getattr(resp, "status_code", None)
            print(f"[{asin}] 抓取失败 status={code}", file=sys.stderr)
            rc = 1
            continue
        html = resp.text
        path = f"/tmp/probe-{asin}.html.gz"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(html)
        report = _report(asin, html)
        report["html_dump"] = path
        print(json.dumps(report, ensure_ascii=False, indent=1))
    await session.close()
    return rc


def main() -> int:
    asins = [a.strip() for a in sys.argv[1:] if a.strip()]
    if not asins:
        print("用法：python -m erp_worker.probe <ASIN> [更多 ASIN...]", file=sys.stderr)
        return 2
    return asyncio.run(_main(asins))


if __name__ == "__main__":
    sys.exit(main())
