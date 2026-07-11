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

import re
from typing import Callable

import flet as ft

from models import BlockType, Document, Line, SegType
import parser
from styles import C_BORDER, C_MUTED, C_TEXT, FONT_MAIN, FONT_MONO, only_border
from views.line_view import LineView
from views.toolbar import Toolbar

_WRAP_MAP: dict[SegType, str] = {
    SegType.STRONG: "**",
    SegType.EMPHASIS: "*",
    SegType.CODESPAN: "`",
    SegType.STRIKE: "~~",
}


def _inline_content(line: Line) -> str:
    """取一行的"行内内容"源码（去掉块级前缀），用于块类型切换。"""
    if line.block_type == BlockType.CODE:
        return line.segments[0].text if line.segments else ""
    if line.block_type == BlockType.HR:
        return ""
    return "".join(
        s.raw
        for s in line.segments
        if s.seg_type
        not in (SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX)
    )


def _next_line_raw(line: Line) -> str:
    """回车续行：列表续列表（含任务/有序递增），否则空段落。"""
    if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O):
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
    if line.block_type == BlockType.QUOTE:
        return "> "
    return ""


@ft.component
def MarkdownEditor(
    document: Document,
    on_dirty_change: Callable[[bool], None] | None = None,
    nav_ref: ft.Ref | None = None,
):
    active, set_active = ft.use_state(None)  # (line_idx, seg_idx) | None
    draft, set_draft = ft.use_state("")  # 当前编辑段文本
    cursor_line, set_cursor_line = ft.use_state(0)
    # 光标跟踪：供外层 on_key 判断左右越界、上下跨行目标偏移
    cursor_extent, set_cursor_extent = ft.use_state(0)
    cursor_base, set_cursor_base = ft.use_state(0)
    # nav_seq：每次跨段/激活递增，触发 TextField key 重建以重新 autofocus
    nav_seq, set_nav_seq = ft.use_state(0)
    # 原文模式：切换到原始 Markdown 文本编辑
    raw_mode, set_raw_mode = ft.use_state(False)
    raw_draft, set_raw_draft = ft.use_state("")

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
    def commit_active(new_raw: str | None = None):
        if active is None:
            return
        li, si = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        raw = new_raw if new_raw is not None else draft

        if line.block_type == BlockType.CODE:
            code = raw
            lang = line.lang
            full = f"```{lang}\n{code}\n```" if code else f"```{lang}\n```"
            parser.reparse_line(line, full)
        elif line.block_type == BlockType.HR:
            parser.reparse_line(line, raw if raw.strip() else "---")
        else:
            if si < len(line.segments):
                line.segments[si].raw = raw
            full = "".join(s.raw for s in line.segments)
            parser.reparse_line(line, full)
        mark_dirty()

    # ---- 激活段 ----
    def _goto(li: int, si: int):
        """跨段/激活目标段：先提交当前段，再切换 draft+active，递增 nav_seq
        触发 TextField key 重建以重新 autofocus。第一版光标落于段尾
        （autofocus 默认），cursor_extent 同步为段长，供后续越界判断。"""
        if active is not None and active != (li, si):
            commit_active(draft)
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if not (0 <= si < len(line.segments)):
            return
        new_draft = _draft_for(li, si)
        set_draft(new_draft)
        set_active((li, si))
        set_cursor_line(li)
        set_cursor_extent(len(new_draft))
        set_cursor_base(len(new_draft))
        set_nav_seq(nav_seq + 1)

    def activate(li: int, si: int):
        _goto(li, si)

    # ---- 段间/行间光标导航（由外层 on_key 经 nav_ref 调用）----
    def move_left_cross():
        if active is None:
            return
        li, si = active
        if li >= len(document.lines):
            return
        line = document.lines[li]
        # 代码块/分隔线：左右在块内移动，不跨段
        if line.block_type in (BlockType.CODE, BlockType.HR):
            return
        if si > 0:
            _goto(li, si - 1)
        elif li > 0:
            prev = document.lines[li - 1]
            _goto(li - 1, max(0, len(prev.segments) - 1))

    def move_right_cross():
        if active is None:
            return
        li, si = active
        if li >= len(document.lines):
            return
        line = document.lines[li]
        if line.block_type in (BlockType.CODE, BlockType.HR):
            return
        if si < len(line.segments) - 1:
            _goto(li, si + 1)
        elif li < len(document.lines) - 1:
            _goto(li + 1, 0)

    def move_home():
        """Home：跳到当前行第一个段的起点。代码块 Home 在块内由 TextField 处理。"""
        if active is None:
            return
        li, _ = active
        if li >= len(document.lines):
            return
        line = document.lines[li]
        if line.block_type in (BlockType.CODE, BlockType.HR):
            return
        _goto(li, 0)

    def move_end():
        """End：跳到当前行最后一个段（段尾由 autofocus 落点）。代码块同上。"""
        if active is None:
            return
        li, _ = active
        if li >= len(document.lines):
            return
        line = document.lines[li]
        if line.block_type in (BlockType.CODE, BlockType.HR):
            return
        _goto(li, max(0, len(line.segments) - 1))

    def _logical_offset(line: Line, seg_idx: int, extent: int) -> int:
        """行内逻辑字符偏移 = 前序段 raw 长度累加 + 段内偏移。"""
        off = 0
        for i in range(seg_idx):
            if i < len(line.segments):
                off += len(line.segments[i].raw)
        return off + extent

    def _locate_seg_by_offset(line: Line, target_off: int) -> tuple[int, int]:
        """在行内找包含逻辑偏移 target_off 的 (段索引, 段内偏移)。"""
        acc = 0
        for i, seg in enumerate(line.segments):
            n = len(seg.raw)
            if acc + n >= target_off:
                return i, max(0, min(target_off - acc, n))
            acc += n
        last = len(line.segments) - 1
        return max(0, last), (len(line.segments[last].raw) if last >= 0 else 0)

    def move_up():
        """上键：按行内逻辑偏移跨到上一行对应段。"""
        if active is None:
            return
        li, si = active
        if li <= 0:
            return
        cur = document.lines[li]
        target = _logical_offset(cur, si, cursor_extent)
        up_line = document.lines[li - 1]
        nsi, _ = _locate_seg_by_offset(up_line, target)
        _goto(li - 1, nsi)

    def move_down():
        """下键：按行内逻辑偏移跨到下一行对应段。"""
        if active is None:
            return
        li, si = active
        if li >= len(document.lines) - 1:
            return
        cur = document.lines[li]
        target = _logical_offset(cur, si, cursor_extent)
        dn_line = document.lines[li + 1]
        nsi, _ = _locate_seg_by_offset(dn_line, target)
        _goto(li + 1, nsi)

    def on_selection_change(e):
        """跟踪光标位置（extent/base），供 on_key 判断左右越界。"""
        sel = e.selection
        if sel is None:
            return
        set_cursor_base(sel.base_offset)
        set_cursor_extent(sel.extent_offset)

    def on_change_draft(value: str):
        set_draft(value)

    def toggle_raw():
        """在 WYSIWYG 编辑与原始 Markdown 文本间切换。

        进入原文模式：序列化当前文档为 raw_draft；
        返回编辑模式：重新解析 raw_draft 为行列表，替换 document.lines。
        """
        if not raw_mode:
            set_raw_draft(parser.serialize(document))
            set_active(None)
            set_raw_mode(True)
        else:
            new_doc = parser.parse_markdown(raw_draft)
            document.lines = new_doc.lines
            mark_dirty()
            set_raw_mode(False)

    def _raw_editor() -> ft.Control:
        """原文模式编辑器：多行 TextField 直接编辑 Markdown 源码。"""
        return ft.Container(
            content=ft.TextField(
                value=raw_draft,
                multiline=True,
                min_lines=10,
                expand=True,
                border=ft.InputBorder.NONE,
                text_size=14,
                text_style=ft.TextStyle(font_family=FONT_MONO, color=C_TEXT),
                content_padding=ft.Padding.symmetric(horizontal=24, vertical=16),
                on_change=lambda e: set_raw_draft(e.control.value),
            ),
            expand=True,
            bgcolor=ft.Colors.WHITE,
        )

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
        if line.block_type == BlockType.CODE:
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
        new_draft = _draft_for(li + 1, target_si)
        set_draft(new_draft)
        set_active((li + 1, target_si))
        set_cursor_line(li + 1)
        set_cursor_extent(len(new_draft))
        set_cursor_base(len(new_draft))
        set_nav_seq(nav_seq + 1)

    # ---- 工具栏：块类型切换 ----
    def set_block(block_type: BlockType, level: int = 0):
        li = active[0] if active is not None else cursor_line
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if active is not None:
            # 先提交当前编辑段
            _commit_for_block(line, active, draft)
            set_active(None)
        content = _inline_content(line)
        if block_type == BlockType.HEADING:
            new_raw = "#" * level + " " + content
        elif block_type == BlockType.LIST_UO:
            new_raw = "- " + content
        elif block_type == BlockType.LIST_O:
            new_raw = "1. " + content
        elif block_type == BlockType.QUOTE:
            new_raw = "> " + content
        elif block_type == BlockType.CODE:
            new_raw = "```\n" + content + "\n```"
        elif block_type == BlockType.HR:
            new_raw = "---"
        else:
            new_raw = content
        parser.reparse_line(line, new_raw)
        mark_dirty()
        target_si = max(0, len(line.segments) - 1)
        new_draft = _draft_for(li, target_si)
        set_draft(new_draft)
        set_active((li, target_si))
        set_cursor_extent(len(new_draft))
        set_cursor_base(len(new_draft))
        set_nav_seq(nav_seq + 1)

    def _commit_for_block(line: Line, active_pair: tuple[int, int], draft_val: str):
        li, si = active_pair
        if line.block_type == BlockType.CODE:
            lang = line.lang
            full = f"```{lang}\n{draft_val}\n```" if draft_val else f"```{lang}\n```"
            parser.reparse_line(line, full)
        elif line.block_type != BlockType.HR:
            if si < len(line.segments):
                line.segments[si].raw = draft_val
            full = "".join(s.raw for s in line.segments)
            parser.reparse_line(line, full)
        mark_dirty()

    # ---- 工具栏：行内格式切换 ----
    def toggle_inline(seg_type: SegType):
        if active is None:
            return
        li, si = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if line.block_type in (BlockType.CODE, BlockType.HR):
            return
        if si >= len(line.segments):
            return
        seg = line.segments[si]
        wrap = _WRAP_MAP.get(seg_type)
        if wrap is None:
            return
        if seg.seg_type == seg_type:
            seg.seg_type = SegType.TEXT
            seg.raw = seg.text
        elif seg.seg_type == SegType.TEXT:
            seg.seg_type = seg_type
            seg.raw = wrap + seg.text + wrap
        else:
            return
        mark_dirty()
        set_draft(seg.raw)
        set_cursor_extent(len(seg.raw))
        set_cursor_base(len(seg.raw))

    def toggle_link():
        if active is None:
            return
        li, si = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if line.block_type in (BlockType.CODE, BlockType.HR):
            return
        if si >= len(line.segments):
            return
        seg = line.segments[si]
        if seg.seg_type == SegType.LINK:
            seg.seg_type = SegType.TEXT
            seg.raw = seg.text
            seg.url = ""
        elif seg.seg_type == SegType.TEXT:
            seg.seg_type = SegType.LINK
            seg.url = "url"
            seg.raw = f"[{seg.text}]({seg.url})"
        else:
            return
        mark_dirty()
        set_draft(seg.raw)
        set_cursor_extent(len(seg.raw))
        set_cursor_base(len(seg.raw))

    # ---- 同步导航接口给外层 on_key（nav_ref）----
    if nav_ref is not None:
        nav_ref.current = {
            "active": active,
            "extent": cursor_extent,
            "base": cursor_base,
            "draft_len": len(draft),
            "move_left": move_left_cross,
            "move_right": move_right_cross,
            "move_home": move_home,
            "move_end": move_end,
            "move_up": move_up,
            "move_down": move_down,
        }

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
                on_selection_change=on_selection_change if a_seg is not None else None,
                initial_cursor=-1,
                nav_seq=nav_seq if a_seg is not None else 0,
            )
        )

    return ft.Column(
        controls=[
            Toolbar(
                on_h1=lambda: set_block(BlockType.HEADING, 1),
                on_h2=lambda: set_block(BlockType.HEADING, 2),
                on_h3=lambda: set_block(BlockType.HEADING, 3),
                on_paragraph=lambda: set_block(BlockType.PARAGRAPH),
                on_list=lambda: set_block(BlockType.LIST_UO),
                on_quote=lambda: set_block(BlockType.QUOTE),
                on_code_block=lambda: set_block(BlockType.CODE),
                on_hr=lambda: set_block(BlockType.HR),
                on_bold=lambda: toggle_inline(SegType.STRONG),
                on_italic=lambda: toggle_inline(SegType.EMPHASIS),
                on_code=lambda: toggle_inline(SegType.CODESPAN),
                on_link=toggle_link,
                on_strike=lambda: toggle_inline(SegType.STRIKE),
                on_toggle_raw=toggle_raw,
                raw_mode=raw_mode,
            ),
            _raw_editor() if raw_mode else ft.Container(
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


def _status_bar(document: Document) -> ft.Control:
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
