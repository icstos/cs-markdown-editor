"""渲染层行组件：Typora 式 WYSIWYG 静态渲染 + 点击/拖拽命中。

作为 Stack 双层架构的底层渲染层：
- 调用 raw_to_visible_spans 把行 segments 渲染为 TextSpan 列表（拼接 == line.raw）
- cursor_off=None：所有语法标记透明（非激活行）
- cursor_off=int：光标所在段的标记变灰可见（激活行，Typora 式最小语法）
- GestureDetector 统一处理点击/拖拽，命中测试返回行级 raw 偏移
- cursor_overlay 非 None 时（激活行），Text 包入 ft.Stack 叠加透明光标 TextField

本组件只负责"渲染 + 命中"，不做状态管理。所有状态由 editor.py 驱动。
不包 _wrap_block（缩进/引用边框由 line_view.py 外层包）。

特殊行：
- 空行：渲染单个空格 TextSpan，可承载光标
- 任务列表项：Checkbox + 内容 Text（光标 overlay 叠在内容 Text 上）
- 图片行：ft.Image 列表（不承载光标）

依赖项：
- models：BlockType / Line / SegType
- styles：FONT_MAIN / _current_colors / block_text_size / block_weight
- utils.segment_helpers：PREFIX_SEGTYPES（段类型常量）
- utils.text_layout：image_fit_size（图片尺寸测量）
- views.pixel_layout：_line_raw_offsets_x / hit_test_line_x_raw（行内 X 偏移与命中）
- views.segment_view：raw_to_visible_spans / selection_highlight_bg（段渲染）
"""

from collections.abc import Callable

import flet as ft

from models import BlockType, Line, SegType
from styles import (
    FONT_MAIN,
    FONT_MONO,
    Radius,
    Spacing,
    _current_colors,
    block_text_size,
    block_weight,
    list_color_level,
    prefix_style,
)
from utils.segment_helpers import PREFIX_SEGTYPES, display_text
from utils.text_layout import image_fit_size
from views.pixel_layout import _line_raw_offsets_x, hit_test_line_x_raw
from views.segment_view import (
    raw_to_visible_spans,
    selection_highlight_bg,
)


def _has_visible_text(line: Line) -> bool:
    """是否有可见文本或前缀段。"""
    for s in line.segments:
        if s.text or s.seg_type in PREFIX_SEGTYPES:
            return True
    return False


def _has_inline_math(line: Line) -> bool:
    """行内是否含 INLINE_MATH 段（需 LaTeX 渲染）。"""
    return any(s.seg_type == SegType.INLINE_MATH for s in line.segments)


def _image_seg_indices(line: Line) -> list[int]:
    """返回行内 IMAGE 段索引。

    若行内含 IMAGE 以外的非空文本段（混合行），返回空列表——此类行
    仍按普通文本渲染，避免图片与文字混排时布局错乱。
    """
    idxs: list[int] = []
    for i, s in enumerate(line.segments):
        if s.seg_type == SegType.IMAGE:
            idxs.append(i)
        elif s.seg_type == SegType.TEXT and not s.text.strip():
            continue
        else:
            return []
    return idxs


def _line_style(base: int, weight: ft.FontWeight, line_height: float) -> ft.TextStyle:
    """渲染层 Text 基础样式（与 cursor_text_field 的 strut 参数对齐）。"""
    c = _current_colors()
    return ft.TextStyle(
        size=base, weight=weight, color=c.text, font_family=FONT_MAIN, height=line_height
    )


def _line_raw_len(line: Line) -> int:
    """整行 raw 长度。"""
    return len(line.raw) if line.raw else sum(len(s.raw) for s in line.segments)


