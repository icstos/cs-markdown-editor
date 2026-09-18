"""代码块 Tab / Shift+Tab 缩进变换（utils/code_indent.py）测试。

背景：原生多行 TextField 不会插入制表符——Flutter 把 Tab 当焦点遍历键，Flet 1.0
没有 Focus / Shortcuts 控件、TextField 也没有 on_key_down（真机探针实测回调返回
True 也拦不住遍历）。因此缩进由代码块组件自行改写文本，本文件锁定这段纯计算的
边界语义：光标列位、跨行选区端点平移、行首无缩进时不产生空变换。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.code_indent import INDENT, apply_indent  # noqa: E402


def test_indent_unit_is_four_spaces():
    """缩进单位是 4 个空格（Markdown 代码块事实标准，避免制表位宽度歧义）。"""
    assert INDENT == "    "


# ==================== 折叠光标 ====================


def test_tab_inserts_indent_at_caret():
    assert apply_indent("ab", 1, 1, 1) == ("a    b", 5, 5)


def test_tab_on_empty_value():
    assert apply_indent("", 0, 0, 1) == ("    ", 4, 4)


def test_tab_on_empty_line_mid_text():
    """空行行首按 Tab：只在光标处插入，不影响其它行。"""
    assert apply_indent("a\n\nb", 2, 2, 1) == ("a\n    \nb", 6, 6)


def test_shift_tab_removes_four_spaces():
    assert apply_indent("    ab", 6, 6, -1) == ("ab", 2, 2)


def test_shift_tab_removes_partial_indent():
    """行首不足 4 个空格：有多少删多少，光标按删除量整体左移（相对位置不变）。"""
    assert apply_indent("  ab", 4, 4, -1) == ("ab", 2, 2)


def test_shift_tab_caret_inside_indent_lands_at_line_start():
    """光标停在缩进内部：删除缩进后落到行首，而不是留在原列号。"""
    assert apply_indent("    ab", 2, 2, -1) == ("ab", 0, 0)


def test_shift_tab_removes_leading_tab_char():
    assert apply_indent("\tab", 2, 2, -1) == ("ab", 1, 1)


def test_shift_tab_at_line_start_without_indent_is_noop():
    """行首本就无缩进：原样返回（调用方据此跳过入历史，避免空撤销条目）。"""
    value, base, extent = "ab", 0, 0
    assert apply_indent(value, base, extent, -1) == (value, base, extent)


def test_shift_tab_only_touches_caret_line():
    """反缩进只删光标所在行的缩进，前一行不动。"""
    assert apply_indent("    a\n  b", 9, 9, -1) == ("    a\nb", 7, 7)


# ==================== 跨行选区 ====================


def test_tab_indents_every_selected_line():
    assert apply_indent("a\nb", 0, 3, 1) == ("    a\n    b", 4, 11)


def test_tab_excludes_line_whose_start_equals_selection_end():
    """选区终点恰在某行行首：该行一个字符都没被选中，不参与缩进。"""
    assert apply_indent("a\nb", 0, 2, 1) == ("    a\nb", 4, 6)


def test_tab_covers_two_lines_out_of_three():
    """选区到第 2 行末尾（含换行）：只缩进前两行，第三行不动。"""
    assert apply_indent("a\nb\nc", 0, 4, 1) == ("    a\n    b\nc", 4, 12)


def test_tab_selection_endpoints_shift_with_inserted_indent():
    """选区端点按插入量平移：缩进落在选区之内（桌面编辑器通行语义）。"""
    # 选整两行 "ab\ncd"：两行各缩进一级，端点各右移 4 / 8
    assert apply_indent("ab\ncd", 0, 4, 1) == ("    ab\n    cd", 4, 12)


def test_tab_selection_ending_at_next_line_start_skips_that_line():
    """选区终点恰在下一行行首：那一行未被选中，不缩进（端点随之保持覆盖同一段文本）。"""
    assert apply_indent("ab\ncd", 1, 3, 1) == ("    ab\ncd", 5, 7)


def test_shift_tab_outdents_every_selected_line():
    assert apply_indent("    a\n    b", 4, 11, -1) == ("a\nb", 0, 3)


def test_shift_tab_outdent_with_mixed_indent_widths():
    """各行缩进宽度不同：逐行按实际宽度删除，端点累计左移。"""
    assert apply_indent("  a\n    b", 0, 9, -1) == ("a\nb", 0, 3)


def test_shift_tab_outdent_without_indent_is_noop():
    value, base, extent = "a\nb", 0, 3
    assert apply_indent(value, base, extent, -1) == (value, base, extent)


def test_selection_inside_indent_region_clamps_to_line_start():
    """选区起点落在被删缩进内部：贴到行首，不会跑到上一行去（extent 为开区间端）。"""
    # 选区 [1,5) 覆盖前 3 个空格与 "a"
    assert apply_indent("    ab", 1, 5, -1) == ("ab", 0, 1)


# ==================== 越界与不变性 ====================


def test_offsets_are_clamped_to_value_length():
    """偏移越界（控件侧异步事件可能滞后）不应抛异常或改坏文本。"""
    assert apply_indent("ab", 99, 99, 1) == ("ab    ", 6, 6)
    assert apply_indent("ab", -5, -5, 1) == ("    ab", 4, 4)
    assert apply_indent("ab", -5, -5, -1) == ("ab", 0, 0)


def test_indent_then_outdent_round_trips():
    """缩进再反缩进回到原文（单行折叠光标）。"""
    value = "    print('hi')"
    indented, b1, e1 = apply_indent(value, 0, 0, 1)
    assert (b1, e1) == (4, 4)
    restored, b2, e2 = apply_indent(indented, b1, e1, -1)
    assert restored == value
    assert (b2, e2) == (0, 0)
