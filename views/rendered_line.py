"""渲染层行组件：Typora 式 WYSIWYG 静态渲染 + 点击/拖拽命中（支持软换行）。

作为 Stack 双层架构的底层渲染层：
- 调用 raw_to_visible_spans 把行 segments 渲染为 TextSpan 列表（拼接 == line.raw）
- cursor_off=None：所有语法标记透明（非激活行）
- cursor_off=int：光标所在段的标记变灰可见（激活行，Typora 式最小语法）
- GestureDetector 统一处理点击/拖拽，命中测试返回行级 raw 偏移
- cursor_overlay 非 None 时（激活行），Text 包入 ft.Stack 叠加透明光标 TextField

软换行（2D 视觉行布局）：
- _line_visual_layout 把一行切成 N 个 VisualLine（与光标测量共用同一换行函数）
- _build_raw_to_flat_map 建立 raw 偏移 → flat 文本位置映射（与 span 构造逻辑一致）
- _slice_spans_for_visual_line 按视觉行 raw 范围切 flat spans（跨边界 span 拆分）
- _maybe_stack_multi 在 Stack 内渲染 N 个单行 Text（top=i*text_h, no_wrap=True）

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
from utils.segment_helpers import PREFIX_SEGTYPES, display_text, split_seg_for_display
from utils.text_layout import image_fit_size, measure_text_width, resolve_image_src
from views.pixel_layout import (
    VisualLine,
    _block_padding,
    _compute_wrap_width,
    _line_raw_offsets_x,
    _line_visual_layout,
    hit_test_line_x_raw,
)
from views.segment_view import (
    raw_to_visible_spans,
    selection_highlight_bg,
)


def _has_visible_text(line: Line) -> bool:
    """是否有可见内容（文本/前缀/行内格式骨架）。

    空链接 []()、空图片 ![]()、空加粗 ** 等骨架段虽 text 为空，渲染层仍产生
    可见内容（编辑态显示语法标记，浏览态显示 '链接'/'图片' 等占位符），不应被
    误判为空行而只渲染单空格占位。任何非 TEXT 段都是格式段或前缀段，其 raw 骨架
    非空，必有可见渲染。
    """
    for s in line.segments:
        if s.text or s.seg_type != SegType.TEXT:
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
    on_hit_test_x: Callable[[int, float], int] | None = None,
    on_hit_test_xy: Callable[[int, float, float], tuple[int, int] | None] | None = None,
    on_double_tap: Callable[[int, int], None] | None = None,
    # 图片右键菜单操作：(action, line_idx, seg_idx, url, alt)
    # action ∈ {"copy_md","copy_image","save_as","delete"}，由 editor 分发
    on_image_action: Callable[[str, int, int, str, str], None] | None = None,
    # 文档路径：用于解析相对路径图片（assets/xxx.png → 文档目录/assets/xxx.png）。
    # None 时相对路径保持原样（向后兼容，但本地图片可能无法显示）
    file_path: str | None = None,
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
    if line.block_type == BlockType.BLANK or not _has_visible_text(line):
        spans = [ft.TextSpan(" ", style=style)]
        ww, vlines = _get_vlayout()
        r2f = _build_raw_to_flat_map(line, cursor_off, outward_range)
        content = _maybe_stack_multi(spans, r2f, vlines, cursor_overlay,
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
            spans = _spans_with_highlight(line, base, cursor_off, heading_level,
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
        r2f = _build_raw_to_flat_map(line, cursor_off, outward_range, skip_prefix=True)
        text_area = _maybe_stack_multi(spans, r2f, vlines, cursor_overlay,
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
        # 容器高度锁定到 text_h 占满行高，Checkbox 居中对齐，避免行高跳变。
        # wrap=False 强制同一行（text_area 的 width=inf 由 expand 约束，
        # 文本软换行由 _maybe_stack_multi 内部多视觉行处理），
        # 避免 text_area 因 width=inf 被换到下一行导致框与文本分离。
        return ft.Row(
            controls=[
                ft.Container(
                    content=checkbox,
                    height=text_h,  # 容器占满行高，Checkbox 居中对齐
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
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            width=float("inf"),  # 可滚动 Column 中占满全宽
        )

    # ============ 图片行 ============
    # 浏览态：ft.Image 列表；左键进入图片 Markdown 编辑，右键弹出上下文菜单。
    # 激活态（cursor_overlay 非 None）跳过此分支，走普通文本渲染显示 ![alt](url) 源码。
    if (img_idxs := _image_seg_indices(line)) and cursor_overlay is None:
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
    spans = _spans_with_highlight(line, base, cursor_off, heading_level, outward_range)
    ww, vlines = _get_vlayout()
    r2f = _build_raw_to_flat_map(line, cursor_off, outward_range)
    content = _maybe_stack_multi(spans, r2f, vlines, cursor_overlay,
                                 base, line_height, ww, style)
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
        spans = _spans_with_selection(line, base, cursor_off, heading_level, outward_range,
                                      skip_prefix)
    if checked:
        spans = _apply_checked_style(spans)
    return spans


def _apply_checked_style(spans: list[ft.TextSpan]) -> list[ft.TextSpan]:
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


# ---------------------------------------------------------------------------
# 软换行：raw→flat 映射 + span 切片 + 多视觉行渲染
# ---------------------------------------------------------------------------

def _build_raw_to_flat_map(
    line: Line,
    cursor_off: int | None = None,
    outward_range: tuple[int, int] | None = None,
    skip_prefix: bool = False,
) -> list[int]:
    """raw 偏移 → flat 文本位置映射。len = len(line.raw)+1。

    与 _spans_with_highlight 的标记折叠逻辑完全一致（单一真源）：
    - 无选区：匹配 raw_to_visible_spans
      · 光标在段内：所有字符（含标记）可见 → flat = seg.raw 逐字符
      · 光标不在段内：标记折叠 → flat = display_text / content pieces
      · HEADING_PREFIX 例外：光标在本行时 # 前缀可见（灰色）
    - 有选区：匹配 _spans_with_selection
      · HEADING_PREFIX 始终折叠（display_text="" ）
      · 有选区交集的段：标记折叠（segment_to_spans_partial 跳过标记）
      · 无交集 + 光标在段：_gray_marker_spans → 全可见
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
        if cursor_off is None:
            cursor_in_seg = False
        elif is_last:
            cursor_in_seg = seg_start <= cursor_off <= seg_end
        else:
            cursor_in_seg = seg_start <= cursor_off < seg_end

        is_prefix = seg.seg_type in PREFIX_SEGTYPES

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


def _slice_spans_for_visual_line(
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


def _maybe_stack_multi(
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

    # 多视觉行或激活行：Stack 内逐行 Text
    controls: list[ft.Control] = []
    for vline in visual_lines:
        vline_spans = _slice_spans_for_visual_line(flat_spans, raw_to_flat, vline, style)
        # 每个视觉行 Text 宽度 = wrap_width：文本在此宽度内换行
        text_w = wrap_width if not is_inf else float("inf")
        controls.append(ft.Text(
            spans=vline_spans,
            style=style,
            width=text_w,
            height=text_h,
            no_wrap=True,
            top=vline.vline_idx * text_h,
            left=0,
        ))

    if cursor_overlay is not None:
        controls.append(cursor_overlay)

    return ft.Container(
        content=ft.Stack(
            controls=controls,
            height=stack_h,
            clip_behavior=ft.ClipBehavior.NONE,  # 不裁切光标层（IME 候选框）
        ),
        width=float("inf"),  # 可滚动 Column 中只有 Container width=inf 撑满全宽
        height=stack_h,
    )
