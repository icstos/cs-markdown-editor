"""文档装载：按文件类型选择解析器（Markdown 行结构 / 纯文本单代码块）。

为什么单列一层：打开文件的入口有三个（Ctrl+O、侧边栏点击、外部修改后重载），
它们必须对同一路径给出**同一份文档形态**——否则「重载后 .py 变回 Markdown 行」
这类缺陷只会在某一条路径上出现。装载规则集中在这里：

- Markdown（.md/.markdown）→ `parser.parse_markdown`（行结构、所见即所得）
- 其他登记过的文本类型（.txt/.py/.cpp/…）→ `parser.parse_plain_text`（整篇一个
  代码块，`Document.plain=True`），并按原文件还原换行符
- 未登记类型（图片 / 压缩包 / 二进制）→ `ValueError`，由调用方决定提示或交系统程序

编码：一律走 `services.file_io.read_text_detailed`（BOM / utf-8 / gb18030 / 兜底），
用户机器上的 GBK 文本文件也能打开；实际使用的编码会记进 `Document.encoding`，
保存时按原编码写回（否则 `# -*- coding: gbk -*-` 的 .py 会被改成 UTF-8 而失效）。
Markdown 仍用严格的 `read_text`，保持既有"UTF-8 文档"契约不变。
"""

from __future__ import annotations

import cs_markdown_editor.parser as parser
from cs_markdown_editor.models.document import Document
from cs_markdown_editor.services.file_io import read_text, read_text_detailed
from cs_markdown_editor.utils import text_formats


class UnsupportedFileType(ValueError):
    """不是可编辑的文本类型（调用方通常转交系统默认程序或提示用户）。"""


def parse_document_text(
    path: str,
    text: str,
    *,
    eol: str = "\n",
    encoding: str = "utf-8",
) -> Document:
    """把**已在内存里的文本**按 `path` 的类型解析成文档。

    与 `load_document` 的分工：那个负责"读盘 + 解析"，这个只负责"解析"，供
    内容来源不是磁盘的行程复用——典型是**对比标签**：两侧内容可能来自尚未保存
    的标签页，不能为了拿文本再去读盘（会把用户的未保存修改比没）。

    为什么必须按类型分派而不是一律 `parse_markdown`：对比视图和普通编辑视图
    必须是**同一种渲染结果**。把 .py 当 Markdown 解析，`# 注释` 会变成一级标题、
    `- item` 变成列表、代码失去语法高亮——同一个文件在两个标签里长得完全不一样，
    差异也无从看起。

    未知类型（`lang_for_path` 返回 None）按 Markdown 解析：对话框理论上已经拦住了
    这类文件，这里只是兜底，不能因为一个意外的扩展名让对比整条链路报错。
    """
    lang = text_formats.lang_for_path(path)
    if lang is not None and not text_formats.is_markdown_path(path):
        doc = parser.parse_plain_text(text, lang)
        doc.eol = eol or "\n"
        doc.encoding = encoding or "utf-8"
        doc.file_path = path
        return doc
    doc = parser.parse_markdown(text)
    doc.file_path = path
    return doc


def load_document(path: str) -> Document:
    """读取并解析 `path`，返回可直接挂到标签页的文档。

    Raises:
        UnsupportedFileType: 扩展名不在文本类型表内。
        OSError / UnicodeDecodeError: 读盘失败（编码已做兜底，实际很少见）。
        ValueError: 二进制文件（含 NUL 字节）。
    """
    lang = text_formats.lang_for_path(path)
    if lang is None:
        raise UnsupportedFileType(f"不支持的文件类型：{path}")
    if text_formats.is_markdown_path(path):
        return parse_document_text(path, read_text(path))
    text, eol, encoding = read_text_detailed(path)
    return parse_document_text(path, text, eol=eol, encoding=encoding)
