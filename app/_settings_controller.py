"""设置与主题控制器（从 main.py 闭包抽取）。

闭包组：apply_theme / mount_picker / toggle_theme / open_settings /
close_settings / select_settings_tab / update_setting / on_capture /
on_cancel_capture / schedule_autosave / reset_settings / reset_shortcuts /
export_shortcuts / import_shortcuts / toggle_sidebar / toggle_word_wrap /
change_sidebar_panel / change_sidebar_width / open_recovery_panel /
pick_backup_dir

跨组依赖（通过 ctx 装配槽，调用时读取）：
- focus_router 组：apply_content_layout（update_setting 后刷新布局）
- file_dialogs 组：show_snack（导入/导出提示）
- file_io_ops 组：save_doc（schedule_autosave 注入）
- tab_management 组：cur_tab_fn（schedule_autosave 注入）
- backup_controller 组：scan_recent_backups（恢复面板入口）

设计要点：
- apply_theme / mount_picker 是 use_effect 回调，定义在此处但 use_effect
  调用在 __init__.py（hooks 顺序约束）。两者均通过 ft.context.page 取 page。
- 渲染期同步写入 page.theme_mode/bgcolor 已在 __init__.py 完成（保证子组件
  取色正确）；apply_theme 在 use_effect 中再次推送，确保原生 chrome 同步。
- shortcut_mgr 在 __init__.py 派生值区创建（每次渲染重建，捕获最新 settings
  与 update_setting 前向引用），本控制器通过 ctx.shortcut_mgr 访问。
- update_setting 持久化后调用 apply_content_layout 刷新布局；快捷键变更时
  定位首个冲突项并聚焦。
- schedule_autosave 已改为间隔触发模型（_backup_controller 启动定时器），
  此处保留入口供 _focus_router.on_dirty_change 调用，实现为空操作。

依赖项：
- json / flet
- config.settings（DEFAULT_SETTINGS / save_settings）
- services.file_io（read_text / write_text）
- styles（get_colors）
- app.autosave（AutosaveContext / schedule_autosave）
"""

import json

import flet as ft

from app.autosave import AutosaveContext, schedule_autosave
from config.settings import DEFAULT_SETTINGS, save_settings
from services.file_io import read_text, write_text
from styles import get_colors


