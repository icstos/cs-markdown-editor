"""块级容器包裹：缩进 / 引用边框 / 当前行高亮 / 跳转脉冲 / diff 背景。

从 views/line_view.py 迁出。多个行渲染分支（普通文本行、代码块、公式块、
YAML 前置元数据）都要包同一层块级容器，而前置元数据已拆到独立模块，因此把
它下沉到本模块以避免 `line_view` ↔ `_frontmatter` 的导入环。

`base` 为必填位置参数：它是段落左侧基准内边距，所有调用点都必须显式传入。
"""

from collections.abc import Callable

import flet as ft

from models.document import BlockType, Line
from styles import Radius, Spacing, _current_colors, only_border


def wrap_block(
    content: ft.Control, line: Line, base: int, line_idx: int | None = None,
    on_click: Callable | None = None,
    is_current_line: bool = False,
    is_flash: bool = False,
    on_size_change: Callable[[int, float], None] | None = None,
    diff_mark: str | None = None,
) -> ft.Control:
    """包一层块级容器：缩进、引用边框、当前行高亮、跳转脉冲高亮、diff 背景着色。

    on_click：挂到最外层 Container 的点击回调（padding 死区兜底）。
    on_size_change：行实际渲染高度上报回调，用于精确计算滚动偏移。
        回调签名为 (line_idx, height)；仅最外层 Container 绑定，避免
        内层引用/激活态包裹容器重复触发。
    is_flash：跳转目标行脉冲高亮（淡蓝底，animate 300ms 淡入/淡出）。
        与 is_current_line 可叠加：flash 更强且 1.2s 消失，current 持续。
    diff_mark：diff 对比行标记。"added"=绿底，"removed"=红底，"modified"=浅绿底。
        作为最底层背景，与 flash/current 叠加时 diff 色在底，高亮在上。
    """
    c = _current_colors()
    pad_left = 0

    # diff 背景着色：作为最底层背景包裹（在 flash/current 之前）
    if diff_mark:
        diff_bg = {
            "added": c.diff_add_bg,
            "removed": c.diff_del_bg,
            "modified": c.diff_add_bg,
        }.get(diff_mark)
        if diff_bg:
            content = ft.Container(
                content=content,
                bgcolor=diff_bg,
                border_radius=Radius.LG,
            )

    if is_flash:
        # 跳转脉冲高亮：淡蓝底，animate 使 flash_li 清回 -1 时 bgcolor 平滑淡出
        content = ft.Container(
            content=content,
            bgcolor=ft.Colors.with_opacity(0.18, c.link),
            border_radius=Radius.LG,
            animate=ft.Animation(300, ft.AnimationCurve.EASE_OUT),
        )

    if is_current_line:
        # 当前行高亮：仅淡色背景，无蓝色竖条（保持界面简洁专业）
        # 左侧不再加 border 竖条，避免视觉噪声；与跳转脉冲高亮风格统一
        content = ft.Container(
            content=content,
            bgcolor=ft.Colors.with_opacity(0.22, c.active_bg),
            border_radius=Radius.LG,
        )

    if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O):
        pad_left = line.level * 20
    elif line.block_type == BlockType.QUOTE:
        # 多级嵌套引用：整块浅蓝背景（Typora 式柔和区分）+ 逐层包裹左侧
        # 彩色边框，颜色复用 heading_colors（红橙绿青蓝紫），与标题/大纲/
        # 列表色阶统一。最外层 = level 1 = 红，每深入一级切换下一色。
        # 边框色降不透明度至 0.5：半透明叠加背景天然去饱和，呈更浅、偏灰的
        # 柔和色调，避免高饱和色块喧宾夺主，保持界面清爽专业。
        lvl = line.level or 1
        for i in range(lvl):
            # i=0 → 最内层（最深 lvl），i=lvl-1 → 最外层（level 1）
            level = lvl - i
            base_color = c.heading_colors.get(min(level, 6), c.quote_bar)
            color = ft.Colors.with_opacity(0.5, base_color)
            kwargs_bg = {
                "bgcolor": ft.Colors.with_opacity(0.55, c.quote_bg),
            } if i == lvl - 1 else {}  # 整块浅蓝底只挂最外层，避免多层叠色变深
            content = ft.Container(
                content=content,
                padding=ft.Padding.only(left=Spacing.XL),
                border=only_border(left=ft.BorderSide(3, color)),
                **kwargs_bg,
            )

    # HR 行 padding 8+8（与 pixel_layout._block_padding HR 分支一致，保证光标 Y 对齐）
    pad_v = Spacing.LG if line.block_type == BlockType.HR else Spacing.XS
    kwargs: dict = {
        "key": f"line-{line_idx}" if line_idx is not None else None,
        "content": content,
        "padding": ft.Padding.only(left=pad_left, top=pad_v, bottom=pad_v),
        "margin": ft.Margin.all(0),
        "ink": False,
    }
    if on_click is not None:
        kwargs["on_click"] = on_click
    if on_size_change is not None and line_idx is not None:
        kwargs["on_size_change"] = lambda e, li=line_idx: on_size_change(li, e.height)
    return ft.Container(**kwargs)
