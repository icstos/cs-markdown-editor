"""行视图：把一行渲染为可读样式，并在该行处于编辑态时进行段级布局。

布局策略（兼顾排版与段级编辑）：
- 非编辑态：整行作为单个 ft.Text(spans=[...])，自动换行、排版美观。
- 编辑态（该行某段被激活）：拆为 [前段 Text] + [激活段 TextField] + [后段 Text]，
  仅激活段显示原生 Markdown，其余段保持渲染样式——Typora 式最小语法编辑。
特殊块（代码块 / 分隔线 / 空行）单独处理。
"""

from typing import Callable

import flet as ft

from models import BlockType, Line, Segment, SegType
from styles import (
    FONT_MAIN,
    FONT_MONO,
    _current_colors,
    block_text_size,
    block_weight,
    image_fit_size,
    measure_text_width,
    only_border,
)
from views.segment_view import (
    _MONO_SEGTYPES,
    _display_text,
    _open_link_url,
    active_text_field,
    segment_to_span,
)

_PREFIX_SEGTYPES = (SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX)


def _seg_font_size(seg: Segment, base: int) -> tuple[str, int]:
    """获取段的字体和字号（与 segment_view 的渲染样式一致）。"""
    is_mono = seg.seg_type in _MONO_SEGTYPES
    return (FONT_MONO if is_mono else FONT_MAIN,
            max(base - 1, 12) if is_mono else base)


def _display_to_raw_offset(seg: Segment, display_offset: int) -> int:
    """将展示文本偏移映射到原始 Markdown 偏移。

    对于 **bold** / `code` 等含语法的段，display text 是去除外壳后的内容，
    需要加上语法前缀长度才能得到 raw 中的偏移。
    """
    display = _display_text(seg)
    raw = seg.raw
    if not display or display_offset <= 0:
        return 0
    if display_offset >= len(display):
        return len(raw)
    if display in raw:
        prefix_len = raw.index(display)
        return min(prefix_len + display_offset, len(raw))
    return len(raw)


def _logical_raw_offset(line: Line, seg_idx: int, seg_offset: int) -> int:
    """段内 raw 偏移 → 整行 raw 逻辑偏移。"""
    return sum(len(line.segments[i].raw) for i in range(seg_idx)) + seg_offset


def _hit_test_tap(line: Line, x: float, y: float, base: int, line_height: float = 1.6) -> tuple[int, int]:
    """根据点击位置计算 (seg_idx, raw_cursor_offset)。

    通过累加各段展示文本宽度做命中测试，再用 measure_text_width
    逐字逼近找到字符偏移。多行文本（y 超过行高）回退到 (-1, -1)。
    """
    if y > base * line_height:
        return (-1, -1)
    acc = 0.0
    for i, seg in enumerate(line.segments):
        display = _display_text(seg)
        font, size = _seg_font_size(seg, base)
        w = measure_text_width(display, font, size)
        if x < acc + w or i == len(line.segments) - 1:
            local_x = x - acc
            # 逐字逼近：找到宽度 >= local_x 的最小前缀
            disp_off = len(display)
            for j in range(1, len(display) + 1):
                if measure_text_width(display[:j], font, size) >= local_x:
                    disp_off = j
                    break
            return (i, _display_to_raw_offset(seg, disp_off))
        acc += w
    return (-1, -1)


