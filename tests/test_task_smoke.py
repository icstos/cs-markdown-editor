"""任务列表关键链路冒烟测试：解析 / toggle 切换 / format_task 转换。

验证内容：
1. 解析 `- [ ] foo` → Line.task=True, Line.checked=False
2. 解析 `- [x] bar` → Line.task=True, Line.checked=True（大写 X 同样识别）
3. toggle 后 raw 正确重建（`- [ ]` ↔ `- [x]`）往返一致
4. format_task 把普通段落转为任务列表项（`foo` → `- [ ] foo`）
5. 缩进 / 星号 / 加号 标记的任务项正确识别
6. 普通无序列表不误判为任务列表

不依赖 UI 层（flet），仅验证数据层解析与重建逻辑。
"""

from __future__ import annotations

import os
import re
import sys

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser import parse_markdown  # noqa: E402
from models import SegType  # noqa: E402


def _first_task_line(text: str):
    """解析并返回第一个 task 行（解析末尾换行可能产生空行，需过滤）。"""
    doc = parse_markdown(text)
    for line in doc.lines:
        if line.task:
            return line
    raise AssertionError(f"未在文本中找到任务行: {text!r}")


def _first_line(text: str):
    """解析并返回第一个非空行。"""
    doc = parse_markdown(text)
    for line in doc.lines:
        if line.raw.strip():
            return line
    raise AssertionError(f"未在文本中找到非空行: {text!r}")


def _rebuild_prefix(line) -> str:
    """复刻 editor.py toggle_task 的前缀重建逻辑。"""
    prefix_raw = line.segments[0].raw if line.segments else "- "
    body = prefix_raw.lstrip()
    marker_match = re.match(r"^([-*+])\s+", body)
    marker = marker_match.group(1) if marker_match else "-"
    return f"{' ' * (line.level or 0)}{marker} [{'x' if line.checked else ' '}] "


def test_parse_unchecked_task():
    """解析未勾选任务项。"""
    line = _first_task_line("- [ ] 待办事项\n")
    assert line.task is True, f"expected task=True, got {line.task}"
    assert line.checked is False, f"expected checked=False, got {line.checked}"
    assert line.segments[0].seg_type == SegType.LIST_PREFIX
    assert "[ ]" in line.segments[0].raw
    print("PASS test_parse_unchecked_task")


def test_parse_checked_task():
    """解析已勾选任务项。"""
    line = _first_task_line("- [x] 完成事项\n")
    assert line.task is True
    assert line.checked is True
    assert "[x]" in line.segments[0].raw
    print("PASS test_parse_checked_task")


def test_parse_uppercase_x():
    """大写 X 同样识别为已勾选。"""
    line = _first_task_line("- [X] 大写X\n")
    assert line.task is True
    assert line.checked is True
    print("PASS test_parse_uppercase_x")


def test_toggle_task_rebuilds_raw():
    """toggle_task 后 raw 正确重建：未勾选 → 已勾选。"""
    line = _first_task_line("- [ ] foo\n")
    assert line.checked is False
    line.checked = not line.checked
    new_prefix = _rebuild_prefix(line)
    rebuilt = parse_markdown(new_prefix + "foo\n")
    assert rebuilt.lines[0].task is True
    assert rebuilt.lines[0].checked is True
    assert "[x]" in rebuilt.lines[0].raw
    print("PASS test_toggle_task_rebuilds_raw")


def test_toggle_back_to_unchecked():
    """已勾选 → 未勾选的往返一致性。"""
    line = _first_task_line("- [x] bar\n")
    assert line.checked is True
    line.checked = not line.checked
    new_prefix = _rebuild_prefix(line)
    rebuilt = parse_markdown(new_prefix + "bar\n")
    assert rebuilt.lines[0].checked is False
    assert "[ ]" in rebuilt.lines[0].raw
    print("PASS test_toggle_back_to_unchecked")


def test_format_task_conversion():
    """普通段落 → 任务列表项（模拟 set_block 的 task=True 分支）。"""
    line = _first_line("普通文本\n")
    assert line.task is not True
    # 模拟 _inline_content：剥离前缀段拼接内容
    prefix_types = (SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX)
    content = "".join(seg.raw for seg in line.segments if seg.seg_type not in prefix_types)
    new_raw = "- [ ] " + content
    rebuilt = parse_markdown(new_raw + "\n")
    rl = _first_task_line(new_raw + "\n")
    assert rl.task is True
    assert rl.checked is False
    assert rl.raw.rstrip() == "- [ ] 普通文本"
    print("PASS test_format_task_conversion")


def test_indented_task():
    """缩进的任务列表项（嵌套层级）。"""
    line = _first_task_line("  - [ ] 缩进任务\n")
    assert line.task is True
    assert line.level == 2
    print("PASS test_indented_task")


def test_star_marker_task():
    """使用 * 标记的任务列表项也应识别。"""
    line = _first_task_line("* [ ] 星号任务\n")
    assert line.task is True
    assert line.checked is False
    print("PASS test_star_marker_task")


def test_plus_marker_task():
    """使用 + 标记的任务列表项也应识别。"""
    line = _first_task_line("+ [ ] 加号任务\n")
    assert line.task is True
    print("PASS test_plus_marker_task")


def test_non_task_list_not_marked():
    """普通无序列表不应被误判为任务列表。"""
    line = _first_line("- 普通列表项\n")
    assert line.task is not True, "普通列表不应被标记为 task"
    print("PASS test_non_task_list_not_marked")


def test_toggle_preserves_marker_and_indent():
    """toggle 保持原标记符（*/+/-）与缩进层级。"""
    line = _first_task_line("  * [x] 保持标记\n")
    line.checked = not line.checked  # x → 空格
    new_prefix = _rebuild_prefix(line)
    rebuilt = parse_markdown(new_prefix + "保持标记\n")
    rl = _first_task_line(new_prefix + "保持标记\n")
    assert rl.checked is False
    assert rl.level == 2
    assert "[ ]" in rl.raw
    # 标记符仍为 *
    assert "*" in rl.segments[0].raw
    print("PASS test_toggle_preserves_marker_and_indent")


if __name__ == "__main__":
    test_parse_unchecked_task()
    test_parse_checked_task()
    test_parse_uppercase_x()
    test_toggle_task_rebuilds_raw()
    test_toggle_back_to_unchecked()
    test_format_task_conversion()
    test_indented_task()
    test_star_marker_task()
    test_plus_marker_task()
    test_non_task_list_not_marked()
    test_toggle_preserves_marker_and_indent()
    print("\n所有任务列表冒烟测试通过 ✅")
