"""EditorContext：编辑器状态容器（每次渲染整体重建）。

双区设计：
- 稳定区：所有 use_ref 对象 + 所有 set_* setter（身份跨渲染不变）+ document
- 快照区：当次渲染的 state 值（cursor_li / cursor_off / outward_sel 等）

工厂函数签名统一：build_xxx(ctx: EditorContext) -> dict[str, Callable]。
工厂内禁止任何 use_* hook（硬性规则）。跨工厂调用通过 ctx 装配槽（普通属性赋值）。

装配顺序（依赖拓扑序）：
  scroll → focus → key → cursor → history → navigation → outward
  → indent → blocks → inline_format → clipboard → fence → raw_mode

依赖项：
- flet（ft.Ref / ft.ThemeMode）
- models（Document）
- core.cursor（CursorState）
- core.history（EditHistory）
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import flet as ft

from models import Document


async def _noop_awaitable() -> bool:
    """paste_image_from_clipboard 装配槽默认值（未装配时返回 False）。"""
    return False


@dataclass
class EditorContext:
    """编辑器上下文：持有所有状态供工厂函数访问。

    使用普通 dataclass（非 frozen、非 slots）以支持装配槽动态赋值。
    每次渲染在 __init__.py 中整体重建，传入各工厂函数。
    """

    # ============ Props（组件参数）============
    document: Document
    file_path: str | None
    on_new: Callable[[], None] | None
    on_open: Callable[[], None] | None
    on_open_folder: Callable[[], None] | None
    on_save: Callable[[], None] | None
    on_export: Callable[[], None] | None
    on_dirty_change: Callable[[bool], None] | None
    nav_ref: ft.Ref | None
    clipboard_ref: ft.Ref | None
    picker_ref: ft.Ref | None
    theme_mode: ft.ThemeMode
    on_toggle_theme: Callable[[], None] | None
    settings: dict[str, Any]
    on_open_settings: Callable[[], None] | None
    sidebar_open: bool
    on_toggle_sidebar: Callable[[], None] | None
    shortcut_mgr: Any
    show_toolbar: bool
    on_editor_focus: Callable[[], None] | None
    keyboard_autofocus: bool
    diff_marks: dict[int, str] | None
    diff_gaps: dict[int, list[float]] | None
    on_scroll_change: Callable[[float, float, float], None] | None
    # 状态栏命令式上报：光标移动 / 内容变化（高频局部 UI，跳过 set_state 全量重建）
    on_cursor_move: Callable[[int, int], None] | None
    on_content_change: Callable[[], None] | None

    # ============ 派生设置 ============
    c: Any  # colors 对象
    content_max_width: int
    content_padding: int
    content_padding_top: int
    show_footer: bool
    body_font_size: int
    line_height: float
    word_wrap: bool
    content_width: float  # 段落自适应宽度（word_wrap 时取 viewport_w - padding，否则 inf）

    # ============ State 值（快照区，每次渲染更新）============
    cursor_li: int | None
    cursor_off: int
    nav_seq: int
    focus_seq: int
    wrap_sel_seq: int  # 软换行触发一次性的选区折叠序号（>0 时折叠 TextField 选区）
    cursor_line: int
    clear_value_seq: int
    cursor_field_value: str
    raw_mode: bool
    raw_draft: str
    viewport_w: float
    outward_sel: tuple[int, int, int, int] | None
    flash_li: int
    table_focus_li: int | None
    math_focus_li: int | None
    # 多光标：每个副光标 (li, base, extent)，base==extent 无选区
    secondary_cursors: list[tuple[int, int, int]]
    # 多光标版本号：每次 _sync 递增，传给 LineView 强制 ft.memo 失效
    secondary_cursors_version: int

    # ============ Setters（稳定区，跨渲染身份不变）============
    set_cursor_li: Callable[[int | None], None]
    set_cursor_off: Callable[[int], None]
    set_nav_seq: Callable[[int], None]
    set_focus_seq: Callable[[int], None]
    set_wrap_sel_seq: Callable[[int], None]
    set_cursor_line: Callable[[int], None]
    set_clear_value_seq: Callable[[int], None]
    set_cursor_field_value: Callable[[str], None]
    set_raw_mode: Callable[[bool], None]
    set_raw_draft: Callable[[str], None]
    set_viewport_w: Callable[[float], None]
    set_flash_li: Callable[[int], None]
    set_table_focus_li: Callable[[int | None], None]
    set_math_focus_li: Callable[[int | None], None]
    set_secondary_cursors: Callable[[list[tuple[int, int, int]]], None]
    set_secondary_cursors_version: Callable[[int], None]

    # ============ Refs（稳定区，跨渲染身份不变）============
    cursor_field_ref: ft.Ref
    input_session_ref: ft.Ref
    cursor_ref: ft.Ref  # CursorState
    suppress_blur: ft.Ref  # bool
    list_view_ref: ft.Ref
    scroll_offset_ref: ft.Ref  # float
    viewport_h_ref: ft.Ref  # float
    max_scroll_ref: ft.Ref  # float
    viewport_w_ref: ft.Ref  # float
    line_heights_ref: ft.Ref  # dict[int, float]
    layout_cache_ref: ft.Ref  # LineLayoutCache | None
    offset_prefix_ref: ft.Ref  # list[float] | None
    preferred_col_ref: ft.Ref  # float | None
    selection_text_ref: ft.Ref  # str
    history_ref: ft.Ref  # EditHistory
    restoring: ft.Ref  # bool
    undo_push_pending: ft.Ref  # bool
    outward_sel_ref: ft.Ref  # outward_sel 镜像
    shift_pressed_ref: ft.Ref  # bool
    ctrl_pressed_ref: ft.Ref  # bool
    code_focus_ref: ft.Ref  # int | None
    code_edit_snapshot: ft.Ref  # EditorSnapshot | None
    code_edit_changed: ft.Ref  # bool
    table_focus_ref: ft.Ref  # int | None（镜像 table_focus_li）
    table_nav_ref: ft.Ref  # Callable | None
    math_focus_ref: ft.Ref  # int | None（镜像 math_focus_li）
    math_field_ref: ft.Ref  # ft.Control | None
    math_edit_snapshot: ft.Ref  # EditorSnapshot | None
    math_edit_changed: ft.Ref  # bool
    # 光标离开编辑器（cursor TextField 真实失焦）回调：触发即时自动保存等
    on_editor_blur: Callable[[], None] | None = None
    # 多光标：ref 镜像（IME 期间同步读取），alt 键状态 ref
    secondary_cursors_ref: ft.Ref = field(default=None)
    alt_pressed_ref: ft.Ref = field(default=None)
    # 粘贴进行中标志（拦截原生 TextField 单行粘贴的 on_change 干扰）
    # KeyDispatcher Ctrl+V 时置 True；_do_paste_check 完成后置 False。
    # handle_char_input 入口检测：True 时跳过（由 handle_paste 统一处理）。
    paste_in_progress_ref: ft.Ref = field(default=None)
    # float：上次同行移动脉冲时间戳（monotonic），_set_cursor 节流重建用
    cursor_pulse_ref: ft.Ref = field(default=None)
    # 上/下键自驱动重复任务标志（asyncio.Task | None），_key.py 长按导航用
    arrow_repeat_ref: ft.Ref = field(default=None)

    # ============ 装配槽（跨工厂调用，工厂装配后写入）============
    # 共享闭包
    mark_dirty: Callable[[], None] = field(default=lambda: None)
    set_outward_sel: Callable[[Any], None] = field(default=lambda v: None)

    # cursor 组
    cursor_base: Callable[..., int] = field(default=lambda *a: 0)
    set_cursor: Callable[..., None] = field(default=lambda *a: None)
    end_input_session: Callable[[], None] = field(default=lambda: None)
    on_tap_line: Callable[[int, int], None] = field(default=lambda *a: None)
    handle_char_input: Callable[[str], None] = field(default=lambda *a: None)
    handle_paste: Callable[..., None] = field(default=lambda *a: None)
    handle_paste_plain: Callable[..., None] = field(default=lambda *a: None)
    backspace_core: Callable[[], None] = field(default=lambda: None)
    delete_core: Callable[[], None] = field(default=lambda: None)
    on_submit: Callable[[str], None] = field(default=lambda *a: None)

    # history 组
    make_snapshot: Callable[..., Any] = field(default=lambda: None)
    push_history: Callable[[], None] = field(default=lambda: None)
    push_line_edit: Callable[[int, str], None] = field(default=lambda *a: None)
    maybe_push_history: Callable[[], None] = field(default=lambda: None)
    undo: Callable[[], None] = field(default=lambda: None)
    redo: Callable[[], None] = field(default=lambda: None)

    # navigation 组
    move_left: Callable[[], None] = field(default=lambda: None)
    move_right: Callable[[], None] = field(default=lambda: None)
    move_home: Callable[[], None] = field(default=lambda: None)
    move_end: Callable[[], None] = field(default=lambda: None)
    move_doc_start: Callable[[], None] = field(default=lambda: None)
    move_doc_end: Callable[[], None] = field(default=lambda: None)
    move_up: Callable[[], None] = field(default=lambda: None)
    move_down: Callable[[], None] = field(default=lambda: None)
    move_vline: Callable[[int, int], None] = field(default=lambda *a: None)
    page_up: Callable[[], None] = field(default=lambda: None)
    page_down: Callable[[], None] = field(default=lambda: None)
    jump_to: Callable[[int, int | None], None] = field(default=lambda *a: None)
    cursor_vline_info: Callable[..., Any] = field(default=lambda *a: None)
    get_line_visual_lines: Callable[..., Any] = field(default=lambda *a: None)
    link_tab_jump: Callable[[int], bool] = field(default=lambda *a: False)

    # scroll 组
    on_scroll: Callable[[Any], None] = field(default=lambda *a: None)
    get_scroll_state: Callable[[], tuple[float, float, float]] = field(
        default=lambda: (0.0, 0.0, 0.0)
    )
    scroll_to_offset: Callable[[float], None] = field(default=lambda *a: None)
    on_content_resize: Callable[[Any], None] = field(default=lambda *a: None)
    on_line_size_change: Callable[[int, float], None] = field(default=lambda *a: None)
    ensure_visible: Callable[..., None] = field(default=lambda *a: None)
    safe_scroll_to: Any = field(default=None)
    estimate_line_height: Callable[[int], float] = field(default=lambda *a: 0.0)
    estimate_line_offset: Callable[[int], float] = field(default=lambda *a: 0.0)
    hit_test_line_x: Callable[[int, float], int] = field(default=lambda *a: 0)
    get_layout_cache: Callable[..., Any] = field(default=lambda: None)
    hit_test_xy: Callable[..., Any] = field(default=lambda *a: None)
    page_vlines: Callable[[], int] = field(default=lambda: 0)
    scroll_by_page: Any = field(default=None)
    reset_line_heights: Callable[[], None] = field(default=lambda: None)
    get_cursor_row_col: Callable[[], tuple[int, int]] = field(default=lambda: (0, 0))
    build_highlight_map: Callable[[], dict[int, tuple[int, int]]] = field(default=lambda: {})

    # outward 组
    step_left: Callable[..., Any] = field(default=lambda *a: None)
    step_right: Callable[..., Any] = field(default=lambda *a: None)
    step_up: Callable[..., Any] = field(default=lambda *a: None)
    step_down: Callable[..., Any] = field(default=lambda *a: None)
    step_home: Callable[..., Any] = field(default=lambda *a: None)
    step_end: Callable[..., Any] = field(default=lambda *a: None)
    start_outward_from_point: Callable[..., None] = field(default=lambda *a: None)
    extend_outward: Callable[..., None] = field(default=lambda *a: None)
    extend_outward_step: Callable[..., None] = field(default=lambda *a: None)
    select_word_at: Callable[[int, int], None] = field(default=lambda *a: None)
    on_extend_outward: Callable[..., None] = field(default=lambda *a: None)
    on_pan_start_outward: Callable[..., None] = field(default=lambda *a: None)
    delete_raw_range: Callable[..., None] = field(default=lambda *a: None)
    handle_outward_delete: Callable[[], None] = field(default=lambda: None)
    handle_outward_cut: Any = field(default=None)
    handle_outward_copy: Any = field(default=None)
    select_all: Callable[[], None] = field(default=lambda: None)
    clear_outward_sel: Callable[[], None] = field(default=lambda: None)

    # indent 组
    indent_or_outdent: Callable[[int], None] = field(default=lambda *a: None)
    new_line_after: Callable[[int], None] = field(default=lambda *a: None)

    # blocks 组
    set_block: Callable[..., None] = field(default=lambda *a: None)
    toggle_task: Callable[[int], None] = field(default=lambda *a: None)
    toggle_task_at_cursor: Callable[[], None] = field(default=lambda: None)
    format_task: Callable[[], None] = field(default=lambda: None)
    format_table: Callable[[], None] = field(default=lambda: None)
    format_document: Callable[[], None] = field(default=lambda: None)
    change_lang: Callable[[int, str], None] = field(default=lambda *a: None)
    insert_text: Callable[[str], None] = field(default=lambda *a: None)

    # inline_format 组
    apply_inline_format: Callable[[str], None] = field(default=lambda *a: None)
    insert_inline_at: Callable[[str, int, int], None] = field(default=lambda *a: None)
    apply_outward_wrap: Callable[[str], None] = field(default=lambda *a: None)
    handle_outward_type_char: Callable[[str], None] = field(default=lambda *a: None)

    # clipboard 组
    compute_markdown_from_text: Callable[[str], str] = field(default=lambda *a: "")
    handle_delete_selection: Callable[[str], None] = field(default=lambda *a: None)
    handle_cut: Any = field(default=None)
    cut_current_line: Any = field(default=None)
    apply_inline_format_to_selection: Callable[[str, str], None] = field(default=lambda *a: None)
    on_selection_area_change: Callable[[Any], None] = field(default=lambda *a: None)

    # fence 组
    on_change_code: Callable[[int, str], None] = field(default=lambda *a: None)
    on_code_focus: Callable[[int], None] = field(default=lambda *a: None)
    on_code_blur: Callable[[int], None] = field(default=lambda *a: None)
    # 空代码块 Backspace 删除：返回 True 已处理（消费 Backspace），False 未处理
    handle_code_backspace: Callable[[int], bool] = field(default=lambda *a: False)
    on_change_math: Callable[[int, str], None] = field(default=lambda *a: None)
    on_math_focus: Callable[[int], None] = field(default=lambda *a: None)
    on_math_blur: Callable[[int], None] = field(default=lambda *a: None)
    on_change_cell: Callable[[int, int, str], None] = field(default=lambda *a: None)
    on_table_op: Callable[[str, dict], None] = field(default=lambda *a: None)
    on_table_focus: Callable[[], None] = field(default=lambda: None)
    on_table_blur: Callable[[], None] = field(default=lambda: None)

    # raw_mode 组
    toggle_raw: Callable[[], None] = field(default=lambda: None)
    toggle_focus_mode: Callable[[], None] = field(default=lambda: None)
    on_blur: Callable[[], None] = field(default=lambda: None)
    on_cursor_focus: Callable[[], None] = field(default=lambda: None)
    suppress_blur_for_click: Callable[[], None] = field(default=lambda: None)
    on_raw_change: Callable[[str], None] = field(default=lambda *a: None)

    # focus 组（use_effect 回调）
    focus_cursor_field: Callable[[], None] = field(default=lambda: None)
    clear_cursor_value: Callable[[], None] = field(default=lambda: None)
    focus_math_field: Callable[[], None] = field(default=lambda: None)

    # key 组
    on_key_down: Callable[[Any], None] = field(default=lambda *a: None)
    on_key_up: Callable[[Any], None] = field(default=lambda *a: None)

    # replace 组（搜索面板触发，作用于当前文档；new_text 已完成反向引用展开）
    replace_match_in_doc: Callable = field(default=lambda *a: None)
    replace_all_in_doc: Callable = field(default=lambda *a: 0)

    # image 组（图片右键菜单操作分发 + Ctrl+V 图片粘贴，由 build_image 装配）
    on_image_action: Callable[[str, int, int, str, str], None] = field(default=lambda *a: None)
    # async：True 已处理图片粘贴（调用方跳过文本粘贴），False 剪贴板无图片
    paste_image_from_clipboard: Callable[..., Awaitable[bool]] = field(
        default=_noop_awaitable
    )

    # ============ 多光标装配槽（build_multi_cursor 装配）============
    # add_secondary_cursor(li, off)：Alt+Click 切换副光标
    # add_column_cursors(target_li, target_off)：Alt+Shift+Click 列光标
    # clear_secondary_cursors()：清空所有副光标（Escape）
    # broadcast_char_input(removed_len, inserted)：主光标输入后同步到副光标
    # broadcast_backspace() / broadcast_delete()：删除键同步
    # broadcast_move_left() / broadcast_move_right()：左右移动同步
    # broadcast_extend_left() / broadcast_extend_right()：Shift+Left/Right 选区同步
    # broadcast_submit(value)：Enter 同步分行
    # has_secondary_cursors()：是否有多光标（用于路由判断）
    add_secondary_cursor: Callable[[int, int], None] = field(default=lambda *a: None)
    add_column_cursors: Callable[[int, int], None] = field(default=lambda *a: None)
    clear_secondary_cursors: Callable[[], None] = field(default=lambda: None)
    broadcast_char_input: Callable[[int, str], None] = field(default=lambda *a: None)
    broadcast_backspace: Callable[[], None] = field(default=lambda: None)
    broadcast_delete: Callable[[], None] = field(default=lambda: None)
    broadcast_move_left: Callable[[], None] = field(default=lambda: None)
    broadcast_move_right: Callable[[], None] = field(default=lambda: None)
    broadcast_extend_left: Callable[[], None] = field(default=lambda: None)
    broadcast_extend_right: Callable[[], None] = field(default=lambda: None)
    broadcast_submit: Callable[[str], None] = field(default=lambda *a: None)
    has_secondary_cursors: Callable[[], bool] = field(default=lambda: False)
    extend_selection_left: Callable[[], None] = field(default=lambda: None)
    extend_selection_right: Callable[[], None] = field(default=lambda: None)
    extend_selection_home: Callable[[], None] = field(default=lambda: None)
    extend_selection_end: Callable[[], None] = field(default=lambda: None)
    # 多光标剪贴板（Ctrl+C/X/V 同步选区操作）
    has_multi_cursor_selection: Callable[[], bool] = field(default=lambda: False)
    collect_multi_cursor_text: Callable[[], list[str] | None] = field(default=lambda: None)
    copy_multi_cursor_selection: Callable[[], Awaitable[None]] = field(default=lambda: None)
    cut_multi_cursor_selection: Callable[[], Awaitable[None]] = field(default=lambda: None)
    paste_to_multi_cursors: Callable[[str], None] = field(default=lambda *a: None)
    paste_to_multi_cursors_plain: Callable[[str], None] = field(default=lambda *a: None)
