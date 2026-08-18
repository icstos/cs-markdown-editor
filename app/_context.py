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
    # 每侧编辑组的激活标签全局索引（不变式：active_index == 焦点侧组的激活索引）
    active_index_left: int
    active_index_right: int
    # 每侧编辑器重建计数器：仅该组激活标签变化时递增（另一侧编辑器不重置光标）
    session_left: int
    session_right: int
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
    set_active_index_left: Callable
    set_active_index_right: Callable
    set_session_left: Callable
    set_session_right: Callable

    # ============ Refs（稳定区）============
    is_diff_tab_ref: ft.Ref
    diff_nav_left: ft.Ref
    diff_nav_right: ft.Ref
    diff_active_pane_ref: ft.Ref
    nav_ref: ft.Ref
    nav_ref_split: ft.Ref
    active_pane_ref: ft.Ref
    # 每侧组激活索引 ref 镜像（异步回调读取最新值，同 tabs_ref 模式）
    active_index_left_ref: ft.Ref
    active_index_right_ref: ft.Ref
    # 每侧会话计数 ref 镜像（同值自增模式，供单事件内多次 bump 不丢计数）
    session_left_ref: ft.Ref
    session_right_ref: ft.Ref
    picker_holder: ft.Ref
    clipboard_holder: ft.Ref
    page_ref: ft.Ref
    tabs_ref: ft.Ref
    active_index_ref: ft.Ref
    settings_ref: ft.Ref  # 最新 settings 快照（供异步任务读取，避免闭包捕获过期快照）
    dispatcher_ref: ft.Ref  # KeyDispatcher 实例
    paste_old_draft: ft.Ref  # 粘贴前 draft 快照（供 handle_paste 做 diff 定位）
    arrow_repeat_ref: ft.Ref  # 上/下键自驱动重复标志（KeyDispatcher 与 editor _on_key_up 共享）
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
    # 统一激活入口：按标签所属组设置组激活索引 + 焦点切换 + 会话计数
    activate_index: Callable = field(default=lambda *a: None)
    # 追加新标签（new_tab 字段）并激活（file_io / diff / backup 打开统一入口）
    append_and_activate: Callable = field(default=lambda *a: None)
    # 指定标签内容被整体替换（重载/外部修改）后递增其所属组的会话计数
    bump_tab_session: Callable = field(default=lambda *a: None)
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
    # 跨文件"打开后跳转"：open_file_by_path(path, jump_to=(li, off)) 写入 pending_jump_ref，
    # session/pending_jump_sig 变化时 _fire_pending_jump effect 消费并调用 jump_to_line(li, off)。
    # 解决 EditorActions 重建时序：open 触发 session++ 重建 MarkdownEditor，子组件先于父 effect
    # 渲染，nav_ref.current 已就位。
    pending_jump_ref: Any = field(default=None)  # ft.Ref[(li, off) | None]
    pending_jump_sig: int = field(default=0)
    set_pending_jump_sig: Callable = field(default=lambda *a: None)
    open_file_and_jump: Callable = field(default=lambda *a: None)
    new_doc: Callable = field(default=lambda: None)
    open_doc: Callable = field(default=lambda: None)
    open_folder: Callable = field(default=lambda: None)
    save_doc: Callable = field(default=lambda *a: None)
    save_as_doc: Callable = field(default=lambda *a: None)
    export_doc: Callable = field(default=lambda: None)
    # 状态栏轻量消息推送：(msg, kind) -> None，kind ∈ info/success/warn/error
    set_status_message: Callable = field(default=lambda *a: None)

    # file_dialogs 组
    show_snack: Callable = field(default=lambda *a: None)
    copy_path: Callable = field(default=lambda *a: None)
    on_file_dialog_confirm: Callable = field(default=lambda *a: None)
    on_file_dialog_cancel: Callable = field(default=lambda: None)
    open_input_dialog: Callable = field(default=lambda *a: None)
    open_delete_dialog: Callable = field(default=lambda *a: None)
    update_tab_for_renamed_file: Callable = field(default=lambda *a: None)
    close_tabs_for_path: Callable = field(default=lambda *a: None)
    on_sidebar_context_action: Callable = field(default=lambda *a: None)
    # 侧边栏拖拽移动文件/文件夹：(src_path, dst_dir) -> None
    move_fs_item: Callable = field(default=lambda *a: None)
    # 强制以当前编辑器内容保存（保存失败弹窗「强制保存」）：tab_index -> bool
    force_save_doc: Callable = field(default=lambda *a: False)

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
    zoom_in: Callable = field(default=lambda: None)
    zoom_out: Callable = field(default=lambda: None)
    zoom_reset: Callable = field(default=lambda: None)
    change_sidebar_panel: Callable = field(default=lambda *a: None)
    change_sidebar_width: Callable = field(default=lambda *a: None)
    mount_picker: Callable = field(default=lambda: None)
    # 恢复面板入口 + 自定义备份目录选择（设置面板按钮回调）
    open_recovery_panel: Callable = field(default=lambda: None)
    pick_backup_dir: Callable = field(default=lambda: None)

    # split_editor 组
    toggle_split_editor: Callable = field(default=lambda: None)
    set_active_pane: Callable = field(default=lambda *a: None)
    set_diff_active_pane: Callable = field(default=lambda *a: None)

    # focus_router 组
    get_active_nav: Callable = field(default=lambda: None)
    apply_content_layout: Callable = field(default=lambda: None)
    jump_to_line: Callable = field(default=lambda *a: None)
    on_dirty_change: Callable = field(default=lambda *a: None)
    # 拆分模式下按视口上报脏状态：更新对应组激活标签（而非全局 active_index）
    on_dirty_change_pane: Callable = field(default=lambda *a: None)

    # keyboard 组
    bind_keyboard: Callable = field(default=lambda: None)

    # 状态栏命令式更新装配槽（__init__.py 装配后写入）
    push_cursor_to_status: Callable = field(default=lambda *a: None)
    schedule_status_count_update: Callable = field(default=lambda: None)

    # 文件系统变更信号（文件增删改后递增，驱动侧边栏文件树异步重扫）
    bump_fs_version: Callable = field(default=lambda: None)

    # 搜索/替换快捷键桥接（KeyDispatcher → App 稳定闭包 → sidebar_replace_ref）
    toggle_replace_bar: Callable = field(default=lambda: None)
    replace_current: Callable = field(default=lambda: None)
    replace_all: Callable = field(default=lambda: None)
    focus_search: Callable = field(default=lambda: None)
    sidebar_replace_ref: Any = field(default=None)  # ft.Ref[dict]

    # ============ backup_controller 组（自动备份 / 崩溃恢复 / 启动扫描）============
    # 定时备份/自动保存循环：use_effect 启动，return cleanup 停止
    start_backup_loop: Callable = field(default=lambda: None)
    # 即时触发自动保存（窗口失焦/最小化时调用）
    trigger_autosave_now: Callable = field(default=lambda: None)
    # 即时触发全量备份（退出/关闭前/崩溃钩子调用）
    trigger_backup_now: Callable = field(default=lambda: None)
    # 退出前写入会话哨兵（记录本次会话备份路径，供下次启动恢复）
    write_exit_sentinel: Callable = field(default=lambda: None)
    # 启动扫描可恢复草稿（返回 BackupInfo 列表，无则空）
    scan_recoverable: Callable = field(default=lambda: [])
    # 手动恢复入口：扫描最近 N 天全量备份
    scan_recent_backups: Callable = field(default=lambda: [])
    # 在新标签页打开备份内容（用户主动恢复）
    open_backup_in_new_tab: Callable = field(default=lambda *a: None)
    # 删除指定备份文件
    delete_backup: Callable = field(default=lambda *a: None)
    # 清理过期备份（启动时与定时触发）
    cleanup_expired_backups: Callable = field(default=lambda: 0)

    # ============ 恢复面板 / 状态消息 UI 状态 ============
    # 恢复面板可见性 + 列表数据：启动时若存在可恢复草稿则弹出
    recovery_open: bool = field(default=False)
    set_recovery_open: Callable = field(default=lambda *a: None)
    recovery_list: Any = field(default=None)  # list[BackupInfo]
    set_recovery_list: Callable = field(default=lambda *a: None)
    # 状态栏轻量消息：(msg, kind, ts) -> None，由 set_status_message 写入 state
    status_message: Any = field(default=None)  # (msg, kind) | None
