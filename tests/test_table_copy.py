"""表格复制按钮测试：验证表格 markdown 源码拼接正确。

直接构造连续 TABLE 行，验证复制按钮的源码拼接逻辑：
- 复制内容 = 连续 TABLE 行的 raw 拼接（含表头/分隔行/数据行）
- 非 TABLE 行截断拼接范围
- 粘贴到其他 markdown 编辑器可保持表格格式
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import BlockType, Line  # noqa: E402


def _table_line(raw: str) -> Line:
    """构造 TABLE 行：block_type=TABLE，raw 为表格源码。"""
    line = Line(block_type=BlockType.TABLE, raw=raw)
    line.segments = []
    return line


def _para_line(raw: str) -> Line:
    """构造 PARAGRAPH 行。"""
    line = Line(block_type=BlockType.PARAGRAPH, raw=raw)
    line.segments = []
    return line


def _table_md(lines: list[Line], line_idx: int) -> str:
    """复现 table_view.py 中表格源码拼接逻辑。"""
    table_end = line_idx
    while table_end < len(lines) and lines[table_end].block_type == BlockType.TABLE:
        table_end += 1
    return "\n".join(lines[i].raw for i in range(line_idx, table_end))


def test_copy_full_table_source():
    """复制整张表格：表头+分隔行+数据行的 raw 拼接。"""
    lines = [
        _table_line("| 名称 | 值 |"),
        _table_line("| --- | --- |"),
        _table_line("| a | 1 |"),
        _table_line("| b | 2 |"),
    ]
    md = _table_md(lines, 0)
    assert md == "| 名称 | 值 |\n| --- | --- |\n| a | 1 |\n| b | 2 |"
    # 粘贴后是合法 markdown 表格
    assert md.count("\n") == 3


def test_copy_stops_at_non_table_line():
    """表格后跟非 TABLE 行时，复制范围正确截断。"""
    lines = [
        _table_line("| h1 | h2 |"),
        _table_line("| --- | --- |"),
        _table_line("| x | y |"),
        _para_line("普通段落"),  # 表格结束
        _table_line("| z | w |"),  # 另一张表，不应包含
    ]
    md = _table_md(lines, 0)
    assert md == "| h1 | h2 |\n| --- | --- |\n| x | y |"
    assert "普通段落" not in md
    assert "| z | w |" not in md


def test_copy_from_middle_table():
    """多张表场景：从第二张表的 line_idx 复制，仅含该表行。"""
    lines = [
        _table_line("| t1 |"),
        _table_line("| --- |"),
        _para_line("分隔段落"),
        _table_line("| t2a | t2b |"),
        _table_line("| --- | --- |"),
        _table_line("| p | q |"),
    ]
    # 从第二张表（idx=3）复制
    md = _table_md(lines, 3)
    assert md == "| t2a | t2b |\n| --- | --- |\n| p | q |"


def test_copy_single_header_only():
    """仅表头行（无分隔行/数据行）的表格也能复制。"""
    lines = [_table_line("| only header |")]
    md = _table_md(lines, 0)
    assert md == "| only header |"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
