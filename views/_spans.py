"""TextSpan 变换与视觉行切片（纯函数组）。

从 views/rendered_line.py 迁出：向外选区高亮、任务勾选淡化、前缀剥离、
搜索命中着色、raw→flat 映射、软换行切片等都在这一层完成，输入输出都是
`ft.TextSpan` 列表，与组件状态无关，故独立成模块。

对外接口见 `__all__`。
"""

from dataclasses import replace

import flet as ft

from models.document import BlockType, Line, SegType
from styles import _current_colors
from utils.segment_helpers import (
    PREFIX_SEGTYPES,
    display_text,
    split_seg_for_display,
)
from views.pixel_layout import VisualLine
from views.segment_view import raw_to_visible_spans


def spans_with_highlight(
    line: Line,
    base: int,
    cursor_off: int | None,
    heading_level: int,
    outward_range: tuple[int, int] | None,
    skip_prefix: bool = False,
    checked: bool = False,
) -> list[ft.TextSpan]:
    """构造渲染 spans：raw_to_visible_spans 基础上注入向外选区高亮。

    skip_prefix=True 时跳过前缀段（任务列表用 Checkbox 替代前缀）。
    checked=True 时（任务列表已勾选项）对所有 span 注入删除线 + muted 文字色，
    保留原 bgcolor（选区高亮底色不丢失）。GitHub/Typora/VS Code 通用约定。
    """
    if outward_range is None:
        spans = raw_to_visible_spans(line, base, cursor_off, heading_level,
                                     skip_seg0=skip_prefix)
    else:
        # 有选区高亮：逐段注入 highlight_bg
        spans = spans_with_selection(line, base, cursor_off, heading_level, outward_range,
                                      skip_prefix)
    if checked:
        spans = apply_checked_style(spans)
    return spans


def apply_checked_style(spans: list[ft.TextSpan]) -> list[ft.TextSpan]:
    """已勾选任务文字样式：追加删除线 + 半透明文字色，保留 bgcolor。

    Typora 风格：已勾选文字不直接覆盖为 muted，而是用半透明（0.55）保留原色，
    视觉上更柔和（避免粗体/链接等格式化文字完全失去色彩对比）。
    删除线颜色也用半透明 muted，比文字本身更淡，符合"已完成"的退后语义。
    """
    c = _current_colors()
    strike_color = ft.Colors.with_opacity(0.5, c.muted)
    result: list[ft.TextSpan] = []
    for sp in spans:
        s = sp.style
        # decoration 并集：原值 | LINE_THROUGH
        orig_decoration = s.decoration if s is not None and s.decoration else ft.TextDecoration.NONE
        new_decoration = orig_decoration | ft.TextDecoration.LINE_THROUGH
        # 半透明文字色：保留原色但降低饱和度（Typora 风格）
        orig_color = s.color if s is not None else None
        new_color = ft.Colors.with_opacity(0.55, orig_color) if orig_color else c.muted
        new_style = ft.TextStyle(
            size=s.size if s is not None else None,
            weight=s.weight if s is not None else None,
            color=new_color,
            italic=s.italic if s is not None else None,
            font_family=s.font_family if s is not None else None,
            decoration=new_decoration,
            decoration_color=strike_color,
            bgcolor=s.bgcolor if s is not None else None,  # 保留选区高亮底色
        )
        result.append(ft.TextSpan(text=sp.text, style=new_style))
    return result


def strip_prefix_spans(spans: list[ft.TextSpan], prefix_len: int) -> list[ft.TextSpan]:
    """从前缀 spans 中移除前缀长度的字符（任务列表用）。"""
    if prefix_len <= 0:
        return spans
    result: list[ft.TextSpan] = []
    remaining = prefix_len
    for sp in spans:
        if remaining <= 0:
            result.append(sp)
            continue
        if len(sp.text) <= remaining:
            remaining -= len(sp.text)
            # 跳过该 span
            continue
        # 部分截断
        new_sp = ft.TextSpan(text=sp.text[remaining:], style=sp.style)
        result.append(new_sp)
        remaining = 0
    return result


