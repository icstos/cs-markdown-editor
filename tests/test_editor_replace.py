"""编辑器替换闭包组测试（views/editor/_replace.py）。

覆盖：
- replace_match_in_doc：单个匹配替换 → line.raw 更新 + reparse + push_history 1 次 + mark_dirty
- replace_all_in_doc：批量替换 → 多行多匹配 + 行内右→左 + push_history 仅 1 次 + mark_dirty
- restoring=True 守卫：撤销/重做中不替换不入栈

用 types.SimpleNamespace 构造最小 mock ctx，Document(lines=[...]) 构造真实文档，
验证替换后 line.raw 正确更新（reparse_line_atomic 原地重解析）。
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from models import BlockType, Document, Line, Segment, SegType  # noqa: E402
from views.editor._replace import build_replace  # noqa: E402


class FakeRef:
    """最小 ft.Ref 桩：仅 .current 读写。"""
    def __init__(self, current):
        self.current = current


def _para_line(raw: str) -> Line:
    """构造段落行：单 TEXT 段，raw 即文本。"""
    line = Line(block_type=BlockType.PARAGRAPH, raw=raw)
    line.segments = [Segment(SegType.TEXT, raw, raw)]
    return line


def _make_ctx(document: Document) -> tuple[types.SimpleNamespace, list]:
    """构造最小 mock EditorContext，仅含 replace 闭包依赖的槽。

    返回 (ctx, calls) calls 记录 push_history / mark_dirty 调用。
    """
    calls: list = []
    ctx = types.SimpleNamespace(
        document=document,
        restoring=FakeRef(False),
        undo_push_pending=FakeRef(True),
        push_history=lambda: calls.append("push_history"),
        mark_dirty=lambda: calls.append("mark_dirty"),
    )
    return ctx, calls


# ---- replace_match_in_doc ----


def test_replace_match_in_doc_basic():
    """单个匹配替换：line.raw 更新 + push_history 1 次 + mark_dirty。"""
    doc = Document(lines=[_para_line("hello world")])
    ctx, calls = _make_ctx(doc)
    cbs = build_replace(ctx)

    # 替换 "world" → "flet"
    cbs["replace_match_in_doc"](0, 6, 11, "flet")

    assert doc.lines[0].raw == "hello flet"
    assert calls.count("push_history") == 1
    assert calls.count("mark_dirty") == 1


def test_replace_match_in_doc_multiple_in_line():
    """同一行替换一个匹配后另一个匹配偏移不变（仅替换单个）。"""
    doc = Document(lines=[_para_line("foo bar foo")])
    ctx, calls = _make_ctx(doc)
    cbs = build_replace(ctx)

    # 替换第一个 "foo"（0..3）→ "XXX"
    cbs["replace_match_in_doc"](0, 0, 3, "XXX")

    assert doc.lines[0].raw == "XXX bar foo"
    assert calls.count("push_history") == 1


def test_replace_match_in_doc_invalid_line():
    """无效行号：静默返回，无副作用。"""
    doc = Document(lines=[_para_line("hello")])
    ctx, calls = _make_ctx(doc)
    cbs = build_replace(ctx)

    cbs["replace_match_in_doc"](99, 0, 3, "XXX")
    assert doc.lines[0].raw == "hello"
    assert calls == []


def test_replace_match_in_doc_invalid_offsets():
    """无效偏移（end > len(raw)）：静默返回。"""
    doc = Document(lines=[_para_line("hi")])
    ctx, calls = _make_ctx(doc)
    cbs = build_replace(ctx)

    cbs["replace_match_in_doc"](0, 0, 100, "XXX")
    assert doc.lines[0].raw == "hi"
    assert calls == []


def test_replace_match_in_doc_restoring_guard():
    """restoring=True 时不替换不入栈。"""
    doc = Document(lines=[_para_line("hello world")])
    ctx, calls = _make_ctx(doc)
    ctx.restoring.current = True
    cbs = build_replace(ctx)

    cbs["replace_match_in_doc"](0, 0, 5, "XXX")
    assert doc.lines[0].raw == "hello world"
    assert calls == []


def test_replace_match_in_doc_reparse_segments():
    """替换后 segments 正确重解析（reparse_line_atomic 原地更新）。"""
    doc = Document(lines=[_para_line("hello world")])
    ctx, calls = _make_ctx(doc)
    cbs = build_replace(ctx)

    cbs["replace_match_in_doc"](0, 0, 5, "HELLO")
    assert doc.lines[0].raw == "HELLO world"
    # segments 应重解析为新文本
    text = "".join(s.text for s in doc.lines[0].segments)
    assert "HELLO" in text


# ---- replace_all_in_doc ----


def test_replace_all_in_doc_single_line_multi():
    """单行多匹配批量替换：右→左保偏移。"""
    doc = Document(lines=[_para_line("foo bar foo baz foo")])
    ctx, calls = _make_ctx(doc)
    cbs = build_replace(ctx)

    # 三个匹配：(0,3), (8,11), (16,19)
    replacements = [(0, [(0, 3, "AAA"), (8, 11, "BBB"), (16, 19, "CCC")])]
    count = cbs["replace_all_in_doc"](replacements)

    assert doc.lines[0].raw == "AAA bar BBB baz CCC"
    assert count == 3
    assert calls.count("push_history") == 1  # 批量仅 1 次全文快照
    assert calls.count("mark_dirty") == 1


def test_replace_all_in_doc_multi_line():
    """多行批量替换。"""
    doc = Document(lines=[
        _para_line("foo bar"),
        _para_line("baz foo"),
        _para_line("qux"),
    ])
    ctx, calls = _make_ctx(doc)
    cbs = build_replace(ctx)

    replacements = [
        (0, [(0, 3, "AAA")]),
        (1, [(4, 7, "BBB")]),
    ]
    count = cbs["replace_all_in_doc"](replacements)

    assert doc.lines[0].raw == "AAA bar"
    assert doc.lines[1].raw == "baz BBB"
    assert doc.lines[2].raw == "qux"
    assert count == 2
    assert calls.count("push_history") == 1


def test_replace_all_in_doc_right_to_left():
    """行内右→左验证：替换文本长度不同时偏移正确。"""
    doc = Document(lines=[_para_line("aXa Xa aXa")])
    ctx, calls = _make_ctx(doc)
    cbs = build_replace(ctx)

    # 匹配 "X" → "YYYY"（变长）
    # 位置: 1, 4, 8
    replacements = [(0, [(1, 2, "YYYY"), (4, 5, "YYYY"), (8, 9, "YYYY")])]
    count = cbs["replace_all_in_doc"](replacements)

    assert doc.lines[0].raw == "aYYYYa YYYYa aYYYYa"
    assert count == 3


def test_replace_all_in_doc_empty_replacements():
    """空 replacements 列表：返回 0，不 push_history。"""
    doc = Document(lines=[_para_line("hello")])
    ctx, calls = _make_ctx(doc)
    cbs = build_replace(ctx)

    count = cbs["replace_all_in_doc"]([])
    assert count == 0
    assert calls == []


def test_replace_all_in_doc_restoring_guard():
    """restoring=True 时不替换不入栈。"""
    doc = Document(lines=[_para_line("foo bar")])
    ctx, calls = _make_ctx(doc)
    ctx.restoring.current = True
    cbs = build_replace(ctx)

    count = cbs["replace_all_in_doc"]([(0, [(0, 3, "XXX")])])
    assert count == 0
    assert doc.lines[0].raw == "foo bar"
    assert calls == []


def test_replace_all_in_doc_invalid_line_skipped():
    """无效行号跳过，不影响其他行。"""
    doc = Document(lines=[_para_line("foo")])
    ctx, calls = _make_ctx(doc)
    cbs = build_replace(ctx)

    replacements = [
        (99, [(0, 3, "XXX")]),  # 无效行
        (0, [(0, 3, "AAA")]),   # 有效行
    ]
    count = cbs["replace_all_in_doc"](replacements)

    assert doc.lines[0].raw == "AAA"
    assert count == 1


def test_replace_all_in_doc_invalid_span_skipped():
    """无效偏移跳过，有效偏移仍替换。"""
    doc = Document(lines=[_para_line("foo")])
    ctx, calls = _make_ctx(doc)
    cbs = build_replace(ctx)

    # (0,3) 有效, (5,10) 无效（越界）
    replacements = [(0, [(5, 10, "XXX"), (0, 3, "AAA")])]
    count = cbs["replace_all_in_doc"](replacements)

    assert doc.lines[0].raw == "AAA"
    assert count == 1


def test_replace_all_in_doc_reparse_segments():
    """批量替换后 segments 正确重解析。"""
    doc = Document(lines=[_para_line("foo bar foo")])
    ctx, calls = _make_ctx(doc)
    cbs = build_replace(ctx)

    replacements = [(0, [(0, 3, "AAA"), (8, 11, "BBB")])]
    cbs["replace_all_in_doc"](replacements)

    assert doc.lines[0].raw == "AAA bar BBB"
    text = "".join(s.text for s in doc.lines[0].segments)
    assert "AAA" in text
    assert "BBB" in text
