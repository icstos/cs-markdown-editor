"""功能栏（横向四列布局第一列）：VSCode / Obsidian 风格活动栏。

顶部图标式大功能选项（文件 / 搜索），底部菜单按钮（≡，收纳文件/编辑/段落/
格式/视图/帮助六组，设置功能在菜单内）。点击当前选中图标 = 一键收起第二列
（管理面板），点击其他图标 = 切换面板并展开。
"""

from collections.abc import Callable

import flet as ft

from styles import Spacing, _current_colors, only_border

_BAR_W = 44  # 活动栏固定宽度：VSCode / Obsidian 风格紧凑带宽（20px 图标 + 两侧 12px 呼吸）
# 桌面端交互直觉：活动栏仅承载图标级导航，宽度只需容纳图标与悬停/激活反馈；
# 44px 较原 60px 更贴近列内容，消除大面积留白，把横向空间让给编辑区（紧凑、专业）。


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
                # 顶部按钮组（文件 / 搜索）：独立子列保持 SM 间距，整体置顶
                ft.Column(
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
                    ],
                    spacing=Spacing.SM,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                # 底部 ≡ 菜单按钮：整列不出现任何 expand/flex 子控件（沉底交给
                # SPACE_BETWEEN），规避桌面客户端「expand 之后的兄弟控件」布局
                # 异常（不居中 / 出现多余滚动条）。按钮行固定整宽整高
                # （_BAR_W × 44），由 Row 自身把 MenuBar 在列内严格居中（横竖
                # 双向 CENTER），不依赖外层任何对齐。
                ft.Row(
                    controls=[menu],
                    width=_BAR_W,
                    height=44,
                    alignment=ft.MainAxisAlignment.CENTER,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    tooltip="菜单",
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
