"""同文件多副本「共享 document」同步行为测试。

拆分左右打开同一文件时两侧共享同一 Document 对象——内容实时互见；
本文件验证配套的状态同步：
- on_dirty_change_pane：一侧编辑变脏 → 共享副本同步变脏
- autosave_all_dirty：共享副本只调度一次保存（不双写盘）
- reload_external：外部重载 → 所有共享副本统一替换新 document + 清脏
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parser

from app._focus_router import build_focus_router
from app.autosave import AutosaveContext, autosave_all_dirty


def _shared_tabs():
    """构造左右两组共享同一 document 的两个标签（均干净）。"""
    doc = parser.parse_markdown("# 共享文档")
    return [
        {"document": doc, "file_path": "D:/notes/a.md", "dirty": False, "group": 0},
        {"document": doc, "file_path": "D:/notes/a.md", "dirty": False, "group": 1},
    ], doc


class TestDirtySync:
    def test_edit_left_marks_both_copies_dirty(self):
        """左组编辑变脏 → 右组共享副本同步变脏（脏标记与内容一致）。"""
        tabs, _ = _shared_tabs()
        ctx = SimpleNamespace(
            is_diff_tab_ref=SimpleNamespace(current=False),
            tabs_ref=SimpleNamespace(current=tabs),
            active_index_left_ref=SimpleNamespace(current=0),
            active_index_right_ref=SimpleNamespace(current=1),
            update_tab=MagicMock(),
            schedule_autosave=MagicMock(),
        )
        cbs = build_focus_router(ctx)
        cbs["on_dirty_change_pane"](0, True)
        # 两个共享副本都更新为脏
        assert ctx.update_tab.call_count == 2
        ctx.update_tab.assert_any_call(0, dirty=True)
        ctx.update_tab.assert_any_call(1, dirty=True)
        ctx.schedule_autosave.assert_called_once()

    def test_independent_docs_not_synced(self):
        """非共享 document 的其他标签不受影响。"""
        doc = parser.parse_markdown("a")
        other = parser.parse_markdown("b")
        tabs = [
            {"document": doc, "file_path": "a.md", "dirty": False, "group": 0},
            {"document": other, "file_path": "b.md", "dirty": False, "group": 1},
        ]
        ctx = SimpleNamespace(
            is_diff_tab_ref=SimpleNamespace(current=False),
            tabs_ref=SimpleNamespace(current=tabs),
            active_index_left_ref=SimpleNamespace(current=0),
            active_index_right_ref=SimpleNamespace(current=1),
            update_tab=MagicMock(),
            schedule_autosave=MagicMock(),
        )
        cbs = build_focus_router(ctx)
        cbs["on_dirty_change_pane"](0, True)
        ctx.update_tab.assert_called_once_with(0, dirty=True)  # 仅编辑侧

    def test_clean_side_reports_false_syncs_too(self):
        """一侧变干净（撤销回原内容）→ 共享副本同步干净。"""
        tabs, _ = _shared_tabs()
        tabs[0]["dirty"] = True
        tabs[1]["dirty"] = True
        ctx = SimpleNamespace(
            is_diff_tab_ref=SimpleNamespace(current=False),
            tabs_ref=SimpleNamespace(current=tabs),
            active_index_left_ref=SimpleNamespace(current=0),
            active_index_right_ref=SimpleNamespace(current=1),
            update_tab=MagicMock(),
            schedule_autosave=MagicMock(),
        )
        cbs = build_focus_router(ctx)
        cbs["on_dirty_change_pane"](0, False)
        ctx.update_tab.assert_any_call(0, dirty=False)
        ctx.update_tab.assert_any_call(1, dirty=False)


class TestAutosaveDedupe:
    def test_shared_copies_saved_once(self):
        """共享 document 的两个脏标签只调度一次自动保存。"""
        tabs, _ = _shared_tabs()
        tabs[0]["dirty"] = True
        tabs[1]["dirty"] = True
        run_task = MagicMock()
        actx = AutosaveContext(
            settings={"auto_save": True},
            page_ref=SimpleNamespace(current=SimpleNamespace(run_task=run_task)),
            tabs_ref=SimpleNamespace(current=tabs),
            save_doc_fn=MagicMock(),
        )
        n = autosave_all_dirty(actx)
        assert n == 1
        assert run_task.call_count == 1

    def test_independent_docs_saved_each(self):
        """不同文件的脏标签各自保存（去重不影响正常多文件）。"""
        tabs = [
            {"document": parser.parse_markdown("a"), "file_path": "a.md",
             "dirty": True, "group": 0},
            {"document": parser.parse_markdown("b"), "file_path": "b.md",
             "dirty": True, "group": 0},
        ]
        run_task = MagicMock()
        actx = AutosaveContext(
            settings={"auto_save": True},
            page_ref=SimpleNamespace(current=SimpleNamespace(run_task=run_task)),
            tabs_ref=SimpleNamespace(current=tabs),
            save_doc_fn=MagicMock(),
        )
        assert autosave_all_dirty(actx) == 2
        assert run_task.call_count == 2


class TestReloadSync:
    def test_reload_replaces_all_shared_copies(self, tmp_path):
        """外部重载：共享旧 doc 的所有副本统一替换 + 清脏 + 各自 bump 会话。"""
        from app._file_dialogs import build_file_dialogs

        target = tmp_path / "a.md"
        target.write_text("# 磁盘最新内容", encoding="utf-8")
        tabs, old_doc = _shared_tabs()
        tabs[0]["dirty"] = True
        tabs[1]["dirty"] = True
        ctx = SimpleNamespace(
            file_dialog={"action": "reload_external", "target": str(target),
                         "target_tab_index": 0},
            set_file_dialog=MagicMock(),
            tabs_ref=SimpleNamespace(current=tabs),
            set_tabs=MagicMock(),
            bump_tab_session=MagicMock(),
            show_snack=MagicMock(),
            set_status_message=MagicMock(),
            open_file_by_path=MagicMock(),
            page_ref=SimpleNamespace(current=None),
        )
        cbs = build_file_dialogs(ctx)
        cbs["on_file_dialog_confirm"]()
        new_tabs = ctx.set_tabs.call_args[0][0]
        # 两个副本都换上新 doc（同一新实例）且清脏
        assert new_tabs[0]["document"] is not old_doc
        assert new_tabs[0]["document"] is new_tabs[1]["document"]
        assert new_tabs[0]["dirty"] is False
        assert new_tabs[1]["dirty"] is False
        assert parser.serialize(new_tabs[1]["document"]) == "# 磁盘最新内容"
        # 左右两个副本各自重建编辑器
        assert ctx.bump_tab_session.call_count == 2


if __name__ == "__main__":
    d = TestDirtySync()
    d.test_edit_left_marks_both_copies_dirty()
    d.test_independent_docs_not_synced()
    d.test_clean_side_reports_false_syncs_too()
    a = TestAutosaveDedupe()
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    a.test_shared_copies_saved_once()
    a.test_independent_docs_saved_each()
    r = TestReloadSync()
    r.test_reload_replaces_all_shared_copies(tmp)
    print("\n所有共享 document 同步测试通过 ✅")
