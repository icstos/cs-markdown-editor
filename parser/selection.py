"""选区操作：选区 → Markdown 源码 / 选区格式化 / 选区删除。

依赖项：
- parser.reparse（reparse_line）
- models（Line / Segment / SegType）
- utils.segment_helpers（WRAP_SYNTAX / display_text / PREFIX_SEGTYPES / line_raw）

对外接口：
- compute_markdown_from_selections(lines, selections)：选区 → Markdown 源码
- match_text_to_selections(lines, plain_text)：纯文本 → 选区字典
- compute_markdown_from_text(lines, plain_text)：纯文本 → Markdown 源码
- apply_inline_format_to_selections(lines, selections, wrap, kind)：选区应用行内格式
- delete_selections(lines, selections)：删除选中内容
- extract_outward_text(lines, a_li, a_off, b_li, b_off)：向外选区 → Markdown 源码

设计要点：
- 偏移约定：selections 的 offset 相对于该行 ft.Text 显示文本
  （所有段 display_text 拼接，前缀段透明）。
- 全段选中 → 用 seg.raw（含完整语法）；部分选中 → 用 _wrap_partial 包裹。
- extract_outward_text 的偏移相对于行级 raw（与 editor 的光标/选区偏移一致）。
"""

from models.document import Line, Segment, SegType
from parser.reparse import reparse_line
from utils.segment_helpers import PREFIX_SEGTYPES, WRAP_SYNTAX, display_text, line_raw


def _wrap_partial(seg: Segment, selected_text: str) -> str:
    """对部分选中的段应用语法包裹，返回 Markdown 源码。

    组合格式（如 ***加粗斜体***）按 marks 外→内顺序嵌套包裹，保证语法完整。
    """
    if not selected_text:
        return ""
    st = seg.seg_type
    # 组合格式：marks 多于一项时按外→内嵌套
    if seg.marks and len(seg.marks) > 1:
        pre = "".join(WRAP_SYNTAX[m][0] for m in seg.marks if m in WRAP_SYNTAX)
        post = "".join(
            WRAP_SYNTAX[m][1] for m in reversed(seg.marks) if m in WRAP_SYNTAX
        )
        return f"{pre}{selected_text}{post}"
    # 单一包裹器行内格式
    if st in WRAP_SYNTAX:
        pre, post = WRAP_SYNTAX[st]
        return f"{pre}{selected_text}{post}"
    # 链接 / 图片 / 行内公式
    if st == SegType.LINK:
        return f"[{selected_text}]({seg.url})"
    if st == SegType.IMAGE:
        return f"![{selected_text}]({seg.url})"
    if st == SegType.INLINE_MATH:
        return f"${selected_text}$"
    # 纯文本 / 前缀段：直接返回选中文本
    return selected_text


def _iter_seg_offsets(line: Line):
    """逐段 yield (seg, seg_start, seg_end, text)，seg_end = seg_start + len(display_text)。

    compute_markdown_from_selections 与 delete_selections 共享此迭代逻辑，
    统一"按展示偏移定位段"的边界处理。
    """
    offset = 0
    for seg in line.segments:
        text = display_text(seg)
        yield seg, offset, offset + len(text), text
        offset += len(text)


def _line_display_len(line: Line) -> int:
    """整行展示文本长度（所有段 display_text 拼接）。"""
    return sum(len(display_text(s)) for s in line.segments)


def compute_markdown_from_selections(
    lines: list[Line], selections: dict[int, tuple[int, int]]
) -> str:
    """根据选区计算对应的 Markdown 源码。

    selections: {line_idx: (base_offset, extent_offset)}
    偏移相对于该行 ft.Text 的显示文本（所有段 display_text 拼接）。
    全段选中 → 用 seg.raw（含完整语法）；
    部分选中 → 用 _wrap_partial 包裹选中文本。
    遍历首尾行之间的所有行（含空行），确保换行符数量正确。
    """
    if not selections:
        return ""

    sorted_lines = sorted(selections.keys())
    first_li, last_li = sorted_lines[0], sorted_lines[-1]
    parts: list[str] = []

    for li in range(first_li, last_li + 1):
        if li < len(lines) and li in selections:
            line = lines[li]
            base, extent = selections[li]
            start, end = min(base, extent), max(base, extent)
            if start != end:
                # 构建段偏移表，逐段计算选区
                for seg, seg_start, seg_end, text in _iter_seg_offsets(line):
                    if seg_end <= start or seg_start >= end:
                        continue  # 不重叠

                    sel_start = max(0, start - seg_start)
                    sel_end = min(len(text), end - seg_start)
                    selected = text[sel_start:sel_end]

                    # 全段选中 → 用 raw（含完整语法）；部分选中 → 包裹语法
                    if sel_start == 0 and sel_end == len(text):
                        parts.append(seg.raw)
                    else:
                        parts.append(_wrap_partial(seg, selected))

        if li < last_li:
            parts.append("\n")

    return "".join(parts)