def _hit_test_code(code: str, x: float, y: float, base: int) -> int:
    """代码块点击命中测试：根据坐标估算字符偏移。

    几何基于 Flet Markdown(GITHUB_WEB) 代码块的近似渲染参数
    （Container padding + lang_tag + spacing + 代码块内边距 + 行高），
    可能存在小偏差，用户可用方向键微调。
    """
    # 估算代码区域起始位置（Container padding=12, lang_tag≈16, spacing=6, md 代码块内边距≈16）
    code_start_y = 12 + 16 + 6 + 16
    code_start_x = 12 + 16  # Container padding + md 代码块内边距
    line_h = max(base * 1.5, 20)  # 代码行高（字号 14 * 1.5）

    if not code:
        return 0
    if y < code_start_y:
        return 0
    if x < code_start_x:
        x = code_start_x

    lines = code.split("\n")
    row = int((y - code_start_y) // line_h)
    row = max(0, min(row, len(lines) - 1))

    # 用 measure_text_width 逐字逼近列号
    line_text = lines[row]
    local_x = x - code_start_x
    col = len(line_text)
    for j in range(1, len(line_text) + 1):
        if measure_text_width(line_text[:j], FONT_MONO, base) >= local_x:
            col = j
            break

    # 偏移 = 前面行的长度（含换行符）+ 当前列
    offset = sum(len(lines[i]) + 1 for i in range(row)) + col
    return min(max(offset, 0), len(code))


def _spans_for(
    line: Line,
    seg_from: int,
    seg_to_excl: int,
    on_activate: Callable[[int], None] | None,
    base_size: int,
) -> list[ft.TextSpan]:
    """构造 [seg_from, seg_to_excl) 范围的 TextSpan 列表。"""
    heading_level = line.level if line.block_type == BlockType.HEADING else 0
    return [
        segment_to_span(line.segments[i], i, on_activate, base_size, heading_level)
        for i in range(seg_from, seg_to_excl)
        if i < len(line.segments)
    ]


def _has_visible_text(line: Line) -> bool:
    """是否有可见文本或前缀段。"""
    for s in line.segments:
        if s.text or s.seg_type in _PREFIX_SEGTYPES:
            return True
    return False


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


def _active_field(
    line: Line,
    draft: str,
    on_change_draft: Callable[[str], None],
    on_submit: Callable[[str], None],
    on_blur: Callable[[], None],
    on_selection_change: Callable | None,
    initial_cursor: int,
    nav_seq: int,
    base_size: int | None = None,
    multiline: bool = False,
    field_ref: ft.Ref | None = None,
    max_width: float | None = None,
    line_height: float = 1.6,
    on_cursor_sync: Callable[[int, int], None] | None = None,
) -> ft.TextField:
    """构造激活态 TextField（统一入口，消除重复调用）。"""
    return active_text_field(
        line.segments[0],
        draft,
        on_change_draft,
        on_submit,
        on_blur,
        base_size=base_size if base_size is not None else block_text_size(line.block_type, line.level),
        multiline=multiline,
        on_selection_change=on_selection_change,
        initial_cursor=initial_cursor,
        nav_seq=nav_seq,
        field_ref=field_ref,
        max_width=max_width,
        line_height=line_height,
        on_cursor_sync=on_cursor_sync,
    )


@ft.component
def LineView(
    line: Line,
    line_idx: int,
    active_seg: int | None,
    draft: str,
    on_activate: Callable[[int, int, int], None],
    on_change_draft: Callable[[str], None],
    on_submit: Callable[[str], None],
    on_blur: Callable[[], None],
    on_selection_change: Callable | None = None,
    on_toggle_task: Callable[[int], None] | None = None,
    toc_entries: list[tuple[int, int, str]] | None = None,
    on_jump_to: Callable[[int], None] | None = None,
    on_change_lang: Callable[[str], None] | None = None,
    on_lang_focus: Callable[[], None] | None = None,
    on_suppress_blur: Callable[[], None] | None = None,
    initial_cursor: int = -1,
    nav_seq: int = 0,
    field_ref: ft.Ref | None = None,
    content_width: float | None = None,
    line_height: float = 1.6,
    on_cursor_sync: Callable[[int, int], None] | None = None,
):
    c = _current_colors()  # 当前主题颜色（亮/暗）
    base = block_text_size(line.block_type, line.level)
    weight = block_weight(line.block_type, line.level)
    line_style = ft.TextStyle(
        size=base, weight=weight, color=c.text, font_family=FONT_MAIN, height=line_height
    )

    # 编辑态 TextField 的最大可用宽度：内容区宽度 - 块级缩进 - 行内边距(8*2)。
    # 用于在单段文本过长时切换为多行换行编辑，避免横向溢出。
    if content_width is not None:
        indent = 0
        if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O):
            indent = line.level * 20
        elif line.block_type == BlockType.QUOTE:
            indent = (line.level or 1) * 12
        avail_width = max(content_width - indent - 16, 80)
    else:
        avail_width = None

    def activate(seg_idx: int, cursor_at: int = -1):
        on_activate(line_idx, seg_idx, cursor_at)

    # 行间空白死区点击兜底：激活最后一个段（与 _on_tap 回退策略一致）。
    # 内层 GestureDetector 会消费其覆盖区域的 tap，此回调仅在 padding 死区触发。
    def _fallback_activate(e):
        if line.block_type == BlockType.HEADING:
            activate(0)
        else:
            activate(max(0, len(line.segments) - 1))

    # ============ 空行 ============
    if line.block_type == BlockType.BLANK or not _has_visible_text(line):
        if active_seg is not None:
            field = _active_field(
                line, draft, on_change_draft, on_submit, on_blur,
                on_selection_change, initial_cursor, nav_seq, field_ref=field_ref,
                max_width=avail_width, line_height=line_height, on_cursor_sync=on_cursor_sync,
            )
            # 编辑态：设置 width=float("inf") 占满整行，点击右侧空白时抑制 blur 并激活最后段
            def _blank_edit_on_click(e):
                if on_suppress_blur:
                    on_suppress_blur()
                activate(max(0, len(line.segments) - 1), cursor_at=-1)

            content = ft.Container(
                content=field,
                padding=ft.Padding.symmetric(horizontal=2),
                width=float("inf"),
                on_click=_blank_edit_on_click,
                ink=True,
            )
        else:
            # Container.on_click 处理整行点击（含右侧空白），Text 占满宽度。
            # autofocus 由 editor.py 的 use_effect 显式调用 focus() 兜底。
            content = ft.Container(
                content=ft.Text(
                    spans=[
                        ft.TextSpan(" ", style=line_style)
                    ],
                    style=line_style,
                    width=float("inf"),
                ),
                height=max(base * line_height, 24),
                padding=ft.Padding.symmetric(horizontal=2),
                ink=True,
                on_click=lambda e: activate(0),
            )
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate)

    # ============ 分隔线 ============
    if line.block_type == BlockType.HR:
        if active_seg is not None:
            field = _active_field(
                line, draft, on_change_draft, on_submit, on_blur,
                on_selection_change, initial_cursor, nav_seq, field_ref=field_ref,
                max_width=avail_width, line_height=line_height, on_cursor_sync=on_cursor_sync,
            )
            content = ft.Container(padding=ft.Padding.symmetric(vertical=6), content=field)
        else:
            content = ft.Container(
                content=ft.Divider(height=1, thickness=1, color=c.quote_bar),
                padding=ft.Padding.symmetric(vertical=8),
                on_click=lambda e: on_activate(line_idx, 0),
                ink=True,
            )
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate)

    # ============ 代码块 ============
    if line.block_type == BlockType.CODE:
        if active_seg == 0:
            inner = _active_field(
                line, draft, on_change_draft, on_submit, on_blur,
                on_selection_change, initial_cursor, nav_seq, field_ref=field_ref,
                base_size=14, multiline=True, max_width=avail_width, line_height=line_height,
                on_cursor_sync=on_cursor_sync,
            )
            # 语言类型输入框：on_focus 设 suppress_blur 防止代码框 blur 退出
            lang_field = ft.TextField(
                value=line.lang,
                hint_text="lang",
                width=160,
                border=ft.InputBorder.NONE,
                text_size=12,
                text_style=ft.TextStyle(color=c.muted, font_family=FONT_MONO),
                content_padding=ft.Padding.symmetric(horizontal=4, vertical=0),
                on_focus=lambda e: on_lang_focus() if on_lang_focus else None,
                on_change=lambda e: on_change_lang(e.control.value) if on_change_lang else None,
            )
            content = ft.Container(
                content=ft.Column(
                    [
                        ft.Container(content=lang_field, padding=ft.Padding.only(left=4, top=2)),
                        inner,
                    ],
                    spacing=4,
                ),
                bgcolor=c.code_block_bg, border_radius=6, padding=12,
            )
        else:
            code = line.segments[0].text if line.segments else ""
            lang = line.lang or ""
            # 亮暗模式适配不同代码高亮主题：亮色用 GitHub 风格，暗色用 One Dark
            page = ft.context.page
            is_dark = page is not None and page.theme_mode == ft.ThemeMode.DARK
            code_theme = ft.MarkdownCodeTheme.ATOM_ONE_DARK if is_dark else ft.MarkdownCodeTheme.GITHUB
            # ft.Markdown + GITHUB_WEB + code_theme 实现代码高亮
            # selectable 留 False：外层 SelectionArea 已提供选择能力，
            # 且 Markdown 自带 selectable 会消费 tap 导致 GestureDetector 失效
            md = ft.Markdown(
                value=f"```{lang}\n{code}\n```",
                extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                code_theme=code_theme,
            )
            lang_tag = (
                ft.Text(value=lang, size=11, color=c.muted, font_family=FONT_MONO)
                if lang else ft.Text(" ")
            )

            def _on_tap(e: ft.TapEvent):
                pos = e.local_position
                if pos is not None:
                    offset = _hit_test_code(code, pos.x, pos.y, 14)
                    activate(0, offset)
                    return
                activate(0)

            content = ft.GestureDetector(
                content=ft.Container(
                    content=ft.Column([lang_tag, md], spacing=6),
                    bgcolor=c.code_block_bg, border_radius=6, padding=12,
                    ink=True,
                ),
                on_tap=_on_tap,
            )
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate)

    # ============ 块级公式 ============
    if line.block_type == BlockType.MATH:
        if active_seg == 0:
            # 多行编辑（同代码块）：Shift+Enter 换行，Enter 触发 on_submit 仅更新 draft
            field = _active_field(
                line, draft, on_change_draft, on_submit, on_blur,
                on_selection_change, initial_cursor, nav_seq, field_ref=field_ref,
                base_size=16, multiline=True, max_width=avail_width, line_height=line_height,
                on_cursor_sync=on_cursor_sync,
            )
            content = ft.Container(
                content=field, bgcolor=c.math_bg, border_radius=6, width=float("inf"),
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            )
        else:
            formula = line.segments[0].text if line.segments else ""
            md = ft.Markdown(
                value=f"$$\n{formula}\n$$",
                selectable=True,
                extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
            )
            content = ft.Container(
                content=md, bgcolor=c.math_bg, border_radius=6, width=float("inf"),
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                alignment=ft.Alignment.CENTER,
                on_click=lambda e: on_activate(line_idx, 0),
                ink=True,
            )
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate)

    # ============ 目录 [toc] ============
    if line.block_type == BlockType.TOC:
        if active_seg is not None:
            field = _active_field(
                line, draft, on_change_draft, on_submit, on_blur,
                on_selection_change, initial_cursor, nav_seq, field_ref=field_ref,
                max_width=avail_width, line_height=line_height, on_cursor_sync=on_cursor_sync,
            )
            content = ft.Container(
                content=field, width=float("inf"),
                padding=ft.Padding.symmetric(horizontal=2),
            )
        else:
            toc_items: list[ft.Control] = [
                ft.Container(
                    content=ft.Text(value=text, size=base - 1, color=c.text, font_family=FONT_MAIN),
                    padding=ft.Padding.only(left=(lvl - 1) * 16),
                    on_click=lambda e, t=li: on_jump_to(t) if on_jump_to else None,
                    ink=True,
                )
                for li, lvl, text in (toc_entries or [])
            ]
            # 目录块：左右撑满整行，灰色背景，便于与正文区分
            content = ft.Container(
                content=ft.Column(controls=toc_items, spacing=2),
                width=float("inf"),
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                bgcolor=c.code_bg, border_radius=6,
            )
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate)

    # ============ 任务列表项（非编辑态）============
    if line.task and active_seg is None:
        content_target_si = 1 if len(line.segments) > 1 else 0
        content_spans = _spans_for(line, 1, len(line.segments), activate, base) or [
            ft.TextSpan(" ", style=line_style, on_click=lambda e: activate(content_target_si))
        ]

        content = ft.Row(
            controls=[
                ft.Checkbox(
                    value=line.checked,
                    on_change=lambda e: on_toggle_task(line_idx) if on_toggle_task else None,
                ),
                ft.Text(spans=content_spans, style=line_style),
            ],
            wrap=True, spacing=4, run_spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate)

    # ============ 图片行（非编辑态）============
    if (img_idxs := _image_seg_indices(line)) and active_seg is None:
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
                            ft.Text(value=seg.text or seg.url or "图片", color=c.muted, size=base - 1, font_family=FONT_MAIN),
                        ],
                        spacing=8, alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                    bgcolor=c.code_block_bg, border_radius=6,
                    alignment=ft.Alignment.CENTER,
                ),
            }
            if w is not None:
                kw["width"] = w
            if h is not None:
                kw["height"] = h
            img_controls.append(
                ft.Container(
                    content=ft.Image(**kw),
                    on_click=lambda e, si=seg_idx: on_activate(line_idx, si),
                    ink=True,
                )
            )
        content = ft.Column(
            controls=img_controls, spacing=4,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate)

    # ============ 普通块（段落 / 标题 / 列表 / 引用）============
    if active_seg is None:
        # spans 不绑定 on_click：GestureDetector 统一处理点击，获取精确坐标
        # 做命中测试，把光标定位到点击的字符位置（而非段尾）。
        spans = _spans_for(line, 0, len(line.segments), None, base)

        def _on_tap(e: ft.TapEvent):
            pos = e.local_position
            if pos is not None:
                si, offset = _hit_test_tap(line, pos.x, pos.y, base, line_height)
                if si >= 0:
                    seg = line.segments[si]
                    if seg.seg_type == SegType.LINK and seg.url:
                        _open_link_url(seg.url)
                        return
                    if line.block_type == BlockType.HEADING:
                        activate(0, _logical_raw_offset(line, si, offset))
                    else:
                        activate(si, offset)
                    return
            # 回退：点击多行区域或无法定位时，激活最后一个段
            if line.block_type == BlockType.HEADING:
                activate(0)
            else:
                activate(max(0, len(line.segments) - 1))

        content = ft.Container(
            content=ft.GestureDetector(
                content=ft.Text(spans=spans, style=line_style, width=float("inf")),
                on_tap=_on_tap,
            ),
            ink=True,
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        )
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate)

    # 标题编辑态：整行原文（含 # 前缀）单字段编辑
    if line.block_type == BlockType.HEADING and active_seg is not None:
        seg0 = line.segments[0] if line.segments else Segment(SegType.HEADING_PREFIX, "# ", "")

        def _heading_edit_on_click(e):
            if on_suppress_blur:
                on_suppress_blur()
            activate(0, cursor_at=-1)

        content = ft.Container(
            content=active_text_field(
                seg0, draft, on_change_draft, on_submit, on_blur, base,
                on_selection_change=on_selection_change,
                initial_cursor=initial_cursor, nav_seq=nav_seq, field_ref=field_ref,
                max_width=avail_width, line_height=line_height, on_cursor_sync=on_cursor_sync,
            ),
            width=float("inf"),
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            on_click=_heading_edit_on_click,
            ink=True,
        )
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate)

    # 编辑态：前段 Text + 激活段 TextField + 后段 Text
    before_spans = _spans_for(line, 0, active_seg, activate, base)
    after_spans = _spans_for(line, active_seg + 1, len(line.segments), activate, base)
    active_seg_obj = line.segments[active_seg] if active_seg < len(line.segments) else None

    if active_seg_obj is None:
        # 段索引越界，退回非编辑态
        spans = _spans_for(line, 0, len(line.segments), activate, base)
        content = ft.Text(spans=spans, style=line_style)
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate)

    controls: list[ft.Control] = []
    if before_spans:
        controls.append(ft.Text(spans=before_spans, style=line_style))
    controls.append(
        active_text_field(
            active_seg_obj, draft, on_change_draft, on_submit, on_blur, base,
            on_selection_change=on_selection_change,
            initial_cursor=initial_cursor, nav_seq=nav_seq, field_ref=field_ref,
            max_width=avail_width, line_height=line_height, on_cursor_sync=on_cursor_sync,
        )
    )
    if after_spans:
        controls.append(ft.Text(spans=after_spans, style=line_style))

    # 编辑态：Row 外包裹 Container，设置 width=float("inf") 占满整行，
    # 防止点击右侧空白触发外层 _fallback_activate。同时添加 on_click：
    # 点击右侧空白时激活最后一个段（行尾），保持编辑状态。
    def _edit_on_click(e):
        if on_suppress_blur:
            on_suppress_blur()
        activate(max(0, len(line.segments) - 1), cursor_at=-1)

    row = ft.Row(
        controls=controls, wrap=True, spacing=0, run_spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    content = ft.Container(
        content=row,
        width=float("inf"),
        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        on_click=_edit_on_click,
        ink=True,
    )
    return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate)


