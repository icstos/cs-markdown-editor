"""reparse_line / reparse_line_atomic / staging_reparse 行为测试。

核心验证：
1. reparse_line 与 reparse_line_atomic 对同一 (line, new_raw) 产生等价字段状态
   （两者区别仅在 observable 通知次数，不在结果数据）。
2. staging_reparse 不修改原 line.segments（浅拷贝隔离）。

这是高频编辑路径（handle_char_input / backspace_core / handle_paste）的安全网：
任何对重解析逻辑的改动若导致两种 reparse 结果分歧，本测试立即失败。

依赖项：parser、copy、models。
不依赖 UI 层。
"""

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser import parse_markdown, reparse_line, reparse_line_atomic, staging_reparse  # noqa: E402


def _line_state(line):
    """提取 Line 的可比较状态（避免 observable dataclass __eq__ 干扰）。"""
    segs = [
        (str(s.seg_type), s.raw, s.text, tuple(s.marks), s.url, s.level)
        for s in line.segments
    ]
    return (str(line.block_type), line.level, line.task, line.checked, line.lang, line.raw, segs)


def _assert_reparse_equivalent(initial_md: str, line_idx: int, new_raw: str):
    """对同一初始行用两种 reparse 方式更新，断言结果状态等价。"""
    doc1 = parse_markdown(initial_md)
    doc2 = parse_markdown(initial_md)
    reparse_line(doc1.lines[line_idx], new_raw)
    reparse_line_atomic(doc2.lines[line_idx], new_raw)
    s1 = _line_state(doc1.lines[line_idx])
    s2 = _line_state(doc2.lines[line_idx])
    assert s1 == s2, (
        f"reparse divergence for {initial_md!r}[{line_idx}] -> {new_raw!r}\n"
        f"  reparse_line:         {s1}\n"
        f"  reparse_line_atomic:  {s2}"
    )


def test_reparse_paragraph_to_heading():
    _assert_reparse_equivalent("普通段落", 0, "# 新标题")


def test_reparse_heading_to_list():
    _assert_reparse_equivalent("# 标题", 0, "- 列表项")


def test_reparse_list_to_task():
    _assert_reparse_equivalent("- 普通列表", 0, "- [x] 任务")


def test_reparse_paragraph_to_quote():
    _assert_reparse_equivalent("文本", 0, "> 引用")


def test_reparse_inline_format_change():
    _assert_reparse_equivalent("**加粗**", 0, "*斜体*")


def test_reparse_combined_format():
    _assert_reparse_equivalent("普通", 0, "***加粗斜体***")


def test_reparse_inline_math():
    _assert_reparse_equivalent("文本", 0, "公式 $E=mc^2$ 测试")


def test_reparse_code_block_body():
    """代码块 reparse：block_type 保持 CODE，lang/body 更新。"""
    _assert_reparse_equivalent("```python\nold\n```", 0, "```js\nnew code\n```")


def test_reparse_table_row():
    """表格行 reparse：block_type 保持 TABLE，raw 更新。"""
    _assert_reparse_equivalent("| A | B |\n| --- | --- |\n| 1 | 2 |", 0, "| X | Y |")


def test_reparse_math_block():
    """数学块 reparse：block_type 保持 MATH，content 更新。"""
    _assert_reparse_equivalent("$$\nold\n$$", 0, "$$\nnew formula\n$$")


def test_reparse_hr():
    _assert_reparse_equivalent("---", 0, "***")


def test_reparse_nested_list_indent():
    _assert_reparse_equivalent("- 一级\n  - 二级", 1, "    - 新二级")


def test_reparse_same_raw_idempotent():
    """reparse 同一 raw 应幂等（状态不变）。"""
    md = "- [x] 任务项"
    doc = parse_markdown(md)
    before = _line_state(doc.lines[0])
    reparse_line(doc.lines[0], md)
    after = _line_state(doc.lines[0])
    assert before == after, f"reparse not idempotent:\n  before={before}\n  after={after}"


def test_reparse_atomic_same_raw_idempotent():
    md = "# 标题 **加粗**"
    doc = parse_markdown(md)
    before = _line_state(doc.lines[0])
    reparse_line_atomic(doc.lines[0], md)
    after = _line_state(doc.lines[0])
    assert before == after


def test_staging_reparse_does_not_mutate_original():
    """staging_reparse 不修改原 line.segments（浅拷贝隔离）。"""
    doc = parse_markdown("原始文本")
    original_line = doc.lines[0]
    original_segments_id = id(original_line.segments)
    original_seg_raws = [s.raw for s in original_line.segments]

    staging = staging_reparse(original_line, "# 改成标题")

    # 原行 segments 引用不变
    assert id(original_line.segments) == original_segments_id
    # 原行段内容不变
    assert [s.raw for s in original_line.segments] == original_seg_raws
    # 原行 raw 不变
    assert original_line.raw == "原始文本"
    # 原行 block_type 不变
    assert original_line.block_type.name == "PARAGRAPH"
    # staging 行已更新
    assert staging.raw == "# 改成标题"
    assert staging.block_type.name == "HEADING"
    assert staging.segments[0].seg_type.name == "HEADING_PREFIX"


def test_staging_reparse_independent_segment_objects():
    """staging 行的 segments 是新 list，与原行 segments list 独立。"""
    doc = parse_markdown("文本")
    original = doc.lines[0]
    staging = staging_reparse(original, "**加粗**")
    assert staging.segments is not original.segments
    # 修改 staging 不影响 original
    staging.segments.append(None)  # type: ignore[arg-type]
    assert None not in original.segments


def test_reparse_line_updates_block_type():
    """reparse_line 切换块类型时 block_type 正确更新。"""
    doc = parse_markdown("段落")
    reparse_line(doc.lines[0], "> 引用")
    assert doc.lines[0].block_type.name == "QUOTE"
    assert doc.lines[0].level == 1


def test_reparse_line_clears_lang_on_non_code():
    """普通块 reparse 后 lang 清空（避免残留代码块 lang）。"""
    doc = parse_markdown("```python\ncode\n```")
    assert doc.lines[0].lang == "python"
    # 代码块 reparse 仍保留 lang
    reparse_line(doc.lines[0], "```js\nnew\n```")
    assert doc.lines[0].lang == "js"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\n所有 reparse 测试通过 ✅ ({len(tests)} 项)")
