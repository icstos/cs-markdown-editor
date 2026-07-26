"""光标像素布局：行级 Y 累加 + 行内 X 累加 + 命中测试。

为 Stack 双层架构提供像素坐标计算：
- LineLayoutCache 缓存整文档每行的 Y 坐标和行内 X 坐标数组
- cursor_px(li, off) 返回光标在 Stack 坐标系内的 (x, y, height)
- hit_test(x, y) 将点击坐标反算为 (li, raw_offset)

行高模型：text_height = block_text_size * line_height；普通行 padding 2+2
（与 _wrap_block 的 top=2/bottom=2 一致）。
行内 X：逐段用 measure_text_offsets（HarfBuzz cluster 级整形，与 Skia 同引擎）
累加。光标不在段内时标记折叠（零宽度），仅 display_text 内容占像素宽度；
光标在段内时标记变灰可见并占宽度。

依赖项：
- models：BlockType / Line / SegType / Segment
- styles：FONT_MAIN / FONT_MONO / block_text_size
- utils.segment_helpers：MONO_SEGTYPES / PREFIX_SEGTYPES / display_text /
  split_seg_for_display（段类型常量与显示拆分）
- utils.text_layout：measure_text_offsets（cluster 级光标偏移）/ measure_text_width（文本像素宽度）
"""

from __future__ import annotations

from dataclasses import dataclass

from models import BlockType, Line, SegType, Segment
from styles import FONT_MAIN, FONT_MONO, block_text_size
from utils.segment_helpers import (
    MONO_SEGTYPES,
    PREFIX_SEGTYPES,
    display_text,
    split_seg_for_display,
)
from utils.text_layout import measure_text_offsets, measure_text_width

# 普通行垂直 padding（_wrap_block 的 top=2, bottom=2）
_PAD_V = 2.0
# 列表每级缩进（与 _wrap_block 的 level * 20 一致）
_LIST_INDENT = 20
# 引用每层缩进（与 _wrap_block 每层 left=12 一致）
_QUOTE_INDENT = 12


@dataclass
class LineLayout:
    """单行像素布局结果。"""

    li: int
    top: float  # 行顶 Y（含 padding，整文档坐标）
    height: float  # 行总高（text_height + pad_top + pad_bottom）
    text_top: float  # 文字区顶 Y（top + pad_top）
    text_height: float  # 文字行高 = base * line_height
    base_size: int
    left_pad: float  # 块级缩进（列表/引用），文字左起点偏移
    raw_offsets_x: list[float]  # raw 偏移 0..len(raw) 的 X 坐标（相对文字左起点）


class LineLayoutCache:
    """整文档像素布局缓存（每次渲染重建）。

    缓存每行 top/height/raw_offsets_x，供 cursor_field 定位 (left, top) 与
    hit_test 反算 (x, y) → (li, raw_offset)。

    非激活行所有标记折叠（cursor_raw_offset=None）；激活行单独计算 offsets。
    """

    def __init__(self, lines: list[Line], content_width: float, line_height: float = 1.6):
        self._layouts: list[LineLayout] = []
        self._total_height: float = 0.0
        self._line_height = line_height
        self._content_width = content_width
        self._build(lines)

    def get(self, li: int) -> LineLayout | None:
        if 0 <= li < len(self._layouts):
            return self._layouts[li]
        return None

    def cursor_px(self, li: int, raw_off: int) -> tuple[float, float, float]:
        """返回 (x, y_in_stack, height)：光标在 Stack 坐标系内的像素位置。

        Stack 内 top=0 = 文字区顶部（Stack 高度 = text_height，与文字同顶点），
        所以 y_in_stack 恒为 0。x = raw_offsets_x[off]（相对文字左起点）。
        """
        layout = self.get(li)
        if layout is None:
            return (0.0, 0.0, 0.0)
        n = len(layout.raw_offsets_x)
        off = max(0, min(raw_off, n - 1))
        return (layout.raw_offsets_x[off], 0.0, layout.text_height)

    def hit_test(self, x: float, y: float) -> tuple[int, int] | None:
        """整文档命中：返回 (li, raw_offset) | None。

        先用 y 二分定位行，再用 x - left_pad 定位行内 raw_offset。
        """
        if not self._layouts:
            return None
        first = self._layouts[0]
        if y < first.top:
            return (first.li, 0)
        last = self._layouts[-1]
        if y >= last.top + last.height:
            return (last.li, len(last.raw_offsets_x) - 1)
        # 二分查找 y 落入哪一行
        lo, hi = 0, len(self._layouts) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            layout = self._layouts[mid]
            if y < layout.top:
                hi = mid - 1
            elif y >= layout.top + layout.height:
                lo = mid + 1
            else:
                lo = mid
                break
        layout = self._layouts[lo]
        local_x = x - layout.left_pad
        off = hit_test_line_x_raw(layout.raw_offsets_x, local_x)
        return (layout.li, off)

    @property
    def total_height(self) -> float:
        return self._total_height

    def _build(self, lines: list[Line]) -> None:
        """逐行累加 Y + 逐段累加 X（非激活行：标记折叠）。"""
        y_acc = 0.0
        for li, line in enumerate(lines):
            base = block_text_size(line.block_type, line.level)
            text_h = base * self._line_height
            pad_top, pad_bottom, left_pad = _block_padding(line)
            total_h = text_h + pad_top + pad_bottom
            offsets_x = _line_raw_offsets_x(line, base, cursor_raw_offset=None)
            self._layouts.append(
                LineLayout(
                    li=li,
                    top=y_acc,
                    height=total_h,
                    text_top=y_acc + pad_top,
                    text_height=text_h,
                    base_size=base,
                    left_pad=left_pad,
                    raw_offsets_x=offsets_x,
                )
            )
            y_acc += total_h
        self._total_height = y_acc


