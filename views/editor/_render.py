"""行视图列表渲染工厂（从 views/editor.py 抽取）。

构造 line_controls 列表：
- diff 间隙（首行前对齐 + 每行后对齐）
- 表格合并（连续 TABLE 行合并为单个 TableView）
- 普通行 LineView（含光标层 / 行内格式 / 围栏岛屿 / diff 标记）

参数：
- ctx：EditorContext（读取 document / cursor_* / theme_mode / diff_* 等）
- stable_cbs：LineView 共享回调稳定包装器（s_on_tap / s_on_pan_* 等）
- table_stable：TableView 回调稳定包装器
- toc_entries：大纲条目（use_memo 稳定化）
- highlight_map：向外选区高亮映射（use_memo 稳定化）

依赖项：
- models（BlockType）
- views.line_view（LineView）
- views.table_view（TableView）
- views.editor._helpers（_build_diff_gap）
"""

from collections.abc import Callable
from typing import Any

from models import BlockType
from views.editor._helpers import _build_diff_gap
from views.line_view import LineView
from views.table_view import TableView


def build_line_controls(
    ctx,
    stable_cbs: dict[str, Callable],
    table_stable: dict[str, Callable],
    toc_entries: list[Any],
    highlight_map: dict[int, tuple[int, int]],
) -> list:
    """构造行视图控件列表。

    遍历 document.lines，表格连续行合并为 TableView，其余行构造 LineView。
    diff_gaps 在首行前与每行后插入等高间隙容器，保持左右视觉行对齐。
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

    line_controls: list = []

    # diff 间隙：首行之前的对齐间隙（对侧在开头有额外行时）
    if diff_gaps:
        _pre_gaps = diff_gaps.get(-1)
        if _pre_gaps:
            for _gh in _pre_gaps:
                line_controls.append(_build_diff_gap(_gh, c))

    i = 0
    while i < len(document.lines):
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
                )
            )
        i += 1
        # diff 间隙：在当前行后插入对齐间隙容器（另一侧有但本侧没有的行）
        if diff_gaps:
            _gaps = diff_gaps.get(i - 1)
            if _gaps:
                for _gh in _gaps:
                    line_controls.append(_build_diff_gap(_gh, c))

    return line_controls
