"""纯文本文档解析：把任意源码 / 文本文件整篇变成一个代码块。

设计（为什么是"一个代码块"而不是"逐行一个块"）：
- 编辑器里**代码块是岛屿**（`views/code_block.py`）：一个 BlockType.CODE 行承载
  多行代码，自带行号、语法高亮、横向滚动与点选定位。整篇用一个 CODE 行，
  用户看到的就是 Typora 里"一个围栏代码块"的样子，交互（点击进编辑、Tab 缩进、
  方向键跳出、复制）全部复用既有实现，不需要给纯文本另开一条渲染路径。
- 反过来"每行一个 CODE 行"会把文件切成一堆碎片：行号每行从 1 重新开始、
  无法整篇复制、`line.lang` 冗余 N 份，且滚动定位会把每行当独立岛屿。
- 因此本模块只做两件事：把正文装进一个 CODE 行；把 `Document.plain` 置真，
  让序列化 / 保存 / 语言切换知道"这不是 Markdown 围栏，正文就是文件本身"。

不变量（下游全部依赖）：
- `doc.lines` 恰好 1 行，`block_type == CODE`，`line.raw == 正文全文`
  （不是 ``` 围栏形式）；`line.segments[0].text` 与正文一致。
- 正文内的换行一律是 "\\n"：代码块按 "\\n" 切行、按行数算行号，
  混入 "\\r" 会变成可见字符并让行号错位。
"""

from __future__ import annotations

from models.document import BlockType, Document, SegType, new_document, new_line, new_segment


def parse_plain_text(text: str, lang: str = "") -> Document:
    """把纯文本 / 源码包装成「整篇一个代码块」的文档。

    Args:
        text: 文件正文（任意换行风格；内部会归一成 "\\n"）。
        lang: 语法高亮语言标识（`utils.text_formats.lang_for_path` 给出；
            空串=纯文本，不做分词）。

    Returns:
        Document：`plain=True`，单行 CODE；`eol` 由调用方（装载层）按原文件设置。
    """
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    line = new_line(
        BlockType.CODE,
        body,
        [new_segment(SegType.CODE, body, body)],
        lang=lang or "",
    )
    return new_document([line], plain=True)


def is_plain_document(doc: Document | None) -> bool:
    """文档是否纯文本模式（防御性读取：非 Document / 旧对象一律当 False）。"""
    return bool(getattr(doc, "plain", False))
