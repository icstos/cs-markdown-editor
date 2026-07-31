"""块级格式工厂（从 views/editor.py 闭包抽取）。

闭包组：set_block / toggle_task / toggle_task_at_cursor /
format_task / format_table / change_lang

跨组依赖（通过 ctx 装配槽，调用时读取）：
- push_history / make_snapshot / maybe_push_history（history 组）
- cursor_base / set_cursor（cursor 组）
- mark_dirty（共享）

组内依赖（直接调用，不经 ctx）：
- toggle_task_at_cursor → toggle_task
- format_task → set_block
- format_table → set_block

依赖项：
- parser（reparse_line_atomic）
- models（BlockType / Line / Segment / SegType）
- utils.segment_helpers（line_raw）
- views.editor._helpers（_inline_content / _RE_UO_MARKER / _make_code_line）
- views.table_view（_join_row）
"""

import parser
from models import BlockType, Line, Segment, SegType
from utils.segment_helpers import line_raw as _line_raw
from views.editor._helpers import _RE_UO_MARKER, _inline_content, _make_code_line
from views.table_view import _join_row

# 高频编辑路径用原子化重解析（仅触发 1 次 observable 通知）
_reparse_atomic = parser.reparse_line_atomic


def build_blocks(ctx):
    """构造块级格式闭包组。

    返回 dict[str, Callable]：
    set_block / toggle_task / toggle_task_at_cursor /
    format_task / format_table / change_lang
    """

    def set_block(block_type: BlockType, level: int = 0, task: bool = False):
        """切换当前行块类型（Ctrl+0~6 / 工具栏）。"""
        li = ctx.cursor_li if ctx.cursor_li is not None else ctx.cursor_line
        if not (0 <= li < len(ctx.document.lines)):
            return
        ctx.push_history()
        ctx.undo_push_pending.current = True
        line = ctx.document.lines[li]
        # 记录旧前缀长度和光标位置（_reparse_atomic 前）
        old_prefix_len = 0
        if line.segments and line.segments[0].seg_type in (
            SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX,
        ):
            old_prefix_len = len(line.segments[0].raw)
        old_off = ctx.cursor_base(len(_line_raw(line)))
        content = _inline_content(line)
        if block_type == BlockType.HEADING:
            new_raw = "#" * level + " " + content
        elif block_type == BlockType.LIST_UO:
            indent_sp = " " * line.level if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O) else ""
            # task=True：转为任务列表项（- [ ] content）；默认 False 走普通无序列表
            prefix = "- [ ] " if task else "- "
            new_raw = indent_sp + prefix + content
        elif block_type == BlockType.LIST_O:
            indent_sp = " " * line.level if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O) else ""
            new_raw = f"{indent_sp}1. " + content
        elif block_type == BlockType.QUOTE:
            new_raw = "> " + content
        elif block_type == BlockType.CODE:
            # CODE 是围栏岛屿（整行 ```\n...\n``` 合并为单编辑单元），不能用
            # _reparse_atomic：其普通块分支走 _build_line/_detect_block，而后者不
            # 识别围栏（围栏仅在 parse_markdown 全量解析时合并），会导致行停留在
            # PARAGRAPH（工具栏"代码块"按钮曾因此失效）。改用 _make_code_line
            # （基于 parse_markdown）整体替换，与 TABLE 一致的早返回模式。
            new_line = _make_code_line("", content)
            ctx.document.lines = ctx.document.lines[:li] + [new_line] + ctx.document.lines[li + 1:]
            ctx.mark_dirty()
            # 退出光标编辑态，代码块 CodeEditor 待用户点击聚焦编辑
            ctx.set_cursor_line(li)
            ctx.set_cursor_li(None)
            return
        elif block_type == BlockType.MATH:
            new_raw = f"$$\n{content}\n$$"
        elif block_type == BlockType.TABLE:
            # TABLE 是多行结构（header + sep + data 行），不能用 _reparse_atomic
            # （后者面向单行 CODE/MATH/HR），需直接切片替换 document.lines。
            # 当前行已是 TABLE 时静默返回（避免重复创建）。
            if line.block_type == BlockType.TABLE:
                return
            header_raw = _join_row([content, ""])
            sep_raw = _join_row(["---", "---"])
            data_raw = _join_row(["", ""])

            def _mk_table_line(raw: str) -> Line:
                nl = Line(block_type=BlockType.TABLE, raw=raw)
                nl.segments = [Segment(SegType.TEXT, raw, raw)]
                return nl

            ctx.document.lines = (
                [*ctx.document.lines[:li], _mk_table_line(header_raw), _mk_table_line(sep_raw), _mk_table_line(data_raw), *ctx.document.lines[li + 1:]]
            )
            ctx.mark_dirty()
            # 退出光标编辑态，进入表格编辑态（TableView auto_focus 首格）
            ctx.set_cursor_line(li)
            ctx.set_cursor_li(None)
            ctx.set_table_focus_li(li)
            return
        elif block_type == BlockType.HR:
            new_raw = "---"
        else:
            new_raw = content
        _reparse_atomic(line, new_raw)
        ctx.mark_dirty()
        if block_type == BlockType.MATH:
            # 公式块：退出光标编辑态，自动进入公式编辑态（Typora 式：插入后立即可输入）
            ctx.set_cursor_line(li)
            ctx.set_cursor_li(None)
            ctx.math_edit_snapshot.current = ctx.make_snapshot()
            ctx.math_edit_changed.current = False
            ctx.math_focus_ref.current = li
            ctx.set_math_focus_li(li)
        elif block_type == BlockType.HR:
            # HR 创建后进入编辑态，光标在 --- 末尾（Typora 式：插入即可编辑）
            ctx.set_cursor_line(li)
            ctx.set_cursor(li, len(new_raw))
        elif block_type == BlockType.TOC:
            ctx.set_cursor_line(li)
            ctx.set_cursor_li(None)
        else:
            # 保持光标在内容部分的相对位置（Typora 式：切换标题/列表级别不跳到文字首部）
            new_line = ctx.document.lines[li]
            new_prefix_len = 0
            if new_line.segments and new_line.segments[0].seg_type in (
                SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX,
            ):
                new_prefix_len = len(new_line.segments[0].raw)
            # 光标在内容部分的相对偏移（光标在前缀部分时视为 0）
            content_off = max(0, old_off - old_prefix_len)
            new_raw_len = len(_line_raw(new_line))
            new_off = min(new_prefix_len + content_off, new_raw_len)
            ctx.set_cursor(li, new_off)

    # ============ 任务列表 ============
    def toggle_task(li: int):
        if not (0 <= li < len(ctx.document.lines)):
            return
        ctx.push_history()
        ctx.undo_push_pending.current = True
        line = ctx.document.lines[li]
        line.checked = not line.checked
        # 重建 raw 以反映勾选状态
        prefix_raw = line.segments[0].raw if line.segments else "- "
        content = _inline_content(line)
        body = prefix_raw.lstrip()
        marker = m.group(1) if (m := _RE_UO_MARKER.match(body)) else "-"
        new_prefix = f"{' ' * (line.level or 0)}{marker} [{'x' if line.checked else ' '}] "
        new_raw = new_prefix + content
        _reparse_atomic(line, new_raw)
        ctx.mark_dirty()

    def toggle_task_at_cursor():
        """Alt+C：切换当前任务列表项的勾选状态。

        编辑态用 cursor_li；浏览态用 cursor_line（最近交互行）兜底。
        非任务行静默忽略，避免在普通段落按 Alt+C 产生副作用。
        """
        li = ctx.cursor_li if ctx.cursor_li is not None else ctx.cursor_line
        if li is None or not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if not line.task:
            return
        toggle_task(li)

    def format_task():
        """Ctrl+Shift+T：当前行转为任务列表项（- [ ] content）。

        复用 set_block 的 LIST_UO 分支 + task=True 标志，光标位置由
        set_block 末尾的重定位逻辑自动保持（前缀 2→6 字符自动处理）。
        """
        set_block(BlockType.LIST_UO, task=True)

    def format_table():
        """Ctrl+Alt+T：当前行转为 2×2 表格（1 表头 + 1 数据行）。

        复用 set_block 的 TABLE 分支：当前行内容作为表头第一列，创建后退出光标
        编辑态并进入表格编辑态（TableView auto_focus 表头首格）。
        """
        set_block(BlockType.TABLE)

    def change_lang(li: int, new_lang: str):
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if line.block_type != BlockType.CODE:
            return
        ctx.maybe_push_history()
        line.lang = new_lang
        code = line.segments[0].text if line.segments else ""
        full = f"```{new_lang}\n{code}\n```" if code else f"```{new_lang}\n```"
        _reparse_atomic(line, full)
        ctx.mark_dirty()

    return {
        "set_block": set_block,
        "toggle_task": toggle_task,
        "toggle_task_at_cursor": toggle_task_at_cursor,
        "format_task": format_task,
        "format_table": format_table,
        "change_lang": change_lang,
    }
