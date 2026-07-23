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

import os
import re
from typing import Callable

import flet as ft

from models import BlockType, Document, Line, SegType
import parser
from services.history import EditHistory, EditorSnapshot
from state.actions import EditorActions
from state.cursor import CursorState
from styles import (
    FONT_MAIN,
    FONT_MONO,
    _current_colors,
    only_border,
)
from views.code_block_view import CodeBlockEditor
from views.line_view import LineView
from views.table_view import TableView
from views.toolbar import Toolbar, _btn, _divider as _tb_divider


# 空操作回调（on_new/on_export 等为 None 时回退）
def _noop():
    pass


# 行内格式包裹语法
_WRAP_MAP: dict[SegType, str] = {
    SegType.STRONG: "**",
    SegType.EMPHASIS: "*",
    SegType.CODESPAN: "`",
    SegType.STRIKE: "~~",
}

# 围栏块（CODE/MATH/HR/TOC）：多行编辑，方向键在块内处理，BackSpace/Delete 不触发行合并
_FENCE_BLOCKS = (BlockType.CODE, BlockType.MATH, BlockType.HR, BlockType.TOC)


def _is_fence(line: Line) -> bool:
    """围栏块判断：CODE / MATH / HR / TOC。

    这类块整体编辑，不参与跨段/跨行光标导航，行首 BackSpace / 行尾 Delete
    不与相邻行合并。
    """
    return line.block_type in _FENCE_BLOCKS


def _locate_seg_by_raw_offset(line: Line, target: int) -> tuple[int, int]:
    """按 raw 偏移定位段：返回 (seg_idx, seg内偏移)。

    用于行合并后光标落 junction 段的定位。target 越界或 segments 为空时
    落到末段尾部（与原 backspace_core / delete_core 内联循环的回退值一致）。
    """
    if not line.segments:
        return 0, 0
    acc = 0
    for i, seg in enumerate(line.segments):
        n = len(seg.raw)
        if acc + n >= target:
            return i, max(0, target - acc)
        acc += n
    last = max(0, len(line.segments) - 1)
    return last, len(line.segments[last].raw)


def _first_content_seg(line: Line) -> int:
    """返回行内首个内容段索引（跳过 HEADING_PREFIX/LIST_PREFIX/QUOTE_PREFIX）。

    用于工具栏操作（indent/set_block/new_line_after）后定位光标到内容段，
    而非前缀段（# / - / > ）。无内容段时返回 0（兜底）。
    """
    for i, seg in enumerate(line.segments):
        if seg.seg_type not in (
            SegType.HEADING_PREFIX,
            SegType.LIST_PREFIX,
            SegType.QUOTE_PREFIX,
        ):
            return i
    return 0


def _line_raw(line: Line) -> str:
    """整行 Markdown 源码（段 raw 拼接，与 line.raw 一致）。"""
    return line.raw or "".join(s.raw for s in line.segments)


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
    """回车续行：列表续列表（含任务/有序递增），否则空段落。

    列表前缀段 raw 含缩进空格，匹配 marker 前先 lstrip，返回时补回
    line.level 个空格以保持级别（否则二级列表回车续行会塌回一级）。
    """
    if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O):
        indent_sp = " " * (line.level or 0)
        prefix = line.segments[0].raw if line.segments else "- "
        body = prefix.lstrip()  # 去掉缩进再匹配 marker
        if m := re.match(r"^([-*+])\s+\[[ xX]\]\s+", body):
            return f"{indent_sp}{m.group(1)} [ ] "
        if m := re.match(r"^([-*+])\s+", body):
            return f"{indent_sp}{m.group(1)} "
        if m := re.match(r"^(\d+)\.\s+", body):
            return f"{indent_sp}{int(m.group(1)) + 1}. "
        return f"{indent_sp}- "
    if line.block_type == BlockType.QUOTE:
        return "> " * (line.level or 1)
    return ""


def _file_name(path: str | None) -> str:
    return os.path.basename(path) if path else "未命名.md"


def _list_body(raw: str) -> tuple[str, str]:
    m = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", raw)
    if not m:
        return "", raw
    return m.group(1), m.group(3)


def _quote_body(raw: str) -> tuple[str, str]:
    m = re.match(r"^(\s*(?:>\s*)+)(.*)$", raw)
    if not m:
        return "", raw
    return m.group(1), m.group(2)


def _heading_prefix(level: int) -> str:
    return f"{'#' * max(level, 1)} "


