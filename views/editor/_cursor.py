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
- cursor TextField value=cursor_field_value（非空镜像），重渲染时 Flet 同步到
  Flutter 端保留 IME 内部状态（Phase 0 验证：value="" 会被同步清空打断 IME）
- nav_seq 仅在撤销/重做/光标移动/会话结束时递增（同行输入不递增以保 IME 组合态）
- IME 热路径必须用 _reparse_atomic（仅 1 次 observable 通知）

依赖项：
- parser（parse_markdown / reparse_line_atomic）
- models（BlockType）
- utils.segment_helpers（is_fence / line_raw）
- views._editor_helpers（_fix_ime_doubling）
- views.editor._helpers（_next_line_raw / _RE_O_PREFIX / _RE_FENCE_TRIGGER / _make_code_line）
"""

import time

import parser
from models import BlockType
from utils.segment_helpers import PREFIX_SEGTYPES
from utils.segment_helpers import is_fence as _is_fence
from utils.segment_helpers import line_raw as _line_raw
from views._editor_helpers import _fix_ime_doubling
from views.editor._helpers import (
    _RE_FENCE_TRIGGER,
    _RE_O_PREFIX,
    _RE_UO_MARKER,
    _inline_content,
    _make_code_line,
    _next_line_raw,
)

# 高频编辑路径用原子化重解析（仅触发 1 次 observable 通知）
_reparse_atomic = parser.reparse_line_atomic

# 同行移动脉冲节流窗口（秒）：Flutter 光标闪烁半周期约 250ms，窗口内只重建
# 一次 TextField 即可保证光标始终处于可见相位；长按/连击方向键时不逐键重建
# （逐键仅更新位置渲染），显著降低 TextField 销毁重建与焦点往返开销。
_CURSOR_PULSE_INTERVAL = 0.25
# 模块级别名：测试可 patch（views.editor._cursor._monotonic）控制节流时间
_monotonic = time.monotonic


def _prefix_sig(line) -> tuple:
    """块级前缀结构签名：前缀段类型/raw + level + task。

    用于检测输入过程中引用/列表/标题前缀的出现、消失或变化（如输入 ">"
    创建引用、"> " 补齐前缀、">> " 加深层级）。前缀结构变化时：
    - 渲染树形状变化 → cursor TextField 重建 → 需 nav_seq 递增重聚焦（否则光标消失）
    - 会话 last_value 含旧前缀（引用前缀渲染零宽度）→ 光标 X 漂移 → 需结束会话
    """
    if line.segments and line.segments[0].seg_type in PREFIX_SEGTYPES:
        s0 = line.segments[0]
        return (s0.seg_type, s0.raw, line.level, line.task)
    return (None, None, line.level, line.task)


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

    def _end_input_session(*, rebuild: bool = True):
        """结束 IME 输入会话：同步 cursor_off + 重置状态 + 清空 cursor_field_value。

        rebuild=True（默认）：递增 nav_seq 强制 TextField 重建（key 变 → 控件销毁
        +重建+重聚焦）。用于同行会话结束（光标位置不连续但 li 不变）。
        rebuild=False：不递增 nav_seq，仅靠 cursor_field_value="" 通过 Flet diff
        同步清空 TextField 内部值。用于跨行场景（cursor_li 变化已使 TextField
        key 变化 → 自动重建，nav_seq++ 冗余）。
        """
        state = ctx.input_session_ref.current
        had_session = state["li"] >= 0 and state["start_off"] >= 0
        if had_session:
            ctx.set_cursor_off(state["start_off"] + len(state["last_value"]))
            ctx.set_cursor_line(state["li"])
        ctx.input_session_ref.current = {"li": -1, "start_off": -1, "last_value": ""}
        ctx.set_cursor_field_value("")
        if had_session and rebuild:
            ctx.set_nav_seq(ctx.nav_seq + 1)

    def _set_cursor(li: int | None, off: int = 0, *, clear_preferred: bool = True):
        """设置光标位置：cursor_li + cursor_off + 节流移动脉冲（nav_seq++）。

        移动脉冲：同行移动递增 nav_seq → TextField key 变化 → 重建 + 重聚焦，
        Flutter 光标在重建聚焦瞬间以不透明相位重启闪烁，保证快速移动时光标
        持续可视。性能优化（低负载、极速响应）：
        - 同位置移动（点击同一位置等）：零状态写入，直接返回
        - 跨行移动：key=li 已触发重建，不再递增 nav_seq（省 1 次 state 更新）
        - 会话结束已重建（_end_input_session 递增 nav_seq）时不再重复脉冲
        - 快速连击/长按方向键：_CURSOR_PULSE_INTERVAL 窗口内只脉冲一次
          （首个移动立即脉冲保证即时可见；位置更新本身仍逐键渲染）
        打字路径（handle_char_input / _move_cursor_inline）不经过本函数，
        IME 组合态不受影响。
        """
        if li is None:
            ctx.set_cursor_li(None)
            _end_input_session()
            return
        if not (0 <= li < len(ctx.document.lines)):
            return
        raw_len = len(_line_raw(ctx.document.lines[li]))
        off = max(0, min(off, raw_len))

        # 会话清理（先于同位置判断：异位会话需结束，即使落点与当前一致）
        state = ctx.input_session_ref.current
        rebuilt = False
        if state["li"] >= 0:
            if state["li"] != li:
                # 跨行：cursor_li 变化已使 TextField key 变化 → 自动重建，
                # nav_seq++ 冗余，用 rebuild=False 跳过（减少 1 次 state 更新）
                _end_input_session(rebuild=False)
            elif state["start_off"] >= 0:
                expected_off = state["start_off"] + len(state["last_value"])
                if off != expected_off:
                    _end_input_session()  # nav_seq++ → 已重建
                    rebuilt = True

        old_li, old_off = ctx.cursor_li, ctx.cursor_off
        if li == old_li and off == old_off:
            # 同位置移动：无任何状态变化（Flet setter 同值自动跳过），零开销返回
            return

        ctx.set_cursor_li(li)
        ctx.set_cursor_off(off)
        ctx.set_cursor_line(li)
        if clear_preferred:
            ctx.preferred_col_ref.current = None
        ctx.cursor_ref.current.reset(off, raw_len)

        # 移动脉冲（仅同行移动需要；跨行 key=li 已重建、会话结束已重建则跳过）。
        # 节流：窗口内只重建一次 TextField，避免长按/连击方向键逐键销毁重建。
        if li == old_li and not rebuilt:
            now = _monotonic()
            if now - (ctx.cursor_pulse_ref.current or 0.0) >= _CURSOR_PULSE_INTERVAL:
                ctx.cursor_pulse_ref.current = now
                ctx.set_nav_seq(ctx.nav_seq + 1)

    def _move_cursor_inline(li: int, new_off: int, new_raw_len: int):
        """同行内轻量光标移动：不递增 nav_seq，不重建 TextField。

        用于 Backspace 等同行编辑，避免 _set_cursor → _end_input_session →
        nav_seq++ → TextField 重建的性能开销（每次 Backspace 重建控件+重聚焦）。

        策略：
        - 光标在会话末尾删除最后字符 → 缩短 last_value + cursor_field_value，
          保持会话连续（下次输入不需重建会话，省 push_line_edit）
        - 光标不在会话末尾 → 静默清空会话（cursor_field_value="" 不重建，
          Flet diff 同步空串到 Flutter 清理 IME 内部状态）
        - 无活动会话 → 仅更新 cursor_off + cursor_ref
        """
        state = ctx.input_session_ref.current
        if state["li"] == li and state["start_off"] >= 0:
            old_lv = state["last_value"]
            sess_end = state["start_off"] + len(old_lv)
            if new_off == sess_end - 1 and old_lv:
                # 光标在会话末尾删除最后字符：缩短 last_value，保持会话连续
                new_lv = old_lv[:-1]
                state["last_value"] = new_lv
                ctx.set_cursor_field_value(new_lv)
            else:
                # 光标不在会话末尾：静默清空会话（不递增 nav_seq）
                ctx.input_session_ref.current = {
                    "li": -1,
                    "start_off": -1,
                    "last_value": "",
                }
                ctx.set_cursor_field_value("")
        ctx.set_cursor_off(new_off)
        ctx.preferred_col_ref.current = None
        ctx.cursor_ref.current.reset(new_off, new_raw_len)

    def _on_tap_line(li: int, raw_off: int):
        """渲染层点击：定位光标到 (li, raw_off)。

        Alt+Click → 切换副光标（VSCode 式多光标）。
        Alt+Shift+Click → 列光标（主光标行与点击行之间所有行插入对应位置光标）。
        """
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        # Alt+Click / Alt+Shift+Click：多光标操作（优先于其他路由）
        # RenderedLine._on_tap 已确保 Alt 时调用此函数，此处检查 alt_pressed_ref
        # 决定具体多光标动作。
        if ctx.alt_pressed_ref.current:
            if ctx.shift_pressed_ref.current:
                ctx.add_column_cursors(li, raw_off)
            else:
                ctx.add_secondary_cursor(li, raw_off)
            # 点击 GestureDetector 会使主光标 TextField 失焦，递增 focus_seq
            # 强制重聚焦，否则多光标模式下无法继续键盘输入。
            ctx.suppress_blur.current = True
            ctx.set_focus_seq(ctx.focus_seq + 1)
            return
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
        # 常规点击（无 Alt）：清除多光标（退出多光标模式）
        if ctx.secondary_cursors_ref.current:
            ctx.clear_secondary_cursors()
        _set_cursor(li, raw_off)
        # 点击同一位置时 cursor_li/cursor_off 不变，use_effect 不触发重新聚焦；
        # 但点击已使 cursor TextField 失焦——递增 focus_seq 强制重聚焦，避免光标丢失。
        ctx.set_focus_seq(ctx.focus_seq + 1)
        # 常规点击编辑：用户点击的行必然已构建（可见才能点击），ensure_visible
        # 据 line_heights 缓存判定跳过滚动，避免滚动条滚动后估算偏差导致文档
        # 上下滚动调整。行未构建的兜底由 _focus_cursor_field 内部处理。
        ctx.ensure_visible(li, only_when_offscreen=True)

    def handle_char_input(value: str):
        """字符输入：delta 计算同步文档（IME 友好，单分支模型）。

        用公共前缀计算 old_value→new_value 的 removed/inserted delta，统一处理
        ASCII 追加 / IME composing 增长 / IME 上屏替换 / composing 取消/缩短。
        替代旧 4 分支模型（ignore / composing-cancel / replace / append），
        无需 _detect_ime_compose / _compute_composing_trim 辅助函数。

        _fix_ime_doubling 保留：Flet 同步 value 打断 IME 导致翻倍根因仍在
        （Phase 0 验证：value="" 会被 Flet 同步清空，cursor_field_value 非空
        镜像保留 IME 状态，但特定 IME 仍可能翻倍）。

        自动覆盖场景：
        - ASCII 追加：old="" new="a" → cp=0, insert "a"
        - composing 增长：old="w" new="wq" → cp=1, insert "q"
        - IME 上屏：old="wq" new="你" → cp=0, removed="wq" insert="你"
        - 连续上屏第二字：old="你vb" new="你好" → cp=1, removed="vb" insert="好"
        - composing 取消：old="你vb" new="你" → cp=1, removed="vb" insert=""
        - composing 全部放弃：old="vb" new="" → cp=0, removed="vb" insert=""
        - 无变化：old="a" new="a" → cp=1, removed="" insert="" → 忽略
        """
        if ctx.cursor_li is None:
            return
        # 有 outward 选区时忽略 IME 输入：on_pan_start_outward 不清 cursor_li
        # （避免重渲染中断 pan 手势），选区期间的字符替换由 KeyDispatcher
        # 路由到 handle_outward_type_char 处理，此处显式拦截防止 TextField
        # 意外聚焦时 IME 输入写到旧光标位置。
        if ctx.outward_sel_ref.current is not None:
            return
        # 粘贴进行中：跳过原生 TextField 单行粘贴的 on_change 干扰。
        # Ctrl+V 时单行 TextField（multiline=False）会把多行文本的 \n 移除拼接
        # 成一行触发 on_change，与 _do_paste_check 的 handle_paste 形成重复插入。
        # KeyDispatcher 在 Ctrl+V 时置 paste_in_progress=True，此处统一拦截，
        # 由 _do_paste_check 走 handle_paste 处理（单行/多行均统一路径）。
        if ctx.paste_in_progress_ref.current:
            return

        # IME 翻倍修正（保留：Flet 同步 value 打断 IME 导致翻倍根因仍在）
        _last_val = (ctx.input_session_ref.current or {}).get("last_value", "")
        value = _fix_ime_doubling(value, _last_val)

        # 空值处理：composing 全部放弃时 value="" 需清理文档区域
        # （last_value 非空 → delta 裁剪为空），不能直接 return。
        # 仅在无活动会话或 last_value 也为空时跳过空值。
        if not value and not _last_val:
            return

        li = ctx.cursor_li
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if _is_fence(line):
            return

        # 多光标模式：主光标有选区时（Shift+Arrow 扩展），输入替换选区
        # 选区由 cursor_ref.base/extent 跟踪（非 outward_sel），需单独处理。
        # 副光标的选区由 broadcast_char_input 内部按各自 base/extent 处理。
        if ctx.secondary_cursors_ref.current and ctx.cursor_ref.current:
            cs = ctx.cursor_ref.current
            if cs.base != cs.extent and value:
                raw = _line_raw(line)
                sel_start = min(cs.base, cs.extent)
                sel_end = max(cs.base, cs.extent)
                new_raw = raw[:sel_start] + value + raw[sel_end:]
                new_off = sel_start + len(value)
                ctx.push_line_edit(li, raw)
                _reparse_atomic(line, new_raw)
                ctx.mark_dirty()
                cs.reset(new_off, len(new_raw))
                ctx.set_cursor_off(new_off)
                ctx.set_cursor_field_value(value)
                # 启动输入会话供后续 IME composing
                _state = ctx.input_session_ref.current
                _state["li"] = li
                _state["start_off"] = sel_start
                _state["last_value"] = value
                # 同步副光标：broadcast_char_input 内部处理各自选区
                ctx.broadcast_char_input(sel_end - sel_start, value)
                return

        state = ctx.input_session_ref.current

        # 新会话启动
        if state["li"] != li or state["start_off"] < 0:
            raw = _line_raw(line)
            off = ctx.cursor_off
            if off + len(value) <= len(raw) and raw[off : off + len(value)] == value:
                state["li"], state["start_off"], state["last_value"] = li, off, value
                ctx.cursor_ref.current.reset(off + len(value), len(raw))
                ctx.set_cursor_field_value(value)
                return
            ctx.push_line_edit(li, raw)
            state["li"] = li
            state["start_off"] = ctx.cursor_off
            state["last_value"] = ""

        start_off = state["start_off"]
        old_value = state["last_value"]
        new_value = value

        # Delta 计算：公共前缀后的 removed/inserted
        cp = 0
        while (
            cp < len(old_value)
            and cp < len(new_value)
            and old_value[cp] == new_value[cp]
        ):
            cp += 1
        removed = old_value[cp:]  # 被删除部分
        inserted = new_value[cp:]  # 被插入部分

        # 无变化：忽略（防重复 on_change）
        if not removed and not inserted:
            return

        raw = _line_raw(line)
        doc_start = start_off + cp
        doc_end = start_off + len(old_value)

        # 钳制到合法范围（防御性：cursor_base 与文档失同步时不过度越界）
        doc_start = max(0, min(doc_start, len(raw)))
        doc_end = max(0, min(doc_end, len(raw)))
        if doc_start > doc_end:
            doc_start = doc_end

        new_raw = raw[:doc_start] + inserted + raw[doc_end:]

        state["last_value"] = new_value
        new_off = start_off + len(new_value)
        ctx.cursor_ref.current.reset(new_off, len(new_raw))
        # 先设置 cursor_field_value（state），再触发 observable 通知：
        # 确保 line.notify() 引发的重渲染使用最新的 cursor_field_value，
        # 避免双重渲染（render #1 用旧 value → IME 重复 on_change）。
        ctx.set_cursor_field_value(new_value)
        old_psig = _prefix_sig(line)
        _reparse_atomic(line, new_raw)
        ctx.mark_dirty()
        # 多光标：同步 delta（removed_len, inserted）到所有副光标
        ctx.broadcast_char_input(len(removed), inserted)
        # 块级前缀结构变化（输入 ">" 创建引用 / "- " 创建列表 / "#" 创建
        # 标题，或补齐/加深前缀）：
        # 1. 渲染树形状变化使 cursor TextField 重建（与 on_submit 同型问题），
        #    需显式递增 nav_seq 触发 use_effect 重聚焦，否则光标消失；
        # 2. 会话 last_value 仍含旧前缀（如 "> "），但引用前缀渲染零宽度，
        #    光标 X 漂移（_eff_value 含前缀 → caret 偏右一个前缀宽度）；
        # 3. last_value 含前缀时后续 Backspace 的 _move_cursor_inline 会缩短
        #    前缀字符，导致"异常编辑引用/列表标识"。
        # 结束会话清空 cursor_field_value，下次输入启动新会话时 start_off 已
        # 在前缀之后，last_value 只含内容部分，cursor_overlay 位置正确。
        if _prefix_sig(line) != old_psig:
            _end_input_session(rebuild=False)
            ctx.suppress_blur.current = True
            ctx.set_nav_seq(ctx.nav_seq + 1)

    def handle_paste(clip_text: str, old_draft: str = ""):
        """多行粘贴：在光标处插入 clip_text，多行时拆分为新行。

        paste_in_progress 路径（Ctrl+V / Ctrl+Shift+V）：
        - 插入完成后重置 paste_in_progress_ref（恢复 on_change 处理）
        - 清空 input_session（防止下次 on_change 用旧 session 计算错误 delta）
        - 单行粘贴时 cursor_li 不变，TextField 不自动重建，需手动递增 nav_seq
          重建控件清空 Flutter 端 value（原生 TextField 已写入拼接内容）
        - 多行粘贴时 cursor_li 变化使 TextField key 变化自动重建，无需手动重建
        """
        if ctx.cursor_li is None or not clip_text:
            # 防御性：重置 paste_in_progress（避免 handle_char_input 永久被拦截）
            if ctx.paste_in_progress_ref.current:
                ctx.paste_in_progress_ref.current = False
            return
        li = ctx.cursor_li
        if not (0 <= li < len(ctx.document.lines)):
            if ctx.paste_in_progress_ref.current:
                ctx.paste_in_progress_ref.current = False
            return
        line = ctx.document.lines[li]
        if _is_fence(line):
            if ctx.paste_in_progress_ref.current:
                ctx.paste_in_progress_ref.current = False
            return
        # 检测 paste_in_progress 路径：重置标志 + 清空 input_session
        paste_active = bool(ctx.paste_in_progress_ref.current)
        if paste_active:
            ctx.paste_in_progress_ref.current = False
            # 清空 input_session：防止下次 on_change 用旧 session 计算错误 delta
            # （原生 TextField 粘贴可能已触发 on_change 但被 handle_char_input 跳过，
            # input_session 仍保留旧值，需重置以启动新会话）
            ctx.input_session_ref.current = {
                "li": -1,
                "start_off": -1,
                "last_value": "",
            }
            ctx.set_cursor_field_value("")
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
            if paste_active:
                # 单行粘贴：cursor_li 不变，TextField 不自动重建，需手动递增
                # nav_seq 重建控件清空 Flutter 端 value（原生 TextField 已写入
                # 粘贴内容，不重建会导致下次 on_change 计算错误 delta）
                ctx.set_nav_seq(ctx.nav_seq + 1)
        else:
            before = raw[:off]
            after = raw[off:]
            # reparse 用 notify=False 静默更新当前行，由紧接的切片赋值 +
            # document.notify() 统一触发唯一一次重渲染（多行粘贴原触发
            # line.notify() + ObservableList 通知 + document.notify() = 3 次冗余通知）
            _reparse_atomic(line, before + parts[0], notify=False)
            middle = [parser.parse_markdown(p).lines[0] for p in parts[1:-1]]
            last_raw = parts[-1] + after
            last_line = parser.parse_markdown(last_raw).lines[0]
            # 原地插入多行 + notify()，避免 O(N) 列表重建
            ctx.document.lines[li + 1 : li + 1] = middle + [last_line]
            ctx.document.notify()
            ctx.mark_dirty()
            last_li = li + 1 + len(middle)
            ctx.suppress_blur.current = True
            _set_cursor(last_li, len(parts[-1]))
            # 多行粘贴：cursor_li 变化使 TextField key 变化自动重建，无需手动重建

    def handle_paste_plain(clip_text: str, old_draft: str = ""):
        """纯文本粘贴（Ctrl+Shift+V）：剥离 Markdown 语法后插入。

        Typora 式：先 strip_markdown 去除所有语法标记（# / ** / ` / []() 等），
        再走 handle_paste 插入纯文本。多行文本中每行独立剥离，保持行结构。
        """
        if not clip_text:
            return
        plain_text = parser.strip_markdown(clip_text)
        handle_paste(plain_text, old_draft)

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
        # 多光标模式：主光标有选区时，删除选区（非单字符）
        if ctx.secondary_cursors_ref.current and ctx.cursor_ref.current:
            cs = ctx.cursor_ref.current
            if cs.base != cs.extent:
                raw = _line_raw(line)
                sel_start = min(cs.base, cs.extent)
                sel_end = max(cs.base, cs.extent)
                new_raw = raw[:sel_start] + raw[sel_end:]
                ctx.push_line_edit(li, raw)
                _reparse_atomic(line, new_raw)
                ctx.mark_dirty()
                cs.reset(sel_start, len(new_raw))
                ctx.set_cursor_off(sel_start)
                ctx.broadcast_backspace()
                return
        off = ctx.cursor_ref.current.base if ctx.cursor_ref.current else ctx.cursor_off
        # HR 行行首 Backspace：删除 HR 转为空段落（不合并 --- 到前一行，Typora 式）
        if line.block_type == BlockType.HR and off == 0:
            ctx.clear_secondary_cursors()
            ctx.push_history()
            ctx.undo_push_pending.current = True
            new_line = parser.parse_markdown("").lines[0]
            ctx.document.lines[li] = new_line
            ctx.document.notify()
            ctx.mark_dirty()
            ctx.suppress_blur.current = True
            ctx.set_cursor(li, 0)
            return
        if off > 0:
            raw = _line_raw(line)
            # 任务行内容首 Backspace：转为普通列表项（Typora 式，不破坏前缀）
            # 光标在内容起点（off == prefix_len）时，- [ ] → -（无论内容是否为空）。
            # 空内容也降级：否则走默认删除会删掉前缀字符 "]"，破坏 task 标识。
            if line.task and line.segments and off == len(line.segments[0].raw):
                ctx.clear_secondary_cursors()
                ctx.push_history()
                ctx.undo_push_pending.current = True
                prefix_raw = line.segments[0].raw
                body = prefix_raw.lstrip()
                marker = m.group(1) if (m := _RE_UO_MARKER.match(body)) else "-"
                indent_sp = " " * (line.level or 0)
                new_prefix = f"{indent_sp}{marker} "
                content = _inline_content(line)
                new_raw = new_prefix + content
                _reparse_atomic(line, new_raw)
                ctx.mark_dirty()
                ctx.suppress_blur.current = True
                _set_cursor(li, len(new_prefix))
                return
            ctx.push_line_edit(li, raw)
            new_raw = raw[: off - 1] + raw[off:]
            _reparse_atomic(line, new_raw)
            ctx.mark_dirty()
            # 轻量光标更新：不递增 nav_seq，避免 TextField 重建（性能优化）
            _move_cursor_inline(li, off - 1, len(new_raw))
            # 多光标：同步 Backspace 到所有副光标
            ctx.broadcast_backspace()
        elif li > 0:
            prev = ctx.document.lines[li - 1]
            if _is_fence(prev):
                return
            ctx.clear_secondary_cursors()
            ctx.push_history()
            ctx.undo_push_pending.current = True
            prev_raw = _line_raw(prev)
            cur_raw = _line_raw(line)
            junction = len(prev_raw)
            merged = prev_raw + cur_raw
            # reparse 用 notify=False 静默更新前一行，由紧接的 del + document.notify()
            # 统一触发唯一一次重渲染（合并行原触发 line.notify() + ObservableList 通知
            # + document.notify() = 3 次冗余通知）
            _reparse_atomic(prev, merged, notify=False)
            # 原地删除行 + notify()，避免 O(N) 列表重建
            del ctx.document.lines[li]
            ctx.document.notify()
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
        # 多光标模式：主光标有选区时，删除选区（非单字符）
        if ctx.secondary_cursors_ref.current and ctx.cursor_ref.current:
            cs = ctx.cursor_ref.current
            if cs.base != cs.extent:
                sel_start = min(cs.base, cs.extent)
                sel_end = max(cs.base, cs.extent)
                new_raw = raw[:sel_start] + raw[sel_end:]
                ctx.push_line_edit(li, raw)
                _reparse_atomic(line, new_raw)
                ctx.mark_dirty()
                cs.reset(sel_start, len(new_raw))
                ctx.set_cursor_off(sel_start)
                ctx.broadcast_delete()
                return
        off = ctx.cursor_ref.current.base if ctx.cursor_ref.current else ctx.cursor_off
        # HR 行行尾 Delete：删除 HR 转为空段落（不合并下一行，Typora 式）
        if line.block_type == BlockType.HR and off >= len(raw):
            ctx.clear_secondary_cursors()
            ctx.push_history()
            ctx.undo_push_pending.current = True
            new_line = parser.parse_markdown("").lines[0]
            ctx.document.lines[li] = new_line
            ctx.document.notify()
            ctx.mark_dirty()
            ctx.suppress_blur.current = True
            ctx.set_cursor(li, 0)
            return
        if off < len(raw):
            ctx.push_line_edit(li, raw)
            new_raw = raw[:off] + raw[off + 1 :]
            _reparse_atomic(line, new_raw)
            ctx.mark_dirty()
            # 光标位置不变，仅更新 cursor_ref 的 raw_len（不触发 _set_cursor 开销）
            ctx.cursor_ref.current.reset(off, len(new_raw))
            # 多光标：同步 Delete 到所有副光标
            ctx.broadcast_delete()
        elif li < len(ctx.document.lines) - 1:
            nxt = ctx.document.lines[li + 1]
            if _is_fence(nxt):
                return
            ctx.clear_secondary_cursors()
            ctx.push_history()
            ctx.undo_push_pending.current = True
            junction = len(raw)
            merged = raw + _line_raw(nxt)
            # reparse 用 notify=False 静默更新当前行，由紧接的 del + document.notify()
            # 统一触发唯一一次重渲染（合并行原触发 line.notify() + ObservableList 通知
            # + document.notify() = 3 次冗余通知）
            _reparse_atomic(line, merged, notify=False)
            # 原地删除行 + notify()，避免 O(N) 列表重建
            del ctx.document.lines[li + 1]
            ctx.document.notify()
            ctx.mark_dirty()
            ctx.suppress_blur.current = True
            _set_cursor(li, junction)

    def on_submit(
        value: str,
        *,
        override_li: int | None = None,
        override_off: int | None = None,
        skip_history: bool = False,
    ):
        """Enter：在光标处分割行，续行加列表/引用前缀。

        override_li/override_off：外部调用（如 outward 选区删除后换行）时强制指定
        光标位置，绕过 ctx.cursor_li 快照（浏览态时为 None）。
        skip_history：跳过 push_history（已由调用方 push 过，避免双倍历史记录）。
        """
        li = override_li if override_li is not None else ctx.cursor_li
        if li is None:
            return
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if _is_fence(line):
            return

        # IME 清理仅 TextField 原生 on_submit 触发时需要（override 调用跳过）
        if override_li is None:
            # 回车前清理未上屏 IME composing 文本（安全网）：composing 期间按回车，
            # IME 放弃 composing，on_change 的 value 为已上屏前缀。handle_char_input
            # 的 delta 模型已裁剪文档区域，但部分 IME 可能不触发 on_change（仅 on_submit），
            # 或事件顺序不确定 → 此处用 delta 模型再次检测同步，确保 composing 残留被清除。
            # 若 handle_char_input 已同步（last_value==value），delta 无变化不会重复裁剪。
            # push_history 之前执行：未上屏 composing 不应进入撤销栈（undo 不恢复废字符）。
            _sess = ctx.input_session_ref.current
            if (
                _sess is not None
                and _sess.get("li") == li
                and _sess.get("start_off", -1) >= 0
            ):
                _lv = _sess.get("last_value", "")
                if _lv and _lv != value:
                    _so = _sess["start_off"]
                    _raw = _line_raw(line)
                    # delta 计算：公共前缀后的 removed/inserted
                    _cp = 0
                    while (
                        _cp < len(_lv) and _cp < len(value) and _lv[_cp] == value[_cp]
                    ):
                        _cp += 1
                    _removed = _lv[_cp:]
                    _inserted = value[_cp:]
                    if _removed or _inserted:
                        _ds = max(0, min(_so + _cp, len(_raw)))
                        _de = max(0, min(_so + len(_lv), len(_raw)))
                        if _ds > _de:
                            _ds = _de
                        _new_raw = _raw[:_ds] + _inserted + _raw[_de:]
                        _reparse_atomic(line, _new_raw)
                        _sess["last_value"] = value
                        ctx.cursor_ref.current.reset(_so + len(value), len(_new_raw))
                        ctx.set_cursor_field_value(value)
                        ctx.mark_dirty()
                        # 多光标：同步 IME composing 清理 delta 到副光标
                        ctx.broadcast_char_input(len(_removed), _inserted)

        if not skip_history:
            ctx.push_history()
            ctx.undo_push_pending.current = True
        # HR 行 Enter：在下方插入新空行（不分割 ---，Typora 式）
        if line.block_type == BlockType.HR:
            ctx.clear_secondary_cursors()
            new_line = parser.parse_markdown("").lines[0]
            ctx.document.lines.insert(li + 1, new_line)
            ctx.document.notify()
            ctx.mark_dirty()
            ctx.suppress_blur.current = True
            ctx.set_cursor(li + 1, 0)
            return
        raw = _line_raw(line)
        if override_off is not None:
            off = max(0, min(override_off, len(raw)))
        else:
            off = (
                ctx.cursor_ref.current.base
                if ctx.cursor_ref.current
                else ctx.cursor_off
            )
            # 行内选区：先删除选区内容（与换行合并为一个撤销操作）
            if (
                ctx.cursor_ref.current
                and ctx.cursor_ref.current.base != ctx.cursor_ref.current.extent
            ):
                cs = ctx.cursor_ref.current
                sel_start = min(cs.base, cs.extent)
                sel_end = max(cs.base, cs.extent)
                sel_start = max(0, min(sel_start, len(raw)))
                sel_end = max(0, min(sel_end, len(raw)))
                if sel_start < sel_end:
                    new_raw = raw[:sel_start] + raw[sel_end:]
                    _reparse_atomic(line, new_raw, notify=False)
                    ctx.mark_dirty()
                    raw = _line_raw(line)
                    off = sel_start
                    cs.reset(off, len(raw))
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
            ctx.clear_secondary_cursors()
            new_line = _make_code_line(m.group(1), "")
            # 原地替换行 + notify()，避免 O(N) 列表重建
            ctx.document.lines[li] = new_line
            ctx.document.notify()
            ctx.mark_dirty()
            ctx.suppress_blur.current = True
            ctx.set_cursor_line(li)
            ctx.set_cursor_li(None)
            return

        # 标题：before 空 → 清空前缀；否则分割成两行
        if line.block_type == BlockType.HEADING:
            if not before.strip():
                ctx.clear_secondary_cursors()
                _reparse_atomic(line, after.lstrip())
                ctx.mark_dirty()
                _set_cursor(li, 0)
                return
            ctx.clear_secondary_cursors()
            _reparse_atomic(line, before, notify=False)
            new_line = parser.parse_markdown(after).lines[0]
            # 原地插入行 + notify()，避免 O(N) 列表重建
            # reparse 用 notify=False 静默更新旧行，由本次 document.notify() 统一触发
            # 唯一一次重渲染（原 reparse 的 line.notify() + document.notify() = 2 次）
            ctx.document.lines.insert(li + 1, new_line)
            ctx.document.notify()
            ctx.mark_dirty()
            ctx.suppress_blur.current = True
            _set_cursor(li + 1, 0)
            return

        # 列表 / 引用：before 仅前缀（空内容）→ 退出列表/引用
        if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O, BlockType.QUOTE):
            # 引用空内容（before 仅引用标记，光标在前缀内或内容起点）→ Enter 退出引用
            # Typora 式：空引用回车回退为常规段落，保持光标位置（行变空后仍在同一点）。
            # 不能复用通用 before.strip() 判断："> " 的 strip() 得 ">" 非空，
            # 需按引用标记集 strip（">x" 等无空格写法也会被正确剥离）。
            if line.block_type == BlockType.QUOTE and not before.strip("> "):
                ctx.clear_secondary_cursors()
                stripped = after.lstrip("> ")
                _reparse_atomic(line, stripped)
                ctx.mark_dirty()
                ctx.suppress_blur.current = True
                ctx.set_nav_seq(ctx.nav_seq + 1)
                _set_cursor(li, 0)
                return
            if not before.strip():
                ctx.clear_secondary_cursors()
                # 引用已由上方专用分支处理；此处仅剩列表（光标在行首 before=""）
                stripped = after.lstrip()
                _reparse_atomic(line, stripped)
                ctx.mark_dirty()
                # 递增 nav_seq + suppress_blur：block_type 变化使 TextField 重建，
                # cursor_li 不变（同一行）需显式递增 nav_seq 触发 use_effect 聚焦
                # 新控件，否则光标丢失（input_session 不活跃时 _set_cursor 内部
                # 不调用 _end_input_session → nav_seq 不递增）
                ctx.suppress_blur.current = True
                ctx.set_nav_seq(ctx.nav_seq + 1)
                _set_cursor(li, 0)
                return
            # 任务项空内容（before 仅前缀，无内容）→ Enter 退出任务列表转为普通段落
            # 必须检查 before 仅含前缀（- [ ] / - [x]），不能仅用 _RE_TASK_MARKER.match
            # 否则 `- [ ] task` 光标在末尾时 before="- [ ] task" 也会匹配，误清空内容
            if line.task and before.strip() in ("- [ ]", "- [x]", "- [X]"):
                ctx.clear_secondary_cursors()
                _reparse_atomic(line, after.lstrip())
                ctx.mark_dirty()
                ctx.suppress_blur.current = True
                ctx.set_nav_seq(ctx.nav_seq + 1)
                _set_cursor(li, 0)
                return
            if line.block_type == BlockType.LIST_UO and before.rstrip() in (
                "-",
                "*",
                "+",
            ):
                ctx.clear_secondary_cursors()
                _reparse_atomic(line, after.lstrip())
                ctx.mark_dirty()
                ctx.suppress_blur.current = True
                ctx.set_nav_seq(ctx.nav_seq + 1)
                _set_cursor(li, 0)
                return
            if line.block_type == BlockType.LIST_O and _RE_O_PREFIX.match(
                before.rstrip()
            ):
                ctx.clear_secondary_cursors()
                _reparse_atomic(line, after.lstrip())
                ctx.mark_dirty()
                ctx.suppress_blur.current = True
                ctx.set_nav_seq(ctx.nav_seq + 1)
                _set_cursor(li, 0)
                return

        # 默认：分割当前行，续行加列表/引用前缀
        cont_prefix = _next_line_raw(line)
        # reparse 用 notify=False 静默更新旧行，由紧接的 lines.insert() +
        # document.notify() 统一触发唯一一次重渲染（原 reparse 的 line.notify()
        # + ObservableList 通知 + document.notify() = 3 次冗余通知）
        _reparse_atomic(line, before, notify=False)
        new_line = parser.parse_markdown(cont_prefix + after).lines[0]
        # 原地插入行 + notify()，避免 O(N) 列表重建
        ctx.document.lines.insert(li + 1, new_line)
        ctx.document.notify()
        ctx.mark_dirty()
        ctx.suppress_blur.current = True
        _set_cursor(li + 1, len(cont_prefix))
        # 多光标：同步 Enter 分行到所有副光标（从下往上处理避免行号偏移）
        # override 调用（outward 选区换行）时跳过广播
        if override_li is None:
            ctx.broadcast_submit(value)

    return {
        "cursor_base": _cursor_base,
        "end_input_session": _end_input_session,
        "set_cursor": _set_cursor,
        "on_tap_line": _on_tap_line,
        "handle_char_input": handle_char_input,
        "handle_paste": handle_paste,
        "handle_paste_plain": handle_paste_plain,
        "backspace_core": backspace_core,
        "delete_core": delete_core,
        "on_submit": on_submit,
    }
