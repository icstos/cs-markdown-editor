"""顶部多文档标签栏 + 关闭确认弹层。

浏览器式标签栏设计（Chrome / Edge 风格）：
- 标签顶部圆角，激活态背景与编辑区一致（c.bg），视觉上「连接」到下方内容区
- 关闭按钮仅激活/悬停时显示（opacity 动画过渡，空间恒定无布局抖动）
- 紧凑高度，文件名省略号截断
- 每个标签包裹 ft.ContextMenu，右键提供完整文件操作菜单
- ConfirmCloseDialog：关闭脏标签时的确认弹层
"""

import os
from collections.abc import Callable

import flet as ft

from styles import FONT_MAIN, Elevation, Radius, Spacing, card_shadow, get_colors, only_border

_DIRTY_COLOR = "#FF9F0A"  # 未保存修改星号色（亮暗通用警示橙）
_TAB_WIDTH = 180          # 标签固定宽度（紧凑，超出文件名用省略号截断）
_TAB_WIDTH_DIFF = 260     # 对比标签宽度（需容纳「左 ⟷ 右」双文件名）
_TAB_ICON = 12            # 标签内图标/字号（紧凑）
_TAB_RADIUS = Radius.SM   # 标签顶部圆角半径


def _file_name(path: str | None) -> str:
    """文件名派生：无路径时回退「未命名.md」。"""
    return os.path.basename(path) if path else "未命名.md"


