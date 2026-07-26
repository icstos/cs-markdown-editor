"""编辑器根组件：Stack 双层叠加光标级实时渲染（Typora 式 WYSIWYG）。

状态分层：
- document：observable Document（行列表 + 文件元信息）
- cursor_li / cursor_off：光标位置（激活行号 + 行级 raw 偏移）
- nav_seq：仅撤销/重做等强制重建场景递增（同行输入不递增，保 IME 组合态）

编辑流（光标级，无段级编辑态，IME 友好）：
- 点击渲染层 → hit_test(x) → raw_off → set_cursor(li, off) → 重渲染
  → use_effect 调 cursor_field.focus() → 透明 TextField 聚焦，光标在像素位置闪烁
- 输入字符 → TextField.on_change(value) → handle_char_input（3 分支：ignore/replace/append）
  → line.raw 插入 value → parser.reparse_line → cursor_ref.reset(off+len(value))
  → 不递增 nav_seq（key 基于 li+nav_seq，同行输入 key 不变 → 不重建 → IME 保持）
  → 不调用 set_cursor_off（避免重渲染打断 IME），cursor_off 在 _end_input_session 同步
  → use_effect([clear_value_seq]) 异步清空 TextField 内部 value → 准备下次输入
  → 渲染层 Text 显示新内容，TextField 重新定位到新光标位置

围栏岛屿（CODE/TABLE/MATH/HR/TOC）：自管理独立可编辑控件，不进入 Stack。

依赖项：
- models / parser / styles（数据与样式）
- core.actions / core.cursor / core.history（编辑器核心状态容器）
- utils.segment_helpers.WRAP_SYNTAX（行内格式包裹标记，统一来源）
- views.line_view / views.table_view / views.toolbar（子视图）
"""

import asyncio
import os
import re
from collections.abc import Callable

import flet as ft

from core.actions import EditorActions
from core.cursor import CursorState
from core.history import EditHistory, EditorSnapshot, LineEditSnapshot
from models import BlockType, Document, Line, Segment, SegType
import parser
from styles import (
    FONT_MAIN,
    FONT_MONO,
    Spacing,
    _current_colors,
    block_text_size,
    only_border,
)
from utils.segment_helpers import WRAP_SYNTAX
from views.line_view import LineView
from views.table_view import TableView, _join_row
from views.toolbar import Toolbar, _btn, _divider as _tb_divider

# 高频编辑路径用原子化重解析（仅触发 1 次 observable 通知，替代 reparse_line 的 2-7 次）
_reparse_atomic = parser.reparse_line_atomic


def _noop() -> None:
    pass


# 围栏块：自管理独立岛屿，不参与光标导航/合并
_FENCE_BLOCKS = (BlockType.CODE, BlockType.MATH, BlockType.HR, BlockType.TOC, BlockType.TABLE)

_ALIGN_RE_TABLE = re.compile(r"^:?-{3,}:?$")


def _is_fence(line: Line) -> bool:
    """围栏块判断：CODE / MATH / HR / TOC / TABLE。"""
    return line.block_type in _FENCE_BLOCKS