def _block_padding(line: Line) -> tuple[float, float, float]:
    """返回 (pad_top, pad_bottom, left_pad)，与 _wrap_block 一致。"""
    if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O):
        return (_PAD_V, _PAD_V, (line.level or 0) * _LIST_INDENT)
    if line.block_type == BlockType.QUOTE:
        return (_PAD_V, _PAD_V, (line.level or 1) * _QUOTE_INDENT)
    if line.block_type == BlockType.HR:
        return (8.0, 8.0, 0.0)
    # 围栏岛屿（CODE/TABLE/MATH/TOC）：pixel_layout 用估算占位高度，
    # 实际高度由原生编辑器决定。hit_test 主要服务普通行，围栏块点击由原生控件处理。
    return (_PAD_V, _PAD_V, 0.0)


def _seg_font_metrics(seg: Segment, base: int) -> tuple[str, int]:
    """段对应的 (font_family, size)，与 segment_view.segment_style 一致。

    codespan/inline_math 用 FONT_MONO + base-1；其余用 FONT_MAIN + base。
    weight 不影响 advance（Pillow getlength 不考虑 weight），与 Skia 基本一致。
    """
    if seg.seg_type in MONO_SEGTYPES:
        return (FONT_MONO, max(base - 1, 12))
    return (FONT_MAIN, base)


