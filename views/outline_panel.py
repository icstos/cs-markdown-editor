"""大纲列（横向四列布局第四列）：VSCode / Obsidian 风格右侧大纲面板。

复用 views.sidebar 的 _compute_toc（标题派生）与 _render_outline_panel
（标题树渲染），标题点击跳转到对应行。收起/展开不再内嵌按钮，改为在
底部状态栏最右侧提供切换入口（参考「切换侧边栏」按钮，VSCode 直觉）；
收起时内容宽度为 0（HARD_EDGE 裁剪 + 200ms 动画）。
"""

from collections.abc import Callable

import flet as ft

from models import BlockType, Document
from styles import FONT_MAIN, Spacing, _current_colors, only_border
from views.sidebar import _compute_toc, _render_outline_panel

_OUTLINE_W = 240  # 大纲列固定宽度（可折叠）


@ft.component
def OutlinePanel(
    document: Document,
    theme_mode: ft.ThemeMode,
    open: bool,
    on_jump_to_line: Callable[[int], None],
) -> ft.Control:
    """右侧大纲列：标题树，开合由底部状态栏最右侧按钮控制。

    open=False 时内容宽度为 0（HARD_EDGE 裁剪 + 200ms 动画），
    展开/收起按钮位于状态栏最右侧（与侧边栏切换同款交互）。
    """
    c = _current_colors()

    # 大纲条目：按标题行签名 use_memo 缓存（仅标题增删改才重算）
    _toc_sig = tuple(
        (i, ln.level, ln.raw)
        for i, ln in enumerate(document.lines)
        if ln.block_type == BlockType.HEADING
    ) if document is not None else ()
    toc_entries = ft.use_memo(lambda: _compute_toc(document), [_toc_sig])

    panel_body = ft.Column(
        controls=[
            # 头部：大纲标题（开合入口在状态栏最右侧，不再内嵌按钮）
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

    return ft.Container(
        width=_OUTLINE_W if open else 0,
        animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        bgcolor=c.surface,
        content=panel_body,
    )
