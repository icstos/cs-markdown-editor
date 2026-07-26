"""段级渲染：把 Segment 转为可点击的 TextSpan（渲染态）。

设计原则：
- 渲染态用 TextSpan 参与 Text 的整体排版（自动换行，符合阅读习惯）。
- 段显示拆分（标记 vs 内容）与段类型常量已抽到 utils/segment_helpers，
  本模块仅保留与样式/主题强相关的渲染逻辑（segment_to_span /
  segment_to_spans_partial / raw_to_visible_spans），避免与 utils 层重复。

依赖项：
- models：BlockType / Line / SegType / Segment
- styles：_current_colors / block_weight / list_color_level / prefix_style / segment_style
- utils.segment_helpers：PREFIX_SEGTYPES / display_text / split_seg_for_display
  （段类型常量与显示拆分）

对外接口：
- segment_to_span(...)：单段 → TextSpan（含点击激活）
- segment_to_spans_partial(...)：段内字符级高亮拆分（向外选区）
- raw_to_visible_spans(...)：整行 → Typora 式可见 TextSpan 列表
- selection_highlight_bg()：向外选区高亮背景色
"""

from collections.abc import Callable

import flet as ft

from models import BlockType, Line, SegType, Segment
from styles import (
    _current_colors,
    block_weight,
    list_color_level,
    prefix_style,
    segment_style,
)
from utils.segment_helpers import (
    PREFIX_SEGTYPES,
    display_text,
    split_seg_for_display,
)


def _open_link_url(url: str) -> None:
    """在系统浏览器中打开链接，不进入段级编辑。"""
    target = (url or "").strip()
    if not target:
        return
    page = ft.context.page
    if page is None:
        return

    async def _launch():
        await page.launch_url(target, web_popup_window_name=ft.UrlTarget.BLANK)

    page.run_task(_launch)


def segment_to_span(
    seg: Segment,
    seg_idx: int,
    on_activate: Callable[[int], None] | None,
    base_size: int,
    heading_level: int = 0,
    highlight_bg: str | None = None,
) -> ft.TextSpan:
    """渲染态：段 -> TextSpan（可点击激活）。on_activate=None 时不绑定 on_click。

    heading_level > 0 时覆盖文字颜色为标题级别色（红橙绿青蓝紫）。
    无序列表前缀圆点按缩进级别复用同一套色阶。
    highlight_bg 非 None 时注入 bgcolor（用于向外选区高亮），覆盖段自身 bgcolor。
    """
    c = _current_colors()  # 当前主题颜色（亮/暗）
    style = (
        prefix_style(seg, base_size)
        if seg.seg_type in PREFIX_SEGTYPES
        else segment_style(seg, base_size)
    )
    if seg.seg_type == SegType.LIST_PREFIX:
        raw = seg.raw.lstrip()
        if raw and raw[0] in "-*+":
            lvl = list_color_level(seg.level)
            style = ft.TextStyle(
                size=style.size,
                weight=style.weight,
                color=c.heading_colors.get(lvl, c.muted),
                italic=style.italic,
                font_family=style.font_family,
                decoration=style.decoration,
                bgcolor=highlight_bg if highlight_bg is not None else style.bgcolor,
            )
    elif highlight_bg is not None:
        # 非 LIST_PREFIX 分支：若需高亮，覆盖 bgcolor
        style = ft.TextStyle(
            size=style.size,
            weight=style.weight,
            color=style.color,
            italic=style.italic,
            font_family=style.font_family,
            decoration=style.decoration,
            bgcolor=highlight_bg,
        )
    if heading_level > 0:
        is_strong = seg.seg_type == SegType.STRONG or SegType.STRONG in (seg.marks or ())
        weight = (
            ft.FontWeight.BOLD
            if is_strong
            else block_weight(BlockType.HEADING, heading_level)
        )
        style = ft.TextStyle(
            size=style.size,
            weight=weight,
            color=c.heading_colors.get(heading_level, c.text),
            italic=style.italic,
            font_family=style.font_family,
            decoration=style.decoration,
            bgcolor=highlight_bg if highlight_bg is not None else style.bgcolor,
        )
    kwargs: dict = {"text": display_text(seg), "style": style}
    if seg.seg_type == SegType.LINK and seg.url:
        kwargs["on_click"] = lambda e, u=seg.url: _open_link_url(u)
    elif on_activate is not None:
        kwargs["on_click"] = lambda e: on_activate(seg_idx)
    return ft.TextSpan(**kwargs)


def selection_highlight_bg() -> str:
    """向外选区高亮背景色（主题相关）。"""
    c = _current_colors()
    return ft.Colors.with_opacity(0.22, c.link)


