"""utils 层单元测试：segment_helpers / table_helpers / file_helpers。

覆盖段常量、显示文本、拆分逻辑、表格行拆拼、对齐分隔判定、文件名派生。
不依赖 UI 层。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from models import SegType, Segment  # noqa: E402
from utils.file_helpers import file_name  # noqa: E402
from utils.segment_helpers import (  # noqa: E402
    MONO_SEGTYPES,
    PREFIX_SEGTYPES,
    WRAP_SYNTAX,
    display_text,
    split_seg_for_display,
)
from utils.table_helpers import ALIGN_RE, is_table_separator, join_row, split_row  # noqa: E402


# ---------------- 常量集合 ----------------
def test_prefix_segtypes_contents():
    assert SegType.HEADING_PREFIX in PREFIX_SEGTYPES
    assert SegType.LIST_PREFIX in PREFIX_SEGTYPES
    assert SegType.QUOTE_PREFIX in PREFIX_SEGTYPES


def test_mono_segtypes_contents():
    assert SegType.CODESPAN in MONO_SEGTYPES
    assert SegType.CODE in MONO_SEGTYPES
    assert SegType.INLINE_MATH in MONO_SEGTYPES
    assert SegType.MATH in MONO_SEGTYPES


def test_wrap_syntax_pairs():
    assert WRAP_SYNTAX[SegType.STRONG] == ("**", "**")
    assert WRAP_SYNTAX[SegType.EMPHASIS] == ("*", "*")
    assert WRAP_SYNTAX[SegType.CODESPAN] == ("`", "`")
    assert WRAP_SYNTAX[SegType.HIGHLIGHT] == ("==", "==")
    assert WRAP_SYNTAX[SegType.INLINE_MATH] == ("$", "$")


# ---------------- display_text ----------------
def test_display_text_heading_prefix_empty():
    seg = Segment(SegType.HEADING_PREFIX, "# ", "# ")
    assert display_text(seg) == ""


def test_display_text_quote_prefix_empty():
    seg = Segment(SegType.QUOTE_PREFIX, "> ", "> ")
    assert display_text(seg) == ""


def test_display_text_uo_list_marker():
    seg = Segment(SegType.LIST_PREFIX, "- ", "- ")
    assert display_text(seg) == "•  "


def test_display_text_ordered_list_marker():
    seg = Segment(SegType.LIST_PREFIX, "1. ", "1. ")
    assert display_text(seg) == "1. "


def test_display_text_task_marker_empty():
    seg = Segment(SegType.LIST_PREFIX, "- [ ] ", "- [ ] ")
    assert display_text(seg) == ""


def test_display_text_task_checked_marker_empty():
    seg = Segment(SegType.LIST_PREFIX, "- [x] ", "- [x] ")
    assert display_text(seg) == ""


def test_display_text_image_with_alt():
    seg = Segment(SegType.IMAGE, "![alt](url)", "alt", url="url")
    assert display_text(seg) == "alt"


def test_display_text_image_no_alt():
    seg = Segment(SegType.IMAGE, "![](url)", "", url="url")
    assert display_text(seg) == "🖼"


def test_display_text_link_with_text():
    seg = Segment(SegType.LINK, "[txt](url)", "txt", url="url")
    assert display_text(seg) == "txt"


def test_display_text_link_no_text_fallback_url():
    seg = Segment(SegType.LINK, "[](url)", "", url="url")
    assert display_text(seg) == "url"


def test_display_text_plain():
    seg = Segment(SegType.TEXT, "hello", "hello")
    assert display_text(seg) == "hello"


# ---------------- split_seg_for_display ----------------
def test_split_prefix_seg():
    seg = Segment(SegType.HEADING_PREFIX, "# ", "# ")
    assert split_seg_for_display(seg) == [("# ", True)]


def test_split_empty_seg():
    seg = Segment(SegType.TEXT, "", "")
    assert split_seg_for_display(seg) == []


def test_split_codespan():
    seg = Segment(SegType.CODESPAN, "`code`", "code")
    assert split_seg_for_display(seg) == [("`", True), ("code", False), ("`", True)]


def test_split_inline_math():
    seg = Segment(SegType.INLINE_MATH, "$x$", "x")
    assert split_seg_for_display(seg) == [("$", True), ("x", False), ("$", True)]


def test_split_strong():
    seg = Segment(SegType.STRONG, "**b**", "b")
    assert split_seg_for_display(seg) == [("**", True), ("b", False), ("**", True)]


def test_split_link():
    seg = Segment(SegType.LINK, "[txt](url)", "txt", url="url")
    parts = split_seg_for_display(seg)
    assert parts == [("[", True), ("txt", False), ("](", True), ("url", True), (")", True)]


def test_split_image():
    seg = Segment(SegType.IMAGE, "![alt](url)", "alt", url="url")
    parts = split_seg_for_display(seg)
    assert parts == [("![", True), ("alt", False), ("](", True), ("url", True), (")", True)]


def test_split_combined_marks():
    """组合标记 (EMPHASIS, STRONG) → ***text***。"""
    seg = Segment(SegType.TEXT, "***bi***", "bi", marks=(SegType.EMPHASIS, SegType.STRONG))
    parts = split_seg_for_display(seg)
    assert parts[0] == ("***", True)
    assert parts[1] == ("bi", False)
    assert parts[2] == ("***", True)


def test_split_no_marks_plain_text():
    seg = Segment(SegType.TEXT, "hello", "hello")
    assert split_seg_for_display(seg) == [("hello", False)]


# ---------------- table_helpers ----------------
def test_align_re_matches():
    assert ALIGN_RE.fullmatch("---")
    assert ALIGN_RE.fullmatch(":---")
    assert ALIGN_RE.fullmatch("---:")
    assert ALIGN_RE.fullmatch(":---:")
    assert ALIGN_RE.fullmatch("----")


def test_align_re_non_match():
    assert not ALIGN_RE.fullmatch("--")  # 太短
    assert not ALIGN_RE.fullmatch(":a-:")
    assert not ALIGN_RE.fullmatch("abc")


def test_split_row_basic():
    assert split_row("| a | b |") == ["a", "b"]


def test_split_row_tight():
    assert split_row("|a|b|") == ["a", "b"]


def test_split_row_cjk():
    assert split_row("| 居左 | 居中 |") == ["居左", "居中"]


def test_split_row_empty_cells():
    assert split_row("|  |  |") == ["", ""]


def test_split_row_single():
    assert split_row("| solo |") == ["solo"]


def test_join_row_basic():
    assert join_row(["a", "b"]) == "| a | b |"


def test_join_row_single():
    assert join_row(["solo"]) == "| solo |"


def test_join_split_roundtrip():
    cells = ["a", "b", "c"]
    assert split_row(join_row(cells)) == cells


def test_is_table_separator_true():
    assert is_table_separator("| --- | --- |")
    assert is_table_separator("| :--- | ---: | :---: |")


def test_is_table_separator_false_data_row():
    assert not is_table_separator("| a | b |")


def test_is_table_separator_false_single_cell():
    assert not is_table_separator("| --- |")  # len(cells) < 2


# ---------------- file_helpers ----------------
def test_file_name_with_path():
    assert file_name("/home/user/note.md") == "note.md"


def test_file_name_none():
    assert file_name(None) == "未命名.md"


def test_file_name_empty():
    assert file_name("") == "未命名.md"


def test_file_name_windows_path():
    assert file_name(r"C:\Users\a\b.md") == "b.md"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
