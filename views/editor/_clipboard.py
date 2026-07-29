"""剪贴板 / SelectionArea 选区工厂（从 views/editor.py 闭包抽取）。

闭包组：compute_markdown_from_text / handle_delete_selection / handle_cut /
cut_current_line / apply_inline_format_to_selection / on_selection_area_change

跨组依赖（通过 ctx 装配槽，调用时读取）：
- push_history / mark_dirty（history / 共享组）
- set_cursor（cursor 组）
- apply_inline_format（行内格式组）

同组直接调用：
- handle_cut → handle_delete_selection

依赖项：
- parser（compute_markdown_from_text / reparse_line_atomic）
- utils.segment_helpers（is_fence / line_raw）
"""

import contextlib

import parser
from utils.segment_helpers import is_fence as _is_fence
from utils.segment_helpers import line_raw as _line_raw

# 高频编辑路径用原子化重解析（仅触发 1 次 observable 通知）
_reparse_atomic = parser.reparse_line_atomic


def build_clipboard(ctx):
    """构造剪贴板 / SelectionArea 选区闭包组。

    返回 dict[str, Callable]：
    compute_markdown_from_text / handle_delete_selection / handle_cut /
    cut_current_line / apply_inline_format_to_selection / on_selection_area_change
    """

    # ============ 剪贴板 / 选区 ============
    def compute_markdown_from_text(text: str) -> str:
        return parser.compute_markdown_from_text(ctx.document.lines, text)

    def handle_delete_selection(plain_text: str):
        """选区删除：优先走 outward 精确路径，无 outward_sel 时受 SelectionArea 限制。

        outward 选区（Shift+Click/方向键/拖拽）有明确 raw 偏移，可精确删除跨行
        选区——委托 delete_raw_range（内部已处理 push_history/mark_dirty/光标复位）。
        无 outward_sel 时 Flet SelectionArea 仅提供纯文本无偏移，无法可靠定位
        删除位置（纯文本可能在文档中重复出现）——清空选区文本，建议用 outward
        选区做删除/剪切。
        """
        sel = ctx.outward_sel_ref.current
        if not plain_text and sel is None:
            return
        if sel is not None:
            a_li, a_off, b_li, b_off = sel
            # 选区端点排序（与 handle_outward_delete 一致）
            if (a_li, a_off) > (b_li, b_off):
                a_li, a_off, b_li, b_off = b_li, b_off, a_li, a_off
            ctx.delete_raw_range(a_li, a_off, b_li, b_off)
            return
        # SelectionArea 选区无偏移：清空选区文本（剪切此时仅复制不删除）
        ctx.selection_text_ref.current = ""

    async def handle_cut(plain_text: str):
        if not plain_text:
            return
        clipboard = ctx.clipboard_ref.current if ctx.clipboard_ref is not None else None
        if clipboard is not None:
            try:
                md = parser.compute_markdown_from_text(ctx.document.lines, plain_text)
                await clipboard.set(md or plain_text)
            except Exception:
                pass
        handle_delete_selection(plain_text)

    async def cut_current_line():
        """无选区时剪切当前行（VSCode 行为：Ctrl+X 剪切光标所在行）。

        - 围栏块（代码/表格/公式/HR/TOC）不处理
        - 唯一行：清空内容（保留空行）
        - 多行：删除当前行，光标移到下一行（或末行删除则上一行）行首
        """
        li = ctx.cursor_li if ctx.cursor_li is not None else ctx.cursor_line
        if li is None or not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if _is_fence(line):
            return
        raw = _line_raw(line)
        ctx.push_history()
        ctx.undo_push_pending.current = True
        # 复制行文本到剪贴板：raw 已是该行 Markdown 源码（含语法标记），
        # 直接写入即可。原先调 compute_markdown_from_text 做 O(n) 全文段遍历
        # 匹配，但 raw 含语法标记与 display_text（去语法纯文本）不匹配，
        # 该调用必然返回空串后回退用 raw —— 纯粹白费 O(n) 开销。
        clipboard = ctx.clipboard_ref.current if ctx.clipboard_ref is not None else None
        if clipboard is not None and raw:
            with contextlib.suppress(Exception):
                await clipboard.set(raw)
        # 删除当前行（或清空唯一行）
        if len(ctx.document.lines) <= 1:
            _reparse_atomic(line, "")
        else:
            ctx.document.lines = ctx.document.lines[:li] + ctx.document.lines[li + 1:]
        ctx.mark_dirty()
        # 光标移到新位置
        new_li = min(li, len(ctx.document.lines) - 1)
        if 0 <= new_li < len(ctx.document.lines):
            ctx.set_cursor(new_li, 0)
            ctx.set_cursor_line(new_li)
        else:
            ctx.set_cursor_li(None)
        ctx.set_nav_seq(ctx.nav_seq + 1)

    def apply_inline_format_to_selection(fmt: str, combo: str):
        """渲染态选区包裹行内格式：有 outward_sel 走精确包裹，无则受 SelectionArea 限制。

        outward 选区有明确 raw 偏移，apply_inline_format 可精确包裹选区文本；
        无 outward_sel 时 Flet SelectionArea 仅提供纯文本无偏移，无法可靠定位
        包裹位置——建议用 outward 选区做格式包裹。
        """
        if ctx.outward_sel_ref.current is not None:
            ctx.apply_inline_format(fmt)
            return
        # SelectionArea 选区无偏移，无法可靠包裹（保持当前行为）

    def on_selection_area_change(e):
        """SelectionArea 选区变化：上报纯文本。"""
        try:
            ctx.selection_text_ref.current = e.data or ""
        except Exception:
            ctx.selection_text_ref.current = ""

    return {
        "compute_markdown_from_text": compute_markdown_from_text,
        "handle_delete_selection": handle_delete_selection,
        "handle_cut": handle_cut,
        "cut_current_line": cut_current_line,
        "apply_inline_format_to_selection": apply_inline_format_to_selection,
        "on_selection_area_change": on_selection_area_change,
    }
