"""app/_tab_management.py 控制器单测。

验证标签 CRUD 核心逻辑（不依赖 Flet runtime）：
- do_close_many 索引计算（removed_before / new_active 调整）
- do_close_many 关闭所有标签回退空白
- request_close 干净标签直接关 / 含脏标签弹确认
- close_tab 委托 request_close
- select_tab / activate_index 同值/越界守卫与组感知激活
- cycle_tab 循环切换（组内循环）
- 拆分组场景：右组清空收起拆分 / 组内邻居选择 / 会话按组递增 /
  append_and_activate / bump_tab_session
- save_and_close_pending 逐个保存脏标签后关闭

mock ctx 用 SimpleNamespace + Mock，覆盖 ctx 装配槽调用。
异步测试用 asyncio.run()（与 test_autosave.py 一致，无 pytest-asyncio 依赖）。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app._tab_management import build_tab_management


def _make_ctx(
    tabs: list,
    active_index: int,
    session: int = 0,
    confirm_close=None,
    split_editor: bool = False,
    active_pane: int = 0,
    active_index_left: int | None = None,
    active_index_right: int = 0,
    session_left: int = 0,
    session_right: int = 0,
) -> SimpleNamespace:
    """构造最小 mock ctx，含 tab_management 控制器所需字段（含拆分组状态）。

    单组场景（split_editor=False）：active_index_left 缺省同步 active_index。
    拆分场景：调用方显式传入两侧组激活索引与焦点视口。
    """
    if active_index_left is None:
        active_index_left = active_index
    return SimpleNamespace(
        tabs=list(tabs),
        active_index=active_index,
        session=session,
        confirm_close=confirm_close,
        split_editor=split_editor,
        active_pane=active_pane,
        active_index_left=active_index_left,
        active_index_right=active_index_right,
        session_left=session_left,
        session_right=session_right,
        tabs_ref=SimpleNamespace(current=list(tabs)),
        active_index_ref=SimpleNamespace(current=active_index),
        active_pane_ref=SimpleNamespace(current=active_pane),
        active_index_left_ref=SimpleNamespace(current=active_index_left),
        active_index_right_ref=SimpleNamespace(current=active_index_right),
        session_left_ref=SimpleNamespace(current=session_left),
        session_right_ref=SimpleNamespace(current=session_right),
        set_tabs=MagicMock(),
        set_active_index=MagicMock(),
        set_session=MagicMock(),
        set_confirm_close=MagicMock(),
        set_active_index_left=MagicMock(),
        set_active_index_right=MagicMock(),
        set_session_left=MagicMock(),
        set_session_right=MagicMock(),
        set_split_editor=MagicMock(),
        set_active_pane=MagicMock(),
        update_tab=MagicMock(),
        save_doc=AsyncMock(return_value=True),
    )


def _make_tab(file_path=None, dirty=False, doc_text="", group=0):
    """构造普通编辑标签（默认左组）。"""
    return {"file_path": file_path, "dirty": dirty, "document": doc_text, "group": group}


# ============ do_close_many 索引计算 ============

class TestDoCloseMany:
    def test_close_middle_active_stays_at_same_content(self):
        """关闭中间标签（active=1），new_active 指向原 2 号标签内容。"""
        ctx = _make_ctx([_make_tab("a"), _make_tab("b"), _make_tab("c")], active_index=1)
        cbs = build_tab_management(ctx)
        cbs["do_close_many"]([1])
        # 新 tabs = [a, c]，new_active=1 指向 c
        new_tabs = ctx.set_tabs.call_args[0][0]
        assert [t["file_path"] for t in new_tabs] == ["a", "c"]
        ctx.set_active_index.assert_called_once_with(1)
        assert ctx.active_index_ref.current == 1

    def test_close_before_active_shifts_active_down(self):
        """关闭 active 之前的标签，new_active 递减。"""
        ctx = _make_ctx([_make_tab("a"), _make_tab("b"), _make_tab("c")], active_index=2)
        cbs = build_tab_management(ctx)
        cbs["do_close_many"]([0])
        new_tabs = ctx.set_tabs.call_args[0][0]
        assert [t["file_path"] for t in new_tabs] == ["b", "c"]
        ctx.set_active_index.assert_called_once_with(1)

    def test_close_all_falls_back_to_blank(self):
        """关闭所有标签，回退为一个空白标签。"""
        ctx = _make_ctx([_make_tab("a"), _make_tab("b")], active_index=1)
        cbs = build_tab_management(ctx)
        cbs["do_close_many"]([0, 1])
        new_tabs = ctx.set_tabs.call_args[0][0]
        assert len(new_tabs) == 1
        assert new_tabs[0]["file_path"] is None
        assert new_tabs[0]["dirty"] is False
        ctx.set_active_index.assert_called_once_with(0)

    def test_close_active_and_before(self):
        """关闭 active 及其之前的标签，new_active=0。"""
        ctx = _make_ctx([_make_tab("a"), _make_tab("b"), _make_tab("c")], active_index=1)
        cbs = build_tab_management(ctx)
        cbs["do_close_many"]([0, 1])
        new_tabs = ctx.set_tabs.call_args[0][0]
        assert [t["file_path"] for t in new_tabs] == ["c"]
        ctx.set_active_index.assert_called_once_with(0)

    def test_empty_indices_noop(self):
        """空 indices 列表不触发任何 set_*。"""
        ctx = _make_ctx([_make_tab("a")], active_index=0)
        cbs = build_tab_management(ctx)
        cbs["do_close_many"]([])
        ctx.set_tabs.assert_not_called()
        ctx.set_active_index.assert_not_called()

    def test_invalid_indices_filtered(self):
        """越界 indices 被过滤。"""
        ctx = _make_ctx([_make_tab("a"), _make_tab("b")], active_index=0)
        cbs = build_tab_management(ctx)
        cbs["do_close_many"]([0, 5, -1])
        new_tabs = ctx.set_tabs.call_args[0][0]
        assert [t["file_path"] for t in new_tabs] == ["b"]

    def test_syncs_tabs_ref_and_session(self):
        """do_close_many 同步写 tabs_ref.current 并递增 session。"""
        ctx = _make_ctx([_make_tab("a"), _make_tab("b")], active_index=0, session=3)
        cbs = build_tab_management(ctx)
        cbs["do_close_many"]([1])
        new_tabs = ctx.set_tabs.call_args[0][0]
        assert ctx.tabs_ref.current is new_tabs
        ctx.set_session.assert_called_once_with(4)


# ============ request_close / close_tab ============

class TestRequestClose:
    def test_clean_tabs_closed_directly(self):
        """干净标签直接关闭，不弹确认。"""
        ctx = _make_ctx([_make_tab("a", dirty=False), _make_tab("b", dirty=False)], active_index=0)
        cbs = build_tab_management(ctx)
        cbs["request_close"]([0])
        ctx.set_confirm_close.assert_not_called()
        ctx.set_tabs.assert_called_once()

    def test_dirty_tabs_trigger_confirm(self):
        """含脏标签时弹确认（set_confirm_close）。"""
        ctx = _make_ctx([_make_tab("a", dirty=True)], active_index=0)
        cbs = build_tab_management(ctx)
        cbs["request_close"]([0])
        ctx.set_confirm_close.assert_called_once_with([0])
        ctx.set_tabs.assert_not_called()

    def test_mixed_clean_dirty_triggers_confirm(self):
        """干净+脏混合时弹确认（保守策略，避免误丢数据）。"""
        ctx = _make_ctx([_make_tab("a", dirty=False), _make_tab("b", dirty=True)], active_index=0)
        cbs = build_tab_management(ctx)
        cbs["request_close"]([0, 1])
        ctx.set_confirm_close.assert_called_once_with([0, 1])

    def test_empty_targets_noop(self):
        """空 targets 列表不触发任何操作。"""
        ctx = _make_ctx([_make_tab("a")], active_index=0)
        cbs = build_tab_management(ctx)
        cbs["request_close"]([])
        ctx.set_confirm_close.assert_not_called()
        ctx.set_tabs.assert_not_called()

    def test_close_tab_delegates_to_request_close(self):
        """close_tab 单个关闭委托 request_close。"""
        ctx = _make_ctx([_make_tab("a")], active_index=0)
        cbs = build_tab_management(ctx)
        cbs["close_tab"](0)
        ctx.set_tabs.assert_called_once()


# ============ select_tab / cycle_tab ============

class TestSelectAndCycle:
    def test_select_tab_same_index_noop(self):
        """切换到当前标签不触发 set_*。"""
        ctx = _make_ctx([_make_tab("a"), _make_tab("b")], active_index=0)
        cbs = build_tab_management(ctx)
        cbs["select_tab"](0)
        ctx.set_active_index.assert_not_called()
        ctx.set_session.assert_not_called()

    def test_select_tab_out_of_range_noop(self):
        """越界索引不触发切换。"""
        ctx = _make_ctx([_make_tab("a")], active_index=0)
        cbs = build_tab_management(ctx)
        cbs["select_tab"](5)
        ctx.set_active_index.assert_not_called()

    def test_select_tab_switches_and_increments_session(self):
        """切换标签递增 session（强制编辑器重置内部状态）。"""
        ctx = _make_ctx([_make_tab("a"), _make_tab("b")], active_index=0, session=2)
        cbs = build_tab_management(ctx)
        cbs["select_tab"](1)
        ctx.set_active_index.assert_called_once_with(1)
        ctx.set_session.assert_called_once_with(3)

    def test_cycle_tab_single_tab_noop(self):
        """单标签循环切换无操作。"""
        ctx = _make_ctx([_make_tab("a")], active_index=0)
        cbs = build_tab_management(ctx)
        cbs["cycle_tab"](1)
        ctx.set_active_index.assert_not_called()

    def test_cycle_tab_forward_wraps(self):
        """正向循环：末尾回到开头。"""
        ctx = _make_ctx([_make_tab("a"), _make_tab("b"), _make_tab("c")], active_index=2)
        ctx.active_index_ref.current = 2
        cbs = build_tab_management(ctx)
        cbs["cycle_tab"](1)
        ctx.set_active_index.assert_called_once_with(0)

    def test_cycle_tab_backward_wraps(self):
        """反向循环：开头回到末尾。"""
        ctx = _make_ctx([_make_tab("a"), _make_tab("b"), _make_tab("c")], active_index=0)
        ctx.active_index_ref.current = 0
        cbs = build_tab_management(ctx)
        cbs["cycle_tab"](-1)
        ctx.set_active_index.assert_called_once_with(2)


# ============ save_and_close_pending ============

class TestSaveAndClosePending:
    def test_no_pending_noop(self):
        """无待确认标签时无操作。"""
        ctx = _make_ctx([_make_tab("a")], active_index=0, confirm_close=None)
        cbs = build_tab_management(ctx)
        asyncio.run(cbs["save_and_close_pending"]())
        ctx.save_doc.assert_not_called()
        ctx.set_tabs.assert_not_called()

    def test_saves_dirty_then_closes(self):
        """逐个保存脏标签后关闭整批。"""
        ctx = _make_ctx(
            [_make_tab("a", dirty=True), _make_tab("b", dirty=True)],
            active_index=0,
            confirm_close=[0, 1],
        )
        ctx.tabs_ref.current = ctx.tabs
        cbs = build_tab_management(ctx)
        asyncio.run(cbs["save_and_close_pending"]())
        assert ctx.save_doc.await_count == 2
        ctx.set_confirm_close.assert_called_once_with(None)
        ctx.set_tabs.assert_called_once()  # do_close_many

    def test_save_cancel_aborts_close(self):
        """保存被取消（返回 False）时中止关闭，保留标签。"""
        ctx = _make_ctx(
            [_make_tab("a", dirty=True), _make_tab("b", dirty=True)],
            active_index=0,
            confirm_close=[0, 1],
        )
        ctx.tabs_ref.current = ctx.tabs
        ctx.save_doc = AsyncMock(return_value=False)
        cbs = build_tab_management(ctx)
        asyncio.run(cbs["save_and_close_pending"]())
        ctx.save_doc.assert_awaited_once_with(0)
        ctx.set_confirm_close.assert_called_once_with(None)
        ctx.set_tabs.assert_not_called()  # 中止，不关闭

    def test_clean_pending_not_saved(self):
        """待确认中的干净标签不调用 save_doc。"""
        ctx = _make_ctx(
            [_make_tab("a", dirty=False), _make_tab("b", dirty=True)],
            active_index=0,
            confirm_close=[0, 1],
        )
        ctx.tabs_ref.current = ctx.tabs
        cbs = build_tab_management(ctx)
        asyncio.run(cbs["save_and_close_pending"]())
        # 只保存脏标签 1，不保存干净标签 0
        ctx.save_doc.assert_awaited_once_with(1)


# ============ close_without_save / cancel_close ============

class TestCloseWithoutSave:
    def test_close_without_save_closes_batch(self):
        """不保存直接关闭整批。"""
        ctx = _make_ctx([_make_tab("a"), _make_tab("b")], active_index=0, confirm_close=[0, 1])
        ctx.tabs_ref.current = ctx.tabs
        cbs = build_tab_management(ctx)
        cbs["close_without_save"]()
        ctx.set_confirm_close.assert_called_once_with(None)
        ctx.set_tabs.assert_called_once()

    def test_no_pending_noop(self):
        """无待确认时无操作。"""
        ctx = _make_ctx([_make_tab("a")], active_index=0, confirm_close=None)
        cbs = build_tab_management(ctx)
        cbs["close_without_save"]()
        ctx.set_tabs.assert_not_called()

    def test_cancel_close_clears_confirm(self):
        """cancel_close 清空确认状态。"""
        ctx = _make_ctx([_make_tab("a")], active_index=0, confirm_close=[0])
        cbs = build_tab_management(ctx)
        cbs["cancel_close"]()
        ctx.set_confirm_close.assert_called_once_with(None)


# ============ 拆分编辑组（group 0=左 / 1=右）============

class TestSplitGroups:
    """拆分模式下的组感知激活 / 关闭 / 循环 / 会话计数。"""

    def test_activate_right_tab_switches_pane_and_bumps_right_session_only(self):
        """激活右组另一标签：焦点切右组，仅右组会话递增（左编辑器不重置）。"""
        tabs = [_make_tab("a", group=0), _make_tab("b", group=0),
                _make_tab("r1", group=1), _make_tab("r2", group=1)]
        ctx = _make_ctx(tabs, active_index=0, split_editor=True, active_pane=0,
                        active_index_left=0, active_index_right=2)
        cbs = build_tab_management(ctx)
        cbs["activate_index"](3)  # 激活右组 r2（原右组激活 r1）
        # 焦点切到右组（pane=1），全局 active_index=右组激活
        ctx.set_active_pane.assert_called_once_with(1)
        assert ctx.active_pane_ref.current == 1
        ctx.set_active_index.assert_called_once_with(3)
        assert ctx.active_index_ref.current == 2 + 1
        # 右组激活变化（r1→r2）→ session_right 递增；session_left 不变
        ctx.set_session_right.assert_called_once_with(1)
        ctx.set_session_left.assert_not_called()
        # 右组激活索引更新
        ctx.set_active_index_right.assert_called_once_with(3)

    def test_activate_same_right_tab_only_switches_pane(self):
        """激活右组当前激活标签（焦点原在左组）：仅切焦点，不递增会话。"""
        tabs = [_make_tab("a", group=0), _make_tab("r1", group=1)]
        ctx = _make_ctx(tabs, active_index=0, split_editor=True, active_pane=0,
                        active_index_left=0, active_index_right=1)
        cbs = build_tab_management(ctx)
        cbs["activate_index"](1)
        ctx.set_active_pane.assert_called_once_with(1)
        # 同标签激活不重建编辑器
        ctx.set_session_right.assert_not_called()
        ctx.set_session_left.assert_not_called()

    def test_activate_same_tab_noop(self):
        """重复激活同组同标签（焦点已在）→ 完全 no-op。"""
        tabs = [_make_tab("a"), _make_tab("b")]
        ctx = _make_ctx(tabs, active_index=0)
        cbs = build_tab_management(ctx)
        cbs["activate_index"](0)
        ctx.set_active_index.assert_not_called()
        ctx.set_session.assert_not_called()
        ctx.set_active_index_left.assert_not_called()

    def test_close_right_last_tab_collapses_split(self):
        """右组最后一个标签关闭：自动收起拆分，焦点回左组。"""
        tabs = [_make_tab("a", group=0), _make_tab("r1", group=1)]
        ctx = _make_ctx(tabs, active_index=1, split_editor=True, active_pane=1,
                        active_index_left=0, active_index_right=1)
        cbs = build_tab_management(ctx)
        cbs["do_close_many"]([1])
        ctx.set_split_editor.assert_called_once_with(False)
        assert ctx.active_pane_ref.current == 0
        # 焦点回左组 → 全局 active_index = 左组激活（a，索引 0）
        ctx.set_active_index.assert_called_once_with(0)

    def test_close_right_active_picks_right_neighbor_in_group(self):
        """关闭右组激活标签：组内右邻居优先（不跨组取左组标签）。"""
        tabs = [_make_tab("a", group=0),
                _make_tab("r1", group=1), _make_tab("r2", group=1), _make_tab("r3", group=1)]
        ctx = _make_ctx(tabs, active_index=1, split_editor=True, active_pane=1,
                        active_index_left=0, active_index_right=1)
        cbs = build_tab_management(ctx)
        cbs["do_close_many"]([1])
        new_tabs = ctx.set_tabs.call_args[0][0]
        assert [t["file_path"] for t in new_tabs] == ["a", "r2", "r3"]
        # 右组新激活 = r2（全局索引 1），焦点在右组 → active_index=1
        ctx.set_active_index_right.assert_called_once_with(1)
        ctx.set_active_index.assert_called_once_with(1)

    def test_close_right_active_no_neighbor_picks_left_in_group(self):
        """关闭右组激活且右侧无邻居：取组内左邻居。"""
        tabs = [_make_tab("a", group=0),
                _make_tab("r1", group=1), _make_tab("r2", group=1)]
        ctx = _make_ctx(tabs, active_index=2, split_editor=True, active_pane=1,
                        active_index_left=0, active_index_right=2)
        cbs = build_tab_management(ctx)
        cbs["do_close_many"]([2])
        ctx.set_active_index_right.assert_called_once_with(1)

    def test_close_left_active_keeps_right_session(self):
        """关闭左组激活：仅左组会话递增，右组编辑器状态不变。"""
        tabs = [_make_tab("a", group=0), _make_tab("b", group=0),
                _make_tab("r1", group=1)]
        ctx = _make_ctx(tabs, active_index=0, split_editor=True, active_pane=0,
                        active_index_left=0, active_index_right=2,
                        session_left=0, session_right=5)
        cbs = build_tab_management(ctx)
        cbs["do_close_many"]([0])
        # 左组激活变化（a→b）→ session_left 递增；session_right 保持 5
        ctx.set_session_left.assert_called_once_with(1)
        ctx.set_session_right.assert_not_called()
        assert ctx.session_right_ref.current == 5

    def test_cycle_tab_stays_in_focused_group(self):
        """Ctrl+Tab 仅在焦点侧组内循环（不切到另一组标签）。"""
        tabs = [_make_tab("a", group=0), _make_tab("b", group=0),
                _make_tab("r1", group=1)]
        ctx = _make_ctx(tabs, active_index=2, split_editor=True, active_pane=1,
                        active_index_left=0, active_index_right=2)
        cbs = build_tab_management(ctx)
        # 右组只有 r1：循环 no-op
        cbs["cycle_tab"](1)
        ctx.set_active_index.assert_not_called()
        # 焦点在左组：左组内 a→b
        ctx.active_pane_ref.current = 0
        ctx.active_index_ref.current = 0
        cbs["cycle_tab"](1)
        ctx.set_active_index.assert_called_once_with(1)

    def test_append_and_activate_appends_to_group(self):
        """append_and_activate 追加标签并激活到所属组。"""
        tabs = [_make_tab("a", group=0)]
        ctx = _make_ctx(tabs, active_index=0, split_editor=True, active_pane=1,
                        active_index_left=0, active_index_right=0)
        cbs = build_tab_management(ctx)
        idx = cbs["append_and_activate"]({
            "file_path": "r1", "dirty": False, "document": "", "group": 1,
        })
        assert idx == 1
        new_tabs = ctx.set_tabs.call_args[0][0]
        assert len(new_tabs) == 2
        assert new_tabs[1]["group"] == 1
        assert "_tid" in new_tabs[1]
        # 激活到右组
        ctx.set_active_index_right.assert_called_once_with(1)

    def test_bump_tab_session_by_tab_group(self):
        """bump_tab_session 按标签所属组递增对应会话计数。"""
        tabs = [_make_tab("a", group=0), _make_tab("r1", group=1)]
        ctx = _make_ctx(tabs, active_index=0, split_editor=True, active_pane=0,
                        active_index_left=0, active_index_right=1)
        cbs = build_tab_management(ctx)
        cbs["bump_tab_session"](1)  # 右组标签
        ctx.set_session_right.assert_called_once_with(1)
        ctx.set_session_left.assert_not_called()
        cbs["bump_tab_session"](0)  # 左组标签
        ctx.set_session_left.assert_called_once_with(1)
