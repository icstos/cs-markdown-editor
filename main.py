"""Markdown 编辑器入口。

- 注册本地字体 AlibabaPuHuiTi-3-55-Regular
- 声明式渲染：page.render(App)
- 文档状态上抛到 App 层，便于 New / Open / Save
- 段级编辑、Typora 式实时渲染由 views/editor 负责

依赖项：
- parser：Markdown 解析
- models：Document
- config.settings / config.sample：默认设置与示例文档
- services.shortcuts：快捷键管理
- services.file_io：文件读写
- styles：主题与排版常量
- views.*：UI 组件（编辑器 / 标签栏 / 侧边栏 / 状态栏 / 设置弹层 / 快捷键分发）
"""

import asyncio
import json
import os

import flet as ft

import parser
from config.sample import SAMPLE_MD
from config.settings import DEFAULT_SETTINGS, load_settings, save_settings
from models import Document
from services import file_ops
from services.file_io import read_text, write_text
from services.shortcuts import ShortcutManager
from services.ui_feedback import show_snack
from styles import FONT_MAIN, FONT_MONO, Radius, Spacing, get_colors, only_border
from utils.file_helpers import file_name
from views.editor import MarkdownEditor
from views.diff_view import compute_diff_for_editors
from views.file_dialogs import FileActionDialog
from views.key_bindings import KeyDispatcher
from views.settings_dialog import SettingsDialog
from views.sidebar import Sidebar
from views.status_bar import StatusBar
from views.tab_bar import ConfirmCloseDialog, TabBar


def _tab_is_dirty(tab: dict) -> bool:
    """统一脏状态判断：diff 标签任一侧脏即为脏，否则取 dirty 字段。"""
    if tab.get("type") == "diff":
        return bool(tab.get("left_dirty")) or bool(tab.get("right_dirty"))
    return bool(tab.get("dirty", False))


def _tab_paths(tab: dict) -> list[str]:
    """统一路径列表：diff 标签返回 [left_path, right_path]，否则 [file_path]。

    用于文件重命名同步、比较文本获取等需要按路径匹配标签的场景。
    """
    if tab.get("type") == "diff":
        return [p for p in (tab.get("left_path"), tab.get("right_path")) if p]
    p = tab.get("file_path")
    return [p] if p else []


