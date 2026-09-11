"""编辑器工厂的显式依赖契约（`typing.Protocol`）。

背景：`EditorContext` 曾是 ~200 字段的单体容器，20 个 `build_xxx(ctx)` 工厂
各自从中读取任意字段——「这个模块依赖什么」只能靠通读函数体才知道。本模块为
每个工厂声明一份**精确依赖契约**（只列该工厂实际读取的字段），于是：

- 工厂签名即依赖清单，`build_cursor(env: CursorEnv)` 一眼看清边界；
- 结构调整时静态可查（配 mypy/pyright 时字段改名会在调用点报错）；
- `EditorContext` 结构性满足全部契约，运行期行为与性能零变化。

契约字段集合由 AST 从各工厂函数体静态提取，并由
``tests/test_editor_contracts.py`` 守护其与实际读取集合一致。
"""

from collections.abc import Callable
from typing import Any, Protocol

import flet as ft

from models.document import Document


class CursorEnv(Protocol):
    """光标 / IME 输入核心组 的依赖契约（build_cursor 实际读取的 40 个字段）。"""

    add_column_cursors: Callable[..., Any]
    add_secondary_cursor: Callable[..., Any]
    alt_pressed_ref: ft.Ref
    broadcast_backspace: Callable[..., Any]
    broadcast_char_input: Callable[..., Any]
    broadcast_delete: Callable[..., Any]
    broadcast_submit: Callable[..., Any]
    clear_secondary_cursors: Callable[..., Any]
    cursor_field_ref: ft.Ref
    cursor_li: int | None
    cursor_off: int
    cursor_pulse_ref: ft.Ref
    cursor_ref: ft.Ref
    document: Document
    ensure_visible: Callable[..., Any]
    focus_seq: int
    handle_outward_delete: Callable[..., Any]
    input_session_ref: ft.Ref
    mark_dirty: Callable[..., None]
    math_focus_li: int
    nav_seq: int
    outward_sel_ref: ft.Ref
    paste_in_progress_ref: ft.Ref
    preferred_col_ref: ft.Ref
    push_history: Callable[..., Any]
    push_line_edit: Callable[..., Any]
    secondary_cursors_ref: ft.Ref
    set_cursor: Callable[..., None]
    set_cursor_field_value: Callable[..., None]
    set_cursor_li: Callable[..., None]
    set_cursor_line: Callable[..., None]
    set_cursor_off: Callable[..., None]
    set_focus_seq: Callable[..., None]
    set_math_focus_li: Callable[..., None]
    set_nav_seq: Callable[..., None]
    set_outward_sel: Callable[..., None]
    set_wrap_sel_seq: Callable[..., None]
    shift_pressed_ref: ft.Ref
    suppress_blur: Any
    undo_push_pending: Any


class HistoryEnv(Protocol):
    """撤销 / 重做 的依赖契约（build_history 实际读取的 19 个字段）。"""

    code_edit_changed: bool
    code_edit_snapshot: Any
    cursor_base: Callable[..., Any]
    cursor_li: int | None
    document: Document
    history_ref: ft.Ref
    mark_dirty: Callable[..., None]
    nav_seq: int
    raw_draft: str
    raw_mode: bool
    restoring: bool
    set_cursor_li: Callable[..., None]
    set_cursor_line: Callable[..., None]
    set_cursor_off: Callable[..., None]
    set_nav_seq: Callable[..., None]
    set_raw_draft: Callable[..., None]
    set_raw_mode: Callable[..., None]
    suppress_blur: Any
    undo_push_pending: Any


class FormatEnv(Protocol):
    """全文 Markdown 格式化 的依赖契约（build_format 实际读取的 11 个字段）。"""

    document: Document
    mark_dirty: Callable[..., None]
    nav_seq: int
    push_history: Callable[..., Any]
    raw_draft: str
    raw_mode: bool
    restoring: bool
    set_cursor_li: Callable[..., None]
    set_cursor_off: Callable[..., None]
    set_nav_seq: Callable[..., None]
    set_raw_draft: Callable[..., None]


class ScrollEnv(Protocol):
    """滚动 / 行高缓存 / 命中测试 的依赖契约（build_scroll 实际读取的 26 个字段）。"""

    body_font_size: float
    content_padding_top: int
    content_width: float
    cursor_base: Callable[..., Any]
    cursor_li: int | None
    cursor_line: int
    cursor_off: int
    cursor_vline_info: Any
    document: Document
    layout_cache_ref: ft.Ref
    line_height: float
    line_heights_ref: ft.Ref
    list_view_ref: ft.Ref
    max_scroll_ref: ft.Ref
    move_vline: Callable[..., Any]
    offset_prefix_ref: ft.Ref
    on_scroll_change: Callable[..., Any]
    outward_sel: tuple[int, int, int, int] | None
    scroll_offset_ref: ft.Ref
    set_cursor: Callable[..., None]
    set_cursor_li: Callable[..., None]
    set_cursor_line: Callable[..., None]
    set_flash_li: Callable[..., None]
    set_viewport_w: Callable[..., None]
    viewport_h_ref: ft.Ref
    viewport_w_ref: ft.Ref


