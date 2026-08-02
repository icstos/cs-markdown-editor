"""文件操作对话框（输入对话框 + 确认对话框）。

为右键菜单的文件操作提供统一的对话框 UI：
- FileActionDialog(mode="input")：新建文件/文件夹、重命名等需要用户输入名称的场景
- FileActionDialog(mode="confirm")：删除等需要用户确认的场景

视觉风格与 views/tab_bar.py 的 ConfirmCloseDialog 保持一致：
半透明遮罩 + 卡片弹层 + 圆角阴影。
"""

from collections.abc import Callable

import flet as ft

from styles import (
    FONT_MAIN,
    Elevation,
    Radius,
    Spacing,
    card_shadow,
    get_colors,
    only_border,
)

# 危险操作（删除）的确认按钮红色
_DANGER_COLOR = "#E5484D"


@ft.component
def FileActionDialog(
    visible: bool,
    mode: str,
    title: str,
    theme_mode: ft.ThemeMode,
    confirm_label: str,
    on_confirm: Callable,
    on_cancel: Callable[[], None],
    # input 模式专用
    input_label: str = "",
    input_value: str = "",
    input_hint: str = "",
    location_hint: str | None = None,
    # confirm 模式专用
    message: str = "",
    danger: bool = False,
    icon: str = ft.Icons.HELP_OUTLINE,
):
    """文件操作统一对话框。

    mode="input"：显示 TextField 供用户输入名称，确认时调用 on_confirm(value: str)。
    mode="confirm"：显示确认消息，确认时调用 on_confirm()。

    on_confirm 的签名因 mode 而异：
    - input 模式：on_confirm(value: str) — 传入用户输入的文本
    - confirm 模式：on_confirm() — 无参数

    danger=True 时确认按钮使用红色（如删除操作）。
    location_hint 用于 input 模式，显示目标路径（如"在 /path/to/dir 创建"）。
    """
    c = get_colors(theme_mode)
    is_dark = theme_mode == ft.ThemeMode.DARK
    # input 模式内部 state 跟踪输入值（不受外部 input_value 限制，支持自由编辑）
    text_value, set_text_value = ft.use_state(input_value)

    def _text_btn(label: str, on_click: Callable, color: str) -> ft.Control:
        return ft.TextButton(
            label,
            on_click=lambda e: on_click(),
            style=ft.ButtonStyle(color=color),
        )

    def _on_submit(e):
        """TextField 回车提交。"""
        if mode == "input":
            val = text_value.strip()
            if val:
                on_confirm(val)
        else:
            on_confirm()

    # 图标颜色：危险操作用红色，普通操作用主题色
    icon_color = _DANGER_COLOR if danger else c.link

    # ---- 确认按钮 ----
    confirm_color = _DANGER_COLOR if danger else ft.Colors.WHITE
    confirm_bgcolor = _DANGER_COLOR if danger else c.link
    confirm_btn = ft.Button(
        confirm_label,
        on_click=lambda e: _on_submit(e),
        color=confirm_color,
        bgcolor=confirm_bgcolor,
    )

    # ---- 内容区 ----
    if mode == "input":
        content_body = ft.Column(
            controls=[
                ft.Text(
                    value=location_hint,
                    size=11,
                    color=c.muted,
                    font_family=FONT_MAIN,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                )
                if location_hint
                else ft.Container(height=0),
                ft.Container(height=Spacing.SM),
                ft.TextField(
                    value=text_value,
                    hint_text=input_hint,
                    label=input_label,
                    dense=True,
                    border=ft.InputBorder.OUTLINE,
                    text_size=13,
                    text_style=ft.TextStyle(font_family=FONT_MAIN),
                    autofocus=True,
                    on_change=lambda e: set_text_value(e.control.value or ""),
                    on_submit=_on_submit,
                ),
            ],
            spacing=0,
        )
    else:
        content_body = ft.Column(
            controls=[
                ft.Text(
                    value=message,
                    size=13,
                    color=c.text,
                    font_family=FONT_MAIN,
                ),
            ],
            spacing=0,
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
                            ft.Icon(icon, color=icon_color, size=24),
                            ft.Text(
                                value=title,
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
                    content_body,
                    ft.Container(height=Spacing.XXL),
                    ft.Row(
                        controls=[
                            ft.Container(expand=True),
                            _text_btn("取消", on_cancel, c.muted),
                            confirm_btn,
                        ],
                        spacing=Spacing.LG,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=0,
            ),
        ),
    )
