"""应用层控制器工厂的显式依赖契约（`typing.Protocol`）。

与 ``views/editor/_contracts.py`` 同构：每个控制器原先从单体 ``AppContext``
（168 字段）中任意读取字段，依赖只能靠通读函数体得知。本模块为每个控制器声明
一份精确契约（只列实际读取的字段），使签名即依赖清单。

``build_render`` 例外：它构造整棵渲染树，按设计需要完整 ctx，不加契约。

契约字段集合由 AST 静态提取，``tests/test_app_contracts.py`` 守护其与实现一致。
"""

from collections.abc import Callable
from typing import Any, Protocol

import flet as ft


class TabManagementEnv(Protocol):
    """多文档标签与拆分组的 CRUD / 激活 / 关闭（build_tab_management 实际读取的 26 个字段）。"""

    active_index: int
    active_index_left_ref: ft.Ref
    active_index_ref: ft.Ref
    active_index_right_ref: ft.Ref
    active_pane_ref: ft.Ref
    closed_tabs_ref: ft.Ref
    confirm_close: list | None
    page_ref: ft.Ref
    save_doc: Callable[..., Any]
    save_doc_sync: Callable[..., Any]
    session: int
    session_left_ref: ft.Ref
    session_right_ref: ft.Ref
    set_active_index: Callable[..., None]
    set_active_index_left: Callable[..., None]
    set_active_index_right: Callable[..., None]
    set_active_pane: Callable[..., None]
    set_confirm_close: Callable[..., None]
    set_session: Callable[..., None]
    set_session_left: Callable[..., None]
    set_session_right: Callable[..., None]
    set_split_editor: Callable[..., None]
    set_tabs: Callable[..., None]
    split_editor: bool
    tabs: list
    tabs_ref: ft.Ref


class FileIoEnv(Protocol):
    """文件读写 / 打开 / 保存 / 导出 / 最近文件 / 重新打开（build_file_io_ops 实际读取的 29 个字段）。"""

    activate_index: Callable[..., Any]
    active_index_left_ref: ft.Ref
    active_index_ref: ft.Ref
    active_index_right_ref: ft.Ref
    active_pane_ref: ft.Ref
    append_and_activate: Callable[..., Any]
    apply_content_layout: Callable[..., Any]
    bump_fs_version: Callable[..., Any]
    bump_tab_session: Callable[..., Any]
    closed_tabs_ref: ft.Ref
    document: Any
    file_path: str | None
    is_diff_tab_ref: ft.Ref
    open_external: Callable[..., Any]
    page_ref: ft.Ref
    pending_jump_ref: ft.Ref
    pending_jump_sig: int
    picker_holder: Any
    set_file_dialog: Callable[..., None]
    set_pending_jump_sig: Callable[..., None]
    set_settings: Callable[..., None]
    set_status_message: Callable[..., None]
    set_tabs: Callable[..., None]
    settings: dict
    show_snack: Callable[..., Any]
    split_editor: bool
    tabs_ref: ft.Ref
    update_setting: Callable[..., Any]
    update_tab: Callable[..., Any]


class FileDialogsEnv(Protocol):
    """文件操作对话框与右键菜单分发（build_file_dialogs 实际读取的 20 个字段）。"""

    bump_fs_version: Callable[..., Any]
    bump_tab_session: Callable[..., Any]
    clipboard_holder: Any
    close_tab: Callable[..., Any]
    compare_with_selected: Any
    diff_active_pane_ref: ft.Ref
    do_close_many: Callable[..., Any]
    file_dialog: dict | None
    force_save_doc: Any
    git_confirm_dialog_action: Callable[..., Any]
    git_open_file_history: Callable[..., Any]
    open_external: Callable[..., Any]
    open_file_by_path: Callable[..., Any]
    page_ref: ft.Ref
    request_close: Callable[..., Any]
    save_doc: Callable[..., Any]
    select_for_compare: Callable[..., Any]
    select_tab: Callable[..., Any]
    set_diff_active_pane: Callable[..., None]
    set_file_dialog: Callable[..., None]
    set_tabs: Callable[..., None]
    tabs_ref: ft.Ref


