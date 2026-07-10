"""段级渲染：把 Segment 转为可点击的 TextSpan（渲染态）与内嵌 TextField（编辑态）。

设计原则：
- 渲染态用 TextSpan 参与 Text 的整体排版（自动换行，符合阅读习惯）。
- 编辑态用一个“无框、同字号”的 TextField 内嵌进行内，仅当前段显示原生 Markdown，
  其余段仍为渲染样式——这就是 Typora 式“最小语法”段级编辑。
"""

from __future__ import annotations

from typing import Callable

import flet as ft

from models import (
    SEG_CODE,
    SEG_CODESPAN,
    SEG_HEADING_PREFIX,
    SEG_IMAGE,
    SEG_LINK,
    SEG_LIST_PREFIX,
    SEG_QUOTE_PREFIX,
    Segment,
)
from styles import (
    C_ACTIVE_BG,
    C_TEXT,
    FONT_MAIN,
    FONT_MONO,
    measure_text_width,
    prefix_style,
    segment_style,
)


def _display_text(seg: Segment) -> str:
    """渲染态展示文本。"""
    if seg.seg_type in (SEG_HEADING_PREFIX, SEG_LIST_PREFIX, SEG_QUOTE_PREFIX):
        return seg.raw
    if seg.seg_type == SEG_IMAGE:
        return seg.text or "🖼"
    if seg.seg_type == SEG_LINK:
        return seg.text or seg.url or "链接"
    return seg.text


def segment_to_span(
    seg: Segment,
    seg_idx: int,
    on_activate: Callable[[int], None],
    base_size: int,
) -> ft.TextSpan:
    """渲染态：段 -> TextSpan（可点击激活）。"""
    if seg.seg_type in (SEG_HEADING_PREFIX, SEG_LIST_PREFIX, SEG_QUOTE_PREFIX):
        style = prefix_style(seg, base_size)
    else:
        style = segment_style(seg, base_size)

    return ft.TextSpan(
        text=_display_text(seg),
        style=style,
        on_click=lambda e: on_activate(seg_idx),
    )


def active_text_field(
    seg: Segment,
    draft: str,
    on_change: Callable[[str], None],
    on_submit: Callable[[str], None],
    on_blur: Callable[[], None],
    base_size: int,
    multiline: bool = False,
) -> ft.TextField:
    """编辑态：段 -> 内嵌无框 TextField，显示该段原生 Markdown。

    单行段：依据本地字体测量文本宽度，让 TextField 恰好包裹文本内容
    （Typora 式最小编辑块），避免撑满整行破坏阅读节奏。
    多行代码块：保持块级宽度，由父容器决定。
    """
    is_code = seg.seg_type in (SEG_CODESPAN, SEG_CODE)
    font_family = FONT_MONO if is_code else FONT_MAIN
    text_size = base_size if not is_code else max(base_size - 1, 12)

    kwargs: dict = dict(
        value=draft,
        autofocus=True,
        multiline=multiline,
        min_lines=1,
        max_lines=None if multiline else 1,
        border=ft.InputBorder.NONE,
        border_radius=4,
        filled=True,
        fill_color=C_ACTIVE_BG,
        content_padding=ft.Padding.symmetric(horizontal=4, vertical=0),
        text_size=text_size,
        text_style=ft.TextStyle(
            font_family=font_family,
            color=C_TEXT,
        ),
        cursor_color=C_TEXT,
        cursor_width=1.5,
        shift_enter=multiline,
        on_change=lambda e: on_change(e.control.value),
        on_submit=lambda e: on_submit(e.control.value),
        on_blur=lambda e: on_blur(),
    )

    if not multiline:
        # 文本像素宽 + 内边距(左右各4) + 光标/子像素余量；空文本给最小宽避免坍缩
        text_w = measure_text_width(draft or "", font_family, text_size)
        field_w = max(text_w + 8 + 6, 24)
        kwargs["width"] = field_w

    return ft.TextField(**kwargs)