@ft.component
def App():
    # 多文档标签：每个 tab 持有 {document, file_path, dirty}；active_index 指向当前标签
    tabs, set_tabs = ft.use_state(
        lambda: [
            {"document": parser.parse_markdown(SAMPLE_MD), "file_path": None, "dirty": False}
        ]
    )
    active_index, set_active_index = ft.use_state(0)
    session, set_session = ft.use_state(0)  # 切换标签时自增，强制编辑器重置内部状态
    confirm_close, set_confirm_close = ft.use_state(None)  # 待确认关闭的 tab index | None
    # 文件操作对话框状态：{"mode":"input"|"confirm", "action":..., "target":...} | None
    # 由右键菜单触发（新建文件/文件夹/重命名/删除），确认后执行对应文件操作
    file_dialog, set_file_dialog = ft.use_state(None)
    # 文件比较（VSCode 风格）：compare_source 为「选择以进行比较」记录的源文件路径。
    # 对比以标签形式管理：tabs 中 type=="diff" 的标签即对比视图（双编辑器 + 行级 diff），
    # 可与普通编辑标签并存、切换、关闭。is_diff_tab_ref 标记当前激活标签是否为对比。
    compare_source, set_compare_source = ft.use_state(None)
    is_diff_tab_ref = ft.use_ref(False)
    # 对比标签双编辑器导航接口 + 焦点视口
    diff_nav_left = ft.use_ref(None)
    diff_nav_right = ft.use_ref(None)
    diff_active_pane, set_diff_active_pane = ft.use_state(0)  # 0=左, 1=右
    diff_active_pane_ref = ft.use_ref(0)
    diff_active_pane_ref.current = diff_active_pane
    # diff 同步滚动（VSCode 风格）：一侧滚动时另一侧跟随相同像素偏移。
    # diff_gaps 已让两侧差异行视觉对齐，像素同步即可保持对应行对齐。
    # - diff_syncing_ref：程序触发滚动期间为 True，被动侧 on_scroll 据此跳过反向同步
    # - diff_sync_direction_ref："lr"=左主动同步右 / "rl"=右主动同步左 / None。
    #   syncing 期间仅主动侧的 on_scroll 累积 pending，被动侧忽略，避免短文档侧
    #   clamp 后反向拉回长文档侧（VSCode 行为：一侧到底时另一侧可继续独立滚动）
    # - diff_pending_*：syncing 期间主动侧累积的最新请求，标记清除后追赶，避免连续
    #   滚轮滚动时中间帧被丢弃导致跟随滞后
    diff_syncing_ref = ft.use_ref(False)
    diff_sync_direction_ref = ft.use_ref(None)
    diff_pending_target_ref = ft.use_ref(None)  # 追赶目标 nav_ref
    diff_pending_offset_ref = ft.use_ref(0.0)   # 追赶目标 offset
    # 亮/暗主题模式
    theme_mode, set_theme_mode = ft.use_state(ft.ThemeMode.LIGHT)
    settings, set_settings = ft.use_state(load_settings)
    settings_open, set_settings_open = ft.use_state(False)
    settings_tab, set_settings_tab = ft.use_state("edit")
    shortcut_focus, set_shortcut_focus = ft.use_state((None, None))
    # 快捷键捕获模式：(layer, action_id) | (None, None)。设置页点"修改"时置为
    # (layer, action_id)，KeyDispatcher 顶部拦截下一个组合键经 _on_capture 写入配置。
    capturing, set_capturing = ft.use_state((None, None))
    # 导航接口：editor 把光标状态与导航函数写入此 ref，App 的 on_key 据此分发
    nav_ref = ft.use_ref(None)
    # 向右拆分编辑器（VSCode 风格 Ctrl+\）：右侧第二个 MarkdownEditor 视口，
    # 共享同一 document（@ft.observable 自动同步），独立光标/滚动。
    # nav_ref_split 为右侧编辑器的导航接口；active_pane 跟踪当前焦点视口（0=左, 1=右），
    # KeyDispatcher 据此选择 actions_ref，状态栏据此选择光标位置来源。
    split_editor, set_split_editor = ft.use_state(False)
    active_pane, set_active_pane = ft.use_state(0)
    nav_ref_split = ft.use_ref(None)
    active_pane_ref = ft.use_ref(active_pane)
    active_pane_ref.current = active_pane

    # FilePicker / Clipboard：service 实例，通过 ref 在事件回调中访问
    picker_holder = ft.use_ref()
    clipboard_holder = ft.use_ref()
    # page 引用：事件回调中 ft.context.page 可能不可用，提前缓存
    page_ref = ft.use_ref()
    # tabs / active_index 的 ref 镜像：异步回调（autosave 延时保存）读取最新值，
    # 避免闭包捕获渲染期快照导致保存到错误标签
    tabs_ref = ft.use_ref(tabs)
    tabs_ref.current = tabs
    active_index_ref = ft.use_ref(active_index)
    active_index_ref.current = active_index

    # 当前激活标签的派生值（供下游闭包与渲染使用）
    _safe_idx = active_index if 0 <= active_index < len(tabs) else 0
    cur_tab = tabs[_safe_idx] if tabs else {"type": "editor", "document": parser.parse_markdown(""), "file_path": None, "dirty": False}
    is_diff_tab = cur_tab.get("type") == "diff"
    is_diff_tab_ref.current = is_diff_tab
    # editor 标签派生值（diff 标签无这些字段，对应分支不访问）
    document = cur_tab.get("document") if not is_diff_tab else None
    file_path = cur_tab.get("file_path") if not is_diff_tab else None
    dirty = _tab_is_dirty(cur_tab) if not is_diff_tab else (cur_tab.get("left_dirty") or cur_tab.get("right_dirty"))

    def _cur_tab():
        """从 ref 读取最新激活标签（异步场景使用）。"""
        ts = tabs_ref.current
        ai = active_index_ref.current
        if ts and 0 <= ai < len(ts):
            return ts[ai]
        return ts[0] if ts else None

    def _update_active(**changes):
        """不可变更新当前激活标签字段，触发 tabs 重渲染。

        同步写 tabs_ref.current，供 autosave 等异步读取者立即读到最新值
        （不必等下一次渲染同步 ref）。
        """
        new_tabs = list(tabs)
        if not (0 <= active_index < len(new_tabs)):
            return
        new_tabs[active_index] = {**new_tabs[active_index], **changes}
        set_tabs(new_tabs)
        tabs_ref.current = new_tabs

    def _update_tab(tab_index: int, **changes):
        """不可变更新指定索引标签字段。"""
        new_tabs = list(tabs)
        if not (0 <= tab_index < len(new_tabs)):
            return
        new_tabs[tab_index] = {**new_tabs[tab_index], **changes}
        set_tabs(new_tabs)
        tabs_ref.current = new_tabs

    def _doc_has_text(doc) -> bool:
        return any(line.raw.strip() for line in doc.lines)

    def _is_blank_untitled(tab) -> bool:
        # diff 标签始终非空白（不复用为新建/打开的载体）
        if tab.get("type") == "diff":
            return False
        return (
            tab.get("file_path") is None
            and not tab.get("dirty")
            and not _doc_has_text(tab["document"])
        )

    def select_tab(index: int):
        if index == active_index:
            return
        if not (0 <= index < len(tabs)):
            return
        set_active_index(index)
        set_session(session + 1)

    def _cycle_tab(direction: int):
        """Ctrl+Tab / Ctrl+Shift+Tab 循环切换标签。"""
        n = len(tabs_ref.current)
        if n <= 1:
            return
        cur = active_index_ref.current
        nxt = (cur + direction) % n
        select_tab(nxt)

    def _do_close_many(indices):
        """一次性移除多个标签（避免逐个 set_tabs 的索引漂移与 stale 覆盖）。

        基于 tabs_ref.current 最新值计算，空列表回退为一个空白标签。
        """
        ts = list(tabs_ref.current)
        remove_set = {i for i in indices if 0 <= i < len(ts)}
        if not remove_set:
            return
        new_tabs = [t for i, t in enumerate(ts) if i not in remove_set]
        if not new_tabs:
            new_tabs = [
                {"document": parser.parse_markdown(""), "file_path": None, "dirty": False}
            ]
        cur_active = active_index_ref.current
        removed_before = sum(1 for i in remove_set if i < cur_active)
        if cur_active in remove_set:
            new_active = min(max(cur_active - removed_before, 0), len(new_tabs) - 1)
        else:
            new_active = cur_active - removed_before
        set_tabs(new_tabs)
        tabs_ref.current = new_tabs
        set_active_index(new_active)
        active_index_ref.current = new_active
        set_session(session + 1)

    def _request_close(targets):
        """请求关闭一批标签：干净标签直接关，含脏标签则弹统一确认。"""
        ts = tabs_ref.current
        valid = [i for i in targets if 0 <= i < len(ts)]
        if not valid:
            return
        if any(_tab_is_dirty(ts[i]) for i in valid):
            set_confirm_close(valid)
        else:
            _do_close_many(valid)

    def close_tab(index: int):
        """关闭单个标签：脏标签走确认，干净标签直接关。"""
        _request_close([index])

    def _on_tab_context_action(action: str, index: int):
        """标签右键菜单回调：处理打开/新建/路径/重命名/副本/删除/关闭/交换对比等操作。"""
        ts = tabs_ref.current
        if not (0 <= index < len(ts)):
            return
        tab = ts[index]
        # diff 标签无 file_path 字段；统一用 .get() 避免 KeyError
        path = tab.get("file_path")
        is_diff = tab.get("type") == "diff"

        if action == "close":
            close_tab(index)
        elif action == "close_others":
            _request_close([j for j in range(len(ts)) if j != index])
        elif action == "close_all":
            _request_close(list(range(len(ts))))
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
            set_tabs(new_tabs)
            tabs_ref.current = new_tabs
            # 切换焦点到原主动侧的对侧，保持视觉焦点对应同一文档
            _set_diff_active_pane(1 - diff_active_pane_ref.current)
            set_session(session + 1)
        elif action == "copy_path":
            page = page_ref.current
            if path and page is not None:
                page.run_task(_copy_path, path)
            elif page is not None:
                _show_snack("该标签无文件路径")
        elif action == "open":
            if path:
                _open_file_by_path(path)
        elif action == "select_for_compare":
            if path:
                _select_for_compare(path)
        elif action == "compare_with_selected":
            if path:
                _compare_with_selected(path)
        elif action == "new_file":
            if path:
                dir_path = os.path.dirname(path)
                _open_input_dialog(
                    "new_file", "新建文件", ft.Icons.NOTE_ADD,
                    "文件名", "输入文件名（自动添加 .md）", "",
                    f"在 {dir_path} 创建", "创建", dir_path,
                )
        elif action == "new_folder":
            if path:
                dir_path = os.path.dirname(path)
                _open_input_dialog(
                    "new_folder", "新建文件夹", ft.Icons.CREATE_NEW_FOLDER,
                    "文件夹名", "输入文件夹名", "",
                    f"在 {dir_path} 创建", "创建", dir_path,
                )
        elif action == "reveal":
            if path:
                try:
                    file_ops.reveal_in_explorer(path)
                except Exception as e:
                    _show_snack(f"打开失败：{e}")
        elif action == "rename":
            if path:
                dir_path = os.path.dirname(path)
                _open_input_dialog(
                    "rename", "重命名", ft.Icons.DRIVE_FILE_RENAME_OUTLINE,
                    "新名称", "输入新文件名", os.path.basename(path),
                    f"位置：{dir_path}", "重命名", path,
                )
        elif action == "duplicate":
            if path:
                try:
                    new_path = file_ops.duplicate_file(path)
                    _open_file_by_path(new_path)
                    _show_snack(f"已创建副本：{os.path.basename(new_path)}")
                except Exception as e:
                    _show_snack(f"创建副本失败：{e}")
        elif action == "delete":
            if path:
                _open_delete_dialog(path, is_dir=False)

    async def _save_and_close_pending():
        """确认弹层「保存并关闭」：逐个保存脏标签，全部成功后关闭整批。

        任一保存被用户在另存对话框取消或失败则中止，保留未关闭标签。
        """
        pending = confirm_close
        if not pending:
            return
        for idx in list(pending):
            ts = tabs_ref.current
            if 0 <= idx < len(ts) and _tab_is_dirty(ts[idx]):
                ok = await save_doc(idx)
                if not ok:
                    set_confirm_close(None)
                    return
        targets = list(pending)
        set_confirm_close(None)
        _do_close_many(targets)

    def _close_without_save():
        """确认弹层「不保存」：直接关闭整批待确认标签。"""
        pending = confirm_close
        if not pending:
            return
        targets = list(pending)
        set_confirm_close(None)
        _do_close_many(targets)

    def _cancel_close():
        set_confirm_close(None)

    async def _copy_path(path: str):
        cb = clipboard_holder.current
        if cb is not None:
            try:
                await cb.set(path)
                if page_ref.current is not None:
                    _show_snack("路径已复制")
            except Exception:
                pass

    def _show_snack(msg: str):
        """SnackBar 提示（委托 services.ui_feedback.show_snack，page 从 page_ref 读取）。"""
        show_snack(page_ref.current, msg)

    def _update_tab_for_renamed_file(old_path: str, new_path: str):
        """文件重命名后，同步更新引用该文件的标签路径（含对比标签两侧）。"""
        ts = list(tabs_ref.current)
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
            set_tabs(ts)
            tabs_ref.current = ts

    def _close_tabs_for_path(path: str):
        """关闭引用指定路径的所有标签（含对比标签，用于删除后清理）。"""
        ts = tabs_ref.current
        indices = [i for i, t in enumerate(ts) if path in _tab_paths(t)]
        if indices:
            _do_close_many(indices)

    def _on_file_dialog_confirm(value: str = ""):
        """文件操作对话框确认回调。

        input 模式：value 为用户输入的文本（文件名/文件夹名/新名称）。
        confirm 模式：value 为空字符串（删除确认）。
        """
        state = file_dialog
        if state is None:
            return
        action = state["action"]
        target = state["target"]
        set_file_dialog(None)  # 先关闭对话框

        if action == "new_file":
            try:
                path = file_ops.create_file(target, value)
                _open_file_by_path(path)
                _show_snack(f"已创建：{os.path.basename(path)}")
            except Exception as e:
                _show_snack(f"创建失败：{e}")
        elif action == "new_folder":
            try:
                file_ops.create_folder(target, value)
                _show_snack(f"已创建文件夹：{value}")
            except Exception as e:
                _show_snack(f"创建失败：{e}")
        elif action == "rename":
            try:
                new_path = file_ops.rename_path(target, value)
                _update_tab_for_renamed_file(target, new_path)
                _show_snack(f"已重命名为：{os.path.basename(new_path)}")
            except Exception as e:
                _show_snack(f"重命名失败：{e}")
        elif action == "delete":
            try:
                fname = os.path.basename(target)
                file_ops.delete_path(target)
                _close_tabs_for_path(target)
                _show_snack(f"已删除：{fname}")
            except Exception as e:
                _show_snack(f"删除失败：{e}")

    def _open_input_dialog(action: str, title: str, icon: str, label: str,
                           hint: str, default_value: str, location: str,
                           confirm_label: str, target: str):
        """弹出输入对话框（新建文件/文件夹/重命名）。"""
        set_file_dialog({
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

    def _open_delete_dialog(target: str, is_dir: bool):
        """弹出删除确认对话框。"""
        fname = os.path.basename(target)
        title = "删除文件夹" if is_dir else "删除文件"
        msg = f"确定删除{'文件夹' if is_dir else '文件'}「{fname}」？\n此操作不可撤销。"
        set_file_dialog({
            "mode": "confirm",
            "title": title,
            "icon": ft.Icons.DELETE_OUTLINE,
            "message": msg,
            "confirm_label": "删除",
            "danger": True,
            "action": "delete",
            "target": target,
        })

    def _get_text_for_compare(path: str) -> str:
        """获取用于比较的文本：优先用已打开标签的内存内容（含未保存修改），否则读磁盘。

        这样比较未保存的草稿也能反映最新编辑结果，与 VSCode 行为一致。
        """
        for t in tabs_ref.current:
            if path in _tab_paths(t):
                # diff 标签：返回对应侧文档内容；editor 标签：返回 document
                if t.get("type") == "diff":
                    if t.get("left_path") == path:
                        return parser.serialize(t["left_doc"])
                    return parser.serialize(t["right_doc"])
                return parser.serialize(t["document"])
        try:
            return read_text(path)
        except Exception as e:
            _show_snack(f"读取失败：{e}")
            return ""

    def _select_for_compare(path: str):
        """记录比较源文件路径，供后续「与已选项目进行比较」使用。"""
        set_compare_source(path)
        _show_snack(f"已选择以进行比较：{os.path.basename(path)}")

    def _compare_with_selected(right_path: str):
        """用已选源（左）与 right_path（右）创建对比标签。

        两侧均加载为可编辑 Document，实时计算行级 diff 标记和间隙对齐。
        对比以 type=="diff" 标签形式存在，可与普通编辑标签并存、切换、关闭。
        """
        src = compare_source
        if not src:
            _show_snack("请先「选择以进行比较」一个文件")
            return
        if os.path.abspath(src) == os.path.abspath(right_path):
            _show_snack("不能与同一个文件进行比较")
            return
        left_text = _get_text_for_compare(src)
        right_text = _get_text_for_compare(right_path)
        left_doc = parser.parse_markdown(left_text)
        left_doc.file_path = src
        right_doc = parser.parse_markdown(right_text)
        right_doc.file_path = right_path
        new_tab = {
            "type": "diff",
            "left_path": src,
            "right_path": right_path,
            "left_doc": left_doc,
            "right_doc": right_doc,
            "left_dirty": False,
            "right_dirty": False,
        }
        # 复用当前空白未命名标签（完全替换，避免残留 editor 字段），否则追加新标签
        if _is_blank_untitled(cur_tab):
            new_tabs = list(tabs)
            new_tabs[active_index] = new_tab
            new_idx = active_index
        else:
            new_tabs = list(tabs)
            new_tabs.append(new_tab)
            new_idx = len(new_tabs) - 1
        set_tabs(new_tabs)
        tabs_ref.current = new_tabs
        set_active_index(new_idx)
        active_index_ref.current = new_idx
        set_diff_active_pane(0)
        diff_active_pane_ref.current = 0
        set_session(session + 1)

    def _on_sidebar_context_action(action: str, path: str):
        """侧边栏文件/文件夹右键菜单回调。

        path 为文件或文件夹的绝对路径。对于新建操作：
        - 文件夹：在其内部创建（dir_path = path）
        - 文件：在其所在目录创建（dir_path = dirname(path)）
        """
        is_dir = os.path.isdir(path)

        if action == "open":
            if not is_dir:
                _open_file_by_path(path)
        elif action == "select_for_compare":
            if not is_dir:
                _select_for_compare(path)
        elif action == "compare_with_selected":
            if not is_dir:
                _compare_with_selected(path)
        elif action == "new_file":
            dir_path = path if is_dir else os.path.dirname(path)
            _open_input_dialog(
                "new_file", "新建文件", ft.Icons.NOTE_ADD,
                "文件名", "输入文件名（自动添加 .md）", "",
                f"在 {dir_path} 创建", "创建", dir_path,
            )
        elif action == "new_folder":
            dir_path = path if is_dir else os.path.dirname(path)
            _open_input_dialog(
                "new_folder", "新建文件夹", ft.Icons.CREATE_NEW_FOLDER,
                "文件夹名", "输入文件夹名", "",
                f"在 {dir_path} 创建", "创建", dir_path,
            )
        elif action == "copy_path":
            page = page_ref.current
            if page is not None:
                page.run_task(_copy_path, path)
        elif action == "reveal":
            try:
                file_ops.reveal_in_explorer(path)
            except Exception as e:
                _show_snack(f"打开失败：{e}")
        elif action == "rename":
            dir_path = os.path.dirname(path)
            _open_input_dialog(
                "rename", "重命名", ft.Icons.DRIVE_FILE_RENAME_OUTLINE,
                "新名称", "输入新名称", os.path.basename(path),
                f"位置：{dir_path}", "重命名", path,
            )
        elif action == "duplicate":
            if not is_dir:
                try:
                    new_path = file_ops.duplicate_file(path)
                    _open_file_by_path(new_path)
                    _show_snack(f"已创建副本：{os.path.basename(new_path)}")
                except Exception as e:
                    _show_snack(f"创建副本失败：{e}")
        elif action == "delete":
            _open_delete_dialog(path, is_dir=is_dir)

    # 同步设置 page.theme_mode：use_effect 在渲染之后执行，本次渲染期间
    # 子组件（MarkdownEditor→LineView 等）调用 _current_colors() 读到的
    # 还是旧 page.theme_mode，导致切换主题后内容颜色不实时刷新。
    # 在渲染期间同步写入，保证子组件取色正确。
    _page_now = ft.context.page
    if _page_now is not None:
        _page_now.theme_mode = theme_mode
        _page_now.bgcolor = get_colors(theme_mode).bg

    def _mount_picker():
        page = ft.context.page
        page_ref.current = page
        # FilePicker / Clipboard 是 service，不需要添加到 page.overlay
        picker_holder.current = ft.FilePicker()
        clipboard_holder.current = ft.Clipboard()

    ft.use_effect(_mount_picker, [])

    def _apply_theme():
        # 推送 page 级属性（theme_mode / bgcolor / 原生 chrome）到 UI
        page = ft.context.page
        page.theme_mode = theme_mode
        page.bgcolor = get_colors(theme_mode).bg
        page.update()
        return

    ft.use_effect(_apply_theme, [theme_mode])

    def toggle_theme():
        set_theme_mode(
            ft.ThemeMode.DARK
            if theme_mode == ft.ThemeMode.LIGHT
            else ft.ThemeMode.LIGHT
        )

    def open_settings():
        set_settings_open(True)

    def close_settings():
        set_capturing((None, None))
        set_settings_open(False)

    def select_settings_tab(tab: str):
        # 切 tab 时退出捕获模式，避免遗留捕获态
        set_capturing((None, None))
        set_settings_tab(tab)

    # ShortcutManager：无状态读取器，每次渲染重建。update_setting 通过 lambda
    # 前向引用（update_setting 在下方定义，调用时才解析），打破循环依赖。
    shortcut_mgr = ShortcutManager(settings, lambda key, value: update_setting(key, value))

    def update_setting(key: str, value):
        next_settings = dict(settings)
        next_settings[key] = value
        set_settings(next_settings)
        save_settings(next_settings)
        _apply_content_layout()
        if key == "shortcuts":
            layer, action = shortcut_mgr.first_conflict_target()
            set_shortcut_focus((layer, action))

    # 快捷键捕获回调：KeyDispatcher 在捕获模式下捕获到组合键后调用。
    # 通过 dispatcher_ref 同步链路，此处引用的 shortcut_mgr 总是当次渲染的最新实例。
    def _on_capture(layer: str, action_id: str, combo: str):
        # combo="" 表示清空绑定（Backspace）
        shortcut_mgr.update(layer, action_id, combo)
        set_capturing((None, None))

    def _on_cancel_capture():
        set_capturing((None, None))

    def _autosave_enabled_for(tab) -> bool:
        """自动保存是否对该标签生效：需开启 auto_save 且标签有可写路径。

        对比标签任一侧有路径即生效；普通标签需有 file_path。
        """
        if not settings.get("auto_save", False) or not tab:
            return False
        return bool(_tab_paths(tab))

    def _schedule_autosave():
        """基于 ref 读取当前激活标签，延时 2s 自动保存该标签。

        捕获调度时的 active_index，即便用户切换到其他标签，仍保存当初变脏的标签。
        """
        tab = _cur_tab()
        if not tab or not _tab_is_dirty(tab) or not _autosave_enabled_for(tab):
            return
        page = page_ref.current
        if page is None:
            return
        sched_idx = active_index_ref.current

        async def _debounced_save():
            await asyncio.sleep(2.0)
            ts = tabs_ref.current
            if not (0 <= sched_idx < len(ts)):
                return
            t2 = ts[sched_idx]
            if _tab_is_dirty(t2) and _autosave_enabled_for(t2):
                await save_doc(sched_idx)

        page.run_task(_debounced_save)

    def reset_settings():
        next_settings = dict(DEFAULT_SETTINGS)
        set_settings(next_settings)
        save_settings(next_settings)

    def reset_shortcuts():
        next_settings = dict(settings)
        next_settings["shortcuts"] = {k: dict(v) for k, v in DEFAULT_SETTINGS["shortcuts"].items()}
        set_settings(next_settings)
        save_settings(next_settings)
        set_shortcut_focus((None, None))
        select_settings_tab("advanced")
        open_settings()

    async def export_shortcuts():
        picker = picker_holder.current
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
                settings.get("shortcuts", DEFAULT_SETTINGS["shortcuts"]),
                ensure_ascii=False,
                indent=2,
            )
            write_text(path, payload)
        except Exception as e:
            _show_snack(f"导出失败：{e}")
            return
        _show_snack("快捷键方案已导出")

    async def import_shortcuts():
        picker = picker_holder.current
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
            next_settings = dict(settings)
            next_settings["shortcuts"] = data
            set_settings(next_settings)
            save_settings(next_settings)
            set_shortcut_focus((None, None))
        except Exception as e:
            _show_snack(f"导入失败：{e}")
            return
        _show_snack("快捷键方案已导入")

    def _push_recent_file(path: str):
        """把 path 加入最近文件列表头部（去重、截断 10 条）并持久化。"""
        if not path:
            return
        recent = list(settings.get("recent_files", []))
        if path in recent:
            recent.remove(path)
        recent.insert(0, path)
        recent = recent[:10]
        update_setting("recent_files", recent)

    def _open_file_by_path(path: str):
        """从绝对路径打开文件（供侧边栏文件树点击与 open_doc 复用）。

        - 该路径已打开过 → 切换到对应标签，不重复开
        - 当前标签为空白未命名 → 复用该标签加载
        - 否则 → 追加新标签并激活
        """
        # 已在某普通编辑标签打开：直接切换（对比标签不算重复打开）
        for i, t in enumerate(tabs):
            if t.get("file_path") == path:
                if i != active_index:
                    set_active_index(i)
                    set_session(session + 1)
                return
        try:
            text = read_text(path)
        except Exception as e:
            _show_snack(f"打开失败：{e}")
            return
        doc = parser.parse_markdown(text)
        doc.file_path = path
        if _is_blank_untitled(cur_tab):
            # 复用当前空标签
            _update_active(document=doc, file_path=path, dirty=False)
        else:
            new_tabs = list(tabs)
            new_tabs.append({"document": doc, "file_path": path, "dirty": False})
            set_tabs(new_tabs)
            tabs_ref.current = new_tabs
            new_idx = len(new_tabs) - 1
            set_active_index(new_idx)
            active_index_ref.current = new_idx
        set_session(session + 1)
        _push_recent_file(path)

    def toggle_sidebar():
        update_setting("sidebar_open", not settings.get("sidebar_open", False))

    def toggle_word_wrap():
        """切换自动换行（VSCode 风格 Alt+Z）：开 = 软换行，关 = 长行不换行。"""
        update_setting("word_wrap", not settings.get("word_wrap", True))

    def toggle_split_editor():
        """向右拆分编辑器（VSCode 风格 Ctrl+\）：切换右侧第二视口，共享同一文档。"""
        # 对比标签下禁用拆分切换：两者互斥，避免对比标签内意外进入拆分
        if is_diff_tab_ref.current:
            return
        next_split = not split_editor
        set_split_editor(next_split)
        # 关闭拆分时焦点回到左侧；打开时默认焦点左侧
        set_active_pane(0)
        active_pane_ref.current = 0

    def _set_active_pane(pane: int):
        """切换焦点视口（点击/光标聚焦触发）。同值不重渲染。"""
        if active_pane_ref.current != pane:
            set_active_pane(pane)
            active_pane_ref.current = pane

    def _set_diff_active_pane(pane: int):
        """切换对比模式焦点视口（0=左, 1=右）。同值不重渲染。"""
        if diff_active_pane_ref.current != pane:
            set_diff_active_pane(pane)
            diff_active_pane_ref.current = pane

    def _on_diff_dirty_change(side: int, dirty: bool):
        """对比标签侧文档脏状态变化回调。

        side: 0=左, 1=右。仅当状态真正变化时更新标签，避免高频回调触发重渲染。
        同步写 tabs_ref.current，使 autosave 等异步读取者立即拿到最新脏状态。
        """
        ts = list(tabs_ref.current)
        ai = active_index_ref.current
        if not (0 <= ai < len(ts)) or ts[ai].get("type") != "diff":
            return
        tab = ts[ai]
        key = "left_dirty" if side == 0 else "right_dirty"
        if tab.get(key) == dirty:
            return
        ts[ai] = {**tab, key: dirty}
        set_tabs(ts)
        tabs_ref.current = ts

    def _get_active_nav():
        """统一获取当前焦点视口的 nav_ref。

        优先级：对比标签 > split_editor > 单编辑器。键盘事件、跳转、状态栏
        光标位置都通过此函数路由，避免散落的分支判断。
        """
        if is_diff_tab_ref.current:
            return diff_nav_right if diff_active_pane_ref.current == 1 else diff_nav_left
        if split_editor and active_pane_ref.current == 1:
            return nav_ref_split
        return nav_ref

    # ============ diff 同步滚动 ============
    def _sync_diff_scroll_to(target_nav, offset: float, direction: str):
        """将 target_nav 侧滚动到 offset（像素同步）。

        direction: "lr"=左主动同步右 / "rl"=右主动同步左。标记主动侧，使 syncing
        期间仅主动侧 on_scroll 累积 pending，被动侧忽略，避免 clamp 反向拉回。

        流程：置 syncing+direction 标记 → 调用目标侧 scroll_to_offset(duration=0)
        → 异步等待 Flutter 执行 + 触发目标侧 on_scroll（被动侧被标记拦截）
        → 清除标记 → 追赶 syncing 期间主动侧累积的最新请求。
        """
        target = target_nav.current if target_nav is not None else None
        if target is None or target.scroll_to_offset is None:
            return
        diff_syncing_ref.current = True
        diff_sync_direction_ref.current = direction
        diff_pending_target_ref.current = None
        diff_pending_offset_ref.current = 0.0
        try:
            target.scroll_to_offset(offset)
        except Exception:
            diff_syncing_ref.current = False
            diff_sync_direction_ref.current = None
            return
        page = page_ref.current
        if page is None:
            diff_syncing_ref.current = False
            diff_sync_direction_ref.current = None
            return
        page.run_task(_after_diff_sync)

    async def _after_diff_sync():
        """等待目标侧滚动完成 + on_scroll 触发后，清除同步标记并追赶累积请求。"""
        # duration=0 的 scroll_to 仍需一次 Flutter 帧往返执行 + 触发 on_scroll
        await asyncio.sleep(0.06)
        direction = diff_sync_direction_ref.current
        diff_syncing_ref.current = False
        diff_sync_direction_ref.current = None
        # 追赶：syncing 期间主动侧继续滚动累积的最新 offset
        pending_nav = diff_pending_target_ref.current
        pending_off = diff_pending_offset_ref.current
        if pending_nav is not None:
            diff_pending_target_ref.current = None
            _sync_diff_scroll_to(pending_nav, pending_off, direction)

    def _on_diff_left_scroll(offset: float, max_scroll: float, viewport_h: float):
        """左侧滚动 → 同步右侧。

        syncing 期间：仅当左侧是主动侧（direction=lr）才累积 pending 追赶；
        若左侧是被动侧（direction=rl，被右侧同步触发），忽略，避免反向拉回。
        """
        if diff_syncing_ref.current:
            if diff_sync_direction_ref.current == "lr":
                diff_pending_target_ref.current = diff_nav_right
                diff_pending_offset_ref.current = offset
            return
        _sync_diff_scroll_to(diff_nav_right, offset, "lr")

    def _on_diff_right_scroll(offset: float, max_scroll: float, viewport_h: float):
        """右侧滚动 → 同步左侧。

        syncing 期间：仅当右侧是主动侧（direction=rl）才累积 pending 追赶；
        若右侧是被动侧（direction=lr，被左侧同步触发），忽略，避免反向拉回。
        """
        if diff_syncing_ref.current:
            if diff_sync_direction_ref.current == "rl":
                diff_pending_target_ref.current = diff_nav_left
                diff_pending_offset_ref.current = offset
            return
        _sync_diff_scroll_to(diff_nav_left, offset, "rl")

    def _apply_content_layout():
        page = page_ref.current
        if page is None:
            return
        page.update()

    def change_sidebar_panel(panel: str):
        update_setting("sidebar_panel", panel)

    def change_sidebar_width(width: int):
        update_setting("sidebar_width", width)

    def jump_to_line(li: int):
        # 跳转到当前焦点视口（diff / 拆分 / 单编辑器统一路由）
        active_nav = _get_active_nav()
        actions = active_nav.current
        if actions is not None:
            actions.jump_to_line(li)

    def on_dirty_change(d: bool):
        """编辑器上报脏状态变化时，更新当前标签的 dirty（仅状态变化时写，避免每键重渲染）。

        仅普通编辑标签走此回调；对比标签两侧各自走 _on_diff_dirty_change。
        """
        if is_diff_tab_ref.current:
            return
        if cur_tab.get("dirty") != d:
            _update_active(dirty=d)
        if d:
            _schedule_autosave()

    def new_doc():
        """新建标签：当前标签为空白未命名时复用，否则追加新空标签。"""
        if _is_blank_untitled(cur_tab):
            return  # 已是空文档，无需新增
        new_tabs = list(tabs)
        new_tabs.append(
            {"document": parser.parse_markdown(""), "file_path": None, "dirty": False}
        )
        set_tabs(new_tabs)
        tabs_ref.current = new_tabs
        new_idx = len(new_tabs) - 1
        set_active_index(new_idx)
        active_index_ref.current = new_idx
        set_session(session + 1)

    async def open_doc():
        picker = picker_holder.current
        if picker is None:
            return
        files = await picker.pick_files(
            dialog_title="打开 Markdown",
            allowed_extensions=["md", "markdown", "txt"],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        if not files:
            return
        _open_file_by_path(files[0].path)

    async def save_doc(tab_index: int | None = None) -> bool:
        """保存指定标签（默认激活标签）。返回是否真正保存成功（用户取消另存则 False）。

        基于 tabs_ref.current 读取/更新，保证批量保存（确认弹层）时不互相覆盖。
        对比标签分别保存两侧脏文档到各自路径；普通标签走单文档保存。
        """
        if tab_index is None:
            tab_index = active_index_ref.current
        ts = tabs_ref.current
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
                    _show_snack(f"左侧保存失败：{e}")
                    return False
            # 保存右侧
            if right_dirty and right_path and right_doc is not None:
                try:
                    write_text(right_path, parser.serialize(right_doc))
                    right_doc.dirty = False
                except Exception as e:
                    _show_snack(f"右侧保存失败：{e}")
                    return False
            latest = list(tabs_ref.current)
            latest[tab_index] = {
                **latest[tab_index],
                "left_dirty": False,
                "right_dirty": False,
            }
            set_tabs(latest)
            tabs_ref.current = latest
            _show_snack("对比文档保存成功")
            return True

        # ---- 普通编辑标签：单文档保存 ----
        doc = tab.get("document")
        path = tab.get("file_path")
        if doc is None:
            return False
        if not path:
            picker = picker_holder.current
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
            _show_snack(f"保存失败：{e}")
            return False
        doc.file_path = path
        doc.dirty = False
        # 不可变更新该 tab，基于最新 tabs_ref 避免批量保存时覆盖前序结果
        latest = list(tabs_ref.current)
        latest[tab_index] = {**latest[tab_index], "file_path": path, "dirty": False}
        set_tabs(latest)
        tabs_ref.current = latest
        _push_recent_file(path)
        return True

    async def export_doc():
        """导出为 HTML 文件。对比标签不支持导出（两侧均可独立导出，请切到对应编辑标签）。"""
        if is_diff_tab_ref.current:
            _show_snack("对比标签不支持导出，请切换到普通编辑标签")
            return
        md_text = parser.serialize(document)
        html = parser.to_html(md_text)
        picker = picker_holder.current
        if picker is None:
            return
        path = await picker.save_file(
            dialog_title="导出 HTML",
            file_name=file_name(file_path).replace(".md", ".html"),
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
            _show_snack(f"导出失败：{e}")
            return
        _show_snack("导出成功")

    # ---- 快捷键 + 光标导航 ----
    # page.on_keyboard_event 的 KeyboardEvent 直接提供 ctrl/meta 修饰键状态
    # 粘贴前的 draft 快照（供 handle_paste 做 diff 定位粘贴位置）
    paste_old_draft = ft.use_ref("")
    # dispatcher_ref：渲染期同步赋值最新 dispatcher，_handler 通过 ref 读取。
    # 修复 use_effect(_bind_keyboard, []) 空依赖导致 _handler 闭包捕获首次渲染
    # dispatcher 的过期问题——改快捷键后新键位才能立即生效（无需重启）。
    dispatcher_ref = ft.use_ref(None)

    # KeyDispatcher：替代 on_key 闭包。持有 shortcut_mgr + nav_ref 引用，
    # editor.py 每次渲染写入最新 EditorActions 后 dispatcher 读到的就是最新值，
    # 无需 on_key_ref 中转层。
    # 拆分/对比编辑器：根据当前模式选择对应视口的 nav_ref，键盘事件作用于焦点视口。
    if is_diff_tab:
        active_nav_ref = diff_nav_right if diff_active_pane == 1 else diff_nav_left
    elif split_editor and active_pane == 1:
        active_nav_ref = nav_ref_split
    else:
        active_nav_ref = nav_ref
    dispatcher = KeyDispatcher(
        shortcut_mgr=shortcut_mgr,
        actions_ref=active_nav_ref,
        clipboard_ref=clipboard_holder,
        page_ref=page_ref,
        paste_old_draft=paste_old_draft,
        app_callbacks={
            "save": save_doc,
            "new": new_doc,
            "open": open_doc,
            "toggle_sidebar": toggle_sidebar,
            "toggle_theme": toggle_theme,
            "toggle_word_wrap": toggle_word_wrap,
            "toggle_split_editor": toggle_split_editor,
            "open_settings": open_settings,
            "close_tab": lambda: close_tab(active_index_ref.current),
            "next_tab": lambda: _cycle_tab(1),
            "prev_tab": lambda: _cycle_tab(-1),
        },
        capturing=capturing,
        on_capture=_on_capture,
        on_cancel_capture=_on_cancel_capture,
    )
    # 渲染期同步：每次重渲染把最新 dispatcher 写入 ref，_handler 即可读到最新值
    dispatcher_ref.current = dispatcher

    def _bind_keyboard():
        page = ft.context.page
        page_ref.current = page
        if page is None:
            return lambda: None

        def _handler(e):
            # 通过 ref 读最新 dispatcher，避免闭包捕获首次渲染的过期实例
            d = dispatcher_ref.current
            if d is None:
                return
            try:
                d.handle(e)
            except Exception:
                return

        page.on_keyboard_event = _handler

        def _cleanup():
            if page_ref.current is not None:
                try:
                    page_ref.current.on_keyboard_event = None
                except Exception:
                    pass

        return _cleanup

    ft.use_effect(_bind_keyboard, [])

    settings_view = SettingsDialog(
        open_state=settings_open,
        tab=settings_tab,
        settings=settings,
        theme_mode=theme_mode,
        shortcut_focus=shortcut_focus,
        shortcut_mgr=shortcut_mgr,
        on_close=close_settings,
        on_select_tab=select_settings_tab,
        on_update=update_setting,
        on_reset_all=reset_settings,
        on_reset_shortcuts=reset_shortcuts,
        on_import=lambda: page_ref.current.run_task(import_shortcuts),
        on_export=lambda: page_ref.current.run_task(export_shortcuts),
        capturing=capturing,
        on_capture_click=lambda layer, action_id: set_capturing((layer, action_id)),
        on_cancel_capture_click=lambda: set_capturing((None, None)),
    )

    sidebar_open = settings.get("sidebar_open", False)
    # 侧边栏：始终渲染 Sidebar，外层 Container 宽度动画 0↔sidebar_width，
    # clip_behavior=HARD_EDGE 在收拢时裁剪内容，实现 VSCode 式平滑开合。
    # 始终保持 Sidebar 挂载可保留内部状态（搜索词 / 文件过滤 / 滚动位置）。
    # 对比标签下 document=None，需传当前焦点侧文档以保持大纲/搜索可用。
    if is_diff_tab:
        _sidebar_doc = cur_tab["right_doc"] if diff_active_pane == 1 else cur_tab["left_doc"]
        _sidebar_path = cur_tab["right_path"] if diff_active_pane == 1 else cur_tab["left_path"]
    else:
        _sidebar_doc = document
        _sidebar_path = file_path
    sidebar_width = settings.get("sidebar_width", 256)
    sidebar_container = ft.Container(
        width=sidebar_width if sidebar_open else 0,
        animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        content=Sidebar(
            document=_sidebar_doc,
            file_path=_sidebar_path,
            theme_mode=theme_mode,
            settings=settings,
            active_panel=settings.get("sidebar_panel", "files"),
            on_change_panel=change_sidebar_panel,
            on_open_file=_open_file_by_path,
            on_jump_to_line=jump_to_line,
            on_width_change=change_sidebar_width,
            on_file_context_action=_on_sidebar_context_action,
            compare_source=compare_source,
        ),
    )
    # 编辑器公共 props：左右两视口共享（仅 nav_ref / key / show_toolbar / on_editor_focus 不同）
    _editor_common = dict(
        document=document,
        file_path=file_path,
        on_new=new_doc,
        on_open=lambda: page_ref.current.run_task(open_doc),
        on_save=lambda: page_ref.current.run_task(save_doc),
        on_export=lambda: page_ref.current.run_task(export_doc),
        on_dirty_change=on_dirty_change,
        clipboard_ref=clipboard_holder,
        theme_mode=theme_mode,
        on_toggle_theme=toggle_theme,
        settings=settings,
        on_open_settings=open_settings,
        sidebar_open=sidebar_open,
        on_toggle_sidebar=toggle_sidebar,
        shortcut_mgr=shortcut_mgr,
    )

    if is_diff_tab:
        # ============ 对比标签：双 MarkdownEditor 并排 + 行级 diff 背景着色 ============
        # 左右各一个原生可编辑 MarkdownEditor，共享 diff_marks/diff_gaps 实现差异可视化。
        # diff 在每次渲染时由 serialize(left_doc)/serialize(right_doc) 重算——Document
        # 为 @ft.observable，任一侧编辑触发 App 重渲染，diff 标记/间隙即时更新。
        _ldoc = cur_tab["left_doc"]
        _rdoc = cur_tab["right_doc"]
        _lpath = cur_tab["left_path"]
        _rpath = cur_tab["right_path"]
        _ltext = parser.serialize(_ldoc)
        _rtext = parser.serialize(_rdoc)
        marks_left, marks_right, gaps_left, gaps_right = compute_diff_for_editors(
            _ltext, _rtext
        )
        # 差异统计：added 仅右侧、removed 仅左侧、modified 两侧各一行
        _added = sum(1 for v in marks_right.values() if v == "added")
        _removed = sum(1 for v in marks_left.values() if v == "removed")
        _modified = sum(1 for v in marks_right.values() if v == "modified")

        # 对比标签公共 props：不复用 _editor_common（其 document/file_path/on_dirty_change
        # 绑定当前 editor 标签），对比编辑器各自持有 diff 文档。on_dirty_change/on_save
        # 按侧传入，不放在共享 dict。
        _diff_common = dict(
            on_new=new_doc,
            on_open=lambda: page_ref.current.run_task(open_doc),
            on_export=lambda: page_ref.current.run_task(export_doc),
            clipboard_ref=clipboard_holder,
            theme_mode=theme_mode,
            on_toggle_theme=toggle_theme,
            settings=settings,
            on_open_settings=open_settings,
            sidebar_open=sidebar_open,
            on_toggle_sidebar=toggle_sidebar,
            shortcut_mgr=shortcut_mgr,
        )

        _c = get_colors(theme_mode)
        _is_dark = theme_mode == ft.ThemeMode.DARK
        _added_char = "#7ee787" if _is_dark else "#1a7f37"
        _removed_char = "#f47067" if _is_dark else "#cf222e"
        _left_name = os.path.basename(_lpath) if _lpath else "未命名"
        _right_name = os.path.basename(_rpath) if _rpath else "未命名"

        # 对比头部：文件名 + 差异统计 + 关闭按钮（关闭当前对比标签）
        _diff_header = ft.Container(
            bgcolor=_c.toolbar_bg,
            border=only_border(bottom=ft.BorderSide(1, _c.border)),
            padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.SM),
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.COMPARE_ARROWS, color=_c.link, size=18),
                    ft.Text(
                        value=f"{_left_name}  →  {_right_name}",
                        size=13, color=_c.text, font_family=FONT_MAIN,
                        weight=ft.FontWeight.W_600,
                    ),
                    ft.Container(width=Spacing.MD),
                    ft.Container(
                        bgcolor=_c.diff_add_bg, border_radius=Radius.SM,
                        padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=2),
                        content=ft.Text(f"+{_added}", size=11, color=_added_char,
                                        font_family=FONT_MONO, weight=ft.FontWeight.W_600),
                    ),
                    ft.Container(
                        bgcolor=_c.diff_del_bg, border_radius=Radius.SM,
                        padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=2),
                        content=ft.Text(f"-{_removed}", size=11, color=_removed_char,
                                        font_family=FONT_MONO, weight=ft.FontWeight.W_600),
                    ),
                    ft.Container(
                        bgcolor=ft.Colors.with_opacity(0.08, _c.text),
                        border_radius=Radius.SM,
                        padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=2),
                        content=ft.Text(f"~{_modified}", size=11, color=_c.muted,
                                        font_family=FONT_MONO, weight=ft.FontWeight.W_600),
                    ),
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE, tooltip="关闭对比标签",
                        on_click=lambda e: close_tab(active_index),
                        icon_size=18, style=ft.ButtonStyle(color=_c.muted),
                    ),
                ],
                spacing=Spacing.SM,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        editor_area = ft.Column(
            controls=[
                _diff_header,
                ft.Row(
                    controls=[
                        ft.Container(
                            content=MarkdownEditor(
                                key=f"diff-left-{active_index}",
                                document=_ldoc,
                                file_path=_lpath,
                                nav_ref=diff_nav_left,
                                diff_marks=marks_left,
                                diff_gaps=gaps_left,
                                on_editor_focus=lambda: _set_diff_active_pane(0),
                                on_dirty_change=lambda d: _on_diff_dirty_change(0, d),
                                on_save=lambda: page_ref.current.run_task(save_doc),
                                on_scroll_change=_on_diff_left_scroll,
                                **_diff_common,
                            ),
                            expand=True,
                            on_click=lambda e: _set_diff_active_pane(0),
                        ),
                        ft.VerticalDivider(width=1, color=_c.border),
                        ft.Container(
                            content=MarkdownEditor(
                                key=f"diff-right-{active_index}",
                                document=_rdoc,
                                file_path=_rpath,
                                nav_ref=diff_nav_right,
                                diff_marks=marks_right,
                                diff_gaps=gaps_right,
                                show_toolbar=False,
                                on_editor_focus=lambda: _set_diff_active_pane(1),
                                on_dirty_change=lambda d: _on_diff_dirty_change(1, d),
                                on_save=lambda: page_ref.current.run_task(save_doc),
                                on_scroll_change=_on_diff_right_scroll,
                                keyboard_autofocus=False,
                                **_diff_common,
                            ),
                            expand=True,
                            on_click=lambda e: _set_diff_active_pane(1),
                        ),
                    ],
                    spacing=0,
                    expand=True,
                ),
            ],
            spacing=0,
            expand=True,
        )
    elif split_editor:
        # 拆分：左 + 分隔线 + 右，各占一半；右侧隐藏工具栏保持简洁。
        # 两视口共享同一 document（@ft.observable），各自独立光标/滚动。
        editor_area = ft.Row(
            controls=[
                ft.Container(
                    content=MarkdownEditor(
                        key=f"{session}-0",
                        nav_ref=nav_ref,
                        on_editor_focus=lambda: _set_active_pane(0),
                        **_editor_common,
                    ),
                    expand=True,
                    on_click=lambda e: _set_active_pane(0),
                ),
                ft.VerticalDivider(width=1, color=get_colors(theme_mode).border),
                ft.Container(
                    content=MarkdownEditor(
                        key=f"{session}-1",
                        nav_ref=nav_ref_split,
                        show_toolbar=False,
                        on_editor_focus=lambda: _set_active_pane(1),
                        keyboard_autofocus=False,
                        **_editor_common,
                    ),
                    expand=True,
                    on_click=lambda e: _set_active_pane(1),
                ),
            ],
            spacing=0,
            expand=True,
        )
    else:
        editor_area = ft.Container(
            content=MarkdownEditor(
                key=f"{session}-0",  # 与拆分时左视口同 key，切换拆分不重置左视口光标
                nav_ref=nav_ref,
                **_editor_common,
            ),
            expand=True,
        )

    body = ft.Row(
        controls=[
            sidebar_container,
            editor_area,
        ],
        spacing=0,
        expand=True,
    )

    # 底部状态栏：贯穿侧边栏 + 编辑区全宽，放在 body 之下
    # 对比标签时反映当前焦点对比视口的文档/路径/光标；拆分时按 active_pane 选择。
    if is_diff_tab:
        _footer_doc = cur_tab["right_doc"] if diff_active_pane == 1 else cur_tab["left_doc"]
        _footer_path = cur_tab["right_path"] if diff_active_pane == 1 else cur_tab["left_path"]
        _footer_split = False
        _footer_split_cb = None  # 对比标签下禁用拆分切换，避免模式冲突
    else:
        _footer_doc = document
        _footer_path = file_path
        _footer_split = split_editor
        _footer_split_cb = toggle_split_editor
    _active_nav = _get_active_nav()
    _actions = _active_nav.current
    cursor_row_col = _actions.get_cursor_row_col() if _actions else (1, 1)
    footer = (
        StatusBar(
            document=_footer_doc,
            file_path=_footer_path,
            dirty=_footer_doc.dirty,
            sidebar_open=settings.get("sidebar_open", False),
            cursor_row_col=cursor_row_col,
            theme_mode=theme_mode,
            on_toggle_sidebar=toggle_sidebar,
            word_wrap=settings.get("word_wrap", True),
            on_toggle_word_wrap=toggle_word_wrap,
            split_editor=_footer_split,
            on_toggle_split_editor=_footer_split_cb,
        )
        if settings.get("show_footer", True)
        else ft.Container(height=0)
    )

    # 顶部多文档标签栏：直接传完整 tabs，TabBar 用 .get() 读取所需展示字段
    # （普通标签读 file_path/dirty，对比标签读 type/left_path/right_path/left_dirty/right_dirty）
    tab_bar = TabBar(
        tabs=tabs,
        active_index=active_index,
        theme_mode=theme_mode,
        on_select=select_tab,
        on_close=close_tab,
        on_new=new_doc,
        on_context_action=_on_tab_context_action,
        compare_source=compare_source,
    )

    main_col = ft.Column(
        controls=[
            tab_bar,
            body,
            footer,
        ],
        spacing=0,
        expand=True,
    )

    # 关闭脏标签确认弹层
    def _tab_display_name(t: dict) -> str:
        """统一标签显示名：diff 标签显示「left ⟷ right」，否则取文件名。"""
        if t.get("type") == "diff":
            left = os.path.basename(t.get("left_path")) if t.get("left_path") else "未命名"
            right = os.path.basename(t.get("right_path")) if t.get("right_path") else "未命名"
            return f"{left} ⟷ {right}"
        return file_name(t.get("file_path"))

    _pending = confirm_close
    if _pending and len(_pending) == 1 and 0 <= _pending[0] < len(tabs):
        _pending_label = _tab_display_name(tabs[_pending[0]])
        _pending_save_label = "保存并关闭"
    elif _pending and len(_pending) > 1:
        _pending_label = f"{len(_pending)} 个标签"
        _pending_save_label = "全部保存并关闭"
    else:
        _pending_label = ""
        _pending_save_label = "保存并关闭"
    confirm_dialog = ConfirmCloseDialog(
        visible=bool(_pending),
        file_name=_pending_label,
        save_label=_pending_save_label,
        theme_mode=theme_mode,
        on_save_and_close=lambda: page_ref.current.run_task(_save_and_close_pending),
        on_close_without_save=_close_without_save,
        on_cancel=_cancel_close,
    )

    # 文件操作对话框（新建文件/文件夹/重命名/删除）
    _fd = file_dialog
    if _fd is not None:
        file_dialog_view = FileActionDialog(
            visible=True,
            mode=_fd["mode"],
            title=_fd["title"],
            theme_mode=theme_mode,
            confirm_label=_fd["confirm_label"],
            on_confirm=_on_file_dialog_confirm,
            on_cancel=lambda: set_file_dialog(None),
            input_label=_fd.get("input_label", ""),
            input_value=_fd.get("input_value", ""),
            input_hint=_fd.get("input_hint", ""),
            location_hint=_fd.get("location_hint"),
            message=_fd.get("message", ""),
            danger=_fd.get("danger", False),
            icon=_fd.get("icon", ft.Icons.HELP_OUTLINE),
        )
    else:
        file_dialog_view = FileActionDialog(
            visible=False,
            mode="confirm",
            title="",
            theme_mode=theme_mode,
            confirm_label="确定",
            on_confirm=lambda value="": None,
            on_cancel=lambda: None,
        )

    # 文件对比已重构为双 MarkdownEditor 原生编辑模式（见上方 is_diff_tab 分支），
    # 以 type=="diff" 标签形式管理，旧的 DiffView 全屏 overlay 已移除。

    return ft.Stack(
        controls=[
            main_col,
            settings_view,
            confirm_dialog,
            file_dialog_view,
        ],
        expand=True,
    )


async def main(page: ft.Page):
    page.title = "Markdown 编辑器"
    page.fonts = {"Alibaba": "assets/fonts/AlibabaPuHuiTi-3-55-Regular.otf"}
    # 亮/暗两套主题，由 App 的 theme_mode state 切换
    # 背景色由 App._apply_theme 通过 page.bgcolor 单独设置，不放在 ColorScheme
    page.theme = ft.Theme(
        font_family="Alibaba",
        color_scheme=ft.ColorScheme(
            surface="#FFFFFF",
            on_surface="#1F2329",
            primary="#1677FF",
        ),
    )
    page.dark_theme = ft.Theme(
        font_family="Alibaba",
        color_scheme=ft.ColorScheme(
            surface="#161B22",
            on_surface="#E6EDF3",
            primary="#58A6FF",
        ),
    )
    page.theme_mode = ft.ThemeMode.LIGHT
    page.window.width = 1200
    page.window.height = 720
    page.window.min_width = 720
    page.window.min_height = 480
    await page.window.center()
    page.render(App)


def main_sync():
    """同步入口，供 console_scripts 调用。"""
    ft.run(main)


if __name__ == "__main__":
    ft.run(main)
