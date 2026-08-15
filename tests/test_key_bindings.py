"""views/key_bindings 单元测试。

覆盖 _combo / _extract_printable_char 纯函数 + KeyDispatcher.handle 路由决策
（捕获模式、原生控件聚焦守卫、outward_sel 路由、全局标签快捷键、行内格式、
Ctrl+0~6 标题、编辑态导航、浏览/编辑层快捷键分发）。
不启动 Flet 页面，用 mock actions_ref / page_ref / shortcut_mgr。
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from core.actions import EditorActions  # noqa: E402
from models import BlockType, Line  # noqa: E402
from services.shortcuts import DEFAULT_SHORTCUTS, ShortcutManager  # noqa: E402
from views.key_bindings import KeyDispatcher, _combo, _extract_printable_char  # noqa: E402


# ---------------- 测试助手 ----------------
class FakeRef:
    """伪 ft.Ref：避免 flet.Ref 内部 weakref 对不可弱引用类型的限制。"""

    def __init__(self, current=None):
        self.current = current


def evt(key: str = "", ctrl: bool = False, shift: bool = False, alt: bool = False, meta: bool = False):
    """构造伪 KeyboardEvent。"""
    return types.SimpleNamespace(key=key, ctrl=ctrl, shift=shift, alt=alt, meta=meta)


def make_actions(
    calls: list,
    *,
    cursor_li: int | None = None,
    cursor_off: int = 0,
    raw_mode: bool = False,
    active_line: Line | None = None,
    outward_sel: tuple | None = None,
    native_focused: bool = False,
    code_backspace_ret: bool | None = None,
) -> EditorActions:
    """构造带记录桩的 EditorActions。每个动作 append 自己的名字到 calls。"""
    def rec(name):
        def fn(*args, **kwargs):
            calls.append(name)
        return fn

    async def rec_async(name):
        async def fn(*args, **kwargs):
            calls.append(name)
        return fn

    code_ref = FakeRef(object() if native_focused else None)
    table_ref = FakeRef(object() if native_focused else None)
    math_ref = FakeRef(None)
    cursor_ref = FakeRef(types.SimpleNamespace(base=0, extent=0, draft_len=0))
    sel_text_ref = FakeRef("")
    shift_ref = FakeRef(False)
    ctrl_ref = FakeRef(False)
    paste_in_progress_ref = FakeRef(False)

    # 空代码块 Backspace 删除桩：code_backspace_ret 非 None 时模拟聚焦代码块（code_ref
    # 持有行号 0）并返回给定布尔值；否则返回 False 不拦截（保持现有测试行为）
    if code_backspace_ret is not None:
        code_ref = FakeRef(0)

        def _code_backspace(li):
            calls.append(("handle_code_backspace", li))
            return code_backspace_ret
    else:
        def _code_backspace(li):
            return False

    actions = EditorActions(
        cursor_li=cursor_li,
        cursor_off=cursor_off,
        active_line=active_line,
        raw_mode=raw_mode,
        cursor_ref=cursor_ref,
        selection_text_ref=sel_text_ref,
        nav_seq=0,
        move_left=rec("move_left"),
        move_right=rec("move_right"),
        move_home=rec("move_home"),
        move_end=rec("move_end"),
        move_doc_start=rec("move_doc_start"),
        move_doc_end=rec("move_doc_end"),
        move_up=rec("move_up"),
        move_down=rec("move_down"),
        page_up=rec("page_up"),
        page_down=rec("page_down"),
        link_tab_jump=lambda i: False,
        backspace_core=rec("backspace_core"),
        delete_core=rec("delete_core"),
        indent_or_outdent=rec("indent_or_outdent"),
        handle_paste=rec("handle_paste"),
        handle_paste_plain=rec("handle_paste_plain"),
        handle_cut=rec("handle_cut"),
        handle_delete_selection=rec("handle_delete_selection"),
        apply_inline_format_to_selection=rec("apply_inline_format_to_selection"),
        compute_markdown_from_text=lambda t: t,
        undo=rec("undo"),
        redo=rec("redo"),
        jump_to_line=rec("jump_to_line"),
        toggle_raw=rec("toggle_raw"),
        toggle_focus_mode=rec("toggle_focus_mode"),
        set_block=rec("set_block"),
        apply_inline_format=rec("apply_inline_format"),
        toggle_task_at_cursor=rec("toggle_task_at_cursor"),
        format_task=rec("format_task"),
        format_table=rec("format_table"),
        insert_text=rec("insert_text"),
        code_focus_ref=code_ref,
        handle_code_backspace=_code_backspace,
        table_focus_ref=table_ref,
        get_cursor_row_col=lambda: (1, 1),
        outward_sel=outward_sel,
        math_focus_ref=math_ref,
        shift_pressed_ref=shift_ref,
        ctrl_pressed_ref=ctrl_ref,
        extend_outward_left=rec("extend_outward_left"),
        extend_outward_right=rec("extend_outward_right"),
        extend_outward_up=rec("extend_outward_up"),
        extend_outward_down=rec("extend_outward_down"),
        handle_outward_cut=rec("handle_outward_cut"),
        handle_outward_delete=rec("handle_outward_delete"),
        handle_outward_copy=rec("handle_outward_copy"),
        clear_outward_sel=rec("clear_outward_sel"),
        select_all=rec("select_all"),
        cut_current_line=rec("cut_current_line"),
        handle_outward_type_char=rec("handle_outward_type_char"),
        paste_in_progress_ref=paste_in_progress_ref,
    )
    return actions


class FakePage:
    def __init__(self):
        self.tasks: list = []

    def run_task(self, fn, *args):
        self.tasks.append((fn, args))


def make_dispatcher(
    actions: EditorActions | None,
    app_calls: list,
    *,
    capturing: tuple = (None, None),
    on_capture=None,
    on_cancel_capture=None,
    shortcuts: dict | None = None,
) -> tuple[KeyDispatcher, list, FakePage]:
    """构造 KeyDispatcher + 配套 refs。返回 (dispatcher, app_calls记录列表, fake_page)。

    shortcuts：可选的 shortcuts 配置覆盖（{layer: {action_id: combo}}），
    用于让特定快捷键以规范化形式（如 ctrl+,）命中 matches。
    """
    settings: dict = {}
    if shortcuts is not None:
        settings["shortcuts"] = shortcuts
    shortcut_mgr = ShortcutManager(settings, lambda k, v: settings.__setitem__(k, v))
    actions_ref = FakeRef(actions)
    clipboard_ref = FakeRef(None)
    fake_page = FakePage()
    page_ref = FakeRef(fake_page)
    paste_old_draft = FakeRef("")

    def make_cb(name):
        def fn():
            app_calls.append(name)
        return fn

    app_callbacks = {
        "save": make_cb("save"),
        "new": make_cb("new"),
        "open": make_cb("open"),
        "toggle_sidebar": make_cb("toggle_sidebar"),
        "toggle_theme": make_cb("toggle_theme"),
        "open_settings": make_cb("open_settings"),
        "close_tab": make_cb("close_tab"),
        "next_tab": make_cb("next_tab"),
        "prev_tab": make_cb("prev_tab"),
        "toggle_word_wrap": make_cb("toggle_word_wrap"),
        "toggle_split_editor": make_cb("toggle_split_editor"),
        "focus_search": make_cb("focus_search"),
        "toggle_replace_bar": make_cb("toggle_replace_bar"),
        "replace_current": make_cb("replace_current"),
        "replace_all": make_cb("replace_all"),
    }
    d = KeyDispatcher(
        shortcut_mgr=shortcut_mgr,
        actions_ref=actions_ref,
        clipboard_ref=clipboard_ref,
        page_ref=page_ref,
        paste_old_draft=paste_old_draft,
        app_callbacks=app_callbacks,
        capturing=capturing,
        on_capture=on_capture,
        on_cancel_capture=on_cancel_capture,
    )
    return d, app_calls, fake_page


# ---------------- _combo ----------------
def test_combo_plain_letter():
    assert _combo(evt("a")) == "a"


def test_combo_ctrl_s():
    assert _combo(evt("s", ctrl=True)) == "ctrl+s"


def test_combo_ctrl_shift_z():
    assert _combo(evt("z", ctrl=True, shift=True)) == "ctrl+shift+z"


def test_combo_alt_z():
    assert _combo(evt("z", alt=True)) == "alt+z"


def test_combo_arrow_keys_mapped():
    assert _combo(evt("arrowleft")) == "left"
    assert _combo(evt("arrowright")) == "right"
    assert _combo(evt("arrowup")) == "up"
    assert _combo(evt("arrowdown")) == "down"


def test_combo_comma_mapped():
    assert _combo(evt("comma", ctrl=True)) == "ctrl+,"


def test_combo_space_mapped():
    # Flet 空格键 KeyboardEvent.key 为 "Space"（首字母大写），非字面空格。
    assert _combo(evt("Space")) == "space"


def test_combo_escape_mapped():
    assert _combo(evt("escape")) == "esc"


def test_combo_pure_modifier_returns_empty():
    assert _combo(evt("control")) == ""
    assert _combo(evt("shift")) == ""
    assert _combo(evt("alt")) == ""


def test_combo_meta_treated_as_ctrl():
    assert _combo(evt("s", meta=True)) == "ctrl+s"


# ---------------- _extract_printable_char ----------------
def test_extract_plain_letter():
    assert _extract_printable_char(evt("a")) == "a"


def test_extract_letter_uppercase_with_shift():
    assert _extract_printable_char(evt("A", shift=True)) == "A"


def test_extract_ctrl_blocks():
    assert _extract_printable_char(evt("a", ctrl=True)) is None


def test_extract_alt_blocks():
    assert _extract_printable_char(evt("a", alt=True)) is None


def test_extract_function_key_blocks():
    assert _extract_printable_char(evt("f5")) is None


def test_extract_navigation_blocks():
    assert _extract_printable_char(evt("home")) is None
    assert _extract_printable_char(evt("arrowleft")) is None


def test_extract_space():
    assert _extract_printable_char(evt("space")) == " "


def test_extract_punct():
    assert _extract_printable_char(evt("/")) == "/"


# ---------------- KeyDispatcher.handle：捕获模式 ----------------
def test_capturing_mode_intercepts_combo():
    captured: list = []
    d, _, _ = make_dispatcher(
        None, [], capturing=("edit", "format_bold"),
        on_capture=lambda layer, action_id, combo: captured.append((layer, action_id, combo)),
    )
    d.handle(evt("b", ctrl=True))
    assert captured == [("edit", "format_bold", "ctrl+b")]


def test_capturing_mode_escape_cancels():
    cancelled: list = []
    # 捕获模式需同时提供 on_capture 才会进入拦截分支（生产环境二者同绑）。
    d, _, _ = make_dispatcher(
        None, [], capturing=("edit", "format_bold"),
        on_capture=lambda layer, action_id, combo: None,
        on_cancel_capture=lambda: cancelled.append(True),
    )
    d.handle(evt("escape"))
    assert cancelled == [True]


def test_capturing_mode_backspace_clears_binding():
    captured: list = []
    d, _, _ = make_dispatcher(
        None, [], capturing=("edit", "format_bold"),
        on_capture=lambda layer, action_id, combo: captured.append((layer, action_id, combo)),
    )
    d.handle(evt("backspace"))
    assert captured == [("edit", "format_bold", "")]


def test_capturing_mode_pure_modifier_waits():
    captured: list = []
    d, _, _ = make_dispatcher(
        None, [], capturing=("edit", "format_bold"),
        on_capture=lambda layer, action_id, combo: captured.append((layer, action_id, combo)),
    )
    d.handle(evt("control"))  # 纯修饰键
    assert captured == []


# ---------------- 原生控件聚焦守卫 ----------------
def test_native_focused_navigation_passthrough():
    """原生控件聚焦时，纯导航键放行（不调用 actions）。"""
    calls: list = []
    actions = make_actions(calls, cursor_li=0, native_focused=True)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("arrowleft"))
    assert "move_left" not in calls


def test_native_focused_clipboard_combo_passthrough():
    calls: list = []
    actions = make_actions(calls, cursor_li=0, native_focused=True)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("c", ctrl=True))
    assert "handle_outward_copy" not in calls


def test_native_focused_global_save_still_works():
    """全局快捷键（Ctrl+S）在原生聚焦时仍生效。"""
    app_calls: list = []
    calls: list = []
    actions = make_actions(calls, cursor_li=0, native_focused=True)
    d, app_calls, fake_page = make_dispatcher(actions, app_calls)
    d.handle(evt("s", ctrl=True))
    # save 经 page.run_task(cb["save"]) 异步调度，FakePage 仅记录不执行，
    # 故验证调度队列而非 app_calls（与 test_browse_ctrl_s_saves 一致）。
    assert any(fn == d._app_callbacks["save"] for fn, _ in fake_page.tasks)


# ---------------- outward_sel 路由 ----------------
def test_outward_sel_ctrl_c_routes_to_copy():
    calls: list = []
    actions = make_actions(calls, outward_sel=(0, 0, 1, 0))
    d, _, fake_page = make_dispatcher(actions, [])
    d.handle(evt("c", ctrl=True))
    # run_task 调度 handle_outward_copy
    assert any(fn is actions.handle_outward_copy for fn, _ in fake_page.tasks)


def test_outward_sel_backspace_routes_to_delete():
    calls: list = []
    actions = make_actions(calls, outward_sel=(0, 0, 1, 0))
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("backspace"))
    assert "handle_outward_delete" in calls


def test_outward_sel_escape_clears():
    calls: list = []
    actions = make_actions(calls, outward_sel=(0, 0, 1, 0))
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("escape"))
    assert "clear_outward_sel" in calls


def test_outward_sel_shift_arrow_extends():
    calls: list = []
    actions = make_actions(calls, outward_sel=(0, 0, 1, 0))
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("arrowleft", shift=True))
    assert "extend_outward_left" in calls


def test_outward_sel_plain_arrow_clears():
    calls: list = []
    actions = make_actions(calls, outward_sel=(0, 0, 1, 0))
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("arrowright"))
    assert "clear_outward_sel" in calls


def test_outward_sel_printable_char_replaces():
    calls: list = []
    actions = make_actions(calls, outward_sel=(0, 0, 1, 0))
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("x"))
    assert "handle_outward_type_char" in calls


# ---------------- 全局标签快捷键 ----------------
def test_ctrl_w_closes_tab():
    app_calls: list = []
    d, app_calls, _ = make_dispatcher(None, app_calls)
    d.handle(evt("w", ctrl=True))
    assert "close_tab" in app_calls


def test_ctrl_tab_next_tab():
    app_calls: list = []
    d, app_calls, _ = make_dispatcher(None, app_calls)
    d.handle(evt("tab", ctrl=True))
    assert "next_tab" in app_calls


def test_ctrl_shift_tab_prev_tab():
    app_calls: list = []
    d, app_calls, _ = make_dispatcher(None, app_calls)
    d.handle(evt("tab", ctrl=True, shift=True))
    assert "prev_tab" in app_calls


def test_ctrl_shift_r_toggle_word_wrap():
    app_calls: list = []
    d, app_calls, _ = make_dispatcher(None, app_calls)
    d.handle(evt("r", ctrl=True, shift=True))
    assert "toggle_word_wrap" in app_calls


def test_ctrl_backslash_toggle_split():
    app_calls: list = []
    d, app_calls, _ = make_dispatcher(None, app_calls)
    d.handle(evt("\\", ctrl=True))
    assert "toggle_split_editor" in app_calls


# ---------------- 搜索/替换快捷键（两层均生效）----------------
def test_ctrl_f_focus_search_browse_mode():
    """Ctrl+F 浏览态：聚焦搜索面板。"""
    app_calls: list = []
    d, app_calls, _ = make_dispatcher(None, app_calls)
    d.handle(evt("f", ctrl=True))
    assert "focus_search" in app_calls


def test_ctrl_f_focus_search_edit_mode():
    """Ctrl+F 编辑态：两层均生效。"""
    app_calls: list = []
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, app_calls, _ = make_dispatcher(actions, app_calls)
    d.handle(evt("f", ctrl=True))
    assert "focus_search" in app_calls


def test_ctrl_h_toggle_replace_bar_browse_mode():
    """Ctrl+H 浏览态：展开/收起替换栏。"""
    app_calls: list = []
    d, app_calls, _ = make_dispatcher(None, app_calls)
    d.handle(evt("h", ctrl=True))
    assert "toggle_replace_bar" in app_calls


def test_ctrl_h_toggle_replace_bar_edit_mode():
    """Ctrl+H 编辑态：两层均生效。"""
    app_calls: list = []
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, app_calls, _ = make_dispatcher(actions, app_calls)
    d.handle(evt("h", ctrl=True))
    assert "toggle_replace_bar" in app_calls


def test_alt_enter_replace_current_browse_mode():
    """Alt+Enter 浏览态：替换当前匹配。"""
    app_calls: list = []
    d, app_calls, _ = make_dispatcher(None, app_calls)
    d.handle(evt("enter", alt=True))
    assert "replace_current" in app_calls


def test_alt_enter_replace_current_edit_mode():
    """Alt+Enter 编辑态：两层均生效（不触发 toggle_raw）。"""
    app_calls: list = []
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, app_calls, _ = make_dispatcher(actions, app_calls)
    d.handle(evt("enter", alt=True))
    assert "replace_current" in app_calls
    # Alt+Enter 不应触发 toggle_raw（那是 Ctrl+Enter）
    assert "toggle_raw" not in calls


def test_ctrl_alt_enter_replace_all_browse_mode():
    """Ctrl+Alt+Enter 浏览态：全部替换。"""
    app_calls: list = []
    d, app_calls, _ = make_dispatcher(None, app_calls)
    d.handle(evt("enter", ctrl=True, alt=True))
    assert "replace_all" in app_calls


def test_ctrl_alt_enter_replace_all_edit_mode():
    """Ctrl+Alt+Enter 编辑态：两层均生效。"""
    app_calls: list = []
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, app_calls, _ = make_dispatcher(actions, app_calls)
    d.handle(evt("enter", ctrl=True, alt=True))
    assert "replace_all" in app_calls
    # 不应触发 toggle_raw（那是 Ctrl+Enter，无 Alt）
    assert "toggle_raw" not in calls


# ---------------- PageUp / PageDown ----------------
def test_pageup_calls_page_up():
    calls: list = []
    actions = make_actions(calls)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("pageup"))
    assert "page_up" in calls


def test_pagedown_calls_page_down():
    calls: list = []
    actions = make_actions(calls)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("pagedown"))
    assert "page_down" in calls


# ---------------- 行内格式快捷键 ----------------
def test_ctrl_b_edit_mode_calls_apply_inline_format():
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("b", ctrl=True))
    assert "apply_inline_format" in calls


def test_ctrl_b_browse_mode_calls_apply_inline_format_to_selection():
    calls: list = []
    actions = make_actions(calls, cursor_li=None)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("b", ctrl=True))
    assert "apply_inline_format_to_selection" in calls


# ---------------- Ctrl+A 全选 ----------------
def test_ctrl_a_browse_calls_select_all():
    calls: list = []
    actions = make_actions(calls, cursor_li=None)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("a", ctrl=True))
    assert "select_all" in calls


# ---------------- Ctrl+0~6 标题 ----------------
def test_ctrl_1_sets_heading():
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("1", ctrl=True))
    assert "set_block" in calls


def test_ctrl_0_sets_paragraph():
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("0", ctrl=True))
    assert "set_block" in calls


def test_ctrl_shift_m_sets_math_block():
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("m", ctrl=True, shift=True))
    assert "set_block" in calls


def test_ctrl_shift_l_formats_task():
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("l", ctrl=True, shift=True))
    assert "format_task" in calls


def test_ctrl_t_formats_table():
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("t", ctrl=True))
    assert "format_table" in calls


def test_alt_c_toggles_task():
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("c", alt=True))
    assert "toggle_task_at_cursor" in calls


# ---------------- 编辑态导航 ----------------
def test_edit_home_calls_move_home():
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("home"))
    assert "move_home" in calls


def test_edit_ctrl_home_calls_move_doc_start():
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("home", ctrl=True))
    assert "move_doc_start" in calls


def test_edit_end_calls_move_end():
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("end"))
    assert "move_end" in calls


def test_edit_arrowup_calls_move_up():
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("arrowup"))
    assert "move_up" in calls


def test_edit_backspace_calls_backspace_core():
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("backspace"))
    assert "backspace_core" in calls


def test_edit_delete_calls_delete_core():
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("delete"))
    assert "delete_core" in calls


def test_edit_tab_calls_indent():
    calls: list = []
    line = Line(BlockType.PARAGRAPH, "text")
    actions = make_actions(calls, cursor_li=0, active_line=line)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("tab"))
    assert "indent_or_outdent" in calls


def test_edit_shift_tab_outdents():
    calls: list = []
    line = Line(BlockType.PARAGRAPH, "text")
    actions = make_actions(calls, cursor_li=0, active_line=line)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("tab", shift=True))
    assert "indent_or_outdent" in calls


def test_edit_tab_in_code_block_consumed():
    """代码块行按 Tab：不放给 indent（交由原生 CodeEditor）。"""
    calls: list = []
    line = Line(BlockType.CODE, "code")
    actions = make_actions(calls, cursor_li=0, active_line=line)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("tab"))
    assert "indent_or_outdent" not in calls


def test_edit_shift_arrow_extends_outward():
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("arrowleft", shift=True))
    assert "extend_outward_left" in calls


# ---------------- 浏览态快捷键 ----------------
def test_browse_ctrl_s_saves():
    app_calls: list = []
    d, app_calls, fake_page = make_dispatcher(None, app_calls)
    d.handle(evt("s", ctrl=True))
    # save 走 page.run_task(cb["save"])
    assert any(fn == d._app_callbacks["save"] for fn, _ in fake_page.tasks)


def test_browse_ctrl_n_new():
    app_calls: list = []
    d, app_calls, _ = make_dispatcher(None, app_calls)
    d.handle(evt("n", ctrl=True))
    assert "new" in app_calls


def test_browse_ctrl_o_open():
    app_calls: list = []
    d, app_calls, fake_page = make_dispatcher(None, app_calls)
    d.handle(evt("o", ctrl=True))
    assert any(fn == d._app_callbacks["open"] for fn, _ in fake_page.tasks)


def test_browse_alt_t_toggle_theme():
    app_calls: list = []
    d, app_calls, _ = make_dispatcher(None, app_calls)
    d.handle(evt("t", alt=True))
    assert "toggle_theme" in app_calls


def test_browse_ctrl_comma_open_settings():
    """Ctrl+, 打开设置。配置以规范化形式 ctrl+, 存储以命中 matches。"""
    app_calls: list = []
    sc = {**DEFAULT_SHORTCUTS["browse"], "open_settings": "ctrl+,"}
    d, app_calls, _ = make_dispatcher(None, app_calls, shortcuts={"browse": sc})
    d.handle(evt("comma", ctrl=True))
    assert "open_settings" in app_calls


def test_browse_ctrl_z_undo():
    calls: list = []
    actions = make_actions(calls)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("z", ctrl=True))
    assert "undo" in calls


def test_browse_ctrl_y_redo():
    calls: list = []
    actions = make_actions(calls)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("y", ctrl=True))
    assert "redo" in calls


def test_browse_ctrl_slash_toggle_raw():
    calls: list = []
    actions = make_actions(calls)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("/", ctrl=True))
    assert "toggle_raw" in calls


def test_edit_ctrl_enter_toggle_raw():
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("enter", ctrl=True))
    assert "toggle_raw" in calls


def test_edit_escape_toggle_sidebar():
    app_calls: list = []
    actions = make_actions([], cursor_li=0)
    d, app_calls, _ = make_dispatcher(actions, app_calls)
    d.handle(evt("escape"))
    assert "toggle_sidebar" in app_calls


# ---------------- shift/ctrl 状态同步 ----------------
def test_shift_state_synced_to_ref():
    calls: list = []
    actions = make_actions(calls)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("a", shift=True))
    assert actions.shift_pressed_ref.current is True


def test_ctrl_state_synced_to_ref():
    calls: list = []
    actions = make_actions(calls)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("a", ctrl=True))
    assert actions.ctrl_pressed_ref.current is True


# ---------------- 空代码块 Backspace 删除（Typora 式）----------------
def test_empty_code_block_backspace_deletes_block():
    """空代码块聚焦 + Backspace（无修饰键）→ handle_code_backspace 消费按键。"""
    calls: list = []
    actions = make_actions(calls, code_backspace_ret=True)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("backspace"))
    # handle_code_backspace 被调用且传入聚焦行号 0
    assert ("handle_code_backspace", 0) in calls
    # 按键被消费，不路由到 backspace_core
    assert "backspace_core" not in calls


def test_nonempty_code_block_backspace_passthrough():
    """非空代码块（handle_code_backspace 返回 False）→ 交由原生 CodeEditor 处理。"""
    calls: list = []
    actions = make_actions(calls, code_backspace_ret=False)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("backspace"))
    # handle_code_backspace 被调用但返回 False
    assert ("handle_code_backspace", 0) in calls
    # 放行给原生控件（不在全局路由），backspace_core 不被调用
    assert "backspace_core" not in calls


def test_code_block_ctrl_backspace_not_intercepted():
    """代码块聚焦 + Ctrl+Backspace → 不触发 handle_code_backspace（修饰键守卫）。"""
    calls: list = []
    actions = make_actions(calls, code_backspace_ret=True)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("backspace", ctrl=True))
    # 修饰键按下时不进入 handle_code_backspace 路由
    assert not any(c == "handle_code_backspace" or isinstance(c, tuple) for c in calls)


def test_table_focused_backspace_not_intercepted():
    """表格聚焦（code_focus_ref 为 None）+ Backspace → 不触发 handle_code_backspace。"""
    calls: list = []
    # native_focused=True 使 table_ref.current 为真值，code_ref.current 仍为 None
    actions = make_actions(calls, native_focused=True)
    d, _, _ = make_dispatcher(actions, [])
    d.handle(evt("backspace"))
    # 表格聚焦走原生放行，不调用代码块删除
    assert not any(c == "handle_code_backspace" or isinstance(c, tuple) for c in calls)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