class DiffEnv(Protocol):
    """文件对比标签的创建与脏状态（build_diff_controller 实际读取的 12 个字段）。"""

    activate_index: Callable[..., Any]
    active_index_left_ref: ft.Ref
    active_index_ref: ft.Ref
    append_and_activate: Callable[..., Any]
    bump_tab_session: Callable[..., Any]
    compare_source: str | None
    diff_active_pane_ref: ft.Ref
    set_compare_source: Callable[..., None]
    set_diff_active_pane: Callable[..., None]
    set_tabs: Callable[..., None]
    show_snack: Callable[..., Any]
    tabs_ref: ft.Ref


class SettingsEnv(Protocol):
    """设置 / 主题 / 快捷键捕获 / 侧边栏（build_settings_controller 实际读取的 22 个字段）。"""

    apply_content_layout: Callable[..., Any]
    clipboard_holder: Any
    native_input_ref: ft.Ref
    page_ref: ft.Ref
    picker_holder: Any
    save_doc: Callable[..., Any]
    scan_recent_backups: Callable[..., Any]
    set_capturing: Callable[..., None]
    set_recovery_list: Callable[..., None]
    set_recovery_open: Callable[..., None]
    set_settings: Callable[..., None]
    set_settings_open: Callable[..., None]
    set_settings_tab: Callable[..., None]
    set_shortcut_focus: Callable[..., None]
    set_status_message: Callable[..., None]
    set_theme_mode: Callable[..., None]
    settings: dict
    settings_ref: ft.Ref
    shortcut_mgr: Any
    show_snack: Callable[..., Any]
    tabs_ref: ft.Ref
    theme_mode: ft.ThemeMode


class SplitEnv(Protocol):
    """拆分编辑器开合与焦点视口（build_split_editor 实际读取的 18 个字段）。"""

    active_index_left_ref: ft.Ref
    active_index_ref: ft.Ref
    active_index_right_ref: ft.Ref
    active_pane_ref: ft.Ref
    append_and_activate: Callable[..., Any]
    diff_active_pane_ref: ft.Ref
    is_diff_tab_ref: ft.Ref
    session: int
    set_active_index: Callable[..., None]
    set_active_index_left: Callable[..., None]
    set_active_index_right: Callable[..., None]
    set_active_pane: Callable[..., None]
    set_diff_active_pane: Callable[..., None]
    set_session: Callable[..., None]
    set_split_editor: Callable[..., None]
    set_tabs: Callable[..., None]
    split_editor: bool
    tabs_ref: ft.Ref


class FocusRouterEnv(Protocol):
    """焦点路由 / 跳转 / 脏状态按组同步（build_focus_router 实际读取的 14 个字段）。"""

    active_index_left_ref: ft.Ref
    active_index_right_ref: ft.Ref
    active_pane_ref: ft.Ref
    diff_active_pane_ref: ft.Ref
    diff_nav_left: Any
    diff_nav_right: Any
    is_diff_tab_ref: ft.Ref
    nav_ref: ft.Ref
    nav_ref_split: Any
    page_ref: ft.Ref
    schedule_autosave: Callable[..., Any]
    split_editor: bool
    tabs_ref: ft.Ref
    update_tab: Callable[..., Any]


class BackupEnv(Protocol):
    """自动备份 / 自动保存 / 崩溃恢复（build_backup_controller 实际读取的 17 个字段）。"""

    active_index_ref: ft.Ref
    active_pane_ref: ft.Ref
    append_and_activate: Callable[..., Any]
    file_dialog: dict | None
    page_ref: ft.Ref
    recovery_list: Any
    save_doc: Callable[..., Any]
    save_doc_sync: Callable[..., Any]
    set_file_dialog: Callable[..., None]
    set_recovery_list: Callable[..., None]
    set_recovery_open: Callable[..., None]
    set_status_message: Callable[..., None]
    settings: dict
    settings_ref: ft.Ref
    show_snack: Callable[..., Any]
    split_editor: bool
    tabs_ref: ft.Ref


