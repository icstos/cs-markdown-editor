"""文档序列化与 HTML 导出。

依赖项：
- parser._engine（_get_html_md 惰性实例）
- models（Document）

对外接口：
- serialize(doc: Document) -> str：文档序列化为 Markdown 文本
- to_html(text: str) -> str：Markdown 文本转 HTML（用于导出）

设计要点：
- serialize 直接拼接 line.raw，保证序列化稳定（不依赖 segments 重建）。
- to_html 用惰性 _get_html_md：仅导出时才构造 mistune HTML 实例
  （含 footnotes/task_lists 插件链），启动期不加载。
"""

from models import Document

from parser._engine import _get_html_md


def serialize(doc: Document) -> str:
    """文档序列化为 Markdown 文本。"""
    return "\n".join(line.raw for line in doc.lines)


def to_html(text: str) -> str:
    """Markdown 文本转 HTML（用于导出）。"""
    return _get_html_md()(text)
