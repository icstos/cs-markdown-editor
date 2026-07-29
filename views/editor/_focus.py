"""聚焦工厂（从 views/editor.py 闭包抽取）。

闭包组：_focus_cursor_field / _clear_cursor_value / _focus_math_field

跨组依赖（通过 ctx 装配槽，调用时读取）：
- cursor_li / cursor_field_ref（cursor 组共享 state/ref）
- math_focus_li / math_field_ref（math 组共享 state/ref）

注：clear_value_seq 作为 use_effect 依赖数组项留在 __init__.py，
闭包体内仅引用 cursor_field_ref，不直接读取 clear_value_seq。

依赖项：
- contextlib（suppress 异常静默）
"""

import contextlib


def build_focus(ctx):
    """构造聚焦闭包组。

    返回 dict[str, Callable]：
    focus_cursor_field / clear_cursor_value / focus_math_field
    """

    async def _focus_cursor_field():
        if ctx.cursor_li is not None and ctx.cursor_field_ref.current is not None:
            with contextlib.suppress(Exception):
                await ctx.cursor_field_ref.current.focus()

    async def _clear_cursor_value():
        if ctx.cursor_field_ref.current is not None:
            with contextlib.suppress(Exception):
                ctx.cursor_field_ref.current.value = ""
                await ctx.cursor_field_ref.current.update()

    async def _focus_math_field():
        if ctx.math_focus_li is not None and ctx.math_field_ref.current is not None:
            with contextlib.suppress(Exception):
                await ctx.math_field_ref.current.focus()

    return {
        "focus_cursor_field": _focus_cursor_field,
        "clear_cursor_value": _clear_cursor_value,
        "focus_math_field": _focus_math_field,
    }
