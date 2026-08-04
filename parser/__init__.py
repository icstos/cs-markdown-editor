"""Markdown 与文档状态之间的双向转换（parser 包）。

子模块按职责分层（依赖方向为包内单向 DAG）：
- _engine：mistune 实例（惰性）+ 块级正则 + 行内包裹器表（叶子，无包内依赖）
- inline：行内解析（parse_inline）← _engine
- block：块级解析（parse_markdown）← _engine + inline + utils.table_helpers
- reparse：行重解析（reparse_line / reparse_line_atomic / staging_reparse）← _engine + block
- serialize：序列化（serialize / to_html）← _engine
- selection：选区操作（compute/delete/apply/match/extract_outward_text）← reparse + utils.segment_helpers

对外接口（保持 `import parser; parser.xxx` 与 `from parser import xxx` 兼容）：
- parse_markdown(text) -> Document
- reparse_line(line, new_raw=None) / reparse_line_atomic(line, new_raw)
- staging_reparse(line, new_raw) / segment_raw(segments) / line_to_raw(line)
- serialize(doc) / to_html(text)
- compute_markdown_from_selections / match_text_to_selections / compute_markdown_from_text
- apply_inline_format_to_selections / delete_selections
- extract_outward_text(lines, a_li, a_off, b_li, b_off)
- parse_inline(content)

设计要点：
- mistune 实例惰性构造（_engine._get_md / _get_html_md 用 lru_cache），
  import parser 不再触发插件链初始化，to_html 延迟到导出时才加载。
- __init__.py 是唯一导入全部子模块的入口，避免调用方直接导入子模块
  产生循环依赖风险。
"""

from parser.block import parse_markdown
from parser.inline import parse_inline
from parser.reparse import (
    line_to_raw,
    reparse_line,
    reparse_line_atomic,
    segment_raw,
    staging_reparse,
)
from parser.selection import (
    apply_inline_format_to_selections,
    compute_markdown_from_selections,
    compute_markdown_from_text,
    delete_selections,
    extract_outward_text,
    match_text_to_selections,
)
from parser.serialize import serialize, strip_markdown, to_html

__all__ = [
    "apply_inline_format_to_selections",
    "compute_markdown_from_selections",
    "compute_markdown_from_text",
    "delete_selections",
    "extract_outward_text",
    "line_to_raw",
    "match_text_to_selections",
    "parse_inline",
    "parse_markdown",
    "reparse_line",
    "reparse_line_atomic",
    "segment_raw",
    "serialize",
    "staging_reparse",
    "strip_markdown",
    "to_html",
]
