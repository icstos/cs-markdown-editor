"""编辑器对外动作集合：替代 nav_ref.current = {20+ 字符串 key} 大字典。

依赖项：
- 标准库 collections.abc、dataclasses、typing
- flet（ft.Ref 类型）
- models（BlockType、Line）

对外接口：EditorActions。

行为约束（来自项目 memory Hard Constraints）：
- main.py 的 on_key 通过 actions.move_left() 等属性访问（替代 nav["move_left"]()）
- 必填字段在 dataclass 构造时即校验，缺失立即报错（替代静默失败）
- cursor_ref 为 ft.Ref[CursorState]：main.py 通过 actions.cursor_ref.current.base
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


@dataclass
class EditorActions:
    """编辑器在每次渲染时上抛给 App 层（main.py on_key / KeyDispatcher / 状态栏）的动作集合。

    所有字段在构造时必填——缺失即报错，避免 nav.get("xxx") 静默失败。
    """

    # ---- 当前状态（每次渲染重建，main.py 据此判断 browse/edit 层）----
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

    # ---- 代码块（始终可编辑 CodeEditor 独立岛屿）----
    code_focus_ref: ft.Ref

    # ---- 表格（始终可编辑 DataTable2 独立岛屿）----
    table_focus_ref: ft.Ref

    # ---- 状态栏 ----
    get_cursor_row_col: Callable[[], tuple[int, int]]

    # ---- 向外选区（Shift+Click / Shift+Arrow / 拖拽 起始的跨行选区）----
    # outward_sel = (anchor_li, anchor_off, active_li, active_off) | None
    outward_sel: tuple | None = None
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

    # ---- 链接语法 Typora 式交互 ----
    # 打字替换 outward 选区（浏览态选中→输入即替换，通用基础编辑行为）
    handle_outward_type_char: Callable[[str], None] | None = None
    # outward_sel 态 Tab 在链接 text/url 字段间跳转，返回是否消费（False 则 fall-through）
    jump_link_field: Callable[[int], bool] | None = None
    # 编辑态 Tab 在链接 text/url 字段间跳转，返回是否消费（False 则 fall-through 到缩进）
    jump_link_cursor: Callable[[int], bool] | None = None