def spans_with_selection(
    line: Line,
    base: int,
    cursor_off: int | None,
    heading_level: int,
    outward_range: tuple[int, int],
    skip_prefix: bool = False,
) -> list[ft.TextSpan]:
    """带向外选区高亮的 spans 构造（字符级拆分）。

    复用 segment_view.segment_to_spans_partial 做字符级高亮拆分。
    """
    from views.segment_view import segment_to_span, segment_to_spans_partial

    hl_s, hl_e = outward_range
    spans: list[ft.TextSpan] = []
    raw_offset = 0
    for seg_idx, seg in enumerate(line.segments):
        seg_start = raw_offset
        seg_end = raw_offset + len(seg.raw)
        is_prefix = seg.seg_type in PREFIX_SEGTYPES

        if skip_prefix and is_prefix and seg_idx == 0:
            raw_offset = seg_end
            continue

        inter_start = max(seg_start, hl_s)
        inter_end = min(seg_end, hl_e)

        if inter_start >= inter_end:
            # 不在高亮范围
            # 段末尾也属于本段（与其他渲染路径一致用 <=，修复包裹段末尾标记折叠 Bug）；
            # 块级前缀段例外：段末尾即内容起点，光标落在边界上视为已离开前缀
            if cursor_off is not None and (
                seg_start <= cursor_off <= seg_end
                if not is_prefix
                else seg_start <= cursor_off < seg_end
            ):
                # 光标在段内：标记变灰
                spans.extend(gray_marker_spans(seg, base, heading_level))
            else:
                spans.append(segment_to_span(seg, seg_idx, None, base, heading_level))
        else:
            # 有交集：字符级拆分高亮
            spans.extend(segment_to_spans_partial(
                seg, seg_idx, None, base, heading_level,
                hl_start_local=inter_start - seg_start,
                hl_end_local=inter_end - seg_start,
            ))
        raw_offset = seg_end
    return spans


def gray_marker_spans(seg, base: int, heading_level: int) -> list[ft.TextSpan]:
    """光标在段内时的渲染：标记灰色、内容正常（复用 raw_to_visible_spans 逻辑）。

    简化处理：构造一个单段行调用 raw_to_visible_spans。
    """
    tmp = Line(block_type=BlockType.PARAGRAPH, raw=seg.raw, segments=[seg])
    return raw_to_visible_spans(tmp, base, cursor_raw_offset=len(seg.raw), heading_level=heading_level)