def _match_on_projection(
    line_texts: list[str], text: str
) -> dict[int, tuple[int, int]]:
    """把纯文本匹配到"去掉换行后的投影"上，再映射回 (行号, 展示偏移) 选区。

    为什么需要这一层（代码块跨行复制）：Flutter 的 `SelectionArea` 跨行拼接时
    **不插入换行符**，而代码块在文档里是**一个 Line 承载多行文本**（该 Line 的展示
    文本自身含 `\\n`）。于是两侧的换行分布不一致：
    - 选中的纯文本 = 代码各行直接拼接（无换行）；
    - 文档侧 `"".join(line_texts)` 含换行 → `find()` 必然失败。
    结果是"跨行选中代码块再复制"什么都还原不出来，剪贴板只留下无换行的拼接串。
    把文档文本投影成"忽略换行的字符序列"再匹配即可精确还原选区位置：投影只删除了
    换行位，匹配到的那一段仍是**同一段连续文本**（换行还原自原始偏移），因此得到的
    Markdown 片段天然带换行。

    歧义保护：同一段文本在投影里出现多次时不敢猜，直接返回 {}（维持原行为——
    剪贴板保留 SelectionArea 的原始结果）。能走到本函数的前提是"选区跨了换行"
    （无换行的选区在前两个策略里就命中了），这种选区足够长，歧义概率极低；
    加这道闸是为了"最坏情况不比现状更差"。

    段落之间本来就没有换行可删，投影与直接拼接等价，故本函数不改变既有段落选区行为。
    浏览态的删除路径不走这里（`views/editor/_clipboard.handle_delete_selection`
    对无偏移选区只清空选区文本），所以删除语义不变。
    """
    proj_chars: list[tuple[int, int]] = []  # 投影下标 → (行号, 该行展示文本内偏移)
    for li, lt in enumerate(line_texts):
        for off, ch in enumerate(lt):
            if ch != "\n":
                proj_chars.append((li, off))
    proj = "".join(line_texts[li][off] for li, off in proj_chars)
    pos = proj.find(text)
    if pos == -1 or proj.find(text, pos + 1) != -1:
        return {}

    per_line: dict[int, list[int]] = {}
    for idx in range(pos, pos + len(text)):
        li, off = proj_chars[idx]
        per_line.setdefault(li, []).append(off)
    # 每行取 [min, max+1)：与 compute_markdown_from_selections 的半开区间一致
    return {li: (min(offs), max(offs) + 1) for li, offs in per_line.items()}


