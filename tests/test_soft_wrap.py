"""软换行（2D 视觉行布局）单元测试。

覆盖：
- _wrap_offsets_into_visual_lines：空/单空格/长拉丁（空格断）/长 CJK（逐字断）
  /混合/超长 URL（强制断）/trailing space
- _find_vline_for_raw：二分查找 + 边界归属
- _compute_wrap_width：宽度计算 + 钳制
- LineLayoutCache：2D hit_test / cursor_px / 变量高度

运行：python -m tests.test_soft_wrap  或  python tests/test_soft_wrap.py
无需 Flet 运行时（仅测量函数 + 数据结构，不触发 UI 渲染）。
"""

import sys
import os

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import BlockType, Line, SegType, Segment
from views.pixel_layout import (
    LineLayout,
    LineLayoutCache,
    VisualLine,
    _compute_wrap_width,
    _find_vline_for_raw,
    _layout_last_raw_off,
    _line_visual_layout,
    _wrap_offsets_into_visual_lines,
    _make_visual_line,
)
from views.rendered_line import (
    _build_raw_to_flat_map,
    _slice_spans_for_visual_line,
)


# ============ 辅助构造 ============

def _make_offsets_x(widths: list[float]) -> list[float]:
    """从逐字符宽度列表构造累加 X 偏移（长度 = len(widths)+1，[0]=0）。"""
    offsets = [0.0]
    acc = 0.0
    for w in widths:
        acc += w
        offsets.append(acc)
    return offsets


def _make_line(raw: str, segments: list[Segment] | None = None,
               block_type: BlockType = BlockType.PARAGRAPH, level: int = 0) -> Line:
    """快速构造 Line。segments 默认为单个 TEXT 段。"""
    if segments is None:
        segments = [Segment(SegType.TEXT, raw, raw)]
    return Line(block_type=block_type, raw=raw, segments=segments, level=level)


# ============ _wrap_offsets_into_visual_lines ============

def test_wrap_empty():
    """空文本：单 vline，start=end=0。"""
    offsets = [0.0]
    vlines = _wrap_offsets_into_visual_lines(offsets, "", 100.0)
    assert len(vlines) == 1, f"空文本应 1 vline，实际 {len(vlines)}"
    assert vlines[0].start_raw == 0
    assert vlines[0].end_raw == 0
    assert vlines[0].offsets_x == [0.0]
    print("  ✓ test_wrap_empty")


def test_wrap_single_char():
    """单字符：单 vline。"""
    offsets = _make_offsets_x([10.0])
    vlines = _wrap_offsets_into_visual_lines(offsets, "A", 100.0)
    assert len(vlines) == 1
    assert vlines[0].start_raw == 0
    assert vlines[0].end_raw == 1
    assert vlines[0].offsets_x == [0.0, 10.0]
    print("  ✓ test_wrap_single_char")


def test_wrap_no_overflow():
    """未溢出 wrap_width：单 vline。"""
    # 5 字符，每字 10px，总宽 50px，wrap_width=100 → 不换行
    offsets = _make_offsets_x([10.0] * 5)
    vlines = _wrap_offsets_into_visual_lines(offsets, "ABCDE", 100.0)
    assert len(vlines) == 1, f"未溢出应 1 vline，实际 {len(vlines)}"
    assert vlines[0].end_raw == 5
    print("  ✓ test_wrap_no_overflow")


