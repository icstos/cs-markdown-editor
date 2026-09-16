"""Ctrl+Shift+T「恢复历史关闭的文件」测试。

覆盖三层：
1. `app/_tab_helpers` 的栈纯函数：快照规则（对比标签 / 空白未命名不记录、
   有路径剥离 document、未命名草稿保留 document）+ LIFO + 容量上限。
2. `app/_tab_management.do_close_many`：关闭即入栈（全部关闭路径的唯一漏斗）。
3. `app/_file_io_ops.reopen_closed_tab`：出栈恢复（有路径从磁盘重载 / 未命名
   草稿还原 document / 文件已删除时提示并继续 / 栈空提示 / 组内去重）。

page 未就绪时走同步降级路径（_do_sync_load），用真实临时文件验证完整流程。
"""

import os
import shutil
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parser
from app._file_io_ops import build_file_io_ops
from app._tab_helpers import (
    CLOSED_TABS_LIMIT,
    closed_tab_snapshot,
    push_closed_tabs,
)
from app._tab_management import build_tab_management
from services.shortcuts import ACTION_REGISTRY, DEFAULT_SHORTCUTS
from tests.test_tab_management import _make_ctx, _make_tab
from views.key_bindings import _GLOBAL_ACTIONS


@pytest.fixture
def md_dir():
    """工作区内可写临时目录。

    刻意不用 md_dir / tempfile.mkdtemp：受限沙箱下 mkdtemp 创建的目录带受限
    DACL，随后写入文件会 PermissionError（环境问题，非代码缺陷）。os.makedirs
    创建的目录可正常读写，因此这里自建并在用例结束后清理。
    """
    d = Path(__file__).resolve().parent / ".tmp-reopen" / uuid.uuid4().hex[:8]
    d.mkdir(parents=True)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _path_tab(path: str, *, display_name: str | None = None, dirty: bool = False) -> dict:
    tab = {
        "file_path": path,
        "dirty": dirty,
        "document": parser.parse_markdown("# x"),
        "group": 0,
        "_tid": 1,
    }
    if display_name is not None:
        tab["display_name"] = display_name
    return tab


def _untitled_tab(text: str = "# draft", *, dirty: bool = True) -> dict:
    return {
        "file_path": None,
        "dirty": dirty,
        "document": parser.parse_markdown(text),
        "group": 0,
        "_tid": 2,
    }


def _file_ctx(closed=None, existing_tabs=None, *, split_editor=False, active_pane=0,
              active_left=0, active_right=0) -> SimpleNamespace:
    """构造 build_file_io_ops 的最小 mock ctx（reopen_closed_tab 所需字段）。"""
    return SimpleNamespace(
        split_editor=split_editor,
        active_pane_ref=SimpleNamespace(current=active_pane),
        tabs_ref=SimpleNamespace(current=list(existing_tabs or [])),
        active_index_left_ref=SimpleNamespace(current=active_left),
        active_index_right_ref=SimpleNamespace(current=active_right),
        # 直接持有传入的列表对象（与真实 ref 一致）：用例可据同一列表观察出栈
        closed_tabs_ref=SimpleNamespace(current=closed if closed is not None else []),
        page_ref=SimpleNamespace(current=None),  # page 未就绪 → _do_sync_load
        pending_jump_ref=SimpleNamespace(current=None),
        pending_jump_sig=0,
        set_pending_jump_sig=MagicMock(),
        activate_index=MagicMock(),
        append_and_activate=MagicMock(),
        update_tab=MagicMock(),
        bump_tab_session=MagicMock(),
        push_recent_file=MagicMock(),
        show_snack=MagicMock(),
        open_external=MagicMock(),
        update_setting=MagicMock(),
        settings={},
    )


# ============ 栈纯函数（app/_tab_helpers.py）============