def _line_raw_offsets_x(
    line: Line, base: int, cursor_raw_offset: int | None = None
) -> list[float]:
    """返回长度 = len(line.raw)+1 的 X 坐标数组。

    raw_offsets_x[i] = 光标在 raw 偏移 i 处的 X（相对文字左起点）。

    - cursor_raw_offset=None（浏览态）：所有段标记折叠，仅 display_text 占宽度。
      标题 # /引用 > 前缀零宽度；无序列表 - 渲染为 • 占宽度；行内 ** ` 等标记零宽度。
    - cursor_raw_offset=int（激活行）：光标所在段用 measure_text_offsets 做
      cluster 级整形测量 raw（含标记宽度，捕获 kerning），其余段标记折叠。
      保证光标 X 与渲染层 TextSpan 像素级对齐（HarfBuzz 与 Skia 同引擎）。
    """
    offsets: list[float] = [0.0]
    acc = 0.0
    raw_offset = 0
    seg_count = len(line.segments)

    for seg_idx, seg in enumerate(line.segments):
        seg_start = raw_offset
        seg_end = raw_offset + len(seg.raw)
        is_last = seg_idx == seg_count - 1

        if cursor_raw_offset is None:
            cursor_in_seg = False
        elif is_last:
            cursor_in_seg = seg_start <= cursor_raw_offset <= seg_end
        else:
            cursor_in_seg = seg_start <= cursor_raw_offset < seg_end

        font, size = _seg_font_metrics(seg, base)
        seg_raw_len = len(seg.raw)
        is_prefix = seg.seg_type in PREFIX_SEGTYPES

        if cursor_in_seg:
            # 光标在段内：cluster 级整形 raw（含标记，标记变灰可见占宽度）。
            # 用 measure_text_offsets 而非逐字符累加：捕获字符间 kerning，
            # 与 Skia 渲染层 TextSpan 像素级对齐（修复 AV/ID: 等含 kerning 文本偏移）。
            seg_offsets = measure_text_offsets(seg.raw, font, size)
            seg_start_x = acc
            for i in range(1, len(seg_offsets)):
                offsets.append(seg_start_x + seg_offsets[i])
            acc = seg_start_x + seg_offsets[-1]
        elif is_prefix:
            # 前缀段（光标不在段内）
            # HEADING_PREFIX 例外：光标在本行时 # 前缀可见占宽度（与渲染层一致）
            if (
                seg.seg_type == SegType.HEADING_PREFIX
                and cursor_raw_offset is not None
            ):
                # 光标在本行：# 前缀占实际宽度（cluster 级整形测量）
                seg_offsets = measure_text_offsets(seg.raw, font, size)
                seg_start_x = acc
                for i in range(1, len(seg_offsets)):
                    offsets.append(seg_start_x + seg_offsets[i])
                acc = seg_start_x + seg_offsets[-1]
            else:
                # 浏览态或非标题前缀：display_text 宽度（•  / N. / 空）
                display = display_text(seg)
                display_w = measure_text_width(display, font, size) if display else 0.0
                seg_start_x = acc
                for i in range(seg_raw_len):
                    if i == seg_raw_len - 1:
                        acc = seg_start_x + display_w
                    offsets.append(acc)
        else:
            # 行内段（光标不在段内）：逐 piece 测量，标记零宽度、内容 cluster 级整形
            pieces = split_seg_for_display(seg)
            for text, is_marker in pieces:
                if not text:
                    continue
                if is_marker:
                    for _ in text:
                        offsets.append(acc)
                else:
                    piece_offsets = measure_text_offsets(text, font, size)
                    piece_start_x = acc
                    for i in range(1, len(piece_offsets)):
                        offsets.append(piece_start_x + piece_offsets[i])
                    acc = piece_start_x + piece_offsets[-1]

        raw_offset = seg_end

    # 兜底：segments 拼接 != line.raw（围栏块 CODE/MATH 无围栏标记）
    if len(offsets) - 1 != len(line.raw):
        offsets = measure_text_offsets(line.raw, FONT_MAIN, base)
    return offsets


def hit_test_line_x_raw(raw_offsets_x: list[float], x: float) -> int:
    """X 命中：返回 raw_offset。中点吸附 + 折叠标记扫描。

    标记折叠时多个 raw_offset 映射到同一 X（零宽度），命中后向前扫描到
    最后一个同 X 偏移（即内容起点），使点击落在内容首字符而非折叠标记内。
    """
    if not raw_offsets_x:
        return 0
    if x <= 0:
        return _scan_forward(raw_offsets_x, 0)
    if x >= raw_offsets_x[-1]:
        return len(raw_offsets_x) - 1
    # 二分查找 x 落入的区间 [i-1, i)
    lo, hi = 0, len(raw_offsets_x) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if raw_offsets_x[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    # 中点吸附：x 在 [offsets[lo-1], offsets[lo]) 内，按中点决定 lo-1 或 lo
    if lo > 0 and x < (raw_offsets_x[lo - 1] + raw_offsets_x[lo]) / 2:
        lo = lo - 1
    # 向前扫描跳过折叠标记（同一 X 的连续偏移取最后一个 = 内容起点）
    return _scan_forward(raw_offsets_x, lo)


def _scan_forward(raw_offsets_x: list[float], idx: int) -> int:
    """向前扫描：跳过折叠标记（同一 X 的连续偏移取最后一个）。"""
    while idx < len(raw_offsets_x) - 1 and raw_offsets_x[idx + 1] == raw_offsets_x[idx]:
        idx += 1
    return idx


def hit_test_line_x(line: Line, x: float, base: int, cursor_raw_offset: int | None = None) -> int:
    """单行 X 命中：返回 raw_offset。"""
    offsets = _line_raw_offsets_x(line, base, cursor_raw_offset)
    return hit_test_line_x_raw(offsets, x)
