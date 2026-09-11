"""`handle_char_input` 行为测试（IME 热路径，daily-editing 正确性核心）。

该闭包用「公共前缀 delta」统一处理 ASCII 追加 / IME composing 增长 / 上屏替换 /
composing 取消/放弃。本测试把这些场景逐条变成断言——这是「中文输入不丢字、
不重复」的机器可验证部分（手感仍需真机确认）。

**会话模型**（由代码与实测确定，非猜测）：

- ``start_off``：本次输入会话在**行内**的起点（行中开输入会话时 > 0）
- ``last_value``：上一次 TextField 的完整 value = ``行内[start_off:]``
- 文档在会话期间**已经包含** ``last_value``（TextField 与文档同步写入）
- ``value``：本次 on_change 传来的完整 value（仍含已上屏内容）

因此 delta = 在 ``[start_off + cp, start_off + len(last_value))`` 区间替换为
``value`` 公共前缀之后的部分。

另覆盖守卫：浏览态、围栏行、向外选区激活、粘贴进行中、越界光标，
以及 `_fix_ime_doubling` 的完美翻倍折叠（有意修复）。
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


def _line(raw: str, block_type=BlockType.PARAGRAPH) -> Line:
    line = Line(block_type=block_type, raw=raw)
    line.segments = [Segment(SegType.TEXT, raw, raw)]
    return line


def _session(start_off: int, last_value: str, li: int = 0) -> dict:
    return {"li": li, "start_off": start_off, "last_value": last_value}


def _make_ctx(lines, cursor_li=0, cursor_off=0, session=None, **overrides):
    """构造覆盖 handle_char_input 热路径的最小 mock ctx。"""
    calls: list = []
    if session is None:
        session = {"li": -1, "start_off": -1, "last_value": ""}
    ctx = types.SimpleNamespace(
        document=Document(lines=lines),
        cursor_li=cursor_li,
        cursor_off=cursor_off,
        cursor_ref=FakeRef(_Cursor(cursor_off)),
        focus_seq=0,
        nav_seq=0,
        outward_sel_ref=FakeRef(None),
        paste_in_progress_ref=FakeRef(False),
        input_session_ref=FakeRef(session),
        secondary_cursors_ref=FakeRef([]),
        suppress_blur=FakeRef(False),
        shift_pressed_ref=FakeRef(False),
        alt_pressed_ref=FakeRef(False),
        push_history=lambda: calls.append("push_history"),
        push_line_edit=lambda li, raw: calls.append(("push_line_edit", li, raw)),
        maybe_push_history=lambda: calls.append("maybe_push_history"),
        mark_dirty=lambda: calls.append("mark_dirty"),
        undo_push_pending=FakeRef(True),
        set_cursor_li=lambda li: calls.append(("set_cursor_li", li)),
        set_cursor_off=lambda off: calls.append(("set_cursor_off", off)),
        set_cursor_line=lambda li: calls.append(("set_cursor_line", li)),
        set_cursor=lambda li, off: calls.append(("set_cursor", li, off)),
        set_nav_seq=lambda n: calls.append(("set_nav_seq", n)),
        set_focus_seq=lambda n: calls.append(("set_focus_seq", n)),
        set_clear_value_seq=lambda n: calls.append(("set_clear_value_seq", n)),
        set_cursor_field_value=lambda v: calls.append(("set_cursor_field_value", v)),
        set_outward_sel=lambda v: calls.append(("set_outward_sel", v)),
        broadcast_char_input=lambda removed, inserted: calls.append(
            ("broadcast_char_input", removed, inserted)
        ),
        broadcast_backspace=lambda: calls.append("broadcast_backspace"),
        broadcast_delete=lambda: calls.append("broadcast_delete"),
        clear_secondary_cursors=lambda: calls.append("clear_secondary_cursors"),
        ensure_visible=lambda li, **kw: None,
        cursor_pulse_ref=FakeRef(0.0),
        preferred_col_ref=FakeRef(None),
        set_secondary_cursors=lambda v: None,
        secondary_cursors=[],
        secondary_cursors_version=0,
        set_secondary_cursors_version=lambda n: None,
        cursor_field_ref=FakeRef(None),
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
        settings={"word_wrap": False},
    )
    for key, value in overrides.items():
        setattr(ctx, key, value)
    ctx._calls = calls
    return ctx, calls


def _type(ctx, value: str) -> str:
    """以 ctx 现有会话调一次 handle_char_input，返回该行最新 raw。"""
    build_cursor(ctx)["handle_char_input"](value)
    return ctx.document.lines[ctx.cursor_li].raw


# ---------------- delta 场景 ----------------


def test_ascii_append():
    """空行输入单字符。"""
    ctx, calls = _make_ctx([_line("")], cursor_off=0, session=_session(0, ""))
    assert _type(ctx, "a") == "a"
    assert "mark_dirty" in calls


def test_composing_grow():
    """composing 增长：末尾追加新编码，不重复写入已有内容。"""
    ctx, _ = _make_ctx([_line("w")], cursor_off=1, session=_session(0, "w"))
    assert _type(ctx, "wq") == "wq"


def test_ime_commit_first_char():
    """首次上屏：composing 编码整体替换为候选字。"""
    ctx, _ = _make_ctx([_line("wq")], cursor_off=2, session=_session(0, "wq"))
    assert _type(ctx, "你") == "你"


def test_ime_commit_second_char():
    """连续上屏第二字：已上屏内容保留，仅替换新增 composing 段。

    用字避开「完美翻倍」形态（``你你`` 会被 `_fix_ime_doubling` 折叠，
    见 `test_perfect_doubling_is_collapsed`）。
    """
    ctx, _ = _make_ctx([_line("你vb")], cursor_off=4, session=_session(0, "你vb"))
    assert _type(ctx, "你好") == "你好"


def test_composing_cancel():
    """composing 取消：仅删除 composing 段，保留已上屏内容。"""
    ctx, _ = _make_ctx([_line("你vb")], cursor_off=4, session=_session(0, "你vb"))
    assert _type(ctx, "你") == "你"


def test_composing_abandon_all():
    """composing 全部放弃（value 为空）：清理 composing 段。"""
    ctx, _ = _make_ctx([_line("vb")], cursor_off=2, session=_session(0, "vb"))
    assert _type(ctx, "") == ""


def test_perfect_doubling_is_collapsed():
    """完美翻倍（value = X + X）被 `_fix_ime_doubling` 折叠为单份。

    这是针对 Windows IME 在特定 TextField 配置下翻倍 composing / commit 文本的
    **有意修复**；固定下来避免后人误判为 delta 计算错误。
    """
    ctx, _ = _make_ctx([_line("vb")], cursor_off=2, session=_session(0, "vb"))
    assert _type(ctx, "你你") == "你"


def test_no_change_is_noop():
    """无变化：不改文档、不标脏。"""
    ctx, calls = _make_ctx([_line("a")], cursor_off=1, session=_session(0, "a"))
    assert _type(ctx, "a") == "a"
    assert "mark_dirty" not in calls


def test_mid_line_session_then_grow():
    """行中会话的 composing 增长：仅在会话区间内追加，前后文不动。

    这条用例把「会话区间 = [start_off, start_off + len(last_value))」这一语义
    固定下来：``ab w`` + 区间 [3,4)("w") + 新值 "wq" → ``ab wq``。
    """
    ctx, _ = _make_ctx([_line("ab w")], cursor_off=4, session=_session(3, "w"))
    assert _type(ctx, "wq") == "ab wq"


# ---------------- 守卫分支 ----------------


def test_browse_mode_ignored():
    ctx, calls = _make_ctx([_line("x")], cursor_li=None)
    build_cursor(ctx)["handle_char_input"]("a")
    assert ctx.document.lines[0].raw == "x"
    assert "mark_dirty" not in calls


def test_outward_selection_blocks_input():
    """向外选区激活时忽略 IME 输入（由 KeyDispatcher 走替换逻辑）。"""
    ctx, calls = _make_ctx([_line("x")], outward_sel_ref=FakeRef((0, 0, 0, 1)))
    build_cursor(ctx)["handle_char_input"]("a")
    assert ctx.document.lines[0].raw == "x"
    assert "mark_dirty" not in calls


def test_paste_in_progress_blocks_input():
    """粘贴进行中忽略原生 on_change（避免与 handle_paste 重复插入）。"""
    ctx, calls = _make_ctx([_line("x")], paste_in_progress_ref=FakeRef(True))
    build_cursor(ctx)["handle_char_input"]("a")
    assert ctx.document.lines[0].raw == "x"
    assert "mark_dirty" not in calls


def test_fence_line_ignored():
    """围栏行（代码块）由 CodeEditor 岛处理，不走字符 delta。"""
    ctx, calls = _make_ctx([_line("```", block_type=BlockType.CODE)])
    build_cursor(ctx)["handle_char_input"]("a")
    assert ctx.document.lines[0].raw == "```"
    assert "mark_dirty" not in calls


def test_out_of_range_cursor_ignored():
    ctx, calls = _make_ctx([_line("x")], cursor_li=5)
    build_cursor(ctx)["handle_char_input"]("a")
    assert ctx.document.lines[0].raw == "x"
    assert "mark_dirty" not in calls


def test_multi_cursor_selection_replaced():
    """多光标 + 主光标有选区：输入替换选区并广播给副光标。"""
    ctx, calls = _make_ctx([_line("hello")], cursor_off=5, session=_session(0, ""))
    ctx.cursor_ref.current = _Cursor(1, 4)  # base=1 extent=4 → 选中 "ell"
    ctx.secondary_cursors_ref.current = [(0, 0, 0)]
    build_cursor(ctx)["handle_char_input"]("X")
    assert ctx.document.lines[0].raw == "hXo"
    assert any(isinstance(c, tuple) and c[0] == "broadcast_char_input" for c in calls)
