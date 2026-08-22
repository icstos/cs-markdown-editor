"""Stack 顶层透明编辑层：光标承载 TextField。

为 Stack 双层架构提供"光标级"输入入口：
- 全透明背景、无边框、零内边距，绝不遮挡底层渲染层
- text_style.color = TRANSPARENT：输入字符不可见（仅作输入载体，渲染由底层
  raw_to_visible_spans 完成）
- strut_style 与渲染层 Text 共用同一实例，强制行高一致，保证光标 baseline
  与渲染层文字 baseline 像素级对齐
- 通过 Stack 内绝对定位（left/top）将光标摆到渲染层的字符间隙像素位置
- 不设置 value 属性（避免 Flet 重渲染同步 value 打断 IME 组合态）；由 editor 端
  use_effect([clear_value_seq]) 在重渲染后异步清空 Flutter 端内部 value

IME 友好策略（key = li + nav_seq）：
- 同行输入：li/nav_seq 均不变 → key 不变 → 不重建 → IME 组合态保持
- 切换行：li 变 → key 变 → 重建（旧行释放 TextField，新行创建）
- 撤销/重做/光标移动：nav_seq 变 → key 变 → 重建（强制刷新内部状态；
  光标移动的重建+重聚焦同时让闪烁重启到可见相位，避免快速移动时
  光标停在熄灭相位从视线中丢失）

定位参数（由 _cursor_overlay 计算，2D 视觉行定位）：
- cursor_px_x：光标 X（相对视觉行左起点，vline.offsets_x[local_off]）
- cursor_px_y：vline_idx * text_h（非零，2D 定位；单视觉行时为 0）
- line_height_px：单视觉行高 = base * line_height（TextField 高度）
- cursor_h：光标高度 = base_size（与文字同高，视觉贴合）
"""

from collections.abc import Callable

import flet as ft

