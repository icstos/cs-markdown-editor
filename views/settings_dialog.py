"""设置面板：编辑 / 外观 / 行为 / 快捷键 / 关于 五个 tab。

结构（自外向内）：
- 遮罩层（半透明黑）→ 对话框（定宽定高、圆角、阴影、裁剪）
- 左列：侧栏导航（应用名 + 五个 tab + 底部「恢复默认」）
- 右列：**固定头部**（标题 / 说明 / 关闭按钮）+ 1px 分隔线 + **滚动内容区**

设计要点：
1. **关闭按钮固定**：头部与分隔线在滚动区**之外**，只有内容区滚动。
   历史版本把头部放进滚动 Column，滚动长列表（快捷键 tab 近百行）时
   关闭按钮会一起滚出视口，用户必须滚回顶部才能关窗。
2. **统一定高**：导航项 / 图标按钮一律用显式尺寸的 `Container(ink=True)`，
   不用 `ft.IconButton`（Material 固有高 40，`visual_density=COMPACT` 也只降到 32，
   压内边距无效）——与顶栏（`views/tab_bar.py`）/ 状态栏同一惯例。
3. **单层滚动**：所有 tab 的内容都由右列这一个滚动区承载。快捷键 tab 不再
   自带内层滚动（嵌套滚动条既难用又难对齐滚动位置）。
4. **快捷键「已修改」标记**：以 `services/shortcuts.ACTION_REGISTRY` 的
   `default` 为基准逐项比对，偏离默认的项打角标，并在分类标题上汇总条数；
   顶部提供「只看已修改」开关（组件内 `use_state`，不引入 App 级状态）。

数据来源：`services.shortcuts.ShortcutManager`（动作注册表 / 冲突检测）、
`config/app_meta`（关于页的版本与外链）。
"""

from collections.abc import Callable

import flet as ft

from config import app_meta
from services.shortcuts import ShortcutManager, normalize
from styles import Elevation, Radius, Spacing, card_shadow, get_colors

# ---- 语义色（与 views/status_bar.py 的状态色一致）----
_AMBER = "#E66A00"   # 已修改
_DANGER = "#E5484D"  # 已清空 / 冲突
_OK = "#35C759"      # 与默认一致

# 表单行：标签宽度与数值列宽度（固定，保证各行左右边缘对齐）
_LABEL_W = 108
_VALUE_W = 56

_SECTIONS = {
    "edit": ("编辑", "调整编辑区布局与写作行为。"),
    "appearance": ("外观", "控制主题、字体与视觉密度。"),
    "behavior": ("行为", "控制保存、专注与工具栏行为。"),
    "shortcuts": ("快捷键", "查看、自定义快捷键，支持导入导出方案。"),
    "about": ("关于", "版本信息、联系与支持渠道。"),
}

_TAB_ICONS = [
    ("edit", "编辑", ft.Icons.EDIT_OUTLINED),
    ("appearance", "外观", ft.Icons.PALETTE_OUTLINED),
    ("behavior", "行为", ft.Icons.TUNE),
    ("shortcuts", "快捷键", ft.Icons.KEYBOARD_OUTLINED),
    ("about", "关于", ft.Icons.INFO_OUTLINE),
]


def _all_border(width: float, color: str) -> ft.Border:
    """便捷构造四边相同 Border（项目 styles.only_border 仅支持单边）。"""
    side = ft.BorderSide(width, color)
    return ft.Border(top=side, right=side, bottom=side, left=side)


