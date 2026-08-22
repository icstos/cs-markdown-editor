"""功能栏（横向四列布局第一列）：VSCode / Obsidian 风格活动栏。

顶部图标式大功能选项（文件 / 搜索），底部菜单按钮（≡，收纳文件/编辑/段落/
格式/视图/帮助六组，设置功能在菜单内）。点击当前选中图标 = 一键收起第二列
（管理面板），点击其他图标 = 切换面板并展开。
"""

from collections.abc import Callable

import flet as ft

from styles import Radius, Spacing, _current_colors, only_border

_BAR_W = 52  # 活动栏固定宽度


@ft.component
def ActivityBar(
    active_panel: str,
    sidebar_open: bool,
    on_click_panel: Callable[[str], None],
    menu: ft.Control,
    theme_mode: ft.ThemeMode,
) -> ft.Control:
    """活动栏：文件 / 搜索（顶部）+ 全局菜单按钮（底部）。

    on_click_panel(key)：点击面板图标，App 侧根据当前选中状态决定
    切换面板还是收起第二列（VSCode 直觉：再点当前图标收起）。
    menu：≡ 全局菜单控件（MenuBar，含设置入口），替代原设置按钮。
    """
    c = _current_colors()

    def _btn(
        key: str,
        icon: str,
        tooltip: str,
        active: bool,
        on_click,
    ) -> ft.Control:
        return ft.Container(
            width=_BAR_W - 10,
            height=42,
            border_radius=Radius.MD,
            bgcolor=ft.Colors.with_opacity(0.12, c.link) if active else None,
            on_click=on_click,
            tooltip=tooltip,
            ink=True,
            content=ft.Icon(
                icon,
                size=20,
                color=c.link if active else c.muted,
            ),
        )

    return ft.Container(
        width=_BAR_W,
        bgcolor=c.toolbar_bg,
        border=only_border(right=ft.BorderSide(1, c.border)),
        padding=ft.Padding.symmetric(vertical=Spacing.LG),
        content=ft.Column(
            controls=[
                _btn(
                    "files",
                    ft.Icons.FOLDER_OUTLINED,
                    "文件",
                    active_panel == "files" and sidebar_open,
                    lambda e: on_click_panel("files"),
                ),
                _btn(
                    "search",
                    ft.Icons.SEARCH,
                    "搜索",
                    active_panel == "search" and sidebar_open,
                    lambda e: on_click_panel("search"),
                ),
                # 弹性空隙：文件/搜索置顶，菜单沉底
                ft.Container(expand=True),
                ft.Container(
                    width=_BAR_W - 10,
                    height=42,
                    border_radius=Radius.MD,
                    alignment=ft.Alignment(0, 0),
                    tooltip="菜单",
                    ink=True,
                    content=menu,
                ),
            ],
            spacing=Spacing.SM,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
