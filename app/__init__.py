"""应用根组件 App（从 main.py 抽取）。

包结构（AppContext + 控制器模式，同 views/editor）：
- __init__.py（本文件）：App 组件入口
  hooks → state/ref 镜像 → 派生值 → 渲染期同步 → shortcut_mgr → ctx 构造
  → 控制器装配 → 装配槽填充 → ref 同步 → use_effect → build_render
- _context.py：AppContext dataclass（双区：稳定区 + 快照区 + 装配槽）
- _tab_management.py / _file_io_ops.py / _file_dialogs.py / _diff_controller.py /
  _settings_controller.py / _split_editor.py / _focus_router.py /
  _keyboard.py：控制器模块（build_xxx(ctx) -> dict）
- _render.py：渲染树构造（build_render(ctx) -> ft.Stack）
- _tab_helpers.py / autosave.py / diff_scroll_sync.py：已抽取的纯函数/状态机

状态分层：
- tabs：多文档标签列表（每个 tab 持有 {document, file_path, dirty} 或 diff 字段）
- active_index / session：当前标签索引 / 切换计数器（强制编辑器重置内部状态）
- theme_mode / settings：主题模式 / 设置字典
- split_editor / active_pane：拆分编辑器开关 / 焦点视口
- diff_active_pane：对比标签焦点视口

硬约束（来自重构架构说明书）：
- 所有 use_* hook 必须在组件函数体顶层顺序调用（Flet 0.86 约束）
- 控制器内禁止任何 use_* hook
- 跨控制器调用通过 ctx 装配槽（带 default=lambda）
- tabs_ref / active_index_ref / diff_active_pane_ref / active_pane_ref /
  is_diff_tab_ref 渲染期同步写入，供异步回调读取最新值
- 渲染期同步 page.theme_mode/bgcolor（保证子组件取色正确），apply_theme
  在 use_effect 中再次推送确保原生 chrome 同步
- shortcut_mgr ↔ update_setting 前向引用循环用 update_setting_ref 打破：
  shortcut_mgr 构造时 lambda 捕获 update_setting_ref，控制器装配后
  update_setting_ref.current = settings_cbs["update_setting"]
"""

import flet as ft

import parser
from app._context import AppContext
from app._diff_controller import build_diff_controller
from app._file_dialogs import build_file_dialogs
from app._file_io_ops import build_file_io_ops
from app._focus_router import build_focus_router
from app._keyboard import build_keyboard
from app._render import build_render
from app._settings_controller import build_settings_controller
from app._split_editor import build_split_editor
from app._tab_management import build_tab_management
from app.diff_scroll_sync import DiffScrollSync
from config.sample import SAMPLE_MD
from config.settings import load_settings
from services.shortcuts import ShortcutManager
from styles import get_colors


