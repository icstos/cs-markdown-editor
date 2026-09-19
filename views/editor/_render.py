"""行视图列表渲染工厂（从 views/editor.py 抽取）。

构造 line_controls 列表：
- diff 间隙（首行前对齐 + 每行后对齐）
- 表格合并（连续 TABLE 行合并为单个 TableView）
- 普通行 LineView（含光标层 / 行内格式 / 围栏岛屿 / diff 标记）
- 窗口外行的**单个**高度占位容器（大文件首屏优化，见 `window` 参数）

参数：
- ctx：EditorContext（读取 document / cursor_* / theme_mode / diff_* 等）
- stable_cbs：LineView 共享回调稳定包装器（s_on_tap / s_on_pan_* 等）
- table_stable：TableView 回调稳定包装器
- toc_entries：大纲条目（use_memo 稳定化）
- highlight_map：向外选区高亮映射（use_memo 稳定化）
- window：(lo, hi) 行窗口上界；None / 覆盖全文档时不窗口化

依赖项：
- models（BlockType）
- views.line_view（LineView）
- views.table_view（TableView）
- views.editor._helpers（_build_diff_gap）

窗口化设计（大文件打开速度 + 编辑响应速度）：
`ft.ListView.build_controls_on_demand` 只让 **Flutter 客户端**懒建 widget，
Python 侧 `controls` 列表里的控件对象仍是全量构造的——实测 3 千行文档构造
控件树约 2.4s，其中 95% 花在 flet 的 `_configure_dataclass`（每个新增控件
都要遍历其 dataclass 字段子树）。真正省时间只能**不构造**视口外的行。

窗口化只构造 `[lo, hi)` 内的行；未构建区域的高度由 `line_padding()` 交给
**ListView 的 padding** 表示（一个常量，零个额外列表项）。

为什么是 padding 而不是「占位控件」——两条真机实测证据（160 行窗口 + 区外
44640px，文档总高 49440px）：

| 未构建区的表示方式             | Flutter 上报的 maxScrollExtent | 评价 |
|--------------------------------|-------------------------------|------|
| 1 个高 44640px 的占位容器      | 4385（真值 48994）            | 外推够不到它，文档后段滚不到 |
| 每行 1 个占位（1395 个项）     | 46205 → 滚动后收敛 48995      | 正确，但项数 = 行数 |
| **ListView 底部 padding**      | **48995**                     | **精确，且项数 = 窗口行数** |

原因：`build_controls_on_demand` 下 `maxScrollExtent` 由 Flutter
`SliverChildBuilderDelegate._extrapolateMaxScrollOffset` 外推得到，
公式为 `leading + (已布局项总高 / 已布局项数) × 剩余项数`——**Flutter 只认
"真正布局过"的那些项的高度**。占位容器是列表项，落在布局窗口之外就永远
不被计入；而 padding 是常量，Flutter 无需布局即知高度、直接计入总高。

代价与收益：窗口行的总高仍走同一条外推（平均高 × 项数），但样本是真实行、
误差只作用在"窗口那几屏"上（实测 <1% 文档总高，且随窗口增长自行收敛）；
换来的是**列表项数 = 窗口行数**，即 flet 控件树 diff 的成本与文档总行数解耦
（实测 1555 行文档：项数从 1555 降到 160，单次重渲染 19.7ms → 3.4ms）。

窗口上界单调外扩（见 `__init__.py` 的 `request_line_window`），
已构建区域只增不减 → 视口上方永远是真实行，不出现估算误差导致的跳动。

依赖项：
- models（BlockType）
- views.line_view（LineView）
- views.table_view（TableView）
- views.editor._helpers（_build_diff_gap）
"""

from collections.abc import Callable
from typing import Any

import flet as ft

from models.document import BlockType
from views.editor._helpers import _build_diff_gap
from views.line_view import LineView
from views.table_view import TableView


def _snap_window(
    lines: list, lo: int, hi: int
) -> tuple[int, int]:
    """把窗口边界对齐到表格块边界：连续 TABLE 行必须整体落在窗口内。

    表格是「连续 TABLE 行合并为单个 TableView」渲染的，窗口若切在表格中间，
    会渲染出只有后半截的表格。上界向后扩、下界向前扩，各自吃掉整个 TABLE 连续段。
    """
    n = len(lines)
    while lo > 0 and lines[lo].block_type == BlockType.TABLE and (
        lines[lo - 1].block_type == BlockType.TABLE
    ):
        lo -= 1
    while hi < n and lines[hi].block_type == BlockType.TABLE:
        hi += 1
    return lo, hi


def resolve_window(document, window: tuple[int, int] | None) -> tuple[int, int]:
    """把请求的窗口 (lo, hi) 归一化为最终生效的行区间（含表格边界对齐）。

    `build_line_controls` 与 `line_padding` 必须用**同一个**归一化结果，
    否则留白高度会与实际构建的行区间错位（表格行被重复或漏算高度）。
    """
    n = len(document.lines)
    if window is None:
        return 0, n
    lo = max(0, min(window[0], n))
    hi = max(lo, min(window[1], n))
    return _snap_window(document.lines, lo, hi)