def match_text_to_selections(
    lines: list[Line], plain_text: str
) -> dict[int, tuple[int, int]]:
    """将 SelectionArea 复制的纯文本匹配回文档行，返回选区字典。

    SelectionArea 跨行复制时不插入换行符，多行文本被直接拼接。
    因此分三种策略：
    1. 若剪贴板含 \\n → 按行逐段匹配
    2. 若无 \\n → 在全部行显示文本的拼接中查找，再映射回各行偏移
    3. 仍匹配不到 → 回退单行匹配；单行也不中且文档侧含换行（代码块）时，
       改走"去换行投影"匹配（见 `_match_on_projection`）
    """
    if not plain_text:
        return {}

    text = plain_text.replace("\r\n", "\n").replace("\r", "\n")

    line_texts = [
        "".join(display_text(seg) for seg in line.segments) for line in lines
    ]

    # 情况1：剪贴板含换行符 → 按行匹配
    if "\n" in text:
        selections: dict[int, tuple[int, int]] = {}
        search_from = 0
        for clip in text.split("\n"):
            if not clip:
                search_from += 1
                continue
            for li in range(search_from, len(line_texts)):
                pos = line_texts[li].find(clip)
                if pos != -1:
                    selections[li] = (pos, pos + len(clip))
                    search_from = li + 1
                    break
        return selections

    # 情况2：无换行符 → 在全部行文本拼接中查找，再映射回各行
    full_concat = "".join(line_texts)
    pos = full_concat.find(text)
    if pos == -1:
        # 回退：单行匹配
        selections = {}
        for li, lt in enumerate(line_texts):
            p = lt.find(text)
            if p != -1:
                selections[li] = (p, p + len(text))
                break
        if selections:
            return selections
        # 回退 2：跨行选中代码块（文档侧含换行、选择侧不含）→ 投影匹配
        return _match_on_projection(line_texts, text)

    # 将拼接位置映射回各行偏移
    end_pos = pos + len(text)
    selections = {}
    offset = 0
    for li, lt in enumerate(line_texts):
        lt_start = offset
        lt_end = offset + len(lt)
        offset = lt_end
        sel_start = max(0, pos - lt_start)
        sel_end = min(len(lt), end_pos - lt_start)
        if sel_start < sel_end:
            selections[li] = (sel_start, sel_end)

    return selections


def compute_markdown_from_text(lines: list[Line], plain_text: str) -> str:
    """从 SelectionArea 复制的纯文本计算 Markdown 源码。"""
    selections = match_text_to_selections(lines, plain_text)
    return compute_markdown_from_selections(lines, selections)


def apply_inline_format_to_selections(
    lines: list[Line],
    selections: dict[int, tuple[int, int]],
    wrap: str,
    kind: str,
) -> tuple[list[Line], int, int, int]:
    """对选区应用行内格式，返回 (新行列表, 光标行索引, 光标段索引, 段内偏移)。

    kind:
    - wrap: 普通包裹格式（bold/italic/highlight/strike/code）
    - link: 链接格式，选区变为 [text](url)
    """
    if not selections:
        return lines, 0, 0, 0

    sorted_lines = sorted(selections.keys())
    first_li, last_li = sorted_lines[0], sorted_lines[-1]
    new_lines = list(lines)

    def _wrap_selected(text: str) -> str:
        if kind == "link":
            return f"[{text}](url)"
        return f"{wrap}{text}{wrap}"

    if first_li == last_li:
        li = first_li
        line = new_lines[li]
        base, extent = selections[li]
        start, end = min(base, extent), max(base, extent)
        rebuilt = []
        cursor_raw = 0
        for seg, seg_start, seg_end, text in _iter_seg_offsets(line):
            if seg_end <= start or seg_start >= end:
                rebuilt.append(seg.raw)
                continue
            sel_start = max(0, start - seg_start)
            sel_end = min(len(text), end - seg_start)
            if sel_start > 0:
                rebuilt.append(seg.raw[:sel_start])
            selected = text[sel_start:sel_end]
            wrapped = _wrap_selected(selected)
            rebuilt.append(wrapped)
            cursor_raw += len(seg.raw[:sel_start]) + (1 if kind == "link" else len(wrap))
            if sel_end < len(text):
                rebuilt.append(seg.raw[sel_end:])
        full_raw = "".join(rebuilt)
        reparse_line(line, full_raw)
        return new_lines, li, 0, cursor_raw

    # 多行：仅简单按行分别格式化各自选区，保留原行结构
    for li in sorted_lines:
        line = new_lines[li]
        base, extent = selections[li]
        start, end = min(base, extent), max(base, extent)
        rebuilt = []
        for seg, seg_start, seg_end, text in _iter_seg_offsets(line):
            if seg_end <= start or seg_start >= end:
                rebuilt.append(seg.raw)
                continue
            sel_start = max(0, start - seg_start)
            sel_end = min(len(text), end - seg_start)
            if sel_start > 0:
                rebuilt.append(seg.raw[:sel_start])
            selected = text[sel_start:sel_end]
            rebuilt.append(_wrap_selected(selected))
            if sel_end < len(text):
                rebuilt.append(seg.raw[sel_end:])
        reparse_line(line, "".join(rebuilt))
    return new_lines, first_li, 0, 0


