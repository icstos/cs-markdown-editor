"""行视图：Stack 双层叠加（渲染层 + 透明光标编辑层）。

架构（Typora 式 WYSIWYG 光标级实时渲染）：
- 非激活行：RenderedLine 纯渲染（语法标记透明）
- 激活行：ft.Stack([RenderedLine, cursor_text_field])，光标 TextField 始终 value=""
  每个字符输入即时渲染到文档，光标像素级对齐渲染层文字间隙
- 围栏岛屿（CODE/MATH/HR/TOC）：保留独立分支，不进入 Stack
  CODE 用 CodeEditor 始终可编辑；MATH/HR/TOC 视图态渲染

状态由 editor.py 驱动：cursor_li/cursor_off/nav_seq。本文件只负责渲染 + 命中。
"""

import asyncio
from typing import Callable

import flet as ft
from flet_code_editor import CodeEditor, CodeLanguage, CodeTheme, GutterStyle

from models import BlockType, Line
from styles import (
    FONT_MAIN,
    FONT_MONO,
    _current_colors,
    block_text_size,
    block_weight,
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
        return CodeLanguage.PYTHON
    key = lang.strip().replace("-", "_").replace(" ", "").upper()
    aliases = {
        "JS": "JAVASCRIPT",
        "TS": "TYPESCRIPT",
        "PY": "PYTHON",
        "C++": "CPP",
        "C#": "C_SHARP",
        "SH": "SHELL",
        "BASH": "SHELL",
        "ZSH": "SHELL",
        "YAML": "YML",
        "CSHARP": "C_SHARP",
    }
    key = aliases.get(key, key)
    return getattr(CodeLanguage, key, CodeLanguage.PYTHON)


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
) -> ft.Control:
    """包一层块级容器：缩进、引用边框、当前行高亮。

    on_click：挂到最外层 Container 的点击回调（padding 死区兜底）。
    """
    c = _current_colors()
    pad_left = 0

    if is_current_line:
        content = ft.Container(
            content=content,
            bgcolor=ft.Colors.with_opacity(0.22, c.active_bg),
            border_radius=8,
            border=only_border(left=ft.BorderSide(3, c.link)),
            padding=ft.Padding.only(left=6),
        )

    if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O):
        pad_left = line.level * 20
    elif line.block_type == BlockType.QUOTE:
        lvl = line.level or 1
        for _ in range(lvl):
            content = ft.Container(
                content=content,
                padding=ft.Padding.only(left=12),
                border=only_border(left=ft.BorderSide(3, c.quote_bar)),
            )

    kwargs: dict = {
        "key": f"line-{line_idx}" if line_idx is not None else None,
        "content": content,
        "padding": ft.Padding.only(left=pad_left, top=2, bottom=2),
        "margin": ft.Margin.all(0),
        "ink": False,
    }
    if on_click is not None:
        kwargs["on_click"] = on_click
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
    )


