"""app/_split_editor.py 控制器单测。

核心回归：装配槽与 state setter 同名（ctx.set_active_pane 先以原始 setter
构造、后被控制器返回值覆盖）。控制器闭包必须调用构造期捕获的原始 setter，
否则运行时读到 ctx.set_active_pane 是自身 → RecursionError。
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parser

from app._split_editor import build_split_editor


def _make_ctx(active_pane=0, diff_active_pane=0, split_editor=False):
    """构造最小 mock ctx：set_active_pane / set_diff_active_pane 为原始 setter mock。"""
    return SimpleNamespace(
        split_editor=split_editor,
        set_active_pane=MagicMock(),
        set_diff_active_pane=MagicMock(),
        active_pane_ref=SimpleNamespace(current=active_pane),
        diff_active_pane_ref=SimpleNamespace(current=diff_active_pane),
    )


def _assemble(ctx):
    """模拟 __init__.py 装配：控制器返回值覆盖同名 ctx 槽位。"""
    cbs = build_split_editor(ctx)
    ctx.set_active_pane = cbs["set_active_pane"]
    ctx.set_diff_active_pane = cbs["set_diff_active_pane"]
    return cbs


class TestSetActivePaneNoRecursion:
    def test_set_active_pane_after_slot_overwrite_no_recursion(self):
        """装配覆盖后调用 set_active_pane 不递归（原始 setter 只被调一次）。"""
        ctx = _make_ctx(active_pane=0)
        raw_setter = ctx.set_active_pane
        _assemble(ctx)
        ctx.set_active_pane(1)  # 覆盖前此处会 RecursionError
        raw_setter.assert_called_once_with(1)
        assert ctx.active_pane_ref.current == 1

    def test_set_diff_active_pane_after_slot_overwrite_no_recursion(self):
        """装配覆盖后调用 set_diff_active_pane 不递归。"""
        ctx = _make_ctx(diff_active_pane=0)
        raw_setter = ctx.set_diff_active_pane
        _assemble(ctx)
        ctx.set_diff_active_pane(1)
        raw_setter.assert_called_once_with(1)
        assert ctx.diff_active_pane_ref.current == 1

    def test_same_value_noop(self):
        """同值调用不触发 state setter（不重渲染）。"""
        ctx = _make_ctx(active_pane=1)
        raw_setter = ctx.set_active_pane
        _assemble(ctx)
        ctx.set_active_pane(1)
        raw_setter.assert_not_called()


class TestToggleSplit:
    def test_split_on_opens_current_file_copy_in_right_group(self):
        """开启拆分：右组与源共享同一 document 对象（编辑实时同步）。"""
        src_doc = parser.parse_markdown("# 标题\n\n正文内容")
        src_tab = {"document": src_doc, "file_path": "D:/notes/a.md",
                   "dirty": True, "group": 0}
        tabs = [src_tab]
        ctx = _make_ctx(active_pane=0, split_editor=False)
        ctx.is_diff_tab_ref = SimpleNamespace(current=False)
        ctx.tabs_ref = SimpleNamespace(current=tabs)
        ctx.active_index_left_ref = SimpleNamespace(current=0)
        ctx.active_index_right_ref = SimpleNamespace(current=0)
        ctx.append_and_activate = MagicMock()
        ctx.set_split_editor = MagicMock()
        cbs = build_split_editor(ctx)
        cbs["toggle_split_editor"]()
        ctx.set_split_editor.assert_called_once_with(True)
        ctx.append_and_activate.assert_called_once()
        fields = ctx.append_and_activate.call_args[0][0]
        # 同路径副本 + dirty 随源带入 + 右组
        assert fields["group"] == 1
        assert fields["file_path"] == "D:/notes/a.md"
        assert fields["dirty"] is True
        # 共享同一 document 对象 → 任一侧编辑实时同步到另一侧
        assert fields["document"] is src_doc
        # 焦点切到右组
        assert ctx.active_pane_ref.current == 1

    def test_split_on_clean_tab_copy_not_dirty(self):
        """源标签干净时副本 dirty=False。"""
        src_tab = {"document": parser.parse_markdown("x"), "file_path": "a.md",
                   "dirty": False, "group": 0}
        ctx = _make_ctx(active_pane=0, split_editor=False)
        ctx.is_diff_tab_ref = SimpleNamespace(current=False)
        ctx.tabs_ref = SimpleNamespace(current=[src_tab])
        ctx.active_index_left_ref = SimpleNamespace(current=0)
        ctx.active_index_right_ref = SimpleNamespace(current=0)
        ctx.append_and_activate = MagicMock()
        ctx.set_split_editor = MagicMock()
        cbs = build_split_editor(ctx)
        cbs["toggle_split_editor"]()
        fields = ctx.append_and_activate.call_args[0][0]
        assert fields["dirty"] is False
        assert fields["file_path"] == "a.md"

    def test_split_on_blank_untitled_yields_blank_right_tab(self):
        """源为空白未命名标签：副本同为空白未命名（file_path=None，等价体验）。"""
        blank_tab = {"document": parser.parse_markdown(""), "file_path": None,
                     "dirty": False, "group": 0}
        ctx = _make_ctx(active_pane=0, split_editor=False)
        ctx.is_diff_tab_ref = SimpleNamespace(current=False)
        ctx.tabs_ref = SimpleNamespace(current=[blank_tab])
        ctx.active_index_left_ref = SimpleNamespace(current=0)
        ctx.active_index_right_ref = SimpleNamespace(current=0)
        ctx.append_and_activate = MagicMock()
        ctx.set_split_editor = MagicMock()
        cbs = build_split_editor(ctx)
        cbs["toggle_split_editor"]()
        fields = ctx.append_and_activate.call_args[0][0]
        assert fields["file_path"] is None
        assert fields["group"] == 1
        assert parser.serialize(fields["document"]) == ""

    def test_toggle_off_merges_right_tabs_to_left(self):
        """关闭拆分：右组空白丢弃、非空白并入左组，激活按对象身份定位。"""
        left_tab = {"document": parser.parse_markdown("a"), "file_path": "a", "dirty": False, "group": 0}
        right_blank = {"document": parser.parse_markdown(""), "file_path": None, "dirty": False, "group": 1}
        right_kept = {"document": parser.parse_markdown("r"), "file_path": "r", "dirty": True, "group": 1}
        tabs = [left_tab, right_blank, right_kept]
        ctx = _make_ctx(active_pane=1, split_editor=True)
        ctx.is_diff_tab_ref = SimpleNamespace(current=False)
        ctx.update = {}
        ctx.tabs_ref = SimpleNamespace(current=tabs)
        ctx.set_tabs = MagicMock()
        ctx.active_index_left_ref = SimpleNamespace(current=0)
        ctx.active_index_right_ref = SimpleNamespace(current=2)
        ctx.set_active_index_left = MagicMock()
        ctx.set_active_index_right = MagicMock()
        ctx.set_active_index = MagicMock()
        ctx.active_index_ref = SimpleNamespace(current=2)
        ctx.set_split_editor = MagicMock()
        ctx.set_session = MagicMock()
        ctx.session = 3
        cbs = build_split_editor(ctx)
        cbs["toggle_split_editor"]()
        new_tabs = ctx.set_tabs.call_args[0][0]
        # 右组空白被丢弃，非空白并入左组（group→0）
        assert [t["file_path"] for t in new_tabs] == ["a", "r"]
        assert new_tabs[1]["group"] == 0
        assert new_tabs[1]["dirty"] is True  # 保留用户数据
        # 左组激活仍是原对象（索引 0），拆分收起，焦点回左组
        ctx.set_active_index_left.assert_called_once_with(0)
        ctx.set_split_editor.assert_called_once_with(False)
        assert ctx.active_pane_ref.current == 0
        ctx.set_active_index.assert_called_once_with(0)


if __name__ == "__main__":
    t = TestSetActivePaneNoRecursion()
    t.test_set_active_pane_after_slot_overwrite_no_recursion()
    t.test_set_diff_active_pane_after_slot_overwrite_no_recursion()
    t.test_same_value_noop()
    s = TestToggleSplit()
    s.test_split_on_opens_current_file_copy_in_right_group()
    s.test_split_on_clean_tab_copy_not_dirty()
    s.test_split_on_blank_untitled_yields_blank_right_tab()
    s.test_toggle_off_merges_right_tabs_to_left()
    print("\n所有 _split_editor 单元测试通过 ✅")
