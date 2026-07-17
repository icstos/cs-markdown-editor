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
from styles import (
    FONT_MAIN,
    FONT_MONO,
    _current_colors,
    only_border,
)
from views.line_view import LineView
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

# 不参与跨段/跨行光标导航的块类型（整块编辑，方向键在块内处理）
_NO_NAV_BLOCKS = (BlockType.CODE, BlockType.HR, BlockType.MATH, BlockType.TOC)


def _line_raw(line: Line) -> str:
    """整行 Markdown 源码（段 raw 拼接，与 line.raw 一致）。"""
    return line.raw or "".join(s.raw for s in line.segments)


def _heading_edit(line: Line) -> bool:
    return line.block_type == BlockType.HEADING


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
    active, set_active = ft.use_state(None)  # (line_idx, seg_idx) | None
    draft, set_draft = ft.use_state("")  # 当前编辑段文本
    cursor_line, set_cursor_line = ft.use_state(0)
    # 光标跟踪（ref 而非 state）：避免 on_selection_change 触发重渲染导致光标跳动
    # 仅在跨段导航/块切换时通过 _sync_cursor 重置；on_key 经 nav_ref 读取
    cursor_ref = ft.use_ref({"base": 0, "extent": 0, "draft_len": 0})
    # applied_cursor：_on_focus 设置光标后记录的目标位置（-1=未设置）。
    # 用于 on_selection_change 识别并丢弃 Flutter 聚焦时默认触发的 stale 段尾事件：
    # Flutter 先触发 on_focus（设置正确光标），再触发 on_selection_change(段尾)，
    # 若不拦截会覆盖 cursor_ref 的正确值，导致 Delete/Backspace 误判光标位置。
    applied_cursor = ft.use_ref(-1)
    # draft_ref：同步镜像 draft 状态。闭包的 draft 在 set_draft 后到下次渲染前是 stale 的，
    # 持续 Delete 时 delete_core 需在渲染前就读到最新 draft 才能正确删除字符。
    draft_ref = ft.use_ref("")
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
    # 撤销 / 重做栈
    history_ref = ft.use_ref(EditHistory(max_size=50))
    restoring = ft.use_ref(False)
    undo_push_pending = ft.use_ref(True)

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

    def _set_draft(value: str):
        """同步更新 draft_ref 并排队 set_draft 重渲染。

        闭包的 draft 变量在 set_draft 后到下次渲染前是 stale 的，持续 Delete
        时 delete_core 需立即读到最新 draft 才能正确删除字符；同时 on_change_draft
        依赖 draft_ref 识别并跳过原生 Delete 产生的同值 on_change，避免重复 set_draft。
        """
        draft_ref.current = value
        set_draft(value)

    def mark_dirty():
        document.dirty = True
        if on_dirty_change:
            on_dirty_change(True)

    def _make_snapshot() -> EditorSnapshot:
        md = raw_draft if raw_mode else parser.serialize(document)
        return EditorSnapshot(
            markdown=md,
            active=active,
            draft=draft_ref.current,
            cursor_base=cursor_ref.current["base"],
            cursor_extent=cursor_ref.current["extent"],
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
                    li, si = snap.active
                    if 0 <= li < len(document.lines):
                        line = document.lines[li]
                        if _heading_edit(line):
                            target_si = 0
                        else:
                            target_si = min(si, max(0, len(line.segments) - 1))
                        _set_draft(snap.draft)
                        set_active((li, target_si))
                        set_cursor_line(li)
                        cursor_at = snap.cursor_base
                        set_cursor_pos(cursor_at if cursor_at >= 0 else -1)
                        _sync_cursor(snap.draft, cursor_at)
                        set_nav_seq(nav_seq + 1)
                    else:
                        set_active(None)
                        _set_draft("")
                else:
                    set_active(None)
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

    def _draft_for(li: int, si: int) -> str:
        if 0 <= li < len(document.lines):
            line = document.lines[li]
            if _heading_edit(line):
                return _line_raw(line)
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
        elif _heading_edit(line):
            parser.reparse_line(line, raw)
        else:
            if si < len(line.segments):
                line.segments[si].raw = raw
            full = "".join(s.raw for s in line.segments)
            parser.reparse_line(line, full)
        mark_dirty()

    # ---- 激活段（统一的状态切换入口）----
    def _goto(li: int, si: int, cursor_at: int = -1, skip_commit: bool = False):
        """跨段/激活目标段：先提交当前段，再切换 draft+active，递增 nav_seq
        触发 TextField key 重建以重新 autofocus。cursor_at: -1=段尾, 0=段首。

        skip_commit=True 跳过提交当前段——用于当前行即将被删除/移位的场景
        （如行首 Backspace 合并），避免把草稿提交到移位后的错误行。
        """
        if not skip_commit and active is not None and active != (li, si):
            commit_active(draft_ref.current)
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if not (0 <= si < len(line.segments)):
            return
        if _heading_edit(line):
            new_draft = _line_raw(line)
            if cursor_at < 0:
                cursor_at = len(new_draft)
            elif si > 0:
                cursor_at = sum(len(line.segments[i].raw) for i in range(si)) + cursor_at
            cursor_at = min(max(cursor_at, 0), len(new_draft))
            _set_draft(new_draft)
            set_active((li, 0))
            set_cursor_line(li)
            set_cursor_pos(cursor_at)
            _sync_cursor(new_draft, cursor_at)
            set_nav_seq(nav_seq + 1)
            return
        new_draft = _draft_for(li, si)
        _set_draft(new_draft)
        set_active((li, si))
        set_cursor_line(li)
        set_cursor_pos(cursor_at)
        _sync_cursor(new_draft, cursor_at)
        set_nav_seq(nav_seq + 1)

    def activate(li: int, si: int, cursor_at: int = -1):
        _goto(li, si, cursor_at=cursor_at)

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
        if _heading_edit(line):
            if cursor_ref.current["base"] > 0:
                return
            if li > 0:
                prev = document.lines[li - 1]
                _goto(li - 1, max(0, len(prev.segments) - 1))
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
        if _heading_edit(line):
            if cursor_ref.current["extent"] < len(draft):
                return
            if li < len(document.lines) - 1:
                _goto(li + 1, 0, cursor_at=0)
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
        line = document.lines[li]
        if _heading_edit(line):
            _goto(li, 0, cursor_at=0)
            return
        _goto(li, 0)

    def move_end():
        """End：跳到当前行最后一个段（段尾由 autofocus 落点）。"""
        if active is None:
            return
        li, _ = active
        if li >= len(document.lines) or _nav_blocked(document.lines[li]):
            return
        line = document.lines[li]
        if _heading_edit(line):
            _goto(li, 0, cursor_at=-1)
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

    def move_line_start():
        if active is None:
            return
        li, _ = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if _nav_blocked(line):
            return
        _goto(li, 0, cursor_at=0)

    def move_line_end():
        if active is None:
            return
        li, _ = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if _nav_blocked(line):
            return
        _goto(li, max(0, len(line.segments) - 1), cursor_at=-1)

    def _first_content_index(line: Line) -> int:
        for i, s in enumerate(line.segments):
            if s.seg_type not in (SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX):
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
        li, _ = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O):
            _indent_list_line(line, delta)
            _goto(li, _first_content_index(line), cursor_at=0)
        elif line.block_type == BlockType.QUOTE:
            _toggle_quote_level(line, delta)
            _goto(li, _first_content_index(line), cursor_at=0)

    def backspace_core():
        if active is None:
            return
        li, si = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        # 代码块 / 块级公式 / 分隔线 / 目录：整块编辑，行首 BackSpace 不处理
        if line.block_type in (BlockType.CODE, BlockType.MATH, BlockType.HR, BlockType.TOC):
            return
        # 光标不在段首：交由 TextField 自身删除
        if cursor_ref.current["extent"] > 0:
            return
        # 行内段非首段（si > 0）段首 BackSpace：跳到上一段末尾，便于继续删除
        if si > 0:
            _goto(li, si - 1, cursor_at=-1)
            return
        # 行首（si == 0）且非首行：与前一行合并（删除上一行行尾换行符），
        # 所有块类型（标题/列表/引用/段落）行为一致
        if li <= 0:
            return
        _push_history()
        undo_push_pending.current = False
        # 先提交当前段草稿（此时当前行仍有效），确保合并内容含最新输入
        commit_active(draft_ref.current)
        prev = document.lines[li - 1]
        # 前一行是围栏块（代码/公式/分隔线/目录）：无法合并，跳到其末尾
        if prev.block_type in (BlockType.CODE, BlockType.MATH, BlockType.HR, BlockType.TOC):
            suppress_blur.current = True
            _goto(li - 1, max(0, len(prev.segments) - 1), cursor_at=-1)
            return
        # 前一行含行内内容（段落/标题/列表/引用）：合并当前行内容到前一行末尾
        prev_raw = _line_raw(prev)
        junction = len(prev_raw)
        merged = prev_raw + _line_raw(document.lines[li])
        parser.reparse_line(prev, merged)
        document.lines = document.lines[:li] + document.lines[li + 1:]
        mark_dirty()
        suppress_blur.current = True
        # 光标落在合并点：定位包含 junction 偏移的段及段内偏移
        if _heading_edit(prev):
            # 标题以整行 raw 为 draft，cursor_at 即整行偏移
            _goto(li - 1, 0, cursor_at=min(junction, len(_line_raw(prev))), skip_commit=True)
            return
        target_si = max(0, len(prev.segments) - 1)
        seg_off = len(prev.segments[target_si].raw)
        offset = 0
        for idx, seg in enumerate(prev.segments):
            seg_len = len(seg.raw)
            if offset + seg_len >= junction:
                target_si = idx
                seg_off = max(0, junction - offset)
                break
            offset += seg_len
        _goto(li - 1, target_si, cursor_at=seg_off, skip_commit=True)

    def delete_core():
        """行尾 Delete：与下一行合并（删除当前行行尾换行符），与行首 BackSpace 对称。

        逻辑与 backspace_core 镜像：
        - 光标不在段尾 → 交由 TextField 原生 Delete 删除下一个字符
        - 非末段段尾 → 跳到下一段首部（便于继续删除）
        - 末段段尾且非末行 → 与下一行合并
        - 下一行是围栏块 → 跳到下一行首部
        """
        if active is None:
            return
        li, si = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        # 代码块 / 块级公式 / 分隔线 / 目录：整块编辑，行尾 Delete 不处理
        if line.block_type in (BlockType.CODE, BlockType.MATH, BlockType.HR, BlockType.TOC):
            return
        # 光标不在段尾：交由 TextField 原生 Delete 处理（正常删除下一个字符）
        # 使用 len(draft_ref.current) 而非 cursor_ref["draft_len"]：
        # on_selection_change 不可靠，可能导致 cursor_ref["draft_len"] stale；
        # draft_ref 通过 on_change_draft 同步更新，始终是最新的。
        if cursor_ref.current["extent"] < len(draft_ref.current):
            return
        # 行内段非末段（si < len-1）段尾 Delete：直接删除下一段的首字符，
        # 而非仅跳转光标。对于短小的 Markdown 语法段（如 **、*），逐段跳转
        # 会让用户感觉被"跳过"，直接删除字符更符合预期。
        # 标题以整行 raw 为 draft（si 恒为 0），跳过段间跳转。
        if not _heading_edit(line) and si < len(line.segments) - 1:
            _push_history()
            undo_push_pending.current = False
            commit_active(draft_ref.current)
            next_seg = line.segments[si + 1]
            if next_seg.raw:
                next_seg.raw = next_seg.raw[1:]
            full = "".join(s.raw for s in line.segments)
            parser.reparse_line(line, full)
            mark_dirty()
            suppress_blur.current = True
            new_si = si + 1 if next_seg.raw else si
            _goto(li, new_si, cursor_at=0, skip_commit=True)
            return
        # 行尾（末段段尾）且非末行：与下一行合并（删除当前行行尾换行符），
        # 所有块类型（标题/列表/引用/段落）行为一致
        if li >= len(document.lines) - 1:
            return
        _push_history()
        undo_push_pending.current = False
        # 先提交当前段草稿（此时当前行仍有效），确保合并内容含最新输入
        commit_active(draft_ref.current)
        line = document.lines[li]
        next_line = document.lines[li + 1]
        # 下一行是围栏块（代码/公式/分隔线/目录）：无法合并，跳到其首部
        if next_line.block_type in (BlockType.CODE, BlockType.MATH, BlockType.HR, BlockType.TOC):
            suppress_blur.current = True
            _goto(li + 1, 0, cursor_at=0)
            return
        # 下一行含行内内容（段落/标题/列表/引用）：合并下一行内容到当前行末尾
        current_raw = _line_raw(line)
        junction = len(current_raw)
        merged = current_raw + _line_raw(next_line)
        parser.reparse_line(line, merged)
        document.lines = document.lines[:li + 1] + document.lines[li + 2:]
        mark_dirty()
        suppress_blur.current = True
        # 光标落在合并点：定位包含 junction 偏移的段及段内偏移
        if _heading_edit(line):
            _goto(li, 0, cursor_at=min(junction, len(_line_raw(line))), skip_commit=True)
            return
        target_si = max(0, len(line.segments) - 1)
        seg_off = len(line.segments[target_si].raw)
        offset = 0
        for idx, seg in enumerate(line.segments):
            seg_len = len(seg.raw)
            if offset + seg_len >= junction:
                target_si = idx
                seg_off = max(0, junction - offset)
                break
            offset += seg_len
        _goto(li, target_si, cursor_at=seg_off, skip_commit=True)

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
        cursor_ref.current["base"] = pos
        cursor_ref.current["extent"] = pos
        cursor_ref.current["draft_len"] = actual_len
        applied_cursor.current = pos
        if nav_ref is not None and nav_ref.current is not None:
            nav_ref.current["base"] = pos
            nav_ref.current["extent"] = pos
            nav_ref.current["draft_len"] = actual_len

    def on_selection_change(e):
        """跟踪光标位置（extent/base），供 on_key 判断左右越界。

        使用 ref 而非 set_state，避免输入时触发重渲染导致光标跳动。
        同时直接更新 nav_ref.current，确保 on_key 读到最新值。

        Stale 事件拦截：Flutter 聚焦时先触发 on_focus 设置正确光标（如行合并后
        的 junction 位置），再触发 on_selection_change(默认段尾=draft_len)。
        若 applied_cursor 已记录目标位置且与段尾不符，说明这是 stale 事件，丢弃。

        关键：只在 TextField 值长度未变时拦截（len(value)==draft_len），
        确保 stale 事件（值未变）被拦截，而用户删除到段尾的正常事件（值变短）
        不被错误拦截——否则 cursor_ref.draft_len 不更新，delete_core 误判
        光标不在段尾，持续 Delete 到段尾后失效。
        """
        if (sel := e.selection) is not None:
            if (applied_cursor.current >= 0
                    and applied_cursor.current != sel.extent_offset
                    and sel.extent_offset == len(e.control.value)
                    and len(e.control.value) == cursor_ref.current["draft_len"]):
                # stale 段尾事件：_on_focus 已设置正确光标，值未变，忽略此覆盖
                applied_cursor.current = -1
                return
            applied_cursor.current = -1
            cursor_ref.current["base"] = sel.base_offset
            cursor_ref.current["extent"] = sel.extent_offset
            cursor_ref.current["draft_len"] = len(e.control.value)
            if nav_ref is not None and nav_ref.current is not None:
                nav_ref.current["base"] = sel.base_offset
                nav_ref.current["extent"] = sel.extent_offset
                nav_ref.current["draft_len"] = len(e.control.value)

    def on_change_draft(value: str):
        _maybe_push_draft_history()
        _set_draft(value)
        # 同步更新 cursor_ref["draft_len"] 和 nav_ref["draft_len"] 作为安全网：
        # on_selection_change 不可靠，可能导致这些值 stale。
        # 每次 draft 变更都同步更新，确保所有读取 draft_len 的地方（如 main.py
        # 的 ArrowRight 判断、backspace_core 的段首判断）都读到最新值。
        n = len(value)
        cursor_ref.current["draft_len"] = n
        if nav_ref is not None and nav_ref.current is not None:
            nav_ref.current["draft_len"] = n

    def toggle_raw():
        """在 WYSIWYG 编辑与原始 Markdown 文本间切换。

        进入原文模式：序列化当前文档为 raw_draft；
        返回编辑模式：重新解析 raw_draft 为行列表，替换 document.lines。
        """
        _push_history()
        undo_push_pending.current = True
        if not raw_mode:
            set_raw_draft(parser.serialize(document))
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

    def on_blur():
        if suppress_blur.current:
            suppress_blur.current = False
            return
        try:
            commit_active(draft_ref.current)
        finally:
            set_active(None)

    def handle_paste(clip_text: str, old_draft: str = ""):
        """处理多行粘贴：用 diff 定位粘贴位置，第一行留当前段，后续行插入为新行。

        单行 TextField（max_lines=1）会剥离换行符，导致粘贴的多行内容变为一行。
        本函数通过对比粘贴前后的 draft 定位粘贴文本，再用剪贴板原始多行文本重建。
        """
        if active is None or not clip_text or "\n" not in clip_text:
            return
        _push_history()
        undo_push_pending.current = True
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

        if _heading_edit(line):
            parser.reparse_line(line, new_raw)
        else:
            if si < len(line.segments):
                line.segments[si].raw = new_raw
            parser.reparse_line(line, "".join(s.raw for s in line.segments))
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
            if _heading_edit(last_line):
                _goto(last_li, 0, cursor_at=-1)
            else:
                target_si = max(0, len(last_line.segments) - 1)
                new_draft_val = (
                    last_line.segments[target_si].raw
                    if target_si < len(last_line.segments)
                    else ""
                )
                _set_draft(new_draft_val)
                set_active((last_li, target_si))
                set_cursor_line(last_li)
                set_cursor_pos(-1)
                _sync_cursor(new_draft_val)
                set_nav_seq(nav_seq + 1)
        else:
            suppress_blur.current = True
            if _heading_edit(line):
                new_draft_val = _line_raw(line)
                _set_draft(new_draft_val)
                set_active((li, 0))
                set_cursor_line(li)
                set_cursor_pos(-1)
                _sync_cursor(new_draft_val)
            else:
                _set_draft(new_raw)
                set_cursor_pos(-1)
                _sync_cursor(new_raw)
            set_nav_seq(nav_seq + 1)

    def on_submit(new_raw: str):
        if active is None:
            return
        _push_history()
        undo_push_pending.current = True
        li, si = active
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        # 代码块 / 块级公式内回车：仅更新 draft（多行输入，Shift+Enter 换行）
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
        split_pos = min(cursor_ref.current["extent"], len(new_raw))
        before = new_raw[:split_pos]
        after = new_raw[split_pos:]
        if _heading_edit(line):
            if not before.strip():
                parser.reparse_line(line, after.lstrip())
                mark_dirty()
                _goto(li + 1 if li + 1 < len(document.lines) else li, 0, cursor_at=0)
                return
            parser.reparse_line(line, before)
            new_line = parser.parse_markdown(after).lines[0]
            document.lines = (
                document.lines[: li + 1] + [new_line] + document.lines[li + 1 :]
            )
            mark_dirty()
            target_si = 0
            if new_line.segments:
                for i, s in enumerate(new_line.segments):
                    if s.seg_type not in (
                        SegType.HEADING_PREFIX,
                        SegType.LIST_PREFIX,
                        SegType.QUOTE_PREFIX,
                    ):
                        target_si = i
                        break
                else:
                    target_si = max(0, len(new_line.segments) - 1)
            suppress_blur.current = True
            _goto(li + 1, target_si, cursor_at=0)
            return
        if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O, BlockType.QUOTE):
            if not before.strip():
                stripped = after.lstrip()
                if line.block_type == BlockType.QUOTE:
                    stripped = stripped.lstrip("> ")
                parser.reparse_line(line, stripped)
                mark_dirty()
                _goto(li + 1 if li + 1 < len(document.lines) else li, 0, cursor_at=0)
                return
            if line.block_type == BlockType.LIST_UO and before.rstrip() in ("-", "*", "+"):
                parser.reparse_line(line, after.lstrip())
                mark_dirty()
                _goto(li + 1 if li + 1 < len(document.lines) else li, 0, cursor_at=0)
                return
            if line.block_type == BlockType.LIST_O and re.match(r'^\d+\.$', before.rstrip()):
                parser.reparse_line(line, after.lstrip())
                mark_dirty()
                _goto(li + 1 if li + 1 < len(document.lines) else li, 0, cursor_at=0)
                return
        cont_prefix = _next_line_raw(line)
        remaining = "".join(s.raw for s in line.segments[si + 1 :])
        current_full = "".join(s.raw for s in line.segments[:si]) + before
        parser.reparse_line(line, current_full)
        new_line = parser.parse_markdown(cont_prefix + after + remaining).lines[0]
        document.lines = (
            document.lines[: li + 1] + [new_line] + document.lines[li + 1 :]
        )
        mark_dirty()
        target_si = 0
        if new_line.segments:
            for i, s in enumerate(new_line.segments):
                if s.seg_type not in (
                    SegType.HEADING_PREFIX,
                    SegType.LIST_PREFIX,
                    SegType.QUOTE_PREFIX,
                ):
                    target_si = i
                    break
            else:
                target_si = max(0, len(new_line.segments) - 1)
        new_draft = (
            new_line.segments[target_si].raw
            if target_si < len(new_line.segments)
            else ""
        )
        suppress_blur.current = True
        _set_draft(new_draft)
        set_active((li + 1, target_si))
        set_cursor_line(li + 1)
        set_cursor_pos(0)
        _sync_cursor(new_draft, 0)
        set_nav_seq(nav_seq + 1)

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
        target_si = max(0, len(new_line.segments) - 1)
        _goto(li + 1, target_si)

    # ---- 工具栏：块类型切换 ----
    def set_block(block_type: BlockType, level: int = 0):
        li = active[0] if active is not None else cursor_line
        if not (0 <= li < len(document.lines)):
            return
        _push_history()
        undo_push_pending.current = True
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
        if line.block_type == BlockType.HEADING:
            _goto(li, 0)
        else:
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
            full = f"$$\n{formula}\n$$" if formula else "$$\n$$"
            parser.reparse_line(line, full)
        elif _heading_edit(line):
            parser.reparse_line(line, draft_val)
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
        _push_history()
        undo_push_pending.current = True
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
        _set_draft(seg.raw)
        _sync_cursor(seg.raw)

    # 别名：供工具栏调用
    def toggle_inline(seg_type: SegType):
        _toggle_seg(seg_type)

    def toggle_link():
        if active is None:
            return
        _push_history()
        undo_push_pending.current = True
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
        _set_draft(seg.raw)
        _sync_cursor(seg.raw)

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
        li = active[0]
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if line.block_type != BlockType.CODE:
            return
        # li == active[0]，draft 即当前编辑草稿；保持与原逻辑一致
        code = draft
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
        _push_history()
        undo_push_pending.current = True
        try:
            md = parser.compute_markdown_from_selections(document.lines, selections)
            if md:
                clipboard = clipboard_ref.current if clipboard_ref is not None else None
                if clipboard is not None:
                    await clipboard.set(md)
            new_lines, cursor_li, cursor_si, cursor_offset = parser.delete_selections(document.lines, selections)
        except Exception:
            return
        document.lines = new_lines
        mark_dirty()
        set_active(None)
        if 0 <= cursor_li < len(document.lines):
            # 激活光标段，cursor_at 为段内偏移（精确到剪切位置）
            _goto(cursor_li, cursor_si, cursor_at=cursor_offset)

    # ---- TOC 跳转 ----
    def jump_to(li: int):
        if not (0 <= li < len(document.lines)):
            return
        # scroll_to 是 async 方法，通过 run_task 调度执行
        page = ft.context.page
        if page is not None and (lv := list_view_ref.current) is not None:
            page.run_task(lv.scroll_to, scroll_key=f"line-{li}")
        _goto(li, 0)

    # ---- 同步导航接口给外层 on_key（nav_ref）----
    if nav_ref is not None:
        nav_ref.current = {
            "active": active,
            "extent": cursor_ref.current["extent"],
            "base": cursor_ref.current["base"],
            "draft_len": cursor_ref.current["draft_len"],
            "draft": draft,
            "active_line": document.lines[active[0]] if active is not None and 0 <= active[0] < len(document.lines) else None,
            "move_left": move_left_cross,
            "move_right": move_right_cross,
            "move_home": move_home,
            "move_end": move_end,
            "move_line_start": move_line_start,
            "move_line_end": move_line_end,
            "backspace_core": backspace_core,
            "delete_core": delete_core,
            "indent_or_outdent": indent_or_outdent,
            "move_up": move_up,
            "move_down": move_down,
            "compute_markdown_from_text": lambda text: (
                parser.compute_markdown_from_text(document.lines, text)
            ),
            "handle_paste": handle_paste,
            "handle_cut": handle_cut,
            "undo": undo,
            "redo": redo,
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
    # 内容区可用宽度 = 内容最大宽度 - 左右内边距，传给 LineView 用于编辑态宽度限位
    content_width = content_max_width - 2 * content_padding
    line_controls = [
        LineView(
            key=f"line-{i}",
            line=line,
            line_idx=i,
            active_seg=active[1]
            if (is_act := active is not None and active[0] == i)
            else None,
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
        )
        for i, line in enumerate(document.lines)
    ]

    # ---- 工具区：菜单 | 工具栏 | 原文切换 + 导出 ----
    def _tool_area():
        menu_items = [
            ft.PopupMenuItem(content="新建", on_click=lambda e: (on_new or _noop)()),
            ft.PopupMenuItem(
                content="打开...", on_click=lambda e: (on_open or _noop)()
            ),
            ft.PopupMenuItem(content="保存", on_click=lambda e: (on_save or _noop)()),
            ft.PopupMenuItem(),
            ft.PopupMenuItem(content="设置", on_click=lambda e: (on_open_settings or _noop)()),
        ]
        title = _file_name(file_path)
        state_text = "已修改" if document.dirty else "已保存"
        if not show_toolbar:
            return ft.Container(height=0)
        return ft.Container(
            bgcolor=c.toolbar_bg,
            border=only_border(bottom=ft.BorderSide(1, c.border)),
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.PopupMenuButton(
                                icon=ft.Icons.MENU,
                                tooltip="文件菜单",
                                items=menu_items,
                            ),
                            ft.Container(width=4),
                            ft.Column(
                                controls=[
                                    ft.Text(
                                        value=title,
                                        size=14,
                                        weight=ft.FontWeight.W_600,
                                        color=c.text,
                                        font_family=FONT_MAIN,
                                    ),
                                    ft.Row(
                                        controls=[
                                            ft.Text(
                                                value=state_text,
                                                size=11,
                                                color=c.muted,
                                                font_family=FONT_MAIN,
                                            ),
                                            ft.Container(
                                                width=6,
                                                height=6,
                                                border_radius=99,
                                                bgcolor="#35C759" if not document.dirty else "#FF9F0A",
                                            ),
                                            ft.Text(
                                                value="Ctrl+S 保存 · Ctrl+Z 撤销 · Ctrl+/ 原文",
                                                size=11,
                                                color=c.muted,
                                                font_family=FONT_MAIN,
                                            ),
                                        ],
                                        spacing=6,
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                ],
                                spacing=0,
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
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(height=8),
                    ft.Container(
                        content=Toolbar(
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
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    ),
                ],
                spacing=0,
            ),
        )

    # ---- 页脚：文件名 | 光标行列 + 字符数 ----
    def _footer():
        if active is not None and 0 <= active[0] < len(document.lines):
            li, si = active
            line = document.lines[li]
            col = (
                _logical_offset(line, si, cursor_ref.current["extent"]) + 1
                if 0 <= si < len(line.segments)
                else 1
            )
            row = li + 1
        else:
            row = cursor_line + 1
            col = 1
        char_count = len(parser.serialize(document))
        word_count = len(re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", parser.serialize(document)))
        fname = os.path.basename(file_path) if file_path else "未命名.md"
        return ft.Container(
            bgcolor=ft.Colors.with_opacity(0.02, c.text),
            border=only_border(top=ft.BorderSide(1, c.border)),
            padding=ft.Padding.symmetric(horizontal=16, vertical=6),
            content=ft.Row(
                controls=[
                    ft.Text(
                        value=("● " if document.dirty else "") + fname,
                        size=12,
                        color=c.muted,
                        font_family=FONT_MAIN,
                    ),
                    ft.Container(expand=True),
                    ft.Text(
                        value=f"行 {row}  列 {col}",
                        size=12,
                        color=c.muted,
                        font_family=FONT_MAIN,
                    ),
                    ft.Container(width=16),
                    ft.Text(
                        value=f"{word_count} 词",
                        size=12,
                        color=c.muted,
                        font_family=FONT_MAIN,
                    ),
                    ft.Container(width=12),
                    ft.Text(
                        value=f"{char_count} 字符",
                        size=12,
                        color=c.muted,
                        font_family=FONT_MAIN,
                    ),
                ],
            ),
        )

    return ft.Column(
        controls=[
            _tool_area(),
            _raw_editor()
            if raw_mode
            else ft.SelectionArea(
                expand=True,
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
                    padding=ft.Padding.symmetric(horizontal=content_padding, vertical=content_padding_top),
                ),
            ),
            _footer() if show_footer else ft.Container(height=0),
        ],
        expand=True,
    )
