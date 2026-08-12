"""HTML 转 Markdown 转换器。

基于标准库 html.parser.HTMLParser，将剪贴板 HTML Format 内容转为 Markdown。

支持转换的 HTML 标签：
- 行内格式：<b>/<strong> → **, <i>/<em> → *, <del>/<s> → ~~, <code> → `
- 链接：<a href="url">text</a> → [text](url)
- 图片：<img src="url" alt="alt"> → ![alt](url)
- 标题：<h1>~<h6> → #~######
- 段落/换行：<p>, <br>, <div>
- 列表：<ul>/<ol>/<li>（支持嵌套）
- 引用：<blockquote> → >
- 代码块：<pre> → ```
- 分割线：<hr> → ---
- 表格：<table>/<tr>/<td>/<th>

依赖项：html.parser（标准库）。
"""

import html
import re
from html.parser import HTMLParser

# 块级元素标签（结束后需换行）
_BLOCK_TAGS = frozenset({
    "p", "div", "section", "article", "header", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "blockquote", "pre", "hr", "table", "tr",
})

# 行内格式标签 → Markdown 包裹符
_INLINE_FORMAT = {
    "b": "**",
    "strong": "**",
    "i": "*",
    "em": "*",
    "del": "~~",
    "s": "~~",
    "strike": "~~",
    "code": "`",
    "mark": "==",
    "u": "",
    "span": "",
    "sub": "",
    "sup": "",
}

# 需要忽略的标签（脚本/样式/头部）
_IGNORE_TAGS = frozenset({
    "script", "style", "head", "meta", "link", "title", "noscript",
})


