"""顶部多文档标签栏 + 关闭确认弹层。

- TabBar：横向标签列表，每标签显示文件名，未保存修改前置 `*`（警告色）；
  激活态有底部 primary 强调条；非激活悬停时高亮。尾部固定「+」新建按钮。
  每个标签包裹 ft.ContextMenu，右键提供「关闭 / 关闭其他 / 关闭全部 / 复制路径」。
- ConfirmCloseDialog：关闭脏标签时的半透明遮罩确认弹层（保存并关闭 / 不保存 / 取消），
  样式与 views/settings_dialog.py 的 overlay 风格保持一致。

设计要点：
- TabBar 内部用单一 hover_index state 管理悬停高亮，避免每个标签独立 state 带来的
  列表协调复杂度；state 变化只重渲染 TabBar 自身，不波及 App。
- 关闭按钮 on_click 调 e.stop_propagation() 阻止冒泡到标签 Container 的 on_click，
  防止「点关闭误触发选中」。
"""

import os
from collections.abc import Callable

import flet as ft

from styles import FONT_MAIN, get_colors, only_border

_DIRTY_COLOR = "#FF9F0A"  # 未保存修改星号色（亮暗通用警示橙）


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
):
    """顶部标签栏。

    tabs: 每项为 {"file_path": str|None, "dirty": bool}（仅展示用元数据）。
    on_context_action(action, i)：action ∈ {"close","close_others","close_all","copy_path"}。
    """
    c = get_colors(theme_mode)
    hover_index, set_hover_index = ft.use_state(-1)

    def _btn_icon(icon: str, tooltip: str, on_click: Callable, color: str) -> ft.Control:
        return ft.IconButton(
            icon=icon,
            tooltip=tooltip,
            icon_size=14,
            on_click=on_click,
            style=ft.ButtonStyle(
                color=color,
                bgcolor=ft.Colors.with_opacity(0.0, c.text),
                padding=2,
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
        )

    tab_controls: list[ft.Control] = []
    for i, t in enumerate(tabs):
        path = t.get("file_path")
        dirty = bool(t.get("dirty"))
        fname = _file_name(path)
        is_active = i == active_index
        is_hover = i == hover_index

        # 背景：激活 > 悬停 > 透明
        if is_active:
            bgcolor = c.surface
        elif is_hover:
            bgcolor = c.hover
        else:
            bgcolor = ft.Colors.with_opacity(0.0, c.text)
        # 底部强调条：激活=primary 2px，非激活=透明 2px（保持高度一致，避免布局抖动）
        bottom_border = ft.BorderSide(2, c.link if is_active else ft.Colors.TRANSPARENT)

        # 关闭按钮颜色：激活或悬停时提亮
        close_color = c.text if (is_active or is_hover) else c.muted

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

        # 右键菜单项
        context_items = [
            ft.PopupMenuItem(
                content="关闭",
                on_click=lambda e, idx=i: on_context_action("close", idx),
            ),
            ft.PopupMenuItem(
                content="关闭其他",
                on_click=lambda e, idx=i: on_context_action("close_others", idx),
            ),
            ft.PopupMenuItem(
                content="关闭全部",
                on_click=lambda e: on_context_action("close_all", 0),
            ),
            ft.PopupMenuItem(),  # 分隔
            ft.PopupMenuItem(
                content="复制路径",
                on_click=lambda e, idx=i: on_context_action("copy_path", idx),
            ),
        ]

        name_color = c.text if is_active else c.muted
        name_weight = ft.FontWeight.W_600 if is_active else ft.FontWeight.NORMAL

        tab_content = ft.Container(
            bgcolor=bgcolor,
            border=only_border(bottom=bottom_border),
            on_click=_on_tab_click,
            on_hover=_on_tab_hover,
            padding=ft.Padding.only(left=12, right=6, top=6, bottom=6),
            content=ft.Row(
                controls=[
                    ft.Text(
                        value="*" if dirty else "",
                        size=13,
                        color=_DIRTY_COLOR,
                        font_family=FONT_MAIN,
                        weight=ft.FontWeight.BOLD,
                        visible=dirty,
                    ),
                    ft.Text(
                        value=fname,
                        size=13,
                        color=name_color,
                        font_family=FONT_MAIN,
                        weight=name_weight,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        tooltip=path or "未命名.md",
                    ),
                    _btn_icon(
                        ft.Icons.CLOSE,
                        "关闭",
                        _on_close_click,
                        close_color,
                    ),
                ],
                spacing=4,
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

    # 尾部「+」新建按钮：固定在滚动区外
    new_btn = ft.Container(
        bgcolor=c.toolbar_bg,
        border=only_border(bottom=ft.BorderSide(2, ft.Colors.TRANSPARENT)),
        padding=ft.Padding.symmetric(horizontal=4, vertical=4),
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
        padding=ft.Padding.only(left=4, right=4, top=0, bottom=0),
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
            border_radius=12,
            padding=24,
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=24,
                color=ft.Colors.with_opacity(0.18, ft.Colors.BLACK),
                offset=ft.Offset(0, 8),
            ),
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
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(height=4),
                    ft.Text(
                        value=f"「{file_name}」包含未保存的修改，关闭前是否保存？",
                        size=13,
                        color=c.muted,
                        font_family=FONT_MAIN,
                    ),
                    ft.Container(height=16),
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
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=0,
            ),
        ),
    )