def delete_selections(
    lines: list[Line], selections: dict[int, tuple[int, int]]
) -> tuple[list[Line], int, int, int]:
    """删除选中内容，返回 (新行列表, 光标行索引, 光标段索引, 段内偏移)。

    selections: {line_idx: (base_offset, extent_offset)}
    偏移相对于该行 ft.Text 的显示文本（所有段 display_text 拼接）。
    光标定位到剪切位置（即选中内容的起点）。

    删除策略：
    - 单行部分选中 → 重构该行段结构，删除选中部分
    - 多行完整选中（首尾行均整行选中）→ 删除所有选中行
    - 多行部分选中 → 首行选中尾部+中间完整行+尾行选中头部合并为一行
    """
    if not selections:
        return lines, 0, 0, 0

    sorted_lines = sorted(selections.keys())
    first_li, last_li = sorted_lines[0], sorted_lines[-1]

    # 检查是否为完整行选中（所有选中行都选中了整行内容）
    is_full_line = True
    for li in sorted_lines:
        line = lines[li]
        total_len = _line_display_len(line)
        base, extent = selections[li]
        if min(base, extent) != 0 or max(base, extent) != total_len:
            is_full_line = False
            break

    if is_full_line:
        # 删除完整行，光标定位到删除位置后的行（如有）的段首
        new_lines = lines[:first_li] + lines[last_li + 1:]
        cursor_li = max(0, min(first_li, len(new_lines) - 1)) if new_lines else 0
        if new_lines:
            # 找到第一个非前缀段作为光标段，偏移为段首（剪切位置）
            cursor_si = 0
            for i, seg in enumerate(new_lines[cursor_li].segments):
                if seg.seg_type not in PREFIX_SEGTYPES:
                    cursor_si = i
                    break
            cursor_offset = 0
        else:
            cursor_si, cursor_offset = 0, 0
        return new_lines, cursor_li, cursor_si, cursor_offset

    # 单行部分选中：重构该行段结构
    if first_li == last_li:
        li = first_li
        line = lines[li]
        base, extent = selections[li]
        start, end = min(base, extent), max(base, extent)

        new_segments: list[Segment] = []
        cursor_si = 0
        cursor_offset = 0
        for seg, seg_start, seg_end, text in _iter_seg_offsets(line):
            if seg_end <= start or seg_start >= end:
                new_segments.append(seg)
                continue
            sel_start = max(0, start - seg_start)
            sel_end = min(len(text), end - seg_start)

            if sel_start > 0:
                prefix_raw = _wrap_partial(seg, text[:sel_start])
                new_segments.append(Segment(SegType.TEXT, prefix_raw, text[:sel_start]))
                # 光标定位到剪切位置：前缀段的末尾
                cursor_si = len(new_segments) - 1
                cursor_offset = len(prefix_raw)

            if sel_end < len(text):
                suffix_raw = _wrap_partial(seg, text[sel_end:])
                new_segments.append(Segment(SegType.TEXT, suffix_raw, text[sel_end:]))
                # 若无前缀段，光标定位到后缀段首
                if sel_start == 0:
                    cursor_si = len(new_segments) - 1
                    cursor_offset = 0

        line.segments = new_segments
        if not line.segments:
            line.segments = [Segment(SegType.TEXT, "", "")]
            cursor_si, cursor_offset = 0, 0
        # 返回新列表触发 @ft.observable 通知：单行部分选中只改了
        # line.segments（Line 非 observable），若返回同一 lines 引用，
        # document.lines = lines 不触发重渲染，剪切/删除视觉无变化。
        return list(lines), li, cursor_si, cursor_offset

    # 跨行部分选中：首行选中尾部 + 尾行选中头部合并为一行，中间行删除
    first_line = lines[first_li]
    last_line = lines[last_li]
    first_base, first_extent = selections[first_li]
    last_base, last_extent = selections[last_li]
    first_start = min(first_base, first_extent)
    last_end = max(last_base, last_extent)

    # 收集首行选中之前的剩余段
    head_segments: list[Segment] = []
    for seg, seg_start, seg_end, text in _iter_seg_offsets(first_line):
        if seg_end <= first_start:
            head_segments.append(seg)
        elif seg_start < first_start:
            sel_start = max(0, first_start - seg_start)
            if sel_start > 0:
                prefix_raw = _wrap_partial(seg, text[:sel_start])
                head_segments.append(Segment(SegType.TEXT, prefix_raw, text[:sel_start]))

    # 收集尾行选中之后的剩余段
    tail_segments: list[Segment] = []
    for seg, seg_start, seg_end, text in _iter_seg_offsets(last_line):
        if seg_start >= last_end:
            tail_segments.append(seg)
        elif seg_end > last_end:
            sel_end = min(len(text), last_end - seg_start)
            if sel_end < len(text):
                suffix_raw = _wrap_partial(seg, text[sel_end:])
                tail_segments.append(Segment(SegType.TEXT, suffix_raw, text[sel_end:]))

    # 光标定位到剪切位置：head_segments 的末尾（即原选中起点）
    # 若 head 为空，光标定位到 tail 首段开头
    if head_segments:
        cursor_si = len(head_segments) - 1
        cursor_offset = len(head_segments[cursor_si].raw)
    elif tail_segments:
        cursor_si = 0
        cursor_offset = 0
    else:
        cursor_si, cursor_offset = 0, 0

    # 合并首行头部 + 尾行尾部，保留首行的块级前缀
    merged_segments = head_segments + tail_segments
    if not merged_segments:
        merged_segments = [Segment(SegType.TEXT, "", "")]

    # 保留首行的块级前缀段（HEADING_PREFIX / LIST_PREFIX / QUOTE_PREFIX）
    prefix_seg = None
    for seg in first_line.segments:
        if seg.seg_type in PREFIX_SEGTYPES:
            prefix_seg = seg
            break
    if prefix_seg is not None and not any(s.seg_type in PREFIX_SEGTYPES for s in merged_segments):
        merged_segments = [prefix_seg] + merged_segments
        # 前缀段插入到头部，光标段索引+1
        cursor_si += 1

    # 用首行作为合并行（保留 block_type/level/task/checked/lang 等属性）
    first_line.segments = merged_segments
    # 重新解析首行，确保段结构一致
    full_raw = "".join(s.raw for s in merged_segments)
    reparse_line(first_line, full_raw)

    # 重新解析后段结构可能变化，通过累积 raw 长度找到原光标位置对应的新段
    # 目标偏移 = 原 head_segments 各段 raw 长度之和（剪切起点）
    target_raw_offset = sum(len(s.raw) for s in head_segments)
    cursor_si = 0
    cursor_offset = 0
    acc = 0
    for i, seg in enumerate(first_line.segments):
        seg_raw_len = len(seg.raw)
        if acc + seg_raw_len >= target_raw_offset:
            cursor_si = i
            cursor_offset = max(0, target_raw_offset - acc)
            break
        acc += seg_raw_len
    else:
        # 未找到，定位到末段尾
        cursor_si = max(0, len(first_line.segments) - 1)
        cursor_offset = len(first_line.segments[cursor_si].raw)

    # 删除中间行和尾行
    new_lines = lines[:first_li + 1] + lines[last_li + 1:]
    return new_lines, first_li, cursor_si, cursor_offset


def extract_outward_text(
    lines: list[Line], a_li: int, a_off: int, b_li: int, b_off: int,
) -> str:
    """提取已排序选区 [a_li,a_off]..[b_li,b_off] 的文本（行级 raw 拼接）。

    cut/copy 共用：统一边界检查，避免重复逻辑。返回行级 Markdown 源码
    拼接（含语法标记），直接写入剪贴板，粘贴时恢复格式。

    偏移约定：a_off / b_off 为行级 raw 偏移（与 editor 光标/选区偏移一致）。
    边界无效时返回空串（与原 editor 闭包行为一致，吞异常）。
    """
    try:
        if not (0 <= a_li < len(lines) and 0 <= b_li < len(lines)):
            return ""
        if a_li == b_li:
            return line_raw(lines[a_li])[a_off:b_off]
        parts = [line_raw(lines[a_li])[a_off:]]
        for i in range(a_li + 1, b_li):
            parts.append(line_raw(lines[i]))
        parts.append(line_raw(lines[b_li])[:b_off])
        return "\n".join(parts)
    except Exception:
        return ""
