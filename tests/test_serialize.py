"""SAMPLE_MD 黄金快照往返测试 + to_html 冒烟。

锁死示例文档的解析/序列化行为，防止重构期行为漂移。
同时验证 to_html（导出 HTML 链路）不报错。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.sample import SAMPLE_MD  # noqa: E402
from parser import parse_markdown, serialize, to_html  # noqa: E402


def test_sample_md_roundtrip():
    """SAMPLE_MD 解析后序列化应还原原文（含代码块/表格/数学块/嵌套列表/引用）。"""
    out = serialize(parse_markdown(SAMPLE_MD))
    assert out == SAMPLE_MD, (
        "SAMPLE_MD 往返不一致（重构可能改变了解析/序列化行为）\n"
        f"--- 期望长度 {len(SAMPLE_MD)}，实际长度 {len(out)} ---\n"
        f"--- 期望 ---\n{SAMPLE_MD!r}\n"
        f"--- 实际 ---\n{out!r}"
    )


def test_sample_md_has_diverse_blocks():
    """SAMPLE_MD 解析后应包含多种块类型（确保示例覆盖全面）。"""
    doc = parse_markdown(SAMPLE_MD)
    block_types = {line.block_type.name for line in doc.lines}
    expected = {"HEADING", "PARAGRAPH", "LIST_UO", "LIST_O", "QUOTE", "CODE", "TABLE", "HR", "MATH", "TOC"}
    missing = expected - block_types
    assert not missing, f"SAMPLE_MD 缺少块类型: {missing}（实际: {block_types}）"


def test_sample_md_has_task_items():
    """SAMPLE_MD 含任务列表项（- [x] / - [ ]）。"""
    doc = parse_markdown(SAMPLE_MD)
    task_lines = [l for l in doc.lines if l.task]
    assert len(task_lines) >= 2, f"任务项不足: {len(task_lines)}"
    checked = [l for l in task_lines if l.checked]
    unchecked = [l for l in task_lines if not l.checked]
    assert checked, "无已勾选任务项"
    assert unchecked, "无未勾选任务项"


def test_to_html_smoke():
    """to_html 对 SAMPLE_MD 不报错且返回非空 HTML。"""
    html = to_html(SAMPLE_MD)
    assert isinstance(html, str)
    assert len(html) > 0
    assert "<" in html, f"to_html 返回非 HTML: {html[:100]!r}"


def test_to_html_simple_markdown():
    """to_html 对简单 Markdown 生成正确标签。"""
    html = to_html("# 标题\n\n**加粗**")
    assert "<h1" in html.lower(), f"缺少 h1: {html!r}"
    assert "<strong" in html.lower() or "<b" in html.lower(), f"缺少 strong: {html!r}"


def test_serialize_empty_doc():
    """空文档序列化为空串。"""
    assert serialize(parse_markdown("")) == ""


def test_serialize_single_line():
    assert serialize(parse_markdown("单行")) == "单行"


def test_serialize_preserves_code_block_structure():
    """代码块序列化保留围栏与语言。"""
    md = "```python\ncode\n```"
    assert serialize(parse_markdown(md)) == md


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\n所有序列化快照测试通过 ✅ ({len(tests)} 项)")
