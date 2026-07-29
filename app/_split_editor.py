r"""拆分编辑器与对比焦点视口控制器（从 main.py 闭包抽取）。

闭包组：toggle_split_editor / set_active_pane / set_diff_active_pane

跨组依赖（通过 ctx 装配槽，调用时读取）：
- 无外部组调用（仅读写自身 state/ref）

设计要点：
- 拆分编辑器（VSCode 风格 Ctrl+\）与对比标签互斥：对比标签下禁用拆分切换，
  避免对比标签内意外进入拆分。
- set_active_pane / set_diff_active_pane 同值不重渲染，并同步写 ref，
  使键盘事件路由（_get_active_nav）立即读到最新焦点视口。

依赖项：
- 无外部依赖（仅读写自身 state/ref）
"""


def build_split_editor(ctx):
    """构造拆分编辑器与对比焦点视口控制器闭包组。

    返回 dict[str, Callable]：
    toggle_split_editor / set_active_pane / set_diff_active_pane
    """

    def toggle_split_editor():
        r"""向右拆分编辑器（VSCode 风格 Ctrl+\）：切换右侧第二视口，共享同一文档。"""
        # 对比标签下禁用拆分切换：两者互斥，避免对比标签内意外进入拆分
        if ctx.is_diff_tab_ref.current:
            return
        next_split = not ctx.split_editor
        ctx.set_split_editor(next_split)
        # 关闭拆分时焦点回到左侧；打开时默认焦点左侧
        ctx.set_active_pane(0)
        ctx.active_pane_ref.current = 0

    def set_active_pane(pane: int):
        """切换焦点视口（点击/光标聚焦触发）。同值不重渲染。"""
        if ctx.active_pane_ref.current != pane:
            ctx.set_active_pane(pane)
            ctx.active_pane_ref.current = pane

    def set_diff_active_pane(pane: int):
        """切换对比模式焦点视口（0=左, 1=右）。同值不重渲染。"""
        if ctx.diff_active_pane_ref.current != pane:
            ctx.set_diff_active_pane(pane)
            ctx.diff_active_pane_ref.current = pane

    return {
        "toggle_split_editor": toggle_split_editor,
        "set_active_pane": set_active_pane,
        "set_diff_active_pane": set_diff_active_pane,
    }
