"""设置面板：编辑 / 外观 / 行为 / 快捷键 / 高级五个 tab。

从 main.py 抽出原 settings_view 块（~700 行）。SettingsDialog 接收 ShortcutManager
实例与回调，main.py 仅单行调用。
"""

from collections.abc import Callable

import flet as ft

from services.shortcuts import ShortcutManager
from styles import get_colors

_SECTIONS = {
    "edit": ("编辑", "调整编辑区布局与写作行为。"),
    "appearance": ("外观", "控制主题、字体与视觉密度。"),
    "behavior": ("行为", "控制保存、专注与工具栏行为。"),
    "shortcuts": ("快捷键", "查看常用快捷键说明。"),
    "advanced": ("高级", "预留代码主题、导出等高级选项。"),
}

_TAB_ICONS = [
    ("edit", "编辑", ft.Icons.EDIT),
    ("appearance", "外观", ft.Icons.PALETTE),
    ("behavior", "行为", ft.Icons.TUNE),
    ("shortcuts", "快捷键", ft.Icons.KEYBOARD),
    ("advanced", "高级", ft.Icons.SETTINGS),
]


@ft.component
def SettingsDialog(
    open_state: bool,
    tab: str,
    settings: dict,
    theme_mode: ft.ThemeMode,
    shortcut_focus: tuple,
    shortcut_mgr: ShortcutManager,
    on_close: Callable[[], None],
    on_select_tab: Callable[[str], None],
    on_update: Callable[[str, object], None],
    on_reset_all: Callable[[], None],
    on_reset_shortcuts: Callable[[], None],
    on_import: Callable[[], None],
    on_export: Callable[[], None],
):
    """设置面板弹层。

    open_state: 是否显示；tab: 当前激活 tab；settings: 当前设置字典；
    shortcut_focus: (layer, action_id) 用于冲突定位高亮；shortcut_mgr: 快捷键管理器。
    on_update: 顶层设置项更新（key, value），由 main.py 的 update_setting 处理。
    快捷键更新直接通过 shortcut_mgr.update(layer, action, combo) 调用，内部回调 on_update。
    """
    c = get_colors(theme_mode)
    current_title, current_desc = _SECTIONS.get(tab, _SECTIONS["edit"])

    return ft.Container(
        visible=open_state,
        expand=True,
        bgcolor=ft.Colors.with_opacity(0.28, ft.Colors.BLACK),
        alignment=ft.Alignment.CENTER,
        content=ft.Container(
            width=1020,
            height=720,
            bgcolor=c.toolbar_bg,
            border_radius=18,
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=24,
                color=ft.Colors.with_opacity(0.18, ft.Colors.BLACK),
                offset=ft.Offset(0, 8),
            ),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Row(
                spacing=0,
                controls=[
                    _sidebar(c, tab, on_select_tab, on_reset_all),
                    ft.Container(width=1, bgcolor=c.border),
                    ft.Container(
                        expand=True,
                        padding=24,
                        content=ft.Column(
                            controls=[
                                _header(current_title, current_desc, c, on_close),
                                ft.Container(height=8),
                                _panel(tab, settings, theme_mode, shortcut_focus,
                                       shortcut_mgr, on_update, on_reset_shortcuts,
                                       on_import, on_export),
                            ],
                            scroll=ft.ScrollMode.AUTO,
                        ),
                    ),
                ],
            ),
        ),
    )


# ---- 子区域 ----

def _sidebar(c, tab: str, on_select_tab, on_reset_all) -> ft.Control:
    return ft.Container(
        width=250,
        bgcolor=ft.Colors.with_opacity(0.18, c.border),
        padding=20,
        content=ft.Column(
            expand=True,
            controls=[
                ft.Text("设置", size=22, weight=ft.FontWeight.W_700),
                ft.Text("Typora 风格的可配置中心", size=12, color=c.muted),
                ft.Container(height=18),
                *[_tab_button(t, label, icon, c, tab, on_select_tab) for t, label, icon in _TAB_ICONS],
                ft.Container(expand=True),
                ft.TextButton("恢复默认", on_click=lambda e: on_reset_all()),
            ],
            spacing=8,
        ),
    )


