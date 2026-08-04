"""空列表/任务项 Enter 退出转常规行时光标保留测试（Bug 修复）。

验证：
- 空无序列表项 "- " + Enter（光标在行尾）→ 转为空行，cursor_li 保留 + nav_seq 递增
- 空有序列表项 "1. " + Enter → 转为空行，cursor_li 保留 + nav_seq 递增
- 空任务项 "- [ ] " + Enter → 转为空行，cursor_li 保留 + nav_seq 递增
- 空引用 "> " + Enter → 转为空行，cursor_li 保留 + nav_seq 递增

Bug 根因：空列表按回车转常规行时 cursor_li 不变（同一行），若 input_session
不活跃则 nav_seq 也不递增 → use_effect 依赖 [cursor_li, nav_seq, ...] 均不变 →
不触发 _focus_cursor_field → block_type 变化导致 TextField 重建后新控件未聚焦 →
光标丢失。

修复：在"空内容→退出"分支中显式递增 nav_seq + 设置 suppress_blur，确保
use_effect 触发聚焦新 TextField。
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


def _uo_line(raw: str, level: int = 0) -> Line:
    """无序列表行：LIST_PREFIX + TEXT。"""
    marker = raw.rstrip() if raw.rstrip() in ("-", "*", "+") else raw.split(" ")[0]
    line = Line(block_type=BlockType.LIST_UO, raw=raw, level=level)
    line.segments = [
        Segment(SegType.LIST_PREFIX, marker, marker),
        Segment(SegType.TEXT, raw[len(marker):], raw[len(marker):]),
    ]
    return line


def _o_line(raw: str, level: int = 0) -> Line:
    """有序列表行：LIST_PREFIX + TEXT。"""
    import re
    m = re.match(r"^(\d+\.)\s*", raw)
    marker = m.group(1) if m else raw.split(" ")[0]
    line = Line(block_type=BlockType.LIST_O, raw=raw, level=level)
    line.segments = [
        Segment(SegType.LIST_PREFIX, marker, marker),
        Segment(SegType.TEXT, raw[len(marker):], raw[len(marker):]),
    ]
    return line


def _task_line(raw: str, checked: bool = False) -> Line:
    """任务列表行：LIST_PREFIX（含 [ ]/[x]）+ TEXT。"""
    marker = "- [x] " if checked else "- [ ] "
    line = Line(block_type=BlockType.LIST_UO, raw=raw, task=True)
    line.segments = [
        Segment(SegType.LIST_PREFIX, marker, marker),
        Segment(SegType.TEXT, raw[len(marker):], raw[len(marker):]),
    ]
    return line


def _quote_line(raw: str) -> Line:
    """引用行：QUOTE_PREFIX + TEXT。"""
    marker = "> "
    line = Line(block_type=BlockType.QUOTE, raw=raw)
    line.segments = [
        Segment(SegType.QUOTE_PREFIX, marker, marker),
        Segment(SegType.TEXT, raw[len(marker):], raw[len(marker):]),
    ]
    return line


def _make_ctx(document: Document, cursor_li: int, base: int,
              session: dict | None = None) -> tuple[types.SimpleNamespace, list]:
    """构造最小 mock EditorContext。session 默认不活跃（li=-1）模拟点击后直接回车。"""
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
        paste_in_progress_ref=FakeRef(False),
        secondary_cursors_ref=FakeRef([]),
        preferred_col_ref=FakeRef(None),
        push_history=lambda: calls.append("push_history"),
        undo_push_pending=FakeRef(True),
        mark_dirty=lambda: calls.append("mark_dirty"),
        suppress_blur=FakeRef(False),
        set_cursor_field_value=lambda v: calls.append(("set_cursor_field_value", v)),
        set_cursor_off=lambda off: calls.append(("set_cursor_off", off)),
        set_cursor_li=lambda li: calls.append(("set_cursor_li", li)),
        set_cursor_line=lambda li: calls.append(("set_cursor_line", li)),
        set_nav_seq=lambda n: calls.append(("set_nav_seq", n)),
        set_clear_value_seq=lambda n: None,
        clear_secondary_cursors=lambda: calls.append("clear_secondary_cursors"),
        broadcast_submit=lambda v: None,
    )
    return ctx, calls


def _assert_cursor_preserved(calls: list, ctx, li: int):
    """断言光标保留：cursor_li 不变 + nav_seq 递增 + suppress_blur 设置。"""
    assert ("set_cursor_li", li) in calls, f"cursor_li 应保留为 {li}"
    assert ("set_nav_seq", 1) in calls, "nav_seq 应递增以触发 use_effect 聚焦"
    assert ctx.suppress_blur.current is True, "suppress_blur 应设置防止 blur 干扰"


# ================ 空无序列表项 Enter 退出 ================

def test_empty_uo_list_enter_preserves_cursor():
    """空无序列表项 "- " + Enter（光标在行尾）→ 转空行，cursor_li 保留 + nav_seq 递增。

    Bug：cursor_li 不变 + input_session 不活跃 → nav_seq 不递增 → use_effect 不触发聚焦 → 光标丢失。
    修复：显式递增 nav_seq 确保 use_effect 触发聚焦新 TextField。
    """
    doc = Document(lines=[_uo_line("- ")])
    # 光标在行尾 offset=2，input_session 不活跃（模拟点击进入后直接回车）
    ctx, calls = _make_ctx(doc, cursor_li=0, base=2)
    on_submit = build_cursor(ctx)["on_submit"]

    on_submit("- ")

    # 行脱离列表（空内容 reparse 为 BLANK）
    assert doc.lines[0].block_type != BlockType.LIST_UO
    assert doc.lines[0].raw == ""
    _assert_cursor_preserved(calls, ctx, 0)


# ================ 空有序列表项 Enter 退出 ================

def test_empty_o_list_enter_preserves_cursor():
    """空有序列表项 "1. " + Enter → 转空行，cursor_li 保留 + nav_seq 递增。"""
    doc = Document(lines=[_o_line("1. ")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=3)
    on_submit = build_cursor(ctx)["on_submit"]

    on_submit("1. ")

    assert doc.lines[0].block_type != BlockType.LIST_O
    assert doc.lines[0].raw == ""
    _assert_cursor_preserved(calls, ctx, 0)


# ================ 空任务列表项 Enter 退出 ================

def test_empty_task_list_enter_preserves_cursor():
    """空任务项 "- [ ] " + Enter → 转空行，cursor_li 保留 + nav_seq 递增。"""
    doc = Document(lines=[_task_line("- [ ] ", checked=False)])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=5)
    on_submit = build_cursor(ctx)["on_submit"]

    on_submit("- [ ] ")

    assert doc.lines[0].raw == ""
    assert not doc.lines[0].task
    _assert_cursor_preserved(calls, ctx, 0)


def test_empty_checked_task_list_enter_preserves_cursor():
    """空已勾选任务项 "- [x] " + Enter → 转空行，cursor_li 保留 + nav_seq 递增。"""
    doc = Document(lines=[_task_line("- [x] ", checked=True)])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=5)
    on_submit = build_cursor(ctx)["on_submit"]

    on_submit("- [x] ")

    assert doc.lines[0].raw == ""
    assert not doc.lines[0].task
    _assert_cursor_preserved(calls, ctx, 0)


# ================ 空引用 Enter 退出 ================

def test_empty_quote_enter_preserves_cursor():
    """空引用 "> " + Enter（光标在行首）→ 转空行，cursor_li 保留 + nav_seq 递增。

    引用退出走分支1（not before.strip()）：光标在行首 before="" 时触发。
    光标在行尾（before="> "）走默认分割续行（Typora 式：续行加 "> " 前缀）。
    """
    doc = Document(lines=[_quote_line("> ")])
    # 光标在行首 offset=0 → before="" → 进入分支1
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0)
    on_submit = build_cursor(ctx)["on_submit"]

    on_submit("")

    assert doc.lines[0].block_type != BlockType.QUOTE
    assert doc.lines[0].raw == ""
    _assert_cursor_preserved(calls, ctx, 0)


# ================ 有内容列表 Enter 分割（不受影响，回归测试） ================

def test_nonempty_uo_list_enter_splits():
    """有内容列表项 "- text" + Enter → 分割为两行，cursor_li 变化（li→li+1）。

    回归测试：有内容时走默认分割分支，cursor_li 变化触发 use_effect 聚焦，
    不受此 Bug 影响也不受修复影响。
    """
    doc = Document(lines=[_uo_line("- text")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=6)
    on_submit = build_cursor(ctx)["on_submit"]

    on_submit("- text")

    # 分割为两行
    assert len(doc.lines) == 2
    # cursor_li 变为 li+1（触发 use_effect 聚焦）
    assert ("set_cursor_li", 1) in calls


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
