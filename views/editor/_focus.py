"""聚焦工厂（从 views/editor.py 闭包抽取）。

闭包组：_focus_cursor_field / _clear_cursor_value / _focus_math_field

跨组依赖（通过 ctx 装配槽，调用时读取）：
- cursor_li / cursor_field_ref（cursor 组共享 state/ref）
- math_focus_li / math_field_ref（math 组共享 state/ref）
- ensure_visible / line_heights_ref（scroll 组）：ListView 懒构建下视口外行
  未挂载，需滚动使其可见后重试聚焦

注：clear_value_seq 作为 use_effect 依赖数组项留在 __init__.py，
闭包体内仅引用 cursor_field_ref，不直接读取 clear_value_seq。

ListView 懒构建下的聚焦策略：
- build_controls_on_demand=True 时视口外的行未挂载到 Flutter 侧，cursor
  TextField 控件对象虽在渲染期已创建（cursor_field_ref.current 非 None），
  但 focus() 对未挂载控件静默无效。
- _focus_cursor_field 用「延迟重试」：先即时聚焦，未命中（行未构建）则等待
  外部滚动（nav/tap/jump_to 已调 ensure_visible/safe_scroll_to）构建目标行；
  仍未命中时主动调 ensure_visible（覆盖 undo/redo 无滚动场景）再重试。
  用 line_heights 缓存是否有实测高度判断行是否已构建。
- ensure_visible 仅在光标不可见时滚动，对已可见行（jump_to 已滚到顶部）空操作，
  避免与外部 to_top 滚动冲突。

依赖项：
- asyncio（重试间隔 sleep）
- contextlib（suppress 异常静默）
"""

import asyncio
import contextlib


def build_focus(ctx):
    """构造聚焦闭包组。

    返回 dict[str, Callable]：
    focus_cursor_field / clear_cursor_value / focus_math_field
    """

    async def _focus_cursor_field():
        if ctx.cursor_li is None:
            return
        # 即时聚焦（行已挂载时直接生效）
        await _try_focus()
        if _line_built():
            return
        # 行未构建：先等待外部滚动（nav/tap/jump_to 的 ensure_visible/safe_scroll_to）
        # 完成构建，再重试。避免与外部 to_top 滚动并发冲突。
        await asyncio.sleep(0.12)
        await _try_focus()
        if _line_built():
            return
        # 仍未构建：undo/redo 等无外部滚动场景，主动滚动使其可见后重试
        if ctx.ensure_visible is not None:
            ctx.ensure_visible(ctx.cursor_li)
            await asyncio.sleep(0.15)
            await _try_focus()
            if _line_built():
                return
            # 最后一次重试（长行两步滚动可能需更久）
            ctx.ensure_visible(ctx.cursor_li)
            await asyncio.sleep(0.15)
            await _try_focus()

    async def _try_focus():
        field = ctx.cursor_field_ref.current
        if field is not None:
            with contextlib.suppress(Exception):
                await field.focus()

    def _line_built() -> bool:
        """目标行是否已构建（line_heights 缓存有实测高度 > 0）。"""
        li = ctx.cursor_li
        if li is None:
            return False
        cache = ctx.line_heights_ref.current
        return cache.get(li, 0.0) > 0

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
