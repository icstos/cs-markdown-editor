"""光标核心工厂（IME 核心组，紧耦合不拆散）。

闭包组：_end_input_session / _set_cursor / _cursor_base / _on_tap_line /
handle_char_input / handle_paste / backspace_core / delete_core / on_submit

跨组依赖（通过 ctx 装配槽，调用时读取）：
- push_history / push_line_edit（history 组）
- handle_outward_delete（outward 组）
- set_outward_sel（共享）
- ensure_visible（scroll 组）
- mark_dirty（共享）

硬约束：
- cursor_ref 必须是 use_ref（非 state），避免重渲染打断 IME
- 透明 cursor TextField 不设 value 属性；value 清空由 use_effect 异步执行
- nav_seq 仅在撤销/重做时递增（同行输入不递增以保 IME 组合态）
- IME 热路径必须用 _reparse_atomic（仅 1 次 observable 通知）

依赖项：
- parser（parse_markdown / reparse_line_atomic）
- models（BlockType）
- utils.segment_helpers（is_fence / line_raw）
- views._editor_helpers（_fix_ime_doubling）
- views.editor._helpers（_next_line_raw / _RE_O_PREFIX）
"""

import parser
from models import BlockType
from utils.segment_helpers import is_fence as _is_fence
from utils.segment_helpers import line_raw as _line_raw
from views._editor_helpers import _fix_ime_doubling
from views.editor._helpers import _RE_O_PREFIX, _next_line_raw

# 高频编辑路径用原子化重解析（仅触发 1 次 observable 通知）
_reparse_atomic = parser.reparse_line_atomic