@ft.component
def LineView(
    line: Line,
    line_idx: int,
    *,
    cursor_li: int | None = None,
    cursor_off: int = 0,
    cursor_ref: ft.Ref | None = None,
    nav_seq: int = 0,
    field_ref: ft.Ref | None = None,
    content_width: float | None = None,
    line_height: float = 1.6,
    is_current_line: bool = False,
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
    # 向外选区
    outward_range: tuple[int, int] | None = None,
    on_extend_outward: Callable[[int, int], None] | None = None,
    on_clear_outward: Callable[[], None] | None = None,
    shift_pressed_ref: ft.Ref | None = None,
    ctrl_pressed_ref: ft.Ref | None = None,
    on_hit_test_x: Callable[[int, float], int] | None = None,
) -> ft.Control:
    """渲染一行：围栏块走独立分支，普通文本行走 RenderedLine + Stack。

    cursor_ref：激活行的光标位置 ref（CursorState）。
    handle_char_input 中不调用 set_cursor_off（避免重渲染打断 IME），
    光标位置仅由 cursor_ref.current.base 跟踪。
    LineView 通过 cursor_ref 读取最新光标位置，计算光标像素坐标。
    parser.reparse_line 触发的重渲染会重新调用 LineView，此时读取
    cursor_ref.current.base 获取最新光标位置，实现光标实时跟随。
    """
    c = _current_colors()
    base = block_text_size(line.block_type, line.level)
    is_active = cursor_li == line_idx and cursor_li is not None

    # 激活行：优先使用 cursor_ref.current.base（IME 组合期间最新位置）
    # cursor_off state 在 IME 组合期间不更新（避免重渲染打断 IME），
    # 仅在 _end_input_session 中同步。cursor_ref 实时跟踪最新位置。
    effective_cursor_off = cursor_off
    if is_active and cursor_ref is not None and cursor_ref.current is not None:
        ref_off = getattr(cursor_ref.current, "base", None)
        if ref_off is not None and ref_off >= 0:
            effective_cursor_off = ref_off

    # ============ 代码块（始终可编辑 CodeEditor 独立岛屿）============
    if line.block_type == BlockType.CODE:
        return _render_code_block(
            line, line_idx, base, content_width, clipboard_ref,
            on_change_code, on_code_focus, on_code_blur, on_change_lang,
            code_field_ref, is_current_line,
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
            content=md, bgcolor=c.math_bg, border_radius=6, width=float("inf"),
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            alignment=ft.Alignment.CENTER,
        )
        return _wrap_block(content, line, base, line_idx, is_current_line=is_current_line)

    # ============ 分隔线 HR（视图态）============
    if line.block_type == BlockType.HR:
        content = ft.Container(
            content=ft.Divider(height=1, thickness=1, color=c.quote_bar),
            padding=ft.Padding.symmetric(vertical=8),
            ink=True,
        )
        return _wrap_block(content, line, base, line_idx, is_current_line=is_current_line)

    # ============ 目录 [toc] ============
    if line.block_type == BlockType.TOC:
        toc_items: list[ft.Control] = [
            ft.Container(
                content=ft.Text(value=text, size=base - 1, color=c.text, font_family=FONT_MAIN),
                padding=ft.Padding.only(left=(lvl - 1) * 16),
                on_click=lambda e, t=li: on_jump_to(t) if on_jump_to else None,
                ink=True,
            )
            for li, lvl, text in (toc_entries or [])
        ]
        content = ft.Container(
            content=ft.Column(controls=toc_items, spacing=2),
            width=float("inf"),
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            bgcolor=c.code_bg, border_radius=6,
        )
        return _wrap_block(content, line, base, line_idx, is_current_line=is_current_line)

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
    )
    return _wrap_block(inner, line, base, line_idx, is_current_line=is_current_line)


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
) -> ft.Control:
    """代码块分支：CodeEditor 始终可编辑独立岛屿。"""
    c = _current_colors()
    code = line.segments[0].text if line.segments else ""
    lang = line.lang or ""
    page = ft.context.page
    is_dark = page is not None and page.theme_mode == ft.ThemeMode.DARK
    code_theme = CodeTheme.ATOM_ONE_DARK if is_dark else CodeTheme.GITHUB

    lang_dropdown = ft.Dropdown(
        value=lang,
        options=_lang_options(lang),
        width=160,
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

    copied, set_copied = ft.use_state(False)
    copy_btn = ft.IconButton(
        icon=ft.Icons.CHECK if copied else ft.Icons.CONTENT_COPY,
        icon_size=14,
        tooltip="已复制" if copied else "复制代码",
        padding=6,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=6),
            color=ft.Colors.GREEN if copied else c.muted,
        ),
        on_click=lambda e, txt=code: (
            page.run_task(_copy_code_to_clipboard, clipboard_ref, txt, set_copied)
            if page is not None and not copied else None
        ),
    )

    line_count = max(1, code.count("\n") + 1)
    digits = len(str(line_count))
    gutter_width = max(48, 24 + digits * 12) + 8
    gutter_bg = ft.Colors.with_opacity(0.22 if is_dark else 0.04, c.text)
    editor_height = max(line_count * 20 + 16, 52)

    editor = CodeEditor(
        key=f"code-{line_idx}",
        value=code,
        language=_code_language(lang),
        code_theme=code_theme,
        gutter_style=GutterStyle(
            width=gutter_width,
            margin=8,
            show_line_numbers=True,
            show_errors=False,
            show_folding_handles=False,
            background_color=gutter_bg,
            text_style=ft.TextStyle(font_family=FONT_MONO, size=11, color=c.muted),
        ),
        text_style=ft.TextStyle(font_family=FONT_MONO, size=14, color=c.text),
        padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        height=editor_height,
        read_only=False,
        autofocus=False,
        on_change=lambda e: (
            on_change_code(line_idx, e.control.value)
            if on_change_code is not None else None
        ),
        on_focus=lambda e: on_code_focus(line_idx) if on_code_focus is not None else None,
        on_blur=lambda e: on_code_blur(line_idx) if on_code_blur is not None else None,
    )
    if code_field_ref is not None:
        editor.ref = code_field_ref

    header = ft.Row(
        controls=[lang_dropdown, ft.Container(expand=True), copy_btn],
        spacing=6,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    content = ft.Container(
        content=ft.Column([header, editor], spacing=4),
        bgcolor=c.code_block_bg,
        border_radius=6,
        padding=ft.Padding.only(left=6, right=8, top=2, bottom=8),
    )
    return _wrap_block(
        content, line, line_idx,
        on_click=(lambda e: on_code_focus(line_idx)) if on_code_focus is not None else None,
        is_current_line=is_current_line,
    )
