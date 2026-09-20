"""Git 面板 / 差异视图 / 分支面板的渲染层装配测试（真实 App 组件树）。

守卫的是「装配断链」这类静默回归：控制器、视图、渲染树三者之间的接线一旦漏掉
一环，功能就是「点了没反应」——单元测试全绿但用户什么也看不到。因此这里断言
的是**渲染树里实际存在的控件**：

1. 侧边栏切到 Git 面板时真的渲染出 ``GitPanel``
2. 差异视图与分支面板作为覆盖层挂在根 Stack 上（默认不可见）
3. 底部状态栏拿到 Git 段的回调（分支 chip / 待提交计数 → 面板）
4. 活动栏「源代码管理」按钮点击后写入 sidebar_panel 设置
5. 全局菜单里存在 Git 组（面板 / 提交 / 同步 / 分支 / 历史 / 初始化）

夹具见 ``tests/harness.py``：进程内渲染真实 ``App``，不需要 Flutter 前端。
Git 侧的真实异步任务由控制器的集成测试（``tests/test_git_controller.py``）覆盖，
这里只关心「有没有接上」。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import flet as ft
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flet.components.component import Component

import config.settings as cs
from app import App
from tests.harness import RenderHarness, walk

_SANDBOX = Path(__file__).resolve().parent / ".git-render-sandbox"
_SANDBOX.mkdir(parents=True, exist_ok=True)
_SETTINGS = _SANDBOX / "settings.json"


def _write_settings(**overrides) -> None:
    payload = {
        "sidebar_open": True,
        "sidebar_panel": "git",
        "theme": "light",
        **overrides,
    }
    _SETTINGS.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def harness(monkeypatch):
    monkeypatch.setattr(cs, "SETTINGS_PATH", str(_SETTINGS))

    def _render():
        h = RenderHarness()
        h.render(App)
        return h

    created: list[RenderHarness] = []

    def _make():
        h = _render()
        created.append(h)
        return h

    try:
        yield _make
    finally:
        for h in created:
            h.dispose()


def _components(h: RenderHarness, name: str) -> list:
    """按函数名取组件实例。

    Flet 1.0 把 ``@ft.component`` 装饰的函数包成统一的 ``Component`` 类，实例身份
    只能靠 ``.fn.__name__`` 区分（``isinstance`` 对函数式组件无效）。
    """
    return [
        n
        for n in walk(h.tree)
        if isinstance(n, Component) and getattr(n.fn, "__name__", "") == name
    ]


def _texts(h: RenderHarness) -> list[str]:
    out: list[str] = []
    for n in walk(h.tree):
        value = getattr(n, "value", None)
        if isinstance(n, ft.Text) and isinstance(value, str):
            out.append(value)
    return out


# ---------------------------------------------------------------------------
# 面板挂载
# ---------------------------------------------------------------------------


def test_git_panel_is_rendered_with_full_props(harness):
    """切到 Git 面板时不仅要渲染出组件，状态 / 动作 props 也必须齐备。

    props 由 ``git_panel_props(ctx)`` 组装；少一个键就是面板里某个按钮点了没反应。
    """
    _write_settings(sidebar_panel="git")
    h = harness()
    panels = _components(h, "GitPanel")
    assert panels, "侧边栏切到 git 时未渲染 GitPanel（面板入口断链）"

    state, actions = panels[0].args[0], panels[0].args[1]
    for key in (
        "available", "version", "workspace", "root", "status", "error", "busy",
        "view", "branches", "history", "history_has_more", "history_loading",
        "history_filter", "commit_details", "expanded_commits", "commit_push",
        "commit_seq", "commit_ref", "active_path", "native_ref",
    ):
        assert key in state, f"GitPanel 状态缺少 {key}"
    for name in (
        "refresh", "init_repo", "set_view", "stage", "unstage", "stage_all",
        "unstage_all", "discard", "discard_all", "open_diff", "commit", "pull",
        "push", "fetch", "undo_commit", "open_branch_dialog", "load_history",
        "load_more_history", "toggle_commit", "set_history_filter",
        "clear_history_filter", "history_file_diff", "open_in_editor",
        "jump_to_conflict", "toggle_commit_push", "abort_operation",
        "open_file_history",
    ):
        assert name in actions, f"GitPanel 动作缺少 {name}"
    assert state["view"] in ("changes", "history")


def test_git_panel_is_absent_for_other_sidebars(harness):
    _write_settings(sidebar_panel="search")
    h = harness()
    assert not _components(h, "GitPanel")


def test_overlays_are_mounted_but_hidden(harness):
    """差异视图 / 分支面板是覆盖层：必须在树里（否则永远打不开），初始不可见。"""
    _write_settings()
    h = harness()
    diffs = _components(h, "GitDiffView")
    menus = _components(h, "GitBranchMenu")
    assert diffs and menus
    assert all(c.kwargs.get("visible") is False for c in diffs)
    assert all(c.kwargs.get("open_state") is False for c in menus)


def test_status_bar_receives_git_callbacks(harness):
    _write_settings()
    h = harness()
    bars = _components(h, "StatusBar")
    assert bars, "未渲染 StatusBar"
    kw = bars[0].kwargs
    assert kw.get("on_click_branch") is not None, "状态栏分支 chip 未接线"
    assert kw.get("on_click_git") is not None, "状态栏待提交计数未接线"
    # 没有仓库数据时不应渲染虚假的分支名
    assert kw.get("git_branch") is None


# ---------------------------------------------------------------------------
# 入口交互
# ---------------------------------------------------------------------------


def _git_activity_button(h: RenderHarness):
    """活动栏「源代码管理」按钮（tooltip 前缀匹配，含变更数角标时的文案）。"""
    for n in walk(h.tree):
        tip = getattr(n, "tooltip", None)
        if isinstance(tip, str) and tip.startswith("源代码管理") and getattr(n, "on_click", None):
            return n
    return None


def test_activity_bar_git_button_opens_git_panel(harness):
    _write_settings(sidebar_panel="search")
    h = harness()
    btn = _git_activity_button(h)
    assert btn is not None, "活动栏缺少源代码管理按钮"

    h.interact(btn.on_click, None)  # 活动栏回调签名带事件参数
    settings = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    assert settings["sidebar_panel"] == "git"
    assert settings["sidebar_open"] is True


def test_activity_bar_git_button_toggles_sidebar_when_active(harness):
    """VSCode 直觉：再点当前图标 = 收起侧边栏（而不是重新打开面板）。"""
    _write_settings(sidebar_panel="git", sidebar_open=True)
    h = harness()
    btn = _git_activity_button(h)
    h.interact(btn.on_click, None)
    settings = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    assert settings["sidebar_open"] is False


def test_global_menu_has_git_group(harness):
    _write_settings()
    h = harness()
    labels = _texts(h)
    for item in (
        "Git", "源代码管理", "提交暂存区", "提交所有更改", "撤销上次提交",
        "获取", "拉取", "推送", "分支管理...", "查看历史", "初始化仓库",
    ):
        assert item in labels, f"全局菜单缺少「{item}」"
