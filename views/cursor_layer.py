"""Stack 顶层透明编辑层：光标承载 TextField。

为 Stack 双层架构提供"光标级"输入入口：
- 全透明背景、无边框、零内边距，绝不遮挡底层渲染层
- text_style.color = TRANSPARENT：输入字符不可见（仅作输入载体，渲染由底层
  raw_to_visible_spans 完成）
- strut_style 与渲染层 Text 共用同一实例，强制行高一致，保证光标 baseline
  与渲染层文字 baseline 像素级对齐
- 通过 Stack 内绝对定位（left/top）将光标摆到渲染层的字符间隙像素位置
- value="" 永远为空：每次输入后由 editor 端 set_nav_seq(+1) 触发 key 重建，
  Flet 销毁旧控件创建新控件，内部状态自动重置为空

定位参数（由 LineLayoutCache.cursor_px 计算）：
- cursor_px_x：光标 X（相对 Stack 左上角 = 文字左起点）
- cursor_px_y：恒为 0（Stack 高度 = text_height，文字顶 = Stack 顶）
- line_height_px：Stack 高度 = base * line_height
- cursor_h：光标高度 = base_size（与文字同高，视觉贴合）
"""

from typing import Callable

import flet as ft

from styles import FONT_MAIN, _current_colors


def make_strut(base_size: int, line_height: float, font_family: str = FONT_MAIN) -> ft.StrutStyle:
    """构造与渲染层 Text 共用的 StrutStyle 实例。

    force_strut_height=True 强制行高 = size * height，忽略字体内置 ascent/descent
    差异；leading=0 不额外加行间距。渲染层 ft.Text 与 cursor TextField 必须传
    同一实例（或同参数构造），否则二者行高不一致会导致光标 Y 偏移。
    """
    return ft.StrutStyle(
        force_strut_height=True,
        height=line_height,
        leading=0,
        size=base_size,
        font_family=font_family,
    )


def cursor_text_field(
    *,
    cursor_px_x: float,
    cursor_px_y: float = 0.0,
    line_height_px: float,
    base_size: int,
    line_height: float = 1.6,
    on_change: Callable[[str], None],
    on_submit: Callable[[str], None] | None = None,
    on_focus: Callable | None = None,
    on_blur: Callable | None = None,
    on_selection_change: Callable | None = None,
    field_ref: ft.Ref | None = None,
    nav_seq: int = 0,
    debug: bool = False,
) -> ft.TextField:
    """构造透明光标 TextField（Stack 顶层绝对定位）。

    参数：
      cursor_px_x/cursor_px_y：Stack 内光标像素位置（来自 LineLayoutCache.cursor_px）
      line_height_px：Stack 高度 = base * line_height，TextField 高度同此
      base_size：基础字号（与渲染层 Text 一致）
      line_height：行高倍数（与渲染层一致）
      on_change(value)：输入回调，value 为 TextField 当前值（应仅 1 字符或空）
      on_submit(value)：Enter 回调
      on_focus/on_blur：聚焦/失焦回调
      on_selection_change：光标位置变化回调（用于跟踪 selection）
      field_ref：TextField 引用（editor 端 use_effect 调 focus()）
      nav_seq：递增触发 key 重建以清空内部状态
      debug：开发期加半透明红色背景肉眼校准光标位置

    宽度策略：
      设为 2px 仅承载光标闪烁（渲染层负责显示文字，TextField 文字透明不可见）。
      IME 组合态候选框可能被裁切（Phase 1 接受），Phase 2 改为撑满剩余宽度。
    """
    c = _current_colors()
    kwargs: dict = {
        # key 变化触发 Flet 销毁旧控件创建新控件，内部 value 自动清空
        "key": f"cursor-field-{nav_seq}",
        "value": "",
        "autofocus": True,
        "multiline": False,
        "min_lines": 1,
        "max_lines": 1,
        # 无边框、透明背景、零内边距，绝不遮挡渲染层
        "border": ft.InputBorder.NONE,
        "border_radius": 0,
        "filled": False,
        "content_padding": ft.Padding.all(0),
        # 文字透明：输入字符不可见，仅光标可见
        "text_size": base_size,
        "text_style": ft.TextStyle(
            font_family=FONT_MAIN,
            color=ft.Colors.TRANSPARENT,
        ),
        # 行高与渲染层共用同一 strut 实例参数
        "strut_style": make_strut(base_size, line_height, FONT_MAIN),
        # 光标样式：与文字同高，显式颜色保证可见
        "cursor_color": c.text,
        "cursor_width": 2,
        "cursor_height": base_size,
        "show_cursor": True,
        # Stack 内绝对定位
        "left": cursor_px_x,
        "top": cursor_px_y,
        "width": 2,  # 极窄承载光标；IME Phase 2 撑满
        "height": line_height_px,
        "dense": True,
        "shift_enter": False,
        "ignore_up_down_keys": True,  # 上下键冒泡到外层做跨行导航
        "on_change": lambda e: on_change(e.control.value),
    }
    if on_submit is not None:
        kwargs["on_submit"] = lambda e: on_submit(e.control.value)
    if on_focus is not None:
        kwargs["on_focus"] = on_focus
    if on_blur is not None:
        kwargs["on_blur"] = on_blur
    if on_selection_change is not None:
        kwargs["on_selection_change"] = on_selection_change
    if field_ref is not None:
        kwargs["ref"] = field_ref
    if debug:
        # 开发期肉眼校准：半透明红色背景显示 TextField 实际占位
        kwargs["filled"] = True
        kwargs["fill_color"] = ft.Colors.with_opacity(0.3, ft.Colors.RED)
    return ft.TextField(**kwargs)
