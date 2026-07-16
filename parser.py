"""Markdown 与文档状态之间的双向转换。

设计要点：
- 行级（Line）：按行扫描，识别块类型（标题 / 列表 / 引用 / 代码块 / 分隔线 / 段落）。
- 段级（Segment）：块级前缀与行内内容统一抽象为 Segment 列表，从而让
  "点击即编辑"对前缀与行内 span 行为一致（参考 Typora）。
- 行内解析复用 mistune 的 AST：把行内内容包成段落解析，再取其 children，
  兼顾正确性与可维护性。
- reparse_line：编辑某段后，由各段 raw 拼接出整行源码，再重新解析行内结构，
  保证模型始终一致（UI = f(state)）。
"""

import re

import mistune

from models import BlockType, Document, Line, SegType, Segment

# 行内解析器：启用删除线/高亮/上下标插件，支持组合语法 ***加粗斜体*** 等
_INLINE_PLUGINS = ["strikethrough", "mark", "superscript", "subscript"]
_md = mistune.create_markdown(renderer="ast", plugins=_INLINE_PLUGINS)
_html_md = mistune.create_markdown(
    renderer="html",
    plugins=_INLINE_PLUGINS + ["table", "footnotes", "task_lists"],
)

# ---- 正则：块级前缀识别 ----
_RE_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_RE_UO_LIST = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_RE_O_LIST = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")
_RE_QUOTE = re.compile(r"^>\s?(.*)$")
_RE_HR = re.compile(r"^(\s*)([-*_])\2\2+\s*$")  # --- ** ___ 等
_RE_CODE_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([\w+-]*)\s*$")
_RE_TASK = re.compile(r"^(\s*)([-*+])\s+\[( |x|X)\]\s+(.*)$")
_RE_MATH_BLOCK = re.compile(r"^\$\$(.+?)\$\$\s*$", re.DOTALL)
_RE_MATH_FENCE = re.compile(r"^\$\$\s*$")  # 块级公式围栏：$$ 独占一行开/闭
_RE_INLINE_MATH = re.compile(r"\$([^$\n]+?)\$")
_RE_TOC = re.compile(r"^\[toc\]\s*$", re.IGNORECASE)

# ---- 块级前缀段类型 ----
_PREFIX_SEGTYPES = (SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX)


# ---------------------------------------------------------------------------
# 行内解析
# ---------------------------------------------------------------------------
def _split_inline_math(text: str) -> list[Segment]:
    """把文本中的 $...$ 提取为行内公式段，其余保留为纯文本段。"""
    parts: list[Segment] = []
    last = 0
    for m in _RE_INLINE_MATH.finditer(text):
        start, end = m.span()
        if start > last:
            parts.append(Segment(SegType.TEXT, text[last:start], text[last:start]))
        formula = m.group(1)
        parts.append(Segment(SegType.INLINE_MATH, f"${formula}$", formula))
        last = end
    if last < len(text):
        parts.append(Segment(SegType.TEXT, text[last:], text[last:]))
    return parts or [Segment(SegType.TEXT, text, text)]


def _flatten_text(children: list[dict]) -> str:
    """递归压平 children 为纯文本（用于展示）。"""
    out: list[str] = []
    for c in children or []:
        ct = c.get("type")
        if ct in ("text", "softbreak", "linebreak"):
            out.append(c.get("raw", "") if ct == "text" else "\n")
        else:
            out.append(_flatten_text(c.get("children", [])))
    return "".join(out)


# 行内 AST 节点类型 -> (SegType, 包裹器) 映射；codespan/link/image 单独处理
# 这些包裹器可任意嵌套组合（如 ***加粗斜体*** = emphasis→strong→text）
_INLINE_WRAPPERS: dict[str, tuple[SegType, str]] = {
    "strong": (SegType.STRONG, "**"),
    "emphasis": (SegType.EMPHASIS, "*"),
    "strikethrough": (SegType.STRIKE, "~~"),
    "mark": (SegType.HIGHLIGHT, "=="),
    "superscript": (SegType.SUPERSCRIPT, "^"),
    "subscript": (SegType.SUBSCRIPT, "~"),
}


