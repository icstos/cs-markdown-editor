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


def _make_ctx(active_pane=0, diff_active_pane=0, split_editor=False,
              top_line_left=0, top_line_right=0, nav_left=True, nav_right=True):
    """构造最小 mock ctx：set_active_pane / set_diff_active_pane 为原始 setter mock。

    top_line_* / nav_* 用于「拆分切换继承浏览位置」：`get_top_line` 是滚动锚点的
    唯一来源，nav_left/right=False 表示该视口导航接口尚未就绪（None）。
    """
    return SimpleNamespace(
        split_editor=split_editor,
        set_active_pane=MagicMock(),
        set_diff_active_pane=MagicMock(),
        active_pane_ref=SimpleNamespace(current=active_pane),
        diff_active_pane_ref=SimpleNamespace(current=diff_active_pane),
        nav_ref=SimpleNamespace(current=_Nav(top_line_left) if nav_left else None),
        nav_ref_split=SimpleNamespace(
            current=_Nav(top_line_right) if nav_right else None
        ),
        session_left_ref=SimpleNamespace(current=0),
        session_right_ref=SimpleNamespace(current=0),
        set_split_scroll=MagicMock(),
    )


class _Nav:
    """最小视口导航替身：拆分滚动锚点只用到 get_top_line。"""

    def __init__(self, top_line: int):
        self._top_line = top_line

    def get_top_line(self) -> int:
        return self._top_line


class _NavRaises:
    """get_top_line 抛异常（编辑器已卸载 / 会话销毁）的导航替身。"""

    def get_top_line(self) -> int:
        raise RuntimeError("destroyed session")


def _bump_session_right(ctx):
    """append_and_activate 的真实副作用替身：右组会话 +1。

    真实实现最终调到 `_bump_session(1)`。锚点必须用 **bump 之后** 的会话号
    （新右视口的 key 就是它），但行号必须在 bump 之前读——这条时序约束由
    TestToggleSplit.test_split_on_anchor_uses_new_session_and_old_reading_line 钉住。
    """
    def _append(_fields):
        ctx.session_right_ref.current += 1

    return _append


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
        ctx = _make_ctx(active_pane=0, split_editor=False, top_line_left=42)
        ctx.is_diff_tab_ref = SimpleNamespace(current=False)
        ctx.tabs_ref = SimpleNamespace(current=tabs)
        ctx.active_index_left_ref = SimpleNamespace(current=0)
        ctx.active_index_right_ref = SimpleNamespace(current=0)
        # 真实 append_and_activate 会 bump 右组会话（锚点必须在这之前取）
        ctx.append_and_activate = MagicMock(side_effect=_bump_session_right(ctx))
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
        # 滚动锚点：两侧视口都重建，故左右两组的会话号都要带上同一浏览行
        ctx.set_split_scroll.assert_called_once_with(((0, 42), (1, 42)))

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
        ctx = _make_ctx(active_pane=1, split_editor=True, top_line_left=15)
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
        # 收起拆分同样会让单编辑器重建 → 也继承左视口的浏览位置
        ctx.set_split_scroll.assert_called_once_with(((0, 15), (0, 15)))


class TestSplitScrollAnchor:
    """切换拆分时下发「浏览位置锚点」：让重建的视口停在原浏览行而非文档首行。"""

    def _ctx_for_split_on(self, **kw):
        ctx = _make_ctx(split_editor=False, **kw)
        ctx.is_diff_tab_ref = SimpleNamespace(current=False)
        ctx.tabs_ref = SimpleNamespace(
            current=[{"document": parser.parse_markdown("x"), "file_path": "a.md",
                      "dirty": False, "group": 0}]
        )
        ctx.active_index_left_ref = SimpleNamespace(current=0)
        ctx.active_index_right_ref = SimpleNamespace(current=0)
        ctx.append_and_activate = MagicMock()
        ctx.set_split_editor = MagicMock()
        return ctx

    def test_split_on_anchor_uses_new_session_and_old_reading_line(self):
        """锚点＝(bump 后的新会话号, bump 前读到的行号)：新右视口的 key 才是会话号。"""
        ctx = self._ctx_for_split_on(active_pane=0, top_line_left=42)
        ctx.session_left_ref.current = 7
        ctx.session_right_ref.current = 3
        ctx.append_and_activate = MagicMock(side_effect=_bump_session_right(ctx))
        build_split_editor(ctx)["toggle_split_editor"]()
        assert ctx.session_right_ref.current == 4  # append 已 bump
        ctx.set_split_scroll.assert_called_once_with(((7, 42), (4, 42)))

    def test_split_on_reads_focused_pane_nav(self):
        """源＝用户正在浏览的窗格：焦点在右组时读右组 nav。"""
        ctx = self._ctx_for_split_on(active_pane=1, top_line_left=11, top_line_right=99)
        build_split_editor(ctx)["toggle_split_editor"]()
        assert ctx.set_split_scroll.call_args[0][0] == ((0, 99), (0, 99))

    def test_split_on_nav_not_ready_publishes_none(self):
        """编辑器尚未挂载（nav 为 None）→ 不下发锚点，视口按默认停在首行。"""
        ctx = self._ctx_for_split_on(active_pane=0, nav_left=False)
        build_split_editor(ctx)["toggle_split_editor"]()
        ctx.set_split_scroll.assert_called_once_with(None)

    def test_split_on_nav_raises_publishes_none(self):
        """nav.get_top_line 抛异常（会话已销毁）→ 静默降级为无锚点，不冒泡进按键路径。"""
        ctx = self._ctx_for_split_on(active_pane=0)
        ctx.nav_ref.current = _NavRaises()
        build_split_editor(ctx)["toggle_split_editor"]()
        ctx.set_split_scroll.assert_called_once_with(None)

    def test_split_off_reads_left_nav_even_when_right_focused(self):
        """收起拆分的存活视口是左组激活标签 → 锚点必须取左组 nav 的行号。"""
        ctx = _make_ctx(active_pane=1, split_editor=True,
                        top_line_left=15, top_line_right=99)
        ctx.is_diff_tab_ref = SimpleNamespace(current=False)
        ctx.tabs_ref = SimpleNamespace(
            current=[{"document": parser.parse_markdown("a"), "file_path": "a",
                      "dirty": False, "group": 0}]
        )
        ctx.active_index_left_ref = SimpleNamespace(current=0)
        ctx.active_index_right_ref = SimpleNamespace(current=0)
        ctx.set_tabs = MagicMock()
        ctx.set_active_index_left = MagicMock()
        ctx.set_active_index_right = MagicMock()
        ctx.set_active_index = MagicMock()
        ctx.active_index_ref = SimpleNamespace(current=0)
        ctx.set_split_editor = MagicMock()
        ctx.set_session = MagicMock()
        ctx.session = 1
        build_split_editor(ctx)["toggle_split_editor"]()
        assert ctx.set_split_scroll.call_args[0][0] == ((0, 15), (0, 15))


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
    a = TestSplitScrollAnchor()
    a.test_split_on_anchor_uses_new_session_and_old_reading_line()
    a.test_split_on_reads_focused_pane_nav()
    a.test_split_on_nav_not_ready_publishes_none()
    a.test_split_on_nav_raises_publishes_none()
    a.test_split_off_reads_left_nav_even_when_right_focused()
    print("\n所有 _split_editor 单元测试通过 ✅")
