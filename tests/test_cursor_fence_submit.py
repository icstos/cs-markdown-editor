"""on_submit 的 ```+Enter 代码块创建测试（Typora 式）。

直接构造最小 mock ctx 调用 build_cursor(ctx)["on_submit"]，验证：
- ```+Enter（光标在行尾）→ 当前行转为空代码块，退出光标编辑态
- ```lang+Enter → 代码块 lang 正确
- 段落含后续内容（after 非空）→ 不触发，走默认分割
- 非段落行（标题）→ 不触发围栏创建
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from models import BlockType, Document, Line, Segment, SegType  # noqa: E402
from views.editor._cursor import build_cursor  # noqa: E402


class FakeRef:
    def __init__(self, current=None):
        self.current = current


def _para_line(raw: str) -> Line:
    """构造段落行：单 TEXT 段，raw 即文本。"""
    line = Line(block_type=BlockType.PARAGRAPH, raw=raw)
    line.segments = [Segment(SegType.TEXT, raw, raw)]
    return line


def _make_ctx(document: Document, cursor_li: int, base: int) -> tuple[types.SimpleNamespace, list]:
    """构造最小 mock EditorContext，仅含 on_submit 围栏触发路径依赖的槽。"""
    calls: list = []

    class _Cursor:
        """最小光标状态桩：reset 原地更新 base/extent。"""
        def __init__(self, b):
            self.base = b
            self.extent = b

        def reset(self, off, raw_len):
            self.base = off
            self.extent = off

    ctx = types.SimpleNamespace(
        document=document,
        cursor_li=cursor_li,
        cursor_off=base,
        cursor_ref=FakeRef(_Cursor(base)),
        push_history=lambda: calls.append("push_history"),
        undo_push_pending=FakeRef(True),
        mark_dirty=lambda: calls.append("mark_dirty"),
        suppress_blur=FakeRef(False),
        set_cursor_line=lambda li: calls.append(("set_cursor_line", li)),
        set_cursor_li=lambda li: calls.append(("set_cursor_li", li)),
        set_cursor=lambda li, off: calls.append(("set_cursor", li, off)),
        # 其余 on_submit 默认分支可能触及（不被围栏路径调用，仅占位防 AttributeError）
        set_nav_seq=lambda n: None,
        input_session_ref=FakeRef({"li": -1, "start_off": -1, "last_value": ""}),
        set_cursor_off=lambda off: None,
        set_clear_value_seq=lambda n: None,
        preferred_col_ref=FakeRef(None),
    )
    return ctx, calls


def test_triple_backtick_enter_creates_code_block():
    """``` + 回车（光标在行尾）→ 当前行转为空代码块。"""
    doc = Document(lines=[_para_line("```")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=3)  # 光标在行尾（after 为空）
    on_submit = build_cursor(ctx)["on_submit"]

    on_submit("")

    # 行转为代码块
    assert doc.lines[0].block_type == BlockType.CODE
    assert doc.lines[0].lang == ""
    # 代码体为空
    assert doc.lines[0].segments[0].text == ""
    # 退出光标编辑态（cursor_li=None）
    assert ("set_cursor_li", None) in calls
    # suppress_blur 置 True
    assert ctx.suppress_blur.current is True
    # 历史入栈
    assert "push_history" in calls


def test_triple_backtick_with_lang_creates_code_block():
    """```python + 回车 → 代码块 lang=python。"""
    doc = Document(lines=[_para_line("```python")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=10)  # 光标在行尾
    on_submit = build_cursor(ctx)["on_submit"]

    on_submit("")

    assert doc.lines[0].block_type == BlockType.CODE
    assert doc.lines[0].lang == "python"
    assert doc.lines[0].segments[0].text == ""


def test_four_backticks_creates_code_block():
    """4 个反引号 + 回车也触发（正则 ^`{3,}）。"""
    doc = Document(lines=[_para_line("````")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=4)
    on_submit = build_cursor(ctx)["on_submit"]

    on_submit("")

    assert doc.lines[0].block_type == BlockType.CODE


def test_backtick_with_trailing_content_not_triggered():
    """光标不在行尾（after 非空）→ 不触发围栏创建，走默认分割。"""
    doc = Document(lines=[_para_line("``` extra")])
    # 光标在 ``` 之后（base=3），after=" extra" 非空
    ctx, calls = _make_ctx(doc, cursor_li=0, base=3)
    on_submit = build_cursor(ctx)["on_submit"]

    on_submit("")

    # 不触发围栏：行未转为 CODE（默认分割会新增一行）
    assert doc.lines[0].block_type != BlockType.CODE or len(doc.lines) > 1


def test_heading_line_not_triggered():
    """标题行（block_type=HEADING）→ 不触发围栏创建（正则不匹配前缀）。"""
    line = Line(block_type=BlockType.HEADING, raw="# ```")
    line.segments = [Segment(SegType.HEADING_PREFIX, "# ", "# "), Segment(SegType.TEXT, "```", "```")]
    doc = Document(lines=[line])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=len("# ```"))
    on_submit = build_cursor(ctx)["on_submit"]

    on_submit("")

    # 标题行不触发围栏（before 含 "# " 前缀，正则不匹配）
    assert doc.lines[0].block_type != BlockType.CODE or all(
        l.block_type != BlockType.CODE for l in doc.lines
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
