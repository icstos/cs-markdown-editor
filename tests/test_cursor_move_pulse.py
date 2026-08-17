"""光标移动脉冲（_set_cursor 节流递增 nav_seq → TextField 重建+重聚焦）单元测试。

背景：Flutter 光标按固定相位闪烁，快速移动光标（含同行左/右）时光标可能停在
"熄灭"相位从视线中丢失。方案：同行移动递增 nav_seq → cursor TextField
key 变化 → 重建 + use_effect 重聚焦 → Flutter 光标以不透明相位重启闪烁
（与跨行移动行为一致），静止后恢复正常闪烁。

性能优化（低负载、极速响应）：
- 同位置移动：零状态写入直接返回
- 跨行移动：key=li 已重建，不再递增 nav_seq
- 会话结束已重建（_end_input_session）时不重复脉冲
- 节流窗口 _CURSOR_PULSE_INTERVAL 内只脉冲一次（首个移动立即脉冲）

覆盖：
- 同行移动递增 nav_seq（移动脉冲）
- 跨行移动不递增（key=li 重建兜底）
- 会话结束移动：仅 _end_input_session 递增（重建兜底，不重复脉冲）
- 同位置移动：零开销（无状态写入）
- 快速连击/长按：节流窗口内只脉冲一次
- 浏览态 set_cursor(None)：不递增（无 TextField 可重建）
- 打字路径 handle_char_input：不递增 nav_seq（IME 组合态安全）
"""

import os
import sys
import types
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import BlockType, Document, Line, Segment, SegType
from views.editor._cursor import build_cursor


class FakeRef:
    def __init__(self, current=None):
        self.current = current


class _Cursor:
    """最小光标状态桩：reset 原地更新 base/extent。"""

    def __init__(self, b):
        self.base = b
        self.extent = b

    def reset(self, off, raw_len):
        self.base = off
        self.extent = off


def _para_line(raw: str) -> Line:
    """构造段落行：单 TEXT 段，raw 即文本。"""
    line = Line(block_type=BlockType.PARAGRAPH, raw=raw)
    line.segments = [Segment(SegType.TEXT, raw, raw)]
    return line


def _make_ctx(document: Document, cursor_li: int, base: int,
              session: dict | None = None) -> tuple[types.SimpleNamespace, list]:
    """构造最小 mock EditorContext（覆盖 set_cursor / handle_char_input 依赖槽）。"""
    calls: list = []
    if session is None:
        session = {"li": -1, "start_off": -1, "last_value": ""}

    ctx = types.SimpleNamespace(
        document=document,
        cursor_li=cursor_li,
        cursor_off=base,
        nav_seq=0,
        cursor_ref=FakeRef(_Cursor(base)),
        input_session_ref=FakeRef(session),
        outward_sel_ref=FakeRef(None),
        push_line_edit=lambda li, raw: calls.append(("push_line_edit", li, raw)),
        mark_dirty=lambda: calls.append("mark_dirty"),
        set_cursor_field_value=lambda v: calls.append(("set_cursor_field_value", v)),
        set_cursor_off=lambda off: calls.append(("set_cursor_off", off)),
        set_cursor_li=lambda li: calls.append(("set_cursor_li", li)),
        set_cursor_line=lambda li: calls.append(("set_cursor_line", li)),
        cursor_pulse_ref=FakeRef(0.0),
        # 占位防 AttributeError
        push_history=lambda: calls.append("push_history"),
        undo_push_pending=FakeRef(True),
        suppress_blur=FakeRef(False),
        set_clear_value_seq=lambda n: None,
        preferred_col_ref=FakeRef(None),
        # 多光标槽占位（handle_char_input 路径检查 secondary_cursors_ref）
        secondary_cursors_ref=FakeRef([]),
        broadcast_char_input=lambda removed, inserted: None,
        broadcast_submit=lambda v: None,
        # 粘贴进行中标志（handle_char_input 入口检测）
        paste_in_progress_ref=FakeRef(False),
    )

    def _set_nav_seq(n: int):
        """记录调用并同步 nav_seq（模拟真实 state setter 的递增语义）。"""
        calls.append(("set_nav_seq", n))
        ctx.nav_seq = n

    ctx.set_nav_seq = _set_nav_seq
    return ctx, calls


