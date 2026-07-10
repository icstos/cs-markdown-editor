"""编辑器根组件：状态编排与编辑操作。

状态分层：
- document：observable Document（行列表 + 文件元信息）
- active  ：(line_idx, seg_idx) | None，当前正在编辑的段
- draft   ：当前编辑段 TextField 的本地文本（避免受控输入光标跳动）
- cursor_line：最近交互行（供工具栏块级操作在没有激活段时使用）

编辑流：点击段 -> activate（必要时先提交上一段）-> on_change 更新 draft ->
on_blur/on_submit 提交（reparse 该行）-> 重新渲染。结构变更通过
`document.lines = 新列表` 触发 observable 通知。
"""

from __future__ import annotations

import re
from typing import Callable, Optional

import flet as ft

from models import (
    BLOCK_CODE,
    BLOCK_HR,
    BLOCK_HEADING,
    BLOCK_LIST_O,
    BLOCK_LIST_UO,
    BLOCK_PARAGRAPH,
    BLOCK_QUOTE,
    Document,
    SEG_CODESPAN,
    SEG_EMPHASIS,
    SEG_HEADING_PREFIX,
    SEG_LINK,
    SEG_LIST_PREFIX,
    SEG_QUOTE_PREFIX,
    SEG_STRONG,
    SEG_STRIKE,
    SEG_TEXT,
)
import parser
from styles import C_BORDER, C_MUTED, C_TEXT, FONT_MAIN, only_border
from views.line_view import LineView
from views.toolbar import Toolbar

_WRAP_MAP = {
    SEG_STRONG: "**",
    SEG_EMPHASIS: "*",
    SEG_CODESPAN: "`",
    SEG_STRIKE: "~~",
}


def _inline_content(line) -> str:
    """取一行的“行内内容”源码（去掉块级前缀），用于块类型切换。"""
    if line.block_type == BLOCK_CODE:
        return line.segments[0].text if line.segments else ""
    if line.block_type == BLOCK_HR:
        return ""
    return "".join(
        s.raw
        for s in line.segments
        if s.seg_type not in (SEG_HEADING_PREFIX, SEG_LIST_PREFIX, SEG_QUOTE_PREFIX)
    )


def _next_line_raw(line) -> str:
    """回车续行：列表续列表（含任务/有序递增），否则空段落。"""
    if line.block_type in (BLOCK_LIST_UO, BLOCK_LIST_O):
        prefix = line.segments[0].raw if line.segments else "- "
        m = re.match(r"^([-*+])\s+\[[ xX]\]\s+", prefix)
        if m:
            return f"{m.group(1)} [ ] "
        m = re.match(r"^([-*+])\s+", prefix)
        if m:
            return f"{m.group(1)} "
        m = re.match(r"^(\d+)\.\s+", prefix)
        if m:
            return f"{int(m.group(1)) + 1}. "
        return "- "
    if line.block_type == BLOCK_QUOTE:
        return "> "
    return ""


