"""渲染树构造（从 main.py 抽取）。

构造根 ft.Stack，包含：
- main_col（tab_bar + body + footer）
- settings_view（SettingsDialog）
- confirm_dialog（ConfirmCloseDialog）
- file_dialog_view（FileActionDialog）

body 根据 is_diff_tab / split_editor 分三种模式：
- 对比标签：双 MarkdownEditor 并排 + 行级 diff 背景着色 + 差异统计头部
- 拆分编辑器：左 + 分隔线 + 右，各占一半
- 单编辑器：单个 MarkdownEditor

跨组依赖（通过 ctx 装配槽，调用时读取）：
- 所有控制器的回调（settings/tab/file/diff/split/focus）

设计要点：
- 对比标签下 document=None，需传当前焦点侧文档以保持大纲/搜索可用。
- 侧边栏始终渲染，Sidebar 内部统一控制宽度动画 0↔width + clip_behavior
  + 拖拽调宽（dragging 时禁用动画即时跟随），实现 VSCode 式平滑开合。
  始终保持 Sidebar 挂载可保留内部状态（搜索词 / 文件过滤 / 滚动位置）。
- 状态栏贯穿侧边栏 + 编辑区全宽，对比标签时反映当前焦点对比视口。
- diff 标记 / 间隙 / 统计由 App use_memo 预计算（按左右文档行内容签名缓存），
  非内容变化的 App 重渲染（主题 / 面板 / 滚动同步）不重复 serialize+difflib；
  Document 为 @ft.observable，任一侧编辑触发 App 重渲染，diff 签名变化时即时重算。

依赖项：
- os / flet
- parser（serialize）
- styles（get_colors）
- views.*（MarkdownEditor / Sidebar / StatusBar / TabBar / SettingsDialog /
  FileActionDialog / ConfirmCloseDialog / DiffHeader）
- views.diff_view.compute_diff_for_editors
- app._tab_helpers.tab_display_name

对比头部已抽取为 views.diff_markers.DiffHeader（@ft.component），props 稳定时
跳过头部控件树重建，避免 App 重渲染（侧边栏切换 / 焦点视口切换）重建头部。
"""

import os

import flet as ft

import parser
from app._tab_helpers import tab_display_name
from styles import get_colors
from views.diff_markers import DiffHeader
from views.diff_view import compute_diff_for_editors
from views.editor import MarkdownEditor
from views.file_dialogs import FileActionDialog
from views.settings_dialog import SettingsDialog
from views.sidebar import Sidebar
from views.status_bar import StatusBar
from views.tab_bar import ConfirmCloseDialog, TabBar


