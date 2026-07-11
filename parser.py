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

from models import (
    BlockType,
    Document,
    Line,
    SegType,
    Segment,
)

# 行内解析器：启用删除线与任务列表插件以获得更丰富的段类型
_md = mistune.create_markdown(renderer="ast", plugins=["strikethrough"])

# ---- 正则：块级前缀识别 ----
_RE_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_RE_UO_LIST = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_RE_O_LIST = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")
_RE_QUOTE = re.compile(r"^>\s?(.*)$")
_RE_HR = re.compile(r"^(\s*)([-*_])\2\2+\s*$")  # --- ** ___ 等
_RE_CODE_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([\w+-]*)\s*$")
_RE_TASK = re.compile(r"^(\s*)([-*+])\s+\[( |x|X)\]\s+(.*)$")
_RE_MATH_BLOCK = re.compile(r"^\$\$(.+?)\$\$\s*$", re.DOTALL)  # $$...$$ 行间公式
_RE_INLINE_MATH = re.compile(r"\$([^$\n]+?)\$")  # $...$ 行内公式


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
    return parts if parts else [Segment(SegType.TEXT, text, text)]


def _token_to_segments(tok: dict) -> list[Segment]:
    """把一个行内 AST 节点转成 Segment 列表。"""
    t = tok.get("type")
    if t == SegType.TEXT.value:
        raw = tok.get("raw", "")
        if not raw:
            return []
        return _split_inline_math(raw)
    if t == "softbreak":
        return [Segment(SegType.TEXT, "\n", "\n")]
    if t == "linebreak":
        return [Segment(SegType.TEXT, "\n", "\n")]
    if t == SegType.STRONG.value:
        inner = "".join(
            c.get("raw", "") for c in tok.get("children", []) if c.get("type") == "text"
        )
        # 递归收集子节点文本（简化：仅取纯文本展示）
        text = _flatten_text(tok.get("children", []))
        return [Segment(SegType.STRONG, f"**{text}**", text)]
    if t == SegType.EMPHASIS.value:
        text = _flatten_text(tok.get("children", []))
        return [Segment(SegType.EMPHASIS, f"*{text}*", text)]
    if t == SegType.STRIKE.value:
        text = _flatten_text(tok.get("children", []))
        return [Segment(SegType.STRIKE, f"~~{text}~~", text)]
    if t == SegType.CODESPAN.value:
        raw = tok.get("raw", "")
        # codespan 的 raw 形如 "code"（不含反引号）
        return [Segment(SegType.CODESPAN, f"`{raw}`", raw)]
    if t == SegType.LINK.value:
        text = _flatten_text(tok.get("children", []))
        url = tok.get("attrs", {}).get("url", "")
        return [Segment(SegType.LINK, f"[{text}]({url})", text, url=url)]
    if t == SegType.IMAGE.value:
        alt = _flatten_text(tok.get("children", []))
        url = tok.get("attrs", {}).get("url", "")
        return [Segment(SegType.IMAGE, f"![{alt}]({url})", alt, url=url)]
    if t == "inline_html":
        raw = tok.get("raw", "")
        return [Segment(SegType.TEXT, raw, raw)]
    # 其它未识别节点退化为纯文本
    text = _flatten_text(tok.get("children", [])) or tok.get("raw", "")
    return [Segment(SegType.TEXT, text, text)] if text else []


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
            if segs:
                return segs
            return [Segment(SegType.TEXT, content, content)]
    # 解析失败时回退为纯文本
    return [Segment(SegType.TEXT, content, content)]


# ---------------------------------------------------------------------------
# 行级解析
# ---------------------------------------------------------------------------
def _detect_block(raw: str) -> tuple[BlockType, dict]:
    """识别一行的块类型，返回 (block_type, info)。"""
    if not raw.strip():
        return BlockType.BLANK, {}

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
        return BlockType.QUOTE, {"content": m.group(1)}

    m = _RE_HR.match(raw)
    if m:
        return BlockType.HR, {}

    m = _RE_MATH_BLOCK.match(raw)
    if m:
        return BlockType.MATH, {"content": m.group(1).strip()}

    return BlockType.PARAGRAPH, {"content": raw}


