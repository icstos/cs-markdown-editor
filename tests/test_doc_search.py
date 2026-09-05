"""文档内搜索：匹配计算 + 行渲染装饰切分（纯函数单测）。

不启动 Flet 页面；compute_doc_matches 基于 parser 解析的 Document，
_decorate_search_hits 构造纯 ft.TextSpan 列表验证区间切分/着色。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flet as ft
import pytest

import parser
from views.doc_search import compute_doc_matches
from views.rendered_line import _decorate_search_hits


def _span(text: str) -> ft.TextSpan:
    return ft.TextSpan(text, style=ft.TextStyle(size=14, color="#111111"))


def test_compute_matches_basic():
    doc = parser.parse_markdown("# Hello\nworld hello")
    m = compute_doc_matches(doc, "hello", case_sensitive=False, regex=False)
    # 行 0 "# Hello" 命中 "Hello"（raw 2..7）；行 1 "world hello" 命中末尾
    assert (0, 2, 7) in m
    assert (1, 6, 11) in m
    assert m == sorted(m)


def test_compute_matches_case_sensitive():
    doc = parser.parse_markdown("# Hello\nhello")
    m = compute_doc_matches(doc, "hello", case_sensitive=True, regex=False)
    # "# Hello" 内含大写 Hello，不命中小写 hello → 仅第 2 行
    assert m == [(1, 0, 5)]


def test_compute_matches_regex_and_invalid():
    doc = parser.parse_markdown("abc abc\nxyz")
    m = compute_doc_matches(doc, r"a.c", case_sensitive=False, regex=True)
    assert len(m) == 2
    # 非法正则 → 无匹配（不抛异常）
    assert compute_doc_matches(doc, "[", case_sensitive=False, regex=True) == []
    # 空查询 → 无匹配
    assert compute_doc_matches(doc, "  ", case_sensitive=False, regex=False) == []


def test_decorate_slices_and_colors():
    # flat text = "hello world"，两个 span：text_a="hello"，text_b=" world"
    spans = [_span("hello"), _span(" world")]
    # raw 与 flat 同构（无折叠标记），len=12
    raw_to_flat = list(range(12))
    hits = [(1, 4, False), (6, 11, True)]  # "ell" 普通 + "world" 当前
    out = _decorate_search_hits(spans, raw_to_flat, hits, "#FFE082", "#FFB300")
    joined = "".join(s.text for s in out)
    assert joined == "hello world"  # 切分不改文本
    colored = [s for s in out if s.style.bgcolor is not None]
    assert any(s.text == "world" and s.style.bgcolor == "#FFB300" for s in colored)
    assert any(s.text == "ell" and s.style.bgcolor == "#FFE082" for s in colored)
    plain = "".join(s.text for s in out if s.style.bgcolor is None)
    assert plain == "ho "  # 未命中部分保持无色且不丢字


def test_decorate_empty_or_zero_width_safe():
    spans = [_span("abc")]
    raw_to_flat = list(range(4))
    # 零宽命中（折叠退化）与无命中都不应炸、不丢字
    out = _decorate_search_hits(spans, raw_to_flat, [], "#FFE082", "#FFB300")
    assert [s.text for s in out] == ["abc"]
    out2 = _decorate_search_hits(
        spans, raw_to_flat, [(2, 2, True)], "#FFE082", "#FFB300"
    )
    assert "".join(s.text for s in out2) == "abc"
    assert all(s.style.bgcolor is None for s in out2)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
