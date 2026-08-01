"""侧边栏替换纯函数测试。

覆盖：
- _flatten_matches：当前文档搜索结果扁平化
- _flatten_cross_matches：跨文件搜索结果扁平化
- _expand_replacement：regex 反向引用展开 + 非 regex 字面量
- _replace_in_string：行内右→左多匹配替换保偏移
- _replace_in_file_text：跨文件切行替换 + regex 反向引用
- _find_match_at：按 (start, end) 精确定位 Match 对象

不依赖 UI 渲染，纯函数直接调用验证返回结构。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from views.sidebar import (  # noqa: E402
    _expand_replacement,
    _find_match_at,
    _flatten_cross_matches,
    _flatten_matches,
    _replace_in_file_text,
    _replace_in_string,
)


# ---- _flatten_matches ----


def test_flatten_matches_single_per_line():
    """每行单匹配：扁平化为 [(li, s, e), ...]。"""
    results = [(0, [(3, 6)]), (2, [(0, 4)])]
    flat = _flatten_matches(results)
    assert flat == [(0, 3, 6), (2, 0, 4)]


def test_flatten_matches_multi_per_line():
    """一行多匹配：保持行内顺序展开。"""
    results = [(1, [(0, 3), (5, 8), (10, 13)])]
    flat = _flatten_matches(results)
    assert flat == [(1, 0, 3), (1, 5, 8), (1, 10, 13)]


def test_flatten_matches_empty():
    """空结果返回空列表。"""
    assert _flatten_matches([]) == []


# ---- _flatten_cross_matches ----


def test_flatten_cross_matches_basic():
    """跨文件结果扁平化：[(path, li, s, e), ...]。"""
    cross = [
        ("/a.md", "a.md", [(0, [(2, 5)]), (3, [(0, 4), (6, 9)])]),
        ("/b.md", "b.md", [(1, [(1, 3)])]),
    ]
    flat = _flatten_cross_matches(cross)
    assert flat == [
        ("/a.md", 0, 2, 5),
        ("/a.md", 3, 0, 4),
        ("/a.md", 3, 6, 9),
        ("/b.md", 1, 1, 3),
    ]


def test_flatten_cross_matches_empty():
    assert _flatten_cross_matches([]) == []


# ---- _expand_replacement ----


def test_expand_replacement_regex_backref_dollar():
    r"""regex 模式：$1/$2 VSCode 风格反向引用展开。"""
    p = __import__("re").compile(r"(\w+)@(\w+)")
    m = p.search("user@host")
    assert m is not None
    result = _expand_replacement(m, r"$2/$1", True)
    assert result == "host/user"


def test_expand_replacement_regex_backref_backslash():
    r"""regex 模式：\1 Python 风格反向引用展开（与 $N 并存）。"""
    p = __import__("re").compile(r"(\w+)@(\w+)")
    m = p.search("user@host")
    assert m is not None
    result = _expand_replacement(m, r"\2/\1", True)
    assert result == "host/user"


def test_expand_replacement_regex_backref_named_group():
    r"""regex 模式：\g<1> 命名反向引用展开。"""
    p = __import__("re").compile(r"(\w+)@(\w+)")
    m = p.search("user@host")
    assert m is not None
    result = _expand_replacement(m, r"\g<2>/\g<1>", True)
    assert result == "host/user"


def test_expand_replacement_regex_dollar_dollar_literal():
    r"""regex 模式：$$ → 字面量 $（VSCode 风格转义）。"""
    p = __import__("re").compile(r"price")
    m = p.search("price is 100")
    assert m is not None
    result = _expand_replacement(m, "$$100", True)
    assert result == "$100"


def test_expand_replacement_regex_dollar_nondigit_literal():
    r"""regex 模式：$ 后非数字 → 字面量 $（如 $abc）。"""
    p = __import__("re").compile(r"var")
    m = p.search("var here")
    assert m is not None
    result = _expand_replacement(m, "$abc", True)
    assert result == "$abc"


def test_expand_replacement_non_regex_literal():
    r"""非 regex 模式：\1 为字面量，不展开。"""
    p = __import__("re").compile(r"foo")
    m = p.search("foobar")
    assert m is not None
    result = _expand_replacement(m, r"\1bar", False)
    assert result == r"\1bar"


def test_expand_replacement_non_regex_dollar_literal():
    """非 regex 模式：$ 为字面量。"""
    p = __import__("re").compile(r"price")
    m = p.search("price is 100")
    assert m is not None
    result = _expand_replacement(m, "$100", False)
    assert result == "$100"


def test_expand_replacement_non_regex_backslash_literal():
    r"""非 regex 模式：\n 为字面量（不做转义）。"""
    p = __import__("re").compile(r"text")
    m = p.search("text here")
    assert m is not None
    result = _expand_replacement(m, r"\n", False)
    assert result == r"\n"


# ---- _replace_in_string ----


def test_replace_in_string_single_match():
    """单个匹配替换。"""
    import re
    p = re.compile(r"foo")
    raw = "hello foo world"
    new_raw, count = _replace_in_string(raw, p, [(6, 9)], "bar", False)
    assert new_raw == "hello bar world"
    assert count == 1


def test_replace_in_string_multi_match_right_to_left():
    """行内多匹配右→左处理，偏移正确无错位。"""
    import re
    p = re.compile(r"aa")
    raw = "aa bb aa cc aa"
    # 三个匹配：(0,2), (6,8), (12,14)
    spans = [(0, 2), (6, 8), (12, 14)]
    new_raw, count = _replace_in_string(raw, p, spans, "XX", False)
    assert new_raw == "XX bb XX cc XX"
    assert count == 3


def test_replace_in_string_regex_backref():
    """regex 模式 VSCode 风格 $N 反向引用展开。"""
    import re
    p = re.compile(r"(\w+)=(\w+)")
    raw = "key=value foo=bar"
    spans = [(m.start(), m.end()) for m in p.finditer(raw)]
    new_raw, count = _replace_in_string(raw, p, spans, r"$2:$1", True)
    assert new_raw == "value:key bar:foo"
    assert count == 2


def test_replace_in_string_empty_spans():
    """空 spans 返回原文本。"""
    import re
    p = re.compile(r"foo")
    new_raw, count = _replace_in_string("hello", p, [], "bar", False)
    assert new_raw == "hello"
    assert count == 0


def test_replace_in_string_replacement_longer():
    """替换文本比匹配长时偏移仍正确（右→左保证）。"""
    import re
    p = re.compile(r"x")
    raw = "x x x"
    spans = [(0, 1), (2, 3), (4, 5)]
    new_raw, count = _replace_in_string(raw, p, spans, "ABC", False)
    assert new_raw == "ABC ABC ABC"
    assert count == 3


def test_replace_in_string_replacement_shorter():
    """替换文本比匹配短时偏移仍正确。"""
    import re
    p = re.compile(r"ABC")
    raw = "ABC ABC ABC"
    spans = [(0, 3), (4, 7), (8, 11)]
    new_raw, count = _replace_in_string(raw, p, spans, "x", False)
    assert new_raw == "x x x"
    assert count == 3


# ---- _replace_in_file_text ----


def test_replace_in_file_text_basic():
    """跨文件文本替换：按 \\n 切行逐行替换。"""
    import re
    p = re.compile(r"foo")
    text = "foo bar\nbaz foo\nqux"
    new_text, count = _replace_in_file_text(text, p, "XXX", False)
    assert new_text == "XXX bar\nbaz XXX\nqux"
    assert count == 2


def test_replace_in_file_text_regex_backref():
    """跨文件 regex 反向引用展开。"""
    import re
    p = re.compile(r"(\w+)@(\w+)")
    text = "user@host\nadmin@server"
    new_text, count = _replace_in_file_text(text, p, r"$2/$1", True)
    assert new_text == "host/user\nserver/admin"
    assert count == 2


def test_replace_in_file_text_no_match():
    """无匹配返回原文本。"""
    import re
    p = re.compile(r"xyz")
    text = "foo bar\nbaz"
    new_text, count = _replace_in_file_text(text, p, "QQQ", False)
    assert new_text == "foo bar\nbaz"
    assert count == 0


def test_replace_in_file_text_multi_per_line():
    """一行多匹配全部替换。"""
    import re
    p = re.compile(r"a")
    text = "banana"
    new_text, count = _replace_in_file_text(text, p, "X", False)
    assert new_text == "bXnXnX"
    assert count == 3


# ---- _find_match_at ----


def test_find_match_at_exact():
    """精确匹配 (start, end) 的 Match 对象。"""
    import re
    p = re.compile(r"(\w+)")
    raw = "hello world"
    m = _find_match_at(p, raw, 6, 11)
    assert m is not None
    assert m.group(1) == "world"


def test_find_match_at_not_found():
    """未找到匹配返回 None。"""
    import re
    p = re.compile(r"(\w+)")
    raw = "hello world"
    m = _find_match_at(p, raw, 0, 5)
    assert m is not None
    assert m.group(1) == "hello"
    # 不存在的区间
    m = _find_match_at(p, raw, 3, 7)
    assert m is None