def _build_line(raw: str) -> Line:
    """把一行源码解析为 Line（非代码块行）。"""
    bt, info = _detect_block(raw)
    line = Line(block_type=bt, raw=raw)

    if bt == BlockType.BLANK:
        line.segments = [Segment(SegType.TEXT, "", "")]
        return line

    if bt == BlockType.HEADING:
        lvl = info["level"]
        line.level = lvl
        prefix = "#" * lvl + " "
        line.segments = [Segment(SegType.HEADING_PREFIX, prefix, "", level=lvl)]
        line.segments.extend(parse_inline(info["content"]))
        return line

    if bt == BlockType.LIST_UO:
        line.level = info.get("indent", 0)
        marker = info["marker"]
        if info.get("task"):
            line.task = True
            line.checked = info["checked"]
            prefix = f"{marker} [{'x' if info['checked'] else ' '}] "
        else:
            prefix = f"{marker} "
        line.segments = [Segment(SegType.LIST_PREFIX, prefix, "", level=line.level)]
        line.segments.extend(parse_inline(info["content"]))
        return line

    if bt == BlockType.LIST_O:
        line.level = info.get("indent", 0)
        prefix = f"{info['num']}. "
        line.segments = [Segment(SegType.LIST_PREFIX, prefix, "", level=line.level)]
        line.segments.extend(parse_inline(info["content"]))
        return line

    if bt == BlockType.QUOTE:
        line.segments = [Segment(SegType.QUOTE_PREFIX, "> ", "")]
        line.segments.extend(parse_inline(info["content"]))
        return line

    if bt == BlockType.HR:
        line.segments = [Segment(SegType.TEXT, "---", "---")]
        return line

    if bt == BlockType.MATH:
        content = info["content"]
        line.segments = [Segment(SegType.MATH, content, content)]
        return line

    # paragraph
    line.segments = parse_inline(raw)
    return line


# ---------------------------------------------------------------------------
# 文档级解析（含代码块合并）
# ---------------------------------------------------------------------------
def parse_markdown(text: str) -> Document:
    """把 Markdown 文本解析为 Document。代码块作为一个编辑单元合并。"""
    lines_src = text.split("\n")
    doc = Document()
    i = 0
    n = len(lines_src)
    while i < n:
        raw = lines_src[i]
        m = _RE_CODE_FENCE.match(raw)
        if m:
            indent = m.group(1)
            fence = m.group(2)
            lang = m.group(3)
            inner: list[str] = []
            j = i + 1
            while j < n:
                if _RE_CODE_FENCE.match(lines_src[j]) and lines_src[
                    j
                ].lstrip().startswith(fence[0] * len(fence)):
                    break
                inner.append(lines_src[j])
                j += 1
            code = "\n".join(inner)
            full = (
                raw
                + ("\n" + code if inner else "")
                + ("\n" + lines_src[j] if j < n else "")
            )
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

    保留代码块与 HR 的特殊结构（它们以整行为单位编辑，不拆段）。
    """
    if new_raw is not None:
        line.raw = new_raw

    raw = line.raw

    # 代码块 / HR：整行编辑，不拆段
    if line.block_type == BlockType.CODE:
        # raw 形如 ```lang\n...\n```；更新内部 code 段
        m = _RE_CODE_FENCE.match(raw)
        if m:
            fence = m.group(2)
            lang = m.group(3)
            line.lang = lang
            # 提取围栏内文本
            body = raw.split("\n", 1)[1] if "\n" in raw else ""
            if body.endswith("\n" + fence[0] * len(fence)):
                body = body[: -(len(fence) + 1)]
            line.segments = [Segment(SegType.CODE, body, body)]
        else:
            line.segments = [Segment(SegType.CODE, raw, raw)]
        return

    if line.block_type == BlockType.HR:
        line.segments = [Segment(SegType.TEXT, raw, raw)]
        return

    if line.block_type == BlockType.MATH:
        m = _RE_MATH_BLOCK.match(raw)
        if m:
            content = m.group(1).strip()
            line.segments = [Segment(SegType.MATH, content, content)]
        else:
            line.segments = [Segment(SegType.MATH, raw, raw)]
        return

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
