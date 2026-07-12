"""段级渲染：把 Segment 转为可点击的 TextSpan（渲染态）与内嵌 TextField（编辑态）。

设计原则：
- 渲染态用 TextSpan 参与 Text 的整体排版（自动换行，符合阅读习惯）。
- 编辑态用一个"无框、同字号"的 TextField 内嵌进行内，仅当前段显示原生 Markdown，
  其余段仍为渲染样式——这就是 Typora 式"最小语法"段级编辑。
"""

from typing import Callable

import flet as ft

from models import SegType, Segment
from styles import (
    C_ACTIVE_BG,
    C_TEXT,
    FONT_MAIN,
    FONT_MONO,
    measure_text_width,
    prefix_style,
    segment_style,
)

_PREFIX_SEGTYPES = (SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX)
_MONO_SEGTYPES = (SegType.CODESPAN, SegType.CODE, SegType.INLINE_MATH, SegType.MATH)


def _display_text(seg: Segment) -> str:
    """渲染态展示文本。"""
    if seg.seg_type in _PREFIX_SEGTYPES:
        return seg.raw
    if seg.seg_type == SegType.IMAGE:
        return seg.text or "🖼"
    if seg.seg_type == SegType.LINK:
        return seg.text or seg.url or "链接"
    return seg.text


def segment_to_span(
    seg: Segment,
    seg_idx: int,
    on_activate: Callable[[int], None],
    base_size: int,
) -> ft.TextSpan:
    """渲染态：段 -> TextSpan（可点击激活）。"""
    style = prefix_style(seg, base_size) if seg.seg_type in _PREFIX_SEGTYPES else segment_style(seg, base_size)
    return ft.TextSpan(text=_display_text(seg), style=style, on_click=lambda e: on_activate(seg_idx))


def active_text_field(
    seg: Segment,
    draft: str,
    on_change: Callable[[str], None],
    on_submit: Callable[[str], None],
    on_blur: Callable[[], None],
    base_size: int,
    multiline: bool = False,
    on_selection_change: Callable | None = None,
    initial_cursor: int = -1,
    nav_seq: int = 0,
) -> ft.TextField:
    """编辑态：段 -> 内嵌无框 TextField，显示该段原生 Markdown。

    单行段：依据本地字体测量文本宽度，让 TextField 恰好包裹文本内容
    （Typora 式最小编辑块），避免撑满整行破坏阅读节奏。
    多行代码块：保持块级宽度，由父容器决定。

    光标导航：
    - on_selection_change：上报光标位置变化（供外层跟踪 extent/base）
    - initial_cursor + nav_seq：跨段时通过 nav_seq 变化触发 key 重建，
      使 selection 精确落到段首/段尾/对应偏移；输入时 nav_seq 不变、
      selection 值不变，光标不被重置。
    - ignore_up_down_keys：单行段置 True，让上下键冒泡到外层做跨行；
      多行代码块保持 False，让上下键在块内移动光标。
    """
    is_mono = seg.seg_type in _MONO_SEGTYPES
    font_family = FONT_MONO if is_mono else FONT_MAIN
    text_size = base_size if not is_mono else max(base_size - 1, 12)

    sel = (
        ft.TextSelection(base_offset=initial_cursor, extent_offset=initial_cursor)
        if initial_cursor >= 0
        else None
    )

    kwargs: dict = {
        "key": f"field-{nav_seq}",
        "value": draft,
        "autofocus": True,
        "multiline": multiline,
        "min_lines": 1,
        "max_lines": None if multiline else 1,
        "border": ft.InputBorder.NONE,
        "border_radius": 4,
        "filled": True,
        "fill_color": C_ACTIVE_BG,
        "content_padding": ft.Padding.symmetric(horizontal=4, vertical=0),
        "text_size": text_size,
        "text_style": ft.TextStyle(font_family=font_family, color=C_TEXT),
        "cursor_color": C_TEXT,
        "cursor_width": 1.5,
        "shift_enter": multiline,
        "ignore_up_down_keys": not multiline,  # 单行段让上下键冒泡到外层跨行
        "on_change": lambda e: on_change(e.control.value),
        "on_submit": lambda e: on_submit(e.control.value),
        "on_blur": lambda e: on_blur(),
    }
    # 仅在跨段导航时传 selection（强制光标落点）；
    # 输入时不传 selection，避免 Flet 重置光标到段尾
    if sel is not None:
        kwargs["selection"] = sel
    if on_selection_change is not None:
        kwargs["on_selection_change"] = on_selection_change

    if not multiline:
        # 文本像素宽 + 内边距(左右各4) + 光标/子像素余量；空文本给最小宽避免坍缩
        text_w = measure_text_width(draft or "", font_family, text_size)
        kwargs["width"] = max(text_w + 14, 24)

    return ft.TextField(**kwargs)
