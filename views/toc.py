"""右侧大纲列的标题树渲染（VSCode / Obsidian 风格）。

从 views/sidebar.py 迁出：大纲面板与文件/搜索面板原先挤在同一文件，而它只
依赖标题条目与配色。独立后 views/outline_panel.py 直接复用，不再反向导入
侧边栏私有渲染函数。本模块自包含（含色条与行容器工厂），不依赖 sidebar。

标题条目结构：[(line_idx, level, text), ...]（见 utils.toc.compute_toc）。
"""

from collections.abc import Callable

import flet as ft

from styles import FONT_MAIN, Radius, Spacing


def outline_color_bar(level: int, c) -> ft.Control:
    """大纲级别色条：细竖线，颜色复用 heading_colors（红橙绿青蓝紫）。"""
    color = c.heading_colors.get(level, c.muted)
    return ft.Container(width=3, height=14, bgcolor=color, border_radius=2)


def _empty_hint(text: str, c) -> ft.Control:
    """空态提示文本（居中弱化样式）。"""
    return ft.Container(
        alignment=ft.Alignment.CENTER,
        padding=ft.Padding.symmetric(vertical=Spacing.XXL),
        content=ft.Text(text, size=11, color=c.muted, font_family=FONT_MAIN),
    )


def _outline_row(
    li: int,
    lvl: int,
    text: str,
    on_jump_to_line: Callable[[int], None],
    c,
) -> ft.Control:
    """单条大纲行：级别色条 + 标题文本，点击跳转到对应行。"""
    return ft.Container(
        border_radius=Radius.MD,
        padding=ft.Padding.only(
            left=(lvl - 1) * 14 + Spacing.XL,
            right=Spacing.SM,
            top=Spacing.XS,
            bottom=Spacing.XS,
        ),
        on_click=lambda e, i=li: on_jump_to_line(i),
        key=f"toc-{li}",
        content=ft.Row(
            controls=[
                outline_color_bar(lvl, c),
                ft.Text(
                    value=text,
                    size=12,
                    color=c.text,
                    font_family=FONT_MAIN,
                    weight=ft.FontWeight.W_600 if lvl <= 2 else ft.FontWeight.NORMAL,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                ),
            ],
            spacing=Spacing.MD,
        ),
    )


def render_outline_panel(
    toc_entries: list[tuple[int, int, str]],
    on_jump_to_line: Callable[[int], None],
    c,
) -> ft.Control:
    """大纲面板：标题按级别缩进，左侧细竖线色条着色，点击跳转。

    同级别条目左对齐到同一缩进位置（(lvl-1)*14 + Spacing.XL）；
    色条颜色对应 heading_colors（红橙绿青蓝紫），一眼区分标题级别。
    H1/H2 文本加粗以突出主要章节。
    """
    if not toc_entries:
        return _empty_hint("文档无标题", c)
    return ft.ListView(
        controls=[
            _outline_row(li, lvl, text, on_jump_to_line, c)
            for li, lvl, text in toc_entries
        ],
        spacing=0,
        expand=True,
        first_item_prototype=True,
        padding=ft.Padding.symmetric(vertical=Spacing.XS),
    )
