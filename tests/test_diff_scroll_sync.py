"""app.diff_scroll_sync 单元测试。

覆盖 DiffScrollSync 状态机：
- sync_to：触发目标侧 scroll_to_offset + 调度异步追赶
- on_left_scroll / on_right_scroll：syncing 期间主动侧累积 pending，被动侧忽略
- _after_sync：清除标记 + 追赶累积请求
- 边界：target 为 None / scroll_to_offset 为 None / page 为 None

不依赖 UI 渲染，用 Mock 模拟 nav_ref.current 和 page.run_task。
"""

import asyncio
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.diff_scroll_sync import DiffScrollSync


# ---------------- 辅助工厂 ----------------
def _make_nav(scrollable: bool = True) -> MagicMock:
    """创建模拟 nav_ref：.current 返回带 scroll_to_offset 的 mock。"""
    nav = MagicMock()
    target = MagicMock()
    if scrollable:
        target.scroll_to_offset = MagicMock()
    else:
        target.scroll_to_offset = None
    nav.current = target
    return nav


def _make_page_ref(run_task_synchronous: bool = False) -> MagicMock:
    """创建模拟 page_ref：.current 返回带 run_task 的 mock。

    run_task_synchronous=True 时立即 await 协程（用于测试 _after_sync 逻辑）。
    """
    page_ref = MagicMock()
    page = MagicMock()

    if run_task_synchronous:

        def _sync_run_task(coro):
            asyncio.run(coro)

        page.run_task = _sync_run_task
    else:
        page.run_task = MagicMock()

    page_ref.current = page
    return page_ref


# ---------------- 构造与初始状态 ----------------
def test_initial_state():
    """构造后 syncing=False, direction=None, pending=None。"""
    page_ref = MagicMock()
    nav_l = MagicMock()
    nav_r = MagicMock()
    sync = DiffScrollSync(page_ref, nav_l, nav_r)
    assert sync._syncing is False
    assert sync._direction is None
    assert sync._pending_target is None
    assert sync._pending_offset == 0.0


# ---------------- sync_to ----------------
def test_sync_to_triggers_scroll_and_run_task():
    """sync_to 调用目标 scroll_to_offset + page.run_task，置 syncing 标记。"""
    page_ref = _make_page_ref()
    nav_r = _make_nav()
    sync = DiffScrollSync(page_ref, MagicMock(), nav_r)

    sync.sync_to(nav_r, 100.0, "lr")

    nav_r.current.scroll_to_offset.assert_called_once_with(100.0)
    assert sync._syncing is True
    assert sync._direction == "lr"
    page_ref.current.run_task.assert_called_once()


def test_sync_to_target_none_skips():
    """target_nav 为 None 时跳过。"""
    page_ref = _make_page_ref()
    sync = DiffScrollSync(page_ref, _make_nav(), _make_nav())

    sync.sync_to(None, 100.0, "lr")

    assert sync._syncing is False
    page_ref.current.run_task.assert_not_called()


def test_sync_to_target_current_none_skips():
    """target_nav.current 为 None 时跳过。"""
    page_ref = _make_page_ref()
    nav = MagicMock()
    nav.current = None
    sync = DiffScrollSync(page_ref, nav, _make_nav())

    sync.sync_to(nav, 100.0, "lr")

    assert sync._syncing is False


def test_sync_to_no_scroll_method_skips():
    """target.scroll_to_offset 为 None 时跳过。"""
    page_ref = _make_page_ref()
    nav = _make_nav(scrollable=False)
    sync = DiffScrollSync(page_ref, _make_nav(), nav)

    sync.sync_to(nav, 100.0, "lr")

    assert sync._syncing is False


def test_sync_to_scroll_exception_resets():
    """scroll_to_offset 抛异常时重置 syncing 标记。"""
    page_ref = _make_page_ref()
    nav_r = _make_nav()
    nav_r.current.scroll_to_offset.side_effect = RuntimeError("boom")
    sync = DiffScrollSync(page_ref, MagicMock(), nav_r)

    sync.sync_to(nav_r, 100.0, "lr")

    assert sync._syncing is False
    assert sync._direction is None


def test_sync_to_page_none_resets():
    """page 为 None 时重置 syncing 标记（不调 run_task）。"""
    page_ref = MagicMock()
    page_ref.current = None
    nav_r = _make_nav()
    sync = DiffScrollSync(page_ref, MagicMock(), nav_r)

    sync.sync_to(nav_r, 100.0, "lr")

    assert sync._syncing is False
    assert sync._direction is None


def test_sync_to_clears_pending():
    """sync_to 调用时清除之前的 pending 请求。"""
    page_ref = _make_page_ref()
    nav_r = _make_nav()
    sync = DiffScrollSync(page_ref, MagicMock(), nav_r)
    sync._pending_target = MagicMock()
    sync._pending_offset = 50.0

    sync.sync_to(nav_r, 100.0, "lr")

    assert sync._pending_target is None
    assert sync._pending_offset == 0.0


# ---------------- on_left_scroll ----------------
def test_on_left_scroll_idle_syncs_right():
    """非 syncing 状态：左滚动 → 同步右。"""
    page_ref = _make_page_ref()
    nav_r = _make_nav()
    sync = DiffScrollSync(page_ref, MagicMock(), nav_r)

    sync.on_left_scroll(200.0, 1000.0, 600.0)

    nav_r.current.scroll_to_offset.assert_called_once_with(200.0)
    assert sync._direction == "lr"


