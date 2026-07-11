"""格式工具栏。

按钮分三组：
- 块级（标题 / 列表 / 引用 / 代码块 / 分隔线）：改变当前行块类型。
- 行内（加粗 / 斜体 / 行内代码 / 链接 / 删除线）：在激活段上包裹或解包语法。
- 视图（原文模式）：在 WYSIWYG 编辑与原始 Markdown 文本间切换。

操作通过回调上抛到编辑器，工具栏本身无状态，符合单向数据流。
"""

from typing import Callable

import flet as ft

from styles import C_BORDER, C_MUTED, C_TOOLBAR_BG, only_border


def _btn(
    icon: str, tooltip: str, on_click: Callable[[], None], toggle_on: bool = False
) -> ft.Control:
    return ft.IconButton(
        icon=icon,
        tooltip=tooltip,
        on_click=lambda e: on_click(),
        icon_size=18,
        style=ft.ButtonStyle(
            color=ft.Colors.with_opacity(1.0, "#1677FF") if toggle_on else C_MUTED,
            bgcolor=ft.Colors.with_opacity(0.0, ft.Colors.TRANSPARENT),
            padding=4,
        ),
    )


def _divider() -> ft.Control:
    return ft.Container(
        width=1, height=20, bgcolor=C_BORDER, margin=ft.Margin.symmetric(horizontal=4)
    )


@ft.component
def Toolbar(
    on_h1: Callable[[], None],
    on_h2: Callable[[], None],
    on_h3: Callable[[], None],
    on_paragraph: Callable[[], None],
    on_list: Callable[[], None],
    on_quote: Callable[[], None],
    on_code_block: Callable[[], None],
    on_hr: Callable[[], None],
    on_bold: Callable[[], None],
    on_italic: Callable[[], None],
    on_code: Callable[[], None],
    on_link: Callable[[], None],
    on_strike: Callable[[], None],
    on_toggle_raw: Callable[[], None],
    raw_mode: bool = False,
):
    return ft.Container(
        bgcolor=C_TOOLBAR_BG,
        padding=ft.Padding.symmetric(horizontal=12, vertical=4),
        border=only_border(bottom=ft.BorderSide(1, C_BORDER)),
        content=ft.Row(
            controls=[
                _btn(ft.Icons.TITLE, "一级标题  Ctrl+1", on_h1),
                _btn(ft.Icons.FORMAT_SIZE, "二级标题  Ctrl+2", on_h2),
                _btn(ft.Icons.TEXT_FIELDS, "三级标题  Ctrl+3", on_h3),
                _btn(ft.Icons.FORMAT_ALIGN_LEFT, "正文段落", on_paragraph),
                _divider(),
                _btn(ft.Icons.FORMAT_LIST_BULLETED, "无序列表", on_list),
                _btn(ft.Icons.FORMAT_QUOTE, "引用", on_quote),
                _btn(ft.Icons.CODE, "代码块", on_code_block),
                _btn(ft.Icons.HORIZONTAL_RULE, "分隔线", on_hr),
                _divider(),
                _btn(ft.Icons.FORMAT_BOLD, "加粗  Ctrl+B", on_bold),
                _btn(ft.Icons.FORMAT_ITALIC, "斜体  Ctrl+I", on_italic),
                _btn(ft.Icons.CODE, "行内代码", on_code),
                _btn(ft.Icons.LINK, "链接  Ctrl+K", on_link),
                _btn(ft.Icons.FORMAT_STRIKETHROUGH, "删除线", on_strike),
                _divider(),
                _btn(
                    ft.Icons.VISIBILITY if not raw_mode else ft.Icons.EDIT,
                    "原文模式" if not raw_mode else "返回编辑",
                    on_toggle_raw,
                    toggle_on=raw_mode,
                ),
            ],
            spacing=2,
            scroll=ft.ScrollMode.AUTO,
        ),
    )