def segment_to_spans_partial(
    seg: Segment,
    seg_idx: int,
    on_activate: Callable[[int], None] | None,
    base_size: int,
    heading_level: int,
    hl_start_local: int,
    hl_end_local: int,
) -> list[ft.TextSpan]:
    """渲染段为多个 TextSpan，在 hl_start_local/hl_end_local 处做字符级高亮拆分。

    用于向外选区部分覆盖某内容段时，只高亮选中部分而非整段。
    hl_start_local/hl_end_local 为段内 raw 偏移（0 <= start < end <= len(raw)）。
    前缀段（HEADING_PREFIX/LIST_PREFIX/QUOTE_PREFIX）不做拆分，整段高亮。
    """
    # 前缀段：不做字符级拆分，整段高亮
    if seg.seg_type in PREFIX_SEGTYPES:
        return [
            segment_to_span(
                seg, seg_idx, on_activate, base_size, heading_level,
                highlight_bg=selection_highlight_bg(),
            )
        ]

    c = _current_colors()
    hl_bg = selection_highlight_bg()

    # 基础样式（不含 highlight_bg）
    base_style = segment_style(seg, base_size)

    # 标题级别覆盖
    if heading_level > 0:
        is_strong = seg.seg_type == SegType.STRONG or SegType.STRONG in (seg.marks or ())
        weight = (
            ft.FontWeight.BOLD
            if is_strong
            else block_weight(BlockType.HEADING, heading_level)
        )
        base_style = ft.TextStyle(
            size=base_style.size,
            weight=weight,
            color=c.heading_colors.get(heading_level, c.text),
            italic=base_style.italic,
            font_family=base_style.font_family,
            decoration=base_style.decoration,
            bgcolor=base_style.bgcolor,
        )

    # 高亮样式 = 基础样式 + highlight bgcolor
    hl_style = ft.TextStyle(
        size=base_style.size,
        weight=base_style.weight,
        color=base_style.color,
        italic=base_style.italic,
        font_family=base_style.font_family,
        decoration=base_style.decoration,
        bgcolor=hl_bg,
    )

    # on_click 处理
    on_click = None
    if seg.seg_type == SegType.LINK and seg.url:
        on_click = lambda e, u=seg.url: _open_link_url(u)
    elif on_activate is not None:
        on_click = lambda e: on_activate(seg_idx)

    def _mk_span(text: str, style: ft.TextStyle, attach_click: bool) -> ft.TextSpan:
        if attach_click and on_click is not None:
            return ft.TextSpan(text=text, style=style, on_click=on_click)
        return ft.TextSpan(text=text, style=style)

    pieces = split_seg_for_display(seg)
    spans: list[ft.TextSpan] = []
    raw_offset = 0  # 段内 raw 偏移

    for text, is_marker in pieces:
        piece_start = raw_offset
        piece_end = raw_offset + len(text)

        if is_marker:
            # 标记在浏览态不显示，跳过（raw_offset 仍需推进以保持偏移对齐）
            raw_offset = piece_end
            continue

        # 内容：按高亮范围拆分
        inter_start = max(piece_start, hl_start_local)
        inter_end = min(piece_end, hl_end_local)

        if inter_start >= inter_end:
            # 不在高亮范围内
            spans.append(_mk_span(text, base_style, attach_click=True))
        else:
            # 部分或全部在高亮范围内：拆分为 before / highlight / after
            before_len = inter_start - piece_start
            in_len = inter_end - inter_start
            after_len = piece_end - inter_end

            if before_len > 0:
                spans.append(_mk_span(text[:before_len], base_style, attach_click=True))
            if in_len > 0:
                spans.append(_mk_span(
                    text[before_len:before_len + in_len], hl_style, attach_click=True,
                ))
            if after_len > 0:
                spans.append(_mk_span(text[before_len + in_len:], base_style, attach_click=True))

        raw_offset = piece_end

    # 兜底：无内容片（如 IMAGE 段被 split_seg_for_display 全标为 marker 时），
    # 回退为整段高亮 span
    if not spans:
        return [
            segment_to_span(
                seg, seg_idx, on_activate, base_size, heading_level,
                highlight_bg=hl_bg,
            )
        ]

    return spans


