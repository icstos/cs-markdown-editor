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
    C_CODE_BLOCK_BG,
    C_MATH_BG,
    C_MUTED,
    C_QUOTE_BAR,
    C_TEXT,
    FONT_MAIN,
    FONT_MONO,
    block_text_size,
    block_weight,
    image_fit_size,
    measure_text_width,
    only_border,
)
from views.segment_view import _display_text, _MONO_SEGTYPES, active_text_field, segment_to_span

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


def _hit_test_tap(line: Line, x: float, y: float, base: int) -> tuple[int, int]:
    """根据点击位置计算 (seg_idx, raw_cursor_offset)。

    通过累加各段展示文本宽度做命中测试，再用 measure_text_width
    逐字逼近找到字符偏移。多行文本（y 超过行高）回退到 (-1, -1)。
    """
    if y > base * 1.6:
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


def _spans_for(
    line: Line,
    seg_from: int,
    seg_to_excl: int,
    on_activate: Callable[[int], None],
    base_size: int,
) -> list[ft.TextSpan]:
    """构造 [seg_from, seg_to_excl) 范围的 TextSpan 列表。"""
    return [
        segment_to_span(line.segments[i], i, on_activate, base_size)
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
    initial_cursor: int = -1,
    nav_seq: int = 0,
    field_ref: ft.Ref | None = None,
):
    base = block_text_size(line.block_type, line.level)
    weight = block_weight(line.block_type, line.level)
    line_style = ft.TextStyle(
        size=base, weight=weight, color=C_TEXT, font_family=FONT_MAIN, height=1.6
    )

    def activate(seg_idx: int, cursor_at: int = -1):
        on_activate(line_idx, seg_idx, cursor_at)

    # ============ 空行 ============
    if line.block_type == BlockType.BLANK or not _has_visible_text(line):
        if active_seg is not None:
            field = _active_field(
                line, draft, on_change_draft, on_submit, on_blur,
                on_selection_change, initial_cursor, nav_seq, field_ref=field_ref,
            )
            content = ft.Container(content=field, padding=ft.Padding.symmetric(horizontal=2))
        else:
            # 用 TextSpan.on_click 而非 GestureDetector.on_tap：
            # GestureDetector 的 tap 处理会干扰新 TextField 的 autofocus，
            # 导致空行点击后编辑块出现但光标不显示。TextSpan.on_click 是文本级
            # 事件，与 SelectionArea 兼容且不影响焦点系统。
            content = ft.Container(
                content=ft.Text(
                    spans=[
                        ft.TextSpan(" ", style=line_style, on_click=lambda e: activate(0))
                    ],
                    style=line_style,
                ),
                height=max(base * 1.6, 24),
                padding=ft.Padding.symmetric(horizontal=2),
                ink=True,
            )
        return _wrap_block(content, line, base, line_idx)

    # ============ 分隔线 ============
    if line.block_type == BlockType.HR:
        if active_seg is not None:
            field = _active_field(
                line, draft, on_change_draft, on_submit, on_blur,
                on_selection_change, initial_cursor, nav_seq, field_ref=field_ref,
            )
            content = ft.Container(padding=ft.Padding.symmetric(vertical=6), content=field)
        else:
            content = ft.Container(
                content=ft.Divider(height=1, thickness=1, color=C_QUOTE_BAR),
                padding=ft.Padding.symmetric(vertical=8),
                on_click=lambda e: on_activate(line_idx, 0),
                ink=True,
            )
        return _wrap_block(content, line, base, line_idx)

    # ============ 代码块 ============
    if line.block_type == BlockType.CODE:
        if active_seg == 0:
            inner = _active_field(
                line, draft, on_change_draft, on_submit, on_blur,
                on_selection_change, initial_cursor, nav_seq, field_ref=field_ref,
                base_size=14, multiline=True,
            )
            content = ft.Container(content=inner, bgcolor=C_CODE_BLOCK_BG, border_radius=6, padding=12)
        else:
            code = line.segments[0].text if line.segments else ""
            lang = line.lang or ""
            # ft.Markdown + GITHUB_WEB + code_theme 实现代码高亮
            md = ft.Markdown(
                value=f"```{lang}\n{code}\n```",
                selectable=True,
                extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                code_theme=ft.MarkdownCodeTheme.A11Y_LIGHT,
            )
            lang_tag = (
                ft.Text(value=lang, size=11, color=C_MUTED, font_family=FONT_MONO)
                if lang else ft.Text(" ")
            )
            content = ft.Container(
                content=ft.Column([lang_tag, md], spacing=6),
                bgcolor=C_CODE_BLOCK_BG, border_radius=6, padding=12,
                on_click=lambda e: on_activate(line_idx, 0),
                ink=True,
            )
        return _wrap_block(content, line, base, line_idx)

    # ============ 行间公式 ============
    if line.block_type == BlockType.MATH:
        if active_seg == 0:
            field = _active_field(
                line, draft, on_change_draft, on_submit, on_blur,
                on_selection_change, initial_cursor, nav_seq, field_ref=field_ref,
                base_size=16,
            )
            content = ft.Container(
                content=field, bgcolor=C_MATH_BG, border_radius=6,
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            )
        else:
            formula = line.segments[0].text if line.segments else ""
            md = ft.Markdown(
                value=f"$${formula}$$",
                selectable=True,
                extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
            )
            content = ft.Container(
                content=md, bgcolor=C_MATH_BG, border_radius=6,
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                alignment=ft.Alignment.CENTER,
                on_click=lambda e: on_activate(line_idx, 0),
                ink=True,
            )
        return _wrap_block(content, line, base, line_idx)

    # ============ 目录 [toc] ============
    if line.block_type == BlockType.TOC:
        if active_seg is not None:
            field = _active_field(
                line, draft, on_change_draft, on_submit, on_blur,
                on_selection_change, initial_cursor, nav_seq, field_ref=field_ref,
            )
            content = ft.Container(content=field, padding=ft.Padding.symmetric(horizontal=2))
        else:
            toc_items: list[ft.Control] = [
                ft.Container(
                    content=ft.Text(value=text, size=base - 1, color=C_TEXT, font_family=FONT_MAIN),
                    padding=ft.Padding.only(left=(lvl - 1) * 16),
                    on_click=lambda e, t=li: on_jump_to(t) if on_jump_to else None,
                    ink=True,
                )
                for li, lvl, text in (toc_entries or [])
            ]
            content = ft.Container(
                content=ft.Column(controls=toc_items, spacing=2),
                padding=ft.Padding.symmetric(vertical=8),
                bgcolor=C_CODE_BLOCK_BG, border_radius=6,
            )
        return _wrap_block(content, line, base, line_idx)

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
        return _wrap_block(content, line, base, line_idx)

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
                            ft.Icon(ft.Icons.IMAGE_NOT_SUPPORTED_OUTLINED, color=C_MUTED, size=20),
                            ft.Text(value=seg.text or seg.url or "图片", color=C_MUTED, size=base - 1, font_family=FONT_MAIN),
                        ],
                        spacing=8, alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                    bgcolor=C_CODE_BLOCK_BG, border_radius=6,
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
        return _wrap_block(content, line, base, line_idx)

    # ============ 普通块（段落 / 标题 / 列表 / 引用）============
    if active_seg is None:
        # spans 不绑定 on_click：GestureDetector 统一处理点击，获取精确坐标
        # 做命中测试，把光标定位到点击的字符位置（而非段尾）。
        spans = _spans_for(line, 0, len(line.segments), None, base)

        def _on_tap(e: ft.TapEvent):
            pos = e.local_position
            if pos is not None:
                si, offset = _hit_test_tap(line, pos.x, pos.y, base)
                if si >= 0:
                    activate(si, offset)
                    return
            # 回退：点击多行区域或无法定位时，激活最后一个段
            activate(max(0, len(line.segments) - 1))

        content = ft.Container(
            content=ft.GestureDetector(
                content=ft.Text(spans=spans, style=line_style),
                on_tap=_on_tap,
            ),
            ink=True,
        )
        return _wrap_block(content, line, base, line_idx)

    # 编辑态：前段 Text + 激活段 TextField + 后段 Text
    before_spans = _spans_for(line, 0, active_seg, activate, base)
    after_spans = _spans_for(line, active_seg + 1, len(line.segments), activate, base)
    active_seg_obj = line.segments[active_seg] if active_seg < len(line.segments) else None

    if active_seg_obj is None:
        # 段索引越界，退回非编辑态
        spans = _spans_for(line, 0, len(line.segments), activate, base)
        content = ft.Text(spans=spans, style=line_style)
        return _wrap_block(content, line, base, line_idx)

    controls: list[ft.Control] = []
    if before_spans:
        controls.append(ft.Text(spans=before_spans, style=line_style))
    controls.append(
        active_text_field(
            active_seg_obj, draft, on_change_draft, on_submit, on_blur, base,
            on_selection_change=on_selection_change,
            initial_cursor=initial_cursor, nav_seq=nav_seq, field_ref=field_ref,
        )
    )
    if after_spans:
        controls.append(ft.Text(spans=after_spans, style=line_style))

    content = ft.Row(
        controls=controls, wrap=True, spacing=0, run_spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    return _wrap_block(content, line, base, line_idx)


def _wrap_block(
    content: ft.Control, line: Line, base: int, line_idx: int | None = None,
) -> ft.Control:
    """包一层块级容器：缩进、引用边框。"""
    pad_left = 0
    border = None

    if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O):
        pad_left = line.level * 20
    elif line.block_type == BlockType.QUOTE:
        pad_left = 12
        border = only_border(left=ft.BorderSide(3, C_QUOTE_BAR))

    container = ft.Container(
        key=f"line-{line_idx}" if line_idx is not None else None,
        content=content,
        padding=ft.Padding.only(left=pad_left, top=2, bottom=2),
        margin=ft.Margin.all(0),
        border=border,
        ink=False,
    )
    return container
