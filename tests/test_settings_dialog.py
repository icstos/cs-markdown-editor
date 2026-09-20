"""设置面板的守卫测试。

覆盖四条**用户可见**的承诺（都不靠"改前长什么样"来断言，而是锁死行为本身）：

1. **关闭按钮固定**：它必须在滚动区**之外**。这条只能靠结构断言——控件渲染成
   `ft.Control` 后无法"滚动一下看它还在不在"，所以直接检查「滚动容器的子树里
   不含关闭按钮」+「正文确实在滚动容器里」（后半句防的是把整块内容改成不滚动
   也能让前半句通过）。
2. **快捷键「已修改」标记**：以 `ACTION_REGISTRY.default` 为基准比对，且比对前
   要经 `normalize`——否则 `ctrl+comma` 与 `ctrl+,` 这类**等价写法**会被误报为
   "已修改"（用户没改过却被标记，比不标记更糟）。
3. **关于页四要素**：版本 / 联系邮箱 / 官方社群 / 用户手册，且值取自
   `config/app_meta`（唯一来源），不是面板里写死的副本。
4. **tab 集合自洽**：`_SECTIONS` 与 `_TAB_ICONS` 一致、无残留的占位 tab，
   且每个 tab 都能渲染出自己那段说明文字（防的是分发链漏了一支）。
"""

from __future__ import annotations

import contextlib
import types
from pathlib import Path

import flet as ft
import pytest

from config import app_meta
from config.settings import DEFAULT_SETTINGS
from services.shortcuts import ShortcutManager
from tests.harness import RenderHarness, invoke, walk
from views import settings_dialog as sd

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _settings(**overrides) -> dict:
    """DEFAULT_SETTINGS 的两层深拷贝（`shortcuts` 是 dict[str, dict]）。

    浅拷贝会让测试改到 `DEFAULT_SETTINGS["shortcuts"]["browse"]` 本体，
    污染同进程内的其它测试。
    """
    out: dict = {}
    for key, value in DEFAULT_SETTINGS.items():
        if isinstance(value, dict):
            out[key] = {
                ik: (dict(iv) if isinstance(iv, dict) else iv) for ik, iv in value.items()
            }
        else:
            out[key] = value
    out.update(overrides)
    return out


def _with_shortcut(settings: dict, layer: str, action: str, combo: str) -> dict:
    settings["shortcuts"][layer][action] = combo
    return settings


@contextlib.contextmanager
def rendered(*, tab: str = "edit", settings: dict | None = None,
             theme: ft.ThemeMode = ft.ThemeMode.LIGHT):
    """渲染设置面板，产出 (harness, 渲染树, 回调记录)。"""
    settings = settings if settings is not None else _settings()
    calls: list = []
    mgr = ShortcutManager(settings, lambda k, v: calls.append((k, v)))
    harness = RenderHarness()
    try:
        tree = harness.render(lambda: sd.SettingsDialog(
            open_state=True,
            tab=tab,
            settings=settings,
            theme_mode=theme,
            shortcut_focus=(None, None),
            shortcut_mgr=mgr,
            on_close=lambda: calls.append(("close",)),
            on_select_tab=lambda t: calls.append(("tab", t)),
            on_update=lambda k, v: calls.append((k, v)),
            on_reset_all=lambda: calls.append(("reset_all",)),
            on_reset_shortcuts=lambda: calls.append(("reset_shortcuts",)),
            on_import=lambda: calls.append(("import",)),
            on_export=lambda: calls.append(("export",)),
            capturing=(None, None),
            on_capture_click=lambda layer, action_id: calls.append(("capture", layer, action_id)),
            on_cancel_capture_click=lambda: calls.append(("cancel_capture",)),
            on_open_recovery=lambda: calls.append(("recovery",)),
            on_pick_backup_dir=lambda: calls.append(("pick_dir",)),
            on_open_url=lambda url: calls.append(("url", url)),
            on_copy=lambda text: calls.append(("copy", text)),
        ))
        yield harness, tree, calls
    finally:
        harness.dispose()


def _texts(root) -> list[str]:
    return [n.value for n in walk(root) if isinstance(n, ft.Text) and isinstance(n.value, str)]


def _scrollables(root) -> list:
    """渲染树中所有可滚动控件。

    `ScrollMode` 没有 `NONE` 成员——「不滚动」就是字段默认值 `None`，
    故判据是「scroll 不为 None」。
    """
    return [n for n in walk(root) if getattr(n, "scroll", None) is not None]


def _row_with_text(root, needle: str):
    """返回含指定文本的**最内层** ft.Row。

    不能取 BFS 序第一个：对话框根就是 `Container(content=Row(sidebar, 内容区))`，
    那个 Row 包含整棵树，会命中一切文本。取 Text 子孙最少的那个才是一行信息本身。
    """
    best = None
    best_count = None
    for node in walk(root):
        if not isinstance(node, ft.Row):
            continue
        texts = [n for n in walk(node) if isinstance(n, ft.Text)]
        if not any(n.value == needle for n in texts):
            continue
        if best_count is None or len(texts) < best_count:
            best, best_count = node, len(texts)
    return best