def _wrap_block(
    content: ft.Control, line: Line, base: int, line_idx: int | None = None,
    on_click: Callable | None = None,
) -> ft.Control:
    """包一层块级容器：缩进、引用边框。

    嵌套引用：根据 line.level 包多层带左边框的 Container，每多一层嵌套
    行首多一个灰色竖线占位（Typora 式嵌套引用视觉）。

    on_click：挂到最外层 Container 的点击回调。内层 GestureDetector 会消费
    其覆盖区域的 tap 事件，因此 on_click 仅在内层未覆盖的区域（如 top/bottom
    padding 死区）触发，作为"点击行间空白也能进入编辑"的兜底。
    """
    c = _current_colors()  # 当前主题颜色（亮/暗）
    pad_left = 0

    if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O):
        pad_left = line.level * 20
    elif line.block_type == BlockType.QUOTE:
        # 嵌套引用：每层一个带左边框的 Container，层级由 line.level 决定
        lvl = line.level or 1
        for _ in range(lvl):
            content = ft.Container(
                content=content,
                padding=ft.Padding.only(left=12),
                border=only_border(left=ft.BorderSide(3, c.quote_bar)),
            )

    kwargs: dict = {
        "key": f"line-{line_idx}" if line_idx is not None else None,
        "content": content,
        "padding": ft.Padding.only(left=pad_left, top=2, bottom=2),
        "margin": ft.Margin.all(0),
        "ink": False,
    }
    if on_click is not None:
        kwargs["on_click"] = on_click
    return ft.Container(**kwargs)
