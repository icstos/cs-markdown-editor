"""格式工具栏。

按钮分两组：
- 块级（标题 / 列表 / 引用 / 代码块 / 分隔线）：改变当前行块类型。
- 行内（加粗 / 斜体 / 行内代码 / 链接 / 删除线）：在激活段上包裹或解包语法。

以紧凑、低干扰的方式承载常用格式操作，符合 Typora 式写作节奏。

行内按钮 tooltip 动态读取 ShortcutManager 自定义键位：用户改键位后 tooltip
立即反映新组合，与设置页保持一致。
"""

from typing import Callable

import flet as ft

from styles import Radius, Spacing, _current_colors


# 修饰键集合：capitalize 显示（Ctrl/Shift/Alt），与 settings_dialog._format_combo_display 一致
_MOD_KEYS = {"ctrl", "shift", "alt"}


def _format_combo(combo: str) -> str:
    """把规范化组合键字符串转为展示形式：ctrl+shift+s → Ctrl+Shift+S。

    与 settings_dialog._format_combo_display 逻辑一致（空串返回 "" 而非"未绑定"，
    供 tooltip 拼接判断）。
    """
    if not combo:
        return ""
    parts = []
    for part in combo.split("+"):
        if part in _MOD_KEYS:
            parts.append(part.capitalize())
        elif part == ",":
            parts.append(",")
        else:
            parts.append(part.upper())
    return "+".join(parts)


def _btn(
    icon: str, tooltip: str, on_click: Callable[[], None], toggle_on: bool = False
) -> ft.Control:
    c = _current_colors()  # 当前主题颜色（亮/暗）
    return ft.IconButton(
        icon=icon,
        tooltip=tooltip,
        on_click=lambda e: on_click(),
        icon_size=18,
        style=ft.ButtonStyle(
            color=c.link if toggle_on else c.muted,
            bgcolor=ft.Colors.with_opacity(0.10 if toggle_on else 0.0, c.link),
            padding=Spacing.SM,
            shape=ft.RoundedRectangleBorder(radius=Radius.LG),
            animation_duration=160,
        ),
    )


def _divider() -> ft.Control:
    c = _current_colors()  # 当前主题颜色（亮/暗）
    return ft.Container(
        width=1, height=20, bgcolor=c.border, margin=ft.Margin.symmetric(horizontal=Spacing.SM)
    )


@ft.component
def Toolbar(
    shortcut_mgr,
    on_h1: Callable[[], None],
    on_h2: Callable[[], None],
    on_h3: Callable[[], None],
    on_paragraph: Callable[[], None],
    on_list: Callable[[], None],
    on_quote: Callable[[], None],
    on_code_block: Callable[[], None],
    on_hr: Callable[[], None],
    on_bold: Callable[[], None],
    on_italic: Callable[[], None],
    on_highlight: Callable[[], None],
    on_code: Callable[[], None],
    on_link: Callable[[], None],
    on_strike: Callable[[], None],
):
    """格式工具栏：仅返回按钮 Row，外层 _tool_area 提供容器与边框。

    shortcut_mgr：ShortcutManager 实例，用于读取行内格式按钮的自定义键位。
    """

    def _combo(action_id: str) -> str:
        """读取并格式化某行内格式动作的当前键位；无绑定时返回空串。"""
        if shortcut_mgr is None:
            return ""
        return _format_combo(shortcut_mgr.shortcut("edit", action_id))

    def _tip(label: str, action_id: str) -> str:
        """组合 tooltip：动作名 + 键位（无绑定时仅显示动作名）。"""
        combo = _combo(action_id)
        return f"{label}  {combo}" if combo else label

    return ft.Row(
        controls=[
            _btn(ft.Icons.TITLE, "一级标题  Ctrl+1", on_h1),
            _btn(ft.Icons.FORMAT_SIZE, "二级标题  Ctrl+2", on_h2),
            _btn(ft.Icons.TEXT_FIELDS, "三级标题  Ctrl+3", on_h3),
            _btn(ft.Icons.FORMAT_ALIGN_LEFT, "正文段落", on_paragraph),
            _divider(),
            _btn(ft.Icons.FORMAT_LIST_BULLETED, "无序列表", on_list),
            _btn(ft.Icons.FORMAT_QUOTE, "引用", on_quote),
            _btn(ft.Icons.CODE, "代码块", on_code_block),
            _btn(ft.Icons.HORIZONTAL_RULE, "分隔线", on_hr),
            _divider(),
            _btn(ft.Icons.FORMAT_BOLD, _tip("加粗", "format_bold"), on_bold),
            _btn(ft.Icons.FORMAT_ITALIC, _tip("斜体", "format_italic"), on_italic),
            _btn(ft.Icons.HIGHLIGHT, _tip("高亮", "format_highlight"), on_highlight),
            _btn(ft.Icons.CODE, _tip("行内代码", "format_code"), on_code),
            _btn(ft.Icons.LINK, _tip("链接", "format_link"), on_link),
            _btn(ft.Icons.FORMAT_STRIKETHROUGH, _tip("删除线", "format_strike"), on_strike),
        ],
        spacing=2,
        wrap=True,
        run_spacing=4,
    )
