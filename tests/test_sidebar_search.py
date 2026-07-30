"""侧边栏搜索增强功能测试。

覆盖：
- views.sidebar._build_query_regex：4 选项组合编译正则
  （普通子串/大小写/整词/正则/无效正则）
- views.sidebar._match_lines：行级匹配返回 offset+长度，多匹配行返回多区间
- views.sidebar._search_in_file：单文件搜索边界（超大文件跳过/读取失败/超长行）
- views.sidebar._collect_md_paths：嵌套树扁平化提取 .md 路径
- views.sidebar._build_preview_spans：高亮预览 spans 构造
- 跳转链路向后兼容：jump_to(li, off=None) 退化为 off=0

不依赖 UI 渲染，用 MagicMock 注入 ctx，纯函数直接调用验证返回结构。
jump_to 测试 mock ft.context.page 避免无 Flet 上下文报错。
"""

import os
import re
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from views.sidebar import (
    _build_preview_spans,
    _build_query_regex,
    _collect_md_paths,
    _match_lines,
    _search_in_file,
)
from views.editor._scroll import build_scroll


# ---- _build_query_regex：4 选项组合 ----


def test_build_regex_plain_substring():
    """普通子串：re.escape 生效，'a.b' 不被当模式。"""
    p = _build_query_regex("a.b", False, False, False)
    assert p is not None
    assert p.search("xa.by") is not None  # 字面量匹配
    assert p.search("aXb") is None         # '.' 不当通配符


def test_build_regex_case_insensitive_default():
    """默认大小写不敏感：'foo' 匹配 'FOO'。"""
    p = _build_query_regex("foo", False, False, False)
    assert p.search("FOO bar") is not None


def test_build_regex_case_sensitive():
    """区分大小写：'Foo' 不匹配 'foo'。"""
    p = _build_query_regex("Foo", True, False, False)
    assert p.search("Foo bar") is not None
    assert p.search("foo bar") is None


def test_build_regex_whole_word():
    """整词匹配：'cat' 不匹配 'category'。"""
    p = _build_query_regex("cat", False, True, False)
    assert p.search("a cat here") is not None
    assert p.search("category") is None
    assert p.search("concat") is None


def test_build_regex_regex_mode():
    """正则模式：'\\d+' 匹配数字。"""
    p = _build_query_regex(r"\d+", False, False, True)
    assert p.search("abc 123 xyz") is not None
    assert p.search("abc xyz") is None


def test_build_regex_regex_with_whole_word():
    """正则 + 整词：模式被 \\b 包裹。"""
    p = _build_query_regex(r"cat|dog", False, True, True)
    assert p.search("I have a cat") is not None
    assert p.search("I have a dog") is not None
    # 'category' 不应匹配 'cat'（整词边界）
    assert p.search("category") is None


def test_build_regex_invalid_regex_returns_none():
    """无效正则返回 None（调用方提示）。"""
    assert _build_query_regex("[", False, False, True) is None
    assert _build_query_regex("*", False, False, True) is None
    assert _build_query_regex("(?P<", False, False, True) is None


def test_build_regex_empty_query_returns_none():
    """空查询返回 None。"""
    assert _build_query_regex("", False, False, False) is None
    assert _build_query_regex("   ", False, False, False) is None


# ---- _match_lines：行级匹配 ----


def _make_doc(lines_raw):
    """构造简易 Document mock：lines[i].raw = lines_raw[i]。"""
    doc = MagicMock()
    doc.lines = [types.SimpleNamespace(raw=r) for r in lines_raw]
    return doc


def test_match_lines_returns_offset_and_length():
    """返回结构携带 offset+长度（供高亮与跳转）。"""
    doc = _make_doc(["hello world hello", "no match", "hello again"])
    p = _build_query_regex("hello", False, False, False)
    results = _match_lines(doc, p)
    # 行 0 和行 2 有匹配
    assert len(results) == 2
    li0, matches0 = results[0]
    assert li0 == 0
    # 行 0 有两个 'hello'：位置 0 和 12
    assert matches0 == [(0, 5), (12, 17)]
    li2, matches2 = results[1]
    assert li2 == 2
    assert matches2 == [(0, 5)]


def test_match_lines_pattern_none_returns_empty():
    """pattern 为 None 时返回 []。"""
    doc = _make_doc(["hello"])
    assert _match_lines(doc, None) == []


def test_match_lines_document_none_returns_empty():
    """document 为 None 时返回 []。"""
    p = _build_query_regex("foo", False, False, False)
    assert _match_lines(None, p) == []


def test_match_lines_respects_limit():
    """limit 截断结果数。"""
    doc = _make_doc([f"line {i} match" for i in range(100)])
    p = _build_query_regex("match", False, False, False)
    results = _match_lines(doc, p, limit=5)
    assert len(results) == 5


# ---- _search_in_file：单文件搜索边界 ----


def test_search_in_file_basic(tmp_path):
    """正常文件按行匹配。"""
    f = tmp_path / "note.md"
    f.write_text("hello world\nno match\nhello again", encoding="utf-8")
    p = _build_query_regex("hello", False, False, False)
    results = _search_in_file(str(f), p)
    assert len(results) == 2
    assert results[0][0] == 0  # 行 0
    assert results[1][0] == 2  # 行 2


def test_search_in_file_skips_oversized(tmp_path):
    """超大文件（> _MAX_FILE_SIZE）跳过，返回 []。"""
    from views.sidebar import _MAX_FILE_SIZE
    f = tmp_path / "big.md"
    f.write_text("x" * (_MAX_FILE_SIZE + 100), encoding="utf-8")
    p = _build_query_regex("x", False, False, False)
    assert _search_in_file(str(f), p) == []


