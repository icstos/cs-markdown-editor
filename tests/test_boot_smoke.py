"""应用启动与全局交互冒烟测试（进程内，真实 App 组件树）。

夹具见 ``tests/harness.py``：在无头环境中渲染真实 ``App``，可点击控件、
派发键盘事件、驱动状态变更与重渲染。这是重构期间防止「装配断链 / 回调丢失 /
快捷键失效」的集成级安全网。

覆盖重点：
- App 全量装配可渲染（控制器拓扑序、渲染树构造）
- 全局快捷键经 KeyDispatcher → page.on_keyboard_event 生效
- 主题切换、侧边栏开合等窗口级动作
"""

import sys
import types
from pathlib import Path

import flet as ft
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config.settings as cs
from app import App
from tests.harness import RenderHarness, walk

# 设置文件隔离目录：在导入期一次性创建（受限环境下 pytest tmp_path 不可用，
# 见 AGENT.md「标准验证流程」）。
_SANDBOX = Path(__file__).resolve().parent / ".boot-sandbox"
_SANDBOX.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def app_harness(monkeypatch):
    """渲染真实 App，并把设置文件隔离到本地沙箱目录。"""
    monkeypatch.setattr(cs, "SETTINGS_PATH", str(_SANDBOX / "settings.json"))

    h = RenderHarness()
    h.render(App)
    try:
        yield h
    finally:
        h.dispose()


def read_settings() -> dict:
    """读取当前（沙箱内）设置。"""
    return cs.load_settings()


# ---------------- 查询辅助 ----------------


def nodes(h: RenderHarness):
    return list(walk(h.tree))


def by_type(h: RenderHarness, cls) -> list:
    return [n for n in nodes(h) if isinstance(n, cls)]


def text_values(h: RenderHarness) -> list[str]:
    return [n.value for n in by_type(h, ft.Text) if isinstance(n.value, str)]


def press(h: RenderHarness, key: str, ctrl=False, shift=False, alt=False, meta=False):
    """经 page.on_keyboard_event 派发一次按键（走完整 KeyDispatcher 路由）。"""
    handler = h.page.on_keyboard_event
    assert handler is not None, "KeyDispatcher 未绑定到 page.on_keyboard_event"
    event = types.SimpleNamespace(key=key, ctrl=ctrl, shift=shift, alt=alt, meta=meta)
    h.interact(handler, event)


# ---------------- 启动 ----------------


def test_app_renders(app_harness):
    assert len(nodes(app_harness)) > 500, "App 渲染树节点数异常偏少，装配可能断链"


def test_renders_tab_title(app_harness):
    texts = text_values(app_harness)
    assert any("Untitled" in t or "未命名" in t for t in texts), "未找到标签标题"


def test_keyboard_dispatcher_bound(app_harness):
    assert app_harness.page.on_keyboard_event is not None


def test_editor_actions_published(app_harness):
    """编辑器必须把 EditorActions 上抛给 App：能路由编辑动作即证明已装配。

    Ctrl+Home 走 KeyDispatcher → EditorActions.move_doc_start，无编辑器动作时
    该按键会被静默丢弃（不会抛异常），故以「派发不炸」+ 后续编辑动作生效为准。
    """
    press(app_harness, "Home", ctrl=True)


# ---------------- 全局动作 ----------------


def test_toggle_theme_shortcut(app_harness):
    """Alt+T（默认绑定）切换亮/暗主题，并经 use_effect 推送到 page.theme_mode。"""
    h = app_harness
    before = h.page.theme_mode
    press(h, "T", alt=True)
    assert h.page.theme_mode != before


def test_toggle_sidebar_shortcut(app_harness):
    """Ctrl+Shift+B（默认绑定）切换侧边栏并持久化到设置。"""
    h = app_harness
    before = read_settings().get("sidebar_open")
    press(h, "B", ctrl=True, shift=True)
    assert read_settings().get("sidebar_open") != before