def raw_to_visible_spans(
    line: Line,
    base_size: int,
    cursor_raw_offset: int | None = None,
    heading_level: int = 0,
    skip_seg0: bool = False,
) -> list[ft.TextSpan]:
    """把一行的 segments 渲染为可见 TextSpan 列表（Typora 式 WYSIWYG）。

    - 光标不在段内：标记完全折叠（零宽度），仅显示 display_text 内容
      （标题 # 前缀消失、无序列表 - 渲染为 •、行内 ** ` 等标记不占位）
    - 光标在段内：逐 piece 渲染，标记变灰可见（Typora 式最小语法）
    - 内容部分按 segment_style 渲染（含标题级别覆盖、列表圆点色阶）
    - skip_seg0=True：跳过第一段（任务行用 Checkbox 替代前缀）

    cursor_raw_offset=None 表示无光标（浏览态），所有段标记折叠。
    """
    c = _current_colors()
    spans: list[ft.TextSpan] = []
    raw_offset = 0
    seg_count = len(line.segments)

    for seg_idx, seg in enumerate(line.segments):
        seg_start = raw_offset
        seg_end = raw_offset + len(seg.raw)

        # 任务行跳过前缀段（Checkbox 替代）
        if skip_seg0 and seg_idx == 0:
            raw_offset = seg_end
            continue
        is_last = seg_idx == seg_count - 1

        # 光标是否在本段范围内（末段含右端点，其余段左闭右开）
        if cursor_raw_offset is None:
            cursor_in_seg = False
        elif is_last:
            cursor_in_seg = seg_start <= cursor_raw_offset <= seg_end
        else:
            cursor_in_seg = seg_start <= cursor_raw_offset < seg_end

        is_prefix = seg.seg_type in PREFIX_SEGTYPES

        # 段基础样式
        if is_prefix:
            base_style = prefix_style(seg, base_size)
            # 无序列表圆点色阶
            if seg.seg_type == SegType.LIST_PREFIX:
                raw_ls = seg.raw.lstrip()
                if raw_ls and raw_ls[0] in "-*+":
                    lvl = list_color_level(seg.level)
                    base_style = ft.TextStyle(
                        size=base_style.size,
                        weight=base_style.weight,
                        color=c.heading_colors.get(lvl, c.muted),
                        italic=base_style.italic,
                        font_family=base_style.font_family,
                        decoration=base_style.decoration,
                        bgcolor=base_style.bgcolor,
                    )
        else:
            base_style = segment_style(seg, base_size)
            # 标题级别覆盖（颜色/字重）
            if heading_level > 0:
                is_strong = seg.seg_type == SegType.STRONG or SegType.STRONG in (seg.marks or ())
                weight = (
                    ft.FontWeight.BOLD
                    if is_strong
                    else block_weight(BlockType.HEADING, heading_level)
                )
                base_style = ft.TextStyle(
                    size=base_style.size,
                    weight=weight,
                    color=c.heading_colors.get(heading_level, c.text),
                    italic=base_style.italic,
                    font_family=base_style.font_family,
                    decoration=base_style.decoration,
                    bgcolor=base_style.bgcolor,
                )

        if cursor_in_seg:
            # 光标在段内：逐 piece 渲染，标记变灰可见（Typora 式最小语法）
            # 传入段内光标偏移，使 LINK/IMAGE 的 URL 仅在光标落于 URL 子段时可见
            cursor_local = cursor_raw_offset - seg_start
            pieces = split_seg_for_display(seg, cursor_local=cursor_local)
            for text, is_marker in pieces:
                if not text:
                    continue
                if is_marker:
                    style = ft.TextStyle(
                        size=base_style.size,
                        weight=base_style.weight,
                        color=c.muted,
                        italic=base_style.italic,
                        font_family=base_style.font_family,
                    )
                    spans.append(ft.TextSpan(text=text, style=style))
                else:
                    spans.append(ft.TextSpan(text=text, style=base_style))
        else:
            # 光标不在段内：标记折叠，仅显示 display_text（零宽度标记）
            # HEADING_PREFIX 例外：光标在本行任意位置时显示 # 前缀（Typora 式：
            # 编辑标题行时可见 # 号，用户可编辑标题级别）
            if (
                seg.seg_type == SegType.HEADING_PREFIX
                and cursor_raw_offset is not None
            ):
                pieces = split_seg_for_display(seg, cursor_local=None)
                for text, is_marker in pieces:
                    if not text:
                        continue
                    style = ft.TextStyle(
                        size=base_style.size,
                        weight=base_style.weight,
                        color=c.muted,
                        italic=base_style.italic,
                        font_family=base_style.font_family,
                    )
                    spans.append(ft.TextSpan(text=text, style=style))
            else:
                display = display_text(seg)
                if display:
                    spans.append(ft.TextSpan(text=display, style=base_style))
                # display 为空时不添加 span（引用 > 前缀完全消失）

        raw_offset = seg_end

    return spans