def _node_raw_text(tok: dict) -> tuple[str, str]:
    """任意 AST 节点的 (raw, text)。raw 保留完整 Markdown 语法，text 为纯展示文本。

    递归重建嵌套包裹器语法，保证 "".join(segments raw) 能还原行源码。
    """
    t = tok.get("type")
    if t == "text":
        r = tok.get("raw", "")
        return r, r
    if t in ("softbreak", "linebreak"):
        return "\n", "\n"
    if t in _INLINE_WRAPPERS:
        _, wrap = _INLINE_WRAPPERS[t]
        parts_r: list[str] = []
        parts_t: list[str] = []
        for c in tok.get("children", []):
            r, tx = _node_raw_text(c)
            parts_r.append(r)
            parts_t.append(tx)
        inner_r = "".join(parts_r)
        inner_t = "".join(parts_t)
        return f"{wrap}{inner_r}{wrap}", inner_t
    if t == "codespan":
        r = tok.get("raw", "")
        return f"`{r}`", r
    if t == "link":
        tx = _flatten_text(tok.get("children", []))
        url = tok.get("attrs", {}).get("url", "")
        return f"[{tx}]({url})", tx
    if t == "image":
        alt = _flatten_text(tok.get("children", []))
        url = tok.get("attrs", {}).get("url", "")
        return f"![{alt}]({url})", alt
    if t == "inline_html":
        r = tok.get("raw", "")
        return r, r
    # 未识别节点退化为纯文本
    tx = _flatten_text(tok.get("children", [])) or tok.get("raw", "")
    return tx, tx


def _collect_marks(tok: dict) -> list[SegType]:
    """沿单子节点包裹器链向下收集所有包裹 SegType（外→内顺序）。

    顶层包裹器始终计入 marks（外层格式作用于整段）。仅当包裹器只有一个
    子节点且该子节点也是包裹器时才继续下钻；多子节点（如 *斜==体==* ：
    emphasis 含 text + mark）时停止，内层语法仅作用于部分文本，不加入
    marks（但 _node_raw_text 仍会重建其语法，保证 raw 完整）。
    """
    marks: list[SegType] = []
    cur = tok
    if cur.get("type") not in _INLINE_WRAPPERS:
        return marks
    while True:
        seg_type, _ = _INLINE_WRAPPERS[cur["type"]]
        marks.append(seg_type)
        children = cur.get("children", [])
        if len(children) != 1:
            break
        cur = children[0]
        if cur.get("type") not in _INLINE_WRAPPERS:
            break
    return marks


def _token_to_segments(tok: dict) -> list[Segment]:
    """把一个行内 AST 节点转成 Segment 列表。"""
    t = tok.get("type")

    # 普通文本：提取 $...$ 行内公式
    if t == "text":
        raw = tok.get("raw", "")
        return _split_inline_math(raw) if raw else []

    # 软换行 / 硬换行
    if t in ("softbreak", "linebreak"):
        return [Segment(SegType.TEXT, "\n", "\n")]

    # 包裹型节点（加粗 / 斜体 / 删除线 / 高亮 / 上下标，含任意嵌套组合）
    if t in _INLINE_WRAPPERS:
        raw, text = _node_raw_text(tok)
        marks = _collect_marks(tok)
        seg_type = marks[0] if marks else SegType.TEXT
        return [Segment(seg_type, raw, text, marks=tuple(marks))]

    # 行内代码：raw 不含反引号
    if t == "codespan":
        raw = tok.get("raw", "")
        return [Segment(SegType.CODESPAN, f"`{raw}`", raw)]

    # 链接 / 图片
    if t == "link":
        text = _flatten_text(tok.get("children", []))
        url = tok.get("attrs", {}).get("url", "")
        return [Segment(SegType.LINK, f"[{text}]({url})", text, url=url)]
    if t == "image":
        alt = _flatten_text(tok.get("children", []))
        url = tok.get("attrs", {}).get("url", "")
        return [Segment(SegType.IMAGE, f"![{alt}]({url})", alt, url=url)]

    # 内联 HTML
    if t == "inline_html":
        raw = tok.get("raw", "")
        return [Segment(SegType.TEXT, raw, raw)]

    # 未识别节点退化为纯文本
    text = _flatten_text(tok.get("children", [])) or tok.get("raw", "")
    return [Segment(SegType.TEXT, text, text)] if text else []


