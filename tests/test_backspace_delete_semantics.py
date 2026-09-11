"""`backspace_core` / `delete_core` 语义测试（日常编辑最高频路径）。

既有测试（`test_cursor_wrap_refocus.py`）只覆盖「软换行收拢时重聚焦」，
**行首合并 / 行尾合并 / 边界行为**没有直接测试——而这些正是 Typora 式编辑的
核心手感（README 明确列为特性）。

本测试按实现语义逐条固定以下契约：

| 操作 | 条件 | 期望 |
|---|---|---|
| Backspace 段内 | ``off > 0`` | 删除光标前一个字符，光标左移一格 |
| Backspace 行首 | ``off == 0`` 且非首行 | 与上一行合并，光标落在接缝处 |
| Backspace 首行首 | ``off == 0`` 且 ``li == 0`` | 无操作 |
| Backspace 围栏行 | 代码块 / 表格 | 无操作（交岛屿控件） |
| Backspace HR 行首 | ``off == 0`` | HR 转为空段落（不合并到上一行） |
| Backspace 相邻为围栏 | 上一行是围栏 | 不合并 |
| Delete 段内 | ``off < len(raw)`` | 删除光标后一个字符，光标不动 |
| Delete 行尾 | 行尾且非末行 | 与下一行合并，光标停在原行尾 |
| Delete 末行尾 | 行尾且 ``li`` 为末行 | 无操作 |
| Delete HR 行尾 | 行尾 | HR 转为空段落（不合并下一行） |
| 向外选区激活 | ``outward_sel`` 非空 | 改为删除选区（委托 handle_outward_delete） |
| 浏览态 | ``cursor_li is None`` | 无操作 |
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.document import BlockType, Document, Line, Segment, SegType  # noqa: E402
from views.editor._cursor import build_cursor  # noqa: E402


class FakeRef:
    def __init__(self, current=None):
        self.current = current


class _Cursor:
    def __init__(self, b=0, e=None):
        self.base = b
        self.extent = b if e is None else e

    def reset(self, off, raw_len):
        self.base = off
        self.extent = off


def _line(raw: str, block_type=BlockType.PARAGRAPH, **kw) -> Line:
    line = Line(block_type=block_type, raw=raw, **kw)
    line.segments = [Segment(SegType.TEXT, raw, raw)]
    return line


def _doc(*raws, block_type=BlockType.PARAGRAPH) -> Document:
    return Document(lines=[_line(r, block_type) for r in raws])


def _make_ctx(document, cursor_li=0, base=0, **overrides):
    """构造覆盖 backspace/delete 路径的最小 mock ctx。"""
    calls: list = []
    ctx = types.SimpleNamespace(
        document=document,
        cursor_li=cursor_li,
        cursor_off=base,
        nav_seq=0,
        cursor_ref=FakeRef(_Cursor(base)),
        input_session_ref=FakeRef({"li": -1, "start_off": -1, "last_value": ""}),
        outward_sel_ref=FakeRef(None),
        secondary_cursors_ref=FakeRef([]),
        suppress_blur=FakeRef(False),
        undo_push_pending=FakeRef(True),
        preferred_col_ref=FakeRef(None),
        # 记录型
        push_line_edit=lambda li, raw: calls.append(("push_line_edit", li, raw)),
        push_history=lambda: calls.append("push_history"),
        mark_dirty=lambda: calls.append("mark_dirty"),
        set_cursor_off=lambda off: calls.append(("set_cursor_off", off)),
        set_cursor_li=lambda li: calls.append(("set_cursor_li", li)),
        set_cursor_line=lambda li: calls.append(("set_cursor_line", li)),
        set_cursor=lambda li, off: calls.append(("set_cursor", li, off)),
        set_nav_seq=lambda n: calls.append(("set_nav_seq", n)),
        set_focus_seq=lambda n: calls.append(("set_focus_seq", n)),
        set_cursor_field_value=lambda v: calls.append(("set_cursor_field_value", v)),
        clear_secondary_cursors=lambda: calls.append("clear_secondary_cursors"),
        broadcast_backspace=lambda: calls.append("broadcast_backspace"),
        broadcast_delete=lambda: calls.append("broadcast_delete"),
        handle_outward_delete=lambda: calls.append("handle_outward_delete"),
        close_outward=None,
        # 软换行检测所需（未开启换行时 _wrap_on 为 False）
        body_font_size=16,
        line_height=1.6,
        content_width=200.0,
        settings={"word_wrap": False},
        line_heights_ref=FakeRef({}),
        layout_cache_ref=FakeRef(None),
        offset_prefix_ref=FakeRef(None),
        viewport_w_ref=FakeRef(0.0),
        scroll_offset_ref=FakeRef(0.0),
        viewport_h_ref=FakeRef(0.0),
        max_scroll_ref=FakeRef(0.0),
        history_ref=FakeRef(None),
        restoring=FakeRef(False),
        code_focus_ref=FakeRef(None),
        table_focus_ref=FakeRef(None),
        math_focus_ref=FakeRef(None),
        set_math_focus_li=lambda li: None,
        math_focus_li=None,
        focus_seq=0,
    )
    for key, value in overrides.items():
        setattr(ctx, key, value)
    ctx._calls = calls
    return ctx, calls


# ---------------- Backspace：段内 ----------------


def test_backspace_mid_line_removes_prev_char():
    ctx, calls = _make_ctx(_doc("abc"), cursor_li=0, base=2)
    build_cursor(ctx)["backspace_core"]()
    assert ctx.document.lines[0].raw == "ac"
    assert ("push_line_edit", 0, "abc") in calls
    assert "mark_dirty" in calls


def test_backspace_mid_line_moves_cursor_left():
    """段内删除后光标左移一格（_move_cursor_inline）。"""
    ctx, _ = _make_ctx(_doc("abc"), cursor_li=0, base=2)
    build_cursor(ctx)["backspace_core"]()
    assert ctx.cursor_ref.current.base == 1


def test_backspace_at_first_line_start_is_noop():
    """首行行首 Backspace：无操作（无上一行可合并）。"""
    ctx, calls = _make_ctx(_doc("abc"), cursor_li=0, base=0)
    build_cursor(ctx)["backspace_core"]()
    assert ctx.document.lines[0].raw == "abc"
    assert "push_history" not in calls


# ---------------- Backspace：行首合并 ----------------


def test_backspace_at_line_start_merges_with_previous():
    ctx, calls = _make_ctx(_doc("ab", "cd"), cursor_li=1, base=0)
    build_cursor(ctx)["backspace_core"]()
    assert [ln.raw for ln in ctx.document.lines] == ["abcd"]
    assert "mark_dirty" in calls
    assert "push_history" in calls


def test_merge_cursor_lands_at_junction():
    """合并后光标落在接缝（前一行原长度）处。"""
    ctx, calls = _make_ctx(_doc("ab", "cd"), cursor_li=1, base=0)
    build_cursor(ctx)["backspace_core"]()
    # _set_cursor(prev_li, junction=2)：经 set_cursor_off / set_cursor_li 落地
    assert ("set_cursor_off", 2) in calls
    assert ("set_cursor_li", 0) in calls


def test_merge_into_empty_previous_line():
    """上一行为空行：合并等价于把当前行内容上移。"""
    ctx, _ = _make_ctx(_doc("", "cd"), cursor_li=1, base=0)
    build_cursor(ctx)["backspace_core"]()
    assert [ln.raw for ln in ctx.document.lines] == ["cd"]


def test_backspace_does_not_merge_when_previous_is_fence():
    """上一行是围栏块（代码块）：不合并。"""
    doc = Document(lines=[_line("```", BlockType.CODE), _line("cd")])
    ctx, calls = _make_ctx(doc, cursor_li=1, base=0)
    build_cursor(ctx)["backspace_core"]()
    assert len(ctx.document.lines) == 2
    assert "push_history" not in calls


def test_backspace_on_fence_line_is_noop():
    """光标在围栏行上：不处理（交岛屿控件）。"""
    doc = Document(lines=[_line("```", BlockType.CODE)])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=2)
    build_cursor(ctx)["backspace_core"]()
    assert ctx.document.lines[0].raw == "```"
    assert "mark_dirty" not in calls


# ---------------- Backspace：HR 行首 ----------------


def test_backspace_at_hr_start_becomes_empty_line():
    """HR 行首 Backspace：HR 转为空行（Typora 式，不合并到上一行）。

    新行由 ``parse_markdown("")`` 产出，即 ``BlockType.BLANK`` + ``raw == ""``
    （对应实现里的 ``new_line = parser.parse_markdown("").lines[0]``）。
    """
    doc = Document(lines=[_line("ab"), _line("---", BlockType.HR)])
    ctx, calls = _make_ctx(doc, cursor_li=1, base=0)
    build_cursor(ctx)["backspace_core"]()
    assert len(ctx.document.lines) == 2, "不应合并掉行"
    assert ctx.document.lines[1].block_type == BlockType.BLANK
    assert ctx.document.lines[1].raw == ""
    assert "push_history" in calls


# ---------------- Delete：段内 ----------------


def test_delete_mid_line_removes_next_char():
    ctx, calls = _make_ctx(_doc("abc"), cursor_li=0, base=1)
    build_cursor(ctx)["delete_core"]()
    assert ctx.document.lines[0].raw == "ac"
    assert ("push_line_edit", 0, "abc") in calls


def test_delete_mid_line_keeps_cursor():
    """段内删除后光标位置不变。"""
    ctx, _ = _make_ctx(_doc("abc"), cursor_li=0, base=1)
    build_cursor(ctx)["delete_core"]()
    assert ctx.cursor_ref.current.base == 1


def test_delete_at_last_line_end_is_noop():
    """末行行尾 Delete：无操作（无下一行可合并）。"""
    ctx, calls = _make_ctx(_doc("abc"), cursor_li=0, base=3)
    build_cursor(ctx)["delete_core"]()
    assert ctx.document.lines[0].raw == "abc"
    assert "push_history" not in calls


# ---------------- Delete：行尾合并 ----------------


def test_delete_at_line_end_merges_with_next():
    ctx, calls = _make_ctx(_doc("ab", "cd"), cursor_li=0, base=2)
    build_cursor(ctx)["delete_core"]()
    assert [ln.raw for ln in ctx.document.lines] == ["abcd"]
    assert "mark_dirty" in calls


def test_delete_does_not_merge_when_next_is_fence():
    doc = Document(lines=[_line("ab"), _line("```", BlockType.CODE)])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=2)
    build_cursor(ctx)["delete_core"]()
    assert len(ctx.document.lines) == 2
    assert "push_history" not in calls


def test_delete_on_fence_line_is_noop():
    doc = Document(lines=[_line("```", BlockType.CODE)])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0)
    build_cursor(ctx)["delete_core"]()
    assert ctx.document.lines[0].raw == "```"
    assert "mark_dirty" not in calls


def test_delete_at_hr_end_becomes_empty_line():
    """HR 行尾 Delete：HR 转为空行（不合并下一行）。"""
    doc = Document(lines=[_line("---", BlockType.HR), _line("ab")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=3)
    build_cursor(ctx)["delete_core"]()
    assert len(ctx.document.lines) == 2, "不应合并掉行"
    assert ctx.document.lines[0].block_type == BlockType.BLANK
    assert ctx.document.lines[0].raw == ""


# ---------------- 守卫 ----------------


def test_backspace_with_outward_selection_delegates():
    """向外选区激活：Backspace 委托给选区删除，不动字符。"""
    ctx, calls = _make_ctx(_doc("abc"), cursor_li=0, base=2)
    ctx.outward_sel_ref = FakeRef((0, 0, 0, 3))
    build_cursor(ctx)["backspace_core"]()
    assert "handle_outward_delete" in calls
    assert ctx.document.lines[0].raw == "abc"


def test_delete_with_outward_selection_delegates():
    ctx, calls = _make_ctx(_doc("abc"), cursor_li=0, base=2)
    ctx.outward_sel_ref = FakeRef((0, 0, 0, 3))
    build_cursor(ctx)["delete_core"]()
    assert "handle_outward_delete" in calls


def test_backspace_in_browse_mode_is_noop():
    ctx, calls = _make_ctx(_doc("abc"), cursor_li=None, base=0)
    build_cursor(ctx)["backspace_core"]()
    assert ctx.document.lines[0].raw == "abc"
    assert "push_history" not in calls


def test_delete_in_browse_mode_is_noop():
    ctx, calls = _make_ctx(_doc("abc"), cursor_li=None, base=0)
    build_cursor(ctx)["delete_core"]()
    assert ctx.document.lines[0].raw == "abc"
    assert "push_history" not in calls