class HTMLToMarkdown(HTMLParser):
    """HTML 转 Markdown 转换器。

    用法：converter = HTMLToMarkdown(); converter.feed(html_str); md = converter.result()
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []        # 输出缓冲
        self._fmt_stack: list[str] = []   # 行内格式栈（包裹符）
        self._link_stack: list[str] = []  # 链接栈（href）
        self._list_stack: list[dict] = [] # 列表栈 [{type: "ul"|"ol", counter: int}]
        self._in_pre = 0                  # <pre> 嵌套深度（保留原始空白）
        self._in_code = 0                 # <code> 嵌套深度
        self._in_ignore = 0               # 忽略标签嵌套深度
        self._block_pending = False       # 块级元素结束待输出换行
        self._in_table = False
        self._table_rows: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: list[str] = []
        self._in_cell = False
        self._pre_lang = ""               # <pre> 代码块语言标识
        self._pre_written = False         # <pre> ``` 围栏是否已写入

    def result(self) -> str:
        """返回转换后的 Markdown 文本。"""
        text = "".join(self._out)
        # 清理多余空行（3+ 连续换行压为 2 个）
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip() + "\n"

    # ---- 标签处理 ----

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag = tag.lower()
        if tag in _IGNORE_TAGS:
            self._in_ignore += 1
            return
        if self._in_ignore:
            return

        # <pre> 代码块：进入原始模式，延迟写入 ``` 围栏（等 <code class> 取语言）
        if tag == "pre":
            self._flush_block()
            self._in_pre += 1
            self._pre_lang = ""  # 待 <code> starttag 或 handle_data 时确定
            # 尝试从 <pre> 自身 class 读取语言
            for k, v in attrs:
                if k == "class" and v:
                    m = re.search(r"language-(\S+)", v)
                    if m:
                        self._pre_lang = m.group(1)
                    else:
                        m = re.search(
                            r"(python|javascript|js|java|c|cpp|go|rust|html|css|json|yaml|sql|bash|shell)\b",
                            v,
                        )
                        if m:
                            self._pre_lang = m.group(1)
            return

        # <code> 标签
        if tag == "code":
            if self._in_pre > 0:
                # <pre> 内的 <code>：读取 class 语言标识（覆盖 <pre> 的 class）
                if not self._pre_written:
                    for k, v in attrs:
                        if k == "class" and v:
                            m = re.search(r"language-(\S+)", v)
                            if m:
                                self._pre_lang = m.group(1)
                            else:
                                m = re.search(
                                    r"(python|javascript|js|java|c|cpp|go|rust|html|css|json|yaml|sql|bash|shell)\b",
                                    v,
                                )
                                if m:
                                    self._pre_lang = m.group(1)
                    self._write_pre_start()
            else:
                # 行内 code
                self._fmt_stack.append("`")
                self._out.append("`")
                self._in_code += 1
            return

        # 行内格式
        if tag in _INLINE_FORMAT:
            wrapper = _INLINE_FORMAT[tag]
            if wrapper:
                self._fmt_stack.append(wrapper)
                self._out.append(wrapper)
            return

        # 链接：starttag 写 [，endtag 写 ](href)，中间文本由 handle_data 写入
        if tag == "a":
            href = ""
            for k, v in attrs:
                if k == "href" and v:
                    href = v
                    break
            self._link_stack.append(href)
            self._out.append("[")
            return

        # 图片
        if tag == "img":
            src = alt = ""
            for k, v in attrs:
                if k == "src" and v:
                    src = v
                elif k == "alt" and v:
                    alt = v
            if src:
                self._out.append(f"![{alt}]({src})")
            return

        # 标题
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_block()
            level = int(tag[1])
            self._out.append("#" * level + " ")
            return

        # 段落 / div
        if tag in ("p", "div", "section", "article", "header", "footer"):
            self._flush_block()
            return

        # 换行 <br>：Markdown 行内换行（单 \n，块内分行）
        # 清除前面多余空格避免行尾空格干扰
        if tag == "br":
            self._strip_trailing_spaces()
            self._out.append("\n")
            return

        # 分割线
        if tag == "hr":
            self._flush_block()
            self._strip_trailing_spaces()
            self._out.append("\n---\n")
            self._block_pending = True
            return

        # 列表
        if tag in ("ul", "ol"):
            self._list_stack.append({"type": tag, "counter": 0})
            if self._list_stack:  # 非首个列表需要换行
                pass
            return

        if tag == "li":
            self._flush_block()
            if self._list_stack:
                item = self._list_stack[-1]
                indent = "  " * (len(self._list_stack) - 1)
                if item["type"] == "ol":
                    item["counter"] += 1
                    self._out.append(f"\n{indent}{item['counter']}. ")
                else:
                    self._out.append(f"\n{indent}- ")
            return

        # 引用
        if tag == "blockquote":
            self._flush_block()
            self._out.append("\n> ")
            return

        # 表格
        if tag == "table":
            self._flush_block()
            self._in_table = True
            self._table_rows = []
            return

        if tag == "tr":
            self._current_row = []
            return

        if tag in ("td", "th"):
            self._in_cell = True
            self._current_cell = []
            return

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in _IGNORE_TAGS:
            if self._in_ignore:
                self._in_ignore -= 1
            return
        if self._in_ignore:
            return

        # <pre> 代码块结束
        if tag == "pre":
            if self._in_pre > 0:
                self._in_pre -= 1
                # 确保 ``` 开始围栏已写入（空 <pre> 或仅有标签无数据时）
                if not self._pre_written:
                    self._write_pre_start()
                self._out.append("\n```\n")
                self._pre_written = False
                self._pre_lang = ""
                self._block_pending = True
            return

        # <code> 行内代码结束
        if tag == "code":
            if self._in_pre == 0 and self._fmt_stack and self._fmt_stack[-1] == "`":
                wrapper = self._fmt_stack.pop()
                self._out.append(wrapper)
                if self._in_code > 0:
                    self._in_code -= 1
            return

        # 行内格式结束
        if tag in _INLINE_FORMAT:
            wrapper = _INLINE_FORMAT.get(tag, "")
            if wrapper and self._fmt_stack and self._fmt_stack[-1] == wrapper:
                self._fmt_stack.pop()
                self._out.append(wrapper)
            return

        # 链接结束：写 ](href)（[ 和文本已在 starttag/handle_data 写入）
        if tag == "a":
            if self._link_stack:
                href = self._link_stack.pop()
                if href:
                    self._out.append(f"]({href})")
                else:
                    # 无 href 的 <a>：去除已写的 [，仅保留文本
                    if self._out and self._out[-1] == "[":
                        # 空链接：移除 [
                        self._out.pop()
                    elif self._out:
                        # 有文本但无 href：找最后一个 [ 移除
                        for i in range(len(self._out) - 1, -1, -1):
                            if self._out[i] == "[":
                                del self._out[i]
                                break
            return

        # 标题结束
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._out.append("\n")
            self._block_pending = True
            return

        # 段落 / div 结束
        if tag in ("p", "div", "section", "article", "header", "footer"):
            self._block_pending = True
            return

        # 列表项结束：不设 _block_pending（避免项间空行），<li> 开始时已写 \n
        if tag == "li":
            return

        # 列表结束
        if tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            self._block_pending = True
            return

        # 引用结束
        if tag == "blockquote":
            self._out.append("\n")
            self._block_pending = True
            return

        # 表格单元格结束
        if tag in ("td", "th"):
            self._in_cell = False
            cell_text = "".join(self._current_cell).strip().replace("\n", " ")
            self._current_row.append(cell_text)
            self._current_cell = []
            return

        if tag == "tr":
            if self._current_row:
                self._table_rows.append(self._current_row)
            self._current_row = []
            return

        if tag == "table":
            self._in_table = False
            self._render_table()
            self._table_rows = []
            self._block_pending = True
            return

    def handle_data(self, data: str):
        if self._in_ignore:
            return

        # <pre> 内保留原始内容（不处理空白），首次写入前补 ``` 围栏
        if self._in_pre:
            if not self._pre_written:
                self._write_pre_start()
            self._out.append(data)
            return

        # 表格单元格内收集文本
        if self._in_cell:
            # 单元格内换行合并为空格
            self._current_cell.append(re.sub(r"\s+", " ", data))
            return

        # 普通文本：所有空白（含 \n \r \t）合并为单个空格
        # HTML 文本节点中的换行是源码格式化空白，不是显示换行
        # 真正的换行由 <br> / <p> / <div> 等标签触发
        if data:
            text = re.sub(r"\s+", " ", data)
            if text != " " or not self._out:
                # 跳过纯空格且输出已有内容的情况（避免多余空格）
                # 但保留有意义的空格（如句间空格）
                self._out.append(text)

    def _flush_block(self):
        """刷新待输出的块级换行：确保块间有双换行（空行分隔）。

        检查 _out 末尾的换行数，不足 2 个则补齐。
        """
        if not self._block_pending:
            return
        self._block_pending = False
        if not self._out:
            return
        # 合并末尾若干元素检查换行状态（_out 是字符串列表，单看末元素会误判）
        tail = "".join(self._out[-6:])
        # 去除尾部空格/制表符（不影响换行判定）
        tail_stripped = tail.rstrip(" \t")
        if not tail_stripped:
            return
        # 计算末尾连续换行数
        nl_count = 0
        for ch in reversed(tail_stripped):
            if ch == "\n":
                nl_count += 1
            else:
                break
        # 块间需 2 个换行（空行分隔），不足则补齐
        if nl_count < 2:
            # 先清除末尾多余空格
            self._strip_trailing_spaces()
            self._out.append("\n" * (2 - nl_count))

    def _strip_trailing_spaces(self):
        """去除 _out 末尾元素的尾部空格/制表符（不触碰换行符）。"""
        while self._out:
            last = self._out[-1]
            stripped = last.rstrip(" \t")
            if stripped == last:
                return
            if stripped:
                self._out[-1] = stripped
            else:
                self._out.pop()

    def _write_pre_start(self):
        """写入 <pre> 代码块的 ``` 开始围栏（含语言标识）。"""
        lang = self._pre_lang or ""
        self._out.append(f"\n```{lang}\n")
        self._pre_written = True

    def _render_table(self):
        """将收集的表格行渲染为 Markdown 表格。"""
        if not self._table_rows:
            return
        # 第一行作为表头
        header = self._table_rows[0]
        body = self._table_rows[1:]
        col_count = len(header)
        if col_count == 0:
            return
        # 表头行
        self._out.append("\n| " + " | ".join(header) + " |\n")
        # 分隔行
        self._out.append("| " + " | ".join(["---"] * col_count) + " |\n")
        # 数据行
        for row in body:
            # 补齐列数
            while len(row) < col_count:
                row.append("")
            self._out.append("| " + " | ".join(row[:col_count]) + " |\n")


def html_to_markdown(html_str: str) -> str:
    """将 HTML 字符串转换为 Markdown。

    自动处理 HTML 实体（&amp; &lt; &nbsp; 等）。
    """
    if not html_str or not html_str.strip():
        return ""
    converter = HTMLToMarkdown()
    converter.feed(html_str)
    converter.close()
    return converter.result()
