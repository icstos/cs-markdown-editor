"""键盘事件工厂（从 views/editor.py 闭包抽取）。

闭包组：_on_key_down / _on_key_up / _on_key_repeat

跨组依赖（通过 ctx 装配槽，调用时读取）：
- shift_pressed_ref / ctrl_pressed_ref（修饰键状态 ref）
- table_focus_ref / table_nav_ref（表格聚焦状态与导航回调 ref）
- arrow_repeat_ref（上/下键自驱动重复标志：_on_key_up 设 None 停止）

长按方向键重复导航设计：
- 左/右：客户端 KeyboardListener key_repeat 驱动（TextField 不拦截左/右键）
- 上/下：页面级 KeyDispatcher 自驱动定时器（TextField 的 ignore_up_down_keys
  返回 handled 在焦点链叶子层吞掉上/下键 → KeyboardListener 永远收不到；
  KeyDispatcher 用 HardwareKeyboard 全局处理器，在焦点分发前看到所有 KeyDown）
- 停止信号：KeyUp（TextField 不拦截 KeyUp → KeyboardListener 收到 → 此处设 None）

依赖项：
- 无模块级 helper
"""

# 长按重复导航的方向键
_REPEAT_NAV_KEYS = ("arrowleft", "arrowright", "arrowup", "arrowdown")


def build_key(ctx):
    """构造键盘事件闭包组。

    返回 dict[str, Callable]：on_key_down / on_key_up / on_key_repeat
    """

    def _on_key_down(e):
        key = (getattr(e, "key", "") or "").lower()
        if key.startswith("shift"):
            ctx.shift_pressed_ref.current = True
        if key.startswith("control"):
            ctx.ctrl_pressed_ref.current = True
        if key.startswith("alt"):
            ctx.alt_pressed_ref.current = True
        # 表格 Tab/Escape/方向键路由
        if ctx.table_focus_ref.current is not None and ctx.table_nav_ref.current is not None:
            if key == "tab" and not ctx.ctrl_pressed_ref.current:
                ctx.table_nav_ref.current("tab", -1 if ctx.shift_pressed_ref.current else 1)
                return
            if key == "escape":
                ctx.table_nav_ref.current("escape")
                return
            nk = key.replace(" ", "")
            if nk == "arrowup":
                ctx.table_nav_ref.current("up")
                return
            if nk == "arrowdown":
                ctx.table_nav_ref.current("down")
                return
        # 多光标：Escape 清空所有副光标
        if key == "escape" and ctx.secondary_cursors_ref.current:
            ctx.clear_secondary_cursors()
            return

    def _on_key_up(e):
        key = (getattr(e, "key", "") or "").lower()
        if key.startswith("shift"):
            ctx.shift_pressed_ref.current = False
        if key.startswith("control"):
            ctx.ctrl_pressed_ref.current = False
        if key.startswith("alt"):
            ctx.alt_pressed_ref.current = False
        # 释放方向键 → 停止上/下键自驱动重复（KeyDispatcher 的定时器读此标志）
        nk = key.replace(" ", "")
        if nk in _REPEAT_NAV_KEYS and ctx.arrow_repeat_ref is not None:
            ctx.arrow_repeat_ref.current = None

    def _on_key_repeat(e):
        """长按左/右键：持续移动光标。

        上/下键的 key_repeat 由 KeyDispatcher 的定时器自驱动（TextField 的
        ignore_up_down_keys 在焦点链叶子层吞掉上/下键，KeyboardListener 收不到；
        KeyDispatcher 用全局 HardwareKeyboard 处理器驱动），此处仅处理左/右。
        """
        key = ((getattr(e, "key", "") or "").lower()).replace(" ", "")
        if key not in _REPEAT_NAV_KEYS:
            return
        if key in ("arrowup", "arrowdown"):
            return  # 由 KeyDispatcher 定时器驱动
        actions = ctx.nav_ref.current
        if actions is None:
            return
        if getattr(actions, "cursor_li", None) is None:
            return
        if (
            (getattr(actions, "code_focus_ref", None) is not None
             and actions.code_focus_ref.current is not None)
            or (getattr(actions, "table_focus_ref", None) is not None
                and actions.table_focus_ref.current is not None)
            or (getattr(actions, "math_focus_ref", None) is not None
                and actions.math_focus_ref.current is not None)
        ):
            return
        shift = bool(ctx.shift_pressed_ref.current) if ctx.shift_pressed_ref else False
        outward = getattr(actions, "outward_sel", None)
        if outward is not None and not getattr(actions, "raw_mode", False):
            if shift:
                handler = {
                    "arrowleft": getattr(actions, "extend_outward_left", None),
                    "arrowright": getattr(actions, "extend_outward_right", None),
                }[key]
                if handler is not None:
                    handler()
            elif actions.clear_outward_sel is not None:
                actions.clear_outward_sel()
            return
        multi = (
            getattr(actions, "has_secondary_cursors", None) is not None
            and actions.has_secondary_cursors()
        )
        if key == "arrowleft":
            if shift:
                if multi and actions.extend_selection_left is not None:
                    actions.extend_selection_left()
                elif actions.extend_outward_left is not None:
                    actions.extend_outward_left()
            else:
                actions.move_left()
            return
        if key == "arrowright":
            if shift:
                if multi and actions.extend_selection_right is not None:
                    actions.extend_selection_right()
                elif actions.extend_outward_right is not None:
                    actions.extend_outward_right()
            else:
                actions.move_right()

    return {
        "on_key_down": _on_key_down,
        "on_key_up": _on_key_up,
        "on_key_repeat": _on_key_repeat,
    }
