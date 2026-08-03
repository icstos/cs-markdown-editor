"""向外选区工厂（从 views/editor.py 闭包抽取）。

闭包组：_step_left / _step_right / _step_up / _step_down / _step_vline /
_start_outward_from_point / _extend_outward / _extend_outward_step /
_select_word_at / on_extend_outward / clear_outward_sel /
_delete_raw_range / handle_outward_delete / _extract_outward_text /
handle_outward_cut / handle_outward_copy / select_all

跨组依赖（通过 ctx 装配槽，调用时读取）：
- cursor_base / set_cursor（cursor 组）
- push_history / mark_dirty（history / 共享组）
- set_outward_sel（共享）
- cursor_vline_info / get_line_visual_lines（cursor 视觉行组）

同组直接调用：
- _step_up / _step_down → _step_vline
- on_extend_outward → _start_outward_from_point / _extend_outward
- handle_outward_delete → _delete_raw_range
- handle_outward_cut → _extract_outward_text / _delete_raw_range
- handle_outward_copy → _extract_outward_text

依赖项：
- parser（extract_outward_text / reparse_line_atomic）
- utils.segment_helpers（is_fence / line_raw）
- views._editor_helpers（_step_left / _step_right / _select_word_bounds /
  _compute_delete_result / _vline_off_at_x 纯函数实现）
"""

import contextlib

import parser
from utils.segment_helpers import is_fence as _is_fence
from utils.segment_helpers import line_raw as _line_raw
from views._editor_helpers import _compute_delete_result as _compute_delete_result_impl
from views._editor_helpers import _select_word_bounds, _vline_off_at_x
from views._editor_helpers import _step_left as _step_left_impl
from views._editor_helpers import _step_right as _step_right_impl

# 高频编辑路径用原子化重解析（仅触发 1 次 observable 通知）
_reparse_atomic = parser.reparse_line_atomic


