"""表格单元格行内 markdown 渲染测试。

验证 _render_cell_spans 把单元格文本解析为带样式 TextSpan：
- 纯文本 → 单 TEXT span
- **bold** / *italic* / `code` / ~~strike~~ / ==hl== → 对应样式 span，标记折叠
- [link](url) → LINK span，保留 on_click
- 混合内容 → 多 span
- 空单元格 → 不换行空格 span
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flet as ft  # noqa: E402

from views.table_view import _render_cell_spans  # noqa: E402


def _span_text(spans: list[ft.TextSpan]) -> str:
    """拼接所有 span 的 text，得到单元格可见文本。"""
    return "".join(s.text for s in spans)


def test_plain_text_single_span():
    """纯文本 → 单个 span，文本完整保留。"""
    spans = _render_cell_spans("hello world", base_size=14)
    assert len(spans) == 1
    assert spans[0].text == "hello world"
    assert _span_text(spans) == "hello world"


def test_bold_rendered():
    """**bold** → 标记折叠，显示 "bold"，weight=BOLD。"""
    spans = _render_cell_spans("**bold**", base_size=14)
    assert _span_text(spans) == "bold"
    bold_span = next(s for s in spans if s.text == "bold")
    assert bold_span.style.weight == ft.FontWeight.BOLD


def test_italic_rendered():
    """*italic* → 显示 "italic"，italic=True。"""
    spans = _render_cell_spans("*italic*", base_size=14)
    assert _span_text(spans) == "italic"
    it_span = next(s for s in spans if s.text == "italic")
    assert it_span.style.italic is True


def test_inline_code_rendered():
    """`code` → 显示 "code"，font_family=MONO。"""
    spans = _render_cell_spans("`code`", base_size=14)
    assert _span_text(spans) == "code"
    code_span = next(s for s in spans if s.text == "code")
    assert code_span.style.font_family is not None


def test_strikethrough_rendered():
    """~~strike~~ → 显示 "strike"，decoration=LINE_THROUGH。"""
    spans = _render_cell_spans("~~strike~~", base_size=14)
    assert _span_text(spans) == "strike"
    strike_span = next(s for s in spans if s.text == "strike")
    assert strike_span.style.decoration == ft.TextDecoration.LINE_THROUGH


def test_link_rendered():
    """[text](url) → 显示 "text"，color=link，绑定 on_click。"""
    spans = _render_cell_spans("[label](https://example.com)", base_size=14)
    assert _span_text(spans) == "label"
    link_span = next(s for s in spans if s.text == "label")
    assert link_span.on_click is not None


def test_mixed_content_multiple_spans():
    """混合内容 a **b** c → 多个 span，可见文本拼接完整。"""
    spans = _render_cell_spans("a **b** c", base_size=14)
    assert len(spans) >= 3
    assert _span_text(spans) == "a b c"
    bold_span = next(s for s in spans if s.text == "b")
    assert bold_span.style.weight == ft.FontWeight.BOLD


def test_empty_cell_returns_nbsp_span():
    """空单元格 → 单个不换行空格 span，确保可点击宽度。"""
    spans = _render_cell_spans("", base_size=14)
    assert len(spans) == 1
    assert spans[0].text == "\u00A0"


def test_whitespace_only_cell_returns_nbsp_span():
    """仅空白字符的单元格 → 不换行空格 span。"""
    spans = _render_cell_spans("   ", base_size=14)
    assert len(spans) == 1
    assert spans[0].text == "\u00A0"


def test_base_size_applied():
    """base_size 传递到 span style。"""
    spans = _render_cell_spans("text", base_size=16)
    assert spans[0].style.size == 16


def test_nested_bold_italic():
    """***bold italic*** → 加粗+斜体组合样式。"""
    spans = _render_cell_spans("***bold italic***", base_size=14)
    assert _span_text(spans) == "bold italic"
    span = next(s for s in spans if s.text == "bold italic")
    assert span.style.weight == ft.FontWeight.BOLD
    assert span.style.italic is True


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
