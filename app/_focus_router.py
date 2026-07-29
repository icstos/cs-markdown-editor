"""焦点路由与脏状态上报控制器（从 main.py 闭包抽取）。

闭包组：get_active_nav / apply_content_layout / jump_to_line /
on_dirty_change

跨组依赖（通过 ctx 装配槽，调用时读取）：
- split_editor 组：无（get_active_nav 直接读 ref，不调用 set_*）
- settings_controller 组：schedule_autosave（on_dirty_change 触发自动保存）
- tab_management 组：update_active（on_dirty_change 更新标签 dirty）

设计要点：
- get_active_nav 统一获取当前焦点视口的 nav_ref，优先级：对比标签 >
  split_editor > 单编辑器。键盘事件、跳转、状态栏光标位置都通过此函数
  路由，避免散落的分支判断。
- on_dirty_change 仅普通编辑标签走此回调；对比标签两侧各自走
  on_diff_dirty_change。仅状态变化时写 dirty，避免每键重渲染。

依赖项：
- 无外部依赖（仅读写 ctx 装配槽）
"""


def build_focus_router(ctx):
    """构造焦点路由与脏状态上报控制器闭包组。

    返回 dict[str, Callable]：
    get_active_nav / apply_content_layout / jump_to_line / on_dirty_change
    """

    def get_active_nav():
        """统一获取当前焦点视口的 nav_ref。

        优先级：对比标签 > split_editor > 单编辑器。键盘事件、跳转、状态栏
        光标位置都通过此函数路由，避免散落的分支判断。
        """
        if ctx.is_diff_tab_ref.current:
            return ctx.diff_nav_right if ctx.diff_active_pane_ref.current == 1 else ctx.diff_nav_left
        if ctx.split_editor and ctx.active_pane_ref.current == 1:
            return ctx.nav_ref_split
        return ctx.nav_ref

    def apply_content_layout():
        page = ctx.page_ref.current
        if page is None:
            return
        page.update()

    def jump_to_line(li: int):
        # 跳转到当前焦点视口（diff / 拆分 / 单编辑器统一路由）
        active_nav = get_active_nav()
        actions = active_nav.current
        if actions is not None:
            actions.jump_to_line(li)

    def on_dirty_change(d: bool):
        """编辑器上报脏状态变化时，更新当前标签的 dirty（仅状态变化时写，避免每键重渲染）。

        仅普通编辑标签走此回调；对比标签两侧各自走 on_diff_dirty_change。
        """
        if ctx.is_diff_tab_ref.current:
            return
        if ctx.cur_tab.get("dirty") != d:
            ctx.update_active(dirty=d)
        if d:
            ctx.schedule_autosave()

    return {
        "get_active_nav": get_active_nav,
        "apply_content_layout": apply_content_layout,
        "jump_to_line": jump_to_line,
        "on_dirty_change": on_dirty_change,
    }