class NavigationEnv(Protocol):
    """光标移动（视觉行 / 垂直导航） 的依赖契约（build_navigation 实际读取的 16 个字段）。"""

    body_font_size: float
    broadcast_move_left: Callable[..., Any]
    broadcast_move_right: Callable[..., Any]
    clear_secondary_cursors: Callable[..., Any]
    content_width: float
    cursor_base: Callable[..., Any]
    cursor_li: int | None
    document: Document
    ensure_visible: Callable[..., Any]
    layout_cache_ref: ft.Ref
    line_height: float
    preferred_col_ref: ft.Ref
    secondary_cursors_ref: ft.Ref
    set_cursor: Callable[..., None]
    set_cursor_li: Callable[..., None]
    set_cursor_line: Callable[..., None]


class OutwardEnv(Protocol):
    """向外选区 的依赖契约（build_outward 实际读取的 19 个字段）。"""

    clipboard_ref: ft.Ref
    cursor_base: Callable[..., Any]
    cursor_li: int | None
    cursor_vline_info: Any
    document: Document
    focus_seq: int
    get_line_visual_lines: Callable[..., Any]
    mark_dirty: Callable[..., None]
    on_submit: Callable[..., None]
    outward_sel: tuple[int, int, int, int] | None
    outward_sel_ref: ft.Ref
    preferred_col_ref: ft.Ref
    push_history: Callable[..., Any]
    set_cursor: Callable[..., None]
    set_cursor_li: Callable[..., None]
    set_focus_seq: Callable[..., None]
    set_outward_sel: Callable[..., None]
    suppress_blur: Any
    undo_push_pending: Any


class IndentEnv(Protocol):
    """缩进 / 反缩进 的依赖契约（build_indent 实际读取的 9 个字段）。"""

    cursor_base: Callable[..., Any]
    cursor_li: int | None
    document: Document
    focus_seq: int
    mark_dirty: Callable[..., None]
    push_history: Callable[..., Any]
    set_cursor: Callable[..., None]
    set_focus_seq: Callable[..., None]
    undo_push_pending: Any


class BlocksEnv(Protocol):
    """块级操作（标题 / 任务 / 表格） 的依赖契约（build_blocks 实际读取的 19 个字段）。"""

    cursor_base: Callable[..., Any]
    cursor_li: int | None
    cursor_line: int
    document: Document
    focus_seq: int
    make_snapshot: Callable[..., Any]
    mark_dirty: Callable[..., None]
    math_edit_changed: bool
    math_edit_snapshot: Any
    math_focus_ref: ft.Ref
    maybe_push_history: Callable[..., Any]
    push_history: Callable[..., Any]
    set_cursor: Callable[..., None]
    set_cursor_li: Callable[..., None]
    set_cursor_line: Callable[..., None]
    set_focus_seq: Callable[..., None]
    set_math_focus_li: Callable[..., None]
    set_table_focus_li: Callable[..., None]
    undo_push_pending: Any


class InlineFormatEnv(Protocol):
    """行内格式（加粗 / 斜体 / 链接） 的依赖契约（build_inline_format 实际读取的 11 个字段）。"""

    cursor_base: Callable[..., Any]
    cursor_li: int | None
    document: Document
    mark_dirty: Callable[..., None]
    nav_seq: int
    outward_sel_ref: ft.Ref
    push_history: Callable[..., Any]
    set_cursor: Callable[..., None]
    set_nav_seq: Callable[..., None]
    set_outward_sel: Callable[..., None]
    undo_push_pending: Any


class ClipboardEnv(Protocol):
    """剪贴板 / SelectionArea 选区 的依赖契约（build_clipboard 实际读取的 20 个字段）。"""

    apply_inline_format: Callable[..., Any]
    clipboard_ref: ft.Ref
    cursor_base: Callable[..., Any]
    cursor_li: int | None
    cursor_line: int
    delete_raw_range: Callable[..., Any]
    document: Document
    insert_inline_at: Callable[..., Any]
    mark_dirty: Callable[..., None]
    nav_seq: int
    outward_sel_ref: ft.Ref
    push_history: Callable[..., Any]
    raw_mode: bool
    selection_text_ref: ft.Ref
    set_cursor: Callable[..., None]
    set_cursor_li: Callable[..., None]
    set_cursor_line: Callable[..., None]
    set_nav_seq: Callable[..., None]
    set_outward_sel: Callable[..., None]
    undo_push_pending: Any


