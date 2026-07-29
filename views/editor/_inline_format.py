"""行内格式工厂（从 views/editor.py 闭包抽取）。

闭包组：apply_inline_format / apply_outward_wrap / handle_outward_type_char

跨组依赖（通过 ctx 装配槽，调用时读取）：
- push_history（history 组）
- cursor_base / set_cursor（cursor 组）
- set_outward_sel（共享）
- mark_dirty（共享）
- set_nav_seq（setter，仅 _apply_outward_wrap 递增以重建 TextField）

组内依赖（直接调用，不经 ctx）：
- apply_inline_format → _apply_outward_wrap（outward 选区优先）

依赖项：
- parser（reparse_line_atomic）
- models（SegType）
- utils.segment_helpers（WRAP_SYNTAX / is_fence / line_raw）
"""

import parser
from models import SegType
from utils.segment_helpers import WRAP_SYNTAX
from utils.segment_helpers import is_fence as _is_fence
from utils.segment_helpers import line_raw as _line_raw

# 高频编辑路径用原子化重解析（仅触发 1 次 observable 通知）
_reparse_atomic = parser.reparse_line_atomic


def build_inline_format(ctx):
    """构造行内格式闭包组。

    返回 dict[str, Callable]：
    apply_inline_format / apply_outward_wrap / handle_outward_type_char
    """

    # ============ 行内格式（光标级包裹）============
    def apply_inline_format(fmt: str):
        """行内格式快捷键：有 outward 选区包裹/取消同段选区；否则在光标处插入空语法。

        fmt: bold/italic/highlight/strike/code/link/inline_math
        """
        # 优先处理 outward 选区（无论浏览态还是编辑态）：toggle 包裹/取消
        if ctx.outward_sel_ref.current is not None:
            _apply_outward_wrap(fmt)
            return
        if ctx.cursor_li is None:
            return
        li = ctx.cursor_li
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if _is_fence(line):
            return
        ctx.push_history()
        ctx.undo_push_pending.current = True
        raw = _line_raw(line)
        off = ctx.cursor_base(len(raw))  # IME 实时光标，避免输入后立即 Ctrl+B 位置错位
        if fmt == "link":
            # 插入空链接骨架 []()，光标在 [] 内（text 位置）
            # 空 URL 避免 Tab 跳到 URL 后需删除占位符；Tab 在 text/url 间切换
            new_raw = raw[:off] + "[]()" + raw[off:]
            _reparse_atomic(line, new_raw)
            ctx.mark_dirty()
            ctx.set_cursor(li, off + 1)  # 光标落在 [ 后（text 位置）
        else:
            seg_type = {
                "bold": SegType.STRONG,
                "italic": SegType.EMPHASIS,
                "highlight": SegType.HIGHLIGHT,
                "strike": SegType.STRIKE,
                "code": SegType.CODESPAN,
                "inline_math": SegType.INLINE_MATH,
            }.get(fmt)
            if seg_type is None:
                return
            wrap = WRAP_SYNTAX.get(seg_type, ("", ""))[0]
            new_raw = raw[:off] + wrap + wrap + raw[off:]
            _reparse_atomic(line, new_raw)
            ctx.mark_dirty()
            ctx.set_cursor(li, off + len(wrap))  # 光标落在两标记之间

    def _apply_outward_wrap(fmt: str):
        """渲染态 outward 选区 toggle 行内格式（仅同段选区）。

        Typora 式 toggle：
        - 选区已包裹同类型标记 → 取消标记（unwrap），保持选区
        - 选区未包裹 → 添加标记（wrap），保持选区（选内容，不含标记）
        - 再次按下快捷键即可 toggle 回来
        """
        sel = ctx.outward_sel_ref.current
        if sel is None:
            return
        a_li, a_off, b_li, b_off = sel
        if (a_li, a_off) > (b_li, b_off):
            a_li, a_off, b_li, b_off = b_li, b_off, a_li, a_off
        if a_li != b_li:
            return
        if not (0 <= a_li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[a_li]
        if _is_fence(line):
            return
        raw = _line_raw(line)
        a_off = max(0, min(a_off, len(raw)))
        b_off = max(a_off, min(b_off, len(raw)))
        selected = raw[a_off:b_off]
        if not selected:
            return  # 空选区不操作

        ctx.push_history()
        ctx.undo_push_pending.current = True

        if fmt == "link":
            # 包裹为 [selected]()，光标定位到 URL 位置（]( 与 ) 之间）。
            # 链接编辑视为常规文本编辑：光标在链接段内时渲染层（raw_to_visible_spans /
            # split_seg_for_display）显示完整语法含 URL，光标离开段后自动折叠为
            # display_text。无需 Tab 字段跳转 / URL 占位符等特殊状态机，亦无 set_nav_seq
            # 重建，避免异步重新聚焦间隙丢失快速输入。
            new_raw = raw[:a_off] + f"[{selected}]()" + raw[b_off:]
            _reparse_atomic(line, new_raw)
            new_lines = list(ctx.document.lines)
            new_lines[a_li] = line
            ctx.document.lines = new_lines
            ctx.mark_dirty()
            ctx.set_outward_sel(None)
            # URL 起点 = a_off + 1([) + len(selected)(text) + 2(])
            ctx.set_cursor(a_li, a_off + 3 + len(selected))
            return

        seg_type = {
            "bold": SegType.STRONG,
            "italic": SegType.EMPHASIS,
            "highlight": SegType.HIGHLIGHT,
            "strike": SegType.STRIKE,
            "code": SegType.CODESPAN,
            "inline_math": SegType.INLINE_MATH,
        }.get(fmt)
        if seg_type is None:
            return
        wrap_open, wrap_close = WRAP_SYNTAX.get(seg_type, ("", ""))
        ol, cl = len(wrap_open), len(wrap_close)

        # ---- toggle 检测：选区是否已被同类型标记包裹 ----
        # Case A: 标记紧贴选区外侧（选区 = 纯内容，如 **|selected|**）
        case_a = (
            a_off >= ol
            and raw[a_off - ol:a_off] == wrap_open
            and b_off + cl <= len(raw)
            and raw[b_off:b_off + cl] == wrap_close
        )
        # Case B: 标记在选区两端内侧（选区 = 标记+内容+标记，如 |**selected**|）
        case_b = (
            selected.startswith(wrap_open)
            and selected.endswith(wrap_close)
            and len(selected) >= ol + cl
        )

        if case_a:
            # Unwrap：移除外侧标记，选区平移到去标记后的内容
            new_raw = raw[:a_off - ol] + selected + raw[b_off + cl:]
            new_sel = (a_li, a_off - ol, a_li, b_off - ol)
        elif case_b:
            # Unwrap：移除内侧标记，选区收缩到纯内容
            inner = selected[ol:len(selected) - cl] if cl else selected[ol:]
            new_raw = raw[:a_off] + inner + raw[b_off:]
            new_sel = (a_li, a_off, a_li, a_off + len(inner))
        else:
            # Wrap：添加标记，选区保持在内容上（不含标记）
            new_raw = raw[:a_off] + wrap_open + selected + wrap_close + raw[b_off:]
            new_sel = (a_li, a_off + ol, a_li, a_off + ol + len(selected))

        _reparse_atomic(line, new_raw)
        new_lines = list(ctx.document.lines)
        new_lines[a_li] = line
        ctx.document.lines = new_lines
        ctx.mark_dirty()
        ctx.set_outward_sel(new_sel)
        ctx.set_nav_seq(ctx.nav_seq + 1)

    # ============ 链接语法 Typora 式交互 ============
    def handle_outward_type_char(char: str):
        """打字替换 outward 选区（浏览态选中→输入即替换，通用基础编辑行为）。

        一次 reparse 完成删除+插入，避免 delete+insert 两次重绘闪烁。
        替换后清除 outward_sel 高亮并切换到编辑态（cursor_li=li），
        现有 use_effect(_focus_cursor_field, [cursor_li]) 自动聚焦 cursor_text_field，
        下一字符走正常 IME 输入流。
        """
        sel = ctx.outward_sel_ref.current
        if sel is None:
            return
        a_li, a_off, b_li, b_off = sel
        if (a_li, a_off) > (b_li, b_off):
            a_li, a_off, b_li, b_off = b_li, b_off, a_li, a_off
        if a_li != b_li:
            return  # 跨行 v1 不处理
        if not (0 <= a_li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[a_li]
        if _is_fence(line):
            return
        raw = _line_raw(line)
        a_off = max(0, min(a_off, len(raw)))
        b_off = max(a_off, min(b_off, len(raw)))
        if a_off == b_off:
            return  # 零宽选区不操作

        ctx.push_history()
        ctx.undo_push_pending.current = True
        new_raw = raw[:a_off] + char + raw[b_off:]
        _reparse_atomic(line, new_raw)
        new_lines = list(ctx.document.lines)
        new_lines[a_li] = line
        ctx.document.lines = new_lines
        ctx.mark_dirty()
        # 清选区 + 切换到编辑态（光标在插入字符后）
        ctx.set_outward_sel(None)
        ctx.set_cursor(a_li, a_off + len(char))
        # 不递增 nav_seq：避免 TextField 重建→异步重新聚焦间隙丢失后续快速输入。
        # cursor_li 由 _set_cursor 设置，LineView 据其重渲染刷新 TextField 的 left/on_change；
        # 旧 value 由 _end_input_session→_clear_cursor_value effect 清空。

    return {
        "apply_inline_format": apply_inline_format,
        "apply_outward_wrap": _apply_outward_wrap,
        "handle_outward_type_char": handle_outward_type_char,
    }
