"""多光标工厂（VSCode 式 Alt+Click / Alt+Shift+Click）。

闭包组：add_secondary_cursor / add_column_cursors / clear_secondary_cursors /
broadcast_char_input / broadcast_backspace / broadcast_delete /
broadcast_move_left / broadcast_move_right / broadcast_extend_left /
broadcast_extend_right / broadcast_submit / has_secondary_cursors

设计原理：
- 主光标持有唯一 IME TextField，副光标为纯视觉标记 + 直接编辑文档
- 主光标每次编辑后，broadcast_* 将相同 delta 同步到所有副光标
- 副光标编辑用 reparse_line_atomic(notify=False) + document.notify() 统一触发
  唯一一次重渲染（多行编辑避免 N 次 observable 通知）
- state/ref 双镜像：set_secondary_cursors 触发重渲染，secondary_cursors_ref
  供 IME 期间同步读取（避免 set_state 滞后）

跨组依赖（通过 ctx 装配槽，调用时读取）：
- set_secondary_cursors / secondary_cursors_ref（state/ref 镜像）
- cursor_li / cursor_off / cursor_ref（主光标状态）
- mark_dirty（共享）

依赖项：
- parser（parse_markdown / reparse_line_atomic）
- utils.segment_helpers（is_fence / line_raw）
- views.editor._helpers（_next_line_raw）
"""

import parser
from utils.segment_helpers import is_fence as _is_fence
from utils.segment_helpers import line_raw as _line_raw
from views.editor._helpers import _next_line_raw

# 高频编辑路径用原子化重解析（仅触发 1 次 observable 通知）
_reparse_atomic = parser.reparse_line_atomic