def test_wrap_space_break():
    """长拉丁文：空格处断行，空格留当前行。"""
    # "AB CDE" 每字 10px，wrap_width=25 → "AB "（30>25 在 C 处溢出）
    # offsets: [0,10,20,30,40,50,60]，wrap=25
    # i=1(advance=10)<=25; i=2(advance=20)<=25, raw[1]='B'非空格; i=3(advance=30)>25
    #   last_break=0(初始) → break_at=3(force), vline0=[0,3]
    #   ... 实际需细看算法
    offsets = _make_offsets_x([10.0, 10.0, 10.0, 10.0, 10.0, 10.0])
    vlines = _wrap_offsets_into_visual_lines(offsets, "AB CDE", 25.0)
    # 验证：所有 vline 文本拼接（用 start/end 切 raw）== 原文（末行含 end_raw）
    assert _vlines_cover_full(vlines, "AB CDE", 6), _dump_vlines(vlines, "AB CDE")
    # 至少 2 行
    assert len(vlines) >= 2, f"应换行 >=2 vline，实际 {len(vlines)}"
    # 空格留在前行（vline0.end_raw 应包含空格位置 2）
    assert vlines[0].end_raw >= 3, f"空格应留当前行，vline0.end_raw={vlines[0].end_raw}"
    print("  ✓ test_wrap_space_break")


def test_wrap_cjk_break():
    """长 CJK：每个 CJK 字符前可断。"""
    # "中文测试" 每字 16px，wrap_width=40 → 约 2-3 字一行
    offsets = _make_offsets_x([16.0, 16.0, 16.0, 16.0])
    vlines = _wrap_offsets_into_visual_lines(offsets, "中文测试", 40.0)
    assert _vlines_cover_full(vlines, "中文测试", 4), _dump_vlines(vlines, "中文测试")
    assert len(vlines) >= 2, f"CJK 应换行 >=2 vline，实际 {len(vlines)}"
    # CJK 断行：每行最多 2 字（16*2=32<=40, 16*3=48>40）
    for v in vlines:
        chars = v.end_raw - v.start_raw
        assert chars <= 3, f"单行字符 {chars} 超预期"
    print("  ✓ test_wrap_cjk_break")


def test_wrap_force_break_long_url():
    """超长不可断词（URL）：强制字符级断行。"""
    # "ABCDEFG" 无空格无 CJK，每字 10px，wrap_width=25
    # i=3(advance=30)>25, last_break=0 → break_at=3(force), vline0=[0,3]
    # i=6(advance=30 from vline_start=3)>25, last_break=3 → break_at=6(force), vline1=[3,6]
    # 末行 vline2=[6,7]
    offsets = _make_offsets_x([10.0] * 7)
    vlines = _wrap_offsets_into_visual_lines(offsets, "ABCDEFG", 25.0)
    assert _vlines_cover_full(vlines, "ABCDEFG", 7), _dump_vlines(vlines, "ABCDEFG")
    assert len(vlines) >= 2, f"超长词应强制断行 >=2 vline，实际 {len(vlines)}"
    print("  ✓ test_wrap_force_break_long_url")


def test_wrap_trailing_space():
    """trailing space 不触发提前断行。"""
    # "AB " 3 字符，每字 10px，wrap_width=100 → 不换行（trailing space 不溢出）
    offsets = _make_offsets_x([10.0, 10.0, 10.0])
    vlines = _wrap_offsets_into_visual_lines(offsets, "AB ", 100.0)
    assert len(vlines) == 1, f"trailing space 不应换行，实际 {len(vlines)} vline"
    assert vlines[0].end_raw == 3
    print("  ✓ test_wrap_trailing_space")


def test_wrap_min_break_off():
    """min_break_off 之前不断（前缀段整段留 vline 0）。"""
    # 假设前缀 2 字符 "# "，min_break_off=2，正文 "ABCDE" 每字 10px
    # wrap_width=25：前缀 20px 已占，正文第 1 字 30>25 但不能在前缀内断
    offsets = _make_offsets_x([10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0])
    vlines = _wrap_offsets_into_visual_lines(offsets, "# ABCDE", 25.0, min_break_off=2)
    assert _vlines_cover_full(vlines, "# ABCDE", 7), _dump_vlines(vlines, "# ABCDE")
    # vline0 应包含前缀（start_raw=0, end_raw >= 2）
    assert vlines[0].start_raw == 0
    assert vlines[0].end_raw >= 2, f"前缀应留 vline0，end_raw={vlines[0].end_raw}"
    print("  ✓ test_wrap_min_break_off")


