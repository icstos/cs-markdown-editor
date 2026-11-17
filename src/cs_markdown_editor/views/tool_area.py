"""工具区组件：文件菜单 + 格式工具栏 + 视图切换按钮。

从 views/editor.py 的 _tool_area 闭包提取而来，职责单一：
仅负责工具区的声明式构建，不持有任何编辑器状态。

记忆化策略（@ft.memo）：
- 主题色通过 theme_mode prop 显式传入（get_colors(theme_mode)），
  避免 _current_colors() 读取 ft.context 导致 memo 失效。
- 所有回调均由调用方用 _make_stable_cb 稳定化后传入，
  确保 editor 重渲染时 ToolArea 不被无谓重算。
- raw_mode / show_toolbar 为基本类型 prop，变化时才重渲染。

依赖项：styles（配色/间距/边框）、views.toolbar（Toolbar/_btn/_divider）、models.BlockType。
"""

from collections.abc import Callable

import ui

from cs_markdown_editor.models.document import BlockType
from cs_markdown_editor.styles import Spacing, get_colors, only_border
from cs_markdown_editor.views.toolbar import Toolbar, _btn
from cs_markdown_editor.views.toolbar import _divider as _tb_divider


@ui.memo
@ui.component
def ToolArea(
    *,
    theme_mode: ui.ThemeMode,
    show_toolbar: bool,
    shortcut_mgr,
    raw_mode: bool,
    on_new: Callable[[], None],
    on_open: Callable[[], None],
    on_open_folder: Callable[[], None],
    on_save: Callable[[], None],
    on_open_settings: Callable[[], None],
    set_block: Callable[..., None],
    apply_inline_format: Callable[[str], None],
    on_toggle_raw: Callable[[], None],
    on_export_html: Callable[[], None],
    on_export_docx: Callable[[], None],
    on_export_pdf: Callable[[], None],
    on_toggle_focus_mode: Callable[[], None],
    on_toggle_theme: Callable[[], None],
):
    """顶部工具区：文件菜单 + 格式工具栏 + 视图/主题切换。

    所有回调应为稳定引用（由 editor 用 _make_stable_cb 包装），
    以保证 @ft.memo 浅比较生效，避免输入时整条工具栏重渲染。

    导出采用 PopupMenuButton 分格式入口（HTML / Word / PDF），
    桌面端软件直觉：类似 Typora/Office 的「文件 → 导出 →」级联菜单。
    """
    c = get_colors(theme_mode)

    if not show_toolbar:
        return ui.Container(height=0)

    menu_items = [
        ui.PopupMenuItem(content="新建", on_click=lambda e: on_new()),
        ui.PopupMenuItem(content="打开...", on_click=lambda e: on_open()),
        ui.PopupMenuItem(content="打开文件夹...", on_click=lambda e: on_open_folder()),
        ui.PopupMenuItem(content="保存", on_click=lambda e: on_save()),
        ui.PopupMenuItem(),
        ui.PopupMenuItem(content="设置", on_click=lambda e: on_open_settings()),
    ]

    # 导出菜单：HTML / Word / PDF 三格式分入口
    export_items = [
        ui.PopupMenuItem(
            content=ui.Row(
                controls=[
                    ui.Icon(ui.Icons.CODE, size=18, color=c.muted),
                    ui.Text("HTML", size=14),
                ],
                spacing=Spacing.SM,
                vertical_alignment=ui.CrossAxisAlignment.CENTER,
            ),
            on_click=lambda e: on_export_html(),
        ),
        ui.PopupMenuItem(
            content=ui.Row(
                controls=[
                    ui.Icon(ui.Icons.DESCRIPTION, size=18, color=c.muted),
                    ui.Text("Word", size=14),
                ],
                spacing=Spacing.SM,
                vertical_alignment=ui.CrossAxisAlignment.CENTER,
            ),
            on_click=lambda e: on_export_docx(),
        ),
        ui.PopupMenuItem(
            content=ui.Row(
                controls=[
                    ui.Icon(ui.Icons.PICTURE_AS_PDF, size=18, color=c.muted),
                    ui.Text("PDF", size=14),
                ],
                spacing=Spacing.SM,
                vertical_alignment=ui.CrossAxisAlignment.CENTER,
            ),
            on_click=lambda e: on_export_pdf(),
        ),
    ]

    return ui.Container(
        bgcolor=ui.Colors.with_opacity(0.96, c.toolbar_bg),
        border=only_border(bottom=ui.BorderSide(1, c.border)),
        padding=ui.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
        content=ui.Row(
            controls=[
                ui.PopupMenuButton(
                    icon=ui.Icons.MENU,
                    tooltip="文件菜单",
                    items=menu_items,
                ),
                _tb_divider(),
                Toolbar(
                    shortcut_mgr=shortcut_mgr,
                    on_h1=lambda: set_block(BlockType.HEADING, 1),
                    on_h2=lambda: set_block(BlockType.HEADING, 2),
                    on_h3=lambda: set_block(BlockType.HEADING, 3),
                    on_paragraph=lambda: set_block(BlockType.PARAGRAPH),
                    on_list=lambda: set_block(BlockType.LIST_UO),
                    on_task=lambda: set_block(BlockType.LIST_UO, task=True),
                    on_quote=lambda: set_block(BlockType.QUOTE),
                    on_code_block=lambda: set_block(BlockType.CODE),
                    on_hr=lambda: set_block(BlockType.HR),
                    on_math_block=lambda: set_block(BlockType.MATH),
                    on_table=lambda: set_block(BlockType.TABLE),
                    on_bold=lambda: apply_inline_format("bold"),
                    on_italic=lambda: apply_inline_format("italic"),
                    on_highlight=lambda: apply_inline_format("highlight"),
                    on_code=lambda: apply_inline_format("code"),
                    on_link=lambda: apply_inline_format("link"),
                    on_inline_math=lambda: apply_inline_format("inline_math"),
                    on_strike=lambda: apply_inline_format("strike"),
                ),
                ui.Container(expand=True),
                _btn(
                    ui.Icons.VISIBILITY if not raw_mode else ui.Icons.EDIT,
                    "原文模式" if not raw_mode else "返回编辑",
                    on_toggle_raw,
                    toggle_on=raw_mode,
                ),
                ui.PopupMenuButton(
                    icon=ui.Icons.FILE_DOWNLOAD,
                    tooltip="导出",
                    items=export_items,
                ),
                _btn(ui.Icons.CENTER_FOCUS_STRONG, "聚焦模式", on_toggle_focus_mode),
                _btn(
                    ui.Icons.DARK_MODE if theme_mode == ui.ThemeMode.LIGHT else ui.Icons.LIGHT_MODE,
                    "切换暗色" if theme_mode == ui.ThemeMode.LIGHT else "切换亮色",
                    on_toggle_theme,
                ),
                _btn(ui.Icons.SETTINGS, "设置  Ctrl+,", on_open_settings),
            ],
            spacing=Spacing.MD,
            vertical_alignment=ui.CrossAxisAlignment.CENTER,
        ),
    )