def parse_inline(content: str) -> list[Segment]:
    """解析行内 Markdown 为 Segment 列表。

    将内容包成段落交由 mistune 解析，避免重复实现行内语法。
    空内容返回单个空文本段，保证行始终可被点击编辑。
    """
    if not content:
        return [Segment(SegType.TEXT, "", "")]
    ast = _md(content)
    for node in ast:
        if node.get("type") in ("paragraph", "heading"):
            segs: list[Segment] = []
            for tok in node.get("children", []):
                segs.extend(_token_to_segments(tok))
            return segs or [Segment(SegType.TEXT, content, content)]
    return [Segment(SegType.TEXT, content, content)]


# ---------------------------------------------------------------------------
# 行级解析
# ---------------------------------------------------------------------------
def _detect_block(raw: str) -> tuple[BlockType, dict]:
    """识别一行的块类型，返回 (block_type, info)。"""
    if not raw.strip():
        return BlockType.BLANK, {}

    # 顺序敏感：TOC / TASK / HR 需先于普通列表识别
    m = _RE_TOC.match(raw)
    if m:
        return BlockType.TOC, {}

    m = _RE_HEADING.match(raw)
    if m:
        return BlockType.HEADING, {
            "level": len(m.group(1)),
            "content": m.group(2).strip(),
        }

    m = _RE_TASK.match(raw)
    if m:
        return BlockType.LIST_UO, {
            "indent": len(m.group(1).expandtabs(4)),
            "marker": m.group(2),
            "task": True,
            "checked": m.group(3).lower() == "x",
            "content": m.group(4),
        }

    m = _RE_UO_LIST.match(raw)
    if m:
        return BlockType.LIST_UO, {
            "indent": len(m.group(1).expandtabs(4)),
            "marker": m.group(2),
            "content": m.group(3),
        }

    m = _RE_O_LIST.match(raw)
    if m:
        return BlockType.LIST_O, {
            "indent": len(m.group(1).expandtabs(4)),
            "num": m.group(2),
            "content": m.group(3),
        }

    m = _RE_QUOTE.match(raw)
    if m:
        # 嵌套引用：循环剥离 > 前缀，计算嵌套深度
        level = 1
        content = m.group(1)
        while True:
            m2 = _RE_QUOTE.match(content)
            if not m2:
                break
            level += 1
            content = m2.group(1)
        return BlockType.QUOTE, {"level": level, "content": content}

    if _RE_HR.match(raw):
        return BlockType.HR, {}

    m = _RE_MATH_BLOCK.match(raw)
    if m:
        return BlockType.MATH, {"content": m.group(1).strip()}

    return BlockType.PARAGRAPH, {"content": raw}


def _make_prefix_segment(block_type: BlockType, info: dict, line: Line) -> Segment:
    """构造块级前缀段。"""
    if block_type == BlockType.HEADING:
        lvl = info["level"]
        line.level = lvl
        return Segment(SegType.HEADING_PREFIX, "#" * lvl + " ", "", level=lvl)
    if block_type == BlockType.LIST_UO:
        indent = info.get("indent", 0)
        line.level = indent
        marker = info["marker"]
        # 前缀段 raw 含缩进空格，保证 "".join(segments) 重建行源码时
        # 不丢失级别（编辑提交 / 续行 / 块切换均依赖此不变量）
        indent_sp = " " * indent
        if info.get("task"):
            line.task = True
            line.checked = info["checked"]
            return Segment(
                SegType.LIST_PREFIX,
                f"{indent_sp}{marker} [{'x' if info['checked'] else ' '}] ",
                "",
                level=indent,
            )
        return Segment(SegType.LIST_PREFIX, f"{indent_sp}{marker} ", "", level=indent)
    if block_type == BlockType.LIST_O:
        indent = info.get("indent", 0)
        line.level = indent
        indent_sp = " " * indent
        return Segment(
            SegType.LIST_PREFIX, f"{indent_sp}{info['num']}. ", "", level=indent
        )
    if block_type == BlockType.QUOTE:
        lvl = info.get("level", 1)
        line.level = lvl
        return Segment(SegType.QUOTE_PREFIX, "> " * lvl, "", level=lvl)
    return Segment(SegType.TEXT, "", "")


