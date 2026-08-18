"""行视图：Stack 双层叠加（渲染层 + 透明光标编辑层）。

架构（Typora 式 WYSIWYG 光标级实时渲染）：
- 非激活行：RenderedLine 纯渲染（语法标记透明）
- 激活行：ft.Stack([RenderedLine, cursor_text_field])，光标 TextField 不设 value 属性
  （IME 友好），由 editor 端 use_effect 异步清空；每个字符输入即时渲染到文档，
  光标像素级对齐渲染层文字间隙
- 围栏岛屿（CODE/MATH/HR/TOC）：保留独立分支，不进入 Stack
  CODE 用 CodeEditor 始终可编辑；MATH/HR/TOC 视图态渲染

状态由 editor.py 驱动：cursor_li/cursor_off/nav_seq/cursor_ref。本文件只负责渲染 + 命中。
"""

import asyncio
import re
from collections.abc import Callable

import flet as ft
from flet_code_editor import CodeEditor, CodeLanguage, CodeTheme, GutterStyle

from models import BlockType, Line
from styles import (
    FONT_MAIN,
    FONT_MONO,
    Elevation,
    Radius,
    Spacing,
    _current_colors,
    block_text_size,
    block_weight,
    card_shadow,
    only_border,
)
from utils.segment_helpers import PREFIX_SEGTYPES
from views.cursor_layer import cursor_text_field, make_strut
from views.pixel_layout import (
    _block_padding,
    _compute_wrap_width,
    _find_vline_for_raw,
    _line_visual_layout,
)
from views.rendered_line import RenderedLine

# 代码块语言选择下拉框的常用语言清单
_COMMON_LANGS: list[tuple[str, str]] = [
    ("", "Plain text"),
    ("python", "Python"),
    ("javascript", "JavaScript"),
    ("typescript", "TypeScript"),
    ("java", "Java"),
    ("kotlin", "Kotlin"),
    ("swift", "Swift"),
    ("go", "Go"),
    ("rust", "Rust"),
    ("c", "C"),
    ("cpp", "C++"),
    ("csharp", "C#"),
    ("php", "PHP"),
    ("ruby", "Ruby"),
    ("html", "HTML"),
    ("css", "CSS"),
    ("json", "JSON"),
    ("yaml", "YAML"),
    ("xml", "XML"),
    ("sql", "SQL"),
    ("bash", "Bash / Shell"),
    ("powershell", "PowerShell"),
    ("markdown", "Markdown"),
    ("dockerfile", "Dockerfile"),
    ("ini", "INI"),
    ("diff", "Diff"),
]


def _code_language(lang: str | None) -> CodeLanguage:
    """把 markdown 围栏语言标识映射为 CodeEditor 的 CodeLanguage 枚举。"""
    if not lang:
        return CodeLanguage.PLAINTEXT
    key = lang.strip().replace("-", "_").replace(" ", "").upper()
    aliases = {
        "JS": "JAVASCRIPT",
        "TS": "TYPESCRIPT",
        "PY": "PYTHON",
        "C++": "CPP",
        "C#": "CS",
        "SH": "SHELL",
        "BASH": "SHELL",
        "ZSH": "SHELL",
        "CSHARP": "CS",
        "PLAIN": "PLAINTEXT",
        "TEXT": "PLAINTEXT",
        "TXT": "PLAINTEXT",
        "NONE": "PLAINTEXT",
        "PLAINTEXT": "PLAINTEXT",
    }
    key = aliases.get(key, key)
    return getattr(CodeLanguage, key, CodeLanguage.PLAINTEXT)


def _lang_options(current_lang: str) -> list[ft.DropdownOption]:
    """构造语言下拉框选项；若当前语言不在常用清单内，追加为额外选项。"""
    options = [ft.DropdownOption(key=k, text=t) for k, t in _COMMON_LANGS]
    known = {k for k, _ in _COMMON_LANGS}
    if current_lang and current_lang not in known:
        options.append(ft.DropdownOption(key=current_lang, text=current_lang))
    return options


async def _copy_code_to_clipboard(
    clipboard_ref: ft.Ref | None,
    text: str,
    set_copied: Callable[[bool], None],
) -> None:
    clipboard = clipboard_ref.current if clipboard_ref is not None else None
    if clipboard is None:
        return
    try:
        await clipboard.set(text)
    except Exception:
        return
    set_copied(True)
    await asyncio.sleep(1.2)
    set_copied(False)