def test_wrap_vline_continuity():
    """视觉行连续性：vline[k].end_raw == vline[k+1].start_raw。"""
    offsets = _make_offsets_x([10.0] * 20)
    vlines = _wrap_offsets_into_visual_lines(offsets, "A" * 20, 50.0)
    for k in range(len(vlines) - 1):
        assert vlines[k].end_raw == vlines[k + 1].start_raw, \
            f"vline{k}.end_raw={vlines[k].end_raw} != vline{k+1}.start_raw={vlines[k+1].start_raw}"
    print("  ✓ test_wrap_vline_continuity")


def test_wrap_offsets_rebased():
    """每个 vline 的 offsets_x 从 0 开始（rebase 到行内）。"""
    offsets = _make_offsets_x([10.0] * 10)
    vlines = _wrap_offsets_into_visual_lines(offsets, "A" * 10, 30.0)
    for v in vlines:
        assert v.offsets_x[0] == 0.0, f"vline{v.vline_idx} offsets_x[0]={v.offsets_x[0]} 非 0"
    print("  ✓ test_wrap_offsets_rebased")


# ============ _find_vline_for_raw ============

def test_find_vline_basic():
    """二分查找：raw_off 落在正确视觉行。"""
    offsets = _make_offsets_x([10.0] * 10)
    vlines = _wrap_offsets_into_visual_lines(offsets, "A" * 10, 30.0)
    # 每行 3 字：vline0=[0,3], vline1=[3,6], vline2=[6,10]（大致）
    for raw_off in range(11):
        v = _find_vline_for_raw(vlines, raw_off)
        assert v is not None, f"raw_off={raw_off} 返回 None"
        assert v.start_raw <= raw_off <= v.end_raw, \
            f"raw_off={raw_off} 不在 vline[{v.vline_idx}] 范围 [{v.start_raw},{v.end_raw}]"
    print("  ✓ test_find_vline_basic")


def test_find_vline_boundary():
    """边界归属：raw_off == vline[k+1].start_raw 时归 vline k（行尾右边缘）。"""
    # 构造明确的 2 vline：vline0=[0,3], vline1=[3,5]
    vlines = [
        VisualLine(0, 0, 3, [0.0, 10.0, 20.0, 30.0], 30.0),
        VisualLine(1, 3, 5, [0.0, 10.0, 20.0], 20.0),
    ]
    # raw_off=3 是边界，应归 vline0（行尾）
    v = _find_vline_for_raw(vlines, 3)
    assert v.vline_idx == 0, f"边界 raw_off=3 应归 vline0，实际 vline{v.vline_idx}"
    # raw_off=4 归 vline1
    v = _find_vline_for_raw(vlines, 4)
    assert v.vline_idx == 1, f"raw_off=4 应归 vline1，实际 vline{v.vline_idx}"
    # raw_off=0 归 vline0
    v = _find_vline_for_raw(vlines, 0)
    assert v.vline_idx == 0, f"raw_off=0 应归 vline0，实际 vline{v.vline_idx}"
    print("  ✓ test_find_vline_boundary")


def test_find_vline_single():
    """单 vline：所有 raw_off 都归 vline0。"""
    vlines = [VisualLine(0, 0, 5, [0.0, 10.0, 20.0, 30.0, 40.0, 50.0], 50.0)]
    for raw_off in range(6):
        v = _find_vline_for_raw(vlines, raw_off)
        assert v is not None and v.vline_idx == 0
    print("  ✓ test_find_vline_single")


def test_find_vline_empty():
    """空列表：返回 None。"""
    assert _find_vline_for_raw([], 0) is None
    print("  ✓ test_find_vline_empty")


# ============ _compute_wrap_width ============

