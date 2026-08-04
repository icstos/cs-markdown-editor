"""文档序列化与 HTML 导出。

依赖项：
- parser._engine（_get_html_md 惰性实例）
- parser.block（parse_markdown）
- models（BlockType / Document / SegType）

对外接口：
- serialize(doc: Document) -> str：文档序列化为 Markdown 文本
- to_html(text: str) -> str：Markdown 文本转 HTML（用于导出）
- strip_markdown(text: str) -> str：Markdown 文本转纯文本（去除所有语法标记）

设计要点：
- serialize 直接拼接 line.raw，保证序列化稳定（不依赖 segments 重建）。
- to_html 用惰性 _get_html_md：仅导出时才构造 mistune HTML 实例
  （含 footnotes/task_lists 插件链），启动期不加载。
- strip_markdown 用于 Ctrl+Shift+V 纯文本粘贴：解析 Markdown 后提取可见文本，
  seg.text 已去除行内语法标记（** / * / ` / $ 等），只需跳过块级前缀段
  并对围栏块（CODE/MATH/TABLE/TOC/HR）做特殊处理。
"""

from models import BlockType, Document, SegType

from parser._engine import _get_html_md
from parser.block import parse_markdown


def serialize(doc: Document) -> str:
    """文档序列化为 Markdown 文本。"""
    return "\n".join(line.raw for line in doc.lines)


def to_html(text: str) -> str:
    """Markdown 文本转 HTML（用于导出）。"""
    return _get_html_md()(text)


def strip_markdown(text: str) -> str:
    """将 Markdown 文本转为纯文本（去除所有语法标记）。

    用于 Ctrl+Shift+V（粘贴为纯文本）：Typora 式行为——剥离所有 Markdown
    语法标记，仅保留可见文本内容。

    规则：
    - 块级前缀段（# / - / >）：跳过，仅保留内容文本
    - 包裹型段（** * ` ~~ == ^ ~ $）：seg.text 已是无标记内容，直接使用
    - 链接 [text](url)：仅保留 text
    - 图片 ![alt](url)：仅保留 alt
    - 围栏块 CODE：保留代码内容（seg.text 已是无 ``` 围栏的纯代码）
    - 围栏块 MATH：保留公式内容（seg.text 已是无 $$ 的 LaTeX）
    - 表格 TABLE：去除 | 分隔符，单元格以 Tab 分隔
    - TOC / HR：跳过（无文本内容）
    """
    if not text:
        return ""
    doc = parse_markdown(text)
    plain_lines: list[str] = []
    for line in doc.lines:
        bt = line.block_type
        if bt in (BlockType.CODE, BlockType.MATH):
            # 围栏块：seg.text 已是无围栏标记的纯内容
            plain_lines.append(line.segments[0].text if line.segments else "")
            continue
        if bt in (BlockType.TOC, BlockType.HR):
            # 目录标记 / 分隔线：跳过（无文本内容）
            continue
        if bt == BlockType.TABLE:
            # 表格行：去除 | 分隔符，单元格以 Tab 分隔
            row = line.raw.strip()
            if row.startswith("|"):
                row = row[1:]
            if row.endswith("|"):
                row = row[:-1]
            cells = [c.strip() for c in row.split("|")]
            plain_lines.append("\t".join(cells))
            continue
        # 普通行：拼接各段 text（seg.text 已去除行内语法标记）
        plain_parts: list[str] = []
        for seg in line.segments:
            # 跳过块级前缀段（标题 # / 列表 - / 引用 > ）
            if seg.seg_type in (SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX):
                continue
            plain_parts.append(seg.text or "")
        plain_lines.append("".join(plain_parts))
    return "\n".join(plain_lines)