def _build_line(raw: str) -> Line:
    """把一行源码解析为 Line（非代码块行）。"""
    bt, info = _detect_block(raw)
    line = Line(block_type=bt, raw=raw)

    if bt == BlockType.BLANK:
        line.segments = [Segment(SegType.TEXT, "", "")]
        return line

    if bt == BlockType.HR:
        line.segments = [Segment(SegType.TEXT, "---", "---")]
        return line

    if bt == BlockType.MATH:
        content = info["content"]
        line.segments = [Segment(SegType.MATH, content, content)]
        return line

    if bt == BlockType.TOC:
        line.segments = [Segment(SegType.TEXT, "[toc]", "[toc]")]
        return line

    # 带前缀的块（heading / list / quote）
    if bt in (BlockType.HEADING, BlockType.LIST_UO, BlockType.LIST_O, BlockType.QUOTE):
        prefix_seg = _make_prefix_segment(bt, info, line)
        line.segments = [prefix_seg, *parse_inline(info["content"])]
        return line

    # paragraph
    line.segments = parse_inline(raw)
    return line


# ---------------------------------------------------------------------------
# 文档级解析（含代码块合并）
# ---------------------------------------------------------------------------
def _split_code_block(raw: str) -> tuple[str, str]:
    """从代码块 raw 中提取 (lang, body)。

    raw 形如 ```lang\\n...\\n```。围栏首行单独匹配，避免多行内容
    导致 `$` 锚点失效（曾引发"双重围栏"bug）。
    """
    first_line = raw.split("\n", 1)[0] if "\n" in raw else raw
    m = _RE_CODE_FENCE.match(first_line)
    if not m:
        return "", raw

    fence = m.group(2)
    lang = m.group(3)
    body = raw.split("\n", 1)[1] if "\n" in raw else ""
    # 去掉末行围栏
    tail = "\n" + fence[0] * len(fence)
    if body.endswith(tail):
        body = body[: -len(tail)]
    return lang, body


def parse_markdown(text: str) -> Document:
    """把 Markdown 文本解析为 Document。代码块作为一个编辑单元合并。"""
    lines_src = text.split("\n")
    doc = Document()
    i, n = 0, len(lines_src)
    while i < n:
        raw = lines_src[i]
        m = _RE_CODE_FENCE.match(raw)
        if m:
            indent, fence, lang = m.group(1), m.group(2), m.group(3)
            inner: list[str] = []
            j = i + 1
            while j < n and not (
                _RE_CODE_FENCE.match(lines_src[j])
                and lines_src[j].lstrip().startswith(fence[0] * len(fence))
            ):
                inner.append(lines_src[j])
                j += 1
            code = "\n".join(inner)
            closing = lines_src[j] if j < n else fence
            full = f"{raw}\n" + (code + "\n" if inner else "") + closing
            line = Line(block_type=BlockType.CODE, raw=full, lang=lang)
            line.segments = [Segment(SegType.CODE, code, code)]
            doc.lines.append(line)
            i = j + 1
            continue
        # 块级公式围栏：$$ 独占一行开闭，中间为公式正文（可多行）。
        # 单行 $$...$$ 仍由 _detect_block -> _RE_MATH_BLOCK 处理。
        if _RE_MATH_FENCE.match(raw):
            inner_m: list[str] = []
            j = i + 1
            while j < n and not _RE_MATH_FENCE.match(lines_src[j]):
                inner_m.append(lines_src[j])
                j += 1
            formula = "\n".join(inner_m)
            closing = lines_src[j] if j < n else "$$"
            full = "$$\n" + (formula + "\n" if inner_m else "") + closing
            line = Line(block_type=BlockType.MATH, raw=full)
            line.segments = [Segment(SegType.MATH, formula, formula)]
            doc.lines.append(line)
            i = j + 1
            continue
        doc.lines.append(_build_line(raw))
        i += 1

    if not doc.lines:
        doc.lines = [_build_line("")]
    return doc