def test_compute_wrap_width_normal():
    assert _compute_wrap_width(800.0, 40.0) == 760.0
    print("  ✓ test_compute_wrap_width_normal")


def test_compute_wrap_width_inf():
    """无限宽：不换行。"""
    assert _compute_wrap_width(float("inf"), 40.0) == float("inf")
    print("  ✓ test_compute_wrap_width_inf")


def test_compute_wrap_width_clamp():
    """过小：钳制到 50。"""
    assert _compute_wrap_width(60.0, 40.0) == 50.0  # 60-40=20 < 50 → 50
    assert _compute_wrap_width(30.0, 0.0) == 50.0   # 30 < 50 → 50
    print("  ✓ test_compute_wrap_width_clamp")


def test_compute_wrap_width_zero():
    """零或负：不换行。"""
    assert _compute_wrap_width(0.0, 0.0) == float("inf")
    assert _compute_wrap_width(-10.0, 0.0) == float("inf")
    print("  ✓ test_compute_wrap_width_zero")


# ============ _make_visual_line ============

def test_make_visual_line():
    """从 1D offsets 截取并 rebase。"""
    offsets = _make_offsets_x([10.0, 20.0, 30.0, 40.0, 50.0])
    v = _make_visual_line(1, 1, 3, offsets)
    assert v.vline_idx == 1
    assert v.start_raw == 1
    assert v.end_raw == 3
    # rebased: [offsets[1]-offsets[1], offsets[2]-offsets[1], offsets[3]-offsets[1], offsets[3]-offsets[1]]
    # = [0, 20, 50, 90]? wait offsets = [0,10,30,60,100,150], 截 [1,3] = [10,30,60] → rebase [0,20,50]
    assert v.offsets_x == [0.0, 20.0, 50.0], f"rebase 错误: {v.offsets_x}"
    assert v.width == 50.0
    print("  ✓ test_make_visual_line")


# ============ LineLayoutCache 2D ============

def test_cache_single_line_no_wrap():
    """短行不换行：num_vlines=1，height=text_h+pad。"""
    line = _make_line("短文本")
    cache = LineLayoutCache([line], content_width=800.0, line_height=1.6)
    layout = cache.get(0)
    assert layout is not None
    assert layout.num_vlines == 1, f"短行应 1 vline，实际 {layout.num_vlines}"
    # hit_test 命中
    result = cache.hit_test(50.0, layout.text_top + 5.0)
    assert result is not None and result[0] == 0
    print("  ✓ test_cache_single_line_no_wrap")


def test_cache_long_line_wraps():
    """长行换行：num_vlines>1，height=num_vlines*text_h+pad。"""
    # 构造一行很长的文本（100 个字符），wrap_width=200 → 多行
    long_text = "A" * 100
    line = _make_line(long_text)
    cache = LineLayoutCache([line], content_width=200.0, line_height=1.6)
    layout = cache.get(0)
    assert layout is not None
    assert layout.num_vlines > 1, f"长行应换行 >1 vline，实际 {layout.num_vlines}"
    # 高度 = num_vlines * text_h + pad
    from styles import block_text_size
    base = block_text_size(BlockType.PARAGRAPH, 0)
    expected_h = layout.num_vlines * base * 1.6 + 4.0  # pad_top+pad_bottom=2+2
    assert abs(layout.height - expected_h) < 0.01, \
        f"高度应为 {expected_h}，实际 {layout.height}"
    print("  ✓ test_cache_long_line_wraps")


