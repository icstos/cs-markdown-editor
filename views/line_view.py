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
from typing import Callable

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
from views.cursor_layer import cursor_text_field, make_strut
from views.pixel_layout import _line_raw_offsets_x
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
) -> ft.Control:
    """包一层块级容器：缩进、引用边框、当前行高亮、跳转脉冲高亮。

    on_click：挂到最外层 Container 的点击回调（padding 死区兜底）。
    on_size_change：行实际渲染高度上报回调，用于精确计算滚动偏移。
        回调签名为 (line_idx, height)；仅最外层 Container 绑定，避免
        内层引用/激活态包裹容器重复触发。
    is_flash：跳转目标行脉冲高亮（淡蓝底，animate 300ms 淡入/淡出）。
        与 is_current_line 可叠加：flash 更强且 1.2s 消失，current 持续。
    """
    c = _current_colors()
    pad_left = 0

    if is_flash:
        # 跳转脉冲高亮：淡蓝底，animate 使 flash_li 清回 -1 时 bgcolor 平滑淡出
        content = ft.Container(
            content=content,
            bgcolor=ft.Colors.with_opacity(0.18, c.link),
            border_radius=Radius.LG,
            animate=ft.Animation(300, ft.AnimationCurve.EASE_OUT),
        )

    if is_current_line:
        content = ft.Container(
            content=content,
            bgcolor=ft.Colors.with_opacity(0.22, c.active_bg),
            border_radius=Radius.LG,
            border=only_border(left=ft.BorderSide(3, c.link)),
            padding=ft.Padding.only(left=Spacing.MD),
        )

    if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O):
        pad_left = line.level * 20
    elif line.block_type == BlockType.QUOTE:
        lvl = line.level or 1
        for _ in range(lvl):
            content = ft.Container(
                content=content,
                padding=ft.Padding.only(left=Spacing.XL),
                border=only_border(left=ft.BorderSide(3, c.quote_bar)),
            )

    kwargs: dict = {
        "key": f"line-{line_idx}" if line_idx is not None else None,
        "content": content,
        "padding": ft.Padding.only(left=pad_left, top=Spacing.XS, bottom=Spacing.XS),
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
    on_change: Callable[[str], None],
    on_submit: Callable[[str], None] | None,
    on_focus: Callable | None,
    on_blur: Callable | None,
    on_selection_change: Callable | None,
) -> ft.TextField:
    """构造光标透明 TextField（Stack 顶层），像素定位到 cursor_off。

    li 传入 cursor_text_field 作为 key 主体，保证同行输入不重建控件（IME 友好）。

    任务行：Checkbox 替代了前缀，text_ctrl 只渲染内容（skip_prefix=True），
    cursor_overlay 在内容 Text 的 Stack 内，需减去前缀宽度使光标 X 相对内容起点。

    光标在段内：该段标记变灰可见占宽度（逐字符测量 raw）；其余段标记折叠。
    """
    offsets_x = _line_raw_offsets_x(line, base, cursor_raw_offset=cursor_off)
    off = max(0, min(cursor_off, len(offsets_x) - 1))
    cursor_px_x = offsets_x[off]
    if line.task and line.segments:
        prefix_raw = line.segments[0].raw
        prefix_len = len(prefix_raw) if prefix_raw else 0
        if 0 < prefix_len < len(offsets_x):
            cursor_px_x -= offsets_x[prefix_len]
    line_height_px = base * line_height
    return cursor_text_field(
        li=li,
        cursor_px_x=cursor_px_x,
        cursor_px_y=0.0,
        line_height_px=line_height_px,
        base_size=base,
        line_height=line_height,
        on_change=on_change,
        on_submit=on_submit,
        on_focus=on_focus,
        on_blur=on_blur,
        on_selection_change=on_selection_change,
        field_ref=field_ref,
        nav_seq=nav_seq,
        content_width=content_width,
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
    content_width: float | None = None,
    line_height: float = 1.6,
    is_current_line: bool = False,
    is_flash: bool = False,
    # 版本号触发 prop：reparse_line 就地修改 line 对象不替换引用，
    # ft.memo 浅比较 line 引用未变会误判未刷新。通过 raw 长度 + 段数
    # 两个值变化触发 memo 检测，让屏幕刷新。LineView 内部不读取这两个值。
    line_raw_version: int = 0,
    line_seg_count: int = 0,
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
    on_hit_test_x: Callable[[int, float], int] | None = None,
    on_hit_test_xy: Callable[[int, float, float], tuple[int, int] | None] | None = None,
    on_double_tap: Callable[[int, int], None] | None = None,
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
    其余 N-2 行 ft.memo 直接复用缓存，跳过 Python 函数体执行。
    """
    c = _current_colors()
    base = block_text_size(line.block_type, line.level)
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
        )

    # ============ 块级公式 MATH（视图态 ft.Markdown）============
    if line.block_type == BlockType.MATH:
        formula = line.segments[0].text if line.segments else ""
        md = ft.Markdown(
            value=f"$$\n{formula}\n$$",
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
        )
        content = ft.Container(
            content=md, bgcolor=c.math_bg, border_radius=Radius.MD, width=float("inf"),
            padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
            alignment=ft.Alignment.CENTER,
        )
        return _wrap_block(
            content, line, base, line_idx,
            is_current_line=is_current_line, is_flash=is_flash, on_size_change=on_line_size_change,
        )

    # ============ 分隔线 HR（视图态）============
    if line.block_type == BlockType.HR:
        content = ft.Container(
            content=ft.Divider(height=1, thickness=1, color=c.quote_bar),
            padding=ft.Padding.symmetric(vertical=Spacing.LG),
            ink=True,
        )
        return _wrap_block(
            content, line, base, line_idx,
            is_current_line=is_current_line, is_flash=is_flash, on_size_change=on_line_size_change,
        )

    # ============ 目录 [toc] ============
    if line.block_type == BlockType.TOC:
        toc_items: list[ft.Control] = [
            ft.Container(
                content=ft.Text(value=text, size=base - 1, color=c.text, font_family=FONT_MAIN),
                padding=ft.Padding.only(left=(lvl - 1) * Spacing.XXL),
                on_click=lambda e, t=li: on_jump_to(t) if on_jump_to else None,
                ink=True,
            )
            for li, lvl, text in (toc_entries or [])
        ]
        content = ft.Container(
            content=ft.Column(controls=toc_items, spacing=Spacing.XS),
            width=float("inf"),
            padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
            bgcolor=c.code_bg, border_radius=Radius.MD,
        )
        return _wrap_block(
            content, line, base, line_idx,
            is_current_line=is_current_line, is_flash=is_flash, on_size_change=on_line_size_change,
        )

    # ============ 普通文本行（段落/标题/列表/引用/空行）：RenderedLine + Stack ============
    cursor_off_val = effective_cursor_off if is_active else None
    overlay = None
    if is_active and on_cursor_change is not None:
        overlay = _cursor_overlay(
            line, base, line_height, effective_cursor_off, content_width, line_idx, nav_seq,
            field_ref, on_cursor_change, on_cursor_submit, on_cursor_focus,
            on_cursor_blur, on_selection_change,
        )

    inner = RenderedLine(
        line=line,
        line_idx=line_idx,
        cursor_off=cursor_off_val,
        base_size=base,
        line_height=line_height,
        content_width=content_width,
        cursor_overlay=overlay,
        on_tap=on_tap,
        on_pan_start=on_pan_start,
        on_pan_update=on_pan_update,
        on_toggle_task=on_toggle_task,
        outward_range=outward_range,
        on_extend_outward=on_extend_outward,
        on_clear_outward=on_clear_outward,
        shift_pressed_ref=shift_pressed_ref,
        ctrl_pressed_ref=ctrl_pressed_ref,
        on_hit_test_x=on_hit_test_x,
        on_hit_test_xy=on_hit_test_xy,
        on_double_tap=on_double_tap,
    )
    return _wrap_block(
        inner, line, base, line_idx,
        is_current_line=is_current_line, on_size_change=on_line_size_change,
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
    )
