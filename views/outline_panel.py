"""大纲列（横向四列布局第四列）：VSCode / Obsidian 风格右侧大纲面板。

标题派生用 utils.toc.compute_toc，标题树渲染用 views.toc.render_outline_panel。
收起/展开不内嵌按钮，改由底部状态栏最右侧提供切换入口（与「切换侧边栏」
同款交互）；收起时内容宽度为 0（HARD_EDGE 裁剪 + 200ms 动画）。
头部高度锁定 styles.TOPBAR_H，与标签行一致——两列顶栏连成同一条水平带。
"""

from collections.abc import Callable

import flet as ft

from models.document import BlockType, Document
from styles import FONT_MAIN, TOPBAR_H, Spacing, _current_colors, only_border
from utils.toc import compute_toc
from views.toc import render_outline_panel

_OUTLINE_W = 240  # 大纲列固定宽度（可折叠）


@ft.component
def OutlinePanel(
    document: Document,
    theme_mode: ft.ThemeMode,
    open: bool,
    on_jump_to_line: Callable[[int], None],
) -> ft.Control:
    """右侧大纲列：标题树，开合由底部状态栏最右侧按钮控制。"""
    c = _current_colors()

    # 大纲条目：按标题行签名 use_memo 缓存（仅标题增删改才重算）
    _toc_sig = (
        tuple(
            (i, ln.level, ln.raw)
            for i, ln in enumerate(document.lines)
            if ln.block_type == BlockType.HEADING
        )
        if document is not None
        else ()
    )
    toc_entries = ft.use_memo(lambda: compute_toc(document), [_toc_sig])

    panel_body = ft.Column(
        controls=[
            # 头部：大纲标题（开合入口在状态栏最右侧，不再内嵌按钮）
            # 与标签行**同物理结构**：定高的内容带（height=TOPBAR_H）+ 外挂 1px 底边线。
            # 底边线必须留在定高盒之外：`Container(height=H, border=bottom 1px)` 的总高
            # **就是 H**（边线被算进 H 内，内容带只剩 H-1），而标签栏的边线是加在内容带
            # 之外的（33 vs 32）——两者会差 1px、底线不共线（真机实测过）。
            # 显式定高而非靠 padding 撑：高度是跨模块契约，不能依赖「单行 11px 文本恰好
            # 多高」这种隐式事实。详见 styles.TOPBAR_H 与 tests/test_topbar_height.py。
            ft.Container(
                border=only_border(bottom=ft.BorderSide(1, c.border)),
                content=ft.Container(
                    bgcolor=c.toolbar_bg,
                    height=TOPBAR_H,
                    alignment=ft.Alignment.CENTER,
                    padding=ft.Padding.symmetric(
                        horizontal=Spacing.LG,
                        vertical=0,
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
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ),
            ),
            ft.Container(
                expand=True,
                content=render_outline_panel(toc_entries, on_jump_to_line, c),
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
        # 与侧边栏右缘对称：左侧 1px 细分割线，列边界清晰
        border=only_border(left=ft.BorderSide(1, c.border)),
        content=panel_body,
    )