def _tab_button(t: str, label: str, icon: str, c, current_tab: str, on_select_tab) -> ft.Control:
    active = current_tab == t
    return ft.Container(
        border_radius=10,
        bgcolor=ft.Colors.with_opacity(0.12, c.link) if active else None,
        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
        content=ft.Row(
            controls=[
                ft.Icon(icon=icon, size=16,
                        color=c.link if active else c.muted),
                ft.TextButton(label, on_click=lambda e: on_select_tab(t)),
            ],
            spacing=8,
        ),
    )


def _header(title: str, desc: str, c, on_close) -> ft.Control:
    return ft.Row(
        controls=[
            ft.Column(
                controls=[
                    ft.Text(title, size=20, weight=ft.FontWeight.W_700),
                    ft.Text(desc, size=12, color=c.muted),
                ],
                spacing=2,
            ),
            ft.Container(expand=True),
            ft.IconButton(icon=ft.Icons.CLOSE, on_click=lambda e: on_close()),
        ]
    )


def _panel(
    tab: str, settings: dict, theme_mode: ft.ThemeMode, shortcut_focus: tuple,
    shortcut_mgr: ShortcutManager, on_update, on_reset_shortcuts, on_import, on_export,
) -> ft.Control:
    if tab == "edit":
        return _edit_panel(settings, theme_mode, on_update)
    if tab == "appearance":
        return _appearance_panel(settings, theme_mode, on_update)
    if tab == "behavior":
        return _behavior_panel(settings, theme_mode, on_update)
    if tab == "shortcuts":
        return _shortcuts_panel(theme_mode)
    return _advanced_panel(theme_mode, shortcut_focus, shortcut_mgr,
                           on_update, on_reset_shortcuts, on_import, on_export)


# ---- 编辑 tab ----

def _edit_panel(settings: dict, theme_mode: ft.ThemeMode, on_update) -> ft.Control:
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("布局", size=14, weight=ft.FontWeight.W_600),
                _slider_row("内容宽度", 96, settings["content_max_width"],
                             lambda v: on_update("content_max_width", int(v)),
                             minv=680, maxv=1200, divisions=13, theme_mode=theme_mode),
                _slider_row("左右边距", 96, settings["content_padding"],
                             lambda v: on_update("content_padding", int(v)),
                             minv=12, maxv=64, divisions=13, theme_mode=theme_mode),
                _slider_row("顶部边距", 96, settings["content_padding_top"],
                             lambda v: on_update("content_padding_top", int(v)),
                             minv=8, maxv=48, divisions=10, theme_mode=theme_mode),
                ft.Switch(label="显示底部状态栏", value=settings["show_footer"],
                          on_change=lambda e: on_update("show_footer", e.control.value)),
            ],
            spacing=12,
        ),
    )


# ---- 外观 tab ----

def _appearance_panel(settings: dict, theme_mode: ft.ThemeMode, on_update) -> ft.Control:
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("字体与排版", size=14, weight=ft.FontWeight.W_600),
                _slider_row("正文大小", 96, settings["body_font_size"],
                             lambda v: on_update("body_font_size", int(v)),
                             minv=14, maxv=20, divisions=6, theme_mode=theme_mode),
                _slider_row("行高", 96, settings["line_height"],
                             lambda v: on_update("line_height", round(float(v), 1)),
                             minv=1.2, maxv=2.0, divisions=8, theme_mode=theme_mode),
                _dropdown_row("字体", 96, settings["font_family"],
                              ["Alibaba", "Sans", "Serif", "Monospace"],
                              lambda v: on_update("font_family", v)),
                _dropdown_row("代码主题(暗)", 96, settings["code_theme_dark"],
                              ["ATOM_ONE_DARK", "GITHUB", "VS2015"],
                              lambda v: on_update("code_theme_dark", v)),
                _dropdown_row("代码主题(亮)", 96, settings["code_theme_light"],
                              ["GITHUB", "ATOM_ONE_LIGHT", "VS2015"],
                              lambda v: on_update("code_theme_light", v)),
            ],
            spacing=12,
        ),
    )


