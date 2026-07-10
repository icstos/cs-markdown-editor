"""共享样式常量与段→TextStyle 映射。

集中管理颜色与排版，保证视图层声明式、无散落的魔法数字。
"""

from __future__ import annotations

import flet as ft

from models import (
    BLOCK_CODE,
    BLOCK_HEADING,
    BLOCK_HR,
    BLOCK_LIST_O,
    BLOCK_LIST_UO,
    BLOCK_PARAGRAPH,
    BLOCK_QUOTE,
    SEG_CODESPAN,
    SEG_EMPHASIS,
    SEG_IMAGE,
    SEG_LINK,
    SEG_STRONG,
    SEG_STRIKE,
    SEG_TEXT,
    Segment,
)

# 字体族
FONT_MAIN = "Alibaba"
FONT_MONO = "Consolas"  # 代码块的等宽回退，提升可读性

# 颜色
C_BG = ft.Colors.WHITE
C_SURFACE = "#FFFFFF"
C_TEXT = "#1F2329"
C_MUTED = "#8A919E"
C_LINK = "#1677FF"
C_CODE_BG = "#F2F3F5"
C_CODE_FG = "#C7254E"
C_STRIKE = "#8A919E"
C_QUOTE_FG = "#595959"
C_QUOTE_BAR = "#D9D9D9"
C_CODE_BLOCK_BG = "#F6F8FA"
C_CODE_BLOCK_FG = "#1F2329"
C_HOVER = "#F0F7FF"
C_ACTIVE_BG = "#FFFBEA"  # 正在编辑的段淡黄底，呼应 Typora
C_TOOLBAR_BG = "#FAFBFC"
C_BORDER = "#E5E6EB"


def block_text_size(block_type: str, level: int = 0) -> int:
    """块级正文基础字号。"""
    if block_type == BLOCK_HEADING:
        return {1: 30, 2: 24, 3: 20, 4: 18, 5: 16, 6: 16}.get(level, 16)
    if block_type == BLOCK_CODE:
        return 14
    return 16


def block_weight(block_type: str, level: int = 0) -> ft.FontWeight:
    if block_type == BLOCK_HEADING:
        return ft.FontWeight.BOLD
    return ft.FontWeight.NORMAL


def segment_style(seg: Segment, base_size: int = 16) -> ft.TextStyle:
    """把段类型映射为 TextStyle（渲染态）。"""
    t = seg.seg_type
    if t == SEG_STRONG:
        return ft.TextStyle(size=base_size, weight=ft.FontWeight.BOLD, color=C_TEXT)
    if t == SEG_EMPHASIS:
        return ft.TextStyle(size=base_size, italic=True, color=C_TEXT)
    if t == SEG_STRIKE:
        return ft.TextStyle(
            size=base_size, color=C_STRIKE, decoration=ft.TextDecoration.LINE_THROUGH
        )
    if t == SEG_CODESPAN:
        return ft.TextStyle(
            size=base_size - 1,
            color=C_CODE_FG,
            bgcolor=C_CODE_BG,
            font_family=FONT_MONO,
        )
    if t == SEG_LINK:
        return ft.TextStyle(
            size=base_size, color=C_LINK, decoration=ft.TextDecoration.UNDERLINE
        )
    if t == SEG_IMAGE:
        return ft.TextStyle(size=base_size, color=C_LINK, italic=True)
    return ft.TextStyle(size=base_size, color=C_TEXT)


def prefix_style(seg: Segment, base_size: int = 16) -> ft.TextStyle:
    """块级前缀段（# - >）的样式：弱化显示。"""
    return ft.TextStyle(size=base_size, color=C_MUTED, weight=ft.FontWeight.BOLD)


_NO_BORDER = ft.BorderSide.none()


def only_border(*, top=None, bottom=None, left=None, right=None) -> ft.Border:
    """便捷构造单边 Border。"""
    return ft.Border(
        top=top or _NO_BORDER,
        right=right or _NO_BORDER,
        bottom=bottom or _NO_BORDER,
        left=left or _NO_BORDER,
    )
