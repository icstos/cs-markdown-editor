"""块级解析：Markdown 文本 → Document（行级 + 块类型识别 + 代码块/数学块/表格合并）。

依赖项：
- parser._engine（块级正则常量）
- parser.inline（parse_inline）
- models（BlockType / Document / Line / SegType / Segment）
- utils.table_helpers（is_table_separator）

对外接口：
- parse_markdown(text: str) -> Document：解析 Markdown 文本
- _detect_block / _make_prefix_segment / _build_line / _is_table_row

设计要点：
- 行级（Line）：按行扫描，识别块类型（标题 / 列表 / 引用 / 代码块 / 分隔线 / 段落）。
- 段级（Segment）：块级前缀与行内内容统一抽象为 Segment 列表，从而让
  "点击即编辑"对前缀与行内 span 行为一致（参考 Typora）。
- 代码块 / 数学块围栏 / 表格作为编辑单元合并，保留分隔行（对齐信息持久化）。
"""

from models.document import (
    BlockType,
    Document,
    Line,
    SegType,
    Segment,
    new_document,
    new_line,
    new_segment,
)
from utils.table_helpers import is_table_separator

from parser._engine import (
    _RE_CODE_FENCE,
    _RE_FRONTMATTER_FENCE,
    _RE_HEADING,
    _RE_HR,
    _RE_MATH_BLOCK,
    _RE_MATH_FENCE,
    _RE_O_LIST,
    _RE_QUOTE,
    _RE_TASK,
    _RE_TOC,
    _RE_UO_LIST,
)
from parser.inline import parse_inline


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
        # 嵌套引用：循环剥离 > 前缀，计算嵌套深度；同时累计实际前缀字符
        # （">" / "> " / ">> " 等，保持 line.raw == "".join(segments.raw)
        # 不变量，避免 ">x"、">>x" 等无空格写法段/行长度不一致导致
        # 光标偏移数组错位）。
        level = 1
        content = m.group(1)
        prefix = raw[: m.start(1)]
        while True:
            m2 = _RE_QUOTE.match(content)
            if not m2:
                break
            level += 1
            prefix += content[: m2.start(1)]
            content = m2.group(1)
        return BlockType.QUOTE, {
            "level": level,
            "content": content,
            "prefix": prefix,
        }

    if _RE_HR.match(raw):
        return BlockType.HR, {}

    m = _RE_MATH_BLOCK.match(raw)
    if m:
        return BlockType.MATH, {"content": m.group(1).strip()}

    return BlockType.PARAGRAPH, {"content": raw}


def _make_prefix_segment(block_type: BlockType, info: dict) -> tuple[Segment, dict]:
    """构造块级前缀段，返回 (segment, line_attrs)。

    line_attrs 含应赋给 Line 的属性（level / task / checked）；由 _build_line
    统一赋值，不在构造过程中副作用修改 Line——分离"构造"与"修改"职责。
    """
    if block_type == BlockType.HEADING:
        lvl = info["level"]
        return new_segment(SegType.HEADING_PREFIX, "#" * lvl + " ", "", level=lvl), {
            "level": lvl
        }
    if block_type == BlockType.LIST_UO:
        indent = info.get("indent", 0)
        marker = info["marker"]
        # 前缀段 raw 含缩进空格，保证 "".join(segments) 重建行源码时
        # 不丢失级别（编辑提交 / 续行 / 块切换均依赖此不变量）
        indent_sp = " " * indent
        if info.get("task"):
            attrs = {"level": indent, "task": True, "checked": info["checked"]}
            return new_segment(
                SegType.LIST_PREFIX,
                f"{indent_sp}{marker} [{'x' if info['checked'] else ' '}] ",
                "",
                level=indent,
            ), attrs
        return new_segment(
            SegType.LIST_PREFIX, f"{indent_sp}{marker} ", "", level=indent
        ), {"level": indent}
    if block_type == BlockType.LIST_O:
        indent = info.get("indent", 0)
        indent_sp = " " * indent
        return (
            new_segment(
                SegType.LIST_PREFIX, f"{indent_sp}{info['num']}. ", "", level=indent
            ),
            {"level": indent},
        )
    if block_type == BlockType.QUOTE:
        lvl = info.get("level", 1)
        # 前缀 raw 用源码实际字符（">" / "> " / ">> "），保证
        # line.raw == "".join(segments.raw) 不变量（编辑/序列化/光标偏移依赖）
        prefix = info.get("prefix") or "> " * lvl
        return new_segment(SegType.QUOTE_PREFIX, prefix, "", level=lvl), {"level": lvl}
    return new_segment(SegType.TEXT, "", ""), {}