# ---------------- set_cursor：移动脉冲 ----------------

def test_set_cursor_same_line_move_bumps_nav_seq():
    """同行移动：递增 nav_seq → TextField key 变 → 重建 + 重聚焦（闪烁重启）。"""
    doc = Document(lines=[_para_line("hello")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0)
    cbs = build_cursor(ctx)
    cbs["set_cursor"](0, 3)
    assert ("set_nav_seq", 1) in calls
    assert ("set_cursor_off", 3) in calls
    assert ctx.cursor_ref.current.base == 3


def test_set_cursor_cross_line_move_no_pulse():
    """跨行移动不递增 nav_seq：key=li 已触发重建，移动脉冲仅服务同行移动。"""
    doc = Document(lines=[_para_line("hello"), _para_line("world")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=5)
    cbs = build_cursor(ctx)
    cbs["set_cursor"](1, 2)
    assert not any(name == "set_nav_seq" for name, *_ in calls)
    assert ("set_cursor_li", 1) in calls
    assert ("set_cursor_off", 2) in calls


def test_set_cursor_ends_session_and_still_bumps():
    """会话中移动：_end_input_session（rebuild）已递增 nav_seq，不再重复脉冲。"""
    doc = Document(lines=[_para_line("hello")])
    session = {"li": 0, "start_off": 0, "last_value": "hi"}
    ctx, calls = _make_ctx(doc, cursor_li=0, base=2, session=session)
    cbs = build_cursor(ctx)
    cbs["set_cursor"](0, 0)  # 光标不连续（会话末尾应为 2）→ 结束会话（重建）
    nav_seq_calls = [n for name, n in calls if name == "set_nav_seq"]
    assert nav_seq_calls == [1]
    # 会话被清空
    assert ctx.input_session_ref.current["li"] == -1


def test_set_cursor_same_position_noop():
    """同位置移动（点击同一位置）：零状态写入，不触发重建。"""
    doc = Document(lines=[_para_line("hello")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=3)
    cbs = build_cursor(ctx)
    cbs["set_cursor"](0, 3)
    # 无任何 cursor 状态写入 / nav_seq 递增
    assert calls == []


def test_set_cursor_same_line_pulse_throttled():
    """快速连击/长按：节流窗口内只脉冲一次，首个移动立即脉冲（即时可见）。"""
    doc = Document(lines=[_para_line("hello")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0)
    cbs = build_cursor(ctx)
    times = iter([10.0, 10.1, 10.4])
    with patch("views.editor._cursor._monotonic", side_effect=lambda: next(times)):
        cbs["set_cursor"](0, 1)  # t=10.0：首次脉冲
        cbs["set_cursor"](0, 2)  # t=10.1：窗口内（0.1s < 0.25s）→ 跳过
        cbs["set_cursor"](0, 3)  # t=10.4：距上次 0.4s ≥ 窗口 → 脉冲
    nav_seq_calls = [n for name, n in calls if name == "set_nav_seq"]
    assert nav_seq_calls == [1, 2]
    # 位置仍逐键更新（渲染实时，仅重建被节流）
    assert ("set_cursor_off", 1) in calls
    assert ("set_cursor_off", 2) in calls
    assert ("set_cursor_off", 3) in calls


def test_set_cursor_browse_mode_no_bump():
    """浏览态 set_cursor(None)：无 TextField 可重建，不递增 nav_seq。"""
    doc = Document(lines=[_para_line("hello")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0)
    cbs = build_cursor(ctx)
    cbs["set_cursor"](None)
    assert not any(name == "set_nav_seq" for name, *_ in calls)
    assert ("set_cursor_li", None) in calls


# ---------------- handle_char_input：不打字打断 IME ----------------

def test_char_input_does_not_bump_nav_seq():
    """打字路径不递增 nav_seq：TextField 不重建，IME 组合态保持。"""
    doc = Document(lines=[_para_line("hello")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0)
    cbs = build_cursor(ctx)
    cbs["handle_char_input"]("a")
    # 字符输入仅走会话镜像，绝不触发重建脉冲
    assert not any(name == "set_nav_seq" for name, *_ in calls)
    # 文档已更新且光标推进
    assert doc.lines[0].raw == "ahello"
    assert ctx.cursor_ref.current.base == 1
