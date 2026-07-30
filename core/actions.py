"""编辑器对外动作集合：替代 nav_ref.current = {20+ 字符串 key} 大字典。

依赖项：
- 标准库 collections.abc、dataclasses、typing
- flet（ft.Ref 类型）
- models（BlockType、Line）

对外接口：EditorActions。

行为约束（来自项目 memory Hard Constraints）：
- app/ 包（原 main.py 拆分）的 on_key 通过 actions.move_left() 等属性访问
  （替代 nav["move_left"]() 字符串下标）
- 必填字段在 dataclass 构造时即校验，缺失立即报错（替代 nav.get("xxx") 静默失败）
- cursor_ref 为 ft.Ref[CursorState]：app/ 包通过 actions.cursor_ref.current.base
  / .extent 实时读取光标位置（这些值在 on_selection_change 中直接修改，非
  set_state 触发，不能用渲染期快照字段）

Stack 双层光标级架构（Typora 式 WYSIWYG）：
- cursor_li / cursor_off 替代旧的 active / active_seg / draft 三状态
- cursor_li=None 表示浏览态（无激活行）；int 表示光标在某行
- cursor_off 为行级 raw 偏移 0..len(line.raw)
- nav_seq 仅撤销/重做递增，强制 cursor_text_field key 重建以刷新内部状态；
  同行输入不递增以保持 IME 组合态
- 透明 cursor_text_field 不设 value 属性（IME 友好），value 清空由 editor 端
  use_effect 异步执行；每个字符输入即时渲染到文档（无段级编辑态）
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import flet as ft

from models import BlockType, Line

# PEP 695 类型别名：向外选区与滚动状态，替代裸 tuple 注解
type OutwardSel = tuple[int, int, int, int] | None  # (anchor_li, anchor_off, active_li, active_off)
type ScrollState = tuple[float, float, float]  # (offset, max_scroll_extent, viewport_height)


@dataclass(slots=True)
class EditorActions:
    """编辑器在每次渲染时上抛给 App 层（app/ 包 on_key / KeyDispatcher / 状态栏）的动作集合。

    所有字段在构造时必填——缺失即报错，避免 nav.get("xxx") 静默失败。
    装配流程：views/editor/_actions.py build_actions(ctx) 构造实例，
    经 EditorContext 上抛，app/_keyboard.py 的 KeyDispatcher 消费。
    """

    # ---- 当前状态（每次渲染重建，app/ 包据此判断 browse/edit 层）----
    cursor_li: int | None  # 激活行号 | None（浏览态）
    cursor_off: int  # 行级 raw 偏移 0..len(line.raw)
    active_line: Line | None  # = lines[cursor_li] if cursor_li is not None else None
    raw_mode: bool
    cursor_ref: ft.Ref  # ft.Ref[CursorState]：实时光标位置（main.py 读 .current.base/.extent）
    selection_text_ref: ft.Ref
    nav_seq: int  # 撤销/重做时递增以强制 cursor_text_field key 重建

    # ---- 行间光标导航 ----
    move_left: Callable[[], None]
    move_right: Callable[[], None]
    move_home: Callable[[], None]
    move_end: Callable[[], None]
    move_doc_start: Callable[[], None]  # Ctrl+Home：跳到文档首行
    move_doc_end: Callable[[], None]  # Ctrl+End：跳到文档末行
    move_up: Callable[[], None]
    move_down: Callable[[], None]
    page_up: Callable[[], None]  # PageUp：光标上移一页 / 浏览态纯滚动
    page_down: Callable[[], None]  # PageDown：光标下移一页 / 浏览态纯滚动

    # ---- 删除 / 缩进 ----
    backspace_core: Callable[[], None]
    delete_core: Callable[[], None]
    indent_or_outdent: Callable[[int], None]

    # ---- 剪贴板 / 选区 ----
    handle_paste: Callable[[str, str], None]
    handle_cut: Callable[[str], Any]  # async
    handle_delete_selection: Callable[[str], None]
    apply_inline_format_to_selection: Callable[[str, str], None]
    compute_markdown_from_text: Callable[[str], str]

    # ---- 全局动作 ----
    undo: Callable[[], None]
    redo: Callable[[], None]
    jump_to_line: Callable[[int], None]
    toggle_raw: Callable[[], None]
    toggle_focus_mode: Callable[[], None]
    set_block: Callable[[BlockType, int], None]  # 切换当前行块类型（Ctrl+0~6 标题级别）
    apply_inline_format: Callable[[str], None]  # 行内格式快捷键入口
    toggle_task_at_cursor: Callable[[], None]  # Alt+C：切换当前任务列表项勾选状态
    format_task: Callable[[], None]  # Ctrl+Shift+T：当前行转为任务列表项（- [ ]）
    format_table: Callable[[], None]  # Ctrl+Alt+T：当前行转为 2×2 表格

    # ---- 代码块（始终可编辑 CodeEditor 独立岛屿）----
    code_focus_ref: ft.Ref
    # 空代码块聚焦时 Backspace → 删除整个代码块（Typora 式）。
    # 返回 True 已处理（消费按键），False 未处理（继续原生 CodeEditor 删除）。
    handle_code_backspace: Callable[[int], bool]

    # ---- 表格（始终可编辑 DataTable2 独立岛屿）----
    table_focus_ref: ft.Ref

    # ---- 状态栏 ----
    get_cursor_row_col: Callable[[], tuple[int, int]]

    # ---- 向外选区（Shift+Click / Shift+Arrow / 拖拽 起始的跨行选区）----
    # outward_sel = (anchor_li, anchor_off, active_li, active_off) | None
    outward_sel: OutwardSel = None
    # ---- 块级公式（点击进入编辑态的独立岛屿）----
    math_focus_ref: ft.Ref | None = None
    shift_pressed_ref: ft.Ref | None = None  # Shift 键状态（editor 内部跟踪）
    ctrl_pressed_ref: ft.Ref | None = None  # Ctrl 键状态（主同步源 KeyDispatcher.e.ctrl）
    extend_outward_left: Callable[[], None] | None = None
    extend_outward_right: Callable[[], None] | None = None
    extend_outward_up: Callable[[], None] | None = None
    extend_outward_down: Callable[[], None] | None = None
    handle_outward_cut: Callable[[], Awaitable[None]] | None = None  # async
    handle_outward_delete: Callable[[], None] | None = None
    handle_outward_copy: Callable[[], Awaitable[None]] | None = None  # Ctrl+C：复制 outward_sel 选区文本
    clear_outward_sel: Callable[[], None] | None = None
    select_all: Callable[[], None] | None = None  # Ctrl+A：全选文档
    cut_current_line: Callable[[], Awaitable[None]] | None = None  # async：无选区时剪切当前行（VSCode 行为）

    # ---- 向外选区打字替换（通用基础编辑行为：浏览态选中→输入即替换）----
    # 链接编辑回归常规文本编辑：光标在链接段内时渲染层显示完整语法，离开则折叠；
    # 无需 Tab 字段跳转等专用动作。
    handle_outward_type_char: Callable[[str], None] | None = None

    # ---- 滚动同步（diff 对比模式：左右编辑器像素偏移同步）----
    # get_scroll_state：返回 (offset, max_scroll_extent, viewport_height)，
    #   供 main.py 读取当前滚动位置做同步对齐。
    # scroll_to_offset：同步调度异步 scroll_to(offset, duration=0)，对外非阻塞。
    #   duration=0 保证跟随滚轮即时响应，无动画延迟。
    get_scroll_state: Callable[[], ScrollState] | None = None
    scroll_to_offset: Callable[[float], None] | None = None
