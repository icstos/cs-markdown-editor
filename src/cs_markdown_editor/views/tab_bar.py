"""顶部多文档标签栏 + 关闭确认弹层。

浏览器式标签栏设计（Chrome / Edge 风格）：
- 标签顶部圆角，激活态背景与编辑区一致（c.bg），视觉上「连接」到下方内容区
- 关闭按钮仅激活/悬停时显示（opacity 动画过渡，空间恒定无布局抖动）
- 紧凑高度，文件名省略号截断
- 每个标签包裹 ft.ContextMenu，右键提供完整文件操作菜单
- ConfirmCloseDialog：关闭脏标签时的确认弹层

行高与大纲头部严格一致（styles.TOPBAR_H，真机探针量取并守护）：两列顶栏连成同一条
水平带，底边线重合、编辑区与大纲列表顶部齐平。故本模块**不出现 ft.IconButton**：
Material 的最小点击区会给它 40px 固有高，行高由最高子项决定 → 标签行会被顶到 44px，
比大纲头部高出 12px（改前的实际症状）。图标按钮统一用固定尺寸的
`Container(ink=True)`——与 views/code_block.py 头部、views/status_bar.py 同一套紧凑惯例。
"""

import os
from collections.abc import Callable

import ui

from cs_markdown_editor.styles import (
    FONT_MAIN,
    TOPBAR_H,
    Elevation,
    Radius,
    Spacing,
    card_shadow,
    get_colors,
    only_border,
)

_DIRTY_COLOR = "#FF9F0A"  # 未保存修改星号色（亮暗通用警示橙）
_TAB_WIDTH = 180          # 标签固定宽度（紧凑，超出文件名用省略号截断）
_TAB_WIDTH_DIFF = 260     # 对比标签宽度（需容纳「左 ⟷ 右」双文件名）
_TAB_ICON = 12            # 标签内图标/字号（紧凑）
_TAB_RADIUS = Radius.SM   # 标签顶部圆角半径
_ICON_BTN = 22            # 关闭 / 新建按钮固定边长（≤ TOPBAR_H，留出上下呼吸）


def _file_name(path: str | None) -> str:
    """文件名派生：无路径时回退「未命名.md」。"""
    return os.path.basename(path) if path else "未命名.md"