def build_render(ctx) -> ft.Control:
    """构造应用根渲染树。

    返回 ft.Stack，包含 main_col / settings_view / confirm_dialog / file_dialog_view。
    """

    # ---- 替换回调路由：通过 get_active_nav 路由到焦点视口（diff/split/单编辑器统一）----
    def _call_replace(li: int, s: int, e: int, nt: str):
        nav = ctx.get_active_nav()
        if nav is not None and nav.current is not None:
            fn = getattr(nav.current, "replace_match_in_doc", None)
            if fn is not None:
                fn(li, s, e, nt)

    def _call_replace_all(reps):
        nav = ctx.get_active_nav()
        if nav is not None and nav.current is not None:
            fn = getattr(nav.current, "replace_all_in_doc", None)
            if fn is not None:
                return fn(reps)
        return 0

    # ============ 设置弹层 ============
    settings_view = SettingsDialog(
        open_state=ctx.settings_open,
        tab=ctx.settings_tab,
        settings=ctx.settings,
        theme_mode=ctx.theme_mode,
        shortcut_focus=ctx.shortcut_focus,
        shortcut_mgr=ctx.shortcut_mgr,
        on_close=ctx.close_settings,
        on_select_tab=ctx.select_settings_tab,
        on_update=ctx.update_setting,
        on_reset_all=ctx.reset_settings,
        on_reset_shortcuts=ctx.reset_shortcuts,
        on_import=lambda: ctx.page_ref.current.run_task(ctx.import_shortcuts),
        on_export=lambda: ctx.page_ref.current.run_task(ctx.export_shortcuts),
        capturing=ctx.capturing,
        on_capture_click=lambda layer, action_id: ctx.set_capturing((layer, action_id)),
        on_cancel_capture_click=lambda: ctx.set_capturing((None, None)),
    )

    # ============ 侧边栏 ============
    sidebar_open = ctx.settings.get("sidebar_open", False)
    # 侧边栏：始终渲染 Sidebar，内部统一控制宽度动画 0↔width + clip_behavior
    # + 拖拽调宽（dragging 时禁用动画即时跟随），实现 VSCode 式平滑开合。
    # 始终保持 Sidebar 挂载可保留内部状态（搜索词 / 文件过滤 / 滚动位置）。
    # 对比标签下 document=None，需传当前焦点侧文档以保持大纲/搜索可用。
    if ctx.is_diff_tab:
        _sidebar_doc = ctx.cur_tab["right_doc"] if ctx.diff_active_pane == 1 else ctx.cur_tab["left_doc"]
        _sidebar_path = ctx.cur_tab["right_path"] if ctx.diff_active_pane == 1 else ctx.cur_tab["left_path"]
    else:
        _sidebar_doc = ctx.document
        _sidebar_path = ctx.file_path
    sidebar = Sidebar(
        document=_sidebar_doc,
        file_path=_sidebar_path,
        theme_mode=ctx.theme_mode,
        settings=ctx.settings,
        active_panel=ctx.settings.get("sidebar_panel", "files"),
        on_change_panel=ctx.change_sidebar_panel,
        on_open_file=ctx.open_file_by_path,
        on_jump_to_line=ctx.jump_to_line,
        on_width_change=ctx.change_sidebar_width,
        on_file_context_action=ctx.on_sidebar_context_action,
        on_close_folder=lambda: ctx.update_setting("workspace_folder", None),
        # 搜索增强：跨文件结果点击（open + pending jump）+ 选项持久化（复用 update_setting）
        on_open_file_and_jump=ctx.open_file_and_jump,
        on_update_setting=ctx.update_setting,
        compare_source=ctx.compare_source,
        fs_version=ctx.fs_version,
        sidebar_open=sidebar_open,
        # 替换功能：当前文档内存替换 + 跨文件写盘 + 快捷键桥接 ref
        on_replace_match_in_doc=_call_replace,
        on_replace_all_in_doc=_call_replace_all,
        on_bump_fs_version=ctx.bump_fs_version,
        replace_actions_ref=ctx.sidebar_replace_ref,
        # VSCode 风格文件树：非 md 文件用系统默认程序打开
        on_open_external=ctx.open_external,
    )

    # ============ 编辑器区 ============
    # 状态栏命令式上报路由：仅焦点视口上报光标 / 内容变化，避免非焦点视口干扰。
    # push_cursor_to_status / schedule_status_count_update 均为 App use_memo 稳定
    # 实例（仅读稳定 ref），故 on_cursor_move / on_content_change prop 身份跨渲染
    # 不变 → MarkdownEditor @ft.component memo 在光标/内容 prop 上成立，App 重渲染
    # （侧边栏切换 / 主题切换）不再因回调身份变化触发编辑器全量重跑。
    # 非焦点视口传 None：编辑器 _report_cursor 中 on_cursor_move is None 提前 return。
    def _pane_cursor_cb(is_active: bool):
        return ctx.push_cursor_to_status if is_active else None

    def _pane_content_cb(is_active: bool):
        return ctx.schedule_status_count_update if is_active else None

    # 编辑器公共 props：左右两视口共享（仅 nav_ref / key / show_toolbar / on_editor_focus 不同）
    _editor_common = {
        "document": ctx.document,
        "file_path": ctx.file_path,
        "on_new": ctx.new_doc,
        "on_open": lambda: ctx.page_ref.current.run_task(ctx.open_doc),
        "on_open_folder": lambda: ctx.page_ref.current.run_task(ctx.open_folder),
        "on_save": lambda: ctx.page_ref.current.run_task(ctx.save_doc),
        "on_export": lambda: ctx.page_ref.current.run_task(ctx.export_doc),
        "on_dirty_change": ctx.on_dirty_change,
        "clipboard_ref": ctx.clipboard_holder,
        "picker_ref": ctx.picker_holder,
        "theme_mode": ctx.theme_mode,
        "on_toggle_theme": ctx.toggle_theme,
        "settings": ctx.settings,
        "on_open_settings": ctx.open_settings,
        "sidebar_open": sidebar_open,
        "on_toggle_sidebar": ctx.toggle_sidebar,
        "shortcut_mgr": ctx.shortcut_mgr,
    }

    if ctx.is_diff_tab:
        editor_area = _build_diff_area(ctx, sidebar_open, _pane_cursor_cb, _pane_content_cb)
    elif ctx.split_editor:
        editor_area = _build_split_area(ctx, _editor_common, _pane_cursor_cb, _pane_content_cb)
    else:
        editor_area = ft.Container(
            content=MarkdownEditor(
                key=f"{ctx.session}-0",  # 与拆分时左视口同 key，切换拆分不重置左视口光标
                nav_ref=ctx.nav_ref,
                on_cursor_move=ctx.push_cursor_to_status,
                on_content_change=ctx.schedule_status_count_update,
                **_editor_common,
            ),
            expand=True,
        )

    body = ft.Row(
        controls=[
            sidebar,
            editor_area,
        ],
        spacing=0,
        expand=True,
    )

    # ============ 底部状态栏 ============
    # 对比标签时反映当前焦点对比视口的文档/路径/光标；拆分时按 active_pane 选择。
    if ctx.is_diff_tab:
        _footer_doc = ctx.cur_tab["right_doc"] if ctx.diff_active_pane == 1 else ctx.cur_tab["left_doc"]
        _footer_path = ctx.cur_tab["right_path"] if ctx.diff_active_pane == 1 else ctx.cur_tab["left_path"]
        _footer_split = False
        _footer_split_cb = None  # 对比标签下禁用拆分切换，避免模式冲突
    else:
        _footer_doc = ctx.document
        _footer_path = ctx.file_path
        _footer_split = ctx.split_editor
        _footer_split_cb = ctx.toggle_split_editor
    footer = (
        StatusBar(
            document=_footer_doc,
            file_path=_footer_path,
            dirty=_footer_doc.dirty,
            sidebar_open=ctx.settings.get("sidebar_open", False),
            theme_mode=ctx.theme_mode,
            on_toggle_sidebar=ctx.toggle_sidebar,
            word_wrap=ctx.settings.get("word_wrap", True),
            on_toggle_word_wrap=ctx.toggle_word_wrap,
            split_editor=_footer_split,
            on_toggle_split_editor=_footer_split_cb,
            status_ref=ctx.status_ref,
        )
        if ctx.settings.get("show_footer", True)
        else ft.Container(height=0)
    )

    # ============ 顶部多文档标签栏 ============
    # 直接传完整 tabs，TabBar 用 .get() 读取所需展示字段
    # （普通标签读 file_path/dirty，对比标签读 type/left_path/right_path/left_dirty/right_dirty）
    tab_bar = TabBar(
        tabs=ctx.tabs,
        active_index=ctx.active_index,
        theme_mode=ctx.theme_mode,
        on_select=ctx.select_tab,
        on_close=ctx.close_tab,
        on_new=ctx.new_doc,
        on_context_action=ctx.on_tab_context_action,
        compare_source=ctx.compare_source,
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

    # ============ 关闭确认对话框 ============
    _pending = ctx.confirm_close
    if _pending and len(_pending) == 1 and 0 <= _pending[0] < len(ctx.tabs):
        _pending_label = tab_display_name(ctx.tabs[_pending[0]])
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
        theme_mode=ctx.theme_mode,
        on_save_and_close=lambda: ctx.page_ref.current.run_task(ctx.save_and_close_pending),
        on_close_without_save=ctx.close_without_save,
        on_cancel=ctx.cancel_close,
    )

    # ============ 文件操作对话框（新建文件/文件夹/重命名/删除）============
    _fd = ctx.file_dialog
    if _fd is not None:
        file_dialog_view = FileActionDialog(
            visible=True,
            mode=_fd["mode"],
            title=_fd["title"],
            theme_mode=ctx.theme_mode,
            confirm_label=_fd["confirm_label"],
            on_confirm=ctx.on_file_dialog_confirm,
            on_cancel=lambda: ctx.set_file_dialog(None),
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
            theme_mode=ctx.theme_mode,
            confirm_label="确定",
            on_confirm=lambda value="": None,
            on_cancel=lambda: None,
        )

    # 文件对比已重构为双 MarkdownEditor 原生编辑模式（见 _build_diff_area），
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