def _line_raw(line: Line) -> str:
    """整行 Markdown 源码。"""
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
    """回车续行：列表续列表（含任务/有序递增），否则空段落。"""
    if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O):
        indent_sp = " " * (line.level or 0)
        prefix = line.segments[0].raw if line.segments else "- "
        body = prefix.lstrip()
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
    shortcut_mgr=None,
):
    c = _current_colors()
    settings = settings or {}
    content_max_width = settings.get("content_max_width", 920)
    content_padding = settings.get("content_padding", 36)
    content_padding_top = settings.get("content_padding_top", 24)
    show_footer = settings.get("show_footer", True)
    body_font_size = settings.get("body_font_size", 16)
    line_height = settings.get("line_height", 1.6)
    show_toolbar = settings.get("show_toolbar", True)

    # ============ 状态：光标级（替代 active/active_seg/draft）============
    cursor_li, set_cursor_li = ft.use_state(None)  # 激活行号 | None（浏览态）
    cursor_off, set_cursor_off = ft.use_state(0)  # 行级 raw 偏移 0..len(line.raw)
    nav_seq, set_nav_seq = ft.use_state(0)  # 仅撤销/重做递增，强制 TextField 重建
    cursor_line, set_cursor_line = ft.use_state(0)  # 最近交互行（供工具栏块级操作）
    cursor_field_ref = ft.use_ref(None)  # 透明 cursor TextField 引用
    # IME 输入会话：on_change 期间不清空 TextField value，用"增量式"编辑同步文档
    # value 从 "w"→"wq"→"你"（IME 组合），文档中对应文本同步替换
    # li/start_off/last_value 三字段足够；reparse_line 后 line.raw 已同步，无需 virtual_raw
    input_session_ref = ft.use_ref({"li": -1, "start_off": -1, "last_value": ""})
    # value 清空序列号：_end_input_session 递增 → use_effect 触发清空 TextField value
    # 不在 _end_input_session 中直接 page.run_task（可能不及时），用 use_effect 确保
    # 在重渲染后执行（cursor_text_field 不设 value 属性，重渲染不同步 value，仅 use_effect 清空）
    clear_value_seq, set_clear_value_seq = ft.use_state(0)

    # 光标跟踪（ref 而非 state）：避免 on_selection_change 触发重渲染导致光标跳动
    cursor_ref = ft.use_ref(CursorState())
    # 粘贴时抑制 on_blur
    suppress_blur = ft.use_ref(False)
    # 原文模式
    raw_mode, set_raw_mode = ft.use_state(False)
    raw_draft, set_raw_draft = ft.use_state("")
    # ListView ref + 滚动跟踪
    list_view_ref = ft.use_ref(None)
    scroll_offset_ref = ft.use_ref(0.0)
    viewport_h_ref = ft.use_ref(0.0)
    max_scroll_ref = ft.use_ref(0.0)
    # 行实际渲染高度缓存：{line_idx: height_px}，由 LineView 的 on_size_change 上报。
    # 用于精确累加计算滚动偏移，避免估算偏差导致大纲跳转/光标导航落点不准。
    # build_controls_on_demand 下未构建的行无缓存，回退到 _estimate_line_height 估算。
    line_heights_ref = ft.use_ref({})
    # 记忆列：垂直导航时记录的行级 raw 偏移
    preferred_col_ref = ft.use_ref(None)
    # SelectionArea 当前选中的纯文本
    selection_text_ref = ft.use_ref("")
    # 撤销 / 重做栈
    history_ref = ft.use_ref(EditHistory(max_size=50))
    restoring = ft.use_ref(False)
    undo_push_pending = ft.use_ref(True)
    # 向外选区
    outward_sel, set_outward_sel = ft.use_state(None)
    outward_sel_ref = ft.use_ref(None)
    shift_pressed_ref = ft.use_ref(False)
    ctrl_pressed_ref = ft.use_ref(False)
    # 跳转目标行脉冲高亮：jump_to 后置为目标行号，1.2s 后异步清回 -1
    # LineView 据 is_flash=(flash_li==i) 渲染淡蓝底脉冲，重渲染 + animate 淡出
    flash_li, set_flash_li = ft.use_state(-1)
    # 代码块 / 表格聚焦
    code_focus_ref = ft.use_ref(None)
    code_edit_snapshot = ft.use_ref(None)  # 代码块聚焦时的快照，用于失焦时推入历史
    code_edit_changed = ft.use_ref(False)  # 代码块编辑会话是否有变化
    table_focus_ref = ft.use_ref(None)
    table_nav_ref = ft.use_ref(None)

    outward_sel_ref.current = outward_sel

    def _set_outward_sel(value):
        outward_sel_ref.current = value
        set_outward_sel(value)

    def mark_dirty():
        # 守卫：dirty 已为 True 时不再赋值，避免 True→True 触发额外 observable 通知
        if not document.dirty:
            document.dirty = True
        if on_dirty_change:
            on_dirty_change(True)

    # ============ 撤销 / 重做 ============
    def _make_snapshot() -> EditorSnapshot:
        md = raw_draft if raw_mode else parser.serialize(document)
        return EditorSnapshot(
            markdown=md,
            cursor_li=cursor_li,
            cursor_off=cursor_off,
            raw_mode=raw_mode,
            raw_draft=raw_draft,
        )

    def _push_history():
        if restoring.current:
            return
        history_ref.current.push(_make_snapshot())

    def _push_line_edit(li: int, before_raw: str):
        """行级快照入栈：仅单行内容编辑（字符输入 / 单字删除 / 行内格式包裹）。

        与 _push_history 一样受 undo_push_pending 门控：每个编辑组仅首次入栈，
        组内后续编辑不入栈。撤销恢复到组前状态（before_raw + 光标位置）。
        重做所需 after 状态由 undo() 时构造 current 快照捕获当前行 raw。
        """
        if restoring.current:
            return
        if not undo_push_pending.current:
            return
        history_ref.current.push(LineEditSnapshot(
            line_idx=li,
            raw=before_raw,
            cursor_li=cursor_li,
            cursor_off=cursor_off,
            raw_mode=raw_mode,
            raw_draft=raw_draft,
        ))
        undo_push_pending.current = False

    def _current_for_undo_redo(top) -> object:
        """构造当前状态快照，供 pop_undo/pop_redo 推入反向栈。

        若栈顶为 LineEditSnapshot，构造同行 LineEditSnapshot（raw=当前行 raw），
        这样重做（或撤销后重做）能恢复到当前行内容。否则用全文 _make_snapshot()。
        """
        if isinstance(top, LineEditSnapshot):
            li = top.line_idx
            if 0 <= li < len(document.lines):
                cur_raw = _line_raw(document.lines[li])
            else:
                cur_raw = top.raw
            return LineEditSnapshot(
                line_idx=li,
                raw=cur_raw,
                cursor_li=cursor_li,
                cursor_off=cursor_off,
                raw_mode=raw_mode,
                raw_draft=raw_draft,
            )
        return _make_snapshot()

    def _restore_snapshot(snap):
        restoring.current = True
        suppress_blur.current = True
        try:
            # 行级快照：仅 reparse 单行，不重建整个 document.lines
            if isinstance(snap, LineEditSnapshot):
                set_raw_mode(snap.raw_mode)
                if snap.raw_mode:
                    set_raw_draft(snap.raw_draft)
                li = snap.line_idx
                if 0 <= li < len(document.lines):
                    _reparse_atomic(document.lines[li], snap.raw)
                    mark_dirty()
                    if snap.cursor_li is not None and 0 <= snap.cursor_li < len(document.lines):
                        set_cursor_li(snap.cursor_li)
                        set_cursor_off(snap.cursor_off)
                        set_cursor_line(snap.cursor_li)
                        set_nav_seq(nav_seq + 1)
                    else:
                        set_cursor_li(None)
                return
            # 全文快照：重建 document.lines
            set_raw_mode(snap.raw_mode)
            if snap.raw_mode:
                set_raw_draft(snap.raw_draft)
                document.lines = parser.parse_markdown(snap.raw_draft).lines
                set_cursor_li(None)
            else:
                document.lines = parser.parse_markdown(snap.markdown).lines
                if snap.cursor_li is not None and 0 <= snap.cursor_li < len(document.lines):
                    set_cursor_li(snap.cursor_li)
                    set_cursor_off(snap.cursor_off)
                    set_cursor_line(snap.cursor_li)
                    set_nav_seq(nav_seq + 1)
                else:
                    set_cursor_li(None)
            mark_dirty()
        finally:
            restoring.current = False
            undo_push_pending.current = True

    def undo():
        hist = history_ref.current
        if not hist.undo:
            return
        current = _current_for_undo_redo(hist.undo[-1])
        prev = hist.pop_undo(current)
        if prev is not None:
            _restore_snapshot(prev)

    def redo():
        hist = history_ref.current
        if not hist.redo:
            return
        current = _current_for_undo_redo(hist.redo[-1])
        nxt = hist.pop_redo(current)
        if nxt is not None:
            _restore_snapshot(nxt)

    def _maybe_push_history():
        if undo_push_pending.current:
            _push_history()
            undo_push_pending.current = False

    # ============ 核心光标函数 ============
    def _end_input_session():
        """结束 IME 输入会话：同步 cursor_off + 重置状态 + 触发清空 value。

        仅从 _set_cursor 调用（li 变化/off 不连续/None）。此时 IME 组合已结束，
        清空 value 安全。use_effect([clear_value_seq]) 在重渲染后异步清空 TextField。

        同步 cursor_off：handle_char_input 中不调用 set_cursor_off（避免重渲染打断
        IME），光标位置仅由 cursor_ref 跟踪。会话结束时统一同步到 state，确保后续
        操作（点击、方向键、撤销/重做）使用正确的光标位置。
        """
        state = input_session_ref.current
        if state["li"] >= 0 and state["start_off"] >= 0:
            set_cursor_off(state["start_off"] + len(state["last_value"]))
            set_cursor_line(state["li"])
        input_session_ref.current = {"li": -1, "start_off": -1, "last_value": ""}
        set_clear_value_seq(clear_value_seq + 1)

    def _set_cursor(li: int | None, off: int = 0, *, clear_preferred: bool = True):
        """设置光标位置：cursor_li + cursor_off（不递增 nav_seq 以保 IME 组合态）。

        检测输入会话结束：li 变化或 off 不连续时调用 _end_input_session 清空
        TextField value（IME 组合已结束，安全清空）。
        """
        if li is None:
            set_cursor_li(None)
            _end_input_session()
            return
        if not (0 <= li < len(document.lines)):
            return
        raw_len = len(_line_raw(document.lines[li]))
        off = max(0, min(off, raw_len))

        # 检测输入会话是否需要结束（光标不连续或切换行）
        state = input_session_ref.current
        if state["li"] >= 0:
            if state["li"] != li:
                _end_input_session()
            elif state["start_off"] >= 0:
                expected_off = state["start_off"] + len(state["last_value"])
                if off != expected_off:
                    _end_input_session()

        set_cursor_li(li)
        set_cursor_off(off)
        set_cursor_line(li)
        # 不再无条件递增 nav_seq：同行输入/导航保持 key 不变，避免重建破坏 IME
        if clear_preferred:
            preferred_col_ref.current = None
        cursor_ref.current.reset(off, raw_len)

    def _on_tap_line(li: int, raw_off: int):
        """渲染层点击：定位光标到 (li, raw_off)。"""
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        # 围栏块点击：更新 cursor_line，不进入光标编辑（CODE/TABLE 有自己的编辑器）
        if _is_fence(line):
            set_cursor_line(li)
            if outward_sel_ref.current is not None:
                _set_outward_sel(None)
            return
        # 既有向外选区：先清除，然后继续定位光标到点击位置
        if outward_sel_ref.current is not None:
            _set_outward_sel(None)
        _set_cursor(li, raw_off)
        _ensure_visible(li)

    def handle_char_input(value: str):
        """字符输入：增量式编辑（IME 友好，3 分支模型）。

        分支：
        - ignore: value == last_value（重发）或 last_value 包含 value（删除由 backspace 处理）
        - replace: IME 组合完成（value 含非 ASCII 且 last_value 全 ASCII），替换 [start_off, end_off]
        - append: 在 end_off 处插入增量（value 以 last_value 为前缀；或上次已提交非 ASCII 后起新组合）

        不调用 set_cursor_off（避免重渲染打断 IME），cursor_off state 在 _end_input_session
        中统一同步；cursor_ref 实时跟踪最新位置供 backspace_core/delete_core 读取。
        reparse_line 后 line.raw 已同步，无需 virtual_raw 跟踪。

        IME 翻倍修正（修复 Windows 五笔/拼音输入重复 bug）：
        Windows 输入法在某些 TextField 配置下会出现 composing text 完美翻倍
        （value = X + X，如 'wqwq'、'你你'）。在入口处检测此模式并取前半部分修正。
        """
        if cursor_li is None or not value:
            return

        # IME 翻倍修正：value = X + X 模式时取 X
        # （Windows 五笔/拼音 composing text 完美翻倍 bug）
        # 仅当长度 >= 2 且偶数，且前半 == 后半时触发
        if len(value) >= 2 and len(value) % 2 == 0:
            half = len(value) // 2
            if value[:half] == value[half:]:
                value = value[:half]

        li = cursor_li
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if _is_fence(line):
            return

        state = input_session_ref.current

        # 新会话启动（首次输入或会话已结束）
        if state["li"] != li or state["start_off"] < 0:
            raw = _line_raw(line)
            off = cursor_off
            # 安全网：value 已在文档中（切行时 use_effect 异步清空窗口期 IME 重发）
            if off + len(value) <= len(raw) and raw[off:off + len(value)] == value:
                state["li"], state["start_off"], state["last_value"] = li, off, value
                cursor_ref.current.reset(off + len(value), len(raw))
                return
            # 行级快照：IME 会话启动时入栈编辑前 raw，撤销恢复到组前状态
            _push_line_edit(li, raw)
            state["li"] = li
            state["start_off"] = cursor_off
            state["last_value"] = ""

        start_off = state["start_off"]
        last_value = state["last_value"]

        # 分支 1: ignore（无变化 / 删除由 backspace_core 处理）
        if value == last_value:
            return
        if last_value and last_value.startswith(value):
            return

        raw = _line_raw(line)  # reparse_line 后 line.raw 已同步，无需 virtual_raw
        end_off = start_off + len(last_value)

        # 分支 2: replace（IME 组合完成：value 含非 ASCII，last_value 全 ASCII）
        is_ime_compose = (
            last_value
            and any(ord(c) > 127 for c in value)
            and all(ord(c) < 128 for c in last_value)
        )
        if is_ime_compose:
            new_raw = raw[:start_off] + value + raw[end_off:]
        # 分支 3: append（在 end_off 处插入增量）
        else:
            if value.startswith(last_value):
                new_part = value[len(last_value):]
            else:
                # 上次为已提交非 ASCII，本次为新组合：提交 last_value，起新会话
                new_part = value
                state["start_off"] = end_off
                start_off = end_off
            new_raw = raw[:end_off] + new_part + raw[end_off:]

        state["last_value"] = value
        new_off = start_off + len(value)
        cursor_ref.current.reset(new_off, len(new_raw))
        _reparse_atomic(line, new_raw)
        mark_dirty()

    def handle_paste(clip_text: str, old_draft: str = ""):
        """多行粘贴：在光标处插入 clip_text，多行时拆分为新行。"""
        if cursor_li is None or not clip_text:
            return
        li = cursor_li
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if _is_fence(line):
            return
        _push_history()
        undo_push_pending.current = True
        raw = _line_raw(line)
        off = cursor_off
        parts = clip_text.split("\n")
        if len(parts) == 1:
            new_raw = raw[:off] + parts[0] + raw[off:]
            _reparse_atomic(line, new_raw)
            mark_dirty()
            suppress_blur.current = True
            _set_cursor(li, off + len(parts[0]))
        else:
            before = raw[:off]
            after = raw[off:]
            _reparse_atomic(line, before + parts[0])
            middle = [parser.parse_markdown(p).lines[0] for p in parts[1:-1]]
            last_raw = parts[-1] + after
            last_line = parser.parse_markdown(last_raw).lines[0]
            document.lines = (
                document.lines[:li + 1] + middle + [last_line] + document.lines[li + 1:]
            )
            mark_dirty()
            last_li = li + 1 + len(middle)
            suppress_blur.current = True
            _set_cursor(last_li, len(parts[-1]))

    def backspace_core():
        """光标级 Backspace：删光标前字符；行首则与前一行合并。"""
        if outward_sel_ref.current is not None:
            handle_outward_delete()
            return
        if cursor_li is None:
            return
        li = cursor_li
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if _is_fence(line):
            return
        # 用 cursor_ref.current.base（IME 期间实时更新），不用 cursor_off state（IME 期间过时）
        off = cursor_ref.current.base if cursor_ref.current else cursor_off
        if off > 0:
            # 删光标前一个字符（行级快照）
            raw = _line_raw(line)
            _push_line_edit(li, raw)
            new_raw = raw[:off - 1] + raw[off:]
            _reparse_atomic(line, new_raw)
            mark_dirty()
            _set_cursor(li, off - 1)
        elif li > 0:
            # 行首：与前一行合并
            prev = document.lines[li - 1]
            if _is_fence(prev):
                return
            _push_history()
            undo_push_pending.current = True
            prev_raw = _line_raw(prev)
            cur_raw = _line_raw(line)
            junction = len(prev_raw)
            merged = prev_raw + cur_raw
            _reparse_atomic(prev, merged)
            document.lines = document.lines[:li] + document.lines[li + 1:]
            mark_dirty()
            suppress_blur.current = True
            _set_cursor(li - 1, junction)

    def delete_core():
        """光标级 Delete：删光标后字符；行尾则与下一行合并。"""
        if outward_sel_ref.current is not None:
            handle_outward_delete()
            return
        if cursor_li is None:
            return
        li = cursor_li
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if _is_fence(line):
            return
        raw = _line_raw(line)
        # 用 cursor_ref.current.base（IME 期间实时更新），不用 cursor_off state（IME 期间过时）
        off = cursor_ref.current.base if cursor_ref.current else cursor_off
        if off < len(raw):
            # 删光标后一个字符（行级快照）
            _push_line_edit(li, raw)
            new_raw = raw[:off] + raw[off + 1:]
            _reparse_atomic(line, new_raw)
            mark_dirty()
            _set_cursor(li, off)
        elif li < len(document.lines) - 1:
            # 行尾：与下一行合并
            nxt = document.lines[li + 1]
            if _is_fence(nxt):
                return
            _push_history()
            undo_push_pending.current = True
            junction = len(raw)
            merged = raw + _line_raw(nxt)
            _reparse_atomic(line, merged)
            document.lines = document.lines[:li + 1] + document.lines[li + 2:]
            mark_dirty()
            suppress_blur.current = True
            _set_cursor(li, junction)

    def on_submit(value: str):
        """Enter：在光标处分割行，续行加列表/引用前缀。"""
        if cursor_li is None:
            return
        _push_history()
        undo_push_pending.current = True
        li = cursor_li
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if _is_fence(line):
            return
        raw = _line_raw(line)
        off = cursor_off
        before = raw[:off]
        after = raw[off:]

        # 标题：before 空 → 清空前缀；否则分割成两行
        if line.block_type == BlockType.HEADING:
            if not before.strip():
                _reparse_atomic(line, after.lstrip())
                mark_dirty()
                _set_cursor(li, 0)
                return
            _reparse_atomic(line, before)
            new_line = parser.parse_markdown(after).lines[0]
            document.lines = document.lines[:li + 1] + [new_line] + document.lines[li + 1:]
            mark_dirty()
            suppress_blur.current = True
            _set_cursor(li + 1, 0)
            return

        # 列表 / 引用：before 仅前缀（空内容）→ 退出列表/引用
        if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O, BlockType.QUOTE):
            if not before.strip():
                stripped = after.lstrip()
                if line.block_type == BlockType.QUOTE:
                    stripped = stripped.lstrip("> ")
                _reparse_atomic(line, stripped)
                mark_dirty()
                _set_cursor(li, 0)
                return
            if line.block_type == BlockType.LIST_UO and before.rstrip() in ("-", "*", "+"):
                _reparse_atomic(line, after.lstrip())
                mark_dirty()
                _set_cursor(li, 0)
                return
            if line.block_type == BlockType.LIST_O and re.match(r"^\d+\.$", before.rstrip()):
                _reparse_atomic(line, after.lstrip())
                mark_dirty()
                _set_cursor(li, 0)
                return

        # 默认：分割当前行，续行加列表/引用前缀
        cont_prefix = _next_line_raw(line)
        _reparse_atomic(line, before)
        new_line = parser.parse_markdown(cont_prefix + after).lines[0]
        document.lines = document.lines[:li + 1] + [new_line] + document.lines[li + 1:]
        mark_dirty()
        suppress_blur.current = True
        _set_cursor(li + 1, len(cont_prefix))

    # ============ 光标导航 ============
    def move_left():
        """← ：光标左移；行首则跳上一行行尾。"""
        if cursor_li is None:
            return
        li = cursor_li
        if not (0 <= li < len(document.lines)):
            return
        if _is_fence(document.lines[li]):
            return
        if cursor_off > 0:
            _set_cursor(li, cursor_off - 1)
        elif li > 0:
            prev = document.lines[li - 1]
            if _is_fence(prev):
                return
            _set_cursor(li - 1, len(_line_raw(prev)))
            _ensure_visible(li - 1)

    def move_right():
        """→ ：光标右移；行尾则跳下一行行首。"""
        if cursor_li is None:
            return
        li = cursor_li
        if not (0 <= li < len(document.lines)):
            return
        if _is_fence(document.lines[li]):
            return
        raw = _line_raw(document.lines[li])
        if cursor_off < len(raw):
            _set_cursor(li, cursor_off + 1)
        elif li < len(document.lines) - 1:
            nxt = document.lines[li + 1]
            if _is_fence(nxt):
                return
            _set_cursor(li + 1, 0)
            _ensure_visible(li + 1)

    def move_home():
        """Smart Home（VSCode 式）：先跳内容首（跳过前缀），再跳行首（raw 0）。

        - 光标在内容中 → 跳到内容首（# / - / > 等前缀之后）
        - 光标在内容首 → 跳到行首（raw 0，前缀之前）
        - 光标在行首 → 不动
        """
        if cursor_li is None:
            return
        if _is_fence(document.lines[cursor_li]):
            return
        line = document.lines[cursor_li]
        # 计算 content_start：跳过 HEADING_PREFIX / LIST_PREFIX / QUOTE_PREFIX 段 0
        content_start = 0
        if line.segments and line.segments[0].seg_type in (
            SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX,
        ):
            content_start = len(line.segments[0].raw)
        raw_len = len(_line_raw(line))
        content_start = min(content_start, raw_len)
        # Smart Home 三态判定
        if cursor_off == 0:
            pass  # 已在行首
        elif cursor_off == content_start:
            _set_cursor(cursor_li, 0)
        else:
            _set_cursor(cursor_li, content_start)
        _ensure_visible(cursor_li)

    def move_end():
        """End：跳到行尾。"""
        if cursor_li is None:
            return
        if not (0 <= cursor_li < len(document.lines)):
            return
        if _is_fence(document.lines[cursor_li]):
            return
        _set_cursor(cursor_li, len(_line_raw(document.lines[cursor_li])))
        _ensure_visible(cursor_li)

    def move_doc_start():
        """Ctrl+Home：跳到文档首行行首。"""
        if not document.lines:
            return
        li = 0
        if _is_fence(document.lines[li]):
            set_cursor_line(li)
            set_cursor_li(None)
            return
        _set_cursor(li, 0)
        _ensure_visible(li)

    def move_doc_end():
        """Ctrl+End：跳到文档末行行尾。"""
        if not document.lines:
            return
        li = len(document.lines) - 1
        if _is_fence(document.lines[li]):
            set_cursor_line(li)
            set_cursor_li(None)
            return
        _set_cursor(li, len(_line_raw(document.lines[li])))
        _ensure_visible(li)

    def _vertical_goto(target_li: int):
        """垂直导航到 target_li，使用记忆列定位。"""
        if not (0 <= target_li < len(document.lines)):
            return
        if cursor_li is None:
            return
        if preferred_col_ref.current is None:
            preferred_col_ref.current = cursor_off
        col = preferred_col_ref.current
        target_line = document.lines[target_li]
        if _is_fence(target_line):
            set_cursor_line(target_li)
            set_cursor_li(None)
            return
        target_off = max(0, min(col, len(_line_raw(target_line))))
        _set_cursor(target_li, target_off, clear_preferred=False)
        _ensure_visible(target_li)

    def move_up():
        if cursor_li is None or cursor_li <= 0:
            return
        _vertical_goto(cursor_li - 1)

    def move_down():
        if cursor_li is None or cursor_li >= len(document.lines) - 1:
            return
        _vertical_goto(cursor_li + 1)

    # ============ 缩进 ============
    def indent_or_outdent(delta: int):
        """Tab / Shift+Tab：列表缩进 / 引用层级。"""
        if cursor_li is None:
            return
        li = cursor_li
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O):
            _push_history()
            undo_push_pending.current = True
            new_level = max(0, (line.level or 0) + delta)
            indent_sp = " " * new_level
            # 从前缀段提取列表标记符号
            prefix_raw = line.segments[0].raw if line.segments else "- "
            # 从整行获取完整内容（排除前缀）
            content = _inline_content(line)
            # 从前缀中提取标记类型
            body = prefix_raw.lstrip()
            if line.task:
                marker_match = re.match(r"^([-*+])\s+", body)
                marker = marker_match.group(1) if marker_match else "-"
                new_prefix = f"{indent_sp}{marker} [{'x' if line.checked else ' '}] "
            elif line.block_type == BlockType.LIST_O:
                num_match = re.match(r"^(\d+)\.\s+", body)
                num = num_match.group(1) if num_match else "1"
                new_prefix = f"{indent_sp}{num}. "
            else:
                marker_match = re.match(r"^([-*+])\s+", body)
                marker = marker_match.group(1) if marker_match else "-"
                new_prefix = f"{indent_sp}{marker} "
            new_raw = new_prefix + content
            _reparse_atomic(line, new_raw)
            mark_dirty()
            _set_cursor(li, len(new_prefix))
        elif line.block_type == BlockType.QUOTE:
            _push_history()
            undo_push_pending.current = True
            new_level = max(1, (line.level or 1) + delta)
            content = _inline_content(line)
            new_raw = "> " * new_level + content
            _reparse_atomic(line, new_raw)
            mark_dirty()
            _set_cursor(li, new_level * 2)
        else:
            # 普通段落：Tab 插入 4 空格
            if delta > 0:
                raw = _line_raw(line)
                new_raw = raw[:cursor_off] + "    " + raw[cursor_off:]
                _reparse_atomic(line, new_raw)
                mark_dirty()
                _set_cursor(li, cursor_off + 4)

    # ============ 块操作 ============
    def new_line_after(li: int):
        if not (0 <= li < len(document.lines)):
            return
        _push_history()
        undo_push_pending.current = True
        new_raw = _next_line_raw(document.lines[li])
        new_line = parser.parse_markdown(new_raw).lines[0]
        document.lines = document.lines[:li + 1] + [new_line] + document.lines[li + 1:]
        mark_dirty()
        _set_cursor(li + 1, len(new_raw))

    def set_block(block_type: BlockType, level: int = 0):
        """切换当前行块类型（Ctrl+0~6 / 工具栏）。"""
        li = cursor_li if cursor_li is not None else cursor_line
        if not (0 <= li < len(document.lines)):
            return
        _push_history()
        undo_push_pending.current = True
        line = document.lines[li]
        content = _inline_content(line)
        if block_type == BlockType.HEADING:
            new_raw = "#" * level + " " + content
        elif block_type == BlockType.LIST_UO:
            indent_sp = " " * line.level if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O) else ""
            new_raw = f"{indent_sp}- " + content
        elif block_type == BlockType.LIST_O:
            indent_sp = " " * line.level if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O) else ""
            new_raw = f"{indent_sp}1. " + content
        elif block_type == BlockType.QUOTE:
            new_raw = "> " + content
        elif block_type == BlockType.CODE:
            new_raw = "```\n" + content + "\n```"
        elif block_type == BlockType.HR:
            new_raw = "---"
        else:
            new_raw = content
        _reparse_atomic(line, new_raw)
        mark_dirty()
        if block_type == BlockType.CODE:
            set_cursor_line(li)
            set_cursor_li(None)
        elif block_type in (BlockType.HR, BlockType.TOC):
            set_cursor_line(li)
            set_cursor_li(None)
        else:
            # 定位到内容首字符（跳过前缀）
            new_line = document.lines[li]
            prefix_len = 0
            if new_line.segments and new_line.segments[0].seg_type in (
                SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX,
            ):
                prefix_len = len(new_line.segments[0].raw)
            _set_cursor(li, prefix_len)

    # ============ 行内格式（光标级包裹）============
    def apply_inline_format(fmt: str):
        """行内格式快捷键：有 outward 选区包裹同段选区；否则在光标处插入空语法。

        fmt: bold/italic/highlight/strike/code/link
        """
        # 渲染态 outward 选区：包裹同段选区
        if cursor_li is None:
            if outward_sel_ref.current is not None:
                _apply_outward_wrap(fmt)
            return
        li = cursor_li
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if _is_fence(line):
            return
        _push_history()
        undo_push_pending.current = True
        raw = _line_raw(line)
        off = cursor_off
        if fmt == "link":
            new_raw = raw[:off] + "[](url)" + raw[off:]
            _reparse_atomic(line, new_raw)
            mark_dirty()
            _set_cursor(li, off + 1)  # 光标落在 [ 后
        else:
            seg_type = {
                "bold": SegType.STRONG,
                "italic": SegType.EMPHASIS,
                "highlight": SegType.HIGHLIGHT,
                "strike": SegType.STRIKE,
                "code": SegType.CODESPAN,
            }.get(fmt)
            if seg_type is None:
                return
            wrap = WRAP_SYNTAX.get(seg_type, ("", ""))[0]
            new_raw = raw[:off] + wrap + wrap + raw[off:]
            _reparse_atomic(line, new_raw)
            mark_dirty()
            _set_cursor(li, off + len(wrap))  # 光标落在两标记之间

    def _apply_outward_wrap(fmt: str):
        """渲染态 outward 选区包裹行内格式（仅同段选区）。"""
        sel = outward_sel_ref.current
        if sel is None:
            return
        a_li, a_off, b_li, b_off = sel
        if (a_li, a_off) > (b_li, b_off):
            a_li, a_off, b_li, b_off = b_li, b_off, a_li, a_off
        if a_li != b_li:
            return
        if not (0 <= a_li < len(document.lines)):
            return
        line = document.lines[a_li]
        if _is_fence(line):
            return
        raw = _line_raw(line)
        a_off = max(0, min(a_off, len(raw)))
        b_off = max(a_off, min(b_off, len(raw)))
        selected = raw[a_off:b_off]
        _push_history()
        undo_push_pending.current = True
        if fmt == "link":
            new_raw = raw[:a_off] + f"[{selected}](url)" + raw[b_off:]
            new_off = a_off + 1
        else:
            seg_type = {
                "bold": SegType.STRONG,
                "italic": SegType.EMPHASIS,
                "highlight": SegType.HIGHLIGHT,
                "strike": SegType.STRIKE,
                "code": SegType.CODESPAN,
            }.get(fmt)
            if seg_type is None:
                return
            wrap = WRAP_SYNTAX.get(seg_type, ("", ""))[0]
            new_raw = raw[:a_off] + wrap + selected + wrap + raw[b_off:]
            new_off = a_off + len(wrap)
        _reparse_atomic(line, new_raw)
        new_lines = list(document.lines)
        new_lines[a_li] = line
        document.lines = new_lines
        mark_dirty()
        _set_outward_sel(None)
        _set_cursor(a_li, new_off)

    # ============ 任务列表 ============
    def toggle_task(li: int):
        if not (0 <= li < len(document.lines)):
            return
        _push_history()
        undo_push_pending.current = True
        line = document.lines[li]
        line.checked = not line.checked
        # 重建 raw 以反映勾选状态
        prefix_raw = line.segments[0].raw if line.segments else "- "
        content = _inline_content(line)
        body = prefix_raw.lstrip()
        marker_match = re.match(r"^([-*+])\s+", body)
        marker = marker_match.group(1) if marker_match else "-"
        new_prefix = f"{' ' * (line.level or 0)}{marker} [{'x' if line.checked else ' '}] "
        new_raw = new_prefix + content
        _reparse_atomic(line, new_raw)
        mark_dirty()

    def change_lang(li: int, new_lang: str):
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if line.block_type != BlockType.CODE:
            return
        _maybe_push_history()
        line.lang = new_lang
        code = line.segments[0].text if line.segments else ""
        full = f"```{new_lang}\n{code}\n```" if code else f"```{new_lang}\n```"
        _reparse_atomic(line, full)
        mark_dirty()

    # ============ 代码块 ============
    def on_change_code(li: int, value: str) -> None:
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if line.block_type != BlockType.CODE:
            return
        old_text = line.segments[0].text if line.segments else ""
        if old_text == value:
            return
        line.segments[0].text = value
        line.segments[0].raw = value
        line.raw = f"```{line.lang}\n{value}\n```"
        # 代码块编辑防抖：第一次修改时将快照推入历史，整个编辑会话只占一个撤销条目
        # 这样即使在代码块聚焦时按 Ctrl+Z 也能正常撤销
        if not code_edit_changed.current and code_edit_snapshot.current is not None:
            if not restoring.current:
                history_ref.current.push(code_edit_snapshot.current)
        code_edit_changed.current = True
        if not document.dirty:
            mark_dirty()

    def on_code_focus(li: int) -> None:
        code_focus_ref.current = li
        # 代码块聚焦时退出光标编辑态
        if cursor_li is not None:
            suppress_blur.current = True
            set_cursor_li(None)
        # 保存聚焦时的快照，用于失焦时与修改前比较
        code_edit_snapshot.current = _make_snapshot()
        code_edit_changed.current = False

    def on_code_blur(li: int) -> None:
        if code_focus_ref.current == li:
            code_focus_ref.current = None
        # 代码块失焦时：清理状态
        # 注意：快照已在第一次修改时推入历史，此处不再重复推入
        code_edit_snapshot.current = None
        code_edit_changed.current = False

    # ============ 表格 ============
    def _table_cells(line: Line) -> list[str]:
        return [cell.strip() for cell in line.raw.strip().strip("|").split("|")]

    def on_change_cell(li: int, cell_idx: int, value: str) -> None:
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if line.block_type != BlockType.TABLE:
            return
        cells = _table_cells(line)
        if cell_idx < len(cells):
            if cells[cell_idx] == value:
                return
            cells[cell_idx] = value
        new_raw = _join_row(cells)
        line.raw = new_raw
        if line.segments:
            line.segments[0].text = new_raw
            line.segments[0].raw = new_raw
        _maybe_push_history()
        if not document.dirty:
            mark_dirty()

    def on_table_op(op: str, params: dict) -> None:
        _push_history()
        undo_push_pending.current = True
        lines = list(document.lines)

        def _find_table_start_from_li(li: int) -> int:
            if not (0 <= li < len(lines)):
                return li
            j = li
            while j > 0 and lines[j - 1].block_type == BlockType.TABLE:
                j -= 1
            return j

        ts = params.get("table_start")
        if ts is None:
            ref_li = params.get("after_li", params.get("li", 0))
            ts = _find_table_start_from_li(ref_li)

        def _find_table_range(start: int) -> tuple[int, int]:
            i = start
            while i < len(lines) and lines[i].block_type == BlockType.TABLE:
                i += 1
            return start, i - 1

        def _find_sep_line(start: int) -> int:
            if start + 1 < len(lines) and lines[start + 1].block_type == BlockType.TABLE:
                cells = [c.strip() for c in lines[start + 1].raw.strip().strip("|").split("|")]
                if all(c and _ALIGN_RE_TABLE.fullmatch(c) for c in cells):
                    return start + 1
            return start + 1

        def _rebuild_table_line(idx: int, new_raw: str) -> None:
            new_line = Line(block_type=BlockType.TABLE, raw=new_raw)
            new_line.segments = [Segment(SegType.TEXT, new_raw, new_raw)]
            lines[idx] = new_line

        if op == "add_row":
            after_li = params["after_li"]
            col_count = params["col_count"]
            new_raw = "| " + " | ".join([""] * col_count) + " |"
            new_line = Line(block_type=BlockType.TABLE, raw=new_raw)
            new_line.segments = [Segment(SegType.TEXT, new_raw, new_raw)]
            lines.insert(after_li + 1, new_line)
            document.lines = lines
            mark_dirty()
        elif op == "delete_row":
            li = params["li"]
            ts2, te2 = _find_table_range(ts)
            sep = _find_sep_line(ts2)
            data_indices = [i for i in range(ts2, te2 + 1) if i != ts2 and i != sep]
            if li in data_indices and len(data_indices) > 1:
                del lines[li]
                document.lines = lines
                mark_dirty()
        elif op == "clear_row":
            li = params["li"]
            if 0 <= li < len(lines):
                cells = _table_cells(lines[li])
                _rebuild_table_line(li, _join_row([""] * len(cells)))
                document.lines = lines
                mark_dirty()
        elif op == "add_col":
            ts2, te2 = _find_table_range(ts)
            col_idx = params["col_idx"]
            sep_li = _find_sep_line(ts2)
            for i in range(ts2, te2 + 1):
                cells = _table_cells(lines[i])
                if i == sep_li:
                    cells.insert(col_idx, "---")
                else:
                    cells.insert(col_idx, "")
                _rebuild_table_line(i, _join_row(cells))
            document.lines = lines
            mark_dirty()
        elif op == "delete_col":
            ts2, te2 = _find_table_range(ts)
            col_idx = params["col_idx"]
            for i in range(ts2, te2 + 1):
                cells = _table_cells(lines[i])
                if 0 <= col_idx < len(cells):
                    del cells[col_idx]
                _rebuild_table_line(i, _join_row(cells))
            document.lines = lines
            mark_dirty()
        elif op == "set_align":
            ts2, te2 = _find_table_range(ts)
            col_idx = params["col_idx"]
            align = params["align"]
            sep_li = _find_sep_line(ts2)
            cells = _table_cells(lines[sep_li])
            if 0 <= col_idx < len(cells):
                cells[col_idx] = align
            _rebuild_table_line(sep_li, _join_row(cells))
            document.lines = lines
            mark_dirty()

    def on_table_focus() -> None:
        _maybe_push_history()

    def on_table_blur() -> None:
        undo_push_pending.current = True

    # ============ 原文模式 ============
    def toggle_raw():
        _push_history()
        undo_push_pending.current = True
        if not raw_mode:
            set_raw_draft(parser.serialize(document))
            selection_text_ref.current = ""
            set_cursor_li(None)
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

    def on_blur():
        """cursor TextField 失焦：若非抑制，退出光标编辑态。"""
        if suppress_blur.current:
            suppress_blur.current = False
            return
        # 不主动退出：保留光标位置（Typora 式，点击别处由 on_tap 处理）

    def suppress_blur_for_click():
        suppress_blur.current = True

    def _raw_editor() -> ft.Control:
        def _on_raw_change(value: str):
            set_raw_draft(value)
            document.lines = parser.parse_markdown(value).lines
            mark_dirty()

        return ft.Container(
            expand=True,
            alignment=ft.Alignment.TOP_CENTER,
            bgcolor=c.bg,
            padding=ft.Padding.symmetric(horizontal=content_padding, vertical=content_padding_top),
            content=ft.Container(
                content=ft.TextField(
                    value=raw_draft,
                    multiline=True,
                    min_lines=20,
                    border=ft.InputBorder.NONE,
                    text_size=body_font_size,
                    text_style=ft.TextStyle(font_family=FONT_MONO, color=c.text),
                    on_change=lambda e: _on_raw_change(e.control.value),
                    expand=True,
                ),
                width=content_max_width,
                alignment=ft.Alignment.TOP_LEFT,
            ),
        )

    # ============ 剪贴板 / 选区 ============
    def compute_markdown_from_text(text: str) -> str:
        return parser.compute_markdown_from_text(document.lines, text)

    def handle_delete_selection(plain_text: str):
        if not plain_text:
            return
        _push_history()
        undo_push_pending.current = True
        # SelectionArea 选区删除：简单实现——序列化→删除纯文本→重新解析
        # 完整实现需 raw 偏移映射，Phase 2 完善
        selection_text_ref.current = ""
        mark_dirty()

    async def handle_cut(plain_text: str):
        if not plain_text:
            return
        clipboard = clipboard_ref.current if clipboard_ref is not None else None
        if clipboard is not None:
            try:
                md = parser.compute_markdown_from_text(document.lines, plain_text)
                await clipboard.set(md or plain_text)
            except Exception:
                pass
        handle_delete_selection(plain_text)

    def apply_inline_format_to_selection(fmt: str, combo: str):
        """渲染态 SelectionArea 选区包裹行内格式。"""
        if outward_sel_ref.current is not None:
            apply_inline_format(fmt)
            return
        # SelectionArea 选区：Phase 2 完善 raw 偏移映射
        # 简化：若有 outward_sel 走 outward 包裹
        plain = selection_text_ref.current or ""
        if plain:
            # 尝试用 SelectionArea 选区文本做包裹（简化）
            pass

    def on_selection_area_change(e):
        """SelectionArea 选区变化：上报纯文本。"""
        try:
            selection_text_ref.current = e.data or ""
        except Exception:
            selection_text_ref.current = ""

    # ============ 向外选区 ============
    def _step_left(li: int, off: int) -> tuple[int, int] | None:
        if off > 0:
            return (li, off - 1)
        if li <= 0:
            return None
        prev = document.lines[li - 1]
        if _is_fence(prev):
            return None
        return (li - 1, len(_line_raw(prev)))

    def _step_right(li: int, off: int) -> tuple[int, int] | None:
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

    def _start_outward_from_point(anchor_li: int, anchor_off: int, target_li: int, target_off: int) -> None:
        if outward_sel_ref.current is not None:
            return
        if not (0 <= anchor_li < len(document.lines) and 0 <= target_li < len(document.lines)):
            return
        anchor_off = max(0, min(anchor_off, len(document.lines[anchor_li].raw or "")))
        target_off = max(0, min(target_off, len(document.lines[target_li].raw or "")))
        if cursor_li is not None:
            suppress_blur.current = True
            set_cursor_li(None)
        _set_outward_sel((anchor_li, anchor_off, target_li, target_off))

    def _extend_outward(target_li: int, target_off: int) -> None:
        current = outward_sel_ref.current
        if current is None:
            return
        if not (0 <= target_li < len(document.lines)):
            return
        target_off = max(0, min(target_off, len(document.lines[target_li].raw or "")))
        a_li, a_off, _, _ = current
        _set_outward_sel((a_li, a_off, target_li, target_off))

    def _extend_outward_step(step_fn) -> None:
        current = outward_sel_ref.current
        if cursor_li is not None and current is None:
            # 从光标起始
            new_pos = step_fn(cursor_li, cursor_off)
            if new_pos is None:
                return
            src_li, src_off = cursor_li, cursor_off
            suppress_blur.current = True
            set_cursor_li(None)
            _set_outward_sel((src_li, src_off, new_pos[0], new_pos[1]))
            return
        if current is None:
            return
        a_li, a_off, b_li, b_off = current
        new_pos = step_fn(b_li, b_off)
        if new_pos is None:
            return
        _set_outward_sel((a_li, a_off, new_pos[0], new_pos[1]))

    def clear_outward_sel() -> None:
        _set_outward_sel(None)

    def on_extend_outward(target_li: int, target_off: int) -> None:
        if outward_sel_ref.current is None:
            if cursor_li is not None:
                # 从光标起始
                src_li, src_off = cursor_li, cursor_off
                suppress_blur.current = True
                set_cursor_li(None)
                _set_outward_sel((src_li, src_off, target_li, target_off))
            else:
                _start_outward_from_point(target_li, target_off, target_li, target_off)
        else:
            _extend_outward(target_li, target_off)

    def _delete_raw_range(start_li: int, start_off: int, end_li: int, end_off: int) -> None:
        _push_history()
        undo_push_pending.current = True
        try:
            if start_li == end_li:
                if not (0 <= start_li < len(document.lines)):
                    return
                line = document.lines[start_li]
                cur_raw = _line_raw(line)
                new_raw = cur_raw[:start_off] + cur_raw[end_off:]
                _reparse_atomic(line, new_raw)
                new_lines = list(document.lines)
            else:
                if not (0 <= start_li < len(document.lines) and 0 <= end_li < len(document.lines)):
                    return
                start_line = document.lines[start_li]
                end_line = document.lines[end_li]
                merged = _line_raw(start_line)[:start_off] + _line_raw(end_line)[end_off:]
                _reparse_atomic(start_line, merged)
                new_lines = document.lines[:start_li + 1] + document.lines[end_li + 1:]
        except Exception:
            return
        document.lines = new_lines
        mark_dirty()
        _set_outward_sel(None)
        if 0 <= start_li < len(document.lines):
            _set_cursor(start_li, start_off)

    def handle_outward_delete() -> None:
        if outward_sel is None:
            return
        a_li, a_off, b_li, b_off = outward_sel
        if (a_li, a_off) > (b_li, b_off):
            a_li, a_off, b_li, b_off = b_li, b_off, a_li, a_off
        _delete_raw_range(a_li, a_off, b_li, b_off)

    async def handle_outward_cut() -> None:
        if outward_sel is None:
            return
        a_li, a_off, b_li, b_off = outward_sel
        if (a_li, a_off) > (b_li, b_off):
            a_li, a_off, b_li, b_off = b_li, b_off, a_li, a_off
        # 提取选区文本
        try:
            if a_li == b_li:
                line = document.lines[a_li]
                text = _line_raw(line)[a_off:b_off]
            else:
                parts = [_line_raw(document.lines[a_li])[a_off:]]
                for i in range(a_li + 1, b_li):
                    parts.append(_line_raw(document.lines[i]))
                parts.append(_line_raw(document.lines[b_li])[:b_off])
                text = "\n".join(parts)
        except Exception:
            text = ""
        clipboard = clipboard_ref.current if clipboard_ref is not None else None
        if clipboard is not None and text:
            try:
                await clipboard.set(text)
            except Exception:
                pass
        _delete_raw_range(a_li, a_off, b_li, b_off)

    async def handle_outward_copy() -> None:
        """Ctrl+C：复制 outward_sel 选区文本到剪贴板（不删除）。

        复用 handle_outward_cut 的文本提取逻辑，但跳过 _delete_raw_range。
        """
        sel = outward_sel_ref.current
        if sel is None:
            return
        a_li, a_off, b_li, b_off = sel
        if (a_li, a_off) > (b_li, b_off):
            a_li, a_off, b_li, b_off = b_li, b_off, a_li, a_off
        try:
            if a_li == b_li:
                if not (0 <= a_li < len(document.lines)):
                    return
                text = _line_raw(document.lines[a_li])[a_off:b_off]
            else:
                if not (0 <= a_li < len(document.lines) and 0 <= b_li < len(document.lines)):
                    return
                parts = [_line_raw(document.lines[a_li])[a_off:]]
                for i in range(a_li + 1, b_li):
                    parts.append(_line_raw(document.lines[i]))
                parts.append(_line_raw(document.lines[b_li])[:b_off])
                text = "\n".join(parts)
        except Exception:
            text = ""
        clipboard = clipboard_ref.current if clipboard_ref is not None else None
        if clipboard is not None and text:
            try:
                await clipboard.set(text)
            except Exception:
                pass

    def select_all() -> None:
        """Ctrl+A：全选文档（outward_sel 跨越整个文档）。"""
        if not document.lines:
            return
        last_li = len(document.lines) - 1
        last_line = document.lines[last_li]
        # 末行为围栏块时全选到其行首（围栏块不参与 raw 选区）
        last_off = 0 if _is_fence(last_line) else len(_line_raw(last_line))
        # 起始行若为围栏块，从下一非围栏行开始
        start_li = 0
        while start_li < last_li and _is_fence(document.lines[start_li]):
            start_li += 1
        if start_li >= last_li and _is_fence(document.lines[start_li]):
            return  # 全文档均为围栏块，无可选文本
        if cursor_li is not None:
            suppress_blur.current = True
            set_cursor_li(None)
        _set_outward_sel((start_li, 0, last_li, last_off))

    # ============ 滚动 / 导航 ============
    def _on_scroll(e):
        try:
            scroll_offset_ref.current = e.pixels
            if hasattr(e, "max_scroll_extent"):
                max_scroll_ref.current = e.max_scroll_extent
            if hasattr(e, "viewport_dimension"):
                viewport_h_ref.current = e.viewport_dimension
        except Exception:
            pass

    def on_line_size_change(li: int, height: float):
        """LineView on_size_change 回调：缓存行实际渲染高度。

        容差 0.5px 内不更新，避免 layout 抖动引发无效写入。缓存供
        _estimate_line_offset 精确累加滚动偏移，未命中的行回退到估算。
        """
        cache = line_heights_ref.current
        if height > 0 and abs(cache.get(li, 0.0) - height) > 0.5:
            cache[li] = height

    def _estimate_line_height(li: int) -> float:
        """估算单行渲染高度（px）。优先用 on_size_change 上报的实际高度。

        未命中缓存（行未构建或已被销毁）时按块类型估算：
        - CODE：头部工具栏 + 每代码行 + padding（多行内容必须计入）
        - 其它：block_text_size × line_height + 上下 padding(2×Spacing.XS)
        """
        cache = line_heights_ref.current
        cached = cache.get(li)
        if cached is not None and cached > 0:
            return cached
        if not (0 <= li < len(document.lines)):
            return body_font_size * line_height + 4
        line = document.lines[li]
        base = block_text_size(line.block_type, line.level)
        if line.block_type == BlockType.CODE:
            code = line.segments[0].text if line.segments else ""
            code_lines = max(1, code.count("\n") + 1)
            # 头部工具栏(~28) + 代码行(14×line_height) + 容器 padding(~12)
            return 28 + code_lines * 14 * line_height + 12
        # 普通行：字号 × 行高 + 上下 padding（Spacing.XS × 2 = 4）
        return base * line_height + 4

    def _estimate_line_offset(li: int) -> float:
        """累加 0..li 行高，得到目标行顶部的 y 偏移（相对 ListView 内容起点）。

        比旧的 li × (目标行字号 × line_height + 4) 估算准确得多：
        - 各行按自身字号/块类型累加，而非统一用目标行字号
        - 已构建行用实测高度，消除代码块/长段落换行/表格的估算偏差
        """
        return sum(_estimate_line_height(j) for j in range(li))

    async def _safe_scroll_to(li: int, to_top: bool = False):
        """异步滚动：Flet 的 scroll_to 是协程，需 await。

        Args:
            li: 目标行索引
            to_top: True=滚动到视口顶部（大纲跳转），False=仅在不可见时滚动（光标导航）

        两步滚动（to_top=True 且目标行未构建时）：
        build_controls_on_demand 下视口外的行尚未构建，无实测高度缓存。
        第一步用估算 offset 滚动到目标附近，触发 ListView 构建目标行并经
        on_size_change 上报实测高度到 line_heights_ref；等待一帧后第二步
        用缓存中的实测高度重新累加 offset，精确滚到视口顶部。这样首次点击
        大纲项即可贴顶，无需第二次点击。缓存已命中时一步到位。
        """
        if list_view_ref.current is None:
            return
        try:
            top_padding = content_padding_top
            if to_top:
                cache = line_heights_ref.current
                already_built = cache.get(li, 0.0) > 0
                est_y = _estimate_line_offset(li)
                target_scroll = max(0, est_y - top_padding)
                if already_built:
                    # 缓存命中：目标行已构建，一步精确到位
                    await list_view_ref.current.scroll_to(target_scroll, duration=200)
                    return
                # 缓存未命中：第一步估算滚动，触发目标行动态构建
                await list_view_ref.current.scroll_to(target_scroll, duration=150)
                # 等待 Flutter layout 完成 + on_size_change 上报实测高度
                await asyncio.sleep(0.15)
                # 第二步：用缓存中的实测高度重新累加，精确贴顶
                precise_y = _estimate_line_offset(li)
                precise_scroll = max(0, precise_y - top_padding)
                if abs(precise_scroll - target_scroll) > 4:
                    await list_view_ref.current.scroll_to(
                        precise_scroll, duration=120
                    )
            else:
                target_y = _estimate_line_offset(li)
                target_h = _estimate_line_height(li)
                viewport = viewport_h_ref.current or 600
                cur = scroll_offset_ref.current
                if target_y < cur + 40:
                    await list_view_ref.current.scroll_to(
                        max(0, target_y - 40), duration=100
                    )
                elif target_y + target_h > cur + viewport - 40:
                    await list_view_ref.current.scroll_to(
                        max(0, target_y + target_h - viewport + 40), duration=100
                    )
        except Exception:
            pass

    def _ensure_visible(li: int):
        page = ft.context.page
        if page is not None:
            page.run_task(_safe_scroll_to, li)

    def _hit_test_line_x(li: int, x: float) -> int:
        """跨行拖拽用：返回目标行 raw 偏移。"""
        if not (0 <= li < len(document.lines)):
            return 0
        from views.pixel_layout import hit_test_line_x
        line = document.lines[li]
        base = block_text_size(line.block_type, line.level)
        return hit_test_line_x(line, x, base)

    def _page_rows() -> int:
        viewport = viewport_h_ref.current or 600
        return max(1, int(viewport / (body_font_size * line_height + 4)))

    def page_up():
        if cursor_li is not None:
            _vertical_goto(max(0, cursor_li - _page_rows()))
        else:
            page = ft.context.page
            if page is not None:
                page.run_task(_scroll_by_page, -1)

    def page_down():
        if cursor_li is not None:
            _vertical_goto(min(len(document.lines) - 1, cursor_li + _page_rows()))
        else:
            page = ft.context.page
            if page is not None:
                page.run_task(_scroll_by_page, 1)

    async def _scroll_by_page(direction: int):
        if list_view_ref.current is None:
            return
        try:
            delta = direction * (viewport_h_ref.current or 600) * 0.9
            await list_view_ref.current.scroll_to(
                scroll_offset_ref.current + delta, duration=100
            )
        except Exception:
            pass

    def jump_to(li: int):
        if not (0 <= li < len(document.lines)):
            return
        line = document.lines[li]
        if _is_fence(line):
            set_cursor_line(li)
            set_cursor_li(None)
        else:
            _set_cursor(li, 0)
        # 跳转目标行脉冲高亮：置 flash_li 触发重渲染，1.2s 后异步清回 -1 淡出
        set_flash_li(li)
        page = ft.context.page
        if page is not None:
            page.run_task(_safe_scroll_to, li, to_top=True)

            async def _clear_flash():
                await asyncio.sleep(1.2)
                set_flash_li(-1)

            page.run_task(_clear_flash)

    def _get_cursor_row_col() -> tuple[int, int]:
        if cursor_li is not None and 0 <= cursor_li < len(document.lines):
            return (cursor_li + 1, cursor_off + 1)
        return (cursor_line + 1, 1)

    def _line_highlight_range(li: int) -> tuple[int, int] | None:
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

    # ============ use_effect：聚焦 cursor TextField ============
    async def _focus_cursor_field():
        if cursor_li is not None and cursor_field_ref.current is not None:
            try:
                await cursor_field_ref.current.focus()
            except Exception:
                pass

    # 依赖 cursor_li + cursor_off：切换行或同行内光标位置变化时重新聚焦
    # 同行内点击定位时 cursor_off 变化，需重新 focus 以保持光标可见
    # IME 安全：_set_cursor 已在光标偏移不连续时调用 _end_input_session()
    ft.use_effect(_focus_cursor_field, [cursor_li, cursor_off])

    # ============ use_effect：文档行数变化时清空行高缓存 ============
    # 插入/删除整行会让 line_idx 错位，旧缓存的高度会对应到错误的行。
    # 行数不变的单行内容编辑由 on_size_change 自动更新对应条目，无需清空。
    def _reset_line_heights():
        line_heights_ref.current = {}

    ft.use_effect(_reset_line_heights, [len(document.lines)])

    # ============ use_effect：清空 cursor TextField 内部 value ============
    # _end_input_session 通过 set_clear_value_seq(+1) 触发此 effect，
    # 在重渲染后异步清空 Flutter 端 TextField 的 value。
    # cursor_text_field 不设 value 属性 → 重渲染不同步 value → IME 组合不被打断。
    # 仅在会话结束（光标移动/Enter/Backspace）时清空，此时 IME 已结束，安全。
    async def _clear_cursor_value():
        if cursor_field_ref.current is not None:
            try:
                cursor_field_ref.current.value = ""
                await cursor_field_ref.current.update()
            except Exception:
                pass

    ft.use_effect(_clear_cursor_value, [clear_value_seq])

    # ============ EditorActions 上报 ============
    active_line = (
        document.lines[cursor_li]
        if cursor_li is not None and 0 <= cursor_li < len(document.lines)
        else None
    )
    if nav_ref is not None:
        nav_ref.current = EditorActions(
            cursor_li=cursor_li,
            cursor_off=cursor_off,
            active_line=active_line,
            raw_mode=raw_mode,
            cursor_ref=cursor_ref,
            selection_text_ref=selection_text_ref,
            nav_seq=nav_seq,
            move_left=move_left,
            move_right=move_right,
            move_home=move_home,
            move_end=move_end,
            move_doc_start=move_doc_start,
            move_doc_end=move_doc_end,
            move_up=move_up,
            move_down=move_down,
            page_up=page_up,
            page_down=page_down,
            backspace_core=backspace_core,
            delete_core=delete_core,
            indent_or_outdent=indent_or_outdent,
            handle_paste=handle_paste,
            handle_cut=handle_cut,
            handle_delete_selection=handle_delete_selection,
            apply_inline_format_to_selection=apply_inline_format_to_selection,
            compute_markdown_from_text=compute_markdown_from_text,
            handle_outward_copy=handle_outward_copy,
            select_all=select_all,
            undo=undo,
            redo=redo,
            jump_to_line=jump_to,
            toggle_raw=toggle_raw,
            toggle_focus_mode=toggle_focus_mode,
            set_block=set_block,
            apply_inline_format=apply_inline_format,
            code_focus_ref=code_focus_ref,
            table_focus_ref=table_focus_ref,
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
            clear_outward_sel=clear_outward_sel,
        )

    # ============ TOC 条目 ============
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

    content_width = content_max_width - 2 * content_padding

    # ============ 行视图列表 ============
    line_controls = []
    i = 0
    while i < len(document.lines):
        line = document.lines[i]
        is_act = cursor_li == i and cursor_li is not None
        if line.block_type == BlockType.TABLE:
            table_start = i
            while (
                i + 1 < len(document.lines)
                and document.lines[i + 1].block_type == BlockType.TABLE
            ):
                i += 1
            table_end = i
            line_controls.append(
                TableView(
                    key=f"table-{table_start}",
                    lines=document.lines,
                    line_idx=table_start,
                    content_width=content_width,
                    clipboard_ref=clipboard_ref,
                    on_change_cell=on_change_cell,
                    on_table_op=on_table_op,
                    on_table_focus=on_table_focus,
                    on_table_blur=on_table_blur,
                    table_nav_ref=table_nav_ref,
                    is_current_line=table_start <= cursor_line <= table_end,
                    # 版本号触发 prop：lines 列表与首行 raw 长度变化时触发 memo 刷新
                    lines_version=len(document.lines),
                    first_line_raw_version=(
                        len(document.lines[table_start].raw)
                        if 0 <= table_start < len(document.lines) else 0
                    ),
                )
            )
        else:
            line_controls.append(
                LineView(
                    key=f"line-{i}",
                    line=line,
                    line_idx=i,
                    cursor_off=cursor_off if is_act else None,
                    cursor_ref=cursor_ref if is_act else None,
                    nav_seq=nav_seq if is_act else 0,
                    field_ref=cursor_field_ref if is_act else None,
                    content_width=content_width,
                    line_height=line_height,
                    is_current_line=is_act,
                    is_flash=flash_li == i,
                    # 版本号触发 prop：reparse_line 就地修改 line 对象不替换引用，
                    # ft.memo 浅比较 line 引用未变会误判未刷新。通过 raw 长度 + 段数
                    # 两个值变化触发 memo 检测，让屏幕刷新。
                    line_raw_version=len(line.raw) if line.raw else 0,
                    line_seg_count=len(line.segments),
                    on_cursor_change=handle_char_input if is_act else None,
                    on_cursor_submit=on_submit if is_act else None,
                    on_cursor_blur=on_blur if is_act else None,
                    on_tap=_on_tap_line,
                    on_pan_start=on_extend_outward,
                    on_pan_update=on_extend_outward,
                    on_toggle_task=toggle_task,
                    on_change_code=on_change_code,
                    on_code_focus=on_code_focus,
                    on_code_blur=on_code_blur,
                    on_change_lang=change_lang,
                    clipboard_ref=clipboard_ref,
                    toc_entries=toc_entries,
                    on_jump_to=jump_to,
                    on_line_size_change=on_line_size_change,
                    outward_range=_line_highlight_range(i),
                    on_extend_outward=on_extend_outward,
                    on_clear_outward=clear_outward_sel,
                    shift_pressed_ref=shift_pressed_ref,
                    ctrl_pressed_ref=ctrl_pressed_ref,
                    on_hit_test_x=_hit_test_line_x,
                )
            )
        i += 1

    # ============ 工具区 ============
    def _tool_area():
        menu_items = [
            ft.PopupMenuItem(content="新建", on_click=lambda e: (on_new or _noop)()),
            ft.PopupMenuItem(content="打开...", on_click=lambda e: (on_open or _noop)()),
            ft.PopupMenuItem(content="保存", on_click=lambda e: (on_save or _noop)()),
            ft.PopupMenuItem(),
            ft.PopupMenuItem(content="设置", on_click=lambda e: (on_open_settings or _noop)()),
        ]
        if not show_toolbar:
            return ft.Container(height=0)
        return ft.Container(
            bgcolor=ft.Colors.with_opacity(0.96, c.toolbar_bg),
            border=only_border(bottom=ft.BorderSide(1, c.border)),
            padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
            content=ft.Row(
                controls=[
                    ft.PopupMenuButton(
                        icon=ft.Icons.MENU,
                        tooltip="文件菜单",
                        items=menu_items,
                    ),
                    _tb_divider(),
                    Toolbar(
                        shortcut_mgr=shortcut_mgr,
                        on_h1=lambda: set_block(BlockType.HEADING, 1),
                        on_h2=lambda: set_block(BlockType.HEADING, 2),
                        on_h3=lambda: set_block(BlockType.HEADING, 3),
                        on_paragraph=lambda: set_block(BlockType.PARAGRAPH),
                        on_list=lambda: set_block(BlockType.LIST_UO),
                        on_quote=lambda: set_block(BlockType.QUOTE),
                        on_code_block=lambda: set_block(BlockType.CODE),
                        on_hr=lambda: set_block(BlockType.HR),
                        on_bold=lambda: apply_inline_format("bold"),
                        on_italic=lambda: apply_inline_format("italic"),
                        on_highlight=lambda: apply_inline_format("highlight"),
                        on_code=lambda: apply_inline_format("code"),
                        on_link=lambda: apply_inline_format("link"),
                        on_strike=lambda: apply_inline_format("strike"),
                    ),
                    ft.Container(expand=True),
                    _btn(
                        ft.Icons.VISIBILITY if not raw_mode else ft.Icons.EDIT,
                        "原文模式" if not raw_mode else "返回编辑",
                        toggle_raw,
                        toggle_on=raw_mode,
                    ),
                    _btn(ft.Icons.FILE_DOWNLOAD, "导出 HTML", on_export or _noop),
                    _btn(ft.Icons.CENTER_FOCUS_STRONG, "聚焦模式", toggle_focus_mode),
                    _btn(
                        ft.Icons.DARK_MODE if theme_mode == ft.ThemeMode.LIGHT else ft.Icons.LIGHT_MODE,
                        "切换暗色" if theme_mode == ft.ThemeMode.LIGHT else "切换亮色",
                        on_toggle_theme or _noop,
                    ),
                    _btn(ft.Icons.SETTINGS, "设置  Ctrl+,", on_open_settings or _noop),
                ],
                spacing=Spacing.MD,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    # ============ 键盘事件 ============
    def _on_key_down(e):
        key = (getattr(e, "key", "") or "").lower()
        if key.startswith("shift"):
            shift_pressed_ref.current = True
        if key.startswith("control"):
            ctrl_pressed_ref.current = True
        # 表格 Tab/Escape 路由
        if table_focus_ref.current is not None and table_nav_ref.current is not None:
            if key == "tab" and not ctrl_pressed_ref.current:
                table_nav_ref.current("tab", -1 if shift_pressed_ref.current else 1)
                return
            if key == "escape":
                table_nav_ref.current("escape")
                return
        # 渲染态行内格式快捷键
        if cursor_li is None:
            if ctrl_pressed_ref.current:
                combo = "ctrl+shift+s" if shift_pressed_ref.current and key == "s" else None
                if combo is None:
                    combo = f"ctrl+{key}" if key else None
                if combo in ("ctrl+b", "ctrl+i", "ctrl+u", "ctrl+shift+s", "ctrl+`", "ctrl+k"):
                    if nav_ref is not None and nav_ref.current is not None:
                        fmt = {
                            "ctrl+b": "bold",
                            "ctrl+i": "italic",
                            "ctrl+u": "highlight",
                            "ctrl+shift+s": "strike",
                            "ctrl+`": "code",
                            "ctrl+k": "link",
                        }[combo]
                        nav_ref.current.apply_inline_format_to_selection(fmt, combo)
                        return
            return

    def _on_key_up(e):
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
                            content=ft.ListView(
                                ref=list_view_ref,
                                controls=line_controls,
                                expand=True,
                                spacing=0,
                                auto_scroll=False,
                                build_controls_on_demand=True,
                                cache_extent=800,
                                on_scroll=_on_scroll,
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
