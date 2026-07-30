"""文件 IO 控制器（从 main.py 闭包抽取）。

闭包组：push_recent_file / open_file_by_path / new_doc / open_doc /
open_folder / save_doc / export_doc

跨组依赖（通过 ctx 装配槽，调用时读取）：
- settings_controller 组：update_setting（push_recent_file 持久化最近文件；
  open_folder 写入 workspace_folder 并展开侧边栏）
- file_dialogs 组：show_snack（错误/成功提示）
- tab_management 组：update_active（复用空白标签时不可变更新）

设计要点：
- 所有标签写操作基于 tabs_ref.current 最新值计算，保证批量保存时不互相覆盖。
- 对比标签分别保存两侧脏文档到各自路径；普通标签走单文档保存。
- save_doc 返回 bool：用户取消另存对话框或失败时返回 False，供
  save_and_close_pending 中止批量关闭。
- 文件路径打开时去重：已在某普通编辑标签打开则直接切换，避免重复开。
- open_folder 锚定工作区根目录（settings.workspace_folder），打开子目录文件时
  侧边栏文件树仍以工作区根排布，不随当前文件目录漂移。

依赖项：
- os / parser（解析/序列化/转 HTML）
- services.file_io（read_text / write_text）
- app._tab_helpers（is_blank_untitled）
- utils.file_helpers（file_name）
- flet（FilePickerFileType）
"""

import os

import flet as ft

import parser
from app._tab_helpers import is_blank_untitled
from config.settings import save_settings
from services.file_io import read_text, write_text
from utils.file_helpers import file_name


