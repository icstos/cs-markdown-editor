"""设置面板：编辑 / 外观 / 行为 / 快捷键 / 高级五个 tab。

从 main.py 抽出原 settings_view 块（~700 行）。SettingsDialog 接收 ShortcutManager
实例与回调，main.py 仅单行调用。
"""

from collections.abc import Callable

import flet as ft

from services.shortcuts import ShortcutManager
from styles import Elevation, Radius, Spacing, card_shadow, get_colors


def _all_border(width: float, color: str) -> ft.Border:
    """便捷构造四边相同 Border（项目 styles.only_border 仅支持单边）。"""
    side = ft.BorderSide(width, color)
    return ft.Border(top=side, right=side, bottom=side, left=side)

_SECTIONS = {
    "edit": ("编辑", "调整编辑区布局与写作行为。"),
    "appearance": ("外观", "控制主题、字体与视觉密度。"),
    "behavior": ("行为", "控制保存、专注与工具栏行为。"),
    "shortcuts": ("快捷键", "查看、自定义快捷键，支持导入导出方案。"),
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
    capturing: tuple = (None, None),
    on_capture_click: Callable[[str, str], None] | None = None,
    on_cancel_capture_click: Callable[[], None] | None = None,
):
    """设置面板弹层。

    open_state: 是否显示；tab: 当前激活 tab；settings: 当前设置字典；
    shortcut_focus: (layer, action_id) 用于冲突定位高亮；shortcut_mgr: 快捷键管理器。
    on_update: 顶层设置项更新（key, value），由 main.py 的 update_setting 处理。
    快捷键更新直接通过 shortcut_mgr.update(layer, action, combo) 调用，内部回调 on_update。
    capturing: (layer, action_id) | (None, None)，快捷键捕获模式状态；
    on_capture_click / on_cancel_capture_click: 设置页"修改"/"取消"按钮回调。
    """
    c = get_colors(theme_mode)
    is_dark = theme_mode == ft.ThemeMode.DARK
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
            border_radius=Radius.XXXL,
            shadow=card_shadow(Elevation.DIALOG, is_dark),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Row(
                spacing=0,
                controls=[
                    _sidebar(c, tab, on_select_tab, on_reset_all),
                    ft.Container(width=1, bgcolor=c.border),
                    ft.Container(
                        expand=True,
                        padding=Spacing.XXXL,
                        content=ft.Column(
                            controls=[
                                _header(current_title, current_desc, c, on_close),
                                ft.Container(height=Spacing.LG),
                                _panel(tab, settings, theme_mode, shortcut_focus,
                                       shortcut_mgr, on_update, on_reset_shortcuts,
                                       on_import, on_export,
                                       capturing, on_capture_click, on_cancel_capture_click),
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
        padding=Spacing.XXL,
        content=ft.Column(
            expand=True,
            controls=[
                ft.Text("设置", size=22, weight=ft.FontWeight.W_700),
                ft.Text("Typora 风格的可配置中心", size=12, color=c.muted),
                ft.Container(height=Spacing.XXL),
                *[_tab_button(t, label, icon, c, tab, on_select_tab) for t, label, icon in _TAB_ICONS],
                ft.Container(expand=True),
                ft.TextButton("恢复默认", on_click=lambda e: on_reset_all()),
            ],
            spacing=Spacing.LG,
        ),
    )


def _tab_button(t: str, label: str, icon: str, c, current_tab: str, on_select_tab) -> ft.Control:
    active = current_tab == t
    return ft.Container(
        border_radius=Radius.XL,
        bgcolor=ft.Colors.with_opacity(0.12, c.link) if active else None,
        padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
        content=ft.Row(
            controls=[
                ft.Icon(icon=icon, size=16,
                        color=c.link if active else c.muted),
                ft.TextButton(label, on_click=lambda e: on_select_tab(t)),
            ],
            spacing=Spacing.LG,
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
                spacing=Spacing.XS,
            ),
            ft.Container(expand=True),
            ft.IconButton(icon=ft.Icons.CLOSE, on_click=lambda e: on_close()),
        ]
    )


def _panel(
    tab: str, settings: dict, theme_mode: ft.ThemeMode, shortcut_focus: tuple,
    shortcut_mgr: ShortcutManager, on_update, on_reset_shortcuts, on_import, on_export,
    capturing: tuple, on_capture_click, on_cancel_capture_click,
) -> ft.Control:
    if tab == "edit":
        return _edit_panel(settings, theme_mode, on_update)
    if tab == "appearance":
        return _appearance_panel(settings, theme_mode, on_update)
    if tab == "behavior":
        return _behavior_panel(settings, theme_mode, on_update)
    if tab == "shortcuts":
        return _shortcuts_panel(
            theme_mode, shortcut_focus, shortcut_mgr, capturing,
            on_capture_click, on_cancel_capture_click,
            on_reset_shortcuts, on_import, on_export,
        )
    return _advanced_panel(theme_mode)


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
            spacing=Spacing.XL,
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
            spacing=Spacing.XL,
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
            spacing=Spacing.XL,
        ),
    )


# ---- 快捷键 tab ----

def _shortcuts_panel(
    theme_mode: ft.ThemeMode, shortcut_focus: tuple, shortcut_mgr: ShortcutManager,
    capturing: tuple, on_capture_click, on_cancel_capture_click,
    on_reset_shortcuts, on_import, on_export,
) -> ft.Control:
    c = get_colors(theme_mode)
    return ft.Container(
        content=ft.Column(
            controls=[
                # 顶部工具栏：导入 / 导出 / 恢复全部默认
                ft.Row(
                    controls=[
                        ft.Column(
                            controls=[
                                ft.Text("快捷键管理", size=14, weight=ft.FontWeight.W_600),
                                ft.Text("点击「修改」后按下新组合键，立即生效。Esc 取消，Backspace 清空。",
                                        size=12, color=c.muted),
                            ],
                            spacing=Spacing.XS,
                            expand=True,
                        ),
                        ft.Container(expand=True),
                        ft.TextButton("导入方案", on_click=lambda e: on_import()),
                        ft.TextButton("导出方案", on_click=lambda e: on_export()),
                        ft.TextButton("恢复全部默认", on_click=lambda e: on_reset_shortcuts()),
                    ]
                ),
                # 冲突卡片
                ft.Row(
                    controls=[
                        _conflict_card("浏览态", shortcut_mgr.conflict_summary("browse"), c),
                        _conflict_card("编辑态", shortcut_mgr.conflict_summary("edit"), c),
                    ],
                    spacing=Spacing.XL,
                ),
                # 固定导航键说明（不可自定义）
                _fixed_keys_hint(c),
                # 动作列表
                ft.Container(
                    expand=True,
                    border_radius=Radius.XL,
                    bgcolor=ft.Colors.with_opacity(0.04, c.text),
                    padding=ft.Padding.all(Spacing.XL),
                    content=ft.Column(
                        controls=_action_rows(
                            shortcut_mgr, theme_mode, shortcut_focus, capturing,
                            on_capture_click, on_cancel_capture_click,
                        ),
                        spacing=Spacing.LG,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                ),
            ],
            spacing=Spacing.XL,
            expand=True,
        ),
    )


def _fixed_keys_hint(c) -> ft.Control:
    """固定导航键说明（不可自定义）。"""
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
        border_radius=Radius.LG,
        bgcolor=ft.Colors.with_opacity(0.06, c.muted),
        content=ft.Column(
            controls=[
                ft.Text("固定导航键（不可自定义）", size=11,
                        weight=ft.FontWeight.W_600, color=c.muted),
                ft.Text("Ctrl+0~6 切换标题级别（0=正文，1~6=H1~H6）",
                        size=11, color=c.muted),
                ft.Text("方向键 / Home / End / PageUp / PageDown 光标导航",
                        size=11, color=c.muted),
            ],
            spacing=Spacing.XS,
        ),
    )


# ---- 高级 tab（占位）----

def _advanced_panel(theme_mode: ft.ThemeMode) -> ft.Control:
    c = get_colors(theme_mode)
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("高级选项", size=14, weight=ft.FontWeight.W_600),
                ft.Text("预留代码主题、导出等高级选项，快捷键管理已迁移至「快捷键」tab。",
                        size=12, color=c.muted),
            ],
            spacing=Spacing.LG,
        ),
    )