def test_on_left_scroll_syncing_lr_accumulates_pending():
    """syncing + direction=lr（左主动）：累积 pending 到右侧。"""
    page_ref = _make_page_ref()
    nav_r = _make_nav()
    sync = DiffScrollSync(page_ref, MagicMock(), nav_r)
    sync._syncing = True
    sync._direction = "lr"

    sync.on_left_scroll(300.0, 1000.0, 600.0)

    assert sync._pending_target is nav_r
    assert sync._pending_offset == 300.0
    nav_r.current.scroll_to_offset.assert_not_called()


def test_on_left_scroll_syncing_rl_ignores():
    """syncing + direction=rl（右主动，左被动）：忽略，不累积。"""
    page_ref = _make_page_ref()
    nav_r = _make_nav()
    sync = DiffScrollSync(page_ref, MagicMock(), nav_r)
    sync._syncing = True
    sync._direction = "rl"

    sync.on_left_scroll(300.0, 1000.0, 600.0)

    assert sync._pending_target is None
    nav_r.current.scroll_to_offset.assert_not_called()


# ---------------- on_right_scroll ----------------
def test_on_right_scroll_idle_syncs_left():
    """非 syncing 状态：右滚动 → 同步左。"""
    page_ref = _make_page_ref()
    nav_l = _make_nav()
    sync = DiffScrollSync(page_ref, nav_l, MagicMock())

    sync.on_right_scroll(200.0, 1000.0, 600.0)

    nav_l.current.scroll_to_offset.assert_called_once_with(200.0)
    assert sync._direction == "rl"


def test_on_right_scroll_syncing_rl_accumulates_pending():
    """syncing + direction=rl（右主动）：累积 pending 到左侧。"""
    page_ref = _make_page_ref()
    nav_l = _make_nav()
    sync = DiffScrollSync(page_ref, nav_l, MagicMock())
    sync._syncing = True
    sync._direction = "rl"

    sync.on_right_scroll(300.0, 1000.0, 600.0)

    assert sync._pending_target is nav_l
    assert sync._pending_offset == 300.0
    nav_l.current.scroll_to_offset.assert_not_called()


def test_on_right_scroll_syncing_lr_ignores():
    """syncing + direction=lr（左主动，右被动）：忽略，不累积。"""
    page_ref = _make_page_ref()
    nav_l = _make_nav()
    sync = DiffScrollSync(page_ref, nav_l, MagicMock())
    sync._syncing = True
    sync._direction = "lr"

    sync.on_right_scroll(300.0, 1000.0, 600.0)

    assert sync._pending_target is None
    nav_l.current.scroll_to_offset.assert_not_called()


# ---------------- _after_sync（异步追赶） ----------------
def test_after_sync_clears_syncing_and_no_pending():
    """_after_sync 清除 syncing 标记，无 pending 时不追赶。"""
    page_ref = _make_page_ref()
    sync = DiffScrollSync(page_ref, _make_nav(), _make_nav())
    sync._syncing = True
    sync._direction = "lr"

    asyncio.run(sync._after_sync())

    assert sync._syncing is False
    assert sync._direction is None


def test_after_sync_chases_pending():
    """_after_sync 有 pending 时调用 sync_to 追赶。"""
    page_ref = _make_page_ref()
    nav_r = _make_nav()
    sync = DiffScrollSync(page_ref, MagicMock(), nav_r)
    sync._syncing = True
    sync._direction = "lr"
    sync._pending_target = nav_r
    sync._pending_offset = 500.0

    asyncio.run(sync._after_sync())

    # 追赶后 syncing 重新置 True（sync_to 内部设置），scroll_to_offset 被调用
    nav_r.current.scroll_to_offset.assert_called_once_with(500.0)
    assert sync._pending_target is None


def test_after_sync_chases_pending_direction_fallback():
    """_after_sync 追赶时 direction 为 None 回退到 'lr'。"""
    page_ref = _make_page_ref()
    nav_r = _make_nav()
    sync = DiffScrollSync(page_ref, MagicMock(), nav_r)
    sync._syncing = True
    sync._direction = None  # 异常场景
    sync._pending_target = nav_r
    sync._pending_offset = 500.0

    asyncio.run(sync._after_sync())

    nav_r.current.scroll_to_offset.assert_called_once_with(500.0)
    assert sync._direction == "lr"


# ---------------- 完整流程（端到端模拟） ----------------
def test_full_flow_left_scrolls_then_right_follows():
    """完整流程：左侧滚动 → sync_to 右 → _after_sync 清除标记 → 无 pending。"""
    page_ref = _make_page_ref()
    nav_r = _make_nav()
    sync = DiffScrollSync(page_ref, MagicMock(), nav_r)

    sync.on_left_scroll(100.0, 1000.0, 600.0)
    assert sync._syncing is True
    assert sync._direction == "lr"

    asyncio.run(sync._after_sync())
    assert sync._syncing is False
    assert sync._direction is None


def test_full_flow_rapid_scroll_accumulates_then_chases():
    """连续滚动：syncing 期间再次滚动累积 pending，_after_sync 追赶最新值。"""
    page_ref = _make_page_ref()
    nav_r = _make_nav()
    sync = DiffScrollSync(page_ref, MagicMock(), nav_r)

    # 第一次滚动触发同步
    sync.on_left_scroll(100.0, 1000.0, 600.0)
    assert sync._syncing is True

    # syncing 期间再次滚动（同方向）→ 累积 pending
    sync.on_left_scroll(200.0, 1000.0, 600.0)
    assert sync._pending_offset == 200.0

    # _after_sync 追赶 pending
    asyncio.run(sync._after_sync())
    nav_r.current.scroll_to_offset.assert_called_with(200.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