def line_padding(ctx, window: tuple[int, int] | None) -> tuple[float, float]:
    """窗口化时未构建区的 (上, 下) 高度留白，供 ListView 的 padding 使用。

    高度取行偏移前缀和（与 `_scroll.py` 的滚动定位同一把尺子），故
    「窗口行真实总高 + 留白 == 文档总高」恒成立：滚动条长度、跳转落点、
    列表项位置三者一致，不会因估算误差漂移。

    padding 是**常量**：Flutter 无需布局即知这部分高度，直接计入
    `maxScrollExtent`（而占位控件是列表项，落在布局窗口外就永远不被计入 →
    实测 1555 行文档 max 只有 4793，后 90% 滚不到。见模块 docstring）。
    """
    lo, hi = resolve_window(ctx.document, window)
    top = ctx.estimate_line_offset(lo) if lo > 0 else 0.0
    bottom = 0.0
    if hi < len(ctx.document.lines):
        bottom = ctx.estimate_line_offset(len(ctx.document.lines)) - ctx.estimate_line_offset(hi)
    return max(0.0, top), max(0.0, bottom)


def build_line_controls(
    ctx,
    stable_cbs: dict[str, Callable],
    table_stable: dict[str, Callable],
    toc_entries: list[Any],
    highlight_map: dict[int, tuple[int, int]],
    window: tuple[int, int] | None = None,
) -> list:
    """构造行视图控件列表。

    遍历 document.lines，表格连续行合并为 TableView，其余行构造 LineView。
    diff_gaps 在首行前与每行后插入等高间隙容器，保持左右视觉行对齐。

    window=(lo, hi) 时只构造 [lo, hi) 内的行（表格边界对齐后）；未构建区域的
    高度由调用方用 `line_padding()` 写进 ListView 的 padding——**不产生任何列表项**，
    列表项数恒等于窗口行数（见模块 docstring：项数决定 flet 控件树 diff 成本）。
    window=None 或覆盖全文档时退化为全量构造，与窗口化前的行为逐字一致。
    """
    c = ctx.c
    document = ctx.document
    cursor_li = ctx.cursor_li
    cursor_off = ctx.cursor_off
    cursor_ref = ctx.cursor_ref
    nav_seq = ctx.nav_seq
    cursor_field_ref = ctx.cursor_field_ref
    cursor_field_value = ctx.cursor_field_value
    input_session_ref = ctx.input_session_ref
    content_width = ctx.content_width
    line_height = ctx.line_height
    flash_li = ctx.flash_li
    math_focus_li = ctx.math_focus_li
    math_field_ref = ctx.math_field_ref
    clipboard_ref = ctx.clipboard_ref
    shift_pressed_ref = ctx.shift_pressed_ref
    ctrl_pressed_ref = ctx.ctrl_pressed_ref
    alt_pressed_ref = ctx.alt_pressed_ref
    secondary_cursors = ctx.secondary_cursors
    theme_mode = ctx.theme_mode
    cursor_line = ctx.cursor_line
    table_focus_li = ctx.table_focus_li
    table_nav_ref = ctx.table_nav_ref
    diff_marks = ctx.diff_marks
    diff_gaps = ctx.diff_gaps

    win_lo, win_hi = resolve_window(document, window)

    line_controls: list = []

    # 窗口上方的未构建区：高度由调用方以 ListView padding.top 表示（不留列表项）

    # diff 间隙：首行之前的对齐间隙（对侧在开头有额外行时）
    if diff_gaps and win_lo == 0:
        _pre_gaps = diff_gaps.get(-1)
        if _pre_gaps:
            for _gh in _pre_gaps:
                line_controls.append(_build_diff_gap(_gh, c))

    i = win_lo
    while i < win_hi:
        line = document.lines[i]
        is_act = cursor_li == i and cursor_li is not None
        # diff 行级标记：从 diff_marks 字典取当前行标记（None=普通行）
        _diff_mark = diff_marks.get(i) if diff_marks else None
        if line.block_type == BlockType.TABLE:
            table_start = i
            while (
                i + 1 < len(document.lines)
                and document.lines[i + 1].block_type == BlockType.TABLE
            ):
                i += 1
            table_end = i
            line_controls.append(
                TableView(
                    key=f"table-{table_start}",
                    lines=document.lines,
                    line_idx=table_start,
                    content_width=content_width,
                    clipboard_ref=clipboard_ref,
                    on_change_cell=table_stable["on_change_cell"],
                    on_table_op=table_stable["on_table_op"],
                    on_table_focus=table_stable["on_table_focus"],
                    on_table_blur=table_stable["on_table_blur"],
                    table_nav_ref=table_nav_ref,
                    is_current_line=table_start <= cursor_line <= table_end,
                    # 表格创建后自动聚焦首格（set_block(TABLE) 设置 table_focus_li）
                    auto_focus_li=table_focus_li,
                    # 版本号触发 prop：lines 列表与首行 raw 长度变化时触发 memo 刷新
                    lines_version=len(document.lines),
                    first_line_raw_version=(
                        len(document.lines[table_start].raw)
                        if 0 <= table_start < len(document.lines) else 0
                    ),
                    # 主题失效 prop：切换主题时让 ft.memo 失效，重新取色
                    theme_mode=theme_mode,
                )
            )
        else:
            line_controls.append(
                LineView(
                    key=f"line-{i}",
                    line=line,
                    line_idx=i,
                    cursor_off=cursor_off if is_act else None,
                    cursor_ref=cursor_ref if is_act else None,
                    nav_seq=nav_seq if is_act else 0,
                    wrap_sel_seq=ctx.wrap_sel_seq if is_act else 0,
                    field_ref=cursor_field_ref if is_act else None,
                    input_session_ref=input_session_ref if is_act else None,
                    cursor_value=cursor_field_value if is_act else "",
                    content_width=content_width,
                    # 软换行总开关：代码块浏览态据此折行 / 横向滚动（段落走 content_width）
                    word_wrap=ctx.word_wrap,
                    line_height=line_height,
                    body_font_size=ctx.body_font_size,
                    is_current_line=is_act,
                    is_flash=flash_li == i,
                    # 版本号触发 prop：reparse_line 就地修改 line 对象不替换引用，
                    # ft.memo 浅比较 line 引用未变会误判未刷新。通过 raw 长度 + 段数
                    # 两个值变化触发 memo 检测，让屏幕刷新。
                    line_raw_version=len(line.raw) if line.raw else 0,
                    line_seg_count=len(line.segments),
                    # 主题失效 prop：切换主题时让 ft.memo 失效，重新取色
                    theme_mode=theme_mode,
                    on_cursor_change=ctx.handle_char_input if is_act else None,
                    on_cursor_submit=ctx.on_submit if is_act else None,
                    on_cursor_focus=ctx.on_cursor_focus if is_act else None,
                    on_cursor_blur=ctx.on_blur if is_act else None,
                    on_tap=stable_cbs["on_tap"],
                    on_pan_start=stable_cbs["on_pan_start"],
                    on_pan_update=stable_cbs["on_pan_update"],
                    on_toggle_task=stable_cbs["on_toggle_task"],
                    on_change_code=stable_cbs["on_change_code"],
                    on_code_focus=stable_cbs["on_code_focus"],
                    on_code_blur=stable_cbs["on_code_blur"],
                    on_code_selection=stable_cbs["on_code_selection"],
                    on_change_lang=stable_cbs["on_change_lang"],
                    # 块级公式：浏览态 ft.Markdown 渲染 LaTeX，点击进入编辑态 TextField
                    is_math_editing=(math_focus_li == i),
                    on_change_math=stable_cbs["on_change_math"],
                    on_math_focus=stable_cbs["on_math_focus"],
                    on_math_blur=stable_cbs["on_math_blur"],
                    math_field_ref=math_field_ref,
                    clipboard_ref=clipboard_ref,
                    toc_entries=toc_entries,
                    on_jump_to=stable_cbs["on_jump_to"],
                    on_line_size_change=stable_cbs["on_line_size_change"],
                    outward_range=highlight_map.get(i),
                    on_extend_outward=stable_cbs["on_extend_outward"],
                    on_clear_outward=stable_cbs["on_clear_outward"],
                    shift_pressed_ref=shift_pressed_ref,
                    ctrl_pressed_ref=ctrl_pressed_ref,
                    alt_pressed_ref=alt_pressed_ref,
                    on_hit_test_x=stable_cbs["on_hit_test_x"],
                    on_hit_test_xy=stable_cbs["on_hit_test_xy"],
                    on_double_tap=stable_cbs["on_double_tap"],
                    # 多光标：本行的副光标列表（li==i 的子集），用于渲染副光标标记
                    secondary_cursors=[
                        sc for sc in secondary_cursors if sc[0] == i
                    ] if secondary_cursors else [],
                    # 版本号：强制 ft.memo 在副光标内容变化时刷新所有行
                    secondary_cursors_version=ctx.secondary_cursors_version,
                    on_image_action=stable_cbs["on_image_action"],
                    file_path=ctx.file_path,
                    diff_mark=_diff_mark,
                    # 文档内搜索（浮层）：本行命中 [(s, e, is_current)]（无则 None）
                    search_hits=ctx.search_hits.get(i),
                    search_hits_version=ctx.search_hits_version,
                )
            )
        i += 1
        # diff 间隙：在当前行后插入对齐间隙容器（另一侧有但本侧没有的行）
        if diff_gaps:
            _gaps = diff_gaps.get(i - 1)
            if _gaps:
                for _gh in _gaps:
                    line_controls.append(_build_diff_gap(_gh, c))

    # 窗口下方的未构建区：高度由调用方以 ListView padding.bottom 表示。
    # 不用占位控件：占位是列表项，落在 Flutter 布局窗口之外就不被计入
    # maxScrollExtent（见模块 docstring 的实测表），且会让 diff 成本 = O(总行数)。
    return line_controls
