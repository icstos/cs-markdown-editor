"""文件对话框与右键菜单控制器（从 main.py 闭包抽取）。

闭包组：show_snack / copy_path / update_tab_for_renamed_file /
close_tabs_for_path / on_file_dialog_confirm / open_input_dialog /
open_delete_dialog / on_tab_context_action / on_sidebar_context_action

跨组依赖（通过 ctx 装配槽，调用时读取）：
- file_io_ops 组：open_file_by_path（右键菜单打开/副本）
- diff_controller 组：select_for_compare / compare_with_selected
- tab_management 组：do_close_many（删除文件后清理引用标签）
- split_editor 组：set_diff_active_pane（交换对比标签侧后切焦点）
- 共享：tab_paths（纯函数，直接导入）

设计要点：
- show_snack 委托 services.ui_feedback.show_snack，page 从 page_ref 读取，
  使异步回调（无 ft.context.page）也能弹提示。
- 文件操作对话框统一用 file_dialog state 管理（input/confirm 双模式），
  确认回调根据 action 字段分发到 file_ops 对应函数。
- 重命名/删除后同步更新/关闭引用该路径的标签（含对比标签两侧）。

依赖项：
- os / flet（Icons / FilePicker 无）
- services.file_ops（reveal_in_explorer / create_file / create_folder /
  rename_path / delete_path / duplicate_file）
- services.ui_feedback.show_snack
- app._tab_helpers.tab_paths
"""

import os

import flet as ft

from app._tab_helpers import tab_paths
from services import file_ops
from services.ui_feedback import show_snack as _show_snack_impl


