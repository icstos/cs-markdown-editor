"""围栏岛屿（CODE/MATH/TABLE）编辑处理器工厂（从 views/editor.py 闭包抽取）。

闭包组：on_change_code / on_code_focus / on_code_blur / handle_code_backspace /
on_change_math / on_math_focus / on_math_blur /
on_change_cell / on_table_op / on_table_focus / on_table_blur

围栏岛屿自管理独立可编辑控件，不进入光标级 Stack，不参与光标导航/合并。
代码块/公式块采用"聚焦时取快照、首次修改时入栈"的防抖策略，整个编辑会话
仅占一个撤销条目；表格操作则在操作前整体 push_history。

跨组依赖（通过 ctx 装配槽）：
- history 组：make_snapshot / push_history / maybe_push_history
- 共享：mark_dirty
- cursor 组：set_cursor_li / set_math_focus_li / set_table_focus_li

依赖项：
- models（BlockType / Line / Segment / SegType）
- utils.segment_helpers（is_fence / line_raw）
- utils.table_helpers（ALIGN_RE）
- views._editor_helpers（_table_cells）
- views.table_view（_align_marker / _join_row）
"""


import parser
from models import BlockType, Line, Segment, SegType
from utils.segment_helpers import is_fence as _is_fence
from utils.segment_helpers import line_raw as _line_raw
from utils.table_helpers import ALIGN_RE
from views._editor_helpers import _table_cells
from views.table_view import _align_marker, _join_row

# 高频编辑路径用原子化重解析（仅触发 1 次 observable 通知）
_reparse_atomic = parser.reparse_line_atomic


