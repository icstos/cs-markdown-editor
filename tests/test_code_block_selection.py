"""代码块参与「向外选区」（浏览态拖选）的测试。

背景（用户点名缺陷）：在代码块**前面或后面**按下鼠标、拖过代码块时，代码块区域
选不上。真机探针（`.workbuddy/probe/app_code_sel_probe.py`）把成因拆成两条，两条
都必须锁住，少一条症状就复现：

1. **命中错位**：浏览态拖选不靠 `SelectionArea`，而是各行的
   `GestureDetector.on_pan_start/on_pan_update` → `on_hit_test_xy` →
   `LineLayoutCache.hit_test` 反算 (行号, raw 偏移)。而 `LineLayoutCache` 把围栏岛屿
   按**一行文本**占位，一个 216px 高的代码块在它眼里只有 ~26px：指针落在代码块中部，
   反算出来却是块下面好几行的段落（真机实测 outward_sel 直接跳到下一个段落）。
   修法：缓存接受 `fence_height`（真实/估算高度），并在 `hit_test` 里把围栏块当作
   原子命中目标（块内任意点 → 该行行首）。
2. **不渲染选中态**：即使行号算对了，代码块岛屿此前根本不接收选区信息。
   修法：`_build_highlight_map` 让围栏块按整块参与，`LineView` 把
   `is_selected` 传给 `render_code_block`，后者逐行铺选中底色。

围栏块的选区语义是**原子**的：整块要么全选中要么全不选（代码内部没有"第 N 个字符"
这种坐标），复制整块（含围栏），删除/剪切/回车让路（不做半截围栏的拼接）。
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import flet as ft

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser import parse_markdown
from parser.selection import extract_outward_text
from styles import get_colors
from tests.harness import RenderHarness
from utils.segment_helpers import FENCE_BLOCK_TYPES, line_raw
from views import line_view as lv
from views._editor_helpers import _build_highlight_map, range_hits_fence
from views.pixel_layout import LineLayoutCache

MD = "PARA_ABOVE_TEXT\n\n```python\nA = 1\nB = 2\nC = 3\n```\n\nPARA_BELOW_TEXT\n"
# 行号：0=段落 1=空行 2=代码块 3=空行 4=段落
CODE_LI = 2
PARA_TOP_LI = 0
PARA_BOTTOM_LI = 4

# 代码块的行高 = round(字号 × 1.5)；这里只是给"真实高度"一个明确的取值来源，
# 数值本身不重要（真机上它来自 on_size_change 上报的实测高度）。
CODE_BLOCK_H = 216.0


def _lines(md: str = MD):
    lines = parse_markdown(md).lines
    assert lines[CODE_LI].block_type in FENCE_BLOCK_TYPES
    return lines


# ============ 1. 命中：围栏块必须按真实高度占位 ============
def test_fence_height_puts_hit_test_inside_the_block():
    """指针落在代码块中部 → 命中代码块那一行（而不是它下面的段落）。

    这是缺陷的根因。去掉 `fence_height` 时，同一个 y 会落到块后面的行——
    `test_without_fence_height_the_same_y_lands_below_the_block` 是它的反证。
    """
    lines = _lines()
    cache = LineLayoutCache(
        lines, 800.0, 1.6, 16, fence_height=lambda li: CODE_BLOCK_H if li == CODE_LI else None
    )
    layout = cache.get(CODE_LI)
    assert layout is not None
    # 围栏块占位是单视觉行、offsets_x 只有起点 —— 块内原子命中就靠这个前提
    assert layout.num_vlines == 1, layout.num_vlines
    assert layout.height == CODE_BLOCK_H, layout.height
    # 块内三个不同高度都命中本块
    for dy in (2.0, CODE_BLOCK_H / 2, CODE_BLOCK_H - 2.0):
        assert cache.hit_test(300.0, layout.top + dy) == (CODE_LI, 0), dy


def test_without_fence_height_the_same_y_lands_below_the_block():
    """反证：不给真实高度时，同一个 y 会命中块下面的行（= 用户看到的"选不上"）。"""
    lines = _lines()
    naive = LineLayoutCache(lines, 800.0, 1.6, 16)
    layout = naive.get(CODE_LI)
    assert layout is not None
    hit = naive.hit_test(300.0, layout.top + CODE_BLOCK_H / 2)
    assert hit is not None
    assert hit[0] != CODE_LI, "缓存若把块当一行文本，块内指针必然落到别的行"


def test_fence_hit_ignores_x():
    """围栏块是原子命中目标：块内任何 x 都折成该行行首。"""
    lines = _lines()
    cache = LineLayoutCache(
        lines, 800.0, 1.6, 16, fence_height=lambda li: CODE_BLOCK_H if li == CODE_LI else None
    )
    y = cache.get(CODE_LI).top + 40
    for x in (2.0, 300.0, 790.0):
        assert cache.hit_test(x, y) == (CODE_LI, 0), x


def test_scroll_group_wires_fence_height():
    """`build_scroll` 必须把真实高度接到缓存上（否则第 1 条修复形同虚设）。

    走 `hit_test_xy` 这条真机拖选的真实入口，而不是直接构造 LineLayoutCache：
    只测缓存本身的话，"忘了传 fence_height"会静默存活。
    """
    from views.editor._scroll import build_scroll

    lines = _lines()
    ctx = MagicMock()
    ctx.document.lines = lines
    ctx.body_font_size = 16
    ctx.line_height = 1.6
    ctx.content_width = 800.0
    ctx.viewport_h_ref.current = 600
    ctx.layout_cache_ref.current = None
    # 实测高度缓存（真机上由代码块 on_size_change 上报）
    ctx.line_heights_ref.current = {CODE_LI: CODE_BLOCK_H}

    scroll_cbs = build_scroll(ctx)
    # anchor_li=0、局部 y=0 → 只需它顺带把缓存建起来
    assert scroll_cbs["hit_test_xy"](0, 300.0, 0.0) is not None
    cache = ctx.layout_cache_ref.current
    layout = cache.get(CODE_LI)
    assert layout is not None
    assert layout.height == CODE_BLOCK_H, f"缓存里的块高 {layout.height} != 实测值"
    assert cache.hit_test(300.0, layout.top + 100.0) == (CODE_LI, 0)


# ============ 2. 选区映射：围栏块整块参与 ============
def test_highlight_map_gives_fence_the_whole_block():
    """代码块作为选区端点/中间行时，映射里是整块范围（不是被裁出来的零长度）。"""
    lines = _lines()
    # 段落中段 → 代码块行首（真机拖选的落点恒为该行行首）
    m = _build_highlight_map(lines, (PARA_TOP_LI, 6, CODE_LI, 0))
    assert m[CODE_LI] == (0, len(line_raw(lines[CODE_LI])))
    # 反向：代码块行首 → 下方段落
    m2 = _build_highlight_map(lines, (CODE_LI, 0, PARA_BOTTOM_LI, 5))
    assert m2[CODE_LI] == (0, len(line_raw(lines[CODE_LI])))
    # 中间行（选区两端在块两侧）
    m3 = _build_highlight_map(lines, (PARA_TOP_LI, 0, PARA_BOTTOM_LI, 5))
    assert m3[CODE_LI] == (0, len(line_raw(lines[CODE_LI])))


def test_highlight_map_keeps_partial_range_for_paragraphs():
    """普通行仍是字符级范围（围栏块的整块规则不得外溢）。"""
    lines = _lines()
    m = _build_highlight_map(lines, (PARA_TOP_LI, 6, CODE_LI, 0))
    assert m[PARA_TOP_LI] == (6, len(line_raw(lines[PARA_TOP_LI])))


# ============ 3. 复制：围栏块整块取出 ============
def test_extract_outward_text_includes_whole_fence():
    """拖选跨到代码块 → 复制出整块围栏（含开闭围栏），而不是空串或半截围栏。"""
    lines = _lines()
    code_raw = line_raw(lines[CODE_LI])
    text = extract_outward_text(lines, PARA_TOP_LI, 5, CODE_LI, 0)
    assert text.endswith(code_raw), text
    assert text.count("```") == 2, "围栏必须成对，否则粘回去就是坏 markdown"
    assert "A = 1" in text and "C = 3" in text


def test_extract_outward_text_zero_length_fence_endpoint_is_whole_block():
    """块内落点恒为行首（零长度）也要取到整块 —— 否则"选中了却复制不出来"。"""
    lines = _lines()
    assert extract_outward_text(lines, CODE_LI, 0, CODE_LI, 0) == line_raw(lines[CODE_LI])


# ============ 4. 破坏性操作让路 ============
def test_range_hits_fence():
    lines = _lines()
    assert range_hits_fence(lines, PARA_TOP_LI, CODE_LI)
    assert range_hits_fence(lines, CODE_LI, PARA_TOP_LI)  # 反向同样成立
    assert range_hits_fence(lines, CODE_LI, CODE_LI)
    assert not range_hits_fence(lines, PARA_TOP_LI, PARA_TOP_LI)
    assert not range_hits_fence(lines, PARA_BOTTOM_LI, PARA_BOTTOM_LI)


class _Box:
    """可写 .current 的通用槽（ft.Ref 用弱引用，装不了 tuple / bool）。"""

    def __init__(self, value=None) -> None:
        self.current = value


class _FakeClipboard:
    def __init__(self) -> None:
        self.written: list[str] = []

    async def set(self, text: str) -> None:
        self.written.append(text)


def _outward_group(sel, calls: list[str]):
    """装配**真实的** build_outward 闭包（最小 ctx），用于验证让路是否真的接上。

    只测 `range_hits_fence` 这个纯函数是不够的：删除/剪切/回车三处 if 忘了写、
    或写在了 `_delete_raw_range` 之后，纯函数测试照样全绿。真机症状是"删一下文档
    就坏掉"，所以必须从 handle_* 入口进。
    """
    from views.editor._outward import build_outward

    doc = parse_markdown(MD)
    clipboard = _FakeClipboard()
    ctx = SimpleNamespace(
        clipboard_ref=_Box(clipboard),
        cursor_base=_noop,
        cursor_li=None,
        cursor_vline_info=None,
        document=doc,
        focus_seq=0,
        get_line_visual_lines=_noop,
        mark_dirty=lambda: calls.append("mark_dirty"),
        on_submit=lambda *_a, **_k: calls.append("on_submit"),
        outward_sel=sel,
        outward_sel_ref=_Box(sel),
        preferred_col_ref=_Box(),
        push_history=lambda *_a, **_k: calls.append("push_history"),
        set_cursor=lambda *_a, **_k: calls.append("set_cursor"),
        set_cursor_li=_noop,
        set_focus_seq=_noop,
        set_outward_sel=lambda v: calls.append(f"set_outward_sel({v})"),
        suppress_blur=_Box(False),
        undo_push_pending=_Box(False),
    )
    return build_outward(ctx), doc, clipboard


def test_outward_delete_leaves_a_fence_selection_alone():
    """Delete 覆盖代码块时一动不动（合并首尾行会留下半截 ```python）。"""
    calls: list[str] = []
    group, doc, _clip = _outward_group((PARA_TOP_LI, 6, CODE_LI, 0), calls)
    before = [line_raw(ln) for ln in doc.lines]
    group["handle_outward_delete"]()
    assert [line_raw(ln) for ln in doc.lines] == before, "文档被改坏了"
    assert calls == [], f"连历史都不该压：{calls}"


def test_outward_enter_leaves_a_fence_selection_alone():
    """Enter 同理：既不删也不换行（on_submit 未调用）。"""
    calls: list[str] = []
    group, doc, _clip = _outward_group((PARA_TOP_LI, 0, PARA_BOTTOM_LI, 5), calls)
    before = [line_raw(ln) for ln in doc.lines]
    group["handle_outward_enter"]()
    assert [line_raw(ln) for ln in doc.lines] == before
    assert "on_submit" not in calls, calls


def test_outward_cut_neither_copies_nor_deletes_when_fence_in_range():
    """剪切覆盖代码块 → 整体让路（不剪文档；复制请走 Ctrl+C）。"""
    calls: list[str] = []
    group, doc, clipboard = _outward_group((PARA_TOP_LI, 6, CODE_LI, 0), calls)
    before = [line_raw(ln) for ln in doc.lines]
    asyncio.run(group["handle_outward_cut"]())
    assert [line_raw(ln) for ln in doc.lines] == before
    assert clipboard.written == [], clipboard.written
    assert calls == [], calls


def test_outward_delete_still_works_without_fence():
    """反证：选区不碰围栏块时，删除照常生效（守卫不得外溢成"什么都删不掉"）。"""
    calls: list[str] = []
    group, doc, _clip = _outward_group((PARA_TOP_LI, 0, PARA_TOP_LI, 5), calls)
    group["handle_outward_delete"]()
    assert line_raw(doc.lines[PARA_TOP_LI]) == "ABOVE_TEXT", line_raw(doc.lines[PARA_TOP_LI])
    assert "push_history" in calls and "mark_dirty" in calls, calls


# ============ 5. 渲染：整块铺选中底色 ============
def _render_code(*, is_selected: bool, active: bool = False):
    """在组件上下文里渲染代码块的浏览态（`ft.use_state` 需要宿主组件）。"""
    line = _lines()[CODE_LI]

    @ft.component
    def _Probe():
        return lv._render_code_block(
            line,
            0,
            16,
            800.0,
            _ref(None),
            _noop,
            _noop,
            _noop,
            _noop,
            _noop,
            _ref(None),
            active,
            False,
            None,
            word_wrap=True,
            is_selected=is_selected,
        )

    harness = RenderHarness()
    try:
        harness.render(_Probe)
        return harness
    except BaseException:
        harness.dispose()
        raise


def _ref(value=None):
    ref = ft.Ref()
    ref.current = value
    return ref


def _noop(*_a, **_k):
    return None


def _selection_bg() -> str:
    """向外选区的底色（与段落选中同一个取色口）。"""
    c = get_colors(ft.ThemeMode.LIGHT)
    return ft.Colors.with_opacity(0.22, c.link)


def _code_rows(h: RenderHarness) -> list[ft.Container]:
    """代码行容器：内容是 Row，第二个子项是带 spans 的 Text。"""
    rows = []
    for node in h.find(lambda n: isinstance(n, ft.Container)):
        content = getattr(node, "content", None)
        controls = getattr(content, "controls", None)
        if not controls or len(controls) < 2:
            continue
        text = controls[1]
        if isinstance(text, ft.Text) and getattr(text, "spans", None):
            rows.append(node)
    return rows


def test_selected_block_paints_every_row():
    """选中态：每一行代码都铺选中底色（整块被子而不是字形级高亮）。"""
    h = _render_code(is_selected=True)
    try:
        rows = _code_rows(h)
        assert len(rows) == 3, f"代码 3 行，实际 {len(rows)} 行容器"
        bg = _selection_bg()
        assert [r.bgcolor for r in rows] == [bg] * 3, [r.bgcolor for r in rows]
    finally:
        h.dispose()


def test_gutter_follows_the_selection_band():
    """选中时行号列不再铺自己的色带，让整行底色贯通（否则行号列颜色深浅不一）。"""
    h = _render_code(is_selected=True)
    try:
        for row in _code_rows(h):
            gutter = row.content.controls[0]
            assert gutter.bgcolor is None, gutter.bgcolor
    finally:
        h.dispose()


def test_unselected_block_has_no_band():
    """未选中：一行都不铺底色（普通文档必须与加选中之前逐像素一致）。"""
    h = _render_code(is_selected=False)
    try:
        rows = _code_rows(h)
        assert len(rows) == 3
        assert [r.bgcolor for r in rows] == [None, None, None]
        for row in rows:
            assert row.content.controls[0].bgcolor is not None  # 行号色带照旧
    finally:
        h.dispose()
