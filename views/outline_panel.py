"""大纲列（横向四列布局第四列）：VSCode / Obsidian 风格右侧大纲面板。

复用 views.sidebar 的 _compute_toc（标题派生）与 _render_outline_panel
（标题树渲染），标题点击跳转到对应行。支持一键收起：内容宽度 0 时仅
保留左侧一条竖条（chevron 提示方向），点击展开/收起。
"""

from collections.abc import Callable

import flet as ft

from models import BlockType, Document
from styles import FONT_MAIN, Spacing, _current_colors, only_border
from views.sidebar import _compute_toc, _render_outline_panel

_OUTLINE_W = 240  # 大纲列固定宽度（可折叠）
_STRIP_W = 18  # 收起/展开竖条宽度（始终可见）


@ft.component
def OutlinePanel(
    document: Document,
    theme_mode: ft.ThemeMode,
    open: bool,
    on_toggle: Callable[[], None],
    on_jump_to_line: Callable[[int], None],
) -> ft.Control:
    """右侧大纲列：标题树 + 一键收起。

    open=False 时内容宽度为 0（HARD_EDGE 裁剪 + 200ms 动画），左侧竖条
    常驻作为展开入口；展开时竖条 chevron 指向右（点击收起）。
    """
    c = _current_colors()

    # 大纲条目：按标题行签名 use_memo 缓存（仅标题增删改才重算）
    _toc_sig = tuple(
        (i, ln.level, ln.raw)
        for i, ln in enumerate(document.lines)
        if ln.block_type == BlockType.HEADING
    ) if document is not None else ()
    toc_entries = ft.use_memo(lambda: _compute_toc(document), [_toc_sig])

    # 收起/展开竖条：始终可见（唯一常驻控件，方向图标提示当前动作）
    strip = ft.Container(
        width=_STRIP_W,
        bgcolor=c.toolbar_bg,
        border=only_border(left=ft.BorderSide(1, c.border)),
        alignment=ft.Alignment(0, 0),
        on_click=lambda e: on_toggle(),
        tooltip="收起大纲" if open else "展开大纲",
        ink=True,
        content=ft.Icon(
            ft.Icons.CHEVRON_RIGHT if open else ft.Icons.CHEVRON_LEFT,
            size=14,
            color=c.muted,
        ),
    )

    panel_body = ft.Column(
        controls=[
            # 头部：大纲标题 + 收起按钮（内容区内的二次入口）
            ft.Container(
                bgcolor=c.toolbar_bg,
                border=only_border(bottom=ft.BorderSide(1, c.border)),
                padding=ft.Padding.symmetric(
                    horizontal=Spacing.LG, vertical=Spacing.LG,
                ),
                content=ft.Row(
                    controls=[
                        ft.Icon(
                            ft.Icons.FORMAT_LIST_BULLETED,
                            size=13,
                            color=c.muted,
                        ),
                        ft.Text(
                            "大纲",
                            size=11,
                            color=c.muted,
                            font_family=FONT_MAIN,
                            weight=ft.FontWeight.W_600,
                        ),
                        ft.Container(expand=True),
                        ft.IconButton(
                            icon=ft.Icons.CHEVRON_RIGHT,
                            icon_size=14,
                            tooltip="收起大纲",
                            on_click=lambda e: on_toggle(),
                            style=ft.ButtonStyle(
                                color=c.muted,
                                padding=Spacing.XS,
                            ),
                        ),
                    ],
                    spacing=Spacing.SM,
                ),
            ),
            ft.Container(
                expand=True,
                content=_render_outline_panel(toc_entries, on_jump_to_line, c),
            ),
        ],
        spacing=0,
        expand=True,
    )

    return ft.Row(
        controls=[
            strip,
            ft.Container(
                width=_OUTLINE_W if open else 0,
                animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                bgcolor=c.surface,
                content=panel_body,
            ),
        ],
        spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
    )
