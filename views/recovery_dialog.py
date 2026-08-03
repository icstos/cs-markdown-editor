"""恢复面板：列出可恢复草稿并提供恢复 / 删除交互。

启动时若存在可恢复草稿（services.recovery.find_recoverable_on_startup），
App 设置 recovery_open=True 弹出本面板。用户可：
- 打开：在新标签页载入备份内容（不强制保存，由用户决定）
- 删除：删除备份文件
- 跳过：关闭面板（不删除备份，下次手动入口仍可访问）

视觉风格与 views/file_dialogs.py / settings_dialog.py 一致：
半透明遮罩 + 卡片弹层 + 圆角阴影。

依赖项：
- os / flet
- services.backup.BackupInfo（备份元信息 dataclass）
- styles（FONT_MAIN / Elevation / Radius / Spacing / card_shadow / get_colors）
"""

import os
from collections.abc import Callable

import flet as ft

from services.backup import BackupInfo
from styles import (
    FONT_MAIN,
    Elevation,
    Radius,
    Spacing,
    card_shadow,
    get_colors,
)


def _format_backup_time(t) -> str:
    """格式化备份时间：YYYY-MM-DD HH:MM:SS。"""
    try:
        return t.strftime("%Y-%m-%d %H:%M:%S")
    except (AttributeError, ValueError):
        return "未知时间"