def build_multi_cursor(ctx):
    """构造多光标闭包组。

    返回 dict[str, Callable]：
    add_secondary_cursor / add_column_cursors / clear_secondary_cursors /
    broadcast_char_input / broadcast_backspace / broadcast_delete /
    broadcast_move_left / broadcast_move_right / broadcast_extend_left /
    broadcast_extend_right / broadcast_submit / has_secondary_cursors
    """

    def _sync(new_list):
        """同步更新 ref 和 state。"""
        ctx.secondary_cursors_ref.current = new_list
        ctx.set_secondary_cursors(new_list)

    def has_secondary_cursors() -> bool:
        return bool(ctx.secondary_cursors_ref.current)

    def clear_secondary_cursors():
        if ctx.secondary_cursors_ref.current:
            _sync([])

    def add_secondary_cursor(li: int, off: int):
        """Alt+Click：在 (li, off) 切换副光标。

        VSCode 式 toggle：该位置已有副光标则移除，否则添加。
        围栏块（CODE/MATH/TOC/TABLE）不添加。
        """
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if _is_fence(line):
            return
        raw_len = len(_line_raw(line))
        off = max(0, min(off, raw_len))
        existing = list(ctx.secondary_cursors_ref.current)
        # Toggle：若已有副光标在该位置，移除
        for i, (eli, ebase, eext) in enumerate(existing):
            if eli == li and ebase == off and eext == off:
                _sync(existing[:i] + existing[i + 1:])
                return
        # 添加新副光标（无选区：base == extent == off）
        existing.append((li, off, off))
        _sync(existing)

    def add_column_cursors(target_li: int, target_off: int):
        """Alt+Shift+Click：列光标。

        在主光标行与 target_li 之间的所有非围栏行的对应 off 位置插入副光标。
        off 钳制到各行 raw 长度（短行光标落在行尾，VSCode 行为）。
        主光标行不添加（主光标已在该行）。
        """
        primary_li = ctx.cursor_li
        if primary_li is None:
            return
        if not (0 <= target_li < len(ctx.document.lines)):
            return
        lo = min(primary_li, target_li)
        hi = max(primary_li, target_li)
        existing = list(ctx.secondary_cursors_ref.current)
        for li in range(lo, hi + 1):
            if li == primary_li:
                continue
            line = ctx.document.lines[li]
            if _is_fence(line):
                continue
            raw_len = len(_line_raw(line))
            off = min(target_off, raw_len)
            cursor = (li, off, off)
            # 避免重复添加
            if cursor not in existing:
                existing.append(cursor)
        _sync(existing)

    # ============ 编辑广播 ============

    def broadcast_char_input(removed_len: int, inserted: str):
        """主光标字符输入后，同步到副光标。

        removed_len：主光标删除的字符数（IME composing 替换时为旧 composing 长度）
        inserted：主光标插入的文本

        对每个副光标：
        - 有选区（base != extent）：替换选区为 inserted
        - 无选区：删 removed_len 字符后插入 inserted（镜像主光标 delta）
        """
        cursors = ctx.secondary_cursors_ref.current
        if not cursors:
            return
        new_list = []
        changed = False
        for (li, base, extent) in cursors:
            if not (0 <= li < len(ctx.document.lines)):
                continue
            line = ctx.document.lines[li]
            if _is_fence(line):
                new_list.append((li, base, extent))
                continue
            raw = _line_raw(line)
            sel_start = min(base, extent)
            sel_end = max(base, extent)
            if sel_start != sel_end:
                new_raw = raw[:sel_start] + inserted + raw[sel_end:]
                new_base = sel_start + len(inserted)
            else:
                remove_start = max(0, base - removed_len)
                new_raw = raw[:remove_start] + inserted + raw[base:]
                new_base = remove_start + len(inserted)
            _reparse_atomic(line, new_raw, notify=False)
            new_list.append((li, new_base, new_base))
            changed = True
        if changed:
            ctx.document.notify()
            ctx.mark_dirty()
            _sync(new_list)

    def broadcast_backspace():
        """主光标 Backspace 后，同步到副光标。

        有选区：删除选区。无选区：删前一个字符。行首不删（不跨行合并）。
        """
        cursors = ctx.secondary_cursors_ref.current
        if not cursors:
            return
        new_list = []
        changed = False
        for (li, base, extent) in cursors:
            if not (0 <= li < len(ctx.document.lines)):
                continue
            line = ctx.document.lines[li]
            if _is_fence(line):
                new_list.append((li, base, extent))
                continue
            raw = _line_raw(line)
            sel_start = min(base, extent)
            sel_end = max(base, extent)
            if sel_start != sel_end:
                new_raw = raw[:sel_start] + raw[sel_end:]
                new_base = sel_start
            elif base > 0:
                new_raw = raw[:base - 1] + raw[base:]
                new_base = base - 1
            else:
                new_list.append((li, base, extent))
                continue
            _reparse_atomic(line, new_raw, notify=False)
            new_list.append((li, new_base, new_base))
            changed = True
        if changed:
            ctx.document.notify()
            ctx.mark_dirty()
            _sync(new_list)

    def broadcast_delete():
        """主光标 Delete 后，同步到副光标。

        有选区：删除选区。无选区：删后一个字符。行尾不删（不跨行合并）。
        """
        cursors = ctx.secondary_cursors_ref.current
        if not cursors:
            return
        new_list = []
        changed = False
        for (li, base, extent) in cursors:
            if not (0 <= li < len(ctx.document.lines)):
                continue
            line = ctx.document.lines[li]
            if _is_fence(line):
                new_list.append((li, base, extent))
                continue
            raw = _line_raw(line)
            sel_start = min(base, extent)
            sel_end = max(base, extent)
            if sel_start != sel_end:
                new_raw = raw[:sel_start] + raw[sel_end:]
                new_base = sel_start
            elif base < len(raw):
                new_raw = raw[:base] + raw[base + 1:]
                new_base = base
            else:
                new_list.append((li, base, extent))
                continue
            _reparse_atomic(line, new_raw, notify=False)
            new_list.append((li, new_base, new_base))
            changed = True
        if changed:
            ctx.document.notify()
            ctx.mark_dirty()
            _sync(new_list)

    # ============ 移动广播 ============

    def broadcast_move_left():
        """← 同步：副光标左移，行首跳上一行行尾（跳过围栏块）。"""
        cursors = ctx.secondary_cursors_ref.current
        if not cursors:
            return
        new_list = []
        for (li, base, extent) in cursors:
            if base > 0:
                new_list.append((li, base - 1, base - 1))
            elif li > 0:
                prev = ctx.document.lines[li - 1]
                if not _is_fence(prev):
                    prev_len = len(_line_raw(prev))
                    new_list.append((li - 1, prev_len, prev_len))
                else:
                    new_list.append((li, base, extent))
            else:
                new_list.append((li, base, extent))
        _sync(new_list)

    def broadcast_move_right():
        """→ 同步：副光标右移，行尾跳下一行行首（跳过围栏块）。"""
        cursors = ctx.secondary_cursors_ref.current
        if not cursors:
            return
        new_list = []
        for (li, base, extent) in cursors:
            if not (0 <= li < len(ctx.document.lines)):
                new_list.append((li, base, extent))
                continue
            line = ctx.document.lines[li]
            if _is_fence(line):
                new_list.append((li, base, extent))
                continue
            raw = _line_raw(line)
            if base < len(raw):
                new_list.append((li, base + 1, base + 1))
            elif li < len(ctx.document.lines) - 1:
                nxt = ctx.document.lines[li + 1]
                if not _is_fence(nxt):
                    new_list.append((li + 1, 0, 0))
                else:
                    new_list.append((li, base, extent))
            else:
                new_list.append((li, base, extent))
        _sync(new_list)

    def broadcast_extend_left():
        """Shift+← 同步：副光标选区左端扩展（base 左移，extent 不变）。"""
        cursors = ctx.secondary_cursors_ref.current
        if not cursors:
            return
        new_list = []
        for (li, base, extent) in cursors:
            if base > 0:
                new_list.append((li, base - 1, extent))
            elif li > 0:
                prev = ctx.document.lines[li - 1]
                if not _is_fence(prev):
                    prev_len = len(_line_raw(prev))
                    new_list.append((li - 1, prev_len, extent))
                else:
                    new_list.append((li, base, extent))
            else:
                new_list.append((li, base, extent))
        _sync(new_list)

    def broadcast_extend_right():
        """Shift+→ 同步：副光标选区右端扩展（base 右移，extent 不变）。"""
        cursors = ctx.secondary_cursors_ref.current
        if not cursors:
            return
        new_list = []
        for (li, base, extent) in cursors:
            if not (0 <= li < len(ctx.document.lines)):
                new_list.append((li, base, extent))
                continue
            line = ctx.document.lines[li]
            if _is_fence(line):
                new_list.append((li, base, extent))
                continue
            raw = _line_raw(line)
            if base < len(raw):
                new_list.append((li, base + 1, extent))
            elif li < len(ctx.document.lines) - 1:
                nxt = ctx.document.lines[li + 1]
                if not _is_fence(nxt):
                    new_list.append((li + 1, 0, extent))
                else:
                    new_list.append((li, base, extent))
            else:
                new_list.append((li, base, extent))
        _sync(new_list)

    def broadcast_submit(value: str):
        """Enter 同步：副光标分行。

        从下往上处理，避免行号偏移影响已处理的副光标。
        每个副光标所在行在 off 处分割，续行加列表/引用前缀。
        """
        cursors = ctx.secondary_cursors_ref.current
        if not cursors:
            return
        # 按行号降序排序：从下往上处理，避免行号偏移
        sorted_cursors = sorted(cursors, key=lambda c: -c[0])
        new_list = []
        line_shift = 0  # 下方已插入的行数
        for (li, base, extent) in sorted_cursors:
            adj_li = li + line_shift
            if not (0 <= adj_li < len(ctx.document.lines)):
                new_list.append((li, base, extent))
                continue
            line = ctx.document.lines[adj_li]
            if _is_fence(line):
                new_list.append((li, base, extent))
                continue
            raw = _line_raw(line)
            sel_start = min(base, extent)
            sel_end = max(base, extent)
            # 有选区时先删除选区再分割
            if sel_start != sel_end:
                raw = raw[:sel_start] + raw[sel_end:]
                off = sel_start
            else:
                off = max(0, min(base, len(raw)))
            before = raw[:off]
            after = raw[off:]
            cont_prefix = _next_line_raw(line)
            _reparse_atomic(line, before, notify=False)
            new_line = parser.parse_markdown(cont_prefix + after).lines[0]
            ctx.document.lines.insert(adj_li + 1, new_line)
            line_shift += 1
            new_off = len(cont_prefix)
            new_list.append((adj_li + 1, new_off, new_off))
        ctx.document.notify()
        ctx.mark_dirty()
        _sync(new_list)

    # ============ 主光标选区扩展（多光标模式 Shift+Arrow）============

    def extend_selection_left():
        """Shift+Left 多光标模式：主光标 base 左移 + 副光标广播扩展。

        主光标：cursor_ref.current.base 左移 1，extent 保持不变（选区锚点）。
        副光标：broadcast_extend_left 同步扩展。
        行首时主光标不跨行（保持位置），副光标各自独立判定。
        """
        if ctx.cursor_li is None:
            return
        li = ctx.cursor_li
        if not (0 <= li < len(ctx.document.lines)):
            return
        if _is_fence(ctx.document.lines[li]):
            return
        raw_len = len(_line_raw(ctx.document.lines[li]))
        base = ctx.cursor_ref.current.base if ctx.cursor_ref.current else ctx.cursor_off
        if base > 0:
            new_base = base - 1
            if ctx.cursor_ref.current:
                ctx.cursor_ref.current.base = new_base
            ctx.set_cursor_off(new_base)
            ctx.preferred_col_ref.current = None
        # 同步副光标
        broadcast_extend_left()

    def extend_selection_right():
        """Shift+Right 多光标模式：主光标 base 右移 + 副光标广播扩展。

        主光标：cursor_ref.current.base 右移 1，extent 保持不变（选区锚点）。
        副光标：broadcast_extend_right 同步扩展。
        行尾时主光标不跨行（保持位置），副光标各自独立判定。
        """
        if ctx.cursor_li is None:
            return
        li = ctx.cursor_li
        if not (0 <= li < len(ctx.document.lines)):
            return
        if _is_fence(ctx.document.lines[li]):
            return
        raw = _line_raw(ctx.document.lines[li])
        base = ctx.cursor_ref.current.base if ctx.cursor_ref.current else ctx.cursor_off
        if base < len(raw):
            new_base = base + 1
            if ctx.cursor_ref.current:
                ctx.cursor_ref.current.base = new_base
            ctx.set_cursor_off(new_base)
            ctx.preferred_col_ref.current = None
        # 同步副光标
        broadcast_extend_right()

    return {
        "add_secondary_cursor": add_secondary_cursor,
        "add_column_cursors": add_column_cursors,
        "clear_secondary_cursors": clear_secondary_cursors,
        "broadcast_char_input": broadcast_char_input,
        "broadcast_backspace": broadcast_backspace,
        "broadcast_delete": broadcast_delete,
        "broadcast_move_left": broadcast_move_left,
        "broadcast_move_right": broadcast_move_right,
        "broadcast_extend_left": broadcast_extend_left,
        "broadcast_extend_right": broadcast_extend_right,
        "broadcast_submit": broadcast_submit,
        "has_secondary_cursors": has_secondary_cursors,
        "extend_selection_left": extend_selection_left,
        "extend_selection_right": extend_selection_right,
    }
