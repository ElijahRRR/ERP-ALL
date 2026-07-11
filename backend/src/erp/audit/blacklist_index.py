"""L2 R4 黑名单词匹配：纯 Python Aho-Corasick + 版本失效内存加载器。

背景（源仓 l2_rules.py 的 Aho-Corasick 设计移植）：
- 旧实现每次审核都 `SELECT brand_norm ...` 全量拉黑名单，再对每个品牌跑一次
  `(?<![a-z0-9])X(?![a-z0-9])` 正则——O(N brands) 的 DB + CPU 成本，36000+ 品牌时
  每条 ASIN ~数百毫秒。
- 本模块把黑名单编译成一个 Aho-Corasick 自动机，一次线性扫描 title/desc 找全部命中，
  并用一个「按 team scope 缓存 + 版本号失效」的加载器复用自动机，导入新增品牌后
  无需重启进程即可自动重建（版本变即懒重建）。

**行为保真（对齐 pipeline.run_l2 旧 R4）**：
- 命中集合来自「全局 team_id IS NULL + 本 team」的 active 黑名单，跳过占位符与
  「长度 < 4 且不含空格」的短单词（源仓纪律：短通用词误伤）。
- 词边界语义严格等价旧正则 `(?<![a-z0-9])<brand>(?![a-z0-9])`：命中前后字符不属于
  `[a-z0-9]` 才算数（AC 本身只找子串，边界需手动校验）。
- 命中按 brand_norm 升序返回（DB 为 C.UTF-8 collation，等价 Python 码点序 `sorted()`），
  以保证 L2→L3 命中词序稳定（L3 llm_cache key 依赖其顺序）。
- 归一化保持与 `_norm` 一致：品牌以 brand_norm 入库、文本由调用方 `_norm` 归一后传入，
  自动机两侧都已是「小写 + 单空格」，无需在此重复归一。
"""

from collections import deque

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession


def _is_word_char(c: str) -> bool:
    """等价旧正则的 `[a-z0-9]`：仅 ASCII 小写字母与数字算「词内字符」。

    注意：不含下划线、也不含大写/非 ASCII——与 `(?<![a-z0-9])`/`(?![a-z0-9])` 完全对齐
    （文本已由 `_norm` 小写化，实际不会出现大写）。
    """
    return ("a" <= c <= "z") or ("0" <= c <= "9")


class Automaton:
    """纯 Python Aho-Corasick 多模式匹配自动机（确定性、零第三方依赖）。

    - `add(word)` 建 trie；`build()` 用 BFS 计算 fail 链并沿 fail 链合并 output；
    - `find_matches(haystack)` 一次线性扫描，返回所有**词边界命中**的模式串集合。

    复杂度：构建 O(总模式长度)，匹配 O(文本长度 + 命中数)。
    """

    __slots__ = ("_fail", "_goto", "_out")

    def __init__(self) -> None:
        # 节点 0 = root。三个平行数组按节点 id 索引。
        self._goto: list[dict[str, int]] = [{}]
        self._fail: list[int] = [0]
        self._out: list[list[str]] = [[]]

    def add(self, word: str) -> None:
        """把一个模式串加入 trie（同一串重复 add 会在终点多存一次，find 端用 set 去重）。"""
        node = 0
        for ch in word:
            nxt = self._goto[node].get(ch)
            if nxt is None:
                nxt = len(self._goto)
                self._goto.append({})
                self._fail.append(0)
                self._out.append([])
                self._goto[node][ch] = nxt
            node = nxt
        self._out[node].append(word)

    def build(self) -> None:
        """BFS 构建 fail 链并把 fail 目标的 output 合并进来（保证子串命中不漏）。"""
        queue: deque[int] = deque()
        # 深度 1 的节点 fail 指向 root（默认已是 0），入队。
        for nxt in self._goto[0].values():
            queue.append(nxt)
        while queue:
            node = queue.popleft()
            for ch, nxt in self._goto[node].items():
                queue.append(nxt)
                f = self._fail[node]
                while f and ch not in self._goto[f]:
                    f = self._fail[f]
                target = self._goto[f].get(ch, 0)
                if target == nxt:  # root 自环兜底（深度 1 情形）
                    target = 0
                self._fail[nxt] = target
                # fail 目标 BFS 上更浅、已合并完毕；extend 即拿到其全部后缀命中。
                self._out[nxt].extend(self._out[target])

    def find_matches(self, haystack: str) -> set[str]:
        """扫描 haystack，返回所有在词边界上命中的模式串（去重）。

        边界语义严格等价 `(?<![a-z0-9])<word>(?![a-z0-9])`：只要某个模式串存在
        **任一** 满足前后边界的出现，即计入命中（与 `re.search` 的「存在性」一致）。
        """
        goto = self._goto
        fail = self._fail
        out = self._out
        matched: set[str] = set()
        n = len(haystack)
        node = 0
        for i, ch in enumerate(haystack):
            while node and ch not in goto[node]:
                node = fail[node]
            node = goto[node].get(ch, 0)
            bucket = out[node]
            if not bucket:
                continue
            for word in bucket:
                if word in matched:
                    continue
                start = i - len(word) + 1  # i 为命中末字符下标（inclusive）
                left_ok = start == 0 or not _is_word_char(haystack[start - 1])
                right_ok = i + 1 == n or not _is_word_char(haystack[i + 1])
                if left_ok and right_ok:
                    matched.add(word)
        return matched


