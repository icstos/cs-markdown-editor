"""功能栏（横向四列布局第一列）：VSCode / Obsidian 风格活动栏。

顶部图标式大功能选项（文件 / 搜索 / 源代码管理），底部菜单按钮（≡，收纳文件/
编辑/段落/格式/视图/Git/帮助七组，设置功能在菜单内）。点击当前选中图标 =
一键收起第二列（管理面板），点击其他图标 = 切换面板并展开。

「源代码管理」图标带变更数角标（VSCode 同款）：角标叠在图标右上角，不占用行宽
——活动栏只有 44px，把数字排进图标行会挤歪图标居中。
"""

from collections.abc import Callable

import ui

from styles import FONT_MAIN, Spacing, _current_colors, only_border

_BAR_W = 44  # 活动栏固定宽度：VSCode / Obsidian 风格紧凑带宽（20px 图标 + 两侧 12px 呼吸）
# 桌面端交互直觉：活动栏仅承载图标级导航，宽度只需容纳图标与悬停/激活反馈；
# 44px 较原 60px 更贴近列内容，消除大面积留白，把横向空间让给编辑区（紧凑、专业）。


@ui.component
def ActivityBar(
    active_panel: str,
    sidebar_open: bool,
    on_click_panel: Callable[[str], None],
    menu: ui.Control,
    theme_mode: ui.ThemeMode,
    git_count: int = 0,
) -> ui.Control:
    """活动栏：文件 / 搜索 / 源代码管理（顶部）+ 全局菜单按钮（底部）。

    on_click_panel(key)：点击面板图标，App 侧根据当前选中状态决定
    切换面板还是收起第二列（VSCode 直觉：再点当前图标收起）。
    menu：≡ 全局菜单控件（MenuBar，含设置入口），替代原设置按钮。
    git_count：待提交变更数，>0 时在源代码管理图标右上角显示角标。
    """
    c = _current_colors()

    def _btn(
        key: str,
        icon: str,
        tooltip: str,
        active: bool,
        on_click,
        badge: int = 0,
    ) -> ui.Control:
        # VSCode 风格：激活项仅左侧 3px 主题色强调条 + 主题色图标，
        # 不再用整块半透明圆角底色（观感更清爽、与冷灰底色更协调）
        icon_control: ui.Control = ui.Icon(
            icon, size=20, color=c.link if active else c.muted
        )
        if badge > 0:
            # Stack：图标保持居中，角标绝对定位到右上角（不挤压图标行宽）。
            # 超过 99 显示 99+（VSCode 同款截断），避免角标被撑宽后出框。
            icon_control = ui.Stack(
                controls=[
                    ui.Container(content=icon_control, alignment=ui.Alignment.CENTER),
                    ui.Container(
                        right=-8,
                        top=-5,
                        bgcolor=c.link,
                        border_radius=ui.BorderRadius.all(7),
                        padding=ui.Padding.symmetric(horizontal=3, vertical=0),
                        content=ui.Text(
                            "99+" if badge > 99 else str(badge),
                            size=8,
                            color="#FFFFFF",
                            font_family=FONT_MAIN,
                            weight=ui.FontWeight.W_700,
                        ),
                    ),
                ],
                width=26,
                height=26,
                alignment=ui.Alignment.CENTER,
            )
        return ui.Container(
            height=44,
            on_click=on_click,
            tooltip=tooltip,
            ink=True,
            content=ui.Row(
                controls=[
                    ui.Container(
                        width=3,
                        height=24,
                        border_radius=ui.BorderRadius.all(2),
                        bgcolor=c.link,
                        visible=active,
                    ),
                    ui.Container(
                        expand=True,
                        alignment=ui.Alignment.CENTER,
                        content=icon_control,
                    ),
                    # 右侧对称占位：图标在剩余空间内严格居中
                    ui.Container(width=3),
                ],
                spacing=0,
            ),
        )

    return ui.Container(
        width=_BAR_W,
        bgcolor=c.toolbar_bg,
        border=only_border(right=ui.BorderSide(1, c.border)),
        padding=ui.Padding.symmetric(vertical=Spacing.LG),
        content=ui.Column(
            controls=[
                # 顶部按钮组（文件 / 搜索 / 源代码管理）：独立子列保持 SM 间距，整体置顶
                ui.Column(
                    controls=[
                        _btn(
                            "files",
                            ui.Icons.FOLDER_OUTLINED,
                            "文件",
                            active_panel == "files" and sidebar_open,
                            lambda e: on_click_panel("files"),
                        ),
                        _btn(
                            "search",
                            ui.Icons.SEARCH,
                            "搜索",
                            active_panel == "search" and sidebar_open,
                            lambda e: on_click_panel("search"),
                        ),
                        _btn(
                            "git",
                            ui.Icons.ACCOUNT_TREE,
                            f"源代码管理（{git_count} 项更改）" if git_count
                            else "源代码管理",
                            active_panel == "git" and sidebar_open,
                            lambda e: on_click_panel("git"),
                            badge=git_count,
                        ),
                    ],
                    spacing=Spacing.SM,
                    horizontal_alignment=ui.CrossAxisAlignment.CENTER,
                ),
                # 底部 ≡ 菜单按钮：整列不出现任何 expand/flex 子控件（沉底交给
                # SPACE_BETWEEN），规避桌面客户端「expand 之后的兄弟控件」布局
                # 异常（不居中 / 出现多余滚动条）。按钮行固定整宽整高
                # （_BAR_W × 44），由 Row 自身把 MenuBar 在列内严格居中（横竖
                # 双向 CENTER），不依赖外层任何对齐。
                ui.Row(
                    controls=[menu],
                    width=_BAR_W,
                    height=44,
                    alignment=ui.MainAxisAlignment.CENTER,
                    vertical_alignment=ui.CrossAxisAlignment.CENTER,
                    tooltip="菜单", spacing=10,
                ),
            ],
            alignment=ui.MainAxisAlignment.SPACE_BETWEEN,
            horizontal_alignment=ui.CrossAxisAlignment.CENTER, spacing=10,
        ),
    )