# ---------------------------------------------------------------------------
# 行重解析（编辑提交后调用）
# ---------------------------------------------------------------------------
def reparse_line(line: Line, new_raw: str | None = None) -> None:
    """用新的整行源码重新解析该行（就地更新 block_type/level/segments）。

    保留代码块 / HR / MATH 的特殊结构（整行为单位编辑，不拆段）。
    """
    if new_raw is not None:
        line.raw = new_raw
    raw = line.raw

    if line.block_type == BlockType.CODE:
        lang, body = _split_code_block(raw)
        line.lang = lang
        line.segments = [Segment(SegType.CODE, body, body)]
        return

    if line.block_type == BlockType.HR:
        line.segments = [Segment(SegType.TEXT, raw, raw)]
        return

    if line.block_type == BlockType.MATH:
        m = _RE_MATH_BLOCK.match(raw)
        content = m.group(1).strip() if m else raw
        line.segments = [Segment(SegType.MATH, content, content)]
        return

    # 普通块：完整重建
    rebuilt = _build_line(raw)
    line.block_type = rebuilt.block_type
    line.level = rebuilt.level
    line.lang = ""
    line.task = rebuilt.task
    line.checked = rebuilt.checked
    line.segments = rebuilt.segments


def segment_raw(segments: list[Segment]) -> str:
    """由段列表拼回行源码。"""
    return "".join(s.raw for s in segments)


def line_to_raw(line: Line) -> str:
    """行的源码（直接取 raw，保证序列化稳定）。"""
    return line.raw


def serialize(doc: Document) -> str:
    """文档序列化为 Markdown 文本。"""
    return "\n".join(line.raw for line in doc.lines)


def to_html(text: str) -> str:
    """Markdown 文本转 HTML（用于导出）。"""
    return _html_md(text)


# ---------------------------------------------------------------------------
# 选区 → Markdown 源码
# ---------------------------------------------------------------------------
# 行内格式包裹语法
_WRAP_SYNTAX: dict[SegType, tuple[str, str]] = {
    SegType.STRONG: ("**", "**"),
    SegType.EMPHASIS: ("*", "*"),
    SegType.CODESPAN: ("`", "`"),
    SegType.STRIKE: ("~~", "~~"),
    SegType.HIGHLIGHT: ("==", "=="),
    SegType.SUPERSCRIPT: ("^", "^"),
    SegType.SUBSCRIPT: ("~", "~"),
}


def _seg_display_text(seg: Segment) -> str:
    """段的显示文本（与 segment_view._display_text 一致）。"""
    if seg.seg_type == SegType.HEADING_PREFIX:
        return ""  # 渲染态不显示 # 前缀
    if seg.seg_type == SegType.QUOTE_PREFIX:
        return ""  # 渲染态不显示 > 前缀，引用由左边框区分
    if seg.seg_type == SegType.LIST_PREFIX:
        # 无序列表标记渲染为圆点；有序列表保留 "N. " 形式
        # raw 可能含缩进空格，先 lstrip 再判断 marker
        raw = seg.raw.lstrip()
        if raw and raw[0] in "-*+":
            return "•  "
        return raw
    if seg.seg_type in _PREFIX_SEGTYPES:
        return seg.raw
    if seg.seg_type == SegType.IMAGE:
        return seg.text or "🖼"
    if seg.seg_type == SegType.LINK:
        return seg.text or seg.url or "链接"
    return seg.text


