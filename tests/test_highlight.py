"""高亮（==text==）功能优化测试。

参考 Typora 高亮效果，验证：
- 解析：==高亮== 正确识别为 SegType.HIGHLIGHT 段
- 序列化往返：==高亮== 解析后序列化还原原文
- HTML 导出：==高亮== 输出 <mark> 标签
- 组合格式：**==加粗高亮==** / *==斜体高亮==* marks 正确累加
- 渲染样式：segment_style 返回含 bgcolor 的 TextStyle
- 快捷键：format_highlight 默认 Ctrl+Shift+H（非 Ctrl+U，避免与下划线语义冲突）
- toggle 包裹/取消：apply_inline_format 对选区包裹 ==，再次按下取消
- 工具栏 tooltip：动态读取快捷键显示
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from models import BlockType, Document, Line, Segment, SegType  # noqa: E402
from parser import parse_markdown, serialize, to_html  # noqa: E402
from services.shortcuts import ShortcutManager  # noqa: E402
from styles import _LIGHT, _DARK, segment_style  # noqa: E402
from utils.segment_helpers import WRAP_SYNTAX, display_text, split_seg_for_display  # noqa: E402


# ================ 解析与序列化 ================

def test_highlight_parse():
    """==高亮== 解析为 HIGHLIGHT 段，text 为纯内容。"""
    doc = parse_markdown("这是一段==高亮==文字")
    line = doc.lines[0]
    highlight_segs = [s for s in line.segments if s.seg_type == SegType.HIGHLIGHT]
    assert len(highlight_segs) == 1
    assert highlight_segs[0].text == "高亮"
    assert highlight_segs[0].raw == "==高亮=="


def test_highlight_serialize_roundtrip():
    """==高亮== 解析后序列化还原原文。"""
    md = "==高亮=="
    assert serialize(parse_markdown(md)) == md


def test_highlight_in_sentence_roundtrip():
    """句子中的高亮往返保持完整。"""
    md = "前文==高亮内容==后文"
    assert serialize(parse_markdown(md)) == md


def test_highlight_multiple_in_line():
    """一行多个高亮段独立解析。"""
    doc = parse_markdown("==a== 中间 ==b==")
    highlight_segs = [s for s in doc.lines[0].segments if s.seg_type == SegType.HIGHLIGHT]
    assert len(highlight_segs) == 2
    assert highlight_segs[0].text == "a"
    assert highlight_segs[1].text == "b"


# ================ HTML 导出 ================

def test_highlight_to_html_mark_tag():
    """==高亮== 导出为 <mark> 标签（HTML5 语义高亮）。"""
    html = to_html("==高亮==")
    assert "<mark>" in html
    assert "高亮" in html


def test_highlight_to_html_in_sentence():
    """句子中的高亮导出 <mark> 包裹正确内容。"""
    html = to_html("前文==高亮==后文")
    assert "<mark>高亮</mark>" in html


# ================ 组合格式 ================

def test_highlight_combined_with_bold():
    """**==加粗高亮==** 组合格式：marks 含 STRONG + HIGHLIGHT。"""
    doc = parse_markdown("**==加粗高亮==**")
    seg = doc.lines[0].segments[0]
    assert SegType.HIGHLIGHT in (seg.marks or ())
    assert SegType.STRONG in (seg.marks or ())


def test_highlight_combined_with_italic():
    """*==斜体高亮==* 组合格式：marks 含 EMPHASIS + HIGHLIGHT。"""
    doc = parse_markdown("*==斜体高亮==*")
    seg = doc.lines[0].segments[0]
    assert SegType.HIGHLIGHT in (seg.marks or ())
    assert SegType.EMPHASIS in (seg.marks or ())


def test_highlight_combined_with_strike():
    """~~==删除高亮==~~ 组合格式：marks 含 STRIKE + HIGHLIGHT。"""
    doc = parse_markdown("~~==删除高亮==~~")
    seg = doc.lines[0].segments[0]
    assert SegType.HIGHLIGHT in (seg.marks or ())
    assert SegType.STRIKE in (seg.marks or ())


def test_highlight_combined_roundtrip():
    """组合格式往返保持完整。"""
    md = "**==加粗高亮==**"
    assert serialize(parse_markdown(md)) == md


# ================ 渲染样式 ================

def test_highlight_segment_style_has_bgcolor():
    """HIGHLIGHT 段渲染样式含 bgcolor（荧光笔背景色）。"""
    seg = Segment(SegType.HIGHLIGHT, "==高亮==", "高亮", marks=(SegType.HIGHLIGHT,))
    style = segment_style(seg, base_size=16)
    assert style.bgcolor is not None
    assert style.bgcolor != ""


def test_highlight_bgcolor_light_theme_typora_style():
    """亮色主题高亮背景为柔和荧光笔黄（Typora 式，非刺眼饱和黄）。"""
    # 优化后从 #FFF3BF 改为 #FBF2A8（柔和荧光笔效果）
    assert _LIGHT.highlight_bg == "#FBF2A8"


def test_highlight_bgcolor_dark_theme_typora_style():
    """暗色主题高亮背景为暖琥珀色（Typora 式，鲜明可辨）。"""
    # 优化后从 #4D3E00 改为 #5D4E1A（与 search_match_bg 一致更鲜明）
    assert _DARK.highlight_bg == "#5D4E1A"


def test_highlight_combined_style_has_bgcolor():
    """组合格式（加粗+高亮）渲染样式仍含 bgcolor。"""
    import flet as ft
    seg = Segment(
        SegType.HIGHLIGHT, "**==加粗高亮==**", "加粗高亮",
        marks=(SegType.STRONG, SegType.HIGHLIGHT),
    )
    style = segment_style(seg, base_size=16)
    assert style.bgcolor is not None
    assert style.weight == ft.FontWeight.BOLD


# ================ 显示文本与标记拆分 ================

def test_highlight_display_text():
    """渲染态 display_text 返回纯内容（不含 == 标记）。"""
    seg = Segment(SegType.HIGHLIGHT, "==高亮==", "高亮", marks=(SegType.HIGHLIGHT,))
    assert display_text(seg) == "高亮"


def test_highlight_split_seg_for_display():
    """split_seg_for_display 拆分为 [标记, 内容, 标记]。"""
    seg = Segment(SegType.HIGHLIGHT, "==高亮==", "高亮", marks=(SegType.HIGHLIGHT,))
    parts = split_seg_for_display(seg)
    assert len(parts) == 3
    assert parts[0] == ("==", True)
    assert parts[1] == ("高亮", False)
    assert parts[2] == ("==", True)


def test_highlight_wrap_syntax():
    """WRAP_SYNTAX 高亮开闭标记为 ==。"""
    assert WRAP_SYNTAX[SegType.HIGHLIGHT] == ("==", "==")


# ================ 快捷键 ================

def _make_mgr() -> ShortcutManager:
    """构造 ShortcutManager（默认空设置，读取默认快捷键）。"""
    settings: dict = {}

    def update_setting(key: str, value: object) -> None:
        settings[key] = value

    return ShortcutManager(settings, update_setting)


def test_highlight_shortcut_default():
    """format_highlight 默认快捷键为 Ctrl+Shift+H（Highlight 语义清晰）。"""
    mgr = _make_mgr()
    assert mgr.shortcut("edit", "format_highlight") == "ctrl+shift+h"


def test_highlight_shortcut_not_ctrl_u():
    """高亮快捷键不得为 Ctrl+U（U 语义为下划线 Underline，避免冲突）。"""
    mgr = _make_mgr()
    assert mgr.shortcut("edit", "format_highlight") != "ctrl+u"


def test_highlight_in_inline_format_combos():
    """inline_format_combos 映射含 ctrl+shift+h → highlight。"""
    mgr = _make_mgr()
    combos = mgr.inline_format_combos()
    assert combos.get("ctrl+shift+h") == "highlight"


def test_highlight_shortcut_no_conflict():
    """高亮快捷键不与其他默认快捷键冲突。"""
    mgr = _make_mgr()
    conflicts = mgr.conflicts("edit")
    highlight_targets = [t for t in conflicts if t[2] == "format_highlight"]
    assert not highlight_targets, f"高亮快捷键冲突: {highlight_targets}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
