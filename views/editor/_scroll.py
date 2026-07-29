"""滚动 / 导航 / 布局命中工厂（从 views/editor.py 闭包抽取）。

闭包组：_on_scroll / _get_scroll_state / _scroll_to_offset / _on_content_resize /
on_line_size_change / _estimate_line_height / _estimate_line_offset / _safe_scroll_to /
_ensure_visible / jump_to / _hit_test_line_x / _get_layout_cache / _hit_test_xy /
_page_vlines / page_up / page_down / _scroll_by_page / _reset_line_heights /
_get_cursor_row_col / _build_highlight_map

跨组依赖（通过 ctx 装配槽，调用时读取）：
- cursor_base（cursor 组）：_ensure_visible 读取
- cursor_vline_info（navigation 组）：_ensure_visible 读取
- set_cursor（cursor 组）：jump_to 调用
- move_vline（navigation 组）：page_up / page_down 调用
- set_cursor_li / set_cursor_line / set_flash_li（state setters）：jump_to 调用

依赖项：
- asyncio（scroll_to 协程 await / sleep）
- flet（ft.context.page.run_task 调度协程）
- models（BlockType：块类型判定）
- styles（FONT_MAIN / block_text_size：字号与行高估算）
- utils.segment_helpers（is_fence / line_raw：行级判定与源码）
- views._editor_helpers（_build_offset_prefix / _build_highlight_map 纯函数实现）

设计要点：
- 行高估算（_estimate_line_height）：优先用 on_size_change 上报的实测高度，
  未命中时按块类型估算（CODE 工具栏 + 代码行 / 围栏块占位 / 普通行软换行）。
- 前缀和缓存（_estimate_line_offset）：O(n) 一次性构建，后续 O(1) 查表；
  行高变化（on_line_size_change）或行数变化（_reset_line_heights）时失效。
- 两步滚动（_safe_scroll_to to_top=True）：视口外未构建行无实测高度，
  先估算滚动触发构建，等一帧后用实测高度精确贴顶。
- vline 级精确滚动：用光标在行内 Y 偏移定位到具体视觉行（软换行长行只滚到光标所在行）。
- LineLayoutCache 惰性构建：首次 pan 事件触发，行数变化时清空重建。
"""

import asyncio
import contextlib

import flet as ft

from models import BlockType
from styles import FONT_MAIN, block_text_size
from utils.segment_helpers import is_fence as _is_fence
from utils.segment_helpers import line_raw as _line_raw
from views._editor_helpers import (
    _build_highlight_map as _build_highlight_map_impl,
)
from views._editor_helpers import _build_offset_prefix


