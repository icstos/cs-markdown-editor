"""文档状态模型：三级状态管理（文档 / 行 / 文本段）。

依赖项：
- 标准库 dataclasses、enum
- flet（@ft.observable 装饰器）

对外接口：
- SegType：段（Span）类型枚举（StrEnum）
- BlockType：行（Block）类型枚举（StrEnum）
- Segment：行内的一个子文本段（@ft.observable dataclass）
- Line：文档中的一行（@ft.observable dataclass）
- Document：整个文档（@ft.observable dataclass）

设计要点：
- Segment：行内的一个子文本段，对应最小可编辑单元（一段纯文本、加粗、斜体、
  行内代码、链接、图片，以及块级前缀如 `# `、`- `、`> `）。
- Line：文档中的一行，持有块类型与有序的 Segment 列表。
- Document：整个文档，持有行列表与文件元信息。

三者均用 @ft.observable 装饰，字段变更会自动触发依赖组件重绘，
符合 UI = f(state) 的声明式范式。

段类型与块类型使用 StrEnum（Python 3.11+）分组管理，兼顾类型安全与字符串兼容。
"""

from dataclasses import dataclass, field
from enum import StrEnum

import flet as ft
from flet.components.observable import ObservableList


class SegType(StrEnum):
    """段（Span）类型。"""

    TEXT = "text"  # 普通文本
    STRONG = "strong"  # **加粗**
    EMPHASIS = "emphasis"  # *斜体*
    CODESPAN = "codespan"  # `行内代码`
    LINK = "link"  # [文本](url)
    IMAGE = "image"  # ![alt](url)
    STRIKE = "strikethrough"  # ~~删除线~~
    HIGHLIGHT = "highlight"  # ==高亮==
    SUPERSCRIPT = "superscript"  # ^上标^
    SUBSCRIPT = "subscript"  # ~下标~
    INLINE_MATH = "inline_math"  # $...$ 行内公式

    # 块级前缀段（也作为 Segment，统一参与"点击即编辑"）
    HEADING_PREFIX = "heading_prefix"  # "# " ~ "###### "
    LIST_PREFIX = "list_prefix"  # "- " / "* " / "1. "
    QUOTE_PREFIX = "quote_prefix"  # "> "

    # 代码块整段（一个代码块作为一个编辑单元）
    CODE = "code"
    # 行间公式整段（$$...$$ 作为一个编辑单元）
    MATH = "math"


class BlockType(StrEnum):
    """行（Block）类型。"""

    PARAGRAPH = "paragraph"
    HEADING = "heading"
    LIST_UO = "list_unordered"
    LIST_O = "list_ordered"
    QUOTE = "quote"
    CODE = "code_block"
    TABLE = "table"
    HR = "hr"
    MATH = "math"  # $$...$$ 行间公式
    TOC = "toc"  # [toc] 目录
    FRONTMATTER = "frontmatter"  # YAML 前置元数据 ---...---
    BLANK = "blank"


@dataclass
@ft.observable
class Segment:
    """行内的一个子文本段。

    seg_type：段类型
    raw：该段的原生 Markdown 源码，如 "**world**"
    text：渲染显示文本，如 "world"
    url：链接/图片地址
    level：heading 级别 / 列表缩进
    marks：组合格式标记（SegType 元组），如 (EMPHASIS, STRONG) 表示 ***加粗斜体***
    """

    seg_type: SegType = SegType.TEXT
    raw: str = ""
    text: str = ""
    url: str = ""
    level: int = 0
    marks: tuple = ()

    @staticmethod
    def text_seg(text: str) -> "Segment":
        """快速构造纯文本段。"""
        return Segment(SegType.TEXT, text, text)


@dataclass
@ft.observable
class Line:
    """文档中的一行。

    block_type：块类型
    raw：整行原生源码（序列化用）
    segments：有序段列表
    level：heading 级别 / 列表缩进
    lang：代码块语言标识
    ordered：有序列表标记
    task：是否为任务列表项（- [ ] / - [x]）
    checked：任务是否已勾选
    """

    block_type: BlockType = BlockType.PARAGRAPH
    raw: str = ""
    segments: list[Segment] = field(default_factory=list)
    level: int = 0
    lang: str = ""
    ordered: bool = False
    task: bool = False
    checked: bool = False

    @property
    def is_blank(self) -> bool:
        """是否为空行（块类型为 BLANK 或整行无可见内容）。"""
        return self.block_type == BlockType.BLANK or (not self.raw.strip())


