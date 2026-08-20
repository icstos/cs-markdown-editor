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

import asyncio
import os

import flet as ft

import parser
from app._tab_helpers import is_blank_untitled, tab_group
from config.settings import save_settings
from services import shortcut
from services.backup import is_large_content, write_backup
from services.export import export_to_docx, export_to_html, export_to_pdf
from services.file_io import read_text, write_text, write_text_atomic
from utils.file_helpers import file_name

# 内存绝对保护上限：异步加载已避免 UI 卡死，但仍需防止极端大文件
# （如误选 GB 级二进制文件）导致内存爆炸。超过此值拒绝打开。
_MAX_FILE_BYTES = 100 * 1024 * 1024  # 100 MB


def build_file_io_ops(ctx):
    """构造文件 IO 控制器闭包组。

    返回 dict[str, Callable]：
    push_recent_file / open_file_by_path / new_doc / open_doc / open_folder /
    save_doc / save_as_doc / export_doc / set_status_message /
    backup_tab_before_overwrite / check_external_change
    """
    # 正在异步加载的文件路径集合：防止用户在加载期间重复点击同一文件
    # 触发多次加载（加载完成时从集合移除）。
    _loading_paths: set[str] = set()

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

        异步加载：read_text + parse_markdown 在后台线程执行，不阻塞 UI 事件循环，
        加载期间用户可继续编辑当前文档。加载完成后基于最新 tabs 状态决定复用
        空白标签还是追加新标签。

        - 该路径已打开过 → 同步切换到对应标签，不重复开
        - 正在加载中 → 忽略重复请求
        - 否则 → 启动异步加载，状态栏提示「正在打开...」

        jump_to=(li, off) 非空时，打开后跳转到指定行 offset（侧边栏跨文件搜索结果点击）。
        时序：open 触发 session 变化重建 MarkdownEditor，EditorActions 写入 nav_ref 后，
        _fire_pending_jump effect 消费 pending_jump_ref 调用 jump_to_line(li, off)。
        文件已是当前 tab 时 session 不变，靠 pending_jump_sig 递增触发 effect。

        快捷方式支持（资源管理器直觉）：
        - .lnk 指向 .md/.markdown → 打开/编辑目标文档（最近文件记录目标路径）
        - .lnk 指向其他类型 → 交系统默认程序打开快捷方式（启动目标程序）
        - .lnk 目标失效（被移动/删除）→ SnackBar 提示，不打开
        """
        # 先登记 pending jump（无论后续 session 是否变化，effect 都会触发）
        if jump_to is not None:
            ctx.pending_jump_ref.current = jump_to
            ctx.set_pending_jump_sig(ctx.pending_jump_sig + 1)

        # 快捷方式解析：统一入口分流（侧边栏点击 / 右键打开 / 最近文件 / Ctrl+O）
        display_name = None
        if shortcut.is_shortcut(path):
            target = shortcut.resolve_shortcut_target(path)
            if target and target.lower().endswith((".md", ".markdown")):
                if not os.path.isfile(target):
                    ctx.show_snack(
                        f"快捷方式目标不存在：{os.path.basename(target)}（可能已被移动或删除）"
                    )
                    return
                # 标签栏显示链接文件名（而非目标文件名）：file_path 仍存目标路径，
                # 编辑/保存/去重/外部修改监测都作用于目标文档，仅显示名用 .lnk 名。
                display_name = os.path.basename(path)
                path = target
            else:
                # 非 md 目标 / 解析失败：交系统打开快捷方式本身（启动目标或提示无效）
                ctx.open_external(path)
                return

        # 组内去重（左右标签完全独立）：目标组（焦点侧组）已打开该路径 → 激活
        # 该组标签；仅另一组打开时在目标组开独立副本——同文件左右各一份，
        # 光标 / 撤销历史 / 脏状态互不影响。对比标签不算重复打开。
        g = 1 if (ctx.split_editor and ctx.active_pane_ref.current == 1) else 0
        for i, t in enumerate(ctx.tabs_ref.current):
            if t.get("file_path") == path and tab_group(t) == g:
                ctx.activate_index(i)
                # 同 tab 也需触发 jump —— pending_jump_sig 已递增，effect 会跑
                return
        # 正在加载中：忽略重复请求（防止用户快速双击文件树触发多次加载）
        if path in _loading_paths:
            return
        # 内存绝对保护：防止误选超大文件导致内存爆炸
        try:
            file_size = os.path.getsize(path)
        except OSError as e:
            ctx.show_snack(f"打开失败：{e}")
            return
        if file_size > _MAX_FILE_BYTES:
            size_mb = file_size / (1024 * 1024)
            ctx.show_snack(
                f"文件过大（{size_mb:.1f} MB），超出内存保护上限"
                f"（{_MAX_FILE_BYTES // (1024 * 1024)} MB）"
            )
            return
        # 启动异步加载：不阻塞 UI 事件循环，用户可继续编辑当前文档
        _loading_paths.add(path)
        page = ctx.page_ref.current
        if page is not None:
            page.run_task(_async_open_file, path, display_name)
        else:
            # page 未就绪兜底：同步降级加载（启动初期极少触发）
            _loading_paths.discard(path)
            _do_sync_load(path, display_name)

    async def _async_open_file(path: str, display_name: str | None = None):
        """异步加载文件：read_text + parse_markdown 在后台线程执行。

        加载期间 UI 事件循环保持响应，用户可继续编辑其他标签。加载完成后
        基于 tabs_ref.current（最新 tabs）决定复用空白标签还是追加新标签，
        避免加载期间用户操作导致的竞态。目标组 = 完成时的焦点侧组
        （拆分下打开到正在编辑的那一侧）。
        """
        fname = os.path.basename(display_name or path)
        set_status_message(f"正在打开 {fname}...", "info")
        try:
            # 后台线程执行 IO + 解析（asyncio.to_thread 在 Python 3.9+ 可用），
            # 不阻塞 Flet 事件循环。read_text 与 parse_markdown 均为纯函数，
            # 线程安全（无共享可变状态）。
            text = await asyncio.to_thread(read_text, path)
            doc = await asyncio.to_thread(parser.parse_markdown, text)
        except Exception as e:
            _loading_paths.discard(path)
            ctx.show_snack(f"打开失败：{e}")
            set_status_message(None)
            return
        # 加载完成：基于最新 tabs_ref.current 更新 state（避免加载期间用户
        # 切换标签/打开其他文件导致的竞态）
        doc.file_path = path
        try:
            last_mtime = os.path.getmtime(path)
        except OSError:
            last_mtime = None
        # 目标组 = 完成时焦点侧组：组内去重（另一组的副本不算重复，独立打开）
        g = 1 if (ctx.split_editor and ctx.active_pane_ref.current == 1) else 0
        for t in ctx.tabs_ref.current:
            if t.get("file_path") == path and tab_group(t) == g:
                _loading_paths.discard(path)
                set_status_message(None)
                return
        # 同文件实时同步：另一组已打开该路径 → 共享其 document 对象（含
        # 未保存修改），两侧编辑实时互见；丢弃刚 parse 的独立副本
        for t in ctx.tabs_ref.current:
            if t.get("file_path") == path and t.get("document") is not None:
                doc = t["document"]
                last_mtime = t.get("_last_known_mtime")
                _shared_doc_dirty = bool(t.get("dirty"))
                break
        else:
            _shared_doc_dirty = False
        # 空白复用仅限目标组的激活标签（另一组的空白不动）
        ts = ctx.tabs_ref.current
        gi = ctx.active_index_right_ref.current if g == 1 else ctx.active_index_left_ref.current
        cur = ts[gi] if 0 <= gi < len(ts) else None
        if cur is not None and is_blank_untitled(cur):
            # 复用目标组空白标签：document 整体替换 → 显式重建该组编辑器
            ctx.update_tab(gi, document=doc, file_path=path,
                           display_name=display_name,
                           dirty=_shared_doc_dirty,
                           _last_known_mtime=last_mtime)
            ctx.activate_index(gi)
            ctx.bump_tab_session(gi)
        else:
            ctx.append_and_activate({
                "document": doc, "file_path": path, "display_name": display_name,
                "dirty": _shared_doc_dirty,
                "_last_known_mtime": last_mtime, "group": g,
            })
        push_recent_file(path)
        _loading_paths.discard(path)
        set_status_message(f"已打开 {fname}", "success")

    def _do_sync_load(path: str, display_name: str | None = None):
        """同步降级加载（page 未就绪时使用，启动初期极少触发）。"""
        try:
            text = read_text(path)
        except Exception as e:
            ctx.show_snack(f"打开失败：{e}")
            return
        doc = parser.parse_markdown(text)
        doc.file_path = path
        try:
            last_mtime = os.path.getmtime(path)
        except OSError:
            last_mtime = None
        # 目标组 = 焦点侧组：组内去重（另一组的副本不算重复，独立打开）
        g = 1 if (ctx.split_editor and ctx.active_pane_ref.current == 1) else 0
        ts = ctx.tabs_ref.current
        for t in ts:
            if t.get("file_path") == path and tab_group(t) == g:
                return
        # 同文件实时同步：另一组已打开该路径 → 共享其 document 对象
        _shared_doc_dirty = False
        for t in ts:
            if t.get("file_path") == path and t.get("document") is not None:
                doc = t["document"]
                last_mtime = t.get("_last_known_mtime")
                _shared_doc_dirty = bool(t.get("dirty"))
                break
        gi = ctx.active_index_right_ref.current if g == 1 else ctx.active_index_left_ref.current
        cur = ts[gi] if 0 <= gi < len(ts) else None
        if cur is not None and is_blank_untitled(cur):
            ctx.update_tab(gi, document=doc, file_path=path,
                           display_name=display_name,
                           dirty=_shared_doc_dirty,
                           _last_known_mtime=last_mtime)
            ctx.activate_index(gi)
            ctx.bump_tab_session(gi)
        else:
            ctx.append_and_activate({
                "document": doc, "file_path": path, "display_name": display_name,
                "dirty": _shared_doc_dirty,
                "_last_known_mtime": last_mtime, "group": g,
            })
        push_recent_file(path)

    def new_doc():
        """新建标签：焦点侧组激活标签为空白未命名时复用，否则在该组追加空标签。"""
        g = 1 if (ctx.split_editor and ctx.active_pane_ref.current == 1) else 0
        ts = ctx.tabs_ref.current
        gi = ctx.active_index_right_ref.current if g == 1 else ctx.active_index_left_ref.current
        cur = ts[gi] if 0 <= gi < len(ts) else None
        if cur is not None and is_blank_untitled(cur):
            return  # 该组已是空文档，无需新增
        ctx.append_and_activate({
            "document": parser.parse_markdown(""), "file_path": None,
            "dirty": False, "group": g,
        })

    async def open_doc():
        picker = ctx.picker_holder.current
        if picker is None:
            return
        files = await picker.pick_files(
            dialog_title="打开 Markdown",
            # lnk：指向 .md 的快捷方式（open_file_by_path 解析后打开目标文档）
            allowed_extensions=["md", "markdown", "txt", "lnk"],
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
                    left_path, left_doc, tab, "left", tab_index
                ):
                    return False
            # 保存右侧
            if right_dirty and right_path and right_doc is not None:
                if not await _save_one_side_atomic(
                    right_path, right_doc, tab, "right", tab_index
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
            # 弹「强制保存」对话框：用户可选择跳过原子校验，直接以当前内容写入
            ctx.set_file_dialog({
                "mode": "confirm",
                "title": "保存失败",
                "icon": ft.Icons.SAVE_OUTLINED,
                "message": (
                    f"保存 {os.path.basename(path)} 时出错：\n{e}\n\n"
                    "当前内容已自动备份到恢复目录。\n"
                    "是否强制以当前内容直接写入文件？（跳过完整性校验）"
                ),
                "confirm_label": "强制保存",
                "cancel_label": "取消",
                "action": "force_save",
                "target": path,
                "target_tab_index": tab_index,
            })
            return False
        doc.file_path = path
        doc.dirty = False
        # 记录保存后的 mtime，用于后续外部修改检测
        try:
            tab["_last_known_mtime"] = os.path.getmtime(path)
        except OSError:
            pass
        # 不可变更新该 tab，基于最新 tabs_ref 避免批量保存时覆盖前序结果；
        # 共享同一 document 的其他标签（同文件另一组副本）同步 dirty=False /
        # mtime / 路径——内容实时同步，保存状态也必须一致
        latest = list(ctx.tabs_ref.current)
        for j in range(len(latest)):
            if (j == tab_index
                    or (latest[j].get("document") is doc
                        and latest[j].get("type") != "diff")):
                latest[j] = {
                    **latest[j],
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
        path: str, doc, tab_data: dict, side: str, tab_index: int
    ) -> bool:
        """对比标签单侧保存（原子写入 + 覆盖前备份）。返回是否成功。

        side="left" / "right"，用于错误提示；tab_index 供失败弹窗定位标签。
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
            # 弹「强制保存」对话框（对比标签：force_save_doc 会分别处理脏侧）
            ctx.set_file_dialog({
                "mode": "confirm",
                "title": "保存失败",
                "icon": ft.Icons.SAVE_OUTLINED,
                "message": (
                    f"保存 {file_name(path)}（{side_label}）时出错：\n{e}\n\n"
                    "当前内容已自动备份到恢复目录。\n"
                    "是否强制以当前内容直接写入文件？（跳过完整性校验）"
                ),
                "confirm_label": "强制保存",
                "cancel_label": "取消",
                "action": "force_save",
                "target": path,
                "target_tab_index": tab_index,
            })
            return False

    def force_save_doc(tab_index: int | None = None) -> bool:
        """强制以当前编辑器内容保存（跳过原子写入与完整性校验）。

        保存失败弹窗中用户选择「强制保存」后调用：write_text 直接写入目标
        文件（不经临时文件/SHA256 校验）。进入此路径前，失败方已将当前
        内容自动备份到恢复目录，数据安全有兜底。
        """
        if tab_index is None:
            tab_index = ctx.active_index_ref.current
        ts = ctx.tabs_ref.current
        if not (0 <= tab_index < len(ts)):
            return False
        tab = ts[tab_index]

        # ---- 对比标签：分别强制保存脏侧 ----
        if tab.get("type") == "diff":
            all_ok = True
            for side in ("left", "right"):
                doc = tab.get(f"{side}_doc")
                p = tab.get(f"{side}_path")
                if not (tab.get(f"{side}_dirty", False) and p and doc is not None):
                    continue
                try:
                    write_text(p, parser.serialize(doc))
                    doc.dirty = False
                except Exception as e:
                    all_ok = False
                    side_label = "左侧" if side == "left" else "右侧"
                    write_backup(ctx.settings, tab, parser.serialize(doc))
                    set_status_message(f"{side_label}强制保存失败：{e}", "error")
            if all_ok:
                latest = list(ctx.tabs_ref.current)
                latest[tab_index] = {
                    **latest[tab_index],
                    "left_dirty": False,
                    "right_dirty": False,
                }
                ctx.set_tabs(latest)
                ctx.tabs_ref.current = latest
                set_status_message("已强制保存", "success")
            return all_ok

        # ---- 普通编辑标签 ----
        doc = tab.get("document")
        path = tab.get("file_path")
        if doc is None or not path:
            return False
        text = parser.serialize(doc)
        try:
            write_text(path, text)
        except Exception as e:
            write_backup(ctx.settings, tab, text)
            set_status_message(f"强制保存失败：{e}", "error")
            ctx.show_snack(f"强制保存失败：{e}（当前内容已备份到恢复目录）")
            return False
        doc.file_path = path
        doc.dirty = False
        try:
            last_mtime = os.path.getmtime(path)
        except OSError:
            last_mtime = None
        latest = list(ctx.tabs_ref.current)
        latest[tab_index] = {
            **latest[tab_index],
            "dirty": False,
            "_last_known_mtime": last_mtime,
        }
        ctx.set_tabs(latest)
        ctx.tabs_ref.current = latest
        set_status_message("已强制保存", "success")
        return True

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
        "force_save_doc": force_save_doc,
        "save_as_doc": save_as_doc,
        "export_doc": export_doc,
        "set_status_message": set_status_message,
    }
