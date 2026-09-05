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
from dataclasses import dataclass, field
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
    link_tab_jump: Callable[[int], bool]  # 链接段内 Tab/Shift+Tab 字段跳转（text↔url↔段尾）；返回 True 已处理，False 走默认缩进

    # ---- 删除 / 缩进 ----
    backspace_core: Callable[[], None]
    delete_core: Callable[[], None]
    indent_or_outdent: Callable[[int], None]

    # ---- 剪贴板 / 选区 ----
    handle_paste: Callable[[str, str], None]
    handle_paste_plain: Callable[[str, str], None]  # Ctrl+Shift+V：纯文本粘贴（剥离 Markdown 语法）
    handle_cut: Callable[[str], Any]  # async
    handle_delete_selection: Callable[[str], None]
    apply_inline_format_to_selection: Callable[[str, str], None]
    compute_markdown_from_text: Callable[[str], str]

    # ---- 全局动作 ----
    undo: Callable[[], None]
    redo: Callable[[], None]
    jump_to_line: Callable[[int, int | None], None]  # (li, off=None)；off=None 退化为行首
    toggle_raw: Callable[[], None]
    toggle_focus_mode: Callable[[], None]
    set_block: Callable[[BlockType, int], None]  # 切换当前行块类型（Ctrl+0~6 标题级别）
    apply_inline_format: Callable[[str], None]  # 行内格式快捷键入口
    toggle_task_at_cursor: Callable[[], None]  # Alt+C：切换当前任务列表项勾选状态
    format_task: Callable[[], None]  # Ctrl+Shift+T：当前行转为任务列表项（- [ ]）
    format_table: Callable[[], None]  # Ctrl+Alt+T：当前行转为 2×2 表格
    insert_text: Callable[[str], None]  # Ctrl+; / Ctrl+Shift+;：在光标处插入模板文本
    format_document: Callable[[], None]  # Shift+Alt+F：全文 Markdown 格式化（含撤销）

    # ---- 代码块（始终可编辑 CodeEditor 独立岛屿）----
    code_focus_ref: ft.Ref
    # 空代码块聚焦时 Backspace → 删除整个代码块（Typora 式）。
    # 返回 True 已处理（消费按键），False 未处理（继续原生 CodeEditor 删除）。
    handle_code_backspace: Callable[[int], bool]
    # 代码块边界方向键跳出（Typora 式）：↑/← 从第一行跳出、↓/→ 从最后一行跳出，
    # 无相邻行时创建新行。code_caret_ref 为 CodeEditor 光标跟踪
    # (value, base_offset, extent_offset)；handle_code_exit(norm) 返回 True 已消费。
    code_caret_ref: ft.Ref
    handle_code_exit: Callable[[str], bool]

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
    alt_pressed_ref: ft.Ref | None = None  # Alt 键状态（KeyDispatcher.e.alt 同步，供 Alt+Click 分发）
    extend_outward_left: Callable[[], None] | None = None
    extend_outward_right: Callable[[], None] | None = None
    extend_outward_up: Callable[[], None] | None = None
    extend_outward_down: Callable[[], None] | None = None
    extend_outward_home: Callable[[], None] | None = None  # Shift+Home：选区扩展到行首
    extend_outward_end: Callable[[], None] | None = None  # Shift+End：选区扩展到行尾
    handle_outward_cut: Callable[[], Awaitable[None]] | None = None  # async
    handle_outward_delete: Callable[[], None] | None = None
    handle_outward_enter: Callable[[], None] | None = None  # Enter：删除选区后在删除点换行
    handle_outward_copy: Callable[[], Awaitable[None]] | None = None  # Ctrl+C：复制 outward_sel 选区文本
    clear_outward_sel: Callable[[], None] | None = None
    select_all: Callable[[], None] | None = None  # Ctrl+A：全选文档
    cut_current_line: Callable[[], Awaitable[None]] | None = None  # async：无选区时剪切当前行（VSCode 行为）

    # ---- 向外选区打字替换（通用基础编辑行为：浏览态选中→输入即替换）----
    # 链接编辑回归常规文本编辑：光标在链接段内时渲染层显示完整语法，离开则折叠；
    # Tab 字段跳转由 link_tab_jump 处理（text↔url↔段尾），不依赖专用状态机。
    handle_outward_type_char: Callable[[str], None] | None = None

    # ---- 多光标（VSCode 式 Alt+Click / Alt+Shift+Click）----
    # has_secondary_cursors：是否有多光标（KeyDispatcher 路由判断用）
    # clear_secondary_cursors：Escape 清空所有副光标
    # extend_selection_left/right：Shift+Arrow 扩展所有光标选区（主+副）
    # has_multi_cursor_selection：是否有任何光标有选区（Ctrl+C/X 路由判断）
    # copy/cut_multi_cursor_selection：多光标复制/剪切（收集所有选区文本）
    # paste_to_multi_cursors：多光标粘贴（智能逐行分配或全文插入）
    has_secondary_cursors: Callable[[], bool] = field(default=lambda: False)
    clear_secondary_cursors: Callable[[], None] = field(default=lambda: None)
    extend_selection_left: Callable[[], None] = field(default=lambda: None)
    extend_selection_right: Callable[[], None] = field(default=lambda: None)
    extend_selection_home: Callable[[], None] = field(default=lambda: None)
    extend_selection_end: Callable[[], None] = field(default=lambda: None)
    has_multi_cursor_selection: Callable[[], bool] = field(default=lambda: False)
    copy_multi_cursor_selection: Callable[[], Awaitable[None]] | None = None  # async
    cut_multi_cursor_selection: Callable[[], Awaitable[None]] | None = None  # async
    paste_to_multi_cursors: Callable[[str], None] | None = None
    paste_to_multi_cursors_plain: Callable[[str], None] | None = None  # Ctrl+Shift+V：多光标纯文本粘贴

    # ---- 滚动同步（diff 对比模式：左右编辑器像素偏移同步）----
    # get_scroll_state：返回 (offset, max_scroll_extent, viewport_height)，
    #   供 main.py 读取当前滚动位置做同步对齐。
    # scroll_to_offset：同步调度异步 scroll_to(offset, duration=0)，对外非阻塞。
    #   duration=0 保证跟随滚轮即时响应，无动画延迟。
    get_scroll_state: Callable[[], ScrollState] | None = None
    scroll_to_offset: Callable[[float], None] | None = None

    # ---- 替换（搜索面板触发，作用于当前文档；new_text 已在 Sidebar 完成反向引用展开）----
    # 替换单个匹配 (li, start..end) → new_text
    replace_match_in_doc: Callable[[int, int, int, str], None] | None = None
    # 批量替换：replacements = [(li, [(s, e, new_text), ...]), ...]，行内右→左保偏移；返回替换条数
    replace_all_in_doc: Callable[[list[tuple[int, list[tuple[int, int, str]]]]], int] | None = None

    # ---- 图片粘贴（Ctrl+V：剪贴板含图片/图片文件 → 落盘 ./assets/ 插入 ![](...)）----
    # async：True 已处理图片粘贴（调用方跳过文本粘贴），False 剪贴板无图片
    paste_image_from_clipboard: Callable[[], Awaitable[bool]] | None = None

    # ---- 粘贴进行中标志（拦截原生 TextField 单行粘贴 on_change 干扰）----
    # Ctrl+V 时 KeyDispatcher 置 True；handle_char_input 入口检测到 True 则跳过，
    # 由 _do_paste_check 走 handle_paste 统一处理（避免单行 TextField 把多行文本
    # 拼接成一行触发 on_change 与 handle_paste 重复插入）。
    paste_in_progress_ref: ft.Ref | None = None

    # ---- 文档内搜索（浮层）扩展动作（可选装配，无则 None）----
    # 跳转到指定行并把该行滚动到视口中部（搜索上一个/下一个，平滑 200ms+）。
    jump_to_line_center: Callable[[int, int | None], None] | None = None
    # 把键盘焦点交还编辑器编辑区（浮层关闭后调用）。
    focus_document: Callable[[], None] | None = None
