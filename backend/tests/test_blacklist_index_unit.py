"""blacklist_index.Automaton 纯单元测试（无需 PostgreSQL，快速）。

覆盖 Aho-Corasick 词边界语义，严格等价旧正则 `(?<![a-z0-9])X(?![a-z0-9])`：
命中/非命中子串、多词短语、前缀/重叠模式、下划线作为边界、空输入。
"""

from erp.audit.blacklist_index import Automaton


def _build(*words: str) -> Automaton:
    ac = Automaton()
    for w in words:
        ac.add(w)
    ac.build()
    return ac


def test_word_boundary_hits_and_misses() -> None:
    ac = _build("nike")
    # 命中：前后是空格 / 标点 / 串首尾
    assert ac.find_matches("nike shoes") == {"nike"}
    assert ac.find_matches("a nike b") == {"nike"}
    assert ac.find_matches("nike") == {"nike"}
    assert ac.find_matches("buy nike.") == {"nike"}
    # 非命中：前/后被 [a-z0-9] 夹住（子串不算词）
    assert ac.find_matches("nikeish") == set()
    assert ac.find_matches("snike") == set()
    assert ac.find_matches("nike123") == set()
    assert ac.find_matches("1nike") == set()


def test_multiword_phrase() -> None:
    ac = _build("red bull")
    assert ac.find_matches("a red bull b") == {"red bull"}
    assert ac.find_matches("red bull") == {"red bull"}
    assert ac.find_matches("red bulls") == set()  # 右边界 's' 破坏
    assert ac.find_matches("red  bull") == set()  # 双空格 != 单空格短语（精确子串）


def test_overlapping_and_prefix_patterns() -> None:
    # 重叠：短语后缀恰是另一个模式（依赖 fail 链 output 合并才不漏）
    ac = _build("bull", "red bull")
    assert ac.find_matches("a red bull b") == {"bull", "red bull"}
    assert ac.find_matches("a bull b") == {"bull"}

    # 前缀：一个模式是另一个的前缀
    ac2 = _build("cat", "category")
    assert ac2.find_matches("cat") == {"cat"}
    assert ac2.find_matches("category") == {"category"}  # "cat" 被 'e' 破坏右边界
    assert ac2.find_matches("a cat and category b") == {"cat", "category"}


def test_underscore_is_boundary() -> None:
    # 旧正则字符类 [a-z0-9] 不含下划线 → '_' 视为词边界，命中成立
    ac = _build("nike")
    assert ac.find_matches("nike_shoes") == {"nike"}
    assert ac.find_matches("x_nike_y") == {"nike"}


def test_empty_and_no_match() -> None:
    ac = _build("nike")
    assert ac.find_matches("") == set()
    assert ac.find_matches("adidas only") == set()
    # 空自动机（无任何模式）永不命中
    empty = _build()
    assert empty.find_matches("anything at all here") == set()