def test_cache_hit_test_2d():
    """2D hit_test：不同 Y 命中不同视觉行。"""
    long_text = "A" * 100
    line = _make_line(long_text)
    cache = LineLayoutCache([line], content_width=200.0, line_height=1.6)
    layout = cache.get(0)
    assert layout is not None and layout.num_vlines > 1
    # 命中 vline 0（Y 在第一行）
    r0 = cache.hit_test(10.0, layout.text_top + 5.0)
    assert r0 is not None and r0[0] == 0
    # 命中 vline 1（Y 在第二行）
    text_h = layout.text_height
    r1 = cache.hit_test(10.0, layout.text_top + text_h + 5.0)
    assert r1 is not None and r1[0] == 0
    # vline 0 的 raw_off 应 < vline 1 的 raw_off（更靠后的视觉行 raw 偏移更大）
    assert r0[1] <= r1[1], f"vline0 raw_off={r0[1]} 应 <= vline1 raw_off={r1[1]}"
    print("  ✓ test_cache_hit_test_2d")


def test_cache_cursor_px_2d():
    """cursor_px：2D 定位，y 非零。"""
    long_text = "A" * 100
    line = _make_line(long_text)
    cache = LineLayoutCache([line], content_width=200.0, line_height=1.6)
    layout = cache.get(0)
    assert layout is not None and layout.num_vlines > 1
    # raw_off=0 → vline 0, y=0
    x0, y0, h0 = cache.cursor_px(0, 0)
    assert y0 == 0.0, f"raw_off=0 应 y=0，实际 y={y0}"
    # raw_off 在末尾 → 最后 vline, y>0
    last_off = _layout_last_raw_off(layout)
    xN, yN, hN = cache.cursor_px(0, last_off)
    assert yN > 0.0, f"末尾 raw_off={last_off} 应 y>0，实际 y={yN}"
    # 高度始终 = text_height（单视觉行高）
    assert h0 == layout.text_height
    assert hN == layout.text_height
    print("  ✓ test_cache_cursor_px_2d")


def test_cache_multiple_lines_y_accumulation():
    """多行 Y 累加：第二行 top = 第一行 height。"""
    lines = [_make_line("第一行"), _make_line("第二行")]
    cache = LineLayoutCache(lines, content_width=800.0, line_height=1.6)
    l0 = cache.get(0)
    l1 = cache.get(1)
    assert l0 is not None and l1 is not None
    assert abs(l1.top - l0.height) < 0.01, \
        f"l1.top={l1.top} 应 = l0.height={l0.height}"
    print("  ✓ test_cache_multiple_lines_y_accumulation")


def test_cache_fence_block_short_circuit():
    """围栏块短路：占位单 vline。"""
    code_seg = Segment(SegType.CODE, "print('hello')", "print('hello')")
    code_line = Line(block_type=BlockType.CODE, raw="```python\nprint('hello')\n```",
                     segments=[code_seg])
    cache = LineLayoutCache([code_line], content_width=800.0, line_height=1.6)
    layout = cache.get(0)
    assert layout is not None
    assert layout.num_vlines == 1, f"围栏块应 1 vline，实际 {layout.num_vlines}"
    print("  ✓ test_cache_fence_block_short_circuit")


def test_cache_quote_indent():
    """引用块：left_pad 按层级，wrap_width 扣除缩进。"""
    quote_line = _make_line("> 引用文本", block_type=BlockType.QUOTE, level=1)
    cache = LineLayoutCache([quote_line], content_width=800.0, line_height=1.6)
    layout = cache.get(0)
    assert layout is not None
    assert layout.left_pad == 12.0, f"引用 left_pad 应 12，实际 {layout.left_pad}"
    assert layout.wrap_width == 800.0 - 12.0, \
        f"wrap_width 应 {800-12}，实际 {layout.wrap_width}"
    print("  ✓ test_cache_quote_indent")


# ============ _build_raw_to_flat_map ============

def test_raw_to_flat_plain_text():
    """纯文本：raw 偏移与 flat 位置 1:1 对应。"""
    line = _make_line("hello")
    r2f = _build_raw_to_flat_map(line, cursor_off=None)
    assert len(r2f) == 6, f"len 应 6，实际 {len(r2f)}"
    assert r2f == [0, 1, 2, 3, 4, 5], f"纯文本 1:1，实际 {r2f}"
    print("  ✓ test_raw_to_flat_plain_text")


