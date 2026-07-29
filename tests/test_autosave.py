"""app.autosave 单元测试。

覆盖：
- autosave_enabled_for：纯函数，检查 settings.auto_save + tab 可写路径
- schedule_autosave：通过 AutosaveContext 注入依赖，debounce 2s 保存

不依赖 UI 渲染，用 Mock 模拟 page_ref / tabs_ref / save_doc_fn。
asyncio.sleep 被 patch 为即时返回，避免 2s 等待。
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.autosave import AutosaveContext, autosave_enabled_for, schedule_autosave


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
    tab=None,
    tabs=None,
    active_index=0,
    save_doc_fn=None,
    page_available=True,
) -> tuple[MagicMock, AutosaveContext]:
    """创建 AutosaveContext + page_ref mock。

    返回 (page_ref, ctx)，page_ref.current.run_task 捕获协程函数供测试 await。
    save_doc_fn 默认为 AsyncMock（await 时返回 None）。
    """
    settings = settings if settings is not None else {"auto_save": True}
    if tab is None:
        tab = _editor_tab("/tmp/note.md", dirty=True)
    tabs_list = tabs if tabs is not None else [tab]

    page_ref = MagicMock()
    if page_available:
        page = MagicMock()
        page_ref.current = page
    else:
        page_ref.current = None

    tabs_ref = MagicMock()
    tabs_ref.current = tabs_list

    active_index_ref = MagicMock()
    active_index_ref.current = active_index

    cur_tab_fn = MagicMock(return_value=tab)
    save_doc_fn = save_doc_fn or AsyncMock()

    ctx = AutosaveContext(
        settings=settings,
        page_ref=page_ref,
        tabs_ref=tabs_ref,
        active_index_ref=active_index_ref,
        cur_tab_fn=cur_tab_fn,
        save_doc_fn=save_doc_fn,
    )
    return page_ref, ctx


def _run_captured(ctx) -> None:
    """提取 run_task 捕获的协程函数并同步执行（patch asyncio.sleep 为即时）。"""
    fn = ctx.page_ref.current.run_task.call_args[0][0]
    with patch("app.autosave.asyncio.sleep", new_callable=AsyncMock):
        asyncio.run(fn())


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


# ---------------- schedule_autosave：前置守卫 ----------------
def test_schedule_tab_not_dirty_skips():
    """标签非脏 → 不调度。"""
    page_ref, ctx = _make_ctx(tab=_editor_tab("/x.md", dirty=False))
    schedule_autosave(ctx)
    page_ref.current.run_task.assert_not_called()


def test_schedule_autosave_disabled_skips():
    """auto_save=False → 不调度。"""
    page_ref, ctx = _make_ctx(settings={"auto_save": False})
    schedule_autosave(ctx)
    page_ref.current.run_task.assert_not_called()


def test_schedule_tab_none_skips():
    """cur_tab_fn 返回 None → 不调度。"""
    _, ctx = _make_ctx()
    ctx.cur_tab_fn = MagicMock(return_value=None)
    schedule_autosave(ctx)
    ctx.page_ref.current.run_task.assert_not_called()


def test_schedule_page_none_skips():
    """page 为 None → 不调度（不抛异常）。"""
    _, ctx = _make_ctx(page_available=False)
    schedule_autosave(ctx)


def test_schedule_no_path_skips():
    """标签无路径 → 不调度。"""
    page_ref, ctx = _make_ctx(tab=_editor_tab(None, dirty=True))
    schedule_autosave(ctx)
    page_ref.current.run_task.assert_not_called()


# ---------------- schedule_autosave：debounce 保存 ----------------
def test_schedule_schedules_run_task():
    """脏标签 + auto_save + 有路径 → 调度 run_task。"""
    page_ref, ctx = _make_ctx()
    schedule_autosave(ctx)
    page_ref.current.run_task.assert_called_once()


def test_schedule_debounce_saves_after_delay():
    """debounce 协程等待 2s 后调用 save_doc_fn。"""
    _, ctx = _make_ctx()
    schedule_autosave(ctx)
    _run_captured(ctx)
    ctx.save_doc_fn.assert_called_once_with(0)


def test_schedule_index_out_of_range_skips_save():
    """调度后 active_index 越界 → 不保存。"""
    _, ctx = _make_ctx(active_index=5, tabs=[_editor_tab("/x.md")])
    schedule_autosave(ctx)
    _run_captured(ctx)
    ctx.save_doc_fn.assert_not_called()


def test_schedule_tab_becomes_clean_skips_save():
    """调度后标签变干净 → 不保存。"""
    tab = _editor_tab("/x.md", dirty=True)
    _, ctx = _make_ctx(tab=tab, tabs=[tab])
    schedule_autosave(ctx)

    # 模拟保存前标签变干净
    tab["dirty"] = False
    ctx.tabs_ref.current = [tab]

    _run_captured(ctx)
    ctx.save_doc_fn.assert_not_called()


def test_schedule_captures_sched_index():
    """调度时捕获 active_index，即便后续切换标签也保存原标签。"""
    tab0 = _editor_tab("/a.md", dirty=True)
    tab1 = _editor_tab("/b.md", dirty=True)
    _, ctx = _make_ctx(tab=tab0, active_index=0, tabs=[tab0, tab1])
    schedule_autosave(ctx)
    _run_captured(ctx)
    # 应保存 index=0（调度时的索引），而非切换后的索引
    ctx.save_doc_fn.assert_called_once_with(0)


def test_schedule_diff_tab_saves():
    """对比标签也能触发自动保存。"""
    tab = _diff_tab(left_path="/a.md", right_path="/b.md")
    _, ctx = _make_ctx(tab=tab, tabs=[tab])
    schedule_autosave(ctx)
    _run_captured(ctx)
    ctx.save_doc_fn.assert_called_once_with(0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
