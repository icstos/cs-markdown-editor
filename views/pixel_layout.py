"""光标像素布局：行级 Y 累加 + 行内 X 累加 + 命中测试（2D 软换行）。

为 Stack 双层架构提供像素坐标计算：
- LineLayoutCache 缓存整文档每行的 Y 坐标和视觉行列表（支持软换行）
- cursor_px(li, off) 返回光标在 Stack 坐标系内的 (x, y, height)，
  y = vline_idx * text_height（非零，2D 定位）
- hit_test(x, y) 将点击坐标反算为 (li, raw_offset)：Y 二分定逻辑行 →
  vline_idx 定视觉行 → 行内 X 命中

行高模型（变量高度）：height = num_vlines * text_height + pad_top + pad_bottom；
text_height = block_text_size * line_height（单视觉行高）。
普通行 padding 2+2（与 _wrap_block 的 top=2/bottom=2 一致）。

软换行：_line_visual_layout 复用 _line_raw_offsets_x（1D，含标记折叠/kerning/
逐段字体），再调用 _wrap_offsets_into_visual_lines 按 wrap_width 切成 N 个
VisualLine。渲染层与光标测量共用同一换行函数，换行点天然一致。
围栏岛屿（CODE/MATH/HR/TOC/TABLE）不参与换行，占位单 vline。

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
from utils.text_layout import _CJK_RE, measure_text_offsets, measure_text_width

# 普通行垂直 padding（_wrap_block 的 top=2, bottom=2）
_PAD_V = 2.0
# 列表每级缩进（与 _wrap_block 的 level * 20 一致）
_LIST_INDENT = 20
# 引用每层缩进（与 _wrap_block 每层 left=12 一致）
_QUOTE_INDENT = 12
# 围栏岛屿块类型（不参与软换行，走原生控件自管理布局）
_FENCE_BLOCK_TYPES = (BlockType.CODE, BlockType.MATH, BlockType.HR, BlockType.TOC, BlockType.TABLE)


@dataclass
class LineLayout:
    """单行像素布局结果（支持软换行：N 视觉行）。

    height 为变量高度：num_vlines * text_height + pad_top + pad_bottom。
    visual_lines 至少 1 个；围栏块占位单 vline（end_raw=0, offsets_x=[0.0]）。
    """

    li: int
    top: float  # 行顶 Y（含 padding，整文档坐标）
    height: float  # 行总高 = num_vlines * text_height + pad_top + pad_bottom
    text_top: float  # 文字区顶 Y（top + pad_top）
    text_height: float  # 单视觉行高 = base * line_height
    base_size: int
    left_pad: float  # 块级缩进（列表/引用），文字左起点偏移
    wrap_width: float  # 可用文本宽度（content_width - left_pad）
    visual_lines: list[VisualLine]  # 视觉行列表（>=1）
    num_vlines: int  # 视觉行数（= len(visual_lines)，冗余存便于 O(1) 访问）


class LineLayoutCache:
    """整文档像素布局缓存（每次渲染重建，支持软换行 2D 布局）。

    缓存每行 top/height/visual_lines，供 cursor_field 定位 (left, top) 与
    hit_test 反算 (x, y) → (li, raw_offset)。

    非激活行所有标记折叠（cursor_raw_offset=None）；激活行由 _cursor_overlay
    单独计算（line_view.py）。content_width 现用于计算 wrap_width 实现软换行。
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

        2D 定位：y_in_stack = vline_idx * text_height（非零，支持软换行）。
        x = vline.offsets_x[local_off]（相对文字左起点，局部于视觉行）。
        """
        layout = self.get(li)
        if layout is None:
            return (0.0, 0.0, 0.0)
        vline = _find_vline_for_raw(layout.visual_lines, raw_off)
        if vline is None:
            return (0.0, 0.0, layout.text_height)
        local_off = raw_off - vline.start_raw
        local_off = max(0, min(local_off, len(vline.offsets_x) - 1))
        x = vline.offsets_x[local_off]
        y = vline.vline_idx * layout.text_height
        return (x, y, layout.text_height)

    def hit_test(self, x: float, y: float) -> tuple[int, int] | None:
        """整文档命中：返回 (li, raw_offset) | None。

        2D 命中：Y 二分定逻辑行 → vline_idx = int((y - text_top) // text_h)
        定视觉行 → hit_test_line_x_raw(vline.offsets_x, local_x) →
        raw_off = vline.start_raw + local_off。
        """
        if not self._layouts:
            return None
        first = self._layouts[0]
        if y < first.top:
            return (first.li, 0)
        last = self._layouts[-1]
        if y >= last.top + last.height:
            return (last.li, _layout_last_raw_off(last))
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
        # 定视觉行：Y 相对文字区顶部，按单视觉行高整除
        local_y = y - layout.text_top
        text_h = layout.text_height
        if text_h <= 0 or layout.num_vlines <= 1:
            vline_idx = 0
        else:
            vline_idx = max(0, min(int(local_y // text_h), layout.num_vlines - 1))
        vline = layout.visual_lines[vline_idx]
        local_off = hit_test_line_x_raw(vline.offsets_x, local_x)
        raw_off = vline.start_raw + local_off
        return (layout.li, raw_off)

    @property
    def total_height(self) -> float:
        return self._total_height

    def _build(self, lines: list[Line]) -> None:
        """逐行累加 Y + 计算视觉行布局（浏览态：标记折叠）。

        变量高度：num_vlines * text_h + pad_top + pad_bottom。
        围栏块短路：占位单 vline，高度仍按 text_h + padding 估算
        （围栏块实际高度由原生控件决定，hit_test 走原生控件，估算仅用于 Y 累加）。
        """
        y_acc = 0.0
        cw = self._content_width
        for li, line in enumerate(lines):
            base = block_text_size(line.block_type, line.level)
            text_h = base * self._line_height
            pad_top, pad_bottom, left_pad = _block_padding(line)
            if line.block_type in _FENCE_BLOCK_TYPES:
                # 围栏岛屿：不参与换行，占位单 vline
                visual_lines = [VisualLine(0, 0, 0, [0.0], 0.0)]
                num_vlines = 1
            else:
                wrap_width = _compute_wrap_width(cw, left_pad)
                visual_lines = _line_visual_layout(
                    line, base, wrap_width,
                    cursor_raw_offset=None,  # 浏览态：所有标记折叠
                    line_height=self._line_height,
                )
                num_vlines = len(visual_lines)
            total_h = num_vlines * text_h + pad_top + pad_bottom
            self._layouts.append(
                LineLayout(
                    li=li,
                    top=y_acc,
                    height=total_h,
                    text_top=y_acc + pad_top,
                    text_height=text_h,
                    base_size=base,
                    left_pad=left_pad,
                    wrap_width=_compute_wrap_width(cw, left_pad),
                    visual_lines=visual_lines,
                    num_vlines=num_vlines,
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


def _compute_wrap_width(content_width: float, left_pad: float) -> float:
    """计算可用文本宽度：content_width - left_pad，保底 50px，无限宽时不换行。

    content_width 可能为 float("inf")（未受限布局）→ 返回 inf（不换行）。
    负值或过小值钳制到 50（避免换行算法退化）。
    """
    if content_width == float("inf") or content_width <= 0:
        return float("inf")
    w = content_width - left_pad
    if w < 50.0:
        return 50.0
    return w


def _layout_last_raw_off(layout: LineLayout) -> int:
    """返回 layout 末视觉行的 end_raw（行最大合法 raw 偏移）。"""
    if not layout.visual_lines:
        return 0
    return layout.visual_lines[-1].end_raw


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


# ---------------------------------------------------------------------------
# 软换行（2D 视觉行布局）
# ---------------------------------------------------------------------------
@dataclass
class VisualLine:
    """一行逻辑行被 wrap_width 切出的单个视觉行。"""

    vline_idx: int  # 0-based，逻辑行内序号
    start_raw: int  # 起始 raw 偏移（含）；vline[k].end_raw == vline[k+1].start_raw
    end_raw: int  # 结束 raw 偏移（含）
    offsets_x: list[float]  # 行内 X，len = end_raw-start_raw+1，[0]=0.0
    width: float  # 行内最大 X（= offsets_x[-1]）


def _make_visual_line(vline_idx: int, start: int, end: int, offsets_x: list[float]) -> VisualLine:
    """从 1D offsets_x 截取 [start, end] 并 rebase 到 0。"""
    sub = [offsets_x[j] - offsets_x[start] for j in range(start, end + 1)]
    return VisualLine(vline_idx, start, end, sub, sub[-1] if sub else 0.0)


def _line_visual_layout(
    line: Line,
    base: int,
    wrap_width: float,
    cursor_raw_offset: int | None = None,
    line_height: float = 1.6,
    *,
    _precomputed_offsets: list[float] | None = None,
) -> list[VisualLine]:
    """2D 布局：按 wrap_width 把一行切成 N 个视觉行。

    复用 _line_raw_offsets_x（1D，已含标记折叠/kerning/逐段字体），再做换行切分。
    wrap_width = 可用文本宽度（已扣除块级 left_pad）。
    cursor_raw_offset=None 浏览态（标记全折叠）；int 激活行（光标段标记可见占宽）。
    返回 >=1 个 VisualLine（空行为单 vline）。

    性能优化：_precomputed_offsets 允许调用方传入已计算的 offsets_x（来自
    _line_raw_offsets_x），避免激活行在 RenderedLine / _cursor_overlay / hit_test
    三处重复调用 _line_raw_offsets_x（内含 HarfBuzz 整形测量）。传入时跳过内部
    _line_raw_offsets_x 调用，直接用预计算结果做换行切分。
    """
    if _precomputed_offsets is not None:
        offsets_x = _precomputed_offsets
    else:
        offsets_x = _line_raw_offsets_x(line, base, cursor_raw_offset)
    raw_text = line.raw if line.raw else "".join(s.raw for s in line.segments)
    # 前缀段（#/•/>）不断行，整段留在 vline 0
    min_break_off = 0
    for seg in line.segments:
        if seg.seg_type in PREFIX_SEGTYPES:
            min_break_off += len(seg.raw)
        else:
            break
    n = len(offsets_x) - 1
    if wrap_width <= 0 or wrap_width == float("inf") or n <= 0:
        return [VisualLine(0, 0, n, list(offsets_x), offsets_x[-1] if offsets_x else 0.0)]
    return _wrap_offsets_into_visual_lines(offsets_x, raw_text, wrap_width, min_break_off)


def _wrap_offsets_into_visual_lines(
    offsets_x: list[float],
    raw_text: str,
    wrap_width: float,
    min_break_off: int = 0,
) -> list[VisualLine]:
    """纯换行算法：把 1D offsets 切成 N 视觉行（渲染与光标共用，保证对齐）。

    断行规则（确定性，渲染层与光标测量调用同一函数）：
    - ASCII 空格/tab 后可断（空格留在当前行，trailing space 不触发提前断行）
    - CJK 字符前可断（每字可断，模拟 Skia/Flutter CJK 换行行为）
    - 超长不可断词（长 URL/代码）强制在当前 offset 断（字符级 force-break）
    - min_break_off 之前不断（前缀段整段留在 vline 0）

    vline[k].end_raw == vline[k+1].start_raw（连续），边界 raw_off 归属靠前行
    （光标在行尾渲染于右边缘，由 _find_vline_for_raw 处理）。
    """
    n = len(offsets_x) - 1  # 字符数
    if n <= 0:
        return [VisualLine(0, 0, 0, [0.0], 0.0)]
    vlines: list[VisualLine] = []
    vline_start = 0
    last_break = 0
    i = 1
    while i <= n:
        # 先判溢出（用上一次的 last_break，不含当前导致溢出的 char）
        advance = offsets_x[i] - offsets_x[vline_start]
        if advance > wrap_width:
            break_at = last_break if last_break > vline_start else i
            vlines.append(_make_visual_line(len(vlines), vline_start, break_at, offsets_x))
            vline_start = break_at
            last_break = break_at
            if break_at == i:
                i += 1  # 强制断在 i：char i 起始新行，trivially fits
            continue  # break_at < i 时不推进 i，从新 vline_start 重新评估
        # 未溢出：更新断行点
        if i >= min_break_off:
            if i - 1 < n and raw_text[i - 1] in " \t":
                last_break = i  # 空格后断：空格留当前行
            elif i < n and _CJK_RE.match(raw_text[i]):
                last_break = i  # CJK 前断：CJK 字符去下一行
        i += 1
    # 末行
    if not vlines or vline_start < n:
        vlines.append(_make_visual_line(len(vlines), vline_start, n, offsets_x))
    return vlines


def _find_vline_for_raw(visual_lines: list[VisualLine], raw_off: int) -> VisualLine | None:
    """二分查找 raw_off 所在视觉行。

    边界归属：raw_off == vline[k+1].start_raw（== vline[k].end_raw）时归 vline k
    （光标在行尾右边缘渲染），仅 raw_off==0 归 vline 0。
    """
    if not visual_lines:
        return None
    lo, hi = 0, len(visual_lines) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2  # 上 mid，找最后一个 start_raw <= raw_off
        if visual_lines[mid].start_raw <= raw_off:
            lo = mid
        else:
            hi = mid - 1
    v = visual_lines[lo]
    if v.vline_idx > 0 and v.start_raw == raw_off:
        return visual_lines[v.vline_idx - 1]
    return v
