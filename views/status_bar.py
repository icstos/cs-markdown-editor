"""底部状态栏：贯穿侧边栏 + 编辑区全宽。

从 main.py 的 _build_footer 抽出，封装为独立组件。显示侧边栏切换、脏标记、
文件名、光标行列、字数与字符数。
"""

import os
import re
from collections.abc import Callable

import flet as ft

from models import Document
import parser
from styles import FONT_MAIN, get_colors, only_border

# 中英文词数统计正则：英文连续字母数字下划线算一词，中文每字算一词
_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def _file_name(path: str | None) -> str:
    return os.path.basename(path) if path else "未命名.md"


@ft.component
def StatusBar(
    document: Document,
    file_path: str | None,
    dirty: bool,
    sidebar_open: bool,
    cursor_row_col: tuple[int, int],
    theme_mode: ft.ThemeMode,
    on_toggle_sidebar: Callable[[], None],
):
    """底部状态栏。

    cursor_row_col 由 main.py 通过 actions.get_cursor_row_col() 取得后传入，
    避免本组件直接依赖 actions（保持纯视图层）。
    """
    c = get_colors(theme_mode)
    row, col = cursor_row_col
    md_text = parser.serialize(document)
    char_count = len(md_text)
    word_count = len(_WORD_RE.findall(md_text))
    fname = _file_name(file_path)

    return ft.Container(
        bgcolor=ft.Colors.with_opacity(0.03, c.text),
        border=only_border(top=ft.BorderSide(1, c.border)),
        padding=ft.Padding.symmetric(horizontal=10, vertical=6),
        content=ft.Row(
            controls=[
                ft.IconButton(
                    icon=ft.Icons.VIEW_SIDEBAR if not sidebar_open else ft.Icons.MENU_OPEN,
                    tooltip="切换侧边栏",
                    on_click=lambda e: on_toggle_sidebar(),
                    icon_size=16,
                    style=ft.ButtonStyle(
                        color=c.link if sidebar_open else c.muted,
                        padding=4,
                    ),
                ),
                ft.Icon(
                    icon=ft.Icons.CIRCLE,
                    size=8,
                    color="#FF9F0A" if document.dirty else "#35C759",
                ),
                ft.Text(
                    value=fname,
                    size=12,
                    color=c.muted,
                    font_family=FONT_MAIN,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Container(expand=True),
                ft.Text(
                    value=f"行 {row}  列 {col}",
                    size=12,
                    color=c.muted,
                    font_family=FONT_MAIN,
                ),
                ft.Container(width=16),
                ft.Text(
                    value=f"{word_count} 词",
                    size=12,
                    color=c.muted,
                    font_family=FONT_MAIN,
                ),
                ft.Container(width=12),
                ft.Text(
                    value=f"{char_count} 字符",
                    size=12,
                    color=c.muted,
                    font_family=FONT_MAIN,
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
