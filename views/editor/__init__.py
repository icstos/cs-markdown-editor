"""编辑器根组件：Stack 双层叠加光标级实时渲染（Typora 式 WYSIWYG）。

包结构（EditorContext + 工厂模式）：
- __init__.py（本文件）：MarkdownEditor 组件入口
  hooks → state/ref 镜像 → 派生设置 → ctx 构造 → 工厂调用 → 装配槽填充
  → 稳定回调包装器 → use_effect → EditorActions → TOC → 行控件列表 → 渲染树
- _context.py：EditorContext dataclass（双区：稳定区 + 快照区）
- _helpers.py：模块级辅助函数与常量
- _history.py / _cursor.py / _navigation.py / _scroll.py / _outward.py /
  _indent.py / _blocks.py / _inline_format.py / _clipboard.py / _fence.py /
  _raw_mode.py / _focus.py / _key.py：工厂模块（build_xxx(ctx) -> dict）
- _actions.py：EditorActions 装配
- _render.py：行视图列表构造

状态分层：
- document：observable Document（行列表 + 文件元信息）
- cursor_li / cursor_off：光标位置（激活行号 + 行级 raw 偏移）
- nav_seq：仅撤销/重做等强制重建场景递增（同行输入不递增，保 IME 组合态）

硬约束（来自重构架构说明书 / core/actions.py docstring）：
- cursor_ref 必须是 ft.use_ref（非 state），避免重渲染打断 IME
- 透明 cursor TextField 不设 value 属性；value 清空由 use_effect([clear_value_seq]) 异步执行
- nav_seq 仅在撤销/重做时递增（同行输入不递增以保 IME 组合态）
- cursor_li=None 表浏览态；cursor_off 为行级 raw 偏移 0..len(line.raw)
- 所有 use_* hook 必须在组件函数体顶层顺序调用（Flet 0.86 约束）
- IME 热路径必须用 reparse_line_atomic（仅 1 次 observable 通知）
"""

from collections.abc import Awaitable, Callable

import flet as ft

from core.cursor import CursorState
from core.history import EditHistory
from models import BlockType, Document, SegType
from styles import _current_colors
from views.editor._actions import build_actions
from views.editor._blocks import build_blocks
from views.editor._clipboard import build_clipboard
from views.editor._context import EditorContext
from views.editor._cursor import build_cursor
from views.editor._fence import build_fence
from views.editor._focus import build_focus
from views.editor._helpers import _make_stable_cb, _noop
from views.editor._history import build_history
from views.editor._indent import build_indent
from views.editor._inline_format import build_inline_format
from views.editor._key import build_key
from views.editor._navigation import build_navigation
from views.editor._outward import build_outward
from views.editor._raw_mode import build_raw_mode
from views.editor._render import build_line_controls
from views.editor._scroll import build_scroll
from views.raw_editor import RawEditor
from views.tool_area import ToolArea


