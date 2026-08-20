"""app._tab_helpers 纯函数单元测试。

覆盖从 App 闭包剥离的无状态标签页助手：
tab_is_dirty / tab_paths / doc_has_text / is_blank_untitled / tab_display_name。
不依赖 UI 层（flet 组件渲染），仅验证纯计算逻辑。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app._tab_helpers import (
    doc_has_text,
    group_indices,
    is_blank_untitled,
    new_tab,
    tab_display_name,
    tab_group,
    tab_is_dirty,
    tab_paths,
)
from models import BlockType, Document, Line


def _make_doc(*raws: str) -> Document:
    """从多行 raw 构造 Document。"""
    return Document(lines=[Line(BlockType.PARAGRAPH, r) for r in raws])


def _editor_tab(**overrides):
    """构造普通编辑器标签（默认空白未命名）。"""
    tab = {"document": _make_doc(""), "file_path": None, "dirty": False}
    tab.update(overrides)
    return tab


def _diff_tab(**overrides):
    """构造 diff 对比标签。"""
    tab = {
        "type": "diff",
        "left_path": None,
        "right_path": None,
        "left_dirty": False,
        "right_dirty": False,
    }
    tab.update(overrides)
    return tab


# ---------------- new_tab / tab_group / group_indices（拆分编辑组）----------------
def test_new_tab_defaults_group_left_and_unique_tid():
    """new_tab 默认 group=0（左组），_tid 进程内唯一递增。"""
    t1 = new_tab(document=None, file_path=None, dirty=False)
    t2 = new_tab(document=None, file_path=None, dirty=False, group=1)
    assert t1["group"] == 0
    assert t2["group"] == 1
    assert isinstance(t1["_tid"], int) and isinstance(t2["_tid"], int)
    assert t1["_tid"] != t2["_tid"]


def test_tab_group_missing_defaults_left():
    """无 group 字段的旧标签视为左组（0）。"""
    assert tab_group({}) == 0
    assert tab_group({"group": None}) == 0


def test_tab_group_returns_field():
    """返回标签 group 字段值。"""
    assert tab_group({"group": 1}) == 1
    assert tab_group({"group": 0}) == 0


def test_group_indices_filters_and_keeps_order():
    """group_indices 返回属于该组的全局索引（保持顺序）。"""
    tabs = [
        {"group": 0},  # 0
        {"group": 1},  # 1
        {},            # 2（缺省 0）
        {"group": 1},  # 3
        {"group": 0},  # 4
    ]
    assert group_indices(tabs, 0) == [0, 2, 4]
    assert group_indices(tabs, 1) == [1, 3]


# ---------------- tab_is_dirty ----------------
def test_tab_is_dirty_editor_clean():
    """普通标签干净：dirty=False → False。"""
    assert tab_is_dirty(_editor_tab(dirty=False)) is False


def test_tab_is_dirty_editor_dirty():
    """普通标签脏：dirty=True → True。"""
    assert tab_is_dirty(_editor_tab(dirty=True)) is True


def test_tab_is_dirty_diff_both_clean():
    """diff 标签两侧干净 → False。"""
    assert tab_is_dirty(_diff_tab(left_dirty=False, right_dirty=False)) is False


def test_tab_is_dirty_diff_left_dirty():
    """diff 标签左侧脏 → True。"""
    assert tab_is_dirty(_diff_tab(left_dirty=True, right_dirty=False)) is True


def test_tab_is_dirty_diff_right_dirty():
    """diff 标签右侧脏 → True。"""
    assert tab_is_dirty(_diff_tab(left_dirty=False, right_dirty=True)) is True


# ---------------- tab_paths ----------------
def test_tab_paths_editor_no_path():
    """普通标签无路径 → 空列表。"""
    assert tab_paths(_editor_tab(file_path=None)) == []


def test_tab_paths_editor_with_path():
    """普通标签有路径 → [file_path]。"""
    assert tab_paths(_editor_tab(file_path="/tmp/test.md")) == ["/tmp/test.md"]


def test_tab_paths_diff_both_paths():
    """diff 标签两侧有路径 → [left, right]。"""
    tab = _diff_tab(left_path="/a.md", right_path="/b.md")
    assert tab_paths(tab) == ["/a.md", "/b.md"]


def test_tab_paths_diff_one_path():
    """diff 标签仅一侧有路径 → 单元素列表。"""
    assert tab_paths(_diff_tab(left_path="/a.md", right_path=None)) == ["/a.md"]
    assert tab_paths(_diff_tab(left_path=None, right_path="/b.md")) == ["/b.md"]


def test_tab_paths_diff_no_paths():
    """diff 标签无路径 → 空列表。"""
    assert tab_paths(_diff_tab(left_path=None, right_path=None)) == []


# ---------------- doc_has_text ----------------
def test_doc_has_text_empty():
    """空文档 → False。"""
    assert doc_has_text(_make_doc("")) is False


def test_doc_has_text_blank_lines():
    """仅空白行 → False。"""
    assert doc_has_text(_make_doc("  ", "\t", "")) is False


def test_doc_has_text_with_content():
    """有内容 → True。"""
    assert doc_has_text(_make_doc("hello")) is True
    assert doc_has_text(_make_doc("", "world")) is True


# ---------------- is_blank_untitled ----------------
def test_is_blank_untitled_true():
    """空白未命名标签 → True。"""
    assert is_blank_untitled(_editor_tab()) is True


def test_is_blank_untitled_with_path():
    """有路径 → False。"""
    assert is_blank_untitled(_editor_tab(file_path="/tmp/test.md")) is False


def test_is_blank_untitled_dirty():
    """脏标签 → False。"""
    assert is_blank_untitled(_editor_tab(dirty=True)) is False


def test_is_blank_untitled_with_text():
    """有内容 → False。"""
    tab = _editor_tab(document=_make_doc("content"))
    assert is_blank_untitled(tab) is False


def test_is_blank_untitled_diff_always_false():
    """diff 标签始终非空白。"""
    assert is_blank_untitled(_diff_tab()) is False


# ---------------- tab_display_name ----------------
def test_tab_display_name_editor_with_path():
    """普通标签有路径 → 文件名。"""
    tab = _editor_tab(file_path="/tmp/test.md")
    assert tab_display_name(tab) == "test.md"


def test_tab_display_name_editor_no_path():
    """普通标签无路径 → 未命名.md。"""
    assert tab_display_name(_editor_tab(file_path=None)) == "未命名.md"


def test_tab_display_name_prefers_display_name():
    """.lnk 快捷方式打开时优先显示链接文件名（file_path 为目标路径）。"""
    tab = _editor_tab(file_path="/docs/Deepseek-cordis.md")
    tab["display_name"] = "Deepseek-cordis.md.lnk"
    assert tab_display_name(tab) == "Deepseek-cordis.md.lnk"


def test_tab_display_name_empty_display_name_falls_back():
    """display_name 为空时回退 file_path 文件名（普通文件不带 display_name）。"""
    tab = _editor_tab(file_path="/docs/note.md")
    tab["display_name"] = ""
    assert tab_display_name(tab) == "note.md"


def test_tab_display_name_diff_both_paths():
    """diff 标签两侧有路径 → left ⟷ right。"""
    tab = _diff_tab(left_path="/a.md", right_path="/b.md")
    assert tab_display_name(tab) == "a.md ⟷ b.md"


def test_tab_display_name_diff_no_paths():
    """diff 标签无路径 → 未命名 ⟷ 未命名。"""
    assert tab_display_name(_diff_tab()) == "未命名 ⟷ 未命名"


def test_tab_display_name_diff_one_path():
    """diff 标签仅一侧有路径。"""
    tab = _diff_tab(left_path="/a.md", right_path=None)
    assert tab_display_name(tab) == "a.md ⟷ 未命名"


if __name__ == "__main__":
    test_tab_is_dirty_editor_clean()
    test_tab_is_dirty_editor_dirty()
    test_tab_is_dirty_diff_both_clean()
    test_tab_is_dirty_diff_left_dirty()
    test_tab_is_dirty_diff_right_dirty()
    test_tab_paths_editor_no_path()
    test_tab_paths_editor_with_path()
    test_tab_paths_diff_both_paths()
    test_tab_paths_diff_one_path()
    test_tab_paths_diff_no_paths()
    test_doc_has_text_empty()
    test_doc_has_text_blank_lines()
    test_doc_has_text_with_content()
    test_is_blank_untitled_true()
    test_is_blank_untitled_with_path()
    test_is_blank_untitled_dirty()
    test_is_blank_untitled_with_text()
    test_is_blank_untitled_diff_always_false()
    test_tab_display_name_editor_with_path()
    test_tab_display_name_editor_no_path()
    test_tab_display_name_diff_both_paths()
    test_tab_display_name_diff_no_paths()
    test_tab_display_name_diff_one_path()
    print("\n所有 _tab_helpers 单元测试通过 ✅")