def build_file_dialogs(ctx):
    """构造文件对话框与右键菜单控制器闭包组。

    返回 dict[str, Callable]：
    show_snack / copy_path / update_tab_for_renamed_file / close_tabs_for_path /
    on_file_dialog_confirm / open_input_dialog / open_delete_dialog /
    on_tab_context_action / on_sidebar_context_action
    """

    def show_snack(msg: str):
        """SnackBar 提示（委托 services.ui_feedback.show_snack，page 从 page_ref 读取）。"""
        _show_snack_impl(ctx.page_ref.current, msg)

    async def copy_path(path: str):
        cb = ctx.clipboard_holder.current
        if cb is not None:
            try:
                await cb.set(path)
                if ctx.page_ref.current is not None:
                    show_snack("路径已复制")
            except Exception:
                pass

    def update_tab_for_renamed_file(old_path: str, new_path: str):
        """文件重命名后，同步更新引用该文件的标签路径（含对比标签两侧）。"""
        ts = list(ctx.tabs_ref.current)
        changed = False
        for i, t in enumerate(ts):
            if t.get("type") == "diff":
                # 对比标签：检查左右两侧路径，更新匹配侧
                updates = {}
                if t.get("left_path") == old_path:
                    updates["left_path"] = new_path
                    if t.get("left_doc") is not None:
                        t["left_doc"].file_path = new_path
                if t.get("right_path") == old_path:
                    updates["right_path"] = new_path
                    if t.get("right_doc") is not None:
                        t["right_doc"].file_path = new_path
                if updates:
                    ts[i] = {**t, **updates}
                    changed = True
            elif t.get("file_path") == old_path:
                ts[i] = {**t, "file_path": new_path}
                if t.get("document") is not None:
                    ts[i]["document"].file_path = new_path
                changed = True
        if changed:
            ctx.set_tabs(ts)
            ctx.tabs_ref.current = ts

    def close_tabs_for_path(path: str):
        """关闭引用指定路径的所有标签（含对比标签，用于删除后清理）。"""
        ts = ctx.tabs_ref.current
        indices = [i for i, t in enumerate(ts) if path in tab_paths(t)]
        if indices:
            ctx.do_close_many(indices)

    def on_file_dialog_confirm(value: str = ""):
        """文件操作对话框确认回调。

        input 模式：value 为用户输入的文本（文件名/文件夹名/新名称）。
        confirm 模式：value 为空字符串（删除确认）。
        """
        state = ctx.file_dialog
        if state is None:
            return
        action = state["action"]
        target = state["target"]
        ctx.set_file_dialog(None)  # 先关闭对话框

        if action == "new_file":
            try:
                path = file_ops.create_file(target, value)
                ctx.open_file_by_path(path)
                ctx.bump_fs_version()  # 刷新侧边栏文件树
                show_snack(f"已创建：{os.path.basename(path)}")
            except Exception as e:
                show_snack(f"创建失败：{e}")
        elif action == "new_folder":
            try:
                file_ops.create_folder(target, value)
                ctx.bump_fs_version()  # 刷新侧边栏文件树
                show_snack(f"已创建文件夹：{value}")
            except Exception as e:
                show_snack(f"创建失败：{e}")
        elif action == "rename":
            try:
                new_path = file_ops.rename_path(target, value)
                update_tab_for_renamed_file(target, new_path)
                ctx.bump_fs_version()  # 刷新侧边栏文件树
                show_snack(f"已重命名为：{os.path.basename(new_path)}")
            except Exception as e:
                show_snack(f"重命名失败：{e}")
        elif action == "delete":
            try:
                fname = os.path.basename(target)
                file_ops.delete_path(target)
                close_tabs_for_path(target)
                ctx.bump_fs_version()  # 刷新侧边栏文件树
                show_snack(f"已删除：{fname}")
            except Exception as e:
                show_snack(f"删除失败：{e}")

    def open_input_dialog(action: str, title: str, icon: str, label: str,
                          hint: str, default_value: str, location: str,
                          confirm_label: str, target: str):
        """弹出输入对话框（新建文件/文件夹/重命名）。"""
        ctx.set_file_dialog({
            "mode": "input",
            "title": title,
            "icon": icon,
            "input_label": label,
            "input_value": default_value,
            "input_hint": hint,
            "location_hint": location,
            "confirm_label": confirm_label,
            "action": action,
            "target": target,
        })

    def open_delete_dialog(target: str, is_dir: bool):
        """弹出删除确认对话框。"""
        fname = os.path.basename(target)
        title = "删除文件夹" if is_dir else "删除文件"
        msg = f"确定删除{'文件夹' if is_dir else '文件'}「{fname}」？\n此操作不可撤销。"
        ctx.set_file_dialog({
            "mode": "confirm",
            "title": title,
            "icon": ft.Icons.DELETE_OUTLINE,
            "message": msg,
            "confirm_label": "删除",
            "danger": True,
            "action": "delete",
            "target": target,
        })

    def on_tab_context_action(action: str, index: int):
        """标签右键菜单回调：处理打开/新建/路径/重命名/副本/删除/关闭/交换对比等操作。"""
        ts = ctx.tabs_ref.current
        if not (0 <= index < len(ts)):
            return
        tab = ts[index]
        # diff 标签无 file_path 字段；统一用 .get() 避免 KeyError
        path = tab.get("file_path")
        is_diff = tab.get("type") == "diff"

        if action == "close":
            ctx.close_tab(index)
        elif action == "close_others":
            ctx.request_close([j for j in range(len(ts)) if j != index])
        elif action == "close_all":
            ctx.request_close(list(range(len(ts))))
        elif action == "swap_diff":
            # 仅对比标签有效：交换左右侧文档/路径/脏状态，便于从不同视角审阅差异
            if not is_diff:
                return
            new_tabs = list(ts)
            new_tabs[index] = {
                **tab,
                "left_path": tab.get("right_path"),
                "right_path": tab.get("left_path"),
                "left_doc": tab.get("right_doc"),
                "right_doc": tab.get("left_doc"),
                "left_dirty": tab.get("right_dirty", False),
                "right_dirty": tab.get("left_dirty", False),
            }
            ctx.set_tabs(new_tabs)
            ctx.tabs_ref.current = new_tabs
            # 切换焦点到原主动侧的对侧，保持视觉焦点对应同一文档
            ctx.set_diff_active_pane(1 - ctx.diff_active_pane_ref.current)
            ctx.set_session(ctx.session + 1)
        elif action == "copy_path":
            page = ctx.page_ref.current
            if path and page is not None:
                page.run_task(copy_path, path)
            elif page is not None:
                show_snack("该标签无文件路径")
        elif action == "open":
            if path:
                ctx.open_file_by_path(path)
        elif action == "select_for_compare":
            if path:
                ctx.select_for_compare(path)
        elif action == "compare_with_selected":
            if path:
                ctx.compare_with_selected(path)
        elif action == "new_file":
            if path:
                dir_path = os.path.dirname(path)
                open_input_dialog(
                    "new_file", "新建文件", ft.Icons.NOTE_ADD,
                    "文件名", "输入文件名（自动添加 .md）", "",
                    f"在 {dir_path} 创建", "创建", dir_path,
                )
        elif action == "new_folder":
            if path:
                dir_path = os.path.dirname(path)
                open_input_dialog(
                    "new_folder", "新建文件夹", ft.Icons.CREATE_NEW_FOLDER,
                    "文件夹名", "输入文件夹名", "",
                    f"在 {dir_path} 创建", "创建", dir_path,
                )
        elif action == "reveal":
            if path:
                try:
                    file_ops.reveal_in_explorer(path)
                except Exception as e:
                    show_snack(f"打开失败：{e}")
        elif action == "rename":
            if path:
                dir_path = os.path.dirname(path)
                open_input_dialog(
                    "rename", "重命名", ft.Icons.DRIVE_FILE_RENAME_OUTLINE,
                    "新名称", "输入新文件名", os.path.basename(path),
                    f"位置：{dir_path}", "重命名", path,
                )
        elif action == "duplicate":
            if path:
                try:
                    new_path = file_ops.duplicate_file(path)
                    ctx.open_file_by_path(new_path)
                    ctx.bump_fs_version()  # 刷新侧边栏文件树
                    show_snack(f"已创建副本：{os.path.basename(new_path)}")
                except Exception as e:
                    show_snack(f"创建副本失败：{e}")
        elif action == "delete" and path:
            open_delete_dialog(path, is_dir=False)

    def on_sidebar_context_action(action: str, path: str):
        """侧边栏文件/文件夹右键菜单回调。

        path 为文件或文件夹的绝对路径。对于新建操作：
        - 文件夹：在其内部创建（dir_path = path）
        - 文件：在其所在目录创建（dir_path = dirname(path)）
        """
        is_dir = os.path.isdir(path)

        if action == "open":
            if not is_dir:
                ctx.open_file_by_path(path)
        elif action == "select_for_compare":
            if not is_dir:
                ctx.select_for_compare(path)
        elif action == "compare_with_selected":
            if not is_dir:
                ctx.compare_with_selected(path)
        elif action == "new_file":
            dir_path = path if is_dir else os.path.dirname(path)
            open_input_dialog(
                "new_file", "新建文件", ft.Icons.NOTE_ADD,
                "文件名", "输入文件名（自动添加 .md）", "",
                f"在 {dir_path} 创建", "创建", dir_path,
            )
        elif action == "new_folder":
            dir_path = path if is_dir else os.path.dirname(path)
            open_input_dialog(
                "new_folder", "新建文件夹", ft.Icons.CREATE_NEW_FOLDER,
                "文件夹名", "输入文件夹名", "",
                f"在 {dir_path} 创建", "创建", dir_path,
            )
        elif action == "copy_path":
            page = ctx.page_ref.current
            if page is not None:
                page.run_task(copy_path, path)
        elif action == "reveal":
            try:
                file_ops.reveal_in_explorer(path)
            except Exception as e:
                show_snack(f"打开失败：{e}")
        elif action == "rename":
            dir_path = os.path.dirname(path)
            open_input_dialog(
                "rename", "重命名", ft.Icons.DRIVE_FILE_RENAME_OUTLINE,
                "新名称", "输入新名称", os.path.basename(path),
                f"位置：{dir_path}", "重命名", path,
            )
        elif action == "duplicate":
            if not is_dir:
                try:
                    new_path = file_ops.duplicate_file(path)
                    ctx.open_file_by_path(new_path)
                    ctx.bump_fs_version()  # 刷新侧边栏文件树
                    show_snack(f"已创建副本：{os.path.basename(new_path)}")
                except Exception as e:
                    show_snack(f"创建副本失败：{e}")
        elif action == "delete":
            open_delete_dialog(path, is_dir=is_dir)

    return {
        "show_snack": show_snack,
        "copy_path": copy_path,
        "update_tab_for_renamed_file": update_tab_for_renamed_file,
        "close_tabs_for_path": close_tabs_for_path,
        "on_file_dialog_confirm": on_file_dialog_confirm,
        "open_input_dialog": open_input_dialog,
        "open_delete_dialog": open_delete_dialog,
        "on_tab_context_action": on_tab_context_action,
        "on_sidebar_context_action": on_sidebar_context_action,
    }