def build_cursor(ctx):
    """构造光标核心闭包组（IME 核心组，紧耦合不拆散）。

    返回 dict[str, Callable]：
    cursor_base / end_input_session / set_cursor / on_tap_line /
    handle_char_input / handle_paste / backspace_core / delete_core / on_submit
    """

    def _cursor_base(raw_len: int | None = None) -> int:
        """IME 实时光标偏移（ref 优先，回退 state；可选钳制到 raw_len）。"""
        base = ctx.cursor_ref.current.base if ctx.cursor_ref.current else ctx.cursor_off
        if raw_len is not None:
            base = max(0, min(base, raw_len))
        return base

    def _end_input_session():
        """结束 IME 输入会话：同步 cursor_off + 重置状态 + 触发清空 value。"""
        state = ctx.input_session_ref.current
        if state["li"] >= 0 and state["start_off"] >= 0:
            ctx.set_cursor_off(state["start_off"] + len(state["last_value"]))
            ctx.set_cursor_line(state["li"])
        ctx.input_session_ref.current = {"li": -1, "start_off": -1, "last_value": ""}
        ctx.set_clear_value_seq(ctx.clear_value_seq + 1)

    def _set_cursor(li: int | None, off: int = 0, *, clear_preferred: bool = True):
        """设置光标位置：cursor_li + cursor_off（不递增 nav_seq 以保 IME 组合态）。"""
        if li is None:
            ctx.set_cursor_li(None)
            _end_input_session()
            return
        if not (0 <= li < len(ctx.document.lines)):
            return
        raw_len = len(_line_raw(ctx.document.lines[li]))
        off = max(0, min(off, raw_len))

        # 检测输入会话是否需要结束（光标不连续或切换行）
        state = ctx.input_session_ref.current
        if state["li"] >= 0:
            if state["li"] != li:
                _end_input_session()
            elif state["start_off"] >= 0:
                expected_off = state["start_off"] + len(state["last_value"])
                if off != expected_off:
                    _end_input_session()

        ctx.set_cursor_li(li)
        ctx.set_cursor_off(off)
        ctx.set_cursor_line(li)
        if clear_preferred:
            ctx.preferred_col_ref.current = None
        ctx.cursor_ref.current.reset(off, raw_len)

    def _on_tap_line(li: int, raw_off: int):
        """渲染层点击：定位光标到 (li, raw_off)。"""
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        # 点击非公式行时退出公式编辑态
        if ctx.math_focus_li is not None and ctx.math_focus_li != li:
            ctx.set_math_focus_li(None)
        # 围栏块点击：更新 cursor_line，不进入光标编辑
        if _is_fence(line):
            ctx.set_cursor_line(li)
            if ctx.outward_sel_ref.current is not None:
                ctx.set_outward_sel(None)
            return
        # 既有向外选区：先清除，然后继续定位光标到点击位置
        if ctx.outward_sel_ref.current is not None:
            ctx.set_outward_sel(None)
        _set_cursor(li, raw_off)
        # 点击同一位置时 cursor_li/cursor_off 不变，use_effect 不触发重新聚焦；
        # 但点击已使 cursor TextField 失焦——递增 focus_seq 强制重聚焦，避免光标丢失。
        ctx.set_focus_seq(ctx.focus_seq + 1)
        ctx.ensure_visible(li)

    def handle_char_input(value: str):
        """字符输入：增量式编辑（IME 友好，3 分支模型）。"""
        if ctx.cursor_li is None or not value:
            return

        # IME 翻倍修正
        _last_val = (ctx.input_session_ref.current or {}).get("last_value", "")
        value = _fix_ime_doubling(value, _last_val)

        li = ctx.cursor_li
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if _is_fence(line):
            return

        state = ctx.input_session_ref.current

        # 新会话启动
        if state["li"] != li or state["start_off"] < 0:
            raw = _line_raw(line)
            off = ctx.cursor_off
            if off + len(value) <= len(raw) and raw[off:off + len(value)] == value:
                state["li"], state["start_off"], state["last_value"] = li, off, value
                ctx.cursor_ref.current.reset(off + len(value), len(raw))
                return
            ctx.push_line_edit(li, raw)
            state["li"] = li
            state["start_off"] = ctx.cursor_off
            state["last_value"] = ""

        start_off = state["start_off"]
        last_value = state["last_value"]

        # 分支 1: ignore
        if value == last_value:
            return
        if last_value and last_value.startswith(value):
            return

        raw = _line_raw(line)
        end_off = start_off + len(last_value)

        # 分支 2: replace（IME 组合完成）
        is_ime_compose = (
            last_value
            and any(ord(c) > 127 for c in value)
            and all(ord(c) < 128 for c in last_value)
        )
        if is_ime_compose:
            new_raw = raw[:start_off] + value + raw[end_off:]
        # 分支 3: append
        else:
            if value.startswith(last_value):
                new_part = value[len(last_value):]
            else:
                new_part = value
                state["start_off"] = end_off
                start_off = end_off
            new_raw = raw[:end_off] + new_part + raw[end_off:]

        state["last_value"] = value
        new_off = start_off + len(value)
        ctx.cursor_ref.current.reset(new_off, len(new_raw))
        _reparse_atomic(line, new_raw)
        ctx.mark_dirty()

    def handle_paste(clip_text: str, old_draft: str = ""):
        """多行粘贴：在光标处插入 clip_text，多行时拆分为新行。"""
        if ctx.cursor_li is None or not clip_text:
            return
        li = ctx.cursor_li
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if _is_fence(line):
            return
        ctx.push_history()
        ctx.undo_push_pending.current = True
        raw = _line_raw(line)
        off = _cursor_base(len(raw))
        parts = clip_text.split("\n")
        if len(parts) == 1:
            new_raw = raw[:off] + parts[0] + raw[off:]
            _reparse_atomic(line, new_raw)
            ctx.mark_dirty()
            ctx.suppress_blur.current = True
            _set_cursor(li, off + len(parts[0]))
        else:
            before = raw[:off]
            after = raw[off:]
            _reparse_atomic(line, before + parts[0])
            middle = [parser.parse_markdown(p).lines[0] for p in parts[1:-1]]
            last_raw = parts[-1] + after
            last_line = parser.parse_markdown(last_raw).lines[0]
            ctx.document.lines = (
                ctx.document.lines[:li + 1] + middle + [last_line] + ctx.document.lines[li + 1:]
            )
            ctx.mark_dirty()
            last_li = li + 1 + len(middle)
            ctx.suppress_blur.current = True
            _set_cursor(last_li, len(parts[-1]))

    def backspace_core():
        """光标级 Backspace：删光标前字符；行首则与前一行合并。"""
        if ctx.outward_sel_ref.current is not None:
            ctx.handle_outward_delete()
            return
        if ctx.cursor_li is None:
            return
        li = ctx.cursor_li
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if _is_fence(line):
            return
        off = ctx.cursor_ref.current.base if ctx.cursor_ref.current else ctx.cursor_off
        if off > 0:
            raw = _line_raw(line)
            ctx.push_line_edit(li, raw)
            new_raw = raw[:off - 1] + raw[off:]
            _reparse_atomic(line, new_raw)
            ctx.mark_dirty()
            _set_cursor(li, off - 1)
        elif li > 0:
            prev = ctx.document.lines[li - 1]
            if _is_fence(prev):
                return
            ctx.push_history()
            ctx.undo_push_pending.current = True
            prev_raw = _line_raw(prev)
            cur_raw = _line_raw(line)
            junction = len(prev_raw)
            merged = prev_raw + cur_raw
            _reparse_atomic(prev, merged)
            ctx.document.lines = ctx.document.lines[:li] + ctx.document.lines[li + 1:]
            ctx.mark_dirty()
            ctx.suppress_blur.current = True
            _set_cursor(li - 1, junction)

    def delete_core():
        """光标级 Delete：删光标后字符；行尾则与下一行合并。"""
        if ctx.outward_sel_ref.current is not None:
            ctx.handle_outward_delete()
            return
        if ctx.cursor_li is None:
            return
        li = ctx.cursor_li
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if _is_fence(line):
            return
        raw = _line_raw(line)
        off = ctx.cursor_ref.current.base if ctx.cursor_ref.current else ctx.cursor_off
        if off < len(raw):
            ctx.push_line_edit(li, raw)
            new_raw = raw[:off] + raw[off + 1:]
            _reparse_atomic(line, new_raw)
            ctx.mark_dirty()
            _set_cursor(li, off)
        elif li < len(ctx.document.lines) - 1:
            nxt = ctx.document.lines[li + 1]
            if _is_fence(nxt):
                return
            ctx.push_history()
            ctx.undo_push_pending.current = True
            junction = len(raw)
            merged = raw + _line_raw(nxt)
            _reparse_atomic(line, merged)
            ctx.document.lines = ctx.document.lines[:li + 1] + ctx.document.lines[li + 2:]
            ctx.mark_dirty()
            ctx.suppress_blur.current = True
            _set_cursor(li, junction)

    def on_submit(value: str):
        """Enter：在光标处分割行，续行加列表/引用前缀。"""
        if ctx.cursor_li is None:
            return
        ctx.push_history()
        ctx.undo_push_pending.current = True
        li = ctx.cursor_li
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if _is_fence(line):
            return
        raw = _line_raw(line)
        off = ctx.cursor_ref.current.base if ctx.cursor_ref.current else ctx.cursor_off
        off = max(0, min(off, len(raw)))
        before = raw[:off]
        after = raw[off:]

        # 标题：before 空 → 清空前缀；否则分割成两行
        if line.block_type == BlockType.HEADING:
            if not before.strip():
                _reparse_atomic(line, after.lstrip())
                ctx.mark_dirty()
                _set_cursor(li, 0)
                return
            _reparse_atomic(line, before)
            new_line = parser.parse_markdown(after).lines[0]
            ctx.document.lines = [*ctx.document.lines[:li + 1], new_line, *ctx.document.lines[li + 1:]]
            ctx.mark_dirty()
            ctx.suppress_blur.current = True
            _set_cursor(li + 1, 0)
            return

        # 列表 / 引用：before 仅前缀（空内容）→ 退出列表/引用
        if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O, BlockType.QUOTE):
            if not before.strip():
                stripped = after.lstrip()
                if line.block_type == BlockType.QUOTE:
                    stripped = stripped.lstrip("> ")
                _reparse_atomic(line, stripped)
                ctx.mark_dirty()
                _set_cursor(li, 0)
                return
            if line.block_type == BlockType.LIST_UO and before.rstrip() in ("-", "*", "+"):
                _reparse_atomic(line, after.lstrip())
                ctx.mark_dirty()
                _set_cursor(li, 0)
                return
            if line.block_type == BlockType.LIST_O and _RE_O_PREFIX.match(before.rstrip()):
                _reparse_atomic(line, after.lstrip())
                ctx.mark_dirty()
                _set_cursor(li, 0)
                return

        # 默认：分割当前行，续行加列表/引用前缀
        cont_prefix = _next_line_raw(line)
        _reparse_atomic(line, before)
        new_line = parser.parse_markdown(cont_prefix + after).lines[0]
        ctx.document.lines = [*ctx.document.lines[:li + 1], new_line, *ctx.document.lines[li + 1:]]
        ctx.mark_dirty()
        ctx.suppress_blur.current = True
        _set_cursor(li + 1, len(cont_prefix))

    return {
        "cursor_base": _cursor_base,
        "end_input_session": _end_input_session,
        "set_cursor": _set_cursor,
        "on_tap_line": _on_tap_line,
        "handle_char_input": handle_char_input,
        "handle_paste": handle_paste,
        "backspace_core": backspace_core,
        "delete_core": delete_core,
        "on_submit": on_submit,
    }
