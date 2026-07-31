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
- views._editor_helpers（_fix_ime_doubling / _detect_ime_compose / _compute_composing_trim）
- views.editor._helpers（_next_line_raw / _RE_O_PREFIX / _RE_FENCE_TRIGGER / _make_code_line）
"""

import parser
from models import BlockType
from utils.segment_helpers import is_fence as _is_fence
from utils.segment_helpers import line_raw as _line_raw
from views._editor_helpers import _compute_composing_trim, _detect_ime_compose, _fix_ime_doubling
from views.editor._helpers import _RE_FENCE_TRIGGER, _RE_O_PREFIX, _make_code_line, _next_line_raw

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
        """结束 IME 输入会话：同步 cursor_off + 重置状态 + 重建 cursor TextField 清空 value。

        Flet 0.86 声明式模型渲染后控件冻结，不能再用 ref.value=""; ref.update()
        命令式清空（会抛 Frozen controls cannot be updated）。改为递增 nav_seq：
        cursor TextField 的 key 含 nav_seq，key 变即重建控件，新控件 value=""
        天然清空，避免旧 value 残留导致下次输入插入陈旧文本。重建后由
        use_effect([cursor_li, nav_seq, focus_seq]) → focus_cursor_field 重聚焦。
        仅在确有活动会话时递增（无会话的浏览态切换不重建，减少无效重建）。
        """
        state = ctx.input_session_ref.current
        had_session = state["li"] >= 0 and state["start_off"] >= 0
        if had_session:
            ctx.set_cursor_off(state["start_off"] + len(state["last_value"]))
            ctx.set_cursor_line(state["li"])
        ctx.input_session_ref.current = {"li": -1, "start_off": -1, "last_value": ""}
        ctx.set_cursor_field_value("")
        if had_session:
            ctx.set_nav_seq(ctx.nav_seq + 1)

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
        # 常规点击编辑：用户点击的行必然已构建（可见才能点击），ensure_visible
        # 据 line_heights 缓存判定跳过滚动，避免滚动条滚动后估算偏差导致文档
        # 上下滚动调整。行未构建的兜底由 _focus_cursor_field 内部处理。
        ctx.ensure_visible(li, only_when_offscreen=True)

    def handle_char_input(value: str):
        """字符输入：增量式编辑（IME 友好，3 分支模型）。"""
        if ctx.cursor_li is None:
            return
        # 空值处理：composing 全部放弃时 on_change value="" 需清理文档区域
        # （last_value 非空 → 裁剪为空），不能直接 return（否则 composing 英文
        # 残留）。仅在无活动会话或 last_value 也为空时跳过空值。
        _early_sess = ctx.input_session_ref.current
        if not value:
            if (_early_sess is None
                    or _early_sess.get("li", -1) < 0
                    or not _early_sess.get("last_value")):
                return
        # 有 outward 选区时忽略 IME 输入：on_pan_start_outward 不清 cursor_li
        # （避免重渲染中断 pan 手势），选区期间的字符替换由 KeyDispatcher
        # 路由到 handle_outward_type_char 处理，此处显式拦截防止 TextField
        # 意外聚焦时 IME 输入写到旧光标位置。
        if ctx.outward_sel_ref.current is not None:
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
                ctx.set_cursor_field_value(value)
                return
            ctx.push_line_edit(li, raw)
            state["li"] = li
            state["start_off"] = ctx.cursor_off
            state["last_value"] = ""

        start_off = state["start_off"]
        last_value = state["last_value"]

        # 分支 1: 相同值 → 忽略（防重复 on_change）
        if value == last_value:
            return

        raw = _line_raw(line)
        end_off = start_off + len(last_value)

        # 分支 2: composing 取消/缩短（value 是 last_value 的真前缀）
        # IME composing 期间按回车/Esc/Backspace，IME 放弃或缩短 composing，
        # on_change 的 value 仅含已上屏部分（last_value 的真前缀）。文档区域
        # [start_off, end_off] 当前为 last_value（含 composing 英文），需据 value
        # 裁剪区域移除废字符。典型场景：五笔 composing "你vb" 按 Enter → IME 放弃
        # "vb" → value="你"；此时若 on_submit 未触发（IME 消费了 Enter），仅靠
        # 此分支清理 composing 残留，避免废字符留在编辑区。
        if last_value and last_value.startswith(value):
            new_raw = raw[:start_off] + value + raw[end_off:]
            state["last_value"] = value
            new_off = start_off + len(value)
            ctx.cursor_ref.current.reset(new_off, len(new_raw))
            _reparse_atomic(line, new_raw)
            ctx.mark_dirty()
            ctx.set_cursor_field_value(value)
            return

        # 分支 3: replace（IME 组合完成：composing ASCII 后缀被上屏非 ASCII 替换）
        # 通用检测见 _detect_ime_compose：基于 value 与 last_value 的公共前缀定位
        # composing 后缀。旧条件 all(ord<128 for c in last_value) 仅捕获首字上屏
        # （last_value 纯 ASCII），连续上屏第二字起 last_value 已含已上屏中文
        # （如 "你vb"）条件失败 → 误走 append 产生 "你vb你好"（五笔连续输入 BUG）。
        is_ime_compose = _detect_ime_compose(value, last_value)
        if is_ime_compose:
            new_raw = raw[:start_off] + value + raw[end_off:]
        # 分支 4: append
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
        # 同步 cursor_field_value：重渲染时 Flet 同步 value 到 Flutter 端，
        # 避免 value 被重置为空导致 IME 重新触发 on_change（字符吞没根因）。
        ctx.set_cursor_field_value(value)

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
        li = ctx.cursor_li
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if _is_fence(line):
            return

        # 回车前清理未上屏 IME composing 文本（安全网）：composing 期间按回车，
        # IME 放弃 composing，on_change 的 value 为已上屏前缀，handle_char_input
        # 分支 2 已裁剪文档区域。但部分 IME 可能不触发 on_change（仅 on_submit），
        # 或事件顺序不确定 → 此处据 value 再次检测裁剪，确保 composing 残留被清除。
        # 若 handle_char_input 已裁剪（last_value==value），_compute_composing_trim
        # 返回 None，不会重复裁剪。
        # push_history 之前执行：未上屏 composing 不应进入撤销栈（undo 不恢复废字符）。
        _sess = ctx.input_session_ref.current
        if _sess is not None and _sess.get("li") == li and _sess.get("start_off", -1) >= 0:
            _lv = _sess.get("last_value", "")
            _trimmed = _compute_composing_trim(value, _lv)
            if _trimmed is not None:
                _so = _sess["start_off"]
                _raw = _line_raw(line)
                _eo = _so + len(_lv)
                if 0 <= _so and _eo <= len(_raw):
                    _new_raw = _raw[:_so] + _trimmed + _raw[_eo:]
                    _reparse_atomic(line, _new_raw)
                    _sess["last_value"] = _trimmed
                    ctx.cursor_ref.current.reset(_so + len(_trimmed), len(_new_raw))
                    ctx.set_cursor_field_value(_trimmed)
                    ctx.mark_dirty()

        ctx.push_history()
        ctx.undo_push_pending.current = True
        raw = _line_raw(line)
        off = ctx.cursor_ref.current.base if ctx.cursor_ref.current else ctx.cursor_off
        off = max(0, min(off, len(raw)))
        before = raw[:off]
        after = raw[off:]

        # Typora 式：```[lang] 独占一行 + 回车（光标在行尾）→ 当前行转为代码块。
        # 仅段落行触发（标题/列表/引用的 before 含前缀，正则不匹配，自然不触发）。
        # 代码块创建走 _make_code_line（parse_markdown 合并围栏），不能用 _reparse_atomic
        # （后者无法把段落转为 CODE 块）。创建后退出光标编辑态，CodeEditor 待点击聚焦。
        if (
            line.block_type == BlockType.PARAGRAPH
            and not after.strip()
            and (m := _RE_FENCE_TRIGGER.match(before)) is not None
        ):
            new_line = _make_code_line(m.group(1), "")
            ctx.document.lines = ctx.document.lines[:li] + [new_line] + ctx.document.lines[li + 1:]
            ctx.mark_dirty()
            ctx.suppress_blur.current = True
            ctx.set_cursor_line(li)
            ctx.set_cursor_li(None)
            return

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