def _open_link_if_ctrl(e: ft.TapEvent, line: Line, raw_off: int,
                       ctrl_pressed_ref: ft.Ref | None) -> bool:
    """Ctrl+Click 链接段 → 系统浏览器打开。返回是否消费了事件。

    Typora 式交互：普通点击定位光标，Ctrl+Click 打开链接。
    """
    if ctrl_pressed_ref is None or not bool(ctrl_pressed_ref.current):
        return False
    # 定位 raw_off 落在哪个段
    acc = 0
    for seg in line.segments:
        n = len(seg.raw)
        if acc <= raw_off < acc + n or (acc + n == raw_off and seg is line.segments[-1]):
            if seg.seg_type == SegType.LINK and seg.url:
                from views.segment_view import _open_link_url
                _open_link_url(seg.url)
                return True
            return False
        acc += n
    return False


def RenderedLine(
    line: Line,
    line_idx: int,
    cursor_off: int | None = None,
    base_size: int | None = None,
    line_height: float = 1.6,
    content_width: float | None = None,
    cursor_overlay: ft.Control | None = None,
    # 点击 / 拖拽
    on_tap: Callable[[int, int], None] | None = None,
    on_pan_start: Callable[[int, int], None] | None = None,
    on_pan_update: Callable[[int, int], None] | None = None,
    on_toggle_task: Callable[[int], None] | None = None,
    # 向外选区
    outward_range: tuple[int, int] | None = None,
    on_extend_outward: Callable[[int, int], None] | None = None,
    on_clear_outward: Callable[[], None] | None = None,
    shift_pressed_ref: ft.Ref | None = None,
    ctrl_pressed_ref: ft.Ref | None = None,
    on_hit_test_x: Callable[[int, float], int] | None = None,
    on_hit_test_xy: Callable[[int, float, float], tuple[int, int] | None] | None = None,
    on_double_tap: Callable[[int, int], None] | None = None,
) -> ft.Control:
    """渲染层行组件（Stack 底层）。

    参数：
      cursor_off：None=非激活行（标记全透明）；int=激活行光标 raw 偏移（标记变灰）
      cursor_overlay：激活行的透明 cursor_text_field；非 None 时 Text 包入 Stack
      on_tap(li, raw_off)：点击命中回调
      on_pan_start/on_pan_update(li, raw_off)：拖拽选区回调
      outward_range：本行向外选区高亮 (start_off, end_off)
      on_hit_test_x(li, x)：跨行拖拽时用同一 x 列定位目标行偏移（按 base 等高估算）
      on_hit_test_xy(li, x, y)：跨行拖拽精确命中（LineLayoutCache.hit_test 透传），
        优先于 on_hit_test_x 使用，解决标题/普通/列表混合行高不一致的估算偏差
      on_double_tap(li, raw_off)：双击选词回调（VSCode 风格词边界）

    返回：内层 content（GestureDetector 包裹），由 line_view.py 外层包 _wrap_block。
    """
    c = _current_colors()
    base = base_size or block_text_size(line.block_type, line.level)
    weight = block_weight(line.block_type, line.level)
    style = _line_style(base, weight, line_height)
    heading_level = line.level if line.block_type == BlockType.HEADING else 0

    # 闭包共享标志：GestureDetector.on_tap 处理 Shift+Click 后置 True，
    # 供外层 Container.on_click 检测并跳过（避免覆盖选区）。每次渲染重建。
    _shift_tap_handled = [False]

    def _prefix_width_px() -> float:
        """任务行前缀像素宽度（LIST_PREFIX 段 0 的 raw 宽度）。

        任务行的 Checkbox 替代了前缀，text_ctrl 只渲染内容（skip_seg0=True），
        所以 GestureDetector.local_x 是相对内容起点，需加回前缀宽度才能用
        整行 offsets_x 做命中测试。

        强制 cursor_raw_offset=0 使前缀段按 raw 逐字符测量（而非折叠的
        display_text 宽度），因为 Checkbox 宽度需用 raw 前缀宽度近似。
        """
        if not line.task or not line.segments:
            return 0.0
        prefix_raw = line.segments[0].raw
        if not prefix_raw:
            return 0.0
        offsets = _line_raw_offsets_x(line, base, cursor_raw_offset=0)
        prefix_len = len(prefix_raw)
        if 0 < prefix_len < len(offsets):
            return offsets[prefix_len]
        return 0.0

    def _hit_raw_off(x: float) -> int:
        """x 相对文字左起点 → raw 偏移（中点吸附 + 折叠标记扫描）。

        任务行：Checkbox 替代了前缀，text_ctrl 只渲染内容（skip_seg0=True），
        local_x 相对内容起点。前缀段在 offsets_x 中已折叠为零宽度，
        scan_forward 自动跳过零宽度区域定位到内容起点。
        """
        if line.task:
            # 任务行：前缀已折叠（display_text=""），用浏览态 offsets
            # scan_forward 跳过前缀零宽度区域，直接定位内容偏移
            offsets = _line_raw_offsets_x(line, base, cursor_raw_offset=None)
        else:
            offsets = _line_raw_offsets_x(line, base, cursor_raw_offset=cursor_off)
        return hit_test_line_x_raw(offsets, x)

    def _pan_target_off(pos) -> tuple[int, int]:
        """根据 pan 坐标估算 (target_li, target_off)。跨行用 y 估算。

        优先调用 on_hit_test_xy（LineLayoutCache 精确命中：Y 二分 + 行内 X），
        解决标题/普通/列表/引用混合行高不一致时 round(y/base*lh) 估算偏差。
        无 on_hit_test_xy 时回退到原等高估算 + on_hit_test_x。
        """
        if pos is None:
            return (line_idx, 0)
        # 优先：精确命中（LineLayoutCache.hit_test 透传）
        if on_hit_test_xy is not None:
            result = on_hit_test_xy(line_idx, pos.x, pos.y)
            if result is not None:
                return result
        # 回退：按 base * line_height 等高估算行号
        _line_h = base * line_height
        line_dy = round(pos.y / _line_h) if _line_h > 0 else 0
        target_li = line_idx + line_dy
        if target_li == line_idx:
            return (line_idx, _hit_raw_off(pos.x))
        # 跨行：用同一 x 列命中目标行偏移
        if on_hit_test_x is not None:
            return (target_li, on_hit_test_x(target_li, pos.x))
        if line_dy < 0:
            return (target_li, 999999)
        return (target_li, 0)

    def _tap_raw_off(pos) -> int:
        """点击命中 raw_off：优先用 LineLayoutCache 精确命中（缓存 offsets），
        回退到 _hit_raw_off（重算 measure_text_offsets）。

        on_hit_test_xy 用缓存 offsets（cursor_raw_offset=None 浏览态），
        对非激活行完全准确；激活行标记可见但偏差仅标记宽度（1-3px），可接受。
        """
        if pos is None:
            return 0
        if on_hit_test_xy is not None:
            result = on_hit_test_xy(line_idx, pos.x, pos.y)
            if result is not None:
                return result[1] if isinstance(result, tuple) else result
        return _hit_raw_off(pos.x)

    def _on_double_tap_down(e: ft.TapEvent):
        """双击选词：命中 raw_off 后回调 on_double_tap(li, raw_off)。

        用 on_double_tap_down 而非 on_double_tap：后者用 ControlEventHandler
        不携带位置信息，前者用 TapEvent 带 local_position。
        VSCode 风格词边界由 editor.py 的 _select_word_at 实现（同类别连续区间）。
        Flet 双击会先触发 on_tap（定位光标）再触发 on_double_tap_down（选词），
        视觉上有短暂光标→选区闪烁，与 VSCode 行为一致。
        """
        if on_double_tap is None:
            return
        pos = e.local_position
        raw_off = _tap_raw_off(pos) if pos is not None else 0
        on_double_tap(line_idx, raw_off)

    def _on_tap(e: ft.TapEvent):
        pos = e.local_position
        if pos is None:
            if on_clear_outward is not None and outward_range is not None:
                on_clear_outward()
            if on_tap is not None:
                on_tap(line_idx, _line_raw_len(line))
            return
        # 优先使用 LineLayoutCache 精确命中（避免每次点击重算 measure_text_offsets）
        raw_off = _tap_raw_off(pos)
        # Ctrl+Click 链接 → 打开（Typora 式）
        if _open_link_if_ctrl(e, line, raw_off, ctrl_pressed_ref):
            return
        shift_held = shift_pressed_ref is not None and bool(shift_pressed_ref.current)
        if shift_held and on_extend_outward is not None:
            on_extend_outward(line_idx, raw_off)
            _shift_tap_handled[0] = True
            return
        # 既有向外选区 + 非 Shift 点击：先清除选区再定位光标
        if outward_range is not None and on_clear_outward is not None:
            on_clear_outward()
        if on_tap is not None:
            on_tap(line_idx, raw_off)

    def _on_pan_start(e: ft.DragStartEvent):
        if on_extend_outward is None:
            return
        # 拖拽起始：先清除已有选区，再以当前点为新起点（修复沿用上次起点 BUG）
        if on_clear_outward is not None:
            on_clear_outward()
        t_li, t_off = _pan_target_off(e.local_position)
        on_extend_outward(t_li, t_off)

    def _on_pan_update(e: ft.DragUpdateEvent):
        if on_extend_outward is None:
            return
        t_li, t_off = _pan_target_off(e.local_position)
        on_extend_outward(t_li, t_off)

    # ============ 空行 ============
    if line.block_type == BlockType.BLANK or not _has_visible_text(line):
        spans = [ft.TextSpan(" ", style=style)]
        text_ctrl = ft.Text(spans=spans, style=style, width=float("inf"))
        content = _maybe_stack(text_ctrl, cursor_overlay, base, line_height,
                               content_width, line, raw_off_for_cursor=cursor_off)
        return ft.GestureDetector(
            content=content, on_tap=_on_tap,
            on_pan_start=_on_pan_start, on_pan_update=_on_pan_update,
            on_double_tap_down=_on_double_tap_down,
        )

    # ============ 任务列表项 ============
    if line.task:
        # 内容段（跳过 LIST_PREFIX 段 0）：用 raw_to_visible_spans 渲染
        # 构造一个只含内容段的子行用于渲染（保持 raw 拼接一致）
        content_segs = line.segments[1:] if len(line.segments) > 1 else line.segments
        if content_segs:
            # 用整行渲染（raw_to_visible_spans 处理前缀段透明），但前缀段不显示
            # 任务列表的 LIST_PREFIX 已由 Checkbox 替代，渲染时跳过前缀段
            spans = _spans_with_highlight(line, base, cursor_off, heading_level,
                                          outward_range, skip_prefix=True)
        else:
            spans = [ft.TextSpan(" ", style=style)]
        text_ctrl = ft.Text(spans=spans, style=style)
        text_area = _maybe_stack(text_ctrl, cursor_overlay, base, line_height,
                                 content_width, line, raw_off_for_cursor=cursor_off)
        return ft.Row(
            controls=[
                ft.Checkbox(
                    value=line.checked,
                    on_change=lambda e: on_toggle_task(line_idx) if on_toggle_task else None,
                ),
                ft.GestureDetector(
                    content=text_area, on_tap=_on_tap,
                    on_pan_start=_on_pan_start, on_pan_update=_on_pan_update,
                    on_double_tap_down=_on_double_tap_down,
                ),
            ],
            wrap=True, spacing=Spacing.SM, run_spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    # ============ 图片行 ============
    if (img_idxs := _image_seg_indices(line)) and cursor_overlay is None:
        img_controls: list[ft.Control] = []
        for seg_idx in img_idxs:
            seg = line.segments[seg_idx]
            w, h = image_fit_size(seg.url)
            kw: dict = {
                "src": seg.url,
                "fit": ft.BoxFit.CONTAIN,
                "tooltip": seg.text,
                "error_content": ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.IMAGE_NOT_SUPPORTED_OUTLINED, color=c.muted, size=20),
                            ft.Text(value=seg.text or seg.url or "图片", color=c.muted,
                                    size=base - 1, font_family=FONT_MAIN),
                        ],
                        spacing=Spacing.LG, alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding.symmetric(horizontal=Spacing.XXL, vertical=Spacing.XL),
                    bgcolor=c.code_block_bg, border_radius=Radius.MD,
                    alignment=ft.Alignment.CENTER,
                ),
            }
            if w is not None:
                kw["width"] = w
            if h is not None:
                kw["height"] = h
            img_controls.append(ft.Container(content=ft.Image(**kw), ink=True))
        return ft.Column(
            controls=img_controls, spacing=Spacing.SM,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    # ============ 含行内公式的行（浏览态用 ft.Markdown 渲染 LaTeX）============
    # Typora 式：浏览态渲染真实数学符号，编辑态切换回 TextSpan 显示源码
    # 剥离前缀段（#/列表/引用），仅内容用 ft.Markdown，避免 ft.Markdown
    # 重复渲染列表标记/引用块级结构与 _wrap_block 冲突（列表标识异常 BUG 修复）
    if cursor_off is None and outward_range is None and _has_inline_math(line):
        prefix_seg = line.segments[0] if line.segments else None
        if prefix_seg and prefix_seg.seg_type in PREFIX_SEGTYPES:
            prefix_display = display_text(prefix_seg)
            content_raw = line.raw[len(prefix_seg.raw):] if prefix_seg.raw else line.raw
        else:
            prefix_seg = None
            prefix_display = ""
            content_raw = line.raw

        # 段落文字样式：标题行用标题字号/色阶，其余用 base
        if heading_level > 0:
            p_color = c.heading_colors.get(heading_level, c.text)
            p_weight = block_weight(BlockType.HEADING, heading_level)
            p_size = block_text_size(BlockType.HEADING, heading_level)
        else:
            p_color = c.text
            p_weight = ft.FontWeight.NORMAL
            p_size = base

        md = ft.Markdown(
            value=content_raw,
            selectable=False,
            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
            soft_line_break=True,
            latex_style=ft.TextStyle(size=p_size, color=c.math_fg),
            md_style_sheet=ft.MarkdownStyleSheet(
                p_text_style=ft.TextStyle(
                    size=p_size, color=p_color, weight=p_weight,
                    font_family=FONT_MAIN, height=line_height,
                ),
                # 行内元素样式须与 segment_style（TextSpan 渲染路径）保持一致，
                # 否则含行内公式的行经 ft.Markdown 渲染时这些元素会退化为默认样式。
                # 特别是 code_text_style：flet 在其为 None 时会把 code 重置为
                # bodyMedium+monospace（丢失 bgcolor/code_fg），导致行内代码
                # 在含公式行中显示异常（无背景、配色错乱）。
                code_text_style=ft.TextStyle(
                    size=p_size - 1,
                    color=c.code_fg,
                    bgcolor=c.code_bg,
                    font_family=FONT_MONO,
                ),
                strong_text_style=ft.TextStyle(
                    size=p_size, weight=ft.FontWeight.BOLD, color=p_color,
                ),
                em_text_style=ft.TextStyle(
                    size=p_size, italic=True, color=p_color,
                ),
                del_text_style=ft.TextStyle(
                    size=p_size, color=c.strike,
                    decoration=ft.TextDecoration.LINE_THROUGH,
                ),
                a_text_style=ft.TextStyle(
                    size=p_size, color=c.link,
                    decoration=ft.TextDecoration.UNDERLINE,
                ),
            ),
        )

        if prefix_display:
            # 列表前缀（• / 1. ）：Text + ft.Markdown 横排
            prefix_st = prefix_style(prefix_seg, base)
            if prefix_seg.seg_type == SegType.LIST_PREFIX:
                raw_ls = prefix_seg.raw.lstrip()
                if raw_ls and raw_ls[0] in "-*+":
                    lvl = list_color_level(prefix_seg.level)
                    prefix_st = ft.TextStyle(
                        size=base, color=c.heading_colors.get(lvl, c.muted),
                        weight=ft.FontWeight.BOLD,
                    )
            content = ft.Row(
                controls=[
                    ft.Text(
                        spans=[ft.TextSpan(text=prefix_display, style=prefix_st)],
                        style=ft.TextStyle(size=base, height=line_height),
                    ),
                    ft.Container(content=md, expand=True),
                ],
                spacing=0,
                wrap=False,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
        else:
            content = md

        return ft.GestureDetector(
            content=content, on_tap=_on_tap,
            on_pan_start=_on_pan_start, on_pan_update=_on_pan_update,
            on_double_tap_down=_on_double_tap_down,
        )

    # ============ 普通块（段落 / 标题 / 列表 / 引用）============
    spans = _spans_with_highlight(line, base, cursor_off, heading_level, outward_range)
    text_ctrl = ft.Text(spans=spans, style=style, width=float("inf"))
    content = _maybe_stack(text_ctrl, cursor_overlay, base, line_height,
                           content_width, line, raw_off_for_cursor=cursor_off)
    return ft.GestureDetector(
        content=content, on_tap=_on_tap,
        on_pan_start=_on_pan_start, on_pan_update=_on_pan_update,
        on_double_tap_down=_on_double_tap_down,
    )


def _spans_with_highlight(
    line: Line,
    base: int,
    cursor_off: int | None,
    heading_level: int,
    outward_range: tuple[int, int] | None,
    skip_prefix: bool = False,
) -> list[ft.TextSpan]:
    """构造渲染 spans：raw_to_visible_spans 基础上注入向外选区高亮。

    skip_prefix=True 时跳过前缀段（任务列表用 Checkbox 替代前缀）。
    """
    if outward_range is None:
        return raw_to_visible_spans(line, base, cursor_off, heading_level,
                                    skip_seg0=skip_prefix)
    # 有选区高亮：逐段注入 highlight_bg
    return _spans_with_selection(line, base, cursor_off, heading_level, outward_range,
                                 skip_prefix)


def _strip_prefix_spans(spans: list[ft.TextSpan], prefix_len: int) -> list[ft.TextSpan]:
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


def _spans_with_selection(
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

    hl_bg = selection_highlight_bg()
    hl_s, hl_e = outward_range
    spans: list[ft.TextSpan] = []
    raw_offset = 0
    seg_count = len(line.segments)
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
            if cursor_off is not None and seg_start <= cursor_off < seg_end:
                # 光标在段内：标记变灰
                spans.extend(_gray_marker_spans(seg, base, heading_level))
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


def _gray_marker_spans(seg, base: int, heading_level: int) -> list[ft.TextSpan]:
    """光标在段内时的渲染：标记灰色、内容正常（复用 raw_to_visible_spans 逻辑）。

    简化处理：构造一个单段行调用 raw_to_visible_spans。
    """
    tmp = Line(block_type=BlockType.PARAGRAPH, raw=seg.raw, segments=[seg])
    return raw_to_visible_spans(tmp, base, cursor_raw_offset=len(seg.raw), heading_level=heading_level)


def _maybe_stack(
    text_ctrl: ft.Text,
    cursor_overlay: ft.Control | None,
    base: int,
    line_height: float,
    content_width: float | None,
    line: Line,
    raw_off_for_cursor: int | None,
) -> ft.Control:
    """若 cursor_overlay 非 None，把 Text 包入 ft.Stack 叠加光标层。

    Stack 高度 = text_height = base * line_height（与 cursor_text_field 一致）。
    cursor_overlay 已由调用方设置 left/top（相对 Stack 左上角 = 文字左起点）。

    高度一致性：无论是否激活，Text 都设 height=text_h，确保光标移动时
    行高不变（非激活行 Text 高度 vs 激活行 Stack 高度一致），
    避免总高度波动导致滚动条长度抖动。
    """
    text_h = base * line_height
    text_ctrl.height = text_h  # 强制 Text 高度 = base * line_height
    if cursor_overlay is None:
        return text_ctrl
    # Stack 宽度：撑满可用区域（content_width 或自动）
    stack_w = content_width if content_width is not None else float("inf")
    return ft.Stack(
        controls=[text_ctrl, cursor_overlay],
        width=stack_w,
        height=text_h,
        clip_behavior=ft.ClipBehavior.NONE,  # 不裁切光标层（IME 候选框）
    )