def build_outward(ctx):
    """构造向外选区闭包组。

    返回 dict[str, Callable]：
    step_left / step_right / step_up / step_down / start_outward_from_point /
    extend_outward / extend_outward_step / select_word_at / on_extend_outward /
    clear_outward_sel / delete_raw_range / handle_outward_delete /
    handle_outward_cut / handle_outward_copy / select_all
    """

    # ============ 向外选区 ============
    # _step_left / _step_right 纯计算已抽取到 _editor_helpers（接收 lines 参数）
    def _step_left(li: int, off: int) -> tuple[int, int] | None:
        return _step_left_impl(ctx.document.lines, li, off)

    def _step_right(li: int, off: int) -> tuple[int, int] | None:
        return _step_right_impl(ctx.document.lines, li, off)

    def _step_up(li: int, off: int) -> tuple[int, int] | None:
        """视觉行向上步进（向外选区 Shift+Up 用）。"""
        return _step_vline(li, off, -1)

    def _step_down(li: int, off: int) -> tuple[int, int] | None:
        """视觉行向下步进（向外选区 Shift+Down 用）。"""
        return _step_vline(li, off, 1)

    def _step_home(li: int, off: int) -> tuple[int, int] | None:
        """Smart Home 目标（Shift+Home 用）：先跳内容首，再跳行首。

        与 move_home 三态逻辑一致：
        - off > content_start → 跳到 content_start（跳过 # / - / > 前缀）
        - off == content_start（且 > 0）→ 跳到行首 raw 0
        - off == 0 → 返回 None（已在行首，不扩展）
        """
        from models import SegType
        if not (0 <= li < len(ctx.document.lines)):
            return None
        line = ctx.document.lines[li]
        if _is_fence(line):
            return None
        raw = _line_raw(line)
        content_start = 0
        if line.segments and line.segments[0].seg_type in (
            SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX,
        ):
            content_start = len(line.segments[0].raw)
        content_start = min(content_start, len(raw))
        if off == 0:
            return None
        if off == content_start:
            return (li, 0)
        return (li, content_start)

    def _step_end(li: int, off: int) -> tuple[int, int] | None:
        """End 目标（Shift+End 用）：跳到行尾 raw_len。

        已在行尾时返回 None（不扩展）。
        """
        if not (0 <= li < len(ctx.document.lines)):
            return None
        line = ctx.document.lines[li]
        if _is_fence(line):
            return None
        raw = _line_raw(line)
        if off >= len(raw):
            return None
        return (li, len(raw))

    def _step_vline(li: int, off: int, direction: int) -> tuple[int, int] | None:
        """视觉行步进：返回目标 (li, off)，不移动光标。

        与 _move_vline 逻辑一致（同逻辑行内移视觉行，越界跨逻辑行跳过围栏块），
        但仅返回目标位置由 _extend_outward_step 设置选区。
        """
        info = ctx.cursor_vline_info(li, off)
        if info is None:
            # 围栏块：退化为逻辑行步进
            if direction > 0:
                if li >= len(ctx.document.lines) - 1:
                    return None
                nxt = ctx.document.lines[li + 1]
                if _is_fence(nxt):
                    return None
                return (li + 1, 0)
            else:
                if li <= 0:
                    return None
                prev = ctx.document.lines[li - 1]
                if _is_fence(prev):
                    return None
                return (li - 1, len(_line_raw(prev)))

        visual_lines, vline, current_x = info
        # 记忆列（与 _move_vline 共享 preferred_col_ref）
        if ctx.preferred_col_ref.current is None:
            ctx.preferred_col_ref.current = current_x
        preferred_x = ctx.preferred_col_ref.current
        target_vline_idx = vline.vline_idx

        if direction > 0:
            if target_vline_idx < len(visual_lines) - 1:
                target_vline_idx += 1
            else:
                nxt_li = li + 1
                while nxt_li < len(ctx.document.lines) and _is_fence(ctx.document.lines[nxt_li]):
                    nxt_li += 1
                if nxt_li >= len(ctx.document.lines):
                    return None
                visual_lines = ctx.get_line_visual_lines(nxt_li)
                if visual_lines is None:
                    return (nxt_li, 0)
                target_vline_idx = 0
                li = nxt_li
        else:
            if target_vline_idx > 0:
                target_vline_idx -= 1
            else:
                prev_li = li - 1
                while prev_li >= 0 and _is_fence(ctx.document.lines[prev_li]):
                    prev_li -= 1
                if prev_li < 0:
                    return None
                visual_lines = ctx.get_line_visual_lines(prev_li)
                if visual_lines is None:
                    return (prev_li, 0)
                target_vline_idx = len(visual_lines) - 1
                li = prev_li

        target_off = _vline_off_at_x(visual_lines, target_vline_idx, preferred_x)
        if target_off is None:
            target_off = 0
        return (li, target_off)

    def _start_outward_from_point(anchor_li: int, anchor_off: int, target_li: int, target_off: int) -> None:
        if ctx.outward_sel_ref.current is not None:
            return
        if not (0 <= anchor_li < len(ctx.document.lines) and 0 <= target_li < len(ctx.document.lines)):
            return
        anchor_off = max(0, min(anchor_off, len(ctx.document.lines[anchor_li].raw or "")))
        target_off = max(0, min(target_off, len(ctx.document.lines[target_li].raw or "")))
        if ctx.cursor_li is not None:
            ctx.suppress_blur.current = True
            ctx.set_cursor_li(None)
        ctx.set_outward_sel((anchor_li, anchor_off, target_li, target_off))

    def _extend_outward(target_li: int, target_off: int) -> None:
        current = ctx.outward_sel_ref.current
        if current is None:
            return
        if not (0 <= target_li < len(ctx.document.lines)):
            return
        target_off = max(0, min(target_off, len(ctx.document.lines[target_li].raw or "")))
        a_li, a_off, _, _ = current
        ctx.set_outward_sel((a_li, a_off, target_li, target_off))

    def _extend_outward_step(step_fn) -> None:
        current = ctx.outward_sel_ref.current
        if ctx.cursor_li is not None and current is None:
            # 从光标起始（用 _cursor_base 取 IME 实时光标，避免输入后立即
            # Shift+方向键起始选区位置错位）
            base = ctx.cursor_base()
            new_pos = step_fn(ctx.cursor_li, base)
            if new_pos is None:
                return
            src_li, src_off = ctx.cursor_li, base
            ctx.suppress_blur.current = True
            ctx.set_cursor_li(None)
            ctx.set_outward_sel((src_li, src_off, new_pos[0], new_pos[1]))
            return
        if current is None:
            return
        a_li, a_off, b_li, b_off = current
        new_pos = step_fn(b_li, b_off)
        if new_pos is None:
            return
        ctx.set_outward_sel((a_li, a_off, new_pos[0], new_pos[1]))

    def clear_outward_sel() -> None:
        ctx.set_outward_sel(None)

    def _select_word_at(li: int, raw_off: int) -> None:
        r"""双击选词：VSCode 风格词边界（同类别连续区间）。

        字符类别：
        - word：\w + CJK（连续 CJK 视为一个词，VSCode 行为）
        - space：\s
        - punct：其他（标点 / Markdown 语法字符等）

        从 raw_off 向左右扩展到同类边界，构造 outward_sel。
        退出光标编辑态（set_cursor_li(None)），与拖拽选区路径一致。

        行尾边界（_select_word_bounds 返回 None）：双击未命中可选词时
        不清除光标，递增 focus_seq 强制重聚焦 TextField，避免双击导致
        TextField 失焦而光标丢失。

        纯计算（_char_kind + _select_word_bounds）已抽取到 _editor_helpers，
        此处仅保留闭包执行（设 outward_sel + 退出光标态）。
        """
        if not (0 <= li < len(ctx.document.lines)):
            return
        # 围栏行不参与选词
        if _is_fence(ctx.document.lines[li]):
            return
        raw = _line_raw(ctx.document.lines[li])
        bounds = _select_word_bounds(raw, raw_off)
        if bounds is None:
            # 空行 / 整行全空白 / 行尾左侧为 word/cjk/space：无可选词。
            # 双击可能已使 cursor TextField 失焦，递增 focus_seq 强制重聚焦，
            # 保留光标编辑态（不丢失光标）。
            if ctx.cursor_li == li:
                ctx.set_focus_seq(ctx.focus_seq + 1)
            return
        start, end = bounds
        # 退出光标编辑态，设为 outward_sel
        if ctx.cursor_li is not None:
            ctx.suppress_blur.current = True
            ctx.set_cursor_li(None)
        ctx.set_outward_sel((li, start, li, end))

    def on_extend_outward(target_li: int, target_off: int) -> None:
        if ctx.outward_sel_ref.current is None:
            if ctx.cursor_li is not None:
                # 从光标起始（用 _cursor_base 取 IME 实时光标，避免输入后立即
                # Shift+点击起始选区位置错位）
                src_li, src_off = ctx.cursor_li, ctx.cursor_base()
                ctx.suppress_blur.current = True
                ctx.set_cursor_li(None)
                ctx.set_outward_sel((src_li, src_off, target_li, target_off))
            else:
                _start_outward_from_point(target_li, target_off, target_li, target_off)
        else:
            _extend_outward(target_li, target_off)

    def on_pan_start_outward(target_li: int, target_off: int) -> None:
        """拖拽起始：以命中点为选区 anchor（不沿用光标位置）。

        与 on_extend_outward 的区别：后者在 cursor_li 非空时以光标为起点
        （供 Shift+点击从光标扩展选区）；鼠标拖拽应以按下点为起点，否则
        会把上次光标位置作为 anchor、命中点作为 target，造成选区错位。

        不清 cursor_li：避免 set_cursor_li(None) 触发的重渲染中断 pan 手势
        （第一次拖拽失效问题——pan_update 不触发，选区停留零长度不可见）。
        cursor_li 保持，outward_sel 非 None 时渲染层通过 is_act 判断自动
        隐藏光标（_render.py: is_act 加 outward_sel is None 条件）。
        cursor_li 在下次 tap（on_tap_line）或选区操作（删除/剪切）时自然清理。
        """
        if not (0 <= target_li < len(ctx.document.lines)):
            return
        target_off = max(0, min(target_off, len(ctx.document.lines[target_li].raw or "")))
        # 抑制 on_blur：TextField 失焦时不触发副作用（on_blur 非抑制分支本就空操作，保险）
        ctx.suppress_blur.current = True
        # 只设 outward_sel：以命中点同时作为 anchor 和 target 构造零长度选区，
        # 后续 pan_update 走 _extend_outward 扩展 target 端。
        ctx.set_outward_sel((target_li, target_off, target_li, target_off))

    def _delete_raw_range(start_li: int, start_off: int, end_li: int, end_off: int) -> None:
        ctx.push_history()
        ctx.undo_push_pending.current = True
        try:
            # 纯决策已抽取到 _editor_helpers._compute_delete_result（可单测）
            result = _compute_delete_result_impl(
                ctx.document.lines, start_li, start_off, end_li, end_off)
            if result is None:
                return
            merge_li, merged_raw, new_lines = result
            # 单行：new_lines is document.lines（行数不变），仅 reparse 触发重渲染
            # 多行：new_lines 是新列表，reparse 后赋值 document.lines 触发结构通知
            _reparse_atomic(ctx.document.lines[merge_li], merged_raw)
            if new_lines is not ctx.document.lines:
                ctx.document.lines = new_lines
        except Exception:
            return
        ctx.mark_dirty()
        ctx.set_outward_sel(None)
        if 0 <= start_li < len(ctx.document.lines):
            ctx.set_cursor(start_li, start_off)
            # 强制重聚焦 cursor TextField：拖拽起始 outward 选区时未清 cursor_li
            # （on_pan_start_outward 注释——避免 set_cursor_li(None) 重渲染中断 pan
            # 手势），删除/剪切后 cursor_li 可能未变（同行选区），focus use_effect
            # 依赖 [cursor_li, nav_seq, focus_seq, ...] 不触发→TextField 保持失焦
            # 态→光标丢失。递增 focus_seq 强制重聚焦，与 _on_tap_line 同型修复。
            ctx.set_focus_seq(ctx.focus_seq + 1)

    def handle_outward_delete() -> None:
        if ctx.outward_sel is None:
            return
        a_li, a_off, b_li, b_off = ctx.outward_sel
        if (a_li, a_off) > (b_li, b_off):
            a_li, a_off, b_li, b_off = b_li, b_off, a_li, a_off
        _delete_raw_range(a_li, a_off, b_li, b_off)

    def _extract_outward_text(a_li: int, a_off: int, b_li: int, b_off: int) -> str:
        """提取已排序选区文本（行级 raw 拼接）。

        纯计算已抽取到 parser.selection.extract_outward_text（可单测），
        此处仅传入 document.lines 适配闭包签名。
        """
        return parser.extract_outward_text(ctx.document.lines, a_li, a_off, b_li, b_off)

    async def handle_outward_cut() -> None:
        if ctx.outward_sel is None:
            return
        a_li, a_off, b_li, b_off = ctx.outward_sel
        if (a_li, a_off) > (b_li, b_off):
            a_li, a_off, b_li, b_off = b_li, b_off, a_li, a_off
        text = _extract_outward_text(a_li, a_off, b_li, b_off)
        clipboard = ctx.clipboard_ref.current if ctx.clipboard_ref is not None else None
        if clipboard is not None and text:
            with contextlib.suppress(Exception):
                await clipboard.set(text)
        _delete_raw_range(a_li, a_off, b_li, b_off)

    async def handle_outward_copy() -> None:
        """Ctrl+C：复制 outward_sel 选区文本到剪贴板（不删除）。

        复用 _extract_outward_text 提取逻辑，但跳过 _delete_raw_range。
        用 outward_sel_ref.current 读取实时光标选区（复制不改变状态）。
        """
        sel = ctx.outward_sel_ref.current
        if sel is None:
            return
        a_li, a_off, b_li, b_off = sel
        if (a_li, a_off) > (b_li, b_off):
            a_li, a_off, b_li, b_off = b_li, b_off, a_li, a_off
        text = _extract_outward_text(a_li, a_off, b_li, b_off)
        clipboard = ctx.clipboard_ref.current if ctx.clipboard_ref is not None else None
        if clipboard is not None and text:
            with contextlib.suppress(Exception):
                await clipboard.set(text)

    def select_all() -> None:
        """Ctrl+A：全选文档（outward_sel 跨越整个文档）。"""
        if not ctx.document.lines:
            return
        last_li = len(ctx.document.lines) - 1
        last_line = ctx.document.lines[last_li]
        # 末行为围栏块时全选到其行首（围栏块不参与 raw 选区）
        last_off = 0 if _is_fence(last_line) else len(_line_raw(last_line))
        # 起始行若为围栏块，从下一非围栏行开始
        start_li = 0
        while start_li < last_li and _is_fence(ctx.document.lines[start_li]):
            start_li += 1
        if start_li >= last_li and _is_fence(ctx.document.lines[start_li]):
            return  # 全文档均为围栏块，无可选文本
        if ctx.cursor_li is not None:
            ctx.suppress_blur.current = True
            ctx.set_cursor_li(None)
        ctx.set_outward_sel((start_li, 0, last_li, last_off))

    return {
        "step_left": _step_left,
        "step_right": _step_right,
        "step_up": _step_up,
        "step_down": _step_down,
        "step_home": _step_home,
        "step_end": _step_end,
        "start_outward_from_point": _start_outward_from_point,
        "extend_outward": _extend_outward,
        "extend_outward_step": _extend_outward_step,
        "select_word_at": _select_word_at,
        "on_extend_outward": on_extend_outward,
        "on_pan_start_outward": on_pan_start_outward,
        "clear_outward_sel": clear_outward_sel,
        "delete_raw_range": _delete_raw_range,
        "handle_outward_delete": handle_outward_delete,
        "handle_outward_cut": handle_outward_cut,
        "handle_outward_copy": handle_outward_copy,
        "select_all": select_all,
    }
