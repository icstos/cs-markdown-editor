"""表格行解析与拼接工具。

依赖项：标准库 re。
对外接口：
- ALIGN_RE：re.Pattern，对齐分隔单元格正则（:?-{3,}:?）
- split_row(raw: str) -> list[str]：拆表格行单元格
- join_row(cells: list[str]) -> str：拼表格行源码
- align_marker(align: str) -> str：对齐方式 → 分隔行标记（"---" / ":---:" / "---:"）
- is_table_separator(raw: str) -> bool：是否为对齐分隔行

单一来源：split_row / join_row / align_marker / is_table_separator 原先在
parser、table_view 等处各有一份，此处统一；视图层与编辑器不得再自带副本。
"""

import re

# 对齐分隔单元格：:?-{3,}:?（左/右/居中对齐）
ALIGN_RE: re.Pattern[str] = re.compile(r"^:?-{3,}:?$")


def split_row(raw: str) -> list[str]:
    """拆表格行源码为单元格列表（trim 首尾空格与竖线）。

    例："| a | b |" → ["a", "b"]
    """
    return [c.strip() for c in raw.strip().strip("|").split("|")]


def join_row(cells: list[str]) -> str:
    """拼单元格列表为表格行源码。

    例：["a", "b"] → "| a | b |"
    """
    return "| " + " | ".join(cells) + " |"


def align_marker(align: str) -> str:
    """对齐方式 → 表格分隔行标记；未知对齐退化为左对齐。"""
    return {"left": "---", "center": ":---:", "right": "---:"}.get(align, "---")


def is_table_separator(raw: str) -> bool:
    """判断一行是否为表格对齐分隔行（所有单元格匹配 :?-{3,}:?）。"""
    cells = split_row(raw)
    if len(cells) < 2:
        return False
    return all(ALIGN_RE.fullmatch(c) is not None for c in cells)