def _conflict_card(label: str, summary: str | None, c) -> ft.Control:
    return ft.Container(
        expand=True,
        padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
        border_radius=Radius.XL,
        bgcolor=ft.Colors.with_opacity(0.08, c.link),
        content=ft.Column(
            controls=[
                ft.Text(label, size=12, weight=ft.FontWeight.W_700),
                ft.Text(summary or "无冲突", size=11,
                        color="#E66A00" if summary else c.muted),
            ],
            spacing=Spacing.XS,
        ),
    )


def _action_rows(
    shortcut_mgr: ShortcutManager, theme_mode: ft.ThemeMode,
    shortcut_focus: tuple, capturing: tuple,
    on_capture_click, on_cancel_capture_click,
) -> list[ft.Control]:
    c = get_colors(theme_mode)
    rows: list[ft.Control] = []
    for layer in shortcut_mgr.layers():
        layer_actions = shortcut_mgr.actions_for_layer(layer)
        cmap = shortcut_mgr.conflict_map(layer)
        rows.append(
            ft.Container(
                padding=ft.Padding.only(top=Spacing.SM, bottom=Spacing.SM),
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
            rows.append(_action_row(
                action, layer, current, default, is_conflict, c, shortcut_mgr,
                capturing, on_capture_click, on_cancel_capture_click,
            ))
    return rows


def _format_combo_display(combo: str) -> str:
    """把 'ctrl+shift+s' 显示为 'Ctrl+Shift+S'，便于阅读。"""
    if not combo:
        return "未绑定"
    parts = []
    for part in combo.split("+"):
        if part in ("ctrl", "shift", "alt"):
            parts.append(part.capitalize())
        elif part == ",":
            parts.append(",")
        else:
            parts.append(part.upper())
    return "+".join(parts)


def _action_row(
    action, layer: str, current: str, default: str, is_conflict: bool, c,
    shortcut_mgr: ShortcutManager,
    capturing: tuple, on_capture_click, on_cancel_capture_click,
) -> ft.Control:
    is_capturing = capturing == (layer, action.id)

    # 左侧：动作名 + 说明
    left = ft.Column(
        controls=[
            ft.Text(action.label, size=13, weight=ft.FontWeight.W_600),
            ft.Text(f"{action.category} · {action.description}",
                    size=11, color=c.muted),
        ],
        spacing=Spacing.XS,
        expand=True,
    )

    if is_capturing:
        # 捕获中：提示 + 取消按钮
        right = ft.Row(
            controls=[
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.MD),
                    border_radius=Radius.MD,
                    bgcolor=ft.Colors.with_opacity(0.12, c.link),
                    content=ft.Text("按下新组合键…（Esc 取消，Backspace 清空）",
                                    size=11, color=c.link, weight=ft.FontWeight.W_600),
                ),
                ft.TextButton("取消", on_click=lambda e: on_cancel_capture_click()),
            ],
            spacing=Spacing.SM,
        )
        bgcolor = ft.Colors.with_opacity(0.08, c.link)
        border = _all_border(1, c.link)
    else:
        # 正常态：只读 kbd 标签 + 修改 + 恢复默认
        kbd_display = current if current else (default or "")
        kbd_color = "#E66A00" if is_conflict else c.text
        kbd_border = "#E66A00" if is_conflict else c.border
        right = ft.Row(
            controls=[
                ft.Container(
                    width=160,
                    padding=ft.Padding.symmetric(horizontal=Spacing.MD, vertical=Spacing.SM),
                    border_radius=Radius.MD,
                    border=_all_border(1, kbd_border),
                    content=ft.Text(_format_combo_display(kbd_display),
                                    size=12, color=kbd_color),
                ),
                ft.TextButton(
                    "修改",
                    on_click=lambda e, l=layer, a=action.id: on_capture_click(l, a),
                ),
                ft.TextButton(
                    "恢复默认",
                    on_click=lambda e, l=layer, a=action.id: shortcut_mgr.reset(l, a),
                    disabled=(not default) or (current == default),
                ),
            ],
            spacing=Spacing.SM,
        )
        bgcolor = ft.Colors.with_opacity(0.10, ft.Colors.RED) if is_conflict else None
        border = None

    return ft.Container(
        bgcolor=bgcolor,
        border=border,
        border_radius=Radius.XL,
        padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
        content=ft.Row(
            controls=[left, right],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
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
