"""缩进工厂（从 views/editor.py 闭包抽取）。

闭包组：indent_or_outdent / new_line_after

跨组依赖（通过 ctx 装配槽，调用时读取）：
- cursor_base / set_cursor（cursor 组）
- push_history（history 组）
- mark_dirty（共享）
- cursor_li / undo_push_pending（state 快照 / ref）

依赖项：
- parser（parse_markdown / reparse_line_atomic）
- models（BlockType）
- utils.segment_helpers（is_fence / line_raw）
- views._editor_helpers（_rebuild_list_prefix / _shift_cursor_off /
  _snap_indent_down / _snap_indent_up）
- views.editor._helpers（_inline_content / _next_line_raw /
  _LIST_INDENT_UNIT / _LIST_MAX_SPACES / _QUOTE_MAX_LEVEL）
"""

import parser
from models import BlockType
from utils.segment_helpers import line_raw as _line_raw
from views._editor_helpers import (
    _rebuild_list_prefix,
    _shift_cursor_off,
    _snap_indent_down,
    _snap_indent_up,
)
from views.editor._helpers import (
    _LIST_INDENT_UNIT,
    _LIST_MAX_SPACES,
    _QUOTE_MAX_LEVEL,
    _inline_content,
    _next_line_raw,
)

# 高频编辑路径用原子化重解析（仅触发 1 次 observable 通知，替代 reparse_line 的 2-7 次）
_reparse_atomic = parser.reparse_line_atomic


def build_indent(ctx):
    """构造缩进闭包组。

    返回 dict[str, Callable]：
    indent_or_outdent / new_line_after
    """

    def indent_or_outdent(delta: int):
        """Tab / Shift+Tab：列表缩进 / 引用层级（桌面端直觉式）。

        列表（无序 / 有序 / 任务）：
        - Tab：缩进一级（+2 空格，与 list_color_level 色阶一致），上限 10 空格（6 色）；
          有序列表缩进时序号重置为 1（嵌套子列表自然计数）。
        - Shift+Tab：减少缩进一级；顶级（0 空格）时转为普通段落（移除标记，保留内容）。
        引用：
        - Tab：增加一级嵌套（+1 个 > ），上限 6 级（6 色边框）。
        - Shift+Tab：减少一级；顶级（1 级）时转为普通段落。
        光标：保留在内容中的相对偏移（仅前缀长度变化时同步平移），自然丝滑。
        """
        if ctx.cursor_li is None:
            return
        li = ctx.cursor_li
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        raw = _line_raw(line)
        cur_off = ctx.cursor_base(len(raw))

        if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O):
            level = line.level or 0
            prefix_raw = line.segments[0].raw if line.segments else ""
            old_prefix_len = len(prefix_raw)
            content = _inline_content(line)
            body = prefix_raw.lstrip()

            if delta > 0:
                # Tab：缩进一级
                new_level = _snap_indent_up(level, _LIST_INDENT_UNIT, _LIST_MAX_SPACES)
                new_prefix = _rebuild_list_prefix(
                    new_level, body, line.block_type, line.task, line.checked, restart_num=True)
                new_raw = new_prefix + content
                ctx.push_history()
                ctx.undo_push_pending.current = True
                _reparse_atomic(line, new_raw)
                ctx.mark_dirty()
                ctx.set_cursor(li, _shift_cursor_off(cur_off, old_prefix_len, len(new_prefix), len(new_raw)))
            else:
                # Shift+Tab：降级或转段落
                if level <= 0:
                    # 顶级列表项 → 普通段落（移除标记，保留内容）
                    new_raw = content
                    ctx.push_history()
                    ctx.undo_push_pending.current = True
                    _reparse_atomic(line, new_raw)
                    ctx.mark_dirty()
                    ctx.set_cursor(li, _shift_cursor_off(cur_off, old_prefix_len, 0, len(new_raw)))
                else:
                    new_level = _snap_indent_down(level, _LIST_INDENT_UNIT)
                    new_prefix = _rebuild_list_prefix(
                        new_level, body, line.block_type, line.task, line.checked, restart_num=False)
                    new_raw = new_prefix + content
                    ctx.push_history()
                    ctx.undo_push_pending.current = True
                    _reparse_atomic(line, new_raw)
                    ctx.mark_dirty()
                    ctx.set_cursor(li, _shift_cursor_off(cur_off, old_prefix_len, len(new_prefix), len(new_raw)))
        elif line.block_type == BlockType.QUOTE:
            level = line.level or 1
            content = _inline_content(line)
            old_prefix_len = level * 2  # "> " * level

            if delta > 0:
                # Tab：增加一级嵌套，上限 6
                new_level = min(level + 1, _QUOTE_MAX_LEVEL)
                new_raw = "> " * new_level + content
                ctx.push_history()
                ctx.undo_push_pending.current = True
                _reparse_atomic(line, new_raw)
                ctx.mark_dirty()
                ctx.set_cursor(li, _shift_cursor_off(cur_off, old_prefix_len, new_level * 2, len(new_raw)))
            else:
                # Shift+Tab：降级或转段落
                if level <= 1:
                    # 顶级引用 → 普通段落
                    new_raw = content
                    ctx.push_history()
                    ctx.undo_push_pending.current = True
                    _reparse_atomic(line, new_raw)
                    ctx.mark_dirty()
                    ctx.set_cursor(li, _shift_cursor_off(cur_off, old_prefix_len, 0, len(new_raw)))
                else:
                    new_level = level - 1
                    new_raw = "> " * new_level + content
                    ctx.push_history()
                    ctx.undo_push_pending.current = True
                    _reparse_atomic(line, new_raw)
                    ctx.mark_dirty()
                    ctx.set_cursor(li, _shift_cursor_off(cur_off, old_prefix_len, new_level * 2, len(new_raw)))
        else:
            # 普通段落：Tab 插入 4 空格（Shift+Tab 无操作）
            if delta > 0:
                off = cur_off
                new_raw = raw[:off] + "    " + raw[off:]
                ctx.push_history()
                ctx.undo_push_pending.current = True
                _reparse_atomic(line, new_raw)
                ctx.mark_dirty()
                ctx.set_cursor(li, off + 4)
            else:
                # Shift+Tab 普通段落无操作，提前返回避免递增 focus_seq
                return
        # 递增 focus_seq 强制重聚焦：Tab/Shift+Tab 改变前缀但 cursor_li 不变时，
        # focus_cursor_field effect 不触发（依赖 cursor_li/nav_seq/focus_seq），
        # TextField 未重新聚焦 → 光标消失。与 set_block / _on_tap_line 一致。
        ctx.set_focus_seq(ctx.focus_seq + 1)

    def new_line_after(li: int):
        if not (0 <= li < len(ctx.document.lines)):
            return
        ctx.push_history()
        ctx.undo_push_pending.current = True
        new_raw = _next_line_raw(ctx.document.lines[li])
        new_line = parser.parse_markdown(new_raw).lines[0]
        ctx.document.lines = [*ctx.document.lines[:li + 1], new_line, *ctx.document.lines[li + 1:]]
        ctx.mark_dirty()
        ctx.set_cursor(li + 1, len(new_raw))

    return {
        "indent_or_outdent": indent_or_outdent,
        "new_line_after": new_line_after,
    }