def _wrap_partial(seg: Segment, selected_text: str) -> str:
    """对部分选中的段应用语法包裹，返回 Markdown 源码。

    组合格式（如 ***加粗斜体***）按 marks 外→内顺序嵌套包裹，保证语法完整。
    """
    if not selected_text:
        return ""
    st = seg.seg_type
    # 组合格式：marks 多于一项时按外→内嵌套
    if seg.marks and len(seg.marks) > 1:
        pre = "".join(_WRAP_SYNTAX[m][0] for m in seg.marks if m in _WRAP_SYNTAX)
        post = "".join(
            _WRAP_SYNTAX[m][1] for m in reversed(seg.marks) if m in _WRAP_SYNTAX
        )
        return f"{pre}{selected_text}{post}"
    # 单一包裹器行内格式
    if st in _WRAP_SYNTAX:
        pre, post = _WRAP_SYNTAX[st]
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
                offset = 0
                for seg in line.segments:
                    text = _seg_display_text(seg)
                    seg_start, seg_end = offset, offset + len(text)
                    offset = seg_end

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


def match_text_to_selections(
    lines: list[Line], plain_text: str
) -> dict[int, tuple[int, int]]:
    """将 SelectionArea 复制的纯文本匹配回文档行，返回选区字典。

    SelectionArea 跨行复制时不插入换行符，多行文本被直接拼接。
    因此分两种策略：
    1. 若剪贴板含 \\n → 按行逐段匹配
    2. 若无 \\n → 在全部行显示文本的拼接中查找，再映射回各行偏移
    """
    if not plain_text:
        return {}

    text = plain_text.replace("\r\n", "\n").replace("\r", "\n")

    line_texts = [
        "".join(_seg_display_text(seg) for seg in line.segments) for line in lines
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
        return selections

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


def delete_selections(lines: list[Line], selections: dict[int, tuple[int, int]]) -> tuple[list[Line], int, int, int]:
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
        total_len = sum(len(_seg_display_text(seg)) for seg in line.segments)
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
                if seg.seg_type not in _PREFIX_SEGTYPES:
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
        offset = 0
        for seg in line.segments:
            text = _seg_display_text(seg)
            seg_start, seg_end = offset, offset + len(text)
            offset = seg_end

            if seg_end <= start:
                new_segments.append(seg)
            elif seg_start >= end:
                new_segments.append(seg)
            else:
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
        return lines, li, cursor_si, cursor_offset

    # 跨行部分选中：首行选中尾部 + 尾行选中头部合并为一行，中间行删除
    first_line = lines[first_li]
    last_line = lines[last_li]
    first_base, first_extent = selections[first_li]
    last_base, last_extent = selections[last_li]
    first_start = min(first_base, first_extent)
    last_end = max(last_base, last_extent)

    # 收集首行选中之前的剩余段
    head_segments: list[Segment] = []
    offset = 0
    for seg in first_line.segments:
        text = _seg_display_text(seg)
        seg_start, seg_end = offset, offset + len(text)
        offset = seg_end
        if seg_end <= first_start:
            head_segments.append(seg)
        elif seg_start < first_start:
            sel_start = max(0, first_start - seg_start)
            if sel_start > 0:
                prefix_raw = _wrap_partial(seg, text[:sel_start])
                head_segments.append(Segment(SegType.TEXT, prefix_raw, text[:sel_start]))

    # 收集尾行选中之后的剩余段
    tail_segments: list[Segment] = []
    offset = 0
    for seg in last_line.segments:
        text = _seg_display_text(seg)
        seg_start, seg_end = offset, offset + len(text)
        offset = seg_end
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
        if seg.seg_type in _PREFIX_SEGTYPES:
            prefix_seg = seg
            break
    if prefix_seg is not None and not any(s.seg_type in _PREFIX_SEGTYPES for s in merged_segments):
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