def _build_line(raw: str) -> Line:
    """把一行源码解析为 Line（非代码块行）。

    统一走 `models.new_line` / `new_segment` 快速构造器：整篇解析要构造
    「行数 × 段数」个对象，常规构造每次都触发 observable 通知链路
    （无监听者时也迭代 WeakSet），实测占解析总耗时 57%。详见
    `models/document.py` 的「批量构造快速路径」说明。
    """
    bt, info = _detect_block(raw)

    if bt == BlockType.BLANK:
        return new_line(bt, raw, [new_segment(SegType.TEXT, "", "")])

    if bt == BlockType.HR:
        # 保留原 raw（---/***/___），确保 line.raw == "".join(s.raw)，
        # 激活态编辑时光标位置与 raw 同步
        return new_line(bt, raw, [new_segment(SegType.TEXT, raw, raw)])

    if bt == BlockType.MATH:
        content = info["content"]
        return new_line(bt, raw, [new_segment(SegType.MATH, content, content)])

    if bt == BlockType.TOC:
        return new_line(bt, raw, [new_segment(SegType.TEXT, "[toc]", "[toc]")])

    if bt == BlockType.TABLE:
        # 表格行由专门的表格视图渲染，segments 只保留原始行源码，便于编辑回写。
        return new_line(bt, raw, [new_segment(SegType.TEXT, raw, raw)])

    # 带前缀的块（heading / list / quote）
    if bt in (BlockType.HEADING, BlockType.LIST_UO, BlockType.LIST_O, BlockType.QUOTE):
        prefix_seg, attrs = _make_prefix_segment(bt, info)
        return new_line(
            block_type=bt,
            raw=raw,
            segments=[prefix_seg, *parse_inline(info["content"])],
            **attrs,
        )

    # paragraph
    return new_line(bt, raw, parse_inline(raw))


def _is_table_row(raw: str) -> bool:
    """表格数据行判定：含 | 且首尾为 |。"""
    return "|" in raw and raw.strip().startswith("|") and raw.strip().endswith("|")


def parse_markdown(text: str) -> Document:
    """把 Markdown 文本解析为 Document。代码块作为一个编辑单元合并。

    行缓冲在普通 list 中累积，最后一次性交给 `new_document` 包装为
    `ObservableList`——避免逐行 `doc.lines.append()` 触发 observable 通知
    （解析期无监听者，通知是纯开销）。产出的 Document 与逐行 append
    完全同构。
    """
    lines_src = text.split("\n")
    out: list[Line] = []
    i, n = 0, len(lines_src)
    while i < n:
        raw = lines_src[i]
        # ---- YAML 前置元数据（仅文档首行，---...--- 围栏）----
        # Obsidian/Pandoc/Jekyll 风格：文档以 --- 开头，配对 --- 闭合。
        # 整块合并为一个 FRONTMATTER 编辑单元（与代码块同等处理），
        # 渲染为 Obsidian 风格的属性卡片。无配对关闭围栏时降级为普通行（HR/段落）。
        if i == 0 and _RE_FRONTMATTER_FENCE.match(raw):
            inner_lines: list[str] = []
            j = i + 1
            while j < n and not _RE_FRONTMATTER_FENCE.match(lines_src[j]):
                inner_lines.append(lines_src[j])
                j += 1
            if j < n:
                # 找到配对关闭围栏，合并为 frontmatter 块
                content = "\n".join(inner_lines)
                full = f"---\n" + (content + "\n" if inner_lines else "") + "---"
                out.append(
                    new_line(
                        BlockType.FRONTMATTER, full,
                        [new_segment(SegType.CODE, content, content)],
                    )
                )
                i = j + 1
                continue
            # 无配对关闭围栏：降级为普通行（不作为 frontmatter 处理）
        m = _RE_CODE_FENCE.match(raw)
        if m:
            _, fence, lang = m.group(1), m.group(2), m.group(3)
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
            out.append(
                new_line(
                    BlockType.CODE, full,
                    [new_segment(SegType.CODE, code, code)],
                    lang=lang,
                )
            )
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
            out.append(
                new_line(
                    BlockType.MATH, full,
                    [new_segment(SegType.MATH, formula, formula)],
                )
            )
            i = j + 1
            continue
        if _is_table_row(raw) and i + 1 < n and is_table_separator(lines_src[i + 1]):
            # 保留分隔行：对齐信息（:---: / ---: / :---）必须持久化到
            # document.lines，否则 set_align 找不到分隔行会把 :---: 写到
            # 数据行，且表格渲染时对齐信息丢失（所有列默认 left）。
            # 此前实现跳过分隔行（j = i + 2），导致：
            #   1. set_align 写到数据行（"在下方单元格写入了 center 字符"）
            #   2. 已有对齐信息丢失（打开 | :---: | 文件后渲染为 left）
            table_lines = [raw, lines_src[i + 1]]
            j = i + 2
            while j < n and _is_table_row(lines_src[j]) and lines_src[j].strip():
                table_lines.append(lines_src[j])
                j += 1
            for row in table_lines:
                out.append(
                    new_line(
                        BlockType.TABLE, row, [new_segment(SegType.TEXT, row, row)]
                    )
                )
            i = j
            continue
        out.append(_build_line(raw))
        i += 1

    if not out:
        out.append(_build_line(""))
    return new_document(out)