class TestClosedTabStack:
    def test_path_snapshot_strips_document(self):
        """有路径的快照剥离 document（重开从磁盘加载，不长期占用内存）。"""
        tab = _path_tab("C:/a.md")
        snap = closed_tab_snapshot(tab)
        assert snap is not tab
        assert snap["file_path"] == "C:/a.md"
        assert "document" not in snap
        assert "document" in tab  # 原标签不被修改

    def test_untitled_snapshot_keeps_document(self):
        """未命名草稿保留 document 引用（内容不在磁盘上，只能靠快照还原）。"""
        tab = _untitled_tab()
        snap = closed_tab_snapshot(tab)
        assert snap["document"] is tab["document"]
        assert snap["dirty"] is True

    def test_blank_untitled_not_recorded(self):
        blank = {"file_path": None, "dirty": False,
                 "document": parser.parse_markdown(""), "group": 0}
        assert closed_tab_snapshot(blank) is None

    def test_diff_tab_not_recorded(self):
        diff = {"type": "diff", "left_path": "a", "right_path": "b",
                "left_doc": None, "right_doc": None, "group": 0}
        assert closed_tab_snapshot(diff) is None

    def test_stack_is_lifo_with_capacity_cap(self):
        """超过上限时丢弃最旧快照；栈尾为最近关闭（LIFO 弹出顺序）。"""
        stack: list = []
        tabs = [_path_tab(f"C:/f{i}.md") for i in range(CLOSED_TABS_LIMIT + 5)]
        added = push_closed_tabs(stack, tabs)
        assert added == CLOSED_TABS_LIMIT + 5
        assert len(stack) == CLOSED_TABS_LIMIT
        assert stack[0]["file_path"] == "C:/f5.md"
        assert stack[-1]["file_path"] == f"C:/f{CLOSED_TABS_LIMIT + 4}.md"


# ============ 关闭即入栈（app/_tab_management.py）============

class TestCloseRecords:
    def test_do_close_many_pushes_snapshot(self):
        ctx = _make_ctx([_path_tab("C:/a.md"), _make_tab("b")], active_index=0)
        cbs = build_tab_management(ctx)
        cbs["do_close_many"]([0])
        stack = ctx.closed_tabs_ref.current
        assert [s["file_path"] for s in stack] == ["C:/a.md"]
        assert "document" not in stack[0]

    def test_do_close_many_skips_blank_and_diff(self):
        blank = {"file_path": None, "dirty": False,
                 "document": parser.parse_markdown(""), "group": 0, "_tid": 1}
        diff = {"type": "diff", "left_path": "a", "right_path": "b",
                "left_doc": None, "right_doc": None, "group": 0, "_tid": 2}
        ctx = _make_ctx([blank, diff], active_index=0)
        cbs = build_tab_management(ctx)
        cbs["do_close_many"]([0, 1])
        assert ctx.closed_tabs_ref.current == []

    def test_confirm_flow_records_on_close_without_save(self):
        """确认弹层「不保存」关闭同样入栈（do_close_many 是唯一漏斗）。"""
        ctx = _make_ctx([_path_tab("C:/a.md", dirty=True)], active_index=0,
                        confirm_close=[0])
        ctx.tabs_ref.current = ctx.tabs
        cbs = build_tab_management(ctx)
        cbs["close_without_save"]()
        assert len(ctx.closed_tabs_ref.current) == 1


# ============ 出栈恢复（app/_file_io_ops.py）============

