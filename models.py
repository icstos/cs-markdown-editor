"""文档状态模型：三级状态管理（文档 / 行 / 文本段）。

- Segment：行内的一个子文本段，对应最小可编辑单元（一段纯文本、加粗、斜体、
  行内代码、链接、图片，以及块级前缀如 `# `、`- `、`> `）。
- Line：文档中的一行，持有块类型与有序的 Segment 列表。
- Document：整个文档，持有行列表与文件元信息。

三者均用 `@ft.observable` 装饰，字段变更会自动触发依赖组件重绘，符合
UI = f(state) 的声明式范式。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import flet as ft

# ---- 段（Span）类型 ----
SEG_TEXT = "text"  # 普通文本
SEG_STRONG = "strong"  # **加粗**
SEG_EMPHASIS = "emphasis"  # *斜体*
SEG_CODESPAN = "codespan"  # `行内代码`
SEG_LINK = "link"  # [文本](url)
SEG_IMAGE = "image"  # ![alt](url)
SEG_STRIKE = "strikethrough"  # ~~删除线~~

# 块级前缀段（也作为 Segment，统一参与“点击即编辑”）
SEG_HEADING_PREFIX = "heading_prefix"  # "# " ~ "###### "
SEG_LIST_PREFIX = "list_prefix"  # "- " / "* " / "1. "
SEG_QUOTE_PREFIX = "quote_prefix"  # "> "

# 代码块整段（一个代码块作为一个编辑单元）
SEG_CODE = "code"

# ---- 行（Block）类型 ----
BLOCK_PARAGRAPH = "paragraph"
BLOCK_HEADING = "heading"
BLOCK_LIST_UO = "list_unordered"
BLOCK_LIST_O = "list_ordered"
BLOCK_QUOTE = "quote"
BLOCK_CODE = "code_block"
BLOCK_HR = "hr"
BLOCK_BLANK = "blank"


@dataclass
@ft.observable
class Segment:
    """行内的一个子文本段。"""

    seg_type: str = SEG_TEXT
    raw: str = ""  # 该段的原生 Markdown 源码，如 "**world**"
    text: str = ""  # 渲染显示文本，如 "world"
    url: str = ""  # 链接/图片地址
    level: int = 0  # heading 级别 / 列表缩进

    @staticmethod
    def text_seg(text: str) -> "Segment":
        return Segment(SEG_TEXT, text, text)


@dataclass
@ft.observable
class Line:
    """文档中的一行。"""

    block_type: str = BLOCK_PARAGRAPH
    raw: str = ""  # 整行原生源码（序列化用）
    segments: list[Segment] = field(default_factory=list)
    level: int = 0  # heading 级别 / 列表缩进
    lang: str = ""  # 代码块语言标识
    ordered: bool = False  # 有序列表标记

    @property
    def is_blank(self) -> bool:
        return self.block_type == BLOCK_BLANK or (not self.raw.strip())


@dataclass
@ft.observable
class Document:
    """整个文档。"""

    lines: list[Line] = field(default_factory=list)
    file_path: Optional[str] = None
    dirty: bool = False