@ft.component
def TabBar(
    tabs: list[dict],
    active_index: int,
    theme_mode: ft.ThemeMode,
    on_select: Callable[[int], None],
    on_close: Callable[[int], None],
    on_new: Callable[[], None],
    on_context_action: Callable[[str, int], None],
    compare_source: str | None = None,
):
    """顶部标签栏。

    tabs: 每项为 {"file_path": str|None, "dirty": bool}（普通编辑标签）或
          {"type":"diff", "left_path", "right_path", "left_dirty", "right_dirty"}（对比标签）。
    on_context_action(action, i)：action ∈ {"open","new_file","new_folder","copy_path",
    "reveal","rename","duplicate","delete","close","close_others","close_all",
    "select_for_compare","compare_with_selected","swap_diff"}。
    有 file_path 的普通标签才显示文件操作项（打开/复制路径/打开位置/重命名/副本/删除/比较）。
    对比标签（type=="diff"）显示「交换左右侧」替代文件操作，并以对比图标 + 双文件名标识。
    compare_source 非空时，所有有 file_path 的标签均显示「与已选项目进行比较」项。
    """
    c = get_colors(theme_mode)
    hover_index, set_hover_index = ft.use_state(-1)

    def _btn_icon(icon: str, tooltip: str, on_click: Callable, color: str) -> ft.Control:
        return ft.IconButton(
            icon=icon,
            tooltip=tooltip,
            icon_size=_TAB_ICON,
            on_click=on_click,
            style=ft.ButtonStyle(
                color=color,
                bgcolor=ft.Colors.with_opacity(0.0, c.text),
                padding=Spacing.XS,
                shape=ft.RoundedRectangleBorder(radius=Radius.MD),
            ),
        )

    tab_controls: list[ft.Control] = []
    for i, t in enumerate(tabs):
        is_diff = t.get("type") == "diff"
        path = t.get("file_path")
        if is_diff:
            # 对比标签：显示「左 ⟷ 右」双文件名，任一侧脏即标脏
            left_name = os.path.basename(t.get("left_path")) if t.get("left_path") else "未命名"
            right_name = os.path.basename(t.get("right_path")) if t.get("right_path") else "未命名"
            fname = f"{left_name} ⟷ {right_name}"
            dirty = bool(t.get("left_dirty")) or bool(t.get("right_dirty"))
            leading_icon = ft.Icons.COMPARE_ARROWS
            leading_color = c.link
        else:
            fname = _file_name(path)
            dirty = bool(t.get("dirty"))
            leading_icon = None
            leading_color = c.muted
        is_active = i == active_index
        is_hover = i == hover_index

        # 浏览器式背景：激活态用 c.bg（与编辑区一致，视觉「连接」到内容区）
        # 悬停态用 c.hover（圆角高亮），其余透明
        if is_active:
            bgcolor = c.bg
        elif is_hover:
            bgcolor = c.hover
        else:
            bgcolor = ft.Colors.with_opacity(0.0, c.text)

        # 关闭按钮颜色：激活或悬停时提亮
        close_color = c.text if (is_active or is_hover) else c.muted
        # 关闭按钮可见性：仅激活/悬停时显示（opacity 过渡，空间恒定无布局抖动）
        close_opacity = 1.0 if (is_active or is_hover) else 0.0

        def _on_tab_click(e, idx=i):
            on_select(idx)

        def _on_tab_hover(e, idx=i):
            # e.data 为 "true"/"false" 字符串
            entered = str(getattr(e, "data", "")).lower() == "true"
            set_hover_index(idx if entered else -1)

        def _on_close_click(e, idx=i):
            # IconButton 的手势识别器会吞掉点击（Flutter gesture arena），
            # 不会冒泡到外层 Container.on_click，故无需 stop_propagation。
            on_close(idx)

        # 右键菜单项：有 file_path 才显示文件操作（打开/比较/复制路径/打开位置/重命名/副本/删除）
        context_items: list[ft.PopupMenuItem] = []
        if path:
            context_items.append(
                ft.PopupMenuItem(
                    content="打开",
                    icon=ft.Icons.OPEN_IN_NEW,
                    on_click=lambda e, idx=i: on_context_action("open", idx),
                )
            )
            # 文件比较：选择以进行比较 / 与已选项目进行比较（VSCode 风格）
            context_items.append(
                ft.PopupMenuItem(
                    content="选择以进行比较",
                    icon=ft.Icons.DIFFERENCE,
                    on_click=lambda e, idx=i: on_context_action("select_for_compare", idx),
                )
            )
            if compare_source and os.path.abspath(compare_source) != os.path.abspath(path):
                context_items.append(
                    ft.PopupMenuItem(
                        content="与已选项目进行比较",
                        icon=ft.Icons.COMPARE_ARROWS,
                        on_click=lambda e, idx=i: on_context_action("compare_with_selected", idx),
                    )
                )
            context_items.append(ft.PopupMenuItem())  # 分隔
        # 新建文件/文件夹（有 file_path 时提供目录上下文）
        if path:
            context_items.append(
                ft.PopupMenuItem(
                    content="新建文件",
                    icon=ft.Icons.NOTE_ADD,
                    on_click=lambda e, idx=i: on_context_action("new_file", idx),
                )
            )
            context_items.append(
                ft.PopupMenuItem(
                    content="新建文件夹",
                    icon=ft.Icons.CREATE_NEW_FOLDER,
                    on_click=lambda e, idx=i: on_context_action("new_folder", idx),
                )
            )
            context_items.append(ft.PopupMenuItem())  # 分隔
            context_items.append(
                ft.PopupMenuItem(
                    content="复制路径",
                    icon=ft.Icons.CONTENT_COPY,
                    on_click=lambda e, idx=i: on_context_action("copy_path", idx),
                )
            )
            context_items.append(
                ft.PopupMenuItem(
                    content="打开文件位置",
                    icon=ft.Icons.FOLDER_OPEN,
                    on_click=lambda e, idx=i: on_context_action("reveal", idx),
                )
            )
            context_items.append(ft.PopupMenuItem())  # 分隔
            context_items.append(
                ft.PopupMenuItem(
                    content="重命名",
                    icon=ft.Icons.DRIVE_FILE_RENAME_OUTLINE,
                    on_click=lambda e, idx=i: on_context_action("rename", idx),
                )
            )
            context_items.append(
                ft.PopupMenuItem(
                    content="创建副本",
                    icon=ft.Icons.FILE_COPY_OUTLINED,
                    on_click=lambda e, idx=i: on_context_action("duplicate", idx),
                )
            )
            context_items.append(
                ft.PopupMenuItem(
                    content="删除",
                    icon=ft.Icons.DELETE_OUTLINE,
                    on_click=lambda e, idx=i: on_context_action("delete", idx),
                )
            )
            context_items.append(ft.PopupMenuItem())  # 分隔
        # 对比标签专属：交换左右侧（便于从不同视角审阅差异）
        if is_diff:
            context_items.append(
                ft.PopupMenuItem(
                    content="交换左右侧",
                    icon=ft.Icons.SWAP_HORIZ,
                    on_click=lambda e, idx=i: on_context_action("swap_diff", idx),
                )
            )
            context_items.append(ft.PopupMenuItem())  # 分隔
        # 关闭操作（始终可用）
        context_items.append(
            ft.PopupMenuItem(
                content="关闭",
                icon=ft.Icons.CLOSE,
                on_click=lambda e, idx=i: on_context_action("close", idx),
            )
        )
        context_items.append(
            ft.PopupMenuItem(
                content="关闭其他",
                icon=ft.Icons.CANCEL,
                on_click=lambda e, idx=i: on_context_action("close_others", idx),
            )
        )
        context_items.append(
            ft.PopupMenuItem(
                content="关闭全部",
                icon=ft.Icons.TAB,
                on_click=lambda e: on_context_action("close_all", 0),
            )
        )

        name_color = c.text if is_active else c.muted
        name_weight = ft.FontWeight.W_600 if is_active else ft.FontWeight.NORMAL

        # 对比标签显示双文件名，加宽以减少截断；普通标签保持固定宽度
        tab_width = _TAB_WIDTH_DIFF if is_diff else _TAB_WIDTH

        row_controls: list[ft.Control] = []
        if leading_icon is not None:
            row_controls.append(
                ft.Icon(
                    leading_icon,
                    size=_TAB_ICON,
                    color=leading_color if is_active else c.muted,
                )
            )
        row_controls.append(
            ft.Text(
                value="*" if dirty else "",
                size=_TAB_ICON,
                color=_DIRTY_COLOR,
                font_family=FONT_MAIN,
                weight=ft.FontWeight.BOLD,
                visible=dirty,
            )
        )
        row_controls.append(
            ft.Text(
                value=fname,
                size=_TAB_ICON,
                color=name_color,
                font_family=FONT_MAIN,
                weight=name_weight,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                tooltip=fname,
                expand=True,
            )
        )
        row_controls.append(
            ft.Container(
                content=_btn_icon(
                    ft.Icons.CLOSE,
                    "关闭",
                    _on_close_click,
                    close_color,
                ),
                opacity=close_opacity,
                animate_opacity=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
            )
        )

        # 浏览器式标签：顶部圆角，无底部强调条（用背景色「连接」到编辑区）
        tab_content = ft.Container(
            bgcolor=bgcolor,
            border_radius=ft.BorderRadius(
                top_left=_TAB_RADIUS, top_right=_TAB_RADIUS,
                bottom_left=0, bottom_right=0,
            ),
            on_click=_on_tab_click,
            on_hover=_on_tab_hover,
            width=tab_width,
            padding=ft.Padding.only(
                left=Spacing.MD, right=Spacing.SM,
                top=Spacing.XS, bottom=Spacing.XS,
            ),
            content=ft.Row(
                controls=row_controls,
                spacing=Spacing.XS,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

        tab_controls.append(
            ft.ContextMenu(
                content=tab_content,
                secondary_items=context_items,
            )
        )

    # 尾部「+」新建按钮：固定在滚动区外，与标签栏底色一致
    new_btn = ft.Container(
        bgcolor=c.toolbar_bg,
        padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=Spacing.XS),
        content=_btn_icon(
            ft.Icons.ADD,
            "新建标签  Ctrl+N",
            lambda e: on_new(),
            c.muted,
        ),
    )

    return ft.Container(
        bgcolor=c.toolbar_bg,
        border=only_border(bottom=ft.BorderSide(1, c.border)),
        padding=ft.Padding.only(left=Spacing.SM, right=Spacing.SM, top=0, bottom=0),
        content=ft.Row(
            controls=[
                ft.Row(
                    controls=tab_controls,
                    spacing=0,
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.END,
                ),
                new_btn,
            ],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


@ft.component
def ConfirmCloseDialog(
    visible: bool,
    file_name: str,
    theme_mode: ft.ThemeMode,
    on_save_and_close: Callable[[], None],
    on_close_without_save: Callable[[], None],
    on_cancel: Callable[[], None],
    save_label: str = "保存并关闭",
):
    """关闭脏标签确认弹层（Stack overlay 模式）。"""
    c = get_colors(theme_mode)
    is_dark = theme_mode == ft.ThemeMode.DARK

    def _text_btn(label: str, on_click: Callable, color: str) -> ft.Control:
        return ft.TextButton(
            label,
            on_click=lambda e: on_click(),
            style=ft.ButtonStyle(color=color),
        )

    return ft.Container(
        visible=visible,
        expand=True,
        bgcolor=ft.Colors.with_opacity(0.28, ft.Colors.BLACK),
        alignment=ft.Alignment.CENTER,
        content=ft.Container(
            width=440,
            bgcolor=c.toolbar_bg,
            border_radius=Radius.XL,
            padding=Spacing.XXXL,
            shadow=card_shadow(Elevation.DIALOG, is_dark),
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(
                                ft.Icons.WARNING_AMBER_ROUNDED,
                                color=_DIRTY_COLOR,
                                size=24,
                            ),
                            ft.Text(
                                value="未保存的修改",
                                size=16,
                                weight=ft.FontWeight.W_600,
                                color=c.text,
                                font_family=FONT_MAIN,
                            ),
                        ],
                        spacing=Spacing.XL,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(height=Spacing.SM),
                    ft.Text(
                        value=f"「{file_name}」包含未保存的修改，关闭前是否保存？",
                        size=13,
                        color=c.muted,
                        font_family=FONT_MAIN,
                    ),
                    ft.Container(height=Spacing.XXL),
                    ft.Row(
                        controls=[
                            ft.Container(expand=True),
                            _text_btn("取消", on_cancel, c.muted),
                            _text_btn("不保存", on_close_without_save, c.muted),
                            ft.Button(
                                save_label,
                                on_click=lambda e: on_save_and_close(),
                                color=ft.Colors.WHITE,
                                bgcolor=c.link,
                            ),
                        ],
                        spacing=Spacing.LG,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=0,
                tight=True,
            ),
        ),
    )