def build_fence(ctx):
    """构造围栏岛屿（CODE/MATH/TABLE）编辑处理器闭包组。

    返回 dict[str, Callable]：
    on_change_code / on_code_focus / on_code_blur / handle_code_backspace /
    on_change_math / on_math_focus / on_math_blur /
    on_change_cell / on_table_op / on_table_focus / on_table_blur
    """

    # ============ 代码块 / YAML 前置元数据 ============
    def on_change_code(li: int, value: str) -> None:
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        # frontmatter 复用 CodeEditor 编辑，与代码块同等处理
        if line.block_type == BlockType.CODE:
            old_text = line.segments[0].text if line.segments else ""
            if old_text == value:
                return
            line.segments[0].text = value
            line.segments[0].raw = value
            line.raw = f"```{line.lang}\n{value}\n```"
        elif line.block_type == BlockType.FRONTMATTER:
            old_text = line.segments[0].text if line.segments else ""
            if old_text == value:
                return
            line.segments[0].text = value
            line.segments[0].raw = value
            line.raw = f"---\n{value}\n---" if value else "---\n---"
        else:
            return
        # 代码块编辑防抖：第一次修改时将快照推入历史，整个编辑会话只占一个撤销条目
        # 这样即使在代码块聚焦时按 Ctrl+Z 也能正常撤销
        if (
            not ctx.code_edit_changed.current
            and ctx.code_edit_snapshot.current is not None
            and not ctx.restoring.current
        ):
            ctx.history_ref.current.push(ctx.code_edit_snapshot.current)
        ctx.code_edit_changed.current = True
        if not ctx.document.dirty:
            ctx.mark_dirty()

    def on_code_focus(li: int) -> None:
        ctx.code_focus_ref.current = li
        # 代码块/frontmatter 聚焦时退出光标编辑态
        if ctx.cursor_li is not None:
            ctx.suppress_blur.current = True
            ctx.set_cursor_li(None)
        # 保存聚焦时的快照，用于失焦时与修改前比较
        ctx.code_edit_snapshot.current = ctx.make_snapshot()
        ctx.code_edit_changed.current = False

    def on_code_blur(li: int) -> None:
        if ctx.code_focus_ref.current == li:
            ctx.code_focus_ref.current = None
        # 代码块/frontmatter 失焦时：清理状态
        # 注意：快照已在第一次修改时推入历史，此处不再重复推入
        ctx.code_edit_snapshot.current = None
        ctx.code_edit_changed.current = False

    def handle_code_backspace(li: int) -> bool:
        """空代码块/空 frontmatter Backspace 删除：替换为空白段落行（Typora 式）。

        返回 True 表示已处理（消费 Backspace，阻止传给原生 CodeEditor）；
        False 表示未处理（非空块 / 非代码块/frontmatter / 越界，继续走原生删除）。

        触发场景：CodeEditor 聚焦时全局 KeyDispatcher 检测到 BackSpace，
        先尝试本函数；若块内容为空（含仅空白字符）则整体删除。
        删除后进入段落编辑态（光标定位到行首），与 set_block 的早返回模式一致。
        """
        if not (0 <= li < len(ctx.document.lines)):
            return False
        line = ctx.document.lines[li]
        if line.block_type not in (BlockType.CODE, BlockType.FRONTMATTER):
            return False
        # 块内容为空（含仅空白字符）才触发整体删除
        code = line.segments[0].text if line.segments else ""
        if code.strip():
            return False
        # 替换为空白段落行（保持光标行位置），push_history 记录删除前状态供撤销
        ctx.push_history()
        ctx.undo_push_pending.current = True
        new_line = Line(block_type=BlockType.PARAGRAPH, raw="")
        new_line.segments = [Segment(SegType.TEXT, "", "")]
        ctx.document.lines = ctx.document.lines[:li] + [new_line] + ctx.document.lines[li + 1:]
        ctx.mark_dirty()
        # 清理代码块聚焦状态（模拟 on_code_blur 的清理，防止 CodeEditor 卸载时
        # on_blur 未触发导致 code_focus_ref 残留，_native_field_focused 误判）
        ctx.code_focus_ref.current = None
        ctx.code_edit_snapshot.current = None
        ctx.code_edit_changed.current = False
        # 进入段落编辑态（光标定位到行首）；suppress_blur 防 CodeEditor 卸载
        # 级联 blur 干扰新聚焦的 cursor TextField
        ctx.suppress_blur.current = True
        ctx.set_cursor(li, 0)
        return True

    # ============ 块级公式 ============
    def on_change_math(li: int, value: str) -> None:
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if line.block_type != BlockType.MATH:
            return
        old_text = line.segments[0].text if line.segments else ""
        if old_text == value:
            return
        # 更新公式源码：segments[0].text/raw 同步，line.raw 重建为 $$\n...\n$$
        line.segments[0].text = value
        line.segments[0].raw = value
        line.raw = f"$$\n{value}\n$$"
        # 公式编辑防抖：第一次修改时将快照推入历史，整个编辑会话只占一个撤销条目
        if (
            not ctx.math_edit_changed.current
            and ctx.math_edit_snapshot.current is not None
            and not ctx.restoring.current
        ):
            ctx.history_ref.current.push(ctx.math_edit_snapshot.current)
        ctx.math_edit_changed.current = True
        if not ctx.document.dirty:
            ctx.mark_dirty()

    def on_math_focus(li: int) -> None:
        ctx.math_focus_ref.current = li
        ctx.set_math_focus_li(li)
        # 公式聚焦时退出光标编辑态
        if ctx.cursor_li is not None:
            ctx.suppress_blur.current = True
            ctx.set_cursor_li(None)
        # 保存聚焦时的快照，用于首次修改时推入历史
        ctx.math_edit_snapshot.current = ctx.make_snapshot()
        ctx.math_edit_changed.current = False

    def on_math_blur(li: int) -> None:
        if ctx.math_focus_ref.current == li:
            ctx.math_focus_ref.current = None
        ctx.set_math_focus_li(None)
        # 公式失焦时：清理状态（快照已在首次修改时推入历史）
        ctx.math_edit_snapshot.current = None
        ctx.math_edit_changed.current = False

    # ============ 表格 ============
    def on_change_cell(li: int, cell_idx: int, value: str) -> None:
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if line.block_type != BlockType.TABLE:
            return
        cells = _table_cells(line)
        if cell_idx < len(cells):
            if cells[cell_idx] == value:
                return
            cells[cell_idx] = value
        new_raw = _join_row(cells)
        line.raw = new_raw
        if line.segments:
            line.segments[0].text = new_raw
            line.segments[0].raw = new_raw
        ctx.maybe_push_history()
        if not ctx.document.dirty:
            ctx.mark_dirty()

    def on_table_op(op: str, params: dict) -> None:
        ctx.push_history()
        ctx.undo_push_pending.current = True
        lines = list(ctx.document.lines)

        def _find_table_start_from_li(li: int) -> int:
            if not (0 <= li < len(lines)):
                return li
            j = li
            while j > 0 and lines[j - 1].block_type == BlockType.TABLE:
                j -= 1
            return j

        ts = params.get("table_start")
        if ts is None:
            ref_li = params.get("after_li", params.get("li", 0))
            ts = _find_table_start_from_li(ref_li)

        def _find_table_range(start: int) -> tuple[int, int]:
            i = start
            while i < len(lines) and lines[i].block_type == BlockType.TABLE:
                i += 1
            return start, i - 1

        def _find_sep_line(start: int) -> int:
            if start + 1 < len(lines) and lines[start + 1].block_type == BlockType.TABLE:
                cells = [c.strip() for c in lines[start + 1].raw.strip().strip("|").split("|")]
                if all(c and ALIGN_RE.fullmatch(c) for c in cells):
                    return start + 1
            return start + 1

        def _rebuild_table_line(idx: int, new_raw: str) -> None:
            new_line = Line(block_type=BlockType.TABLE, raw=new_raw)
            new_line.segments = [Segment(SegType.TEXT, new_raw, new_raw)]
            lines[idx] = new_line

        if op == "add_row":
            after_li = params["after_li"]
            col_count = params["col_count"]
            new_raw = "| " + " | ".join([""] * col_count) + " |"
            new_line = Line(block_type=BlockType.TABLE, raw=new_raw)
            new_line.segments = [Segment(SegType.TEXT, new_raw, new_raw)]
            lines.insert(after_li + 1, new_line)
            ctx.document.lines = lines
            ctx.mark_dirty()
        elif op == "delete_row":
            li = params["li"]
            ts2, te2 = _find_table_range(ts)
            sep = _find_sep_line(ts2)
            data_indices = [i for i in range(ts2, te2 + 1) if i != ts2 and i != sep]
            if li in data_indices and len(data_indices) > 1:
                del lines[li]
                ctx.document.lines = lines
                ctx.mark_dirty()
        elif op == "clear_row":
            li = params["li"]
            if 0 <= li < len(lines):
                cells = _table_cells(lines[li])
                _rebuild_table_line(li, _join_row([""] * len(cells)))
                ctx.document.lines = lines
                ctx.mark_dirty()
        elif op == "add_col":
            ts2, te2 = _find_table_range(ts)
            col_idx = params["col_idx"]
            sep_li = _find_sep_line(ts2)
            for i in range(ts2, te2 + 1):
                cells = _table_cells(lines[i])
                if i == sep_li:
                    cells.insert(col_idx, "---")
                else:
                    cells.insert(col_idx, "")
                _rebuild_table_line(i, _join_row(cells))
            ctx.document.lines = lines
            ctx.mark_dirty()
        elif op == "delete_col":
            ts2, te2 = _find_table_range(ts)
            col_idx = params["col_idx"]
            for i in range(ts2, te2 + 1):
                cells = _table_cells(lines[i])
                if 0 <= col_idx < len(cells):
                    del cells[col_idx]
                _rebuild_table_line(i, _join_row(cells))
            ctx.document.lines = lines
            ctx.mark_dirty()
        elif op == "set_align":
            ts2, te2 = _find_table_range(ts)
            col_idx = params["col_idx"]
            align = params["align"]
            sep_li = _find_sep_line(ts2)
            cells = _table_cells(lines[sep_li])
            if 0 <= col_idx < len(cells):
                # align 为语义字符串（"left"/"center"/"right"），需转为 Markdown
                # 对齐标记（"---"/":---:"/"---:"）写入分隔行。直接写 "center"
                # 不匹配 _ALIGN_RE，下次解析该行会被当作数据行，导致"在下方单元格
                # 写入了 center 字符"且对齐失效。
                cells[col_idx] = _align_marker(align)
            _rebuild_table_line(sep_li, _join_row(cells))
            ctx.document.lines = lines
            ctx.mark_dirty()

    def on_table_focus() -> None:
        ctx.maybe_push_history()

    def on_table_blur() -> None:
        ctx.undo_push_pending.current = True
        # 清空表格聚焦 state：退出表格编辑态，恢复全局快捷键（_native_field_focused）
        ctx.set_table_focus_li(None)

    return {
        "on_change_code": on_change_code,
        "on_code_focus": on_code_focus,
        "on_code_blur": on_code_blur,
        "handle_code_backspace": handle_code_backspace,
        "on_change_math": on_change_math,
        "on_math_focus": on_math_focus,
        "on_math_blur": on_math_blur,
        "on_change_cell": on_change_cell,
        "on_table_op": on_table_op,
        "on_table_focus": on_table_focus,
        "on_table_blur": on_table_blur,
    }