@dataclass
@ft.observable
class Document:
    """整个文档。

    lines：行列表
    file_path：关联的文件绝对路径（未保存时为 None）
    dirty：是否有未保存修改
    """

    lines: list[Line] = field(default_factory=list)
    file_path: str | None = None
    dirty: bool = False


# ---------------------------------------------------------------------------
# 批量构造快速路径（整篇解析 / 行重建专用）
# ---------------------------------------------------------------------------
#
# 背景：`@ft.observable` 的 `Observable.__setattr__` 每次赋值都要走
# `_wrap_if_collection` → `value_equal` → `_notify`，而 `_notify` 即便
# **没有任何监听者**也会迭代 WeakSet 并自增版本号。整篇解析要构造
# 「行数 × 段数 × 字段数」个对象，实测解析 3 千行文档有 20 万次 `__setattr__`、
# 6 万次 `_notify`，**observable 通知机制占解析总耗时 57%**（cProfile）。
#
# 这些通知在解析期是纯浪费：解析发生在文档挂载之前，此时 Document/Line
# 上不可能存在监听者（监听由 flet 组件渲染时经 ObservableSubscription 建立）。
#
# 因此下面的构造器用 `object.__setattr__` 直写 `__dict__`，跳过通知链路，
# 但**产出的对象与常规构造完全同构**（`segments` / `lines` 仍是
# `ObservableList`，字段值一致），后续编辑期的可观察语义不受影响。
# 这与 `parser/reparse.py::reparse_line_atomic` 已在用的做法同源
# （该函数同样是 `object.__setattr__` + 末尾单次 notify）。
#
# 实测（3 千行混合文档）：30k 个 Segment 常规构造 206ms → 快速构造 36ms（5.7×）。


def new_segment(
    seg_type: SegType = SegType.TEXT,
    raw: str = "",
    text: str = "",
    url: str = "",
    level: int = 0,
    marks: tuple = (),
) -> Segment:
    """快速构造 Segment（跳过 observable 逐字段通知，见本节说明）。"""
    obj = Segment.__new__(Segment)
    _sa = object.__setattr__
    _sa(obj, "seg_type", seg_type)
    _sa(obj, "raw", raw)
    _sa(obj, "text", text)
    _sa(obj, "url", url)
    _sa(obj, "level", level)
    _sa(obj, "marks", marks)
    return obj


def new_line(
    block_type: BlockType = BlockType.PARAGRAPH,
    raw: str = "",
    segments: list[Segment] | tuple[Segment, ...] = (),
    level: int = 0,
    lang: str = "",
    ordered: bool = False,
    task: bool = False,
    checked: bool = False,
) -> Line:
    """快速构造 Line（跳过 observable 逐字段通知，见本节说明）。

    `segments` 仍包装为 `ObservableList`（归属本行），与常规构造的字段类型
    逐一对齐——编辑期 `line.segments` 的赋值/替换语义因此完全不变。
    """
    obj = Line.__new__(Line)
    _sa = object.__setattr__
    _sa(obj, "block_type", block_type)
    _sa(obj, "raw", raw)
    _sa(obj, "level", level)
    _sa(obj, "lang", lang)
    _sa(obj, "ordered", ordered)
    _sa(obj, "task", task)
    _sa(obj, "checked", checked)
    _sa(obj, "segments", ObservableList(obj, "segments", segments))
    return obj


def new_document(
    lines: list[Line] | None = None,
    file_path: str | None = None,
    dirty: bool = False,
) -> Document:
    """快速构造 Document（跳过 observable 逐字段通知，见本节说明）。

    `lines` 仍包装为 `ObservableList`：编辑器大量使用
    `document.lines.insert / __setitem__ / __delitem__` 原地变更，
    依赖其 `_touch()` 触发重渲染，类型必须与常规构造一致。
    """
    obj = Document.__new__(Document)
    _sa = object.__setattr__
    _sa(obj, "lines", ObservableList(obj, "lines", lines or []))
    _sa(obj, "file_path", file_path)
    _sa(obj, "dirty", dirty)
    return obj
