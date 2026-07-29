"""原文模式工厂（从 views/editor.py 闭包抽取）。

闭包组：toggle_raw / toggle_focus_mode / on_blur / on_cursor_focus /
suppress_blur_for_click / _on_raw_change

跨组依赖（通过 ctx 装配槽，调用时读取）：
- push_history（history 组）
- mark_dirty（共享）
- set_raw_draft / set_cursor_li / set_raw_mode（共享 state setter）
- raw_mode / raw_draft / undo_push_pending / selection_text_ref（共享 state/ref）
- suppress_blur（共享 ref）
- on_editor_focus（父组件回调）

依赖项：
- parser（serialize / parse_markdown）
- flet（ft.context.page）
"""

import flet as ft

import parser


def build_raw_mode(ctx):
    """构造原文模式闭包组。

    返回 dict[str, Callable]：
    toggle_raw / toggle_focus_mode / on_blur / on_cursor_focus /
    suppress_blur_for_click / on_raw_change
    """

    def toggle_raw():
        ctx.push_history()
        ctx.undo_push_pending.current = True
        if not ctx.raw_mode:
            ctx.set_raw_draft(parser.serialize(ctx.document))
            ctx.selection_text_ref.current = ""
            ctx.set_cursor_li(None)
            ctx.set_raw_mode(True)
        else:
            new_doc = parser.parse_markdown(ctx.raw_draft)
            ctx.document.lines = new_doc.lines
            ctx.mark_dirty()
            ctx.set_raw_mode(False)

    def toggle_focus_mode():
        page = ft.context.page
        if page is None:
            return
        try:
            page.window.full_screen = not bool(page.window.full_screen)
            page.update()
        except Exception:
            pass

    def on_blur():
        """cursor TextField 失焦：若非抑制，退出光标编辑态。"""
        if ctx.suppress_blur.current:
            ctx.suppress_blur.current = False
            return
        # 不主动退出：保留光标位置（Typora 式，点击别处由 on_tap 处理）

    def on_cursor_focus():
        """cursor TextField 聚焦：通知父组件当前编辑器获得焦点（拆分视口跟踪 active pane）。"""
        if ctx.on_editor_focus is not None:
            ctx.on_editor_focus()

    def suppress_blur_for_click():
        ctx.suppress_blur.current = True

    def _on_raw_change(value: str):
        """原文模式输入同步：更新草稿 + 重解析整篇 + 标脏。

        由 RawEditor 组件的 on_change 触发，文档同步逻辑集中在 editor 闭包，
        RawEditor 仅负责受控 TextField 的声明式构建。
        """
        ctx.set_raw_draft(value)
        ctx.document.lines = parser.parse_markdown(value).lines
        ctx.mark_dirty()

    return {
        "toggle_raw": toggle_raw,
        "toggle_focus_mode": toggle_focus_mode,
        "on_blur": on_blur,
        "on_cursor_focus": on_cursor_focus,
        "suppress_blur_for_click": suppress_blur_for_click,
        "on_raw_change": _on_raw_change,
    }
