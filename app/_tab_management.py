"""标签管理控制器（从 main.py 闭包抽取）。

闭包组：cur_tab / update_active / update_tab / select_tab / cycle_tab /
do_close_many / request_close / close_tab /
save_and_close_pending / close_without_save / cancel_close

跨组依赖（通过 ctx 装配槽，调用时读取）：
- file_io_ops 组：save_doc（save_and_close_pending 逐个保存脏标签）
- 共享：tab_is_dirty（纯函数，直接导入）

设计要点：
- 所有写操作基于 tabs_ref.current 最新值计算，避免批量操作时索引漂移与
  stale 覆盖（autosave 等异步读取者依赖 tabs_ref.current 同步）。
- _update_active / _update_tab / _do_close_many 同步写 tabs_ref.current，
  供 autosave 等异步读取者立即读到最新值（不必等下一次渲染同步 ref）。
- 关闭含脏标签时统一走 confirm_close 弹层确认，避免误丢数据。

依赖项：
- parser（空文档回退）
- app._tab_helpers.tab_is_dirty
"""

import parser
from app._tab_helpers import tab_is_dirty


def build_tab_management(ctx):
    """构造标签管理控制器闭包组。

    返回 dict[str, Callable]：
    cur_tab / update_active / update_tab / select_tab / cycle_tab /
    do_close_many / request_close / close_tab /
    save_and_close_pending / close_without_save / cancel_close
    """

    def cur_tab():
        """从 ref 读取最新激活标签（异步场景使用）。"""
        ts = ctx.tabs_ref.current
        ai = ctx.active_index_ref.current
        if ts and 0 <= ai < len(ts):
            return ts[ai]
        return ts[0] if ts else None

    def update_active(**changes):
        """不可变更新当前激活标签字段，触发 tabs 重渲染。

        同步写 tabs_ref.current，供 autosave 等异步读取者立即读到最新值
        （不必等下一次渲染同步 ref）。
        """
        new_tabs = list(ctx.tabs)
        if not (0 <= ctx.active_index < len(new_tabs)):
            return
        new_tabs[ctx.active_index] = {**new_tabs[ctx.active_index], **changes}
        ctx.set_tabs(new_tabs)
        ctx.tabs_ref.current = new_tabs

    def update_tab(tab_index: int, **changes):
        """不可变更新指定索引标签字段。"""
        new_tabs = list(ctx.tabs)
        if not (0 <= tab_index < len(new_tabs)):
            return
        new_tabs[tab_index] = {**new_tabs[tab_index], **changes}
        ctx.set_tabs(new_tabs)
        ctx.tabs_ref.current = new_tabs

    def select_tab(index: int):
        if index == ctx.active_index:
            return
        if not (0 <= index < len(ctx.tabs)):
            return
        ctx.set_active_index(index)
        ctx.set_session(ctx.session + 1)

    def cycle_tab(direction: int):
        """Ctrl+Tab / Ctrl+Shift+Tab 循环切换标签。"""
        n = len(ctx.tabs_ref.current)
        if n <= 1:
            return
        cur = ctx.active_index_ref.current
        nxt = (cur + direction) % n
        select_tab(nxt)

    def do_close_many(indices):
        """一次性移除多个标签（避免逐个 set_tabs 的索引漂移与 stale 覆盖）。

        基于 tabs_ref.current 最新值计算，空列表回退为一个空白标签。
        """
        ts = list(ctx.tabs_ref.current)
        remove_set = {i for i in indices if 0 <= i < len(ts)}
        if not remove_set:
            return
        new_tabs = [t for i, t in enumerate(ts) if i not in remove_set]
        if not new_tabs:
            new_tabs = [
                {"document": parser.parse_markdown(""), "file_path": None, "dirty": False}
            ]
        cur_active = ctx.active_index_ref.current
        removed_before = sum(1 for i in remove_set if i < cur_active)
        if cur_active in remove_set:
            new_active = min(max(cur_active - removed_before, 0), len(new_tabs) - 1)
        else:
            new_active = cur_active - removed_before
        ctx.set_tabs(new_tabs)
        ctx.tabs_ref.current = new_tabs
        ctx.set_active_index(new_active)
        ctx.active_index_ref.current = new_active
        ctx.set_session(ctx.session + 1)

    def request_close(targets):
        """请求关闭一批标签：干净标签直接关，含脏标签则弹统一确认。"""
        ts = ctx.tabs_ref.current
        valid = [i for i in targets if 0 <= i < len(ts)]
        if not valid:
            return
        if any(tab_is_dirty(ts[i]) for i in valid):
            ctx.set_confirm_close(valid)
        else:
            do_close_many(valid)

    def close_tab(index: int):
        """关闭单个标签：脏标签走确认，干净标签直接关。"""
        request_close([index])

    async def save_and_close_pending():
        """确认弹层「保存并关闭」：逐个保存脏标签，全部成功后关闭整批。

        任一保存被用户在另存对话框取消或失败则中止，保留未关闭标签。
        """
        pending = ctx.confirm_close
        if not pending:
            return
        for idx in list(pending):
            ts = ctx.tabs_ref.current
            if 0 <= idx < len(ts) and tab_is_dirty(ts[idx]):
                ok = await ctx.save_doc(idx)
                if not ok:
                    ctx.set_confirm_close(None)
                    return
        targets = list(pending)
        ctx.set_confirm_close(None)
        do_close_many(targets)

    def close_without_save():
        """确认弹层「不保存」：直接关闭整批待确认标签。"""
        pending = ctx.confirm_close
        if not pending:
            return
        targets = list(pending)
        ctx.set_confirm_close(None)
        do_close_many(targets)

    def cancel_close():
        ctx.set_confirm_close(None)

    return {
        "cur_tab": cur_tab,
        "update_active": update_active,
        "update_tab": update_tab,
        "select_tab": select_tab,
        "cycle_tab": cycle_tab,
        "do_close_many": do_close_many,
        "request_close": request_close,
        "close_tab": close_tab,
        "save_and_close_pending": save_and_close_pending,
        "close_without_save": close_without_save,
        "cancel_close": cancel_close,
    }
