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

import contextlib

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
        """同步更新 ref 和 state，递增版本号强制 ft.memo 失效。"""
        ctx.secondary_cursors_ref.current = new_list
        ctx.set_secondary_cursors(new_list)
        ctx.set_secondary_cursors_version(ctx.secondary_cursors_version + 1)

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

    def broadcast_extend_home():
        """Shift+Home 同步：副光标选区扩展到行首（base 跳到行首，extent 不变）。

        Smart Home 三态：content_start → 0 → 不动。每个副光标独立判定。
        """
        cursors = ctx.secondary_cursors_ref.current
        if not cursors:
            return
        new_list = []
        for (li, base, extent) in cursors:
            target = ctx.step_home(li, base)
            if target is not None:
                new_list.append((li, target[1], extent))
            else:
                new_list.append((li, base, extent))
        _sync(new_list)

    def broadcast_extend_end():
        """Shift+End 同步：副光标选区扩展到行尾（base 跳到行尾，extent 不变）。

        已在行尾的副光标不扩展。
        """
        cursors = ctx.secondary_cursors_ref.current
        if not cursors:
            return
        new_list = []
        for (li, base, extent) in cursors:
            target = ctx.step_end(li, base)
            if target is not None:
                new_list.append((li, target[1], extent))
            else:
                new_list.append((li, base, extent))
        _sync(new_list)

    def extend_selection_home():
        """Shift+Home 多光标模式：主光标 base 跳到行首 + 副光标广播扩展。

        Smart Home 三态：content_start → 0 → 不动。
        主光标 base 跳到 target，extent 保持不变（选区锚点）。
        """
        if ctx.cursor_li is None:
            return
        li = ctx.cursor_li
        if not (0 <= li < len(ctx.document.lines)):
            return
        if _is_fence(ctx.document.lines[li]):
            return
        base = ctx.cursor_ref.current.base if ctx.cursor_ref.current else ctx.cursor_off
        target = ctx.step_home(li, base)
        if target is not None:
            new_base = target[1]
            if ctx.cursor_ref.current:
                ctx.cursor_ref.current.base = new_base
            ctx.set_cursor_off(new_base)
            ctx.preferred_col_ref.current = None
        # 同步副光标
        broadcast_extend_home()

    def extend_selection_end():
        """Shift+End 多光标模式：主光标 base 跳到行尾 + 副光标广播扩展。

        主光标 base 跳到行尾 raw_len，extent 保持不变（选区锚点）。
        已在行尾的主光标不扩展。
        """
        if ctx.cursor_li is None:
            return
        li = ctx.cursor_li
        if not (0 <= li < len(ctx.document.lines)):
            return
        if _is_fence(ctx.document.lines[li]):
            return
        base = ctx.cursor_ref.current.base if ctx.cursor_ref.current else ctx.cursor_off
        target = ctx.step_end(li, base)
        if target is not None:
            new_base = target[1]
            if ctx.cursor_ref.current:
                ctx.cursor_ref.current.base = new_base
            ctx.set_cursor_off(new_base)
            ctx.preferred_col_ref.current = None
        # 同步副光标
        broadcast_extend_end()

    # ============ 多光标剪贴板（Ctrl+C/X/V）============

    def _collect_all_cursors() -> list[tuple[int, int, int]]:
        """收集所有光标（主+副），按行号升序排序。

        返回 [(li, base, extent), ...]，主光标标记为第一个元素（is_primary 字段
        通过位置 0 判断，调用方需自行区分）。
        """
        all_cursors: list[tuple[int, int, int]] = []
        if ctx.cursor_li is not None and ctx.cursor_ref.current:
            cs = ctx.cursor_ref.current
            all_cursors.append((ctx.cursor_li, cs.base, cs.extent))
        for c in ctx.secondary_cursors_ref.current:
            all_cursors.append(c)
        all_cursors.sort(key=lambda c: c[0])
        return all_cursors

    def has_multi_cursor_selection() -> bool:
        """是否有任何光标（主+副）有选区（base != extent）。"""
        if ctx.cursor_ref.current and ctx.cursor_ref.current.base != ctx.cursor_ref.current.extent:
            return True
        for (_, base, extent) in ctx.secondary_cursors_ref.current:
            if base != extent:
                return True
        return False

    def collect_multi_cursor_text() -> list[str] | None:
        """收集所有有选区光标的选区文本，按行号升序返回文本列表。

        无任何选区时返回 None。
        """
        texts: list[str] = []
        # 主光标
        if (
            ctx.cursor_li is not None
            and ctx.cursor_ref.current
            and ctx.cursor_ref.current.base != ctx.cursor_ref.current.extent
        ):
            line = ctx.document.lines[ctx.cursor_li]
            raw = _line_raw(line)
            cs = ctx.cursor_ref.current
            sel_start = min(cs.base, cs.extent)
            sel_end = max(cs.base, cs.extent)
            texts.append(raw[sel_start:sel_end])
        # 副光标（按行号排序，保证复制顺序与视觉一致）
        for (li, base, extent) in sorted(ctx.secondary_cursors_ref.current, key=lambda c: c[0]):
            if base == extent:
                continue
            if not (0 <= li < len(ctx.document.lines)):
                continue
            line = ctx.document.lines[li]
            if _is_fence(line):
                continue
            raw = _line_raw(line)
            sel_start = min(base, extent)
            sel_end = max(base, extent)
            texts.append(raw[sel_start:sel_end])
        return texts if texts else None

    async def copy_multi_cursor_selection():
        """多光标 Ctrl+C：收集所有选区文本，用 \\n 连接写入剪贴板。"""
        texts = collect_multi_cursor_text()
        if not texts:
            return
        clipboard = ctx.clipboard_ref.current if ctx.clipboard_ref is not None else None
        if clipboard is None:
            return
        md = "\n".join(texts)
        with contextlib.suppress(Exception):
            await clipboard.set(md)

    def _delete_all_selections():
        """删除所有光标（主+副）的选区。主光标选区单独处理，副光标走广播。"""
        # 主光标选区
        if (
            ctx.cursor_li is not None
            and ctx.cursor_ref.current
            and ctx.cursor_ref.current.base != ctx.cursor_ref.current.extent
        ):
            li = ctx.cursor_li
            line = ctx.document.lines[li]
            raw = _line_raw(line)
            cs = ctx.cursor_ref.current
            sel_start = min(cs.base, cs.extent)
            sel_end = max(cs.base, cs.extent)
            new_raw = raw[:sel_start] + raw[sel_end:]
            ctx.push_line_edit(li, raw)
            _reparse_atomic(line, new_raw, notify=False)
            cs.reset(sel_start, len(new_raw))
            ctx.set_cursor_off(sel_start)
        # 副光标选区
        cursors = ctx.secondary_cursors_ref.current
        if cursors:
            new_list = []
            for (li, base, extent) in cursors:
                if base == extent:
                    new_list.append((li, base, extent))
                    continue
                if not (0 <= li < len(ctx.document.lines)):
                    new_list.append((li, base, extent))
                    continue
                line = ctx.document.lines[li]
                if _is_fence(line):
                    new_list.append((li, base, extent))
                    continue
                raw = _line_raw(line)
                sel_start = min(base, extent)
                sel_end = max(base, extent)
                new_raw = raw[:sel_start] + raw[sel_end:]
                _reparse_atomic(line, new_raw, notify=False)
                new_list.append((li, sel_start, sel_start))
            _sync(new_list)

    async def cut_multi_cursor_selection():
        """多光标 Ctrl+X：复制所有选区文本 + 删除所有选区 + 清除副光标。"""
        await copy_multi_cursor_selection()
        ctx.push_history()
        ctx.undo_push_pending.current = True
        _delete_all_selections()
        ctx.document.notify()
        ctx.mark_dirty()
        # 剪切后清除副光标（选区已删除，多光标模式结束）
        clear_secondary_cursors()
        # 递增 nav_seq 重建 TextField（cursor_off 已变，需刷新）
        ctx.set_nav_seq(ctx.nav_seq + 1)

    def paste_to_multi_cursors(text: str):
        """多光标 Ctrl+V：在每个光标处插入文本（替换选区如有）。

        VSCode 智能粘贴：若剪贴板行数 == 光标数，逐行分配（第 i 行→第 i 个光标，
        按行号排序）；否则全文插入到主光标并清除副光标（回退单光标粘贴）。

        paste_in_progress 路径（Ctrl+V / Ctrl+Shift+V）：
        - 非智能路径回退 handle_paste，由 handle_paste 内部处理重置 + 重建
        - 智能路径直接编辑文档，需自行重置标志 + 重建 TextField（cursor_li
          不变，TextField 不自动重建，需手动递增 nav_seq 清空 Flutter 端 value）
        """
        cursors = ctx.secondary_cursors_ref.current
        if not cursors or ctx.cursor_li is None:
            return
        # 结束主光标 IME 会话（粘贴不走 IME 路径）
        ctx.end_input_session()

        all_cursors = _collect_all_cursors()
        lines = text.split("\n")
        # 智能粘贴：行数 == 光标数 → 逐行分配（单行，不涉及行分割）
        smart = len(lines) == len(all_cursors) and len(all_cursors) > 1

        if not smart:
            # 非智能：全文粘贴到主光标，清除副光标（回退单光标行为）
            # handle_paste 内部会处理 paste_in_progress 重置 + TextField 重建
            clear_secondary_cursors()
            ctx.handle_paste(text, "")
            return

        # 智能分配路径：直接编辑文档，需自行处理 paste_in_progress
        paste_active = bool(ctx.paste_in_progress_ref.current)
        if paste_active:
            ctx.paste_in_progress_ref.current = False
            # 清空 input_session + cursor_field_value（同 handle_paste 路径）
            ctx.input_session_ref.current = {"li": -1, "start_off": -1, "last_value": ""}
            ctx.set_cursor_field_value("")

        ctx.push_history()
        ctx.undo_push_pending.current = True

        # 按行号升序分配（lines[i] → all_cursors[i]），编辑从下往上避免行号偏移
        # 单行粘贴不改变行号，但为一致性仍按降序处理
        new_secondary: list[tuple[int, int, int]] = []
        # 主光标在 all_cursors 中的索引（排序后位置 0 是最小行号）
        primary_idx = 0
        primary_li = ctx.cursor_li
        for i, (li, _, _) in enumerate(all_cursors):
            if li == primary_li:
                primary_idx = i
                break

        for i in range(len(all_cursors) - 1, -1, -1):
            li, base, extent = all_cursors[i]
            if not (0 <= li < len(ctx.document.lines)):
                continue
            line = ctx.document.lines[li]
            if _is_fence(line):
                continue
            raw = _line_raw(line)
            sel_start = min(base, extent)
            sel_end = max(base, extent)
            insert_text = lines[i]
            new_raw = raw[:sel_start] + insert_text + raw[sel_end:]
            _reparse_atomic(line, new_raw, notify=False)
            new_off = sel_start + len(insert_text)
            if i == primary_idx:
                # 主光标：更新 cursor_ref + cursor_off
                ctx.cursor_ref.current.reset(new_off, len(new_raw))
                ctx.set_cursor_off(new_off)
            else:
                new_secondary.append((li, new_off, new_off))

        ctx.document.notify()
        ctx.mark_dirty()
        _sync(new_secondary)
        if paste_active:
            # 智能分配：cursor_li 不变（主光标仍在原行），需手动重建 TextField
            # 清空 Flutter 端 value（原生 TextField 已写入粘贴内容）
            ctx.set_nav_seq(ctx.nav_seq + 1)

    def paste_to_multi_cursors_plain(text: str):
        """多光标 Ctrl+Shift+V：纯文本粘贴，先剥离 Markdown 语法再插入。

        Typora 式：先 strip_markdown 去除所有语法标记，再走 paste_to_multi_cursors
        智能分配到各光标。智能粘贴的行数匹配基于剥离后的文本行数。
        """
        if not text:
            return
        plain_text = parser.strip_markdown(text)
        paste_to_multi_cursors(plain_text)

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
        "extend_selection_home": extend_selection_home,
        "extend_selection_end": extend_selection_end,
        "has_multi_cursor_selection": has_multi_cursor_selection,
        "collect_multi_cursor_text": collect_multi_cursor_text,
        "copy_multi_cursor_selection": copy_multi_cursor_selection,
        "cut_multi_cursor_selection": cut_multi_cursor_selection,
        "paste_to_multi_cursors": paste_to_multi_cursors,
        "paste_to_multi_cursors_plain": paste_to_multi_cursors_plain,
    }
