"""handle_char_input 软换行触发时重聚焦（保持光标不丢失）测试。

验证：开启换行时，输入使行的视觉行数变化（软换行/收拢）时，handle_char_input
递增 focus_seq 强制重聚焦（不重建 TextField，保 IME 组合态）；未触发换行时不递增。

背景 BUG：中文输入法在满行末尾输入第一个拼音字母触发软换行时，cursor TextField
的 top 属性从 0 跳变到 vline_idx*text_h，Flutter 端属性更新短暂移除焦点，而
cursor_li/nav_seq/focus_seq/word_wrap/viewport_w 均不变 → focus effect 不触发，
光标丢失、组合态被中断、无法继续编辑。
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


class _Cursor:
    def __init__(self, b):
        self.base = b
        self.extent = b

    def reset(self, off, raw_len):
        self.base = off
        self.extent = off


def _para_line(raw: str) -> Line:
    line = Line(block_type=BlockType.PARAGRAPH, raw=raw)
    line.segments = [Segment(SegType.TEXT, raw, raw)]
    return line


def _make_ctx(document: Document, cursor_li: int, base: int,
              content_width: float = 200.0,
              session: dict | None = None) -> tuple[types.SimpleNamespace, list]:
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
        set_nav_seq=lambda n: calls.append(("set_nav_seq", n)),
        push_history=lambda: calls.append("push_history"),
        undo_push_pending=FakeRef(True),
        suppress_blur=FakeRef(False),
        set_clear_value_seq=lambda n: None,
        preferred_col_ref=FakeRef(None),
        secondary_cursors_ref=FakeRef([]),
        broadcast_char_input=lambda removed, inserted: None,
        broadcast_submit=lambda v: None,
        broadcast_backspace=lambda: None,
        broadcast_delete=lambda: None,
        paste_in_progress_ref=FakeRef(False),
        # 软换行检测依赖的排版字段 + 重聚焦槽
        body_font_size=16,
        line_height=1.6,
        content_width=content_width,
        focus_seq=0,
        set_focus_seq=lambda n: calls.append(("set_focus_seq", n)),
    )
    return ctx, calls


def test_wrap_triggers_refocus():
    """输入触发软换行（视觉行数变化）→ 递增 focus_seq 重聚焦。"""
    doc = Document(lines=[_para_line("")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0, content_width=200.0)
    handle_char_input = build_cursor(ctx)["handle_char_input"]

    # 一次性插入 19 个 CJK 字（奇数长度避开 _fix_ime_doubling；19 * ~16px ≈ 304px
    # > 200px 换行宽度）→ 1 vline → 2 vline
    handle_char_input("你" * 19)

    assert doc.lines[0].raw == "你" * 19
    # 触发软换行 → focus_seq 被递增（重聚焦，保持光标）
    assert ("set_focus_seq", 1) in calls, f"应触发重聚焦，calls={calls}"


def test_no_wrap_no_refocus():
    """输入未触发软换行（视觉行数不变）→ 不递增 focus_seq。"""
    doc = Document(lines=[_para_line("")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0, content_width=200.0)
    handle_char_input = build_cursor(ctx)["handle_char_input"]

    # 单个 CJK 字（~16px <= 200px）→ 仍 1 vline
    handle_char_input("你")

    assert doc.lines[0].raw == "你"
    # 未触发换行 → 不递增 focus_seq
    assert not any(name == "set_focus_seq" for name, *_ in calls), \
        f"不应触发重聚焦，calls={calls}"


def test_wrap_refocus_keeps_nav_seq_unchanged():
    """软换行重聚焦不重建 TextField（不递增 nav_seq，保 IME 组合态）。"""
    doc = Document(lines=[_para_line("")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0, content_width=200.0)
    handle_char_input = build_cursor(ctx)["handle_char_input"]

    handle_char_input("你" * 19)

    assert ("set_focus_seq", 1) in calls
    # 纯软换行（无块级前缀变化）不应重建 TextField（nav_seq 不递增）
    assert not any(name == "set_nav_seq" for name, *_ in calls), \
        f"软换行不应递增 nav_seq，calls={calls}"


def test_backspace_unwrap_triggers_refocus():
    """Backspace 使行收拢（视觉行数变化）→ 递增 focus_seq 重聚焦。"""
    # 13 个 CJK 字（13*16px ≈ 208px > 200px）→ 2 vlines；删 1 字 → 12 字 1 vline
    doc = Document(lines=[_para_line("你" * 13)])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=13, content_width=200.0)
    backspace_core = build_cursor(ctx)["backspace_core"]

    backspace_core()

    assert doc.lines[0].raw == "你" * 12
    # 行收拢（2 vline → 1 vline）→ focus_seq 递增（重聚焦，保持光标）
    assert ("set_focus_seq", 1) in calls, f"应触发重聚焦，calls={calls}"
    # 不重建 TextField（_move_cursor_inline 路径不递增 nav_seq）
    assert not any(name == "set_nav_seq" for name, *_ in calls), \
        f"Backspace 收拢不应递增 nav_seq，calls={calls}"


def test_backspace_no_unwrap_no_refocus():
    """Backspace 未跨视觉行边界（视觉行数不变）→ 不递增 focus_seq。"""
    # 19 个 CJK 字 2 vlines；删 1 字 → 18 字仍 2 vlines（不跨边界）
    doc = Document(lines=[_para_line("你" * 19)])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=19, content_width=200.0)
    backspace_core = build_cursor(ctx)["backspace_core"]

    backspace_core()

    assert doc.lines[0].raw == "你" * 18
    assert not any(name == "set_focus_seq" for name, *_ in calls), \
        f"未跨边界不应重聚焦，calls={calls}"


def test_delete_unwrap_triggers_refocus():
    """Delete 使行收拢（视觉行数变化）→ 递增 focus_seq 重聚焦。"""
    doc = Document(lines=[_para_line("你" * 13)])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=12, content_width=200.0)
    delete_core = build_cursor(ctx)["delete_core"]

    delete_core()

    assert doc.lines[0].raw == "你" * 12
    # 行收拢（2 vline → 1 vline）→ focus_seq 递增
    assert ("set_focus_seq", 1) in calls, f"应触发重聚焦，calls={calls}"


def test_delete_no_unwrap_no_refocus():
    """Delete 未跨视觉行边界（视觉行数不变）→ 不递增 focus_seq。"""
    doc = Document(lines=[_para_line("你" * 19)])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0, content_width=200.0)
    delete_core = build_cursor(ctx)["delete_core"]

    delete_core()

    assert doc.lines[0].raw == "你" * 18
    assert not any(name == "set_focus_seq" for name, *_ in calls), \
        f"未跨边界不应重聚焦，calls={calls}"



if __name__ == "__main__":
    pytest.main([__file__, "-v"])