def _wrap_block(
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


def _cursor_overlay(
    line: Line,
    base: int,
    line_height: float,
    cursor_off: int,
    content_width: float | None,
    li: int,
    nav_seq: int,
    field_ref: ft.Ref | None,
    cursor_value: str,
    on_change: Callable[[str], None],
    on_submit: Callable[[str], None] | None,
    on_focus: Callable | None,
    on_blur: Callable | None,
    on_selection_change: Callable | None,
    *,
    precomputed_vlines: list | None = None,
    precomputed_wrap_width: float | None = None,
    pos_value: str | None = None,
) -> ft.TextField:
    """构造光标透明 TextField（Stack 顶层），像素定位到 cursor_off（2D 视觉行）。

    软换行 2D 定位（与渲染层 _maybe_stack_multi 共用 _line_visual_layout，
    换行点天然一致）：
    - _line_visual_layout 按 wrap_width 切出 N 视觉行
    - _find_vline_for_raw 找到 cursor_off 所在视觉行
    - cursor_px_x = vline.offsets_x[local_off]（局部于视觉行，已 rebase 到 0）
    - cursor_px_y = vline.vline_idx * text_h（非零，2D 定位）

    li 传入 cursor_text_field 作为 key 主体，保证同行输入不重建控件（IME 友好）；
    key 不含 vline_idx → 同行跨视觉行移动仅改 top/left，不重建控件，IME 组合态保持。

    任务行：Checkbox 替代了前缀，text_ctrl 只渲染内容（skip_prefix=True），
    cursor_overlay 在内容 Text 的 Stack 内。前缀段在 vline 0 占宽度，需扣除
    vline.offsets_x[prefix_len] 使光标 X 相对内容起点；vline 1+ 起点已在内容区，
    无需扣除。

    光标在段内：该段标记变灰可见占宽度（逐字符测量 raw）；其余段标记折叠。

    性能优化：precomputed_vlines / precomputed_wrap_width 由 LineView 共享传入，
    避免与 RenderedLine._get_vlayout 重复调用 _line_visual_layout（内含
    HarfBuzz 整形测量）。传入时跳过内部 _line_visual_layout 调用。
    """
    # 视觉行布局：优先复用 LineView 预计算结果，否则自行计算
    if precomputed_vlines is not None and precomputed_wrap_width is not None:
        visual_lines = precomputed_vlines
        wrap_width = precomputed_wrap_width
    else:
        _, _, left_pad = _block_padding(line)
        cw = content_width if content_width is not None else float("inf")
        wrap_width = _compute_wrap_width(cw, left_pad)
        visual_lines = _line_visual_layout(
            line, base, wrap_width,
            cursor_raw_offset=cursor_off,
            line_height=line_height,
        )

    # 找到 cursor_off 所在视觉行
    vline = _find_vline_for_raw(visual_lines, cursor_off)
    text_h = base * line_height
    # 定位与 TextField value 均使用 _eff_value：
    # 优先 pos_value（input_session_ref 的 last_value，ref 即时同步 cursor_ref），
    # 避免 cursor_field_value state 滞后于 cursor_ref.current.base 导致 start_local
    # 偏移、光标与内容距离累积增大（连续输入漂移根因）。
    _eff_value = pos_value if pos_value is not None else cursor_value
    if vline is None:
        cursor_px_x = 0.0
        cursor_px_y = 0.0
    else:
        local_off = cursor_off - vline.start_raw
        local_off = max(0, min(local_off, len(vline.offsets_x) - 1))
        cursor_px_x = vline.offsets_x[local_off]
        cursor_px_y = vline.vline_idx * text_h
        # value 文本起始位置：TextField 显示 value（透明但占宽度），
        # 需将 left 调整为 value 起始位置，使光标落在 cursor_off 对应的像素位置。
        # start_local = local_off - len(value)，对应 value 首字符的像素位置。
        if _eff_value:
            start_local = local_off - len(_eff_value)
            if 0 <= start_local < len(vline.offsets_x):
                cursor_px_x = vline.offsets_x[start_local]
            # start_local < 0：value 跨视觉行，保持当前 cursor_px_x（视觉行起点）

    # 任务行前缀宽度扣除（仅 vline 0：前缀占宽度，需相对内容起点定位光标）
    if line.task and line.segments and vline is not None and vline.vline_idx == 0:
        prefix_raw = line.segments[0].raw
        prefix_len = len(prefix_raw) if prefix_raw else 0
        if 0 < prefix_len < len(vline.offsets_x):
            cursor_px_x -= vline.offsets_x[prefix_len]

    return cursor_text_field(
        li=li,
        cursor_px_x=cursor_px_x,
        cursor_px_y=cursor_px_y,
        line_height_px=text_h,  # 单视觉行高（TextField 高度，Stack 高 = N * text_h）
        base_size=base,
        line_height=line_height,
        value=_eff_value,
        on_change=on_change,
        on_submit=on_submit,
        on_focus=on_focus,
        on_blur=on_blur,
        on_selection_change=on_selection_change,
        field_ref=field_ref,
        nav_seq=nav_seq,
        content_width=wrap_width,  # vline 内剩余宽度（IME 友好）
    )


def _render_math_block(
    line: Line,
    line_idx: int,
    base: int,
    content_width: float | None,
    on_change_math: Callable[[int, str], None] | None,
    on_math_focus: Callable[[int], None] | None,
    on_math_blur: Callable[[int], None] | None,
    math_field_ref: ft.Ref | None,
    is_editing: bool,
    is_current_line: bool,
    is_flash: bool = False,
    on_line_size_change: Callable[[int, float], None] | None = None,
    diff_mark: str | None = None,
) -> ft.Control:
    """公式块：浏览态 ft.Markdown 渲染 LaTeX；编辑态源码 + 实时预览。

    双态切换由 editor 端 math_focus_li state 驱动（Typora 式：点击进入编辑，
    失焦/点击外部回到渲染态）。左侧 math_fg 强调边框 + math_bg 底色，
    与行内公式配色统一。

    编辑态布局（垂直堆叠）：
    - header：ƒ 图标 + "公式编辑" + "点击外部完成"
    - 源码区：TextField（monospace, math_fg）+ "源码" 标签
    - 分隔线：math_fg 半透明
    - 预览区：ft.Markdown 实时渲染 LaTeX + "预览" 标签
    on_change_math 更新 line.segments[0].text 触发重渲染，预览自动刷新。
    """
    c = _current_colors()
    formula = line.segments[0].text if line.segments else ""

    if is_editing:
        # 编辑态：源码编辑器 + 实时预览（垂直堆叠）
        # on_change_math 更新 line.segments[0].text 触发 observable 重渲染，
        # 本函数重新执行读取最新 formula，preview_md 的 value 自动同步刷新。
        text_field = ft.TextField(
            key=f"math-edit-{line_idx}",
            value=formula,
            multiline=True,
            min_lines=2,
            max_lines=6,
            border=ft.InputBorder.NONE,
            text_size=14,
            text_style=ft.TextStyle(font_family=FONT_MONO, color=c.math_fg),
            on_change=lambda e: on_change_math(line_idx, e.control.value)
                if on_change_math else None,
            on_focus=lambda e: on_math_focus(line_idx) if on_math_focus else None,
            on_blur=lambda e: on_math_blur(line_idx) if on_math_blur else None,
            expand=True,
        )
        if math_field_ref is not None:
            text_field.ref = math_field_ref

        header = ft.Row([
            ft.Icon(ft.Icons.FUNCTIONS, size=13, color=c.math_fg),
            ft.Text("公式编辑", size=11, color=c.muted,
                    font_family=FONT_MONO, weight=ft.FontWeight.W_600),
            ft.Container(expand=True),
            ft.Text("点击外部完成", size=11, color=c.muted),
        ], spacing=Spacing.SM, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        source_label = ft.Text(
            "源码", size=10, color=c.muted,
            font_family=FONT_MONO, weight=ft.FontWeight.W_500,
        )

        source_section = ft.Container(
            content=text_field,
            border_radius=Radius.SM,
            padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=Spacing.XS),
        )

        divider = ft.Divider(
            height=1, thickness=1,
            color=ft.Colors.with_opacity(0.2, c.math_fg),
            leading_indent=0, trailing_indent=0,
        )

        preview_label = ft.Text(
            "预览", size=10, color=c.muted,
            font_family=FONT_MONO, weight=ft.FontWeight.W_500,
        )

        preview_md = ft.Markdown(
            value=f"$$\n{formula}\n$$",
            selectable=False,
            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
            soft_line_break=True,
            latex_style=ft.TextStyle(color=c.text),
        )

        preview_section = ft.Container(
            content=preview_md,
            alignment=ft.Alignment.CENTER_LEFT,
            padding=ft.Padding.symmetric(horizontal=Spacing.MD, vertical=Spacing.SM),
        )

        content = ft.Container(
            content=ft.Column(
                controls=[
                    header,
                    source_label,
                    source_section,
                    divider,
                    preview_label,
                    preview_section,
                ],
                spacing=Spacing.SM,
                tight=True,
            ),
            bgcolor=c.math_bg, border_radius=Radius.MD, width=float("inf"),
            padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
            border=only_border(left=ft.BorderSide(3, c.math_fg)),
        )
    else:
        # 浏览态：ft.Markdown 渲染 LaTeX（ selectable 便于复制）
        md = ft.Markdown(
            value=f"$$\n{formula}\n$$",
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
        )
        content = ft.Container(
            content=md, bgcolor=c.math_bg, border_radius=Radius.MD, width=float("inf"),
            padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
            alignment=ft.Alignment.CENTER,
            ink=True,
            on_click=lambda e: on_math_focus(line_idx) if on_math_focus else None,
        )

    return _wrap_block(
        content, line, base, line_idx,
        is_current_line=is_current_line, is_flash=is_flash,
        on_size_change=on_line_size_change, diff_mark=diff_mark,
    )


@ft.memo
@ft.component
def LineView(
    line: Line,
    line_idx: int,
    *,
    cursor_off: int | None = None,
    cursor_ref: ft.Ref | None = None,
    nav_seq: int = 0,
    field_ref: ft.Ref | None = None,
    input_session_ref: ft.Ref | None = None,
    cursor_value: str = "",
    content_width: float | None = None,
    line_height: float = 1.6,
    body_font_size: int = 16,
    is_current_line: bool = False,
    is_flash: bool = False,
    # 版本号触发 prop：reparse_line 就地修改 line 对象不替换引用，
    # ft.memo 浅比较 line 引用未变会误判未刷新。通过 raw 长度 + 段数
    # 两个值变化触发 memo 检测，让屏幕刷新。LineView 内部不读取这两个值。
    line_raw_version: int = 0,
    line_seg_count: int = 0,
    # 主题失效 prop：切换主题时 line/版本号/回调均不变，ft.memo 会复用缓存
    # 跳过函数体执行，导致 _current_colors() 不被重新调用，行内代码、公式、
    # 引用、列表、标题色与 CodeEditor 的 code_theme 停留在旧主题。
    # 通过 theme_mode 变化触发 memo 失效，重新执行函数体取色。LineView
    # 内部不读取此值（_current_colors 直接读 page.theme_mode，已由 App
    # 在渲染期同步写入），仅作 memo 触发用。
    theme_mode: ft.ThemeMode = ft.ThemeMode.LIGHT,
    # 光标输入（激活行用）
    on_cursor_change: Callable[[str], None] | None = None,
    on_cursor_submit: Callable[[str], None] | None = None,
    on_cursor_focus: Callable | None = None,
    on_cursor_blur: Callable | None = None,
    on_selection_change: Callable | None = None,
    # 点击 / 拖拽
    on_tap: Callable[[int, int], None] | None = None,
    on_pan_start: Callable[[int, int], None] | None = None,
    on_pan_update: Callable[[int, int], None] | None = None,
    on_toggle_task: Callable[[int], None] | None = None,
    # 代码块
    on_change_code: Callable[[int, str], None] | None = None,
    on_code_focus: Callable[[int], None] | None = None,
    on_code_blur: Callable[[int], None] | None = None,
    code_field_ref: ft.Ref | None = None,
    on_change_lang: Callable[[int, str], None] | None = None,
    # 块级公式：浏览态 ft.Markdown 渲染 LaTeX，编辑态 TextField 编辑源码
    is_math_editing: bool = False,
    on_change_math: Callable[[int, str], None] | None = None,
    on_math_focus: Callable[[int], None] | None = None,
    on_math_blur: Callable[[int], None] | None = None,
    math_field_ref: ft.Ref | None = None,
    clipboard_ref: ft.Ref | None = None,
    # TOC
    toc_entries: list[tuple[int, int, str]] | None = None,
    on_jump_to: Callable[[int], None] | None = None,
    # 行高上报：on_size_change 触发时回传 (line_idx, height)，用于精确滚动定位
    on_line_size_change: Callable[[int, float], None] | None = None,
    # 向外选区
    outward_range: tuple[int, int] | None = None,
    on_extend_outward: Callable[[int, int], None] | None = None,
    on_clear_outward: Callable[[], None] | None = None,
    shift_pressed_ref: ft.Ref | None = None,
    ctrl_pressed_ref: ft.Ref | None = None,
    alt_pressed_ref: ft.Ref | None = None,
    on_hit_test_x: Callable[[int, float], int] | None = None,
    on_hit_test_xy: Callable[[int, float, float], tuple[int, int] | None] | None = None,
    on_double_tap: Callable[[int, int], None] | None = None,
    # 图片右键菜单操作：(action, line_idx, seg_idx, url, alt)，透传至 RenderedLine
    on_image_action: Callable[[str, int, int, str, str], None] | None = None,
    # 文档路径：透传至 RenderedLine，用于解析相对路径图片（assets/xxx.png）
    file_path: str | None = None,
    # diff 对比：行级背景着色标记（"added"|"removed"|"modified"|None）
    diff_mark: str | None = None,
    # 多光标：本行的副光标列表 [(li, base, extent)]，用于渲染副光标标记 + 选区高亮
    secondary_cursors: list[tuple[int, int, int]] | None = None,
    # 多光标版本号：secondary_cursors 内容变化时递增，强制 ft.memo 失效重渲染
    # （ft.memo 对 list 走身份比较，list comprehension 每次生成新对象虽能触发
    # 重渲染，但状态批处理可能导致部分行未及时刷新，版本号兜底确保所有行同步）
    secondary_cursors_version: int = 0,
) -> ft.Control:
    """渲染一行：围栏块走独立分支，普通文本行走 RenderedLine + Stack。

    is_current_line：唯一激活标志（替代原 cursor_li == line_idx 比较）。
    cursor_ref：激活行的光标位置 ref（CursorState）。
    handle_char_input 中不调用 set_cursor_off（避免重渲染打断 IME），
    光标位置仅由 cursor_ref.current.base 跟踪。
    LineView 通过 cursor_ref 读取最新光标位置，计算光标像素坐标。
    parser.reparse_line 触发的重渲染会重新调用 LineView，此时读取
    cursor_ref.current.base 获取最新光标位置，实现光标实时跟随。

    memo 化：非激活行的 prop 集合稳定（line/line_idx/content_width/line_height
    + 版本号 prop + 回调），cursor 移动时仅旧激活行 + 新激活行 prop 变化，
    其余 N-2 行 ft.memo 直接复用缓存，跳过 Python 函数体执行。主题切换时
    line/版本号/回调均不变，靠 theme_mode prop 变化触发 memo 失效，让
    _current_colors() 与 CodeEditor 的 code_theme 重新取最新主题色。
    """
    c = _current_colors()
    base = block_text_size(line.block_type, line.level, body_font_size)
    is_active = is_current_line

    # 激活行：优先使用 cursor_ref.current.base（IME 组合期间最新位置）
    # cursor_off state 在 IME 组合期间不更新（避免重渲染打断 IME），
    # 仅在 _end_input_session 中同步。cursor_ref 实时跟踪最新位置。
    effective_cursor_off = cursor_off if cursor_off is not None else 0
    if is_active and cursor_ref is not None and cursor_ref.current is not None:
        ref_off = getattr(cursor_ref.current, "base", None)
        if ref_off is not None and ref_off >= 0:
            effective_cursor_off = ref_off

    # ============ 代码块（始终可编辑 CodeEditor 独立岛屿）============
    if line.block_type == BlockType.CODE:
        return _render_code_block(
            line, line_idx, base, content_width, clipboard_ref,
            on_change_code, on_code_focus, on_code_blur, on_change_lang,
            code_field_ref, is_current_line, is_flash, on_line_size_change,
            diff_mark=diff_mark,
        )

    # ============ YAML 前置元数据（Obsidian 风格属性卡片）============
    if line.block_type == BlockType.FRONTMATTER:
        return _render_frontmatter(
            line, line_idx, base, content_width, clipboard_ref,
            on_change_code, on_code_focus, on_code_blur,
            code_field_ref, is_current_line, is_flash, on_line_size_change,
            diff_mark=diff_mark,
        )

    # ============ 块级公式 MATH（浏览态 ft.Markdown / 编辑态 TextField）============
    if line.block_type == BlockType.MATH:
        return _render_math_block(
            line, line_idx, base, content_width,
            on_change_math, on_math_focus, on_math_blur, math_field_ref,
            is_math_editing, is_current_line, is_flash, on_line_size_change,
            diff_mark=diff_mark,
        )

    # ============ 分隔线 HR（Typora 式 WYSIWYG：浏览态横线，激活态 fall through 显示源码）============
    if line.block_type == BlockType.HR and not is_active:
        content = ft.Container(
            content=ft.Container(
                height=1,
                bgcolor=ft.Colors.with_opacity(0.25, c.muted),
                border_radius=0.5,
                width=float("inf"),
            ),
            padding=ft.Padding.symmetric(vertical=Spacing.LG),
            alignment=ft.Alignment.CENTER,
            ink=True,
            on_click=lambda e, raw=line.raw: on_tap(line_idx, len(raw) if raw else 0) if on_tap else None,
        )
        return _wrap_block(
            content, line, base, line_idx,
            is_current_line=is_current_line, is_flash=is_flash, on_size_change=on_line_size_change,
            diff_mark=diff_mark,
        )
    # HR 激活态：fall through 到下方普通文本路径（显示 --- 源码 + 光标，可编辑）

    # ============ 目录 [toc] ============
    if line.block_type == BlockType.TOC:
        # 目录卡片：与侧边栏大纲面板视觉一致——彩色细竖线区分标题级别
        # （红橙绿青蓝紫），同级别条目左对齐到同一缩进位置，H1/H2 加粗突出主章节。
        # 头部含图标 + 标题 + 计数，清爽卡片边框，科学有序。
        entries = toc_entries or []
        header = ft.Row(
            controls=[
                ft.Icon(ft.Icons.FORMAT_LIST_BULLETED, size=14, color=c.muted),
                ft.Text(
                    value="目录",
                    size=base - 5,
                    color=c.muted,
                    font_family=FONT_MAIN,
                    weight=ft.FontWeight.W_600,
                ),
                ft.Container(expand=True),
                ft.Text(
                    value=f"{len(entries)} 项",
                    size=base - 6,
                    color=c.muted,
                    font_family=FONT_MONO,
                ),
            ],
            spacing=Spacing.SM,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        if not entries:
            body = ft.Container(
                content=ft.Text(
                    value="文档无标题，目录为空",
                    size=base - 4,
                    color=c.muted,
                    font_family=FONT_MAIN,
                ),
                padding=ft.Padding.symmetric(vertical=Spacing.XL),
                alignment=ft.Alignment.CENTER,
                width=float("inf"),
            )
        else:
            items: list[ft.Control] = []
            for li, lvl, text in entries:
                color = c.heading_colors.get(lvl, c.muted)
                bar = ft.Container(width=2, height=14, bgcolor=color, border_radius=2)
                txt = ft.Text(
                    value=text,
                    size=base - 3,
                    color=c.text,
                    font_family=FONT_MAIN,
                    weight=ft.FontWeight.W_600 if lvl <= 2 else ft.FontWeight.NORMAL,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                )
                row = ft.Row(
                    controls=[bar, txt],
                    spacing=Spacing.MD,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
                # 同级别左对齐：缩进 = (lvl-1) * Spacing.XXL，色条作为级别标识
                items.append(ft.Container(
                    content=row,
                    padding=ft.Padding.only(
                        left=(lvl - 1) * Spacing.XXL,
                        top=Spacing.SM,
                        bottom=Spacing.SM,
                        right=Spacing.SM,
                    ),
                    on_click=lambda e, t=li: on_jump_to(t) if on_jump_to else None,
                    ink=True,
                    border_radius=Radius.SM,
                ))
            body = ft.Column(controls=items, spacing=0)

        content = ft.Container(
            content=ft.Column(
                controls=[
                    header,
                    ft.Divider(height=1, thickness=1, color=c.border),
                    body,
                ],
                spacing=Spacing.SM,
            ),
            width=float("inf"),
            padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
            bgcolor=c.code_bg,
            border_radius=Radius.LG,
            border=only_border(
                top=ft.BorderSide(1, c.border),
                bottom=ft.BorderSide(1, c.border),
                left=ft.BorderSide(1, c.border),
                right=ft.BorderSide(1, c.border),
            ),
        )
        return _wrap_block(
            content, line, base, line_idx,
            is_current_line=is_current_line, is_flash=is_flash, on_size_change=on_line_size_change,
            diff_mark=diff_mark,
        )

    # ============ 普通文本行（段落/标题/列表/引用/空行）：RenderedLine + Stack ============
    # 性能优化：激活行在此计算一次视觉行布局，共享给 _cursor_overlay 和 RenderedLine，
    # 消除原先三处独立调用 _line_visual_layout（内含 HarfBuzz 整形测量）的冗余。
    # 非激活行无 overlay，vlayout 由 RenderedLine 内部惰性计算（无重复）。
    cursor_off_val = effective_cursor_off if is_active else None
    shared_vlayout: tuple[float, list] | None = None
    if is_active:
        _, _, left_pad = _block_padding(line)
        cw = content_width if content_width is not None else float("inf")
        shared_ww = _compute_wrap_width(cw, left_pad)
        shared_vlines = _line_visual_layout(
            line, base, shared_ww,
            cursor_raw_offset=effective_cursor_off,
            line_height=line_height,
        )
        shared_vlayout = (shared_ww, shared_vlines)

    overlay = None
    if is_active and on_cursor_change is not None:
        # 定位用 value：优先 input_session_ref 的 last_value（ref，与
        # cursor_ref.current.base 在同一 handle_char_input 调用中即时更新），
        # 避免 cursor_field_value state 滞后导致 start_local 偏移、光标与内容
        # 距离累积增大（连续输入漂移根因）。会话不在本行或已结束时回退 cursor_value。
        pos_value = cursor_value
        if input_session_ref is not None and input_session_ref.current is not None:
            _sess = input_session_ref.current
            if _sess.get("li") == line_idx and _sess.get("start_off", -1) >= 0:
                _lv = _sess.get("last_value")
                if _lv is not None:
                    pos_value = _lv
        overlay = _cursor_overlay(
            line, base, line_height, effective_cursor_off, content_width, line_idx, nav_seq,
            field_ref, cursor_value, on_cursor_change, on_cursor_submit, on_cursor_focus,
            on_cursor_blur, on_selection_change,
            precomputed_vlines=shared_vlayout[1] if shared_vlayout else None,
            precomputed_wrap_width=shared_vlayout[0] if shared_vlayout else None,
            pos_value=pos_value,
        )

    inner = RenderedLine(
        line=line,
        line_idx=line_idx,
        cursor_off=cursor_off_val,
        base_size=base,
        line_height=line_height,
        body_font_size=body_font_size,
        content_width=content_width,
        cursor_overlay=overlay,
        precomputed_vlayout=shared_vlayout,
        on_tap=on_tap,
        on_pan_start=on_pan_start,
        on_pan_update=on_pan_update,
        on_toggle_task=on_toggle_task,
        outward_range=outward_range,
        on_extend_outward=on_extend_outward,
        on_clear_outward=on_clear_outward,
        shift_pressed_ref=shift_pressed_ref,
        ctrl_pressed_ref=ctrl_pressed_ref,
        alt_pressed_ref=alt_pressed_ref,
        on_hit_test_x=on_hit_test_x,
        on_hit_test_xy=on_hit_test_xy,
        on_double_tap=on_double_tap,
        on_image_action=on_image_action,
        file_path=file_path,
    )

    # ============ 多光标：副光标标记 + 选区高亮 ============
    # 副光标为纯视觉标记（thin vertical bar），不承载 IME 输入（仅主光标有 TextField）。
    # 有选区时（base != extent）渲染半透明背景高亮。
    # 主光标在多光标模式下也用 cursor_ref.base/extent 跟踪选区（非 outward_sel），
    # 此处一并渲染主光标选区高亮。
    # 关键：主光标所在行通常没有副光标（副光标在其他行），条件必须同时覆盖
    # "本行有副光标" 和 "本行是激活行且主光标有选区" 两种情况，否则主光标
    # 选区高亮不渲染。
    _has_primary_sel = (
        is_active
        and cursor_ref is not None
        and cursor_ref.current is not None
        and cursor_ref.current.base != cursor_ref.current.extent
    )
    if secondary_cursors or _has_primary_sel:
        # 计算视觉行布局：复用激活行的 shared_vlayout，否则惰性计算
        if shared_vlayout is not None:
            sec_ww, sec_vlines = shared_vlayout
        else:
            _, _, left_pad = _block_padding(line)
            cw = content_width if content_width is not None else float("inf")
            sec_ww = _compute_wrap_width(cw, left_pad)
            sec_vlines = _line_visual_layout(
                line, base, sec_ww,
                cursor_raw_offset=None,
                line_height=line_height,
            )
        text_h = base * line_height
        # 光标竖条垂直居中偏移：text 内文字在 line_height 行框内垂直居中，
        # 竖条 height=base 需下移 (text_h - base) / 2 才对齐文字基线区域。
        cursor_y_off = (text_h - base) / 2
        sec_overlays: list[ft.Control] = []
        # 主光标选区高亮（多光标模式 Shift+Arrow 扩展的选区）
        if _has_primary_sel:
            pbase = cursor_ref.current.base
            pext = cursor_ref.current.extent
            pv = _find_vline_for_raw(sec_vlines, pbase)
            if pv is not None:
                ps = min(pbase, pext)
                pe = max(pbase, pext)
                ps_local = max(0, min(ps - pv.start_raw, len(pv.offsets_x) - 1))
                pe_local = max(0, min(pe - pv.start_raw, len(pv.offsets_x) - 1))
                psx = pv.offsets_x[ps_local]
                pex = pv.offsets_x[pe_local]
                if line.task and line.segments and pv.vline_idx == 0:
                    prefix_raw = line.segments[0].raw
                    prefix_len = len(prefix_raw) if prefix_raw else 0
                    if 0 < prefix_len < len(pv.offsets_x):
                        psx -= pv.offsets_x[prefix_len]
                        pex -= pv.offsets_x[prefix_len]
                sec_overlays.append(ft.Container(
                    width=max(pex - psx, 2),
                    height=text_h,
                    left=psx,
                    top=pv.vline_idx * text_h,
                    bgcolor=ft.Colors.with_opacity(0.25, c.link),
                    border_radius=2,
                ))
        for (_sli, sbase, sext) in secondary_cursors:
            vline = _find_vline_for_raw(sec_vlines, sbase)
            if vline is None:
                continue
            local_off = sbase - vline.start_raw
            local_off = max(0, min(local_off, len(vline.offsets_x) - 1))
            px_x = vline.offsets_x[local_off]
            px_y = vline.vline_idx * text_h
            # 任务行前缀宽度扣除（vline 0：前缀占宽度，需相对内容起点定位）
            if line.task and line.segments and vline.vline_idx == 0:
                prefix_raw = line.segments[0].raw
                prefix_len = len(prefix_raw) if prefix_raw else 0
                if 0 < prefix_len < len(vline.offsets_x):
                    px_x -= vline.offsets_x[prefix_len]
            # 选区高亮（base != extent）
            if sbase != sext:
                sel_start_raw = min(sbase, sext)
                sel_end_raw = max(sbase, sext)
                start_local = sel_start_raw - vline.start_raw
                end_local = sel_end_raw - vline.start_raw
                start_local = max(0, min(start_local, len(vline.offsets_x) - 1))
                end_local = max(0, min(end_local, len(vline.offsets_x) - 1))
                sel_x_start = vline.offsets_x[start_local]
                sel_x_end = vline.offsets_x[end_local]
                if line.task and line.segments and vline.vline_idx == 0:
                    prefix_raw = line.segments[0].raw
                    prefix_len = len(prefix_raw) if prefix_raw else 0
                    if 0 < prefix_len < len(vline.offsets_x):
                        sel_x_start -= vline.offsets_x[prefix_len]
                        sel_x_end -= vline.offsets_x[prefix_len]
                sel_width = max(sel_x_end - sel_x_start, 2)
                sec_overlays.append(ft.Container(
                    width=sel_width,
                    height=text_h,
                    left=sel_x_start,
                    top=px_y,
                    bgcolor=ft.Colors.with_opacity(0.25, c.link),
                    border_radius=2,
                ))
            # 副光标竖条标记（垂直居中于行框）
            sec_overlays.append(ft.Container(
                width=2,
                height=base,
                left=px_x,
                top=px_y + cursor_y_off,
                bgcolor=c.text,
                border_radius=1,
            ))
        if sec_overlays:
            inner = ft.Stack(
                controls=[inner] + sec_overlays,
                width=float("inf"),
            )

    # 最外层 Container 点击兜底：点击引用缩进/边框、列表缩进等 padding 死区时
    # 定位光标到内容起点（内层 GestureDetector 只覆盖内容区）。桌面端直觉：
    # 点击整行任意位置（含引用左侧色条）即可编辑。
    content_start_off = 0
    if line.segments and line.segments[0].seg_type in PREFIX_SEGTYPES:
        content_start_off = len(line.segments[0].raw)
    return _wrap_block(
        inner, line, base, line_idx,
        is_current_line=is_current_line, on_size_change=on_line_size_change,
        diff_mark=diff_mark,
        on_click=(
            lambda e, li=line_idx, off=content_start_off: on_tap(li, off)
            if on_tap is not None
            else None
        ),
    )


def _parse_yaml_pairs(content: str) -> list[tuple[str, str]]:
    """简易 YAML 键值对解析（仅支持扁平 key: value 格式）。

    不引入 PyYAML 依赖，仅做行级拆分：每行以 "key: value" 形式存在。
    复杂结构（嵌套/列表/多行字符串）的行原样保留为键值对（key=原行，value=""）。
    """
    pairs: list[tuple[str, str]] = []
    for raw_line in content.split("\n"):
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        idx = raw_line.find(":")
        if idx <= 0:
            continue
        key = raw_line[:idx].strip()
        val = raw_line[idx + 1:].strip()
        pairs.append((key, val))
    return pairs


def _pairs_to_yaml(pairs: list) -> str:
    """把键值对列表序列化为 YAML 文本（跳过键为空的行，两侧空白剥离）。

    与 _commit_pairs 写回文档的口径一致；供写回与“外部内容同步判定”共用，
    保证对比口径相同（编辑态含待定空键行时不会误判为外部变更）。
    """
    filtered = [(k.strip(), v.strip()) for k, v in pairs if k.strip()]
    return "\n".join(f"{k}: {v}" for k, v in filtered) if filtered else ""


def _render_frontmatter(
    line: Line,
    line_idx: int,
    base: int,
    content_width: float | None,
    clipboard_ref: ft.Ref | None,
    on_change_code: Callable[[int, str], None] | None,
    on_code_focus: Callable[[int], None] | None,
    on_code_blur: Callable[[int], None] | None,
    code_field_ref: ft.Ref | None,
    is_current_line: bool,
    is_flash: bool = False,
    on_line_size_change: Callable[[int, float], None] | None = None,
    diff_mark: str | None = None,
) -> ft.Control:
    """YAML 前置元数据渲染（Obsidian 风格可编辑属性表格）。

    渲染态：键值对表格化展示，键/值均为 TextField 可直接编辑。
    整体卡片式带浅色背景和左侧彩色边框，可折叠/展开。
    增删改：底部"+"新增行，每行右侧"×"删除行，键/值 TextField 实时编辑。

    交互：
    - 键/值 TextField 直接编辑 → 实时序列化为 YAML 写回文档
    - 底部"添加属性"按钮 → 新增空属性行
    - 每行"×"按钮 → 删除该行
    - 折叠按钮 → 切换折叠/展开（折叠时仅显示首行摘要）
    - 复制按钮 → 复制原始 YAML 文本
    """
    c = _current_colors()
    content = line.segments[0].text if line.segments else ""
    page = ft.context.page
    is_dark = page is not None and page.theme_mode == ft.ThemeMode.DARK

    # 语义化数据类型颜色（科学区分 YAML 值类型，亮/暗主题各自适配）
    # 取色原则：每种类型一个固定色相，亮暗模式仅调整明度/饱和度
    if is_dark:
        _type_colors = {
            "bool":   "#6BA0F5",  # 柔蓝（真/假：逻辑值）
            "number": "#65C292",  # 柔薄荷绿（数值）
            "date":   "#DD9658",  # 柔琥珀橙（日期时间）
            "array":  "#B08FD8",  # 柔丁香紫（列表/字典）
            "null":   "#8B939E",  # 中性灰（空值）
            "string": "#E6EDF3",  # 主文本色（字符串）
        }
        _key_color = "#75A4F0"   # 柔雾蓝（键名 + 标题，突出属性标识）
    else:
        _type_colors = {
            "bool":   "#1677FF",  # Ant Design 蓝（逻辑值）
            "number": "#0E7C66",  # 深青绿（数值）
            "date":   "#B54708",  # 焦糖橙（日期时间）
            "array":  "#6B5B95",  # 雅致紫（列表/字典）
            "null":   "#8A919E",  # 中性灰（空值）
            "string": "#1F2329",  # 主文本色（字符串）
        }
        _key_color = "#1A4480"   # 深海军蓝（键名 + 标题，权威标识）

    # ---- 状态 ----
    copied, set_copied = ft.use_state(False)
    is_collapsed, set_collapsed = ft.use_state(False)

    # ---- 解析键值对 ----
    pairs = _parse_yaml_pairs(content) if content else []

    # ---- 复制按钮 ----
    copy_btn = ft.IconButton(
        icon=ft.Icons.CHECK if copied else ft.Icons.CONTENT_COPY,
        icon_size=13,
        tooltip="已复制" if copied else "复制 YAML",
        padding=ft.Padding.all(Spacing.SM),
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=Radius.SM),
            color=ft.Colors.GREEN if copied else c.muted,
        ),
        on_click=lambda e, txt=content: (
            page.run_task(_copy_code_to_clipboard, clipboard_ref, txt, set_copied)
            if page is not None and not copied else None
        ),
    )

    # ---- 折叠按钮 ----
    collapse_btn = ft.IconButton(
        icon=ft.Icons.EXPAND_MORE if is_collapsed else ft.Icons.EXPAND_LESS,
        icon_size=13,
        tooltip="展开" if is_collapsed else "折叠",
        padding=ft.Padding.all(Spacing.SM),
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=Radius.SM),
            color=c.muted,
        ),
        on_click=lambda e: set_collapsed(not is_collapsed),
    )

    # ---- 头部工具栏 ----
    header = ft.Row(
        controls=[
            ft.Icon(ft.Icons.DATA_OBJECT, size=14, color=_key_color),
            ft.Text(
                value="YAML 前置元数据",
                size=11,
                color=_key_color,
                font_family=FONT_MAIN,
                weight=ft.FontWeight.W_600,
            ),
            ft.Container(expand=True),
            ft.Container(
                content=ft.Text(
                    value=f"{len(pairs)} 项" if pairs else "空",
                    size=10,
                    color=c.muted,
                    font_family=FONT_MONO,
                ),
                bgcolor=ft.Colors.with_opacity(0.06, c.text),
                padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=2),
                border_radius=Radius.SM,
            ),
            copy_btn,
            collapse_btn,
        ],
        spacing=Spacing.SM,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # ---- 可编辑属性表格 ----
    _KEY_COL_WIDTH = 140  # 键列固定宽度
    _DEL_BTN_WIDTH = 32   # 删除按钮列宽度

    # 编辑态键值对列表：本地 state 管理实时编辑，变化时序列化写回文档
    editing_pairs, set_editing_pairs = ft.use_state(
        [list(p) for p in pairs] if pairs else []
    )

    # 文档内容外部变更（撤销/重做/拆分视口对侧编辑/外部重载）时同步本地编辑态：
    # 否则撤销后表格仍显示修改前的旧内容，Ctrl+Z 看似失效。
    # 仅当文档内容与当前编辑态序列化不一致时才重置，避免打断正在输入的内容
    # （含键为空尚未写入文档的待定行），也不会与自身 _commit_pairs 写回产生回路。
    def _sync_editing_pairs() -> None:
        if content == _pairs_to_yaml(editing_pairs):
            return
        set_editing_pairs([list(p) for p in pairs] if pairs else [])

    ft.use_effect(_sync_editing_pairs, [content])

    def _value_style(val: str) -> tuple[str, str]:
        """根据值内容推断 (color, font_family)。

        语义化数据类型着色：
        布尔值 → 蓝；数字 → 绿；日期 → 橙；列表/字典 → 紫；空 → 灰；字符串 → 主文本色。
        等宽字体用于布尔/数字/列表（结构化数据），正常字体用于日期/字符串（自然语言）。
        """
        if not val:
            return _type_colors["null"], FONT_MAIN
        vl = val.lower()
        if vl in ("true", "false", "yes", "no", "null", "~", "none"):
            return _type_colors["bool"], FONT_MONO
        stripped = val.replace(".", "").replace("-", "")
        if stripped.isdigit():
            return _type_colors["number"], FONT_MONO
        if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", val):
            return _type_colors["date"], FONT_MAIN
        if val.startswith("[") or val.startswith("{"):
            return _type_colors["array"], FONT_MONO
        return _type_colors["string"], FONT_MAIN

    def _commit_pairs(new_pairs: list[list[str]]) -> None:
        """把编辑后的键值对序列化为 YAML 写回文档。

        跳过键为空的行（避免 YAML 语法错误）；过滤后全空则写空串。
        """
        yaml_text = _pairs_to_yaml(new_pairs)
        if on_change_code is not None:
            on_change_code(line_idx, yaml_text)

    def _on_key_change(idx: int, new_key: str) -> None:
        """键 TextField 输入变化：更新本地 state 并写回文档。"""
        new_pairs = [list(p) for p in editing_pairs]
        if idx < len(new_pairs):
            new_pairs[idx][0] = new_key
        else:
            new_pairs.append([new_key, ""])
        set_editing_pairs(new_pairs)
        _commit_pairs(new_pairs)

    def _on_value_change(idx: int, new_val: str) -> None:
        """值 TextField 输入变化：更新本地 state 并写回文档。"""
        new_pairs = [list(p) for p in editing_pairs]
        if idx < len(new_pairs):
            new_pairs[idx][1] = new_val
        else:
            new_pairs.append(["", new_val])
        set_editing_pairs(new_pairs)
        _commit_pairs(new_pairs)

    def _add_row() -> None:
        """新增空属性行。"""
        new_pairs = [list(p) for p in editing_pairs]
        new_pairs.append(["", ""])
        set_editing_pairs(new_pairs)
        # 不立即 commit：键为空的行不写入 YAML，等用户输入键后再写

    def _delete_row(idx: int) -> None:
        """删除指定属性行。"""
        new_pairs = [list(p) for p in editing_pairs]
        if 0 <= idx < len(new_pairs):
            new_pairs.pop(idx)
        set_editing_pairs(new_pairs)
        _commit_pairs(new_pairs)

    def _build_property_table() -> ft.Control:
        """构造 Obsidian 风格的可编辑属性表格。

        表头行（属性 | 值 | 操作）+ 数据行（TextField 键/值 + 删除按钮），
        底部新增行按钮。键列固定宽度，值列自适应。整体圆角裁剪。
        """
        # ---- 表头行：略深背景，与数据行明显分层 ----
        header_bg = ft.Colors.with_opacity(0.09 if is_dark else 0.06, c.text)
        header_row = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(
                        content=ft.Text(
                            value="属性",
                            size=base - 7,
                            color=c.muted,
                            font_family=FONT_MAIN,
                            weight=ft.FontWeight.W_600,
                        ),
                        width=_KEY_COL_WIDTH,
                        padding=ft.Padding.only(left=Spacing.SM, right=Spacing.SM),
                    ),
                    ft.Text(
                        value="值",
                        size=base - 7,
                        color=c.muted,
                        font_family=FONT_MAIN,
                        weight=ft.FontWeight.W_600,
                        expand=True,
                    ),
                    ft.Container(width=_DEL_BTN_WIDTH),
                ],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=header_bg,
            padding=ft.Padding.symmetric(vertical=6),
        )

        # ---- 数据行：斑马纹增强可读性 ----
        zebra_bg = ft.Colors.with_opacity(0.045 if is_dark else 0.03, c.text)
        data_rows: list[ft.Control] = [header_row]

        rows_data = editing_pairs if editing_pairs else []
        for idx, pair in enumerate(rows_data):
            key_val = pair[0] if len(pair) > 0 else ""
            val_val = pair[1] if len(pair) > 1 else ""
            val_color, val_font = _value_style(val_val)
            row_bg = zebra_bg if idx % 2 == 1 else None

            # 键 TextField：品牌蓝色突出属性标识，等宽字体
            key_field = ft.TextField(
                value=key_val,
                text_size=base - 6,
                color=_key_color,
                text_style=ft.TextStyle(font_family=FONT_MONO, weight=ft.FontWeight.W_500),
                border=ft.InputBorder.NONE,
                fill_color=ft.Colors.TRANSPARENT,
                dense=True,
                content_padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=3),
                hint_text="键名",
                hint_style=ft.TextStyle(
                    size=base - 6,
                    color=ft.Colors.with_opacity(0.35, c.muted),
                    font_family=FONT_MONO,
                ),
                on_change=lambda e, i=idx: _on_key_change(i, e.control.value or ""),
                on_focus=lambda e: on_code_focus(line_idx) if on_code_focus is not None else None,
                on_blur=lambda e: on_code_blur(line_idx) if on_code_blur is not None else None,
            )
            # 值 TextField：按数据类型着色，无边框透明底
            val_field = ft.TextField(
                value=val_val,
                text_size=base - 5,
                color=val_color,
                text_style=ft.TextStyle(font_family=val_font),
                border=ft.InputBorder.NONE,
                fill_color=ft.Colors.TRANSPARENT,
                dense=True,
                content_padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=3),
                hint_text="值",
                hint_style=ft.TextStyle(
                    size=base - 5,
                    color=ft.Colors.with_opacity(0.35, c.muted),
                ),
                on_change=lambda e, i=idx: _on_value_change(i, e.control.value or ""),
                on_focus=lambda e: on_code_focus(line_idx) if on_code_focus is not None else None,
                on_blur=lambda e: on_code_blur(line_idx) if on_code_blur is not None else None,
            )
            # 删除按钮：悬停时显红色警示
            del_btn = ft.IconButton(
                icon=ft.Icons.CLOSE,
                icon_size=13,
                tooltip="删除此行",
                padding=ft.Padding.all(4),
                style=ft.ButtonStyle(
                    shape=ft.RoundedRectangleBorder(radius=Radius.SM),
                    color={
                        ft.ControlState.HOVERED: "#E5484D",
                        ft.ControlState.DEFAULT: ft.Colors.with_opacity(0.4, c.muted),
                    },
                    bgcolor={
                        ft.ControlState.HOVERED: ft.Colors.with_opacity(0.08, "#E5484D"),
                        ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT,
                    },
                ),
                on_click=lambda e, i=idx: _delete_row(i),
            )

            row = ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Container(
                            content=key_field,
                            width=_KEY_COL_WIDTH,
                        ),
                        val_field,
                        ft.Container(
                            content=del_btn,
                            width=_DEL_BTN_WIDTH,
                            alignment=ft.Alignment.CENTER,
                        ),
                    ],
                    spacing=0,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                bgcolor=row_bg,
                padding=ft.Padding.symmetric(vertical=1),
            )
            data_rows.append(row)

        # ---- 新增行按钮：悬停高亮 ----
        add_row_btn = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.ADD, size=14, color=_key_color),
                    ft.Text(
                        value="添加属性",
                        size=base - 6,
                        color=_key_color,
                        font_family=FONT_MAIN,
                        weight=ft.FontWeight.W_500,
                    ),
                ],
                spacing=Spacing.XS,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=Spacing.XS),
            ink=True,
            border_radius=Radius.SM,
            on_click=lambda e: _add_row(),
        )
        data_rows.append(add_row_btn)

        # ---- 表格容器：圆角裁剪 + 清晰边框 ----
        table_border = ft.Colors.with_opacity(0.14 if is_dark else 0.10, c.text)
        return ft.Container(
            content=ft.Column(
                controls=data_rows,
                spacing=0,
            ),
            border_radius=Radius.SM,
            border=only_border(
                top=ft.BorderSide(1, table_border),
                bottom=ft.BorderSide(1, table_border),
                left=ft.BorderSide(1, table_border),
                right=ft.BorderSide(1, table_border),
            ),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

    # ---- 折叠态摘要 ----
    collapsed_preview = ft.Container(
        content=ft.Row(
            controls=[
                ft.Text(
                    value=(pairs[0][0] + ": " + pairs[0][1]) if pairs else "(空)",
                    size=base - 6,
                    color=c.muted,
                    font_family=FONT_MONO,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                ),
                ft.Text(
                    value=f"{len(pairs)} 项",
                    size=10,
                    color=c.muted,
                    font_family=FONT_MONO,
                ),
            ],
            spacing=Spacing.MD,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=Spacing.MD, vertical=Spacing.XS),
    )

    # ---- 主内容：折叠时显示摘要，否则显示可编辑表格 ----
    body = collapsed_preview if is_collapsed else _build_property_table()

    main_content = ft.Column(
        controls=[header, body],
        spacing=Spacing.XS,
    )

    # ---- 卡片容器（Obsidian 风格：左侧彩色边框 + 浅色背景）----
    border_color = ft.Colors.with_opacity(0.12 if is_dark else 0.08, c.text)
    # 左侧强调色：与键名/标题色一致（_key_color），整体配色统一
    accent = _key_color
    content_ctrl = ft.Container(
        content=main_content,
        bgcolor=ft.Colors.with_opacity(0.5, c.code_block_bg),
        border_radius=Radius.MD,
        padding=ft.Padding.only(
            left=Spacing.MD, right=Spacing.MD,
            top=Spacing.XS, bottom=Spacing.SM
        ),
        border=only_border(
            top=ft.BorderSide(1, border_color),
            bottom=ft.BorderSide(1, border_color),
            left=ft.BorderSide(3, accent),
            right=ft.BorderSide(1, border_color),
        ),
    )

    return _wrap_block(
        content_ctrl, line, line_idx,
        is_current_line=is_current_line,
        is_flash=is_flash,
        on_size_change=on_line_size_change,
        diff_mark=diff_mark,
    )


def _render_code_block(
    line: Line,
    line_idx: int,
    base: int,
    content_width: float | None,
    clipboard_ref: ft.Ref | None,
    on_change_code: Callable[[int, str], None] | None,
    on_code_focus: Callable[[int], None] | None,
    on_code_blur: Callable[[int], None] | None,
    on_change_lang: Callable[[int, str], None] | None,
    code_field_ref: ft.Ref | None,
    is_current_line: bool,
    is_flash: bool = False,
    on_line_size_change: Callable[[int, float], None] | None = None,
    diff_mark: str | None = None,
) -> ft.Control:
    """代码块分支：CodeEditor 始终可编辑独立岛屿（Typora/VSCode 风格）。

    特性：
    - 动态高度：通过 on_size_change 回调自适应内容高度
    - 语法高亮：基于 CodeTheme（GitHub/Atom One Dark）
    - 行号显示：紧凑行号区，宽度自适应
    - 自动补全：启用 autocomplete=True
    - 语言选择：下拉框支持搜索
    - 折叠支持：点击折叠按钮可折叠代码块
    - 精致视觉：代码块容器带阴影、主题适配
    """
    c = _current_colors()
    code = line.segments[0].text if line.segments else ""
    lang = line.lang or ""
    page = ft.context.page
    is_dark = page is not None and page.theme_mode == ft.ThemeMode.DARK
    code_theme = CodeTheme.ATOM_ONE_DARK if is_dark else CodeTheme.GITHUB

    # ---- 状态 ----
    copied, set_copied = ft.use_state(False)
    is_collapsed, set_collapsed = ft.use_state(False)

    # ---- 语言选择器 ----
    lang_dropdown = ft.Dropdown(
        value=lang,
        options=_lang_options(lang),
        width=150,
        text_size=12,
        dense=True,
        content_padding=ft.Padding.symmetric(horizontal=6, vertical=0),
        border=ft.InputBorder.NONE,
        fill_color=ft.Colors.TRANSPARENT,
        enable_search=True,
        editable=False,
        on_select=lambda e: (
            on_change_lang(line_idx, e.control.value or "")
            if on_change_lang is not None and e.control.value is not None
            else None
        ),
    )

    # ---- 复制按钮 ----
    copy_btn = ft.IconButton(
        icon=ft.Icons.CHECK if copied else ft.Icons.CONTENT_COPY,
        icon_size=14,
        tooltip="已复制" if copied else "复制代码",
        padding=ft.Padding.all(Spacing.MD),
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=Radius.MD),
            color=ft.Colors.GREEN if copied else c.muted,
        ),
        on_click=lambda e, txt=code: (
            page.run_task(_copy_code_to_clipboard, clipboard_ref, txt, set_copied)
            if page is not None and not copied else None
        ),
    )

    # ---- 折叠按钮 ----
    collapse_btn = ft.IconButton(
        icon=ft.Icons.EXPAND_MORE if is_collapsed else ft.Icons.EXPAND_LESS,
        icon_size=14,
        tooltip="展开" if is_collapsed else "折叠",
        padding=ft.Padding.all(Spacing.MD),
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=Radius.MD),
            color=c.muted,
        ),
        on_click=lambda e: set_collapsed(not is_collapsed),
    )

    # ---- 行号区宽度计算（含折叠手柄预留 20px）----
    line_count = max(1, code.count("\n") + 1)
    digits = len(str(line_count))
    gutter_width = max(56, 20 + digits * 10 + 20 + Spacing.SM)
    gutter_bg = ft.Colors.with_opacity(0.18 if is_dark else 0.03, c.text)

    # ---- CodeEditor ----
    editor = CodeEditor(
        key=f"code-{line_idx}-{digits}",
        value=code,
        language=_code_language(lang),
        code_theme=code_theme,
        gutter_style=GutterStyle(
            width=gutter_width,
            margin=Spacing.XS,
            show_line_numbers=True,
            show_errors=False,
            show_folding_handles=True,
            background_color=gutter_bg,
            text_style=ft.TextStyle(font_family=FONT_MONO, size=11, color=c.muted),
        ),
        text_style=ft.TextStyle(font_family=FONT_MONO, size=14, color=c.text),
        padding=ft.Padding.symmetric(horizontal=Spacing.MD, vertical=Spacing.SM),
        height=0 if is_collapsed else None,
        read_only=False,
        autofocus=False,
        autocomplete=True,
        on_change=lambda e: (
            on_change_code(line_idx, e.control.value)
            if on_change_code is not None else None
        ),
        on_focus=lambda e: on_code_focus(line_idx) if on_code_focus is not None else None,
        on_blur=lambda e: on_code_blur(line_idx) if on_code_blur is not None else None,
    )
    if code_field_ref is not None:
        editor.ref = code_field_ref

    # ---- 头部工具栏 ----
    header = ft.Row(
        controls=[
            collapse_btn,
            lang_dropdown,
            ft.Container(expand=True),
            ft.Text(
                value=f"{line_count} 行",
                size=11,
                color=c.muted,
                font_family=FONT_MONO,
            ),
            copy_btn,
        ],
        spacing=Spacing.SM,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # ---- 折叠时显示摘要 ----
    preview_text = code.split("\n")[0][:60] + ("…" if len(code.split("\n")[0]) > 60 else "")
    collapsed_preview = ft.Container(
        content=ft.Row(
            controls=[
                ft.Text(
                    value=preview_text or "(空代码块)",
                    size=12,
                    color=c.muted,
                    font_family=FONT_MONO,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                ),
                ft.Text(
                    value=f"{line_count} 行",
                    size=11,
                    color=c.muted,
                    font_family=FONT_MONO,
                ),
            ],
            spacing=Spacing.MD,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=Spacing.MD, vertical=Spacing.SM),
        bgcolor=ft.Colors.with_opacity(0.5, c.code_block_bg),
        border_radius=Radius.MD,
    )

    # ---- 主内容 ----
    main_content = ft.Column(
        controls=[
            header,
            collapsed_preview if is_collapsed else editor,
        ],
        spacing=Spacing.XS,
    )

    # ---- 代码块容器 ----
    border_color = ft.Colors.with_opacity(0.08 if is_dark else 0.06, c.text)
    content = ft.Container(
        content=main_content,
        bgcolor=c.code_block_bg,
        border_radius=Radius.MD,
        padding=ft.Padding.only(
            left=Spacing.MD, right=Spacing.MD,
            top=Spacing.XS, bottom=Spacing.SM
        ),
        shadow=card_shadow(Elevation.LOW, is_dark),
        border=only_border(
            top=ft.BorderSide(1, border_color),
            bottom=ft.BorderSide(1, border_color),
            left=ft.BorderSide(1, border_color),
            right=ft.BorderSide(1, border_color),
        ),
    )

    return _wrap_block(
        content, line, line_idx,
        on_click=(lambda e: on_code_focus(line_idx)) if on_code_focus is not None else None,
        is_current_line=is_current_line,
        is_flash=is_flash,
        on_size_change=on_line_size_change,
        diff_mark=diff_mark,
    )
