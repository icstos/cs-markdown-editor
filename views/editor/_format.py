"""全文 Markdown 格式化闭包组（Shift+Alt+F）。

- format_document：对整篇文档执行 services.markdown_format.format_markdown，
  变化时推入全文撤销快照并重建 document.lines；原文模式（raw_mode）下
  格式化 raw_draft。格式化后光标回到浏览态（行结构可能整体变化，
  旧光标位置无意义），编辑器通过 nav_seq 递增强制重建光标层。

依赖项：
- parser（serialize / parse_markdown）
- services.markdown_format（纯函数格式化器）
"""

import parser
from services.markdown_format import format_markdown as _format_markdown

_reparse = parser.parse_markdown


def build_format(ctx):
    """构造全文格式化闭包组。

    返回 dict[str, Callable]：format_document
    """

    def format_document() -> None:
        """全文格式化：清理行尾/末尾换行、行内代码、任务列表、引用、中英文空格。"""
        if ctx.restoring.current:
            return
        if ctx.raw_mode:
            text = ctx.raw_draft
            formatted = _format_markdown(text)
            if formatted == text:
                return
            ctx.push_history()
            ctx.set_raw_draft(formatted)
            ctx.mark_dirty()
            return
        md = parser.serialize(ctx.document)
        formatted = _format_markdown(md)
        # 行级存储无“末尾换行”概念：serialize 拼接不携带末尾 \n，
        # 比较时对齐两侧末尾换行，避免已规范文档每次格式化都被判为变化。
        if formatted.rstrip("\n") == md.rstrip("\n"):
            return
        ctx.push_history()
        ctx.document.lines = _reparse(formatted).lines
        ctx.mark_dirty()
        # 行结构整体变化：退出编辑态，强制重建光标层
        ctx.set_cursor_li(None)
        ctx.set_cursor_off(0)
        ctx.set_nav_seq(ctx.nav_seq + 1)

    return {"format_document": format_document}
