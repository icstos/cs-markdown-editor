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

import logging
import os

import flet as ft

import parser
from app._tab_helpers import tab_display_name, tab_group
from styles import get_colors
from views.activity_bar import ActivityBar
from views.diff_markers import DiffHeader
from views.diff_view import compute_diff_for_editors
from views.editor import MarkdownEditor
from views.file_dialogs import FileActionDialog
from views.floating_search import FloatingSearch
from views.git_branch_menu import GitBranchMenu
from views.git_diff import GitDiffView
from views.git_panel import GitPanel, git_panel_props
from views.global_menu import build_global_menu
from views.outline_panel import OutlinePanel
from views.recovery_dialog import RecoveryDialog
from views.settings_dialog import SettingsDialog
from views.sidebar import Sidebar
from views.status_bar import StatusBar
from views.tab_bar import ConfirmCloseDialog, TabBar

log = logging.getLogger(__name__)


def _editor_search_props(ctx, doc):
    """返回某编辑器的文档内搜索高亮 props：(hits_map, version)。

    仅当编辑器绑定文档与「搜索作用文档」为同一对象时下发命中数据（按行索引
    定位），避免把另一份文档的行号错配到本编辑器。
    """
    if doc is not None and ctx.doc_search_doc is doc:
        return ctx.doc_search_map, ctx.doc_search_map_version
    return {}, 0


