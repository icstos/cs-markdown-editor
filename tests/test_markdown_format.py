"""Markdown 文档格式化器测试（Shift+Alt+F 格式化规则）。

覆盖 5 条规则：
1. 清理行尾多余空格；文件末尾统一保留一个换行
2. 行内代码统一反引号包裹、内容无多余空格（含反引号时升级分隔符）
3. 任务列表 `- [ ]` / `- [x]` 规范统一（含引用行内任务列表）
4. 引用 `>` 后加空格、嵌套层级合并
5. 中英文混排加半角空格（不破坏行内代码/围栏块）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.markdown_format import format_markdown


def fmt(text: str) -> str:
    """单行场景便捷断言：忽略规则 1 的末尾换行。"""
    return format_markdown(text).rstrip("\n")


# ---------------- 规则 1：行尾空格 + 末尾换行 ----------------

def test_trailing_whitespace_cleaned():
    """行尾多余空格被清理。"""
    assert format_markdown("text  \nnext\t\n") == "text\nnext\n"


def test_end_single_newline():
    """文件末尾统一保留一个换行。"""
    assert format_markdown("line1\nline2") == "line1\nline2\n"
    assert format_markdown("line1\n\n\n") == "line1\n"


def test_empty_document():
    """空文档/纯空白文档 → 空字符串。"""
    assert format_markdown("") == ""
    assert format_markdown("   \n  \n") == ""


def test_blank_lines_preserved_between_paragraphs():
    """段落间空行保留（仅清理行尾与末尾多余空行）。"""
    assert format_markdown("a  \n\nb  \n") == "a\n\nb\n"


def test_code_fence_inner_trailing_spaces_kept():
    """代码块内部行尾空格保留（不破坏代码）。"""
    text = "```python\nx = 1  \ny = 2\n```\n"
    assert format_markdown(text) == text


# ---------------- 规则 2：行内代码 ----------------

def test_inline_code_whitespace_removed():
    """反引号与内容间多余空格被清理。"""
    assert fmt("使用 ` code ` 示例") == "使用 `code` 示例"
    assert fmt("`  spaced  `") == "`spaced`"


def test_inline_code_with_backtick_upgrades_fence():
    """内容首/尾含反引号：升级分隔符并保留围栏与内容间空格（合法包裹）。"""
    assert fmt("`` `code` ``") == "`` `code` ``"
    assert fmt("` ``x`` `") == "``` ``x`` ```"


def test_inline_code_normal_unaffected():
    """已规范的行内代码保持不变。"""
    assert fmt("`ok` 和 `x_y`") == "`ok` 和 `x_y`"


def test_code_block_fence_not_treated_as_inline():
    """代码块围栏与内部内容不受行内代码规则影响。"""
    text = "```\n`raw`  \n```\n"
    assert format_markdown(text) == text


# ---------------- 规则 3：任务列表 ----------------

def test_task_marker_normalized():
    """任务标记统一：X → x，空格规范。"""
    assert fmt("- [X] 完成") == "- [x] 完成"
    assert fmt("- [x]完成") == "- [x] 完成"
    assert fmt("- [] 空") == "- [ ] 空"
    assert fmt("* [x] star") == "* [x] star"
    assert fmt("+ [ ] plus") == "+ [ ] plus"


def test_task_in_quote_normalized():
    """引用行内的任务列表同样规范化。"""
    assert fmt("> - [x]todo") == "> - [x] todo"


# ---------------- 规则 4：引用 ----------------

def test_quote_space_after_marker():
    """`>` 后统一加空格。"""
    assert fmt(">text") == "> text"
    assert fmt(">>nested") == ">> nested"
    assert fmt(">  double") == "> double"


def test_quote_nested_merged():
    """嵌套引用 `> >` 合并为连续前缀，层级对应。"""
    assert fmt("> > deep") == ">> deep"
    assert fmt("> > > x") == ">>> x"


def test_quote_empty_line():
    """空引用行 `>` 保留单个前缀。"""
    assert fmt(">") == ">"


def test_quote_with_task_and_code():
    """引用行内混排任务/代码均被处理。"""
    assert fmt("> - [X] 用 ` code `") == "> - [x] 用 `code`"


# ---------------- 规则 5：中英文混排 ----------------

def test_cjk_ascii_spacing():
    """中文与英文/数字之间加半角空格。"""
    assert fmt("使用Python编程") == "使用 Python 编程"
    assert fmt("共5个文件") == "共 5 个文件"
    assert fmt("Python和中文") == "Python 和中文"
    assert fmt("版本v2.0发布") == "版本 v2.0 发布"


def test_cjk_spacing_no_duplicate():
    """已有空格不重复添加。"""
    assert fmt("使用 Python 编程") == "使用 Python 编程"


def test_cjk_spacing_skips_inline_code():
    """行内代码内容不被加空格，代码与中文之间加空格。"""
    assert fmt("运行`print(1)`命令") == "运行 `print(1)` 命令"
    assert fmt("`print(1)`") == "`print(1)`"


def test_cjk_spacing_skips_code_fence():
    """代码块内部不被加空格。"""
    text = "```python\nx = '中文abc'\n```\n"
    assert format_markdown(text) == text


def test_cjk_spacing_punctuation_untouched():
    """中文与标点之间不加空格；英文标点与中文相邻不加。"""
    assert fmt("你好，世界！") == "你好，世界！"
    assert fmt("中文(English)") == "中文(English)"


def test_cjk_spacing_markdown_syntax_preserved():
    """Markdown 语法标记不被破坏（标题/加粗/链接）。"""
    assert fmt("# 标题Hello") == "# 标题 Hello"
    assert fmt("**加粗text**") == "**加粗 text**"
    assert fmt("[链接text](url)") == "[链接 text](url)"


# ---------------- 组合场景 ----------------

def test_full_document_format():
    """综合场景：多种规则同时生效。"""
    text = (
        "# 标题\n\n"
        "> 引用text  \n"
        "- [X]完成任务  \n"
        "使用` code `与Python混排\n\n"
    )
    expected = (
        "# 标题\n\n"
        "> 引用 text\n"
        "- [x] 完成任务\n"
        "使用 `code` 与 Python 混排\n"
    )
    assert format_markdown(text) == expected


def test_frontmatter_preserved():
    """frontmatter 围栏内容原样保留。"""
    text = "---\ntitle: 测试A\n---\n\n正文text\n"
    assert format_markdown(text) == "---\ntitle: 测试A\n---\n\n正文 text\n"


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
