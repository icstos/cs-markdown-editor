"""行视图：把一行渲染为可读样式，并在该行处于编辑态时进行段级布局。

布局策略（兼顾排版与段级编辑）：
- 非编辑态：整行作为单个 ft.Text(spans=[...])，自动换行、排版美观。
- 编辑态（该行某段被激活）：拆为 [前段 Text] + [激活段 TextField] + [后段 Text]，
  仅激活段显示原生 Markdown，其余段保持渲染样式——Typora 式最小语法编辑。
特殊块（代码块 / 分隔线 / 空行）单独处理。
"""

from typing import Callable

import flet as ft

from models import BlockType, Line, SegType
from styles import (
    C_CODE_BLOCK_BG,
    C_CODE_BLOCK_FG,
    C_MATH_BG,
    C_MUTED,
    C_QUOTE_BAR,
    C_TEXT,
    FONT_MAIN,
    FONT_MONO,
    block_text_size,
    block_weight,
    image_fit_size,
    only_border,
)
from views.segment_view import active_text_field, segment_to_span


def _spans_for(
    line: Line,
    seg_from: int,
    seg_to_excl: int,
    on_activate: Callable[[int], None],
    base_size: int,
) -> list[ft.TextSpan]:
    out = []
    for i in range(seg_from, seg_to_excl):
        if i < len(line.segments):
            out.append(segment_to_span(line.segments[i], i, on_activate, base_size))
    return out


def _has_visible_text(line: Line) -> bool:
    for s in line.segments:
        if s.text or s.raw:
            # 前缀段 raw 非空也算可见
            if s.text or s.seg_type in (
                SegType.HEADING_PREFIX,
                SegType.LIST_PREFIX,
                SegType.QUOTE_PREFIX,
            ):
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
            continue  # 忽略空白文本段
        else:
            return []
    return idxs


