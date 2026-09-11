"""键盘分发器补充覆盖（第 10 轮新增）。

覆盖此前**无直接测试**的三处：

1. `_handle_multi_cursor_clipboard`（51 行）：多光标下 Ctrl+C/X/V 的分支与守卫
   ——副光标没有原生 TextField，无法走原生剪贴板，必须在此拦截。
2. `_sync_modifier_keys`（21 行）：页面级事件的修饰键 → editor `*_pressed_ref`
   ——编辑器 KeyboardListener 事件不可靠（无 ctrl 字段、Shift/Alt 键名带 Left/Right）。
3. **全局动作在两条路径下的行为等价性**：`_GLOBAL_ACTIONS` 是唯一来源，
   焦点在编辑器内 vs 焦点在原生输入框时，同一按键应触发同一 app 回调
   （调用方式可不同：协程走 run_task）。
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_key_bindings import (  # noqa: E402
    FakeRef,
    evt,
    make_actions,
    make_dispatcher,
)


class _MultiCursorActions:
    """多光标场景的最小 EditorActions 替身（只提供剪贴板分支读取的属性）。"""

    def __init__(self, *, has_selection: bool = True):
        self.calls: list[str] = []
        self._has_selection = has_selection
        self.outward_sel = None
        self.cursor_li = None
        self.native_focused = False
        self._calls = self.calls

    # --- 剪贴板分支读取的属性 ---
    def has_secondary_cursors(self) -> bool:
        return True

    def has_multi_cursor_selection(self) -> bool:
        return self._has_selection

    def copy_multi_cursor_selection(self):
        self.calls.append("copy_multi_cursor_selection")

    def cut_multi_cursor_selection(self):
        self.calls.append("cut_multi_cursor_selection")

    def paste_to_multi_cursors(self, text: str) -> None:
        self.calls.append("paste_to_multi_cursors")

    def paste_to_multi_cursors_plain(self, text: str) -> None:
        self.calls.append("paste_to_multi_cursors_plain")

    # --- 兜底：其余属性按需返回 None / no-op ---
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return None


def _multi_cursor_dispatcher(actions, app_calls: list):
    """装配一个把 actions 暴露给 dispatcher 的实例（cb 补齐剪贴板分支所需项）。"""

    def rec(name):
        def fn():
            app_calls.append(name)
        return fn

    from services.shortcuts import ShortcutManager

    from views.key_bindings import KeyDispatcher

    cb = {
        "copy": rec("cb.copy"),
        "cut": rec("cb.cut"),
        "paste": rec("cb.paste"),
        "paste_plain": rec("cb.paste_plain"),
        "save": rec("save"),
    }
    d = KeyDispatcher(
        shortcut_mgr=ShortcutManager({}, lambda k, v: None),
        actions_ref=FakeRef(actions),
        clipboard_ref=FakeRef(None),
        page_ref=FakeRef(types.SimpleNamespace(run_task=lambda fn, *a: None)),
        paste_old_draft=FakeRef(""),
        app_callbacks=cb,
    )
    return d


# ---------------- 多光标剪贴板 ----------------


def test_multi_cursor_ctrl_c_consumed_with_selection():
    """多光标 + 有选区：Ctrl+C 被消费（走多光标复制任务，不落原生）。"""
    actions = _MultiCursorActions(has_selection=True)
    d = _multi_cursor_dispatcher(actions, [])
    assert d._handle_multi_cursor_clipboard("ctrl+c", actions) is True


def test_multi_cursor_ctrl_c_not_consumed_without_selection():
    """多光标但无选区：Ctrl+C 不消费（交原生 / 其它分支处理）。"""
    actions = _MultiCursorActions(has_selection=False)
    d = _multi_cursor_dispatcher(actions, [])
    assert d._handle_multi_cursor_clipboard("ctrl+c", actions) is False


def test_multi_cursor_ctrl_x_consumed_with_selection():
    actions = _MultiCursorActions(has_selection=True)
    d = _multi_cursor_dispatcher(actions, [])
    assert d._handle_multi_cursor_clipboard("ctrl+x", actions) is True


def test_multi_cursor_ctrl_v_consumed_and_begins_paste():
    """Ctrl+V：消费按键并进入粘贴会话（paste_in_progress 置位）。"""
    actions = _MultiCursorActions()
    d = _multi_cursor_dispatcher(actions, [])
    assert d._handle_multi_cursor_clipboard("ctrl+v", actions) is True
    assert d._paste_old_draft.current == ""


def test_multi_cursor_ctrl_shift_v_consumed():
    actions = _MultiCursorActions()
    d = _multi_cursor_dispatcher(actions, [])
    assert d._handle_multi_cursor_clipboard("ctrl+shift+v", actions) is True


def test_multi_cursor_unrelated_combo_not_consumed():
    """无关组合键不消费（不应吞掉正常按键）。"""
    actions = _MultiCursorActions()
    d = _multi_cursor_dispatcher(actions, [])
    assert d._handle_multi_cursor_clipboard("ctrl+s", actions) is False
    assert d._handle_multi_cursor_clipboard("a", actions) is False


# ---------------- 修饰键同步 ----------------


def test_sync_modifier_keys_writes_all_three_refs():
    """e.shift / e.ctrl / e.alt 必须全部同步到对应 ref。"""
    calls: list = []
    actions = make_actions(calls)
    # make_actions 未提供 alt_pressed_ref（默认 None）——补上以验证三路同步
    actions.alt_pressed_ref = FakeRef(False)
    d, _app_calls, _page = make_dispatcher(actions, calls)
    d._sync_modifier_keys(evt("a", ctrl=True, shift=False, alt=True), actions)
    assert actions.shift_pressed_ref.current is False
    assert actions.ctrl_pressed_ref.current is True
    assert actions.alt_pressed_ref.current is True


def test_sync_modifier_keys_tolerates_missing_actions():
    """actions 为 None 时不得抛异常（浏览态无编辑器实例）。"""
    calls: list = []
    actions = make_actions(calls)
    d, _app_calls, _page = make_dispatcher(actions, calls)
    d._sync_modifier_keys(evt("a", ctrl=True), None)  # 不应抛


def test_sync_modifier_keys_runs_on_every_handle_call():
    """handle() 每次分发都同步修饰键（供 Alt+Click 多光标等读取）。"""
    calls: list = []
    actions = make_actions(calls)
    actions.alt_pressed_ref = FakeRef(False)
    d, _app_calls, _page = make_dispatcher(actions, calls)
    d.handle(evt("a", ctrl=True, alt=True))
    assert actions.ctrl_pressed_ref.current is True
    assert actions.alt_pressed_ref.current is True
    d.handle(evt("a"))
    assert actions.ctrl_pressed_ref.current is False
    assert actions.alt_pressed_ref.current is False


# ---------------- 两条路径的行为等价性 ----------------

# (按键事件, 期望 app 回调名) —— 取 make_dispatcher 已装配 cb 的子集
PARITY_CASES = [
    (evt("s", ctrl=True), "save"),
    (evt("n", ctrl=True), "new"),
    (evt("o", ctrl=True), "open"),
    (evt("w", ctrl=True), "close_tab"),
    (evt("tab", ctrl=True), "next_tab"),
    (evt("tab", ctrl=True, shift=True), "prev_tab"),
    (evt("b", ctrl=True, shift=True), "toggle_sidebar"),
    (evt("t", alt=True), "toggle_theme"),
    (evt("k", ctrl=True, shift=True), "focus_mode"),
    (evt("r", ctrl=True, shift=True), "toggle_word_wrap"),
    (evt("f", ctrl=True), "focus_search"),
    (evt("h", ctrl=True), "toggle_replace_bar"),
    (evt("enter", alt=True), "replace_current"),
    (evt("enter", ctrl=True, alt=True), "replace_all"),
]


def _dispatch_and_collect(event, *, foreign: bool) -> list:
    """派发一次按键，返回触发的 app 回调名（同步执行 run_task，便于断言协程动作）。"""
    app_calls: list = []
    actions = make_actions(app_calls, cursor_li=0)
    d, _calls, page = make_dispatcher(actions, app_calls, foreign=foreign)
    # 让 run_task 立即执行：save / open 等动作以协程函数调度，
    # 原 FakePage 只记录不执行，这里需要真实效果
    page.run_task = lambda fn, *a: fn(*a) if callable(fn) else None
    d.handle(event)
    return [c for c in app_calls if not c.startswith("cb.")]


def test_global_actions_behave_identically_in_both_paths():
    """同一全局按键在「编辑器内」与「原生输入框内」必须触发同一 app 回调。

    这是 `_GLOBAL_ACTIONS` 单一来源的核心保证：若某动作只加进一条路径，
    它就会在另一类焦点下静默失效——本测试会立刻失败。
    """
    problems = {}
    for event, expected in PARITY_CASES:
        inside = _dispatch_and_collect(event, foreign=False)
        outside = _dispatch_and_collect(event, foreign=True)
        if expected not in inside:
            problems[f"{expected}@编辑器内"] = inside
        if expected not in outside:
            problems[f"{expected}@原生输入框"] = outside
    assert not problems, f"全局动作在两条路径下行为不一致: {problems}"
