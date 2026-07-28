"""表格创建链路冒烟测试：验证 set_block(TABLE) 生成的 raw 结构正确。

parse_markdown 既有行为：分隔行不创建为独立 Line（被跳过），但 set_block(TABLE)
直接切片替换 document.lines（保留分隔行），table_view.py 从 document.lines 读取
时分隔行存在、对齐信息不丢。本测试验证：
1. _join_row 生成的 header/sep/data raw 格式正确（首尾为 |、单元格数匹配）
2. 分隔行 raw 被 is_table_separator 识别
3. header 含原文本内容
4. parse_markdown 对表格的既有行为（header + data，分隔行跳过）
5. join_row 支持任意列数

不依赖 UI 层（flet），仅验证 utils.table_helpers 与 parser 的数据链路。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser import parse_markdown  # noqa: E402
from utils.table_helpers import join_row, is_table_separator, split_row  # noqa: E402
from views.table_view import _align_marker  # noqa: E402


def test_join_row_format():
    """_join_row 生成的 raw 格式：首尾为 |，单元格数匹配。"""
    header = join_row(["表头1", ""])
    assert header.startswith("| ") and header.endswith(" |"), f"bad format: {header!r}"
    cells = split_row(header)
    assert cells == ["表头1", ""], f"cells={cells}"
    print("PASS test_join_row_format")


def test_separator_raw_recognized():
    """分隔行 raw 被 is_table_separator 识别（含对齐变体）。"""
    sep = join_row(["---", "---"])
    assert is_table_separator(sep), f"{sep!r} should be separator"
    # 对齐变体
    assert is_table_separator(join_row([":---:", "---:"]))
    assert is_table_separator(join_row(["---:", ":---"]))
    print("PASS test_separator_raw_recognized")


def test_header_preserves_content():
    """把有内容的行转为表格：表头第一列含原文本。"""
    content = "原有内容"
    header = join_row([content, ""])
    cells = split_row(header)
    assert cells[0] == content
    print("PASS test_header_preserves_content")


def test_three_table_lines_structure():
    """set_block(TABLE) 生成的 3 行 raw 结构：header / sep / data 各 2 列。"""
    header = join_row(["A", "B"])
    sep = join_row(["---", "---"])
    data = join_row(["", ""])
    # 每行都首尾为 |
    for raw in (header, sep, data):
        assert raw.startswith("| ") and raw.endswith(" |"), f"bad: {raw!r}"
    # 列数一致（2 列）
    assert len(split_row(header)) == 2
    assert len(split_row(sep)) == 2
    assert len(split_row(data)) == 2
    # 分隔行识别
    assert is_table_separator(sep)
    print("PASS test_three_table_lines_structure")


def test_parse_markdown_table_behavior():
    """parse_markdown 保留分隔行：header + sep + data 解析为 TABLE（3 行）。

    此前实现跳过分隔行（只 header + data = 2 行），导致：
      1. set_align 找不到分隔行，把 :---: 写到数据行
      2. 已有对齐信息丢失（打开 | :---: | 文件后渲染为 left）
    修复后保留分隔行，对齐信息持久化到 document.lines。
    """
    header = join_row(["A", "B"])
    sep = join_row(["---", "---"])
    data = join_row(["1", "2"])
    text = f"{header}\n{sep}\n{data}\n"
    doc = parse_markdown(text)
    table_lines = [l for l in doc.lines if l.block_type.name == "TABLE"]
    # 修复后：header + sep + data = 3 行（分隔行保留）
    assert len(table_lines) == 3, f"expected 3 (header+sep+data), got {len(table_lines)}"
    # header 第一行含 A B
    assert split_row(table_lines[0].raw) == ["A", "B"]
    # 第二行是分隔行（保留对齐信息）
    assert is_table_separator(table_lines[1].raw), f"line 1 not sep: {table_lines[1].raw!r}"
    # 第三行是数据行
    assert split_row(table_lines[2].raw) == ["1", "2"]
    print("PASS test_parse_markdown_table_behavior")


def test_parse_markdown_preserves_alignment():
    """parse_markdown 保留对齐标记：| :---: | ---: | 解析后仍存在。

    回归 BUG：此前跳过分隔行，打开含对齐信息的表格后所有列变 left。
    """
    header = join_row(["A", "B"])
    sep = join_row([":---:", "---:"])  # 居中 + 右对齐
    data = join_row(["1", "2"])
    text = f"{header}\n{sep}\n{data}\n"
    doc = parse_markdown(text)
    table_lines = [l for l in doc.lines if l.block_type.name == "TABLE"]
    assert len(table_lines) == 3, f"expected 3 lines, got {len(table_lines)}"
    # 分隔行保留原始对齐标记
    sep_cells = split_row(table_lines[1].raw)
    assert sep_cells == [":---:", "---:"], f"align lost: {sep_cells}"
    # 序列化往返：serialize 应还原原始 markdown（含分隔行）
    from parser import serialize
    out = serialize(doc).strip()
    assert ":---:" in out and "---:" in out, f"align lost in serialize: {out!r}"
    print("PASS test_parse_markdown_preserves_alignment")


def test_multi_column_join():
    """join_row 支持任意列数（3 列表格）。"""
    row = join_row(["X", "Y", "Z"])
    assert split_row(row) == ["X", "Y", "Z"]
    print("PASS test_multi_column_join")


def test_set_align_writes_marker_not_semantic_string():
    """set_align 应写入 Markdown 对齐标记，而非语义字符串。

    回归 BUG：on_table_op('set_align', {'align': 'center'}) 直接把 'center'
    写入分隔行单元格，导致该单元格不匹配 _ALIGN_RE，下次解析时该行被当作
    数据行，表现为"在下方单元格写入了 center 字符"且对齐失效。
    正确行为：语义 'left'/'center'/'right' 应转为 '---'/':---:'/'---:'。
    """
    assert _align_marker("left") == "---"
    assert _align_marker("center") == ":---:"
    assert _align_marker("right") == "---:"
    # 写入分隔行后仍能被识别为分隔行（含对齐变体）
    sep = join_row([_align_marker("center"), _align_marker("right")])
    assert is_table_separator(sep), f"sep not recognized: {sep!r}"
    # 反例：直接写语义字符串（BUG 现象），不应被识别为分隔行
    bad_sep = join_row(["center", "right"])
    assert not is_table_separator(bad_sep), f"semantic str wrongly recognized: {bad_sep!r}"
    print("PASS test_set_align_writes_marker_not_semantic_string")


if __name__ == "__main__":
    test_join_row_format()
    test_separator_raw_recognized()
    test_header_preserves_content()
    test_three_table_lines_structure()
    test_parse_markdown_table_behavior()
    test_parse_markdown_preserves_alignment()
    test_multi_column_join()
    test_set_align_writes_marker_not_semantic_string()
    print("\n所有表格创建冒烟测试通过 ✅")