@ft.component
def MarkdownEditor(
    document: Document,
    on_dirty_change: Optional[Callable[[bool], None]] = None,
):
    active, set_active = ft.use_state(None)  # (line_idx, seg_idx) | None
    draft, set_draft = ft.use_state("")  # 当前编辑段文本
    cursor_line, set_cursor_line = ft.use_state(0)

    def mark_dirty():
        document.dirty = True
        if on_dirty_change:
            on_dirty_change(True)

    def _draft_for(li: int, si: int) -> str:
        if 0 <= li < len(document.lines):
            line = document.lines[li]
            if 0 <= si < len(line.segments):
                return line.segments[si].raw
        return ""

    # ---- 提交当前激活段 ----
    def commit_active(new_raw: Optional[str] = None):
        if active is None:
            return
        li, si = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        raw = new_raw if new_raw is not None else draft

        if line.block_type == BLOCK_CODE:
            code = raw
            lang = line.lang
            full = f"```{lang}\n{code}\n```" if code else f"```{lang}\n```"
            parser.reparse_line(line, full)
        elif line.block_type == BLOCK_HR:
            parser.reparse_line(line, raw if raw.strip() else "---")
        else:
            if si < len(line.segments):
                line.segments[si].raw = raw
            full = "".join(s.raw for s in line.segments)
            parser.reparse_line(line, full)
        mark_dirty()

    # ---- 激活段 ----
    def activate(li: int, si: int):
        if active is not None and active != (li, si):
            commit_active(draft)
        set_draft(_draft_for(li, si))
        set_active((li, si))
        set_cursor_line(li)

    def on_change_draft(value: str):
        set_draft(value)

    def on_blur():
        commit_active(draft)
        set_active(None)

    def on_submit(new_raw: str):
        if active is None:
            return
        li, si = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        # 代码块内回车：仅更新 draft（多行输入）
        if line.block_type == BLOCK_CODE:
            set_draft(new_raw)
            return
        commit_active(new_raw)
        set_active(None)
        new_line_after(li)

    def new_line_after(li: int):
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        new_raw = _next_line_raw(line)
        new_line = parser.parse_markdown(new_raw).lines[0]
        document.lines = (
            document.lines[: li + 1] + [new_line] + document.lines[li + 1 :]
        )
        mark_dirty()
        target_si = max(0, len(new_line.segments) - 1)
        set_draft(_draft_for(li + 1, target_si))
        set_active((li + 1, target_si))
        set_cursor_line(li + 1)

    # ---- 工具栏：块类型切换 ----
    def set_block(block_type: str, level: int = 0):
        li = active[0] if active is not None else cursor_line
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if active is not None:
            # 先提交当前编辑段
            _commit_for_block(line, active, draft)
            set_active(None)
        content = _inline_content(line)
        if block_type == BLOCK_HEADING:
            new_raw = "#" * level + " " + content
        elif block_type == BLOCK_LIST_UO:
            new_raw = "- " + content
        elif block_type == BLOCK_LIST_O:
            new_raw = "1. " + content
        elif block_type == BLOCK_QUOTE:
            new_raw = "> " + content
        elif block_type == BLOCK_CODE:
            new_raw = "```\n" + content + "\n```"
        elif block_type == BLOCK_HR:
            new_raw = "---"
        else:
            new_raw = content
        parser.reparse_line(line, new_raw)
        mark_dirty()
        target_si = max(0, len(line.segments) - 1)
        set_draft(_draft_for(li, target_si))
        set_active((li, target_si))

    def _commit_for_block(line, active_pair, draft_val):
        li, si = active_pair
        if line.block_type == BLOCK_CODE:
            lang = line.lang
            full = f"```{lang}\n{draft_val}\n```" if draft_val else f"```{lang}\n```"
            parser.reparse_line(line, full)
        elif line.block_type != BLOCK_HR:
            if si < len(line.segments):
                line.segments[si].raw = draft_val
            full = "".join(s.raw for s in line.segments)
            parser.reparse_line(line, full)
        mark_dirty()

    # ---- 工具栏：行内格式切换 ----
    def toggle_inline(seg_type: str):
        if active is None:
            return
        li, si = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if line.block_type in (BLOCK_CODE, BLOCK_HR):
            return
        if si >= len(line.segments):
            return
        seg = line.segments[si]
        wrap = _WRAP_MAP.get(seg_type)
        if wrap is None:
            return
        if seg.seg_type == seg_type:
            seg.seg_type = SEG_TEXT
            seg.raw = seg.text
        elif seg.seg_type == SEG_TEXT:
            seg.seg_type = seg_type
            seg.raw = wrap + seg.text + wrap
        else:
            return
        mark_dirty()
        set_draft(seg.raw)

    def toggle_link():
        if active is None:
            return
        li, si = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if line.block_type in (BLOCK_CODE, BLOCK_HR):
            return
        if si >= len(line.segments):
            return
        seg = line.segments[si]
        if seg.seg_type == SEG_LINK:
            seg.seg_type = SEG_TEXT
            seg.raw = seg.text
            seg.url = ""
        elif seg.seg_type == SEG_TEXT:
            seg.seg_type = SEG_LINK
            seg.url = "url"
            seg.raw = f"[{seg.text}]({seg.url})"
        else:
            return
        mark_dirty()
        set_draft(seg.raw)

    # ---- 行视图列表 ----
    line_controls = []
    for i, line in enumerate(document.lines):
        a_seg = active[1] if (active is not None and active[0] == i) else None
        line_controls.append(
            LineView(
                key=f"line-{i}",
                line=line,
                line_idx=i,
                active_seg=a_seg,
                draft=draft,
                on_activate=activate,
                on_change_draft=on_change_draft,
                on_commit=lambda r: None,
                on_submit=on_submit,
                on_blur=on_blur,
                on_new_line_after=lambda idx: None,
            )
        )

    return ft.Column(
        controls=[
            Toolbar(
                on_h1=lambda: set_block(BLOCK_HEADING, 1),
                on_h2=lambda: set_block(BLOCK_HEADING, 2),
                on_h3=lambda: set_block(BLOCK_HEADING, 3),
                on_paragraph=lambda: set_block(BLOCK_PARAGRAPH),
                on_list=lambda: set_block(BLOCK_LIST_UO),
                on_quote=lambda: set_block(BLOCK_QUOTE),
                on_code_block=lambda: set_block(BLOCK_CODE),
                on_hr=lambda: set_block(BLOCK_HR),
                on_bold=lambda: toggle_inline(SEG_STRONG),
                on_italic=lambda: toggle_inline(SEG_EMPHASIS),
                on_code=lambda: toggle_inline(SEG_CODESPAN),
                on_link=toggle_link,
                on_strike=lambda: toggle_inline(SEG_STRIKE),
            ),
            ft.Container(
                content=ft.ListView(
                    controls=line_controls,
                    expand=True,
                    spacing=2,
                    padding=ft.Padding.symmetric(horizontal=24, vertical=16),
                    auto_scroll=False,
                ),
                expand=True,
                bgcolor=ft.Colors.WHITE,
            ),
            _status_bar(document),
        ],
        expand=True,
    )


def _status_bar(document) -> ft.Control:
    return ft.Container(
        bgcolor=ft.Colors.with_opacity(0.02, C_TEXT),
        border=only_border(top=ft.BorderSide(1, C_BORDER)),
        padding=ft.Padding.symmetric(horizontal=16, vertical=6),
        content=ft.Row(
            controls=[
                ft.Text(
                    value=("● " if document.dirty else "")
                    + (document.file_path or "未命名.md"),
                    size=12,
                    color=C_MUTED,
                    font_family=FONT_MAIN,
                ),
                ft.Text(
                    value=f"{len(document.lines)} 行",
                    size=12,
                    color=C_MUTED,
                    font_family=FONT_MAIN,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
    )
