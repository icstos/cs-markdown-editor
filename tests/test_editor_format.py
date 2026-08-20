"""全文 Markdown 格式化动作（Shift+Alt+F）测试。

直接构造最小 mock ctx 调用 build_format(ctx)["format_document"]，验证：
- 格式化有变化：推入全文撤销快照、重建 document.lines、mark_dirty、退出编辑态
- 内容已规范：无变化时不推历史、不重建
- 原文模式（raw_mode）：格式化 raw_draft
- restoring 期间：不执行
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.history import EditHistory
from models import BlockType, Document, Line, Segment, SegType
from views.editor._format import build_format


class FakeRef:
    def __init__(self, current=None):
        self.current = current


def _para(raw: str) -> Line:
    line = Line(block_type=BlockType.PARAGRAPH, raw=raw)
    line.segments = [Segment(SegType.TEXT, raw, raw)]
    return line


def _make_ctx(doc: Document, raw_mode: bool = False, raw_draft: str = "") -> tuple[object, list, EditHistory]:
    calls: list = []
    history = EditHistory()

    def _push_history():
        calls.append("push_history")
        history.push(("snapshot",))

    ctx = types.SimpleNamespace(
        restoring=FakeRef(False),
        raw_mode=raw_mode,
        raw_draft=raw_draft,
        document=doc,
        push_history=_push_history,
        set_raw_draft=lambda v: calls.append(("set_raw_draft", v)),
        mark_dirty=lambda: calls.append("mark_dirty"),
        set_cursor_li=lambda v: calls.append(("set_cursor_li", v)),
        set_cursor_off=lambda v: calls.append(("set_cursor_off", v)),
        set_nav_seq=lambda v: calls.append(("set_nav_seq", v)),
        nav_seq=5,
    )
    return ctx, calls, history


def test_format_changes_document_and_pushes_history():
    """格式化有变化：推历史、重建 lines、mark_dirty、退出编辑态。"""
    doc = Document(lines=[_para("标题text  "), _para("")])
    ctx, calls, _h = _make_ctx(doc)
    fmt = build_format(ctx)["format_document"]

    fmt()

    assert "push_history" in calls
    assert "mark_dirty" in calls
    assert ("set_cursor_li", None) in calls
    assert ("set_nav_seq", 6) in calls
    # 行内容被格式化（中英文空格 + 行尾清理 + 末尾换行）
    assert doc.lines[0].raw == "标题 text"


def test_format_no_change_does_nothing():
    """内容已符合规范：不推历史、不重建。"""
    doc = Document(lines=[_para("规范 text")])
    ctx, calls, _h = _make_ctx(doc)
    fmt = build_format(ctx)["format_document"]

    fmt()

    assert "push_history" not in calls
    assert "mark_dirty" not in calls
    assert doc.lines[0].raw == "规范 text"


def test_format_raw_mode_formats_raw_draft():
    """原文模式：格式化 raw_draft。"""
    doc = Document(lines=[])
    ctx, calls, _h = _make_ctx(doc, raw_mode=True, raw_draft="标题text  ")
    fmt = build_format(ctx)["format_document"]

    fmt()

    assert ("set_raw_draft", "标题 text\n") in calls
    assert "push_history" in calls
    assert "mark_dirty" in calls


def test_format_restoring_skipped():
    """restoring 期间（撤销/重做恢复中）不执行格式化。"""
    doc = Document(lines=[_para("标题text  ")])
    ctx, calls, _h = _make_ctx(doc)
    ctx.restoring.current = True
    fmt = build_format(ctx)["format_document"]

    fmt()

    assert "push_history" not in calls
    assert "mark_dirty" not in calls
    assert doc.lines[0].raw == "标题text  "


def test_format_code_block_content_preserved():
    """代码块围栏内容不被格式化破坏（行尾空格/中英文空格保留）。"""
    code = Line(block_type=BlockType.CODE)
    code.segments = [Segment(SegType.CODE, "x = '中文abc'  ", "x = '中文abc'  ")]
    code.raw = "```python\nx = '中文abc'  \n```"
    doc = Document(lines=[_para("正文text"), code])
    ctx, calls, _h = _make_ctx(doc)
    fmt = build_format(ctx)["format_document"]

    fmt()

    assert "push_history" in calls
    assert doc.lines[0].raw == "正文 text"
    # 代码块行原样保留
    assert doc.lines[1].raw == "```python\nx = '中文abc'  \n```"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
