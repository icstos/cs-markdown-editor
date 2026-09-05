"""文档内搜索浮层（悬浮于编辑区右上角的小尺寸紧凑搜索条）。

Ctrl+F 唤起的「当前文档搜索」UI：固定悬浮、不遮挡编辑主体、不阻塞编辑器输入。
面板内容（自左向右）：
- 搜索输入框（自动聚焦，可直接打字）
- 大小写开关（Aa） / 正则开关（.*）
- 上一个 / 下一个按钮（循环跳转，Enter / Shift+Enter 等价）
- 计数文本：实时「当前第 N / 共 M 个匹配」，无匹配显示「0/0 未找到结果」
- 关闭按钮（Esc 等价）

设计约束：
- 纯受控组件（props 驱动），不持有业务状态；
- 输入框聚焦时由 App 置入 native_input_ref 焦点域（views/native_scope），
  保证 Ctrl+A/C/V/X/Z 等只作用于输入框自身，不波及编辑器文档；
- 焦点行为：open 置 True（或 focus_seq 递增）后 use_effect 聚焦输入框；
  关闭时由 App 负责把焦点交还编辑器编辑区。
"""

import contextlib
from collections.abc import Callable

import flet as ft

from styles import (
    FONT_MAIN,
    FONT_MONO,
    Radius,
    Spacing,
    _current_colors,
    card_shadow,
)
from views.native_scope import native_focus_hooks


@ft.component
def FloatingSearch(
    open: bool,
    query: str,
    set_query: Callable[[str], None],
    case_on: bool,
    on_toggle_case: Callable[[bool], None],
    regex_on: bool,
    on_toggle_regex: Callable[[bool], None],
    current_idx: int,  # 0-based 当前匹配序号；无匹配时为 -1
    total: int,
    on_prev: Callable[[], None],
    on_next: Callable[[], None],
    on_close: Callable[[], None],
    # Ctrl+F 每次唤起都会递增 focus_seq，驱动本组件聚焦输入框
    focus_seq: int = 0,
    native_input_ref=None,  # 外部输入焦点域 ref（可空）
    theme_mode: ft.ThemeMode | None = None,
):
    """搜索浮层面板。所有状态由父级（App）持有，本组件只做展示与转发。"""
    c = _current_colors()
    is_dark = theme_mode == ft.ThemeMode.DARK
    field_ref = ft.use_ref(None)

    # 唤起后自动聚焦搜索输入框（open 变化或 focus_seq 递增时触发）
    async def _focus_field():
        if not open:
            return
        f = field_ref.current
        if f is not None:
            with contextlib.suppress(Exception):
                await f.focus()

    ft.use_effect(_focus_field, [open, focus_seq])

    def _text_toggle(label: str, tooltip: str, active: bool, on_toggle) -> ft.Control:
        """文本切换按钮（Aa / .*），active 时主题色半透明背景。"""
        return ft.Container(
            bgcolor=ft.Colors.with_opacity(0.15, c.link) if active else None,
            border_radius=Radius.SM,
            padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=Spacing.XS),
            content=ft.Text(
                label,
                size=11,
                weight=ft.FontWeight.W_600,
                color=c.link if active else c.muted,
                font_family=FONT_MONO,
            ),
            on_click=lambda e: on_toggle(not active),
            ink=True,
            tooltip=tooltip,
        )

    def _nav_btn(icon: str, tooltip: str, on_click, disabled: bool) -> ft.Control:
        return ft.IconButton(
            icon=icon,
            tooltip=tooltip,
            icon_size=14,
            disabled=disabled,
            on_click=on_click,
            style=ft.ButtonStyle(color=c.muted, padding=Spacing.XS),
        )

    # 计数文本：有匹配「当前第 N / 共 M 个匹配」，无匹配「0/0 未找到结果」
    if total > 0:
        count_label = f"第 {current_idx + 1} / 共 {total} 个匹配"
        count_color = c.text
    else:
        count_label = "0/0 未找到结果"
        count_color = c.muted

    focus_h, blur_h = native_focus_hooks(native_input_ref)

    return ft.Container(
        visible=open,
        bgcolor=ft.Colors.with_opacity(0.96, c.toolbar_bg),
        border=ft.Border.all(1, c.border),
        border_radius=Radius.LG,
        padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=Spacing.XS),
        shadow=card_shadow(2, is_dark=is_dark),  # 面板悬浮层次
        content=ft.Row(
            controls=[
                ft.TextField(
                    ref=field_ref,
                    value=query,
                    hint_text="搜索当前文档…",
                    prefix_icon=ft.Icons.SEARCH,
                    dense=True,
                    border=ft.InputBorder.NONE,
                    text_size=13,
                    text_style=ft.TextStyle(font_family=FONT_MAIN),
                    content_padding=ft.Padding.symmetric(
                        horizontal=Spacing.SM, vertical=Spacing.LG
                    ),
                    on_change=lambda e: set_query(e.control.value or ""),
                    on_focus=focus_h,
                    on_blur=blur_h,
                    width=200,
                ),
                ft.Container(width=1, height=18, bgcolor=c.border),
                _text_toggle("Aa", "区分大小写", case_on, on_toggle_case),
                _text_toggle(".*", "正则表达式", regex_on, on_toggle_regex),
                ft.Container(width=1, height=18, bgcolor=c.border),
                _nav_btn(
                    ft.Icons.KEYBOARD_ARROW_UP,
                    "上一个 (Shift+Enter)",
                    lambda e: on_prev(),
                    disabled=total == 0,
                ),
                _nav_btn(
                    ft.Icons.KEYBOARD_ARROW_DOWN,
                    "下一个 (Enter)",
                    lambda e: on_next(),
                    disabled=total == 0,
                ),
                ft.Text(
                    count_label,
                    size=11,
                    color=count_color,
                    font_family=FONT_MONO,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    width=118,
                ),
                _nav_btn(ft.Icons.CLOSE, "关闭 (Esc)", lambda e: on_close(), disabled=False),
            ],
            spacing=Spacing.SM,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