def _format_size(size: int) -> str:
    """格式化文件大小：B / KB / MB。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def _backup_kind_label(info: BackupInfo) -> str:
    """备份类型标签：未命名草稿 / 已命名文档。"""
    return "未命名草稿" if info.is_untitled else "已命名文档"


@ft.component
def RecoveryDialog(
    open_state: bool,
    backups: list[BackupInfo],
    theme_mode: ft.ThemeMode,
    on_open: Callable[[str], None],
    on_delete: Callable[[str], None],
    on_close: Callable[[], None],
    title: str = "恢复未保存的草稿",
    empty_hint: str = "没有可恢复的草稿",
):
    """恢复面板弹层。

    open_state: 是否显示；backups: BackupInfo 列表（按时间降序）。
    on_open(path): 用户点击「打开」时调用，path 为备份文件路径。
    on_delete(path): 用户点击「删除」时调用。
    on_close(): 用户点击「跳过」/「关闭」时调用。
    title: 面板标题（启动时为「恢复未保存的草稿」，手动入口为「历史备份」）。
    empty_hint: 无可恢复草稿时的提示文案。
    """
    c = get_colors(theme_mode)
    is_dark = theme_mode == ft.ThemeMode.DARK

    def _text_btn(label: str, on_click: Callable, color: str) -> ft.Control:
        return ft.TextButton(
            label,
            on_click=lambda e: on_click(),
            style=ft.ButtonStyle(color=color),
        )

    # 备份条目卡片
    items: list[ft.Control] = []
    if not backups:
        items.append(
            ft.Container(
                padding=Spacing.XXXL,
                alignment=ft.Alignment.CENTER,
                content=ft.Text(
                    empty_hint,
                    size=13,
                    color=c.muted,
                    font_family=FONT_MAIN,
                    text_align=ft.TextAlign.CENTER,
                ),
            )
        )
    else:
        for info in backups:
            items.append(_backup_card(info, c, on_open, on_delete))

    return ft.Container(
        visible=open_state,
        expand=True,
        bgcolor=ft.Colors.with_opacity(0.28, ft.Colors.BLACK),
        alignment=ft.Alignment.CENTER,
        content=ft.Container(
            width=720,
            height=560,
            bgcolor=c.toolbar_bg,
            border_radius=Radius.XXXL,
            shadow=card_shadow(Elevation.DIALOG, is_dark),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            padding=Spacing.XXXL,
            content=ft.Column(
                controls=[
                    # 顶部标题 + 关闭按钮
                    ft.Row(
                        controls=[
                            ft.Icon(
                                ft.Icons.RESTORE_PAGE,
                                color=c.link,
                                size=24,
                            ),
                            ft.Text(
                                value=title,
                                size=18,
                                weight=ft.FontWeight.W_600,
                                color=c.text,
                                font_family=FONT_MAIN,
                            ),
                            ft.Container(expand=True),
                            ft.IconButton(
                                icon=ft.Icons.CLOSE,
                                on_click=lambda e: on_close(),
                                icon_size=18,
                            ),
                        ],
                        spacing=Spacing.XL,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(height=Spacing.SM),
                    ft.Text(
                        value="下列草稿来自上次会话或历史备份，可选择打开或删除。",
                        size=12,
                        color=c.muted,
                        font_family=FONT_MAIN,
                    ),
                    ft.Container(height=Spacing.LG),
                    # 备份列表（可滚动）
                    ft.Container(
                        expand=True,
                        border_radius=Radius.XL,
                        bgcolor=ft.Colors.with_opacity(0.04, c.text),
                        padding=ft.Padding.all(Spacing.LG),
                        content=ft.Column(
                            controls=items,
                            spacing=Spacing.MD,
                            scroll=ft.ScrollMode.AUTO,
                        ),
                    ),
                    ft.Container(height=Spacing.LG),
                    # 底部按钮
                    ft.Row(
                        controls=[
                            ft.Container(expand=True),
                            _text_btn("跳过", on_close, c.muted),
                        ],
                        spacing=Spacing.LG,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=0,
            ),
        ),
    )


def _backup_card(
    info: BackupInfo,
    c,
    on_open: Callable[[str], None],
    on_delete: Callable[[str], None],
) -> ft.Control:
    """单条备份卡片：预览 + 元信息 + 打开 / 删除按钮。"""
    kind_label = _backup_kind_label(info)
    kind_color = c.link if info.is_untitled else c.muted
    time_str = _format_backup_time(info.backup_time)
    size_str = _format_size(info.size_bytes)

    # 原文件路径显示（已命名文档）
    if info.original_path:
        path_text = os.path.basename(info.original_path)
        path_hint = f"原文件：{info.original_path}"
    else:
        path_text = ""
        path_hint = ""

    # 预览文本
    preview_text = info.preview or "(空文档)"

    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
        border_radius=Radius.LG,
        bgcolor=ft.Colors.with_opacity(0.06, c.border),
        content=ft.Row(
            controls=[
                # 左侧：预览 + 元信息
                ft.Column(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Container(
                                    padding=ft.Padding.symmetric(
                                        horizontal=Spacing.SM, vertical=Spacing.XS
                                    ),
                                    border_radius=Radius.SM,
                                    bgcolor=ft.Colors.with_opacity(0.10, kind_color),
                                    content=ft.Text(
                                        kind_label,
                                        size=10,
                                        color=kind_color,
                                        weight=ft.FontWeight.W_600,
                                    ),
                                ),
                                ft.Text(
                                    value=preview_text,
                                    size=13,
                                    color=c.text,
                                    font_family=FONT_MAIN,
                                    max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                    expand=True,
                                ),
                            ],
                            spacing=Spacing.MD,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Row(
                            controls=[
                                ft.Text(
                                    value=time_str,
                                    size=11,
                                    color=c.muted,
                                    font_family=FONT_MAIN,
                                ),
                                ft.Container(width=Spacing.LG),
                                ft.Text(
                                    value=size_str,
                                    size=11,
                                    color=c.muted,
                                    font_family=FONT_MAIN,
                                ),
                                *(
                                    [
                                        ft.Container(width=Spacing.LG),
                                        ft.Text(
                                            value=path_text,
                                            size=11,
                                            color=c.muted,
                                            font_family=FONT_MAIN,
                                            max_lines=1,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                            tooltip=path_hint,
                                        ),
                                    ]
                                    if path_text
                                    else []
                                ),
                            ],
                            spacing=0,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ],
                    spacing=Spacing.XS,
                    expand=True,
                ),
                # 右侧：操作按钮
                ft.Row(
                    controls=[
                        ft.TextButton(
                            "打开",
                            on_click=lambda e, p=info.backup_path: on_open(p),
                            style=ft.ButtonStyle(color=c.link),
                        ),
                        ft.TextButton(
                            "删除",
                            on_click=lambda e, p=info.backup_path: on_delete(p),
                            style=ft.ButtonStyle(color=c.muted),
                        ),
                    ],
                    spacing=Spacing.SM,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
