"""行内格式工厂（从 views/editor.py 闭包抽取）。

闭包组：apply_inline_format / apply_outward_wrap / handle_outward_type_char

跨组依赖（通过 ctx 装配槽，调用时读取）：
- push_history（history 组）
- cursor_base / set_cursor（cursor 组）
- set_outward_sel（共享）
- mark_dirty（共享）
- set_nav_seq（setter，仅 _apply_outward_wrap 递增以重建 TextField）

组内依赖（直接调用，不经 ctx）：
- apply_inline_format → _apply_outward_wrap（outward 选区优先）

依赖项：
- parser（reparse_line_atomic）
- models（Line / SegType）
- utils.segment_helpers（WRAP_SYNTAX / is_fence / line_raw）
"""

import parser
from models import Line, SegType
from utils.segment_helpers import WRAP_SYNTAX
from utils.segment_helpers import is_fence as _is_fence
from utils.segment_helpers import line_raw as _line_raw

# 高频编辑路径用原子化重解析（仅触发 1 次 observable 通知）
_reparse_atomic = parser.reparse_line_atomic


def _find_seg_for_range(
    line: Line, a_off: int, b_off: int
) -> tuple[int, int, int] | tuple[None, None, None]:
    """找到完全包含 [a_off, b_off] 的段索引（选区落在单段内）。

    返回 (seg_idx, seg_start, seg_end)；选区跨段或越界返回 (None, None, None)。
    用于 _apply_outward_wrap 基于 Segment.marks 判断 toggle：选区在单段内时
    段 marks 可信，跨段时回退到 wrap（添加标记）。
    """
    acc = 0
    for i, s in enumerate(line.segments):
        seg_end = acc + len(s.raw)
        if acc <= a_off and b_off <= seg_end:
            return i, acc, seg_end
        acc = seg_end
    return None, None, None


def _compute_wrap_toggle(
    line: Line, a_off: int, b_off: int, seg_type: SegType,
    wrap_open: str, wrap_close: str,
) -> tuple[str, tuple[int, int, int, int]]:
    """计算 toggle 包裹/取消后的新 raw 与选区（纯函数，无副作用）。

    基于 Segment.marks 判断（避免 `*` 与 `**`、`~` 与 `~~` 子串误匹配）：
    - 选区所在段 marks 含目标 seg_type → unwrap（移除该层标记）
    - 否则 → wrap（添加标记）
    - 选区跨段或段 raw 结构与 marks 不匹配 → 回退 wrap

    返回 (new_raw, (a_li, a_off, b_li, b_off))，调用方负责 reparse 与 set_outward_sel。
    """
    raw = _line_raw(line)
    selected = raw[a_off:b_off]
    ol, cl = len(wrap_open), len(wrap_close)
    a_li = 0  # 调用方在闭包内重写为真实 a_li；此处仅占位保持元组形状

    seg_idx, seg_start, seg_end = _find_seg_for_range(line, a_off, b_off)
    cur_seg = line.segments[seg_idx] if seg_idx is not None else None
    cur_marks = (cur_seg.marks if cur_seg else None) or ()

    if cur_seg is not None and seg_type in cur_marks:
        # Unwrap：段 raw 结构 = prefix(所有 marks open 拼接) + content +
        # suffix(所有 marks close 拼接, reversed 顺序)。移除第 mark_idx 层。
        marks_list = list(cur_marks)
        mark_idx = marks_list.index(seg_type)
        prefix_str = "".join(WRAP_SYNTAX[m][0] for m in marks_list)
        suffix_str = "".join(WRAP_SYNTAX[m][1] for m in reversed(marks_list))
        seg_raw = cur_seg.raw
        # 安全检查：段 raw 与 marks 一致才 unwrap，否则回退 wrap
        if (seg_raw.startswith(prefix_str) and seg_raw.endswith(suffix_str)
                and len(seg_raw) >= len(prefix_str) + len(suffix_str)):
            prefix_lens = [len(WRAP_SYNTAX[m][0]) for m in marks_list]
            prefix_total = sum(prefix_lens)
            prefix_before = sum(prefix_lens[:mark_idx])
            prefix_mark_len = prefix_lens[mark_idx]
            rev_idx = len(marks_list) - 1 - mark_idx
            suffix_lens = [len(WRAP_SYNTAX[m][1]) for m in reversed(marks_list)]
            suffix_total = sum(suffix_lens)
            suffix_before = sum(suffix_lens[:rev_idx])
            suffix_mark_len = suffix_lens[rev_idx]

            content = seg_raw[prefix_total:len(seg_raw) - suffix_total]
            # 新 prefix：跳过第 mark_idx 层
            new_prefix = (seg_raw[:prefix_before]
                          + seg_raw[prefix_before + prefix_mark_len:prefix_total])
            # 新 suffix：跳过第 rev_idx 层（reversed 序列中位置）
            suffix_start = len(seg_raw) - suffix_total
            new_suffix = (seg_raw[suffix_start:suffix_start + suffix_before]
                          + seg_raw[suffix_start + suffix_before + suffix_mark_len:])
            new_seg_raw = new_prefix + content + new_suffix
            new_raw = raw[:seg_start] + new_seg_raw + raw[seg_end:]
            new_content_start = seg_start + len(new_prefix)
            return new_raw, (a_li, new_content_start, a_li, new_content_start + len(content))

    # Wrap（含回退）：在选区外侧添加标记，选区保持在内容上
    new_raw = raw[:a_off] + wrap_open + selected + wrap_close + raw[b_off:]
    return new_raw, (a_li, a_off + ol, a_li, a_off + ol + len(selected))


