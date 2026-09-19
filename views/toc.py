"""右侧大纲列的标题树渲染（VSCode / Obsidian 风格）。

从 views/sidebar.py 迁出：大纲面板与文件/搜索面板原先挤在同一文件，而它只
依赖标题条目与配色。独立后 views/outline_panel.py 直接复用，不再反向导入
侧边栏私有渲染函数。本模块自包含（含色条与行容器工厂），不依赖 sidebar。

标题条目结构：[(line_idx, level, text), ...]（见 utils.toc.compute_toc）。

配色契约（本模块的核心不变式）：
**色条与文字同色，且都取自 styles.heading_text_color(level)** —— 与编辑区正文里
那行标题用的是同一个取色口（views/segment_view.py 的标题分支同源）。
于是大纲里每一条的颜色就是它在正文里的颜色：扫一眼侧栏即可把条目对回正文，
不需要靠「记住第 3 个颜色是 H3」这种间接映射；亮/暗主题各自的六级色阶由
styles 维护，大纲自动跟随，本模块不出现任何写死的颜色。

字体层级同理取自 styles.outline_heading_weight（编辑区字重降一档），
保证「大纲里的粗细顺序」与「正文里的粗细顺序」逐级一致。
tests/test_outline_color.py 直接对比「大纲渲染出的色/重」与「编辑区渲染出的色/重」。
"""

from collections.abc import Callable

import flet as ft

from styles import (
    FONT_MAIN,
    Radius,
    Spacing,
    heading_text_color,
    outline_heading_weight,
)

_INDENT_STEP = 14  # 每级缩进（逻辑 px）
_BAR_W = 3  # 级别色条宽
_BAR_H = 14  # 级别色条高（≈ 12px 文字的行框高，与文字垂直居中）
_BAR_RADIUS = 2
_TEXT_SIZE = 12  # 条目标题字号


def outline_color_bar(level: int, c) -> ft.Control:
    """大纲级别色条：细竖线，与条目标题文字同色（heading_text_color）。"""
    return ft.Container(
        width=_BAR_W,
        height=_BAR_H,
        bgcolor=heading_text_color(level, c),
        border_radius=_BAR_RADIUS,
    )


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
    """单条大纲行：级别色条 + 标题文本，点击跳转到对应行。

    文字的颜色与字重都按级别取自「与正文标题同源」的两个 styles 取色口，
    因此同级条目在侧栏与正文里的观感一致（色条只是同一颜色的短竖线强调）。
    """
    return ft.Container(
        border_radius=Radius.MD,
        padding=ft.Padding.only(
            left=(lvl - 1) * _INDENT_STEP + Spacing.XL,
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
                    size=_TEXT_SIZE,
                    color=heading_text_color(lvl, c),
                    font_family=FONT_MAIN,
                    weight=outline_heading_weight(lvl),
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
    文字色与色条色都取自 heading_text_color(level)，与编辑区标题一一对应，
    改主题时侧栏与正文同步换色，无需在这里维护第二套配色。
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
