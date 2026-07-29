"""选区操作四函数测试：compute_markdown_from_selections / match_text_to_selections /
apply_inline_format_to_selections / delete_selections。

偏移约定：selections 的 offset 相对于该行 ft.Text 显示文本
（所有段 display_text 拼接，前缀段透明）。

这是选区复制/剪切/删除/格式化链路的回归安全网。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser import (  # noqa: E402
    apply_inline_format_to_selections,
    compute_markdown_from_selections,
    compute_markdown_from_text,
    delete_selections,
    match_text_to_selections,
    parse_markdown,
    serialize,
)
from utils.segment_helpers import display_text  # noqa: E402


def _line_display(line) -> str:
    """行的显示文本拼接（与选区偏移口径一致）。"""
    return "".join(display_text(s) for s in line.segments)


# ---------------------------------------------------------------------------
# compute_markdown_from_selections
# ---------------------------------------------------------------------------
def test_compute_selection_full_text():
    """整段文本全选 → 返回纯文本。"""
    doc = parse_markdown("普通文本")
    # display = "普通文本"（4 字符）
    md = compute_markdown_from_selections(doc.lines, {0: (0, 4)})
    assert md == "普通文本", f"got {md!r}"


def test_compute_selection_partial_text():
    """部分选中文本 → 返回选中片段。"""
    doc = parse_markdown("普通文本")
    md = compute_markdown_from_selections(doc.lines, {0: (0, 2)})
    assert md == "普通", f"got {md!r}"


def test_compute_selection_full_bold_segment():
    """全选加粗段 → 返回含语法的 raw（**加粗**）。"""
    doc = parse_markdown("**加粗**")
    # display = "加粗"（2 字符）
    md = compute_markdown_from_selections(doc.lines, {0: (0, 2)})
    assert md == "**加粗**", f"got {md!r}"


def test_compute_selection_partial_bold_segment():
    """部分选中加粗段 → 用 _wrap_partial 包裹选中部分。"""
    doc = parse_markdown("**加粗**")
    md = compute_markdown_from_selections(doc.lines, {0: (0, 1)})
    assert md == "**加**", f"got {md!r}"


def test_compute_selection_heading_content():
    """标题行选内容：前缀段透明，偏移从内容开始。"""
    doc = parse_markdown("# 标题")
    # display = "" + "标题" = "标题"（2 字符）
    md = compute_markdown_from_selections(doc.lines, {0: (0, 2)})
    assert md == "标题", f"got {md!r}"


def test_compute_selection_multiline():
    """多行选区 → 用换行符连接。"""
    doc = parse_markdown("aaa\nbbb")
    md = compute_markdown_from_selections(doc.lines, {0: (0, 3), 1: (0, 3)})
    assert md == "aaa\nbbb", f"got {md!r}"


def test_compute_selection_empty():
    """空选区 → 返回空串。"""
    doc = parse_markdown("文本")
    assert compute_markdown_from_selections(doc.lines, {}) == ""
    assert compute_markdown_from_selections(doc.lines, {0: (2, 2)}) == ""


def test_compute_selection_reversed_offsets():
    """base/extent 顺序无关（内部 min/max）。"""
    doc = parse_markdown("普通文本")
    md = compute_markdown_from_selections(doc.lines, {0: (4, 0)})
    assert md == "普通文本"


# ---------------------------------------------------------------------------
# match_text_to_selections
# ---------------------------------------------------------------------------
def test_match_text_single_line():
    doc = parse_markdown("aaa\nbbb")
    sel = match_text_to_selections(doc.lines, "aaa")
    assert sel == {0: (0, 3)}, f"got {sel!r}"


def test_match_text_multiline_with_newline():
    """剪贴板含换行 → 按行逐段匹配。"""
    doc = parse_markdown("aaa\nbbb")
    sel = match_text_to_selections(doc.lines, "bbb\naaa")
    # clip="bbb" → li=1 (0,3); clip="aaa" → li=2 超出
    assert sel == {1: (0, 3)}, f"got {sel!r}"


def test_match_text_cross_line_concat():
    """无换行跨行文本 → 在拼接中查找再映射回各行。"""
    doc = parse_markdown("aaa\nbbb")
    sel = match_text_to_selections(doc.lines, "aaabbb")
    assert sel == {0: (0, 3), 1: (0, 3)}, f"got {sel!r}"


def test_match_text_not_found():
    doc = parse_markdown("aaa")
    assert match_text_to_selections(doc.lines, "zzz") == {}


def test_match_text_empty():
    doc = parse_markdown("aaa")
    assert match_text_to_selections(doc.lines, "") == {}


def test_compute_markdown_from_text_delegates():
    """compute_markdown_from_text = match → compute 链路。"""
    doc = parse_markdown("**加粗**")
    md = compute_markdown_from_text(doc.lines, "加粗")
    assert md == "**加粗**", f"got {md!r}"


# ---------------------------------------------------------------------------
# apply_inline_format_to_selections
# ---------------------------------------------------------------------------
def test_apply_format_wrap_bold_single_line():
    """单行包裹加粗：选中文本变 **文本**。"""
    doc = parse_markdown("文本")
    new_lines, li, si, off = apply_inline_format_to_selections(
        doc.lines, {0: (0, 2)}, "**", "wrap"
    )
    assert new_lines[0].raw == "**文本**", f"raw={new_lines[0].raw!r}"
    assert li == 0
    # 光标 raw 偏移 = len(wrap) = 2（包裹前缀之后）
    assert off == 2, f"off={off}"


def test_apply_format_link():
    """链接格式：选区变 [text](url)。"""
    doc = parse_markdown("文本")
    new_lines, li, si, off = apply_inline_format_to_selections(
        doc.lines, {0: (0, 2)}, "", "link"
    )
    assert new_lines[0].raw == "[文本](url)", f"raw={new_lines[0].raw!r}"
    # link 光标偏移 = 1（[ 之后）
    assert off == 1, f"off={off}"


def test_apply_format_partial_bold():
    """部分加粗：未选中部分保留。"""
    doc = parse_markdown("abcdef")
    new_lines, _, _, _ = apply_inline_format_to_selections(
        doc.lines, {0: (2, 4)}, "**", "wrap"
    )
    assert new_lines[0].raw == "ab**cd**ef", f"raw={new_lines[0].raw!r}"


def test_apply_format_empty_selection():
    """空选区 → 原样返回。"""
    doc = parse_markdown("文本")
    new_lines, li, si, off = apply_inline_format_to_selections(
        doc.lines, {}, "**", "wrap"
    )
    assert li == 0 and si == 0 and off == 0


def test_apply_format_multiline():
    """多行选区：各自行分别格式化。"""
    doc = parse_markdown("aaa\nbbb")
    new_lines, _, _, _ = apply_inline_format_to_selections(
        doc.lines, {0: (0, 3), 1: (0, 3)}, "**", "wrap"
    )
    assert new_lines[0].raw == "**aaa**", f"raw0={new_lines[0].raw!r}"
    assert new_lines[1].raw == "**bbb**", f"raw1={new_lines[1].raw!r}"


# ---------------------------------------------------------------------------
# delete_selections
# ---------------------------------------------------------------------------
def test_delete_single_line_partial():
    """单行部分删除：删除选中片段，保留其余。

    注意：delete_selections 单行部分删除分支只更新 line.segments，不回写
    line.raw（与跨行分支调用 reparse_line 不同）。这是当前实际行为——
    调用方（editor）负责后续同步 raw。本测试验证 segments 拼接结果正确。
    """
    from parser import segment_raw

    doc = parse_markdown("abcdef")
    new_lines, li, si, off = delete_selections(doc.lines, {0: (2, 4)})
    assert segment_raw(new_lines[0].segments) == "abef", (
        f"segments={segment_raw(new_lines[0].segments)!r}"
    )
    assert li == 0


def test_delete_single_line_full():
    """单行整行删除（整行选中）。"""
    doc = parse_markdown("aaa\nbbb")
    new_lines, li, _, _ = delete_selections(doc.lines, {0: (0, 3)})
    assert len(new_lines) == 1, f"expected 1 line, got {len(new_lines)}"
    assert new_lines[0].raw == "bbb", f"raw={new_lines[0].raw!r}"


def test_delete_multiple_full_lines():
    """多行整行删除。"""
    doc = parse_markdown("aaa\nbbb\nccc")
    new_lines, _, _, _ = delete_selections(doc.lines, {0: (0, 3), 1: (0, 3)})
    assert len(new_lines) == 1
    assert new_lines[0].raw == "ccc"


def test_delete_cross_line_partial():
    """跨行部分删除：首行头部 + 尾行尾部合并为一行。"""
    doc = parse_markdown("abcdef\nghijkl")
    # 删除第一行 cd 之后 + 第二行 gh 之前 → 合并 "ab" + "ijkl"
    new_lines, li, _, _ = delete_selections(doc.lines, {0: (2, 6), 1: (0, 2)})
    assert len(new_lines) == 1, f"expected 1 line, got {len(new_lines)}"
    assert new_lines[0].raw == "abijkl", f"raw={new_lines[0].raw!r}"
    assert li == 0


def test_delete_empty_selection():
    """空选区 → 原样返回。"""
    doc = parse_markdown("文本")
    new_lines, li, si, off = delete_selections(doc.lines, {})
    assert li == 0 and si == 0 and off == 0


def test_delete_preserves_heading_prefix():
    """跨行删除时保留首行的标题前缀。"""
    doc = parse_markdown("# 标题内容\n第二行")
    # display 行0 = "标题内容"（前缀透明，6 字符）
    # 删除 "题内容" + "第二" → 保留 "# 标" + "行"
    new_lines, _, _, _ = delete_selections(doc.lines, {0: (2, 6), 1: (0, 2)})
    assert new_lines[0].block_type.name == "HEADING", f"block_type lost"
    assert "标" in new_lines[0].raw, f"raw={new_lines[0].raw!r}"
    assert "行" in new_lines[0].raw, f"raw={new_lines[0].raw!r}"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\n所有选区操作测试通过 ✅ ({len(tests)} 项)")