def build_inline_format(ctx):
    """构造行内格式闭包组。

    返回 dict[str, Callable]：
    apply_inline_format / apply_outward_wrap / handle_outward_type_char
    """

    # ============ 行内格式（光标级包裹）============
    def insert_inline_at(fmt: str, li: int, off: int):
        """在指定 (li, off) 插入空语法骨架并进入编辑态。

        apply_inline_format（编辑态，光标处插入）与 apply_inline_format_to_selection
        （浏览态无选区，当前行激活插入）共用。link 插入 []() 光标落 text 位置；
        其余格式插入成对标记，光标落两标记之间。
        """
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if _is_fence(line):
            return
        raw = _line_raw(line)
        off = max(0, min(off, len(raw)))
        ctx.push_history()
        ctx.undo_push_pending.current = True
        if fmt == "link":
            # 插入空链接骨架 []()，光标在 [] 内（text 位置）。
            # Tab 在 text/url/段尾间字段跳转（见 navigation.link_tab_jump），
            # 无需 URL 占位符——空 URL 利于 Tab 跳转后直接输入。
            new_raw = raw[:off] + "[]()" + raw[off:]
            _reparse_atomic(line, new_raw)
            ctx.mark_dirty()
            ctx.set_cursor(li, off + 1)  # 光标落在 [ 后（text 位置）
            return
        seg_type = {
            "bold": SegType.STRONG,
            "italic": SegType.EMPHASIS,
            "highlight": SegType.HIGHLIGHT,
            "strike": SegType.STRIKE,
            "code": SegType.CODESPAN,
            "inline_math": SegType.INLINE_MATH,
        }.get(fmt)
        if seg_type is None:
            return
        wrap = WRAP_SYNTAX.get(seg_type, ("", ""))[0]
        new_raw = raw[:off] + wrap + wrap + raw[off:]
        _reparse_atomic(line, new_raw)
        ctx.mark_dirty()
        ctx.set_cursor(li, off + len(wrap))  # 光标落在两标记之间

    def apply_inline_format(fmt: str):
        """行内格式快捷键：有 outward 选区包裹/取消同段选区；否则在光标处插入空语法。

        fmt: bold/italic/highlight/strike/code/link/inline_math
        """
        # 优先处理 outward 选区（无论浏览态还是编辑态）：toggle 包裹/取消
        if ctx.outward_sel_ref.current is not None:
            _apply_outward_wrap(fmt)
            return
        if ctx.cursor_li is None:
            return
        li = ctx.cursor_li
        if not (0 <= li < len(ctx.document.lines)):
            return
        if _is_fence(ctx.document.lines[li]):
            return
        raw = _line_raw(ctx.document.lines[li])
        off = ctx.cursor_base(len(raw))  # IME 实时光标，避免输入后立即 Ctrl+B 位置错位
        insert_inline_at(fmt, li, off)

    def _apply_outward_wrap(fmt: str):
        """渲染态 outward 选区 toggle 行内格式（仅同段选区）。

        Typora 式 toggle：
        - 选区已包裹同类型标记 → 取消标记（unwrap），保持选区
        - 选区未包裹 → 添加标记（wrap），保持选区（选内容，不含标记）
        - 再次按下快捷键即可 toggle 回来
        """
        sel = ctx.outward_sel_ref.current
        if sel is None:
            return
        a_li, a_off, b_li, b_off = sel
        if (a_li, a_off) > (b_li, b_off):
            a_li, a_off, b_li, b_off = b_li, b_off, a_li, a_off
        if a_li != b_li:
            return
        if not (0 <= a_li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[a_li]
        if _is_fence(line):
            return
        raw = _line_raw(line)
        a_off = max(0, min(a_off, len(raw)))
        b_off = max(a_off, min(b_off, len(raw)))
        selected = raw[a_off:b_off]
        if not selected:
            return  # 空选区不操作

        ctx.push_history()
        ctx.undo_push_pending.current = True

        if fmt == "link":
            # 包裹为 [selected]()，光标定位到 URL 位置（]( 与 ) 之间）。
            # 链接编辑视为常规文本编辑：光标在链接段内时渲染层（raw_to_visible_spans /
            # split_seg_for_display）显示完整语法含 URL，光标离开段后自动折叠为
            # display_text。无 URL 占位符等特殊状态机，亦无 set_nav_seq 重建，避免异步
            # 重新聚焦间隙丢失快速输入。Tab 字段跳转（text↔url↔段尾）由 link_tab_jump
            # 处理，仅移动光标无状态机。
            new_raw = raw[:a_off] + f"[{selected}]()" + raw[b_off:]
            _reparse_atomic(line, new_raw)
            new_lines = list(ctx.document.lines)
            new_lines[a_li] = line
            ctx.document.lines = new_lines
            ctx.mark_dirty()
            ctx.set_outward_sel(None)
            # URL 起点 = a_off + 1([) + len(selected)(text) + 2(])
            ctx.set_cursor(a_li, a_off + 3 + len(selected))
            return

        seg_type = {
            "bold": SegType.STRONG,
            "italic": SegType.EMPHASIS,
            "highlight": SegType.HIGHLIGHT,
            "strike": SegType.STRIKE,
            "code": SegType.CODESPAN,
            "inline_math": SegType.INLINE_MATH,
        }.get(fmt)
        if seg_type is None:
            return
        wrap_open, wrap_close = WRAP_SYNTAX.get(seg_type, ("", ""))

        # toggle 检测基于段 marks 判断（避免 * 与 **、~ 与 ~~ 子串误匹配），
        # 详见 _compute_wrap_toggle 注释。
        new_raw, new_sel = _compute_wrap_toggle(
            line, a_off, b_off, seg_type, wrap_open, wrap_close,
        )
        # 纯函数返回占位 a_li=0，替换为真实行索引
        new_sel = (a_li, new_sel[1], a_li, new_sel[3])

        _reparse_atomic(line, new_raw)
        new_lines = list(ctx.document.lines)
        new_lines[a_li] = line
        ctx.document.lines = new_lines
        ctx.mark_dirty()
        ctx.set_outward_sel(new_sel)
        ctx.set_nav_seq(ctx.nav_seq + 1)

    # ============ 链接语法 Typora 式交互 ============
    def handle_outward_type_char(char: str):
        """打字替换 outward 选区（浏览态选中→输入即替换，通用基础编辑行为）。

        一次 reparse 完成删除+插入，避免 delete+insert 两次重绘闪烁。
        替换后清除 outward_sel 高亮并切换到编辑态（cursor_li=li），
        现有 use_effect(_focus_cursor_field, [cursor_li]) 自动聚焦 cursor_text_field，
        下一字符走正常 IME 输入流。
        """
        sel = ctx.outward_sel_ref.current
        if sel is None:
            return
        a_li, a_off, b_li, b_off = sel
        if (a_li, a_off) > (b_li, b_off):
            a_li, a_off, b_li, b_off = b_li, b_off, a_li, a_off
        if a_li != b_li:
            return  # 跨行 v1 不处理
        if not (0 <= a_li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[a_li]
        if _is_fence(line):
            return
        raw = _line_raw(line)
        a_off = max(0, min(a_off, len(raw)))
        b_off = max(a_off, min(b_off, len(raw)))
        if a_off == b_off:
            return  # 零宽选区不操作

        ctx.push_history()
        ctx.undo_push_pending.current = True
        new_raw = raw[:a_off] + char + raw[b_off:]
        _reparse_atomic(line, new_raw)
        new_lines = list(ctx.document.lines)
        new_lines[a_li] = line
        ctx.document.lines = new_lines
        ctx.mark_dirty()
        # 清选区 + 切换到编辑态（光标在插入字符后）
        ctx.set_outward_sel(None)
        ctx.set_cursor(a_li, a_off + len(char))
        # 不递增 nav_seq：避免 TextField 重建→异步重新聚焦间隙丢失后续快速输入。
        # cursor_li 由 _set_cursor 设置，LineView 据其重渲染刷新 TextField 的 left/on_change；
        # 旧 value 由 _end_input_session 递增 nav_seq 重建 cursor TextField 清空
        # （key 含 nav_seq → 新控件 value="" 天然清空，无需命令式 ref.value=""）。

    return {
        "apply_inline_format": apply_inline_format,
        "insert_inline_at": insert_inline_at,
        "apply_outward_wrap": _apply_outward_wrap,
        "handle_outward_type_char": handle_outward_type_char,
    }
