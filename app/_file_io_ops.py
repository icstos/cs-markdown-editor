"""文件 IO 控制器（从 main.py 闭包抽取）。

闭包组：push_recent_file / open_file_by_path / new_doc / open_doc /
open_folder / save_doc / save_as_doc / export_doc / set_status_message /
backup_tab_before_overwrite / check_external_change

跨组依赖（通过 ctx 装配槽，调用时读取）：
- settings_controller 组：update_setting（push_recent_file 持久化最近文件；
  open_folder 写入 workspace_folder 并展开侧边栏）
- file_dialogs 组：show_snack（错误/成功提示）
- tab_management 组：update_active（复用空白标签时不可变更新）

设计要点：
- 所有标签写操作基于 tabs_ref.current 最新值计算，保证批量保存时不互相覆盖。
- 对比标签分别保存两侧脏文档到各自路径；普通标签走单文档保存。
- save_doc / save_as_doc 采用 write_text_atomic 原子写入，覆盖原文件前
  生成一份历史副本到备份目录（防止误覆盖后无法找回）。
- save_doc 返回 bool：用户取消另存对话框或失败时返回 False，供
  save_and_close_pending 中止批量关闭。
- 文件路径打开时去重：已在某普通编辑标签打开则直接切换，避免重复开。
- 写入失败兜底：将当前内容写入备份目录，确保数据不丢失；状态栏醒目提示失败原因。
- 外部修改检测：保存前检查文件 mtime，若被外部程序修改弹出重载确认对话框。

依赖项：
- os / parser（解析/序列化）
- services.file_io（read_text / write_text_atomic）
- services.backup（write_backup：覆盖前历史副本 + 写入失败兜底）
- services.export（export_to_html / export_to_docx / export_to_pdf：pandoc 多格式导出）
- app._tab_helpers（is_blank_untitled）
- utils.file_helpers（file_name）
- flet（FilePickerFileType）
"""

import os

import flet as ft

import parser
from app._tab_helpers import is_blank_untitled
from config.settings import save_settings
from services.backup import is_large_content, write_backup
from services.export import export_to_docx, export_to_html, export_to_pdf
from services.file_io import read_text, write_text, write_text_atomic
from utils.file_helpers import file_name