# ---- 行为 tab ----

def _behavior_panel(settings: dict, theme_mode: ft.ThemeMode, on_update) -> ft.Control:
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("行为", size=14, weight=ft.FontWeight.W_600),
                ft.Switch(label="自动保存", value=settings["auto_save"],
                          on_change=lambda e: on_update("auto_save", e.control.value)),
                ft.Switch(label="记住聚焦模式", value=settings["remember_focus_mode"],
                          on_change=lambda e: on_update("remember_focus_mode", e.control.value)),
                ft.Switch(label="显示工具栏", value=settings["show_toolbar"],
                          on_change=lambda e: on_update("show_toolbar", e.control.value)),
                ft.Switch(label="显示行号", value=settings["show_line_numbers"],
                          on_change=lambda e: on_update("show_line_numbers", e.control.value)),
                _slider_row("自动保存间隔(秒)", 140, 10, lambda v: None,
                             minv=3, maxv=60, divisions=19, theme_mode=theme_mode),
                _dropdown_row("导出默认格式", 140, settings["export_format"],
                              ["html", "pdf", "md"],
                              lambda v: on_update("export_format", v)),
            ],
            spacing=12,
        ),
    )


# ---- 快捷键 tab ----

def _shortcuts_panel(theme_mode: ft.ThemeMode) -> ft.Control:
    c = get_colors(theme_mode)
    hints = [
        "Ctrl+S 保存", "Ctrl+O 打开", "Ctrl+N 新建", "Ctrl+Z 撤销",
        "Ctrl+Y / Ctrl+Shift+Z 重做",
        "编辑态：Ctrl+Enter 原文模式 / Esc 侧边栏",
        "浏览态：Ctrl+/ 原文模式 / Ctrl+, 设置",
        "Ctrl+B 切换侧边栏", "Ctrl+Shift+L 切换主题", "Ctrl+K 聚焦模式",
        "Tab / Shift+Tab 列表缩进", "Home / End 段首段尾", "Ctrl+1/2/3 标题",
    ]
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("常用快捷键", size=14, weight=ft.FontWeight.W_600),
                *[ft.Text(h, size=12, color=c.text) for h in hints],
            ],
            spacing=8,
        ),
    )


# ---- 高级 tab ----

def _advanced_panel(
    theme_mode: ft.ThemeMode, shortcut_focus: tuple,
    shortcut_mgr: ShortcutManager, on_update, on_reset_shortcuts, on_import, on_export,
) -> ft.Control:
    c = get_colors(theme_mode)
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Column(
                            controls=[
                                ft.Text("动作管理", size=14, weight=ft.FontWeight.W_600),
                                ft.Text("统一查看并管理浏览态 / 编辑态动作、默认键位与冲突状态。",
                                        size=12, color=c.muted),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        ft.Container(expand=True),
                        ft.TextButton("导入方案", on_click=lambda e: on_import()),
                        ft.TextButton("导出方案", on_click=lambda e: on_export()),
                        ft.TextButton("恢复默认快捷键", on_click=lambda e: on_reset_shortcuts()),
                    ]
                ),
                ft.Container(height=6),
                ft.Row(
                    controls=[
                        ft.Container(
                            expand=True,
                            content=ft.TextField(
                                hint_text="搜索动作、说明、快捷键…",
                                dense=True,
                                border=ft.InputBorder.OUTLINE,
                                prefix_icon=ft.Icons.SEARCH,
                                on_change=lambda e: None,
                            ),
                        ),
                    ],
                    spacing=10,
                ),
                ft.Row(
                    controls=[
                        _conflict_card("浏览态", shortcut_mgr.conflict_summary("browse"), c),
                        _conflict_card("编辑态", shortcut_mgr.conflict_summary("edit"), c),
                    ],
                    spacing=10,
                ),
                ft.Container(height=4),
                ft.Container(
                    expand=True,
                    border_radius=12,
                    bgcolor=ft.Colors.with_opacity(0.04, c.text),
                    padding=ft.Padding.all(12),
                    content=ft.Column(
                        controls=_action_rows(shortcut_mgr, theme_mode, shortcut_focus, on_update),
                        spacing=8,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                ),
            ],
            spacing=10,
        ),
    )


