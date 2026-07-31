"""HR（分隔线）渲染测试。

验证 Typora 式 WYSIWYG 渲染规则：
- 激活态：---/***/___ 整体显示为 muted 灰色（语法标记淡化，
  与 # - > 前缀灰色设计语言一致），光标在段内/段外均如此
- 文本内容完整保留（--- / *** / ___ 不被折叠）
- 普通段落激活态保持 c.text（确保 HR 灰色不误伤其他块类型）

依赖项：parser、views.segment_view、styles。
不依赖 UI 层（_current_colors 在非渲染上下文回退亮色 _LIGHT）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flet as ft  # noqa: E402

from parser import parse_markdown  # noqa: E402
from styles import _current_colors  # noqa: E402
from views.segment_view import raw_to_visible_spans  # noqa: E402


def _span_text(spans: list[ft.TextSpan]) -> str:
    return "".join(s.text for s in spans)


def test_hr_active_marker_is_muted():
    """HR 激活态 --- 显示为 muted 灰色（语法标记淡化，Typora 式）。"""
    c = _current_colors()
    line = parse_markdown("---").lines[0]
    spans = raw_to_visible_spans(line, 16, cursor_raw_offset=0)
    assert _span_text(spans) == "---"
    assert all(s.style.color == c.muted for s in spans), (
        f"HR --- 应全部 muted 灰色，实际 {[s.style.color for s in spans]}"
    )


def test_hr_active_marker_variants_muted():
    """HR 标记 *** / ___ 激活态同样显示为 muted，且文本保留原标记。"""
    c = _current_colors()
    for raw in ("***", "___"):
        line = parse_markdown(raw).lines[0]
        spans = raw_to_visible_spans(line, 16, cursor_raw_offset=len(raw))
        assert _span_text(spans) == raw
        assert all(s.style.color == c.muted for s in spans), (
            f"HR {raw} 应全部 muted 灰色，实际 {[s.style.color for s in spans]}"
        )


def test_hr_cursor_at_end_still_muted():
    """光标在 --- 末尾（off=3）时仍为 muted（is_last 段右端点含光标）。"""
    c = _current_colors()
    line = parse_markdown("---").lines[0]
    spans = raw_to_visible_spans(line, 16, cursor_raw_offset=3)
    assert _span_text(spans) == "---"
    assert all(s.style.color == c.muted for s in spans)


def test_hr_browse_mode_no_cursor_muted():
    """HR 无光标（cursor_raw_offset=None）时仍为 muted（base_style 覆盖在段基础样式阶段）。"""
    c = _current_colors()
    line = parse_markdown("---").lines[0]
    spans = raw_to_visible_spans(line, 16, cursor_raw_offset=None)
    assert _span_text(spans) == "---"
    assert all(s.style.color == c.muted for s in spans)


def test_paragraph_active_not_muted():
    """普通段落激活态保持 c.text（确保 HR 灰色不误伤其他块类型）。"""
    c = _current_colors()
    line = parse_markdown("hello").lines[0]
    spans = raw_to_visible_spans(line, 16, cursor_raw_offset=0)
    assert _span_text(spans) == "hello"
    assert all(s.style.color == c.text for s in spans), (
        f"普通段落应 c.text，实际 {[s.style.color for s in spans]}"
    )


def test_hr_marker_not_bold():
    """HR 标记字重为正常（非加粗，区别于标题前缀的 BOLD）。

    segment_style(TEXT) 不设 weight（None=Flet 默认正常字重），
    而 prefix_style 设 weight=BOLD。HR 走 TEXT 路径应为非加粗。
    """
    line = parse_markdown("---").lines[0]
    spans = raw_to_visible_spans(line, 16, cursor_raw_offset=0)
    assert all(s.style.weight != ft.FontWeight.BOLD for s in spans), (
        f"HR 标记应非加粗，实际 {[s.style.weight for s in spans]}"
    )


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\n所有 HR 渲染测试通过 ✅ ({len(tests)} 项)")