def _hairline(c, opacity: float = 1.0) -> ft.Container:
    """1px 分隔线（用 Container 而非 ft.Divider：Divider 自带高度与外边距，
    在紧凑布局里会多出几像素的不可控留白）。"""
    return ft.Container(
        height=1,
        bgcolor=ft.Colors.with_opacity(opacity, c.border),
    )


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
    on_open_recovery: Callable[[], None] | None = None,
    on_pick_backup_dir: Callable[[], None] | None = None,
    on_open_url: Callable[[str], None] | None = None,
    on_copy: Callable[[str], None] | None = None,
):
    """设置面板弹层。

    open_state: 是否显示；tab: 当前激活 tab；settings: 当前设置字典；
    shortcut_focus: (layer, action_id) 用于冲突定位高亮；shortcut_mgr: 快捷键管理器。
    on_update: 顶层设置项更新（key, value），由 main.py 的 update_setting 处理。
    快捷键更新直接通过 shortcut_mgr.update(layer, action, combo) 调用，内部回调 on_update。
    capturing: (layer, action_id) | (None, None)，快捷键捕获模式状态；
    on_capture_click / on_cancel_capture_click: 设置页"修改"/"取消"按钮回调。
    on_open_recovery: 打开恢复面板（扫描历史备份）；on_pick_backup_dir: 选择自定义备份目录。
    on_open_url / on_copy: 「关于」页的外链打开与复制（系统浏览器 / 剪贴板）。
    """
    c = get_colors(theme_mode)
    is_dark = theme_mode == ft.ThemeMode.DARK
    current_title, current_desc = _SECTIONS.get(tab, _SECTIONS["edit"])
    # 「只看已修改」为面板内部状态：不引入 App 级 state，切窗时随组件销毁复位
    only_modified, set_only_modified = ft.use_state(False)

    return ft.Container(
        visible=open_state,
        expand=True,
        bgcolor=ft.Colors.with_opacity(0.32, ft.Colors.BLACK),
        alignment=ft.Alignment.CENTER,
        content=ft.Container(
            width=1040,
            height=740,
            bgcolor=c.surface,
            border_radius=Radius.XXXL,
            shadow=card_shadow(Elevation.DIALOG, is_dark),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Row(
                spacing=0,
                controls=[
                    _sidebar(c, tab, on_select_tab, on_reset_all),
                    _hairline(c),
                    ft.Container(
                        expand=True,
                        content=ft.Column(
                            # expand 必须写：否则该 Column 按内容取高，
                            # 下面的滚动列拿不到有界高度 → 内容被卡片裁掉且**滚不动**
                            # （真机实测：滚轮打了 6 次内容区零位移）。父级 Row 的
                            # 交叉轴约束是"至多 740"，只有 expand 才会变成紧约束。
                            expand=True,
                            spacing=0,
                            controls=[
                                # 固定头部：不随内容滚动
                                _header(current_title, current_desc, c, on_close),
                                _hairline(c),
                                # 唯一滚动区
                                ft.Container(
                                    expand=True,
                                    padding=ft.Padding.only(
                                        left=Spacing.XXXL,
                                        right=Spacing.XXXL,
                                        top=Spacing.XXL,
                                        bottom=Spacing.XXXL,
                                    ),
                                    content=ft.Column(
                                        expand=True,
                                        spacing=0,
                                        scroll=ft.ScrollMode.AUTO,
                                        controls=[
                                            _panel(
                                                tab, settings, theme_mode, shortcut_focus,
                                                shortcut_mgr, on_update, on_reset_shortcuts,
                                                on_import, on_export,
                                                capturing, on_capture_click,
                                                on_cancel_capture_click,
                                                on_open_recovery, on_pick_backup_dir,
                                                only_modified, set_only_modified,
                                                on_open_url, on_copy,
                                            ),
                                        ],
                                    ),
                                ),
                            ],
                        ),
                    ),
                ],
            ),
        ),
    )


# ---------------------------------------------------------------------------
# 通用部件
# ---------------------------------------------------------------------------

def _icon_button(
    c, icon, on_click, *, tooltip: str = "", color: str | None = None,
    box: int = 28, icon_size: int = 16,
) -> ft.Control:
    """定尺寸图标按钮（不用 ft.IconButton：Material 固有高 40 不可压缩）。"""
    return ft.Container(
        width=box,
        height=box,
        border_radius=Radius.MD,
        alignment=ft.Alignment.CENTER,
        ink=True,
        tooltip=tooltip or None,
        on_click=lambda e: on_click() if on_click else None,
        content=ft.Icon(icon, size=icon_size, color=color or c.muted),
    )


def _card(c, *, title: str = "", desc: str = "", controls: list) -> ft.Control:
    """统一的分组卡片：细边框 + 圆角 + 可选标题。

    设置项按语义分组进卡片（而不是裸 Text + Divider 平铺），是「更精致」的主要
    来源——分组边界清晰、卡片间留白一致，扫读时能按块定位。
    """
    body: list[ft.Control] = []
    if title:
        head: list[ft.Control] = [
            ft.Text(title, size=13, weight=ft.FontWeight.W_600, color=c.text)
        ]
        if desc:
            head.append(ft.Text(desc, size=11, color=c.muted))
        body.append(ft.Column(controls=head, spacing=Spacing.XS))
        body.append(ft.Container(height=Spacing.MD))
    body.extend(controls)
    return ft.Container(
        padding=ft.Padding.all(Spacing.XXL),
        border_radius=Radius.XL,
        border=_all_border(1, c.border),
        content=ft.Column(controls=body, spacing=Spacing.XL),
    )


def _badge(c, text: str, color: str) -> ft.Control:
    """小圆角角标（「已修改」/「已清空」）。"""
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=Spacing.MD, vertical=1),
        border_radius=Radius.SM,
        bgcolor=ft.Colors.with_opacity(0.14, color),
        content=ft.Text(text, size=10, color=color, weight=ft.FontWeight.W_600),
    )