class FenceEnv(Protocol):
    """围栏岛屿（代码 / 公式 / 表格） 的依赖契约（build_fence 实际读取的 21 个字段）。"""

    code_caret_ref: ft.Ref
    code_edit_changed: bool
    code_edit_snapshot: Any
    code_focus_ref: ft.Ref
    cursor_li: int | None
    document: Document
    history_ref: ft.Ref
    make_snapshot: Callable[..., Any]
    mark_dirty: Callable[..., None]
    math_edit_changed: bool
    math_edit_snapshot: Any
    math_focus_ref: ft.Ref
    maybe_push_history: Callable[..., Any]
    push_history: Callable[..., Any]
    restoring: bool
    set_cursor: Callable[..., None]
    set_cursor_li: Callable[..., None]
    set_math_focus_li: Callable[..., None]
    set_table_focus_li: Callable[..., None]
    suppress_blur: Any
    undo_push_pending: Any


class RawModeEnv(Protocol):
    """原文模式 / 聚焦模式 的依赖契约（build_raw_mode 实际读取的 13 个字段）。"""

    document: Document
    mark_dirty: Callable[..., None]
    on_editor_blur: Callable[..., Any]
    on_editor_focus: Callable[..., Any]
    push_history: Callable[..., Any]
    raw_draft: str
    raw_mode: bool
    selection_text_ref: ft.Ref
    set_cursor_li: Callable[..., None]
    set_raw_draft: Callable[..., None]
    set_raw_mode: Callable[..., None]
    suppress_blur: Any
    undo_push_pending: Any


class FocusEnv(Protocol):
    """光标 / 公式输入框聚焦 的依赖契约（build_focus 实际读取的 6 个字段）。"""

    cursor_field_ref: ft.Ref
    cursor_li: int | None
    ensure_visible: Callable[..., Any]
    line_heights_ref: ft.Ref
    math_field_ref: ft.Ref
    math_focus_li: int


class KeyEnv(Protocol):
    """组件级键盘事件 的依赖契约（build_key 实际读取的 9 个字段）。"""

    alt_pressed_ref: ft.Ref
    arrow_repeat_ref: ft.Ref
    clear_secondary_cursors: Callable[..., Any]
    ctrl_pressed_ref: ft.Ref
    nav_ref: ft.Ref
    secondary_cursors_ref: ft.Ref
    shift_pressed_ref: ft.Ref
    table_focus_ref: ft.Ref
    table_nav_ref: ft.Ref


class ImageEnv(Protocol):
    """图片交互 的依赖契约（build_image 实际读取的 14 个字段）。"""

    clipboard_ref: ft.Ref
    cursor_base: Callable[..., Any]
    cursor_li: int | None
    cursor_line: int
    document: Document
    file_path: str | None
    mark_dirty: Callable[..., None]
    nav_seq: int
    picker_ref: ft.Ref
    push_history: Callable[..., Any]
    set_cursor: Callable[..., None]
    set_cursor_line: Callable[..., None]
    set_nav_seq: Callable[..., None]
    undo_push_pending: Any


class MultiCursorEnv(Protocol):
    """多光标 的依赖契约（build_multi_cursor 实际读取的 24 个字段）。"""

    clipboard_ref: ft.Ref
    cursor_li: int | None
    cursor_off: int
    cursor_ref: ft.Ref
    document: Document
    end_input_session: Callable[..., None]
    handle_paste: Callable[..., Any]
    input_session_ref: ft.Ref
    mark_dirty: Callable[..., None]
    nav_seq: int
    paste_in_progress_ref: ft.Ref
    preferred_col_ref: ft.Ref
    push_history: Callable[..., Any]
    push_line_edit: Callable[..., Any]
    secondary_cursors_ref: ft.Ref
    secondary_cursors_version: int
    set_cursor_field_value: Callable[..., None]
    set_cursor_off: Callable[..., None]
    set_nav_seq: Callable[..., None]
    set_secondary_cursors: Callable[..., None]
    set_secondary_cursors_version: Callable[..., None]
    step_end: Callable[..., Any]
    step_home: Callable[..., Any]
    undo_push_pending: Any


class ReplaceEnv(Protocol):
    """搜索面板替换 的依赖契约（build_replace 实际读取的 5 个字段）。"""

    document: Document
    mark_dirty: Callable[..., None]
    push_history: Callable[..., Any]
    restoring: bool
    undo_push_pending: Any


