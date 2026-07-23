"""表格视图：基于 DataTable2 的 Typora/Word 风格表格渲染与编辑。

表格作为独立可编辑岛屿（类似代码块的 CodeEditor）：
- 单击单元格进入编辑模式（TextField 替换 Text）
- Tab/Shift+Tab/Enter 单元格间导航（Tab 在末格新增行）
- 工具栏 + 右键菜单支持行列增删、对齐设置
- on_change_cell 原地更新行模型（不触发 observable 重渲染，避免光标跳动）
- on_table_op 结构操作触发重渲染
- table_nav_ref 供 editor.py _on_key_down 调用 Tab/Escape 导航
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable

import flet as ft

try:
    from flet_datatable2 import DataTable2
except Exception:  # pragma: no cover
    DataTable2 = ft.DataTable

from models import BlockType, Line
from styles import FONT_MAIN, FONT_MONO, _current_colors

_ALIGN_RE = re.compile(r"^:?-{3,}:?$")


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _parse_table_lines(
    lines: list[Line], start_idx: int
) -> tuple[int, int, list[int], list[list[str]], list[str]]:
    """解析连续 TABLE 行。

    返回 (header_idx, sep_idx, row_indices, rows, aligns)。
    row_indices 不含 header 和 separator。aligns 为对齐标记原始字符串。
    """
    row_indices: list[int] = []
    rows: list[list[str]] = []
    aligns: list[str] = []
    i = start_idx
    header_idx = start_idx
    sep_idx = -1
    seen_header = False
    while i < len(lines) and lines[i].block_type == BlockType.TABLE:
        cells = [c.strip() for c in lines[i].raw.strip().strip("|").split("|")]
        if all(_ALIGN_RE.fullmatch(c or "---") for c in cells):
            aligns = cells
            sep_idx = i
        elif not seen_header:
            header_idx = i
            rows.append(cells)
            seen_header = True
        else:
            row_indices.append(i)
            rows.append(cells)
        i += 1
    return header_idx, sep_idx, row_indices, rows, aligns


def _split_row(raw: str) -> list[str]:
    return [c.strip() for c in raw.strip().strip("|").split("|")]


def _join_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _normalize_rows(rows: list[list[str]]) -> list[list[str]]:
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


def _cell_text(cell: str) -> str:
    return cell.strip() or " "


def _safe_color(color: str, opacity: float) -> str:
    return ft.Colors.with_opacity(opacity, color)


def _align_of(sep_cell: str) -> str:
    s = sep_cell.strip()
    if s.startswith(":") and s.endswith(":"):
        return "center"
    if s.endswith(":"):
        return "right"
    return "left"


def _align_text_align(align: str) -> ft.TextAlign:
    return {
        "left": ft.TextAlign.LEFT,
        "center": ft.TextAlign.CENTER,
        "right": ft.TextAlign.RIGHT,
    }.get(align, ft.TextAlign.LEFT)


def _align_marker(align: str) -> str:
    return {"left": "---", "center": ":---:", "right": "---:"}.get(align, "---")


def _align_icon(align: str) -> str:
    return {
        "left": ft.Icons.FORMAT_ALIGN_LEFT,
        "center": ft.Icons.FORMAT_ALIGN_CENTER,
        "right": ft.Icons.FORMAT_ALIGN_RIGHT,
    }.get(align, ft.Icons.FORMAT_ALIGN_LEFT)


# ---------------------------------------------------------------------------
# TableView 组件
# ---------------------------------------------------------------------------

@ft.component
def TableView(
    lines: list[Line],
    line_idx: int,
    content_width: float | None = None,
    clipboard_ref: ft.Ref | None = None,
    on_change_cell: Callable[[int, int, str], None] | None = None,
    on_table_op: Callable[[str, dict], None] | None = None,
    on_table_focus: Callable[[], None] | None = None,
    on_table_blur: Callable[[], None] | None = None,
    table_nav_ref: ft.Ref | None = None,
    is_current_line: bool = False,
):
    """自管理的表格编辑组件（独立岛屿，不使用 active/draft 系统）。"""
    c = _current_colors()
    header_idx, sep_idx, row_indices, rows, aligns = _parse_table_lines(lines, line_idx)
    if not rows:
        return ft.Container()

    normalized = _normalize_rows(rows)
    col_count = len(normalized[0]) if normalized else 0
    if col_count == 0:
        return ft.Container()

    aligns = [_align_of(aligns[i]) if i < len(aligns) else "left" for i in range(col_count)]
    header_row = normalized[0]
    body_rows = normalized[1:] if len(normalized) > 1 else []

    # ---- 内部状态 ----
    edit_cell, set_edit_cell = ft.use_state(None)  # (line_idx, col_idx) | None
    edit_draft, set_edit_draft = ft.use_state("")
    edit_draft_ref = ft.use_ref("")
    edit_draft_ref.current = edit_draft
    pending_blur_ref = ft.use_ref(False)
    nav_seq, set_nav_seq = ft.use_state(0)
    pending_nav, set_pending_nav = ft.use_state(None)  # ("new_row", col_idx) | None
    # 导航守卫：_start_edit 切换单元格时设为 True，阻止旧 TextField 卸载触发的
    # on_blur 退出编辑模式（Tab/Enter 导航、点击新单元格均会触发重渲染→旧 field 卸载）。
    # 0.1s 后自动复位，允许真正的失焦（点击表格外部）正常退出。
    nav_guard_ref = ft.use_ref(False)

    # ---- 辅助方法 ----
    def _cell_value(li: int, ci: int) -> str:
        if 0 <= li < len(lines):
            cells = _split_row(lines[li].raw)
            if ci < len(cells):
                return cells[ci]
        return ""

    def _start_edit(li: int, ci: int):
        """进入单元格编辑模式。"""
        if edit_cell is not None and edit_cell != (li, ci):
            _commit_current()
        # 设导航守卫：阻止 set_edit_cell 触发重渲染后旧 TextField 卸载的 on_blur
        nav_guard_ref.current = True
        pending_blur_ref.current = False
        text = _cell_value(li, ci)
        edit_draft_ref.current = text
        set_edit_draft(text)
        set_edit_cell((li, ci))
        set_nav_seq(nav_seq + 1)
        if on_table_focus is not None:
            on_table_focus()
        # 延迟复位守卫：等待 on_blur 触发窗口过去后恢复
        page = ft.context.page
        if page is not None:
            async def _reset_guard():
                await asyncio.sleep(0.1)
                nav_guard_ref.current = False
            page.run_task(_reset_guard)

    def _commit_current():
        """提交当前编辑单元格的草稿到行模型（on_change_cell 已实时同步，此处兜底）。"""
        if edit_cell is not None and on_change_cell is not None:
            on_change_cell(edit_cell[0], edit_cell[1], edit_draft_ref.current)

    def _exit_edit():
        """退出编辑模式。"""
        nav_guard_ref.current = False
        pending_blur_ref.current = False
        _commit_current()
        set_edit_cell(None)
        if on_table_blur is not None:
            on_table_blur()

    # ---- 导航 ----
    def _move_cell(delta: int):
        """Tab(1)/Shift+Tab(-1)：移动到下一/上一格。末格 Tab 新增行。"""
        _commit_current()
        current = edit_cell or (header_idx, 0)
        all_rows = [header_idx] + row_indices
        try:
            row_idx = all_rows.index(current[0])
        except ValueError:
            row_idx = 0
        ci = current[1]
        next_ci = ci + delta
        next_row_idx = row_idx
        if next_ci >= col_count:
            next_ci = 0
            next_row_idx = row_idx + 1
        elif next_ci < 0:
            next_ci = col_count - 1
            next_row_idx = row_idx - 1
        if 0 <= next_row_idx < len(all_rows):
            _start_edit(all_rows[next_row_idx], next_ci)
        elif next_row_idx >= len(all_rows) and on_table_op is not None:
            on_table_op("add_row", {"after_li": all_rows[-1], "col_count": col_count})
            set_pending_nav(("new_row", 0))

    def _move_down():
        """Enter：移动到下一行同列。末行 Enter 新增行。"""
        _commit_current()
        current = edit_cell or (header_idx, 0)
        all_rows = [header_idx] + row_indices
        try:
            row_idx = all_rows.index(current[0])
        except ValueError:
            row_idx = 0
        ci = current[1]
        next_row_idx = row_idx + 1
        if next_row_idx < len(all_rows):
            _start_edit(all_rows[next_row_idx], ci)
        elif on_table_op is not None:
            on_table_op("add_row", {"after_li": all_rows[-1], "col_count": col_count})
            set_pending_nav(("new_row", ci))

    # ---- TextField 事件 ----
    def _on_change_draft(value: str):
        edit_draft_ref.current = value
        set_edit_draft(value)
        if edit_cell is not None and on_change_cell is not None:
            on_change_cell(edit_cell[0], edit_cell[1], value)

    def _on_blur():
        """延迟失焦：允许点击另一单元格时在新 TextField 聚焦前取消清除。

        导航守卫（nav_guard_ref）为 True 时直接返回：Tab/Enter/点击新单元格
        触发的重渲染会卸载旧 TextField 产生 on_blur，这不是真正的失焦。
        """
        if nav_guard_ref.current:
            return
        pending_blur_ref.current = True

        async def _deferred():
            await asyncio.sleep(0.05)
            if pending_blur_ref.current:
                pending_blur_ref.current = False
                _commit_current()
                set_edit_cell(None)
                if on_table_blur is not None:
                    on_table_blur()

        page = ft.context.page
        if page is not None:
            page.run_task(_deferred)

    def _on_submit(e):
        """Enter 键：移动到下一行。"""
        _move_down()

    # ---- 导航回调（供 editor.py _on_key_down 通过 table_nav_ref 调用）----
    def _navigate(action: str, delta: int = 0):
        if action == "tab":
            _move_cell(delta)
        elif action == "escape":
            _exit_edit()

    if table_nav_ref is not None:
        table_nav_ref.current = _navigate

    # ---- 结构变更后定位新行 ----
    def _resolve_pending_nav():
        if pending_nav is None:
            return
        kind, col_idx = pending_nav
        if kind == "new_row":
            _, _, new_row_indices, _, _ = _parse_table_lines(lines, line_idx)
            if new_row_indices:
                _start_edit(new_row_indices[-1], col_idx)
        set_pending_nav(None)

    ft.use_effect(_resolve_pending_nav, [pending_nav])

    # ---- 当前选中行/列（工具栏操作目标）----
    sel = edit_cell or (
        (row_indices[-1] if row_indices else header_idx, col_count - 1)
    )
    sel_li, sel_ci = sel

    # ---- 工具栏操作 ----
    def _do_add_row():
        _commit_current()
        target_li = sel_li if sel_li in row_indices else (
            row_indices[-1] if row_indices else sep_idx
        )
        if on_table_op is not None:
            on_table_op("add_row", {"after_li": target_li, "col_count": col_count})
            set_pending_nav(("new_row", sel_ci))

    def _do_delete_row():
        if not row_indices:
            return
        target_li = sel_li if sel_li in row_indices else row_indices[-1]
        if len(row_indices) <= 1:
            if on_table_op is not None:
                on_table_op("clear_row", {"li": target_li})
            return
        _commit_current()
        if on_table_op is not None:
            on_table_op("delete_row", {"li": target_li})
        set_edit_cell(None)

    def _do_add_col():
        if on_table_op is not None:
            on_table_op("add_col", {"table_start": line_idx, "col_idx": sel_ci + 1})

    def _do_delete_col():
        if col_count <= 1:
            return
        if on_table_op is not None:
            on_table_op("delete_col", {"table_start": line_idx, "col_idx": sel_ci})
        set_edit_cell(None)

    def _do_set_align(align: str):
        if on_table_op is not None:
            on_table_op("set_align", {
                "table_start": line_idx, "col_idx": sel_ci, "align": align,
            })

    # ---- 右键菜单 ----
    def _cell_context_items(li: int, ci: int, is_header: bool) -> list:
        items: list = []
        if not is_header:
            items.extend([
                ft.PopupMenuItem(
                    content="上方插入行",
                    on_click=lambda e: (
                        on_table_op("add_row", {"after_li": li - 1, "col_count": col_count})
                        if on_table_op else None
                    ),
                ),
                ft.PopupMenuItem(
                    content="下方插入行",
                    on_click=lambda e: (
                        on_table_op("add_row", {"after_li": li, "col_count": col_count})
                        if on_table_op else None
                    ),
                ),
                ft.PopupMenuItem(
                    content="删除行",
                    on_click=lambda e: (
                        on_table_op("delete_row", {"li": li})
                        if on_table_op else None
                    ),
                ),
                ft.PopupMenuItem(),
            ])
        items.extend([
            ft.PopupMenuItem(
                content="左侧插入列",
                on_click=lambda e: (
                    on_table_op("add_col", {"table_start": line_idx, "col_idx": ci})
                    if on_table_op else None
                ),
            ),
            ft.PopupMenuItem(
                content="右侧插入列",
                on_click=lambda e: (
                    on_table_op("add_col", {"table_start": line_idx, "col_idx": ci + 1})
                    if on_table_op else None
                ),
            ),
            ft.PopupMenuItem(
                content="删除列",
                on_click=lambda e: (
                    on_table_op("delete_col", {"table_start": line_idx, "col_idx": ci})
                    if on_table_op else None
                ),
            ),
            ft.PopupMenuItem(),
            ft.PopupMenuItem(
                content="左对齐",
                on_click=lambda e: (
                    on_table_op("set_align", {
                        "table_start": line_idx, "col_idx": ci, "align": "left",
                    }) if on_table_op else None
                ),
            ),
            ft.PopupMenuItem(
                content="居中对齐",
                on_click=lambda e: (
                    on_table_op("set_align", {
                        "table_start": line_idx, "col_idx": ci, "align": "center",
                    }) if on_table_op else None
                ),
            ),
            ft.PopupMenuItem(
                content="右对齐",
                on_click=lambda e: (
                    on_table_op("set_align", {
                        "table_start": line_idx, "col_idx": ci, "align": "right",
                    }) if on_table_op else None
                ),
            ),
        ])
        return items

    # ---- 渲染：列头 ----
    columns: list = []
    for ci in range(col_count):
        is_editing = edit_cell == (header_idx, ci)
        if is_editing:
            label = ft.Container(
                content=ft.TextField(
                    key=f"th-edit-{nav_seq}",
                    value=edit_draft,
                    autofocus=True,
                    border=ft.InputBorder.NONE,
                    filled=True,
                    fill_color=_safe_color(c.link, 0.10),
                    dense=True,
                    content_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                    text_style=ft.TextStyle(
                        font_family=FONT_MAIN, color=c.text, size=14,
                        weight=ft.FontWeight.W_600,
                    ),
                    cursor_color=c.link,
                    selection_color=_safe_color(c.link, 0.18),
                    on_change=lambda e: _on_change_draft(e.control.value),
                    on_submit=_on_submit,
                    on_blur=lambda e: _on_blur(),
                    on_focus=lambda e: on_table_focus() if on_table_focus else None,
                ),
                bgcolor=_safe_color(c.link, 0.06),
                border_radius=6,
                padding=0,
            )
        else:
            inner = ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Icon(
                            _align_icon(aligns[ci]),
                            size=12,
                            color=c.muted,
                            tooltip=f"对齐: {aligns[ci]}",
                        ),
                        ft.Text(
                            value=_cell_text(header_row[ci]),
                            style=ft.TextStyle(
                                font_family=FONT_MAIN,
                                weight=ft.FontWeight.W_600,
                                color=c.text,
                                size=14,
                            ),
                            text_align=_align_text_align(aligns[ci]),
                            expand=True,
                        ),
                    ],
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding.symmetric(vertical=8, horizontal=8),
                border_radius=6,
            )
            label = ft.ContextMenu(
                content=ft.GestureDetector(
                    content=inner,
                    on_tap=lambda e, ci=ci: _start_edit(header_idx, ci),
                    mouse_cursor=ft.MouseCursor.CLICK,
                ),
                secondary_items=_cell_context_items(header_idx, ci, is_header=True),
            )
        columns.append(ft.DataColumn(label=label))

    # ---- 渲染：数据行 ----
    data_rows: list[ft.DataRow] = []
    for ri, source_li in enumerate(row_indices):
        row = normalized[ri + 1] if ri + 1 < len(normalized) else [""] * col_count
        cells: list[ft.DataCell] = []
        for ci in range(col_count):
            is_editing = edit_cell == (source_li, ci)
            if is_editing:
                content = ft.Container(
                    content=ft.TextField(
                        key=f"td-edit-{nav_seq}",
                        value=edit_draft,
                        autofocus=True,
                        border=ft.InputBorder.NONE,
                        filled=True,
                        fill_color=_safe_color(c.link, 0.10),
                        dense=True,
                        content_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                        text_style=ft.TextStyle(
                            font_family=FONT_MAIN, color=c.text, size=14,
                        ),
                        cursor_color=c.link,
                        selection_color=_safe_color(c.link, 0.18),
                        on_change=lambda e: _on_change_draft(e.control.value),
                        on_submit=_on_submit,
                        on_blur=lambda e: _on_blur(),
                        on_focus=lambda e: on_table_focus() if on_table_focus else None,
                    ),
                    border_radius=6,
                    bgcolor=_safe_color(c.link, 0.05),
                    padding=0,
                )
            else:
                inner = ft.Container(
                    content=ft.Text(
                        value=_cell_text(row[ci]),
                        style=ft.TextStyle(
                            font_family=FONT_MAIN, color=c.text, size=14,
                        ),
                        text_align=_align_text_align(aligns[ci]),
                        max_lines=4,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                    border_radius=6,
                )
                content = ft.ContextMenu(
                    content=ft.GestureDetector(
                        content=inner,
                        on_tap=lambda e, li=source_li, ci=ci: _start_edit(li, ci),
                        mouse_cursor=ft.MouseCursor.CLICK,
                    ),
                    secondary_items=_cell_context_items(source_li, ci, is_header=False),
                )
            cells.append(ft.DataCell(content=content))
        data_rows.append(
            ft.DataRow(
                cells=cells,
                color=_safe_color(c.text, 0.015 if ri % 2 == 0 else 0.0),
            )
        )

    # ---- 工具栏 ----
    def _tb_btn(label: str, on_click, icon: str | None = None, tooltip: str = ""):
        ctrl = ft.TextButton(
            label,
            on_click=on_click,
            icon=icon,
            tooltip=tooltip or label,
            style=ft.ButtonStyle(
                text_style=ft.TextStyle(size=12, color=c.text),
                padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                bgcolor=ft.Colors.TRANSPARENT,
            ),
        )
        return ctrl

    current_align = aligns[sel_ci] if sel_ci < len(aligns) else "left"
    align_dropdown = ft.Dropdown(
        value=current_align,
        options=[
            ft.DropdownOption(key="left", text="左对齐"),
            ft.DropdownOption(key="center", text="居中"),
            ft.DropdownOption(key="right", text="右对齐"),
        ],
        width=88,
        text_size=12,
        dense=True,
        content_padding=ft.Padding.symmetric(horizontal=6, vertical=0),
        border=ft.InputBorder.NONE,
        fill_color=ft.Colors.TRANSPARENT,
        on_select=lambda e: (
            _do_set_align(e.control.value)
            if e.control.value is not None else None
        ),
    )

    toolbar = ft.Row(
        controls=[
            ft.Icon(ft.Icons.TABLE_ROWS_ROUNDED, size=14, color=c.muted),
            ft.Text(
                f"{len(body_rows) + 1} × {col_count}",
                size=11, color=c.muted, font_family=FONT_MONO,
            ),
            ft.Container(expand=True),
            _tb_btn("+ 行", lambda e: _do_add_row(), tooltip="新增行"),
            _tb_btn("+ 列", lambda e: _do_add_col(), tooltip="新增列"),
            _tb_btn("删行", lambda e: _do_delete_row(), tooltip="删除行"),
            _tb_btn("删列", lambda e: _do_delete_col(), tooltip="删除列"),
            align_dropdown,
        ],
        spacing=4,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # ---- DataTable2 ----
    table = DataTable2(
        columns=columns,
        rows=data_rows,
        column_spacing=12,
        horizontal_margin=8,
        data_row_height=48,
        heading_row_height=44,
        divider_thickness=1,
        horizontal_lines=ft.BorderSide(1, _safe_color(c.border, 0.08)),
        vertical_lines=ft.BorderSide(1, _safe_color(c.border, 0.06)),
        border=ft.TableBorder.all(
            1, _safe_color(c.border, 0.10),
        ) if hasattr(ft, "TableBorder") else ft.Border.all(
            1, _safe_color(c.border, 0.10),
        ),
        border_radius=12,
        show_bottom_border=True,
        heading_row_color=_safe_color(c.link, 0.04),
        data_row_color={
            ft.ControlState.HOVERED: _safe_color(c.link, 0.04),
            ft.ControlState.PRESSED: _safe_color(c.link, 0.08),
        },
        bgcolor=_safe_color(
            getattr(c, "surface", c.code_bg), 0.96,
        ),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        show_checkbox_column=False,
        fixed_top_rows=1,
        fixed_left_columns=0,
        min_width=content_width,
    )

    container_bg = _safe_color(c.code_bg, 0.55)
    container_border = _safe_color(c.border, 0.08)

    # is_current_line 高亮
    content = ft.Container(
        content=ft.Column(
            controls=[toolbar, ft.Container(height=4), table],
            spacing=0,
        ),
        width=float("inf"),
        padding=ft.Padding.symmetric(horizontal=10, vertical=10),
        bgcolor=container_bg,
        border_radius=14,
        border=ft.Border.all(1, container_border),
    )

    if is_current_line:
        content = ft.Container(
            content=content,
            border=ft.Border.all(
                2, _safe_color(c.link, 0.20),
            ),
            border_radius=16,
            padding=ft.Padding.all(1),
        )

    return content