# ── 版本失效内存加载器（按 team scope 缓存自动机）──

# 版本键：count + 最大 added_at；导入新增/移除品牌都会改变它 → 下次调用懒重建。
_VERSION_SQL = (
    "SELECT count(*)::text || ':' || COALESCE(max(added_at)::text, '')"
    " FROM app.blacklist_brand"
    " WHERE status = 'active' AND (team_id IS NULL OR team_id = :team)"
)
# 与旧 run_l2 完全一致的候选集：全局 + 本 team，按 brand_norm 升序。
_BRANDS_SQL = (
    "SELECT brand_norm FROM app.blacklist_brand"
    " WHERE status = 'active' AND (team_id IS NULL OR team_id = :team)"
    " ORDER BY brand_norm"
)

_MIN_LEN = 4  # 源仓纪律：长度 < 4 且不含空格的短单词不做子串扫（误伤通用词）

# 每个 team scope 缓存 (version, Automaton)。单进程 asyncio：并发缺失最多重复构建，
# 但每次都以刚读到的 DB 版本比对，版本变即重建，**绝不返回过期结果**（无需显式锁）。
_CACHE: dict[int, tuple[str, Automaton]] = {}


def clear_cache() -> None:
    """清空进程内自动机缓存（测试隔离用；生产无需调用，靠版本号自动失效）。"""
    _CACHE.clear()


async def _current_version(session: AsyncSession, team_id: int) -> str:
    row = (await session.execute(sql_text(_VERSION_SQL), {"team": team_id})).scalar_one()
    return str(row)


async def _build_automaton(session: AsyncSession, team_id: int) -> Automaton:
    # 延迟导入：pipeline 在模块顶层导入本模块，若在此顶层反向 import 会构成循环
    # （import 时 NON_BRAND_PLACEHOLDERS 尚未定义）。函数级导入在首次调用时才解析，安全。
    from erp.audit.pipeline import NON_BRAND_PLACEHOLDERS  # noqa: PLC0415

    rows = (await session.execute(sql_text(_BRANDS_SQL), {"team": team_id})).scalars()
    automaton = Automaton()
    for brand in rows:
        if brand in NON_BRAND_PLACEHOLDERS or (len(brand) < _MIN_LEN and " " not in brand):
            continue
        automaton.add(brand)
    automaton.build()
    return automaton


async def _get_automaton(session: AsyncSession, team_id: int) -> Automaton:
    version = await _current_version(session, team_id)
    cached = _CACHE.get(team_id)
    if cached is not None and cached[0] == version:
        return cached[1]
    automaton = await _build_automaton(session, team_id)
    _CACHE[team_id] = (version, automaton)
    return automaton


async def scan_blacklist(session: AsyncSession, *, team_id: int, text: str) -> list[dict[str, str]]:
    """R4 黑名单词扫描：返回按 brand_norm 升序的全部命中 `[{"brand": brand_norm}, ...]`。

    `text` 需为调用方 `_norm` 归一后的产品文本；命中上限（`[:10]`）由调用方在拼装
    evidence 时施加，本函数返回全部命中以保持与旧 run_l2 一致的语义。
    """
    if not text:
        return []
    automaton = await _get_automaton(session, team_id)
    matched = automaton.find_matches(text)
    return [{"brand": b} for b in sorted(matched)]
