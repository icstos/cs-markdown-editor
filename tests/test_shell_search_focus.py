"""侧边栏搜索唤起（Ctrl+Shift+F / Ctrl+F / Ctrl+H）的端到端回归测试。

背景（本轮修复的真实缺陷）：
``build_settings_controller.update_setting`` 原以 ``settings_ref.current`` 为基准
重建整个 settings dict，但 ``settings_ref`` 只在渲染期刷新。于是一次 tick 内连续
调用（Ctrl+Shift+F 要连写 sidebar_open + sidebar_panel + search_folder 三个键）
时，后一次调用基于渲染期旧快照重建，把前一次写入覆盖掉，最终只有最后一个键生效
→ 侧边栏没展开、面板没切换、搜索框不聚焦。

修复：``update_setting`` 写入后立即回填 ``settings_ref.current``，使 ref 成为
「含未提交写入」的最新值；所有读取统一走 ``latest_settings()``。

测试不依赖 Flet 前端：见 ``tests/harness.py``。
"""

import sys
import types
from pathlib import Path

import flet as ft
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config.settings as cs
from app import App
from app._settings_controller import build_settings_controller
from tests.harness import RenderHarness, walk
from tests.test_boot_smoke import press, read_settings

_SANDBOX = Path(__file__).resolve().parent / ".search-sandbox"
_SANDBOX.mkdir(parents=True, exist_ok=True)
_SETTINGS_FILE = _SANDBOX / "settings.json"


@pytest.fixture
def app_harness(monkeypatch):
    """渲染真实 App；设置文件隔离到本地沙箱且每个用例从默认设置起步。"""
    monkeypatch.setattr(cs, "SETTINGS_PATH", str(_SETTINGS_FILE))
    _SETTINGS_FILE.unlink(missing_ok=True)  # 沙箱按用例重置，避免共享文件串味
    h = RenderHarness()
    h.render(App)
    try:
        yield h
    finally:
        h.dispose()


def dispatcher(h: RenderHarness):
    """取当前 page 上绑定的 KeyDispatcher（经 _handler 闭包内的 AppContext 读取）。"""
    handler = h.page.on_keyboard_event
    ctx = next(
        c.cell_contents for c in handler.__closure__
        if type(c.cell_contents).__name__ == "AppContext"
    )
    d = ctx.dispatcher_ref.current
    assert d is not None, "dispatcher_ref 未被渲染期同步"
    return d


def sidebar_props(h: RenderHarness) -> dict:
    """当前渲染中 Sidebar 组件收到的 props。"""
    from flet.components.component import Component

    sides = [
        n for n in walk(h.tree)
        if isinstance(n, Component) and n.fn.__name__ == "Sidebar"
    ]
    assert sides, "渲染树中未找到 Sidebar 组件"
    return sides[0].kwargs


def search_box_hints(h: RenderHarness) -> list[str]:
    return [n.hint_text for n in walk(h.tree) if isinstance(n, ft.TextField)]


# ---------------- Ctrl+Shift+F：侧边栏文件夹全局搜索 ----------------


def test_ctrl_shift_f_opens_sidebar_search_panel(app_harness):
    """Ctrl+Shift+F 一次触发即完成：展开侧边栏 + 切 search 面板 + 开启文件夹范围。"""
    press(app_harness, "F", ctrl=True, shift=True)

    props = sidebar_props(app_harness)
    assert props["sidebar_open"] is True
    assert props["active_panel"] == "search"

    persisted = read_settings()
    assert persisted["sidebar_open"] is True
    assert persisted["sidebar_panel"] == "search"
    assert persisted["search_folder"] is True

    # 文件夹范围生效的直接证据：搜索框占位文案切换为跨文件版本
    assert "在文件夹中查找…" in search_box_hints(app_harness)


def test_ctrl_shift_f_focuses_search_input(app_harness, monkeypatch):
    """Ctrl+Shift+F 后搜索框被聚焦，用户可直接输入。"""
    focused: list[str] = []

    async def fake_focus(self, *args, **kwargs):
        focused.append(self.hint_text)

    monkeypatch.setattr(ft.TextField, "focus", fake_focus)

    press(app_harness, "F", ctrl=True, shift=True)
    assert focused == ["在文件夹中查找…"]


def test_ctrl_shift_f_refocuses_when_already_open(app_harness, monkeypatch):
    """已在搜索面板时再次按 Ctrl+Shift+F 仍重新聚焦（序号递增驱动 effect）。"""
    focused: list[str] = []

    async def fake_focus(self, *args, **kwargs):
        focused.append(self.hint_text)

    monkeypatch.setattr(ft.TextField, "focus", fake_focus)

    press(app_harness, "F", ctrl=True, shift=True)
    first_seq = sidebar_props(app_harness)["search_focus_seq"]
    press(app_harness, "F", ctrl=True, shift=True)
    second_seq = sidebar_props(app_harness)["search_focus_seq"]

    assert second_seq > first_seq
    assert len(focused) == 2, "重复触发必须再次聚焦（而非被去重吞掉）"