def _build_diff_area(ctx, sidebar_open: bool, pane_cursor_cb, pane_content_cb) -> ft.Control:
    """构造对比标签编辑区：双 MarkdownEditor 并排 + 行级 diff 背景着色 + 差异统计头部。

    左右各一个原生可编辑 MarkdownEditor，共享 diff_marks/diff_gaps 实现差异可视化。
    对比标签公共 props 不复用 _editor_common（其 document/file_path/on_dirty_change
    绑定当前 editor 标签），对比编辑器各自持有 diff 文档。on_dirty_change/on_save
    按侧传入，不放在共享 dict。pane_cursor_cb / pane_content_cb 按焦点视口路由
    状态栏命令式上报（diff_active_pane 决定哪侧上报）。
    """
    _ldoc = ctx.cur_tab["left_doc"]
    _rdoc = ctx.cur_tab["right_doc"]
    _lpath = ctx.cur_tab["left_path"]
    _rpath = ctx.cur_tab["right_path"]
    # diff 标记 / 间隙 / 统计由 App use_memo 预计算（按左右文档行内容签名缓存），
    # 避免每次 App 重渲染（主题/面板/滚动）重复 serialize+difflib。
    _dr = ctx.diff_result
    if _dr is not None:
        marks_left, marks_right, gaps_left, gaps_right, _added, _removed, _modified = _dr
    else:
        # 兜底：memo 未命中（首次渲染竞态 / 异常）时现场计算，保证可用
        _ltext = parser.serialize(_ldoc)
        _rtext = parser.serialize(_rdoc)
        marks_left, marks_right, gaps_left, gaps_right = compute_diff_for_editors(
            _ltext, _rtext
        )
        _added = sum(1 for v in marks_right.values() if v == "added")
        _removed = sum(1 for v in marks_left.values() if v == "removed")
        _modified = sum(1 for v in marks_right.values() if v == "modified")

    _diff_common = {
        "on_new": ctx.new_doc,
        "on_open": lambda: ctx.page_ref.current.run_task(ctx.open_doc),
        "on_open_folder": lambda: ctx.page_ref.current.run_task(ctx.open_folder),
        "on_export": lambda: ctx.page_ref.current.run_task(ctx.export_doc),
        "clipboard_ref": ctx.clipboard_holder,
        "picker_ref": ctx.picker_holder,
        "theme_mode": ctx.theme_mode,
        "on_toggle_theme": ctx.toggle_theme,
        "settings": ctx.settings,
        "on_open_settings": ctx.open_settings,
        "sidebar_open": sidebar_open,
        "on_toggle_sidebar": ctx.toggle_sidebar,
        "shortcut_mgr": ctx.shortcut_mgr,
    }

    _c = get_colors(ctx.theme_mode)
    _left_name = os.path.basename(_lpath) if _lpath else "未命名"
    _right_name = os.path.basename(_rpath) if _rpath else "未命名"

    # 对比头部：抽取为 DiffHeader（@ft.memo + @ft.component）。props（文件名 / 统计 /
    # 主题 / 关闭回调）在 diff 内容不变 / 主题不变 / 标签不切换时稳定，@ft.memo 浅比较
    # 命中即跳过头部控件树重建。on_close 用稳定化的 ctx.close_current_tab（use_memo
    # 实例，读 close_tab_ref + active_index_ref），避免 lambda 身份变化击穿 memo。
    _diff_header = DiffHeader(
        left_name=_left_name,
        right_name=_right_name,
        added=_added,
        removed=_removed,
        modified=_modified,
        theme_mode=ctx.theme_mode,
        on_close=ctx.close_current_tab,
    )

    return ft.Column(
        controls=[
            _diff_header,
            ft.Row(
                controls=[
                    ft.Container(
                        content=MarkdownEditor(
                            key=f"diff-left-{ctx.active_index}",
                            document=_ldoc,
                            file_path=_lpath,
                            nav_ref=ctx.diff_nav_left,
                            diff_marks=marks_left,
                            diff_gaps=gaps_left,
                            on_editor_focus=lambda: ctx.set_diff_active_pane(0),
                            on_dirty_change=lambda d: ctx.on_diff_dirty_change(0, d),
                            on_save=lambda: ctx.page_ref.current.run_task(ctx.save_doc),
                            on_scroll_change=ctx.diff_sync.on_left_scroll,
                            on_cursor_move=pane_cursor_cb(ctx.diff_active_pane == 0),
                            on_content_change=pane_content_cb(ctx.diff_active_pane == 0),
                            **_diff_common,
                        ),
                        expand=True,
                        on_click=lambda e: ctx.set_diff_active_pane(0),
                    ),
                    ft.VerticalDivider(width=1, color=_c.border),
                    ft.Container(
                        content=MarkdownEditor(
                            key=f"diff-right-{ctx.active_index}",
                            document=_rdoc,
                            file_path=_rpath,
                            nav_ref=ctx.diff_nav_right,
                            diff_marks=marks_right,
                            diff_gaps=gaps_right,
                            show_toolbar=False,
                            on_editor_focus=lambda: ctx.set_diff_active_pane(1),
                            on_dirty_change=lambda d: ctx.on_diff_dirty_change(1, d),
                            on_save=lambda: ctx.page_ref.current.run_task(ctx.save_doc),
                            on_scroll_change=ctx.diff_sync.on_right_scroll,
                            keyboard_autofocus=False,
                            on_cursor_move=pane_cursor_cb(ctx.diff_active_pane == 1),
                            on_content_change=pane_content_cb(ctx.diff_active_pane == 1),
                            **_diff_common,
                        ),
                        expand=True,
                        on_click=lambda e: ctx.set_diff_active_pane(1),
                    ),
                ],
                spacing=0,
                expand=True,
            ),
        ],
        spacing=0,
        expand=True,
    )