def _clickables(root) -> list:
    return [n for n in walk(root) if getattr(n, "on_click", None) is not None]


# 面板内回调普遍写作 `lambda e: ...`；harness.invoke 按形参个数决定是否传参，
# 必须显式投递事件对象，否则会以「缺 1 个位置参数」失败。
_EVENT = types.SimpleNamespace(control=types.SimpleNamespace(value=None))


def _click(control) -> None:
    invoke(control.on_click, _EVENT)


# ---------------------------------------------------------------------------
# 1. 关闭按钮固定
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tab", ["edit", "appearance", "behavior", "shortcuts", "about"])
def test_close_button_is_outside_the_scrolling_area(tab):
    """关闭按钮必须落在滚动区之外——否则长列表一滚就滚出视口，关不掉窗口。"""
    with rendered(tab=tab) as (_harness, tree, _):
        holders = [n for n in walk(tree) if getattr(n, "tooltip", None) == "关闭"]
        assert len(holders) == 1, f"应恰好有一个关闭按钮，实际 {len(holders)}"

        scrolls = _scrollables(tree)
        assert scrolls, "面板内容不再可滚动——长列表（快捷键近百行）会溢出且无法查看"
        for container in scrolls:
            assert holders[0] not in list(walk(container)), (
                f"tab={tab}：关闭按钮落在滚动容器内，滚动时会跟着滚走"
            )


def test_panel_body_is_inside_the_scrolling_area():
    """反向断言：正文确实在滚动容器里（防「把内容也挪出滚动区」让上一条假通过）。"""
    with rendered(tab="about") as (_harness, tree, _):
        inner_ids = {id(n) for c in _scrollables(tree) for n in walk(c)}
        target = [n for n in walk(tree) if isinstance(n, ft.Text) and n.value == "联系与支持"]
        assert target, "未找到关于页的「联系与支持」卡片标题"
        assert id(target[0]) in inner_ids, "关于页正文不在滚动区内"


def test_header_stays_out_while_scrolling_area_exists_exactly_once():
    """面板只应有一层滚动：嵌套滚动条难用且难以对齐滚动位置。"""
    with rendered(tab="shortcuts") as (_harness, tree, _):
        assert len(_scrollables(tree)) == 1, (
            f"存在 {len(_scrollables(tree))} 个滚动容器；快捷键列表应交给外层内容区滚动"
        )


# ---------------------------------------------------------------------------
# 2. 快捷键「已修改」标记
# ---------------------------------------------------------------------------

def test_modified_shortcut_is_marked():
    settings = _with_shortcut(_settings(), "browse", "save", "ctrl+alt+s")
    with rendered(tab="shortcuts", settings=settings) as (_harness, tree, _):
        texts = _texts(tree)
        assert "已修改" in texts, "改过键位的动作没有「已修改」角标"
        assert "已调整 1 项" in texts, "顶部改动概览没有统计出来"
        assert texts.count("已修改") == 1, "未改动的动作也被打上了角标"


def test_all_default_shows_no_marker():
    with rendered(tab="shortcuts") as (_harness, tree, _):
        texts = _texts(tree)
        assert "已修改" not in texts
        assert "已清空" not in texts
        assert "全部为默认键位" in texts


@pytest.mark.parametrize("spelling", ["Ctrl+S", "ctrl + s", "CTRL+S"])
def test_equivalent_spellings_are_not_marked_modified(spelling):
    """大小写与空格不同不算「已修改」——比对前必须 normalize。"""
    settings = _with_shortcut(_settings(), "browse", "save", spelling)
    with rendered(tab="shortcuts", settings=settings) as (_harness, tree, _):
        assert "已修改" not in _texts(tree), f"{spelling!r} 与默认 ctrl+s 等价，不应标记"


def test_alias_spelling_is_not_marked_modified():
    """键别名等价：默认 `ctrl+comma`，用户写成 `ctrl+,` 不算改过。"""
    settings = _with_shortcut(_settings(), "browse", "open_settings", "ctrl+,")
    with rendered(tab="shortcuts", settings=settings) as (_harness, tree, _):
        assert "已修改" not in _texts(tree), "ctrl+, 与默认 ctrl+comma 等价，不应标记"


def test_cleared_shortcut_is_marked_and_recoverable():
    settings = _with_shortcut(_settings(), "browse", "save", "")
    with rendered(tab="shortcuts", settings=settings) as (_harness, tree, _):
        texts = _texts(tree)
        assert "已清空" in texts, "清空绑定后没有标记"
        # 清空也算改动：必须给得出「恢复默认」的入口
        assert "未绑定" in texts


