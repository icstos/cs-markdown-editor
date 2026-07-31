"""views/_editor_helpers 纯函数单元测试。

覆盖从 MarkdownEditor 闭包剥离的无状态助手：
_snap_indent_up / _snap_indent_down / _shift_cursor_off / _vline_off_at_x / _table_cells /
_fix_ime_doubling / _detect_ime_compose / _compute_composing_trim / _rebuild_list_prefix /
_char_kind / _select_word_bounds / _build_highlight_map / _step_left / _step_right /
_build_offset_prefix / _make_snapshot / _compute_delete_result。
不依赖 UI 层（flet 组件渲染），仅验证纯计算逻辑。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import BlockType, Line
from views._editor_helpers import (
    _build_highlight_map,
    _build_offset_prefix,
    _char_kind,
    _compute_delete_result,
    _compute_composing_trim,
    _detect_ime_compose,
    _fix_ime_doubling,
    _make_snapshot,
    _rebuild_list_prefix,
    _select_word_bounds,
    _shift_cursor_off,
    _snap_indent_down,
    _snap_indent_up,
    _step_left,
    _step_right,
    _table_cells,
    _vline_off_at_x,
)
from views.pixel_layout import VisualLine


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


def test_ime_doubling_ascii_repeat_chars_not_folded():
    """连续输入相同 ASCII 字符不折叠（修复 'aaaa' 被误折叠导致第4字被吞 BUG）。

    连续输入时 value 逐步累积（每次 +1 字符），last_value 是 value 去掉末字符，
    满足 len(value)==len(last_value)+1 且 value.startswith(last_value)。
    """
    # 4 个 a：last_value='aaa'（第3次后），value='aaaa' → 不折叠
    assert _fix_ime_doubling("aaaa", "aaa") == "aaaa"
    # 6 个 a：last_value='aaaaa'，value='aaaaaa' → 不折叠
    assert _fix_ime_doubling("aaaaaa", "aaaaa") == "aaaaaa"
    # 4 个 w：last_value='www'，value='wwww' → 不折叠
    assert _fix_ime_doubling("wwww", "www") == "wwww"
    # 4 个 /：last_value='///'，value='////' → 不折叠
    assert _fix_ime_doubling("////", "///") == "////"


def test_ime_doubling_ascii_compose_still_folded():
    """IME composing 翻倍仍折叠：'wqwq' last_value='wq' → 'wq'（len 4 != 3）。"""
    # composing 翻倍：last_value='wq'，value='wqwq'，len(4) != len('wq')+1(3) → 折叠
    assert _fix_ime_doubling("wqwq", "wq") == "wq"
    # 新会话 composing 翻倍：last_value=''，value='wqwq' → 折叠
    assert _fix_ime_doubling("wqwq", "") == "wq"
    # 长串 composing 翻倍：'abcabc' last_value='abc' → 'abc'（len 6 != 4）
    assert _fix_ime_doubling("abcabc", "abc") == "abc"


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


# ---------------- _detect_ime_compose ----------------
def test_ime_compose_first_commit_pure_ascii():
    """首字上屏：last_value 为纯 ASCII composing（'wq'→'你'）→ True。"""
    assert _detect_ime_compose("你", "wq") is True


def test_ime_compose_second_commit_mixed():
    """连续上屏第二字：last_value 已含已上屏中文（'你vb'→'你好'）→ True。

    五笔输入 '你好啊' 错写成 '你vb你好kb你好啊' 的根因用例：旧条件
    all(ord<128 for c in last_value) 在此场景失败（last_value 含 '你'）。
    """
    assert _detect_ime_compose("你好", "你vb") is True


def test_ime_compose_third_commit_mixed():
    """连续上屏第三字：last_value='你好kb'→'你好啊' → True。"""
    assert _detect_ime_compose("你好啊", "你好kb") is True


def test_ime_compose_pinyin_multi_char_commit():
    """拼音整句上屏：'niha'→'你好'（一次上屏多字）→ True。"""
    assert _detect_ime_compose("你好", "niha") is True


def test_ime_compose_phrase_after_commit():
    """已上屏后再输入词组：'你好hao'→'你好世界' → True（公共前缀 '你好'）。"""
    assert _detect_ime_compose("你好世界", "你好hao") is True


def test_ime_compose_composing_append():
    """composing 进行中（追加 ASCII）：'你'→'你v' → False（composing 后缀为空，属 append）。"""
    assert _detect_ime_compose("你v", "你") is False


def test_ime_compose_composing_grow():
    """composing 增长：'你v'→'你vb' → False（committed='b' 全 ASCII，属 append）。"""
    assert _detect_ime_compose("你vb", "你v") is False


def test_ime_compose_legitimate_cjk_repeat():
    """合法连续输入两个'好'：'好'→'好好' → False（composing 后缀为空，属 append）。"""
    assert _detect_ime_compose("好好", "好") is False


def test_ime_compose_ascii_continuous():
    """ASCII 连续输入：'abc'→'abcd' → False（committed 全 ASCII，属 append）。"""
    assert _detect_ime_compose("abcd", "abc") is False


def test_ime_compose_empty_last_value():
    """新会话 last_value 为空 → False（无 composing 可替换，由 append 分支处理）。"""
    assert _detect_ime_compose("你", "") is False


def test_ime_compose_equal_values():
    """value == last_value → False（公共前缀为整体，composing 后缀为空）。"""
    assert _detect_ime_compose("你好", "你好") is False


def test_ime_compose_ascii_composing_replaced_by_ascii():
    """composing ASCII 被另一 ASCII 串替换（非 IME 上屏）→ False。"""
    assert _detect_ime_compose("abx", "abc") is False


# ---------------- _compute_composing_trim ----------------
def test_composing_trim_cancel_keeps_committed():
    """composing 取消：'你vb'→'你'，保留已上屏 '你'，移除 'vb'。"""
    assert _compute_composing_trim("你", "你vb") == "你"


def test_composing_trim_cancel_third_char():
    """第三字 composing 取消：'你好kb'→'你好'。"""
    assert _compute_composing_trim("你好", "你好kb") == "你好"


def test_composing_trim_full_cancel_empty():
    """composing 全部放弃（无已上屏文本）：'vb'→''。"""
    assert _compute_composing_trim("", "vb") == ""


def test_composing_trim_pinyin_cancel():
    """拼音 composing 取消：'你ha'→'你'。"""
    assert _compute_composing_trim("你", "你ha") == "你"


def test_composing_trim_no_composing_direct_input():
    """无 composing（直接输入）：value==last_value → None（不裁剪）。"""
    assert _compute_composing_trim("abc", "abc") is None


def test_composing_trim_cjk_direct_input():
    """中文直接输入（已上屏）：value==last_value → None。"""
    assert _compute_composing_trim("你好", "你好") is None


def test_composing_trim_ascii_continuous_not_trimmed():
    """ASCII 连续输入不裁剪：'abcd'==last_value → None。"""
    assert _compute_composing_trim("abcd", "abcd") is None


def test_composing_trim_empty_last_value():
    """无活动会话（last_value 空）→ None。"""
    assert _compute_composing_trim("你", "") is None


def test_composing_trim_value_not_prefix():
    """value 非 last_value 前缀（异常形态）→ None（保守不裁剪）。"""
    assert _compute_composing_trim("你好", "你vb") is None
    assert _compute_composing_trim("xyz", "abc") is None


def test_composing_trim_shrink_mid_composing():
    """composing 缩短（仍活跃）：'你vb'→'你v'，value 是真前缀 → 返回 '你v'。

    on_submit 专用：回车时若 value 是真前缀即视为 composing 放弃并裁剪。
    （composing 缩短本身不会触发 on_submit，仅在回车时发生。）
    """
    assert _compute_composing_trim("你v", "你vb") == "你v"


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


# ---------------- _rebuild_list_prefix ----------------
def test_rebuild_list_prefix_uo():
    """无序列表前缀：保留原 marker 符号。"""
    assert _rebuild_list_prefix(2, "- body", BlockType.LIST_UO, False, False, False) == "  - "
    assert _rebuild_list_prefix(0, "* body", BlockType.LIST_UO, False, False, False) == "* "
    assert _rebuild_list_prefix(4, "+ body", BlockType.LIST_UO, False, False, False) == "    + "


def test_rebuild_list_prefix_o_keep_num():
    """有序列表前缀：restart_num=False 时保留原序号。"""
    assert _rebuild_list_prefix(0, "3. body", BlockType.LIST_O, False, False, False) == "3. "
    assert _rebuild_list_prefix(2, "10. body", BlockType.LIST_O, False, False, False) == "  10. "


def test_rebuild_list_prefix_o_restart():
    """有序列表前缀：restart_num=True 时序号重置为 1。"""
    assert _rebuild_list_prefix(2, "5. body", BlockType.LIST_O, False, False, True) == "  1. "


def test_rebuild_list_prefix_task():
    """任务列表前缀：保留 marker + 勾选状态。"""
    assert _rebuild_list_prefix(0, "- [ ] body", BlockType.LIST_UO, True, False, False) == "- [ ] "
    assert _rebuild_list_prefix(2, "- [x] body", BlockType.LIST_UO, True, True, False) == "  - [x] "


def test_rebuild_list_prefix_fallback_marker():
    """body 无有效 marker 时回退默认符号。"""
    assert _rebuild_list_prefix(0, "body", BlockType.LIST_UO, False, False, False) == "- "
    assert _rebuild_list_prefix(0, "body", BlockType.LIST_O, False, False, False) == "1. "


# ---------------- _char_kind ----------------
def test_char_kind_space():
    assert _char_kind(" ") == "space"
    assert _char_kind("\t") == "space"


def test_char_kind_cjk():
    assert _char_kind("你") == "cjk"
    assert _char_kind("あ") == "cjk"  # 平假名
    assert _char_kind("ア") == "cjk"  # 片假名
    assert _char_kind("한") == "cjk"  # 韩文


def test_char_kind_word():
    assert _char_kind("a") == "word"
    assert _char_kind("Z") == "word"
    assert _char_kind("0") == "word"
    assert _char_kind("_") == "word"


def test_char_kind_punct():
    assert _char_kind("*") == "punct"
    assert _char_kind("#") == "punct"
    assert _char_kind("-") == "punct"
    assert _char_kind(".") == "punct"


# ---------------- _select_word_bounds ----------------
def test_select_word_bounds_word():
    """英文单词：扩展到词边界。"""
    assert _select_word_bounds("hello world", 0) == (0, 5)
    assert _select_word_bounds("hello world", 4) == (0, 5)
    assert _select_word_bounds("hello world", 6) == (6, 11)


def test_select_word_bounds_cjk():
    """CJK：连续 CJK 视为一词。"""
    assert _select_word_bounds("你好世界", 0) == (0, 4)
    assert _select_word_bounds("你好世界", 2) == (0, 4)


def test_select_word_bounds_mixed_cjk_word():
    """中英混排：CJK 与 word 互相合并。"""
    assert _select_word_bounds("你好abc世界", 2) == (0, 7)  # 7 = len("你好abc世界")


def test_select_word_bounds_punct():
    """标点：连续标点视为一词。"""
    assert _select_word_bounds("**bold**", 0) == (0, 2)
    assert _select_word_bounds("**bold**", 6) == (6, 8)


def test_select_word_bounds_space_skip_left():
    """空白：向左找首个非空字符。"""
    assert _select_word_bounds("a   b", 2) == (0, 1)


def test_select_word_bounds_space_skip_right():
    """空白：左侧无非空时向右找。"""
    assert _select_word_bounds("   b", 1) == (3, 4)


def test_select_word_bounds_all_space():
    """整行全空白：返回 None。"""
    assert _select_word_bounds("   ", 1) is None


def test_select_word_bounds_empty():
    """空串：返回 None。"""
    assert _select_word_bounds("", 0) is None


def test_select_word_bounds_off_past_end():
    """off == len(raw) 时按 punct 处理（与原闭包一致）。"""
    bounds = _select_word_bounds("abc", 3)
    assert bounds is not None
    start, end = bounds
    assert start <= 3 and end >= 3


# ---------------- _build_highlight_map ----------------
def test_build_highlight_map_none():
    """无选区：返回空 dict。"""
    assert _build_highlight_map([], None) == {}


def test_build_highlight_map_single_line():
    """单行选区：该行 (a_off, b_off)。"""
    lines = [Line(BlockType.PARAGRAPH, "hello world")]
    result = _build_highlight_map(lines, (0, 2, 0, 7))
    assert result == {0: (2, 7)}


def test_build_highlight_map_multi_line():
    """多行选区：首行 (a_off, len)，中间行 (0, len)，尾行 (0, b_off)。"""
    lines = [
        Line(BlockType.PARAGRAPH, "first"),
        Line(BlockType.PARAGRAPH, "middle"),
        Line(BlockType.PARAGRAPH, "last"),
    ]
    result = _build_highlight_map(lines, (0, 2, 2, 3))
    assert result == {0: (2, 5), 1: (0, 6), 2: (0, 3)}


def test_build_highlight_map_reversed():
    """反向选区：自动归一化。"""
    lines = [Line(BlockType.PARAGRAPH, "hello")]
    result = _build_highlight_map(lines, (0, 4, 0, 1))
    assert result == {0: (1, 4)}


# ---------------- _step_left / _step_right ----------------
def test_step_left_in_line():
    """行内左移。"""
    lines = [Line(BlockType.PARAGRAPH, "hello")]
    assert _step_left(lines, 0, 3) == (0, 2)


def test_step_left_cross_line():
    """行首跳上一行尾。"""
    lines = [Line(BlockType.PARAGRAPH, "first"), Line(BlockType.PARAGRAPH, "second")]
    assert _step_left(lines, 1, 0) == (0, 5)


def test_step_left_at_doc_start():
    """文档起点返回 None。"""
    lines = [Line(BlockType.PARAGRAPH, "hello")]
    assert _step_left(lines, 0, 0) is None


def test_step_left_skip_fence():
    """上一行为围栏块时返回 None。"""
    lines = [Line(BlockType.CODE, "```py"), Line(BlockType.PARAGRAPH, "text")]
    assert _step_left(lines, 1, 0) is None


def test_step_right_in_line():
    """行内右移。"""
    lines = [Line(BlockType.PARAGRAPH, "hello")]
    assert _step_right(lines, 0, 2) == (0, 3)


def test_step_right_cross_line():
    """行尾跳下一行首。"""
    lines = [Line(BlockType.PARAGRAPH, "first"), Line(BlockType.PARAGRAPH, "second")]
    assert _step_right(lines, 0, 5) == (1, 0)


def test_step_right_at_doc_end():
    """文档末尾返回 None。"""
    lines = [Line(BlockType.PARAGRAPH, "hello")]
    assert _step_right(lines, 0, 5) is None


def test_step_right_skip_fence():
    """下一行为围栏块时返回 None。"""
    lines = [Line(BlockType.PARAGRAPH, "text"), Line(BlockType.CODE, "```py")]
    assert _step_right(lines, 0, 4) is None


# ---------------- _build_offset_prefix ----------------
def test_build_offset_prefix_basic():
    """前缀和：prefix[i] = sum(heights[0..i-1])。"""
    assert _build_offset_prefix([10.0, 20.0, 30.0]) == [0.0, 10.0, 30.0, 60.0]


def test_build_offset_prefix_empty():
    """空列表：prefix = [0.0]。"""
    assert _build_offset_prefix([]) == [0.0]


def test_build_offset_prefix_single():
    """单元素：prefix = [0.0, h]。"""
    assert _build_offset_prefix([15.0]) == [0.0, 15.0]


# ---------------- _make_snapshot ----------------
def test_make_snapshot_basic():
    """构造快照：字段透传。"""
    snap = _make_snapshot(3, 10, False, "", "# Title\n")
    assert snap.cursor_li == 3
    assert snap.cursor_off == 10
    assert snap.raw_mode is False
    assert snap.raw_draft == ""
    assert snap.markdown == "# Title\n"


def test_make_snapshot_browse_mode():
    """浏览态：cursor_li=None。"""
    snap = _make_snapshot(None, 0, False, "", "")
    assert snap.cursor_li is None
    assert snap.cursor_off == 0


def test_make_snapshot_raw_mode():
    """原文模式：markdown = raw_draft。"""
    snap = _make_snapshot(0, 5, True, "raw draft", "raw draft")
    assert snap.raw_mode is True
    assert snap.raw_draft == "raw draft"
    assert snap.markdown == "raw draft"


# ---------------- _compute_delete_result ----------------
def test_compute_delete_single_line():
    """单行删除：返回同列表引用 + 新 raw。"""
    lines = [Line(BlockType.PARAGRAPH, "hello world")]
    result = _compute_delete_result(lines, 0, 2, 0, 7)
    assert result is not None
    merge_li, merged_raw, new_lines = result
    assert merge_li == 0
    assert merged_raw == "heorld"
    assert new_lines is lines  # 同引用，行数不变


def test_compute_delete_multi_line():
    """多行删除：返回新列表 + 合并行 raw。"""
    lines = [
        Line(BlockType.PARAGRAPH, "first"),
        Line(BlockType.PARAGRAPH, "middle"),
        Line(BlockType.PARAGRAPH, "last"),
    ]
    result = _compute_delete_result(lines, 0, 2, 2, 2)
    assert result is not None
    merge_li, merged_raw, new_lines = result
    assert merge_li == 0
    assert merged_raw == "fist"  # "fi" + "st"
    assert len(new_lines) == 1  # 3行 → 1行
    assert new_lines is not lines  # 新列表


def test_compute_delete_invalid_bounds():
    """行号越界：返回 None。"""
    lines = [Line(BlockType.PARAGRAPH, "hello")]
    assert _compute_delete_result(lines, 0, 0, 5, 0) is None
    assert _compute_delete_result(lines, -1, 0, 0, 0) is None


def test_compute_delete_whole_line_content():
    """删除整行内容：merged_raw = ""。"""
    lines = [Line(BlockType.PARAGRAPH, "hello")]
    result = _compute_delete_result(lines, 0, 0, 0, 5)
    assert result is not None
    _, merged_raw, _ = result
    assert merged_raw == ""


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