def build_scroll(ctx):
    """构造滚动 / 导航 / 布局命中闭包组。

    返回 dict[str, Callable]：
    on_scroll / get_scroll_state / scroll_to_offset / on_content_resize /
    on_line_size_change / estimate_line_height / estimate_line_offset /
    safe_scroll_to / ensure_visible / jump_to / hit_test_line_x /
    get_layout_cache / hit_test_xy / page_vlines / page_up / page_down /
    scroll_by_page / reset_line_heights / get_cursor_row_col / build_highlight_map
    """

    def _on_scroll(e):
        try:
            ctx.scroll_offset_ref.current = e.pixels
            if hasattr(e, "max_scroll_extent"):
                ctx.max_scroll_ref.current = e.max_scroll_extent
            if hasattr(e, "viewport_dimension"):
                ctx.viewport_h_ref.current = e.viewport_dimension
            # diff 对比模式滚动同步：上报当前滚动位置，main.py 据此驱动另一侧
            if ctx.on_scroll_change is not None:
                ctx.on_scroll_change(
                    ctx.scroll_offset_ref.current,
                    ctx.max_scroll_ref.current,
                    ctx.viewport_h_ref.current,
                )
        except Exception:
            pass

    def _get_scroll_state() -> tuple[float, float, float]:
        """返回当前滚动状态 (offset, max_scroll_extent, viewport_height)。"""
        return (
            ctx.scroll_offset_ref.current,
            ctx.max_scroll_ref.current,
            ctx.viewport_h_ref.current,
        )

    def _scroll_to_offset(offset: float) -> None:
        """同步调度异步 scroll_to(offset, duration=0)。

        duration=0 即时跳转，跟随滚轮无动画延迟（diff 同步滚动专用）。
        对外非阻塞：内部用 page.run_task 调度协程。
        """
        page = ft.context.page

        async def _do():
            if ctx.list_view_ref.current is None:
                return
            with contextlib.suppress(Exception):
                await ctx.list_view_ref.current.scroll_to(offset, duration=0)

        if page is not None:
            page.run_task(_do)

    def _on_content_resize(e):
        """内容 Container 尺寸变化回调：跟踪视口宽度，实现段落自适应换行。

        程序尺寸变化时 Container 宽度变化 → set_viewport_w 触发重渲染 →
        content_width 重算为 min(视口宽度, content_max_width) → 文本按新宽度换行。
        去重：宽度变化 >1px 才更新 state，避免 sub-pixel 抖动引发频繁重渲染。
        """
        try:
            new_w = float(e.width) if e.width else 0.0
            if new_w > 0 and abs(new_w - ctx.viewport_w_ref.current) > 1:
                ctx.viewport_w_ref.current = new_w
                ctx.set_viewport_w(new_w)
        except Exception:
            pass

    def on_line_size_change(li: int, height: float):
        """LineView on_size_change 回调：缓存行实际渲染高度。

        容差 0.5px 内不更新，避免 layout 抖动引发无效写入。缓存供
        _estimate_line_offset 精确累加滚动偏移，未命中的行回退到估算。
        行高变化时同步失效前缀和缓存（offset_prefix_ref）。
        """
        cache = ctx.line_heights_ref.current
        if height > 0 and abs(cache.get(li, 0.0) - height) > 0.5:
            cache[li] = height
            ctx.offset_prefix_ref.current = None  # 失效前缀和缓存

    def _estimate_line_height(li: int) -> float:
        """估算单行渲染高度（px）。优先用 on_size_change 上报的实际高度。

        未命中缓存（行未构建或已被销毁）时按块类型估算：
        - CODE：头部工具栏 + 每代码行 + padding（多行内容必须计入）
        - 围栏块（MATH/HR/TOC/TABLE）：占位单行高
        - 普通行：num_vlines × text_h + padding（软换行变量高度）
          · 优先用 LineLayoutCache 的精确视觉行数
          · 回退：measure_text_width(raw) / wrap_width 估算
        """
        cache = ctx.line_heights_ref.current
        cached = cache.get(li)
        if cached is not None and cached > 0:
            return cached
        if not (0 <= li < len(ctx.document.lines)):
            return ctx.body_font_size * ctx.line_height + 4
        line = ctx.document.lines[li]
        base = block_text_size(line.block_type, line.level)
        if line.block_type == BlockType.CODE:
            code = line.segments[0].text if line.segments else ""
            code_lines = max(1, code.count("\n") + 1)
            # 头部工具栏(~28) + 代码行(14×line_height) + 容器 padding(~12)
            return 28 + code_lines * 14 * ctx.line_height + 12
        # 围栏块（MATH/HR/TOC/TABLE）：占位单行高（实际高度由原生控件决定）
        if line.block_type in (BlockType.MATH, BlockType.HR, BlockType.TOC, BlockType.TABLE):
            return base * ctx.line_height + 4
        # 普通行：估算视觉行数（软换行）
        # 优先用 LineLayoutCache 的精确视觉行数
        layout_cache = ctx.layout_cache_ref.current
        if layout_cache is not None:
            layout = layout_cache.get(li)
            if layout is not None:
                return layout.num_vlines * base * ctx.line_height + 4
        # 回退：用 measure_text_width 估算视觉行数
        from utils.text_layout import measure_text_width
        from views.pixel_layout import _block_padding, _compute_wrap_width
        _, _, left_pad = _block_padding(line)
        cw = ctx.content_width if ctx.content_width is not None else float("inf")
        wrap_width = _compute_wrap_width(cw, left_pad)
        raw = _line_raw(line)
        if wrap_width != float("inf") and raw:
            est_w = measure_text_width(raw, FONT_MAIN, base)
            num_vlines = max(1, int(est_w / wrap_width) + 1)
        else:
            num_vlines = 1
        return num_vlines * base * ctx.line_height + 4

    def _estimate_line_offset(li: int) -> float:
        """累加 0..li 行高，得到目标行顶部的 y 偏移（相对 ListView 内容起点）。

        比旧的 li × (目标行字号 × line_height + 4) 估算准确得多：
        - 各行按自身字号/块类型累加，而非统一用目标行字号
        - 已构建行用实测高度，消除代码块/长段落换行/表格的估算偏差

        性能优化：前缀和缓存——首次调用 O(n) 构建前缀和数组，后续调用 O(1) 查表。
        原先每次调用都 O(li) 逐行累加，光标在第 500 行时每次导航需 500 次高度查找。
        缓存在行高变化（on_line_size_change）或行数变化（reset_line_heights）时失效。
        """
        n = len(ctx.document.lines)
        prefix = ctx.offset_prefix_ref.current
        if prefix is None or len(prefix) != n + 1:
            # 前缀和构建已抽取到 _editor_helpers._build_offset_prefix（可单测）
            prefix = _build_offset_prefix([_estimate_line_height(j) for j in range(n)])
            ctx.offset_prefix_ref.current = prefix
        if 0 <= li <= n:
            return prefix[li]
        return prefix[n] if n > 0 else 0.0

    async def _safe_scroll_to(li: int, to_top: bool = False,
                              cursor_y_in_line: float = 0.0):
        """异步滚动：Flet 的 scroll_to 是协程，需 await。

        Args:
            li: 目标行索引
            to_top: True=滚动到视口顶部（大纲跳转），False=仅在不可见时滚动（光标导航）
            cursor_y_in_line: 光标在行内的 Y 偏移（vline_idx * text_h），
                to_top=False 时用于 vline 级精确滚动（软换行：长行只滚到光标所在视觉行）

        两步滚动（to_top=True 且目标行未构建时）：
        build_controls_on_demand 下视口外的行尚未构建，无实测高度缓存。
        第一步用估算 offset 滚动到目标附近，触发 ListView 构建目标行并经
        on_size_change 上报实测高度到 line_heights_ref；等待一帧后第二步
        用缓存中的实测高度重新累加 offset，精确滚到视口顶部。这样首次点击
        大纲项即可贴顶，无需第二次点击。缓存已命中时一步到位。
        """
        if ctx.list_view_ref.current is None:
            return
        try:
            top_padding = ctx.content_padding_top
            if to_top:
                cache = ctx.line_heights_ref.current
                already_built = cache.get(li, 0.0) > 0
                est_y = _estimate_line_offset(li)
                target_scroll = max(0, est_y - top_padding)
                if already_built:
                    # 缓存命中：目标行已构建，一步精确到位
                    await ctx.list_view_ref.current.scroll_to(target_scroll, duration=200)
                    return
                # 缓存未命中：第一步估算滚动，触发目标行动态构建
                await ctx.list_view_ref.current.scroll_to(target_scroll, duration=150)
                # 等待 Flutter layout 完成 + on_size_change 上报实测高度
                await asyncio.sleep(0.15)
                # 第二步：用缓存中的实测高度重新累加，精确贴顶
                precise_y = _estimate_line_offset(li)
                precise_scroll = max(0, precise_y - top_padding)
                if abs(precise_scroll - target_scroll) > 4:
                    await ctx.list_view_ref.current.scroll_to(
                        precise_scroll, duration=120
                    )
            else:
                # vline 级精确滚动：用光标在行内的 Y 偏移定位到具体视觉行
                line_y = _estimate_line_offset(li)
                cursor_abs_y = line_y + cursor_y_in_line
                text_h = ctx.body_font_size * ctx.line_height  # 单视觉行高
                viewport = ctx.viewport_h_ref.current or 600
                cur = ctx.scroll_offset_ref.current
                if cursor_abs_y < cur + 40:
                    await ctx.list_view_ref.current.scroll_to(
                        max(0, cursor_abs_y - 40), duration=100
                    )
                elif cursor_abs_y + text_h > cur + viewport - 40:
                    await ctx.list_view_ref.current.scroll_to(
                        max(0, cursor_abs_y + text_h - viewport + 40), duration=100
                    )
        except Exception:
            pass

    def _ensure_visible(li: int):
        """确保光标所在视觉行可见（vline 级精确滚动）。

        计算光标在行内的 Y 偏移（vline_idx × text_h），传给 _safe_scroll_to
        实现软换行场景下的精确滚动：长行只滚到光标所在视觉行，而非整行顶部。
        """
        page = ft.context.page
        if page is not None:
            cursor_y_in_line = 0.0
            if 0 <= li < len(ctx.document.lines):
                off = ctx.cursor_base()
                info = ctx.cursor_vline_info(li, off)
                if info is not None:
                    _, vline, _ = info
                    base = block_text_size(
                        ctx.document.lines[li].block_type, ctx.document.lines[li].level
                    )
                    cursor_y_in_line = vline.vline_idx * base * ctx.line_height
            page.run_task(_safe_scroll_to, li, cursor_y_in_line=cursor_y_in_line)

    def _hit_test_line_x(li: int, x: float) -> int:
        """跨行拖拽用：返回目标行 raw 偏移。"""
        if not (0 <= li < len(ctx.document.lines)):
            return 0
        from views.pixel_layout import hit_test_line_x
        line = ctx.document.lines[li]
        base = block_text_size(line.block_type, line.level)
        return hit_test_line_x(line, x, base)

    def _get_layout_cache():
        """惰性构建 LineLayoutCache：跨行拖拽精确命中。

        首次 pan 事件触发构建，行数变化时由 reset_line_heights 清空（置 None），
        下次 pan 事件重新构建。典型文档（数百行）单次构建约 10-30ms，可接受。
        """
        if ctx.layout_cache_ref.current is None:
            from views.pixel_layout import LineLayoutCache
            ctx.layout_cache_ref.current = LineLayoutCache(
                ctx.document.lines, ctx.content_width, ctx.line_height
            )
        return ctx.layout_cache_ref.current

    def _hit_test_xy(anchor_li: int, x: float, y: float) -> tuple[int, int] | None:
        """跨行拖拽精确命中：LineLayoutCache.hit_test 透传。

        anchor_li：拖拽起始行（pan 事件来源行的 line_idx）
        x/y：GestureDetector 局部坐标（相对 anchor_li 行内容左上角）

        坐标系换算：
        - LineLayoutCache 的 Y 是整文档坐标（top=0 = 文档第一行顶）
        - GestureDetector 局部 Y 是相对 anchor_li 行内容顶
        - 加上 anchor_li 的 layout.text_top 即得到整文档 Y
        - 同理 X 加上 anchor_li 的 left_pad 得到整文档 X

        返回 (target_li, target_raw_off) | None（cache 未就绪或越界）
        """
        cache = _get_layout_cache()
        layout = cache.get(anchor_li)
        if layout is None:
            return None
        # GestureDetector 局部坐标 → 整文档坐标
        doc_x = x + layout.left_pad
        doc_y = y + layout.text_top
        return cache.hit_test(doc_x, doc_y)

    def _page_vlines() -> int:
        """每页视觉行数：视口高度 / 单视觉行高（软换行按视觉行翻页）。"""
        viewport = ctx.viewport_h_ref.current or 600
        text_h = ctx.body_font_size * ctx.line_height
        return max(1, int(viewport / text_h))

    def page_up():
        if ctx.cursor_li is not None:
            ctx.move_vline(-1, _page_vlines())
        else:
            page = ft.context.page
            if page is not None:
                page.run_task(_scroll_by_page, -1)

    def page_down():
        if ctx.cursor_li is not None:
            ctx.move_vline(1, _page_vlines())
        else:
            page = ft.context.page
            if page is not None:
                page.run_task(_scroll_by_page, 1)

    async def _scroll_by_page(direction: int):
        if ctx.list_view_ref.current is None:
            return
        try:
            delta = direction * (ctx.viewport_h_ref.current or 600) * 0.9
            await ctx.list_view_ref.current.scroll_to(
                ctx.scroll_offset_ref.current + delta, duration=100
            )
        except Exception:
            pass

    def jump_to(li: int):
        if not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if _is_fence(line):
            ctx.set_cursor_line(li)
            ctx.set_cursor_li(None)
        else:
            ctx.set_cursor(li, 0)
        # 跳转目标行脉冲高亮：置 flash_li 触发重渲染，1.2s 后异步清回 -1 淡出
        ctx.set_flash_li(li)
        page = ft.context.page
        if page is not None:
            page.run_task(_safe_scroll_to, li, to_top=True)

            async def _clear_flash():
                await asyncio.sleep(1.2)
                ctx.set_flash_li(-1)

            page.run_task(_clear_flash)

    def _get_cursor_row_col() -> tuple[int, int]:
        if ctx.cursor_li is not None and 0 <= ctx.cursor_li < len(ctx.document.lines):
            return (ctx.cursor_li + 1, ctx.cursor_off + 1)
        return (ctx.cursor_line + 1, 1)

    def _build_highlight_map() -> dict[int, tuple[int, int]]:
        # 纯计算已抽取到 _editor_helpers（接收 lines + outward_sel 参数）
        return _build_highlight_map_impl(ctx.document.lines, ctx.outward_sel)

    def _reset_line_heights():
        ctx.line_heights_ref.current = {}
        ctx.layout_cache_ref.current = None
        ctx.offset_prefix_ref.current = None  # 失效前缀和缓存

    return {
        "on_scroll": _on_scroll,
        "get_scroll_state": _get_scroll_state,
        "scroll_to_offset": _scroll_to_offset,
        "on_content_resize": _on_content_resize,
        "on_line_size_change": on_line_size_change,
        "estimate_line_height": _estimate_line_height,
        "estimate_line_offset": _estimate_line_offset,
        "safe_scroll_to": _safe_scroll_to,
        "ensure_visible": _ensure_visible,
        "jump_to": jump_to,
        "hit_test_line_x": _hit_test_line_x,
        "get_layout_cache": _get_layout_cache,
        "hit_test_xy": _hit_test_xy,
        "page_vlines": _page_vlines,
        "page_up": page_up,
        "page_down": page_down,
        "scroll_by_page": _scroll_by_page,
        "reset_line_heights": _reset_line_heights,
        "get_cursor_row_col": _get_cursor_row_col,
        "build_highlight_map": _build_highlight_map,
    }