def build_raw_to_flat_map(
    line: Line,
    cursor_off: int | None = None,
    outward_range: tuple[int, int] | None = None,
    skip_prefix: bool = False,
) -> list[int]:
    """raw 偏移 → flat 文本位置映射。len = len(line.raw)+1。

    与 spans_with_highlight 的标记折叠逻辑完全一致（单一真源）：
    - 无选区：匹配 raw_to_visible_spans
      · 光标在段内：所有字符（含标记）可见 → flat = seg.raw 逐字符
      · 光标不在段内：标记折叠 → flat = display_text / content pieces
      · HEADING_PREFIX 例外：光标在本行时 # 前缀可见（灰色）
    - 有选区：匹配 spans_with_selection
      · HEADING_PREFIX 始终折叠（display_text="" ）
      · 有选区交集的段：标记折叠（segment_to_spans_partial 跳过标记）
      · 无交集 + 光标在段：gray_marker_spans → 全可见
      · 无交集 + 光标不在段：segment_to_span → display_text

    前缀段（#/•/>）：所有 raw 偏移映射到同一 flat_pos（不拆分，整段留 vline 0），
    flat_pos 前进 len(display_text)。
    """
    raw_to_flat = [0]
    flat_pos = 0
    raw_offset = 0
    seg_count = len(line.segments)
    has_selection = outward_range is not None
    hl_s, hl_e = outward_range if has_selection else (-1, -1)

    for seg_idx, seg in enumerate(line.segments):
        seg_start = raw_offset
        seg_raw_len = len(seg.raw)
        seg_end = seg_start + seg_raw_len

        if skip_prefix and seg_idx == 0 and seg.seg_type in PREFIX_SEGTYPES:
            for _ in range(seg_raw_len):
                raw_to_flat.append(flat_pos)
            raw_offset = seg_end
            continue

        is_last = seg_idx == seg_count - 1
        is_prefix = seg.seg_type in PREFIX_SEGTYPES
        if cursor_off is None:
            cursor_in_seg = False
        elif is_last:
            cursor_in_seg = seg_start <= cursor_off <= seg_end
        elif is_prefix:
            # 块级前缀段：段末尾即内容起点，光标落在边界上视为已离开前缀
            # （引用/标题前缀渲染零宽度，避免 caret 偏右一个前缀宽度）
            cursor_in_seg = seg_start <= cursor_off < seg_end
        else:
            # 非末段：段末尾也属于本段（与 pixel_layout / segment_view 一致用 <=）
            # 修复 Bug：光标在包裹段末尾时标记被折叠，flat 映射与可见标记不对齐
            cursor_in_seg = seg_start <= cursor_off <= seg_end

        # 选区交集判断
        if has_selection:
            inter_start = max(seg_start, hl_s)
            inter_end = min(seg_end, hl_e)
            has_overlap = inter_start < inter_end
        else:
            has_overlap = False

        if is_prefix:
            if (
                seg.seg_type == SegType.HEADING_PREFIX
                and cursor_off is not None
                and not has_selection
            ):
                # 无选区 + 光标在本行：# 前缀可见（逐字符，flat = seg.raw）
                for _ in range(seg_raw_len):
                    flat_pos += 1
                    raw_to_flat.append(flat_pos)
            else:
                # 浏览态/有选区：display_text（前缀段不拆分，整段映射到同一 flat_pos）
                # 末 raw 偏移映射到 flat_pos + len(display)（与 _line_raw_offsets_x
                # 的 offsets[prefix_len] = display_w 一致：前缀末尾 = 显示末尾）
                display = display_text(seg)
                display_len = len(display)
                for i in range(seg_raw_len):
                    if i == seg_raw_len - 1:
                        flat_pos += display_len
                    raw_to_flat.append(flat_pos)
        elif cursor_in_seg and not has_overlap:
            # 光标在段内 + 无选区交集：全字符可见（flat = seg.raw 逐字符）
            for _ in range(seg_raw_len):
                flat_pos += 1
                raw_to_flat.append(flat_pos)
        else:
            # 浏览态/选区交集：标记折叠，逐 piece 走（marker 不前进 flat，content 前进）
            pieces = split_seg_for_display(seg)
            for text, is_marker in pieces:
                if not text:
                    continue
                if is_marker:
                    for _ in range(len(text)):
                        raw_to_flat.append(flat_pos)
                else:
                    for _ in range(len(text)):
                        flat_pos += 1
                        raw_to_flat.append(flat_pos)

        raw_offset = seg_end

    # 围栏块兜底：segments 拼接 != line.raw（CODE/MATH 无围栏标记）
    if len(raw_to_flat) - 1 != len(line.raw):
        raw_to_flat = list(range(len(line.raw) + 1))
    return raw_to_flat


