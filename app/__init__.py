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

import asyncio
import contextlib

import flet as ft

import parser
from app._backup_controller import build_backup_controller
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
from services import file_ops
from services.shortcuts import ShortcutManager
from styles import get_colors
from views.diff_view import compute_diff_for_editors
from views.doc_search import compute_doc_matches
from views.status_bar import _compute_counts


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
    # 拆分编辑组（VSCode 风格）：每侧组各自的激活标签全局索引与会话计数。
    # 不变式：active_index == 焦点侧组的激活索引（activate_index 统一维护）。
    # session_left/right 仅该组激活标签变化时递增 → 另一侧编辑器 key 不变，
    # 光标/滚动不重置；编辑器 key 统一用组会话（f"{session_left}-0" 等），
    # 切换拆分模式时左编辑器 key 不变、不重建。
    active_index_left, set_active_index_left = ft.use_state(0)
    active_index_right, set_active_index_right = ft.use_state(0)
    session_left, set_session_left = ft.use_state(0)
    session_right, set_session_right = ft.use_state(0)
    # 跨文件"打开后跳转"：open_file_by_path(path, jump_to=(li, off)) 暂存跳转任务，
    # session 变化（重建 MarkdownEditor）或 sig 变化（文件已是当前 tab，session 不变）
    # 时 _fire_pending_jump effect 消费并调用 jump_to_line(li, off)。
    # 双触发器设计：session 处理"切换/打开新 tab"，sig 处理"同 tab 重复点击搜索结果"。
    pending_jump_ref = ft.use_ref(None)  # (li, off) | None
    pending_jump_sig, set_pending_jump_sig = ft.use_state(0)
    confirm_close, set_confirm_close = ft.use_state(None)  # 待确认关闭的 tab index | None
    # Ctrl+F 聚焦搜索框：序号递增驱动 Sidebar 的 use_effect 聚焦搜索输入框。
    # ref 记录最新值供稳定闭包读取；use_state 触发 Sidebar 渲染侧 effect。
    search_focus_seq_ref = ft.use_ref(0)
    search_focus_seq, set_search_focus_seq = ft.use_state(0)
    # ============ 文档内搜索浮层状态（Ctrl+F；独立于侧边栏搜索面板）============
    # open/query/case/regex/active 全部提升到 App：查询结果要驱动编辑器行级
    # 高亮装饰与匹配导航。active = 当前匹配在扁平匹配列表中的索引（-1=无）。
    doc_search_open, set_doc_search_open = ft.use_state(False)
    doc_search_open_ref = ft.use_ref(False)
    doc_search_open_ref.current = doc_search_open
    doc_search_query, set_doc_search_query = ft.use_state("")
    doc_search_case, set_doc_search_case = ft.use_state(False)
    doc_search_regex, set_doc_search_regex = ft.use_state(False)
    doc_search_active, set_doc_search_active = ft.use_state(-1)
    doc_search_active_ref = ft.use_ref(-1)
    doc_search_active_ref.current = doc_search_active
    # Ctrl+F 每次唤起递增 focus_seq：FloatingSearch use_effect 聚焦其输入框
    doc_search_focus_seq_ref = ft.use_ref(0)
    doc_search_focus_seq, set_doc_search_focus_seq = ft.use_state(0)
    # 匹配列表 ref 镜像（稳定闭包读取最新，避免捕获过期渲染快照）
    doc_search_matches_ref = ft.use_ref([])
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
    # 组激活索引 / 组会话计数 ref 镜像：控制器（activate_index / do_close_many）
    # 与异步回调读取最新值，避免闭包捕获渲染期快照（同 tabs_ref 模式）。
    active_index_left_ref = ft.use_ref(active_index_left)
    active_index_left_ref.current = active_index_left
    active_index_right_ref = ft.use_ref(active_index_right)
    active_index_right_ref.current = active_index_right
    session_left_ref = ft.use_ref(session_left)
    session_left_ref.current = session_left
    session_right_ref = ft.use_ref(session_right)
    session_right_ref.current = session_right
    # update_setting_ref：打破 shortcut_mgr ↔ update_setting 前向引用循环。
    # shortcut_mgr 构造时 lambda 捕获此 ref，控制器装配后写入实际 update_setting。
    update_setting_ref = ft.use_ref(None)
    # 粘贴前的 draft 快照（供 handle_paste 做 diff 定位粘贴位置）
    paste_old_draft = ft.use_ref("")
    # 上/下键自驱动重复标志（KeyDispatcher 启动/停止，editor _on_key_up 停止）
    arrow_repeat_ref = ft.use_ref(None)
    # dispatcher_ref：渲染期同步赋值最新 dispatcher，_handler 通过 ref 读取。
    # 修复 use_effect(_bind_keyboard, []) 空依赖导致 _handler 闭包捕获首次渲染
    # dispatcher 的过期问题——改快捷键后新键位才能立即生效（无需重启）。
    dispatcher_ref = ft.use_ref(None)
    # 非编辑器原生输入框焦点域 ref：各输入框 on_focus/on_blur 写入 token
    # （views.native_scope.native_focus_hooks）。KeyDispatcher 据此把「焦点在
    # 外部输入框时的按键」与编辑器隔离（如搜索框内 Ctrl+A 只全选搜索框文本）。
    native_input_ref = ft.use_ref(None)
    # close_tab_ref：渲染期同步赋值最新 close_tab（控制器装配产物，每次渲染新建但
    # 行为一致——仅读稳定 ref/setter）。供稳定化的 close_current_tab 读取最新实例，
    # 打破「lambda 捕获 ctx.close_tab 导致 DiffHeader on_close 身份不稳定」问题。
    close_tab_ref = ft.use_ref(None)
    # 状态栏命令式更新器注册点：StatusBar 在渲染期写入 update_cursor/update_counts
    status_ref = ft.use_ref(None)
    # 字数防抖：mark_dirty 触发 on_content_change → 300ms 防抖后重算计数并命令式推送。
    # count_token_ref 递增 token 做防抖 + 跨标签竞态防护：过期任务醒来校验失败即丢弃。
    count_token_ref = ft.use_ref(0)
    # 文件系统版本号：文件增删改后递增，驱动侧边栏文件树异步重扫。
    # fs_version_ref 持最新值供 bump 闭包读取（避免闭包捕获渲染期快照）。
    fs_version, set_fs_version = ft.use_state(0)
    fs_version_ref = ft.use_ref(0)
    fs_version_ref.current = fs_version
    # 搜索/替换快捷键桥接 ref：Sidebar 渲染期写入 {replace_current, replace_all}，
    # App 稳定闭包通过此 ref 调用 Sidebar 最新回调（类似 nav_ref 模式）。
    sidebar_replace_ref = ft.use_ref({})
    # settings_ref：供 use_memo([]) 稳定闭包读取最新设置（避免闭包捕获渲染期快照）
    settings_ref = ft.use_ref(settings)
    settings_ref.current = settings

    # ============ 自动备份 / 崩溃恢复 / 状态栏消息 状态 ============
    # status_message: (msg, kind) | None —— 状态栏轻量消息（"已自动保存" 等），
    # 3 秒后由 StatusBar 内部计时器调 on_status_clear 清空。
    status_message, set_status_message_state = ft.use_state(None)
    # recovery_open / recovery_list: 恢复面板可见性与可恢复草稿列表。
    # 启动时若 scan_recoverable 返回非空则自动弹出。
    recovery_open, set_recovery_open = ft.use_state(False)
    recovery_list, set_recovery_list = ft.use_state(None)
    # backup_started_ref: 防止 use_effect 严格模式下重复启动备份循环
    backup_started_ref = ft.use_ref(False)

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

    # ============ diff 计算 memoize ============
    # 对比标签下 serialize(left/right)+difflib 较耗时，非内容变化的 App 重渲染
    # （主题切换 / 侧边栏面板切换 / 宽度拖拽 / 滚动同步）不应重复计算。
    # 签名含左右文档 id + 行 raw 元组：内容不变则复用缓存结果。
    # 非对比标签签名为 ()，工厂早退返回 None，零开销。
    if is_diff_tab:
        _ld = cur_tab.get("left_doc")
        _rd = cur_tab.get("right_doc")
        _diff_sig = (
            id(_ld), id(_rd),
            tuple(ln.raw for ln in _ld.lines) if _ld is not None else (),
            tuple(ln.raw for ln in _rd.lines) if _rd is not None else (),
        )
    else:
        _diff_sig = ()

    def _compute_diff_result():
        if not is_diff_tab:
            return None
        _ld = cur_tab.get("left_doc")
        _rd = cur_tab.get("right_doc")
        if _ld is None or _rd is None:
            return None
        _ltext = parser.serialize(_ld)
        _rtext = parser.serialize(_rd)
        marks_l, marks_r, gaps_l, gaps_r = compute_diff_for_editors(_ltext, _rtext)
        _added = sum(1 for v in marks_r.values() if v == "added")
        _removed = sum(1 for v in marks_l.values() if v == "removed")
        _modified = sum(1 for v in marks_r.values() if v == "modified")
        return (marks_l, marks_r, gaps_l, gaps_r, _added, _removed, _modified)

    _diff_result = ft.use_memo(_compute_diff_result, [_diff_sig])

    # ============ 文档内搜索（浮层）派生 ============
    # 作用文档 = 当前焦点编辑视口的文档（diff 按焦点侧、拆分按焦点组、否则激活标签）
    if is_diff_tab:
        _search_doc = cur_tab.get("right_doc") if diff_active_pane == 1 else cur_tab.get("left_doc")
    elif split_editor and active_pane == 1:
        _gi = active_index_right if 0 <= active_index_right < len(tabs) else active_index_left
        _search_doc = tabs[_gi].get("document") if tabs else None
    else:
        _search_doc = document
    _search_sig = (
        tuple(ln.raw for ln in _search_doc.lines) if _search_doc is not None else ()
    )
    doc_search_matches = ft.use_memo(
        lambda: (
            compute_doc_matches(
                _search_doc, doc_search_query, doc_search_case, doc_search_regex
            )
            if doc_search_open and _search_doc is not None
            else []
        ),
        [doc_search_query, doc_search_case, doc_search_regex,
         doc_search_open, id(_search_doc), _search_sig],
    )
    doc_search_matches_ref.current = doc_search_matches
    _ds_total = len(doc_search_matches)
    _ds_active = doc_search_active if 0 <= doc_search_active < _ds_total else -1

    # {li: [(s, e, is_current)]}：行级高亮装饰输入（identity 稳定化，避免无关
    # 渲染击穿 LineView memo；数据不变时列表对象复用）
    def _build_ds_map():
        if not doc_search_matches or _search_doc is None:
            return {}
        out: dict[int, list[tuple[int, int, bool]]] = {}
        for i, (li, s, e) in enumerate(doc_search_matches):
            out.setdefault(li, []).append((s, e, i == _ds_active))
        return out

    doc_search_map = ft.use_memo(_build_ds_map, [doc_search_matches, _ds_active])
    # 版本号：查询结果数 + 当前匹配索引复合（数据变化时必变，供行级 memo 兜底）
    doc_search_map_version = _ds_total * 100003 + (_ds_active + 1)

    # ============ 构造 AppContext ============
    ctx = AppContext(
        # State 值
        tabs=tabs,
        active_index=active_index,
        session=session,
        pending_jump_ref=pending_jump_ref,
        pending_jump_sig=pending_jump_sig,
        set_pending_jump_sig=set_pending_jump_sig,
        search_focus_seq=search_focus_seq,
        set_search_focus_seq=set_search_focus_seq,
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
        active_index_left=active_index_left,
        active_index_right=active_index_right,
        session_left=session_left,
        session_right=session_right,
        fs_version=fs_version,
        # 派生值
        cur_tab=cur_tab,
        is_diff_tab=is_diff_tab,
        document=document,
        file_path=file_path,
        shortcut_mgr=shortcut_mgr,
        diff_sync=diff_sync,
        diff_result=_diff_result,
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
        set_active_index_left=set_active_index_left,
        set_active_index_right=set_active_index_right,
        set_session_left=set_session_left,
        set_session_right=set_session_right,
        # Refs
        is_diff_tab_ref=is_diff_tab_ref,
        diff_nav_left=diff_nav_left,
        diff_nav_right=diff_nav_right,
        diff_active_pane_ref=diff_active_pane_ref,
        nav_ref=nav_ref,
        nav_ref_split=nav_ref_split,
        active_pane_ref=active_pane_ref,
        active_index_left_ref=active_index_left_ref,
        active_index_right_ref=active_index_right_ref,
        session_left_ref=session_left_ref,
        session_right_ref=session_right_ref,
        picker_holder=picker_holder,
        clipboard_holder=clipboard_holder,
        page_ref=page_ref,
        tabs_ref=tabs_ref,
        active_index_ref=active_index_ref,
        settings_ref=settings_ref,
        dispatcher_ref=dispatcher_ref,
        native_input_ref=native_input_ref,
        paste_old_draft=paste_old_draft,
        arrow_repeat_ref=arrow_repeat_ref,
        status_ref=status_ref,
        sidebar_replace_ref=sidebar_replace_ref,
        # 自动备份 / 崩溃恢复 / 状态消息 UI 状态
        recovery_open=recovery_open,
        set_recovery_open=set_recovery_open,
        recovery_list=recovery_list,
        set_recovery_list=set_recovery_list,
        status_message=status_message,
    )

    # ============ 文档内搜索（浮层）控制器与装配槽 ============
    # 在控制器装配前把状态/回调挂到 ctx（build_keyboard 的 app_callbacks 与
    # build_render 立即读取）。事件回调运行时经 ref/attr 读取最新状态。
    def _ds_jump_to(li: int, off: int) -> None:
        """路由到焦点视口的编辑动作：优先视口中部平滑滚动，缺省退化为常规跳转。"""
        nav = ctx.get_active_nav()
        if nav is not None and nav.current is not None:
            act = nav.current
            fn = getattr(act, "jump_to_line_center", None) or act.jump_to_line
            with contextlib.suppress(Exception):
                fn(li, off)

    def _ds_goto(idx: int) -> None:
        m = doc_search_matches_ref.current
        if not m:
            return
        idx = idx % len(m)
        set_doc_search_active(idx)
        doc_search_active_ref.current = idx
        li, s, _e = m[idx]
        _ds_jump_to(li, s)

    def _ds_open() -> None:
        """Ctrl+F：唤起浮层文档内搜索并聚焦输入框（不触碰侧边栏）。"""
        set_doc_search_open(True)
        doc_search_focus_seq_ref.current += 1
        set_doc_search_focus_seq(doc_search_focus_seq_ref.current)
        if doc_search_active == -1 and doc_search_matches_ref.current:
            set_doc_search_active(0)
            doc_search_active_ref.current = 0

    def _ds_close() -> None:
        """关闭浮层：清高亮并把焦点交还编辑器编辑区。

        焦点函数是 async（Flet 需 await 才真正执行）：把整个 async 可调用对象
        交给 page.run_task 调度（项目约定：run_task 接收 async 函数而非协程
        实例），绝不自行调用生成悬空协程。
        """
        set_doc_search_open(False)
        set_doc_search_active(-1)
        doc_search_active_ref.current = -1
        nav = ctx.get_active_nav()
        if nav is not None and nav.current is not None:
            fn = getattr(nav.current, "focus_document", None)
            if fn is not None:
                page = ctx.page_ref.current
                if page is not None:
                    with contextlib.suppress(Exception):
                        page.run_task(fn)

    def _ds_next() -> None:
        m = doc_search_matches_ref.current
        if not m:
            return
        cur = doc_search_active_ref.current
        _ds_goto((cur + 1) if 0 <= cur < len(m) else 0)

    def _ds_prev() -> None:
        m = doc_search_matches_ref.current
        if not m:
            return
        cur = doc_search_active_ref.current
        _ds_goto((cur - 1) if 0 <= cur < len(m) else len(m) - 1)

    def _ds_global_search() -> None:
        """Ctrl+Shift+F：激活侧边栏「文件夹全局搜索」并聚焦输入框。

        打开侧边栏 → 切到 search 面板 → 打开 search_folder（跨文件/文件夹范围）
        选项 → 递增 focus_seq 驱动 Sidebar 聚焦输入框。与 Ctrl+F（文档内浮层）
        严格分流。
        """
        us = update_setting_ref.current
        if us is None:
            return
        s = settings_ref.current or {}
        if not s.get("sidebar_open", False):
            us("sidebar_open", True)
        us("sidebar_panel", "search")
        us("search_folder", True)
        search_focus_seq_ref.current += 1
        set_search_focus_seq(search_focus_seq_ref.current)

    ctx.doc_search_open = doc_search_open
    ctx.doc_search_open_ref = doc_search_open_ref
    ctx.doc_search_focus_seq = doc_search_focus_seq
    ctx.doc_search_query = doc_search_query
    ctx.set_doc_search_query = set_doc_search_query
    ctx.doc_search_case = doc_search_case
    ctx.set_doc_search_case = set_doc_search_case
    ctx.doc_search_regex = doc_search_regex
    ctx.set_doc_search_regex = set_doc_search_regex
    ctx.doc_search_total = _ds_total
    ctx.doc_search_active = doc_search_active
    ctx.doc_search_doc = _search_doc
    ctx.doc_search_map = doc_search_map
    ctx.doc_search_map_version = doc_search_map_version
    ctx.doc_search_matches_ref = doc_search_matches_ref
    ctx.doc_search_active_ref = doc_search_active_ref
    ctx.open_doc_search = _ds_open
    ctx.close_doc_search = _ds_close
    ctx.doc_search_next = _ds_next
    ctx.doc_search_prev = _ds_prev
    ctx.global_search = _ds_global_search
    ctx.native_input_ref = native_input_ref

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
    # 统一激活入口 + 新标签入口 + 会话计数（拆分组感知，file_io/diff/backup 共用）
    ctx.activate_index = tab_cbs["activate_index"]
    ctx.append_and_activate = tab_cbs["append_and_activate"]
    ctx.bump_tab_session = tab_cbs["bump_tab_session"]
    # 渲染期同步：close_tab 每次渲染新建但行为一致（仅读稳定 ref/setter），
    # 写入 ref 供稳定化的 close_current_tab 读取最新实例。
    close_tab_ref.current = tab_cbs["close_tab"]
    ctx.save_and_close_pending = tab_cbs["save_and_close_pending"]
    ctx.close_without_save = tab_cbs["close_without_save"]
    ctx.cancel_close = tab_cbs["cancel_close"]

    file_cbs = build_file_io_ops(ctx)
    ctx.push_recent_file = file_cbs["push_recent_file"]
    ctx.open_file_by_path = file_cbs["open_file_by_path"]

    # 跨文件"打开后跳转"：供侧边栏跨文件搜索结果点击调用。
    # 内部转调 open_file_by_path(path, jump_to=(li, off))，由 pending_jump 机制处理时序。
    def _open_file_and_jump(path: str, li: int, off: int | None = None):
        ctx.open_file_by_path(path, jump_to=(li, off))

    ctx.open_file_and_jump = _open_file_and_jump

    ctx.new_doc = file_cbs["new_doc"]
    ctx.open_doc = file_cbs["open_doc"]
    ctx.open_folder = file_cbs["open_folder"]
    ctx.save_doc = file_cbs["save_doc"]
    ctx.save_doc_sync = file_cbs["save_doc_sync"]
    ctx.force_save_doc = file_cbs["force_save_doc"]
    ctx.save_as_doc = file_cbs["save_as_doc"]
    ctx.export_doc = file_cbs["export_doc"]

    dialog_cbs = build_file_dialogs(ctx)
    ctx.show_snack = dialog_cbs["show_snack"]
    ctx.copy_path = dialog_cbs["copy_path"]
    ctx.on_file_dialog_confirm = dialog_cbs["on_file_dialog_confirm"]
    ctx.on_file_dialog_cancel = dialog_cbs["on_file_dialog_cancel"]
    ctx.open_input_dialog = dialog_cbs["open_input_dialog"]
    ctx.open_delete_dialog = dialog_cbs["open_delete_dialog"]
    ctx.update_tab_for_renamed_file = dialog_cbs["update_tab_for_renamed_file"]
    ctx.close_tabs_for_path = dialog_cbs["close_tabs_for_path"]
    ctx.on_tab_context_action = dialog_cbs["on_tab_context_action"]
    ctx.on_sidebar_context_action = dialog_cbs["on_sidebar_context_action"]
    ctx.move_fs_item = dialog_cbs["move_fs_item"]

    # 非 md 文件用系统默认程序打开（资源管理器双击直觉）：try/except 捕获后 SnackBar 提示
    def _open_external(path: str):
        try:
            file_ops.open_external(path)
        except Exception as e:
            ctx.show_snack(f"打开失败：{e}")

    ctx.open_external = _open_external

    diff_cbs = build_diff_controller(ctx)
    ctx.get_text_for_compare = diff_cbs["get_text_for_compare"]
    ctx.select_for_compare = diff_cbs["select_for_compare"]
    ctx.compare_with_selected = diff_cbs["compare_with_selected"]
    ctx.on_diff_dirty_change = diff_cbs["on_diff_dirty_change"]

    # ============ 状态栏消息推送桥接 ============
    # set_status_message(msg, kind) → 写入 status_message state（tuple），
    # StatusBar 监听 status_message prop 变化展示，3s 后自动清空。
    # msg=None 时清空状态（供 on_status_clear 回调使用）。
    # 必须在 backup_controller / file_io_ops 装配前设置（二者闭包内调用此槽位）。
    def _set_status_message(msg: str | None, kind: str = "info"):
        if msg is None:
            set_status_message_state(None)
        else:
            set_status_message_state((msg, kind))

    ctx.set_status_message = _set_status_message

    # ============ 备份控制器（自动备份 / 崩溃恢复 / 启动扫描）============
    # 在 settings_controller 之前装配：settings_controller 的 open_recovery_panel
    # 闭包在调用时读取 ctx.scan_recent_backups，需此槽位已填充。
    backup_cbs = build_backup_controller(ctx)
    ctx.start_backup_loop = backup_cbs["start_backup_loop"]
    ctx.trigger_autosave_now = backup_cbs["trigger_autosave_now"]
    # 程序退出前同步自动保存所有脏标签到原文件（auto_save 开启时）
    ctx.autosave_on_exit = backup_cbs["autosave_on_exit"]
    ctx.trigger_backup_now = backup_cbs["trigger_backup_now"]
    ctx.write_exit_sentinel = backup_cbs["write_exit_sentinel"]
    ctx.scan_recoverable = backup_cbs["scan_recoverable"]
    ctx.scan_recent_backups = backup_cbs["scan_recent_backups"]
    ctx.open_backup_in_new_tab = backup_cbs["open_backup_in_new_tab"]
    ctx.delete_backup = backup_cbs["delete_backup"]
    ctx.cleanup_expired_backups = backup_cbs["cleanup_expired_backups"]

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
    ctx.toggle_outline = settings_cbs["toggle_outline"]
    ctx.toggle_word_wrap = settings_cbs["toggle_word_wrap"]
    ctx.zoom_in = settings_cbs["zoom_in"]
    ctx.zoom_out = settings_cbs["zoom_out"]
    ctx.zoom_reset = settings_cbs["zoom_reset"]
    ctx.change_sidebar_panel = settings_cbs["change_sidebar_panel"]
    ctx.change_sidebar_width = settings_cbs["change_sidebar_width"]
    ctx.open_recovery_panel = settings_cbs["open_recovery_panel"]
    ctx.pick_backup_dir = settings_cbs["pick_backup_dir"]

    split_cbs = build_split_editor(ctx)
    ctx.toggle_split_editor = split_cbs["toggle_split_editor"]
    ctx.set_active_pane = split_cbs["set_active_pane"]
    ctx.set_diff_active_pane = split_cbs["set_diff_active_pane"]

    focus_cbs = build_focus_router(ctx)
    ctx.get_active_nav = focus_cbs["get_active_nav"]
    ctx.apply_content_layout = focus_cbs["apply_content_layout"]
    ctx.jump_to_line = focus_cbs["jump_to_line"]
    ctx.on_dirty_change = focus_cbs["on_dirty_change"]
    ctx.on_dirty_change_pane = focus_cbs["on_dirty_change_pane"]

    keyboard_cbs = build_keyboard(ctx)
    # 打破前向引用循环：update_setting 装配后写入 ref，shortcut_mgr 的 lambda 即可调用
    update_setting_ref.current = settings_cbs["update_setting"]
    # 渲染期同步：每次重渲染把最新 dispatcher 写入 ref，_handler 即可读到最新值
    dispatcher_ref.current = keyboard_cbs["dispatcher"]

    # ============ 状态栏命令式更新装配 ============
    # push_cursor_to_status(row, col)：async，直接调 status_ref.update_cursor。
    # schedule_status_count_update()：sync，300ms 防抖后异步重算计数并推送。
    # 由 _render.py 按焦点视口包装（拆分/对比模式下非焦点视口上报被丢弃）。
    #
    # 稳定化：两个更新器用 use_memo([]) 创建一次——它们仅读稳定 ref（status_ref /
    # count_token_ref / page_ref / tabs_ref / active_index_ref / diff_active_pane_ref），
    # 行为与每次渲染重建一致（读取 ref 的最新值），但函数身份跨渲染不变 →
    # on_cursor_move / on_content_change prop 身份稳定，MarkdownEditor @ft.component
    # memo 在光标/内容 prop 上成立，App 重渲染（侧边栏切换 / 主题切换）不再因此
    # 触发编辑器全量重跑。这是规则 2（高频局部 UI 稳定回调身份）的关键一环。

    # 文件系统变更信号：文件增删改后递增 fs_version，驱动侧边栏文件树异步重扫。
    # 在文件操作控制器之前装配，供 on_file_dialog_confirm / duplicate 等回调调用。
    def _bump_fs_version():
        fs_version_ref.current += 1
        set_fs_version(fs_version_ref.current)

    ctx.bump_fs_version = _bump_fs_version

    def _make_push_cursor():
        async def _push(row: int, col: int):
            s = status_ref.current
            if s is not None:
                await s.update_cursor(row, col)
        return _push

    def _make_schedule_count():
        def _schedule():
            # token 防抖：每次调用递增 token，_do_count 醒来后校验 token 是否仍是最新，
            # 过期任务（防抖期间又有新编辑 / 切标签）直接丢弃，等价于取消未触发的定时器。
            count_token_ref.current += 1
            my_token = count_token_ref.current
            page = page_ref.current
            if page is None:
                return

            async def _do_count():
                await asyncio.sleep(0.3)
                # 防抖期间又有新编辑 → token 已变，本任务放弃
                if count_token_ref.current != my_token:
                    return
                # 取当前焦点视口的文档（diff 模式按 diff_active_pane 选侧）
                ts = tabs_ref.current
                ai = active_index_ref.current
                if not (0 <= ai < len(ts)):
                    return
                tab = ts[ai]
                if tab.get("type") == "diff":
                    doc = (tab.get("right_doc") if diff_active_pane_ref.current == 1
                           else tab.get("left_doc"))
                else:
                    doc = tab.get("document")
                if doc is None:
                    return
                # 跨标签竞态校验：doc 引用须仍是当前焦点侧文档
                ts2 = tabs_ref.current
                ai2 = active_index_ref.current
                if not (0 <= ai2 < len(ts2)):
                    return
                tab2 = ts2[ai2]
                cur_doc = (tab2.get("right_doc") if (tab2.get("type") == "diff"
                            and diff_active_pane_ref.current == 1)
                           else (tab2.get("left_doc") if tab2.get("type") == "diff"
                                 else tab2.get("document")))
                if doc is not cur_doc:
                    return
                word, char, para, reading = _compute_counts(doc)
                s = status_ref.current
                if s is not None:
                    await s.update_counts(word, char, para, reading)

            page.run_task(_do_count)
        return _schedule

    ctx.push_cursor_to_status = ft.use_memo(_make_push_cursor, [])
    ctx.schedule_status_count_update = ft.use_memo(_make_schedule_count, [])

    # 稳定化「关闭当前标签」：供 DiffHeader on_close 使用。@ft.memo 要求 prop 身份
    # 跨渲染不变，原 lambda: ctx.close_tab(ctx.active_index) 每次渲染新建会击穿 memo。
    # 读 close_tab_ref.current（渲染期同步最新 close_tab）+ active_index_ref.current，
    # 行为与原 lambda 一致，身份稳定 → DiffHeader memo 成立。
    def _make_close_current_tab():
        def _close():
            fn = close_tab_ref.current
            if fn is not None:
                fn(active_index_ref.current)
        return _close

    ctx.close_current_tab = ft.use_memo(_make_close_current_tab, [])

    # ============ 搜索/替换稳定闭包（KeyDispatcher → App → Sidebar 桥接）============
    # 与 close_current_tab 同模式：use_memo([]) 创建一次，通过 ref 读取最新值。
    # update_setting_ref 在 settings_controller 装配后写入；settings_ref 每渲染同步。
    # Ctrl+F 聚焦：search_focus_seq_ref 持最新序号，set_search_focus_seq 触发渲染。
    def _make_focus_search():
        def _focus():
            us = update_setting_ref.current
            if us is None:
                return
            s = settings_ref.current
            if not s.get("sidebar_open", False):
                us("sidebar_open", True)
            us("sidebar_panel", "search")
            # 驱动 Sidebar 聚焦搜索输入框（面板切换 + 序号变化 → effect 聚焦）
            search_focus_seq_ref.current += 1
            set_search_focus_seq(search_focus_seq_ref.current)
        return _focus

    def _make_toggle_replace_bar():
        def _toggle():
            us = update_setting_ref.current
            if us is None:
                return
            s = settings_ref.current
            if not s.get("sidebar_open", False):
                us("sidebar_open", True)
            us("sidebar_panel", "search")
            us("search_replace_expanded", not s.get("search_replace_expanded", False))
        return _toggle

    def _make_replace_current():
        def _replace():
            actions = sidebar_replace_ref.current
            fn = actions.get("replace_current") if actions else None
            if fn is not None:
                fn()
        return _replace

    def _make_replace_all():
        def _replace():
            actions = sidebar_replace_ref.current
            fn = actions.get("replace_all") if actions else None
            if fn is not None:
                fn()
        return _replace

    ctx.focus_search = ft.use_memo(_make_focus_search, [])
    ctx.toggle_replace_bar = ft.use_memo(_make_toggle_replace_bar, [])
    ctx.replace_current = ft.use_memo(_make_replace_current, [])
    ctx.replace_all = ft.use_memo(_make_replace_all, [])

    # ============ use_effect（hooks 顺序约束：函数体顶层调用）============
    ft.use_effect(settings_cbs["mount_picker"], [])
    ft.use_effect(settings_cbs["apply_theme"], [theme_mode])
    ft.use_effect(keyboard_cbs["bind_keyboard"], [])

    # 文档搜索当前匹配索引归一化：匹配数变化后自动落到首/末合法值（-1=无匹配）
    def _normalize_doc_search_active():
        if not doc_search_open:
            return
        total = len(doc_search_matches_ref.current)
        cur = doc_search_active_ref.current
        if total == 0:
            if cur != -1:
                set_doc_search_active(-1)
                doc_search_active_ref.current = -1
        elif not (0 <= cur < total):
            set_doc_search_active(0)
            doc_search_active_ref.current = 0

    ft.use_effect(_normalize_doc_search_active, [doc_search_open, _ds_total])

    # 跨文件"打开后跳转"：消费 pending_jump_ref。
    # 触发条件：session 变化（切换/打开 tab 重建编辑器）或 pending_jump_sig 变化
    # （文件已是当前 tab，session 不变）。Flet effect 在子组件渲染后执行，此时
    # nav_ref.current 已是新的 EditorActions，可安全调用 jump_to_line(li, off)。
    # 消费即清，避免重复跳转。
    def _fire_pending_jump():
        job = pending_jump_ref.current
        if job is None:
            return
        pending_jump_ref.current = None
        li, off = job
        # ctx.jump_to_line 已路由到当前焦点视口（diff/拆分/单编辑器统一）
        ctx.jump_to_line(li, off)

    ft.use_effect(_fire_pending_jump, [session, pending_jump_sig])

    # ============ 自动备份循环 + 窗口事件钩子 + 启动扫描 ============
    # start_backup_loop：启动自动保存 + 备份两个后台 asyncio 循环（独立于自动保存
    # 开关，备份始终运行）。返回 cleanup 取消任务，组件卸载 / 应用退出时调用。
    ft.use_effect(ctx.start_backup_loop, [])

    # 窗口事件钩子：失焦 / 最小化 → 即时触发自动保存；关闭 / 断连 → 先同步
    # 自动保存所有脏标签到原文件（autosave_on_exit），再写退出哨兵。
    # page.window.on_event 在桌面端捕获窗口状态变化（Flet 0.86+ API）；
    # on_disconnect 捕获 websocket 断连（含异常崩溃场景）。
    # 两者共同确保崩溃/退出前最后一次状态既落盘到原文件（自动保存开启时）
    # 也写入备份目录（哨兵 + 全量备份，未命名文档由恢复面板兜底）。
    def _bind_window_hooks():
        page = page_ref.current
        if page is None:
            return lambda: None

        def _on_window_event(e):
            # Flet 0.86+：e.type 为 WindowEventType 枚举（.value 小写字符串）
            # BLUR/FOCUS/MINIMIZE/HIDE/CLOSE 等，统一取 .value 小写比较
            etype = getattr(e, "type", None)
            etype_val = etype.value if etype is not None else ""
            etype_str = str(etype_val).lower() if etype_val else str(etype).lower()
            if etype_str in ("blur", "minimize", "hide"):
                ctx.trigger_autosave_now()
            elif etype_str == "close":
                ctx.autosave_on_exit()
                ctx.write_exit_sentinel()

        def _on_disconnect(_e):
            # websocket 断连（含崩溃）→ 尽力同步自动保存 + 写入退出哨兵
            ctx.autosave_on_exit()
            ctx.write_exit_sentinel()

        # Flet 0.86+：page.window.on_event 替代旧的 page.on_window_event
        window = getattr(page, "window", None)
        try:
            if window is not None:
                window.on_event = _on_window_event
            page.on_disconnect = _on_disconnect
        except Exception:
            pass

        def _cleanup():
            try:
                if window is not None and window.on_event is _on_window_event:
                    window.on_event = None
                if page.on_disconnect is _on_disconnect:
                    page.on_disconnect = None
            except Exception:
                pass

        return _cleanup

    ft.use_effect(_bind_window_hooks, [])

    # 启动扫描可恢复草稿：仅在首次挂载时执行一次（backup_started_ref 防严格模式重复）。
    # 扫描上次会话哨兵，若存在未保存文档则弹出恢复面板（非阻塞）。
    # 同时清理过期备份（低频，启动时一次）。
    def _startup_scan_recoverable():
        if backup_started_ref.current:
            return
        backup_started_ref.current = True

        def _do_scan():
            try:
                ctx.cleanup_expired_backups()
            except Exception:
                pass
            infos = ctx.scan_recoverable()
            if infos:
                set_recovery_list(infos)
                set_recovery_open(True)

        page = page_ref.current
        if page is not None:
            try:
                page.run_task(_do_scan)
            except Exception:
                pass

    ft.use_effect(_startup_scan_recoverable, [])

    # ============ 渲染树 ============
    return build_render(ctx)
