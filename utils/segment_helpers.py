"""段（Segment）共享常量与显示逻辑。

依赖项：models（SegType / Segment）。
对外接口：
- PREFIX_SEGTYPES：tuple[SegType]，块级前缀段类型集合
- MONO_SEGTYPES：tuple[SegType]，等宽字体段类型集合
- WRAP_SYNTAX：dict[SegType, tuple[str, str]]，包裹型段的开闭标记
- display_text(seg: Segment) -> str：渲染态展示文本
- split_seg_for_display(seg: Segment) -> list[tuple[str, bool]]：拆段为 [(text, is_marker), ...]

消除重复：原先 _PREFIX_SEGTYPES / _WRAP_CHAR / _WRAP_MAP / _WRAP_SYNTAX /
_display_text / _seg_display_text / _split_seg_for_display 在 parser.py、
segment_view.py、rendered_line.py、editor.py 多处重复定义，此处统一。
所有视图与解析层都从此处导入，保证段显示行为一致。
"""

from models import SegType, Segment

# 块级前缀段（# / - / >）：渲染态透明、编辑态灰色
PREFIX_SEGTYPES: tuple[SegType, ...] = (
    SegType.HEADING_PREFIX,
    SegType.LIST_PREFIX,
    SegType.QUOTE_PREFIX,
)

# 等宽字体段：codespan / code / inline_math / math
MONO_SEGTYPES: tuple[SegType, ...] = (
    SegType.CODESPAN,
    SegType.CODE,
    SegType.INLINE_MATH,
    SegType.MATH,
)

# 包裹型段（包裹器）的开闭标记，用于 raw 重建与显示拆分
WRAP_SYNTAX: dict[SegType, tuple[str, str]] = {
    SegType.STRONG: ("**", "**"),
    SegType.EMPHASIS: ("*", "*"),
    SegType.CODESPAN: ("`", "`"),
    SegType.STRIKE: ("~~", "~~"),
    SegType.HIGHLIGHT: ("==", "=="),
    SegType.SUPERSCRIPT: ("^", "^"),
    SegType.SUBSCRIPT: ("~", "~"),
    SegType.INLINE_MATH: ("$", "$"),  # 行内公式 $...$
}

# split_seg_for_display 用的单字符包裹映射（key 为单一包裹器 SegType）
_WRAP_CHAR: dict[SegType, str] = {
    SegType.STRONG: "**",
    SegType.EMPHASIS: "*",
    SegType.STRIKE: "~~",
    SegType.HIGHLIGHT: "==",
    SegType.SUPERSCRIPT: "^",
    SegType.SUBSCRIPT: "~",
}


def display_text(seg: Segment) -> str:
    """渲染态展示文本。

    规则：
    - HEADING_PREFIX / QUOTE_PREFIX：渲染态透明（返回 ""），由颜色/边框区分
    - LIST_PREFIX：无序标记渲染为圆点 "•  "；有序保留 "N. "；任务项返回 ""
    - IMAGE：返回 alt 文本或占位符
    - LINK：返回链接文本或 url 或占位符
    - 其余：返回 seg.text

    与 segment_view._display_text、parser._seg_display_text 行为完全一致。
    """
    t = seg.seg_type
    if t == SegType.HEADING_PREFIX:
        return ""
    if t == SegType.QUOTE_PREFIX:
        return ""
    if t == SegType.LIST_PREFIX:
        raw = seg.raw.lstrip()
        if raw and raw[0] in "-*+":
            rest = raw[1:].lstrip()
            if rest[:3] in ("[ ]", "[x]", "[X]"):
                return ""
            return "•  "
        return raw
    if t in PREFIX_SEGTYPES:
        return seg.raw
    if t == SegType.IMAGE:
        return seg.text or "🖼"
    if t == SegType.LINK:
        return seg.text or seg.url or "链接"
    return seg.text


def split_seg_for_display(
    seg: Segment, cursor_local: int | None = None
) -> list[tuple[str, bool]]:
    """把段拆成 [(text, is_marker), ...]，用于 Typora 式渲染态展示。

    is_marker=True 的部分在 Typora 模式下可透明/灰色切换；
    is_marker=False 的部分为内容，按段样式渲染。

    cursor_local：光标在段内 raw 的偏移（0..len(raw)）；None=浏览态/偏移测量态。
    LINK/IMAGE 的 URL 子段：始终返回非空（url_part），由调用方决定渲染方式。
    - 光标在段内（cursor_local=int）：URL 灰色可见（编辑链接需看到完整语法）。
    - cursor_local=None：URL 作为 marker，渲染层对 marker 零宽度处理（浏览态折叠）。
    """
    raw = seg.raw
    if not raw:
        return []

    t = seg.seg_type

    if t in PREFIX_SEGTYPES:
        return [(raw, True)]

    if t == SegType.INLINE_MATH:
        if len(raw) >= 2 and raw[0] == "$" and raw[-1] == "$":
            return [("$", True), (raw[1:-1], False), ("$", True)]
        return [(raw, False)]

    if t == SegType.CODESPAN:
        if len(raw) >= 2 and raw[0] == "`" and raw[-1] == "`":
            return [("`", True), (raw[1:-1], False), ("`", True)]
        return [(raw, False)]

    if t == SegType.LINK:
        if raw.startswith("[") and raw.endswith(")") and "](" in raw:
            idx = raw.index("](")
            text_part = raw[1:idx]
            url_part = raw[idx + 2:-1]
            # 光标在段内时 URL 始终可见（编辑链接需看到完整语法，避免 URL 被折叠）
            # 与 pixel_layout 偏移测量一致（光标在段内时 measure_text_offsets 测完整 raw 含 URL）
            # cursor_local=None（偏移测量/选区高亮）：URL 作为 marker，由调用方决定是否渲染
            return [("[", True), (text_part, False), ("](", True), (url_part, True), (")", True)]
        return [(raw, False)]

    if t == SegType.IMAGE:
        if raw.startswith("![") and raw.endswith(")") and "](" in raw:
            idx = raw.index("](")
            alt_part = raw[2:idx]
            url_part = raw[idx + 2:-1]
            return [("![", True), (alt_part, False), ("](", True), (url_part, True), (")", True)]
        return [(raw, False)]

    marks = seg.marks or ()
    if not marks and t in _WRAP_CHAR:
        marks = (t,)
    if marks:
        prefix = "".join(_WRAP_CHAR[m] for m in marks)
        suffix = "".join(_WRAP_CHAR[m] for m in reversed(marks))
        if prefix and raw.startswith(prefix) and raw.endswith(suffix) and len(raw) >= len(prefix) + len(suffix):
            content = raw[len(prefix):len(raw) - len(suffix)] if suffix else raw[len(prefix):]
            return [(prefix, True), (content, False), (suffix, True)]
        return [(raw, False)]

    return [(raw, False)]
