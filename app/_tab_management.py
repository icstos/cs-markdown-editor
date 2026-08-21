"""标签管理控制器（从 main.py 闭包抽取）。

闭包组：cur_tab / update_active / update_tab / select_tab / cycle_tab /
do_close_many / request_close / close_tab / activate_index /
append_and_activate / bump_tab_session /
save_and_close_pending / close_without_save / cancel_close

跨组依赖（通过 ctx 装配槽，调用时读取）：
- file_io_ops 组：save_doc（save_and_close_pending 逐个保存脏标签）
- 共享：tab_is_dirty / new_tab / tab_group / group_indices（纯函数，直接导入）

设计要点：
- 所有写操作基于 tabs_ref.current 最新值计算，避免批量操作时索引漂移与
  stale 覆盖（autosave 等异步读取者依赖 tabs_ref.current 同步）。
- _update_active / _update_tab / _do_close_many 同步写 tabs_ref.current，
  供 autosave 等异步读取者立即读到最新值（不必等下一次渲染同步 ref）。
- 关闭含脏标签时统一走 confirm_close 弹层确认，避免误丢数据。

拆分编辑组（VSCode 风格，group 0=左 / 1=右）：
- 不变式：active_index == 焦点侧组的激活标签全局索引；非拆分时所有标签
  属于 group 0。activate_index 是唯一激活入口（组激活 + 焦点切换 + 会话计数），
  file_io / diff / backup 的打开操作统一经 append_and_activate。
- do_close_many 按组选新激活（对象身份匹配，优先右邻居、次左邻居——
  与既有单组行为一致）；右组清空时自动收起拆分；全部清空回退空白标签。
- 仅激活标签变化的那一侧递增 session_left/right（另一侧编辑器不重置光标）；
  全局 session 仍每次递增（驱动 pending_jump 等跨组 effect）。

依赖项：
- parser（空文档回退）
- app._tab_helpers（tab_is_dirty / new_tab / tab_group / group_indices）
"""

import parser
from app._tab_helpers import group_indices, new_tab, tab_group, tab_is_dirty
from app.autosave import (
    AutosaveContext,
    autosave_all_dirty_sync,
    autosave_on_switch_enabled,
)