def _switch_row(c, label: str, value: bool, on_change, *, desc: str = "") -> ft.Control:
    """开关行：左侧标签（+ 说明），右侧开关。"""
    left: list[ft.Control] = [ft.Text(label, size=13, color=c.text)]
    if desc:
        left.append(ft.Text(desc, size=11, color=c.muted))
    return ft.Row(
        controls=[
            ft.Column(controls=left, spacing=Spacing.XS, expand=True),
            ft.Switch(value=value, on_change=on_change),
        ],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def _slider_row(
    c, label: str, value, on_change, *,
    minv: float, maxv: float, divisions: int, suffix: str = "",
) -> ft.Control:
    """滑杆行：标签 / 滑杆 / 右对齐数值（数值列定宽，各行右边缘对齐）。"""
    return ft.Row(
        controls=[
            ft.Text(label, width=_LABEL_W, size=13, color=c.text),
            ft.Slider(
                min=minv, max=maxv, divisions=divisions,
                value=value, expand=True,
                on_change=lambda e: on_change(e.control.value),
            ),
            ft.Text(
                f"{value}{suffix}",
                width=_VALUE_W,
                size=12,
                color=c.muted,
                text_align=ft.TextAlign.RIGHT,
            ),
        ],
        spacing=Spacing.LG,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def _dropdown_row(
    c, label: str, value, options: list[str], on_select,
) -> ft.Control:
    """下拉行。

    注意：**不给 ft.Dropdown 设 height** —— 内部 InputDecorator 的高度是固定的，
    设 height 只会改外框而裁切内部文字（项目红线，见 AGENT.md 第 4 节）。
    """
    return ft.Row(
        controls=[
            ft.Text(label, width=_LABEL_W, size=13, color=c.text),
            ft.Dropdown(
                options=[ft.dropdown.Option(o) for o in options],
                value=value, expand=True,
                on_select=lambda e: on_select(e.control.value),
            ),
        ],
        spacing=Spacing.LG,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def _value_row(c, label: str, control: ft.Control, *, desc: str = "") -> ft.Control:
    """只读/自定义控件行：定宽标签 + 右侧内容。"""
    left: list[ft.Control] = [ft.Text(label, size=13, color=c.text)]
    if desc:
        left.append(ft.Text(desc, size=11, color=c.muted))
    return ft.Row(
        controls=[
            ft.Column(controls=left, spacing=Spacing.XS, width=_LABEL_W),
            ft.Container(expand=True, content=control),
        ],
        spacing=Spacing.LG,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


# ---------------------------------------------------------------------------
# 侧栏 / 头部
# ---------------------------------------------------------------------------

def _sidebar(c, tab: str, on_select_tab, on_reset_all) -> ft.Control:
    return ft.Container(
        width=232,
        bgcolor=ft.Colors.with_opacity(0.30, c.border),
        padding=ft.Padding.only(
            left=Spacing.XXL, right=Spacing.XXL,
            top=Spacing.XXL, bottom=Spacing.XL,
        ),
        content=ft.Column(
            expand=True,
            spacing=0,
            controls=[
                ft.Text(app_meta.APP_NAME, size=15, weight=ft.FontWeight.W_700,
                        color=c.text, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text("设置中心", size=11, color=c.muted),
                ft.Container(height=Spacing.XXL),
                ft.Column(
                    spacing=Spacing.XS,
                    controls=[
                        _nav_item(t, label, icon, c, tab, on_select_tab)
                        for t, label, icon in _TAB_ICONS
                    ],
                ),
                ft.Container(expand=True),
                _hairline(c),
                ft.Container(height=Spacing.MD),
                ft.Container(
                    height=34,
                    border_radius=Radius.LG,
                    ink=True,
                    padding=ft.Padding.symmetric(horizontal=Spacing.XL),
                    on_click=lambda e: on_reset_all(),
                    content=ft.Row(
                        spacing=Spacing.XL,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Icon(ft.Icons.RESTART_ALT, size=15, color=c.muted),
                            ft.Text("恢复默认设置", size=12, color=c.muted),
                        ],
                    ),
                ),
            ],
        ),
    )


def _nav_item(t: str, label: str, icon: str, c, current_tab: str, on_select_tab) -> ft.Control:
    """侧栏导航项：整行可点（原实现把 TextButton 嵌在 Container 里，
    只有文字那一点可点，行内其余位置点不动）。"""
    active = current_tab == t
    return ft.Container(
        height=38,
        border_radius=Radius.LG,
        bgcolor=ft.Colors.with_opacity(0.14, c.link) if active else None,
        padding=ft.Padding.symmetric(horizontal=Spacing.XL),
        ink=True,
        on_click=lambda e: on_select_tab(t),
        content=ft.Row(
            spacing=Spacing.XL,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(icon, size=17, color=c.link if active else c.muted),
                ft.Text(
                    label, size=13,
                    color=c.link if active else c.text,
                    weight=ft.FontWeight.W_600 if active else ft.FontWeight.W_400,
                ),
            ],
        ),
    )


def _header(title: str, desc: str, c, on_close) -> ft.Control:
    """固定头部：标题 / 说明在左，关闭按钮在右。位于滚动区之外。"""
    return ft.Container(
        padding=ft.Padding.only(
            left=Spacing.XXXL, right=Spacing.XXL,
            top=Spacing.XXL, bottom=Spacing.XXL,
        ),
        content=ft.Row(
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Column(
                    spacing=Spacing.XS,
                    expand=True,
                    controls=[
                        ft.Text(title, size=19, weight=ft.FontWeight.W_700, color=c.text),
                        ft.Text(desc, size=12, color=c.muted),
                    ],
                ),
                _icon_button(
                    c, ft.Icons.CLOSE, on_close, tooltip="关闭", box=32, icon_size=18,
                ),
            ],
        ),
    )


# ---------------------------------------------------------------------------
# 面板分发
# ---------------------------------------------------------------------------

def _panel(
    tab: str, settings: dict, theme_mode: ft.ThemeMode, shortcut_focus: tuple,
    shortcut_mgr: ShortcutManager, on_update, on_reset_shortcuts, on_import, on_export,
    capturing: tuple, on_capture_click, on_cancel_capture_click,
    on_open_recovery=None, on_pick_backup_dir=None,
    only_modified: bool = False, set_only_modified=None,
    on_open_url=None, on_copy=None,
) -> ft.Control:
    if tab == "edit":
        return _edit_panel(settings, theme_mode, on_update)
    if tab == "appearance":
        return _appearance_panel(settings, theme_mode, on_update)
    if tab == "behavior":
        return _behavior_panel(
            settings, theme_mode, on_update, on_open_recovery, on_pick_backup_dir
        )
    if tab == "shortcuts":
        return _shortcuts_panel(
            theme_mode, shortcut_focus, shortcut_mgr, capturing,
            on_capture_click, on_cancel_capture_click,
            on_reset_shortcuts, on_import, on_export,
            only_modified, set_only_modified,
        )
    return _about_panel(theme_mode, on_open_url, on_copy)


# ---------------------------------------------------------------------------
# 编辑 tab
# ---------------------------------------------------------------------------

def _edit_panel(settings: dict, theme_mode: ft.ThemeMode, on_update) -> ft.Control:
    c = get_colors(theme_mode)
    return ft.Column(
        spacing=Spacing.XXL,
        controls=[
            _card(
                c,
                title="页面布局",
                desc="控制正文书写区域的宽度与留白。",
                controls=[
                    _slider_row(c, "内容宽度", settings["content_max_width"],
                                lambda v: on_update("content_max_width", int(v)),
                                minv=680, maxv=1200, divisions=13, suffix=" px"),
                    _slider_row(c, "左右边距", settings["content_padding"],
                                lambda v: on_update("content_padding", int(v)),
                                minv=12, maxv=64, divisions=13, suffix=" px"),
                    _slider_row(c, "顶部边距", settings["content_padding_top"],
                                lambda v: on_update("content_padding_top", int(v)),
                                minv=8, maxv=48, divisions=10, suffix=" px"),
                ],
            ),
            _card(
                c,
                title="界面元素",
                controls=[
                    _switch_row(
                        c, "显示底部状态栏", settings["show_footer"],
                        lambda e: on_update("show_footer", e.control.value),
                        desc="显示光标行列、字数与自动保存状态。",
                    ),
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 外观 tab
# ---------------------------------------------------------------------------

def _appearance_panel(settings: dict, theme_mode: ft.ThemeMode, on_update) -> ft.Control:
    c = get_colors(theme_mode)
    return ft.Column(
        spacing=Spacing.XXL,
        controls=[
            _card(
                c,
                title="字体与排版",
                controls=[
                    _slider_row(c, "正文大小", settings["body_font_size"],
                                lambda v: on_update("body_font_size", int(v)),
                                minv=14, maxv=20, divisions=6, suffix=" px"),
                    _slider_row(c, "行高", settings["line_height"],
                                lambda v: on_update("line_height", round(float(v), 1)),
                                minv=1.2, maxv=2.0, divisions=8),
                    _dropdown_row(c, "字体", settings["font_family"],
                                  ["Alibaba", "Sans", "Serif", "Monospace"],
                                  lambda v: on_update("font_family", v)),
                ],
            ),
            _card(
                c,
                title="代码主题",
                desc="分别指定亮色与暗色模式下的代码块高亮配色。",
                controls=[
                    _dropdown_row(c, "代码主题（暗）", settings["code_theme_dark"],
                                  ["ATOM_ONE_DARK", "GITHUB", "VS2015"],
                                  lambda v: on_update("code_theme_dark", v)),
                    _dropdown_row(c, "代码主题（亮）", settings["code_theme_light"],
                                  ["GITHUB", "ATOM_ONE_LIGHT", "VS2015"],
                                  lambda v: on_update("code_theme_light", v)),
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 行为 tab
# ---------------------------------------------------------------------------

def _behavior_panel(
    settings: dict,
    theme_mode: ft.ThemeMode,
    on_update,
    on_open_recovery=None,
    on_pick_backup_dir=None,
) -> ft.Control:
    c = get_colors(theme_mode)
    return ft.Column(
        spacing=Spacing.XXL,
        controls=[
            _card(
                c,
                title="编辑行为",
                controls=[
                    _switch_row(
                        c, "记住聚焦模式", settings["remember_focus_mode"],
                        lambda e: on_update("remember_focus_mode", e.control.value),
                    ),
                    _switch_row(
                        c, "显示工具栏", settings["show_toolbar"],
                        lambda e: on_update("show_toolbar", e.control.value),
                    ),
                    _switch_row(
                        c, "显示行号", settings["show_line_numbers"],
                        lambda e: on_update("show_line_numbers", e.control.value),
                    ),
                    _dropdown_row(c, "导出默认格式", settings["export_format"],
                                  ["html", "pdf", "md"],
                                  lambda v: on_update("export_format", v)),
                ],
            ),
            _card(
                c,
                title="自动保存",
                controls=[
                    _switch_row(
                        c, "启用自动保存", settings["auto_save"],
                        lambda e: on_update("auto_save", e.control.value),
                    ),
                    _slider_row(
                        c, "保存间隔", settings.get("auto_save_interval", 5),
                        lambda v: on_update("auto_save_interval", int(v)),
                        minv=1, maxv=30, divisions=29, suffix=" 分钟",
                    ),
                    _switch_row(
                        c, "窗口失焦时立即保存", settings.get("auto_save_on_blur", True),
                        lambda e: on_update("auto_save_on_blur", e.control.value),
                    ),
                    _switch_row(
                        c, "切换/关闭文档时立即保存", settings.get("auto_save_on_switch", True),
                        lambda e: on_update("auto_save_on_switch", e.control.value),
                    ),
                ],
            ),
            _card(
                c,
                title="备份与恢复",
                controls=[
                    _switch_row(
                        c, "启用自动备份", settings.get("backup_enabled", True),
                        lambda e: on_update("backup_enabled", e.control.value),
                    ),
                    _slider_row(
                        c, "备份间隔", settings.get("backup_interval", 10),
                        lambda v: on_update("backup_interval", int(v)),
                        minv=5, maxv=60, divisions=55, suffix=" 分钟",
                    ),
                    _slider_row(
                        c, "备份保留天数", settings.get("backup_retention_days", 30),
                        lambda v: on_update("backup_retention_days", int(v)),
                        minv=7, maxv=90, divisions=83, suffix=" 天",
                    ),
                    _slider_row(
                        c, "草稿保留天数", settings.get("recover_untitled_days", 7),
                        lambda v: on_update("recover_untitled_days", int(v)),
                        minv=1, maxv=30, divisions=29, suffix=" 天",
                    ),
                    _switch_row(
                        c, "检测外部修改", settings.get("detect_external_changes", True),
                        lambda e: on_update("detect_external_changes", e.control.value),
                        desc="文件被其它程序改动时提示重新载入。",
                    ),
                    _hairline(c),
                    _value_row(
                        c,
                        "备份目录",
                        ft.Row(
                            spacing=Spacing.SM,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Container(
                                    expand=True,
                                    padding=ft.Padding.symmetric(
                                        horizontal=Spacing.MD, vertical=Spacing.SM,
                                    ),
                                    border_radius=Radius.MD,
                                    bgcolor=ft.Colors.with_opacity(0.04, c.text),
                                    border=_all_border(1, c.border),
                                    content=ft.Text(
                                        value=settings.get("backup_dir") or "（平台默认路径）",
                                        size=12, color=c.muted, max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                ),
                                ft.TextButton(
                                    "选择目录",
                                    on_click=lambda e: (
                                        on_pick_backup_dir() if on_pick_backup_dir else None
                                    ),
                                ),
                            ],
                        ),
                    ),
                    _value_row(
                        c,
                        "历史备份",
                        ft.Row(
                            spacing=Spacing.SM,
                            controls=[
                                ft.TextButton(
                                    "恢复未保存的草稿",
                                    icon=ft.Icons.RESTORE_PAGE,
                                    on_click=lambda e: (
                                        on_open_recovery() if on_open_recovery else None
                                    ),
                                ),
                            ],
                        ),
                        desc="从历史备份中找回误删或未及时保存的内容。",
                    ),
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 快捷键 tab
# ---------------------------------------------------------------------------

def _shortcut_diff(current: str, default: str) -> str:
    """判断当前键位相对默认的偏离程度，返回角标文案（空串 = 与默认一致）。

    比对前先 `normalize`（去空格、小写、键别名归一），否则 `ctrl+comma` 与
    `ctrl+,` 这类等价写法会被误判成「已修改」。
    """
    cur, dft = normalize(current), normalize(default)
    if cur == dft:
        return ""
    if not cur:
        return "已清空"
    if not dft:
        return "已自定义"
    return "已修改"


def _shortcuts_panel(
    theme_mode: ft.ThemeMode, shortcut_focus: tuple, shortcut_mgr: ShortcutManager,
    capturing: tuple, on_capture_click, on_cancel_capture_click,
    on_reset_shortcuts, on_import, on_export,
    only_modified: bool = False, set_only_modified=None,
) -> ft.Control:
    c = get_colors(theme_mode)
    rows, total, changed = _action_rows(
        shortcut_mgr, theme_mode, shortcut_focus, capturing,
        on_capture_click, on_cancel_capture_click, only_modified,
    )
    # 过滤后可能一行不剩：区分「本来就是全默认」与「有改动但被筛掉」
    empty_msg = "所有快捷键均为默认设置。" if changed == 0 else "没有符合条件的快捷键。"
    return ft.Column(
        spacing=Spacing.XXL,
        controls=[
            # 工具栏
            ft.Row(
                controls=[
                    ft.Column(
                        controls=[
                            ft.Text("快捷键管理", size=14, weight=ft.FontWeight.W_600,
                                    color=c.text),
                            ft.Text(
                                "点击「修改」后按下新组合键，立即生效。Esc 取消，Backspace 清空。",
                                size=12, color=c.muted,
                            ),
                        ],
                        spacing=Spacing.XS,
                        expand=True,
                    ),
                    ft.TextButton("导入方案", on_click=lambda e: on_import()),
                    ft.TextButton("导出方案", on_click=lambda e: on_export()),
                    ft.TextButton("恢复全部默认", on_click=lambda e: on_reset_shortcuts()),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            # 改动概览 + 过滤开关
            _modified_summary(c, changed, total, only_modified, set_only_modified),
            # 冲突卡片
            ft.Row(
                controls=[
                    _conflict_card("浏览态", shortcut_mgr.conflict_summary("browse"), c),
                    _conflict_card("编辑态", shortcut_mgr.conflict_summary("edit"), c),
                ],
                spacing=Spacing.XL,
            ),
            _fixed_keys_hint(c),
            # 动作列表（单层滚动：由外层内容区承载，此处不再自带滚动）
            ft.Container(
                border_radius=Radius.XL,
                border=_all_border(1, c.border),
                padding=ft.Padding.all(Spacing.XL),
                content=ft.Column(
                    controls=rows or [
                        ft.Container(
                            padding=ft.Padding.all(Spacing.XXL),
                            alignment=ft.Alignment.CENTER,
                            content=ft.Text(empty_msg, size=12, color=c.muted),
                        )
                    ],
                    spacing=Spacing.SM,
                ),
            ),
        ],
    )


def _modified_summary(c, changed: int, total: int, only_modified: bool, set_only_modified) -> ft.Control:
    """改动概览条：左为统计，右为「只看已修改」开关。"""
    if changed:
        tone = _AMBER
        head = f"已调整 {changed} 项"
        tail = f"共 {total} 项，其余与默认一致。"
    else:
        tone = _OK
        head = "全部为默认键位"
        tail = f"共 {total} 项，未做任何修改。"
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
        border_radius=Radius.LG,
        bgcolor=ft.Colors.with_opacity(0.08, tone),
        content=ft.Row(
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(
                    ft.Icons.CHECK_CIRCLE_OUTLINE if not changed else ft.Icons.EDIT,
                    size=15, color=tone,
                ),
                ft.Container(width=Spacing.MD),
                ft.Text(head, size=12, weight=ft.FontWeight.W_600, color=tone),
                ft.Text(tail, size=11, color=c.muted),
                ft.Container(expand=True),
                ft.Switch(
                    label="只看已修改",
                    label_position=ft.LabelPosition.LEFT,
                    label_text_style=ft.TextStyle(size=12, color=c.muted),
                    value=bool(only_modified),
                    disabled=not total,
                    on_change=(
                        (lambda e: set_only_modified(bool(e.control.value)))
                        if set_only_modified else None
                    ),
                ),
            ],
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


def _conflict_card(label: str, summary: str | None, c) -> ft.Control:
    tone = _DANGER if summary else _OK
    return ft.Container(
        expand=True,
        padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
        border_radius=Radius.XL,
        bgcolor=ft.Colors.with_opacity(0.08, tone),
        content=ft.Column(
            controls=[
                ft.Row(
                    spacing=Spacing.MD,
                    controls=[
                        ft.Icon(
                            ft.Icons.ERROR_OUTLINE if summary else ft.Icons.CHECK_CIRCLE_OUTLINE,
                            size=14, color=tone,
                        ),
                        ft.Text(f"{label}快捷键", size=12,
                                weight=ft.FontWeight.W_700, color=tone),
                    ],
                ),
                ft.Text(summary or "无冲突", size=11, color=tone if summary else c.muted),
            ],
            spacing=Spacing.XS,
        ),
    )


def _action_rows(
    shortcut_mgr: ShortcutManager, theme_mode: ft.ThemeMode,
    shortcut_focus: tuple, capturing: tuple,
    on_capture_click, on_cancel_capture_click,
    only_modified: bool = False,
) -> tuple[list[ft.Control], int, int]:
    """构造动作行，返回 (控件列表, 总项数, 已修改项数)。

    按「层 → 分类」两级分组：层用大标题，分类用小标题并汇总该分类下的改动条数。
    分类顺序取自 `ACTION_REGISTRY` 的登记顺序（保序分组），不额外维护顺序表。
    """
    c = get_colors(theme_mode)
    focus_key = tuple(shortcut_focus or (None, None))
    rows: list[ft.Control] = []
    total = 0
    changed_total = 0

    for layer in shortcut_mgr.layers():
        layer_actions = shortcut_mgr.actions_for_layer(layer)
        cmap = shortcut_mgr.conflict_map(layer)

        # 先算各动作的当前/默认/角标，再按分类分组
        entries: list[tuple] = []
        for action in layer_actions:
            current = shortcut_mgr.shortcut(layer, action.id)
            default = action.default.get(layer, "")
            badge = _shortcut_diff(current, default)
            entries.append((action, current, default, badge))
            total += 1
            if badge:
                changed_total += 1

        visible = [e for e in entries if e[3]] if only_modified else entries
        if not visible:
            continue

        rows.append(
            ft.Container(
                padding=ft.Padding.only(top=Spacing.SM, bottom=Spacing.XS),
                content=ft.Row(
                    spacing=Spacing.MD,
                    controls=[
                        ft.Text("浏览态" if layer == "browse" else "编辑态",
                                size=13, weight=ft.FontWeight.W_700, color=c.text),
                        ft.Text(f"{len(visible)} 项", size=11, color=c.muted),
                    ],
                ),
            )
        )

        groups: dict[str, list[tuple]] = {}
        for entry in visible:
            groups.setdefault(entry[0].category, []).append(entry)

        for category, items in groups.items():
            cat_changed = sum(1 for e in items if e[3])
            head: list[ft.Control] = [
                ft.Text(category, size=11, weight=ft.FontWeight.W_600, color=c.muted),
                ft.Text(f"{len(items)} 项", size=10, color=c.muted),
            ]
            if cat_changed:
                head.append(_badge(c, f"{cat_changed} 项已调整", _AMBER))
            rows.append(
                ft.Container(
                    padding=ft.Padding.only(top=Spacing.XL, bottom=Spacing.XS),
                    content=ft.Row(spacing=Spacing.MD, controls=head),
                )
            )
            for action, current, default, badge in items:
                is_conflict = bool(current and current in cmap)
                rows.append(_action_row(
                    action, layer, current, default, badge, is_conflict,
                    (layer, action.id) == focus_key,
                    c, shortcut_mgr,
                    capturing, on_capture_click, on_cancel_capture_click,
                ))

    return rows, total, changed_total


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
    action, layer: str, current: str, default: str, badge: str, is_conflict: bool,
    is_focused: bool, c, shortcut_mgr: ShortcutManager,
    capturing: tuple, on_capture_click, on_cancel_capture_click,
) -> ft.Control:
    is_capturing = capturing == (layer, action.id)
    row_key = f"{layer}:{action.id}"

    # 左侧：动作名（+ 已修改角标）+ 说明
    name_row: list[ft.Control] = [
        ft.Text(action.label, size=13, weight=ft.FontWeight.W_600, color=c.text),
    ]
    if badge:
        name_row.append(_badge(c, badge, _DANGER if badge == "已清空" else _AMBER))
    left = ft.Column(
        controls=[
            ft.Row(spacing=Spacing.MD, controls=name_row),
            ft.Text(
                f"{action.category} · {action.description}"
                + (f" · 默认 {_format_combo_display(default)}" if badge and default else ""),
                size=11, color=c.muted,
            ),
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
        # 不回退显示默认键：否则「已清空」角标旁边写着 Ctrl+S，自相矛盾。
        # 默认键改在下方说明里以「默认 Ctrl+S」给出，语义不冲突。
        kbd_display = current or ""
        kbd_color = _DANGER if is_conflict else c.text
        kbd_border = _DANGER if is_conflict else c.border
        kbd_bg = (
            ft.Colors.with_opacity(0.10, _DANGER) if is_conflict
            else ft.Colors.with_opacity(0.04, c.text)
        )
        right = ft.Row(
            controls=[
                ft.Container(
                    width=160,
                    padding=ft.Padding.symmetric(horizontal=Spacing.MD, vertical=Spacing.SM),
                    border_radius=Radius.MD,
                    bgcolor=kbd_bg,
                    border=_all_border(1, kbd_border),
                    alignment=ft.Alignment.CENTER,
                    content=ft.Text(_format_combo_display(kbd_display),
                                    size=12, color=kbd_color),
                ),
                ft.TextButton(
                    "修改",
                    on_click=lambda e, lyr=layer, a=action.id: on_capture_click(lyr, a),
                ),
                ft.TextButton(
                    "恢复默认",
                    on_click=lambda e, lyr=layer, a=action.id: shortcut_mgr.reset(lyr, a),
                    disabled=(not default) or (not badge),
                ),
            ],
            spacing=Spacing.SM,
        )
        # 冲突定位高亮（update_setting 写入 shortcuts 后指向首个冲突项）
        bgcolor = ft.Colors.with_opacity(0.05, c.link) if is_focused else None
        border = _all_border(1, c.link) if is_focused else None

    return ft.Container(
        key=row_key,
        bgcolor=bgcolor,
        border=border,
        border_radius=Radius.XL,
        padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.MD),
        content=ft.Row(
            controls=[left, right],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


# ---------------------------------------------------------------------------
# 关于 tab
# ---------------------------------------------------------------------------

def _about_row(
    c, icon, label: str, value: str, *, on_open=None, on_copy_value=None,
    tooltip: str = "",
) -> ft.Control:
    """关于页的信息行：图标 + 标题 + 值 + 右侧操作按钮。

    回调契约（两者都接收**本行的值**，不是零参）：
    - `on_open(value)`：用系统浏览器打开；邮箱行在这里把值转成 `mailto:`。
    - `on_copy_value(value)`：复制到剪贴板。
    两者都为 None 时该行不渲染对应按钮。
    """
    actions: list[ft.Control] = []
    if on_copy_value:
        actions.append(_icon_button(
            c, ft.Icons.CONTENT_COPY, lambda: on_copy_value(value),
            tooltip="复制", box=26, icon_size=14,
        ))
    if on_open:
        actions.append(_icon_button(
            c, ft.Icons.OPEN_IN_NEW, lambda: on_open(value),
            tooltip=tooltip or "在浏览器中打开", box=26, icon_size=15,
        ))
    return ft.Row(
        spacing=Spacing.LG,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Container(
                width=28, height=28,
                border_radius=Radius.MD,
                alignment=ft.Alignment.CENTER,
                bgcolor=ft.Colors.with_opacity(0.08, c.text),
                content=ft.Icon(icon, size=15, color=c.muted),
            ),
            ft.Text(label, width=76, size=12, color=c.text),
            ft.Container(
                expand=True,
                content=ft.Text(value, size=12, color=c.muted, max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS),
            ),
            *actions,
        ],
    )


def _about_panel(theme_mode: ft.ThemeMode, on_open_url=None, on_copy=None) -> ft.Control:
    """关于页：版本信息 + 联系邮箱 / 官方社群 / 用户手册 + 相关链接。

    外链与版本全部取自 `config/app_meta`（唯一来源），此处不写死任何 URL。
    """
    c = get_colors(theme_mode)
    version = app_meta.app_version()
    email = (app_meta.SUPPORT_EMAIL or "").strip()
    community = (app_meta.COMMUNITY_URL or "").strip()
    manual = (app_meta.USER_MANUAL_URL or "").strip()

    hero = ft.Container(
        padding=ft.Padding.all(Spacing.XXL),
        border_radius=Radius.XL,
        bgcolor=ft.Colors.with_opacity(0.06, c.link),
        content=ft.Row(
            spacing=Spacing.XXL,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Container(
                    width=56, height=56,
                    border_radius=Radius.XXL,
                    alignment=ft.Alignment.CENTER,
                    bgcolor=ft.Colors.with_opacity(0.16, c.link),
                    content=ft.Icon(ft.Icons.ARTICLE_OUTLINED, size=28, color=c.link),
                ),
                ft.Column(
                    spacing=Spacing.XS,
                    expand=True,
                    controls=[
                        ft.Text(app_meta.APP_NAME, size=18,
                                weight=ft.FontWeight.W_700, color=c.text),
                        ft.Row(
                            spacing=Spacing.MD,
                            controls=[
                                _badge(c, f"v{version}", c.link),
                                ft.Text(app_meta.APP_TAGLINE, size=12, color=c.muted),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    )

    version_rows: list[ft.Control] = [
        _about_row(c, ft.Icons.TAG, "版本", version,
                   on_copy_value=(lambda v: on_copy(v)) if on_copy else None,
                   tooltip="复制版本号"),
        _about_row(c, ft.Icons.MEMORY, "运行环境", app_meta.runtime_summary(),
                   on_copy_value=(lambda v: on_copy(v)) if on_copy else None,
                   tooltip="复制运行环境"),
        _about_row(c, ft.Icons.FINGERPRINT, "作者", app_meta.APP_AUTHOR),
        _about_row(c, ft.Icons.BALANCE, "开源许可", app_meta.APP_LICENSE),
    ]

    support_rows: list[ft.Control] = [
        _about_row(
            c, ft.Icons.MAIL_OUTLINE, "联系邮箱",
            email or "未配置",
            on_open=(lambda v: on_open_url(app_meta.mailto(v)))
            if (on_open_url and email) else None,
            on_copy_value=(lambda v: on_copy(v)) if (on_copy and email) else None,
            tooltip="用邮件客户端写信",
        ),
        _about_row(
            c, ft.Icons.GROUPS_OUTLINED, "官方社群",
            community or "未配置",
            on_open=(lambda v: on_open_url(v))
            if (on_open_url and community) else None,
            on_copy_value=(lambda v: on_copy(v)) if (on_copy and community) else None,
        ),
        _about_row(
            c, ft.Icons.MENU_BOOK_OUTLINED, "用户手册",
            manual or "未配置",
            on_open=(lambda v: on_open_url(v))
            if (on_open_url and manual) else None,
            on_copy_value=(lambda v: on_copy(v)) if (on_copy and manual) else None,
        ),
    ]

    link_rows: list[ft.Control] = [
        _about_row(
            c, getattr(ft.Icons, icon_name), title, url,
            on_open=(lambda v: on_open_url(v)) if on_open_url else None,
            on_copy_value=(lambda v: on_copy(v)) if on_copy else None,
        )
        for icon_name, title, url in app_meta.EXTRA_LINKS
    ]

    return ft.Column(
        spacing=Spacing.XXL,
        controls=[
            hero,
            _card(c, title="版本信息", controls=version_rows),
            _card(
                c,
                title="联系与支持",
                desc="遇到问题或想反馈建议，欢迎通过以下渠道联系。",
                controls=support_rows,
            ),
            _card(c, title="相关链接", controls=link_rows),
        ],
    )