@ft.component
def LineView(
    line: Line,
    line_idx: int,
    active_seg: int | None,
    draft: str,
    on_activate: Callable[[int, int], None],
    on_change_draft: Callable[[str], None],
    on_commit: Callable[[str], None],
    on_submit: Callable[[str], None],
    on_blur: Callable[[], None],
    on_new_line_after: Callable[[int], None],
    on_selection_change: Callable | None = None,
    on_toggle_task: Callable[[int], None] | None = None,
    toc_entries: list[tuple[int, int, str]] | None = None,
    on_jump_to: Callable[[int], None] | None = None,
    initial_cursor: int = -1,
    nav_seq: int = 0,
):
    base = block_text_size(line.block_type, line.level)
    weight = block_weight(line.block_type, line.level)
    line_style = ft.TextStyle(
        size=base, weight=weight, color=C_TEXT, font_family=FONT_MAIN, height=1.6
    )

    def activate(seg_idx: int):
        on_activate(line_idx, seg_idx)

    # ---- 空行：可点击的空白区域 ----
    if line.block_type == BlockType.BLANK or not _has_visible_text(line):
        if active_seg is not None:
            field = active_text_field(
                line.segments[0],
                draft,
                on_change_draft,
                on_submit,
                on_blur,
                base,
                on_selection_change=on_selection_change,
                initial_cursor=initial_cursor,
                nav_seq=nav_seq,
            )
            content = ft.Container(
                content=field,
                padding=ft.Padding.symmetric(horizontal=2),
            )
        else:
            content = ft.GestureDetector(
                content=ft.Container(
                    content=ft.Text(" ", size=base, height=1.6),
                    height=max(base * 1.6, 24),
                    padding=ft.Padding.symmetric(horizontal=2),
                    ink=True,
                ),
                on_tap=lambda: on_activate(line_idx, 0),
                mouse_cursor=ft.MouseCursor.TEXT,
            )
        return _wrap_block(content, line, base, line_idx)

    # ---- 分隔线 ----
    if line.block_type == BlockType.HR:
        if active_seg is not None:
            field = active_text_field(
                line.segments[0],
                draft,
                on_change_draft,
                on_submit,
                on_blur,
                base,
                on_selection_change=on_selection_change,
                initial_cursor=initial_cursor,
                nav_seq=nav_seq,
            )
            content = ft.Container(
                padding=ft.Padding.symmetric(vertical=6), content=field
            )
        else:
            content = ft.GestureDetector(
                content=ft.Container(
                    content=ft.Divider(height=1, thickness=1, color=C_QUOTE_BAR),
                    padding=ft.Padding.symmetric(vertical=8),
                ),
                on_tap=lambda: on_activate(line_idx, 0),
                mouse_cursor=ft.MouseCursor.TEXT,
            )
        return _wrap_block(content, line, base, line_idx)

    # ---- 代码块：整段作为一个多行 TextField ----
    if line.block_type == BlockType.CODE:
        if active_seg == 0:
            inner = active_text_field(
                line.segments[0],
                draft,
                on_change_draft,
                on_submit,
                on_blur,
                base_size=14,
                multiline=True,
                on_selection_change=on_selection_change,
                initial_cursor=initial_cursor,
                nav_seq=nav_seq,
            )
            content = ft.Container(
                content=inner,
                bgcolor=C_CODE_BLOCK_BG,
                border_radius=6,
                padding=12,
            )
        else:
            code = line.segments[0].text if line.segments else ""
            txt = ft.Text(
                value=code or " ",
                style=ft.TextStyle(
                    size=14, color=C_CODE_BLOCK_FG, font_family=FONT_MONO, height=1.5
                ),
                selectable=True,
            )
            lang_tag = (
                ft.Text(
                    value=line.lang or "code",
                    size=11,
                    color=C_MUTED,
                    font_family=FONT_MONO,
                )
                if line.lang
                else ft.Text(" ")
            )
            content = ft.GestureDetector(
                content=ft.Container(
                    content=ft.Column([lang_tag, txt], spacing=6),
                    bgcolor=C_CODE_BLOCK_BG,
                    border_radius=6,
                    padding=12,
                ),
                on_tap=lambda: on_activate(line_idx, 0),
                mouse_cursor=ft.MouseCursor.TEXT,
            )
        return _wrap_block(content, line, base, line_idx)

    # ---- 行间公式：$$...$$ 用 ft.Markdown 渲染 LaTeX ----
    if line.block_type == BlockType.MATH:
        if active_seg == 0:
            field = active_text_field(
                line.segments[0],
                draft,
                on_change_draft,
                on_submit,
                on_blur,
                base_size=16,
                on_selection_change=on_selection_change,
                initial_cursor=initial_cursor,
                nav_seq=nav_seq,
            )
            content = ft.Container(
                content=field,
                bgcolor=C_MATH_BG,
                border_radius=6,
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            )
        else:
            formula = line.segments[0].text if line.segments else ""
            md = ft.Markdown(
                value=f"$${formula}$$",
                selectable=True,
                extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
            )
            content = ft.GestureDetector(
                content=ft.Container(
                    content=md,
                    bgcolor=C_MATH_BG,
                    border_radius=6,
                    padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                    alignment=ft.Alignment.CENTER,
                ),
                on_tap=lambda: on_activate(line_idx, 0),
                mouse_cursor=ft.MouseCursor.TEXT,
            )
        return _wrap_block(content, line, base, line_idx)

    # ---- 目录：[toc] 渲染为可点击的标题列表 ----
    if line.block_type == BlockType.TOC:
        if active_seg is not None:
            field = active_text_field(
                line.segments[0],
                draft,
                on_change_draft,
                on_submit,
                on_blur,
                base,
                on_selection_change=on_selection_change,
                initial_cursor=initial_cursor,
                nav_seq=nav_seq,
            )
            content = ft.Container(
                content=field,
                padding=ft.Padding.symmetric(horizontal=2),
            )
        else:
            toc_items: list[ft.Control] = []
            for li, lvl, text in toc_entries or []:
                toc_items.append(
                    ft.GestureDetector(
                        content=ft.Container(
                            content=ft.Text(
                                value=text,
                                size=base - 1,
                                color=C_TEXT,
                                font_family=FONT_MAIN,
                            ),
                            padding=ft.Padding.only(left=(lvl - 1) * 16),
                            ink=True,
                        ),
                        on_tap=lambda e, target_li=li: on_jump_to(target_li)
                        if on_jump_to
                        else None,
                        mouse_cursor=ft.MouseCursor.CLICK,
                    )
                )
            content = ft.Container(
                content=ft.Column(
                    controls=toc_items,
                    spacing=2,
                ),
                padding=ft.Padding.symmetric(vertical=8),
                bgcolor=C_CODE_BLOCK_BG,
                border_radius=6,
            )
        return _wrap_block(content, line, base, line_idx)

    # ---- 任务列表项（非编辑态）：复选框 + 内容 ----
    if line.task and active_seg is None:
        content_spans = _spans_for(line, 1, len(line.segments), activate, base)
        if not content_spans:
            content_spans = [ft.TextSpan(" ", line_style)]
        content_target_si = 1 if len(line.segments) > 1 else 0
        content = ft.Row(
            controls=[
                ft.Checkbox(
                    value=line.checked,
                    on_change=lambda e: on_toggle_task(line_idx)
                    if on_toggle_task
                    else None,
                ),
                ft.Text(
                    spans=content_spans,
                    style=line_style,
                    selectable=False,
                    on_tap=lambda: on_activate(line_idx, content_target_si),
                ),
            ],
            wrap=True,
            spacing=4,
            run_spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return _wrap_block(content, line, base, line_idx)

    # ---- 图片行：用 ft.Image 渲染（非编辑态）----
    img_idxs = _image_seg_indices(line)
    if img_idxs and active_seg is None:
        img_controls: list[ft.Control] = []
        for seg_idx in img_idxs:
            seg = line.segments[seg_idx]
            w, h = image_fit_size(seg.url)
            kw: dict = dict(
                src=seg.url,
                fit=ft.BoxFit.CONTAIN,
                tooltip=seg.text,
                error_content=ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Icon(
                                ft.Icons.IMAGE_NOT_SUPPORTED_OUTLINED,
                                color=C_MUTED,
                                size=20,
                            ),
                            ft.Text(
                                value=seg.text or seg.url or "图片",
                                color=C_MUTED,
                                size=base - 1,
                                font_family=FONT_MAIN,
                            ),
                        ],
                        spacing=8,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                    bgcolor=C_CODE_BLOCK_BG,
                    border_radius=6,
                    alignment=ft.Alignment.CENTER,
                ),
            )
            if w is not None:
                kw["width"] = w
            if h is not None:
                kw["height"] = h
            img_controls.append(
                ft.GestureDetector(
                    content=ft.Image(**kw),
                    on_tap=lambda e, si=seg_idx: on_activate(line_idx, si),
                    mouse_cursor=ft.MouseCursor.TEXT,
                )
            )
        content = ft.Column(
            controls=img_controls,
            spacing=4,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return _wrap_block(content, line, base, line_idx)

    # ---- 普通块（段落 / 标题 / 列表 / 引用）----
    if active_seg is None:
        spans = _spans_for(line, 0, len(line.segments), activate, base)
        content = ft.Text(
            spans=spans,
            style=line_style,
            selectable=False,
            on_tap=lambda: on_activate(line_idx, 0),
        )
        return _wrap_block(content, line, base, line_idx)

    # 编辑态：前段 Text + 激活段 TextField + 后段 Text
    before_spans = _spans_for(line, 0, active_seg, activate, base)
    after_spans = _spans_for(line, active_seg + 1, len(line.segments), activate, base)
    active_seg_obj = (
        line.segments[active_seg] if active_seg < len(line.segments) else None
    )

    if active_seg_obj is None:
        # 段索引越界，退回非编辑态
        spans = _spans_for(line, 0, len(line.segments), activate, base)
        content = ft.Text(spans=spans, style=line_style)
        return _wrap_block(content, line, base, line_idx)

    controls = []
    if before_spans:
        controls.append(ft.Text(spans=before_spans, style=line_style))
    controls.append(
        active_text_field(
            active_seg_obj,
            draft,
            on_change_draft,
            on_submit,
            on_blur,
            base,
            on_selection_change=on_selection_change,
            initial_cursor=initial_cursor,
            nav_seq=nav_seq,
        )
    )
    if after_spans:
        controls.append(ft.Text(spans=after_spans, style=line_style))

    content = ft.Row(
        controls=controls,
        wrap=True,
        spacing=0,
        run_spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    return _wrap_block(content, line, base, line_idx)


def _wrap_block(content: ft.Control, line: Line, base: int, line_idx: int | None = None) -> ft.Control:
    """包一层块级容器：缩进、引用边框、悬停反馈。"""
    pad_left = 0
    pad_right = 0
    border = None
    bgcolor = None

    if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O):
        pad_left = line.level * 20
    elif line.block_type == BlockType.QUOTE:
        pad_left = 12
        border = only_border(left=ft.BorderSide(3, C_QUOTE_BAR))
        # 引用文本用更柔和的颜色：通过覆盖 content 实现（此处仅容器层面留边框）

    container = ft.Container(
        content=content,
        padding=ft.Padding.only(left=pad_left, right=pad_right, top=2, bottom=2),
        margin=ft.Margin.all(0),
        border=border,
        bgcolor=bgcolor,
        ink=False,
    )
    if line_idx is not None:
        container.scroll_key = f"line-{line_idx}"
    return container