def build_file_io_ops(ctx):
    """构造文件 IO 控制器闭包组。

    返回 dict[str, Callable]：
    push_recent_file / open_file_by_path / new_doc / open_doc / open_folder /
    save_doc / save_as_doc / export_doc / set_status_message /
    backup_tab_before_overwrite / check_external_change
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

    def open_file_by_path(
        path: str,
        jump_to: tuple[int, int | None] | None = None,
    ):
        """从绝对路径打开文件（供侧边栏文件树点击与 open_doc 复用）。

        - 该路径已打开过 → 切换到对应标签，不重复开
        - 当前标签为空白未命名 → 复用该标签加载
        - 否则 → 追加新标签并激活

        jump_to=(li, off) 非空时，打开后跳转到指定行 offset（侧边栏跨文件搜索结果点击）。
        时序：open 触发 session 变化重建 MarkdownEditor，EditorActions 写入 nav_ref 后，
        _fire_pending_jump effect 消费 pending_jump_ref 调用 jump_to_line(li, off)。
        文件已是当前 tab 时 session 不变，靠 pending_jump_sig 递增触发 effect。
        """
        # 先登记 pending jump（无论后续 session 是否变化，effect 都会触发）
        if jump_to is not None:
            ctx.pending_jump_ref.current = jump_to
            ctx.set_pending_jump_sig(ctx.pending_jump_sig + 1)

        # 已在某普通编辑标签打开：直接切换（对比标签不算重复打开）
        for i, t in enumerate(ctx.tabs):
            if t.get("file_path") == path:
                if i != ctx.active_index:
                    ctx.set_active_index(i)
                    ctx.set_session(ctx.session + 1)
                # 同 tab 也需触发 jump —— pending_jump_sig 已递增，effect 会跑
                return
        try:
            text = read_text(path)
        except Exception as e:
            ctx.show_snack(f"打开失败：{e}")
            return
        doc = parser.parse_markdown(text)
        doc.file_path = path
        # 记录文件 mtime，用于后续外部修改检测（缺失则后续首次保存时补记）
        try:
            last_mtime = os.path.getmtime(path)
        except OSError:
            last_mtime = None
        if is_blank_untitled(ctx.cur_tab):
            # 复用当前空标签
            ctx.update_active(
                document=doc, file_path=path, dirty=False,
                _last_known_mtime=last_mtime,
            )
        else:
            new_tabs = list(ctx.tabs)
            new_tabs.append({
                "document": doc, "file_path": path, "dirty": False,
                "_last_known_mtime": last_mtime,
            })
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

    def set_status_message(msg: str, kind: str = "info"):
        """推送轻量状态栏消息（如「已自动保存」「保存失败」）。

        - kind: "info" / "success" / "warn" / "error"，影响颜色与持续时间
        - 消息在状态栏轻量展示，3 秒后自动清空（由 StatusBar 内部计时器清理）
        """
        try:
            ctx.set_status_message(msg, kind)
        except Exception:
            pass

    def _backup_before_overwrite(path: str, content: str, tab_data: dict) -> None:
        """覆盖前备份：原文件存在时生成一份历史副本到备份目录。

        防止误覆盖后无法找回。失败静默忽略（不阻塞保存流程）。
        """
        if not path or not os.path.isfile(path):
            return
        try:
            # 读原文件内容作为历史副本
            original = read_text(path)
            if not original:
                return
            # 用原文件内容 + 当前 tab 元信息生成历史备份
            write_backup(ctx.settings, tab_data, original)
        except Exception:
            pass

    async def save_doc(tab_index: int | None = None, force: bool = False) -> bool:
        """保存指定标签（默认激活标签）。返回是否真正保存成功（用户取消另存则 False）。

        基于 tabs_ref.current 读取/更新，保证批量保存（确认弹层）时不互相覆盖。
        对比标签分别保存两侧脏文档到各自路径；普通标签走单文档保存。

        写入规范：
        - 采用 write_text_atomic 原子写入（临时文件 → 校验 → 替换原文件）。
        - 覆盖原文件前先生成一份历史副本到备份目录（_backup_before_overwrite）。
        - 写入失败时兜底备份当前内容到备份目录，状态栏醒目提示失败原因。
        - 保存前检测外部修改：若原文件被外部程序修改，弹确认对话框。
          force=True 时跳过检测（用户在对话框中选择「保留本地版本」后强制覆盖）。
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
                if not await _save_one_side_atomic(
                    left_path, left_doc, tab, "left"
                ):
                    return False
            # 保存右侧
            if right_dirty and right_path and right_doc is not None:
                if not await _save_one_side_atomic(
                    right_path, right_doc, tab, "right"
                ):
                    return False
            latest = list(ctx.tabs_ref.current)
            latest[tab_index] = {
                **latest[tab_index],
                "left_dirty": False,
                "right_dirty": False,
            }
            ctx.set_tabs(latest)
            ctx.tabs_ref.current = latest
            set_status_message("已保存", "success")
            return True

        # ---- 普通编辑标签：单文档保存 ----
        doc = tab.get("document")
        path = tab.get("file_path")
        if doc is None:
            return False
        _is_new_file = not path  # 另存为：原 path 为空，写盘后新增文件需刷新侧边栏文件树
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
        # 外部修改检测：保存前若文件被外部修改，弹出重载确认
        # force=True 时跳过（用户在对话框中选择「保留本地版本」后强制覆盖）
        if not _is_new_file and not force and ctx.settings.get("detect_external_changes", True):
            ext_check = _check_external_modification(tab, path)
            if ext_check == "modified":
                # 触发外部修改确认对话框（设置 file_dialog state）
                ctx.set_file_dialog({
                    "mode": "confirm",
                    "title": "文件已被外部修改",
                    "message": f"{os.path.basename(path)} 已在外部被修改。\n是否重新加载最新内容？\n（选择「保留本地版本」将用本地内容覆盖外部修改）",
                    "confirm_label": "重新加载",
                    "cancel_label": "保留本地版本",
                    "action": "reload_external",
                    "target": path,
                    "target_tab_index": tab_index,
                })
                return False
        text = parser.serialize(doc)
        # 覆盖前备份：原文件存在时生成历史副本
        if not _is_new_file:
            _backup_before_overwrite(path, text, tab)
        try:
            write_text_atomic(path, text)
        except Exception as e:
            # 写入失败兜底：将当前内容写入备份目录，确保数据不丢失
            write_backup(ctx.settings, tab, text)
            set_status_message(f"保存失败：{e}", "error")
            ctx.show_snack(f"保存失败：{e}（已自动备份到恢复目录）")
            return False
        doc.file_path = path
        doc.dirty = False
        # 记录保存后的 mtime，用于后续外部修改检测
        try:
            tab["_last_known_mtime"] = os.path.getmtime(path)
        except OSError:
            pass
        # 不可变更新该 tab，基于最新 tabs_ref 避免批量保存时覆盖前序结果
        latest = list(ctx.tabs_ref.current)
        latest[tab_index] = {
            **latest[tab_index],
            "file_path": path,
            "dirty": False,
            "_last_known_mtime": tab.get("_last_known_mtime"),
        }
        ctx.set_tabs(latest)
        ctx.tabs_ref.current = latest
        push_recent_file(path)
        if _is_new_file:
            ctx.bump_fs_version()  # 新文件入树，刷新侧边栏
        set_status_message("已保存", "success")
        return True

    async def _save_one_side_atomic(
        path: str, doc, tab_data: dict, side: str
    ) -> bool:
        """对比标签单侧保存（原子写入 + 覆盖前备份）。返回是否成功。

        side="left" / "right"，用于错误提示。
        """
        # 覆盖前备份
        _backup_before_overwrite(path, parser.serialize(doc), tab_data)
        try:
            write_text_atomic(path, parser.serialize(doc))
            doc.dirty = False
            return True
        except Exception as e:
            # 写入失败兜底
            write_backup(ctx.settings, tab_data, parser.serialize(doc))
            side_label = "左侧" if side == "left" else "右侧"
            set_status_message(f"{side_label}保存失败：{e}", "error")
            ctx.show_snack(f"{side_label}保存失败：{e}（已自动备份）")
            return False

    def _check_external_modification(tab: dict, path: str) -> str:
        """检测原文件是否被外部程序修改。

        返回值：
        - "modified"：文件被外部修改（mtime 大于上次记录）
        - "unchanged"：未变化
        - "missing"：文件已被删除

        通过 _last_known_mtime 字段记录上次保存 / 加载时的 mtime。
        """
        if not os.path.isfile(path):
            return "missing"
        try:
            current_mtime = os.path.getmtime(path)
        except OSError:
            return "unchanged"
        last_mtime = tab.get("_last_known_mtime")
        if last_mtime is None:
            # 首次检查：记录当前 mtime，不视为修改
            tab["_last_known_mtime"] = current_mtime
            return "unchanged"
        if current_mtime > last_mtime:
            return "modified"
        return "unchanged"

    async def save_as_doc(tab_index: int | None = None) -> bool:
        """另存为新文件（Ctrl+Shift+S）。

        始终弹出保存对话框，让用户指定新路径；保存后当前编辑上下文切换到新文件路径。
        对比标签不支持另存为（两侧均可独立保存，请切到对应编辑标签）。
        """
        if tab_index is None:
            tab_index = ctx.active_index_ref.current
        ts = ctx.tabs_ref.current
        if not (0 <= tab_index < len(ts)):
            return False
        tab = ts[tab_index]
        if tab.get("type") == "diff":
            ctx.show_snack("对比标签不支持另存为，请切换到普通编辑标签")
            return False
        doc = tab.get("document")
        if doc is None:
            return False
        picker = ctx.picker_holder.current
        if picker is None:
            return False
        # 默认文件名：原文件名 / 未命名.md
        cur_path = tab.get("file_path")
        default_name = os.path.basename(cur_path) if cur_path else "未命名.md"
        new_path = await picker.save_file(
            dialog_title="另存为 Markdown",
            file_name=default_name,
            allowed_extensions=["md"],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        if not new_path:
            return False
        if not new_path.lower().endswith(".md"):
            new_path += ".md"
        # 如果与原路径不同且新路径已存在，先做覆盖前备份
        if new_path != cur_path and os.path.isfile(new_path):
            _backup_before_overwrite(new_path, parser.serialize(doc), tab)
        text = parser.serialize(doc)
        try:
            write_text_atomic(new_path, text)
        except Exception as e:
            # 写入失败兜底备份
            write_backup(ctx.settings, tab, text)
            set_status_message(f"另存失败：{e}", "error")
            ctx.show_snack(f"另存失败：{e}")
            return False
        doc.file_path = new_path
        doc.dirty = False
        # 记录新路径的 mtime
        try:
            tab["_last_known_mtime"] = os.path.getmtime(new_path)
        except OSError:
            pass
        # 切换当前编辑上下文到新路径
        latest = list(ctx.tabs_ref.current)
        latest[tab_index] = {
            **latest[tab_index],
            "file_path": new_path,
            "dirty": False,
            "_last_known_mtime": tab.get("_last_known_mtime"),
        }
        ctx.set_tabs(latest)
        ctx.tabs_ref.current = latest
        push_recent_file(new_path)
        ctx.bump_fs_version()  # 新文件入树
        set_status_message("已另存", "success")
        return True

    async def export_doc(fmt: str = "html"):
        """导出为指定格式（html/docx/pdf）。

        fmt: html / docx / pdf（默认 html，向后兼容）
        对比标签不支持导出（两侧均可独立导出，请切到对应编辑标签）。
        pandoc 不可用时，HTML 回退 mistune 渲染，docx/pdf 给出安装提示。
        """
        if ctx.is_diff_tab_ref.current:
            ctx.show_snack("对比标签不支持导出，请切换到普通编辑标签")
            return
        if fmt not in ("html", "docx", "pdf"):
            fmt = "html"

        md_text = parser.serialize(ctx.document)
        # 文档标题：取文件名（去 .md）或「文档」
        base_name = file_name(ctx.file_path) if ctx.file_path else "文档.md"
        title = base_name[:-3] if base_name.lower().endswith(".md") else base_name

        ext_map = {"html": "html", "docx": "docx", "pdf": "pdf"}
        ext = ext_map[fmt]
        # 默认文件名：原文件名替换扩展名
        default_name = base_name[:-3] + f".{ext}" if base_name.lower().endswith(".md") else f"{base_name}.{ext}"

        picker = ctx.picker_holder.current
        if picker is None:
            return
        dialog_title_map = {"html": "导出 HTML", "docx": "导出 Word", "pdf": "导出 PDF"}
        path = await picker.save_file(
            dialog_title=dialog_title_map[fmt],
            file_name=default_name,
            allowed_extensions=[ext],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        if not path:
            return
        if not path.lower().endswith(f".{ext}"):
            path += f".{ext}"

        try:
            if fmt == "html":
                export_to_html(md_text, path, title=title)
            elif fmt == "docx":
                export_to_docx(md_text, path)
            elif fmt == "pdf":
                export_to_pdf(md_text, path, title=title)
        except RuntimeError as e:
            # pandoc 不可用 / 转换失败：错误消息含安装指引
            ctx.show_snack(str(e))
            return
        except Exception as e:
            ctx.show_snack(f"导出失败：{e}")
            return

        # 成功提示含格式与路径
        size_kb = os.path.getsize(path) // 1024
        ctx.show_snack(f"已导出 {ext.upper()}（{size_kb} KB）")

    return {
        "push_recent_file": push_recent_file,
        "open_file_by_path": open_file_by_path,
        "new_doc": new_doc,
        "open_doc": open_doc,
        "open_folder": open_folder,
        "save_doc": save_doc,
        "save_as_doc": save_as_doc,
        "export_doc": export_doc,
        "set_status_message": set_status_message,
    }
