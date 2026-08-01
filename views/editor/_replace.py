"""替换闭包组：搜索面板触发，作用于当前文档。

仅做纯文本替换 + reparse + history——反向引用展开由 Sidebar（持有 pattern）
完成，本模块不感知 regex，避免 editor → sidebar 循环依赖。

跨组依赖（通过 ctx 装配槽）：
- push_history / mark_dirty（共享 + history 组）
- restoring / undo_push_pending（history 组 ref）

依赖项：
- parser.reparse_line_atomic（行原子重解析，高频路径仅 1 次 observable 通知）
- utils.segment_helpers.line_raw（整行源码）

设计要点：
- 单次替换 / 全部替换各用 1 次 push_history 全文快照，一次 Ctrl+Z 还原
  （替换可能跨多行，LineEditSnapshot 仅支持单行恢复，故用 EditorSnapshot）
- 行内多匹配右→左处理（sorted by start desc），左侧替换不破坏右侧偏移
- restoring 守卫：撤销/重做进行中不入栈，避免污染历史
"""

import parser
from utils.segment_helpers import line_raw as _line_raw

# 高频编辑路径用原子化重解析（仅触发 1 次 observable 通知）
_reparse_atomic = parser.reparse_line_atomic


def build_replace(ctx):
    """构造替换闭包组。

    返回 dict[str, Callable]：replace_match_in_doc / replace_all_in_doc
    """

    def replace_match_in_doc(li: int, start: int, end: int, new_text: str) -> None:
        """替换单个匹配 (li, start..end) → new_text（已展开反向引用）。

        全文快照入栈 → 行 raw 切片替换 → reparse_line_atomic → mark_dirty。
        """
        if ctx.restoring.current:
            return
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        raw = _line_raw(line)
        if end > len(raw) or start < 0 or start > end:
            return
        # 全文快照：单次替换也用全文快照，一次 Ctrl+Z 还原整文档（VSCode 风格）
        ctx.push_history()
        ctx.undo_push_pending.current = True
        new_raw = raw[:start] + new_text + raw[end:]
        _reparse_atomic(line, new_raw)
        ctx.mark_dirty()

    def replace_all_in_doc(replacements) -> int:
        """批量替换：replacements = [(li, [(s, e, new_text), ...]), ...]。

        单次全文快照（一次撤销还原全部）→ 按 li 升序、行内右→左应用 →
        每行 reparse_line_atomic → mark_dirty。返回实际替换条数。
        """
        if ctx.restoring.current:
            return 0
        if not replacements:
            return 0
        ctx.push_history()
        ctx.undo_push_pending.current = True
        count = 0
        # 按 li 升序处理（不同行互不影响偏移）
        for li, spans in replacements:
            if not (0 <= li < len(ctx.document.lines)):
                continue
            line = ctx.document.lines[li]
            raw = _line_raw(line)
            # 行内右→左：左侧替换不破坏右侧偏移
            for s, e, new_text in sorted(spans, key=lambda t: t[0], reverse=True):
                if e > len(raw) or s < 0 or s > e:
                    continue
                raw = raw[:s] + new_text + raw[e:]
                count += 1
            _reparse_atomic(line, raw)
        ctx.mark_dirty()
        return count

    return {
        "replace_match_in_doc": replace_match_in_doc,
        "replace_all_in_doc": replace_all_in_doc,
    }
