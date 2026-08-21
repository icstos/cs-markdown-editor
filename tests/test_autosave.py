"""app.autosave 单元测试（间隔触发模型）。

覆盖：
- autosave_enabled_for：纯函数，检查 settings.auto_save + tab 可写路径
- schedule_autosave：兼容入口，间隔触发模型下为空操作
- autosave_all_dirty：扫描 tabs_ref，对所有脏且可自动保存的标签异步触发 save_doc

不依赖 UI 渲染，用 Mock 模拟 page_ref / tabs_ref / save_doc_fn / set_status_fn。
"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.autosave import (
    AutosaveContext,
    autosave_all_dirty,
    autosave_all_dirty_sync,
    autosave_enabled_for,
    autosave_on_switch_enabled,
    schedule_autosave,
)


# ---------------- 辅助工厂 ----------------
def _editor_tab(file_path=None, dirty=True) -> dict:
    """创建普通编辑器标签。"""
    return {"type": "editor", "file_path": file_path, "dirty": dirty}


def _diff_tab(left_path=None, right_path=None, left_dirty=True, right_dirty=True) -> dict:
    """创建对比标签。"""
    return {
        "type": "diff",
        "left_path": left_path,
        "right_path": right_path,
        "left_dirty": left_dirty,
        "right_dirty": right_dirty,
    }


def _make_ctx(
    settings=None,
    tabs=None,
    save_doc_fn=None,
    set_status_fn=None,
    page_available=True,
) -> tuple[MagicMock, AutosaveContext]:
    """创建 AutosaveContext + page_ref mock。

    返回 (page_ref, ctx)，page_ref.current.run_task 捕获协程函数供测试断言。
    save_doc_fn 默认为 MagicMock（同步，不返回值）。
    """
    settings = settings if settings is not None else {"auto_save": True}
    tabs_list = tabs if tabs is not None else [_editor_tab("/tmp/note.md", dirty=True)]

    page_ref = MagicMock()
    if page_available:
        page = MagicMock()
        page_ref.current = page
    else:
        page_ref.current = None

    tabs_ref = MagicMock()
    tabs_ref.current = tabs_list

    save_doc_fn = save_doc_fn or MagicMock()
    ctx = AutosaveContext(
        settings=settings,
        page_ref=page_ref,
        tabs_ref=tabs_ref,
        save_doc_fn=save_doc_fn,
        set_status_fn=set_status_fn,
    )
    return page_ref, ctx


# ---------------- autosave_enabled_for ----------------
def test_enabled_auto_save_off():
    """auto_save=False → 不生效。"""
    assert autosave_enabled_for({"auto_save": False}, _editor_tab("/x.md")) is False


def test_enabled_tab_none():
    """tab=None → 不生效。"""
    assert autosave_enabled_for({"auto_save": True}, None) is False


def test_enabled_editor_with_path():
    """普通标签有 file_path → 生效。"""
    assert autosave_enabled_for({"auto_save": True}, _editor_tab("/x.md")) is True


def test_enabled_editor_no_path():
    """普通标签无 file_path → 不生效。"""
    assert autosave_enabled_for({"auto_save": True}, _editor_tab(None)) is False


def test_enabled_diff_left_path_only():
    """对比标签仅左侧有路径 → 生效。"""
    assert autosave_enabled_for(
        {"auto_save": True}, _diff_tab(left_path="/a.md", right_path=None)
    ) is True


def test_enabled_diff_right_path_only():
    """对比标签仅右侧有路径 → 生效。"""
    assert autosave_enabled_for(
        {"auto_save": True}, _diff_tab(left_path=None, right_path="/b.md")
    ) is True


def test_enabled_diff_no_paths():
    """对比标签两侧均无路径 → 不生效。"""
    assert autosave_enabled_for(
        {"auto_save": True}, _diff_tab(left_path=None, right_path=None)
    ) is False


# ---------------- schedule_autosave（空操作） ----------------
def test_schedule_autosave_is_noop():
    """间隔触发模型下 schedule_autosave 为空操作，不调度任何任务。"""
    page_ref, ctx = _make_ctx()
    schedule_autosave(ctx)
    page_ref.current.run_task.assert_not_called()


def test_schedule_autosave_returns_none():
    """schedule_autosave 返回 None。"""
    _, ctx = _make_ctx()
    assert schedule_autosave(ctx) is None


# ---------------- autosave_all_dirty：前置守卫 ----------------
def test_autosave_all_dirty_auto_save_off():
    """auto_save=False → 返回 0，不调度。"""
    page_ref, ctx = _make_ctx(settings={"auto_save": False})
    assert autosave_all_dirty(ctx) == 0
    page_ref.current.run_task.assert_not_called()


def test_autosave_all_dirty_page_none():
    """page 为 None → 返回 0，不抛异常。"""
    _, ctx = _make_ctx(page_available=False)
    assert autosave_all_dirty(ctx) == 0


def test_autosave_all_dirty_no_dirty_tabs():
    """所有标签非脏 → 返回 0。"""
    page_ref, ctx = _make_ctx(tabs=[_editor_tab("/x.md", dirty=False)])
    assert autosave_all_dirty(ctx) == 0
    page_ref.current.run_task.assert_not_called()


def test_autosave_all_dirty_no_path_skips():
    """脏标签无路径 → 跳过，返回 0。"""
    page_ref, ctx = _make_ctx(tabs=[_editor_tab(None, dirty=True)])
    assert autosave_all_dirty(ctx) == 0
    page_ref.current.run_task.assert_not_called()


# ---------------- autosave_all_dirty：调度行为 ----------------
def test_autosave_all_dirty_schedules_one():
    """单个脏标签 + 有路径 → 调度一次 run_task，返回 1。"""
    page_ref, ctx = _make_ctx(tabs=[_editor_tab("/x.md", dirty=True)])
    assert autosave_all_dirty(ctx) == 1
    page_ref.current.run_task.assert_called_once()
    # run_task 的第一个参数是 save_doc_fn，第二个是 tab_index
    args = page_ref.current.run_task.call_args[0]
    assert args[0] is ctx.save_doc_fn
    assert args[1] == 0


def test_autosave_all_dirty_schedules_multiple():
    """多个脏标签 → 逐一调度，返回计数。"""
    tabs = [
        _editor_tab("/a.md", dirty=True),
        _editor_tab("/b.md", dirty=True),
        _editor_tab("/c.md", dirty=False),  # 非脏，跳过
        _editor_tab(None, dirty=True),       # 无路径，跳过
    ]
    page_ref, ctx = _make_ctx(tabs=tabs)
    assert autosave_all_dirty(ctx) == 2
    assert page_ref.current.run_task.call_count == 2
    # 验证调度的 index 分别为 0 和 1
    call_args = [c.args[1] for c in page_ref.current.run_task.call_args_list]
    assert call_args == [0, 1]


def test_autosave_all_dirty_diff_tab():
    """对比标签脏且有路径 → 调度保存。"""
    tab = _diff_tab(left_path="/a.md", right_path="/b.md")
    page_ref, ctx = _make_ctx(tabs=[tab])
    assert autosave_all_dirty(ctx) == 1
    page_ref.current.run_task.assert_called_once()


def test_autosave_all_dirty_diff_no_path_skips():
    """对比标签脏但两侧均无路径 → 跳过。"""
    tab = _diff_tab(left_path=None, right_path=None)
    page_ref, ctx = _make_ctx(tabs=[tab])
    assert autosave_all_dirty(ctx) == 0
    page_ref.current.run_task.assert_not_called()


def test_autosave_all_dirty_empty_tabs():
    """空标签列表 → 返回 0。"""
    page_ref, ctx = _make_ctx(tabs=[])
    assert autosave_all_dirty(ctx) == 0
    page_ref.current.run_task.assert_not_called()


def test_autosave_all_dirty_triggers_status_message():
    """有触发保存时 → 调用 set_status_fn 推送「已自动保存」。"""
    set_status_fn = MagicMock()
    _, ctx = _make_ctx(
        tabs=[_editor_tab("/x.md", dirty=True)],
        set_status_fn=set_status_fn,
    )
    autosave_all_dirty(ctx)
    set_status_fn.assert_called_once_with("已自动保存", "success")


def test_autosave_all_dirty_no_status_when_nothing_saved():
    """无触发保存时 → 不调用 set_status_fn。"""
    set_status_fn = MagicMock()
    _, ctx = _make_ctx(
        tabs=[_editor_tab("/x.md", dirty=False)],
        set_status_fn=set_status_fn,
    )
    autosave_all_dirty(ctx)
    set_status_fn.assert_not_called()


def test_autosave_all_dirty_no_status_fn_ok():
    """set_status_fn=None 时不报错。"""
    _, ctx = _make_ctx(
        tabs=[_editor_tab("/x.md", dirty=True)],
        set_status_fn=None,
    )
    # 不应抛异常
    result = autosave_all_dirty(ctx)
    assert result == 1


def _make_ctx_sync(
    settings=None,
    tabs=None,
    save_doc_sync_fn=None,
    indices=None,
) -> tuple[MagicMock, AutosaveContext]:
    """创建支持同步保存的 AutosaveContext（autosave_all_dirty_sync 专用）。"""
    settings = settings if settings is not None else {"auto_save": True}
    tabs_list = tabs if tabs is not None else [_editor_tab("/tmp/note.md", dirty=True)]
    tabs_ref = MagicMock()
    tabs_ref.current = tabs_list
    save_doc_sync_fn = save_doc_sync_fn or MagicMock(return_value=True)
    ctx = AutosaveContext(
        settings=settings,
        page_ref=MagicMock(),
        tabs_ref=tabs_ref,
        save_doc_fn=MagicMock(),
        save_doc_sync_fn=save_doc_sync_fn,
    )
    return save_doc_sync_fn, ctx


# ---------------- autosave_on_switch_enabled ----------------
def test_switch_enabled_all_on():
    """auto_save + auto_save_on_switch 均开启 → True。"""
    assert autosave_on_switch_enabled(
        {"auto_save": True, "auto_save_on_switch": True}
    ) is True


def test_switch_enabled_auto_save_off():
    """auto_save 关闭 → False（主开关优先）。"""
    assert autosave_on_switch_enabled(
        {"auto_save": False, "auto_save_on_switch": True}
    ) is False


def test_switch_enabled_switch_off():
    """auto_save_on_switch 关闭 → False。"""
    assert autosave_on_switch_enabled(
        {"auto_save": True, "auto_save_on_switch": False}
    ) is False


def test_switch_enabled_defaults_true():
    """缺省 auto_save_on_switch → 视为开启（兼容旧 settings.json）。"""
    assert autosave_on_switch_enabled({"auto_save": True}) is True


# ---------------- autosave_all_dirty_sync：前置守卫 ----------------
def test_sync_auto_save_off():
    """auto_save=False → 返回 0，不调用保存回调。"""
    fn, ctx = _make_ctx_sync(settings={"auto_save": False})
    assert autosave_all_dirty_sync(ctx) == 0
    fn.assert_not_called()


def test_sync_no_callback_injected():
    """未注入 save_doc_sync_fn → 返回 0，不抛异常。"""
    ctx = AutosaveContext(
        settings={"auto_save": True},
        page_ref=MagicMock(),
        tabs_ref=MagicMock(),
        save_doc_fn=MagicMock(),
    )
    tabs_ref = ctx.tabs_ref
    tabs_ref.current = [_editor_tab("/x.md", dirty=True)]
    assert autosave_all_dirty_sync(ctx) == 0


def test_sync_force_skips_master_switch():
    """force=True 跳过 auto_save 开关检查（程序退出场景）。"""
    fn, ctx = _make_ctx_sync(settings={"auto_save": False})
    assert autosave_all_dirty_sync(ctx, force=True) == 1
    fn.assert_called_once_with(0, force=True)


# ---------------- autosave_all_dirty_sync：同步保存行为 ----------------
def test_sync_saves_dirty_path_tabs():
    """脏且有路径 → 同步调用保存回调，返回计数。"""
    fn, ctx = _make_ctx_sync(
        tabs=[
            _editor_tab("/a.md", dirty=True),
            _editor_tab("/b.md", dirty=True),
            _editor_tab("/c.md", dirty=False),
            _editor_tab(None, dirty=True),
        ]
    )
    assert autosave_all_dirty_sync(ctx) == 2
    assert fn.call_count == 2
    assert [c.args[0] for c in fn.call_args_list] == [0, 1]


def test_sync_indices_filter():
    """indices 限制扫描范围（关闭/切换时只保存涉及的标签）。"""
    fn, ctx = _make_ctx_sync(
        tabs=[
            _editor_tab("/a.md", dirty=True),
            _editor_tab("/b.md", dirty=True),
        ]
    )
    assert autosave_all_dirty_sync(ctx, indices=[1]) == 1
    fn.assert_called_once_with(1, force=True)


def test_sync_out_of_range_indices_ignored():
    """越界 indices 安全忽略。"""
    fn, ctx = _make_ctx_sync(tabs=[_editor_tab("/a.md", dirty=True)])
    assert autosave_all_dirty_sync(ctx, indices=[5]) == 0
    fn.assert_not_called()


def test_sync_diff_tab_saved():
    """对比标签脏且有路径 → 同步保存。"""
    fn, ctx = _make_ctx_sync(
        tabs=[_diff_tab(left_path="/a.md", right_path="/b.md")]
    )
    assert autosave_all_dirty_sync(ctx) == 1
    fn.assert_called_once_with(0, force=True)


def test_sync_save_failure_continues():
    """单个标签保存失败不影响其余标签。"""
    def _flaky(i, force=False):
        return False if i == 0 else True

    fn, ctx = _make_ctx_sync(
        tabs=[
            _editor_tab("/a.md", dirty=True),
            _editor_tab("/b.md", dirty=True),
        ],
        save_doc_sync_fn=MagicMock(side_effect=_flaky),
    )
    assert autosave_all_dirty_sync(ctx) == 1
    assert fn.call_count == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
