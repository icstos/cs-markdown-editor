"""parser.selection.extract_outward_text 纯函数单元测试。

覆盖向外选区文本提取（cut/copy 共用）：单行/多行/边界无效/空选区。
偏移约定为行级 raw 偏移（与 editor 光标/选区偏移一致）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import BlockType, Line
from parser.selection import extract_outward_text


def _make_doc(*raws: str) -> list[Line]:
    """从多行 raw 构造 Line 列表。"""
    return [Line(BlockType.PARAGRAPH, r) for r in raws]


# ---------------- 单行选区 ----------------
def test_extract_single_line_partial():
    """单行部分选区：raw[a_off:b_off]。"""
    lines = _make_doc("hello world")
    assert extract_outward_text(lines, 0, 2, 0, 7) == "llo w"


def test_extract_single_line_full():
    """单行完整选区：整行 raw。"""
    lines = _make_doc("hello world")
    assert extract_outward_text(lines, 0, 0, 0, 11) == "hello world"


def test_extract_single_line_empty():
    """单行空选区（a_off == b_off）：空串。"""
    lines = _make_doc("hello world")
    assert extract_outward_text(lines, 0, 3, 0, 3) == ""


# ---------------- 多行选区 ----------------
def test_extract_multi_line_adjacent():
    """相邻行选区：首行尾部 + 尾行头部，换行符拼接。"""
    lines = _make_doc("first", "second")
    assert extract_outward_text(lines, 0, 2, 1, 3) == "rst\nsec"


def test_extract_multi_line_with_middle():
    """跨 3 行选区：首行尾 + 中间整行 + 尾行头。"""
    lines = _make_doc("first", "middle", "last")
    assert extract_outward_text(lines, 0, 2, 2, 2) == "rst\nmiddle\nla"


def test_extract_multi_line_full():
    """跨多行完整选区：含所有行的完整 raw。"""
    lines = _make_doc("aaa", "bbb", "ccc")
    assert extract_outward_text(lines, 0, 0, 2, 3) == "aaa\nbbb\nccc"


# ---------------- 边界无效 ----------------
def test_extract_invalid_a_li():
    """a_li 越界：返回空串。"""
    lines = _make_doc("hello")
    assert extract_outward_text(lines, 5, 0, 0, 3) == ""


def test_extract_invalid_b_li():
    """b_li 越界：返回空串。"""
    lines = _make_doc("hello")
    assert extract_outward_text(lines, 0, 0, 5, 3) == ""


def test_extract_empty_lines():
    """空行列表：返回空串。"""
    assert extract_outward_text([], 0, 0, 0, 0) == ""


# ---------------- 含语法标记 ----------------
def test_extract_with_syntax():
    """选区含 Markdown 语法标记：保留完整 raw。"""
    lines = _make_doc("**bold** text")
    assert extract_outward_text(lines, 0, 0, 0, 8) == "**bold**"


if __name__ == "__main__":
    test_extract_single_line_partial()
    test_extract_single_line_full()
    test_extract_single_line_empty()
    test_extract_multi_line_adjacent()
    test_extract_multi_line_with_middle()
    test_extract_multi_line_full()
    test_extract_invalid_a_li()
    test_extract_invalid_b_li()
    test_extract_empty_lines()
    test_extract_with_syntax()
    print("\n所有 extract_outward_text 单元测试通过 ✅")
