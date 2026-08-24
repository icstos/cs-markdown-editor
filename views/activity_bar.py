"""功能栏（横向四列布局第一列）：VSCode / Obsidian 风格活动栏。

顶部图标式大功能选项（文件 / 搜索），底部菜单按钮（≡，收纳文件/编辑/段落/
格式/视图/帮助六组，设置功能在菜单内）。点击当前选中图标 = 一键收起第二列
（管理面板），点击其他图标 = 切换面板并展开。
"""

from collections.abc import Callable

import flet as ft

from styles import Spacing, _current_colors, only_border

_BAR_W = 60  # 活动栏固定宽度


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
        # VSCode 风格：激活项仅左侧 3px 主题色强调条 + 主题色图标，
        # 不再用整块半透明圆角底色（观感更清爽、与冷灰底色更协调）
        return ft.Container(
            height=44,
            on_click=on_click,
            tooltip=tooltip,
            ink=True,
            content=ft.Row(
                controls=[
                    ft.Container(
                        width=3,
                        height=24,
                        border_radius=ft.BorderRadius.all(2),
                        bgcolor=c.link,
                        visible=active,
                    ),
                    ft.Container(
                        expand=True,
                        alignment=ft.Alignment.CENTER,
                        content=ft.Icon(
                            icon,
                            size=20,
                            color=c.link if active else c.muted,
                        ),
                    ),
                    # 右侧对称占位：图标在剩余空间内严格居中
                    ft.Container(width=3),
                ],
                spacing=0,
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
                    height=44,
                    tooltip="菜单",
                    # 菜单按钮保持居中：容器随内容自适应，铺满 Row + CENTER
                    # 对齐（MenuBar 自然宽度即使略大于图标也居中显示，不偏右）
                    content=ft.Row(
                        controls=[menu],
                        alignment=ft.MainAxisAlignment.CENTER,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ),
            ],
            spacing=Spacing.SM,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
