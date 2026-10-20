"""站点外壳：顶栏、滚动容器、主题切换、页脚。

状态只有三个
------------
当前导航项、站点主题、视口宽度。它们都在 `SiteShell` 里；各区块不持有全局
状态（演示页签、文档目录这类纯展示状态除外）。状态越集中，重渲染范围越可预测。

为什么视口宽度要进 state
------------------------
Flet 的 `Container` 没有 max-width 语义，写死 1120 会在窄窗口里横向溢出。
所以内容宽度 = min(1120, 视口 - 留白)，由外壳按 `page.on_resize` 维护，
再作为参数传给每个区块——参数的显式传递也让"这个区块多宽"一眼可见。
"""

from __future__ import annotations

import asyncio
import contextlib

import flet as ft

from cmsite import _content as K
from cmsite._demo import DemoSection
from cmsite._docs import DocsSection
from cmsite._sections import about_section, architecture_section, features_section, hero_section
from cmsite._theme import (
    Layout,
    Radius,
    Spacing,
    Type,
    current_colors,
    only_border,
    tint,
)
from cmsite._ui import icon, on_event, text

#: 视口宽度拿不到时的兜底值（首帧 / 某些嵌入式视图）
_FALLBACK_W = 1280


@ft.component
def SiteShell() -> ft.Control:
    """站点根组件。"""
    active_anchor, set_active_anchor = ft.use_state(K.START_ANCHOR or K.NAV_ITEMS[0].anchor)
    theme_mode, set_theme_mode = ft.use_state(
        ft.ThemeMode.DARK if K.START_THEME == "dark" else ft.ThemeMode.LIGHT
    )
    viewport, set_viewport = ft.use_state(_FALLBACK_W)
    scroll_ref = ft.use_ref(None)
    clipboard_ref = ft.use_ref(None)

    page = ft.context.page
    # 渲染期同步主题：`use_effect` 在渲染之后才跑，若只靠它，切主题那一帧
    # `current_colors()` 读到的还是旧值，界面会有一帧旧配色。
    if page is not None:
        page.theme_mode = theme_mode
        page.bgcolor = current_colors().bg

    c = current_colors()
    width = Layout.content_width(viewport)
    compact = viewport < Layout.COMPACT_W

    def _sync_viewport() -> None:
        if page is not None and page.width:
            set_viewport(page.width)

    async def _scroll_to(anchor: str, duration: int = 420) -> None:
        """滚到锚点。

        ⚠️ 1.0.4 里 `ScrollableControl.scroll_to` 是**协程**：不 await 时只产生一条
        `coroutine was never awaited` 的 RuntimeWarning，而界面纹丝不动——
        症状看起来像"点击没反应"，实际是没等它跑。
        """
        col = scroll_ref.current
        if col is None:
            return
        with contextlib.suppress(RuntimeError, ValueError):
            await col.scroll_to(
                scroll_key=anchor,
                duration=duration,
                curve=ft.AnimationCurve.EASE_OUT,
            )

    async def _jump_task(anchor: str) -> None:
        """挂载后定位到指定锚点（等布局完成，否则目标区块还没量出高度）。"""
        await asyncio.sleep(0.6)
        await _scroll_to(anchor, duration=0)

    def _bind() -> None:
        """挂载期：接上 resize 事件，读一次真实视口宽度，并构造剪贴板服务。

        `ft.Clipboard` 是 Service，构造时即注册到当前 session——**不要**放进
        控件树：它会被当成普通控件布局，渲染成一个红色报错占位块。
        """
        if page is None:
            return
        page.on_resize = lambda _e: _sync_viewport()
        _sync_viewport()
        if clipboard_ref.current is None:
            clipboard_ref.current = ft.Clipboard()
        if K.START_ANCHOR:
            page.run_task(_jump_task, K.START_ANCHOR)

    ft.use_effect(_bind, [])

    def jump(anchor: str):
        """生成导航回调：先高亮，再滚动（顺序反了会像"点了没反应"）。"""

        def handler() -> None:
            set_active_anchor(anchor)
            if page is not None:
                page.run_task(_scroll_to, anchor)

        return handler

    def toggle_theme() -> None:
        set_theme_mode(
            ft.ThemeMode.DARK if theme_mode == ft.ThemeMode.LIGHT else ft.ThemeMode.LIGHT
        )

    go_home = jump("hero")

    return ft.Column(
        spacing=0,
        tight=True,
        expand=True,
        controls=[
            _top_bar(
                active=active_anchor,
                on_jump=jump,
                theme_mode=theme_mode,
                on_toggle_theme=toggle_theme,
                compact=compact,
                gutter=Layout.gutter(viewport),
                on_home=go_home,
                c=c,
            ),
            ft.Container(
                expand=True,
                alignment=ft.Alignment.TOP_CENTER,
                content=ft.Column(
                    ref=scroll_ref,
                    spacing=0,
                    tight=True,
                    scroll=ft.ScrollMode.AUTO,
                    controls=[
                        hero_section(width, jump, clipboard_ref),
                        features_section(width),
                        DemoSection(width, clipboard_ref),
                        DocsSection(width, clipboard_ref),
                        architecture_section(width),
                        about_section(width, clipboard_ref),
                        _footer(width, c),
                    ],
                ),
            ),
        ],
    )