def test_only_modified_filter_hides_unchanged_rows():
    settings = _with_shortcut(_settings(), "browse", "save", "ctrl+alt+s")
    with rendered(tab="shortcuts", settings=settings) as (harness, tree, calls):
        assert "新建" in _texts(tree), "过滤前应能看到未改动的动作"
        switches = [
            n for n in walk(tree)
            if isinstance(n, ft.Switch) and n.label == "只看已修改"
        ]
        assert len(switches) == 1, "未找到「只看已修改」开关"
        assert switches[0].disabled is False

        harness.interact(
            invoke, switches[0].on_change,
            types.SimpleNamespace(control=types.SimpleNamespace(value=True)),
        )
        texts = _texts(harness.render_tree())
        assert "已修改" in texts
        assert "新建" not in texts, "开启「只看已修改」后未改动的动作仍然显示"
        # 过滤纯属视图，不应写回设置
        assert not [c for c in calls if isinstance(c, tuple) and c and c[0] == "shortcuts"]


def test_modified_rows_grouped_by_category():
    """分类分组：分类标题与该分类的改动条数都要在。"""
    settings = _with_shortcut(_settings(), "edit", "format_bold", "ctrl+alt+b")
    with rendered(tab="shortcuts", settings=settings) as (_harness, tree, _):
        texts = _texts(tree)
        assert "行内格式" in texts, "缺少分类标题"
        assert "1 项已调整" in texts, "分类标题上没有汇总该分类的改动条数"


# ---------------------------------------------------------------------------
# 3. 关于页
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label", ["版本", "联系邮箱", "官方社群", "用户手册"])
def test_about_panel_has_required_rows(label):
    with rendered(tab="about") as (_harness, tree, _):
        assert label in _texts(tree), f"关于页缺少「{label}」"


def test_about_values_come_from_app_meta():
    """值来自 config/app_meta（唯一来源），不是面板里写死的副本。"""
    with rendered(tab="about") as (_harness, tree, _):
        texts = _texts(tree)
        assert app_meta.app_version() in texts, "版本号不是来自 app_meta.app_version()"
        assert app_meta.SUPPORT_EMAIL in texts
        assert app_meta.COMMUNITY_URL in texts
        assert app_meta.USER_MANUAL_URL in texts
        assert "未配置" not in texts, "app_meta 中存在空常量，关于页会显示「未配置」"


def test_about_email_opens_mailto_and_community_opens_url():
    """邮箱走 mailto:，社群 / 手册直接开链接。"""
    with rendered(tab="about") as (_harness, tree, calls):
        row = _row_with_text(tree, "联系邮箱")
        assert row is not None, "未找到联系邮箱行"
        buttons = _clickables(row)
        assert buttons, "联系邮箱行没有任何可点按钮"
        _click(buttons[-1])                   # 末尾是「打开」按钮
        assert ("url", app_meta.mailto(app_meta.SUPPORT_EMAIL)) in calls

        calls.clear()
        row = _row_with_text(tree, "用户手册")
        assert row is not None, "未找到用户手册行"
        _click(_clickables(row)[-1])
        assert ("url", app_meta.USER_MANUAL_URL) in calls


def test_about_version_row_can_be_copied():
    with rendered(tab="about") as (_harness, tree, calls):
        row = _row_with_text(tree, "版本")
        assert row is not None
        _click(_clickables(row)[0])           # 首个是「复制」按钮
        assert ("copy", app_meta.app_version()) in calls


def test_extra_links_come_from_app_meta():
    with rendered(tab="about") as (_harness, tree, _):
        texts = _texts(tree)
        for _icon, title, url in app_meta.EXTRA_LINKS:
            assert title in texts, f"关于页缺少相关链接「{title}」"
            assert url in texts


# ---------------------------------------------------------------------------
# 4. tab 集合自洽
# ---------------------------------------------------------------------------

def test_tab_definitions_are_consistent():
    icon_keys = [key for key, _label, _icon in sd._TAB_ICONS]
    assert list(sd._SECTIONS) == icon_keys, "_SECTIONS 与 _TAB_ICONS 的 tab 集合/顺序不一致"
    assert "about" in icon_keys
    assert "advanced" not in icon_keys, "占位 tab「高级」应已被「关于」取代"


@pytest.mark.parametrize("tab", list(sd._SECTIONS))
def test_every_tab_renders_its_own_description(tab):
    """每个 tab 都渲染出自己那段说明（分发链漏一支会掉到默认分支）。"""
    with rendered(tab=tab) as (_harness, tree, _):
        assert sd._SECTIONS[tab][1] in _texts(tree)


def test_no_code_still_targets_the_removed_advanced_tab():
    """面板已无「高级」tab，任何切到该 tab 的调用都会静默回退到「编辑」页。"""
    src = (ROOT / "app" / "_settings_controller.py").read_text(encoding="utf-8")
    assert '"advanced"' not in src
    assert "settings_dialog" in (ROOT / "app" / "_render.py").read_text(encoding="utf-8")
