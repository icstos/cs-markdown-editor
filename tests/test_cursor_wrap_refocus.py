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
    # 与真实 state setter 一致：同步 cursor_off 属性（会话结束后新会话起点依赖它）
    ctx.set_cursor_off = lambda off: (calls.append(("set_cursor_off", off)), setattr(ctx, "cursor_off", off))
    return ctx, calls


def test_wrap_triggers_refocus():
    """输入触发软换行（视觉行数变化）→ 结束会话 + nav_seq 重建重聚焦。

    会话值跨视觉行时单行 TextField 无法正确布局（光标脱离文字），且 value
    镜像跨边界会漂移 → 结束会话（cursor_field_value=""）+ nav_seq 重建，
    光标直接定位到当前位置，后续输入从新会话开始。
    """
    doc = Document(lines=[_para_line("")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0, content_width=200.0)
    handle_char_input = build_cursor(ctx)["handle_char_input"]

    # 一次性插入 19 个 CJK 字（19 * ~16px ≈ 304px > 200px 换行宽度）→ 1 vline → 2 vline
    handle_char_input("你" * 19)

    assert doc.lines[0].raw == "你" * 19
    # 触发软换行 → nav_seq 递增（重建 TextField + 重聚焦，保持光标）
    assert ("set_nav_seq", 1) in calls, f"应重建重聚焦，calls={calls}"
    # 会话已结束：value 镜像清空（跨视觉行会话不再保留）
    assert ("set_cursor_field_value", "") in calls, f"应清空会话 value，calls={calls}"
    assert ctx.input_session_ref.current == {"li": -1, "start_off": -1, "last_value": ""}, \
        f"会话应被重置，calls={calls}"


def test_no_wrap_no_refocus():
    """输入未触发软换行（视觉行数不变）→ 不重建、不重聚焦。"""
    doc = Document(lines=[_para_line("")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0, content_width=200.0)
    handle_char_input = build_cursor(ctx)["handle_char_input"]

    # 单个 CJK 字（~16px <= 200px）→ 仍 1 vline
    handle_char_input("你")

    assert doc.lines[0].raw == "你"
    # 未触发换行 → 不重建（nav_seq/focus_seq 均不变），会话保持
    assert not any(name in ("set_nav_seq", "set_focus_seq") for name, *_ in calls), \
        f"不应重建/重聚焦，calls={calls}"
    assert ctx.input_session_ref.current["last_value"] == "你"


def test_wrap_keeps_document_intact():
    """软换行触发后文档内容不丢失（整行丢失 BUG 回归）。"""
    doc = Document(lines=[_para_line("")])
    ctx, _calls = _make_ctx(doc, cursor_li=0, base=0, content_width=200.0)
    handle_char_input = build_cursor(ctx)["handle_char_input"]

    handle_char_input("你" * 19)
    assert doc.lines[0].raw == "你" * 19

    # 会话结束后继续输入：新会话逐字累积（每事件 +1 字符）跨边界增长，内容不被吞
    for n in range(20, 25):
        handle_char_input("你" * (n - 19))
        assert doc.lines[0].raw == "你" * n, f"继续输入不应丢字，n={n} raw={doc.lines[0].raw!r}"


def test_sequential_repeat_input_no_doubling_collapse():
    """连续输入相同 CJK 字符不被 _fix_ime_doubling 误折叠（整行丢失根因）。

    逐字累积（每轮 +1 字符）："你" → "你你" → "你你你" → "你你你你"，
    第 4 个字符起 value 形如 X+X，旧启发式误判为 IME 翻倍折叠吞字。
    """
    doc = Document(lines=[_para_line("")])
    ctx, _calls = _make_ctx(doc, cursor_li=0, base=0, content_width=200.0)
    handle_char_input = build_cursor(ctx)["handle_char_input"]

    handle_char_input("你")
    handle_char_input("你你")
    handle_char_input("你你你")
    handle_char_input("你你你你")

    assert doc.lines[0].raw == "你你你你", f"连续输入被误折叠，raw={doc.lines[0].raw!r}"


def test_ime_commit_repeat_no_doubling_collapse():
    """IME 上屏后行内容形如 X+X（含 composing 残留 last_value）不被误折叠。

    满行末尾输入拼音触发软换行后继续上屏：last_value="你"*13+"i"（composing
    残留），value="你"*14 为合法内容，旧启发式折叠成 "你"*7（整行丢失）。
    """
    doc = Document(lines=[_para_line("你" * 13 + "i")])
    ctx, _calls = _make_ctx(doc, cursor_li=0, base=14, content_width=200.0,
                            session={"li": 0, "start_off": 0, "last_value": "你" * 13 + "i"})
    handle_char_input = build_cursor(ctx)["handle_char_input"]

    handle_char_input("你" * 14)

    assert doc.lines[0].raw == "你" * 14, f"上屏内容被误折叠，raw={doc.lines[0].raw!r}"



def test_backspace_unwrap_triggers_refocus():
    """Backspace 使行收拢（无活动会话）→ 递增 focus_seq 重聚焦。"""
    # 13 个 CJK 字（13*16px ≈ 208px > 200px）→ 2 vlines；删 1 字 → 12 字 1 vline
    doc = Document(lines=[_para_line("你" * 13)])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=13, content_width=200.0)
    backspace_core = build_cursor(ctx)["backspace_core"]

    backspace_core()

    assert doc.lines[0].raw == "你" * 12
    # 行收拢（2 vline → 1 vline）→ focus_seq 递增（重聚焦，保持光标）
    assert ("set_focus_seq", 1) in calls, f"应触发重聚焦，calls={calls}"
    # 无活动会话时不重建 TextField（nav_seq 不递增）
    assert not any(name == "set_nav_seq" for name, *_ in calls), \
        f"Backspace 收拢不应递增 nav_seq，calls={calls}"


def test_backspace_unwrap_active_session_rebuilds():
    """Backspace 使行收拢（活动会话跨边界）→ 结束会话 + nav_seq 重建重聚焦。"""
    doc = Document(lines=[_para_line("你" * 13)])
    ctx, calls = _make_ctx(
        doc, cursor_li=0, base=13, content_width=200.0,
        session={"li": 0, "start_off": 0, "last_value": "你" * 13},
    )
    backspace_core = build_cursor(ctx)["backspace_core"]

    backspace_core()

    assert doc.lines[0].raw == "你" * 12
    # 活动会话跨边界收拢 → 结束会话 + nav_seq 重建（光标重新定位）
    assert ("set_nav_seq", 1) in calls, f"应重建重聚焦，calls={calls}"
    assert ("set_cursor_field_value", "") in calls, f"应清空会话 value，calls={calls}"
    assert ctx.input_session_ref.current == {"li": -1, "start_off": -1, "last_value": ""}



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