from styles import FONT_MAIN, _current_colors
from utils.text_layout import _FLET_DEFAULT_LETTER_SPACING


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
    li: int,
    cursor_px_x: float,
    cursor_px_y: float = 0.0,
    line_height_px: float,
    base_size: int,
    line_height: float = 1.6,
    value: str = "",
    on_change: Callable[[str], None],
    on_submit: Callable[[str], None] | None = None,
    on_focus: Callable | None = None,
    on_blur: Callable | None = None,
    on_selection_change: Callable | None = None,
    field_ref: ft.Ref | None = None,
    nav_seq: int = 0,
    selection: ft.TextSelection | None = None,
    content_width: float | None = None,
) -> ft.TextField:
    """构造透明光标 TextField（Stack 顶层绝对定位）。

    参数：
      li：光标所在行号（key 主体，切换行才重建控件，保持 IME 连接）
      cursor_px_x/cursor_px_y：Stack 内光标像素位置（来自 LineLayoutCache.cursor_px）
      line_height_px：Stack 高度 = base * line_height，TextField 高度同此
      base_size：基础字号（与渲染层 Text 一致）
      line_height：行高倍数（与渲染层一致）
      on_change(value)：输入回调，value 为 TextField 当前完整值（IME 期间可能多字符）
      on_submit(value)：Enter 回调
      on_focus/on_blur：聚焦/失焦回调
      on_selection_change：光标位置变化回调（用于跟踪 selection）
      field_ref：TextField 引用（editor 端 use_effect 调 focus()）
      nav_seq：撤销/重做/光标移动等强制重建场景递增；同 li 输入不递增以保持 IME 组合态
      content_width：行内容最大宽度（用于计算 TextField 右边界，IME 友好宽度）

    key 策略（IME 友好）：
      key = f"cursor-field-li-{li}-seq-{nav_seq}"
      - 同行输入：li/nav_seq 均不变 → key 不变 → 不重建 → IME 组合态保持
      - 切换行：li 变 → key 变 → 重建（合理，旧行释放 TextField）
      - 撤销/重做/光标移动：nav_seq 变 → key 变 → 重建（强制刷新内部状态；
        光标移动的重建+重聚焦让 Flutter 光标以不透明相位重启闪烁，
        保证快速移动时持续可视）
      输入后 value 清空由 editor 端 _end_input_session 异步执行（光标移动时触发）。

    value 属性策略（IME 关键）：
      设置 value=cursor_field_value（input_session 的 last_value 镜像）。
      重渲染时 Flet 同步 value 到 Flutter 端，避免 value 被重置为空导致
      IME 重新触发 on_change（连续输入字符吞没根因）。
      _end_input_session 递增 nav_seq 重建控件清空 value（新控件 value=""）。

    宽度策略（IME 修复）：
      从光标位置撑到行尾（right=0 或 width=content_width - cursor_px_x），
      确保 IME 有足够空间管理组合文本。极窄（2px）TextField 会导致 Windows
      五笔/拼音输入法组合文本重复翻倍的 bug。
    """
    c = _current_colors()
    # 宽度：从光标位置到行尾，保底 200px 给 IME 组合文本空间。
    # 软换行场景：光标在视觉行末尾时 cursor_px_x ≈ wrap_width，
    # content_width - cursor_px_x ≈ 0，极窄 TextField 会触发 Windows IME
    # composing text 翻倍 bug。Stack clip_behavior=NONE 不裁切，多出的宽度
    # 不影响视觉（TextField 文字透明），仅保证 IME 有足够空间管理组合文本。
    if content_width is not None:
        w = max(content_width - cursor_px_x, 200.0)
    else:
        w = 200.0
    kwargs: dict = {
        # key = li + nav_seq：同行输入不重建（保 IME），切行/撤销/移动时重建
        "key": f"cursor-field-li-{li}-seq-{nav_seq}",
        # value 属性：镜像 input_session 的 last_value，重渲染时 Flet 同步 value
        # 到 Flutter 端，避免 value 被重置为空导致 IME 重新触发 on_change
        # （连续输入字符吞没根因）。_end_input_session 递增 nav_seq 重建控件
        # 清空 value（新控件 value="" 天然清空）。
        "value": value,
        # selection：软换行触发一次性折叠选区（清掉 IME 提交并选中的文本）。
        # 仅 wrap_sel_seq>0 时携带；平时 None 不发送（客户端不动控制器选区，
        # 不干扰 IME 组合态）。
        "selection": selection,

        # 不设 autofocus！autofocus 在每次重渲染时都会发送到 Flutter，导致
        # TextField 重新聚焦，IME 重新触发 on_change（双发问题的根因）。
        # 聚焦由 editor 端 use_effect([cursor_li]) 在切行时异步执行。
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
            # letter_spacing 必须与 HarfBuzz 测量端（_FLET_DEFAULT_LETTER_SPACING）
            # 及渲染层 ft.Text 默认值（0.25）对齐。TextField 的 text_style 默认
            # letter_spacing=None（Flutter 标准 0.0），若不显式设置，TextField
            # 内文字宽度比 HarfBuzz 测量值每字少 0.25px，连续输入时光标与渲染
            # 内容距离线性累积（N 字 → N×0.25px 偏移）。
            letter_spacing=_FLET_DEFAULT_LETTER_SPACING,
        ),
        # 行高与渲染层共用同一 strut 实例参数
        "strut_style": make_strut(base_size, line_height, FONT_MAIN),
        # 光标样式：与文字同高，显式颜色保证可见
        "cursor_color": c.text,
        "cursor_width": 2,
        "cursor_height": base_size,
        "show_cursor": True,
        # Stack 内绝对定位：从光标位置撑到行尾（IME 友好宽度）
        "left": cursor_px_x,
        "top": cursor_px_y,
        "width": w,
        "height": line_height_px,
        "dense": True,
        "shift_enter": False,
        "ignore_up_down_keys": True,  # 上下键冒泡到外层做跨行导航
        "ignore_pointers": True,  # 点击穿透：不吸收鼠标事件，让 GestureDetector 处理光标定位
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
    return ft.TextField(**kwargs)
