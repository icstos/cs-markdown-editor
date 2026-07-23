"""编辑器对外动作集合：替代 nav_ref.current = {20+ 字符串 key} 大字典。

行为约束（来自 memory Hard Constraints）：
- main.py 的 on_key 通过 actions.move_left() 等属性访问（替代 nav["move_left"]()）
- 必填字段在 dataclass 构造时即校验，缺失立即报错（替代静默失败）
- 字段集合对应 main.py on_key 中所有 nav.get("xxx") 访问点
- cursor_ref 为 ft.Ref[CursorState]：main.py 通过 actions.cursor_ref.current.base
  / .extent / .draft_len 实时读取光标位置。这些值在 on_selection_change 中直接修改
  cursor_ref.current（非 set_state 触发），不能用渲染期快照字段，否则 main.py
  ArrowLeft/ArrowRight 越界判断会读到 stale 值。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import flet as ft

from models import Line


@dataclass
class EditorActions:
    """编辑器在每次渲染时上抛给 App 层（main.py on_key / 状态栏）的动作集合。

    所有字段在构造时必填——缺失即报错，避免 main.py 用 nav.get("xxx") 静默
    失败的旧问题。字段命名与 main.py 原先访问的 nav key 一一对应，方便对照。
    """

    # ---- 当前状态（每次渲染重建，main.py 据此判断 browse/edit 层）----
    active: int | None  # line_idx | None（Typora 式行级编辑）
    active_seg: int | None  # seg_idx | None（段级编辑：当前编辑段索引）
    draft: str
    active_line: Line | None
    raw_mode: bool
    cursor_ref: ft.Ref  # ft.Ref[CursorState]：实时光标位置（main.py 读 .current.base/.extent/.draft_len）
    selection_text_ref: ft.Ref

    # ---- 段间 / 行间光标导航 ----
    move_left: Callable[[], None]
    move_right: Callable[[], None]
    move_home: Callable[[], None]
    move_end: Callable[[], None]
    move_doc_start: Callable[[], None]  # Ctrl+Home：跳到文档首行
    move_doc_end: Callable[[], None]  # Ctrl+End：跳到文档末行
    move_up: Callable[[], None]
    move_down: Callable[[], None]
    page_up: Callable[[], None]  # PageUp：编辑态光标上移一页 / 浏览态纯滚动
    page_down: Callable[[], None]  # PageDown：编辑态光标下移一页 / 浏览态纯滚动

    # ---- 删除 / 缩进 ----
    backspace_core: Callable[[], None]
    delete_core: Callable[[], None]
    indent_or_outdent: Callable[[int], None]

    # ---- 剪贴板 / 选区 ----
    handle_paste: Callable[[str, str], None]
    handle_cut: Callable[[str], Any]  # async
    handle_delete_selection: Callable[[str], None]
    compute_markdown_from_text: Callable[[str], str]

    # ---- 全局动作 ----
    undo: Callable[[], None]
    redo: Callable[[], None]
    jump_to_line: Callable[[int], None]
    toggle_raw: Callable[[], None]
    toggle_focus_mode: Callable[[], None]

    # ---- 代码块（始终可编辑 CodeEditor 独立岛屿）----
    # 当前聚焦的代码块行索引 | None。KeyDispatcher 据此在代码编辑时跳过全局导航/
    # 剪贴板键，交由 CodeEditor 原生处理 Tab/Backspace/方向键/复制等。
    code_focus_ref: ft.Ref

    # ---- 状态栏 ----
    get_cursor_row_col: Callable[[], tuple[int, int]]

    # ---- 向外选区（Shift+Click / Shift+Arrow 起始的跨段/跨行选区）----
    # outward_sel = (anchor_li, anchor_off, active_li, active_off) | None
    outward_sel: tuple | None = None
    shift_pressed_ref: ft.Ref | None = None  # Shift 键状态（editor 内部跟踪）
    ctrl_pressed_ref: ft.Ref | None = None  # Ctrl 键状态（主同步源 KeyDispatcher.e.ctrl）
    extend_outward_left: Callable[[], None] | None = None
    extend_outward_right: Callable[[], None] | None = None
    extend_outward_up: Callable[[], None] | None = None
    extend_outward_down: Callable[[], None] | None = None
    handle_outward_cut: Callable[[], Awaitable[None]] | None = None  # async
    handle_outward_delete: Callable[[], None] | None = None
    handle_segment_cut_sync: Callable[[], str | None] | None = None  # 同步：捕获选区+剪切+提交，返回选中文本
    handle_segment_cut_clipboard: Callable[[str], Awaitable[None]] | None = None  # async：写入剪贴板
    clear_outward_sel: Callable[[], None] | None = None
