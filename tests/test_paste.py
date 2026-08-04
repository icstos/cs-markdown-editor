"""粘贴功能单元测试（Typora 式智能粘贴 + 纯文本粘贴）。

验证：
- Ctrl+V 单行粘贴：正确插入，无重复、无拼接
- Ctrl+V 多行粘贴：正确拆分换行，保持行结构（修复"所有行拼接成一行"Bug）
- Ctrl+Shift+V 纯文本粘贴：剥离 Markdown 语法后插入
- paste_in_progress 标志管理：粘贴后重置，handle_char_input 不被永久拦截
- 单行粘贴 + paste_in_progress：手动递增 nav_seq 重建 TextField
- 多光标智能粘贴：行数匹配时逐行分配
- 多光标非智能粘贴：行数不匹配时回退单光标
- 边界场景：空文本 / 浏览态 / 围栏行跳过

直接构造最小 mock ctx 调用 build_cursor(ctx)["handle_paste"] /
build_multi_cursor(ctx)["paste_to_multi_cursors"]，验证文档状态 + 标志重置。
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from models import BlockType, Document, Line, Segment, SegType  # noqa: E402
from views.editor._cursor import build_cursor  # noqa: E402
from views.editor._multi_cursor import build_multi_cursor  # noqa: E402


class FakeRef:
    """伪 ft.Ref：避免 flet.Ref weakref 限制。"""

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


def _make_ctx(
    document: Document,
    cursor_li: int | None,
    base: int,
    *,
    session: dict | None = None,
    paste_active: bool = False,
    secondary_cursors: list | None = None,
) -> tuple[types.SimpleNamespace, list]:
    """构造最小 mock EditorContext，含 handle_paste / paste_to_multi_cursors 依赖的槽。

    paste_active=True 时初始化 paste_in_progress_ref.current=True，模拟
    KeyDispatcher._begin_paste 设置的粘贴进行中状态。
    """
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
        paste_in_progress_ref=FakeRef(paste_active),
        secondary_cursors_ref=FakeRef(list(secondary_cursors) if secondary_cursors else []),
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
        set_secondary_cursors=lambda lst: calls.append(("set_secondary_cursors", list(lst))),
        set_secondary_cursors_version=lambda v: calls.append(("set_secondary_cursors_version", v)),
        secondary_cursors_version=0,
        push_line_edit=lambda li, raw: calls.append(("push_line_edit", li, raw)),
        # end_input_session 由 build_cursor 返回，此处占位（paste_to_multi_cursors 用）
        end_input_session=lambda: calls.append("end_input_session"),
        # 多光标 broadcast / clear 占位（paste 路径不调用，防 AttributeError）
        broadcast_char_input=lambda removed, inserted: None,
        broadcast_submit=lambda v: None,
        clear_secondary_cursors=lambda: calls.append("clear_secondary_cursors"),
        set_clear_value_seq=lambda n: None,
    )
    return ctx, calls


# ================ 单行粘贴（Ctrl+V） ================

def test_single_line_paste_inserts_at_cursor():
    """单行粘贴：在光标处插入文本，无重复、无拼接。"""
    doc = Document(lines=[_para_line("hello world")])
    # 光标在 "hello " 之后（offset=6）
    ctx, calls = _make_ctx(doc, cursor_li=0, base=6)
    handle_paste = build_cursor(ctx)["handle_paste"]

    handle_paste("插入")

    # 文档行变为 "hello 插入world"
    assert doc.lines[0].raw == "hello 插入world"
    # 仅 1 行（未拼接、未拆分）
    assert len(doc.lines) == 1
    # 光标移到 "插入" 之后（offset=8）
    assert ctx.cursor_ref.current.base == 8
    # 历史入栈 + 脏标记
    assert "push_history" in calls
    assert "mark_dirty" in calls


def test_single_line_paste_at_line_start():
    """单行粘贴在行首：文本插入到行首。"""
    doc = Document(lines=[_para_line("world")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0)
    handle_paste = build_cursor(ctx)["handle_paste"]

    handle_paste("hello ")

    assert doc.lines[0].raw == "hello world"
    assert ctx.cursor_ref.current.base == 6


def test_single_line_paste_at_line_end():
    """单行粘贴在行尾：文本追加到行尾。"""
    doc = Document(lines=[_para_line("hello")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=5)
    handle_paste = build_cursor(ctx)["handle_paste"]

    handle_paste(" world")

    assert doc.lines[0].raw == "hello world"
    assert ctx.cursor_ref.current.base == 11


def test_single_line_paste_with_paste_in_progress_resets_flag():
    """paste_in_progress 路径的单行粘贴：插入后重置标志 + 递增 nav_seq 重建 TextField。

    这是 Ctrl+V 的关键路径：KeyDispatcher._begin_paste 设置 paste_in_progress=True，
    handle_paste 插入完成后必须重置为 False（否则 handle_char_input 永久被拦截），
    且单行粘贴 cursor_li 不变，需手动递增 nav_seq 重建 TextField 清空 Flutter 端 value。
    """
    doc = Document(lines=[_para_line("abc")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=1, paste_active=True)
    handle_paste = build_cursor(ctx)["handle_paste"]

    handle_paste("XY")

    # 文档变为 "aXYbc"
    assert doc.lines[0].raw == "aXYbc"
    # paste_in_progress 已重置
    assert ctx.paste_in_progress_ref.current is False
    # input_session 已清空
    assert ctx.input_session_ref.current["li"] == -1
    # cursor_field_value 已清空
    assert ("set_cursor_field_value", "") in calls
    # nav_seq 递增（重建 TextField）
    assert ("set_nav_seq", 1) in calls


def test_single_line_paste_without_paste_in_progress_no_rebuild():
    """非 paste_in_progress 路径的单行粘贴（程序调用）：不递增 nav_seq。

    程序调用 handle_paste（如拖拽插入）不走 paste_in_progress 路径，
    不应触发 nav_seq 重建（避免不必要的控件销毁重建）。
    """
    doc = Document(lines=[_para_line("abc")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=1, paste_active=False)
    handle_paste = build_cursor(ctx)["handle_paste"]

    handle_paste("XY")

    assert doc.lines[0].raw == "aXYbc"
    # nav_seq 不递增
    nav_seq_calls = [c for c in calls if isinstance(c, tuple) and c[:1] == ("set_nav_seq",)]
    assert nav_seq_calls == []


# ================ 多行粘贴（Ctrl+V）—— Bug 修复核心 ================

def test_multi_line_paste_splits_into_lines():
    """多行粘贴：文本拆分为多行，保持行结构（修复"所有行拼接成一行"Bug）。

    Bug 根因：原生单行 TextField（multiline=False）会把多行文本的 \\n 移除拼接成
    一行触发 on_change，与 handle_paste 形成重复插入 + 行拼接。

    修复：通过 paste_in_progress_ref 标志拦截原生 on_change，统一由 handle_paste
    处理多行拆分。
    """
    doc = Document(lines=[_para_line("start end")])
    # 光标在 "start " 之后（offset=6）
    ctx, calls = _make_ctx(doc, cursor_li=0, base=6, paste_active=True)
    handle_paste = build_cursor(ctx)["handle_paste"]

    handle_paste("line1\nline2\nline3")

    # 原行 "start end" 在 offset=6 处分割：before="start " after="end"
    # parts=["line1","line2","line3"] → 3 行：
    #   line0 = "start line1"，line1 = "line2"，line2 = "line3" + "end" = "line3end"
    assert len(doc.lines) == 3
    assert doc.lines[0].raw == "start line1"
    assert doc.lines[1].raw == "line2"
    assert doc.lines[2].raw == "line3end"
    # paste_in_progress 已重置
    assert ctx.paste_in_progress_ref.current is False
    # 光标移到最后一行 "line3" 之后（"line3end" 中 offset=5）
    assert ctx.cursor_ref.current.base == 5


def test_multi_line_paste_at_line_start():
    """多行粘贴在行首：原行内容移到最后一行末尾。"""
    doc = Document(lines=[_para_line("original")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0)
    handle_paste = build_cursor(ctx)["handle_paste"]

    handle_paste("a\nb")

    assert len(doc.lines) == 2
    assert doc.lines[0].raw == "a"
    assert doc.lines[1].raw == "boriginal"


def test_multi_line_paste_at_line_end():
    """多行粘贴在行尾：原行内容保留在第一行末尾。"""
    doc = Document(lines=[_para_line("original")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=8)
    handle_paste = build_cursor(ctx)["handle_paste"]

    handle_paste("a\nb")

    assert len(doc.lines) == 2
    assert doc.lines[0].raw == "originala"
    assert doc.lines[1].raw == "b"


def test_multi_line_paste_with_paste_in_progress_resets_flag():
    """多行粘贴 paste_in_progress 路径：重置标志，cursor_li 变化自动重建 TextField。"""
    doc = Document(lines=[_para_line("abc")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=1, paste_active=True)
    handle_paste = build_cursor(ctx)["handle_paste"]

    handle_paste("x\ny")

    # 原行 "abc" 在 offset=1 处分割：before="a" after="bc"
    # parts=["x","y"] → 2 行：line0="ax"，line1="y"+"bc"="ybc"
    assert len(doc.lines) == 2
    assert doc.lines[0].raw == "ax"
    assert doc.lines[1].raw == "ybc"
    # paste_in_progress 已重置
    assert ctx.paste_in_progress_ref.current is False
    # input_session 已清空
    assert ctx.input_session_ref.current["li"] == -1
    # 多行粘贴 cursor_li 变化自动重建，不手动递增 nav_seq
    nav_seq_calls = [c for c in calls if isinstance(c, tuple) and c[:1] == ("set_nav_seq",)]
    assert nav_seq_calls == []


def test_multi_line_paste_preserves_line_structure():
    """多行粘贴保持完整行结构：3 行文本 + 原行分割 = 4 行。"""
    doc = Document(lines=[_para_line("HEADTAIL")])
    # 光标在 "HEAD" 之后（offset=4）
    ctx, calls = _make_ctx(doc, cursor_li=0, base=4)
    handle_paste = build_cursor(ctx)["handle_paste"]

    handle_paste("第一行\n第二行\n第三行")

    # 原行 "HEADTAIL" 在 offset=4 处分割：before="HEAD" after="TAIL"
    # parts=["第一行","第二行","第三行"] → 3 行：
    #   line0="HEAD第一行"，line1="第二行"，line2="第三行"+"TAIL"="第三行TAIL"
    assert len(doc.lines) == 3
    assert doc.lines[0].raw == "HEAD第一行"
    assert doc.lines[1].raw == "第二行"
    assert doc.lines[2].raw == "第三行TAIL"


def test_multi_line_paste_does_not_concatenate():
    """回归测试：多行粘贴不得将所有行拼接成一行。

    这是 Bug 修复的核心验证：旧版本会因原生 TextField on_change 干扰，
    将 "a\\nb\\nc" 拼接成 "abc" 一行。修复后必须保持 3 行结构。
    """
    doc = Document(lines=[_para_line("")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0, paste_active=True)
    handle_paste = build_cursor(ctx)["handle_paste"]

    clip_text = "a\nb\nc"
    handle_paste(clip_text)

    # 不得拼接成一行
    assert doc.lines[0].raw != "abc"
    # 应保持 3 行结构
    assert len(doc.lines) == 3
    assert doc.lines[0].raw == "a"
    assert doc.lines[1].raw == "b"
    assert doc.lines[2].raw == "c"


# ================ 纯文本粘贴（Ctrl+Shift+V） ================

def test_paste_plain_strips_markdown_syntax():
    """纯文本粘贴：剥离 Markdown 语法后插入。

    Ctrl+Shift+V Typora 式行为：**bold** → bold，# 标题 → 标题文本
    """
    doc = Document(lines=[_para_line("text")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=4)
    handle_paste_plain = build_cursor(ctx)["handle_paste_plain"]

    handle_paste_plain("**bold** and `code`")

    # Markdown 语法被剥离：** ** 和 ` ` 去除
    assert doc.lines[0].raw == "textbold and code"


def test_paste_plain_strips_heading_marker():
    """纯文本粘贴：剥离标题 # 标记。"""
    doc = Document(lines=[_para_line("")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0)
    handle_paste_plain = build_cursor(ctx)["handle_paste_plain"]

    handle_paste_plain("# 标题")

    # # 标记被剥离
    assert doc.lines[0].raw == "标题"


def test_paste_plain_strips_list_marker():
    """纯文本粘贴：剥离列表 - 标记。"""
    doc = Document(lines=[_para_line("")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0)
    handle_paste_plain = build_cursor(ctx)["handle_paste_plain"]

    handle_paste_plain("- 列表项")

    # - 标记被剥离
    assert doc.lines[0].raw == "列表项"


def test_paste_plain_preserves_line_structure():
    """纯文本粘贴多行：每行独立剥离语法，保持行结构。"""
    doc = Document(lines=[_para_line("")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0)
    handle_paste_plain = build_cursor(ctx)["handle_paste_plain"]

    handle_paste_plain("**bold**\n# heading\n`code`")

    # 3 行结构保持，每行语法被剥离
    assert len(doc.lines) == 3
    assert doc.lines[0].raw == "bold"
    assert doc.lines[1].raw == "heading"
    assert doc.lines[2].raw == "code"


def test_paste_plain_empty_text_noop():
    """纯文本粘贴空文本：无操作。"""
    doc = Document(lines=[_para_line("abc")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0)
    handle_paste_plain = build_cursor(ctx)["handle_paste_plain"]

    handle_paste_plain("")

    # 文档不变
    assert doc.lines[0].raw == "abc"
    assert len(doc.lines) == 1


# ================ 边界场景 ================

def test_paste_empty_text_noop():
    """空文本粘贴：无操作，但重置 paste_in_progress（防御性）。"""
    doc = Document(lines=[_para_line("abc")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=1, paste_active=True)
    handle_paste = build_cursor(ctx)["handle_paste"]

    handle_paste("")

    # 文档不变
    assert doc.lines[0].raw == "abc"
    # paste_in_progress 已重置（防御性，避免永久拦截 handle_char_input）
    assert ctx.paste_in_progress_ref.current is False


def test_paste_browse_mode_noop():
    """浏览态（cursor_li is None）：不粘贴，但重置 paste_in_progress。"""
    doc = Document(lines=[_para_line("abc")])
    ctx, calls = _make_ctx(doc, cursor_li=None, base=0, paste_active=True)
    handle_paste = build_cursor(ctx)["handle_paste"]

    handle_paste("text")

    # 文档不变
    assert doc.lines[0].raw == "abc"
    # paste_in_progress 已重置
    assert ctx.paste_in_progress_ref.current is False


def test_paste_fence_line_skipped():
    """围栏行（代码块）粘贴：跳过，但重置 paste_in_progress。"""
    code_line = Line(block_type=BlockType.CODE, lang="python")
    code_line.segments = [Segment(SegType.CODE, "print('hi')", "print('hi')")]
    code_line.raw = "```python\nprint('hi')\n```"
    doc = Document(lines=[code_line])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0, paste_active=True)
    handle_paste = build_cursor(ctx)["handle_paste"]

    handle_paste("new code")

    # 围栏行不变
    assert doc.lines[0].block_type == BlockType.CODE
    assert doc.lines[0].segments[0].text == "print('hi')"
    # paste_in_progress 已重置
    assert ctx.paste_in_progress_ref.current is False


def test_paste_out_of_range_li_noop():
    """越界行号：无操作，但重置 paste_in_progress。"""
    doc = Document(lines=[_para_line("abc")])
    ctx, calls = _make_ctx(doc, cursor_li=5, base=0, paste_active=True)
    handle_paste = build_cursor(ctx)["handle_paste"]

    handle_paste("text")

    # 文档不变
    assert doc.lines[0].raw == "abc"
    # paste_in_progress 已重置
    assert ctx.paste_in_progress_ref.current is False


# ================ 多光标智能粘贴 ================

def test_multi_cursor_paste_smart_distributes_lines():
    """多光标智能粘贴：剪贴板行数 == 光标数时逐行分配。

    场景：2 个光标（主光标 li=0 + 副光标 li=1），剪贴板 2 行文本，
    第 1 行分配到 li=0，第 2 行分配到 li=1。
    """
    doc = Document(lines=[_para_line("aaa"), _para_line("bbb")])
    # 主光标在 li=0 offset=1，副光标在 li=1 offset=1
    ctx, calls = _make_ctx(
        doc, cursor_li=0, base=1, paste_active=True,
        secondary_cursors=[(1, 1, 1)],
    )
    paste_to_multi_cursors = build_multi_cursor(ctx)["paste_to_multi_cursors"]

    paste_to_multi_cursors("X\nY")

    # li=0 插入 "X"：aXaa
    assert doc.lines[0].raw == "aXaa"
    # li=1 插入 "Y"：bYbb
    assert doc.lines[1].raw == "bYbb"
    # 行数不变（单行文本插入不增加行）
    assert len(doc.lines) == 2
    # paste_in_progress 已重置
    assert ctx.paste_in_progress_ref.current is False
    # nav_seq 递增（智能分配 cursor_li 不变，需手动重建）
    assert ("set_nav_seq", 1) in calls


def test_multi_cursor_paste_non_smart_fallback_to_handle_paste():
    """多光标非智能粘贴：行数 != 光标数时回退单光标 handle_paste。

    场景：2 个光标，剪贴板 1 行文本（行数不匹配），
    全文插入到主光标，清除副光标。
    """
    doc = Document(lines=[_para_line("aaa"), _para_line("bbb")])
    ctx, calls = _make_ctx(
        doc, cursor_li=0, base=1, paste_active=True,
        secondary_cursors=[(1, 1, 1)],
    )
    # 回退路径调用 ctx.handle_paste，需从 build_cursor 装配
    cursor_cbs = build_cursor(ctx)
    ctx.handle_paste = cursor_cbs["handle_paste"]
    paste_to_multi_cursors = build_multi_cursor(ctx)["paste_to_multi_cursors"]

    paste_to_multi_cursors("XYZ")

    # 主光标插入 "XYZ"：aXYZaa
    assert doc.lines[0].raw == "aXYZaa"
    # 副光标行不变（回退单光标）
    assert doc.lines[1].raw == "bbb"
    # 副光标已清除
    assert ctx.secondary_cursors_ref.current == []
    # paste_in_progress 由 handle_paste 内部重置
    assert ctx.paste_in_progress_ref.current is False


def test_multi_cursor_paste_plain_strips_then_distributes():
    """多光标纯文本粘贴：先剥离 Markdown 再智能分配。

    场景：2 个光标，剪贴板 2 行含 Markdown 语法的文本，
    先 strip_markdown 剥离语法，再逐行分配。
    """
    doc = Document(lines=[_para_line("aaa"), _para_line("bbb")])
    ctx, calls = _make_ctx(
        doc, cursor_li=0, base=1, paste_active=True,
        secondary_cursors=[(1, 1, 1)],
    )
    paste_to_multi_cursors_plain = build_multi_cursor(ctx)["paste_to_multi_cursors_plain"]

    # 剥离后为 "bold\ncode"（2 行匹配 2 光标）
    paste_to_multi_cursors_plain("**bold**\n`code`")

    # li=0 插入 "bold"：aboldaa
    assert doc.lines[0].raw == "aboldaa"
    # li=1 插入 "code"：bcodebb
    assert doc.lines[1].raw == "bcodebb"
    # paste_in_progress 已重置
    assert ctx.paste_in_progress_ref.current is False


def test_multi_cursor_paste_no_secondary_cursors_noop():
    """无副光标时多光标粘贴：无操作。"""
    doc = Document(lines=[_para_line("abc")])
    ctx, calls = _make_ctx(doc, cursor_li=0, base=0, secondary_cursors=[])
    paste_to_multi_cursors = build_multi_cursor(ctx)["paste_to_multi_cursors"]

    paste_to_multi_cursors("text")

    # 文档不变
    assert doc.lines[0].raw == "abc"


def test_multi_cursor_paste_cursor_li_none_noop():
    """多光标粘贴但主光标 cursor_li is None：无操作。"""
    doc = Document(lines=[_para_line("abc")])
    ctx, calls = _make_ctx(
        doc, cursor_li=None, base=0,
        secondary_cursors=[(0, 0, 0)],
    )
    paste_to_multi_cursors = build_multi_cursor(ctx)["paste_to_multi_cursors"]

    paste_to_multi_cursors("text")

    # 文档不变
    assert doc.lines[0].raw == "abc"


# ================ 多光标智能粘贴——选区替换 ================

def test_multi_cursor_paste_smart_replaces_selection():
    """多光标智能粘贴：光标有选区时替换选区内容。

    场景：2 个光标（主光标 li=0 选区 [1,3)，副光标 li=1 选区 [1,3)），
    剪贴板 2 行 "X\\nY"，智能匹配后各光标替换选区。
    """
    doc = Document(lines=[_para_line("aaa"), _para_line("bbb")])
    ctx, calls = _make_ctx(
        doc, cursor_li=0, base=1, paste_active=True,
        secondary_cursors=[(1, 1, 3)],  # 副光标有选区 [1,3)
    )
    # 主光标设置选区 [1,3)
    ctx.cursor_ref.current.base = 1
    ctx.cursor_ref.current.extent = 3
    paste_to_multi_cursors = build_multi_cursor(ctx)["paste_to_multi_cursors"]

    # 2 光标 + 2 行 → 智能匹配，各光标替换选区
    paste_to_multi_cursors("X\nY")

    # li=0：选区 [1,3) 替换 "aa" 为 "X"：raw[:1]+"X"+raw[3:] = "a"+"X"+"" = "aX"
    assert doc.lines[0].raw == "aX"
    # li=1：选区 [1,3) 替换 "bb" 为 "Y"：raw[:1]+"Y"+raw[3:] = "b"+"Y"+"" = "bY"
    assert doc.lines[1].raw == "bY"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
