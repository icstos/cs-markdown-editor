"""搜索框外观的回归护栏：全应用统一的「圆角填底 + 聚焦描边」外壳。

为什么要有这组断言：搜索框的观感由几处**同时成立**的细节决定（外壳圆角、
填充底来自 `Colors.input_bg`、输入框自身不带 Material 边框下划线、浮动条
高度固定、内部按钮不用会撑高的 `ft.IconButton`）。任一处理解成旧写法，界面
就会退回「一条下划线 + 一排拥挤小按钮」的样子，但功能测试全绿——所以单独
钉住这些几何/取色不变量。

对应实现：
- `views/_widgets._search_shell` / `_search_box`（侧边栏搜索、替换、过滤框）
- `views/floating_search.FloatingSearch`（Ctrl+F 浮层）
"""

import json
import sys
from pathlib import Path

import flet as ft
import pytest
from flet.components.component import Component

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config.settings as cs
from app import App
from styles import Radius, get_colors
from tests.harness import RenderHarness, walk

_SANDBOX = Path(__file__).resolve().parent / ".search-style-sandbox"
_SANDBOX.mkdir(parents=True, exist_ok=True)
_SETTINGS_FILE = _SANDBOX / "settings.json"

# 浮层/侧边栏共用的两个高度与圆角（改动这里必须同步改实现，否则就是回归）
_SIDEBAR_INPUT_H = 30
_FLOATING_H = 40


@pytest.fixture
def app_harness(monkeypatch):
    """渲染真实 App，且直接停在「搜索」面板（省去按键步骤）。"""
    monkeypatch.setattr(cs, "SETTINGS_PATH", str(_SETTINGS_FILE))
    _SETTINGS_FILE.write_text(
        json.dumps({"sidebar_open": True, "sidebar_panel": "search"}),
        encoding="utf-8",
    )
    h = RenderHarness()
    h.render(App)
    try:
        yield h
    finally:
        h.dispose()


def floating_body(h: RenderHarness) -> object:
    """浮动搜索组件的渲染体（Container）。"""
    comp = next(
        n
        for n in walk(h.tree)
        if isinstance(n, Component) and n.fn.__name__ == "FloatingSearch"
    )
    body = comp._b
    assert isinstance(body, ft.Container), "浮动搜索的渲染根应是一个 Container"
    return body


def text_fields(root) -> list[ft.TextField]:
    return [n for n in walk(root) if isinstance(n, ft.TextField)]


def containers(root) -> list[ft.Container]:
    return [n for n in walk(root) if isinstance(n, ft.Container)]


def test_sidebar_search_field_has_rounded_filled_shell(app_harness):
    """侧边栏搜索框：30 高、圆角 LG、填底取自 Colors.input_bg（不再是下划线）。"""
    c = get_colors(ft.ThemeMode.LIGHT)
    fields = [
        f for f in text_fields(app_harness.tree) if f.hint_text == "在当前文档中查找…"
    ]
    assert fields, "搜索面板里没找到搜索输入框"

    shells = [
        ct
        for ct in containers(app_harness.tree)
        if ct.height == _SIDEBAR_INPUT_H
        and ct.border_radius == Radius.LG
        and ct.bgcolor == c.input_bg
    ]
    assert shells, "搜索框没有用统一的圆角填底外壳（views/_widgets._search_shell）"


def test_search_fields_do_not_use_underline_border(app_harness):
    """输入框自身必须是 NoInputBorder：底色/描边只由外层外壳提供。"""
    for f in text_fields(app_harness.tree):
        if f.hint_text in ("在当前文档中查找…", "替换为…", "过滤文件…"):
            assert isinstance(f.border, ft.NoInputBorder), (
                f"「{f.hint_text}」仍在用 Material 自带边框（{f.border!r}），"
                "会与外层外壳的圆角对不上"
            )


def test_floating_search_is_one_big_search_box(app_harness):
    """浮动搜索条：整条面板就是一个搜索框（surface 底 + XL 圆角 + 固定 40 高）。"""
    c = get_colors(ft.ThemeMode.LIGHT)
    body = floating_body(app_harness)
    assert body.height == _FLOATING_H
    assert body.border_radius == Radius.XL
    assert body.bgcolor == c.surface


def test_floating_search_avoids_material_icon_buttons(app_harness):
    """浮动条内不得出现 ft.IconButton：Material 40×40 的最小点击区会顶破面板高度。"""
    bad = [
        n
        for n in walk(floating_body(app_harness))
        if isinstance(n, ft.IconButton)
    ]
    assert not bad, f"浮动搜索条里出现了 {len(bad)} 个 IconButton（应用紧凑 Container）"


def test_floating_search_shows_compact_count(app_harness):
    """计数区恒存在（固定宽度，避免结果数变化时面板抖动）。"""
    body = floating_body(app_harness)
    widths = {
        ct.width for ct in containers(body) if ct.width is not None
    }
    assert 58 in widths, "浮动条的计数区宽度常量丢失（面板会随结果数抖动）"