class ActionsEnv(Protocol):
    """EditorActions 装配（对外动作契约） 的依赖契约（build_actions 实际读取的 83 个字段）。"""

    alt_pressed_ref: ft.Ref
    apply_inline_format: Callable[..., Any]
    apply_inline_format_to_selection: Callable[..., Any]
    backspace_core: Callable[..., None]
    clear_outward_sel: Callable[..., None]
    clear_secondary_cursors: Callable[..., Any]
    code_caret_ref: ft.Ref
    code_focus_ref: ft.Ref
    compute_markdown_from_text: Callable[..., Any]
    copy_multi_cursor_selection: Any
    ctrl_pressed_ref: ft.Ref
    cursor_li: int | None
    cursor_off: int
    cursor_ref: ft.Ref
    cut_current_line: Any
    cut_multi_cursor_selection: Any
    delete_core: Callable[..., None]
    document: Document
    extend_outward_step: Callable[..., Any]
    extend_selection_end: Callable[..., Any]
    extend_selection_home: Callable[..., Any]
    extend_selection_left: Callable[..., Any]
    extend_selection_right: Callable[..., Any]
    format_document: Callable[..., Any]
    format_table: Callable[..., Any]
    format_task: Callable[..., Any]
    get_cursor_row_col: Callable[..., tuple[int, int]]
    get_scroll_state: Callable[..., tuple[float, float, float]]
    handle_code_backspace: Callable[..., bool]
    handle_code_exit: Callable[..., bool]
    handle_cut: Any
    handle_delete_selection: Callable[..., Any]
    handle_outward_copy: Any
    handle_outward_cut: Any
    handle_outward_delete: Callable[..., Any]
    handle_outward_enter: Callable[..., Any]
    handle_outward_type_char: Callable[..., Any]
    handle_paste: Callable[..., Any]
    handle_paste_plain: Callable[..., Any]
    has_multi_cursor_selection: Callable[..., bool]
    has_secondary_cursors: Callable[..., bool]
    indent_or_outdent: Callable[..., None]
    insert_text: Callable[..., Any]
    jump_to: Callable[..., Any]
    link_tab_jump: Callable[..., bool]
    math_focus_ref: ft.Ref
    move_doc_end: Callable[..., None]
    move_doc_start: Callable[..., None]
    move_down: Callable[..., None]
    move_end: Callable[..., None]
    move_home: Callable[..., None]
    move_left: Callable[..., None]
    move_right: Callable[..., None]
    move_up: Callable[..., None]
    nav_ref: ft.Ref
    nav_seq: int
    outward_sel: tuple[int, int, int, int] | None
    page_down: Callable[..., None]
    page_up: Callable[..., None]
    paste_image_from_clipboard: Any
    paste_in_progress_ref: ft.Ref
    paste_to_multi_cursors: Callable[..., Any]
    paste_to_multi_cursors_plain: Callable[..., Any]
    raw_mode: bool
    redo: Callable[..., None]
    replace_all_in_doc: Callable[..., Any]
    replace_match_in_doc: Callable[..., Any]
    scroll_to_offset: Callable[..., Any]
    select_all: Callable[..., None]
    selection_text_ref: ft.Ref
    set_block: Callable[..., None]
    shift_pressed_ref: ft.Ref
    step_down: Callable[..., Any]
    step_end: Callable[..., Any]
    step_home: Callable[..., Any]
    step_left: Callable[..., Any]
    step_right: Callable[..., Any]
    step_up: Callable[..., Any]
    table_focus_ref: ft.Ref
    toggle_focus_mode: Callable[..., None]
    toggle_raw: Callable[..., None]
    toggle_task_at_cursor: Callable[..., Any]
    undo: Callable[..., None]


class LineControlsEnv(Protocol):
    """行控件列表构造 的依赖契约（build_line_controls 实际读取的 35 个字段）。"""

    alt_pressed_ref: ft.Ref
    body_font_size: float
    c: Any
    clipboard_ref: ft.Ref
    content_width: float
    ctrl_pressed_ref: ft.Ref
    cursor_field_ref: ft.Ref
    cursor_field_value: str
    cursor_li: int | None
    cursor_line: int
    cursor_off: int
    cursor_ref: ft.Ref
    diff_gaps: dict[int, list[float]] | None
    diff_marks: dict[int, str] | None
    document: Document
    file_path: str | None
    flash_li: int
    handle_char_input: Callable[..., Any]
    input_session_ref: ft.Ref
    line_height: float
    math_field_ref: ft.Ref
    math_focus_li: int
    nav_seq: int
    on_blur: Callable[..., None]
    on_cursor_focus: Callable[..., None]
    on_submit: Callable[..., None]
    search_hits: dict[int, list]
    search_hits_version: Any
    secondary_cursors: list
    secondary_cursors_version: int
    shift_pressed_ref: ft.Ref
    table_focus_li: int
    table_nav_ref: ft.Ref
    theme_mode: Any
    wrap_sel_seq: int
