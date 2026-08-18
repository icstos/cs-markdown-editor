"""长按方向键重复导航测试。

左/右：editor _key.py 的 on_key_repeat（KeyboardListener key_repeat 驱动）
上/下：KeyDispatcher 的自驱动定时器（页面级 HardwareKeyboard 全局处理器驱动，
  因 TextField 的 ignore_up_down_keys 在焦点链叶子层吞掉上/下键，
  KeyboardListener 永远收不到上/下键事件）

覆盖：
- 左/右 key_repeat → move_*（持续移动）
- Shift+左/右 → extend_outward_*（多光标 extend_selection_*）
- 外向选区：Shift 扩展 / 普通取消
- 浏览态/原生控件聚焦不导航
- 非方向键忽略
- KeyDispatcher 上/下定时器：启动→持续移动→KeyUp/其他键/边界停止
"""

import asyncio
import os
import sys
import types
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from views.editor._key import build_key as build_editor_key
from views.key_bindings import KeyDispatcher


class FakeRef:
    def __init__(self, current=None):
        self.current = current


class _Cursor:
    def __init__(self, b=0):
        self.base = b
        self.extent = b

    def reset(self, off, raw_len):
        self.base = off
        self.extent = off


class _Actions:
    """记录型 EditorActions 桩。"""

    def __init__(self, cursor_li=0):
        self.calls = []
        self.cursor_li = cursor_li
        self.raw_mode = False
        self.outward_sel = None
        self.shift_pressed_ref = FakeRef(False)
        self.ctrl_pressed_ref = FakeRef(False)
        self.alt_pressed_ref = FakeRef(False)
        self.code_focus_ref = FakeRef(None)
        self.table_focus_ref = FakeRef(None)
        self.math_focus_ref = FakeRef(None)
        self.cursor_ref = FakeRef(_Cursor(0))

    def _rec(self, name):
        def fn(*a, **k):
            self.calls.append(name)
        return fn

    def __getattr__(self, name):
        if name.startswith(("move_", "extend_", "clear_outward_sel", "has_")):
            return self._rec(name)
        raise AttributeError(name)

    def has_secondary_cursors(self):
        return bool(getattr(self, "_secondary", False))


def _make_ctx(actions):
    return types.SimpleNamespace(
        nav_ref=FakeRef(actions),
        cursor_li=actions.cursor_li,
        shift_pressed_ref=actions.shift_pressed_ref,
        ctrl_pressed_ref=actions.ctrl_pressed_ref,
        alt_pressed_ref=actions.alt_pressed_ref,
        table_focus_ref=actions.table_focus_ref,
        table_nav_ref=FakeRef(None),
        secondary_cursors_ref=FakeRef([]),
        clear_secondary_cursors=lambda: None,
        cursor_ref=actions.cursor_ref,
        arrow_repeat_ref=FakeRef(None),
    )


def _evt(key: str):
    return types.SimpleNamespace(key=key)


# ================ 左/右：editor on_key_repeat ================

def test_repeat_left_right_move_continuously():
    actions = _Actions()
    repeat = build_editor_key(_make_ctx(actions))["on_key_repeat"]
    for _ in range(5):
        repeat(_evt("ArrowRight"))
    assert actions.calls == ["move_right"] * 5


def test_repeat_shift_arrow_extend_outward():
    actions = _Actions()
    actions.shift_pressed_ref.current = True
    repeat = build_editor_key(_make_ctx(actions))["on_key_repeat"]
    repeat(_evt("ArrowLeft"))
    repeat(_evt("ArrowLeft"))
    assert actions.calls == ["extend_outward_left", "extend_outward_left"]


def test_repeat_shift_multi_cursor_extend_selection():
    actions = _Actions()
    actions._secondary = True
    actions.shift_pressed_ref.current = True
    repeat = build_editor_key(_make_ctx(actions))["on_key_repeat"]
    repeat(_evt("ArrowRight"))
    assert actions.calls == ["extend_selection_right"]


def test_repeat_outward_sel_active():
    actions = _Actions()
    actions.outward_sel = (0, 0, 0, 2)
    ctx = _make_ctx(actions)
    repeat = build_editor_key(ctx)["on_key_repeat"]
    actions.shift_pressed_ref.current = True
    repeat(_evt("ArrowLeft"))
    assert actions.calls == ["extend_outward_left"]
    actions.shift_pressed_ref.current = False
    repeat(_evt("ArrowLeft"))
    assert actions.calls == ["extend_outward_left", "clear_outward_sel"]


def test_repeat_browse_mode_ignored():
    actions = _Actions(cursor_li=None)
    repeat = build_editor_key(_make_ctx(actions))["on_key_repeat"]
    repeat(_evt("ArrowLeft"))
    assert actions.calls == []


def test_repeat_native_field_focused_ignored():
    actions = _Actions()
    actions.code_focus_ref.current = 0
    repeat = build_editor_key(_make_ctx(actions))["on_key_repeat"]
    repeat(_evt("ArrowRight"))
    assert actions.calls == []


def test_repeat_non_arrow_ignored():
    actions = _Actions()
    repeat = build_editor_key(_make_ctx(actions))["on_key_repeat"]
    repeat(_evt("Backspace"))
    repeat(_evt("a"))
    assert actions.calls == []


