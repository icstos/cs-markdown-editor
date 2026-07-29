"""views/_editor_helpers 纯函数单元测试。

覆盖从 MarkdownEditor 闭包剥离的 5 个无状态助手：
_snap_indent_up / _snap_indent_down / _shift_cursor_off / _vline_off_at_x / _table_cells。
不依赖 UI 层（flet 组件渲染），仅验证纯计算逻辑。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import BlockType, Line  # noqa: E402
from views._editor_helpers import (  # noqa: E402
    _fix_ime_doubling,
    _shift_cursor_off,
    _snap_indent_down,
    _snap_indent_up,
    _table_cells,
    _vline_off_at_x,
)
from views.pixel_layout import VisualLine  # noqa: E402


# ---------------- _snap_indent_up ----------------
def test_snap_indent_up_basic():
    """向 unit 倍数上取一级。"""
    assert _snap_indent_up(2, 2, 10) == 4
    assert _snap_indent_up(0, 2, 10) == 2


def test_snap_indent_up_clamp():
    """钳制到 limit，不越界。"""
    assert _snap_indent_up(8, 2, 10) == 10
    assert _snap_indent_up(10, 2, 10) == 10


def test_snap_indent_up_unit4():
    assert _snap_indent_up(3, 4, 16) == 4  # 下一个 4 的倍数（3//4=0 → 4）
    assert _snap_indent_up(4, 4, 16) == 8  # 已在倍数上，仍上取一级 → 8


# ---------------- _snap_indent_down ----------------
def test_snap_indent_down_basic():
    """向 unit 倍数下取一级（不含当前）。"""
    assert _snap_indent_down(3, 2) == 2
    assert _snap_indent_down(1, 2) == 0


def test_snap_indent_down_zero():
    """level<=0 时返回 0。"""
    assert _snap_indent_down(0, 2) == 0
    assert _snap_indent_down(-1, 2) == 0


def test_snap_indent_down_at_multiple():
    """已在倍数上时下取到前一级（不含当前）。"""
    assert _snap_indent_down(4, 2) == 2
    assert _snap_indent_down(2, 2) == 0


# ---------------- _shift_cursor_off ----------------
def test_shift_cursor_in_content():
    """光标在内容区：按前缀长度差平移。"""
    assert _shift_cursor_off(5, 2, 4, 10) == 7  # 5 + (4-2)


def test_shift_cursor_in_prefix():
    """光标在前缀内：落到新前缀末尾。"""
    assert _shift_cursor_off(1, 2, 4, 10) == 4


def test_shift_cursor_clamp_high():
    """平移后越界：钳制到 new_raw_len。"""
    assert _shift_cursor_off(5, 2, 4, 6) == 6  # 5+(4-2)=7 → 6


def test_shift_cursor_no_prefix():
    """无前缀变化（old=new=0）：光标原位。"""
    assert _shift_cursor_off(3, 0, 0, 5) == 3


# ---------------- _table_cells ----------------
def test_table_cells_spaced():
    assert _table_cells(Line(BlockType.TABLE, "| a | b |")) == ["a", "b"]


def test_table_cells_tight():
    assert _table_cells(Line(BlockType.TABLE, "|a|b|")) == ["a", "b"]


def test_table_cells_cjk():
    assert _table_cells(Line(BlockType.TABLE, "| 居左 | 居中 |")) == ["居左", "居中"]


def test_table_cells_empty():
    assert _table_cells(Line(BlockType.TABLE, "|  |  |")) == ["", ""]


def test_table_cells_single():
    assert _table_cells(Line(BlockType.TABLE, "| solo |")) == ["solo"]


# ---------------- _fix_ime_doubling ----------------
def test_ime_doubling_cjk_single_commit_new_session():
    """五笔/拼音单次上屏翻倍：新会话 last_value="" 时 '你你' → '你'。"""
    assert _fix_ime_doubling("你你", "") == "你"


def test_ime_doubling_cjk_after_composing():
    """composing 编码转上屏翻倍：last_value='wq' 时 '你你' → '你'。"""
    assert _fix_ime_doubling("你你", "wq") == "你"


def test_ime_doubling_cjk_legitimate_repeat():
    """合法连续输入两个'好'：last_value='好' 时 '好好' 不折叠。"""
    assert _fix_ime_doubling("好好", "好") == "好好"


def test_ime_doubling_ascii_compose_doubled():
    """ASCII composing 翻倍：'wqwq' → 'wq'（阈值 >=4）。"""
    assert _fix_ime_doubling("wqwq", "") == "wq"
    assert _fix_ime_doubling("wqwq", "wq") == "wq"


def test_ime_doubling_ascii_double_strike_not_folded():
    """ASCII len=2 双叠不折叠：避免误伤 'ww'、'//' 连击。"""
    assert _fix_ime_doubling("ww", "w") == "ww"
    assert _fix_ime_doubling("//", "") == "//"


def test_ime_doubling_no_doubling_passthrough():
    """非双叠 / 奇数长度 / 空串：原样返回。"""
    assert _fix_ime_doubling("你好", "") == "你好"
    assert _fix_ime_doubling("abcd", "") == "abcd"
    assert _fix_ime_doubling("你", "") == "你"
    assert _fix_ime_doubling("", "") == ""


def test_ime_doubling_cjk_long_doubled():
    """非 ASCII 长串翻倍：'你你你你' last_value='' → '你你'。"""
    assert _fix_ime_doubling("你你你你", "") == "你你"


def test_ime_doubling_cjk_long_legitimate():
    """非 ASCII 长串合法连续：'你你你你' last_value='你你' → 不折叠。"""
    assert _fix_ime_doubling("你你你你", "你你") == "你你你你"


def test_ime_doubling_mixed_not_folded():
    """混合 ASCII/非 ASCII 非双叠：原样返回。"""
    assert _fix_ime_doubling("a你b你", "") == "a你b你"


# ---------------- _vline_off_at_x ----------------
def test_vline_off_empty():
    """空视觉行列表：返回 None。"""
    assert _vline_off_at_x([], 0, 0.0) is None


def test_vline_off_out_of_range():
    """vline_idx 越界（负 / 超长）：返回 None。"""
    vline = VisualLine(vline_idx=0, start_raw=2, end_raw=2, offsets_x=[0.0], width=0.0)
    assert _vline_off_at_x([vline], -1, 0.0) is None
    assert _vline_off_at_x([vline], 1, 0.0) is None


def test_vline_off_exact_match():
    """单点视觉行：X 命中起点 → start_raw + 0。"""
    vline = VisualLine(vline_idx=0, start_raw=2, end_raw=2, offsets_x=[0.0], width=0.0)
    assert _vline_off_at_x([vline], 0, 0.0) == 2


if __name__ == "__main__":
    test_snap_indent_up_basic()
    test_snap_indent_up_clamp()
    test_snap_indent_up_unit4()
    test_snap_indent_down_basic()
    test_snap_indent_down_zero()
    test_snap_indent_down_at_multiple()
    test_shift_cursor_in_content()
    test_shift_cursor_in_prefix()
    test_shift_cursor_clamp_high()
    test_shift_cursor_no_prefix()
    test_table_cells_spaced()
    test_table_cells_tight()
    test_table_cells_cjk()
    test_table_cells_empty()
    test_table_cells_single()
    test_vline_off_empty()
    test_vline_off_out_of_range()
    test_vline_off_exact_match()
    print("\n所有 _editor_helpers 单元测试通过 ✅")
