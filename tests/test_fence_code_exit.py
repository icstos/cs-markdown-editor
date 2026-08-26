"""handle_code_exit / on_code_selection 单元测试（代码块边界方向键跳出，Typora 式）。

直接构造最小 mock ctx 调用 build_fence(ctx)["handle_code_exit"]，验证：
- ↑（第一行）→ 跳出到代码块上一行行尾
- ←（第一行行首）→ 跳出到上一行行尾（换行回绕）
- ↓（最后一行）→ 跳出到下一行行首
- →（最后一行行尾）→ 跳出到下一行行首（换行回绕）
- 代码块前/后无行（或相邻行为岛屿块）→ 创建新空段落行承接光标
- 非边界 / 有选区 / 未聚焦代码块 / 无光标信息 → 返回 False（放行原生导航）
- on_code_selection：记录 (value, base, extent)；非聚焦行忽略
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from models import BlockType, Document, Line, Segment, SegType
from views.editor._fence import build_fence


class FakeRef:
    """伪 ft.Ref：避免 flet.Ref weakref 限制。"""

    def __init__(self, current=None):
        self.current = current


def _para(raw: str) -> Line:
    line = Line(block_type=BlockType.PARAGRAPH, raw=raw)
    line.segments = [Segment(SegType.TEXT, raw, raw)]
    return line


def _code_line(code: str, lang: str = "py") -> Line:
    line = Line(block_type=BlockType.CODE, lang=lang)
    line.segments = [Segment(SegType.CODE, code, code)]
    line.raw = f"```{lang}\n{code}\n```"
    return line


def _math_line() -> Line:
    line = Line(block_type=BlockType.MATH)
    line.segments = [Segment(SegType.MATH, "x", "x")]
    line.raw = "$$\nx\n$$"
    return line


def _make_ctx(
    document: Document,
    *,
    focus_li: int | None = 1,
    caret: tuple | None = ("abc\ndef", 1, 1),
) -> tuple[types.SimpleNamespace, list]:
    """构造最小 mock EditorContext，仅含 handle_code_exit 依赖的槽。"""
    calls: list = []
    ctx = types.SimpleNamespace(
        document=document,
        code_focus_ref=FakeRef(focus_li),
        code_caret_ref=FakeRef(caret),
        code_edit_snapshot=FakeRef(object()),
        code_edit_changed=FakeRef(False),
        push_history=lambda: calls.append("push_history"),
        undo_push_pending=FakeRef(False),
        mark_dirty=lambda: calls.append("mark_dirty"),
        suppress_blur=FakeRef(False),
        set_cursor=lambda li, off: calls.append(("set_cursor", li, off)),
    )
    return ctx, calls


def _exit(ctx) -> callable:
    return build_fence(ctx)["handle_code_exit"]


# ---------------- ↑ 第一行向上跳出 ----------------
def test_up_on_first_line_exits_to_previous_line_end():
    """第一行任意列按 ↑ → 跳出到上一行行尾。"""
    doc = Document(lines=[_para("before"), _code_line("abc\ndef"), _para("after")])
    ctx, calls = _make_ctx(doc, focus_li=1, caret=("abc\ndef", 2, 2))
    result = _exit(ctx)("arrowup")
    assert result is True
    # 光标落到上一行（li=0）行尾
    assert ("set_cursor", 0, len("before")) in calls


def test_up_on_second_line_not_exit():
    """非第一行按 ↑ → 返回 False（放行原生导航）。"""
    doc = Document(lines=[_para("before"), _code_line("abc\ndef"), _para("after")])
    ctx, calls = _make_ctx(doc, focus_li=1, caret=("abc\ndef", 5, 5))  # 第二行内
    result = _exit(ctx)("arrowup")
    assert result is False
    assert not calls


def test_up_at_doc_start_creates_new_line_before():
    """代码块是文档首行，按 ↑ → 在代码块前创建新空行，光标落到新行行首。"""
    doc = Document(lines=[_code_line("abc")])
    ctx, calls = _make_ctx(doc, focus_li=0, caret=("abc", 1, 1))
    result = _exit(ctx)("arrowup")
    assert result is True
    assert len(doc.lines) == 2
    assert doc.lines[0].block_type == BlockType.PARAGRAPH
    assert doc.lines[0].raw == ""
    assert doc.lines[1].block_type == BlockType.CODE  # 原代码块仍在
    assert ("set_cursor", 0, 0) in calls
    assert "push_history" in calls
    assert "mark_dirty" in calls
    assert ctx.undo_push_pending.current is True


def test_up_above_island_creates_new_line_between():
    """代码块上方是另一个代码块（岛屿）→ 在两者之间创建新空行承接光标。"""
    doc = Document(lines=[_code_line("x", "a"), _code_line("y", "b")])
    ctx, calls = _make_ctx(doc, focus_li=1, caret=("y", 0, 0))
    result = _exit(ctx)("arrowup")
    assert result is True
    assert len(doc.lines) == 3
    assert doc.lines[0].block_type == BlockType.CODE
    assert doc.lines[1].block_type == BlockType.PARAGRAPH
    assert doc.lines[2].block_type == BlockType.CODE
    assert ("set_cursor", 1, 0) in calls


def test_up_above_math_island_creates_new_line():
    """代码块上方是公式块（岛屿）→ 创建新空行。"""
    doc = Document(lines=[_math_line(), _code_line("y")])
    ctx, calls = _make_ctx(doc, focus_li=1, caret=("y", 0, 0))
    result = _exit(ctx)("arrowup")
    assert result is True
    assert len(doc.lines) == 3
    assert doc.lines[1].block_type == BlockType.PARAGRAPH
    assert ("set_cursor", 1, 0) in calls


# ---------------- ← 第一行行首向左跳出 ----------------
def test_left_at_first_line_start_exits_to_previous_line_end():
    """第一行行首按 ← → 跳出到上一行行尾（换行回绕）。"""
    doc = Document(lines=[_para("before"), _code_line("abc\ndef"), _para("after")])
    ctx, calls = _make_ctx(doc, focus_li=1, caret=("abc\ndef", 0, 0))
    result = _exit(ctx)("arrowleft")
    assert result is True
    assert ("set_cursor", 0, len("before")) in calls


def test_left_not_at_line_start_not_exit():
    """第一行非行首按 ← → 返回 False（原生左移）。"""
    doc = Document(lines=[_para("before"), _code_line("abc\ndef"), _para("after")])
    ctx, calls = _make_ctx(doc, focus_li=1, caret=("abc\ndef", 2, 2))
    result = _exit(ctx)("arrowleft")
    assert result is False
    assert not calls


def test_left_at_second_line_start_not_exit():
    """第二行行首按 ← → 返回 False（原生回绕到第一行行尾，仍在代码块内）。"""
    doc = Document(lines=[_para("before"), _code_line("abc\ndef"), _para("after")])
    ctx, calls = _make_ctx(doc, focus_li=1, caret=("abc\ndef", 4, 4))
    result = _exit(ctx)("arrowleft")
    assert result is False
    assert not calls


# ---------------- ↓ 最后一行向下跳出 ----------------
def test_down_on_last_line_exits_to_next_line_start():
    """最后一行按 ↓ → 跳出到下一行行首。"""
    doc = Document(lines=[_para("before"), _code_line("abc\ndef"), _para("after")])
    ctx, calls = _make_ctx(doc, focus_li=1, caret=("abc\ndef", 5, 5))  # 第二行
    result = _exit(ctx)("arrowdown")
    assert result is True
    assert ("set_cursor", 2, 0) in calls


def test_down_on_first_line_not_exit():
    """非最后一行按 ↓ → 返回 False。"""
    doc = Document(lines=[_para("before"), _code_line("abc\ndef"), _para("after")])
    ctx, calls = _make_ctx(doc, focus_li=1, caret=("abc\ndef", 1, 1))  # 第一行
    result = _exit(ctx)("arrowdown")
    assert result is False
    assert not calls


def test_down_at_doc_end_creates_new_line_after():
    """代码块是文档末行，按 ↓ → 在代码块后创建新空行，光标落到新行行首。"""
    doc = Document(lines=[_para("before"), _code_line("abc\ndef")])
    ctx, calls = _make_ctx(doc, focus_li=1, caret=("abc\ndef", 5, 5))
    result = _exit(ctx)("arrowdown")
    assert result is True
    assert len(doc.lines) == 3
    assert doc.lines[2].block_type == BlockType.PARAGRAPH
    assert doc.lines[2].raw == ""
    assert doc.lines[1].block_type == BlockType.CODE
    assert ("set_cursor", 2, 0) in calls
    assert "push_history" in calls
    assert "mark_dirty" in calls


# ---------------- → 最后一行行尾向右跳出 ----------------
def test_right_at_last_line_end_exits_to_next_line_start():
    """最后一行行尾按 → → 跳出到下一行行首（换行回绕）。"""
    doc = Document(lines=[_para("before"), _code_line("abc\ndef"), _para("after")])
    ctx, calls = _make_ctx(doc, focus_li=1, caret=("abc\ndef", 7, 7))  # 末尾
    result = _exit(ctx)("arrowright")
    assert result is True
    assert ("set_cursor", 2, 0) in calls


def test_right_not_at_line_end_not_exit():
    """最后一行非行尾按 → → 返回 False（原生右移）。"""
    doc = Document(lines=[_para("before"), _code_line("abc\ndef"), _para("after")])
    ctx, calls = _make_ctx(doc, focus_li=1, caret=("abc\ndef", 6, 6))
    result = _exit(ctx)("arrowright")
    assert result is False
    assert not calls


# ---------------- 空代码块 ----------------
def test_empty_code_block_all_directions_exit():
    """空代码块（value=""）：首行==末行、行首==行尾，四个方向均跳出。"""
    doc = Document(lines=[_para("before"), _code_line(""), _para("after")])
    ctx, calls = _make_ctx(doc, focus_li=1, caret=("", 0, 0))
    exit_fn = _exit(ctx)
    # 每次跳出后聚焦态被清理（与生产一致），需重新模拟聚焦 + 选区事件
    assert exit_fn("arrowup") is True
    assert ("set_cursor", 0, len("before")) in calls
    calls.clear()
    ctx.code_focus_ref.current = 1
    ctx.code_caret_ref.current = ("", 0, 0)
    assert exit_fn("arrowleft") is True
    assert ("set_cursor", 0, len("before")) in calls
    calls.clear()
    ctx.code_focus_ref.current = 1
    ctx.code_caret_ref.current = ("", 0, 0)
    assert exit_fn("arrowdown") is True
    assert ("set_cursor", 2, 0) in calls
    calls.clear()
    ctx.code_focus_ref.current = 1
    ctx.code_caret_ref.current = ("", 0, 0)
    assert exit_fn("arrowright") is True
    assert ("set_cursor", 2, 0) in calls


# ---------------- 退出后的聚焦态清理 ----------------
def test_exit_clears_code_focus_state():
    """跳出后清理代码块聚焦态（code_focus_ref / 快照 / caret），防后续按键误路由。"""
    doc = Document(lines=[_para("before"), _code_line("abc"), _para("after")])
    ctx, _ = _make_ctx(doc, focus_li=1, caret=("abc", 0, 0))
    result = _exit(ctx)("arrowup")
    assert result is True
    assert ctx.code_focus_ref.current is None
    assert ctx.code_edit_snapshot.current is None
    assert ctx.code_edit_changed.current is False
    assert ctx.code_caret_ref.current is None
    assert ctx.suppress_blur.current is True


# ---------------- 不触发场景 ----------------
def test_not_focused_returns_false():
    """code_focus_ref 为 None（表格/公式聚焦或无聚焦）→ 返回 False。"""
    doc = Document(lines=[_para("before"), _code_line("abc"), _para("after")])
    ctx, calls = _make_ctx(doc, focus_li=None, caret=("abc", 0, 0))
    assert _exit(ctx)("arrowup") is False
    assert not calls


def test_no_caret_info_returns_false():
    """code_caret_ref 为 None（尚无 on_selection_change 事件）→ 返回 False。"""
    doc = Document(lines=[_para("before"), _code_line("abc"), _para("after")])
    ctx, calls = _make_ctx(doc, focus_li=1, caret=None)
    assert _exit(ctx)("arrowup") is False
    assert not calls


def test_selection_active_returns_false():
    """有选区（Shift+方向键扩展中）→ 返回 False，交给原生控件。"""
    doc = Document(lines=[_para("before"), _code_line("abc"), _para("after")])
    ctx, calls = _make_ctx(doc, focus_li=1, caret=("abc", 0, 2))
    assert _exit(ctx)("arrowup") is False
    assert not calls


def test_unsupported_norm_returns_false():
    """非方向键 norm → 返回 False。"""
    doc = Document(lines=[_para("before"), _code_line("abc"), _para("after")])
    ctx, calls = _make_ctx(doc, focus_li=1, caret=("abc", 0, 0))
    assert _exit(ctx)("arrowhome") is False
    assert not calls


def test_out_of_range_li_returns_false():
    """聚焦行号越界 → 返回 False。"""
    doc = Document(lines=[_para("before"), _code_line("abc"), _para("after")])
    ctx, calls = _make_ctx(doc, focus_li=99, caret=("abc", 0, 0))
    assert _exit(ctx)("arrowup") is False
    assert not calls


def test_table_focused_not_intercepted():
    """非 CODE/FRONTMATTER 行（如表格行）聚焦 → 返回 False。"""
    table = Line(block_type=BlockType.TABLE, raw="| a |")
    table.segments = [Segment(SegType.TEXT, "| a |", "| a |")]
    doc = Document(lines=[table])
    ctx, calls = _make_ctx(doc, focus_li=0, caret=("abc", 0, 0))
    assert _exit(ctx)("arrowup") is False
    assert not calls


# ---------------- on_code_selection ----------------
def test_selection_records_caret():
    """on_code_selection 记录 (value, base, extent) 到 code_caret_ref。"""
    doc = Document(lines=[_para("before"), _code_line("abc\ndef"), _para("after")])
    ctx, _ = _make_ctx(doc, focus_li=1, caret=None)
    e = types.SimpleNamespace(
        control=types.SimpleNamespace(value="abc\ndef"),
        selection=types.SimpleNamespace(base_offset=4, extent_offset=4),
    )
    build_fence(ctx)["on_code_selection"](1, e)
    assert ctx.code_caret_ref.current == ("abc\ndef", 4, 4)


def test_selection_ignores_non_focused_line():
    """on_code_selection 非聚焦行事件 → 忽略（不覆盖已有记录）。"""
    doc = Document(lines=[_para("before"), _code_line("abc"), _para("after")])
    ctx, _ = _make_ctx(doc, focus_li=1, caret=("abc", 1, 1))
    e = types.SimpleNamespace(
        control=types.SimpleNamespace(value="zzz"),
        selection=types.SimpleNamespace(base_offset=2, extent_offset=2),
    )
    build_fence(ctx)["on_code_selection"](2, e)
    assert ctx.code_caret_ref.current == ("abc", 1, 1)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