def build_settings_controller(ctx):
    """构造设置与主题控制器闭包组。

    返回 dict[str, Callable]：
    apply_theme / mount_picker / toggle_theme / open_settings / close_settings /
    select_settings_tab / update_setting / on_capture / on_cancel_capture /
    schedule_autosave / reset_settings / reset_shortcuts / export_shortcuts /
    import_shortcuts / toggle_sidebar / toggle_word_wrap /
    change_sidebar_panel / change_sidebar_width
    """

    def apply_theme():
        """推送 page 级属性（theme_mode / bgcolor / 原生 chrome）到 UI。

        use_effect 回调：渲染期已同步写入 page.theme_mode/bgcolor（保证子组件
        取色正确），此处 use_effect 再次推送确保原生 chrome 同步。
        """
        page = ft.context.page
        page.theme_mode = ctx.theme_mode
        page.bgcolor = get_colors(ctx.theme_mode).bg
        page.update()

    def mount_picker():
        """挂载 FilePicker / Clipboard service（use_effect 回调，空依赖）。

        FilePicker / Clipboard 是 service，不需要添加到 page.overlay。
        """
        page = ft.context.page
        ctx.page_ref.current = page
        ctx.picker_holder.current = ft.FilePicker()
        ctx.clipboard_holder.current = ft.Clipboard()

    def toggle_theme():
        ctx.set_theme_mode(
            ft.ThemeMode.DARK
            if ctx.theme_mode == ft.ThemeMode.LIGHT
            else ft.ThemeMode.LIGHT
        )

    def open_settings():
        ctx.set_settings_open(True)

    def close_settings():
        ctx.set_capturing((None, None))
        ctx.set_settings_open(False)

    def select_settings_tab(tab: str):
        # 切 tab 时退出捕获模式，避免遗留捕获态
        ctx.set_capturing((None, None))
        ctx.set_settings_tab(tab)

    def update_setting(key: str, value):
        # 通过 settings_ref 读取最新 settings，避免闭包捕获的 ctx.settings
        # 是过期快照（异步任务如自动保存运行时可能持有旧渲染周期的 ctx）。
        # 否则 dict(ctx.settings) 会用旧快照覆盖最新值（如 workspace_folder 丢失）。
        current = ctx.settings_ref.current if ctx.settings_ref is not None else ctx.settings
        next_settings = dict(current)
        next_settings[key] = value
        ctx.set_settings(next_settings)
        save_settings(next_settings)
        ctx.apply_content_layout()
        if key == "shortcuts":
            layer, action = ctx.shortcut_mgr.first_conflict_target()
            ctx.set_shortcut_focus((layer, action))

    # 快捷键捕获回调：KeyDispatcher 在捕获模式下捕获到组合键后调用。
    # 通过 dispatcher_ref 同步链路，此处引用的 shortcut_mgr 总是当次渲染的最新实例。
    def on_capture(layer: str, action_id: str, combo: str):
        # combo="" 表示清空绑定（Backspace）
        ctx.shortcut_mgr.update(layer, action_id, combo)
        ctx.set_capturing((None, None))

    def on_cancel_capture():
        ctx.set_capturing((None, None))

    def schedule_autosave_cb():
        """间隔触发模型的兼容入口（空操作）。

        历史上 on_dirty_change 触发 2s debounce 保存；现由 _backup_controller
        启动定时器扫描脏标签，变脏事件无需立即调度。保留入口避免改动
        _focus_router 调用链。实际调度由 start_backup_loop 启动的定时器负责。
        """
        schedule_autosave(AutosaveContext(
            settings=ctx.settings,
            page_ref=ctx.page_ref,
            tabs_ref=ctx.tabs_ref,
            save_doc_fn=ctx.save_doc,
            set_status_fn=ctx.set_status_message,
        ))

    def open_recovery_panel():
        """打开恢复面板：扫描最近 N 天全量备份并展示。

        跳过启动 sentinel 机制（那是上次会话草稿），此处展示全量历史备份，
        供用户主动找回。扫描由 backup_controller.scan_recent_backups 完成。
        """
        infos = ctx.scan_recent_backups()
        ctx.set_recovery_list(infos)
        ctx.set_recovery_open(True)

    async def pick_backup_dir():
        """选择自定义备份目录（FilePicker.get_directory_path）。

        选择 None 表示恢复平台默认路径（用户清空自定义路径）。
        选择后立即持久化到 settings.backup_dir。
        """
        picker = ctx.picker_holder.current
        if picker is None:
            return
        folder = await picker.get_directory_path(dialog_title="选择备份目录")
        if folder is None:
            # 用户取消：保持原值不变（不视为清空）
            return
        update_setting("backup_dir", folder)
        ctx.show_snack(f"备份目录已设置为：{folder}")

    def reset_settings():
        next_settings = dict(DEFAULT_SETTINGS)
        ctx.set_settings(next_settings)
        save_settings(next_settings)

    def reset_shortcuts():
        next_settings = dict(ctx.settings)
        next_settings["shortcuts"] = {k: dict(v) for k, v in DEFAULT_SETTINGS["shortcuts"].items()}
        ctx.set_settings(next_settings)
        save_settings(next_settings)
        ctx.set_shortcut_focus((None, None))
        select_settings_tab("advanced")
        open_settings()

    async def export_shortcuts():
        picker = ctx.picker_holder.current
        if picker is None:
            return
        path = await picker.save_file(
            dialog_title="导出快捷键方案",
            file_name="shortcuts.json",
            allowed_extensions=["json"],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            payload = json.dumps(
                ctx.settings.get("shortcuts", DEFAULT_SETTINGS["shortcuts"]),
                ensure_ascii=False,
                indent=2,
            )
            write_text(path, payload)
        except Exception as e:
            ctx.show_snack(f"导出失败：{e}")
            return
        ctx.show_snack("快捷键方案已导出")

    async def import_shortcuts():
        picker = ctx.picker_holder.current
        if picker is None:
            return
        files = await picker.pick_files(
            dialog_title="导入快捷键方案",
            allowed_extensions=["json"],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        if not files:
            return
        try:
            payload = read_text(files[0].path)
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError("JSON 格式不正确")
            next_settings = dict(ctx.settings)
            next_settings["shortcuts"] = data
            ctx.set_settings(next_settings)
            save_settings(next_settings)
            ctx.set_shortcut_focus((None, None))
        except Exception as e:
            ctx.show_snack(f"导入失败：{e}")
            return
        ctx.show_snack("快捷键方案已导入")

    def toggle_sidebar():
        update_setting("sidebar_open", not ctx.settings.get("sidebar_open", False))

    def toggle_word_wrap():
        """切换自动换行（VSCode 风格 Alt+Z）：开 = 软换行，关 = 长行不换行。"""
        update_setting("word_wrap", not ctx.settings.get("word_wrap", True))

    def zoom_in():
        """放大界面（Typora 式 Ctrl+Shift+=）：每次 +10%，上限 200%。"""
        cur = ctx.settings.get("zoom", 100)
        update_setting("zoom", min(200, cur + 10))

    def zoom_out():
        """缩小界面（Typora 式 Ctrl+Shift+-）：每次 -10%，下限 50%。"""
        cur = ctx.settings.get("zoom", 100)
        update_setting("zoom", max(50, cur - 10))

    def zoom_reset():
        """重置缩放为 100%（Typora 式 Ctrl+Shift+0）。"""
        update_setting("zoom", 100)

    def change_sidebar_panel(panel: str):
        update_setting("sidebar_panel", panel)

    def change_sidebar_width(width: int):
        update_setting("sidebar_width", width)

    return {
        "apply_theme": apply_theme,
        "mount_picker": mount_picker,
        "toggle_theme": toggle_theme,
        "open_settings": open_settings,
        "close_settings": close_settings,
        "select_settings_tab": select_settings_tab,
        "update_setting": update_setting,
        "on_capture": on_capture,
        "on_cancel_capture": on_cancel_capture,
        "schedule_autosave": schedule_autosave_cb,
        "open_recovery_panel": open_recovery_panel,
        "pick_backup_dir": pick_backup_dir,
        "reset_settings": reset_settings,
        "reset_shortcuts": reset_shortcuts,
        "export_shortcuts": export_shortcuts,
        "import_shortcuts": import_shortcuts,
        "toggle_sidebar": toggle_sidebar,
        "toggle_word_wrap": toggle_word_wrap,
        "zoom_in": zoom_in,
        "zoom_out": zoom_out,
        "zoom_reset": zoom_reset,
        "change_sidebar_panel": change_sidebar_panel,
        "change_sidebar_width": change_sidebar_width,
    }
