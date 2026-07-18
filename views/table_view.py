"""表格视图：基于 DataTable2 的 Typora 风格 Markdown 表格渲染与编辑。"""

from __future__ import annotations

import re
from collections.abc import Callable

import flet as ft

try:
    from flet_datatable2 import DataTable2
except Exception:  # pragma: no cover - optional dependency fallback
    DataTable2 = ft.DataTable

from models import BlockType, Line
from styles import FONT_MAIN, FONT_MONO, _current_colors

_ALIGN_RE = re.compile(r"^:?-{3,}:?$")


def _parse_table_lines(
    lines: list[Line], start_idx: int
) -> tuple[int, list[int], list[list[str]], list[str]]:
    """从 start_idx 开始收集连续表格行，返回 (header_idx, row_indices, rows, aligns)。"""
    row_indices: list[int] = []
    rows: list[list[str]] = []
    aligns: list[str] = []
    i = start_idx
    header_idx = start_idx
    seen_header = False
    while i < len(lines) and lines[i].block_type == BlockType.TABLE:
        cells = [c.strip() for c in lines[i].raw.strip().strip("|").split("|")]
        if all(_ALIGN_RE.fullmatch(c or "---") for c in cells):
            aligns = cells
        elif not seen_header:
            header_idx = i
            rows.append(cells)
            seen_header = True
        else:
            row_indices.append(i)
            rows.append(cells)
        i += 1
    return header_idx, row_indices, rows, aligns


def _normalize_rows(rows: list[list[str]]) -> list[list[str]]:
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


def _cell_text(cell: str) -> str:
    return cell.strip() or " "


def _safe_color(color: str, opacity: float) -> str:
    return ft.Colors.with_opacity(opacity, color)


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
    active_line_idx: int | None = None,
    active_cell_idx: int | None = None,
):
    c = _current_colors()
    header_idx, row_indices, rows, aligns = _parse_table_lines(lines, line_idx)
    active_line_idx = line_idx if active_line_idx is None else active_line_idx
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

    def _on_cell_tap(source_line_idx: int, ci: int):
        on_activate(source_line_idx, 0, ci)

    def _cell_padding(is_active: bool) -> ft.Padding:
        return ft.Padding.symmetric(horizontal=12 if is_active else 10, vertical=9)

    columns = []
    for ci in range(col_count):
        columns.append(
            ft.DataColumn(
                label=ft.Container(
                    content=ft.Text(
                        value=_cell_text(normalized[0][ci]),
                        style=ft.TextStyle(
                            font_family=FONT_MAIN,
                            weight=ft.FontWeight.W_600,
                            color=c.text,
                            size=14,
                        ),
                        text_align=_align(ci),
                    ),
                    padding=ft.Padding.symmetric(vertical=10, horizontal=10),
                ),
            )
        )

    data_rows: list[ft.DataRow] = []
    body_rows = normalized[1:] if len(normalized) > 1 else []
    for ri, row in enumerate(body_rows, start=1):
        source_line_idx = row_indices[ri - 1]
        cells: list[ft.DataCell] = []
        for ci in range(col_count):
            is_active = active_cell_idx == ci and source_line_idx == active_line_idx
            value = draft if is_active else _cell_text(row[ci])
            if is_active:
                cell_content = ft.Container(
                    content=ft.TextField(
                        key=f"table-field-{nav_seq}",
                        value=draft,
                        autofocus=True,
                        border=ft.InputBorder.NONE,
                        filled=True,
                        fill_color=_safe_color(c.link, 0.08),
                        dense=True,
                        content_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                        text_style=ft.TextStyle(font_family=FONT_MAIN, color=c.text, size=15),
                        cursor_color=c.link,
                        selection_color=_safe_color(c.link, 0.18),
                        on_change=lambda e: on_change_draft(e.control.value),
                        on_submit=lambda e: on_submit(e.control.value),
                        on_blur=lambda e: on_blur(),
                        on_selection_change=on_selection_change,
                        ref=field_ref,
                    ),
                    border_radius=8,
                    bgcolor=_safe_color(c.link, 0.05),
                    padding=0,
                )
            else:
                cell_content = ft.GestureDetector(
                    content=ft.Container(
                        content=ft.Text(
                            value=value,
                            style=ft.TextStyle(
                                font_family=FONT_MAIN,
                                color=c.text,
                                size=15,
                            ),
                            text_align=_align(ci),
                            no_wrap=False,
                            selectable=False,
                            max_lines=4,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        padding=ft.Padding.symmetric(horizontal=10, vertical=10),
                        border_radius=8,
                    ),
                    on_tap=lambda e, source_line_idx=source_line_idx, ci=ci: _on_cell_tap(source_line_idx, ci),
                    mouse_cursor=ft.MouseCursor.CLICK,
                )
            cells.append(ft.DataCell(content=cell_content))
        row_color = _safe_color(c.text, 0.02 if ri % 2 == 0 else 0.00)
        data_rows.append(ft.DataRow(cells=cells, color=row_color))

    table_cls = DataTable2
    table = table_cls(
        columns=columns,
        rows=data_rows,
        column_spacing=16,
        horizontal_margin=10,
        data_row_height=52,
        heading_row_height=46,
        divider_thickness=1,
        horizontal_lines=ft.BorderSide(1, _safe_color(c.border, 0.08)),
        vertical_lines=ft.BorderSide(1, _safe_color(c.border, 0.08)),
        border=ft.Border.all(1, _safe_color(c.border, 0.10)),
        border_radius=14,
        show_bottom_border=True,
        heading_row_color=_safe_color(c.link, 0.04),
        data_row_color={
            ft.ControlState.HOVERED: _safe_color(c.link, 0.04),
            ft.ControlState.PRESSED: _safe_color(c.link, 0.08),
        },
        bgcolor=_safe_color(c.surface if hasattr(c, "surface") else c.code_bg, 0.96),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        heading_checkbox_theme=None,
        show_checkbox_column=False,
        fixed_top_rows=1,
        fixed_left_columns=0,
        fixed_columns_color=_safe_color(c.surface if hasattr(c, "surface") else c.code_bg, 0.98),
        min_width=content_width,
    )

    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.TABLE_ROWS_ROUNDED, size=16, color=c.muted),
                        ft.Text(
                            "Markdown Table",
                            size=12,
                            color=c.muted,
                            font_family=FONT_MONO,
                        ),
                        ft.Container(
                            content=ft.Text(
                                f"{len(body_rows)} × {col_count}",
                                size=11,
                                color=c.muted,
                                font_family=FONT_MONO,
                            ),
                            padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                            border_radius=999,
                            bgcolor=_safe_color(c.text, 0.04),
                        ),
                    ],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(height=8),
                table,
            ],
            spacing=0,
        ),
        width=float("inf"),
        padding=ft.Padding.symmetric(horizontal=10, vertical=10),
        bgcolor=_safe_color(c.code_bg, 0.55),
        border_radius=16,
        border=ft.Border.all(1, _safe_color(c.border, 0.08)),
    )
