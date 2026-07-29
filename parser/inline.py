"""行内解析：Markdown 行内内容 → Segment 列表。

依赖项：
- parser._engine（_get_md / _RE_INLINE_MATH / _INLINE_WRAPPERS）
- models（SegType / Segment）

对外接口：
- parse_inline(content: str) -> list[Segment]：行内 Markdown 解析
- _split_inline_math / _flatten_text / _node_raw_text / _collect_marks / _token_to_segments

设计要点：
- 行内解析复用 mistune 的 AST：把行内内容包成段落解析，再取其 children，
  兼顾正确性与可维护性。
- _node_raw_text 递归重建嵌套包裹器语法，保证 "".join(segments raw) 还原行源码。
"""

from models import SegType, Segment

from parser._engine import _INLINE_WRAPPERS, _RE_INLINE_MATH, _get_md


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

    match t:
        # 普通文本：提取 $...$ 行内公式
        case "text":
            raw = tok.get("raw", "")
            return _split_inline_math(raw) if raw else []

        # 软换行 / 硬换行
        case "softbreak" | "linebreak":
            return [Segment(SegType.TEXT, "\n", "\n")]

        # 包裹型节点（加粗 / 斜体 / 删除线 / 高亮 / 上下标，含任意嵌套组合）
        case _ if t in _INLINE_WRAPPERS:
            raw, text = _node_raw_text(tok)
            marks = _collect_marks(tok)
            seg_type = marks[0] if marks else SegType.TEXT
            return [Segment(seg_type, raw, text, marks=tuple(marks))]

        # 行内代码：raw 不含反引号
        case "codespan":
            raw = tok.get("raw", "")
            return [Segment(SegType.CODESPAN, f"`{raw}`", raw)]

        # 链接 / 图片
        case "link":
            text = _flatten_text(tok.get("children", []))
            url = tok.get("attrs", {}).get("url", "")
            return [Segment(SegType.LINK, f"[{text}]({url})", text, url=url)]
        case "image":
            alt = _flatten_text(tok.get("children", []))
            url = tok.get("attrs", {}).get("url", "")
            return [Segment(SegType.IMAGE, f"![{alt}]({url})", alt, url=url)]

        # 内联 HTML
        case "inline_html":
            raw = tok.get("raw", "")
            return [Segment(SegType.TEXT, raw, raw)]

        # 未识别节点退化为纯文本
        case _:
            text = _flatten_text(tok.get("children", [])) or tok.get("raw", "")
            return [Segment(SegType.TEXT, text, text)] if text else []


def parse_inline(content: str) -> list[Segment]:
    """解析行内 Markdown 为 Segment 列表。

    将内容包成段落交由 mistune 解析，避免重复实现行内语法。
    空内容返回单个空文本段，保证行始终可被点击编辑。
    """
    if not content:
        return [Segment(SegType.TEXT, "", "")]
    ast = _get_md()(content)
    for node in ast:
        if node.get("type") in ("paragraph", "heading"):
            segs: list[Segment] = []
            for tok in node.get("children", []):
                segs.extend(_token_to_segments(tok))
            return segs or [Segment(SegType.TEXT, content, content)]
    return [Segment(SegType.TEXT, content, content)]
