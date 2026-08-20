"""拆分组「同文件多副本」打开语义测试（open_file_by_path 组内去重）。

左右标签完全独立：同一文件可在左右两组各开一份副本（独立 document /
光标 / 撤销 / 脏状态）；同组内重复打开仍激活已有标签（组内去重）。

走同步降级路径（page 未就绪 → _do_sync_load），用真实临时文件验证完整流程。
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app._file_io_ops
import parser
from app._file_io_ops import build_file_io_ops


def _make_ctx(tabs, split_editor=False, active_pane=0, active_left=0, active_right=0):
    """构造最小 mock ctx（open_file_by_path + _do_sync_load 所需字段）。"""
    return SimpleNamespace(
        split_editor=split_editor,
        active_pane_ref=SimpleNamespace(current=active_pane),
        tabs_ref=SimpleNamespace(current=list(tabs)),
        active_index_left_ref=SimpleNamespace(current=active_left),
        active_index_right_ref=SimpleNamespace(current=active_right),
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


def _tab(path, group=0):
    return {"file_path": path, "dirty": False,
            "document": parser.parse_markdown(""), "group": group}


class TestGroupOpenDedupe:
    def test_same_group_reopen_activates_existing(self, tmp_path):
        """同组重复打开 → 激活已有标签，不新增副本。"""
        f = tmp_path / "a.md"
        f.write_text("# a", encoding="utf-8")
        ctx = _make_ctx([_tab(str(f), group=0)])
        cbs = build_file_io_ops(ctx)
        cbs["open_file_by_path"](str(f))
        ctx.activate_index.assert_called_once_with(0)
        ctx.append_and_activate.assert_not_called()

    def test_other_group_open_creates_independent_copy(self, tmp_path):
        """左组已开 a.md，焦点在右组再开 → 右组副本共享同一 document（实时同步）。"""
        f = tmp_path / "a.md"
        f.write_text("# a", encoding="utf-8")
        g = tmp_path / "x.md"
        g.write_text("# x", encoding="utf-8")
        left_doc = parser.parse_markdown("# a\n\n左组未保存修改")
        # tabs：左组已有 a.md（索引 0，脏），右组已开 x.md（索引 1，激活）
        ctx = _make_ctx(
            [_tab(str(f), group=0) | {"document": left_doc, "dirty": True},
             _tab(str(g), group=1)],
            split_editor=True, active_pane=1, active_left=0, active_right=1)
        cbs = build_file_io_ops(ctx)
        cbs["open_file_by_path"](str(f))
        # 左组标签（索引 0）未被激活——两侧标签独立
        ctx.activate_index.assert_not_called()
        # 右组新增 a.md 副本：与左组共享 document（含未保存修改）+ dirty 跟随
        ctx.append_and_activate.assert_called_once()
        fields = ctx.append_and_activate.call_args[0][0]
        assert fields["group"] == 1
        assert fields["file_path"] == str(f)
        assert fields["document"] is left_doc  # 共享对象 → 编辑实时同步
        assert fields["dirty"] is True  # 源脏 → 副本同步脏

    def test_focused_group_existing_tab_activated_not_duplicated(self, tmp_path):
        """焦点组已有该文件 → 激活该组标签（组内去重，另一组副本无关）。"""
        f = tmp_path / "a.md"
        f.write_text("# a", encoding="utf-8")
        # 左组副本（索引 0）、右组副本（索引 1，激活中）
        ctx = _make_ctx([_tab(str(f), group=0), _tab(str(f), group=1)],
                        split_editor=True, active_pane=1,
                        active_left=0, active_right=1)
        cbs = build_file_io_ops(ctx)
        cbs["open_file_by_path"](str(f))
        # 激活右组已有标签（索引 1），不新增第三份副本
        ctx.activate_index.assert_called_once_with(1)
        ctx.append_and_activate.assert_not_called()

    def test_blank_reused_in_target_group_only(self, tmp_path):
        """打开文件复用目标组激活的空白标签；另一组的空白不动。"""
        f = tmp_path / "b.md"
        f.write_text("# b", encoding="utf-8")
        # 左组空白（索引 0）、右组空白（索引 1，激活）→ 复用右组空白
        ctx = _make_ctx([_tab(None, group=0), _tab(None, group=1)],
                        split_editor=True, active_pane=1,
                        active_left=0, active_right=1)
        cbs = build_file_io_ops(ctx)
        cbs["open_file_by_path"](str(f))
        ctx.append_and_activate.assert_not_called()
        ctx.update_tab.assert_called_once()
        args, kwargs = ctx.update_tab.call_args
        assert args[0] == 1  # 右组空白（全局索引 1）
        assert kwargs["file_path"] == str(f)
        # 复用后激活 + 重建该组编辑器
        ctx.activate_index.assert_called_once_with(1)

    def test_lnk_open_keeps_link_name_as_display_name(self, tmp_path, monkeypatch):
        """.lnk 快捷方式打开：标签记录链接文件名（display_name），file_path 为目标。"""
        target = tmp_path / "Deepseek-cordis.md"
        target.write_text("# 目标文档", encoding="utf-8")
        lnk = tmp_path / "Deepseek-cordis.md.lnk"
        # mock 快捷方式解析：is_shortcut → True，目标 → target
        monkeypatch.setattr(app._file_io_ops.shortcut, "is_shortcut", lambda p: True)
        monkeypatch.setattr(
            app._file_io_ops.shortcut, "resolve_shortcut_target", lambda p: str(target)
        )
        ctx = _make_ctx([], split_editor=False)
        cbs = build_file_io_ops(ctx)
        cbs["open_file_by_path"](str(lnk))
        ctx.append_and_activate.assert_called_once()
        fields = ctx.append_and_activate.call_args[0][0]
        # 标签名 = 链接文件名；file_path = 目标路径（编辑/保存作用于目标文档）
        assert fields["display_name"] == "Deepseek-cordis.md.lnk"
        assert fields["file_path"] == str(target)

    def test_lnk_open_reuse_blank_sets_display_name(self, tmp_path, monkeypatch):
        """.lnk 打开复用空白标签：display_name 一并写入。"""
        target = tmp_path / "note.md"
        target.write_text("# note", encoding="utf-8")
        lnk = tmp_path / "note.md.lnk"
        monkeypatch.setattr(app._file_io_ops.shortcut, "is_shortcut", lambda p: True)
        monkeypatch.setattr(
            app._file_io_ops.shortcut, "resolve_shortcut_target", lambda p: str(target)
        )
        ctx = _make_ctx([_tab(None, group=0)], split_editor=False, active_left=0)
        cbs = build_file_io_ops(ctx)
        cbs["open_file_by_path"](str(lnk))
        ctx.append_and_activate.assert_not_called()
        ctx.update_tab.assert_called_once()
        _args, kwargs = ctx.update_tab.call_args
        assert kwargs["display_name"] == "note.md.lnk"
        assert kwargs["file_path"] == str(target)

    def test_plain_md_open_has_no_display_name(self, tmp_path):
        """普通 .md 打开不带 display_name（标签显示回退目标文件名）。"""
        f = tmp_path / "plain.md"
        f.write_text("# plain", encoding="utf-8")
        ctx = _make_ctx([], split_editor=False)
        cbs = build_file_io_ops(ctx)
        cbs["open_file_by_path"](str(f))
        fields = ctx.append_and_activate.call_args[0][0]
        assert fields.get("display_name") is None
        assert fields["file_path"] == str(f)


if __name__ == "__main__":
    import pathlib
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp())
    TestGroupOpenDedupe().test_same_group_reopen_activates_existing(tmp)
    TestGroupOpenDedupe().test_other_group_open_creates_independent_copy(tmp)
    TestGroupOpenDedupe().test_focused_group_existing_tab_activated_not_duplicated(tmp)
    TestGroupOpenDedupe().test_blank_reused_in_target_group_only(tmp)
    print("\n所有组内去重打开测试通过 ✅")