def test_search_in_file_read_failure_returns_empty(tmp_path):
    """读取失败（文件不存在）返回 []，不抛异常。"""
    p = _build_query_regex("foo", False, False, False)
    assert _search_in_file(str(tmp_path / "nonexistent.md"), p) == []


def test_search_in_file_respects_max_per_file(tmp_path):
    """每文件结果上限。"""
    from views.sidebar import _MAX_PER_FILE
    f = tmp_path / "many.md"
    f.write_text("\n".join(f"match {i}" for i in range(_MAX_PER_FILE + 10)), encoding="utf-8")
    p = _build_query_regex("match", False, False, False)
    results = _search_in_file(str(f), p)
    assert len(results) == _MAX_PER_FILE


# ---- _collect_md_paths：嵌套树扁平化 ----


def test_collect_md_paths_flattens_tree():
    """嵌套树扁平化提取所有 .md 文件路径（深度优先）。"""
    tree = [
        ("dir", "docs", [
            ("file", "a.md", "/abs/docs/a.md"),
            ("dir", "sub", [
                ("file", "b.md", "/abs/docs/sub/b.md"),
            ]),
        ]),
        ("file", "c.md", "/abs/c.md"),
    ]
    paths = _collect_md_paths(tree)
    assert paths == ["/abs/docs/a.md", "/abs/docs/sub/b.md", "/abs/c.md"]


def test_collect_md_paths_empty_tree():
    """空树返回 []。"""
    assert _collect_md_paths([]) == []


# ---- _build_preview_spans：高亮预览 ----


def _make_colors():
    """简易 Colors mock。"""
    return types.SimpleNamespace(
        text="#1F2329", muted="#8A919E",
        search_match_bg="#FFE082", search_match_fg="#1F2329",
    )


def test_build_preview_spans_creates_match_spans():
    """匹配段生成带 bgcolor 的 TextSpan。"""
    import flet as ft
    c = _make_colors()
    raw = "hello world hello"
    matches = [(0, 5), (12, 17)]
    text = _build_preview_spans(raw, matches, c)
    assert isinstance(text, ft.Text)
    assert text.spans is not None
    # 至少有匹配 span（带 bgcolor）
    match_spans = [s for s in text.spans if s.style.bgcolor == c.search_match_bg]
    assert len(match_spans) >= 1


def test_build_preview_spans_adds_ellipsis_on_truncation():
    """截断时加 … 前缀/后缀。"""
    c = _make_colors()
    # 长文本，匹配在中间，前后都应截断
    raw = "x" * 100 + "target" + "y" * 100
    matches = [(100, 106)]
    text = _build_preview_spans(raw, matches, c, radius=10)
    # 第一个 span 应是 "…" 前缀
    assert text.spans[0].text == "…"
    # 最后一个 span 应是 "…" 后缀
    assert text.spans[-1].text == "…"


def test_build_preview_spans_no_match_fallback():
    """无匹配时退化展示前若干字符。"""
    import flet as ft
    c = _make_colors()
    text = _build_preview_spans("some text", [], c)
    assert isinstance(text, ft.Text)
    # 无 spans（用 value）
    assert text.value is not None


def test_build_preview_spans_empty_raw():
    """空 raw 返回空 Text。"""
    import flet as ft
    c = _make_colors()
    text = _build_preview_spans("", [(0, 0)], c)
    assert isinstance(text, ft.Text)


# ---- 跳转链路向后兼容：jump_to(li, off=None) ----


@patch("views.editor._scroll.ft")
def test_jump_to_off_none_uses_zero(mock_ft):
    """off=None 退化为 off=0（向后兼容大纲等仅传 li 的调用方）。"""
    from models import BlockType
    ctx = MagicMock()
    line = MagicMock()
    line.block_type = BlockType.PARAGRAPH  # 非围栏块
    ctx.document.lines = [line]
    scroll_cbs = build_scroll(ctx)
    scroll_cbs["jump_to"](0)
    ctx.set_cursor.assert_called_once_with(0, 0)


@patch("views.editor._scroll.ft")
def test_jump_to_with_off_uses_provided_offset(mock_ft):
    """off=int 时跳到精确 raw 偏移。"""
    from models import BlockType
    ctx = MagicMock()
    line = MagicMock()
    line.block_type = BlockType.PARAGRAPH
    ctx.document.lines = [line]
    scroll_cbs = build_scroll(ctx)
    scroll_cbs["jump_to"](0, 5)
    ctx.set_cursor.assert_called_once_with(0, 5)


@patch("views.editor._scroll.ft")
def test_jump_to_fence_falls_back_to_browse_mode(mock_ft):
    """围栏块 fallback 到浏览态（set_cursor_line + set_cursor_li(None)）。"""
    from models import BlockType
    ctx = MagicMock()
    line = MagicMock()
    line.block_type = BlockType.CODE  # 围栏块
    ctx.document.lines = [line]
    scroll_cbs = build_scroll(ctx)
    scroll_cbs["jump_to"](0, 5)
    ctx.set_cursor_line.assert_called_once_with(0)
    ctx.set_cursor_li.assert_called_once_with(None)
    ctx.set_cursor.assert_not_called()


@patch("views.editor._scroll.ft")
def test_jump_to_out_of_range_li_does_nothing(mock_ft):
    """越界 li 静默返回。"""
    ctx = MagicMock()
    ctx.document.lines = []
    scroll_cbs = build_scroll(ctx)
    scroll_cbs["jump_to"](99, 5)
    ctx.set_cursor.assert_not_called()
    ctx.set_cursor_line.assert_not_called()
