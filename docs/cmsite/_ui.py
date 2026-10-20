"""呈现原语：区块、卡片、徽标、键帽、按钮、网格。

约定
----
本模块全是**无状态纯函数**：不读 `use_state`、不持有闭包状态，因此可以被任意
区块直接调用，也能在进程内渲染夹具里单独断言。需要状态的小部件（复制按钮、
页签）在 `_widgets.py`。

所有尺寸都显式给值：站点的网格靠 `grid()` 自己算等宽列，避免依赖 Material
固有尺寸（`IconButton` 40 / `Dropdown` 48 这类会把行高顶开）。
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Sequence
from typing import Any

import flet as ft

from cmsite import _content as K
from cmsite._theme import (
    FONT_MAIN,
    FONT_MONO,
    Colors,
    Layout,
    Radius,
    Spacing,
    Type,
    current_colors,
    only_border,
    tint,
)

__all__ = [
    "badge",
    "card",
    "divider",
    "feature_card",
    "ghost_button",
    "grid",
    "icon",
    "icon_tile",
    "keycap",
    "on_event",
    "pill_tabs",
    "primary_button",
    "rich",
    "section",
    "section_header",
    "stat",
    "tag",
    "text",
]


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------


def on_event(fn: Callable[[], Any]) -> Callable[..., Any]:
    """把「无参回调」适配成 Flet 的事件处理器。

    Flet 1.x 派发事件时**必定传入一个 `Event`**（`handler(e)`）。所以写
    `def f()` / `lambda: ...` 会在点击瞬间崩掉——`TypeError: ... takes 0
    positional arguments but 1 was given`，而且只在真点的时候才炸，静态检查
    与结构渲染都看不见。反过来要求调用方写 `lambda e: ...` 又让每个调用点被迫
    关心一个用不到的参数。

    统一在这里吞掉：**本模块所有原语的回调都是无参的**。协程回调也支持（Flet
    会自行 await）。
    """
    if inspect.iscoroutinefunction(fn):

        async def _async(_event: Any = None) -> Any:
            return await fn()

        return _async

    def _sync(_event: Any = None) -> Any:
        return fn()

    return _sync


def icon(name: str, size: int = 18, color: str | None = None) -> ft.Icon:
    """按名字取 Material 图标。

    站点的内容层不 import flet（保持可无渲染断言），图标只存名字字符串，
    在这里一次性解析成 `ft.Icons` 成员——**必须用枚举而不是字符串**：
    字符串名在 1.0.4 里不会渲染成字形。
    """
    glyph = getattr(ft.Icons, name, None) or ft.Icons.CIRCLE
    return ft.Icon(glyph, size=size, color=color)


def text(
    value: str,
    *,
    size: float = Type.BODY,
    color: str | None = None,
    weight: ft.FontWeight | None = None,
    mono: bool = False,
    selectable: bool = False,
    max_lines: int | None = None,
) -> ft.Text:
    """统一文字样式（字体族、字号、颜色集中在这里，避免各处手写）。"""
    c = current_colors()
    return ft.Text(
        value,
        size=size,
        color=color or c.text,
        weight=weight,
        font_family=FONT_MONO if mono else FONT_MAIN,
        selectable=selectable,
        max_lines=max_lines,
        overflow=ft.TextOverflow.ELLIPSIS if max_lines else None,
    )


def divider(c: Colors | None = None) -> ft.Control:
    """1px 分隔线。"""
    colors = c or current_colors()
    return ft.Container(height=1, bgcolor=colors.border)


#: 反引号包起来的片段按行内代码渲染（只支持这一种行内标记，够用且不会写歪）
_CODE_SPAN = re.compile(r"`([^`]+)`")


def rich(
    value: str,
    *,
    size: float = Type.BODY,
    color: str | None = None,
    weight: ft.FontWeight | None = None,
) -> ft.Control:
    """支持 `` `code` `` 行内代码的文字。

    站点的文案里经常要写 Ctrl+S、settings.json 这类片段。与其在数据层
    记一堆 HTML，不如只支持一种最常用的行内标记：反引号。
    """
    c = current_colors()
    base = color or c.text
    parts = _CODE_SPAN.split(value)
    if len(parts) <= 1:
        return text(value, size=size, color=base, weight=weight)
    spans: list[ft.TextSpan] = []
    for i, part in enumerate(parts):
        if not part:
            continue
        if i % 2:  # 反引号内的片段
            spans.append(
                ft.TextSpan(
                    f"\u2009{part}\u2009",  # 细空格撑一点内边距（TextSpan 无 padding）
                    ft.TextStyle(
                        size=size * 0.94,
                        color=c.code_fg,
                        bgcolor=c.code_bg,
                        font_family=FONT_MONO,
                    ),
                )
            )
        else:
            spans.append(
                ft.TextSpan(part, ft.TextStyle(size=size, color=base, weight=weight,
                                               font_family=FONT_MAIN))
            )
    return ft.Text(spans=spans)


def spacer(height: int) -> ft.Control:
    """定高留白（比 `Column.spacing` 更好控制节奏的地方用它）。"""
    return ft.Container(height=height)


# ---------------------------------------------------------------------------
# 区块骨架
# ---------------------------------------------------------------------------


def section(anchor: str, width: int, *children: ft.Control) -> ft.Control:
    """带锚点的内容区块。

    `anchor` 就是 `ScrollKey`：顶栏导航滚到这里，不依赖像素偏移，
    布局随窗口变化时锚点依然准确。

    顶部留 20px：`scroll_to(scroll_key=...)` 会把容器顶边对齐视口顶边，
    不留白的话区块标题会贴着顶栏下沿，看起来像被切掉。
    """
    return ft.Container(
        key=ft.ScrollKey(anchor),
        width=width,
        padding=ft.Padding.only(top=20, bottom=Layout.SECTION_GAP),
        content=ft.Column(spacing=Spacing.XXXL, tight=True, controls=list(children)),
    )


def section_header(
    eyebrow: str,
    title: str,
    desc: str = "",
    *,
    accent: str | None = None,
    width: int | None = None,
) -> ft.Control:
    """区块标题：编号 + 大标题 + 一句说明。"""
    c = current_colors()
    tone = accent or c.link
    body: list[ft.Control] = [
        ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Container(width=22, height=2, bgcolor=tone, border_radius=1),
                text(eyebrow, size=Type.TINY, color=tone, weight=ft.FontWeight.W_600),
            ],
        ),
        text(title, size=Type.H2, weight=ft.FontWeight.W_700),
    ]
    if desc:
        body.append(ft.Container(width=width or 720, content=text(desc, color=c.muted)))
    return ft.Column(spacing=12, tight=True, controls=body)


# ---------------------------------------------------------------------------
# 卡片
# ---------------------------------------------------------------------------


def card(
    *children: ft.Control,
    pad: int = 20,
    bgcolor: str | None = None,
    border: bool = True,
    on_click: Callable[[], None] | None = None,
    width: int | None = None,
    spacing: int = 10,
    alignment: ft.Alignment | None = None,
) -> ft.Control:
    """通用卡片：圆角 + 1px 边框 + 表面色。

    默认不加阴影：长页面靠边框与底色分层就够了，加阴影容易显得脏。
    """
    c = current_colors()
    return ft.Container(
        width=width,
        padding=ft.Padding.all(pad),
        bgcolor=bgcolor or c.surface,
        border_radius=Radius.LG,
        border=ft.Border.all(1, c.border) if border else None,
        ink=on_click is not None,
        on_click=on_event(on_click) if on_click is not None else None,
        alignment=alignment,
        content=ft.Column(spacing=spacing, tight=True, controls=list(children)),
    )


def icon_tile(name: str, tone: str, size: int = 36, glyph: int = 19) -> ft.Control:
    """图标底板：浅色圆角方块 + 居中图标。"""
    return ft.Container(
        width=size,
        height=size,
        border_radius=Radius.MD,
        bgcolor=tint(tone, 0.10),
        alignment=ft.Alignment.CENTER,
        content=icon(name, size=glyph, color=tone),
    )


def feature_card(feature: K.Feature, tone: str | None = None) -> ft.Control:
    """特性卡片：图标 + 标题 + 一句话 + 要点。"""
    c = current_colors()
    accent = tone or c.link
    return card(
        ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                icon_tile(feature.icon, accent),
                ft.Container(
                    expand=True,
                    content=text(feature.title, size=Type.H4, weight=ft.FontWeight.W_600),
                ),
            ],
        ),
        ft.Container(content=rich(feature.summary, size=Type.SMALL, color=c.muted)),
        *(
            [
                ft.Column(
                    spacing=6,
                    tight=True,
                    controls=[_bullet(point, c) for point in feature.points],
                )
            ]
            if feature.points
            else []
        ),
    )


def _bullet(content: str, c: Colors) -> ft.Control:
    """要点行：小圆点 + 文字（圆点是并列关系，不用编号）。"""
    return ft.Row(
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.START,
        controls=[
            ft.Container(
                margin=ft.Margin.only(top=6),
                width=4,
                height=4,
                bgcolor=tint(c.muted, 0.9),
                border_radius=2,
            ),
            ft.Container(expand=True, content=rich(content, size=Type.TINY, color=c.muted)),
        ],
    )


# ---------------------------------------------------------------------------
# 徽标 / 标签 / 键帽
# ---------------------------------------------------------------------------


def badge(label: str, tone: str | None = None, filled: bool = False) -> ft.Control:
    """小徽标（版本号、分类）。"""
    c = current_colors()
    color = tone or c.link
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=7, vertical=2),
        border_radius=Radius.SM,
        bgcolor=color if filled else tint(color, 0.10),
        content=text(
            label,
            size=Type.TINY,
            color=c.bg if filled else color,
            weight=ft.FontWeight.W_500,
            mono=True,
        ),
    )


def tag(label: str) -> ft.Control:
    """等宽小标签（语法片段、模块名）。"""
    c = current_colors()
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=6, vertical=2),
        border_radius=Radius.SM,
        bgcolor=c.code_bg,
        content=text(label, size=Type.TINY, color=c.code_fg, mono=True),
    )


def keycap(combo: str, c: Colors | None = None) -> ft.Control:
    """键帽。"""
    colors = c or current_colors()
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=7, vertical=3),
        border_radius=Radius.SM,
        bgcolor=colors.bg,
        border=ft.Border.all(1, colors.border),
        content=text(combo, size=Type.TINY, color=colors.text, mono=True),
    )


def stat(metric: K.Metric) -> ft.Control:
    """首屏指标：大数字 + 说明。"""
    c = current_colors()
    return ft.Column(
        spacing=2,
        tight=True,
        controls=[
            text(metric.value, size=Type.STAT, weight=ft.FontWeight.W_700),
            text(metric.label, size=Type.SMALL, weight=ft.FontWeight.W_500),
            text(metric.hint, size=Type.TINY, color=c.muted),
        ],
    )


# ---------------------------------------------------------------------------
# 按钮
# ---------------------------------------------------------------------------


def primary_button(
    label: str,
    on_click: Callable[[], None],
    glyph: str = "",
) -> ft.Control:
    """实心主按钮（显式定高 40，避免被文字行盒算出不一致的高度）。"""
    c = current_colors()
    return ft.Container(
        height=40,
        padding=ft.Padding.symmetric(horizontal=18),
        border_radius=Radius.MD,
        bgcolor=c.link,
        ink=True,
        on_click=on_event(on_click),
        tooltip=label,
        content=ft.Row(
            spacing=8,
            tight=True,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                *([icon(glyph, size=17, color=c.bg)] if glyph else []),
                text(label, size=Type.SMALL, color=c.bg, weight=ft.FontWeight.W_600),
            ],
        ),
    )


def ghost_button(
    label: str,
    on_click: Callable[[], None],
    glyph: str = "",
) -> ft.Control:
    """描边次按钮。"""
    c = current_colors()
    return ft.Container(
        height=40,
        padding=ft.Padding.symmetric(horizontal=16),
        border_radius=Radius.MD,
        bgcolor=tint(c.text, 0.03),
        border=ft.Border.all(1, c.border),
        ink=True,
        on_click=on_event(on_click),
        tooltip=label,
        content=ft.Row(
            spacing=8,
            tight=True,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                *([icon(glyph, size=17, color=c.muted)] if glyph else []),
                text(label, size=Type.SMALL, weight=ft.FontWeight.W_500),
            ],
        ),
    )


def pill_tabs(
    items: Sequence[tuple[str, str]],
    active: str,
    on_select: Callable[[str], None],
) -> ft.Control:
    """胶囊页签组（演示样例切换、文档目录共用）。

    `items` 是 `(key, label)`。选中态实心、未选中透明——页签是二态控件，
    反差要够强，否则看不出当前在哪一页。
    """
    c = current_colors()
    return ft.Row(
        spacing=4,
        wrap=True,
        run_spacing=4,
        controls=[
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=12, vertical=7),
                border_radius=Radius.MD,
                bgcolor=c.link if key == active else None,
                ink=True,
                on_click=lambda _e=None, k=key: on_select(k),
                tooltip=label,
                content=text(
                    label,
                    size=Type.SMALL,
                    color=c.bg if key == active else c.muted,
                    weight=ft.FontWeight.W_600 if key == active else ft.FontWeight.W_400,
                ),
            )
            for key, label in items
        ],
    )


# ---------------------------------------------------------------------------
# 网格 / 代码
# ---------------------------------------------------------------------------


def grid(width: int, columns: int, children: Sequence[ft.Control], gap: int | None = None) -> ft.Control:
    """等宽网格：按内容宽度自己算列宽。

    不用 `ResponsiveRow` 的原因：它的断点看的是**页面**宽度，而这里的可用宽度
    是外壳算出来的内容宽度（页面宽度减去留白），两者不一致时网格会提前折行。
    列数按「每列至少 320px」收敛，窄屏自动退化为 2 列 / 1 列。
    """
    gap = Layout.GRID if gap is None else gap
    cols = max(1, min(columns, width // 320))
    cell = (width - gap * (cols - 1)) // cols
    return ft.Row(
        spacing=gap,
        run_spacing=gap,
        wrap=True,
        controls=[ft.Container(width=cell, content=child) for child in children],
    )


def inset_panel(*children: ft.Control, bgcolor: str | None = None) -> ft.Control:
    """内嵌面板：比卡片再浅一层的容器（用于卡片内部的次级区域）。"""
    c = current_colors()
    return ft.Container(
        padding=ft.Padding.all(14),
        bgcolor=bgcolor or tint(c.text, 0.025),
        border_radius=Radius.MD,
        border=only_border(left=ft.BorderSide(2, tint(c.link, 0.5))),
        content=ft.Column(spacing=8, tight=True, controls=list(children)),
    )
