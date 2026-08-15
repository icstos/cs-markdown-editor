r"""拆分编辑器与对比焦点视口控制器（从 main.py 闭包抽取）。

闭包组：toggle_split_editor / set_active_pane / set_diff_active_pane

跨组依赖（通过 ctx 装配槽，调用时读取）：
- tab_management 组：append_and_activate（拆分时右侧追加空白标签）
- 共享：group_indices / is_blank_untitled / tab_group（纯函数，直接导入）

设计要点：
- 拆分编辑组（VSCode 风格 Ctrl+\）：左右两组各自独立标签列表（tab.group
  0=左 / 1=右），可打开不同文件；标签行与编辑区同步分左右。
- 开启：右组追加一个空白未命名标签（用户在其上打开文件），焦点切到右组
  （VSCode「向右拆分」聚焦新组）。
- 关闭：右组空白标签直接丢弃；非空白标签合并回左组（不丢数据），左组
  激活与编辑器状态不变，焦点回左组。
- 对比标签与拆分互斥：对比标签激活时禁用拆分切换。
- set_active_pane / set_diff_active_pane 同值不重渲染，并同步写 ref，
  使键盘事件路由（_get_active_nav）立即读到最新焦点视口。

依赖项：
- parser（空白文档）
- app._tab_helpers（is_blank_untitled / tab_group）
"""

import parser
from app._tab_helpers import is_blank_untitled, tab_group


def build_split_editor(ctx):
    """构造拆分编辑器与对比焦点视口控制器闭包组。

    返回 dict[str, Callable]：
    toggle_split_editor / set_active_pane / set_diff_active_pane
    """
    # 捕获原始 state setter：装配后 ctx.set_active_pane / set_diff_active_pane
    # 会被本控制器返回值覆盖（ctx 装配槽与 state setter 同名）。若闭包运行时
    # 再读 ctx.set_active_pane 会递归调用自身（RecursionError）。ctx 每次渲染
    # 重建、构造时槽位仍为原始 setter，构造期捕获即安全且稳定。
    _set_active_pane_state = ctx.set_active_pane
    _set_diff_pane_state = ctx.set_diff_active_pane

    def _focus_pane(pane: int):
        if ctx.active_pane_ref.current != pane:
            _set_active_pane_state(pane)
            ctx.active_pane_ref.current = pane

    def _split_on():
        """开启拆分：右组打开当前文件副本，与源共享同一 Document 对象。

        VSCode「向右拆分」直觉：拆分即复制当前编辑上下文到右侧。两侧绑定
        同一 document（@ft.observable）——任一侧编辑实时同步到另一侧；
        光标/滚动/撤销历史是编辑器内部状态，仍各自独立。源标签的
        dirty / mtime 一并带入副本。无有效源标签（防御）时右组空白标签。
        """
        ctx.set_split_editor(True)
        ts = ctx.tabs_ref.current
        gi = (ctx.active_index_right_ref if ctx.active_pane_ref.current == 1
              else ctx.active_index_left_ref).current
        src = ts[gi] if 0 <= gi < len(ts) else None
        if src is not None and src.get("type") != "diff" and src.get("document") is not None:
            ctx.append_and_activate({
                "document": src["document"],  # 共享同一对象 → 左右实时同步
                "file_path": src.get("file_path"),
                "dirty": bool(src.get("dirty")),
                "_last_known_mtime": src.get("_last_known_mtime"),
                "group": 1,
            })
        else:
            ctx.append_and_activate({
                "document": parser.parse_markdown(""),
                "file_path": None,
                "dirty": False,
                "group": 1,
            })
        _focus_pane(1)

    def _split_off():
        """关闭拆分：右组空白丢弃、非空白合并回左组，焦点回左组。

        左组激活标签对象不变（合并只改右组标签的 group 字段 / 丢弃右组
        空白），左编辑器 key 不变 → 光标/滚动不重置。
        """
        old_tabs = list(ctx.tabs_ref.current)
        old_left = ctx.active_index_left_ref.current
        left_tab = old_tabs[old_left] if 0 <= old_left < len(old_tabs) else None
        new_tabs = []
        for t in old_tabs:
            if tab_group(t) != 1:
                new_tabs.append(t)
            elif not is_blank_untitled(t):
                new_tabs.append({**t, "group": 0})  # 保留用户数据，并入左组
            # 右组空白未命名标签：直接丢弃
        if not new_tabs:  # 防御：左组也为空（正常流程不可达）
            new_tabs = [{
                "document": parser.parse_markdown(""), "file_path": None,
                "dirty": False, "group": 0,
            }]
            left_tab = new_tabs[0]
        # 按对象身份重新定位左组激活（丢弃右组空白可能使索引漂移）
        new_left = 0
        if left_tab is not None:
            for j, t in enumerate(new_tabs):
                if t is left_tab:
                    new_left = j
                    break
        ctx.set_tabs(new_tabs)
        ctx.tabs_ref.current = new_tabs
        ctx.active_index_left_ref.current = new_left
        ctx.set_active_index_left(new_left)
        ctx.active_index_right_ref.current = 0
        ctx.set_active_index_right(0)
        ctx.set_split_editor(False)
        _focus_pane(0)
        ctx.set_active_index(new_left)
        ctx.active_index_ref.current = new_left
        # 左组激活未变 → 不 bump session_left；右编辑器随拆分关闭卸载
        ctx.set_session(ctx.session + 1)

    def toggle_split_editor():
        r"""向右拆分编辑器（VSCode 风格 Ctrl+\）：切换左右独立标签组。"""
        # 对比标签下禁用拆分切换：两者互斥，避免对比标签内意外进入拆分
        if ctx.is_diff_tab_ref.current:
            return
        if ctx.split_editor:
            _split_off()
        else:
            _split_on()

    def set_active_pane(pane: int):
        """切换焦点视口（点击/光标聚焦触发）。同值不重渲染。"""
        if ctx.active_pane_ref.current != pane:
            _set_active_pane_state(pane)
            ctx.active_pane_ref.current = pane

    def set_diff_active_pane(pane: int):
        """切换对比模式焦点视口（0=左, 1=右）。同值不重渲染。"""
        if ctx.diff_active_pane_ref.current != pane:
            _set_diff_pane_state(pane)
            ctx.diff_active_pane_ref.current = pane

    return {
        "toggle_split_editor": toggle_split_editor,
        "set_active_pane": set_active_pane,
        "set_diff_active_pane": set_diff_active_pane,
    }
