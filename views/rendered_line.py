"""渲染层行组件：Typora 式 WYSIWYG 静态渲染 + 点击/拖拽命中（支持软换行）。

作为 Stack 双层架构的底层渲染层：
- 调用 raw_to_visible_spans 把行 segments 渲染为 TextSpan 列表（拼接 == line.raw）
- cursor_off=None：所有语法标记透明（非激活行）
- cursor_off=int：光标所在段的标记变灰可见（激活行，Typora 式最小语法）
- GestureDetector 统一处理点击/拖拽，命中测试返回行级 raw 偏移
- cursor_overlay 非 None 时（激活行），Text 包入 ft.Stack 叠加透明光标 TextField

软换行（2D 视觉行布局）：
- _line_visual_layout 把一行切成 N 个 VisualLine（与光标测量共用同一换行函数）
- _spans.build_raw_to_flat_map 建立 raw 偏移 → flat 文本位置映射（与 span 构造逻辑一致）
- _spans.slice_spans_for_visual_line 按视觉行 raw 范围切 flat spans（跨边界 span 拆分）
- _spans.maybe_stack_multi 在 Stack 内渲染 N 个单行 Text（top=i*text_h, no_wrap=True）

本组件只负责"渲染 + 命中"，不做状态管理。所有状态由 editor.py 驱动。
不包 _wrap_block（缩进/引用边框由 line_view.py 外层包）。

特殊行：
- 空行：渲染单个空格 TextSpan，可承载光标
- 任务列表项：Checkbox + 内容 Text（光标 overlay 叠在内容 Text 上）
- 图片行：ft.Image 列表（浏览态）
  · 左键点击 → on_tap(line_idx, seg_raw_off) 进入图片 Markdown 编辑
    （激活行 cursor_overlay 非 None，跳过图片分支，渲染 ![alt](url) 源码 + 光标）
  · 右键 → ft.ContextMenu：拷贝 Markdown / 拷贝图片 / 另存为 / 删除

依赖项：
- models：BlockType / Line / SegType
- styles：FONT_MAIN / _current_colors / block_text_size / block_weight
- utils.segment_helpers：PREFIX_SEGTYPES / display_text / split_seg_for_display
- utils.text_layout：image_fit_size（图片尺寸测量）
- views.pixel_layout：_line_raw_offsets_x / hit_test_line_x_raw / _line_visual_layout /
  _compute_wrap_width / _block_padding / VisualLine
- views.segment_view：raw_to_visible_spans / selection_highlight_bg（段渲染）
"""

from collections.abc import Callable

import flet as ft

from models.document import BlockType, Line, SegType
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
from utils.text_layout import image_fit_size, measure_text_width, resolve_image_src
from views.pixel_layout import (
    VisualLine,
    _block_padding,
    _compute_wrap_width,
    _line_raw_offsets_x,
    _line_visual_layout,
    hit_test_line_x_raw,
)
from views import _line_helpers, _spans


