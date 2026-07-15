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
    HEADING_COLORS,
    measure_text_width,
    prefix_style,
    segment_style,
)

_PREFIX_SEGTYPES = (SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX)
_MONO_SEGTYPES = (SegType.CODESPAN, SegType.CODE, SegType.INLINE_MATH, SegType.MATH)


def _display_text(seg: Segment) -> str:
    """渲染态展示文本。"""
    if seg.seg_type == SegType.HEADING_PREFIX:
        return ""  # 渲染态不显示 # 前缀，用颜色区分标题级别
    if seg.seg_type == SegType.QUOTE_PREFIX:
        return ""  # 渲染态不显示 > 前缀，引用由左边框区分
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
    on_activate: Callable[[int], None] | None,
    base_size: int,
    heading_level: int = 0,
) -> ft.TextSpan:
    """渲染态：段 -> TextSpan（可点击激活）。on_activate=None 时不绑定 on_click。

    heading_level > 0 时覆盖文字颜色为标题级别色（红橙绿青蓝紫）。
    """
    style = (
        prefix_style(seg, base_size)
        if seg.seg_type in _PREFIX_SEGTYPES
        else segment_style(seg, base_size)
    )
    if heading_level > 0:
        style = ft.TextStyle(
            size=style.size,
            weight=style.weight,
            color=HEADING_COLORS.get(heading_level, C_TEXT),
            italic=style.italic,
            font_family=style.font_family,
            decoration=style.decoration,
            bgcolor=style.bgcolor,
        )
    kwargs: dict = {"text": _display_text(seg), "style": style}
    if on_activate is not None:
        kwargs["on_click"] = lambda e: on_activate(seg_idx)
    return ft.TextSpan(**kwargs)


def active_text_field(
    seg: Segment,
    draft: str,
    on_change: Callable[[str], None],
    on_submit: Callable[[str, str | None], None],
    on_blur: Callable[[], None],
    base_size: int,
    multiline: bool = False,
    on_selection_change: Callable | None = None,
    initial_cursor: int = -1,
    nav_seq: int = 0,
    field_ref: ft.Ref | None = None,
) -> ft.TextField:
    """编辑态：段 -> 内嵌无框 TextField，显示该段原生 Markdown。

    单行段：依据本地字体测量文本宽度，让 TextField 恰好包裹文本内容
    （Typora 式最小编辑块），避免撑满整行破坏阅读节奏。
    多行代码块：保持块级宽度，由父容器决定。

    光标导航：
    - on_selection_change：上报光标位置变化（供外层跟踪 extent/base）
    - initial_cursor + on_focus：跨段时通过 nav_seq 变化触发 key 重建，
      autofocus 聚焦后 on_focus 强制把光标设到 initial_cursor（段首/段尾）。
      cursor_applied 标志确保仅应用一次，后续聚焦不覆盖用户光标位置。
    - ignore_up_down_keys：单行段置 True，让上下键冒泡到外层做跨行；
      多行代码块保持 False，让上下键在块内移动光标。
    """
    is_mono = seg.seg_type in _MONO_SEGTYPES
    font_family = FONT_MONO if is_mono else FONT_MAIN
    text_size = base_size if not is_mono else max(base_size - 1, 12)

    # autofocus 在 SelectionArea 内点击 span 时不可靠（手势竞争导致不触发 focus）。
    # 用 on_focus 在聚焦后强制设置光标位置，仅应用一次：
    #   initial_cursor >= 0 → 指定位置（跨段导航）
    #   initial_cursor < 0  → 段尾（直接点击 span 激活）
    # 声明式模式下控件被冻结（_frozen），需临时解冻才能命令式设置 selection 并 update。
    cursor_applied = [False]

    def _on_focus(e):
        if not cursor_applied[0]:
            cursor_applied[0] = True
            pos = initial_cursor if initial_cursor >= 0 else len(draft)
            ctrl = e.control
            frozen = getattr(ctrl, "_frozen", None)
            if frozen is not None:
                del ctrl._frozen
            try:
                ctrl.selection = ft.TextSelection(base_offset=pos, extent_offset=pos)
                ctrl.update()
            finally:
                if frozen is not None:
                    ctrl._frozen = frozen

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
        "on_focus": _on_focus,
        "on_change": lambda e: on_change(e.control.value),
        "on_submit": lambda e: on_submit(e.control.value),
        "on_blur": lambda e: on_blur(),
    }
    if on_selection_change is not None:
        kwargs["on_selection_change"] = on_selection_change

    if field_ref is not None:
        kwargs["ref"] = field_ref

    if not multiline:
        # 文本像素宽 + 内边距(左右各4) + 光标/子像素余量；空文本给最小宽避免坍缩
        text_w = measure_text_width(draft or "", font_family, text_size)
        kwargs["width"] = max(text_w + 14, 24)

    return ft.TextField(**kwargs)