def test_repeat_vertical_keys_not_handled_by_editor():
    """上/下 key_repeat 不由 editor 处理（KeyDispatcher 定时器驱动）。"""
    actions = _Actions()
    repeat = build_editor_key(_make_ctx(actions))["on_key_repeat"]
    repeat(_evt("ArrowDown"))
    repeat(_evt("ArrowUp"))
    assert actions.calls == []


def test_editor_keyup_stops_arrow_repeat():
    """释放方向键 → editor _on_key_up 设 arrow_repeat_ref=None（停止 KeyDispatcher 定时器）。"""
    actions = _Actions()
    ctx = _make_ctx(actions)
    ctx.arrow_repeat_ref.current = True  # 模拟定时器运行中
    cbs = build_editor_key(ctx)
    cbs["on_key_up"](_evt("ArrowDown"))
    assert ctx.arrow_repeat_ref.current is None


def test_editor_keyup_modifier_does_not_stop():
    """松开 Shift 等修饰键不停止上/下重复。"""
    actions = _Actions()
    ctx = _make_ctx(actions)
    ctx.arrow_repeat_ref.current = True
    cbs = build_editor_key(ctx)
    cbs["on_key_up"](_evt("Shift"))
    assert ctx.arrow_repeat_ref.current is True  # 仍在运行


# ================ 上/下：KeyDispatcher 自驱动定时器 ================

def _make_dispatcher_ctx(start_li=0):
    """构造 AppContext mock + 模拟真实移动的 actions。"""
    actions = _Actions(cursor_li=start_li)
    pos = {"li": start_li, "base": 0}

    def _move_down():
        actions.calls.append("move_down")
        pos["li"] += 1
        pos["base"] += 1
        actions.cursor_li = pos["li"]
        actions.cursor_ref.current.base = pos["base"]

    def _move_up():
        actions.calls.append("move_up")
        pos["li"] = max(0, pos["li"] - 1)
        pos["base"] = max(0, pos["base"] - 1)
        actions.cursor_li = pos["li"]
        actions.cursor_ref.current.base = pos["base"]

    actions.move_down = _move_down
    actions.move_up = _move_up
    arrow_ref = FakeRef(None)
    return actions, arrow_ref


def _make_dispatcher(actions, arrow_ref):
    from views.key_bindings import KeyDispatcher
    d = KeyDispatcher(
        shortcut_mgr=type("M", (), {
            "get": lambda self, layer: {},
            "inline_format_combos": lambda self: {},
            "first_conflict_target": lambda self: (None, None),
        })(),
        actions_ref=FakeRef(actions),
        clipboard_ref=FakeRef(None),
        page_ref=FakeRef(None),
        paste_old_draft=FakeRef(""),
        app_callbacks={},
        arrow_repeat_ref=arrow_ref,
    )
    return d


def test_dispatcher_vertical_timer_continuous_until_stop():
    """上/下 KeyDown → KeyDispatcher 启动定时器 → 持续移动 → KeyUp 停止。"""
    actions, arrow_ref = _make_dispatcher_ctx()
    d = _make_dispatcher(actions, arrow_ref)

    async def _drive():
        with patch.object(KeyDispatcher, "_REPEAT_DELAY", 0.01), \
             patch.object(KeyDispatcher, "_REPEAT_INTERVAL", 0.01):
            d._start_arrow_repeat("arrowdown")
            await asyncio.sleep(0.08)
            d._stop_arrow_repeat()
            await asyncio.sleep(0.02)
            assert arrow_ref.current is None

    asyncio.run(_drive())
    assert actions.calls.count("move_down") >= 3


def test_dispatcher_vertical_timer_stops_at_boundary():
    """到达文档边界（无位移连续 3 次）→ 自停。"""
    actions, arrow_ref = _make_dispatcher_ctx()
    actions.move_down = lambda: actions.calls.append("move_down")  # no-op：不移动
    d = _make_dispatcher(actions, arrow_ref)

    async def _drive():
        with patch.object(KeyDispatcher, "_REPEAT_DELAY", 0.01), \
             patch.object(KeyDispatcher, "_REPEAT_INTERVAL", 0.01):
            d._start_arrow_repeat("arrowdown")
            await asyncio.sleep(0.1)
            assert arrow_ref.current is None

    asyncio.run(_drive())


def test_dispatcher_vertical_timer_stopped_by_other_key():
    """其他键 KeyDown → 停止。"""
    actions, arrow_ref = _make_dispatcher_ctx()
    d = _make_dispatcher(actions, arrow_ref)

    async def _drive():
        with patch.object(KeyDispatcher, "_REPEAT_DELAY", 0.01), \
             patch.object(KeyDispatcher, "_REPEAT_INTERVAL", 0.01):
            d._start_arrow_repeat("arrowdown")
            await asyncio.sleep(0.04)
            moves_before = actions.calls.count("move_down")
            d._stop_arrow_repeat()
            await asyncio.sleep(0.03)
            assert actions.calls.count("move_down") == moves_before

    asyncio.run(_drive())
