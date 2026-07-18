"""表格视图：基于 Flet DataTable 的 Typora 风格 Markdown 表格渲染与编辑。"""

from __future__ import annotations

import re
from collections.abc import Callable

import flet as ft

from models import BlockType, Line
from styles import FONT_MAIN, FONT_MONO, _current_colors

_ALIGN_RE = re.compile(r"^:?-{3,}:?$")


def _parse_table_lines(
    lines: list[Line], start_idx: int
) -> tuple[list[int], list[list[str]], list[str]]:
    """从 start_idx 开始收集连续表格行，返回 (row_indices, rows, aligns)。"""
    row_indices: list[int] = []
    rows: list[list[str]] = []
    aligns: list[str] = []
    i = start_idx
    while i < len(lines) and lines[i].block_type == BlockType.TABLE:
        cells = [c.strip() for c in lines[i].raw.strip().strip("|").split("|")]
        if not row_indices:
            row_indices.append(i)
            rows.append(cells)
        else:
            if all(_ALIGN_RE.fullmatch(c or "---") for c in cells):
                aligns = cells
            else:
                row_indices.append(i)
                rows.append(cells)
        i += 1
    return row_indices, rows, aligns


def _normalize_rows(rows: list[list[str]]) -> list[list[str]]:
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


def _cell_text(cell: str) -> str:
    return cell.strip() or " "


@ft.component
def TableView(
    lines: list[Line],
    line_idx: int,
    active_seg: int | None,
    draft: str,
    on_activate: Callable[[int, int, int], None],
    on_change_draft: Callable[[str], None],
    on_submit: Callable[[str], None],
    on_blur: Callable[[], None],
    on_selection_change: Callable | None = None,
    initial_cursor: int = -1,
    nav_seq: int = 0,
    field_ref: ft.Ref | None = None,
    content_width: float | None = None,
):
    c = _current_colors()
    row_indices, rows, aligns = _parse_table_lines(lines, line_idx)
    if not rows:
        return ft.Container()

    normalized = _normalize_rows(rows)
    col_count = len(normalized[0]) if normalized else 0
    aligns = (aligns + [""] * max(0, col_count - len(aligns)))[:col_count]

    def _align(idx: int) -> ft.TextAlign:
        val = aligns[idx].strip() if idx < len(aligns) else ""
        if val.startswith(":") and val.endswith(":"):
            return ft.TextAlign.CENTER
        if val.endswith(":"):
            return ft.TextAlign.RIGHT
        return ft.TextAlign.LEFT

    def _on_cell_tap(li: int, ci: int):
        on_activate(row_indices[li], ci, -1)

    header_style = ft.TextStyle(
        font_family=FONT_MAIN, weight=ft.FontWeight.W_600, color=c.text
    )
    body_style = ft.TextStyle(font_family=FONT_MAIN, color=c.text)

    columns = []
    for ci in range(col_count):
        columns.append(
            ft.DataColumn(
                label=ft.Container(
                    content=ft.Text(
                        value=_cell_text(normalized[0][ci]),
                        style=header_style,
                        text_align=_align(ci),
                    ),
                    padding=ft.Padding.symmetric(vertical=8, horizontal=4),
                ),
            )
        )

    data_rows: list[ft.DataRow] = []
    for ri, row in enumerate(normalized[1:] if len(normalized) > 1 else normalized):
        cells: list[ft.DataCell] = []
        for ci in range(col_count):
            is_active = active_seg == ci and row_indices[ri] == line_idx
            value = draft if is_active else _cell_text(row[ci])
            text = ft.Text(
                value=value,
                style=ft.TextStyle(
                    font_family=FONT_MONO if is_active else FONT_MAIN,
                    color=c.text,
                    size=15,
                ),
                text_align=_align(ci),
                no_wrap=not is_active,
            )
            if is_active:
                cell_content = ft.Container(
                    content=ft.TextField(
                        key=f"table-field-{nav_seq}",
                        value=draft,
                        autofocus=True,
                        border=ft.InputBorder.NONE,
                        filled=True,
                        fill_color=c.active_bg,
                        dense=True,
                        content_padding=ft.Padding.symmetric(horizontal=4, vertical=2),
                        text_style=ft.TextStyle(font_family=FONT_MAIN, color=c.text),
                        on_change=lambda e: on_change_draft(e.control.value),
                        on_submit=lambda e: on_submit(e.control.value),
                        on_blur=lambda e: on_blur(),
                        on_selection_change=on_selection_change,
                        ref=field_ref,
                    ),
                    padding=ft.Padding.symmetric(horizontal=4, vertical=2),
                )
            else:
                cell_content = ft.Container(
                    content=text,
                    padding=ft.Padding.symmetric(horizontal=8, vertical=8),
                    on_click=lambda e, ri=ri, ci=ci: _on_cell_tap(ri, ci),
                    ink=True,
                )
            cells.append(ft.DataCell(content=cell_content))
        row_color = ft.Colors.with_opacity(0.03 if ri % 2 == 0 else 0.00, c.text)
        data_rows.append(ft.DataRow(cells=cells, color=row_color))

    table = ft.DataTable(
        columns=columns,
        rows=data_rows,
        column_spacing=18,
        horizontal_margin=8,
        data_row_min_height=42,
        data_row_max_height=72,
        heading_row_height=48,
        divider_thickness=1,
        horizontal_lines=ft.BorderSide(1, ft.Colors.with_opacity(0.10, c.border)),
        vertical_lines=ft.BorderSide(1, ft.Colors.with_opacity(0.07, c.border)),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.12, c.border)),
        border_radius=8,
        show_bottom_border=True,
        heading_row_color=ft.Colors.with_opacity(0.05, c.text),
        data_row_color={ft.ControlState.PRESSED: c.active_bg},
        bgcolor=c.code_bg,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )

    return ft.Container(
        content=ft.Column(
            [
                ft.Container(
                    content=ft.Text(
                        "Markdown Table", size=12, color=c.muted, font_family=FONT_MONO
                    ),
                    padding=ft.Padding.only(bottom=6),
                ),
                table,
            ],
            spacing=0,
        ),
        width=float("inf"),
        padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        bgcolor=c.code_bg,
        border_radius=10,
    )