@ft.component
def MarkdownEditor(
    document: Document,
    file_path: str | None = None,
    on_new: Callable[[], None] | None = None,
    on_open: Callable[[], None] | None = None,
    on_save: Callable[[], None] | None = None,
    on_export: Callable[[], None] | None = None,
    on_dirty_change: Callable[[bool], None] | None = None,
    nav_ref: ft.Ref | None = None,
    clipboard_ref: ft.Ref | None = None,
    theme_mode: ft.ThemeMode = ft.ThemeMode.LIGHT,
    on_toggle_theme: Callable[[], None] | None = None,
    settings: dict | None = None,
    on_open_settings: Callable[[], None] | None = None,
    sidebar_open: bool = False,
    on_toggle_sidebar: Callable[[], None] | None = None,
):
    c = _current_colors()  # 当前主题颜色（亮/暗）
    settings = settings or {}
    content_max_width = settings.get("content_max_width", 920)
    content_padding = settings.get("content_padding", 36)
    content_padding_top = settings.get("content_padding_top", 24)
    show_footer = settings.get("show_footer", True)
    body_font_size = settings.get("body_font_size", 16)
    line_height = settings.get("line_height", 1.6)
    show_toolbar = settings.get("show_toolbar", True)
    active, set_active = ft.use_state(None)  # line_idx | None（Typora 式行级编辑）
    active_seg, set_active_seg = ft.use_state(None)  # seg_idx | None（段级编辑：当前编辑段）
    table_cell, set_table_cell = ft.use_state(None)  # 当前表格编辑列 | None
    table_selected_cell, set_table_selected_cell = ft.use_state(None)  # 当前表格选中列 | None
    draft, set_draft = ft.use_state("")  # 当前编辑段 raw（inline 块）或段内容（CODE/MATH/TABLE）
    cursor_line, set_cursor_line = ft.use_state(0)
    # 光标跟踪（ref 而非 state）：避免 on_selection_change 触发重渲染导致光标跳动
    # 仅在跨行导航/块切换时通过 _sync_cursor 重置；on_key 经 nav_ref 读取
    cursor_ref = ft.use_ref(CursorState())
    # applied_cursor：_on_focus 设置光标后记录的目标位置（-1=未设置）。
    # 用于 on_selection_change 识别并丢弃 Flutter 聚焦时默认触发的 stale 段尾事件：
    # Flutter 先触发 on_focus（设置正确光标），再触发 on_selection_change(段尾)，
    # 若不拦截会覆盖 cursor_ref 的正确值，导致 Delete/Backspace 误判光标位置。
    applied_cursor = ft.use_ref(-1)
    # draft_ref：同步镜像 draft 状态。闭包的 draft 在 set_draft 后到下次渲染前是 stale 的，
    # 持续 Delete 时 delete_core 需在渲染前就读到最新 draft 才能正确删除字符。
    draft_ref = ft.use_ref("")
    # nav_seq：每次跨行/激活递增，触发 TextField key 重建以重新 autofocus
    nav_seq, set_nav_seq = ft.use_state(0)
    # 跨行导航时的光标落点：-1=段尾(autofocus), 0=段首, >0=段内 raw 偏移
    cursor_pos, set_cursor_pos = ft.use_state(-1)
    # 粘贴时抑制 on_blur：handle_paste 修改 document.lines 触发重渲染，
    # 旧 TextField 卸载导致 on_blur 覆盖 set_active，需跳过这一次 blur
    suppress_blur = ft.use_ref(False)
    # 激活行 TextField 的 ref：use_effect 在渲染后显式调用 focus()，
    # 绕过 SelectionArea 内 autofocus 因手势竞争不可靠的问题
    active_field_ref = ft.use_ref(None)
    # 原文模式：切换到原始 Markdown 文本编辑
    raw_mode, set_raw_mode = ft.use_state(False)
    raw_draft, set_raw_draft = ft.use_state("")
    # ListView ref 用于 TOC 点击跳转滚动
    list_view_ref = ft.use_ref(None)
    # SelectionArea 当前选中的纯文本（on_change 上报），供 Backspace 删除选区
    selection_text_ref = ft.use_ref("")
    # 撤销 / 重做栈
    history_ref = ft.use_ref(EditHistory(max_size=50))
    restoring = ft.use_ref(False)
    undo_push_pending = ft.use_ref(True)
    # 向外选区：(anchor_li, anchor_off, active_li, active_off) | None
    # Shift+Click / Shift+Arrow 起始的跨段/跨行选区；*_off 为行级 raw 偏移
    outward_sel, set_outward_sel = ft.use_state(None)
    outward_sel_ref = ft.use_ref(None)
    # Shift 键状态跟踪（ref，不触发重渲染；由 _on_key_down/_on_key_up 维护）
    shift_pressed_ref = ft.use_ref(False)
    # Ctrl 键状态：主同步源为 KeyDispatcher.handle() 的 e.ctrl（KeyboardEvent.ctrl 可靠），
    # _on_key_down/_on_key_up 用 key 名做兜底同步。用于 tab 分支判断 Ctrl+Tab，
    # 避免代码块/表格内 Ctrl+Tab 同时触发缩进与标签切换。
    ctrl_pressed_ref = ft.use_ref(False)

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

    # 每次渲染同步 draft_ref 到最新 draft 状态；_set_draft 在渲染前也会同步，
    # 确保 delete_core 等闭包在持续按键时也能读到最新 draft。
    draft_ref.current = draft
    outward_sel_ref.current = outward_sel

    def _set_draft(value: str):
        """同步更新 draft_ref 并排队 set_draft 重渲染。

        闭包的 draft 变量在 set_draft 后到下次渲染前是 stale 的，持续 Delete
        时 delete_core 需立即读到最新 draft 才能正确删除字符；同时 on_change_draft
        依赖 draft_ref 识别并跳过原生 Delete 产生的同值 on_change，避免重复 set_draft。
        """
        draft_ref.current = value
        set_draft(value)

    def _set_outward_sel(value):
        """同步更新 outward_sel_ref 并排队 set_outward_sel 重渲染。

        闭包的 outward_sel 变量在 set_outward_sel 后到下次渲染前是 stale 的，
        连续按 Shift+Arrow 扩展选区时需立即读到最新值，否则每次都重新设置锚点。
        """
        outward_sel_ref.current = value
        set_outward_sel(value)

    def mark_dirty():
        document.dirty = True
        if on_dirty_change:
            on_dirty_change(True)

    def _make_snapshot() -> EditorSnapshot:
        md = raw_draft if raw_mode else parser.serialize(document)
        return EditorSnapshot(
            markdown=md,
            active=active,
            active_seg=active_seg,
            draft=draft_ref.current,
            cursor_base=cursor_ref.current.base,
            cursor_extent=cursor_ref.current.extent,
            raw_mode=raw_mode,
            raw_draft=raw_draft,
        )

    def _push_history():
        if restoring.current:
            return
        history_ref.current.push(_make_snapshot())

    def _restore_snapshot(snap: EditorSnapshot):
        restoring.current = True
        suppress_blur.current = True
        try:
            set_raw_mode(snap.raw_mode)
            if snap.raw_mode:
                set_raw_draft(snap.raw_draft)
                document.lines = parser.parse_markdown(snap.raw_draft).lines
                set_active(None)
                _set_draft("")
            else:
                document.lines = parser.parse_markdown(snap.markdown).lines
                if snap.active is not None:
                    li = snap.active
                    if 0 <= li < len(document.lines):
                        _set_draft(snap.draft)
                        set_active(li)
                        set_active_seg(snap.active_seg)
                        set_cursor_line(li)
                        cursor_at = snap.cursor_base
                        set_cursor_pos(cursor_at if cursor_at >= 0 else -1)
                        _sync_cursor(snap.draft, cursor_at)
                        set_nav_seq(nav_seq + 1)
                    else:
                        set_active(None)
                        set_active_seg(None)
                        _set_draft("")
                else:
                    set_active(None)
                    set_active_seg(None)
                    _set_draft("")
            mark_dirty()
        finally:
            restoring.current = False
            undo_push_pending.current = True

    def undo():
        prev = history_ref.current.pop_undo(_make_snapshot())
        if prev is not None:
            _restore_snapshot(prev)

    def redo():
        nxt = history_ref.current.pop_redo(_make_snapshot())
        if nxt is not None:
            _restore_snapshot(nxt)

    def _maybe_push_draft_history():
        if undo_push_pending.current:
            _push_history()
            undo_push_pending.current = False

    def _table_cells(line: Line) -> list[str]:
        return [cell.strip() for cell in line.raw.strip().strip("|").split("|")]

    def _table_cell_at(line: Line, cell_idx: int) -> str:
        cells = _table_cells(line)
        return cells[cell_idx] if 0 <= cell_idx < len(cells) else ""

    def _draft_for(li: int, seg_idx: int | None = None, table_cell_idx: int | None = None) -> str:
        """获取激活段的 draft 文本。

        - 表格：返回 cell 内容
        - CODE/MATH：返回段内容（不含围栏）
        - inline 块（段落/标题/列表/引用/HR/TOC/BLANK）：返回激活段 raw（段级编辑）
        """
        if 0 <= li < len(document.lines):
            line = document.lines[li]
            if line.block_type == BlockType.TABLE and table_cell_idx is not None:
                return _table_cell_at(line, table_cell_idx)
            if line.block_type == BlockType.CODE:
                return line.segments[0].text if line.segments else ""
            if line.block_type == BlockType.MATH:
                return line.segments[0].text if line.segments else ""
            # inline 块：返回激活段 raw
            if seg_idx is not None and 0 <= seg_idx < len(line.segments):
                return line.segments[seg_idx].raw
            return line.segments[0].raw if line.segments else ""
        return ""

    def _reconstruct_line_raw(line: Line, seg_idx: int, seg_draft: str) -> str:
        """重构整行 raw = before_raw + seg_draft + after_raw（段级提交用）。"""
        before_raw = "".join(s.raw for s in line.segments[:seg_idx])
        after_raw = "".join(s.raw for s in line.segments[seg_idx + 1:])
        return before_raw + seg_draft + after_raw

    def _sync_cursor(text: str, cursor_at: int = -1):
        """同步光标状态。cursor_at=-1: 段尾; 0: 段首; >0: 段内偏移。"""
        n = len(text)
        pos = cursor_at if cursor_at >= 0 else n
        cursor_ref.current.reset(pos, n)

    # ---- 提交当前激活行 ----
    def commit_active(new_raw: str | None = None):
        if active is None:
            return
        li = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        # 使用 draft_ref.current 而非闭包 draft：持续 Delete 时闭包 draft 在
        # set_draft 后到下次渲染前是 stale 的，会导致提交错误的旧 draft 到文档。
        raw = new_raw if new_raw is not None else draft_ref.current

        if line.block_type == BlockType.CODE:
            lang = line.lang
            full = f"```{lang}\n{raw}\n```" if raw else f"```{lang}\n```"
            parser.reparse_line(line, full)
        elif line.block_type == BlockType.MATH:
            # 块级公式：多行围栏形式 $$\n...\n$$，保留公式内换行
            formula = raw.strip()
            full = f"$$\n{formula}\n$$" if formula else "$$\n$$"
            parser.reparse_line(line, full)
        elif line.block_type == BlockType.HR:
            parser.reparse_line(line, raw if raw.strip() else "---")
        elif line.block_type == BlockType.TABLE:
            cell_idx = table_cell if table_cell is not None else 0
            cells = _table_cells(line)
            if cell_idx < len(cells):
                cells[cell_idx] = raw
            parser.reparse_line(line, "| " + " | ".join(cells) + " |")
        else:
            # inline 块（段落/标题/列表/引用/HR/TOC/BLANK）：段级重构整行 raw
            seg_idx = active_seg if active_seg is not None else 0
            if 0 <= seg_idx < len(line.segments):
                full_raw = _reconstruct_line_raw(line, seg_idx, raw)
            else:
                full_raw = raw
            parser.reparse_line(line, full_raw)
        mark_dirty()

    # ---- 激活行（统一的状态切换入口）----
    def _goto(
        li: int,
        seg_idx: int | None = None,
        cursor_at: int = -1,
        skip_commit: bool = False,
        table_cell_idx: int | None = None,
    ):
        """跨行/激活目标段：先提交当前段，再切换 draft+active+active_seg，递增 nav_seq
        触发 TextField key 重建以重新 autofocus。cursor_at: -1=段尾, 0=段首, >0=段内 raw 偏移。

        skip_commit=True 跳过提交当前段——用于当前段即将被删除/移位的场景
        （如段首 Backspace 合并），避免把草稿提交到移位后的错误段。
        """
        is_new_table_cell = (
            0 <= li < len(document.lines)
            and document.lines[li].block_type == BlockType.TABLE
            and table_cell_idx != table_cell
        )
        if not skip_commit and active is not None and (active != li or is_new_table_cell):
            commit_active(draft_ref.current)
        if not (0 <= li < len(document.lines)):
            return
        new_draft = _draft_for(li, seg_idx, table_cell_idx)
        actual_cursor = len(new_draft) if cursor_at < 0 else min(cursor_at, len(new_draft))
        _set_draft(new_draft)
        set_active(li)
        set_active_seg(seg_idx)
        set_cursor_line(li)
        set_cursor_pos(actual_cursor)
        _sync_cursor(new_draft, actual_cursor)
        set_nav_seq(nav_seq + 1)

    def activate(li: int, seg_idx: int | None = None, cursor_at: int = -1):
        """激活段。cursor_at: -1=段尾, 0=段首, >0=段内 raw 偏移。

        表格行：cursor_at 作为 cell 索引，走 table_cell 路径。
        """
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if line.block_type == BlockType.TABLE:
            cell_idx = cursor_at if cursor_at >= 0 else 0
            set_table_selected_cell(cell_idx)
            set_table_cell(cell_idx)
            _goto(li, seg_idx=None, cursor_at=-1, table_cell_idx=cell_idx)
            return
        _goto(li, seg_idx=seg_idx, cursor_at=cursor_at)

    # ---- 段间/行间光标导航（由外层 on_key 经 nav_ref 调用）----
    def _nav_blocked(line: Line) -> bool:
        return _is_fence(line)

    def move_left_cross():
        """段首 ← 越界：先尝试跳上一段段尾，首段则跳上一行末段段尾。"""
        if active is None:
            return
        li = active
        if li >= len(document.lines):
            return
        line = document.lines[li]
        if _nav_blocked(line):
            return
        if cursor_ref.current.extent > 0:
            return  # 不在段首
        seg_idx = active_seg if active_seg is not None else 0
        # 段内：跳上一段段尾
        if seg_idx > 0:
            commit_active(draft_ref.current)
            _goto(li, seg_idx=seg_idx - 1, cursor_at=-1)
            return
        # 首段：跳上一行末段段尾
        if li <= 0:
            return
        commit_active(draft_ref.current)
        prev = document.lines[li - 1]
        if _is_fence(prev):
            _goto_quiet(li - 1, seg_idx=None, cursor_at=-1)
            return
        prev_last = max(0, len(prev.segments) - 1)
        _goto(li - 1, seg_idx=prev_last, cursor_at=-1)

    def move_right_cross():
        """段尾 → 越界：先尝试跳下一段段首，末段则跳下一行首段段首。"""
        if active is None:
            return
        li = active
        if li >= len(document.lines):
            return
        line = document.lines[li]
        if _nav_blocked(line):
            return
        if cursor_ref.current.extent < len(draft_ref.current):
            return  # 不在段尾
        seg_idx = active_seg if active_seg is not None else 0
        # 段内：跳下一段段首
        if seg_idx < len(line.segments) - 1:
            commit_active(draft_ref.current)
            _goto(li, seg_idx=seg_idx + 1, cursor_at=0)
            return
        # 末段：跳下一行首段段首
        if li >= len(document.lines) - 1:
            return
        commit_active(draft_ref.current)
        _goto(li + 1, seg_idx=0, cursor_at=0)

    def move_home():
        """Home：跳到本行首段段首。"""
        if active is None:
            return
        li = active
        if li >= len(document.lines) or _nav_blocked(document.lines[li]):
            return
        commit_active(draft_ref.current)
        _goto(li, seg_idx=0, cursor_at=0)

    def move_end():
        """End：跳到本行末段段尾。"""
        if active is None:
            return
        li = active
        if li >= len(document.lines) or _nav_blocked(document.lines[li]):
            return
        last_seg = max(0, len(document.lines[li].segments) - 1)
        commit_active(draft_ref.current)
        _goto(li, seg_idx=last_seg, cursor_at=-1)

    def _vertical_cursor_offset(line: Line, target_x_chars: int) -> int:
        """估算上下行垂直导航的目标 raw 偏移。

        基于行内字符累加：target_x_chars 是当前行光标的 raw 偏移，
        直接作为目标行的 raw 偏移（截断到目标行长度）。
        这是简化估算，用户可用方向键微调。
        """
        return max(0, min(target_x_chars, len(_line_raw(line))))

    def move_up():
        """上键：跳到上一行，定位到对应行级 raw 偏移所在段。"""
        if active is None:
            return
        li = active
        if li <= 0:
            return
        target = cursor_ref.current.extent
        seg_idx = active_seg if active_seg is not None else 0
        cur_line = document.lines[li]
        # 计算行级 raw 偏移（前段 raw + 段内偏移）
        line_offset = sum(len(cur_line.segments[i].raw) for i in range(seg_idx)) + target
        commit_active(draft_ref.current)
        prev_line = document.lines[li - 1]
        if _is_fence(prev_line):
            _goto_quiet(li - 1, seg_idx=None, cursor_at=-1)
            return
        target_seg, seg_offset = _locate_seg_by_raw_offset(prev_line, line_offset)
        _goto(li - 1, seg_idx=target_seg, cursor_at=seg_offset)

    def move_down():
        """下键：跳到下一行，定位到对应行级 raw 偏移所在段。"""
        if active is None:
            return
        li = active
        if li >= len(document.lines) - 1:
            return
        target = cursor_ref.current.extent
        seg_idx = active_seg if active_seg is not None else 0
        cur_line = document.lines[li]
        line_offset = sum(len(cur_line.segments[i].raw) for i in range(seg_idx)) + target
        commit_active(draft_ref.current)
        next_line = document.lines[li + 1]
        if _is_fence(next_line):
            _goto_quiet(li + 1, seg_idx=None, cursor_at=-1)
            return
        target_seg, seg_offset = _locate_seg_by_raw_offset(next_line, line_offset)
        _goto(li + 1, seg_idx=target_seg, cursor_at=seg_offset)

    def move_line_start():
        """Ctrl+Home：跳到本行首段段首。"""
        if active is None:
            return
        li = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if _nav_blocked(line):
            return
        commit_active(draft_ref.current)
        _goto(li, seg_idx=0, cursor_at=0)

    def move_line_end():
        """Ctrl+End：跳到本行末段段尾。"""
        if active is None:
            return
        li = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if _nav_blocked(line):
            return
        last_seg = max(0, len(line.segments) - 1)
        commit_active(draft_ref.current)
        _goto(li, seg_idx=last_seg, cursor_at=-1)

    def _first_content_index(line: Line) -> int:
        for i, s in enumerate(line.segments):
            if s.seg_type not in (
                SegType.HEADING_PREFIX,
                SegType.LIST_PREFIX,
                SegType.QUOTE_PREFIX,
            ):
                return i
        return max(0, len(line.segments) - 1)

    def _indent_list_line(line: Line, delta: int):
        raw = _line_raw(line)
        if line.block_type not in (BlockType.LIST_UO, BlockType.LIST_O):
            return
        if delta > 0:
            parser.reparse_line(line, "  " + raw)
        else:
            body = raw[2:] if raw.startswith("  ") else raw.lstrip()
            parser.reparse_line(line, body)
        mark_dirty()

    def _toggle_quote_level(line: Line, delta: int):
        raw = _line_raw(line)
        if delta > 0:
            parser.reparse_line(line, "> " + raw)
        else:
            body = raw[2:] if raw.startswith("> ") else raw.lstrip()
            parser.reparse_line(line, body)
        mark_dirty()

    def indent_or_outdent(delta: int):
        if active is None:
            return
        _push_history()
        undo_push_pending.current = True
        li = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O):
            _indent_list_line(line, delta)
            _goto(li, seg_idx=_first_content_seg(document.lines[li]), cursor_at=0)
        elif line.block_type == BlockType.QUOTE:
            _toggle_quote_level(line, delta)
            _goto(li, seg_idx=_first_content_seg(document.lines[li]), cursor_at=0)

    def backspace_core():
        """段首 BackSpace：先尝试跳上一段段尾；首段则与前一行合并。

        段级编辑：光标在段内时交由 TextField 原生删除（光标与字符一致）。
        段首且非首段：跳到上一段段尾（不合并段，符合 memory 约束）。
        段首且首段：与前一行合并（删除上一行行尾换行符）。
        向外选区激活时：删除整个选区。
        """
        if outward_sel is not None:
            handle_outward_delete()
            return
        if active is None:
            return
        li = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        # 代码块 / 块级公式 / 分隔线 / 目录 / 表格：整块编辑，行首 BackSpace 不处理
        if _is_fence(line) or line.block_type == BlockType.TABLE:
            return
        # 光标不在段首：交由 TextField 自身删除
        if cursor_ref.current.extent > 0:
            return
        seg_idx = active_seg if active_seg is not None else 0
        # 段内分支：非首段跳上一段段尾（不合并段，符合 memory 约束）
        if seg_idx > 0:
            commit_active(draft_ref.current)
            _goto(li, seg_idx=seg_idx - 1, cursor_at=-1)
            return
        # 首段段首且非首行：与前一行合并
        if li <= 0:
            return
        _push_history()
        undo_push_pending.current = False
        # 先提交当前行草稿（此时当前行仍有效），确保合并内容含最新输入
        commit_active(draft_ref.current)
        prev = document.lines[li - 1]
        # 前一行是围栏块（代码/公式/分隔线/目录）：无法合并，跳到其末尾
        if _is_fence(prev):
            _goto_quiet(li - 1, seg_idx=0, cursor_at=-1)
            return
        # 前一行含行内内容（段落/标题/列表/引用）：合并当前行内容到前一行末尾
        prev_raw = _line_raw(prev)
        junction = len(prev_raw)
        merged = prev_raw + _line_raw(document.lines[li])
        parser.reparse_line(prev, merged)
        document.lines = document.lines[:li] + document.lines[li + 1:]
        mark_dirty()
        suppress_blur.current = True
        # 光标落在合并点：定位到 junction 所在段
        junction_seg, junction_off = _locate_seg_by_raw_offset(prev, junction)
        _goto(li - 1, seg_idx=junction_seg, cursor_at=junction_off, skip_commit=True)

    def delete_core():
        """段尾 Delete：先尝试跳下一段段首；末段则与下一行合并。

        段级编辑：光标在段内时交由 TextField 原生 Delete（光标与字符一致）。
        段尾且非末段：跳到下一段段首。
        段尾且末段：与下一行合并（删除当前行行尾换行符）。
        向外选区激活时：删除整个选区。
        """
        if outward_sel is not None:
            handle_outward_delete()
            return
        if active is None:
            return
        li = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        # 代码块 / 块级公式 / 分隔线 / 目录 / 表格：整块编辑，行尾 Delete 不处理
        if _is_fence(line) or line.block_type == BlockType.TABLE:
            return
        # 光标不在段尾：交由 TextField 原生 Delete 处理
        if cursor_ref.current.extent < len(draft_ref.current):
            return
        seg_idx = active_seg if active_seg is not None else 0
        # 段内分支：非末段跳下一段段首
        if seg_idx < len(line.segments) - 1:
            commit_active(draft_ref.current)
            _goto(li, seg_idx=seg_idx + 1, cursor_at=0)
            return
        # 末段段尾且非末行：与下一行合并
        if li >= len(document.lines) - 1:
            return
        _push_history()
        undo_push_pending.current = False
        # 先提交当前行草稿（此时当前行仍有效），确保合并内容含最新输入
        commit_active(draft_ref.current)
        line = document.lines[li]
        next_line = document.lines[li + 1]
        # 下一行是围栏块：无法合并，跳到其首部
        if _is_fence(next_line):
            _goto_quiet(li + 1, seg_idx=0, cursor_at=0)
            return
        # 下一行含行内内容：合并下一行内容到当前行末尾
        current_raw = _line_raw(line)
        junction = len(current_raw)
        merged = current_raw + _line_raw(next_line)
        parser.reparse_line(line, merged)
        document.lines = document.lines[:li + 1] + document.lines[li + 2:]
        mark_dirty()
        suppress_blur.current = True
        # 光标落在合并点：定位到 junction 所在段
        junction_seg, junction_off = _locate_seg_by_raw_offset(line, junction)
        _goto(li, seg_idx=junction_seg, cursor_at=junction_off, skip_commit=True)

    def _on_cursor_sync(pos: int, draft_len: int):
        """_on_focus 设置光标后直接同步 cursor_ref，并记录 applied_cursor。

        Flutter 聚焦时先触发 on_focus（设置正确光标位置），紧接着触发
        on_selection_change(默认段尾)。若不处理，段尾事件会覆盖 cursor_ref，
        导致 Delete/Backspace 误判光标在段尾。此处先记录正确位置，再由
        on_selection_change 识别并丢弃 stale 段尾事件。

        draft_len 使用 draft_ref.current 而非参数：持续 Delete 时段中删除已更新
        draft_ref，但 segment_view 的 draft 参数在重渲染前是 stale 的，
        len(draft) 会覆盖为旧长度，导致 delete_core 误判光标在段尾而触发合并。
        """
        actual_len = len(draft_ref.current)
        cursor_ref.current.reset(pos, actual_len)
        applied_cursor.current = pos

    def on_selection_change(e):
        """跟踪光标位置（extent/base），供 on_key 判断左右越界。

        使用 ref 而非 set_state，避免输入时触发重渲染导致光标跳动。
        main.py 端通过 actions.cursor_ref.current 直接读取，无需双向同步。

        Stale 事件拦截：Flutter 聚焦时先触发 on_focus 设置正确光标（如行合并后
        的 junction 位置），再触发 on_selection_change(默认段尾=draft_len)。
        若 applied_cursor 已记录目标位置且与段尾不符，说明这是 stale 事件，丢弃。

        关键：只在 TextField 值长度未变时拦截（len(value)==draft_len），
        确保 stale 事件（值未变）被拦截，而用户删除到段尾的正常事件（值变短）
        不被错误拦截——否则 cursor_ref.draft_len 不更新，delete_core 误判
        光标不在段尾，持续 Delete 到段尾后失效。
        """
        if (sel := e.selection) is not None:
            if (
                applied_cursor.current >= 0
                and applied_cursor.current != sel.extent_offset
                and sel.extent_offset == len(e.control.value)
                and len(e.control.value) == cursor_ref.current.draft_len
            ):
                # stale 段尾事件：_on_focus 已设置正确光标，值未变，忽略此覆盖
                applied_cursor.current = -1
                return
            applied_cursor.current = -1
            cursor_ref.current.base = sel.base_offset
            cursor_ref.current.extent = sel.extent_offset
            cursor_ref.current.draft_len = len(e.control.value)

    def on_change_draft(value: str):
        if value == draft_ref.current:
            return  # 跳过同值事件（保留现有约束）
        _maybe_push_draft_history()
        _set_draft(value)
        # 同步更新 cursor_ref.draft_len 作为安全网：
        # on_selection_change 不可靠，可能导致 draft_len stale。
        # 每次 draft 变更都同步更新，确保所有读取 draft_len 的地方（如 main.py
        # 的 ArrowRight 判断、backspace_core 的段首判断）都读到最新值。
        n = len(value)
        cursor_ref.current.draft_len = n
        set_cursor_pos(-1)  # 清除强制光标位置，让 on_selection_change 接管
        # 段级编辑：前后段保持静态渲染态，仅激活段 TextField 显示原生 Markdown，
        # 无需 staging reparse。提交时（blur/跨段/工具栏）才由 commit_active 重构整行 reparse。

    def toggle_raw():
        """在 WYSIWYG 编辑与原始 Markdown 文本间切换。

        进入原文模式：序列化当前文档为 raw_draft；
        返回编辑模式：重新解析 raw_draft 为行列表，替换 document.lines。
        """
        _push_history()
        undo_push_pending.current = True
        if not raw_mode:
            set_raw_draft(parser.serialize(document))
            selection_text_ref.current = ""
            set_active(None)
            set_raw_mode(True)
        else:
            new_doc = parser.parse_markdown(raw_draft)
            document.lines = new_doc.lines
            mark_dirty()
            set_raw_mode(False)

    def toggle_focus_mode():
        page = ft.context.page
        if page is None:
            return
        try:
            page.window.full_screen = not bool(page.window.full_screen)
            page.update()
        except Exception:
            pass

    def _set_suppress_blur():
        suppress_blur.current = True

    def _goto_quiet(
        li: int,
        seg_idx: int | None = None,
        cursor_at: int = -1,
        skip_commit: bool = False,
        table_cell_idx: int | None = None,
    ):
        """抑制 blur + 跨段/跨行导航的统一入口（段级：active=li, active_seg=seg_idx）。

        重渲染会导致旧 TextField 卸载触发 on_blur，覆盖 _goto 设置的 active；
        先设 suppress_blur 抑制此次 blur。table_cell_idx 用于表格跨格导航。
        """
        suppress_blur.current = True
        _goto(
            li,
            seg_idx=seg_idx,
            cursor_at=cursor_at,
            skip_commit=skip_commit,
            table_cell_idx=table_cell_idx,
        )

    code_editor = CodeBlockEditor(
        get_active=lambda: active,
        get_line=lambda li: document.lines[li] if 0 <= li < len(document.lines) else None,
        draft_ref=draft_ref,
        cursor_ref=cursor_ref,
        active_field_ref=active_field_ref,
        selection_text_ref=selection_text_ref,
        clipboard_ref=clipboard_ref,
        set_draft=_set_draft,
        mark_dirty=mark_dirty,
        commit_active=lambda: commit_active(draft_ref.current),
        suppress_blur=_set_suppress_blur,
        deactivate=lambda: set_active(None),
    )

    def _on_raw_draft_change(value: str):
        _maybe_push_draft_history()
        set_raw_draft(value)
        mark_dirty()

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
                text_style=ft.TextStyle(font_family=FONT_MONO, color=c.text),
                content_padding=ft.Padding.symmetric(horizontal=24, vertical=16),
                on_change=lambda e: _on_raw_draft_change(e.control.value),
            ),
            expand=True,
            bgcolor=c.bg,
        )

    def commit_and_exit():
        commit_active(draft_ref.current)
        set_active(None)

    def on_blur():
        # Shift+Click 时保持 active 不变：on_blur 在 GestureDetector.on_tap 之前触发，
        # 若此时调用 set_active(None)，后续 _start_outward 检查 active 时为 None，
        # 提前返回导致选区无法起始。此处跳过，_start_outward 内部会补上 commit_active。
        if shift_pressed_ref is not None and bool(shift_pressed_ref.current):
            return
        if suppress_blur.current:
            suppress_blur.current = False
            return
        try:
            commit_active(draft_ref.current)
        finally:
            set_active(None)

    def handle_paste(clip_text: str, old_draft: str = ""):
        """处理多行粘贴：diff 定位粘贴位置，第一行留当前段，后续行插入为新行。

        段级编辑下 draft = segment.raw，diff 在段 raw 上定位粘贴区域，
        再重构整行 raw = before + new_seg_raw + after 提交。
        单行 TextField（max_lines=1）会剥离换行符，导致粘贴的多行内容变为一行。
        本函数通过对比粘贴前后的 draft 定位粘贴文本，再用剪贴板原始多行文本重建。
        """
        if active is None or not clip_text or "\n" not in clip_text:
            return
        _push_history()
        undo_push_pending.current = True
        li = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        # 代码块/数学/HR/TOC 本身多行编辑或特殊块，不处理
        if line.block_type in (BlockType.CODE, BlockType.MATH, BlockType.HR, BlockType.TOC):
            return

        new_draft = draft_ref.current  # 粘贴后（换行符已剥离）

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

        # 重建当前段 raw：旧段前缀 + 第一行 + 旧段后缀
        new_seg_raw = old_draft[:pre] + first
        if suf > 0:
            new_seg_raw += old_draft[len(old_draft) - suf :]

        # 段级：重构整行 raw = before + new_seg_raw + after
        seg_idx = active_seg if active_seg is not None else 0
        if 0 <= seg_idx < len(line.segments):
            full_raw = _reconstruct_line_raw(line, seg_idx, new_seg_raw)
        else:
            full_raw = new_seg_raw
        parser.reparse_line(line, full_raw)
        mark_dirty()

        if rest:
            new_lines = [parser.parse_markdown(p).lines[0] for p in rest]
            document.lines = (
                document.lines[: li + 1] + new_lines + document.lines[li + 1 :]
            )
            # 抑制重渲染导致的 on_blur（旧 TextField 卸载）
            suppress_blur.current = True
            # 段级：激活最后一行末段段尾
            last_li = li + len(new_lines)
            last_line = document.lines[last_li]
            last_seg = max(0, len(last_line.segments) - 1)
            _goto(last_li, seg_idx=last_seg, cursor_at=-1)
        else:
            suppress_blur.current = True
            _set_draft(new_seg_raw)
            set_cursor_pos(-1)
            _sync_cursor(new_seg_raw)
            set_nav_seq(nav_seq + 1)

    def on_submit(new_raw: str):
        if active is None:
            return
        _push_history()
        undo_push_pending.current = True
        li = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        # 代码块 / 块级公式内回车：仅更新 draft，不在 Enter 时退出编辑态
        # 代码块的提交统一交给 Ctrl+Enter / Esc / 失焦，以获得更接近 IDE 的行为。
        if line.block_type in (BlockType.CODE, BlockType.MATH):
            _set_draft(new_raw)
            return
        # 特殊块（分隔线 / 目录）：提交后创建空行
        if line.block_type in (BlockType.HR, BlockType.TOC):
            commit_active(new_raw)
            suppress_blur.current = True
            set_active(None)
            new_line_after(li)
            return

        # 段级：new_raw 是激活段 raw，需重构整行再按行级偏移分割
        seg_idx = active_seg if active_seg is not None else 0
        if 0 <= seg_idx < len(line.segments):
            line_raw = _reconstruct_line_raw(line, seg_idx, new_raw)
        else:
            line_raw = new_raw
        before_raw_len = sum(len(line.segments[i].raw) for i in range(seg_idx))
        split_pos = min(before_raw_len + cursor_ref.current.extent, len(line_raw))
        before = line_raw[:split_pos]
        after = line_raw[split_pos:]

        # 标题：before 空 → 清空前缀；否则分割成两行（标题 + 段落）
        if line.block_type == BlockType.HEADING:
            if not before.strip():
                parser.reparse_line(line, after.lstrip())
                mark_dirty()
                target_li = li + 1 if li + 1 < len(document.lines) else li
                _goto(
                    target_li,
                    seg_idx=_first_content_seg(document.lines[target_li]),
                    cursor_at=0,
                )
                return
            parser.reparse_line(line, before)
            new_line = parser.parse_markdown(after).lines[0]
            document.lines = (
                document.lines[: li + 1] + [new_line] + document.lines[li + 1 :]
            )
            mark_dirty()
            _goto_quiet(li + 1, seg_idx=_first_content_seg(new_line), cursor_at=0)
            return

        # 列表 / 引用：before 仅前缀（空内容）→ 退出列表/引用
        if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O, BlockType.QUOTE):
            if not before.strip():
                stripped = after.lstrip()
                if line.block_type == BlockType.QUOTE:
                    stripped = stripped.lstrip("> ")
                parser.reparse_line(line, stripped)
                mark_dirty()
                target_li = li + 1 if li + 1 < len(document.lines) else li
                _goto(
                    target_li,
                    seg_idx=_first_content_seg(document.lines[target_li]),
                    cursor_at=0,
                )
                return
            if line.block_type == BlockType.LIST_UO and before.rstrip() in (
                "-",
                "*",
                "+",
            ):
                parser.reparse_line(line, after.lstrip())
                mark_dirty()
                target_li = li + 1 if li + 1 < len(document.lines) else li
                _goto(
                    target_li,
                    seg_idx=_first_content_seg(document.lines[target_li]),
                    cursor_at=0,
                )
                return
            if line.block_type == BlockType.LIST_O and re.match(
                r"^\d+\.$", before.rstrip()
            ):
                parser.reparse_line(line, after.lstrip())
                mark_dirty()
                target_li = li + 1 if li + 1 < len(document.lines) else li
                _goto(
                    target_li,
                    seg_idx=_first_content_seg(document.lines[target_li]),
                    cursor_at=0,
                )
                return

        # 默认：分割当前行，续行加列表/引用前缀
        cont_prefix = _next_line_raw(line)
        parser.reparse_line(line, before)
        new_line = parser.parse_markdown(cont_prefix + after).lines[0]
        document.lines = (
            document.lines[: li + 1] + [new_line] + document.lines[li + 1 :]
        )
        mark_dirty()
        # 段级：cursor_at 是行级偏移（len(cont_prefix)），定位到对应段
        target_seg, target_off = _locate_seg_by_raw_offset(new_line, len(cont_prefix))
        suppress_blur.current = True
        _goto_quiet(li + 1, seg_idx=target_seg, cursor_at=target_off)

    def _table_move(delta: int):
        if active is None or table_cell is None:
            return
        li = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if line.block_type != BlockType.TABLE:
            return
        cells = _table_cells(line)
        if not cells:
            return
        next_idx = table_cell + delta
        if next_idx < 0:
            prev_li = li - 1
            while prev_li >= 0 and document.lines[prev_li].block_type != BlockType.TABLE:
                prev_li -= 1
            if prev_li < 0:
                next_idx = 0
            else:
                prev_cells = _table_cells(document.lines[prev_li])
                next_idx = len(prev_cells) - 1 if prev_cells else 0
                li = prev_li
        elif next_idx >= len(cells):
            next_li = li + 1
            while next_li < len(document.lines) and document.lines[next_li].block_type != BlockType.TABLE:
                next_li += 1
            if next_li < len(document.lines):
                li = next_li
                next_idx = 0
            else:
                next_idx = len(cells) - 1
        if li == active and next_idx == table_cell:
            return
        commit_active(draft_ref.current)
        suppress_blur.current = True
        set_table_selected_cell(next_idx)
        set_table_cell(next_idx)
        _goto(li, cursor_at=-1, table_cell_idx=next_idx)

    def _table_tab(delta: int):
        _table_move(delta)

    def _table_enter():
        if active is None or table_cell is None:
            return
        li = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if line.block_type != BlockType.TABLE:
            return
        cells = _table_cells(line)
        if not cells:
            return
        if table_cell < len(cells) - 1:
            _table_move(1)
            return
        if li + 1 < len(document.lines) and document.lines[li + 1].block_type == BlockType.TABLE:
            commit_active(draft_ref.current)
            suppress_blur.current = True
            set_table_selected_cell(0)
            set_table_cell(0)
            _goto(li + 1, cursor_at=-1, table_cell_idx=0)
            return
        commit_active(draft_ref.current)
        set_table_selected_cell(None)
        set_table_cell(None)
        set_active(None)

    def new_line_after(li: int):
        if not (0 <= li < len(document.lines)):
            return
        _push_history()
        undo_push_pending.current = True
        new_raw = _next_line_raw(document.lines[li])
        new_line = parser.parse_markdown(new_raw).lines[0]
        document.lines = (
            document.lines[: li + 1] + [new_line] + document.lines[li + 1 :]
        )
        mark_dirty()
        _goto(li + 1, seg_idx=_first_content_seg(new_line), cursor_at=-1)

    # ---- 工具栏：块类型切换 ----
    def set_block(block_type: BlockType, level: int = 0):
        li = active if active is not None else cursor_line
        if not (0 <= li < len(document.lines)):
            return
        _push_history()
        undo_push_pending.current = True
        line = document.lines[li]
        if active is not None:
            _commit_for_block(line, draft_ref.current)
            # 抑制旧 TextField 卸载时触发的 on_blur，避免覆盖 _goto 设置的 active
            suppress_blur.current = True
            set_active(None)
        content = _inline_content(line)
        if block_type == BlockType.HEADING:
            new_raw = "#" * level + " " + content
        elif block_type == BlockType.LIST_UO:
            # 源行已是列表时保留缩进级别，避免工具栏切换列表类型时塌回一级
            indent_sp = (
                " " * line.level
                if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O)
                else ""
            )
            new_raw = f"{indent_sp}- " + content
        elif block_type == BlockType.LIST_O:
            indent_sp = (
                " " * line.level
                if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O)
                else ""
            )
            new_raw = f"{indent_sp}1. " + content
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
        _goto(li, seg_idx=_first_content_seg(document.lines[li]), cursor_at=-1)

    def _commit_for_block(line: Line, draft_val: str):
        """块切换前提交当前段级 draft（避免丢失）。

        CODE/MATH：draft_val 是段内容（不含围栏），按围栏包裹 reparse。
        inline 块：draft_val 是激活段 raw，需重构整行 = before + draft + after 再 reparse。
        """
        if line.block_type == BlockType.CODE:
            full = (
                f"```{line.lang}\n{draft_val}\n```"
                if draft_val
                else f"```{line.lang}\n```"
            )
            parser.reparse_line(line, full)
        elif line.block_type == BlockType.MATH:
            formula = draft_val.strip()
            full = f"$$\n{formula}\n$$" if formula else "$$\n$$"
            parser.reparse_line(line, full)
        else:
            # inline 块：段级重构整行
            seg_idx = active_seg if active_seg is not None else 0
            if 0 <= seg_idx < len(line.segments):
                full_raw = _reconstruct_line_raw(line, seg_idx, draft_val)
            else:
                full_raw = draft_val
            parser.reparse_line(line, full_raw)
        mark_dirty()

    # ---- 工具栏：行内格式切换 ----
    def _toggle_seg(seg_type: SegType):
        """通用行内格式切换（段级）：有选区包裹/解裹段内选区；无选区切换光标所在段。

        draft 是激活段 raw（段级编辑），操作产生 new_seg_raw 后用
        _reconstruct_line_raw 重构整行 reparse，再用行级偏移重新定位光标所在段
        （reparse 可能改变段数与边界，需重新查找）。
        """
        if active is None:
            return
        _push_history()
        undo_push_pending.current = True
        li = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if _nav_blocked(line):
            return
        wrap = _WRAP_MAP.get(seg_type)
        if wrap is None:
            return
        seg_idx = active_seg if active_seg is not None else 0
        if not (0 <= seg_idx < len(line.segments)):
            return
        seg_raw = draft_ref.current  # 段 raw
        base = cursor_ref.current.base
        extent = cursor_ref.current.extent

        if base != extent:
            # 有选区：包裹/解裹段内选区
            selected = seg_raw[base:extent]
            if (
                selected.startswith(wrap)
                and selected.endswith(wrap)
                and len(selected) >= 2 * len(wrap)
            ):
                inner = selected[len(wrap):-len(wrap)]
                new_seg_raw = seg_raw[:base] + inner + seg_raw[extent:]
                cursor_at = base
            else:
                new_seg_raw = seg_raw[:base] + wrap + selected + wrap + seg_raw[extent:]
                cursor_at = base + len(wrap)
        else:
            # 无选区：切换激活段格式
            seg = line.segments[seg_idx]
            if seg.seg_type == seg_type:
                # 解裹
                inner = (
                    seg_raw[len(wrap):-len(wrap)]
                    if len(seg_raw) >= 2 * len(wrap)
                    else seg_raw
                )
                new_seg_raw = inner
                cursor_at = max(0, extent - len(wrap))
            elif seg.seg_type == SegType.TEXT:
                # 包裹
                new_seg_raw = wrap + seg_raw + wrap
                cursor_at = extent + len(wrap)
            else:
                return

        # 段级：重构整行 raw = before + new_seg_raw + after，reparse
        full_raw = _reconstruct_line_raw(line, seg_idx, new_seg_raw)
        parser.reparse_line(line, full_raw)
        mark_dirty()
        # 重新定位光标所在段：行级偏移 = before_raw_len + cursor_at
        before_raw_len = sum(len(line.segments[i].raw) for i in range(seg_idx))
        new_seg, new_offset = _locate_seg_by_raw_offset(line, before_raw_len + cursor_at)
        _goto(li, seg_idx=new_seg, cursor_at=new_offset)

    # 别名：供工具栏调用
    def toggle_inline(seg_type: SegType):
        _toggle_seg(seg_type)

    def toggle_link():
        """链接切换（段级）：有选区包裹为 [text](url)；无选区切换光标所在段。

        draft 是激活段 raw，操作产生 new_seg_raw 后重构整行 reparse，
        再用行级偏移重新定位光标所在段。
        """
        if active is None:
            return
        _push_history()
        undo_push_pending.current = True
        li = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if _nav_blocked(line):
            return
        seg_idx = active_seg if active_seg is not None else 0
        if not (0 <= seg_idx < len(line.segments)):
            return
        seg_raw = draft_ref.current
        base = cursor_ref.current.base
        extent = cursor_ref.current.extent

        if base != extent:
            # 有选区：包裹段内选区为 [text](url)
            selected = seg_raw[base:extent]
            new_seg_raw = seg_raw[:base] + f"[{selected}](url)" + seg_raw[extent:]
            cursor_at = base + 1
        else:
            # 无选区：切换激活段格式
            seg = line.segments[seg_idx]
            if seg.seg_type == SegType.LINK:
                # 解裹为纯文本
                new_seg_raw = seg.text
                cursor_at = 0
            elif seg.seg_type == SegType.TEXT:
                new_seg_raw = f"[{seg.text}](url)"
                cursor_at = len(seg.text) + 3  # 落在 ) 前
            else:
                return

        # 段级：重构整行 raw，reparse，重新定位段
        full_raw = _reconstruct_line_raw(line, seg_idx, new_seg_raw)
        parser.reparse_line(line, full_raw)
        mark_dirty()
        before_raw_len = sum(len(line.segments[i].raw) for i in range(seg_idx))
        new_seg, new_offset = _locate_seg_by_raw_offset(line, before_raw_len + cursor_at)
        _goto(li, seg_idx=new_seg, cursor_at=new_offset)

    # ---- 任务列表项：切换勾选状态 ----
    def toggle_task(li: int):
        if not (0 <= li < len(document.lines)):
            return
        _push_history()
        undo_push_pending.current = True
        line = document.lines[li]
        if not line.task:
            return
        # 切换 [ ]/[x] 标记，然后重解析
        pattern, repl = (r"\[[xX]\]", "[ ]") if line.checked else (r"\[ \]", "[x]")
        line.raw = re.sub(pattern, repl, line.raw, count=1)
        parser.reparse_line(line)
        mark_dirty()

    # ---- 代码块语言修改 ----
    def change_lang(new_lang: str):
        """代码块编辑态：修改语言类型，同步更新围栏首行。"""
        if active is None:
            return
        _push_history()
        undo_push_pending.current = True
        li = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if line.block_type != BlockType.CODE:
            return
        # li == active，draft 即当前编辑草稿；保持与原逻辑一致
        code = draft_ref.current
        line.lang = new_lang
        full = f"```{new_lang}\n{code}\n```" if code else f"```{new_lang}\n```"
        try:
            parser.reparse_line(line, full)
        except Exception:
            return
        mark_dirty()

    def suppress_blur_for_lang():
        """语言输入框聚焦时设置 suppress_blur，防止代码框 blur 退出编辑态。"""
        suppress_blur.current = True

    def suppress_blur_for_click():
        """点击编辑行右侧空白时设置 suppress_blur，防止 TextField blur 退出编辑态。"""
        suppress_blur.current = True

    def _commit_selection_delete(selections: dict[int, tuple[int, int]]) -> None:
        """删除已匹配的选区并定位光标到删除起点。"""
        _push_history()
        undo_push_pending.current = True
        try:
            new_lines, cursor_li, cursor_si, cursor_offset = parser.delete_selections(
                document.lines, selections
            )
        except Exception:
            return
        document.lines = new_lines
        mark_dirty()
        selection_text_ref.current = ""
        set_active(None)
        if 0 <= cursor_li < len(document.lines):
            # 行级：cursor_offset 即 raw 偏移；越界回退行尾。
            # cursor_si 在行级模式下不再使用（保留返回值兼容）。
            line = document.lines[cursor_li]
            if cursor_offset < 0 or cursor_offset > len(line.raw):
                cursor_offset = -1
            if cursor_offset < 0:
                # 段尾：定位到首个内容段
                _goto(cursor_li, seg_idx=_first_content_seg(line), cursor_at=-1)
            else:
                # 行级偏移定位到段
                target_seg, target_off = _locate_seg_by_raw_offset(line, cursor_offset)
                _goto(cursor_li, seg_idx=target_seg, cursor_at=target_off)

    def handle_delete_selection(plain_text: str):
        """处理 Backspace 删除选中内容（非编辑态 SelectionArea 选区）。"""
        if not plain_text:
            return
        try:
            selections = parser.match_text_to_selections(document.lines, plain_text)
        except Exception:
            return
        if not selections:
            return
        _commit_selection_delete(selections)

    async def handle_cut(plain_text: str):
        """处理 Ctrl+X 剪切：复制选中内容为 Markdown 到剪贴板，并删除文档中选中内容。"""
        if not plain_text:
            return
        try:
            selections = parser.match_text_to_selections(document.lines, plain_text)
        except Exception:
            return
        if not selections:
            return
        try:
            md = parser.compute_markdown_from_selections(document.lines, selections)
            if md:
                clipboard = clipboard_ref.current if clipboard_ref is not None else None
                if clipboard is not None:
                    await clipboard.set(md)
        except Exception:
            return
        _commit_selection_delete(selections)

    def on_selection_area_change(e):
        selection_text_ref.current = e.data if e.data else ""

    # ---- 向外选区（Shift+Click / Shift+Arrow 起始的跨段/跨行选区）----
    def _line_raw_offset(li: int, seg_idx: int, seg_offset: int) -> int:
        """段内偏移 → 整行 raw 偏移。"""
        line = document.lines[li]
        return sum(len(line.segments[i].raw) for i in range(seg_idx)) + seg_offset

    def _step_left(li: int, off: int) -> tuple[int, int] | None:
        """raw 偏移空间向左一步，跨行时跳过围栏块。"""
        if off > 0:
            return (li, off - 1)
        if li <= 0:
            return None
        prev = document.lines[li - 1]
        if _is_fence(prev):
            return None
        return (li - 1, len(_line_raw(prev)))

    def _step_right(li: int, off: int) -> tuple[int, int] | None:
        """raw 偏移空间向右一步。"""
        if not (0 <= li < len(document.lines)):
            return None
        cur_raw = _line_raw(document.lines[li])
        if off < len(cur_raw):
            return (li, off + 1)
        if li >= len(document.lines) - 1:
            return None
        nxt = document.lines[li + 1]
        if _is_fence(nxt):
            return None
        return (li + 1, 0)

    def _step_up(li: int, off: int) -> tuple[int, int] | None:
        if li <= 0:
            return None
        prev = document.lines[li - 1]
        if _is_fence(prev):
            return None
        return (li - 1, min(off, len(_line_raw(prev))))

    def _step_down(li: int, off: int) -> tuple[int, int] | None:
        if li >= len(document.lines) - 1:
            return None
        nxt = document.lines[li + 1]
        if _is_fence(nxt):
            return None
        return (li + 1, min(off, len(_line_raw(nxt))))

    def _start_outward(target_li: int, target_off: int) -> None:
        """从当前编辑光标起始向外选区，扩展到 (target_li, target_off)。"""
        if active is None or outward_sel is not None:
            return
        if not (0 <= target_li < len(document.lines)):
            return
        target_off = max(0, min(target_off, len(document.lines[target_li].raw or "")))
        src_li = active
        if not (0 <= src_li < len(document.lines)):
            return
        seg_idx = active_seg if active_seg is not None else 0
        src_off = _line_raw_offset(src_li, seg_idx, cursor_ref.current.extent)
        commit_active(draft_ref.current)
        # 抑制重渲染导致的 on_blur（旧 TextField 卸载）——与 _goto_quiet 一致，
        # 否则 on_blur 会用 stale closure 的 active 再次 commit_active + set_active(None)，
        # 虽然 commit 对同一 draft 幂等，但 set_active(None) 会触发额外重渲染，
        # 干扰刚设置的 outward_sel 状态（编辑光标起始跨段/跨行选区无效的根因）。
        suppress_blur.current = True
        set_active(None)
        set_active_seg(None)
        _set_outward_sel((src_li, src_off, target_li, target_off))

    def _start_outward_from_point(anchor_li: int, anchor_off: int, target_li: int, target_off: int) -> None:
        """从指定点起始向外选区，不依赖 active 状态。

        用于非编辑模式拖动选区（active=None）和编辑模式拖动（on_blur 在 on_pan_start
        之前触发，导致 active 已被清除）。anchor 为选区起点，target 为当前终点。
        """
        if outward_sel is not None:
            return
        if not (0 <= anchor_li < len(document.lines) and 0 <= target_li < len(document.lines)):
            return
        anchor_off = max(0, min(anchor_off, len(document.lines[anchor_li].raw or "")))
        target_off = max(0, min(target_off, len(document.lines[target_li].raw or "")))
        # 若存在 active 编辑段，先提交草稿
        if active is not None:
            commit_active(draft_ref.current)
            suppress_blur.current = True
            set_active(None)
            set_active_seg(None)
        _set_outward_sel((anchor_li, anchor_off, target_li, target_off))

    def _extend_outward(target_li: int, target_off: int) -> None:
        """已存在向外选区时，保留 anchor，更新 active 端点。"""
        if outward_sel is None:
            return _start_outward(target_li, target_off)
        if not (0 <= target_li < len(document.lines)):
            return
        target_off = max(0, min(target_off, len(document.lines[target_li].raw or "")))
        a_li, a_off, _, _ = outward_sel
        _set_outward_sel((a_li, a_off, target_li, target_off))

    def _extend_outward_step(step_fn) -> None:
        """Shift+Arrow：用 step_fn 移动 active 端点一步。"""
        current_outward = outward_sel_ref.current
        if active is not None and current_outward is None:
            # 起始：anchor = 当前光标
            seg_idx = active_seg if active_seg is not None else 0
            cur_off = _line_raw_offset(active, seg_idx, cursor_ref.current.extent)
            new_pos = step_fn(active, cur_off)
            if new_pos is None:
                return
            commit_active(draft_ref.current)
            src_li = active
            # 抑制重渲染导致的 on_blur（旧 TextField 卸载）——与 _goto_quiet 一致，
            # 否则 on_blur 会用 stale closure 的 active 再次 commit_active + set_active(None)，
            # 虽然 commit 对同一 draft 幂等，但 set_active(None) 会触发额外重渲染，
            # 干扰刚设置的 outward_sel 状态（编辑光标起始跨段/跨行选区无效的根因）。
            suppress_blur.current = True
            set_active(None)
            set_active_seg(None)
            _set_outward_sel((src_li, cur_off, new_pos[0], new_pos[1]))
            return
        if current_outward is None:
            return
        a_li, a_off, act_li, act_off = current_outward
        new_pos = step_fn(act_li, act_off)
        if new_pos is None:
            return
        _set_outward_sel((a_li, a_off, new_pos[0], new_pos[1]))

    def clear_outward_sel() -> None:
        """取消向外选区（Esc、非 Shift 点击等）。"""
        if outward_sel is not None:
            _set_outward_sel(None)

    def on_extend_outward(target_li: int, target_off: int) -> None:
        """LineView Shift+Click / 拖动选区回调：起始或扩展向外选区。"""
        if outward_sel is None:
            # 无选区时：若有 active 编辑段，从编辑光标起始；否则从点击/拖动点起始
            if active is not None:
                _start_outward(target_li, target_off)
            else:
                # 非编辑模式或编辑模式但 on_blur 已清除 active：从点击/拖动点起始
                _start_outward_from_point(target_li, target_off, target_li, target_off)
        else:
            _extend_outward(target_li, target_off)

    def _delete_raw_range(start_li: int, start_off: int, end_li: int, end_off: int) -> None:
        """删除 raw 范围 [start, end)，跨行时合并边界行。光标定位到 start。"""
        _push_history()
        undo_push_pending.current = True
        try:
            if start_li == end_li:
                if not (0 <= start_li < len(document.lines)):
                    return
                line = document.lines[start_li]
                cur_raw = _line_raw(line)
                new_raw = cur_raw[:start_off] + cur_raw[end_off:]
                parser.reparse_line(line, new_raw)
                new_lines = list(document.lines)
            else:
                if not (0 <= start_li < len(document.lines) and 0 <= end_li < len(document.lines)):
                    return
                start_line = document.lines[start_li]
                end_line = document.lines[end_li]
                merged = _line_raw(start_line)[:start_off] + _line_raw(end_line)[end_off:]
                parser.reparse_line(start_line, merged)
                new_lines = document.lines[:start_li + 1] + document.lines[end_li + 1:]
        except Exception:
            return
        document.lines = new_lines  # 新列表对象触发 observable 通知
        mark_dirty()
        _set_outward_sel(None)
        set_active(None)
        set_active_seg(None)
        # 光标定位到删除起点
        if 0 <= start_li < len(document.lines):
            line = document.lines[start_li]
            cur_raw = _line_raw(line)
            if start_off < 0 or start_off > len(cur_raw):
                start_off = -1
            if start_off < 0:
                _goto(start_li, seg_idx=_first_content_seg(line), cursor_at=-1)
            else:
                target_seg, target_off = _locate_seg_by_raw_offset(line, start_off)
                _goto(start_li, seg_idx=target_seg, cursor_at=target_off)

    def handle_outward_delete() -> None:
        """BackSpace/Delete on outward_sel：删除选区。"""
        if outward_sel is None:
            return
        a_li, a_off, b_li, b_off = outward_sel
        # 归一化为 (start < end)
        if (a_li, a_off) > (b_li, b_off):
            a_li, a_off, b_li, b_off = b_li, b_off, a_li, a_off
        _delete_raw_range(a_li, a_off, b_li, b_off)

    async def handle_outward_cut() -> None:
        """Ctrl+X on outward_sel：复制 Markdown 到剪贴板，再删除选区。"""
        if outward_sel is None:
            return
        a_li, a_off, b_li, b_off = outward_sel
        if (a_li, a_off) > (b_li, b_off):
            a_li, a_off, b_li, b_off = b_li, b_off, a_li, a_off
        # 提取选区 raw 文本作为 Markdown
        md_parts: list[str] = []
        for li in range(a_li, b_li + 1):
            if not (0 <= li < len(document.lines)):
                break
            line = document.lines[li]
            raw = _line_raw(line)
            s = a_off if li == a_li else 0
            e = b_off if li == b_li else len(raw)
            md_parts.append(raw[s:e])
        md = "\n".join(md_parts)
        clipboard = clipboard_ref.current if clipboard_ref is not None else None
        if clipboard is not None and md:
            try:
                await clipboard.set(md)
            except Exception:
                pass
        _delete_raw_range(a_li, a_off, b_li, b_off)

    def handle_segment_cut_sync() -> str | None:
        """编辑态段内 Ctrl+X 同步部分：捕获选区、剪切 draft、立即提交、光标重定位。

        必须同步执行（不通过 page.run_task），在原生 TextField 剪切之前完成。
        原因：原生剪切会触发 on_change_draft 更新 draft_ref.current，但
        on_selection_change 可能尚未更新 cursor_ref（光标仍为旧选区 base/extent），
        若异步执行会读到「已剪切 draft + 旧光标选区」→ 再次剪切 → 双份剪切 bug。

        同步执行后，draft_ref.current 已更新为剪切后文本；原生剪切产生的
        on_change 事件因值相等被 on_change_draft 去重逻辑跳过（见 L812-813）。

        返回选中文本供异步写入剪贴板（handle_segment_cut_clipboard）。
        """
        if active is None:
            return None
        cur = cursor_ref.current
        if cur.base == cur.extent:
            return None  # 段内无选区，交由 TextField 原生
        start = min(cur.base, cur.extent)
        end = max(cur.base, cur.extent)
        selected = draft_ref.current[start:end]
        new_draft = draft_ref.current[:start] + draft_ref.current[end:]
        seg_idx = active_seg if active_seg is not None else 0
        _push_history()
        undo_push_pending.current = True
        commit_active(new_draft)
        if 0 <= active < len(document.lines):
            _goto(active, seg_idx=seg_idx, cursor_at=start, skip_commit=True)
        return selected

    async def handle_segment_cut_clipboard(text: str) -> None:
        """编辑态段内 Ctrl+X 异步部分：将选中文本写入剪贴板。"""
        if not text:
            return
        clipboard = clipboard_ref.current if clipboard_ref is not None else None
        if clipboard is not None:
            try:
                await clipboard.set(text)
            except Exception:
                pass

    # ---- TOC 跳转 ----
    def jump_to(li: int):
        if not (0 <= li < len(document.lines)):
            return
        # scroll_to 是 async 方法，通过 run_task 调度执行
        page = ft.context.page
        if page is not None and (lv := list_view_ref.current) is not None:
            page.run_task(lv.scroll_to, scroll_key=f"line-{li}")
        _goto(li, seg_idx=_first_content_seg(document.lines[li]), cursor_at=0)

    # ---- 同步导航接口给外层 on_key（nav_ref）----
    def _get_cursor_row_col() -> tuple[int, int]:
        """返回当前光标 (row, col)，供外层状态栏使用。

        行级编辑：col = cursor_ref.current.extent + 1（raw 偏移 +1 转 1-indexed）。
        """
        if active is not None and 0 <= active < len(document.lines):
            row = active + 1
            col = cursor_ref.current.extent + 1
        else:
            row = cursor_line + 1
            col = 1
        return row, col

    if nav_ref is not None:
        nav_ref.current = EditorActions(
            active=active,
            active_seg=active_seg,
            draft=draft,
            active_line=document.lines[active]
            if active is not None and 0 <= active < len(document.lines)
            else None,
            raw_mode=raw_mode,
            cursor_ref=cursor_ref,
            selection_text_ref=selection_text_ref,
            move_left=move_left_cross,
            move_right=move_right_cross,
            move_home=move_home,
            move_end=move_end,
            move_line_start=move_line_start,
            move_line_end=move_line_end,
            move_up=move_up,
            move_down=move_down,
            backspace_core=backspace_core,
            delete_core=delete_core,
            indent_or_outdent=indent_or_outdent,
            handle_paste=handle_paste,
            handle_cut=handle_cut,
            handle_delete_selection=handle_delete_selection,
            compute_markdown_from_text=lambda text: (
                parser.compute_markdown_from_text(document.lines, text)
            ),
            undo=undo,
            redo=redo,
            jump_to_line=jump_to,
            toggle_raw=toggle_raw,
            toggle_focus_mode=toggle_focus_mode,
            exit_code_block=code_editor.exit,
            handle_tab_in_code=code_editor.tab,
            handle_backspace_in_code=code_editor.backspace,
            handle_delete_in_code=code_editor.delete,
            handle_enter_in_code=code_editor.enter,
            get_cursor_row_col=_get_cursor_row_col,
            outward_sel=outward_sel,
            shift_pressed_ref=shift_pressed_ref,
            ctrl_pressed_ref=ctrl_pressed_ref,
            extend_outward_left=lambda: _extend_outward_step(_step_left),
            extend_outward_right=lambda: _extend_outward_step(_step_right),
            extend_outward_up=lambda: _extend_outward_step(_step_up),
            extend_outward_down=lambda: _extend_outward_step(_step_down),
            handle_outward_cut=handle_outward_cut,
            handle_outward_delete=handle_outward_delete,
            handle_segment_cut_sync=handle_segment_cut_sync,
            handle_segment_cut_clipboard=handle_segment_cut_clipboard,
            clear_outward_sel=clear_outward_sel,
        )

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
    # 内容区可用宽度 = 内容最大宽度 - 左右内边距，传给 LineView 用于编辑态宽度限位
    content_width = content_max_width - 2 * content_padding

    def _line_highlight_range(li: int) -> tuple[int, int] | None:
        """计算某行的向外选区高亮范围 (start_off, end_off)，无则 None。"""
        if outward_sel is None:
            return None
        a_li, a_off, b_li, b_off = outward_sel
        if (a_li, a_off) > (b_li, b_off):
            a_li, a_off, b_li, b_off = b_li, b_off, a_li, a_off
        if li < a_li or li > b_li:
            return None
        if not (0 <= li < len(document.lines)):
            return None
        line_raw_len = len(_line_raw(document.lines[li]))
        if li == a_li and li == b_li:
            return (a_off, b_off)
        if li == a_li:
            return (a_off, line_raw_len)
        if li == b_li:
            return (0, b_off)
        return (0, line_raw_len)

    line_controls = []
    i = 0
    while i < len(document.lines):
        line = document.lines[i]
        is_act = active is not None and active == i
        # 段级编辑：使用真实 active_seg state（不再覆盖为 0）
        active_seg_val = active_seg if is_act else None
        if line.block_type == BlockType.TABLE:
            table_start = i
            while (
                i + 1 < len(document.lines)
                and document.lines[i + 1].block_type == BlockType.TABLE
            ):
                i += 1
            table_end = i
            table_is_active = (
                active is not None and table_start <= active <= table_end
            )
            line_controls.append(
                TableView(
                    key=f"table-{table_start}",
                    lines=document.lines,
                    line_idx=table_start,
                    active_line_idx=active if table_is_active else None,
                    active_cell_idx=table_selected_cell if table_is_active else None,
                    active_seg=0 if table_is_active else None,
                    draft=draft,
                    on_activate=activate,
                    on_change_draft=on_change_draft,
                    on_submit=on_submit,
                    on_blur=on_blur,
                    on_selection_change=on_selection_change if table_is_active else None,
                    initial_cursor=cursor_pos if table_is_active else -1,
                    nav_seq=nav_seq if table_is_active else 0,
                    field_ref=active_field_ref if table_is_active else None,
                    content_width=content_width,
                )
            )
        else:
            line_controls.append(
                LineView(
                    key=f"line-{i}",
                    line=line,
                    line_idx=i,
                    active_seg=active_seg_val,
                    draft=draft,
                    on_activate=activate,
                    on_change_draft=on_change_draft,
                    on_submit=on_submit,
                    on_blur=on_blur,
                    on_selection_change=on_selection_change if is_act else None,
                    on_toggle_task=toggle_task,
                    toc_entries=toc_entries,
                    on_jump_to=jump_to,
                    on_change_lang=change_lang,
                    on_lang_focus=suppress_blur_for_lang,
                    on_suppress_blur=suppress_blur_for_click,
                    initial_cursor=cursor_pos if is_act else -1,
                    nav_seq=nav_seq if is_act else 0,
                    field_ref=active_field_ref if is_act else None,
                    content_width=content_width,
                    line_height=line_height,
                    on_cursor_sync=_on_cursor_sync if is_act else None,
                    is_current_line=is_act,
                    clipboard_ref=clipboard_ref,
                    outward_range=_line_highlight_range(i),
                    on_extend_outward=on_extend_outward,
                    shift_pressed_ref=shift_pressed_ref,
                    on_clear_outward=clear_outward_sel,
                )
            )
        i += 1

    # ---- 工具区：菜单 | 工具栏 | 原文切换 + 导出 ----
    def _tool_area():
        """单行工具区：菜单 | 分隔 | Toolbar | 弹性 | 原文/导出/聚焦/主题。

        文件名与状态信息移到底部状态栏（_footer），工具区只保留操作按钮，保持简洁。
        """
        menu_items = [
            ft.PopupMenuItem(content="新建", on_click=lambda e: (on_new or _noop)()),
            ft.PopupMenuItem(
                content="打开...", on_click=lambda e: (on_open or _noop)()
            ),
            ft.PopupMenuItem(content="保存", on_click=lambda e: (on_save or _noop)()),
            ft.PopupMenuItem(),
            ft.PopupMenuItem(
                content="设置", on_click=lambda e: (on_open_settings or _noop)()
            ),
        ]
        if not show_toolbar:
            return ft.Container(height=0)
        return ft.Container(
            bgcolor=ft.Colors.with_opacity(0.96, c.toolbar_bg),
            border=only_border(bottom=ft.BorderSide(1, c.border)),
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            content=ft.Row(
                controls=[
                    ft.PopupMenuButton(
                        icon=ft.Icons.MENU,
                        tooltip="文件菜单",
                        items=menu_items,
                    ),
                    _tb_divider(),
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
                    ),
                    ft.Container(expand=True),
                    _btn(
                        ft.Icons.VISIBILITY if not raw_mode else ft.Icons.EDIT,
                        "原文模式" if not raw_mode else "返回编辑",
                        toggle_raw,
                        toggle_on=raw_mode,
                    ),
                    _btn(
                        ft.Icons.FILE_DOWNLOAD,
                        "导出 HTML",
                        on_export or _noop,
                    ),
                    _btn(
                        ft.Icons.CENTER_FOCUS_STRONG,
                        "聚焦模式",
                        toggle_focus_mode,
                    ),
                    _btn(
                        ft.Icons.DARK_MODE
                        if theme_mode == ft.ThemeMode.LIGHT
                        else ft.Icons.LIGHT_MODE,
                        "切换暗色" if theme_mode == ft.ThemeMode.LIGHT else "切换亮色",
                        on_toggle_theme or _noop,
                    ),
                    _btn(
                        ft.Icons.SETTINGS,
                        "设置  Ctrl+,",
                        on_open_settings or _noop,
                    ),
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    # ---- 页脚已提升到 App 层，贯穿侧边栏 + 编辑区全宽 ----

    def _on_key_down(e):
        key = (getattr(e, "key", "") or "").lower()
        # Flet 0.86.2 KeyDownEvent 只有 key 字段，无 ctrl/shift/meta。
        # 用 shift_pressed_ref 跟踪 Shift 状态（_on_key_up 释放时清零）。
        # KeyDownEvent.key 对 Shift 可能返回 "Shift Left" / "Shift Right"，
        # 用 startswith 兼容所有变体。主同步源为 KeyDispatcher.handle() 的 e.shift。
        if key.startswith("shift"):
            shift_pressed_ref.current = True
        if key.startswith("control"):
            ctrl_pressed_ref.current = True
        # 兼容旧代码：用 shift_pressed_ref 替代失效的 e.shift
        shift = shift_pressed_ref.current
        ctrl = False  # KeyDownEvent 无 ctrl 字段；Ctrl 组合键由 page.on_keyboard_event 处理
        if active is None:
            return
        if key == "tab" and active is not None and not ctrl_pressed_ref.current:
            li = active
            if 0 <= li < len(document.lines) and document.lines[li].block_type == BlockType.TABLE:
                _table_tab(-1 if shift else 1)
                return
        if key in ("enter", "numpad enter") and active is not None:
            li = active
            if 0 <= li < len(document.lines) and document.lines[li].block_type == BlockType.TABLE:
                _table_enter()
                return
        li = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if line.block_type != BlockType.CODE:
            return
        if key in ("enter", "numpad enter") and ctrl:
            code_editor.exit()
        elif key == "escape":
            code_editor.exit()
        elif key == "tab" and not ctrl_pressed_ref.current:
            # Ctrl+Tab 由 KeyDispatcher 顶部拦截为标签切换，此处跳过缩进
            code_editor.tab(-1 if shift else 1)
        elif key == "backspace":
            code_editor.backspace()
        elif key in ("enter", "numpad enter"):
            code_editor.enter()

    def _on_key_up(e):
        """跟踪 Shift 释放：Flet 0.86.2 KeyUpEvent 仅 key 字段，无修饰键。

        page.on_keyboard_event 仅在 key-down 触发，无法捕获 Shift 单独释放；
        KeyboardListener.on_key_up 是 Shift 释放信号的唯一来源。
        """
        key = (getattr(e, "key", "") or "").lower()
        if key.startswith("shift"):
            shift_pressed_ref.current = False
        if key.startswith("control"):
            ctrl_pressed_ref.current = False

    return ft.KeyboardListener(
        autofocus=True,
        on_key_down=_on_key_down,
        on_key_up=_on_key_up,
        content=ft.Column(
            controls=[
                _tool_area(),
                _raw_editor()
                if raw_mode
                else ft.SelectionArea(
                    expand=True,
                    on_change=on_selection_area_change,
                    content=ft.Container(
                        content=ft.Container(
                            content=ft.Column(
                                ref=list_view_ref,
                                controls=line_controls,
                                expand=True,
                                spacing=0,  # 行间无间距：避免行间空白死区导致点击无效
                                scroll=ft.ScrollMode.AUTO,
                            ),
                            width=content_max_width,
                            alignment=ft.Alignment.TOP_LEFT,
                        ),
                        expand=True,
                        alignment=ft.Alignment.TOP_CENTER,
                        bgcolor=c.bg,
                        padding=ft.Padding.symmetric(
                            horizontal=content_padding, vertical=content_padding_top
                        ),
                    ),
                ),
            ],
            expand=True,
        ),
    )