@ft.component
def App():
    # ============ State（快照区）============
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
    # 状态机封装在 app.diff_scroll_sync.DiffScrollSync（page_ref 定义后通过 use_memo 创建）。
    # 亮/暗主题模式
    theme_mode, set_theme_mode = ft.use_state(ft.ThemeMode.LIGHT)
    settings, set_settings = ft.use_state(load_settings)
    settings_open, set_settings_open = ft.use_state(False)
    settings_tab, set_settings_tab = ft.use_state("edit")
    shortcut_focus, set_shortcut_focus = ft.use_state((None, None))
    # 快捷键捕获模式：(layer, action_id) | (None, None)。设置页点"修改"时置为
    # (layer, action_id)，KeyDispatcher 顶部拦截下一个组合键经 on_capture 写入配置。
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
    # diff 滚动同步状态机（封装 4-ref + 60ms 异步追赶逻辑，详见 app.diff_scroll_sync）
    diff_sync = ft.use_memo(
        lambda: DiffScrollSync(page_ref, diff_nav_left, diff_nav_right), []
    )
    # tabs / active_index 的 ref 镜像：异步回调（autosave 延时保存）读取最新值，
    # 避免闭包捕获渲染期快照导致保存到错误标签
    tabs_ref = ft.use_ref(tabs)
    tabs_ref.current = tabs
    active_index_ref = ft.use_ref(active_index)
    active_index_ref.current = active_index
    # update_setting_ref：打破 shortcut_mgr ↔ update_setting 前向引用循环。
    # shortcut_mgr 构造时 lambda 捕获此 ref，控制器装配后写入实际 update_setting。
    update_setting_ref = ft.use_ref(None)
    # 粘贴前的 draft 快照（供 handle_paste 做 diff 定位粘贴位置）
    paste_old_draft = ft.use_ref("")
    # dispatcher_ref：渲染期同步赋值最新 dispatcher，_handler 通过 ref 读取。
    # 修复 use_effect(_bind_keyboard, []) 空依赖导致 _handler 闭包捕获首次渲染
    # dispatcher 的过期问题——改快捷键后新键位才能立即生效（无需重启）。
    dispatcher_ref = ft.use_ref(None)

    # ============ 派生值 ============
    # 当前激活标签的派生值（供下游闭包与渲染使用）
    _safe_idx = active_index if 0 <= active_index < len(tabs) else 0
    cur_tab = tabs[_safe_idx] if tabs else {"type": "editor", "document": parser.parse_markdown(""), "file_path": None, "dirty": False}
    is_diff_tab = cur_tab.get("type") == "diff"
    is_diff_tab_ref.current = is_diff_tab
    # editor 标签派生值（diff 标签无这些字段，对应分支不访问）
    document = cur_tab.get("document") if not is_diff_tab else None
    file_path = cur_tab.get("file_path") if not is_diff_tab else None

    # ============ 渲染期同步 page.theme_mode/bgcolor ============
    # 同步设置 page.theme_mode：use_effect 在渲染之后执行，本次渲染期间
    # 子组件（MarkdownEditor→LineView 等）调用 _current_colors() 读到的
    # 还是旧 page.theme_mode，导致切换主题后内容颜色不实时刷新。
    # 在渲染期间同步写入，保证子组件取色正确。
    _page_now = ft.context.page
    if _page_now is not None:
        _page_now.theme_mode = theme_mode
        _page_now.bgcolor = get_colors(theme_mode).bg

    # ============ shortcut_mgr（use_memo 缓存 + 前向引用 update_setting_ref）============
    # ShortcutManager：无状态读取器。原每次渲染重建，现用 use_memo([id(settings)])
    # 缓存——settings 不变时（文档编辑/光标移动等高频场景）复用实例，避免重复解析
    # shortcuts dict。update_setting 通过 lambda 前向引用 update_setting_ref
    # （控制器装配后写入），打破循环依赖。
    shortcut_mgr = ft.use_memo(
        lambda: ShortcutManager(
            settings, lambda key, value: update_setting_ref.current(key, value)
        ),
        [id(settings)],
    )

    # ============ 构造 AppContext ============
    ctx = AppContext(
        # State 值
        tabs=tabs,
        active_index=active_index,
        session=session,
        confirm_close=confirm_close,
        file_dialog=file_dialog,
        compare_source=compare_source,
        diff_active_pane=diff_active_pane,
        theme_mode=theme_mode,
        settings=settings,
        settings_open=settings_open,
        settings_tab=settings_tab,
        shortcut_focus=shortcut_focus,
        capturing=capturing,
        split_editor=split_editor,
        active_pane=active_pane,
        # 派生值
        cur_tab=cur_tab,
        is_diff_tab=is_diff_tab,
        document=document,
        file_path=file_path,
        shortcut_mgr=shortcut_mgr,
        diff_sync=diff_sync,
        # Setters
        set_tabs=set_tabs,
        set_active_index=set_active_index,
        set_session=set_session,
        set_confirm_close=set_confirm_close,
        set_file_dialog=set_file_dialog,
        set_compare_source=set_compare_source,
        set_diff_active_pane=set_diff_active_pane,
        set_theme_mode=set_theme_mode,
        set_settings=set_settings,
        set_settings_open=set_settings_open,
        set_settings_tab=set_settings_tab,
        set_shortcut_focus=set_shortcut_focus,
        set_capturing=set_capturing,
        set_split_editor=set_split_editor,
        set_active_pane=set_active_pane,
        # Refs
        is_diff_tab_ref=is_diff_tab_ref,
        diff_nav_left=diff_nav_left,
        diff_nav_right=diff_nav_right,
        diff_active_pane_ref=diff_active_pane_ref,
        nav_ref=nav_ref,
        nav_ref_split=nav_ref_split,
        active_pane_ref=active_pane_ref,
        picker_holder=picker_holder,
        clipboard_holder=clipboard_holder,
        page_ref=page_ref,
        tabs_ref=tabs_ref,
        active_index_ref=active_index_ref,
        dispatcher_ref=dispatcher_ref,
        paste_old_draft=paste_old_draft,
    )

    # ============ 控制器装配（拓扑序）============
    # 装配顺序：tab_management → file_io_ops → file_dialogs → diff_controller
    # → settings_controller → split_editor → focus_router → keyboard
    # 跨控制器调用通过 ctx 装配槽延迟求值，但 KeyDispatcher 构造时
    # app_callbacks 立即求值 ctx.save_doc 等，故 file_io_ops 必须先于 keyboard。
    tab_cbs = build_tab_management(ctx)
    ctx.cur_tab_fn = tab_cbs["cur_tab"]
    ctx.update_active = tab_cbs["update_active"]
    ctx.update_tab = tab_cbs["update_tab"]
    ctx.select_tab = tab_cbs["select_tab"]
    ctx.cycle_tab = tab_cbs["cycle_tab"]
    ctx.do_close_many = tab_cbs["do_close_many"]
    ctx.request_close = tab_cbs["request_close"]
    ctx.close_tab = tab_cbs["close_tab"]
    ctx.save_and_close_pending = tab_cbs["save_and_close_pending"]
    ctx.close_without_save = tab_cbs["close_without_save"]
    ctx.cancel_close = tab_cbs["cancel_close"]

    file_cbs = build_file_io_ops(ctx)
    ctx.push_recent_file = file_cbs["push_recent_file"]
    ctx.open_file_by_path = file_cbs["open_file_by_path"]
    ctx.new_doc = file_cbs["new_doc"]
    ctx.open_doc = file_cbs["open_doc"]
    ctx.open_folder = file_cbs["open_folder"]
    ctx.save_doc = file_cbs["save_doc"]
    ctx.export_doc = file_cbs["export_doc"]

    dialog_cbs = build_file_dialogs(ctx)
    ctx.show_snack = dialog_cbs["show_snack"]
    ctx.copy_path = dialog_cbs["copy_path"]
    ctx.on_file_dialog_confirm = dialog_cbs["on_file_dialog_confirm"]
    ctx.open_input_dialog = dialog_cbs["open_input_dialog"]
    ctx.open_delete_dialog = dialog_cbs["open_delete_dialog"]
    ctx.update_tab_for_renamed_file = dialog_cbs["update_tab_for_renamed_file"]
    ctx.close_tabs_for_path = dialog_cbs["close_tabs_for_path"]
    ctx.on_tab_context_action = dialog_cbs["on_tab_context_action"]
    ctx.on_sidebar_context_action = dialog_cbs["on_sidebar_context_action"]

    diff_cbs = build_diff_controller(ctx)
    ctx.get_text_for_compare = diff_cbs["get_text_for_compare"]
    ctx.select_for_compare = diff_cbs["select_for_compare"]
    ctx.compare_with_selected = diff_cbs["compare_with_selected"]
    ctx.on_diff_dirty_change = diff_cbs["on_diff_dirty_change"]

    settings_cbs = build_settings_controller(ctx)
    ctx.apply_theme = settings_cbs["apply_theme"]
    ctx.mount_picker = settings_cbs["mount_picker"]
    ctx.toggle_theme = settings_cbs["toggle_theme"]
    ctx.open_settings = settings_cbs["open_settings"]
    ctx.close_settings = settings_cbs["close_settings"]
    ctx.select_settings_tab = settings_cbs["select_settings_tab"]
    ctx.update_setting = settings_cbs["update_setting"]
    ctx.on_capture = settings_cbs["on_capture"]
    ctx.on_cancel_capture = settings_cbs["on_cancel_capture"]
    ctx.schedule_autosave = settings_cbs["schedule_autosave"]
    ctx.reset_settings = settings_cbs["reset_settings"]
    ctx.reset_shortcuts = settings_cbs["reset_shortcuts"]
    ctx.export_shortcuts = settings_cbs["export_shortcuts"]
    ctx.import_shortcuts = settings_cbs["import_shortcuts"]
    ctx.toggle_sidebar = settings_cbs["toggle_sidebar"]
    ctx.toggle_word_wrap = settings_cbs["toggle_word_wrap"]
    ctx.change_sidebar_panel = settings_cbs["change_sidebar_panel"]
    ctx.change_sidebar_width = settings_cbs["change_sidebar_width"]

    split_cbs = build_split_editor(ctx)
    ctx.toggle_split_editor = split_cbs["toggle_split_editor"]
    ctx.set_active_pane = split_cbs["set_active_pane"]
    ctx.set_diff_active_pane = split_cbs["set_diff_active_pane"]

    focus_cbs = build_focus_router(ctx)
    ctx.get_active_nav = focus_cbs["get_active_nav"]
    ctx.apply_content_layout = focus_cbs["apply_content_layout"]
    ctx.jump_to_line = focus_cbs["jump_to_line"]
    ctx.on_dirty_change = focus_cbs["on_dirty_change"]

    keyboard_cbs = build_keyboard(ctx)
    # 打破前向引用循环：update_setting 装配后写入 ref，shortcut_mgr 的 lambda 即可调用
    update_setting_ref.current = settings_cbs["update_setting"]
    # 渲染期同步：每次重渲染把最新 dispatcher 写入 ref，_handler 即可读到最新值
    dispatcher_ref.current = keyboard_cbs["dispatcher"]

    # ============ use_effect（hooks 顺序约束：函数体顶层调用）============
    ft.use_effect(settings_cbs["mount_picker"], [])
    ft.use_effect(settings_cbs["apply_theme"], [theme_mode])
    ft.use_effect(keyboard_cbs["bind_keyboard"], [])

    # ============ 渲染树 ============
    return build_render(ctx)