def _build_split_area(ctx, editor_common: dict, pane_cursor_cb, pane_content_cb) -> ft.Control:
    """构造拆分编辑区：左 + 分隔线 + 右，各占一半；右侧隐藏工具栏保持简洁。

    两视口共享同一 document（@ft.observable），各自独立光标/滚动。
    pane_cursor_cb / pane_content_cb 按焦点视口路由状态栏命令式上报
    （active_pane 决定哪侧上报）。
    """
    return ft.Row(
        controls=[
            ft.Container(
                content=MarkdownEditor(
                    key=f"{ctx.session}-0",
                    nav_ref=ctx.nav_ref,
                    on_editor_focus=lambda: ctx.set_active_pane(0),
                    on_cursor_move=pane_cursor_cb(ctx.active_pane == 0),
                    on_content_change=pane_content_cb(ctx.active_pane == 0),
                    **editor_common,
                ),
                expand=True,
                on_click=lambda e: ctx.set_active_pane(0),
            ),
            ft.VerticalDivider(width=1, color=get_colors(ctx.theme_mode).border),
            ft.Container(
                content=MarkdownEditor(
                    key=f"{ctx.session}-1",
                    nav_ref=ctx.nav_ref_split,
                    show_toolbar=False,
                    on_editor_focus=lambda: ctx.set_active_pane(1),
                    keyboard_autofocus=False,
                    on_cursor_move=pane_cursor_cb(ctx.active_pane == 1),
                    on_content_change=pane_content_cb(ctx.active_pane == 1),
                    **editor_common,
                ),
                expand=True,
                on_click=lambda e: ctx.set_active_pane(1),
            ),
        ],
        spacing=0,
        expand=True,
    )