def RenderedLine(
    line: Line,
    line_idx: int,
    cursor_off: int | None = None,
    base_size: int | None = None,
    line_height: float = 1.6,
    body_font_size: int = 16,
    content_width: float | None = None,
    cursor_overlay: ft.Control | None = None,
    # 预计算视觉行布局：(wrap_width, visual_lines)，由 LineView 共享传入避免重复计算
    precomputed_vlayout: tuple[float, list[VisualLine]] | None = None,
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
    alt_pressed_ref: ft.Ref | None = None,
    on_hit_test_x: Callable[[int, float], int] | None = None,
    on_hit_test_xy: Callable[[int, float, float], tuple[int, int] | None] | None = None,
    on_double_tap: Callable[[int, int], None] | None = None,
    # 图片右键菜单操作：(action, line_idx, seg_idx, url, alt)
    # action ∈ {"copy_md","copy_image","save_as","delete"}，由 editor 分发
    on_image_action: Callable[[str, int, int, str, str], None] | None = None,
    # 文档路径：用于解析相对路径图片（assets/xxx.png → 文档目录/assets/xxx.png）。
    # None 时相对路径保持原样（向后兼容，但本地图片可能无法显示）
    file_path: str | None = None,
    # 文档内搜索（浮层）：本行命中 [(s, e, is_current)]（raw 偏移）。仅做装饰
    # bgcolor（不改变文字/宽度/测量）；outward_range 有值时跳过避免覆盖选区高亮。
    search_hits: list[tuple[int, int, bool]] | None = None,
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
    base = base_size or block_text_size(line.block_type, line.level, body_font_size)
    weight = block_weight(line.block_type, line.level)
    style = _line_helpers._line_style(base, weight, line_height)
    heading_level = line.level if line.block_type == BlockType.HEADING else 0

    # 软换行视觉行布局（惰性计算，仅普通文本行/任务行/空行使用）
    _vlayout_cache: list = [None]  # [0] = (wrap_width, visual_lines) or None
    # 行内 offsets_x 缓存：_line_raw_offsets_x 结果（含 HarfBuzz 整形测量）。
    # vlayout 计算和 hit_test 共用同一份，避免激活行点击时重复测量。
    _offsets_cache: list[list[float] | None] = [None]

    def _get_offsets() -> list[float]:
        """惰性计算行内 offsets_x（含标记折叠/kerning/逐段字体），hit_test 复用。"""
        if _offsets_cache[0] is None:
            _offsets_cache[0] = _line_raw_offsets_x(line, base, cursor_raw_offset=cursor_off)
        return _offsets_cache[0]

    def _get_vlayout() -> tuple[float, list[VisualLine]]:
        """惰性计算 (wrap_width, visual_lines)，与光标测量共用同一换行函数。

        性能优化：优先使用 LineView 传入的 precomputed_vlayout（激活行已在外部
        计算一次，此处直接复用）；否则用 _get_offsets() 缓存的 offsets 传入
        _line_visual_layout，避免内部重复调用 _line_raw_offsets_x。
        """
        if _vlayout_cache[0] is None:
            if precomputed_vlayout is not None:
                _vlayout_cache[0] = precomputed_vlayout
            else:
                _, _, left_pad = _block_padding(line)
                cw = content_width if content_width is not None else float("inf")
                ww = _compute_wrap_width(cw, left_pad)
                offsets = _get_offsets()
                vlines = _line_visual_layout(
                    line, base, ww,
                    cursor_raw_offset=cursor_off,
                    line_height=line_height,
                    _precomputed_offsets=offsets,
                )
                _vlayout_cache[0] = (ww, vlines)
        return _vlayout_cache[0]

    # 闭包共享标志：GestureDetector.on_tap 处理 Shift+Click 后置 True，
    # 供外层 Container.on_click 检测并跳过（避免覆盖选区）。每次渲染重建。
    _shift_tap_handled = [False]

    def _prefix_width_px() -> float:
        """任务行前缀像素宽度（Checkbox 占位宽度）。

        任务行的 Checkbox 替代了前缀，text_ctrl 只渲染内容（skip_seg0=True），
        所以 GestureDetector.local_x 是相对内容起点，需加回前缀宽度才能用
        整行 offsets_x 做命中测试。

        Checkbox 用 VisualDensity.COMPACT + margin=0，视觉宽度约为 Material
        基准 24px。加 Spacing.SM（Row 水平间距）得到前缀总占位宽度。
        """
        if not line.task or not line.segments:
            return 0.0
        return 24.0 + Spacing.SM

    def _hit_raw_off(x: float) -> int:
        """x 相对文字左起点 → raw 偏移（中点吸附 + 折叠标记扫描）。

        任务行：Checkbox 替代了前缀，text_ctrl 只渲染内容（skip_seg0=True），
        local_x 相对内容起点。前缀段在 offsets_x 中已折叠为零宽度，
        scan_forward 自动跳过零宽度区域定位到内容起点。

        性能优化：非任务行复用 _get_offsets() 缓存（与 vlayout 共用同一份
        _line_raw_offsets_x 结果），避免每次点击/拖拽重新调用 HarfBuzz 整形测量。
        """
        if line.task:
            # 任务行：前缀已折叠（display_text=""），用浏览态 offsets
            # scan_forward 跳过前缀零宽度区域，直接定位内容偏移
            offsets = _line_raw_offsets_x(line, base, cursor_raw_offset=None)
        else:
            offsets = _get_offsets()
        return hit_test_line_x_raw(offsets, x)

    def _pan_target_off(pos) -> tuple[int, int]:
        """根据 pan 坐标估算 (target_li, target_off)。跨行用 y 估算。

        行内多视觉行（pos.y 在当前行视觉行范围内）：用内部 vlayout 按 Y 定 vline
        后 X 命中，与渲染层一致，避免外部 LineLayoutCache 的 content_width 一致性
        问题（cache num_vlines 错为 1 时第二视觉行命中到第一行）。与 _tap_raw_off
        共用同一份 _get_vlayout，换行点天然一致。

        跨行：优先 on_hit_test_xy（LineLayoutCache.hit_test：Y 二分定行 + 行内 X），
        解决标题/普通/列表/引用混合行高不一致时 round(y/base*lh) 估算偏差。
        无 on_hit_test_xy 时回退到原等高估算 + on_hit_test_x。
        """
        if pos is None:
            return (line_idx, 0)
        # 行内多视觉行：用内部 vlayout 精确命中（避免 cache 一致性问题）
        _, vlines = _get_vlayout()
        if len(vlines) > 1:
            text_h = base * line_height
            if text_h > 0 and 0 <= pos.y < len(vlines) * text_h:
                vline_idx = min(int(pos.y // text_h), len(vlines) - 1)
                vline = vlines[vline_idx]
                local_x = pos.x
                if line.task and vline.vline_idx == 0 and line.segments:
                    prefix_raw = line.segments[0].raw
                    prefix_len = len(prefix_raw) if prefix_raw else 0
                    if 0 < prefix_len < len(vline.offsets_x):
                        local_x += vline.offsets_x[prefix_len]
                local_off = hit_test_line_x_raw(vline.offsets_x, local_x)
                return (line_idx, vline.start_raw + local_off)
        # 跨行：优先精确命中（LineLayoutCache.hit_test 透传）
        if on_hit_test_xy is not None:
            result = on_hit_test_xy(line_idx, pos.x, pos.y)
            if result is not None:
                return (result[0], result[1])
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
        """点击命中 raw_off：多视觉行按 Y 定视觉行后 X 命中，单视觉行走 _hit_raw_off。

        多视觉行（word_wrap）时直接用 RenderedLine 内部 _get_vlayout() 的视觉行
        布局（与渲染层共用同一 _line_visual_layout，换行点天然一致）：
        - 按 pos.y // text_h 定位 vline_idx（GestureDetector 局部 Y 相对 Stack 顶）
        - 在该 vline.offsets_x 上做 X 命中（已 rebase 到 0 的单调数组，二分查找正确）
        - raw_off = vline.start_raw + local_off

        不依赖外部 LineLayoutCache：该 cache 为跨行拖拽设计，惰性构建且 content_width
        一致性受构建时机/闭包捕获影响——若构建时 content_width 未就绪（inf/0）或
        闭包未随 content_width 更新，cache 中 num_vlines 错为 1，hit_test 会把第二
        视觉行点击强制映射到第一行（vline_idx=0）。内部 vlayout 始终用当前
        content_width + cursor_off，与渲染完全一致，彻底绕过该问题。

        任务行 vline 0：Checkbox 替代前缀，pos.x 相对内容起点，需加回 vlayout 中
        前缀段折叠宽度（offsets_x[prefix_len]）对齐到整行 offsets；vline 1+ 起点已
        在内容区，无需加回。
        """
        if pos is None:
            return 0
        _, vlines = _get_vlayout()
        if len(vlines) > 1:
            text_h = base * line_height
            vline_idx = max(0, min(
                int(pos.y // text_h) if text_h > 0 else 0, len(vlines) - 1
            ))
            vline = vlines[vline_idx]
            local_x = pos.x
            # 任务行 vline 0：前缀段在 offsets_x 占折叠宽度，pos.x 相对内容起点需加回
            if line.task and vline.vline_idx == 0 and line.segments:
                prefix_raw = line.segments[0].raw
                prefix_len = len(prefix_raw) if prefix_raw else 0
                if 0 < prefix_len < len(vline.offsets_x):
                    local_x += vline.offsets_x[prefix_len]
            local_off = hit_test_line_x_raw(vline.offsets_x, local_x)
            return vline.start_raw + local_off
        # 单视觉行或未换行：整行 offsets_x 单调，二分查找正确
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
                on_tap(line_idx, _line_helpers._line_raw_len(line))
            return
        # 优先使用 LineLayoutCache 精确命中（避免每次点击重算 measure_text_offsets）
        raw_off = _tap_raw_off(pos)
        # Ctrl+Click 链接 → 打开（Typora 式）
        if _line_helpers._open_link_if_ctrl(e, line, raw_off, ctrl_pressed_ref):
            return
        # Alt+Click / Alt+Shift+Click → 多光标操作（优先于 Shift+Click 选区）
        # on_tap_line 内部检查 alt_pressed_ref + shift_pressed_ref 决定路由：
        # Alt → add_secondary_cursor，Alt+Shift → add_column_cursors
        alt_held = alt_pressed_ref is not None and bool(alt_pressed_ref.current)
        if alt_held:
            if on_tap is not None:
                on_tap(line_idx, raw_off)
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
        # pan_start 用专用 on_pan_start 回调：以命中点为 anchor（不沿用光标位置）。
        # 回退兼容：未提供 on_pan_start 时退用 on_extend_outward（保留旧行为）。
        cb = on_pan_start if on_pan_start is not None else on_extend_outward
        if cb is None:
            return
        # 拖拽起始：先清除已有选区，再以当前点为新起点
        if on_clear_outward is not None:
            on_clear_outward()
        t_li, t_off = _pan_target_off(e.local_position)
        cb(t_li, t_off)

    def _on_pan_update(e: ft.DragUpdateEvent):
        if on_extend_outward is None:
            return
        t_li, t_off = _pan_target_off(e.local_position)
        on_extend_outward(t_li, t_off)

    # ============ 空行 ============
    if line.block_type == BlockType.BLANK or not _line_helpers._has_visible_text(line):
        spans = [ft.TextSpan(" ", style=style)]
        ww, vlines = _get_vlayout()
        r2f = _spans.build_raw_to_flat_map(line, cursor_off, outward_range)
        content = _spans.maybe_stack_multi(spans, r2f, vlines, cursor_overlay,
                                     base, line_height, ww, style)
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
            # checked=True 时注入删除线 + muted 文字色（GitHub/Typora/VS Code 约定）
            spans = _spans.spans_with_highlight(line, base, cursor_off, heading_level,
                                          outward_range, skip_prefix=True,
                                          checked=line.checked)
        else:
            # 空任务：浏览态显示淡灰占位符（编辑态仍可输入，保留单空格 span）
            if cursor_off is None:
                spans = [ft.TextSpan(
                    "待办事项...",
                    style=ft.TextStyle(color=c.muted, italic=True, size=base),
                )]
            else:
                spans = [ft.TextSpan(" ", style=style)]
        ww, vlines = _get_vlayout()
        r2f = _spans.build_raw_to_flat_map(line, cursor_off, outward_range, skip_prefix=True)
        if search_hits and outward_range is None:
            spans = _spans.decorate_search_hits(
                spans, r2f, search_hits, c.search_match_bg, c.search_active_bg
            )
        text_area = _spans.maybe_stack_multi(spans, r2f, vlines, cursor_overlay,
                                       base, line_height, ww, style)
        # 主题感知 Checkbox：颜色随亮/暗主题、圆角 4px、focus overlay 透明
        # （消除 Material 默认焦点矩形——即用户记忆中的"左侧横线"）
        #
        # 尺寸优化：Checkbox 默认含 Material padding（约 40px 高），远大于文本行高
        # （base * line_height ≈ 25px），导致任务行明显高于普通行。通过 visual_density
        # 收紧 padding + margin 清零控制外框尺寸，Checkbox 保持 Material 基准 24px
        # 视觉尺寸（清晰可点，与 Typora 16-18px 视觉等效），由 Container 锁定高度
        # 占满行高实现垂直居中对齐。
        text_h = base * line_height
        checkbox = ft.Checkbox(
            value=line.checked,
            on_change=lambda e: on_toggle_task(line_idx) if on_toggle_task else None,
            active_color=c.link,
            check_color=ft.Colors.WHITE,
            fill_color={
                ft.ControlState.SELECTED: c.link,
                ft.ControlState.DEFAULT: c.surface,
            },
            overlay_color={
                ft.ControlState.HOVERED: ft.Colors.with_opacity(0.06, c.text),
                ft.ControlState.FOCUSED: ft.Colors.TRANSPARENT,
            },
            border_side=ft.BorderSide(1.5, c.muted if not line.checked else c.link),
            shape=ft.RoundedRectangleBorder(radius=Radius.SM),
            tristate=False,
            splash_radius=0,
            # 紧凑布局：收紧 padding + 清零 margin，不缩放（保持 Material 基准视觉尺寸）
            visual_density=ft.VisualDensity.COMPACT,  # 最小化 Material padding
            margin=ft.Margin(0, 0, 0, 0),
        )
        # 布局：Checkbox 容器 + GestureDetector(expand) 占据剩余空间。
        # 容器高度锁定到 text_h 占满单行高，Checkbox 在容器内居中对齐，避免行高跳变。
        # 垂直对齐用 START（顶对齐，Typora 式）：开启软换行、内容折为多视觉行时，
        # Row 高度 = N * text_h，若 CENTER 会把单行高的 Checkbox 容器上下居中到整块
        # 多行内容中部，复选框脱离第一行；START 让复选框始终停留在第一行，与首行
        # 文字对齐，符合桌面端任务列表直觉。
        # wrap=False 强制同一行（text_area 的 width=inf 由 expand 约束，
        # 文本软换行由 _spans.maybe_stack_multi 内部多视觉行处理），
        # 避免 text_area 因 width=inf 被换到下一行导致框与文本分离。
        return ft.Row(
            controls=[
                ft.Container(
                    content=checkbox,
                    height=text_h,  # 容器占满单行高，Checkbox 在首行内居中对齐
                    alignment=ft.Alignment.CENTER,
                    margin=ft.Margin(0, 0, 0, 0),
                    padding=0,
                ),
                ft.GestureDetector(
                    content=text_area, on_tap=_on_tap,
                    on_pan_start=_on_pan_start, on_pan_update=_on_pan_update,
                    on_double_tap_down=_on_double_tap_down,
                    expand=True,
                ),
            ],
            wrap=False, spacing=Spacing.SM, run_spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.START,  # 复选框固定在第一行（Typora 式）
            width=float("inf"),  # 可滚动 Column 中占满全宽
        )

    # ============ 图片行 ============
    # 浏览态：ft.Image 列表；左键进入图片 Markdown 编辑，右键弹出上下文菜单。
    # 激活态（cursor_overlay 非 None）跳过此分支，走普通文本渲染显示 ![alt](url) 源码。
    if (img_idxs := _line_helpers._image_seg_indices(line)) and cursor_overlay is None:
        img_controls: list[ft.Control] = []
        for seg_idx in img_idxs:
            seg = line.segments[seg_idx]
            # 相对路径基于文档目录解析为绝对路径（修复 ![](assets/xxx.png) 无法
            # 显示：PIL/ft.Image 默认按 cwd 解析相对路径，而 cwd 非文档目录）
            abs_src = resolve_image_src(seg.url, file_path)
            w, h = image_fit_size(abs_src)
            kw: dict = {
                "src": abs_src,
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
            img = ft.Image(**kw)

            # 图片段起始 raw 偏移：左键点击定位光标到此处，触发激活行渲染源码
            seg_raw_off = sum(len(s.raw) for s in line.segments[:seg_idx])

            def _on_img_tap(e: ft.TapEvent, off=seg_raw_off):
                # 清除既有向外选区，再定位光标到图片段（与普通行点击一致）
                if outward_range is not None and on_clear_outward is not None:
                    on_clear_outward()
                if on_tap is not None:
                    on_tap(line_idx, off)

            # 右键上下文菜单（Typora 式）：拷贝 Markdown / 拷贝图片 / 另存为 / 删除
            # url_text/alt_text/si 均通过默认参数绑定，避免闭包捕获循环变量末值
            # （多图片行时每个菜单项回调须用各自图片的 url/alt）
            alt_text = seg.text or ""
            url_text = seg.url or ""
            if on_image_action is not None:
                def _mi(label, icon, action, si=seg_idx, u=url_text, al=alt_text):
                    return ft.PopupMenuItem(
                        content=label, icon=icon,
                        on_click=lambda e, act=action, idx=si, url=u, alt=al:
                            on_image_action(act, line_idx, idx, url, alt),
                    )

                menu_items: list[ft.PopupMenuItem] = [
                    _mi("拷贝图片 Markdown", ft.Icons.CONTENT_COPY, "copy_md"),
                    _mi("拷贝图片", ft.Icons.IMAGE_OUTLINED, "copy_image"),
                    _mi("将图像另存为", ft.Icons.SAVE_OUTLINED, "save_as"),
                    ft.PopupMenuItem(),  # 分隔
                    _mi("删除图片", ft.Icons.DELETE_OUTLINE, "delete"),
                ]
                wrapped = ft.ContextMenu(
                    content=ft.GestureDetector(
                        content=ft.Container(content=img, ink=True),
                        on_tap=_on_img_tap,
                    ),
                    secondary_items=menu_items,
                )
            else:
                wrapped = ft.GestureDetector(
                    content=ft.Container(content=img, ink=True),
                    on_tap=_on_img_tap,
                )
            img_controls.append(wrapped)
        return ft.Column(
            controls=img_controls, spacing=Spacing.SM,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            width=float("inf"),  # 可滚动 Column 中占满全宽
        )

    # ============ 含行内公式的行（浏览态用 ft.Markdown 渲染 LaTeX）============
    # Typora 式：浏览态渲染真实数学符号，编辑态切换回 TextSpan 显示源码
    # 剥离前缀段（#/列表/引用），仅内容用 ft.Markdown，避免 ft.Markdown
    # 重复渲染列表标记/引用块级结构与 _wrap_block 冲突（列表标识异常 BUG 修复）
    if cursor_off is None and outward_range is None and _line_helpers._has_inline_math(line):
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
            p_size = block_text_size(BlockType.HEADING, heading_level, body_font_size)
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
            # 约束 Markdown 宽度到 wrap_width（减去前缀宽度），让长公式行原生换行
            _, _, left_pad = _block_padding(line)
            cw = content_width if content_width is not None else float("inf")
            full_ww = _compute_wrap_width(cw, left_pad)
            prefix_w = measure_text_width(prefix_display, FONT_MAIN, base) if prefix_display else 0.0
            md_w = full_ww - prefix_w if full_ww != float("inf") else float("inf")
            content = ft.Row(
                controls=[
                    ft.Text(
                        spans=[ft.TextSpan(text=prefix_display, style=prefix_st)],
                        style=ft.TextStyle(size=base, height=line_height),
                    ),
                    ft.Container(content=md, expand=True, width=md_w if md_w != float("inf") else None),
                ],
                spacing=0,
                wrap=False,
                vertical_alignment=ft.CrossAxisAlignment.START,
                width=float("inf"),  # 占满父容器全宽（与代码块/公式块一致）
            )
        else:
            # 约束 Markdown 宽度到 wrap_width，让长公式行原生换行
            _, _, left_pad = _block_padding(line)
            cw = content_width if content_width is not None else float("inf")
            full_ww = _compute_wrap_width(cw, left_pad)
            if full_ww != float("inf"):
                # 外层 Container 占满全宽（高亮背景铺满整行），内层约束 Markdown 到 wrap_width 换行
                content = ft.Container(
                    content=ft.Container(content=md, width=full_ww),
                    width=float("inf"),
                )
            else:
                content = md

        return ft.GestureDetector(
            content=content, on_tap=_on_tap,
            on_pan_start=_on_pan_start, on_pan_update=_on_pan_update,
            on_double_tap_down=_on_double_tap_down,
        )

    # ============ 普通块（段落 / 标题 / 列表 / 引用）============
    spans = _spans.spans_with_highlight(line, base, cursor_off, heading_level, outward_range)
    ww, vlines = _get_vlayout()
    r2f = _spans.build_raw_to_flat_map(line, cursor_off, outward_range)
    # 文档内搜索装饰：仅无向外选区冲突时做字符级 bgcolor（纯装饰不改排版/测量）
    if search_hits and outward_range is None:
        spans = _spans.decorate_search_hits(
            spans, r2f, search_hits, c.search_match_bg, c.search_active_bg
        )
    content = _spans.maybe_stack_multi(spans, r2f, vlines, cursor_overlay,
                                 base, line_height, ww, style)
    return ft.GestureDetector(
        content=content, on_tap=_on_tap,
        on_pan_start=_on_pan_start, on_pan_update=_on_pan_update,
        on_double_tap_down=_on_double_tap_down,
    )


# ---------------------------------------------------------------------------
# 软换行：raw→flat 映射 + span 切片 + 多视觉行渲染
# ---------------------------------------------------------------------------


