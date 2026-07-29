"""parse_markdown → serialize 往返一致性测试。

验证「解析后序列化」能还原原始 Markdown 文本（含各种块类型与行内格式）。
这是后续重构（parser 拆包、纯函数提取、控制器封装）的核心回归安全网：
任何对解析/序列化逻辑的改动，若导致往返结果变化，本测试立即失败。

依赖项：parser（parse_markdown / serialize）、models（BlockType）。
不依赖 UI 层（flet）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser import parse_markdown, serialize  # noqa: E402


def _roundtrip(md: str) -> str:
    """解析再序列化，返回结果文本。"""
    return serialize(parse_markdown(md))


def test_paragraph_roundtrip():
    md = "普通段落文本"
    assert _roundtrip(md) == md


def test_heading_roundtrip():
    for md in ["# 一级", "## 二级", "### 三级", "#### 四级", "##### 五级", "###### 六级"]:
        assert _roundtrip(md) == md, f"heading roundtrip failed: {md!r}"


def test_unordered_list_roundtrip():
    md = "- 项目一\n- 项目二\n- 项目三"
    assert _roundtrip(md) == md


def test_ordered_list_roundtrip():
    md = "1. 第一步\n2. 第二步\n3. 第三步"
    assert _roundtrip(md) == md


def test_nested_list_roundtrip():
    md = "- 一级\n  - 二级\n    - 三级\n  - 二级b"
    assert _roundtrip(md) == md


def test_task_list_roundtrip():
    md = "- [x] 已完成\n- [ ] 待办"
    assert _roundtrip(md) == md


def test_quote_roundtrip():
    md = "> 引用一行\n> 引用二行"
    assert _roundtrip(md) == md


def test_nested_quote_roundtrip():
    md = "> 外层\n> > 内层"
    assert _roundtrip(md) == md


def test_hr_roundtrip():
    for md in ["---", "***", "___"]:
        assert _roundtrip(md) == md, f"hr roundtrip failed: {md!r}"


def test_toc_roundtrip():
    md = "[toc]"
    assert _roundtrip(md) == md


def test_inline_math_roundtrip():
    md = "行内公式 $E=mc^2$ 测试"
    assert _roundtrip(md) == md


def test_inline_formats_roundtrip():
    cases = [
        "**加粗**",
        "*斜体*",
        "~~删除线~~",
        "==高亮==",
        "`行内代码`",
        "[链接](https://flet.dev)",
        "![图片](assets/x.png)",
    ]
    for md in cases:
        assert _roundtrip(md) == md, f"inline roundtrip failed: {md!r}"


def test_combined_format_roundtrip():
    """组合格式 ***加粗斜体*** 往返。"""
    md = "***加粗斜体***"
    assert _roundtrip(md) == md


def test_nested_inline_format_roundtrip():
    """嵌套行内格式 *斜==体==* 往返。"""
    md = "*斜==体==*"
    assert _roundtrip(md) == md


def test_code_block_with_lang_roundtrip():
    md = "```python\nimport os\n\ndef greet():\n    pass\n```"
    assert _roundtrip(md) == md


def test_code_block_without_lang_roundtrip():
    md = "```\n纯文本代码\n第二行\n```"
    assert _roundtrip(md) == md


def test_math_block_fence_roundtrip():
    md = "$$\nx = \\dfrac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}\n$$"
    assert _roundtrip(md) == md


def test_math_block_inline_roundtrip():
    """单行 $$...$$ 公式块往返。"""
    md = "$$E=mc^2$$"
    assert _roundtrip(md) == md


def test_table_roundtrip():
    md = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    assert _roundtrip(md) == md


def test_table_with_alignment_roundtrip():
    md = "| A | B | C |\n| :--- | :---: | ---: |\n| 左 | 中 | 右 |"
    assert _roundtrip(md) == md


def test_blank_line_roundtrip():
    md = "第一段\n\n第二段"
    assert _roundtrip(md) == md


def test_trailing_newline_roundtrip():
    md = "内容\n"
    assert _roundtrip(md) == md


def test_mixed_document_roundtrip():
    """混合文档往返（标题/列表/代码块/表格/引用）。"""
    md = (
        "# 标题\n"
        "\n"
        "段落文本 **加粗**。\n"
        "\n"
        "- 列表项\n"
        "  - 嵌套\n"
        "\n"
        "> 引用\n"
        "\n"
        "```python\ncode\n```\n"
        "\n"
        "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    )
    assert _roundtrip(md) == md


def test_block_type_correct():
    """解析后块类型正确赋值。"""
    doc = parse_markdown("# 标题\n- 列表\n> 引用\n---")
    assert doc.lines[0].block_type.name == "HEADING"
    assert doc.lines[1].block_type.name == "LIST_UO"
    assert doc.lines[2].block_type.name == "QUOTE"
    assert doc.lines[3].block_type.name == "HR"


def test_empty_document():
    """空文档至少有一个空行（parse_markdown 保证）。"""
    doc = parse_markdown("")
    assert len(doc.lines) >= 1
    assert serialize(doc) == ""


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\n所有往返测试通过 ✅ ({len(tests)} 项)")