class GitEnv(Protocol):
    """Git 版本管理（build_git_controller 实际读取的 52 个字段）。

    跨控制器依赖只有三处：``update_setting``（面板入口 / 差异模式与自动推送的
    持久化）、``open_file_and_jump``（差异行号 → 编辑器定位）、``set_file_dialog``
    （破坏性操作的二次确认）。仓储层（``services.git``）全部同步，控制器负责
    用 ``asyncio.to_thread`` 线程化。
    """

    active_index_ref: ft.Ref
    bump_fs_version: Callable[..., Any]
    git_active_path: str | None
    git_available: bool
    git_branch_menu_open: bool
    git_busy_ref: ft.Ref
    git_commit_details: dict
    git_commit_message_ref: ft.Ref
    git_commit_push: bool
    git_commit_seq: int
    git_dialog_seq_ref: ft.Ref
    git_diff_meta: dict
    git_diff_open: bool
    git_expanded_commits: frozenset
    git_history: list
    git_history_filter: dict
    git_history_loading: bool
    git_refresh_token: ft.Ref
    git_root: str | None
    git_service_ref: ft.Ref
    git_status: Any
    git_view: str
    open_file_and_jump: Callable[..., Any]
    page_ref: ft.Ref
    set_file_dialog: Callable[..., None]
    set_git_active_path: Callable[..., None]
    set_git_available: Callable[..., None]
    set_git_branch_menu_open: Callable[..., None]
    set_git_branches: Callable[..., None]
    set_git_busy: Callable[..., None]
    set_git_commit_details: Callable[..., None]
    set_git_commit_push: Callable[..., None]
    set_git_commit_seq: Callable[..., None]
    set_git_diff: Callable[..., None]
    set_git_diff_meta: Callable[..., None]
    set_git_diff_mode: Callable[..., None]
    set_git_diff_open: Callable[..., None]
    set_git_error: Callable[..., None]
    set_git_expanded_commits: Callable[..., None]
    set_git_history: Callable[..., None]
    set_git_history_filter: Callable[..., None]
    set_git_history_has_more: Callable[..., None]
    set_git_history_loading: Callable[..., None]
    set_git_root: Callable[..., None]
    set_git_status: Callable[..., None]
    set_git_version: Callable[..., None]
    set_git_view: Callable[..., None]
    set_git_workspace: Callable[..., None]
    set_status_message: Callable[..., None]
    settings: dict
    show_snack: Callable[..., Any]
    tabs_ref: ft.Ref
    update_setting: Callable[..., Any]


class KeyboardEnv(Protocol):
    """键盘分发装配（KeyDispatcher + page 绑定）（build_keyboard 实际读取的 46 个字段）。"""

    active_index_ref: ft.Ref
    active_pane: int
    arrow_repeat_ref: ft.Ref
    capturing: tuple
    clipboard_holder: Any
    close_doc_search: Callable[..., Any]
    close_tab: Callable[..., Any]
    cycle_tab: Callable[..., Any]
    diff_active_pane: int
    diff_nav_left: Any
    diff_nav_right: Any
    dispatcher_ref: ft.Ref
    doc_search_next: Any
    doc_search_open_ref: ft.Ref
    doc_search_prev: Any
    focus_mode: Callable[..., Any]
    focus_search: Callable[..., Any]
    git_commit: Callable[..., Any]
    git_escape: Callable[..., Any]
    git_open_panel: Callable[..., Any]
    global_search: Any
    is_diff_tab: bool
    native_input_ref: ft.Ref
    nav_ref: ft.Ref
    nav_ref_split: Any
    new_doc: Callable[..., Any]
    on_cancel_capture: Callable[..., Any]
    on_capture: Callable[..., Any]
    open_doc: Callable[..., Any]
    open_doc_search: Callable[..., Any]
    open_folder: Callable[..., Any]
    open_settings: Callable[..., Any]
    page_ref: ft.Ref
    paste_old_draft: Any
    reopen_closed_tab: Callable[..., Any]
    replace_all: Callable[..., Any]
    replace_current: Callable[..., Any]
    save_as_doc: Callable[..., Any]
    save_doc: Callable[..., Any]
    shortcut_mgr: Any
    split_editor: bool
    toggle_replace_bar: Callable[..., Any]
    toggle_sidebar: Callable[..., Any]
    toggle_split_editor: Callable[..., Any]
    toggle_theme: Callable[..., Any]
    toggle_word_wrap: Callable[..., Any]
    zoom_in: Callable[..., Any]
    zoom_out: Callable[..., Any]
    zoom_reset: Callable[..., Any]