def build_file_io_ops(ctx):
    """构造文件 IO 控制器闭包组。

    返回 dict[str, Callable]：
    push_recent_file / open_file_by_path / new_doc / open_doc / save_doc / export_doc
    """

    def push_recent_file(path: str):
        """把 path 加入最近文件列表头部（去重、截断 10 条）并持久化。"""
        if not path:
            return
        recent = list(ctx.settings.get("recent_files", []))
        if path in recent:
            recent.remove(path)
        recent.insert(0, path)
        recent = recent[:10]
        ctx.update_setting("recent_files", recent)

    def open_file_by_path(path: str):
        """从绝对路径打开文件（供侧边栏文件树点击与 open_doc 复用）。

        - 该路径已打开过 → 切换到对应标签，不重复开
        - 当前标签为空白未命名 → 复用该标签加载
        - 否则 → 追加新标签并激活
        """
        # 已在某普通编辑标签打开：直接切换（对比标签不算重复打开）
        for i, t in enumerate(ctx.tabs):
            if t.get("file_path") == path:
                if i != ctx.active_index:
                    ctx.set_active_index(i)
                    ctx.set_session(ctx.session + 1)
                return
        try:
            text = read_text(path)
        except Exception as e:
            ctx.show_snack(f"打开失败：{e}")
            return
        doc = parser.parse_markdown(text)
        doc.file_path = path
        if is_blank_untitled(ctx.cur_tab):
            # 复用当前空标签
            ctx.update_active(document=doc, file_path=path, dirty=False)
        else:
            new_tabs = list(ctx.tabs)
            new_tabs.append({"document": doc, "file_path": path, "dirty": False})
            ctx.set_tabs(new_tabs)
            ctx.tabs_ref.current = new_tabs
            new_idx = len(new_tabs) - 1
            ctx.set_active_index(new_idx)
            ctx.active_index_ref.current = new_idx
        ctx.set_session(ctx.session + 1)
        push_recent_file(path)

    def new_doc():
        """新建标签：当前标签为空白未命名时复用，否则追加新空标签。"""
        if is_blank_untitled(ctx.cur_tab):
            return  # 已是空文档，无需新增
        new_tabs = list(ctx.tabs)
        new_tabs.append(
            {"document": parser.parse_markdown(""), "file_path": None, "dirty": False}
        )
        ctx.set_tabs(new_tabs)
        ctx.tabs_ref.current = new_tabs
        new_idx = len(new_tabs) - 1
        ctx.set_active_index(new_idx)
        ctx.active_index_ref.current = new_idx
        ctx.set_session(ctx.session + 1)

    async def open_doc():
        picker = ctx.picker_holder.current
        if picker is None:
            return
        files = await picker.pick_files(
            dialog_title="打开 Markdown",
            allowed_extensions=["md", "markdown", "txt"],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        if not files:
            return
        open_file_by_path(files[0].path)

    async def open_folder():
        """打开文件夹作为工作区：锚定侧边栏文件树根目录为该文件夹。

        - 通过 FilePicker.get_directory_path 选择目录
        - 持久化到 settings.workspace_folder（跨会话保留）
        - 自动展开侧边栏并切到「文件」面板，确保用户立即看到文件树
        - 在该文件夹内打开子目录文件时，文件树仍以工作区根为锚点
          （open_file_by_path 不再改写 workspace_folder）

        单次原子合并：一次性写入 workspace_folder + sidebar_open + sidebar_panel。
        若分多次调 update_setting，每次基于渲染期快照重建会覆盖前序写入
        （Flet 批量提交 set_settings，末次值生效），故此处直接合并后单次提交。
        """
        picker = ctx.picker_holder.current
        if picker is None:
            return
        folder = await picker.get_directory_path(dialog_title="打开文件夹")
        if not folder:
            return
        # 原子合并：基于最新 ctx.settings 一次性写入三个键
        next_settings = dict(ctx.settings)
        next_settings["workspace_folder"] = folder
        next_settings["sidebar_open"] = True
        next_settings["sidebar_panel"] = "files"
        ctx.set_settings(next_settings)
        save_settings(next_settings)
        ctx.apply_content_layout()
        ctx.show_snack(f"已打开文件夹：{os.path.basename(folder) or folder}")

    async def save_doc(tab_index: int | None = None) -> bool:
        """保存指定标签（默认激活标签）。返回是否真正保存成功（用户取消另存则 False）。

        基于 tabs_ref.current 读取/更新，保证批量保存（确认弹层）时不互相覆盖。
        对比标签分别保存两侧脏文档到各自路径；普通标签走单文档保存。
        """
        if tab_index is None:
            tab_index = ctx.active_index_ref.current
        ts = ctx.tabs_ref.current
        if not (0 <= tab_index < len(ts)):
            return False
        tab = ts[tab_index]

        # ---- 对比标签：分别保存左右两侧脏文档 ----
        if tab.get("type") == "diff":
            left_doc = tab.get("left_doc")
            right_doc = tab.get("right_doc")
            left_path = tab.get("left_path")
            right_path = tab.get("right_path")
            left_dirty = tab.get("left_dirty", False)
            right_dirty = tab.get("right_dirty", False)
            if not left_dirty and not right_dirty:
                return True  # 两侧均无修改，无需保存
            # 保存左侧
            if left_dirty and left_path and left_doc is not None:
                try:
                    write_text(left_path, parser.serialize(left_doc))
                    left_doc.dirty = False
                except Exception as e:
                    ctx.show_snack(f"左侧保存失败：{e}")
                    return False
            # 保存右侧
            if right_dirty and right_path and right_doc is not None:
                try:
                    write_text(right_path, parser.serialize(right_doc))
                    right_doc.dirty = False
                except Exception as e:
                    ctx.show_snack(f"右侧保存失败：{e}")
                    return False
            latest = list(ctx.tabs_ref.current)
            latest[tab_index] = {
                **latest[tab_index],
                "left_dirty": False,
                "right_dirty": False,
            }
            ctx.set_tabs(latest)
            ctx.tabs_ref.current = latest
            ctx.show_snack("对比文档保存成功")
            return True

        # ---- 普通编辑标签：单文档保存 ----
        doc = tab.get("document")
        path = tab.get("file_path")
        if doc is None:
            return False
        if not path:
            picker = ctx.picker_holder.current
            if picker is None:
                return False
            path = await picker.save_file(
                dialog_title="保存 Markdown",
                file_name="未命名.md",
                allowed_extensions=["md"],
                file_type=ft.FilePickerFileType.CUSTOM,
            )
            if not path:
                return False
            if not path.lower().endswith(".md"):
                path += ".md"
        text = parser.serialize(doc)
        try:
            write_text(path, text)
        except Exception as e:
            ctx.show_snack(f"保存失败：{e}")
            return False
        doc.file_path = path
        doc.dirty = False
        # 不可变更新该 tab，基于最新 tabs_ref 避免批量保存时覆盖前序结果
        latest = list(ctx.tabs_ref.current)
        latest[tab_index] = {**latest[tab_index], "file_path": path, "dirty": False}
        ctx.set_tabs(latest)
        ctx.tabs_ref.current = latest
        push_recent_file(path)
        return True

    async def export_doc():
        """导出为 HTML 文件。对比标签不支持导出（两侧均可独立导出，请切到对应编辑标签）。"""
        if ctx.is_diff_tab_ref.current:
            ctx.show_snack("对比标签不支持导出，请切换到普通编辑标签")
            return
        md_text = parser.serialize(ctx.document)
        html = parser.to_html(md_text)
        picker = ctx.picker_holder.current
        if picker is None:
            return
        path = await picker.save_file(
            dialog_title="导出 HTML",
            file_name=file_name(ctx.file_path).replace(".md", ".html"),
            allowed_extensions=["html"],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        if not path:
            return
        if not path.lower().endswith(".html"):
            path += ".html"
        try:
            write_text(path, html)
        except Exception as e:
            ctx.show_snack(f"导出失败：{e}")
            return
        ctx.show_snack("导出成功")

    return {
        "push_recent_file": push_recent_file,
        "open_file_by_path": open_file_by_path,
        "new_doc": new_doc,
        "open_doc": open_doc,
        "open_folder": open_folder,
        "save_doc": save_doc,
        "export_doc": export_doc,
    }
