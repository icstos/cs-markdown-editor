"""文档内搜索浮层（悬浮于编辑区右上角的搜索条）。

Ctrl+F 唤起的「当前文档搜索」UI：固定悬浮、不遮挡编辑主体、不阻塞编辑器输入。
面板内容（自左向右）：
- 搜索输入框（自动聚焦，可直接打字）
- 大小写开关（Aa） / 正则开关（.*）
- 计数：实时「当前 / 总数」，无匹配显示「无匹配」
- 上一个 / 下一个按钮（循环跳转，Enter / Shift+Enter 等价）
- 关闭按钮（Esc 等价）

视觉：整条面板**就是一个搜索框**（填底 + 细边 + 大圆角 + 悬浮阴影），聚焦时
外框与放大镜一起转主题色并加一圈 focus ring —— 内部不再套第二层描边，避免
「框里还有框」的碎片感。侧边栏搜索框（views/_widgets._search_box）复用同一套
外壳与紧凑按钮（_search_shell / _compact_btn / _option_pill）。

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
    Elevation,
    Radius,
    Spacing,
    _current_colors,
    card_shadow,
    focus_ring,
)
from views._widgets import _compact_btn, _option_pill
from views.native_scope import native_focus_hooks

# 输入区宽度（含放大镜）：搜索词通常不长，留够一行可见即可；再宽就会把
# 计数与导航挤到面板边缘以外。
_FIELD_W = 190
# 计数区固定宽度：结果数变化时不改变面板宽度（否则整条搜索结果一亮一暗地抖）
_COUNT_W = 58
# 面板高度（逻辑 px）：比侧边栏搜索框（30）高一档，因为它是浮层主角，
# 也需要容下 22px 的选项 pill 与 24px 的图标按钮。
_PANEL_H = 40


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
    focused, set_focused = ft.use_state(False)

    # 唤起后自动聚焦搜索输入框（open 变化或 focus_seq 递增时触发）
    async def _focus_field():
        if not open:
            return
        f = field_ref.current
        if f is not None:
            with contextlib.suppress(Exception):
                await f.focus()

    ft.use_effect(_focus_field, [open, focus_seq])

    focus_h, blur_h = native_focus_hooks(native_input_ref)

    def _on_focus(e=None):
        set_focused(True)
        if focus_h is not None:
            focus_h(e)

    def _on_blur(e=None):
        set_focused(False)
        if blur_h is not None:
            blur_h(e)

    def _divider() -> ft.Control:
        """组间分隔：1px 细线，比边框更弱，只切断节奏不制造格子。"""
        return ft.Container(
            width=1,
            height=18,
            bgcolor=ft.Colors.with_opacity(0.8, c.border),
        )

    def _count() -> ft.Control:
        """计数区：数字用等宽体（跳动不抖），无匹配时才出现中文提示。"""
        if not query.strip():
            body: ft.Control = ft.Container()
        elif total > 0:
            body = ft.Row(
                controls=[
                    ft.Text(
                        str(current_idx + 1),
                        size=12,
                        weight=ft.FontWeight.W_600,
                        color=c.text,
                        font_family=FONT_MONO,
                        max_lines=1,
                    ),
                    ft.Text(
                        f"/ {total}",
                        size=11,
                        color=c.muted,
                        font_family=FONT_MONO,
                        max_lines=1,
                    ),
                ],
                spacing=Spacing.SM,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        else:
            body = ft.Text(
                "无匹配",
                size=11,
                color=c.muted,
                font_family=FONT_MAIN,
                max_lines=1,
            )
        return ft.Container(
            width=_COUNT_W,
            alignment=ft.Alignment.CENTER_RIGHT,
            content=body,
        )

    has_nav = total > 0

    return ft.Container(
        visible=open,
        height=_PANEL_H,
        bgcolor=c.surface,
        border=ft.Border.all(1, c.link if focused else c.border),
        border_radius=Radius.XL,
        padding=ft.Padding.symmetric(horizontal=14, vertical=0),
        shadow=card_shadow(Elevation.MEDIUM, is_dark=is_dark)
        + (focus_ring(c.link, is_dark) if focused else []),
        alignment=ft.Alignment.CENTER_LEFT,
        content=ft.Row(
            controls=[
                # 输入区：放大镜 + 无边框输入框（底色/描边一律由外壳给）
                ft.Container(
                    width=_FIELD_W,
                    content=ft.Row(
                        controls=[
                            ft.Icon(
                                ft.Icons.SEARCH,
                                size=15,
                                color=c.link if focused else c.muted,
                            ),
                            ft.TextField(
                                ref=field_ref,
                                value=query,
                                hint_text="搜索当前文档…",
                                hint_style=ft.TextStyle(
                                    size=13, color=c.muted, font_family=FONT_MAIN
                                ),
                                dense=True,
                                border=ft.NoInputBorder(),
                                filled=False,
                                text_size=13,
                                text_style=ft.TextStyle(
                                    size=13, color=c.text, font_family=FONT_MAIN
                                ),
                                content_padding=ft.Padding.symmetric(
                                    horizontal=0, vertical=Spacing.LG
                                ),
                                cursor_color=c.link,
                                on_change=lambda e: set_query(e.control.value or ""),
                                on_focus=_on_focus,
                                on_blur=_on_blur,
                                expand=True,
                            ),
                        ],
                        spacing=Spacing.MD,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ),
                _divider(),
                _option_pill("Aa", "区分大小写", case_on, on_toggle_case, c),
                _option_pill(".*", "正则表达式", regex_on, on_toggle_regex, c),
                _divider(),
                _count(),
                _compact_btn(
                    ft.Icons.KEYBOARD_ARROW_UP,
                    "上一个 (Shift+Enter)",
                    lambda e: on_prev(),
                    c,
                    box=24,
                    disabled=not has_nav,
                ),
                _compact_btn(
                    ft.Icons.KEYBOARD_ARROW_DOWN,
                    "下一个 (Enter)",
                    lambda e: on_next(),
                    c,
                    box=24,
                    disabled=not has_nav,
                ),
                _divider(),
                _compact_btn(
                    ft.Icons.CLOSE, "关闭 (Esc)", lambda e: on_close(), c, box=24
                ),
            ],
            spacing=Spacing.SM,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