class TestReopen:
    def test_path_tab_reloads_from_disk(self, md_dir):
        f = md_dir / "a.md"
        f.write_text("# 标题", encoding="utf-8")
        stack = [closed_tab_snapshot(_path_tab(str(f)))]
        ctx = _file_ctx(closed=stack)
        cbs = build_file_io_ops(ctx)
        cbs["reopen_closed_tab"]()
        fields = ctx.append_and_activate.call_args[0][0]
        assert fields["file_path"] == str(f)
        assert "# 标题" in parser.serialize(fields["document"])
        # 重开同样写入最近文件（push_recent_file 内部经 ctx.update_setting 持久化）
        assert ctx.update_setting.call_args[0][0] == "recent_files"
        assert str(f) in ctx.update_setting.call_args[0][1]
        assert stack == []

    def test_lnk_display_name_preserved(self, md_dir):
        f = md_dir / "note.md"
        f.write_text("# n", encoding="utf-8")
        stack = [closed_tab_snapshot(_path_tab(str(f), display_name="note.md.lnk"))]
        ctx = _file_ctx(closed=stack)
        cbs = build_file_io_ops(ctx)
        cbs["reopen_closed_tab"]()
        fields = ctx.append_and_activate.call_args[0][0]
        assert fields["display_name"] == "note.md.lnk"

    def test_untitled_draft_restored_into_focused_group(self):
        """未命名草稿：document 原样还原，落到焦点侧组，旧 _tid/group 丢弃。"""
        tab = _untitled_tab()
        stack = [closed_tab_snapshot(tab)]
        ctx = _file_ctx(closed=stack, split_editor=True, active_pane=1)
        cbs = build_file_io_ops(ctx)
        cbs["reopen_closed_tab"]()
        fields = ctx.append_and_activate.call_args[0][0]
        assert fields["document"] is tab["document"]
        assert fields["dirty"] is True
        assert fields["group"] == 1
        assert "_tid" not in fields

    def test_empty_stack_shows_snack(self):
        ctx = _file_ctx(closed=[])
        cbs = build_file_io_ops(ctx)
        cbs["reopen_closed_tab"]()
        ctx.append_and_activate.assert_not_called()
        assert "没有可重新打开" in ctx.show_snack.call_args[0][0]

    def test_missing_file_skips_to_next(self, md_dir):
        """失效项不挡路：提示后继续弹下一个快照。"""
        f = md_dir / "b.md"
        f.write_text("# b", encoding="utf-8")
        # LIFO：栈尾是最近关闭的，失效项放在栈尾才会被先弹出
        stack = [
            closed_tab_snapshot(_path_tab(str(f))),
            closed_tab_snapshot(_path_tab(str(md_dir / "gone.md"))),
        ]
        ctx = _file_ctx(closed=stack)
        cbs = build_file_io_ops(ctx)
        cbs["reopen_closed_tab"]()
        assert "已被移动或删除" in ctx.show_snack.call_args[0][0]
        fields = ctx.append_and_activate.call_args[0][0]
        assert fields["file_path"] == str(f)
        assert stack == []

    def test_all_missing_ends_with_empty_hint(self, md_dir):
        stack = [
            closed_tab_snapshot(_path_tab(str(md_dir / f"g{i}.md")))
            for i in range(2)
        ]
        ctx = _file_ctx(closed=stack)
        cbs = build_file_io_ops(ctx)
        cbs["reopen_closed_tab"]()
        assert ctx.show_snack.call_count == 3  # 2 个失效项 + 1 个栈空提示
        assert "没有可重新打开" in ctx.show_snack.call_args[0][0]
        ctx.append_and_activate.assert_not_called()

    def test_existing_group_tab_activated_not_duplicated(self, md_dir):
        """同组已打开该文件 → 只激活（组内去重），不新增副本。"""
        f = md_dir / "a.md"
        f.write_text("# a", encoding="utf-8")
        existing = {"file_path": str(f), "dirty": False,
                    "document": parser.parse_markdown("# a"), "group": 0, "_tid": 9}
        stack = [closed_tab_snapshot(_path_tab(str(f)))]
        ctx = _file_ctx(closed=stack, existing_tabs=[existing], active_left=0)
        cbs = build_file_io_ops(ctx)
        cbs["reopen_closed_tab"]()
        ctx.activate_index.assert_called_once_with(0)
        ctx.append_and_activate.assert_not_called()
        assert stack == []

    def test_close_then_reopen_roundtrip(self, md_dir):
        """关闭 → 入栈 → 出栈重开的端到端往返（跨两个控制器）。"""
        f = md_dir / "a.md"
        f.write_text("# 往返", encoding="utf-8")
        ctx = _make_ctx([_path_tab(str(f)), _make_tab("b")], active_index=0)
        build_tab_management(ctx)["do_close_many"]([0])
        stack = ctx.closed_tabs_ref.current
        assert len(stack) == 1

        fctx = _file_ctx(closed=stack)
        build_file_io_ops(fctx)["reopen_closed_tab"]()
        fields = fctx.append_and_activate.call_args[0][0]
        assert fields["file_path"] == str(f)
        assert stack == []


# ============ 快捷键注册 ============

def test_default_shortcut_and_global_action_registered():
    assert DEFAULT_SHORTCUTS["browse"]["reopen_closed_tab"] == "ctrl+shift+t"
    assert any(a.id == "reopen_closed_tab" for a in ACTION_REGISTRY)
    assert ("reopen_closed_tab", "ctrl+shift+t", "cb") in _GLOBAL_ACTIONS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