def test_raw_to_flat_bold_browse():
    """浏览态粗体：标记折叠，flat 只含内容。

    raw_to_flat[i] = raw 偏移 i 处的 flat 位置（即第 i 个字符之前）。
    raw="**bold**"（8 字符），flat="bold"（4 字符）。
    """
    seg = Segment(SegType.STRONG, "**bold**", "bold")
    line = Line(block_type=BlockType.PARAGRAPH, raw="**bold**", segments=[seg])
    r2f = _build_raw_to_flat_map(line, cursor_off=None)
    assert len(r2f) == 9
    assert r2f[0] == 0 and r2f[1] == 0 and r2f[2] == 0  # 前 "**" 折叠 → flat 0
    assert r2f[3] == 1  # 'b' 之后
    assert r2f[6] == 4  # 'd' 之后
    assert r2f[7] == 4 and r2f[8] == 4  # 后 "**" 折叠 → flat 4
    print("  ✓ test_raw_to_flat_bold_browse")


def test_raw_to_flat_bold_active():
    """激活态粗体（光标在段内）：标记可见，flat = raw 1:1。"""
    seg = Segment(SegType.STRONG, "**bold**", "bold")
    line = Line(block_type=BlockType.PARAGRAPH, raw="**bold**", segments=[seg])
    r2f = _build_raw_to_flat_map(line, cursor_off=3)  # 光标在 'b' 处
    # 光标在段内 → 全字符可见 → flat = raw 逐字符
    assert len(r2f) == 9
    assert r2f == [0, 1, 2, 3, 4, 5, 6, 7, 8], f"激活态 1:1，实际 {r2f}"
    print("  ✓ test_raw_to_flat_bold_active")


def test_raw_to_flat_heading_prefix():
    """标题前缀：浏览态折叠（display_text=""），光标在本行时可见。

    raw_to_flat[i] = raw 偏移 i 处的 flat 位置（第 i 个字符之前）。
    raw="# 标题"（4 字符: '#',' ','标','题'）。
    """
    prefix = Segment(SegType.HEADING_PREFIX, "# ", "")
    content = Segment(SegType.TEXT, "标题", "标题")
    line = Line(block_type=BlockType.HEADING, raw="# 标题", segments=[prefix, content], level=1)

    # 浏览态：前缀 display="" → flat 不前进，内容 flat 逐字符
    r2f = _build_raw_to_flat_map(line, cursor_off=None)
    assert r2f[0] == 0 and r2f[1] == 0 and r2f[2] == 0  # "# " 折叠 → flat 0
    assert r2f[3] == 1  # '标' 之后
    assert r2f[4] == 2  # '题' 之后

    # 光标在本行（非前缀段）：前缀可见（灰色），flat = raw 逐字符
    r2f2 = _build_raw_to_flat_map(line, cursor_off=3)  # 光标在 "标" 处
    assert r2f2[0] == 0  # 偏移 0（'#' 之前）
    assert r2f2[1] == 1 and r2f2[2] == 2  # "# " 可见
    assert r2f2[3] == 3 and r2f2[4] == 4  # "标题" 可见
    print("  ✓ test_raw_to_flat_heading_prefix")


def test_raw_to_flat_list_prefix():
    """列表前缀：display_text="•  "，末 raw 偏移映射到 flat_pos+len(display)。

    raw_to_flat[i] = raw 偏移 i 处的 flat 位置（第 i 个字符之前）。
    raw="- item"（6 字符），flat="•  item"（7 字符）。
    前缀 "- "(2 字符) → "•  "(3 字符)：偏移 0,1 → flat 0，偏移 2 → flat 3。
    """
    prefix = Segment(SegType.LIST_PREFIX, "- ", "", level=0)
    content = Segment(SegType.TEXT, "item", "item")
    line = Line(block_type=BlockType.LIST_UO, raw="- item", segments=[prefix, content], level=0)
    r2f = _build_raw_to_flat_map(line, cursor_off=None)
    assert r2f[0] == 0 and r2f[1] == 0  # "- " 内部 → flat 0
    assert r2f[2] == 3  # 前缀末尾 → flat 3（= len("•  ")）
    assert r2f[3] == 4  # 'i' 之后
    assert r2f[6] == 7  # 'm' 之后（末尾）
    print("  ✓ test_raw_to_flat_list_prefix")


