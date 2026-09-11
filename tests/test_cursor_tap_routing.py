"""`on_tap_line` 路由测试（渲染层点击 → 光标定位入口）。

`on_tap_line(li, raw_off)` 是「点击文档任意位置」的唯一入口，内部路由分支较多：
Alt+Click 多光标、Alt+Shift+Click 列光标、围栏块点击、公式编辑态退出、向外选区清除、
多光标模式退出、常规定位 + 强制重聚焦 + 按需滚动。

此前**无任何测试**（`build_cursor` 的 10 个闭包中唯一未被提及的一个），
而错误的路由会直接表现为「点击后光标跑到别处 / 点击无效 / 多光标点不出来」。
本测试用最小 mock ctx 覆盖每个分支的**决策结果**（调用哪些槽、不改哪些槽）。
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
    """最小光标状态桩：reset 原地更新 base/extent。"""

    def __init__(self, b=0):
        self.base = b
        self.extent = b

    def reset(self, off, raw_len):
        self.base = off
        self.extent = off


def _line(raw: str, block_type=BlockType.PARAGRAPH, level: int = 0) -> Line:
    line = Line(block_type=block_type, raw=raw, level=level)
    line.segments = [Segment(SegType.TEXT, raw, raw)]
    return line


def _make_ctx(calls: list, **overrides):
    """构造覆盖 on_tap_line 全部槽位的最小 mock ctx。"""
    doc = overrides.pop("document", None)
    if doc is None:
        doc = Document(lines=[_line("第一行"), _line("第二行")])

    ctx = types.SimpleNamespace(
        document=doc,
        cursor_li=None,
        cursor_off=0,
        cursor_ref=FakeRef(_Cursor()),
        focus_seq=0,
        math_focus_li=None,
        secondary_cursors_ref=FakeRef([]),
        outward_sel_ref=FakeRef(None),
        alt_pressed_ref=FakeRef(False),
        shift_pressed_ref=FakeRef(False),
        suppress_blur=FakeRef(False),
        # 记录型槽位
        set_cursor=lambda li, off: calls.append(("set_cursor", li, off)),
        set_cursor_li=lambda li: calls.append(("set_cursor_li", li)),
        set_cursor_line=lambda li: calls.append(("set_cursor_line", li)),
        set_cursor_off=lambda off: calls.append(("set_cursor_off", off)),
        set_focus_seq=lambda n: calls.append(("set_focus_seq", n)),
        set_math_focus_li=lambda li: calls.append(("set_math_focus_li", li)),
        set_outward_sel=lambda v: calls.append(("set_outward_sel", v)),
        add_secondary_cursor=lambda li, off: calls.append(("add_secondary_cursor", li, off)),
        add_column_cursors=lambda li, off: calls.append(("add_column_cursors", li, off)),
        clear_secondary_cursors=lambda: calls.append("clear_secondary_cursors"),
        ensure_visible=lambda li, **kw: calls.append(("ensure_visible", li, kw)),
        # 其它占位（on_tap_line 不直接调用，防 AttributeError）
        push_history=lambda: None,
        push_line_edit=lambda *a: None,
        maybe_push_history=lambda: None,
        mark_dirty=lambda: None,
        undo_push_pending=FakeRef(True),
        nav_seq=0,
        set_nav_seq=lambda n: None,
        cursor_pulse_ref=FakeRef(0.0),
        input_session_ref=FakeRef({"li": -1, "start_off": -1, "last_value": ""}),
        set_clear_value_seq=lambda n: None,
        preferred_col_ref=FakeRef(None),
        secondary_cursors=[],
        set_secondary_cursors=lambda v: None,
        set_secondary_cursors_version=lambda n: None,
        secondary_cursors_version=0,
        paste_in_progress_ref=FakeRef(False),
        cursor_field_ref=FakeRef(None),
        set_cursor_field_value=lambda v: None,
        set_wrap_sel_seq=lambda n: None,
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
    )
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


def _tap(ctx):
    return build_cursor(ctx)["on_tap_line"]


# ---------------- 越界与常规点击 ----------------


def test_out_of_range_li_is_ignored():
    """越界行号不产生任何副作用。"""
    calls: list = []
    ctx = _make_ctx(calls)
    _tap(ctx)(99, 0)
    assert calls == []


def test_plain_tap_sets_cursor_and_refocuses():
    """常规点击：定位光标 + 递增 focus_seq 强制重聚焦 + 按需滚动。"""
    calls: list = []
    ctx = _make_ctx(calls)
    _tap(ctx)(1, 2)
    # 定位光标经由 _set_cursor（写 li / off / line 三个 state）
    assert ("set_cursor_li", 1) in calls
    assert ("set_cursor_off", 2) in calls
    assert ("set_cursor_line", 1) in calls
    assert ("set_focus_seq", 1) in calls
    assert any(isinstance(c, tuple) and c[0] == "ensure_visible" for c in calls)


def test_plain_tap_clears_existing_outward_selection():
    """已有向外选区时点击：先清除选区，再定位光标。"""
    calls: list = []
    ctx = _make_ctx(calls, outward_sel_ref=FakeRef((0, 0, 1, 1)))
    _tap(ctx)(1, 1)
    assert ("set_outward_sel", None) in calls
    assert ("set_cursor_li", 1) in calls
    assert ("set_cursor_off", 1) in calls


def test_plain_tap_exits_multi_cursor_mode():
    """常规点击应退出多光标模式（清空副光标）。"""
    calls: list = []
    ctx = _make_ctx(calls, secondary_cursors_ref=FakeRef([(0, 0, 0)]))
    _tap(ctx)(1, 0)
    assert "clear_secondary_cursors" in calls


def test_no_secondary_cursors_no_clear_call():
    """无副光标时不应调用清空（避免无谓重渲染）。"""
    calls: list = []
    ctx = _make_ctx(calls)
    _tap(ctx)(1, 0)
    assert "clear_secondary_cursors" not in calls


# ---------------- Alt / Alt+Shift 多光标 ----------------


def test_alt_tap_adds_secondary_cursor():
    """Alt+Click → 添加副光标，且不移动主光标。"""
    calls: list = []
    ctx = _make_ctx(calls, alt_pressed_ref=FakeRef(True))
    _tap(ctx)(1, 3)
    assert ("add_secondary_cursor", 1, 3) in calls
    assert not any(isinstance(c, tuple) and c[0] == "set_cursor" for c in calls)
    assert ("set_focus_seq", 1) in calls
    assert ctx.suppress_blur.current is True


def test_alt_shift_tap_adds_column_cursors():
    """Alt+Shift+Click → 列光标；且不添加单个副光标。"""
    calls: list = []
    ctx = _make_ctx(
        calls, alt_pressed_ref=FakeRef(True), shift_pressed_ref=FakeRef(True)
    )
    _tap(ctx)(1, 3)
    assert ("add_column_cursors", 1, 3) in calls
    assert not any(isinstance(c, tuple) and c[0] == "add_secondary_cursor" for c in calls)


# ---------------- 围栏块与公式 ----------------


def test_tap_on_fence_sets_cursor_line_without_entering_edit():
    """围栏块（代码/表格/公式）点击：只更新 cursor_line，不进入光标编辑态。"""
    calls: list = []
    doc = Document(lines=[_line("```", block_type=BlockType.CODE)])
    ctx = _make_ctx(calls, document=doc)
    _tap(ctx)(0, 3)
    assert ("set_cursor_line", 0) in calls
    assert not any(isinstance(c, tuple) and c[0] == "set_cursor" for c in calls)


def test_tap_on_fence_clears_outward_selection():
    """围栏块点击若存在向外选区，应一并清除。"""
    calls: list = []
    doc = Document(lines=[_line("```", block_type=BlockType.CODE)])
    ctx = _make_ctx(calls, document=doc, outward_sel_ref=FakeRef((0, 0, 0, 1)))
    _tap(ctx)(0, 1)
    assert ("set_outward_sel", None) in calls
    assert ("set_cursor_line", 0) in calls


def test_tap_other_line_exits_math_editing():
    """点击非公式行时退出公式编辑态。"""
    calls: list = []
    ctx = _make_ctx(calls, math_focus_li=0)
    _tap(ctx)(1, 0)
    assert ("set_math_focus_li", None) in calls


def test_tap_same_math_line_keeps_editing():
    """点击公式行自身不退出编辑态。"""
    calls: list = []
    doc = Document(lines=[_line("$$", block_type=BlockType.MATH), _line("x")])
    ctx = _make_ctx(calls, document=doc, math_focus_li=0)
    _tap(ctx)(0, 0)
    assert ("set_math_focus_li", None) not in calls
