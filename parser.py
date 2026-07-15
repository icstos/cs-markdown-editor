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

# 行内解析器：启用删除线插件以获得更丰富的段类型
_md = mistune.create_markdown(renderer="ast", plugins=["strikethrough"])
_html_md = mistune.create_markdown(
    renderer="html", plugins=["strikethrough", "table", "footnotes", "task_lists"]
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
_INLINE_WRAPPERS: dict[str, tuple[SegType, str]] = {
    "strong": (SegType.STRONG, "**"),
    "emphasis": (SegType.EMPHASIS, "*"),
    "strikethrough": (SegType.STRIKE, "~~"),
}


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

    # 包裹型节点（加粗 / 斜体 / 删除线）
    if t in _INLINE_WRAPPERS:
        seg_type, wrap = _INLINE_WRAPPERS[t]
        text = _flatten_text(tok.get("children", []))
        return [Segment(seg_type, f"{wrap}{text}{wrap}", text)]

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
_PREFIX_SEGTYPES = (SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX)

# 行内格式包裹语法
_WRAP_SYNTAX: dict[SegType, tuple[str, str]] = {
    SegType.STRONG: ("**", "**"),
    SegType.EMPHASIS: ("*", "*"),
    SegType.CODESPAN: ("`", "`"),
    SegType.STRIKE: ("~~", "~~"),
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
    """对部分选中的段应用语法包裹，返回 Markdown 源码。"""
    if not selected_text:
        return ""
    st = seg.seg_type
    # 带包裹器的行内格式（加粗/斜体/行内代码/删除线）
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
