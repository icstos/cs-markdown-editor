"""行级渲染小工具（纯函数 / 只读判断，无状态）。

从 views/rendered_line.py 迁出：这些判断与样式选择被行渲染组件复用，
与组件状态无关，独立后可单独推理与测试。
"""

import flet as ft

from models.document import Line, SegType
from styles import FONT_MAIN, _current_colors


def _has_visible_text(line: Line) -> bool:
    """是否有可见内容（文本/前缀/行内格式骨架）。

    空链接 []()、空图片 ![]()、空加粗 ** 等骨架段虽 text 为空，渲染层仍产生
    可见内容（编辑态显示语法标记，浏览态显示 '链接'/'图片' 等占位符），不应被
    误判为空行而只渲染单空格占位。任何非 TEXT 段都是格式段或前缀段，其 raw 骨架
    非空，必有可见渲染。
    """
    for s in line.segments:
        if s.text or s.seg_type != SegType.TEXT:
            return True
    return False


def _has_inline_math(line: Line) -> bool:
    """行内是否含 INLINE_MATH 段（需 LaTeX 渲染）。"""
    return any(s.seg_type == SegType.INLINE_MATH for s in line.segments)


def _image_seg_indices(line: Line) -> list[int]:
    """返回行内 IMAGE 段索引。

    若行内含 IMAGE 以外的非空文本段（混合行），返回空列表——此类行
    仍按普通文本渲染，避免图片与文字混排时布局错乱。
    """
    idxs: list[int] = []
    for i, s in enumerate(line.segments):
        if s.seg_type == SegType.IMAGE:
            idxs.append(i)
        elif s.seg_type == SegType.TEXT and not s.text.strip():
            continue
        else:
            return []
    return idxs


def _line_style(base: int, weight: ft.FontWeight, line_height: float) -> ft.TextStyle:
    """渲染层 Text 基础样式（与 cursor_text_field 的 strut 参数对齐）。"""
    c = _current_colors()
    return ft.TextStyle(
        size=base, weight=weight, color=c.text, font_family=FONT_MAIN, height=line_height
    )


def _line_raw_len(line: Line) -> int:
    """整行 raw 长度。"""
    return len(line.raw) if line.raw else sum(len(s.raw) for s in line.segments)


def _open_link_if_ctrl(e: ft.TapEvent, line: Line, raw_off: int,
                       ctrl_pressed_ref: ft.Ref | None) -> bool:
    """Ctrl+Click 链接段 → 系统浏览器打开。返回是否消费了事件。

    Typora 式交互：普通点击定位光标，Ctrl+Click 打开链接。
    """
    if ctrl_pressed_ref is None or not bool(ctrl_pressed_ref.current):
        return False
    # 定位 raw_off 落在哪个段
    acc = 0
    for seg in line.segments:
        n = len(seg.raw)
        if acc <= raw_off < acc + n or (acc + n == raw_off and seg is line.segments[-1]):
            if seg.seg_type == SegType.LINK and seg.url:
                from views.segment_view import _open_link_url
                _open_link_url(seg.url)
                return True
            return False
        acc += n
    return False
