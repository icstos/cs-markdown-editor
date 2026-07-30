"""AppContext：应用层状态容器（每次渲染整体重建）。

双区设计（同 EditorContext）：
- 稳定区：所有 use_ref 对象 + 所有 set_* setter（身份跨渲染不变）
- 快照区：当次渲染的 state 值

控制器签名统一：build_xxx(ctx: AppContext) -> dict[str, Callable]。
控制器内禁止任何 use_* hook（硬性规则）。跨控制器调用通过 ctx 装配槽。

装配顺序（依赖拓扑序）：
  tab_management → file_io_ops → file_dialogs → diff_controller
  → settings_controller → split_editor → focus_router → keyboard

打破 shortcut_mgr ↔ update_setting 前向引用循环：
  shortcut_mgr 构造时用 lambda 捕获 update_setting_ref，
  控制器装配后 update_setting_ref.current = settings_cbs["update_setting"]

依赖项：
- flet（ft.Ref / ft.ThemeMode）
- app.diff_scroll_sync.DiffScrollSync
- services.shortcuts.ShortcutManager
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import flet as ft


@dataclass(kw_only=True)
class AppContext:
    """应用层上下文：持有所有状态供控制器函数访问。

    使用普通 dataclass（非 frozen、非 slots，kw_only=True）以支持装配槽动态赋值
    且无字段顺序约束（有 default 的装配槽字段可位于无 default 的 state/setter/ref
    字段之前）。每次渲染在 app/__init__.py 中整体重建，传入各控制器函数。
    """

    # ============ State 值（快照区）============
    tabs: list
    active_index: int
    session: int
    confirm_close: list | None
    file_dialog: dict | None
    compare_source: str | None
    diff_active_pane: int
    theme_mode: ft.ThemeMode
    settings: dict
    settings_open: bool
    settings_tab: str
    shortcut_focus: tuple
    capturing: tuple
    split_editor: bool
    active_pane: int
    fs_version: int  # 文件系统版本号：文件增删改后递增，驱动侧边栏文件树重扫

    # ============ 派生值 ============
    cur_tab: dict
    is_diff_tab: bool
    document: Any  # Document | None
    file_path: str | None
    shortcut_mgr: Any  # ShortcutManager
    diff_sync: Any  # DiffScrollSync
    diff_result: Any  # memoized (marks_l, marks_r, gaps_l, gaps_r, added, removed, modified) | None

    # ============ Setters（稳定区）============
    set_tabs: Callable
    set_active_index: Callable
    set_session: Callable
    set_confirm_close: Callable
    set_file_dialog: Callable
    set_compare_source: Callable
    set_diff_active_pane: Callable
    set_theme_mode: Callable
    set_settings: Callable
    set_settings_open: Callable
    set_settings_tab: Callable
    set_shortcut_focus: Callable
    set_capturing: Callable
    set_split_editor: Callable
    set_active_pane: Callable

    # ============ Refs（稳定区）============
    is_diff_tab_ref: ft.Ref
    diff_nav_left: ft.Ref
    diff_nav_right: ft.Ref
    diff_active_pane_ref: ft.Ref
    nav_ref: ft.Ref
    nav_ref_split: ft.Ref
    active_pane_ref: ft.Ref
    picker_holder: ft.Ref
    clipboard_holder: ft.Ref
    page_ref: ft.Ref
    tabs_ref: ft.Ref
    active_index_ref: ft.Ref
    dispatcher_ref: ft.Ref  # KeyDispatcher 实例
    paste_old_draft: ft.Ref  # 粘贴前 draft 快照（供 handle_paste 做 diff 定位）
    status_ref: ft.Ref  # 状态栏命令式更新器（update_cursor / update_counts）

    # ============ 装配槽（跨控制器调用，控制器装配后写入）============
    # tab_management 组
    cur_tab_fn: Callable = field(default=lambda: None)
    update_active: Callable = field(default=lambda **kw: None)
    update_tab: Callable = field(default=lambda *a, **kw: None)
    select_tab: Callable = field(default=lambda *a: None)
    cycle_tab: Callable = field(default=lambda *a: None)
    do_close_many: Callable = field(default=lambda *a: None)
    request_close: Callable = field(default=lambda *a: None)
    close_tab: Callable = field(default=lambda *a: None)
    # 稳定化「关闭当前标签」：use_memo 实例，读 close_tab_ref + active_index_ref，
    # 身份跨渲染不变 → DiffHeader @ft.memo 的 on_close prop 稳定，头部 memo 成立。
    close_current_tab: Callable = field(default=lambda: None)
    save_and_close_pending: Callable = field(default=lambda: None)
    close_without_save: Callable = field(default=lambda: None)
    cancel_close: Callable = field(default=lambda *a: None)
    on_tab_context_action: Callable = field(default=lambda *a: None)

    # file_io_ops 组
    push_recent_file: Callable = field(default=lambda *a: None)
    open_file_by_path: Callable = field(default=lambda *a: None)
    new_doc: Callable = field(default=lambda: None)
    open_doc: Callable = field(default=lambda: None)
    open_folder: Callable = field(default=lambda: None)
    save_doc: Callable = field(default=lambda *a: None)
    export_doc: Callable = field(default=lambda: None)

    # file_dialogs 组
    show_snack: Callable = field(default=lambda *a: None)
    copy_path: Callable = field(default=lambda *a: None)
    on_file_dialog_confirm: Callable = field(default=lambda *a: None)
    open_input_dialog: Callable = field(default=lambda *a: None)
    open_delete_dialog: Callable = field(default=lambda *a: None)
    update_tab_for_renamed_file: Callable = field(default=lambda *a: None)
    close_tabs_for_path: Callable = field(default=lambda *a: None)
    on_sidebar_context_action: Callable = field(default=lambda *a: None)

    # diff_controller 组
    get_text_for_compare: Callable = field(default=lambda *a: "")
    select_for_compare: Callable = field(default=lambda *a: None)
    compare_with_selected: Callable = field(default=lambda *a: None)
    on_diff_dirty_change: Callable = field(default=lambda *a: None)

    # settings_controller 组
    apply_theme: Callable = field(default=lambda: None)
    toggle_theme: Callable = field(default=lambda: None)
    open_settings: Callable = field(default=lambda: None)
    close_settings: Callable = field(default=lambda: None)
    select_settings_tab: Callable = field(default=lambda *a: None)
    update_setting: Callable = field(default=lambda *a: None)
    on_capture: Callable = field(default=lambda *a: None)
    on_cancel_capture: Callable = field(default=lambda: None)
    reset_settings: Callable = field(default=lambda: None)
    reset_shortcuts: Callable = field(default=lambda: None)
    export_shortcuts: Callable = field(default=lambda: None)
    import_shortcuts: Callable = field(default=lambda: None)
    schedule_autosave: Callable = field(default=lambda: None)
    toggle_sidebar: Callable = field(default=lambda: None)
    toggle_word_wrap: Callable = field(default=lambda: None)
    change_sidebar_panel: Callable = field(default=lambda *a: None)
    change_sidebar_width: Callable = field(default=lambda *a: None)
    mount_picker: Callable = field(default=lambda: None)

    # split_editor 组
    toggle_split_editor: Callable = field(default=lambda: None)
    set_active_pane: Callable = field(default=lambda *a: None)
    set_diff_active_pane: Callable = field(default=lambda *a: None)

    # focus_router 组
    get_active_nav: Callable = field(default=lambda: None)
    apply_content_layout: Callable = field(default=lambda: None)
    jump_to_line: Callable = field(default=lambda *a: None)
    on_dirty_change: Callable = field(default=lambda *a: None)

    # keyboard 组
    bind_keyboard: Callable = field(default=lambda: None)

    # 状态栏命令式更新装配槽（__init__.py 装配后写入）
    push_cursor_to_status: Callable = field(default=lambda *a: None)
    schedule_status_count_update: Callable = field(default=lambda: None)

    # 文件系统变更信号（文件增删改后递增，驱动侧边栏文件树异步重扫）
    bump_fs_version: Callable = field(default=lambda: None)