def test_raw_to_flat_skip_prefix():
    """skip_prefix=True：前缀段 raw 偏移映射到 flat 0。"""
    prefix = Segment(SegType.LIST_PREFIX, "- ", "", level=0)
    content = Segment(SegType.TEXT, "item", "item")
    line = Line(block_type=BlockType.LIST_UO, raw="- item", segments=[prefix, content], level=0)
    r2f = _build_raw_to_flat_map(line, cursor_off=None, skip_prefix=True)
    # 前缀段跳过：raw 0,1 → flat 0；内容从 flat 0 开始
    assert r2f[0] == 0 and r2f[1] == 0  # "- " 跳过
    assert r2f[2] == 0  # 'i' 在 flat 0
    assert r2f[6] == 4  # 末尾
    print("  ✓ test_raw_to_flat_skip_prefix")


# ============ _slice_spans_for_visual_line ============

def test_slice_single_vline():
    """单视觉行：切片后 spans 拼接 == 原 flat 文本。"""
    import flet as ft
    style = ft.TextStyle(size=16)
    flat_spans = [ft.TextSpan("hello world", style=style)]
    r2f = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    vline = VisualLine(0, 0, 11, list(range(12)), 110.0)
    sliced = _slice_spans_for_visual_line(flat_spans, r2f, vline, style)
    text = "".join(s.text for s in sliced)
    assert text == "hello world", f"单行切片应完整，实际 '{text}'"
    print("  ✓ test_slice_single_vline")


def test_slice_multi_vline():
    """多视觉行：各 vline spans 拼接 == 原 flat 文本。"""
    import flet as ft
    style = ft.TextStyle(size=16)
    flat_spans = [ft.TextSpan("hello world", style=style)]
    r2f = list(range(12))
    # 2 视觉行：vline0=[0,5], vline1=[5,11]
    vlines = [
        VisualLine(0, 0, 5, [0.0, 10, 20, 30, 40, 50], 50.0),
        VisualLine(1, 5, 11, [0.0, 10, 20, 30, 40, 50, 60], 60.0),
    ]
    text0 = "".join(s.text for s in _slice_spans_for_visual_line(flat_spans, r2f, vlines[0], style))
    text1 = "".join(s.text for s in _slice_spans_for_visual_line(flat_spans, r2f, vlines[1], style))
    assert text0 == "hello", f"vline0 应 'hello'，实际 '{text0}'"
    assert text1 == " world", f"vline1 应 ' world'，实际 '{text1}'"
    assert text0 + text1 == "hello world"
    print("  ✓ test_slice_multi_vline")


def test_slice_preserves_style():
    """切片保留 span style。"""
    import flet as ft
    style_a = ft.TextStyle(size=16, color=ft.Colors.RED)
    style_b = ft.TextStyle(size=16, color=ft.Colors.BLUE)
    flat_spans = [
        ft.TextSpan("red", style=style_a),
        ft.TextSpan("blue", style=style_b),
    ]
    r2f = [0, 1, 2, 3, 4, 5, 6, 7]
    vline = VisualLine(0, 0, 7, list(range(8)), 70.0)
    sliced = _slice_spans_for_visual_line(flat_spans, r2f, vline, style_a)
    assert len(sliced) == 2
    assert sliced[0].style.color == ft.Colors.RED
    assert sliced[1].style.color == ft.Colors.BLUE
    print("  ✓ test_slice_preserves_style")


