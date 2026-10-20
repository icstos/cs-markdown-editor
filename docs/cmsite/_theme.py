"""站点设计 token：颜色 / 字号 / 间距 / 宽度 / 渐变。

与主程序的关系
--------------
配色与字体**直接复用 `styles.py`**（`Colors` / `FONT_MAIN` / `FONT_MONO`），
因此网站和编辑器不会出现「两套蓝」。本模块只补网站特有的排版常量。

⚠️ 一个必须记住的坑：Flet 的 8 位十六进制颜色是 **`#AARRGGBB`（alpha 在前）**，
不是 CSS 的 `#RRGGBBAA`。写成后者会让「6% 透明度的蓝」变成「8% 透明度的绿」——
肉眼看到的是渐变发绿，而不是报错。所有半透明色一律经 `tint()` 生成。
"""

from __future__ import annotations

import flet as ft

from styles import FONT_MAIN, FONT_MONO, Colors, Radius, Spacing, get_colors

__all__ = [
    "FONT_MAIN",
    "FONT_MONO",
    "Colors",
    "Layout",
    "Radius",
    "Spacing",
    "Type",
    "current_colors",
    "hero_gradient",
    "is_dark",
    "only_border",
    "tint",
]


def current_colors() -> Colors:
    """渲染期取色：跟随 `page.theme_mode`；非渲染上下文回退亮色。

    必须在组件体内调用（与主程序 `styles._current_colors()` 同语义），
    这样切主题的那一帧就已经是新颜色。
    """
    page = ft.context.page
    if page is not None:
        return get_colors(page.theme_mode)
    return get_colors(ft.ThemeMode.LIGHT)


def is_dark(c: Colors | None = None) -> bool:
    """按背景亮度判断明暗（不额外维护主题标记，避免两处状态漂移）。"""
    return _luminance((c or current_colors()).bg) < 0.5


def tint(color: str, opacity: float) -> str:
    """给颜色加透明度，返回 Flet 认的 `#AARRGGBB`。

    Args:
        color: `#RGB` 或 `#RRGGBB`。
        opacity: 0–1，越大越不透明。
    """
    h = color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) != 6:
        return color  # 命名色 / 已带 alpha 的值：原样返回
    alpha = round(max(0.0, min(1.0, opacity)) * 255)
    return f"#{alpha:02X}{h}"


def only_border(
    *,
    top: ft.BorderSide | None = None,
    bottom: ft.BorderSide | None = None,
    left: ft.BorderSide | None = None,
    right: ft.BorderSide | None = None,
) -> ft.Border:
    """单边边框（`ft.border.all` 之外只想要一条线时用）。"""
    none = ft.BorderSide.none()
    return ft.Border(
        top=top or none, right=right or none, bottom=bottom or none, left=left or none
    )


class Layout:
    """站点布局常量（4px 基准网格，与 `styles.Spacing` 同源节奏）。"""

    #: 正文内容最大宽度。浏览器里超过 ~120 字符/行就很难读了。
    MAX_W = 1120
    #: 宽屏左右留白
    GUTTER = 32
    #: 窄屏左右留白
    GUTTER_NARROW = 16
    #: 低于此宽度进入紧凑模式（导航变简、留白收紧）
    COMPACT_W = 900
    #: 顶栏高度
    NAV_H = 60
    #: 区块之间的纵向节奏
    SECTION_GAP = 76
    #: 卡片网格间距
    GRID = 16
    #: 首屏纵向留白
    HERO_PAD = 72

    @staticmethod
    def gutter(viewport: int) -> int:
        """按视口宽度选留白。"""
        return Layout.GUTTER_NARROW if viewport < Layout.COMPACT_W else Layout.GUTTER

    @staticmethod
    def content_width(viewport: int) -> int:
        """内容宽度 = min(最大宽度, 视口 - 两侧留白)。

        Web 页面必须按视口算宽：写死 `width=1120` 在 800px 窗口里会横向溢出，
        而 Flet 的 `Container` 没有 max-width 语义。视口宽度由外壳组件经
        `page.on_resize` 维持，作为参数显式传到每个区块。
        """
        if viewport <= 0:
            return Layout.MAX_W
        usable = viewport - 2 * Layout.gutter(viewport)
        return max(280, min(Layout.MAX_W, usable))


class Type:
    """字号阶梯（逻辑 px）。"""

    HERO = 44
    H2 = 30
    H3 = 21
    H4 = 16
    LEAD = 17
    BODY = 15
    SMALL = 13.5
    TINY = 12
    STAT = 32
    CODE = 12.5


def hero_gradient(c: Colors) -> ft.LinearGradient:
    """首屏背景渐变：在页面底色附近取蓝色 / 紫色两端的极淡着色。

    注意别用 `heading_colors[5]`（亮色主题下是暖棕 #B54708）——它会让首屏
    泛出一层"桃色"，与蓝色的品牌色打架。这里明确只取蓝与紫。
    """
    strength = 0.10 if is_dark(c) else 0.05
    violet = c.heading_colors.get(4, c.link)
    return ft.LinearGradient(
        begin=ft.Alignment.TOP_LEFT,
        end=ft.Alignment.BOTTOM_RIGHT,
        colors=[tint(c.link, strength), c.bg, tint(violet, strength * 0.8)],
    )


def _luminance(hex_color: str) -> float:
    """sRGB 相对亮度（0–1）。"""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) < 6:
        return 1.0
    h = h[-6:]  # 容忍 #AARRGGBB
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
