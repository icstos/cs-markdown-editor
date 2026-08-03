"""handle_char_input 的 IME composing 取消/缩短测试。

验证 composing 期间（未上屏）按 Enter/Esc/Backspace 导致 IME 放弃或缩短
composing 时，on_change 的 value 为 last_value 的真前缀，handle_char_input
应裁剪文档区域移除废字符，而非忽略（旧 BUG：ignore 分支直接 return 导致
composing 英文残留编辑区）。

直接构造最小 mock ctx 调用 build_cursor(ctx)["handle_char_input"]，验证：
- composing 取消：last_value="你vb" + value="你" → 文档区域裁剪为 "你"
- composing 全部放弃：last_value="vb" + value="" → 文档区域裁剪为 ""
- composing 缩短（Backspace）：last_value="你vb" + value="你v" → 文档区域裁剪为 "你v"
- 正常 composing 追加不受影响：last_value="v" + value="vb" → 文档追加 "b"
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
    """构造最小 mock EditorContext，仅含 handle_char_input 依赖的槽。"""
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
    )
    return ctx, calls


# ---------------- composing 取消（Enter/Esc）----------------

def test_composing_cancel_trims_document():
    """composing 取消：last_value="你vb" + value="你" → 文档裁剪为 "你"。

    典型场景：五笔 composing "vb"（未上屏）按 Enter，IME 放弃 "vb"，
    on_change 的 value="你"（已上屏部分），文档区域应裁剪移除 "vb"。
    """
    doc = Document(lines=[_para_line("你vb")])
    session = {"li": 0, "start_off": 0, "last_value": "你vb"}
    ctx, calls = _make_ctx(doc, cursor_li=0, base=3, session=session)
    handle_char_input = build_cursor(ctx)["handle_char_input"]

    handle_char_input("你")

    # 文档区域裁剪为 "你"
    assert doc.lines[0].raw == "你"
    # session last_value 更新为 "你"
    assert session["last_value"] == "你"
    # 光标移到 "你" 之后（offset 1）
    assert ctx.cursor_ref.current.base == 1
    # mark_dirty 被调用
    assert "mark_dirty" in calls
    # cursor_field_value 同步为 "你"
    assert ("set_cursor_field_value", "你") in calls


def test_composing_cancel_third_char_trims():
    """第三字 composing 取消：last_value="你好kb" + value="你好" → 文档裁剪为 "你好"。"""
    doc = Document(lines=[_para_line("你好kb")])
    session = {"li": 0, "start_off": 0, "last_value": "你好kb"}
    ctx, calls = _make_ctx(doc, cursor_li=0, base=4, session=session)
    handle_char_input = build_cursor(ctx)["handle_char_input"]

    handle_char_input("你好")

    assert doc.lines[0].raw == "你好"
    assert session["last_value"] == "你好"
    assert ctx.cursor_ref.current.base == 2


def test_composing_full_cancel_empty():
    """composing 全部放弃（无已上屏文本）：last_value="vb" + value="" → 文档裁剪为 ""。"""
    doc = Document(lines=[_para_line("vb")])
    session = {"li": 0, "start_off": 0, "last_value": "vb"}
    ctx, calls = _make_ctx(doc, cursor_li=0, base=2, session=session)
    handle_char_input = build_cursor(ctx)["handle_char_input"]

    handle_char_input("")

    assert doc.lines[0].raw == ""
    assert session["last_value"] == ""
    assert ctx.cursor_ref.current.base == 0


def test_composing_cancel_pinyin():
    """拼音 composing 取消：last_value="你ha" + value="你" → 文档裁剪为 "你"。"""
    doc = Document(lines=[_para_line("你ha")])
    session = {"li": 0, "start_off": 0, "last_value": "你ha"}
    ctx, calls = _make_ctx(doc, cursor_li=0, base=3, session=session)
    handle_char_input = build_cursor(ctx)["handle_char_input"]

    handle_char_input("你")

    assert doc.lines[0].raw == "你"
    assert session["last_value"] == "你"
    assert ctx.cursor_ref.current.base == 1


# ---------------- composing 缩短（Backspace）----------------

def test_composing_shrink_backspace():
    """composing 期间 Backspace：last_value="你vb" + value="你v" → 文档裁剪为 "你v"。"""
    doc = Document(lines=[_para_line("你vb")])
    session = {"li": 0, "start_off": 0, "last_value": "你vb"}
    ctx, calls = _make_ctx(doc, cursor_li=0, base=3, session=session)
    handle_char_input = build_cursor(ctx)["handle_char_input"]

    handle_char_input("你v")

    assert doc.lines[0].raw == "你v"
    assert session["last_value"] == "你v"
    assert ctx.cursor_ref.current.base == 2


# ---------------- composing 取消后 on_submit 正常分割 ----------------

def test_composing_cancel_then_submit_no_double_trim():
    """composing 取消后再 on_submit：last_value 已裁剪为 "你"，on_submit 不重复裁剪。"""
    doc = Document(lines=[_para_line("你vb")])
    session = {"li": 0, "start_off": 0, "last_value": "你vb"}
    ctx, calls = _make_ctx(doc, cursor_li=0, base=3, session=session)
    # handle_char_input 与 on_submit 共享同一 ctx（同一 session）
    cbs = build_cursor(ctx)
    handle_char_input = cbs["handle_char_input"]
    on_submit = cbs["on_submit"]

    # Step 1: on_change 触发，composing 取消
    handle_char_input("你")
    assert doc.lines[0].raw == "你"
    assert session["last_value"] == "你"

    # Step 2: on_submit 触发（value="你"），不重复裁剪，正常分割
    on_submit("你")
    # 行被分割：原行 "你" + 新行
    assert len(doc.lines) == 2
    assert doc.lines[0].raw == "你"


# ---------------- 正常 composing 不受影响 ----------------

def test_normal_compose_append_not_affected():
    """正常 composing 追加：last_value="v" + value="vb" → 文档追加 "b"（非裁剪）。"""
    doc = Document(lines=[_para_line("v")])
    session = {"li": 0, "start_off": 0, "last_value": "v"}
    ctx, calls = _make_ctx(doc, cursor_li=0, base=1, session=session)
    handle_char_input = build_cursor(ctx)["handle_char_input"]

    handle_char_input("vb")

    # 正常追加：文档变为 "vb"
    assert doc.lines[0].raw == "vb"
    assert session["last_value"] == "vb"
    assert ctx.cursor_ref.current.base == 2


def test_ime_compose_commit_not_affected():
    """IME 组合完成上屏：last_value="wq" + value="你" → replace（非裁剪）。"""
    doc = Document(lines=[_para_line("wq")])
    session = {"li": 0, "start_off": 0, "last_value": "wq"}
    ctx, calls = _make_ctx(doc, cursor_li=0, base=2, session=session)
    handle_char_input = build_cursor(ctx)["handle_char_input"]

    handle_char_input("你")

    # replace：文档区域 "wq" 被替换为 "你"
    assert doc.lines[0].raw == "你"
    assert session["last_value"] == "你"
    assert ctx.cursor_ref.current.base == 1


def test_same_value_ignored():
    """相同值 → 忽略（防重复 on_change）：文档不变。"""
    doc = Document(lines=[_para_line("你vb")])
    session = {"li": 0, "start_off": 0, "last_value": "你vb"}
    ctx, calls = _make_ctx(doc, cursor_li=0, base=3, session=session)
    handle_char_input = build_cursor(ctx)["handle_char_input"]

    handle_char_input("你vb")

    # 文档不变
    assert doc.lines[0].raw == "你vb"
    assert session["last_value"] == "你vb"
    # mark_dirty 不被调用
    assert "mark_dirty" not in calls


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