@ft.component
def MarkdownEditor(
    document: Document,
    file_path: str | None = None,
    on_new: Callable[[], None] | None = None,
    on_open: Callable[[], None] | None = None,
    on_open_folder: Callable[[], None] | None = None,
    on_save: Callable[[], None] | None = None,
    on_export: Callable[[], None] | None = None,
    on_dirty_change: Callable[[bool], None] | None = None,
    nav_ref: ft.Ref | None = None,
    clipboard_ref: ft.Ref | None = None,
    theme_mode: ft.ThemeMode = ft.ThemeMode.LIGHT,
    on_toggle_theme: Callable[[], None] | None = None,
    settings: dict | None = None,
    on_open_settings: Callable[[], None] | None = None,
    sidebar_open: bool = False,
    on_toggle_sidebar: Callable[[], None] | None = None,
    shortcut_mgr=None,
    show_toolbar: bool | None = None,
    on_editor_focus: Callable[[], None] | None = None,
    keyboard_autofocus: bool = True,
    # diff 对比模式：diff_marks 映射行号→标记，diff_gaps 映射行号→间隙高度列表
    diff_marks: dict[int, str] | None = None,
    diff_gaps: dict[int, list[float]] | None = None,
    # 滚动同步回调：滚动时上报 (offset, max_scroll, viewport_h)，供 diff 对比模式
    # 驱动另一侧同步滚动。None 时不同步（单编辑器 / 拆分编辑器）。
    on_scroll_change: Callable[[float, float, float], None] | None = None,
    # 状态栏命令式上报（高频局部 UI，跳过 set_state 全量重建）：
    # on_cursor_move(row, col)：光标位置变化时异步推送至状态栏（仅焦点视口上报）。
    # on_content_change()：文档内容变化（mark_dirty）时触发，App 防抖重算字数。
    on_cursor_move: Callable[[int, int], Awaitable[None] | None] | None = None,
    on_content_change: Callable[[], None] | None = None,
):
    # ============ 派生设置 ============
    c = _current_colors()
    settings = settings or {}
    content_max_width = settings.get("content_max_width", 920)
    content_padding = settings.get("content_padding", 36)
    content_padding_top = settings.get("content_padding_top", 24)
    show_footer = settings.get("show_footer", True)
    body_font_size = settings.get("body_font_size", 16)
    line_height = settings.get("line_height", 1.6)
    # show_toolbar prop：None 时回落到 settings，False 时强制隐藏（用于右侧拆分编辑器）
    show_toolbar = show_toolbar if show_toolbar is not None else settings.get("show_toolbar", True)
    word_wrap = settings.get("word_wrap", True)

    # ============ 状态：光标级（替代 active/active_seg/draft）============
    cursor_li, set_cursor_li = ft.use_state(None)  # 激活行号 | None（浏览态）
    cursor_off, set_cursor_off = ft.use_state(0)  # 行级 raw 偏移 0..len(line.raw)
    nav_seq, set_nav_seq = ft.use_state(0)  # 仅撤销/重做递增，强制 TextField 重建
    # 光标重聚焦触发器：点击同一位置时 cursor_li/cursor_off 不变，use_effect 不触发，
    # 但点击已使 cursor TextField 失焦。递增此值强制 _focus_cursor_field 重新聚焦。
    focus_seq, set_focus_seq = ft.use_state(0)
    cursor_line, set_cursor_line = ft.use_state(0)  # 最近交互行（供工具栏块级操作）
    cursor_field_ref = ft.use_ref(None)  # 透明 cursor TextField 引用
    # IME 输入会话：on_change 期间不清空 TextField value，用"增量式"编辑同步文档
    input_session_ref = ft.use_ref({"li": -1, "start_off": -1, "last_value": ""})
    # value 清空序列号：_end_input_session 递增 → use_effect 触发清空 TextField value
    clear_value_seq, set_clear_value_seq = ft.use_state(0)

    # 光标跟踪（ref 而非 state）：避免 on_selection_change 触发重渲染导致光标跳动
    cursor_ref = ft.use_ref(CursorState())
    # 粘贴时抑制 on_blur
    suppress_blur = ft.use_ref(False)
    # 原文模式
    raw_mode, set_raw_mode = ft.use_state(False)
    raw_draft, set_raw_draft = ft.use_state("")
    # ListView ref + 滚动跟踪
    list_view_ref = ft.use_ref(None)
    scroll_offset_ref = ft.use_ref(0.0)
    viewport_h_ref = ft.use_ref(0.0)
    max_scroll_ref = ft.use_ref(0.0)
    # 视口宽度跟踪：程序尺寸变化时段落自适应宽度
    viewport_w, set_viewport_w = ft.use_state(0.0)
    viewport_w_ref = ft.use_ref(0.0)
    # 行实际渲染高度缓存：{line_idx: height_px}
    line_heights_ref = ft.use_ref({})
    # LineLayoutCache 缓存：跨行拖拽选区精确命中
    layout_cache_ref = ft.use_ref(None)
    # 行偏移前缀和缓存
    _offset_prefix_ref = ft.use_ref(None)
    # 记忆列：垂直导航时记录的 X 像素
    preferred_col_ref = ft.use_ref(None)
    # SelectionArea 当前选中的纯文本
    selection_text_ref = ft.use_ref("")
    # 撤销 / 重做栈
    history_ref = ft.use_ref(EditHistory(max_size=50))
    restoring = ft.use_ref(False)
    undo_push_pending = ft.use_ref(True)
    # 向外选区
    outward_sel, set_outward_sel = ft.use_state(None)
    outward_sel_ref = ft.use_ref(None)
    shift_pressed_ref = ft.use_ref(False)
    ctrl_pressed_ref = ft.use_ref(False)
    # 跳转目标行脉冲高亮
    flash_li, set_flash_li = ft.use_state(-1)
    # 代码块 / 表格聚焦
    code_focus_ref = ft.use_ref(None)
    code_edit_snapshot = ft.use_ref(None)
    code_edit_changed = ft.use_ref(False)
    table_focus_ref = ft.use_ref(None)
    table_nav_ref = ft.use_ref(None)
    # 表格聚焦 state（修复 table_focus_ref 从未赋值 Bug）
    table_focus_li, set_table_focus_li = ft.use_state(None)
    table_focus_ref.current = table_focus_li
    # 块级公式聚焦：浏览态 ft.Markdown 渲染 LaTeX，点击进入编辑态 TextField
    math_focus_li, set_math_focus_li = ft.use_state(None)
    math_focus_ref = ft.use_ref(None)
    math_focus_ref.current = math_focus_li
    math_field_ref = ft.use_ref(None)
    math_edit_snapshot = ft.use_ref(None)
    math_edit_changed = ft.use_ref(False)

    # ============ state → ref 镜像（单一编排点）============
    outward_sel_ref.current = outward_sel

    # ============ 共享闭包（留在 __init__.py，所有工厂通过 ctx 访问）============
    def _set_outward_sel(value):
        outward_sel_ref.current = value
        set_outward_sel(value)

    def mark_dirty():
        # 守卫：dirty 已为 True 时不再赋值，避免 True→True 触发额外 observable 通知
        if not document.dirty:
            document.dirty = True
        if on_dirty_change:
            on_dirty_change(True)
        # 状态栏字数防抖重算：在 reparse_line_atomic 之后调用，document 状态已更新。
        # on_content_change 仅调度防抖任务，不阻塞 IME 热路径。
        if on_content_change:
            on_content_change()

    # ============ content_width：段落换行宽度 ============
    # word_wrap=False：inf（不换行）
    # word_wrap=True：视口可用宽度（占满整行，VSCode 风格）
    #   viewport_w=0（首次渲染前）回退到 content_max_width
    if not word_wrap:
        content_width = float("inf")
    elif viewport_w > 0:
        available = viewport_w - 2 * content_padding
        content_width = available if available > 0 else content_max_width - 2 * content_padding
    else:
        content_width = content_max_width - 2 * content_padding

    # ============ 构造 EditorContext ============
    ctx = EditorContext(
        # Props
        document=document,
        file_path=file_path,
        on_new=on_new,
        on_open=on_open,
        on_open_folder=on_open_folder,
        on_save=on_save,
        on_export=on_export,
        on_dirty_change=on_dirty_change,
        nav_ref=nav_ref,
        clipboard_ref=clipboard_ref,
        theme_mode=theme_mode,
        on_toggle_theme=on_toggle_theme,
        settings=settings,
        on_open_settings=on_open_settings,
        sidebar_open=sidebar_open,
        on_toggle_sidebar=on_toggle_sidebar,
        shortcut_mgr=shortcut_mgr,
        show_toolbar=show_toolbar,
        on_editor_focus=on_editor_focus,
        keyboard_autofocus=keyboard_autofocus,
        diff_marks=diff_marks,
        diff_gaps=diff_gaps,
        on_scroll_change=on_scroll_change,
        on_cursor_move=on_cursor_move,
        on_content_change=on_content_change,
        # 派生设置
        c=c,
        content_max_width=content_max_width,
        content_padding=content_padding,
        content_padding_top=content_padding_top,
        show_footer=show_footer,
        body_font_size=body_font_size,
        line_height=line_height,
        word_wrap=word_wrap,
        content_width=content_width,
        # State 值
        cursor_li=cursor_li,
        cursor_off=cursor_off,
        nav_seq=nav_seq,
        focus_seq=focus_seq,
        cursor_line=cursor_line,
        clear_value_seq=clear_value_seq,
        raw_mode=raw_mode,
        raw_draft=raw_draft,
        viewport_w=viewport_w,
        outward_sel=outward_sel,
        flash_li=flash_li,
        table_focus_li=table_focus_li,
        math_focus_li=math_focus_li,
        # Setters
        set_cursor_li=set_cursor_li,
        set_cursor_off=set_cursor_off,
        set_nav_seq=set_nav_seq,
        set_focus_seq=set_focus_seq,
        set_cursor_line=set_cursor_line,
        set_clear_value_seq=set_clear_value_seq,
        set_raw_mode=set_raw_mode,
        set_raw_draft=set_raw_draft,
        set_viewport_w=set_viewport_w,
        set_flash_li=set_flash_li,
        set_table_focus_li=set_table_focus_li,
        set_math_focus_li=set_math_focus_li,
        # Refs
        cursor_field_ref=cursor_field_ref,
        input_session_ref=input_session_ref,
        cursor_ref=cursor_ref,
        suppress_blur=suppress_blur,
        list_view_ref=list_view_ref,
        scroll_offset_ref=scroll_offset_ref,
        viewport_h_ref=viewport_h_ref,
        max_scroll_ref=max_scroll_ref,
        viewport_w_ref=viewport_w_ref,
        line_heights_ref=line_heights_ref,
        layout_cache_ref=layout_cache_ref,
        offset_prefix_ref=_offset_prefix_ref,
        preferred_col_ref=preferred_col_ref,
        selection_text_ref=selection_text_ref,
        history_ref=history_ref,
        restoring=restoring,
        undo_push_pending=undo_push_pending,
        outward_sel_ref=outward_sel_ref,
        shift_pressed_ref=shift_pressed_ref,
        ctrl_pressed_ref=ctrl_pressed_ref,
        code_focus_ref=code_focus_ref,
        code_edit_snapshot=code_edit_snapshot,
        code_edit_changed=code_edit_changed,
        table_focus_ref=table_focus_ref,
        table_nav_ref=table_nav_ref,
        math_focus_ref=math_focus_ref,
        math_field_ref=math_field_ref,
        math_edit_snapshot=math_edit_snapshot,
        math_edit_changed=math_edit_changed,
    )

    # ============ 工厂调用（无 hook，可任意顺序；闭包在调用时读 ctx）============
    cursor_cbs = build_cursor(ctx)
    history_cbs = build_history(ctx)
    scroll_cbs = build_scroll(ctx)
    nav_cbs = build_navigation(ctx)
    outward_cbs = build_outward(ctx)
    indent_cbs = build_indent(ctx)
    blocks_cbs = build_blocks(ctx)
    inline_fmt_cbs = build_inline_format(ctx)
    clipboard_cbs = build_clipboard(ctx)
    fence_cbs = build_fence(ctx)
    raw_mode_cbs = build_raw_mode(ctx)
    focus_cbs = build_focus(ctx)
    key_cbs = build_key(ctx)

    # ============ 装配槽填充（跨工厂调用通过 ctx 属性）============
    # 共享
    ctx.mark_dirty = mark_dirty
    ctx.set_outward_sel = _set_outward_sel
    # cursor 组
    ctx.cursor_base = cursor_cbs["cursor_base"]
    ctx.set_cursor = cursor_cbs["set_cursor"]
    ctx.end_input_session = cursor_cbs["end_input_session"]
    ctx.on_tap_line = cursor_cbs["on_tap_line"]
    ctx.handle_char_input = cursor_cbs["handle_char_input"]
    ctx.handle_paste = cursor_cbs["handle_paste"]
    ctx.backspace_core = cursor_cbs["backspace_core"]
    ctx.delete_core = cursor_cbs["delete_core"]
    ctx.on_submit = cursor_cbs["on_submit"]
    # history 组
    ctx.make_snapshot = history_cbs["make_snapshot"]
    ctx.push_history = history_cbs["push_history"]
    ctx.push_line_edit = history_cbs["push_line_edit"]
    ctx.maybe_push_history = history_cbs["maybe_push_history"]
    ctx.undo = history_cbs["undo"]
    ctx.redo = history_cbs["redo"]
    # scroll 组
    ctx.on_scroll = scroll_cbs["on_scroll"]
    ctx.get_scroll_state = scroll_cbs["get_scroll_state"]
    ctx.scroll_to_offset = scroll_cbs["scroll_to_offset"]
    ctx.on_content_resize = scroll_cbs["on_content_resize"]
    ctx.on_line_size_change = scroll_cbs["on_line_size_change"]
    ctx.ensure_visible = scroll_cbs["ensure_visible"]
    ctx.safe_scroll_to = scroll_cbs["safe_scroll_to"]
    ctx.estimate_line_height = scroll_cbs["estimate_line_height"]
    ctx.estimate_line_offset = scroll_cbs["estimate_line_offset"]
    ctx.hit_test_line_x = scroll_cbs["hit_test_line_x"]
    ctx.get_layout_cache = scroll_cbs["get_layout_cache"]
    ctx.hit_test_xy = scroll_cbs["hit_test_xy"]
    ctx.page_vlines = scroll_cbs["page_vlines"]
    ctx.page_up = scroll_cbs["page_up"]
    ctx.page_down = scroll_cbs["page_down"]
    ctx.scroll_by_page = scroll_cbs["scroll_by_page"]
    ctx.reset_line_heights = scroll_cbs["reset_line_heights"]
    ctx.get_cursor_row_col = scroll_cbs["get_cursor_row_col"]
    ctx.build_highlight_map = scroll_cbs["build_highlight_map"]
    ctx.jump_to = scroll_cbs["jump_to"]
    # navigation 组
    ctx.move_left = nav_cbs["move_left"]
    ctx.move_right = nav_cbs["move_right"]
    ctx.move_home = nav_cbs["move_home"]
    ctx.move_end = nav_cbs["move_end"]
    ctx.move_doc_start = nav_cbs["move_doc_start"]
    ctx.move_doc_end = nav_cbs["move_doc_end"]
    ctx.move_up = nav_cbs["move_up"]
    ctx.move_down = nav_cbs["move_down"]
    ctx.move_vline = nav_cbs["move_vline"]
    ctx.cursor_vline_info = nav_cbs["cursor_vline_info"]
    ctx.get_line_visual_lines = nav_cbs["get_line_visual_lines"]
    # outward 组
    ctx.step_left = outward_cbs["step_left"]
    ctx.step_right = outward_cbs["step_right"]
    ctx.step_up = outward_cbs["step_up"]
    ctx.step_down = outward_cbs["step_down"]
    ctx.start_outward_from_point = outward_cbs["start_outward_from_point"]
    ctx.extend_outward = outward_cbs["extend_outward"]
    ctx.extend_outward_step = outward_cbs["extend_outward_step"]
    ctx.select_word_at = outward_cbs["select_word_at"]
    ctx.on_extend_outward = outward_cbs["on_extend_outward"]
    ctx.delete_raw_range = outward_cbs["delete_raw_range"]
    ctx.handle_outward_delete = outward_cbs["handle_outward_delete"]
    ctx.handle_outward_cut = outward_cbs["handle_outward_cut"]
    ctx.handle_outward_copy = outward_cbs["handle_outward_copy"]
    ctx.select_all = outward_cbs["select_all"]
    ctx.clear_outward_sel = outward_cbs["clear_outward_sel"]
    # indent 组
    ctx.indent_or_outdent = indent_cbs["indent_or_outdent"]
    ctx.new_line_after = indent_cbs["new_line_after"]
    # blocks 组
    ctx.set_block = blocks_cbs["set_block"]
    ctx.toggle_task = blocks_cbs["toggle_task"]
    ctx.toggle_task_at_cursor = blocks_cbs["toggle_task_at_cursor"]
    ctx.format_task = blocks_cbs["format_task"]
    ctx.format_table = blocks_cbs["format_table"]
    ctx.change_lang = blocks_cbs["change_lang"]
    # inline_format 组
    ctx.apply_inline_format = inline_fmt_cbs["apply_inline_format"]
    ctx.apply_outward_wrap = inline_fmt_cbs["apply_outward_wrap"]
    ctx.handle_outward_type_char = inline_fmt_cbs["handle_outward_type_char"]
    # clipboard 组
    ctx.compute_markdown_from_text = clipboard_cbs["compute_markdown_from_text"]
    ctx.handle_delete_selection = clipboard_cbs["handle_delete_selection"]
    ctx.handle_cut = clipboard_cbs["handle_cut"]
    ctx.cut_current_line = clipboard_cbs["cut_current_line"]
    ctx.apply_inline_format_to_selection = clipboard_cbs["apply_inline_format_to_selection"]
    ctx.on_selection_area_change = clipboard_cbs["on_selection_area_change"]
    # fence 组
    ctx.on_change_code = fence_cbs["on_change_code"]
    ctx.on_code_focus = fence_cbs["on_code_focus"]
    ctx.on_code_blur = fence_cbs["on_code_blur"]
    ctx.handle_code_backspace = fence_cbs["handle_code_backspace"]
    ctx.on_change_math = fence_cbs["on_change_math"]
    ctx.on_math_focus = fence_cbs["on_math_focus"]
    ctx.on_math_blur = fence_cbs["on_math_blur"]
    ctx.on_change_cell = fence_cbs["on_change_cell"]
    ctx.on_table_op = fence_cbs["on_table_op"]
    ctx.on_table_focus = fence_cbs["on_table_focus"]
    ctx.on_table_blur = fence_cbs["on_table_blur"]
    # raw_mode 组
    ctx.toggle_raw = raw_mode_cbs["toggle_raw"]
    ctx.toggle_focus_mode = raw_mode_cbs["toggle_focus_mode"]
    ctx.on_blur = raw_mode_cbs["on_blur"]
    ctx.on_cursor_focus = raw_mode_cbs["on_cursor_focus"]
    ctx.suppress_blur_for_click = raw_mode_cbs["suppress_blur_for_click"]
    ctx.on_raw_change = raw_mode_cbs["on_raw_change"]
    # focus 组
    ctx.focus_cursor_field = focus_cbs["focus_cursor_field"]
    ctx.clear_cursor_value = focus_cbs["clear_cursor_value"]
    ctx.focus_math_field = focus_cbs["focus_math_field"]
    # key 组
    ctx.on_key_down = key_cbs["on_key_down"]
    ctx.on_key_up = key_cbs["on_key_up"]

    # ============ use_memo：向外选区高亮映射 ============
    _highlight_map = ft.use_memo(
        scroll_cbs["build_highlight_map"], [outward_sel, len(document.lines)]
    )

    # ============ use_effect：聚焦 cursor TextField ============
    # 依赖 cursor_li + nav_seq + focus_seq：
    # - cursor_li 变化：切换行，TextField key 含 li，key 变 → 新控件需聚焦
    # - nav_seq 变化：撤销/重做，TextField key 含 seq，key 变 → 新控件需聚焦
    # - focus_seq 变化：点击同一位置（cursor_li/off 均不变，但点击已使 TextField
    #   失焦），须强制重聚焦避免光标丢失
    ft.use_effect(focus_cbs["focus_cursor_field"], [cursor_li, nav_seq, focus_seq])

    # ============ use_effect：文档行数变化时清空行高缓存 ============
    ft.use_effect(scroll_cbs["reset_line_heights"], [len(document.lines), word_wrap, viewport_w])

    # ============ use_effect：状态栏光标位置命令式上报 ============
    # 依赖 cursor_li/cursor_off/cursor_line/nav_seq：覆盖所有光标状态变化路径
    #（含围栏块 set_cursor_line、undo/redo nav_seq 递增、IME 会话结束 set_cursor_off），
    # 无需 instrument _set_cursor 的 15+ 散点。use_effect 在 commit 后触发，状态栏
    # 更新比光标渲染晚一帧（~16ms，人眼不可察）。on_cursor_move 由 App 路由到
    # 焦点视口的状态栏（拆分/对比模式下非焦点视口上报被丢弃）。
    async def _report_cursor():
        if on_cursor_move is None:
            return
        if cursor_li is not None and 0 <= cursor_li < len(document.lines):
            row, col = cursor_li + 1, cursor_off + 1
        else:
            row, col = cursor_line + 1, 1
        res = on_cursor_move(row, col)
        if res is not None:
            await res

    ft.use_effect(_report_cursor, [cursor_li, cursor_off, cursor_line, nav_seq])

    # ============ use_effect：清空 cursor TextField 内部 value ============
    ft.use_effect(focus_cbs["clear_cursor_value"], [clear_value_seq])

    # ============ use_effect：聚焦公式 TextField ============
    ft.use_effect(focus_cbs["focus_math_field"], [math_focus_li])

    # ============ EditorActions 上报 ============
    build_actions(ctx)

    # ============ TOC 条目（use_memo 稳定化）============
    # 增量签名：tuple of (i, level, raw) 避免大字符串拼接复制；
    # 比较时利用 raw 同对象 O(1) 字符串比较（未编辑行 raw 引用稳定），
    # 较原 "|".join(...) 拼接（每次复制全部标题 raw）显著降低大文档开销。
    _toc_sig = tuple(
        (i, ln.level, ln.raw)
        for i, ln in enumerate(document.lines)
        if ln.block_type == BlockType.HEADING
    )

    def _build_toc():
        return [
            (
                i,
                line.level,
                "".join(
                    s.text for s in line.segments if s.seg_type != SegType.HEADING_PREFIX
                ).strip(),
            )
            for i, line in enumerate(document.lines)
            if line.block_type == BlockType.HEADING
            and "".join(
                s.text for s in line.segments if s.seg_type != SegType.HEADING_PREFIX
            ).strip()
        ]

    toc_entries = ft.use_memo(_build_toc, [_toc_sig])

    # ============ 回调稳定化：LineView 共享回调 ============
    # editor 每次重渲染时闭包重建，若直接传给 LineView 会导致 ft.memo 浅比较
    # 误判所有行变化并重渲染。此处用 ref 持有最新闭包，use_memo([]) 产出稳定引用。
    _cb_ref = ft.use_ref({})
    _cb_ref.current = {
        "on_tap": cursor_cbs["on_tap_line"],
        "on_pan_start": outward_cbs["on_extend_outward"],
        "on_pan_update": outward_cbs["on_extend_outward"],
        "on_toggle_task": blocks_cbs["toggle_task"],
        "on_change_code": fence_cbs["on_change_code"],
        "on_code_focus": fence_cbs["on_code_focus"],
        "on_code_blur": fence_cbs["on_code_blur"],
        "on_change_lang": blocks_cbs["change_lang"],
        "on_change_math": fence_cbs["on_change_math"],
        "on_math_focus": fence_cbs["on_math_focus"],
        "on_math_blur": fence_cbs["on_math_blur"],
        "on_jump_to": scroll_cbs["jump_to"],
        "on_line_size_change": scroll_cbs["on_line_size_change"],
        "on_extend_outward": outward_cbs["on_extend_outward"],
        "on_clear_outward": outward_cbs["clear_outward_sel"],
        "on_hit_test_x": scroll_cbs["hit_test_line_x"],
        "on_hit_test_xy": scroll_cbs["hit_test_xy"],
        "on_double_tap": outward_cbs["select_word_at"],
    }
    _STABLE_CB_KEYS = (
        "on_tap", "on_pan_start", "on_pan_update", "on_toggle_task",
        "on_change_code", "on_code_focus", "on_code_blur", "on_change_lang",
        "on_change_math", "on_math_focus", "on_math_blur", "on_jump_to",
        "on_line_size_change", "on_extend_outward", "on_clear_outward",
        "on_hit_test_x", "on_hit_test_xy", "on_double_tap",
    )
    _stable_cbs = ft.use_memo(
        lambda: {k: _make_stable_cb(_cb_ref, k) for k in _STABLE_CB_KEYS},
        [],
    )

    # ============ 回调稳定化：TableView 回调 ============
    _table_cb_ref = ft.use_ref({})
    _table_cb_ref.current = {
        "on_change_cell": fence_cbs["on_change_cell"],
        "on_table_op": fence_cbs["on_table_op"],
        "on_table_focus": fence_cbs["on_table_focus"],
        "on_table_blur": fence_cbs["on_table_blur"],
    }
    _TABLE_CB_KEYS = ("on_change_cell", "on_table_op", "on_table_focus", "on_table_blur")
    _table_stable = ft.use_memo(
        lambda: {k: _make_stable_cb(_table_cb_ref, k) for k in _TABLE_CB_KEYS},
        [],
    )

    # ============ 回调稳定化：ToolArea / RawEditor 回调 ============
    _tool_cb_ref = ft.use_ref({})
    _tool_cb_ref.current = {
        "on_new": on_new or _noop,
        "on_open": on_open or _noop,
        "on_open_folder": on_open_folder or _noop,
        "on_save": on_save or _noop,
        "on_open_settings": on_open_settings or _noop,
        "set_block": blocks_cbs["set_block"],
        "apply_inline_format": inline_fmt_cbs["apply_inline_format"],
        "toggle_raw": raw_mode_cbs["toggle_raw"],
        "on_export": on_export or _noop,
        "toggle_focus_mode": raw_mode_cbs["toggle_focus_mode"],
        "on_toggle_theme": on_toggle_theme or _noop,
        "on_raw_change": raw_mode_cbs["on_raw_change"],
    }
    _TOOL_CB_KEYS = (
        "on_new", "on_open", "on_open_folder", "on_save", "on_open_settings",
        "set_block", "apply_inline_format", "toggle_raw",
        "on_export", "toggle_focus_mode", "on_toggle_theme",
        "on_raw_change",
    )
    _tool_stable = ft.use_memo(
        lambda: {k: _make_stable_cb(_tool_cb_ref, k) for k in _TOOL_CB_KEYS},
        [],
    )

    # ============ 行视图列表 ============
    line_controls = build_line_controls(
        ctx, _stable_cbs, _table_stable, toc_entries, _highlight_map
    )

    # ============ 渲染树 ============
    return ft.KeyboardListener(
        autofocus=keyboard_autofocus,
        on_key_down=key_cbs["on_key_down"],
        on_key_up=key_cbs["on_key_up"],
        expand=True,
        content=ft.Column(
            controls=[
                ToolArea(
                    theme_mode=theme_mode,
                    show_toolbar=show_toolbar,
                    shortcut_mgr=shortcut_mgr,
                    raw_mode=raw_mode,
                    on_new=_tool_stable["on_new"],
                    on_open=_tool_stable["on_open"],
                    on_open_folder=_tool_stable["on_open_folder"],
                    on_save=_tool_stable["on_save"],
                    on_open_settings=_tool_stable["on_open_settings"],
                    set_block=_tool_stable["set_block"],
                    apply_inline_format=_tool_stable["apply_inline_format"],
                    on_toggle_raw=_tool_stable["toggle_raw"],
                    on_export=_tool_stable["on_export"],
                    on_toggle_focus_mode=_tool_stable["toggle_focus_mode"],
                    on_toggle_theme=_tool_stable["on_toggle_theme"],
                ),
                RawEditor(
                    theme_mode=theme_mode,
                    raw_draft=raw_draft,
                    on_change=_tool_stable["on_raw_change"],
                    content_padding=content_padding,
                    content_padding_top=content_padding_top,
                    body_font_size=body_font_size,
                )
                if raw_mode
                else ft.SelectionArea(
                    expand=True,
                    on_change=clipboard_cbs["on_selection_area_change"],
                    content=ft.Container(
                        content=ft.ListView(
                            ref=list_view_ref,
                            controls=line_controls,
                            expand=True,
                            spacing=0,
                            # ListView 虚拟化（build_controls_on_demand=True 默认）：
                            # 仅构建视口内可见行，数千行文档不卡顿。maxScrollExtent
                            # 由首项高度估算，_scroll.py 的两步滚动逻辑已为此设计
                            # （视口外行无实测高度 → 先估算滚动触发构建再精确贴顶）。
                            # padding 保留在外层 Container：顶部 content_padding_top
                            # 作为固定留白不随内容滚动。
                            on_scroll=scroll_cbs["on_scroll"],
                        ),
                        expand=True,
                        alignment=ft.Alignment.TOP_LEFT,
                        bgcolor=c.bg,
                        padding=ft.Padding.symmetric(
                            horizontal=content_padding, vertical=content_padding_top
                        ),
                        on_size_change=scroll_cbs["on_content_resize"],
                    ),
                ),
            ],
            expand=True,
        ),
    )