def decorate_search_hits(
    flat_spans: list[ft.TextSpan],
    raw_to_flat: list[int],
    hits: list[tuple[int, int, bool]],
    bg_normal: str,
    bg_active: str,
) -> list[ft.TextSpan]:
    """把文档内搜索命中的 raw 区间转成 flat 区间，逐 span 切分并注入 bgcolor。

    纯装饰层：只改写 TextSpan.style.bgcolor，不改文字内容与排版宽度，因此
    HarfBuzz 测量 / 换行 / 光标像素对齐完全不受影响。命中区间在 raw→flat 折叠
    中退化为零宽（如命中被折叠的 URL 子段）时跳过，不产生脏 span。
    """
    if not hits:
        return list(flat_spans)
    intervals: list[tuple[int, int, str]] = []
    n = len(raw_to_flat)
    for s, e, is_cur in hits:
        if 0 <= s < n and 0 <= e < n:
            fs, fe = raw_to_flat[s], raw_to_flat[e]
            if fe > fs:
                intervals.append((fs, fe, bg_active if is_cur else bg_normal))
    if not intervals:
        return list(flat_spans)
    intervals.sort(key=lambda t: t[0])

    def _copy_span(span: ft.TextSpan, text: str, bgcolor: str | None) -> ft.TextSpan:
        style = span.style
        if bgcolor is not None:
            style = replace(style, bgcolor=bgcolor)
        kwargs: dict = {"text": text, "style": style}
        on_click = getattr(span, "on_click", None)
        if on_click is not None:
            kwargs["on_click"] = on_click
        tooltip = getattr(span, "tooltip", None)
        if tooltip is not None:
            kwargs["tooltip"] = tooltip
        return ft.TextSpan(**kwargs)

    result: list[ft.TextSpan] = []
    pos = 0
    for span in flat_spans:
        text = span.text or ""
        start, end = pos, pos + len(text)
        pos = end
        if end <= start:
            result.append(span)
            continue
        # 本 span 与命中区间的相交段（单调递增，hits 已按行内升序）
        segs: list[tuple[int, int, str]] = []
        for fs, fe, color in intervals:
            a, b = max(start, fs), min(end, fe)
            if a < b:
                segs.append((a, b, color))
        if not segs:
            result.append(span)
            continue
        prev = start
        for a, b, color in segs:
            if a > prev:
                result.append(_copy_span(span, text[prev - start:a - start], None))
            result.append(_copy_span(span, text[a - start:b - start], color))
            prev = b
        if prev < end:
            result.append(_copy_span(span, text[prev - start:end - start], None))
    return result


def slice_spans_for_visual_line(
    flat_spans: list[ft.TextSpan],
    raw_to_flat: list[int],
    vline: VisualLine,
    fallback_style: ft.TextStyle,
) -> list[ft.TextSpan]:
    """按视觉行 raw 范围切 flat spans（跨边界 span 拆分，保留 style/on_click/tooltip）。

    flat_spans 的文本拼接 = flat text；raw_to_flat[vline.start_raw/end_raw] 给出
    该视觉行在 flat text 中的 [start, end) 范围。遍历 spans，切出范围内的文本。
    """
    flat_start = raw_to_flat[vline.start_raw] if vline.start_raw < len(raw_to_flat) else 0
    flat_end = raw_to_flat[vline.end_raw] if vline.end_raw < len(raw_to_flat) else flat_start

    if flat_start >= flat_end:
        # 空范围（如纯标记行）：返回单个空格 span 保持行高
        return [ft.TextSpan(" ", style=fallback_style)]

    result: list[ft.TextSpan] = []
    current_pos = 0
    for span in flat_spans:
        span_text = span.text or ""
        span_len = len(span_text)
        span_start = current_pos
        span_end = current_pos + span_len

        if span_end <= flat_start or span_start >= flat_end:
            current_pos = span_end
            continue

        # 裁切到 [flat_start, flat_end) 范围
        local_start = max(0, flat_start - span_start)
        local_end = min(span_len, flat_end - span_start)
        sliced_text = span_text[local_start:local_end]

        if sliced_text:
            kwargs = {"text": sliced_text, "style": span.style}
            # 保留 on_click / tooltip（Flet TextSpan 属性）
            on_click = getattr(span, "on_click", None)
            if on_click is not None:
                kwargs["on_click"] = on_click
            tooltip = getattr(span, "tooltip", None)
            if tooltip is not None:
                kwargs["tooltip"] = tooltip
            result.append(ft.TextSpan(**kwargs))

        current_pos = span_end

    if not result:
        return [ft.TextSpan(" ", style=fallback_style)]
    return result


