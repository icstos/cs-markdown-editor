"""导航工厂（从 views/editor.py 闭包抽取）。

闭包组：move_left / move_right / move_home / move_end / move_doc_start /
move_doc_end / _get_line_visual_lines / _cursor_vline_info / _move_vline /
move_up / move_down

跨组依赖（通过 ctx 装配槽，调用时读取）：
- cursor_base / set_cursor / ensure_visible（cursor / scroll 组）
- layout_cache_ref / preferred_col_ref（共享 ref）
- cursor_li / cursor_off（state 快照）
- line_height / content_width（派生设置）

同组内部直接调用（不经过 ctx）：
- _get_line_visual_lines / _cursor_vline_info / _move_vline

依赖项：
- models（SegType）
- styles（block_text_size）
- utils.segment_helpers（is_fence / line_raw）
- views._editor_helpers（_vline_off_at_x）
- views.pixel_layout（_block_padding / _compute_wrap_width /
  _line_visual_layout / _find_vline_for_raw —— 函数内惰性导入，避免循环依赖）
"""

from models import SegType
from styles import block_text_size
from utils.segment_helpers import is_fence as _is_fence
from utils.segment_helpers import line_raw as _line_raw
from views._editor_helpers import _vline_off_at_x


def build_navigation(ctx):
    """构造导航闭包组。

    返回 dict[str, Callable]：
    move_left / move_right / move_home / move_end / move_doc_start /
    move_doc_end / move_up / move_down / cursor_vline_info / get_line_visual_lines
    """

    def move_left():
        """← ：光标左移；行首则跳上一行行尾。"""
        if ctx.cursor_li is None:
            return
        li = ctx.cursor_li
        if not (0 <= li < len(ctx.document.lines)):
            return
        if _is_fence(ctx.document.lines[li]):
            return
        off = ctx.cursor_base(len(_line_raw(ctx.document.lines[li])))
        if off > 0:
            ctx.set_cursor(li, off - 1)
        elif li > 0:
            prev = ctx.document.lines[li - 1]
            if _is_fence(prev):
                return
            ctx.set_cursor(li - 1, len(_line_raw(prev)))
            ctx.ensure_visible(li - 1)

    def move_right():
        """→ ：光标右移；行尾则跳下一行行首。"""
        if ctx.cursor_li is None:
            return
        li = ctx.cursor_li
        if not (0 <= li < len(ctx.document.lines)):
            return
        if _is_fence(ctx.document.lines[li]):
            return
        raw = _line_raw(ctx.document.lines[li])
        off = ctx.cursor_base(len(raw))
        if off < len(raw):
            ctx.set_cursor(li, off + 1)
        elif li < len(ctx.document.lines) - 1:
            nxt = ctx.document.lines[li + 1]
            if _is_fence(nxt):
                return
            ctx.set_cursor(li + 1, 0)
            ctx.ensure_visible(li + 1)

    def move_home():
        """Smart Home（VSCode 式）：先跳内容首（跳过前缀），再跳行首（raw 0）。

        - 光标在内容中 → 跳到内容首（# / - / > 等前缀之后）
        - 光标在内容首 → 跳到行首（raw 0，前缀之前）
        - 光标在行首 → 不动
        """
        if ctx.cursor_li is None:
            return
        if _is_fence(ctx.document.lines[ctx.cursor_li]):
            return
        line = ctx.document.lines[ctx.cursor_li]
        # 计算 content_start：跳过 HEADING_PREFIX / LIST_PREFIX / QUOTE_PREFIX 段 0
        content_start = 0
        if line.segments and line.segments[0].seg_type in (
            SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX,
        ):
            content_start = len(line.segments[0].raw)
        raw_len = len(_line_raw(line))
        content_start = min(content_start, raw_len)
        # Smart Home 三态判定（用 IME 实时光标位置）
        off = ctx.cursor_base(raw_len)
        if off == 0:
            pass  # 已在行首
        elif off == content_start:
            ctx.set_cursor(ctx.cursor_li, 0)
        else:
            ctx.set_cursor(ctx.cursor_li, content_start)
        ctx.ensure_visible(ctx.cursor_li)

    def move_end():
        """End：跳到行尾。"""
        if ctx.cursor_li is None:
            return
        if not (0 <= ctx.cursor_li < len(ctx.document.lines)):
            return
        if _is_fence(ctx.document.lines[ctx.cursor_li]):
            return
        ctx.set_cursor(ctx.cursor_li, len(_line_raw(ctx.document.lines[ctx.cursor_li])))
        ctx.ensure_visible(ctx.cursor_li)

    def move_doc_start():
        """Ctrl+Home：跳到文档首行行首。"""
        if not ctx.document.lines:
            return
        li = 0
        if _is_fence(ctx.document.lines[li]):
            ctx.set_cursor_line(li)
            ctx.set_cursor_li(None)
            return
        ctx.set_cursor(li, 0)
        ctx.ensure_visible(li)

    def move_doc_end():
        """Ctrl+End：跳到文档末行行尾。"""
        if not ctx.document.lines:
            return
        li = len(ctx.document.lines) - 1
        if _is_fence(ctx.document.lines[li]):
            ctx.set_cursor_line(li)
            ctx.set_cursor_li(None)
            return
        ctx.set_cursor(li, len(_line_raw(ctx.document.lines[li])))
        ctx.ensure_visible(li)

    def _get_line_visual_lines(li: int, cursor_off: int | None = None):
        """获取目标行的视觉行列表。

        cursor_off=None：浏览态（标记折叠），优先从已构建的 LineLayoutCache 读取
        （不强制构建——导航只需 1-2 行，实时计算比构建全文档 cache 更快）。
        cursor_off=int：激活态（光标段标记可见），实时计算（与 _cursor_overlay 一致）。
        """
        # 浏览态：若 cache 已构建则复用（避免逐行重算 measure_text_offsets）
        if cursor_off is None and ctx.layout_cache_ref.current is not None:
            layout = ctx.layout_cache_ref.current.get(li)
            if layout is not None:
                return layout.visual_lines
        from views.pixel_layout import (
            _block_padding,
            _compute_wrap_width,
            _line_visual_layout,
        )
        if not (0 <= li < len(ctx.document.lines)):
            return None
        line = ctx.document.lines[li]
        if _is_fence(line):
            return None
        base = block_text_size(line.block_type, line.level)
        _, _, left_pad = _block_padding(line)
        cw = ctx.content_width if ctx.content_width is not None else float("inf")
        wrap_width = _compute_wrap_width(cw, left_pad)
        return _line_visual_layout(
            line, base, wrap_width,
            cursor_raw_offset=cursor_off, line_height=ctx.line_height,
        )

    def _cursor_vline_info(li: int, off: int):
        """返回当前光标的 (visual_lines, vline, current_x)。

        用激活态 cursor_raw_offset=off 计算视觉行布局（标记可见，与渲染层一致）。
        current_x = vline.offsets_x[local_off]（vline 内 X 像素，用于记忆列）。
        返回 None 表示围栏块或无效行。
        """
        from views.pixel_layout import _find_vline_for_raw
        visual_lines = _get_line_visual_lines(li, cursor_off=off)
        if visual_lines is None:
            return None
        vline = _find_vline_for_raw(visual_lines, off)
        if vline is None:
            return None
        local_off = off - vline.start_raw
        local_off = max(0, min(local_off, len(vline.offsets_x) - 1))
        current_x = vline.offsets_x[local_off]
        return (visual_lines, vline, current_x)

    def _move_vline(direction: int, steps: int = 1):
        """视觉行导航：移动 steps 个视觉行（direction: -1=上, +1=下）。

        - 同逻辑行内：移到上/下视觉行（X 列保持）
        - 越界：跨逻辑行（跳过围栏块），目标行用浏览态视觉行取末行/首行
        - preferred_col_ref 存储 X 像素（跨视觉行一致列定位，比 raw 偏移更准）
        - 围栏块：进入编辑态（set_cursor_line + set_cursor_li(None)）
        """
        if ctx.cursor_li is None:
            return
        li = ctx.cursor_li
        off = ctx.cursor_base()

        # 获取当前视觉行信息
        info = _cursor_vline_info(li, off)
        if info is None:
            # 围栏块或无效行：退化为逻辑行导航
            target_li = max(0, min(len(ctx.document.lines) - 1, li + direction * steps))
            if _is_fence(ctx.document.lines[target_li]):
                ctx.set_cursor_line(target_li)
                ctx.set_cursor_li(None)
            else:
                ctx.set_cursor(target_li, 0)
                ctx.ensure_visible(target_li)
            return

        visual_lines, vline, current_x = info
        target_vline_idx = vline.vline_idx

        # 记忆列：首次垂直导航时记录当前 X 像素
        if ctx.preferred_col_ref.current is None:
            ctx.preferred_col_ref.current = current_x
        preferred_x = ctx.preferred_col_ref.current

        # 沿 direction 走 steps 个视觉行
        target_li = li
        target_vlines = visual_lines
        remaining = steps

        while remaining > 0:
            if direction > 0:
                # 向下
                if target_vline_idx < len(target_vlines) - 1:
                    target_vline_idx += 1
                    remaining -= 1
                else:
                    # 跨到下一非围栏逻辑行
                    nxt_li = target_li + 1
                    while nxt_li < len(ctx.document.lines) and _is_fence(ctx.document.lines[nxt_li]):
                        nxt_li += 1
                    if nxt_li >= len(ctx.document.lines):
                        # 到达文档末尾：落到末行末尾（可能是围栏块）
                        last_li = len(ctx.document.lines) - 1
                        if _is_fence(ctx.document.lines[last_li]):
                            ctx.set_cursor_line(last_li)
                            ctx.set_cursor_li(None)
                        else:
                            ctx.set_cursor(last_li, len(_line_raw(ctx.document.lines[last_li])),
                                            clear_preferred=False)
                            ctx.ensure_visible(last_li)
                        return
                    target_li = nxt_li
                    target_vlines = _get_line_visual_lines(target_li)
                    if target_vlines is None:
                        ctx.set_cursor(target_li, 0, clear_preferred=False)
                        ctx.ensure_visible(target_li)
                        return
                    target_vline_idx = 0
                    remaining -= 1
            else:
                # 向上
                if target_vline_idx > 0:
                    target_vline_idx -= 1
                    remaining -= 1
                else:
                    # 跨到上一非围栏逻辑行
                    prev_li = target_li - 1
                    while prev_li >= 0 and _is_fence(ctx.document.lines[prev_li]):
                        prev_li -= 1
                    if prev_li < 0:
                        # 到达文档顶部
                        ctx.set_cursor(0, 0, clear_preferred=False)
                        ctx.ensure_visible(0)
                        return
                    target_li = prev_li
                    target_vlines = _get_line_visual_lines(target_li)
                    if target_vlines is None:
                        ctx.set_cursor(target_li, 0, clear_preferred=False)
                        ctx.ensure_visible(target_li)
                        return
                    target_vline_idx = len(target_vlines) - 1
                    remaining -= 1

        # 在目标视觉行上用 preferred_x 命中 raw 偏移
        target_off = _vline_off_at_x(target_vlines, target_vline_idx, preferred_x)
        if target_off is None:
            target_off = 0
        ctx.set_cursor(target_li, target_off, clear_preferred=False)
        ctx.ensure_visible(target_li)

    def move_up():
        if ctx.cursor_li is None:
            return
        _move_vline(-1, 1)

    def move_down():
        if ctx.cursor_li is None:
            return
        _move_vline(1, 1)

    return {
        "move_left": move_left,
        "move_right": move_right,
        "move_home": move_home,
        "move_end": move_end,
        "move_doc_start": move_doc_start,
        "move_doc_end": move_doc_end,
        "move_up": move_up,
        "move_down": move_down,
        "move_vline": _move_vline,
        "cursor_vline_info": _cursor_vline_info,
        "get_line_visual_lines": _get_line_visual_lines,
    }
