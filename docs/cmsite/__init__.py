"""文档站点的公开入口。

对外只有三样：`SiteShell`（根组件）、`main`（页面配置）、`inject_runtime_facts`。
其余模块以下划线开头，属内部实现。

运行时事实注入
--------------
版本号与运行环境摘要来自 `config.app_meta`——与编辑器「关于」页**同源**。
放在这里而不是 `_content`，是为了让内容层保持零依赖：它在没有应用环境时
也能被导入与断言。
"""

from __future__ import annotations

import flet as ft

from cmsite import _content
from cmsite._shell import SiteShell
from styles import get_colors

__all__ = [
    "SiteShell",
    "inject_runtime_facts",
    "main",
    "set_start_anchor",
    "set_start_size",
    "set_start_theme",
]


def set_start_anchor(anchor: str | None) -> None:
    """指定打开站点时滚动到的区块（None = 首屏）。

    `SiteShell` 在挂载期读这个值：既用于 `python docs/main.py --start docs`
    这种调试 / 演示用法，也为将来的 URL 深链留了口子。
    """
    _content.START_ANCHOR = anchor


def set_start_theme(theme: str) -> None:
    """指定打开时的主题（"light" / "dark"）。"""
    _content.START_THEME = "dark" if theme == "dark" else "light"


def set_start_size(width: int, height: int) -> None:
    """指定桌面窗口的初始尺寸（也用于预览窄屏布局）。"""
    _content.START_SIZE = (max(360, width), max(320, height))


def inject_runtime_facts() -> None:
    """把应用元信息写进 `_content.RUNTIME`（读不到就保留静态回退值）。"""
    try:
        from config.app_meta import app_version, runtime_summary
    except Exception:  # 站点被单独拷走 / 未安装主程序：用回退值
        return
    _content.RUNTIME["version"] = app_version()
    _content.RUNTIME["runtime"] = runtime_summary()


async def main(page: ft.Page) -> None:
    """页面配置：字体、主题、滚动与首帧。

    Web 与桌面共用这一个入口——`ft.run(..., view=AppView.WEB_BROWSER)` 走浏览器，
    `view=AppView.FLET_APP` 走桌面窗口，站点本身不关心跑在哪。
    """
    inject_runtime_facts()

    # 与主程序同一套随包字体（assets/fonts/），保证站点与编辑器观感一致。
    page.fonts = {
        "Alibaba": "fonts/AlibabaPuHuiTi-3-55-Regular.otf",
        "NotoSansMonoCJKsc": "fonts/NotoSansMonoCJKsc-Regular.otf",
    }
    page.theme = ft.Theme(
        font_family="Alibaba",
        color_scheme=ft.ColorScheme(
            surface="#FAFBFC", on_surface="#1F2329", primary="#1677FF"
        ),
    )
    page.dark_theme = ft.Theme(
        font_family="Alibaba",
        color_scheme=ft.ColorScheme(
            surface="#14161A", on_surface="#E6EDF3", primary="#6BA0F5"
        ),
    )
    page.theme_mode = (
        ft.ThemeMode.DARK if _content.START_THEME == "dark" else ft.ThemeMode.LIGHT
    )
    page.title = f"{_content.APP_NAME} · {_content.APP_TAGLINE}"
    # 站点自己管滚动（顶栏固定 + 内容区滚动），页面级滚动必须关掉：
    # 只要 Page 可滚动，内容区就变成了"整页一起滚"，顶栏会跟着滚出去——
    # 而 `scroll_to(scroll_key=...)` 也会落到外层滚动容器上。
    page.scroll = None
    page.padding = 0
    page.spacing = 0
    page.bgcolor = get_colors(ft.ThemeMode.LIGHT).bg
    page.window.width = _content.START_SIZE[0]
    page.window.height = _content.START_SIZE[1]
    page.window.min_width = 420
    page.render(SiteShell)
