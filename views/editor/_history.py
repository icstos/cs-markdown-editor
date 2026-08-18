"""历史/撤销/重做工厂（从 views/editor.py 闭包抽取）。

闭包组：_make_snapshot / _push_history / _push_line_edit /
_current_for_undo_redo / _restore_snapshot / undo / redo / _maybe_push_history

跨组依赖（通过 ctx 装配槽）：
- cursor_base（cursor 组）：_make_snapshot / _push_line_edit / _current_for_undo_redo 读取
- mark_dirty（共享）：_restore_snapshot 调用

依赖项：
- parser（serialize / parse_markdown / reparse_line_atomic）
- core.history（EditorSnapshot / LineEditSnapshot）
- utils.segment_helpers（line_raw）
- views._editor_helpers（_make_snapshot 纯函数实现）
"""

import parser
from core.history import EditorSnapshot, LineEditSnapshot
from utils.segment_helpers import line_raw as _line_raw
from views._editor_helpers import _make_snapshot as _make_snapshot_impl

# 高频编辑路径用原子化重解析（仅触发 1 次 observable 通知）
_reparse_atomic = parser.reparse_line_atomic


def build_history(ctx):
    """构造历史/撤销/重做闭包组。

    返回 dict[str, Callable]：
    make_snapshot / push_history / push_line_edit /
    current_for_undo_redo / restore_snapshot / undo / redo / maybe_push_history
    """

    def _make_snapshot() -> EditorSnapshot:
        md = ctx.raw_draft if ctx.raw_mode else parser.serialize(ctx.document)
        return _make_snapshot_impl(ctx.cursor_li, ctx.cursor_base(), ctx.raw_mode, ctx.raw_draft, md)

    def _push_history():
        if ctx.restoring.current:
            return
        ctx.history_ref.current.push(_make_snapshot())

    def _push_line_edit(li: int, before_raw: str):
        """行级快照入栈：仅单行内容编辑（字符输入 / 单字删除 / 行内格式包裹）。"""
        if ctx.restoring.current:
            return
        if not ctx.undo_push_pending.current:
            return
        ctx.history_ref.current.push(LineEditSnapshot(
            line_idx=li,
            raw=before_raw,
            cursor_li=ctx.cursor_li,
            cursor_off=ctx.cursor_base(),
            raw_mode=ctx.raw_mode,
            raw_draft=ctx.raw_draft,
        ))
        ctx.undo_push_pending.current = False

    def _current_for_undo_redo(top) -> object:
        """构造当前状态快照，供 pop_undo/pop_redo 推入反向栈。"""
        if isinstance(top, LineEditSnapshot):
            li = top.line_idx
            if 0 <= li < len(ctx.document.lines):
                cur_raw = _line_raw(ctx.document.lines[li])
            else:
                cur_raw = top.raw
            return LineEditSnapshot(
                line_idx=li,
                raw=cur_raw,
                cursor_li=ctx.cursor_li,
                cursor_off=ctx.cursor_base(),
                raw_mode=ctx.raw_mode,
                raw_draft=ctx.raw_draft,
            )
        return _make_snapshot()

    def _restore_snapshot(snap):
        ctx.restoring.current = True
        ctx.suppress_blur.current = True
        try:
            # 行级快照：仅 reparse 单行，不重建整个 document.lines
            if isinstance(snap, LineEditSnapshot):
                ctx.set_raw_mode(snap.raw_mode)
                if snap.raw_mode:
                    ctx.set_raw_draft(snap.raw_draft)
                li = snap.line_idx
                if 0 <= li < len(ctx.document.lines):
                    _reparse_atomic(ctx.document.lines[li], snap.raw)
                    ctx.mark_dirty()
                    if snap.cursor_li is not None and 0 <= snap.cursor_li < len(ctx.document.lines):
                        ctx.set_cursor_li(snap.cursor_li)
                        ctx.set_cursor_off(snap.cursor_off)
                        ctx.set_cursor_line(snap.cursor_li)
                        ctx.set_nav_seq(ctx.nav_seq + 1)
                    else:
                        ctx.set_cursor_li(None)
                return
            # 全文快照：重建 document.lines
            ctx.set_raw_mode(snap.raw_mode)
            if snap.raw_mode:
                ctx.set_raw_draft(snap.raw_draft)
                ctx.document.lines = parser.parse_markdown(snap.raw_draft).lines
                ctx.set_cursor_li(None)
            else:
                ctx.document.lines = parser.parse_markdown(snap.markdown).lines
                if snap.cursor_li is not None and 0 <= snap.cursor_li < len(ctx.document.lines):
                    ctx.set_cursor_li(snap.cursor_li)
                    ctx.set_cursor_off(snap.cursor_off)
                    ctx.set_cursor_line(snap.cursor_li)
                    ctx.set_nav_seq(ctx.nav_seq + 1)
                else:
                    ctx.set_cursor_li(None)
            ctx.mark_dirty()
        finally:
            ctx.restoring.current = False
            ctx.undo_push_pending.current = True
            # 撤销/重做后旧聚焦快照已失效（文档已在快照之后被修改）：清空会话态，
            # 下次代码块/frontmatter 修改时由 on_change_code 惰性重新捕获，
            # 保证撤销后继续编辑仍能再次撤销。
            if getattr(ctx, "code_edit_snapshot", None) is not None:
                ctx.code_edit_snapshot.current = None
                ctx.code_edit_changed.current = False

    def undo():
        hist = ctx.history_ref.current
        if not hist.undo:
            return
        current = _current_for_undo_redo(hist.undo[-1])
        prev = hist.pop_undo(current)
        if prev is not None:
            _restore_snapshot(prev)

    def redo():
        hist = ctx.history_ref.current
        if not hist.redo:
            return
        current = _current_for_undo_redo(hist.redo[-1])
        nxt = hist.pop_redo(current)
        if nxt is not None:
            _restore_snapshot(nxt)

    def _maybe_push_history():
        if ctx.undo_push_pending.current:
            _push_history()
            ctx.undo_push_pending.current = False

    return {
        "make_snapshot": _make_snapshot,
        "push_history": _push_history,
        "push_line_edit": _push_line_edit,
        "current_for_undo_redo": _current_for_undo_redo,
        "restore_snapshot": _restore_snapshot,
        "undo": undo,
        "redo": redo,
        "maybe_push_history": _maybe_push_history,
    }
