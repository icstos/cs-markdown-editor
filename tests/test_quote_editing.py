"""引用块编辑支持测试（修复：行首 > 创建引用光标消失 / 引用块无法点击编辑）。

覆盖：
- parser：引用前缀段 raw 与源码一致（">x"、">> x"、"> > x" 保持
  line.raw == "".join(segments.raw) 不变量，避免段/行长度错位）
- 打字创建引用（handle_char_input）：
  · ">" → 行变 QUOTE，前缀结构变化 → 结束会话 + nav_seq 递增（重建+重聚焦，
    修复光标消失）
  · 继续输入 " " / "a" 光标落在内容起点 / 字符后（前缀折叠，无 X 漂移）
- 前缀边界：光标在引用前缀末尾（内容起点）时前缀折叠（cursor_px=0），
  不再偏右一个前缀宽度
- 点击命中：浏览态引用行点击内容起点映射到 raw_off=2（跳过前缀）
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import BlockType, Document, Line, Segment, SegType
from parser.block import parse_markdown
from views.editor._cursor import build_cursor
from views.pixel_layout import (
    _find_vline_for_raw,
    _line_visual_layout,
    hit_test_line_x_raw,
)


class FakeRef:
    def __init__(self, current=None):
        self.current = current


class _Cursor:
    def __init__(self, b):
        self.base = b
        self.extent = b

    def reset(self, off, raw_len):
        self.base = off
        self.extent = off


def _make_ctx(doc, cursor_li, base):
    calls = []
    ctx = types.SimpleNamespace(
        document=doc, cursor_li=cursor_li, cursor_off=base, nav_seq=0,
        cursor_ref=FakeRef(_Cursor(base)),
        input_session_ref=FakeRef({'li': -1, 'start_off': -1, 'last_value': ''}),
        outward_sel_ref=FakeRef(None), preferred_col_ref=FakeRef(None),
        cursor_pulse_ref=FakeRef(0.0), suppress_blur=FakeRef(False),
        push_line_edit=lambda li, raw: calls.append(('push_line_edit', li, raw)),
        mark_dirty=lambda: calls.append('mark_dirty'),
        set_cursor_field_value=lambda v: calls.append(('set_cursor_field_value', v)),
        set_cursor_off=lambda off: (calls.append(('set_cursor_off', off)),
                                    setattr(ctx, 'cursor_off', off)),
        set_cursor_li=lambda li: calls.append(('set_cursor_li', li)),
        set_cursor_line=lambda li: calls.append(('set_cursor_line', li)),
        set_nav_seq=lambda n: calls.append(('set_nav_seq', n)),
        push_history=lambda: calls.append('push_history'),
        undo_push_pending=FakeRef(True),
        secondary_cursors_ref=FakeRef([]), broadcast_char_input=lambda a, b: None,
        broadcast_submit=lambda v: None, paste_in_progress_ref=FakeRef(False),
    )
    return ctx, calls


def _cursor_px(line, off):
    """激活行 cursor_off 的光标像素 X（前缀折叠后应为内容起点 0）。"""
    vlines = _line_visual_layout(line, 16, 10000.0, cursor_raw_offset=off,
                                 line_height=1.6)
    vline = next((v for v in vlines if v.start_raw <= off <= v.end_raw), vlines[0])
    local = max(0, min(off - vline.start_raw, len(vline.offsets_x) - 1))
    return vline.offsets_x[local]


def _empty_para() -> Line:
    return Line(block_type=BlockType.PARAGRAPH, raw='',
                segments=[Segment(SegType.TEXT, '', '')])


# ---------------- parser：前缀与源码一致 ----------------

def test_quote_prefix_preserves_source_invariant():
    """无空格/嵌套写法前缀段 raw 与源码一致（join == raw 不变量）。"""
    for raw in ('>', '>x', '> x', '>> x', '> > x'):
        line = parse_markdown(raw).lines[0]
        assert line.block_type == BlockType.QUOTE
        assert ''.join(s.raw for s in line.segments) == line.raw, raw
    # 多级层级
    assert parse_markdown('>> x').lines[0].level == 2
    assert parse_markdown('> > x').lines[0].level == 2


# ---------------- 打字创建引用：光标不消失 + 无 X 漂移 ----------------

def test_type_quote_creates_block_and_refocuses():
    """输入 ">" 创建引用：前缀结构变化 → 结束会话 + nav_seq 递增（重建重聚焦）。"""
    doc = Document(lines=[_empty_para()])
    ctx, calls = _make_ctx(doc, 0, 0)
    hc = build_cursor(ctx)['handle_char_input']

    hc('>')

    line = doc.lines[0]
    assert line.block_type == BlockType.QUOTE
    assert line.segments[0].seg_type == SegType.QUOTE_PREFIX
    # 会话已结束（last_value 清空，避免前缀混入 value 导致光标 X 漂移）
    assert ctx.input_session_ref.current['last_value'] == ''
    # nav_seq 递增 → TextField 重建 + use_effect 重聚焦（光标不消失）
    assert ('set_nav_seq', 1) in calls
    assert ctx.suppress_blur.current is True
    # 光标在内容起点（前缀折叠，px=0，不偏右）
    assert ctx.cursor_ref.current.base == 1
    assert _cursor_px(line, 1) == 0.0


def test_type_quote_content_cursor_lands_correctly():
    """> + 空格 + 内容：光标始终落在内容起点/字符后（无 13px 前缀漂移）。"""
    doc = Document(lines=[_empty_para()])
    ctx, _calls = _make_ctx(doc, 0, 0)
    hc = build_cursor(ctx)['handle_char_input']

    hc('>')
    hc(' ')   # 会话已结束 → 字段空 → 值仅新字符
    line = doc.lines[0]
    assert line.raw == '> '
    assert line.block_type == BlockType.QUOTE
    # 空引用：光标在内容起点（px=0，前缀折叠）
    assert ctx.cursor_ref.current.base == 2
    assert _cursor_px(line, 2) == 0.0

    hc('a')
    line = doc.lines[0]
    assert line.raw == '> a'
    assert ctx.cursor_ref.current.base == 3
    # 光标在字符后，前缀仍折叠（px = 'a' 宽度，而非 '> a' 宽度）
    px = _cursor_px(line, 3)
    assert px > 0 and px < 20  # 约 10px（'a' 宽度），非 23px（'> a' 宽度）


def test_click_quote_content_start_maps_to_prefix_end():
    """浏览态点击引用内容起点 → raw_off=2（跳过前缀，可正常定位编辑）。"""
    line = Line(block_type=BlockType.QUOTE, raw='> hello', level=1)
    line.segments = [Segment(SegType.QUOTE_PREFIX, '> ', ''),
                     Segment(SegType.TEXT, 'hello', 'hello')]
    vlines = _line_visual_layout(line, 16, 10000.0, cursor_raw_offset=None,
                                 line_height=1.6)
    offsets = vlines[0].offsets_x
    # 浏览态前缀零宽度：点击内容起点落在 raw 2（'> ' 之后）
    assert offsets[0] == 0.0 and offsets[2] == 0.0
    assert hit_test_line_x_raw(offsets, 0.0) == 2
    # 点击首字符中段 → 内容偏移
    assert hit_test_line_x_raw(offsets, 20.0) == 4


def test_active_quote_prefix_folded_at_content_start():
    """激活态光标在内容起点：前缀折叠（cursor_px=0），修复点击后 caret 偏右。"""
    line = Line(block_type=BlockType.QUOTE, raw='> hello', level=1)
    line.segments = [Segment(SegType.QUOTE_PREFIX, '> ', ''),
                     Segment(SegType.TEXT, 'hello', 'hello')]
    vlines = _line_visual_layout(line, 16, 10000.0, cursor_raw_offset=2,
                                 line_height=1.6)
    vline = _find_vline_for_raw(vlines, 2)
    # 前缀段边界（seg_end=2）折叠：offsets[2] == 0 == 内容起点
    assert vline.offsets_x[2] == 0.0
