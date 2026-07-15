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

# 行内格式包裹语法
_WRAP_MAP: dict[SegType, str] = {
    SegType.STRONG: "**",
    SegType.EMPHASIS: "*",
    SegType.CODESPAN: "`",
    SegType.STRIKE: "~~",
}

# 不参与跨段/跨行光标导航的块类型（整块编辑，方向键在块内处理）
_NO_NAV_BLOCKS = (BlockType.CODE, BlockType.HR, BlockType.MATH, BlockType.TOC)


def _inline_content(line: Line) -> str:
    """取一行的"行内内容"源码（去掉块级前缀），用于块类型切换。"""
    if line.block_type in (BlockType.CODE, BlockType.MATH):
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
        if m := re.match(r"^([-*+])\s+\[[ xX]\]\s+", prefix):
            return f"{m.group(1)} [ ] "
        if m := re.match(r"^([-*+])\s+", prefix):
            return f"{m.group(1)} "
        if m := re.match(r"^(\d+)\.\s+", prefix):
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
    # 光标跟踪（ref 而非 state）：避免 on_selection_change 触发重渲染导致光标跳动
    # 仅在跨段导航/块切换时通过 _sync_cursor 重置；on_key 经 nav_ref 读取
    cursor_ref = ft.use_ref({"base": 0, "extent": 0, "draft_len": 0})
    # nav_seq：每次跨段/激活递增，触发 TextField key 重建以重新 autofocus
    nav_seq, set_nav_seq = ft.use_state(0)
    # 跨段导航时的光标落点：-1=段尾(autofocus), 0=段首
    cursor_pos, set_cursor_pos = ft.use_state(-1)
    # 粘贴时抑制 on_blur：handle_paste 修改 document.lines 触发重渲染，
    # 旧 TextField 卸载导致 on_blur 覆盖 set_active，需跳过这一次 blur
    suppress_blur = ft.use_ref(False)
    # 激活段 TextField 的 ref：use_effect 在渲染后显式调用 focus()，
    # 绕过 SelectionArea 内 autofocus 因手势竞争不可靠的问题
    active_field_ref = ft.use_ref(None)
    # 原文模式：切换到原始 Markdown 文本编辑
    raw_mode, set_raw_mode = ft.use_state(False)
    raw_draft, set_raw_draft = ft.use_state("")
    # ListView ref 用于 TOC 点击跳转滚动
    list_view_ref = ft.use_ref(None)

    # 渲染后显式聚焦激活段 TextField：SelectionArea 内点击 span 触发的
    # autofocus 因手势竞争不可靠，用 use_effect 在渲染提交后调用 focus() 确保聚焦。
    # focus() 是 async 方法，需用 async def + await。
    async def _focus_active_field():
        if active is not None and active_field_ref.current is not None:
            try:
                await active_field_ref.current.focus()
            except Exception:
                pass

    ft.use_effect(_focus_active_field, [active, nav_seq])

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

    def _sync_cursor(text: str, cursor_at: int = -1):
        """同步光标状态。cursor_at=-1: 段尾; 0: 段首; >0: 指定偏移。"""
        n = len(text)
        pos = cursor_at if cursor_at >= 0 else n
        cursor_ref.current["base"] = pos
        cursor_ref.current["extent"] = pos
        cursor_ref.current["draft_len"] = n

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
            lang = line.lang
            full = f"```{lang}\n{raw}\n```" if raw else f"```{lang}\n```"
            parser.reparse_line(line, full)
        elif line.block_type == BlockType.MATH:
            formula = raw.strip()
            full = f"$${formula}$$" if formula else "$$$$"
            parser.reparse_line(line, full)
        elif line.block_type == BlockType.HR:
            parser.reparse_line(line, raw if raw.strip() else "---")
        else:
            if si < len(line.segments):
                line.segments[si].raw = raw
            full = "".join(s.raw for s in line.segments)
            parser.reparse_line(line, full)
        mark_dirty()

    # ---- 激活段（统一的状态切换入口）----
    def _goto(li: int, si: int, cursor_at: int = -1):
        """跨段/激活目标段：先提交当前段，再切换 draft+active，递增 nav_seq
        触发 TextField key 重建以重新 autofocus。cursor_at: -1=段尾, 0=段首。"""
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
        set_cursor_pos(cursor_at)
        _sync_cursor(new_draft, cursor_at)
        set_nav_seq(nav_seq + 1)

    def activate(li: int, si: int):
        _goto(li, si)

    # ---- 段间/行间光标导航（由外层 on_key 经 nav_ref 调用）----
    def _nav_blocked(line: Line) -> bool:
        return line.block_type in _NO_NAV_BLOCKS

    def move_left_cross():
        if active is None:
            return
        li, si = active
        if li >= len(document.lines):
            return
        line = document.lines[li]
        if _nav_blocked(line):
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
        if _nav_blocked(line):
            return
        if si < len(line.segments) - 1:
            _goto(li, si + 1, cursor_at=0)
        elif li < len(document.lines) - 1:
            _goto(li + 1, 0, cursor_at=0)

    def move_home():
        """Home：跳到当前行第一个段的起点。"""
        if active is None:
            return
        li, _ = active
        if li >= len(document.lines) or _nav_blocked(document.lines[li]):
            return
        _goto(li, 0)

    def move_end():
        """End：跳到当前行最后一个段（段尾由 autofocus 落点）。"""
        if active is None:
            return
        li, _ = active
        if li >= len(document.lines) or _nav_blocked(document.lines[li]):
            return
        _goto(li, max(0, len(document.lines[li].segments) - 1))

    def _logical_offset(line: Line, seg_idx: int, extent: int) -> int:
        """行内逻辑字符偏移 = 前序段 raw 长度累加 + 段内偏移。"""
        return sum(len(line.segments[i].raw) for i in range(seg_idx)) + extent

    def _locate_seg_by_offset(line: Line, target_off: int) -> int:
        """在行内找包含逻辑偏移 target_off 的段索引。"""
        acc = 0
        for i, seg in enumerate(line.segments):
            n = len(seg.raw)
            if acc + n >= target_off:
                return i
            acc += n
        return max(0, len(line.segments) - 1)

    def move_up():
        """上键：按行内逻辑偏移跨到上一行对应段。"""
        if active is None:
            return
        li, si = active
        if li <= 0:
            return
        target = _logical_offset(document.lines[li], si, cursor_ref.current["extent"])
        nsi = _locate_seg_by_offset(document.lines[li - 1], target)
        _goto(li - 1, nsi)

    def move_down():
        """下键：按行内逻辑偏移跨到下一行对应段。"""
        if active is None:
            return
        li, si = active
        if li >= len(document.lines) - 1:
            return
        target = _logical_offset(document.lines[li], si, cursor_ref.current["extent"])
        nsi = _locate_seg_by_offset(document.lines[li + 1], target)
        _goto(li + 1, nsi)

    def on_selection_change(e):
        """跟踪光标位置（extent/base），供 on_key 判断左右越界。

        使用 ref 而非 set_state，避免输入时触发重渲染导致光标跳动。
        同时直接更新 nav_ref.current，确保 on_key 读到最新值。
        """
        if (sel := e.selection) is not None:
            cursor_ref.current["base"] = sel.base_offset
            cursor_ref.current["extent"] = sel.extent_offset
            cursor_ref.current["draft_len"] = len(e.control.value)
            if nav_ref is not None and nav_ref.current is not None:
                nav_ref.current["base"] = sel.base_offset
                nav_ref.current["extent"] = sel.extent_offset
                nav_ref.current["draft_len"] = len(e.control.value)

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
        if suppress_blur.current:
            suppress_blur.current = False
            return
        commit_active(draft)
        set_active(None)

    def handle_paste(clip_text: str, old_draft: str = ""):
        """处理多行粘贴：用 diff 定位粘贴位置，第一行留当前段，后续行插入为新行。

        单行 TextField（max_lines=1）会剥离换行符，导致粘贴的多行内容变为一行。
        本函数通过对比粘贴前后的 draft 定位粘贴文本，再用剪贴板原始多行文本重建。
        """
        if active is None or not clip_text or "\n" not in clip_text:
            return
        li, si = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        # 代码块/数学/HR 本身多行编辑，不处理
        if line.block_type in (BlockType.CODE, BlockType.MATH, BlockType.HR):
            return

        new_draft = draft  # 粘贴后（换行符已剥离）

        # diff：找 old/new 的公共前缀和后缀，定位粘贴区域
        pre = 0
        while (
            pre < len(old_draft)
            and pre < len(new_draft)
            and old_draft[pre] == new_draft[pre]
        ):
            pre += 1
        suf = 0
        while (
            suf < len(old_draft) - pre
            and suf < len(new_draft) - pre
            and old_draft[len(old_draft) - 1 - suf]
            == new_draft[len(new_draft) - 1 - suf]
        ):
            suf += 1

        parts = clip_text.split("\n")
        first = parts[0]
        rest = parts[1:]

        # 重建当前段 raw：旧前缀 + 第一行 + 旧后缀
        new_raw = old_draft[:pre] + first
        if suf > 0:
            new_raw += old_draft[len(old_draft) - suf :]

        if si < len(line.segments):
            line.segments[si].raw = new_raw
        full = "".join(s.raw for s in line.segments)
        parser.reparse_line(line, full)
        mark_dirty()

        if rest:
            new_lines = [parser.parse_markdown(p).lines[0] for p in rest]
            document.lines = (
                document.lines[: li + 1] + new_lines + document.lines[li + 1 :]
            )
            # 抑制重渲染导致的 on_blur（旧 TextField 卸载）
            suppress_blur.current = True
            # 激活最后一行最后一段
            last_li = li + len(new_lines)
            last_line = document.lines[last_li]
            target_si = max(0, len(last_line.segments) - 1)
            new_draft_val = (
                last_line.segments[target_si].raw
                if target_si < len(last_line.segments)
                else ""
            )
            set_draft(new_draft_val)
            set_active((last_li, target_si))
            set_cursor_line(last_li)
            set_cursor_pos(-1)
            _sync_cursor(new_draft_val)
            set_nav_seq(nav_seq + 1)
        else:
            suppress_blur.current = True
            set_draft(new_raw)
            set_cursor_pos(-1)
            _sync_cursor(new_raw)
            set_nav_seq(nav_seq + 1)

    def on_submit(new_raw: str):
        if active is None:
            return
        li, _ = active
        if not (0 <= li < len(document.lines)):
            return
        # 代码块内回车：仅更新 draft（多行输入）
        if document.lines[li].block_type == BlockType.CODE:
            set_draft(new_raw)
            return
        commit_active(new_raw)
        # 抑制旧 TextField 卸载时触发的 on_blur，避免覆盖 _goto 设置的 active
        suppress_blur.current = True
        set_active(None)
        new_line_after(li)

    def new_line_after(li: int):
        if not (0 <= li < len(document.lines)):
            return
        new_raw = _next_line_raw(document.lines[li])
        new_line = parser.parse_markdown(new_raw).lines[0]
        document.lines = (
            document.lines[: li + 1] + [new_line] + document.lines[li + 1 :]
        )
        mark_dirty()
        target_si = max(0, len(new_line.segments) - 1)
        _goto(li + 1, target_si)

    # ---- 工具栏：块类型切换 ----
    def set_block(block_type: BlockType, level: int = 0):
        li = active[0] if active is not None else cursor_line
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if active is not None:
            _commit_for_block(line, active, draft)
            # 抑制旧 TextField 卸载时触发的 on_blur，避免覆盖 _goto 设置的 active
            suppress_blur.current = True
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
        _goto(li, target_si)

    def _commit_for_block(line: Line, active_pair: tuple[int, int], draft_val: str):
        """块切换前先提交当前编辑段（避免丢失草稿）。"""
        li, si = active_pair
        if line.block_type == BlockType.CODE:
            full = (
                f"```{line.lang}\n{draft_val}\n```"
                if draft_val
                else f"```{line.lang}\n```"
            )
            parser.reparse_line(line, full)
        elif line.block_type == BlockType.MATH:
            formula = draft_val.strip()
            full = f"$${formula}$$" if formula else "$$$$"
            parser.reparse_line(line, full)
        elif line.block_type != BlockType.HR:
            if si < len(line.segments):
                line.segments[si].raw = draft_val
            parser.reparse_line(line, "".join(s.raw for s in line.segments))
        mark_dirty()

    # ---- 工具栏：行内格式切换 ----
    def _toggle_seg(seg_type: SegType):
        """通用行内格式切换（加粗/斜体/行内代码/删除线）。"""
        if active is None:
            return
        li, si = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if _nav_blocked(line):
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
        _sync_cursor(seg.raw)

    # 别名：供工具栏调用
    def toggle_inline(seg_type: SegType):
        _toggle_seg(seg_type)

    def toggle_link():
        if active is None:
            return
        li, si = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if _nav_blocked(line) or si >= len(line.segments):
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
        _sync_cursor(seg.raw)

    # ---- 任务列表项：切换勾选状态 ----
    def toggle_task(li: int):
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if not line.task:
            return
        # 切换 [ ]/[x] 标记，然后重解析
        pattern, repl = (r"\[[xX]\]", "[ ]") if line.checked else (r"\[ \]", "[x]")
        line.raw = re.sub(pattern, repl, line.raw, count=1)
        parser.reparse_line(line)
        mark_dirty()

    # ---- TOC 跳转 ----
    def jump_to(li: int):
        if not (0 <= li < len(document.lines)):
            return
        # scroll_to 是 async 方法，通过 run_task 调度执行
        if (lv := list_view_ref.current) is not None:
            ft.context.page.run_task(lv.scroll_to, scroll_key=f"line-{li}")
        _goto(li, 0)

    # ---- 同步导航接口给外层 on_key（nav_ref）----
    if nav_ref is not None:
        nav_ref.current = {
            "active": active,
            "extent": cursor_ref.current["extent"],
            "base": cursor_ref.current["base"],
            "draft_len": cursor_ref.current["draft_len"],
            "draft": draft,
            "move_left": move_left_cross,
            "move_right": move_right_cross,
            "move_home": move_home,
            "move_end": move_end,
            "move_up": move_up,
            "move_down": move_down,
            "compute_markdown_from_text": lambda text: (
                parser.compute_markdown_from_text(document.lines, text)
            ),
            "handle_paste": handle_paste,
        }

    # ---- 预计算 TOC 条目（所有标题）----
    toc_entries = [
        (
            i,
            line.level,
            "".join(
                s.text for s in line.segments if s.seg_type != SegType.HEADING_PREFIX
            ).strip(),
        )
        for i, line in enumerate(document.lines)
        if line.block_type == BlockType.HEADING
        and "".join(
            s.text for s in line.segments if s.seg_type != SegType.HEADING_PREFIX
        ).strip()
    ]

    # ---- 行视图列表 ----
    line_controls = [
        LineView(
            key=f"line-{i}",
            line=line,
            line_idx=i,
            active_seg=active[1] if (is_act := active is not None and active[0] == i) else None,
            draft=draft,
            on_activate=activate,
            on_change_draft=on_change_draft,
            on_submit=on_submit,
            on_blur=on_blur,
            on_selection_change=on_selection_change if is_act else None,
            on_toggle_task=toggle_task,
            toc_entries=toc_entries,
            on_jump_to=jump_to,
            initial_cursor=cursor_pos if is_act else -1,
            nav_seq=nav_seq if is_act else 0,
            field_ref=active_field_ref if is_act else None,
        )
        for i, line in enumerate(document.lines)
    ]

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
            _raw_editor()
            if raw_mode
            else ft.SelectionArea(
                content=ft.Container(
                    content=ft.Column(
                        ref=list_view_ref,
                        controls=line_controls,
                        expand=True,
                        spacing=2,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    expand=True,
                    bgcolor=ft.Colors.WHITE,
                    padding=ft.Padding.symmetric(horizontal=24, vertical=16),
                ),
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
