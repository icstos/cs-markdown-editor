"""键盘事件工厂（从 views/editor.py 闭包抽取）。

闭包组：_on_key_down / _on_key_up

跨组依赖（通过 ctx 装配槽，调用时读取）：
- shift_pressed_ref / ctrl_pressed_ref（修饰键状态 ref）
- table_focus_ref / table_nav_ref（表格聚焦状态与导航回调 ref）

依赖项：
- 无模块级 helper
"""


def build_key(ctx):
    """构造键盘事件闭包组。

    返回 dict[str, Callable]：
    on_key_down / on_key_up
    """

    def _on_key_down(e):
        key = (getattr(e, "key", "") or "").lower()
        if key.startswith("shift"):
            ctx.shift_pressed_ref.current = True
        if key.startswith("control"):
            ctx.ctrl_pressed_ref.current = True
        # 表格 Tab/Escape/方向键路由（table_focus_ref 修复后此块真正生效）
        if ctx.table_focus_ref.current is not None and ctx.table_nav_ref.current is not None:
            if key == "tab" and not ctx.ctrl_pressed_ref.current:
                ctx.table_nav_ref.current("tab", -1 if ctx.shift_pressed_ref.current else 1)
                return
            if key == "escape":
                ctx.table_nav_ref.current("escape")
                return
            # 方向键单元格间导航（Excel 行为）：单行 TextField 内 ArrowUp/Down 无意义，
            # 直接用于跨行导航。key 可能含空格（"Arrow Up"），去空格统一匹配。
            nk = key.replace(" ", "")
            if nk == "arrowup":
                ctx.table_nav_ref.current("up")
                return
            if nk == "arrowdown":
                ctx.table_nav_ref.current("down")
                return
        # 行内格式快捷键由 KeyDispatcher 统一分发（支持自定义键位、原生控件聚焦检测），
        # 此处不再重复分发——避免双重触发导致 toggle wrap→unwrap 抵消（闪烁后无效果）。

    def _on_key_up(e):
        key = (getattr(e, "key", "") or "").lower()
        if key.startswith("shift"):
            ctx.shift_pressed_ref.current = False
        if key.startswith("control"):
            ctx.ctrl_pressed_ref.current = False

    return {
        "on_key_down": _on_key_down,
        "on_key_up": _on_key_up,
    }