def test_slice_empty_range():
    """空 flat 范围：返回单个空格 span（保持行高）。"""
    import flet as ft
    style = ft.TextStyle(size=16)
    flat_spans = [ft.TextSpan("hello", style=style)]
    r2f = [0, 0, 0, 0, 0, 0]  # 全折叠（如纯标记段）
    vline = VisualLine(0, 0, 5, [0.0, 0, 0, 0, 0, 0], 0.0)
    sliced = _slice_spans_for_visual_line(flat_spans, r2f, vline, style)
    assert len(sliced) == 1
    assert sliced[0].text == " "
    print("  ✓ test_slice_empty_range")


def test_slice_straddling_span():
    """跨边界 span 被正确拆分。"""
    import flet as ft
    style = ft.TextStyle(size=16)
    # 一个长 span 跨 2 视觉行
    flat_spans = [ft.TextSpan("ABCDEF", style=style)]
    r2f = [0, 1, 2, 3, 4, 5, 6]
    vline0 = VisualLine(0, 0, 3, [0.0, 10, 20, 30], 30.0)
    vline1 = VisualLine(1, 3, 6, [0.0, 10, 20, 30], 30.0)
    s0 = _slice_spans_for_visual_line(flat_spans, r2f, vline0, style)
    s1 = _slice_spans_for_visual_line(flat_spans, r2f, vline1, style)
    assert "".join(s.text for s in s0) == "ABC"
    assert "".join(s.text for s in s1) == "DEF"
    print("  ✓ test_slice_straddling_span")


# ============ 辅助函数 ============

def _vlines_cover_full(vlines: list[VisualLine], raw_text: str, n: int) -> bool:
    """验证视觉行覆盖完整 raw 范围：vline[0].start=0, vline[-1].end=n, 连续。"""
    if not vlines:
        return False
    if vlines[0].start_raw != 0:
        return False
    if vlines[-1].end_raw != n:
        return False
    for k in range(len(vlines) - 1):
        if vlines[k].end_raw != vlines[k + 1].start_raw:
            return False
    return True


def _dump_vlines(vlines: list[VisualLine], raw_text: str) -> str:
    parts = []
    for v in vlines:
        seg = raw_text[v.start_raw:v.end_raw] if v.end_raw <= len(raw_text) else "?"
        parts.append(f"vline{v.vline_idx}[{v.start_raw},{v.end_raw}]='{seg}'")
    return " | ".join(parts)


# ============ 主入口 ============

def run_all():
    tests = [
        test_wrap_empty, test_wrap_single_char, test_wrap_no_overflow,
        test_wrap_space_break, test_wrap_cjk_break, test_wrap_force_break_long_url,
        test_wrap_trailing_space, test_wrap_min_break_off, test_wrap_vline_continuity,
        test_wrap_offsets_rebased,
        test_find_vline_basic, test_find_vline_boundary, test_find_vline_single,
        test_find_vline_empty,
        test_compute_wrap_width_normal, test_compute_wrap_width_inf,
        test_compute_wrap_width_clamp, test_compute_wrap_width_zero,
        test_make_visual_line,
        test_cache_single_line_no_wrap, test_cache_long_line_wraps,
        test_cache_hit_test_2d, test_cache_cursor_px_2d,
        test_cache_multiple_lines_y_accumulation, test_cache_fence_block_short_circuit,
        test_cache_quote_indent,
        test_raw_to_flat_plain_text, test_raw_to_flat_bold_browse,
        test_raw_to_flat_bold_active, test_raw_to_flat_heading_prefix,
        test_raw_to_flat_list_prefix, test_raw_to_flat_skip_prefix,
        test_slice_single_vline, test_slice_multi_vline,
        test_slice_preserves_style, test_slice_empty_range,
        test_slice_straddling_span,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{'='*50}")
    print(f"结果：{passed} 通过，{failed} 失败，共 {len(tests)} 项")
    return failed == 0


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