@ui.component
def TabBar(
    tabs: list[dict],
    active_index: int,
    theme_mode: ui.ThemeMode,
    on_select: Callable[[int], None],
    on_close: Callable[[int], None],
    on_new: Callable[[], None],
    on_context_action: Callable[[str, int], None],
    compare_source: str | None = None,
    leading: ui.Control | None = None,
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
    hover_index, set_hover_index = ui.use_state(-1)

    def _btn_icon(icon: str, tooltip: str, on_click: Callable, color: str) -> ui.Control:
        """固定 _ICON_BTN 见方的紧凑图标按钮（不用 ft.IconButton）。

        真机实测：`ft.IconButton` 无论如何压内边距/传 icon_size，Material 的最小点击区
        都把它定在 40px 高（`visual_density=COMPACT` 也只降到 32）→ 标签行被顶到
        40+2×2=44，比大纲头部高 12px。改用 `Container(ink=True)` 后行高由 TOPBAR_H
        决定（见 styles.TOPBAR_H），点击仍有水波反馈、悬停有 tooltip。
        """
        return ui.Container(
            width=_ICON_BTN,
            height=_ICON_BTN,
            border_radius=Radius.MD,
            alignment=ui.Alignment.CENTER,
            ink=True,
            tooltip=tooltip,
            on_click=on_click,
            content=ui.Icon(icon, size=_TAB_ICON, color=color),
        )

    tab_controls: list[ui.Control] = []
    for i, t in enumerate(tabs):
        is_diff = t.get("type") == "diff"
        path = t.get("file_path")
        if is_diff:
            # 对比标签：显示「左 ⟷ 右」双文件名，任一侧脏即标脏
            left_name = os.path.basename(t.get("left_path")) if t.get("left_path") else "未命名"
            right_name = os.path.basename(t.get("right_path")) if t.get("right_path") else "未命名"
            fname = f"{left_name} ⟷ {right_name}"
            dirty = bool(t.get("left_dirty")) or bool(t.get("right_dirty"))
            leading_icon = ui.Icons.COMPARE_ARROWS
            leading_color = c.link
        else:
            # .lnk 快捷方式打开时 display_name 记录链接文件名（file_path 为目标路径）
            fname = t.get("display_name") or _file_name(path)
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
            bgcolor = ui.Colors.with_opacity(0.0, c.text)

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
        context_items: list[ui.PopupMenuItem] = []
        if path:
            context_items.append(
                ui.PopupMenuItem(
                    content="打开",
                    icon=ui.Icons.OPEN_IN_NEW,
                    on_click=lambda e, idx=i: on_context_action("open", idx),
                )
            )
            # 文件比较：选择以进行比较 / 与已选项目进行比较（VSCode 风格）
            context_items.append(
                ui.PopupMenuItem(
                    content="选择以进行比较",
                    icon=ui.Icons.DIFFERENCE,
                    on_click=lambda e, idx=i: on_context_action("select_for_compare", idx),
                )
            )
            if compare_source and os.path.abspath(compare_source) != os.path.abspath(path):
                context_items.append(
                    ui.PopupMenuItem(
                        content="与已选项目进行比较",
                        icon=ui.Icons.COMPARE_ARROWS,
                        on_click=lambda e, idx=i: on_context_action("compare_with_selected", idx),
                    )
                )
            context_items.append(ui.PopupMenuItem())  # 分隔
            # Git：单文件专属修改历史（VS Code「Open Timeline / File History」等价入口）
            context_items.append(
                ui.PopupMenuItem(
                    content="查看文件历史",
                    icon=ui.Icons.HISTORY,
                    on_click=lambda e, idx=i: on_context_action("git_history", idx),
                )
            )
            context_items.append(ui.PopupMenuItem())  # 分隔
        # 新建文件/文件夹（有 file_path 时提供目录上下文）
        if path:
            context_items.append(
                ui.PopupMenuItem(
                    content="新建文件",
                    icon=ui.Icons.NOTE_ADD,
                    on_click=lambda e, idx=i: on_context_action("new_file", idx),
                )
            )
            context_items.append(
                ui.PopupMenuItem(
                    content="新建文件夹",
                    icon=ui.Icons.CREATE_NEW_FOLDER,
                    on_click=lambda e, idx=i: on_context_action("new_folder", idx),
                )
            )
            context_items.append(ui.PopupMenuItem())  # 分隔
            context_items.append(
                ui.PopupMenuItem(
                    content="复制路径",
                    icon=ui.Icons.CONTENT_COPY,
                    on_click=lambda e, idx=i: on_context_action("copy_path", idx),
                )
            )
            context_items.append(
                ui.PopupMenuItem(
                    content="打开文件位置",
                    icon=ui.Icons.FOLDER_OPEN,
                    on_click=lambda e, idx=i: on_context_action("reveal", idx),
                )
            )
            context_items.append(ui.PopupMenuItem())  # 分隔
            context_items.append(
                ui.PopupMenuItem(
                    content="重命名",
                    icon=ui.Icons.DRIVE_FILE_RENAME_OUTLINE,
                    on_click=lambda e, idx=i: on_context_action("rename", idx),
                )
            )
            context_items.append(
                ui.PopupMenuItem(
                    content="创建副本",
                    icon=ui.Icons.FILE_COPY_OUTLINED,
                    on_click=lambda e, idx=i: on_context_action("duplicate", idx),
                )
            )
            context_items.append(
                ui.PopupMenuItem(
                    content="删除",
                    icon=ui.Icons.DELETE_OUTLINE,
                    on_click=lambda e, idx=i: on_context_action("delete", idx),
                )
            )
            context_items.append(ui.PopupMenuItem())  # 分隔
        # 对比标签专属：交换左右侧（便于从不同视角审阅差异）
        if is_diff:
            context_items.append(
                ui.PopupMenuItem(
                    content="交换左右侧",
                    icon=ui.Icons.SWAP_HORIZ,
                    on_click=lambda e, idx=i: on_context_action("swap_diff", idx),
                )
            )
            context_items.append(ui.PopupMenuItem())  # 分隔
        # 关闭操作（始终可用）
        context_items.append(
            ui.PopupMenuItem(
                content="关闭",
                icon=ui.Icons.CLOSE,
                on_click=lambda e, idx=i: on_context_action("close", idx),
            )
        )
        context_items.append(
            ui.PopupMenuItem(
                content="关闭其他",
                icon=ui.Icons.CANCEL,
                on_click=lambda e, idx=i: on_context_action("close_others", idx),
            )
        )
        context_items.append(
            ui.PopupMenuItem(
                content="关闭全部",
                icon=ui.Icons.TAB,
                on_click=lambda e: on_context_action("close_all", 0),
            )
        )

        name_color = c.text if is_active else c.muted
        name_weight = ui.FontWeight.W_600 if is_active else ui.FontWeight.NORMAL

        # 对比标签显示双文件名，加宽以减少截断；普通标签保持固定宽度
        tab_width = _TAB_WIDTH_DIFF if is_diff else _TAB_WIDTH

        row_controls: list[ui.Control] = []
        if leading_icon is not None:
            row_controls.append(
                ui.Icon(
                    leading_icon,
                    size=_TAB_ICON,
                    color=leading_color if is_active else c.muted,
                )
            )
        row_controls.append(
            ui.Text(
                value="*" if dirty else "",
                size=_TAB_ICON,
                color=_DIRTY_COLOR,
                font_family=FONT_MAIN,
                weight=ui.FontWeight.BOLD,
                visible=dirty,
            )
        )
        row_controls.append(
            ui.Text(
                value=fname,
                size=_TAB_ICON,
                color=name_color,
                font_family=FONT_MAIN,
                weight=name_weight,
                max_lines=1,
                overflow=ui.TextOverflow.ELLIPSIS,
                tooltip=fname,
                expand=True,
            )
        )
        row_controls.append(
            ui.Container(
                content=_btn_icon(
                    ui.Icons.CLOSE,
                    "关闭",
                    _on_close_click,
                    close_color,
                ),
                opacity=close_opacity,
                animate_opacity=ui.Animation(150, ui.AnimationCurve.EASE_OUT),
            )
        )

        # 浏览器式标签：顶部圆角，无底部强调条（用背景色「连接」到编辑区）
        # height=TOPBAR_H：标签铺满整条顶栏，激活态背景因此**直达底边线**——与下方
        # 编辑区无缝合，同时把标签行整体高度锁在 TOPBAR_H（行高由最高子项决定）。
        tab_content = ui.Container(
            bgcolor=bgcolor,
            border_radius=ui.BorderRadius(
                top_left=_TAB_RADIUS, top_right=_TAB_RADIUS,
                bottom_left=0, bottom_right=0,
            ),
            on_click=_on_tab_click,
            on_hover=_on_tab_hover,
            width=tab_width,
            height=TOPBAR_H,
            padding=ui.Padding.only(
                left=Spacing.MD, right=Spacing.SM,
                top=Spacing.XS, bottom=Spacing.XS,
            ),
            content=ui.Row(
                controls=row_controls,
                spacing=Spacing.XS,
                tight=True,
                vertical_alignment=ui.CrossAxisAlignment.CENTER,
            ),
        )

        tab_controls.append(
            ui.ContextMenu(
                content=tab_content,
                secondary_items=context_items,
            )
        )

    # 尾部「+」新建按钮：固定在滚动区外，与标签栏底色一致。
    # 与标签容器同样锁定 TOPBAR_H，使整条顶栏高度只由这一个常量决定。
    new_btn = ui.Container(
        bgcolor=c.toolbar_bg,
        height=TOPBAR_H,
        alignment=ui.Alignment.CENTER,
        padding=ui.Padding.symmetric(horizontal=Spacing.SM, vertical=0),
        content=_btn_icon(
            ui.Icons.ADD,
            "新建标签  Ctrl+N",
            lambda e: on_new(),
            c.muted,
        ),
    )

    # 标签栏内容：leading 菜单 + 标签列表 + 新建按钮
    row_controls = []
    if leading is not None:
        row_controls.append(leading)
    row_controls.append(
        ui.Row(
            controls=tab_controls,
            spacing=0,
            scroll=ui.ScrollMode.AUTO,
            expand=True,
            vertical_alignment=ui.CrossAxisAlignment.END,
        )
    )
    row_controls.append(new_btn)

    return ui.Container(
        bgcolor=c.toolbar_bg,
        border=only_border(bottom=ui.BorderSide(1, c.border)),
        padding=ui.Padding.only(left=Spacing.SM, right=Spacing.SM, top=0, bottom=0),
        content=ui.Row(
            controls=row_controls,
            spacing=0,
            vertical_alignment=ui.CrossAxisAlignment.CENTER,
        ),
    )


@ui.component
def ConfirmCloseDialog(
    visible: bool,
    file_name: str,
    theme_mode: ui.ThemeMode,
    on_save_and_close: Callable[[], None],
    on_close_without_save: Callable[[], None],
    on_cancel: Callable[[], None],
    save_label: str = "保存并关闭",
):
    """关闭脏标签确认弹层（Stack overlay 模式）。"""
    c = get_colors(theme_mode)
    is_dark = theme_mode == ui.ThemeMode.DARK

    def _text_btn(label: str, on_click: Callable, color: str) -> ui.Control:
        return ui.TextButton(
            label,
            on_click=lambda e: on_click(),
            style=ui.ButtonStyle(color=color),
        )

    return ui.Container(
        visible=visible,
        expand=True,
        bgcolor=ui.Colors.with_opacity(0.28, ui.Colors.BLACK),
        alignment=ui.Alignment.CENTER,
        content=ui.Container(
            width=440,
            bgcolor=c.toolbar_bg,
            border_radius=Radius.XL,
            padding=Spacing.XXXL,
            shadow=card_shadow(Elevation.DIALOG, is_dark),
            content=ui.Column(
                controls=[
                    ui.Row(
                        controls=[
                            ui.Icon(
                                ui.Icons.WARNING_AMBER_ROUNDED,
                                color=_DIRTY_COLOR,
                                size=24,
                            ),
                            ui.Text(
                                value="未保存的修改",
                                size=16,
                                weight=ui.FontWeight.W_600,
                                color=c.text,
                                font_family=FONT_MAIN,
                            ),
                        ],
                        spacing=Spacing.XL,
                        vertical_alignment=ui.CrossAxisAlignment.CENTER,
                    ),
                    ui.Container(height=Spacing.SM),
                    ui.Text(
                        value=f"「{file_name}」包含未保存的修改，关闭前是否保存？",
                        size=13,
                        color=c.muted,
                        font_family=FONT_MAIN,
                    ),
                    ui.Container(height=Spacing.XXL),
                    ui.Row(
                        controls=[
                            ui.Container(expand=True),
                            _text_btn("取消", on_cancel, c.muted),
                            _text_btn("不保存", on_close_without_save, c.muted),
                            ui.ft.Button(
                                save_label,
                                on_click=lambda e: on_save_and_close(),
                                color=ui.Colors.WHITE,
                                bgcolor=c.link,
                            ),
                        ],
                        spacing=Spacing.LG,
                        vertical_alignment=ui.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=0,
                tight=True,
            ),
        ),
    )