def test_ctrl_f_keeps_document_overlay_and_leaves_sidebar_alone(app_harness):
    """Ctrl+F 是文档内浮层搜索：不写设置、不改侧边栏面板（与 Ctrl+Shift+F 分流）。"""
    before = read_settings()
    press(app_harness, "F", ctrl=True)
    assert read_settings() == before

    from flet.components.component import Component

    overlay = next(
        n for n in walk(app_harness.tree)
        if isinstance(n, Component) and n.fn.__name__ == "FloatingSearch"
    )
    assert overlay.kwargs["open"] is True


def test_ctrl_h_opens_sidebar_search_with_replace_bar(app_harness):
    """Ctrl+H 同样连写 3 个键（含 replace_expanded 翻转），验证多写合成。"""
    before = read_settings().get("search_replace_expanded", False)
    press(app_harness, "H", ctrl=True)

    props = sidebar_props(app_harness)
    assert props["sidebar_open"] is True
    assert props["active_panel"] == "search"
    assert read_settings()["search_replace_expanded"] is not before


# ---------------- KeyDispatcher 装配完整性 ----------------


def test_keyboard_callbacks_are_not_context_defaults(app_harness):
    """`_GLOBAL_ACTIONS` 表里每个 cb 型动作都必须装配到分发器上。

    回归守护：KeyDispatcher 构造期立即取出 ``app_callbacks`` 的值，若某个
    ``ctx.<name>`` 在 ``build_keyboard`` 之后才被赋值，分发器就永久持有
    ``AppContext`` 的默认 no-op —— 该快捷键完全无反应且不报错
    （Ctrl+H / Alt+Enter / Ctrl+Alt+Enter / Ctrl+F 回退路径曾整体失效）。
    """
    from app._context import AppContext
    from views.key_bindings import _GLOBAL_ACTIONS

    d = dispatcher(app_harness)
    missing = []
    for name, _default, style in _GLOBAL_ACTIONS:
        if style != "cb":
            continue
        fn = d._app_callbacks.get(name)
        field = AppContext.__dataclass_fields__.get(name)
        if fn is None or (field is not None and fn is field.default):
            missing.append(name)
    assert not missing, f"以下全局动作未真正装配到 KeyDispatcher：{missing}"


# ---------------- update_setting 连续写入合成（根因单测） ----------------


def _minimal_settings_ctx(initial: dict):
    """构造 update_setting 所需的最小 ctx（settings_ref 模拟渲染期快照）。"""
    ref = types.SimpleNamespace(current=dict(initial))
    committed: dict = {}

    def set_settings(new_value):
        # 真实 use_state：立即更新 hook 值，但不刷新 settings_ref（渲染期才刷）
        committed.clear()
        committed.update(new_value)

    ctx = types.SimpleNamespace(
        settings=dict(initial),
        settings_ref=ref,
        set_settings=set_settings,
        apply_content_layout=lambda: None,
    )
    return ctx, ref, committed


def test_update_setting_composes_consecutive_writes(monkeypatch):
    """一次 tick 内连写多个键必须全部保留（修复前只有最后一个键存活）。"""
    monkeypatch.setattr(cs, "SETTINGS_PATH", str(_SETTINGS_FILE))
    initial = {"sidebar_open": False, "sidebar_panel": "files", "search_folder": False}
    ctx, ref, committed = _minimal_settings_ctx(initial)
    update_setting = build_settings_controller(ctx)["update_setting"]

    update_setting("sidebar_open", True)
    update_setting("sidebar_panel", "search")
    update_setting("search_folder", True)

    expected = {"sidebar_open": True, "sidebar_panel": "search", "search_folder": True}
    assert committed == expected, "hook 值丢失了前序写入（末次生效覆盖）"
    assert dict(ref.current) == expected


def test_update_setting_starts_from_latest_ref_value(monkeypatch):
    """基准取 ref 最新值：外部（异步任务）写入后不会被旧快照覆盖。"""
    monkeypatch.setattr(cs, "SETTINGS_PATH", str(_SETTINGS_FILE))
    ctx, ref, committed = _minimal_settings_ctx({"workspace_folder": None})
    update_setting = build_settings_controller(ctx)["update_setting"]

    ref.current = {"workspace_folder": "/tmp/ws"}
    update_setting("sidebar_open", True)

    assert committed == {"workspace_folder": "/tmp/ws", "sidebar_open": True}
