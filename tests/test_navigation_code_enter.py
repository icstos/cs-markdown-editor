"""方向键"从代码块外进入代码块内"（views/editor/_navigation.py::_move_vline）。

背景：`handle_code_exit` 负责"内 → 外"（光标在代码块内按到边界就跳出去），但反方向
原先缺失——`_move_vline` 遇到围栏块一律**跳过**，于是"段落 → 代码块 → 段落"这种
最常见结构里，↑/↓ 会直接穿过代码块，用户只能改用手去点。

现在把紧邻的**代码块**改成"进得去"：↓ 在代码块上一行、↑ 在代码块下一行时，光标进入
代码块（↓ 落首行行首、↑ 落末行行尾）。发起方只算"目标行 + 目标偏移"，进入编辑态由
code_block 组件自己完成（组件状态不该被外部直接改）。

同时锁定：其它岛屿（公式/表格/TOC/FRONTMATTER）**仍跳过**——本次只动代码块，
避免顺带改变公式/表格的既有导航手感。

用最小 mock ctx 直接调 `build_navigation(ctx)["move_up"/"move_down"]`。
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.document import BlockType, Document, Line, Segment, SegType
from views.editor._navigation import build_navigation


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


def _make_ctx(document: Document, *, cursor_li: int, cursor_off: int) -> tuple:
    """构造最小 mock EditorContext，只含 _move_vline 依赖的槽。"""
    calls: list = []
    off_holder = {"off": cursor_off}
    ctx = types.SimpleNamespace(
        document=document,
        cursor_li=cursor_li,
        cursor_base=lambda *a: off_holder["off"],
        set_cursor=lambda li, off, **kw: calls.append(("set_cursor", li, off)),
        set_cursor_li=lambda li: calls.append(("set_cursor_li", li)),
        set_cursor_line=lambda li: calls.append(("set_cursor_line", li)),
        set_code_enter=lambda li, off: calls.append(("set_code_enter", li, off)),
        ensure_visible=lambda li: calls.append(("ensure_visible", li)),
        clear_secondary_cursors=lambda: calls.append("clear_secondary_cursors"),
        broadcast_move_left=lambda: None,
        broadcast_move_right=lambda: None,
        layout_cache_ref=FakeRef(None),
        preferred_col_ref=FakeRef(None),
        secondary_cursors_ref=FakeRef([]),
        content_width=800.0,
        line_height=1.6,
        body_font_size=16,
    )
    return ctx, calls


def _nav(ctx):
    return build_navigation(ctx)


def _cursor_line(calls: list) -> int | None:
    """最近一次 set_cursor 命中的行号（偏移按记忆列计算，断言只看行号）。"""
    for c in reversed(calls):
        if isinstance(c, tuple) and c[0] == "set_cursor":
            return c[1]
    return None


# ---------------- ↓ 进入下方代码块 ----------------
def test_down_from_line_above_enters_code_block_at_first_line():
    """段落末行按 ↓ → 进入下方代码块，光标落在**正文偏移 0**（首行行首）。"""
    doc = Document(lines=[_para("before"), _code_line("abc\ndef"), _para("after")])
    ctx, calls = _make_ctx(doc, cursor_li=0, cursor_off=len("before"))
    _nav(ctx)["move_down"]()
    assert ("set_code_enter", 1, 0) in calls
    # 同时把编辑器光标交出去：cursor_line 指向代码块、cursor_li 清空
    assert ("set_cursor_line", 1) in calls
    assert ("set_cursor_li", None) in calls
    # 不得再落回普通文本行（否则同一帧里会出现两个光标）
    assert not any(c[0] == "set_cursor" for c in calls if isinstance(c, tuple))


def test_down_into_code_block_uses_body_length_not_fence_raw():
    """↑ 进入时用的偏移是**代码正文字长**，不是含 ``` 的 line.raw 长度。"""
    code = "abc\ndef"
    doc = Document(lines=[_code_line(code), _para("after")])
    ctx, calls = _make_ctx(doc, cursor_li=1, cursor_off=0)
    _nav(ctx)["move_up"]()
    assert ("set_code_enter", 0, len(code)) in calls, calls


def test_up_from_line_below_enters_code_block_at_last_line_end():
    """段落首行按 ↑ → 进入上方代码块，光标落在正文末尾（末行行尾）。"""
    code = "abc\ndef"
    doc = Document(lines=[_para("before"), _code_line(code), _para("after")])
    ctx, calls = _make_ctx(doc, cursor_li=2, cursor_off=0)
    _nav(ctx)["move_up"]()
    assert ("set_code_enter", 1, len(code)) in calls


def test_down_inside_wrapped_line_does_not_enter_code_block():
    """未走到本行最后一个视觉行时只下移一个视觉行，不得提前进入代码块。"""
    doc = Document(lines=[_para("x" * 400), _code_line("abc")])
    ctx, calls = _make_ctx(doc, cursor_li=0, cursor_off=0)
    _nav(ctx)["move_down"]()
    assert not any(isinstance(c, tuple) and c[0] == "set_code_enter" for c in calls), calls
    assert any(c[0] == "set_cursor" for c in calls if isinstance(c, tuple)), calls


def test_non_adjacent_code_block_is_still_skipped():
    """代码块与当前行之间隔着别的行时，仍按原逻辑跳过（只处理**紧邻**的代码块）。"""
    doc = Document(
        lines=[_para("before"), _para("middle"), _code_line("abc"), _para("after")]
    )
    ctx, calls = _make_ctx(doc, cursor_li=0, cursor_off=len("before"))
    _nav(ctx)["move_down"]()
    assert not any(isinstance(c, tuple) and c[0] == "set_code_enter" for c in calls)
    # 落到紧邻的普通行 li=1（偏移按记忆列命中，此处只断言行号）
    assert _cursor_line(calls) == 1


def test_other_island_types_are_still_skipped():
    """公式等其它岛屿不进——本次只改代码块，避免顺带改变公式/表格的导航手感。"""
    doc = Document(lines=[_para("before"), _math_line(), _para("after")])
    ctx, calls = _make_ctx(doc, cursor_li=0, cursor_off=len("before"))
    _nav(ctx)["move_down"]()
    assert not any(isinstance(c, tuple) and c[0] == "set_code_enter" for c in calls)
    assert _cursor_line(calls) == 2


def test_document_bottom_still_degrades_to_last_line():
    """文档末尾没有下一行时行为不变（落到末行末尾）。"""
    doc = Document(lines=[_para("before"), _code_line("abc")])
    ctx, calls = _make_ctx(doc, cursor_li=1, cursor_off=0)
    # 光标直接落在代码块行上（异常态）：退化分支不涉及 set_code_enter
    _nav(ctx)["move_down"]()
    assert not any(isinstance(c, tuple) and c[0] == "set_code_enter" for c in calls)


# ===========================================================================
# 请求生命周期：兑现后必须作废（`_code_enter_after_consumed`）
#
# 请求是一次性待办，不是"当前状态"。不作废的后果（真机可达）：
# 编辑器按 window 只物化视口附近的行 → 目标行永久带非 0 序号 → 该行滚出窗口被卸载、
# 再滚回来重建时，挂载期 effect 拿同一个旧序号再消费一次 → 代码块自己跳进编辑态。
# ===========================================================================
def test_consumed_request_is_cleared():
    """兑现的就是当前那条请求 → 作废。"""
    from views.editor import _code_enter_after_consumed

    assert _code_enter_after_consumed((3, 1, 0), 3) is None


def test_consumed_report_does_not_clear_another_lines_request():
    """晚到的兑现回报不得误伤后来发给**别**行的新请求：原对象原样返回。

    返回同一对象（而不是内容相等的新元组）让调用方可以用 `is` 判定"没变化、
    不必 set_state"，避免无意义重渲染。
    """
    from views.editor import _code_enter_after_consumed

    pending = (7, 2, 5)
    assert _code_enter_after_consumed(pending, 3) is pending


def test_consumed_report_without_pending_request_is_noop():
    """没有待办时回报 → 仍是没有待办（且不产生新对象）。"""
    from views.editor import _code_enter_after_consumed

    assert _code_enter_after_consumed(None, 0) is None


# ===========================================================================
# 渲染期翻译：`code_enter_props`（请求 state → 本行的 LineView prop）
#
# 上面管"请求何时作废"，这里管"作废之后渲染出什么"。缺了后者，上面那条规则
# 到不了控件：`state=None` 若没被翻译成 `(0, None)`，重建的行照样会进编辑态。
# ===========================================================================
def test_render_props_only_target_line_gets_request():
    """命中目标行 → 拿到 (序号, 偏移)，两者都透传。"""
    from views.editor._render import code_enter_props

    assert code_enter_props((2, 7, 5), 2) == (7, 5)


def test_render_props_other_lines_stay_inert():
    """非目标行恒为 (0, None)：otherwise 逐行击穿 ft.memo，一次方向键重渲整块。"""
    from views.editor._render import code_enter_props

    state = (2, 7, 5)
    assert code_enter_props(state, 1) == (0, None)
    assert code_enter_props(state, 3) == (0, None)


def test_render_props_cleared_request_never_reenters():
    """请求已作废 → 任何行都是 (0, None)。

    这是"行被卸载又重建后不幽灵进入编辑态"的最后一道闸：真机可达的缺陷路径是
    目标行滚出视口被卸载、再滚回时重建，挂载期 effect 拿旧序号**再消费一次**。
    """
    from views.editor._render import code_enter_props

    for li in range(5):
        assert code_enter_props(None, li) == (0, None)
