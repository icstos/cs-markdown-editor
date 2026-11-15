"""演示区块：左边 Markdown 源码，右边同一份内容的渲染结果。

为什么用 `ft.Markdown` 而不是把编辑器嵌进来
------------------------------------------
编辑器是桌面控件树（透明光标层 + 本机字体的 HarfBuzz 测量），搬进浏览器既
不必要也不可靠；而且它是整个仓库里最重的一组依赖。这里要展示的是**渲染能力**
与「源码和结果一致」，用 Flet 内置 Markdown 已经足够真实。

状态
----
只有「当前看哪份样例」一个 state，且只属于这个区块——外壳不关心它。
"""

from __future__ import annotations

import ui

from cmsite import _content as K
from cmsite._theme import FONT_MONO, Layout, Radius, Type, current_colors, is_dark, tint
from cmsite._ui import card, icon, pill_tabs, section, section_header, text
from cmsite._widgets import CodePanel

#: 两张对照卡片的**外框**固定同高：样例长短不一，不定高会让整页随页签跳动。
#: 高度写在外层而不是内层，左右两边才能严格对齐（内层各自留白不同）。
_CARD_H = 430
#: 卡片内的滚动区高度（等于外框减去内边距与标题行）
_SRC_SCROLL_H = 342
_RENDER_SCROLL_H = 382


@ui.component
def DemoSection(width: int, clipboard_ref: ui.Ref | None = None) -> ui.Control:
    """实时预览区块。"""
    active, set_active = ui.use_state(0)
    sample = K.DEMO_SAMPLES[active]
    c = current_colors()
    narrow = width < 820

    tabs = pill_tabs(
        [(str(i), s.label) for i, s in enumerate(K.DEMO_SAMPLES)],
        str(active),
        lambda key: set_active(int(key)),
    )

    source_pane = ui.Container(
        height=_CARD_H,
        content=card(
            CodePanel(
                sample.source,
                clipboard_ref=clipboard_ref,
                label="markdown",
                height=_SRC_SCROLL_H,
                pad=12,
            ),
            pad=10,
            spacing=0,
        ),
    )
    render_pane = ui.Container(
        height=_CARD_H,
        content=card(
            ui.Container(
                height=_RENDER_SCROLL_H,
                padding=ui.Padding.only(right=4),
                content=ui.ListView(
                    spacing=0,
                    controls=[
                        ui.ft.Markdown(
                            value=sample.source,
                            extension_set=ui.MarkdownExtensionSet.GITHUB_FLAVORED,
                            code_theme=(
                                ui.MarkdownCodeTheme.ATOM_ONE_DARK
                                if is_dark(c)
                                else ui.MarkdownCodeTheme.GITHUB
                            ),
                            md_style_sheet=_md_style(c),
                            selectable=True,
                            shrink_wrap=True,
                        )
                    ], clip_behavior=ui.ClipBehavior.HARD_EDGE,
                ),
            ),
            pad=14,
            spacing=0,
        ),
    )

    panes: list[ui.Control] = (
        [source_pane, render_pane]
        if narrow
        else [
            ui.Row(
                spacing=Layout.GRID,
                vertical_alignment=ui.CrossAxisAlignment.START,
                controls=[
                    ui.Container(expand=True, content=source_pane),
                    ui.Container(expand=True, content=render_pane),
                ],
            )
        ]
    )

    return section(
        "demo",
        width,
        section_header(
            "02 / 演示",
            "源码与结果，同屏对照",
            "切换样例看各类语法块。编辑器里没有预览按钮，编辑时这两栏其实是同一栏。",
            width=width,
        ),
        _tabs_row(tabs, sample.desc, c, narrow),
        *panes,
        _caption(c),
    )


def _tabs_row(tabs: ui.Control, desc: str, c, narrow: bool) -> ui.Control:
    """页签 + 右侧说明。

    宽屏用 `SPACE_BETWEEN` 两端对齐，窄屏上下堆叠。
    注意不要写成 `Row(wrap=True)` 里塞 `expand=True` 的占位——
    `Wrap` 不接受 flex 子项，会直接抛错（整块变灰）。
    """
    if narrow:
        return ui.Column(spacing=8, tight=True, controls=[tabs, _hint(desc, c)])
    return ui.Row(
        spacing=12,
        vertical_alignment=ui.CrossAxisAlignment.CENTER,
        alignment=ui.MainAxisAlignment.SPACE_BETWEEN,
        controls=[tabs, _hint(desc, c)],
    )


def _md_style(c) -> ui.MarkdownStyleSheet:
    """把演示用的 Markdown 套上站点自己的排版与标题色阶。

    默认样式是 flutter_markdown 的一刀切黑色，与编辑器里的彩色标题、
    紧凑行距差得很远；不覆盖的话，"同屏对照"反而会让人以为产品就这样。
    """
    head = c.heading_colors

    def h(level: int, size: float) -> ui.TextStyle:
        return ui.TextStyle(
            size=size,
            weight=ui.FontWeight.W_700 if level <= 2 else ui.FontWeight.W_600,
            color=head.get(level, c.text),
            height=1.35,
        )

    return ui.MarkdownStyleSheet(
        p_text_style=ui.TextStyle(size=13.5, color=c.text, height=1.7),
        h1_text_style=h(1, 20),
        h2_text_style=h(2, 17),
        h3_text_style=h(3, 15),
        h4_text_style=h(4, 14),
        h5_text_style=h(5, 13.5),
        h6_text_style=h(6, 13),
        code_text_style=ui.TextStyle(
            size=12, color=c.code_fg, bgcolor=c.code_bg, font_family=FONT_MONO
        ),
        blockquote_text_style=ui.TextStyle(size=13.5, color=c.quote_fg),
        list_bullet_text_style=ui.TextStyle(size=13.5, color=c.muted),
        a_text_style=ui.TextStyle(size=13.5, color=c.link),
        strong_text_style=ui.TextStyle(weight=ui.FontWeight.W_700, color=c.text),
        em_text_style=ui.TextStyle(italic=True, color=c.text),
        table_body_text_style=ui.TextStyle(size=12.5, color=c.text),
        table_head_text_style=ui.TextStyle(size=12.5, color=c.text, weight=ui.FontWeight.W_600),
        block_spacing=10,
        list_indent=18,
    )


def _hint(desc: str, c) -> ui.Control:
    return ui.Container(
        width=360,
        content=text(desc, size=Type.TINY, color=c.muted),
    )


def _caption(c) -> ui.Control:
    """底部说明条：把「为什么这样做」讲清楚，避免演示沦为装饰。"""
    return ui.Container(
        padding=ui.Padding.all(12),
        bgcolor=tint(c.link, 0.04),
        border_radius=Radius.MD,
        content=ui.Row(
            spacing=8,
            vertical_alignment=ui.CrossAxisAlignment.START,
            controls=[
                icon("LIGHTBULB_OUTLINE", size=15, color=c.link),
                ui.Container(
                    expand=True,
                    content=text(
                        "右侧由 Flutter 渲染，语法规则与编辑器同源。"
                        "左边源码能选中复制，改完粘回去就是结果。",
                        size=Type.TINY,
                        color=c.muted,
                    ),
                ),
            ],
        ),
    )