def _attach_doc_search_overlay(content: ft.Control, ctx) -> ft.Control:
    """把文档内搜索浮层叠到 editor_body 上：Stack 顶层右上角，不随内容滚动。

    浮层只在自身矩形内可命中（visible=False 时零尺寸），其余区域点击/滚动
    全部穿透到编辑器。
    """
    total = ctx.doc_search_total
    if total > 0:
        cur = ctx.doc_search_active
        cur_idx = cur if 0 <= cur < total else 0
    else:
        cur_idx = -1
    overlay = FloatingSearch(
        open=ctx.doc_search_open,
        query=ctx.doc_search_query,
        set_query=ctx.set_doc_search_query,
        case_on=ctx.doc_search_case,
        on_toggle_case=ctx.set_doc_search_case,
        regex_on=ctx.doc_search_regex,
        on_toggle_regex=ctx.set_doc_search_regex,
        current_idx=cur_idx,
        total=total,
        on_prev=ctx.doc_search_prev,
        on_next=ctx.doc_search_next,
        on_close=ctx.close_doc_search,
        focus_seq=ctx.doc_search_focus_seq,
        native_input_ref=ctx.native_input_ref,
        theme_mode=ctx.theme_mode,
    )
    return ft.Stack(
        controls=[
            content,
            ft.Container(
                content=overlay,
                right=14,
                top=8,
            ),
        ],
        expand=True,
    )


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

    def _open_settings_url(url: str):
        """「关于」页外链：交给系统浏览器打开（与帮助菜单同一路径）。"""
        page = ctx.page_ref.current
        if page is None or not url:
            return
        try:
            page.launch_url(url, web_popup_window_name=ft.UrlTarget.BLANK)
        except Exception:
            log.debug("打开外链失败 url=%s", url, exc_info=True)

    def _copy_settings_text(text: str):
        """「关于」页复制（邮箱 / 版本号 / 运行环境）。"""
        page = ctx.page_ref.current
        clip = ctx.clipboard_holder.current
        if page is None or clip is None or not text:
            return
        try:
            page.run_task(clip.set, text)
        except Exception:
            log.debug("复制到剪贴板失败 text=%s", text, exc_info=True)
            return
        ctx.show_snack(f"已复制：{text}")

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
        on_open_recovery=ctx.open_recovery_panel,
        on_pick_backup_dir=lambda: ctx.page_ref.current.run_task(ctx.pick_backup_dir),
        on_open_url=_open_settings_url,
        on_copy=_copy_settings_text,
    )

    # ============ 侧边栏（第二列：管理面板）+ 功能栏（第一列）+ 大纲列（第四列）============
    sidebar_open = ctx.settings.get("sidebar_open", False)
    outline_open = ctx.settings.get("outline_open", True)
    # 面板归一化：旧版大纲面板已独立为第四列，sidebar_panel 仅文件/搜索/源代码管理有效
    _active_panel = ctx.settings.get("sidebar_panel", "files")
    if _active_panel not in ("files", "search", "git"):
        _active_panel = "files"
    # 源代码管理面板：控件树在此构造（面板需要 App 持有的仓库状态 + 与差异视图 /
    # 状态栏共享同一份数据），Sidebar 只负责放进第二列。
    _git_state, _git_actions = git_panel_props(ctx)
    git_panel_control = GitPanel(_git_state, _git_actions, ctx.theme_mode)
    _git_st = ctx.git_status
    _git_change_count = _git_st.change_count if _git_st is not None else 0
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
        active_panel=_active_panel,
        on_open_file=ctx.open_file_by_path,
        on_jump_to_line=ctx.jump_to_line,
        on_width_change=ctx.change_sidebar_width,
        on_file_context_action=ctx.on_sidebar_context_action,
        # VSCode 风格文件树拖拽：文件/文件夹移动到目标文件夹
        on_file_drop=ctx.move_fs_item,
        on_close_folder=lambda: ctx.update_setting("workspace_folder", None),
        # 搜索增强：跨文件结果点击（open + pending jump）+ 选项持久化（复用 update_setting）
        on_open_file_and_jump=ctx.open_file_and_jump,
        on_update_setting=ctx.update_setting,
        compare_source=ctx.compare_source,
        fs_version=ctx.fs_version,
        sidebar_open=sidebar_open,
        # Ctrl+F：切换搜索面板后聚焦搜索输入框（序号递增驱动 Sidebar effect）
        search_focus_seq=ctx.search_focus_seq,
        # 外部输入焦点域：搜索/替换/过滤输入框聚焦时置 token（快捷键与编辑器隔离）
        native_input_ref=ctx.native_input_ref,
        # 替换功能：当前文档内存替换 + 跨文件写盘 + 快捷键桥接 ref
        on_replace_match_in_doc=_call_replace,
        on_replace_all_in_doc=_call_replace_all,
        on_bump_fs_version=ctx.bump_fs_version,
        replace_actions_ref=ctx.sidebar_replace_ref,
        # VSCode 风格文件树：非 md 文件用系统默认程序打开
        on_open_external=ctx.open_external,
        # 源代码管理面板（控件树由本函数构造，见上方 git_panel_props）
        git_panel=git_panel_control,
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

    # 编辑器公共 props：不含 document/file_path/on_dirty_change（单编辑器与
    # 拆分两侧各自绑定——拆分下左右组激活标签不同，脏状态按组路由）
    # MarkText 风格重构：show_toolbar=False 隐藏原有顶部工具栏，功能已收纳进全局菜单栏
    _editor_common = {
        "show_toolbar": False,
        "on_new": ctx.new_doc,
        "on_open": lambda: ctx.page_ref.current.run_task(ctx.open_doc),
        "on_open_folder": lambda: ctx.page_ref.current.run_task(ctx.open_folder),
        "on_save": lambda: ctx.page_ref.current.run_task(ctx.save_doc),
        "on_export_html": lambda: ctx.page_ref.current.run_task(ctx.export_doc, "html"),
        "on_export_docx": lambda: ctx.page_ref.current.run_task(ctx.export_doc, "docx"),
        "on_export_pdf": lambda: ctx.page_ref.current.run_task(ctx.export_doc, "pdf"),
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

    # 组激活标签安全读取：拆分下两侧编辑器各自绑定所属组激活标签
    def _group_tab(g: int) -> dict:
        ts = ctx.tabs
        gi = ctx.active_index_right if g == 1 else ctx.active_index_left
        return ts[gi] if 0 <= gi < len(ts) else {}

    if ctx.is_diff_tab:
        editor_body = _build_diff_area(ctx, sidebar_open, _pane_cursor_cb, _pane_content_cb)
    elif ctx.split_editor:
        editor_body = _build_split_area(
            ctx, _editor_common, _group_tab, _pane_cursor_cb, _pane_content_cb
        )
    else:
        _ed_sp, _ed_ver = _editor_search_props(ctx, ctx.document)
        editor_body = ft.Container(
            content=MarkdownEditor(
                # key 用左组会话：与拆分时左视口同 key，切换拆分不重置左视口光标；
                # 非拆分态激活标签=左组激活（不变式），session_left 随激活变化递增
                key=f"{ctx.session_left}-0",
                nav_ref=ctx.nav_ref,
                arrow_repeat_ref=ctx.arrow_repeat_ref,
                document=ctx.document,
                file_path=ctx.file_path,
                on_dirty_change=ctx.on_dirty_change,
                on_cursor_move=ctx.push_cursor_to_status,
                on_content_change=ctx.schedule_status_count_update,
                # 光标离开编辑器 → 即时自动保存（auto_save_on_blur 开关）
                on_editor_blur=ctx.trigger_autosave_now,
                search_hits=_ed_sp,
                search_hits_version=_ed_ver,
                **_editor_common,
            ),
            expand=True,
        )

    # ============ 全局菜单（收纳进功能栏底部）+ 标签行（放入第三列）============
    # MarkText 风格：≡ 菜单按钮收纳文件/编辑/段落/格式/视图/帮助六组（含「设置」，
    # 文件→设置 / Ctrl+,），置于功能栏底部替代原设置按钮；标签行随编辑区进入
    # 第三列，宽度与文档编辑区一致。所有功能通过 ctx 装配槽 + get_active_nav 路由。
    # 拆分模式（非对比标签）：标签行同步分左右——每组一个 TabBar，只显示该组
    # 标签（VSCode「向右拆分」直觉：标签行与编辑区同步分栏）。
    global_menu = build_global_menu(ctx, ctx.theme_mode)

    def _group_tab_bar(g: int, leading) -> ft.Control:
        """构造 g 组（0=左 / 1=右）的 TabBar：过滤该组标签，索引映射回全局。

        TabBar 回调的索引相对于过滤后的组内列表，这里统一映射为全局索引后
        再路由到 select_tab / close_tab / on_tab_context_action（组语义由
        控制器按标签所属组处理：close_others/close_all 仅影响该组）。
        """
        idxs = [i for i, t in enumerate(ctx.tabs) if tab_group(t) == g]
        tabs_g = [ctx.tabs[i] for i in idxs]
        active_g = ctx.active_index_right if g == 1 else ctx.active_index_left
        try:
            pos = idxs.index(active_g)
        except ValueError:
            pos = 0

        def _sel(gi: int):
            if 0 <= gi < len(idxs):
                ctx.select_tab(idxs[gi])

        def _close(gi: int):
            if 0 <= gi < len(idxs):
                ctx.close_tab(idxs[gi])

        def _ctx_action(action: str, gi: int):
            # close_all 由 TabBar 固定传 idx=0：映射到该组任一标签即可
            #（控制器按标签所属组展开语义）；其余 action 用点击标签全局索引
            idx = idxs[gi] if 0 <= gi < len(idxs) else (idxs[0] if idxs else 0)
            ctx.on_tab_context_action(action, idx)

        def _new():
            # 点哪组的「+」就在哪组新建：先聚焦该组（set_active_pane 同步写
            # ref），new_doc 读 active_pane_ref 定向到焦点组
            if ctx.split_editor:
                ctx.set_active_pane(g)
            ctx.new_doc()

        return TabBar(
            tabs=tabs_g,
            active_index=pos,
            theme_mode=ctx.theme_mode,
            on_select=_sel,
            on_close=_close,
            on_new=_new,
            on_context_action=_ctx_action,
            compare_source=ctx.compare_source,
            leading=leading,
        )

    if ctx.split_editor and not ctx.is_diff_tab:
        # 拆分：双 TabBar 并排（中缝分隔线与编辑区对齐），宽度跟随编辑区
        _c_tb = get_colors(ctx.theme_mode)
        tab_bar = ft.Row(
            controls=[
                ft.Container(content=_group_tab_bar(0, None), expand=True),
                ft.VerticalDivider(width=1, color=_c_tb.border),
                ft.Container(content=_group_tab_bar(1, None), expand=True),
            ],
            spacing=0,
        )
    else:
        # 单栏（非拆分 / 对比标签全宽）：全部标签共用一行
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

    # ============ 功能栏（第一列）+ 大纲列（第四列）============
    # 功能栏点击：当前活动图标再点 = 一键收起第二列（VSCode 直觉）；
    # 点其他图标 = 切换面板并展开（若已收起）。
    def _activity_click(key: str):
        if key == _active_panel and sidebar_open:
            ctx.toggle_sidebar()
        elif key == "git":
            # Git 面板走专用入口：它额外承担「打开即刷新」（保存文件不会递增
            # fs_version，不刷新的话面板/角标可能停留在上一次快照）。
            ctx.git_open_panel()
        else:
            ctx.change_sidebar_panel(key)
            if not sidebar_open:
                ctx.toggle_sidebar()

    activity_bar = ActivityBar(
        active_panel=_active_panel,
        sidebar_open=sidebar_open,
        on_click_panel=_activity_click,
        menu=global_menu,
        theme_mode=ctx.theme_mode,
        git_count=_git_change_count,
    )

    # 大纲列：始终渲染（保留滚动位置），open 时内容宽 240，收起时仅剩竖条
    outline_panel = OutlinePanel(
        document=_sidebar_doc,
        theme_mode=ctx.theme_mode,
        open=outline_open,
        on_jump_to_line=ctx.jump_to_line,
    )

    # 第三列：标签行 + 编辑区（标签行宽度与编辑区一致）；
    # 编辑区顶部叠文档内搜索浮层（右上角悬浮，仅自身矩形可命中）
    editor_area = ft.Column(
        controls=[tab_bar, _attach_doc_search_overlay(editor_body, ctx)],
        spacing=0,
        expand=True,
    )

    # 第二~四列：管理面板 + 编辑区 + 大纲（状态栏仅存在于这三列下方）
    body = ft.Row(
        controls=[
            sidebar,
            editor_area,
            outline_panel,
        ],
        spacing=0,
        expand=True,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
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
    # .lnk 快捷方式打开时显示链接文件名（file_path 为目标路径）
    _footer_display = ctx.cur_tab.get("display_name")
    footer = (
        StatusBar(
            document=_footer_doc,
            file_path=_footer_path,
            display_name=_footer_display,
            dirty=_footer_doc.dirty,
            sidebar_open=ctx.settings.get("sidebar_open", False),
            theme_mode=ctx.theme_mode,
            on_toggle_sidebar=ctx.toggle_sidebar,
            word_wrap=ctx.settings.get("word_wrap", True),
            on_toggle_word_wrap=ctx.toggle_word_wrap,
            split_editor=_footer_split,
            on_toggle_split_editor=_footer_split_cb,
            # 大纲开合入口：状态栏最右侧（与左侧侧边栏切换对称）
            outline_open=outline_open,
            on_toggle_outline=ctx.toggle_outline,
            status_ref=ctx.status_ref,
            status_message=ctx.status_message,
            on_status_clear=lambda: ctx.set_status_message(None),
            # ---- Git 段：分支名（点击唤起分支面板）/ 领先落后 / 待提交计数 ----
            git_branch=_git_st.branch if _git_st is not None else None,
            git_detached=bool(_git_st.detached) if _git_st is not None else False,
            git_ahead=_git_st.ahead if _git_st is not None else 0,
            git_behind=_git_st.behind if _git_st is not None else 0,
            git_pending=_git_change_count,
            git_op=_git_st.op_label if (_git_st is not None and _git_st.in_operation) else None,
            on_click_branch=ctx.git_open_branch_dialog,
            on_click_git=ctx.git_open_panel,
        )
        if ctx.settings.get("show_footer", True)
        else ft.Container(height=0)
    )

    # 第一列功能栏独占整列高度（STRETCH 撑满到窗口底），状态栏只存在于
    # 第二~四列（管理面板 + 编辑区 + 大纲）下方：VSCode 式整高活动栏。
    main_col = ft.Column(
        controls=[
            ft.Row(
                controls=[
                    activity_bar,
                    ft.Column(
                        controls=[body, footer],
                        spacing=0,
                        expand=True,
                    ),
                ],
                spacing=0,
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
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
            # 每次弹窗 instance 递增 → key 变化 → 组件重挂载：输入框 state
            # 重新初始化（新建文件/文件夹不再保留上次输入）并重新 autofocus
            key=f"fd-{_fd.get('instance', 0)}",
            mode=_fd["mode"],
            title=_fd["title"],
            theme_mode=ctx.theme_mode,
            confirm_label=_fd["confirm_label"],
            on_confirm=ctx.on_file_dialog_confirm,
            on_cancel=ctx.on_file_dialog_cancel,
            cancel_label=_fd.get("cancel_label", "取消"),
            input_label=_fd.get("input_label", ""),
            input_value=_fd.get("input_value", ""),
            input_hint=_fd.get("input_hint", ""),
            location_hint=_fd.get("location_hint"),
            message=_fd.get("message", ""),
            danger=_fd.get("danger", False),
            icon=_fd.get("icon", ft.Icons.HELP_OUTLINE),
            native_input_ref=ctx.native_input_ref,
        )
    else:
        file_dialog_view = FileActionDialog(
            visible=False,
            key="fd-hidden",
            mode="confirm",
            title="",
            theme_mode=ctx.theme_mode,
            confirm_label="确定",
            on_confirm=lambda value="": None,
            on_cancel=lambda: None,
            native_input_ref=ctx.native_input_ref,
        )

    # 文件对比已重构为双 MarkdownEditor 原生编辑模式（见 _build_diff_area），
    # 以 type=="diff" 标签形式管理，旧的 DiffView 全屏 overlay 已移除。

    # ============ Git 内嵌差异视图 + 分支管理面板（叠在编辑区之上）============
    # 两者都是「覆盖式面板」而非标签：Git diff 的内容不属于任何可编辑文档
    # （工作区 / 暂存区 / 历史提交三个来源），做成标签需要改动标签模型、脏状态、
    # 关闭确认、自动保存等一整套语义——覆盖层把影响面限制在渲染层，符合
    # 「模块化扩展、不破坏原有编辑器核心功能」的约束。
    # 叠放顺序：main_col → git_diff → git_branch → 对话框/设置，保证破坏性操作
    # 的确认对话框永远显示在 Git 面板之上。
    _git_meta = ctx.git_diff_meta or {}
    git_diff_view = GitDiffView(
        visible=ctx.git_diff_open,
        diff=ctx.git_diff,
        meta=_git_meta,
        mode=ctx.git_diff_mode,
        theme_mode=ctx.theme_mode,
        busy=ctx.git_busy,
        on_set_mode=ctx.git_set_diff_mode,
        on_close=ctx.git_close_diff,
        on_jump=ctx.git_diff_jump,
        on_open_file=ctx.git_open_in_editor,
        # 历史提交的差异是「当时的样子」，不可暂存 / 丢弃
        on_stage=None if _git_meta.get("history") else ctx.git_diff_stage,
        on_unstage=None if _git_meta.get("history") else ctx.git_diff_unstage,
        on_discard=None if _git_meta.get("history") else ctx.git_diff_discard,
    )
    _gb_st = ctx.git_status
    git_branch_menu = GitBranchMenu(
        open_state=ctx.git_branch_menu_open,
        branches=ctx.git_branches or [],
        current=_gb_st.branch if _gb_st is not None else None,
        theme_mode=ctx.theme_mode,
        busy=ctx.git_busy,
        native_input_ref=ctx.native_input_ref,
        on_switch=ctx.git_switch_branch,
        on_create=ctx.git_create_branch,
        on_delete=ctx.git_delete_branch,
        on_merge=ctx.git_merge_branch,
        on_refresh=ctx.git_refresh_branches,
        on_close=ctx.git_close_branch_menu,
    )

    # ============ 恢复面板（启动时若存在可恢复草稿则弹出，手动入口在设置面板）============
    recovery_dialog = RecoveryDialog(
        open_state=ctx.recovery_open,
        backups=ctx.recovery_list or [],
        theme_mode=ctx.theme_mode,
        on_open=ctx.open_backup_in_new_tab,
        on_delete=ctx.delete_backup,
        on_close=lambda: ctx.set_recovery_open(False),
    )

    return ft.Stack(
        controls=[
            main_col,
            git_diff_view,
            git_branch_menu,
            settings_view,
            confirm_dialog,
            file_dialog_view,
            recovery_dialog,
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
    # 文档内搜索浮层 props：仅与搜索作用文档身份一致的编辑器获得行级高亮数据
    _lsp, _lver = _editor_search_props(ctx, _ldoc)
    _rsp, _rver = _editor_search_props(ctx, _rdoc)
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
        "show_toolbar": False,
        "on_open": lambda: ctx.page_ref.current.run_task(ctx.open_doc),
        "on_open_folder": lambda: ctx.page_ref.current.run_task(ctx.open_folder),
        "on_export_html": lambda: ctx.page_ref.current.run_task(ctx.export_doc, "html"),
        "on_export_docx": lambda: ctx.page_ref.current.run_task(ctx.export_doc, "docx"),
        "on_export_pdf": lambda: ctx.page_ref.current.run_task(ctx.export_doc, "pdf"),
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
                            on_editor_blur=ctx.trigger_autosave_now,
                            on_dirty_change=lambda d: ctx.on_diff_dirty_change(0, d),
                            on_save=lambda: ctx.page_ref.current.run_task(ctx.save_doc),
                            on_scroll_change=ctx.diff_sync.on_left_scroll,
                            on_cursor_move=pane_cursor_cb(ctx.diff_active_pane == 0),
                            on_content_change=pane_content_cb(ctx.diff_active_pane == 0),
                            search_hits=_lsp,
                            search_hits_version=_lver,
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
                            on_editor_focus=lambda: ctx.set_diff_active_pane(1),
                            on_editor_blur=ctx.trigger_autosave_now,
                            on_dirty_change=lambda d: ctx.on_diff_dirty_change(1, d),
                            on_save=lambda: ctx.page_ref.current.run_task(ctx.save_doc),
                            on_scroll_change=ctx.diff_sync.on_right_scroll,
                            keyboard_autofocus=False,
                            on_cursor_move=pane_cursor_cb(ctx.diff_active_pane == 1),
                            on_content_change=pane_content_cb(ctx.diff_active_pane == 1),
                            search_hits=_rsp,
                            search_hits_version=_rver,
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


def _build_split_area(ctx, editor_common: dict, group_tab_fn, pane_cursor_cb, pane_content_cb) -> ft.Control:
    """构造拆分编辑区：左 + 分隔线 + 右，各组绑定本组激活标签的文档。

    左右两组独立标签列表（tab.group 0/1），各自激活标签的 document/file_path
    分别绑定到对应视口——两侧可打开不同文件独立编辑。编辑器 key 用各组
    session 计数（f"{session_left}-0" / f"{session_right}-1"）：
    - 仅本组激活标签变化时本组编辑器重建，另一侧光标/滚动不重置；
    - 左视口 key 与单编辑器模式一致，切换拆分不重置左视口状态。
    on_dirty_change 按组路由（on_dirty_change_pane），脏状态精确写到
    对应组激活标签。pane_cursor_cb / pane_content_cb 按焦点视口路由
    状态栏命令式上报（active_pane 决定哪侧上报）。
    """
    left_tab = group_tab_fn(0)
    right_tab = group_tab_fn(1)
    _lsp2, _lver2 = _editor_search_props(ctx, left_tab.get("document"))
    _rsp2, _rver2 = _editor_search_props(ctx, right_tab.get("document"))
    return ft.Row(
        controls=[
            ft.Container(
                content=MarkdownEditor(
                    key=f"{ctx.session_left}-0",
                    nav_ref=ctx.nav_ref,
                    document=left_tab.get("document"),
                    file_path=left_tab.get("file_path"),
                    on_editor_focus=lambda: ctx.set_active_pane(0),
                    on_editor_blur=ctx.trigger_autosave_now,
                    on_dirty_change=lambda d: ctx.on_dirty_change_pane(0, d),
                    on_cursor_move=pane_cursor_cb(ctx.active_pane == 0),
                    on_content_change=pane_content_cb(ctx.active_pane == 0),
                    search_hits=_lsp2,
                    search_hits_version=_lver2,
                    **editor_common,
                ),
                expand=True,
                on_click=lambda e: ctx.set_active_pane(0),
            ),
            ft.VerticalDivider(width=1, color=get_colors(ctx.theme_mode).border),
            ft.Container(
                content=MarkdownEditor(
                    key=f"{ctx.session_right}-1",
                    nav_ref=ctx.nav_ref_split,
                    document=right_tab.get("document"),
                    file_path=right_tab.get("file_path"),
                    on_editor_focus=lambda: ctx.set_active_pane(1),
                    on_editor_blur=ctx.trigger_autosave_now,
                    keyboard_autofocus=False,
                    on_dirty_change=lambda d: ctx.on_dirty_change_pane(1, d),
                    on_cursor_move=pane_cursor_cb(ctx.active_pane == 1),
                    on_content_change=pane_content_cb(ctx.active_pane == 1),
                    search_hits=_rsp2,
                    search_hits_version=_rver2,
                    **editor_common,
                ),
                expand=True,
                on_click=lambda e: ctx.set_active_pane(1),
            ),
        ],
        spacing=0,
        expand=True,
    )
