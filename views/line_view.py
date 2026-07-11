"""行视图：把一行渲染为可读样式，并在该行处于编辑态时进行段级布局。

布局策略（兼顾排版与段级编辑）：
- 非编辑态：整行作为单个 ft.Text(spans=[...])，自动换行、排版美观。
- 编辑态（该行某段被激活）：拆为 [前段 Text] + [激活段 TextField] + [后段 Text]，
  仅激活段显示原生 Markdown，其余段保持渲染样式——Typora 式最小语法编辑。
特殊块（代码块 / 分隔线 / 空行）单独处理。
"""

from __future__ import annotations

from typing import Callable, Optional

import flet as ft

from models import (
    BLOCK_BLANK,
    BLOCK_CODE,
    BLOCK_HR,
    BLOCK_LIST_O,
    BLOCK_LIST_UO,
    BLOCK_QUOTE,
    Line,
)
from styles import (
    C_CODE_BLOCK_BG,
    C_CODE_BLOCK_FG,
    C_MUTED,
    C_QUOTE_BAR,
    C_TEXT,
    FONT_MAIN,
    FONT_MONO,
    block_text_size,
    block_weight,
    only_border,
)
from views.segment_view import active_text_field, segment_to_span


def _spans_for(
    line: Line,
    seg_from: int,
    seg_to_excl: int,
    on_activate: Callable[[int], None],
    base_size: int,
):
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
                "heading_prefix",
                "list_prefix",
                "quote_prefix",
            ):
                return True
    return False


@ft.component
def LineView(
    line: Line,
    line_idx: int,
    active_seg: Optional[int],
    draft: str,
    on_activate: Callable[[int, int], None],
    on_change_draft: Callable[[str], None],
    on_commit: Callable[[str], None],
    on_submit: Callable[[str], None],
    on_blur: Callable[[], None],
    on_new_line_after: Callable[[int], None],
    on_selection_change: Optional[Callable] = None,
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
    if line.block_type == BLOCK_BLANK or not _has_visible_text(line):
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
        return _wrap_block(content, line, base)

    # ---- 分隔线 ----
    if line.block_type == BLOCK_HR:
        if active_seg is not None:
            field = active_text_field(
                line.segments[0], draft, on_change_draft, on_submit, on_blur, base,
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
        return _wrap_block(content, line, base)

    # ---- 代码块：整段作为一个多行 TextField ----
    if line.block_type == BLOCK_CODE:
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
        return _wrap_block(content, line, base)

    # ---- 普通块（段落 / 标题 / 列表 / 引用）----
    if active_seg is None:
        spans = _spans_for(line, 0, len(line.segments), activate, base)
        content = ft.Text(
            spans=spans,
            style=line_style,
            selectable=False,
            on_tap=lambda: on_activate(line_idx, 0),
        )
        return _wrap_block(content, line, base)

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
        return _wrap_block(content, line, base)

    controls = []
    if before_spans:
        controls.append(ft.Text(spans=before_spans, style=line_style))
    controls.append(
        active_text_field(
            active_seg_obj, draft, on_change_draft, on_submit, on_blur, base,
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
    return _wrap_block(content, line, base)


def _wrap_block(content: ft.Control, line: Line, base: int) -> ft.Control:
    """包一层块级容器：缩进、引用边框、悬停反馈。"""
    pad_left = 0
    pad_right = 0
    border = None
    bgcolor = None

    if line.block_type in (BLOCK_LIST_UO, BLOCK_LIST_O):
        pad_left = line.level * 20
    elif line.block_type == BLOCK_QUOTE:
        pad_left = 12
        border = only_border(left=ft.BorderSide(3, C_QUOTE_BAR))
        # 引用文本用更柔和的颜色：通过覆盖 content 实现（此处仅容器层面留边框）

    return ft.Container(
        content=content,
        padding=ft.Padding.only(left=pad_left, right=pad_right, top=2, bottom=2),
        margin=ft.Margin.all(0),
        border=border,
        bgcolor=bgcolor,
        ink=False,
    )