def build_tab_management(ctx):
    """构造标签管理控制器闭包组。

    返回 dict[str, Callable]：
    cur_tab / update_active / update_tab / select_tab / cycle_tab /
    do_close_many / request_close / close_tab / activate_index /
    append_and_activate / bump_tab_session /
    save_and_close_pending / close_without_save / cancel_close
    """

    # ---- 组内部工具（读 ref 最新值，避免闭包快照过期）----

    def _group_active_ref(g: int):
        return ctx.active_index_right_ref if g == 1 else ctx.active_index_left_ref

    def _set_group_active(g: int, index: int):
        _group_active_ref(g).current = index
        (ctx.set_active_index_right if g == 1 else ctx.set_active_index_left)(index)

    def _bump_session(g: int):
        ref = ctx.session_right_ref if g == 1 else ctx.session_left_ref
        ref.current = (ref.current or 0) + 1
        (ctx.set_session_right if g == 1 else ctx.set_session_left)(ref.current)

    def _same_tab(tabs: list, index: int, tab) -> bool:
        """index 处的标签是否就是 tab（对象身份匹配，兼容无 _tid 的旧字典）。"""
        return 0 <= index < len(tabs) and tabs[index] is tab

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

    def _current_settings():
        """读取最新设置（settings_ref 每渲染同步，避免闭包捕获过期快照）。

        用 getattr 防御：单测 Mock（SimpleNamespace）可能未注入 settings_ref。
        """
        ref = getattr(ctx, "settings_ref", None)
        if ref is not None and ref.current is not None:
            return ref.current
        return getattr(ctx, "settings", {})

    def _autosave_tabs_sync(indices: list[int] | tuple[int, ...] | range | None = None):
        """切换/关闭文档前同步自动保存（开启 auto_save + auto_save_on_switch 时）。

        同步写盘后立即更新 dirty 标记：保存成功的标签变为干净状态，
        request_close 据此直接关闭不弹确认框；未命名标签（无路径）无法
        自动保存，保持脏状态走既有确认流程。
        """
        settings = _current_settings()
        if not autosave_on_switch_enabled(settings):
            return
        try:
            autosave_all_dirty_sync(
                AutosaveContext(
                    settings=settings,
                    page_ref=ctx.page_ref,
                    tabs_ref=ctx.tabs_ref,
                    save_doc_fn=ctx.save_doc,
                    save_doc_sync_fn=ctx.save_doc_sync,
                ),
                indices=indices,
            )
        except Exception:
            pass  # 自动保存失败不阻塞切换/关闭流程

    def activate_index(index: int):
        """统一激活入口：设置所属组激活索引、切换焦点侧、递增会话计数。

        VSCode 直觉：点击/打开某组的标签即聚焦该组（active_pane 跟随）。
        同组同标签重复激活为 no-op（含焦点已在别侧的场景——此时仅切焦点）。
        仅当该组激活标签对象变化时递增该组 session（编辑器重建），全局
        session 始终递增（驱动 pending_jump effect）。
        """
        ts = ctx.tabs_ref.current
        if not (0 <= index < len(ts)):
            return
        tab = ts[index]
        g = tab_group(tab)
        old_idx = _group_active_ref(g).current
        active_changed = not _same_tab(ts, old_idx, tab)
        # 同组同标签且焦点已在该组：完全 no-op（重复点击不重渲染）
        if (
            not active_changed
            and (not ctx.split_editor or ctx.active_pane_ref.current == g)
            and ctx.active_index_ref.current == index
        ):
            return
        # 切换文档前自动保存即将离开的文档：全局焦点侧激活标签 + 目标组原激活
        # 标签（拆分下切换另一组标签时，该组被替换的文档也离开屏幕；共享同一
        # document 的副本在 autosave_all_dirty_sync 内按对象去重只保存一次）
        _autosave_tabs_sync([ctx.active_index_ref.current, old_idx])
        # 焦点切换：激活哪组就聚焦哪组（仅拆分时有意义）
        if ctx.split_editor and ctx.active_pane_ref.current != g:
            ctx.set_active_pane(g)
            ctx.active_pane_ref.current = g
        _set_group_active(g, index)
        # 不变式：active_index = 焦点侧组激活索引（焦点已随激活切换 → 即 index）
        if ctx.active_index_ref.current != index:
            ctx.set_active_index(index)
            ctx.active_index_ref.current = index
        if active_changed:
            _bump_session(g)
        ctx.set_session(ctx.session + 1)

    def select_tab(index: int):
        """点击标签切换：与 activate_index 等价（保留旧名供 TabBar 回调）。"""
        activate_index(index)

    def cycle_tab(direction: int):
        """Ctrl+Tab / Ctrl+Shift+Tab 在焦点侧组内循环切换（VSCode 组内循环）。"""
        ts = ctx.tabs_ref.current
        g = 1 if (ctx.split_editor and ctx.active_pane_ref.current == 1) else 0
        idxs = group_indices(ts, g)
        if len(idxs) <= 1:
            return
        cur = _group_active_ref(g).current
        # 当前激活不在本组（异常防御）：从头开始
        try:
            pos = idxs.index(cur)
        except ValueError:
            pos = 0
        activate_index(idxs[(pos + direction) % len(idxs)])

    def _pick_group_active(old_tabs: list, old_idx: int, removed_set: set,
                           new_tabs: list, g: int) -> int:
        """关闭后为组 g 选新激活索引：优先原激活存活 → 右邻居 → 左邻居 → 组内首个。"""
        # 原激活存活：按对象身份在新列表定位
        if 0 <= old_idx < len(old_tabs):
            old_tab = old_tabs[old_idx]
            for j, t in enumerate(new_tabs):
                if t is old_tab:
                    return j
            # 已被移除：向右找第一个存活的同组邻居，其次向左（VSCode/Chrome 右邻优先）
            fwd = bwd = None
            for k in range(old_idx + 1, len(old_tabs)):
                if k in removed_set or tab_group(old_tabs[k]) != g:
                    continue
                fwd = old_tabs[k]
                break
            for k in range(old_idx - 1, -1, -1):
                if k in removed_set or tab_group(old_tabs[k]) != g:
                    continue
                bwd = old_tabs[k]
                break
            pick = fwd if fwd is not None else bwd
            if pick is not None:
                for j, t in enumerate(new_tabs):
                    if t is pick:
                        return j
        gi = group_indices(new_tabs, g)
        return gi[0] if gi else 0

    def do_close_many(indices):
        """一次性移除多个标签（避免逐个 set_tabs 的索引漂移与 stale 覆盖）。

        基于 tabs_ref.current 最新值计算，全部标签清空时回退为一个空白标签。
        组感知：按组分别选新激活；右组清空时自动收起拆分并聚焦左组；
        仅激活标签变化的那一侧递增 session。
        """
        old_tabs = list(ctx.tabs_ref.current)
        remove_set = {i for i in indices if 0 <= i < len(old_tabs)}
        if not remove_set:
            return
        new_tabs = [t for i, t in enumerate(old_tabs) if i not in remove_set]
        if not new_tabs:
            new_tabs = [new_tab(document=parser.parse_markdown(""),
                                file_path=None, dirty=False)]
        old_left = ctx.active_index_left_ref.current
        old_right = ctx.active_index_right_ref.current
        # 右组清空：收起拆分（拆分态下右组不可为空的不变式恢复）
        right_empty = not group_indices(new_tabs, 1)
        if right_empty and ctx.split_editor:
            ctx.set_split_editor(False)
            if ctx.active_pane_ref.current == 1:
                ctx.active_pane_ref.current = 0
                ctx.set_active_pane(0)
        # 左组清空但右组非空（防御，正常流程不会出现）：右组并入左组
        if not group_indices(new_tabs, 0):
            new_tabs = [{**t, "group": 0} for t in new_tabs]
            old_left = old_right
        new_left = _pick_group_active(old_tabs, old_left, remove_set, new_tabs, 0)
        new_right = _pick_group_active(old_tabs, old_right, remove_set, new_tabs, 1)
        # 焦点侧组激活 → 全局 active_index
        pane = 1 if (ctx.split_editor and ctx.active_pane_ref.current == 1) else 0
        new_active = new_right if pane == 1 else new_left
        # 同步全部状态 + ref
        _set_group_active(0, new_left)
        _set_group_active(1, new_right)
        ctx.set_tabs(new_tabs)
        ctx.tabs_ref.current = new_tabs
        ctx.set_active_index(new_active)
        ctx.active_index_ref.current = new_active
        # 仅激活标签变化的那一侧重建编辑器（另一侧光标/滚动不重置）
        if not _same_tab(new_tabs, new_left, old_tabs[old_left] if 0 <= old_left < len(old_tabs) else None):
            _bump_session(0)
        if not _same_tab(new_tabs, new_right, old_tabs[old_right] if 0 <= old_right < len(old_tabs) else None):
            _bump_session(1)
        ctx.set_session(ctx.session + 1)

    def request_close(targets):
        """请求关闭一批标签：干净标签直接关，含脏标签则弹统一确认。

        关闭前先同步自动保存待关闭标签（开启 auto_save + auto_save_on_switch
        时）：有路径的脏标签写回原文件后变干净 → 直接关闭不弹确认；未命名
        标签（无路径）无法自动保存，保持脏状态弹确认框由用户决定。
        """
        ts = ctx.tabs_ref.current
        valid = [i for i in targets if 0 <= i < len(ts)]
        if not valid:
            return
        # 自动保存待关闭标签（同步写盘，成功后 dirty 立即清除）
        _autosave_tabs_sync(valid)
        ts = ctx.tabs_ref.current  # 重新读取（autosave 可能已更新 dirty）
        if any(tab_is_dirty(ts[i]) for i in valid):
            ctx.set_confirm_close(valid)
        else:
            do_close_many(valid)

    def close_tab(index: int):
        """关闭单个标签：脏标签走确认，干净标签直接关。"""
        request_close([index])

    def append_and_activate(fields: dict) -> int:
        """追加新标签（字段经 new_tab 规范化）并激活，返回新标签全局索引。

        file_io（打开/新建）/ diff / backup 的「新标签」统一入口：
        调用方通过 fields["group"] 指定目标组（焦点侧组），激活逻辑与
        activate_index 一致（组激活 + 焦点切换 + 会话计数）。
        """
        ts = list(ctx.tabs_ref.current)
        tab = new_tab(**fields)
        ts.append(tab)
        ctx.set_tabs(ts)
        ctx.tabs_ref.current = ts
        index = len(ts) - 1
        activate_index(index)
        return index

    def bump_tab_session(index: int):
        """标签内容被整体替换（外部修改重载等）后，重建其所属组的编辑器。"""
        ts = ctx.tabs_ref.current
        if not (0 <= index < len(ts)):
            return
        _bump_session(tab_group(ts[index]))
        ctx.set_session(ctx.session + 1)

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
        "activate_index": activate_index,
        "append_and_activate": append_and_activate,
        "bump_tab_session": bump_tab_session,
        "save_and_close_pending": save_and_close_pending,
        "close_without_save": close_without_save,
        "cancel_close": cancel_close,
    }
