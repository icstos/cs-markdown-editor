"""包裹段末尾光标位置测试（Bug 修复）。

验证光标在包裹格式段（==高亮==/**加粗**/`code` 等）末尾时，标记正确展开
（占宽度），pixel_layout 与 segment_view 行为一致。

Bug 根因：非末段的 cursor_in_seg 判定用 `< seg_end`（严格小于），导致光标
在段末尾（offset == seg_end）时 cursor_in_seg=False，标记被折叠（零宽度），
光标 X 坐标与可见标记不对齐。修复：非末段也用 `<=`（与末段一致）。

raw 偏移示例（'前文==高亮==后文'）：
    offset 0: 前, 1: 文, 2: =, 3: =, 4: 高, 5: 亮, 6: =, 7: =, 8: 后, 9: 文, 10: (end)
    highlight 段 seg_start=2, seg_end=8
    开始 == 在 offset 2,3；结束 == 在 offset 6,7
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from parser import parse_markdown  # noqa: E402
from views.pixel_layout import _line_raw_offsets_x  # noqa: E402
from views.segment_view import raw_to_visible_spans  # noqa: E402


def _widths(offsets: list[float]) -> list[float]:
    """相邻 offset 差值 = 每个字符的宽度。"""
    return [round(offsets[i + 1] - offsets[i], 1) for i in range(len(offsets) - 1)]


def _char_widths(line, base: int, cursor_off: int) -> list[float]:
    """返回每个 raw 字符的宽度列表。"""
    offsets = _line_raw_offsets_x(line, base=base, cursor_raw_offset=cursor_off)
    return _widths(offsets)


# ================ 高亮段末尾 ================

def test_highlight_seg_end_marker_visible():
    """高亮段末尾（==高亮==|后文）：结束 == 标记展开占宽（非零）。

    raw = '前文==高亮==后文'，highlight 段 seg_start=2, seg_end=8。
    光标在 seg_end=8（后文之前）时，结束 == 在 offset 6,7 应展开占宽。

    Bug：修复前 cursor_in_seg 用 < seg_end，光标在 offset=8 时
    cursor_in_seg=False，结束 == 标记宽度为 0（折叠），光标位置与可见标记不对齐。
    """
    doc = parse_markdown("前文==高亮==后文")
    line = doc.lines[0]
    widths = _char_widths(line, base=16, cursor_off=8)
    # 结束 == 在 offset 6,7（raw[6]='=', raw[7]='='）
    assert widths[6] > 0, "结束 == 第一个字符（offset 6）宽度不应为零（标记应展开）"
    assert widths[7] > 0, "结束 == 第二个字符（offset 7）宽度不应为零（标记应展开）"


def test_highlight_seg_end_spans_contain_marker():
    """高亮段末尾光标：spans 文本含 == 标记（标记可见）。"""
    doc = parse_markdown("前文==高亮==后文")
    line = doc.lines[0]
    spans = raw_to_visible_spans(line, base_size=16, cursor_raw_offset=8)
    text = "".join(s.text for s in spans)
    assert "==高亮==" in text, f"段末尾光标应显示标记，实际: {text!r}"


def test_highlight_seg_middle_unchanged():
    """高亮段中间光标（高|亮）：标记仍展开（回归测试，确保修复不破坏中间场景）。"""
    doc = parse_markdown("前文==高亮==后文")
    line = doc.lines[0]
    widths = _char_widths(line, base=16, cursor_off=6)
    # 段内光标：开始 == 在 offset 2,3 展开
    assert widths[2] > 0, "开始 == 第一个字符宽度不应为零"
    assert widths[3] > 0, "开始 == 第二个字符宽度不应为零"


# ================ 加粗段末尾（回归测试，同一 Bug） ================

def test_bold_seg_end_marker_visible():
    """加粗段末尾（**加粗**|后文）：结束 ** 标记展开占宽。

    raw = '前文**加粗**后文'，strong 段 seg_start=2, seg_end=8。
    结束 ** 在 offset 6,7。
    """
    doc = parse_markdown("前文**加粗**后文")
    line = doc.lines[0]
    widths = _char_widths(line, base=16, cursor_off=8)
    assert widths[6] > 0, "结束 ** 第一个字符宽度不应为零"
    assert widths[7] > 0, "结束 ** 第二个字符宽度不应为零"


def test_bold_seg_end_spans_contain_marker():
    """加粗段末尾光标：spans 文本含 ** 标记。"""
    doc = parse_markdown("前文**加粗**后文")
    line = doc.lines[0]
    spans = raw_to_visible_spans(line, base_size=16, cursor_raw_offset=8)
    text = "".join(s.text for s in spans)
    assert "**加粗**" in text, f"段末尾光标应显示标记，实际: {text!r}"


# ================ 行内代码段末尾 ================

def test_codespan_seg_end_marker_visible():
    """行内代码段末尾（`code`|后文）：结束 ` 标记展开占宽。

    raw = '前文`code`后文'，codespan 段 seg_start=2, seg_end=8。
    结束 ` 在 offset 7。
    """
    doc = parse_markdown("前文`code`后文")
    line = doc.lines[0]
    widths = _char_widths(line, base=16, cursor_off=8)
    assert widths[7] > 0, "结束 ` 宽度不应为零"


# ================ 相邻 TEXT 段光标：包裹段标记折叠 ================

def test_adjacent_text_seg_highlight_marker_folded():
    """光标在后文段（前文==高亮==|后文）：highlight 段标记折叠（零宽度）。

    光标在 offset=10（后文末尾）时不在 highlight 段内，标记应折叠。
    """
    doc = parse_markdown("前文==高亮==后文")
    line = doc.lines[0]
    widths = _char_widths(line, base=16, cursor_off=10)
    # 开始 == 在 offset 2,3，结束 == 在 offset 6,7，应折叠为 0
    assert widths[2] == 0, "光标不在段内时开始 == 应折叠"
    assert widths[3] == 0
    assert widths[6] == 0, "光标不在段内时结束 == 应折叠"
    assert widths[7] == 0


def test_adjacent_text_seg_bold_marker_folded():
    """光标在后文段（前文**加粗**|后文）：strong 段标记折叠。"""
    doc = parse_markdown("前文**加粗**后文")
    line = doc.lines[0]
    widths = _char_widths(line, base=16, cursor_off=10)
    assert widths[2] == 0
    assert widths[3] == 0
    assert widths[6] == 0
    assert widths[7] == 0


# ================ pixel_layout 与 segment_view 一致性 ================

def test_pixel_layout_and_segment_view_consistent_at_seg_end():
    """段末尾光标：pixel_layout 与 segment_view 标记可见性一致。"""
    doc = parse_markdown("前文==高亮==后文")
    line = doc.lines[0]
    # raw 字符位置：0前 1文 2= 3= 4高 5亮 6= 7= 8后 9文
    marker_offsets = (2, 3, 6, 7)  # == 标记的 offset
    for off in [2, 4, 6, 8, 10]:
        spans = raw_to_visible_spans(line, base_size=16, cursor_raw_offset=off)
        offsets = _line_raw_offsets_x(line, base=16, cursor_raw_offset=off)
        widths = _widths(offsets)
        span_text = "".join(s.text for s in spans)
        markers_visible = "==" in span_text
        if markers_visible:
            # 标记可见时，标记 offset 宽度非零
            for mo in marker_offsets:
                assert widths[mo] > 0, f"off={off}: 标记可见但 offset {mo} 宽度为零"
        else:
            # 标记折叠时，标记 offset 宽度为零
            for mo in marker_offsets:
                assert widths[mo] == 0, f"off={off}: 标记折叠但 offset {mo} 宽度非零"


# ================ 末段（无相邻 TEXT） ================

def test_last_seg_highlight_end_marker_visible():
    """末段高亮（前文==高亮==）：段末尾光标标记展开。

    raw = '前文==高亮=='，highlight 是末段 seg_start=2, seg_end=8。
    光标在 seg_end=8（行尾）时，结束 == 在 offset 6,7 应展开。
    """
    doc = parse_markdown("前文==高亮==")
    line = doc.lines[0]
    widths = _char_widths(line, base=16, cursor_off=8)
    assert widths[6] > 0, "末段结束 == 第一个字符应展开"
    assert widths[7] > 0, "末段结束 == 第二个字符应展开"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
