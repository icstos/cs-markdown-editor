"""原文模式编辑器：纯 Markdown 源码编辑视图。

从 views/editor.py 的 _raw_editor 闭包提取而来，职责单一：
仅负责原文 TextField 的声明式构建与受控值绑定，不直接操作文档。

记忆化策略（@ft.memo）：
- 主题色通过 theme_mode prop 显式传入（get_colors(theme_mode)）。
- on_change 回调由调用方稳定化后传入，避免输入时无谓重渲染。
- raw_draft 为受控值 prop，变化时同步 TextField.value。

依赖项：styles（配色/字体）、flet。
"""

from collections.abc import Callable

import ui

from cs_markdown_editor.styles import FONT_MONO, Spacing, get_colors


@ui.memo
@ui.component
def RawEditor(
    *,
    theme_mode: ui.ThemeMode,
    raw_draft: str,
    on_change: Callable[[str], None],
    content_padding: int,
    content_padding_top: int,
    body_font_size: int,
):
    """原文模式：等宽字体的多行 TextField，受控于 raw_draft。

    on_change(value) 由调用方实现文档同步（set_raw_draft + 重解析 + mark_dirty）。
    """
    c = get_colors(theme_mode)

    return ui.Container(
        expand=True,
        alignment=ui.Alignment.TOP_LEFT,
        bgcolor=c.bg,
        # 垂直留白固定不随内容滚动；水平内边距移入 TextField（content_padding），
        # 使滚动条贴紧列最右缘，与所见即所得模式一致
        padding=ui.Padding.symmetric(vertical=content_padding_top),
        content=ui.TextField(
            value=raw_draft,
            multiline=True,
            min_lines=20,
            border=ui.NoInputBorder(),
            text_size=body_font_size,
            text_style=ui.TextStyle(font_family=FONT_MONO, color=c.text),
            on_change=lambda e: on_change(e.control.value),
            content_padding=ui.Padding.symmetric(
                horizontal=content_padding, vertical=Spacing.SM
            ),
            expand=True,
        ),
    )