def maybe_stack_multi(
    flat_spans: list[ft.TextSpan],
    raw_to_flat: list[int],
    visual_lines: list[VisualLine],
    cursor_overlay: ft.Control | None,
    base: int,
    line_height: float,
    wrap_width: float,
    style: ft.TextStyle,
) -> ft.Control:
    """渲染 N 个视觉行（Stack 内逐行 Text）+ 可选光标 overlay。

    每个视觉行渲染为单独的 ft.Text（no_wrap=True, top=i*text_h），保证换行点
    与光标测量完全一致（共用 _line_visual_layout）。Stack 高度 = N * text_h。
    cursor_overlay 由调用方定位（Phase 4 传 cursor_px_y）。

    宽度策略（占满整行）：
    - 外层 Container width=inf：在可滚动 Column 中，只有 Container 的 width=inf
      才能撑满父容器全宽（Stack/Text 的 width=inf 无效）。与代码块/公式块一致，
      当前行高亮背景、选区高亮铺满整行。
    - 内层每个视觉行 Text 宽度 = wrap_width：文本在此宽度内换行，左对齐。
    - Stack 无 width 约束：由父 Container 决定宽度，Stack 撑满 Container。

    wrap_width=inf（不换行）时退化为单行 Text（行为与旧 _maybe_stack 一致）。
    """
    text_h = base * line_height
    num_vlines = len(visual_lines)
    stack_h = num_vlines * text_h
    is_inf = wrap_width == float("inf")

    # 不换行（单视觉行）：退化为简单 Text，避免 Stack 开销
    if num_vlines <= 1 and cursor_overlay is None:
        text = flat_spans[0] if len(flat_spans) == 1 else None
        if text is not None and text.text == " ":
            # 空行快捷路径
            return ft.Container(
                content=ft.Text(spans=flat_spans, style=style, height=text_h),
                width=float("inf"),
                height=text_h,
            )
        return ft.Container(
            content=ft.Text(spans=flat_spans, style=style, height=text_h),
            width=float("inf"),
            height=text_h,
        )

    # 多视觉行或激活行：视觉行 Text 放入内层 Stack（top 定位），光标 overlay 作为
    # 外层 Stack 的独立子项（index 稳定）。
    # 关键：换行触发时（1→2 视觉行）内层 Stack 新增 Text 不影响 overlay 在外层
    # Stack 的 index —— diff 不产生 move 操作，overlay 元素不移动/不重挂载，焦点
    # 与 IME 组合态不受干扰（否则元素移动触发 Flutter 重挂载，Windows IME 会提交
    # 并选中正在拼写的文本，继续输入会覆盖选区）。
    text_controls: list[ft.Control] = []
    for vline in visual_lines:
        vline_spans = slice_spans_for_visual_line(flat_spans, raw_to_flat, vline, style)
        # 每个视觉行 Text 宽度 = wrap_width：文本在此宽度内换行
        text_w = wrap_width if not is_inf else float("inf")
        text_controls.append(ft.Text(
            spans=vline_spans,
            style=style,
            width=text_w,
            height=text_h,
            no_wrap=True,
            top=vline.vline_idx * text_h,
            left=0,
        ))

    inner = ft.Stack(
        controls=text_controls,
        width=float("inf"),  # 撑满外层（外层 Stack 尺寸由本子项决定，与旧版一致）
        height=stack_h,
        clip_behavior=ft.ClipBehavior.NONE,
    )
    outer_controls: list[ft.Control] = [inner]
    if cursor_overlay is not None:
        outer_controls.append(cursor_overlay)

    return ft.Container(
        content=ft.Stack(
            controls=outer_controls,
            height=stack_h,
            clip_behavior=ft.ClipBehavior.NONE,  # 不裁切光标层（IME 候选框）
        ),
        width=float("inf"),  # 可滚动 Column 中只有 Container width=inf 撑满全宽
        height=stack_h,
    )