def _top_bar(
    active: str,
    on_jump,
    theme_mode: ft.ThemeMode,
    on_toggle_theme,
    compact: bool,
    gutter: int,
    on_home,
    c,
) -> ft.Control:
    """顶栏：品牌（点击回顶）+ 导航 + 主题开关。

    显式定高 60；内部不使用 `ft.IconButton`（Material 固有高 40 会把行高顶开），
    图标按钮一律是定尺寸 `Container(ink=True)`。
    """
    nav = ft.Row(
        spacing=2,
        wrap=True,
        run_spacing=2,
        controls=[
            _nav_item(item.label, item.anchor == active, on_jump(item.anchor), c)
            for item in K.NAV_ITEMS
        ],
    )

    brand = ft.Container(
        ink=True,
        on_click=on_event(on_home),
        tooltip="回到顶部",
        padding=ft.Padding.symmetric(horizontal=4, vertical=4),
        border_radius=Radius.SM,
        content=ft.Row(
            spacing=8,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Container(
                    width=26,
                    height=26,
                    border_radius=Radius.MD,
                    bgcolor=c.link,
                    alignment=ft.Alignment.CENTER,
                    content=icon("EDIT_NOTE", size=16, color=c.bg),
                ),
                *(
                    [text(K.APP_NAME, size=Type.SMALL, weight=ft.FontWeight.W_600)]
                    if not compact
                    else []
                ),
            ],
        ),
    )

    return ft.Container(
        height=Layout.NAV_H,
        padding=ft.Padding.symmetric(horizontal=gutter),
        bgcolor=tint(c.bg, 0.96),
        border=only_border(bottom=ft.BorderSide(1, c.border)),
        content=ft.Row(
            spacing=Spacing.XL,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                brand,
                ft.Container(expand=True, alignment=ft.Alignment.CENTER_RIGHT, content=nav),
                _theme_switch(theme_mode, on_toggle_theme, c),
            ],
        ),
    )


def _nav_item(label: str, active: bool, on_click, c) -> ft.Control:
    """导航项：选中态用强调色文字 + 2px 下划条。

    用下划条而不是整块底色——长页面上底色块会抢走内容的注意力。
    """
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=10, vertical=6),
        border_radius=Radius.SM,
        ink=True,
        on_click=on_event(on_click),
        tooltip=label,
        content=ft.Column(
            spacing=3,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                text(
                    label,
                    size=Type.SMALL,
                    color=c.link if active else c.muted,
                    weight=ft.FontWeight.W_600 if active else ft.FontWeight.W_400,
                ),
                ft.Container(
                    height=2,
                    width=16,
                    border_radius=1,
                    bgcolor=c.link if active else tint(c.link, 0.0),
                ),
            ],
        ),
    )


def _theme_switch(theme_mode: ft.ThemeMode, on_click, c) -> ft.Control:
    """亮 / 暗主题开关（定尺寸 30，与顶栏 60 的行高协调）。"""
    dark = theme_mode == ft.ThemeMode.DARK
    return ft.Container(
        width=30,
        height=30,
        border_radius=Radius.MD,
        bgcolor=tint(c.text, 0.05),
        border=ft.Border.all(1, c.border),
        ink=True,
        on_click=on_event(on_click),
        tooltip="切换到亮色主题" if dark else "切换到暗色主题",
        alignment=ft.Alignment.CENTER,
        content=icon("LIGHT_MODE" if dark else "DARK_MODE", size=15, color=c.text),
    )


def _footer(width: int, c) -> ft.Control:
    """页脚：身份信息 + 一句技术说明。"""
    return ft.Container(
        width=width,
        padding=ft.Padding.only(top=Spacing.XXXL, bottom=40),
        border=only_border(top=ft.BorderSide(1, c.border)),
        content=ft.Row(
            spacing=Spacing.XL,
            run_spacing=12,
            wrap=True,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Column(
                    spacing=3,
                    tight=True,
                    controls=[
                        text(K.APP_NAME, size=Type.SMALL, weight=ft.FontWeight.W_600),
                        text(
                            f"v{K.RUNTIME['version']} · {K.APP_LICENSE} · {K.APP_AUTHOR} / "
                            f"{K.APP_COMPANY}",
                            size=Type.TINY,
                            color=c.muted,
                            mono=True,
                        ),
                    ],
                ),
                text(K.FOOTER_NOTE, size=Type.TINY, color=c.muted),
            ],
        ),
    )