def _conflict_card(label: str, summary: str | None, c) -> ft.Control:
    return ft.Container(
        expand=True,
        padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        border_radius=10,
        bgcolor=ft.Colors.with_opacity(0.08, c.link),
        content=ft.Column(
            controls=[
                ft.Text(label, size=12, weight=ft.FontWeight.W_700),
                ft.Text(summary or "无冲突", size=11,
                        color="#E66A00" if summary else c.muted),
            ],
            spacing=2,
        ),
    )


def _action_rows(
    shortcut_mgr: ShortcutManager, theme_mode: ft.ThemeMode,
    shortcut_focus: tuple, on_update,
) -> list[ft.Control]:
    c = get_colors(theme_mode)
    rows: list[ft.Control] = []
    for layer in shortcut_mgr.layers():
        layer_actions = shortcut_mgr.actions_for_layer(layer)
        cmap = shortcut_mgr.conflict_map(layer)
        rows.append(
            ft.Container(
                padding=ft.Padding.only(top=4, bottom=4),
                content=ft.Text(
                    "浏览态" if layer == "browse" else "编辑态",
                    size=13, weight=ft.FontWeight.W_700,
                ),
            )
        )
        for action in layer_actions:
            current = shortcut_mgr.shortcut(layer, action.id)
            default = action.default.get(layer, "")
            is_conflict = bool(current and current in cmap)
            rows.append(_action_row(action, layer, current, default, is_conflict, c, shortcut_mgr))
    return rows


def _action_row(
    action, layer: str, current: str, default: str, is_conflict: bool, c,
    shortcut_mgr: ShortcutManager,
) -> ft.Control:
    return ft.Container(
        bgcolor=ft.Colors.with_opacity(0.10, ft.Colors.RED) if is_conflict else None,
        border_radius=10,
        padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Column(
                            controls=[
                                ft.Text(action.label, size=13, weight=ft.FontWeight.W_600),
                                ft.Text(f"{action.category} · {action.description}",
                                        size=11, color=c.muted),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        ft.TextField(
                            value=current,
                            hint_text=default or "未绑定",
                            dense=True,
                            border=ft.InputBorder.UNDERLINE,
                            text_size=12,
                            width=160,
                            border_color="#E66A00" if is_conflict else None,
                            focused_border_color="#E66A00" if is_conflict else c.link,
                            on_submit=lambda e, l=layer, a=action.id: (
                                shortcut_mgr.update(l, a, (e.control.value or "").lower())
                            ),
                        ),
                        ft.TextButton(
                            "恢复默认",
                            on_click=lambda e, l=layer, a=action.id: (
                                shortcut_mgr.reset(l, a)
                            ),
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            ],
            spacing=6,
        ),
    )


# ---- 通用行控件 ----

def _slider_row(
    label: str, label_width: int, value, on_change,
    *, minv: float, maxv: float, divisions: int, theme_mode: ft.ThemeMode,
) -> ft.Control:
    return ft.Row(
        [
            ft.Text(label, width=label_width),
            ft.Slider(
                min=minv, max=maxv, divisions=divisions,
                value=value, expand=True,
                on_change=lambda e: on_change(e.control.value),
            ),
            ft.Text(str(value)),
        ]
    )


def _dropdown_row(
    label: str, label_width: int, value, options: list[str], on_select,
) -> ft.Control:
    return ft.Row(
        [
            ft.Text(label, width=label_width),
            ft.Dropdown(
                options=[ft.dropdown.Option(o) for o in options],
                value=value, expand=True,
                on_select=lambda e: on_select(e.control.value),
            ),
        ]
    )
