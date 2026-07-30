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
- 侧边栏始终渲染，外层 Container 宽度动画 0↔sidebar_width，
  clip_behavior=HARD_EDGE 在收拢时裁剪内容，实现 VSCode 式平滑开合。
  始终保持 Sidebar 挂载可保留内部状态（搜索词 / 文件过滤 / 滚动位置）。
- 状态栏贯穿侧边栏 + 编辑区全宽，对比标签时反映当前焦点对比视口。
- diff 在每次渲染时由 serialize(left_doc)/serialize(right_doc) 重算——Document
  为 @ft.observable，任一侧编辑触发 App 重渲染，diff 标记/间隙即时更新。

依赖项：
- os / flet
- parser（serialize）
- styles（FONT_MAIN / FONT_MONO / Radius / Spacing / get_colors / only_border）
- views.*（MarkdownEditor / Sidebar / StatusBar / TabBar / SettingsDialog /
  FileActionDialog / ConfirmCloseDialog）
- views.diff_view.compute_diff_for_editors
- app._tab_helpers.tab_display_name
"""

import os

import flet as ft

import parser
from app._tab_helpers import tab_display_name
from styles import FONT_MAIN, FONT_MONO, Radius, Spacing, get_colors, only_border
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
    # 侧边栏：始终渲染 Sidebar，外层 Container 宽度动画 0↔sidebar_width，
    # clip_behavior=HARD_EDGE 在收拢时裁剪内容，实现 VSCode 式平滑开合。
    # 始终保持 Sidebar 挂载可保留内部状态（搜索词 / 文件过滤 / 滚动位置）。
    # 对比标签下 document=None，需传当前焦点侧文档以保持大纲/搜索可用。
    if ctx.is_diff_tab:
        _sidebar_doc = ctx.cur_tab["right_doc"] if ctx.diff_active_pane == 1 else ctx.cur_tab["left_doc"]
        _sidebar_path = ctx.cur_tab["right_path"] if ctx.diff_active_pane == 1 else ctx.cur_tab["left_path"]
    else:
        _sidebar_doc = ctx.document
        _sidebar_path = ctx.file_path
    sidebar_width = ctx.settings.get("sidebar_width", 256)
    sidebar_container = ft.Container(
        width=sidebar_width if sidebar_open else 0,
        animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        content=Sidebar(
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
            compare_source=ctx.compare_source,
        ),
    )

    # ============ 编辑器区 ============
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
        "theme_mode": ctx.theme_mode,
        "on_toggle_theme": ctx.toggle_theme,
        "settings": ctx.settings,
        "on_open_settings": ctx.open_settings,
        "sidebar_open": sidebar_open,
        "on_toggle_sidebar": ctx.toggle_sidebar,
        "shortcut_mgr": ctx.shortcut_mgr,
    }

    if ctx.is_diff_tab:
        editor_area = _build_diff_area(ctx, sidebar_open)
    elif ctx.split_editor:
        editor_area = _build_split_area(ctx, _editor_common)
    else:
        editor_area = ft.Container(
            content=MarkdownEditor(
                key=f"{ctx.session}-0",  # 与拆分时左视口同 key，切换拆分不重置左视口光标
                nav_ref=ctx.nav_ref,
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
    _active_nav = ctx.get_active_nav()
    _actions = _active_nav.current
    cursor_row_col = _actions.get_cursor_row_col() if _actions else (1, 1)
    footer = (
        StatusBar(
            document=_footer_doc,
            file_path=_footer_path,
            dirty=_footer_doc.dirty,
            sidebar_open=ctx.settings.get("sidebar_open", False),
            cursor_row_col=cursor_row_col,
            theme_mode=ctx.theme_mode,
            on_toggle_sidebar=ctx.toggle_sidebar,
            word_wrap=ctx.settings.get("word_wrap", True),
            on_toggle_word_wrap=ctx.toggle_word_wrap,
            split_editor=_footer_split,
            on_toggle_split_editor=_footer_split_cb,
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


def _build_diff_area(ctx, sidebar_open: bool) -> ft.Control:
    """构造对比标签编辑区：双 MarkdownEditor 并排 + 行级 diff 背景着色 + 差异统计头部。

    左右各一个原生可编辑 MarkdownEditor，共享 diff_marks/diff_gaps 实现差异可视化。
    对比标签公共 props 不复用 _editor_common（其 document/file_path/on_dirty_change
    绑定当前 editor 标签），对比编辑器各自持有 diff 文档。on_dirty_change/on_save
    按侧传入，不放在共享 dict。
    """
    _ldoc = ctx.cur_tab["left_doc"]
    _rdoc = ctx.cur_tab["right_doc"]
    _lpath = ctx.cur_tab["left_path"]
    _rpath = ctx.cur_tab["right_path"]
    _ltext = parser.serialize(_ldoc)
    _rtext = parser.serialize(_rdoc)
    marks_left, marks_right, gaps_left, gaps_right = compute_diff_for_editors(
        _ltext, _rtext
    )
    # 差异统计：added 仅右侧、removed 仅左侧、modified 两侧各一行
    _added = sum(1 for v in marks_right.values() if v == "added")
    _removed = sum(1 for v in marks_left.values() if v == "removed")
    _modified = sum(1 for v in marks_right.values() if v == "modified")

    _diff_common = {
        "on_new": ctx.new_doc,
        "on_open": lambda: ctx.page_ref.current.run_task(ctx.open_doc),
        "on_open_folder": lambda: ctx.page_ref.current.run_task(ctx.open_folder),
        "on_export": lambda: ctx.page_ref.current.run_task(ctx.export_doc),
        "clipboard_ref": ctx.clipboard_holder,
        "theme_mode": ctx.theme_mode,
        "on_toggle_theme": ctx.toggle_theme,
        "settings": ctx.settings,
        "on_open_settings": ctx.open_settings,
        "sidebar_open": sidebar_open,
        "on_toggle_sidebar": ctx.toggle_sidebar,
        "shortcut_mgr": ctx.shortcut_mgr,
    }

    _c = get_colors(ctx.theme_mode)
    _is_dark = ctx.theme_mode == ft.ThemeMode.DARK
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
                    on_click=lambda e: ctx.close_tab(ctx.active_index),
                    icon_size=18, style=ft.ButtonStyle(color=_c.muted),
                ),
            ],
            spacing=Spacing.SM,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
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


def _build_split_area(ctx, editor_common: dict) -> ft.Control:
    """构造拆分编辑区：左 + 分隔线 + 右，各占一半；右侧隐藏工具栏保持简洁。

    两视口共享同一 document（@ft.observable），各自独立光标/滚动。
    """
    return ft.Row(
        controls=[
            ft.Container(
                content=MarkdownEditor(
                    key=f"{ctx.session}-0",
                    nav_ref=ctx.nav_ref,
                    on_editor_focus=lambda: ctx.set_active_pane(0),
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
                    **editor_common,
                ),
                expand=True,
                on_click=lambda e: ctx.set_active_pane(1),
            ),
        ],
        spacing=0,
        expand=True,
    )
