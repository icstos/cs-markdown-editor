"""段级渲染：把 Segment 转为可点击的 TextSpan（渲染态）与内嵌编辑控件（编辑态）。

设计原则：
- 渲染态用 TextSpan 参与 Text 的整体排版（自动换行，符合阅读习惯）。
- 编辑态用一个"无框、同字号"的编辑控件内嵌进行编辑，仅当前段显示原生 Markdown，
  其余段仍为渲染样式——这就是 Typora 式"最小语法"段级编辑。
"""

from typing import Callable

import flet as ft
from flet_code_editor import CodeEditor, CodeLanguage, CodeTheme, GutterStyle

from models import BlockType, Line, SegType, Segment
from styles import (
    FONT_MAIN,
    FONT_MONO,
    _current_colors,
    block_weight,
    list_color_level,
    measure_text_width,
    only_border,
    prefix_style,
    segment_style,
)

_PREFIX_SEGTYPES = (SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX)
_MONO_SEGTYPES = (SegType.CODESPAN, SegType.CODE, SegType.INLINE_MATH, SegType.MATH)

# 包裹型段的标记字符（用于 _split_seg_for_display 拆分 raw）
_WRAP_CHAR: dict[SegType, str] = {
    SegType.STRONG: "**",
    SegType.EMPHASIS: "*",
    SegType.STRIKE: "~~",
    SegType.HIGHLIGHT: "==",
    SegType.SUPERSCRIPT: "^",
    SegType.SUBSCRIPT: "~",
}


def _open_link_url(url: str) -> None:
    """在系统浏览器中打开链接，不进入段级编辑。"""
    target = (url or "").strip()
    if not target:
        return
    page = ft.context.page
    if page is None:
        return

    async def _launch():
        await page.launch_url(target, web_popup_window_name=ft.UrlTarget.BLANK)

    page.run_task(_launch)


def _display_text(seg: Segment) -> str:
    """渲染态展示文本。"""
    if seg.seg_type == SegType.HEADING_PREFIX:
        return ""  # 渲染态不显示 # 前缀，用颜色区分标题级别
    if seg.seg_type == SegType.QUOTE_PREFIX:
        return ""  # 渲染态不显示 > 前缀，引用由左边框区分
    if seg.seg_type == SegType.LIST_PREFIX:
        # 无序列表标记渲染为圆点；有序列表保留 "N. " 形式
        # raw 可能含缩进空格，先 lstrip 再判断 marker
        raw = seg.raw.lstrip()
        if raw and raw[0] in "-*+":
            return "•  "
        return raw
    if seg.seg_type in _PREFIX_SEGTYPES:
        return seg.raw
    if seg.seg_type == SegType.IMAGE:
        return seg.text or "🖼"
    if seg.seg_type == SegType.LINK:
        return seg.text or seg.url or "链接"
    return seg.text


def segment_to_span(
    seg: Segment,
    seg_idx: int,
    on_activate: Callable[[int], None] | None,
    base_size: int,
    heading_level: int = 0,
) -> ft.TextSpan:
    """渲染态：段 -> TextSpan（可点击激活）。on_activate=None 时不绑定 on_click。

    heading_level > 0 时覆盖文字颜色为标题级别色（红橙绿青蓝紫）。
    无序列表前缀圆点按缩进级别复用同一套色阶。
    """
    c = _current_colors()  # 当前主题颜色（亮/暗）
    style = (
        prefix_style(seg, base_size)
        if seg.seg_type in _PREFIX_SEGTYPES
        else segment_style(seg, base_size)
    )
    if seg.seg_type == SegType.LIST_PREFIX:
        raw = seg.raw.lstrip()
        if raw and raw[0] in "-*+":
            lvl = list_color_level(seg.level)
            style = ft.TextStyle(
                size=style.size,
                weight=style.weight,
                color=c.heading_colors.get(lvl, c.muted),
                italic=style.italic,
                font_family=style.font_family,
                decoration=style.decoration,
                bgcolor=style.bgcolor,
            )
    if heading_level > 0:
        is_strong = seg.seg_type == SegType.STRONG or SegType.STRONG in (seg.marks or ())
        weight = (
            ft.FontWeight.BOLD
            if is_strong
            else block_weight(BlockType.HEADING, heading_level)
        )
        style = ft.TextStyle(
            size=style.size,
            weight=weight,
            color=c.heading_colors.get(heading_level, c.text),
            italic=style.italic,
            font_family=style.font_family,
            decoration=style.decoration,
            bgcolor=style.bgcolor,
        )
    kwargs: dict = {"text": _display_text(seg), "style": style}
    if seg.seg_type == SegType.LINK and seg.url:
        kwargs["on_click"] = lambda e, u=seg.url: _open_link_url(u)
    elif on_activate is not None:
        kwargs["on_click"] = lambda e: on_activate(seg_idx)
    return ft.TextSpan(**kwargs)


def _split_seg_for_display(seg: Segment) -> list[tuple[str, bool]]:
    """把段拆成 [(text, is_marker), ...]，拼接 == seg.raw。

    is_marker=True 的部分在 Typora 模式下可透明/灰色切换；
    is_marker=False 的部分为内容，按段样式渲染。
    """
    raw = seg.raw
    if not raw:
        return []

    t = seg.seg_type

    # 块级前缀段：整段为 marker
    if t in _PREFIX_SEGTYPES:
        return [(raw, True)]

    # 行内公式 $...$
    if t == SegType.INLINE_MATH:
        if len(raw) >= 2 and raw[0] == "$" and raw[-1] == "$":
            return [("$", True), (raw[1:-1], False), ("$", True)]
        return [(raw, False)]

    # 行内代码 `...`
    if t == SegType.CODESPAN:
        if len(raw) >= 2 and raw[0] == "`" and raw[-1] == "`":
            return [("`", True), (raw[1:-1], False), ("`", True)]
        return [(raw, False)]

    # 链接 [text](url)：用 "](" 定位分割，兼容 text/url 含特殊字符
    if t == SegType.LINK:
        if raw.startswith("[") and raw.endswith(")") and "](" in raw:
            idx = raw.index("](")
            text_part = raw[1:idx]
            url_part = raw[idx + 2:-1]
            return [("[", True), (text_part, False), ("](", True), (url_part, True), (")", True)]
        return [(raw, False)]

    # 图片 ![alt](url)
    if t == SegType.IMAGE:
        if raw.startswith("![") and raw.endswith(")") and "](" in raw:
            idx = raw.index("](")
            alt_part = raw[2:idx]
            url_part = raw[idx + 2:-1]
            return [("![", True), (alt_part, False), ("](", True), (url_part, True), (")", True)]
        return [(raw, False)]

    # 包裹型（含组合 marks）：前缀 = 各 mark 包裹符按外→内拼接；后缀 = 内→外
    marks = seg.marks or ()
    if not marks and t in _WRAP_CHAR:
        marks = (t,)
    if marks:
        prefix = "".join(_WRAP_CHAR[m] for m in marks)
        suffix = "".join(_WRAP_CHAR[m] for m in reversed(marks))
        if prefix and raw.startswith(prefix) and raw.endswith(suffix) and len(raw) >= len(prefix) + len(suffix):
            content = raw[len(prefix):len(raw) - len(suffix)] if suffix else raw[len(prefix):]
            return [(prefix, True), (content, False), (suffix, True)]
        return [(raw, False)]

    # TEXT / CODE / MATH：整段为内容
    return [(raw, False)]


def raw_to_visible_spans(
    line: Line,
    base_size: int,
    cursor_raw_offset: int | None = None,
    heading_level: int = 0,
) -> list[ft.TextSpan]:
    """把一行的 segments 渲染为可见 TextSpan 列表（拼接后 == line.raw）。

    Typora 式渲染（用于激活行 Stack 底层 Text）：
    - 语法标记（**、*、`、~~、==、^、~、$、[]()）默认透明（光标在段外）
    - 光标落在某段范围内时，该段所有标记变灰可见
    - 内容部分按 segment_style 渲染（含标题级别覆盖、列表圆点色阶）
    - 整段文本拼接 == line.raw，与透明 TextField.value 字符级对齐

    cursor_raw_offset=None 表示无光标信息，所有标记透明。
    """
    c = _current_colors()
    spans: list[ft.TextSpan] = []
    raw_offset = 0
    seg_count = len(line.segments)

    for seg_idx, seg in enumerate(line.segments):
        pieces = _split_seg_for_display(seg)
        seg_start = raw_offset
        seg_end = raw_offset + len(seg.raw)
        is_last = seg_idx == seg_count - 1

        # 光标是否在本段范围内（末段含右端点，其余段左闭右开）
        if cursor_raw_offset is None:
            cursor_in_seg = False
        elif is_last:
            cursor_in_seg = seg_start <= cursor_raw_offset <= seg_end
        else:
            cursor_in_seg = seg_start <= cursor_raw_offset < seg_end

        is_prefix = seg.seg_type in _PREFIX_SEGTYPES

        # 段基础样式
        if is_prefix:
            base_style = prefix_style(seg, base_size)
            # 无序列表圆点色阶
            if seg.seg_type == SegType.LIST_PREFIX:
                raw_ls = seg.raw.lstrip()
                if raw_ls and raw_ls[0] in "-*+":
                    lvl = list_color_level(seg.level)
                    base_style = ft.TextStyle(
                        size=base_style.size,
                        weight=base_style.weight,
                        color=c.heading_colors.get(lvl, c.muted),
                        italic=base_style.italic,
                        font_family=base_style.font_family,
                        decoration=base_style.decoration,
                        bgcolor=base_style.bgcolor,
                    )
        else:
            base_style = segment_style(seg, base_size)
            # 标题级别覆盖（颜色/字重）
            if heading_level > 0:
                is_strong = seg.seg_type == SegType.STRONG or SegType.STRONG in (seg.marks or ())
                weight = (
                    ft.FontWeight.BOLD
                    if is_strong
                    else block_weight(BlockType.HEADING, heading_level)
                )
                base_style = ft.TextStyle(
                    size=base_style.size,
                    weight=weight,
                    color=c.heading_colors.get(heading_level, c.text),
                    italic=base_style.italic,
                    font_family=base_style.font_family,
                    decoration=base_style.decoration,
                    bgcolor=base_style.bgcolor,
                )

        for text, is_marker in pieces:
            if not text:
                continue
            if is_marker:
                # 标记：透明（光标不在段内）/灰色（光标在段内）
                marker_color = c.muted if cursor_in_seg else ft.Colors.TRANSPARENT
                style = ft.TextStyle(
                    size=base_style.size,
                    weight=base_style.weight,
                    color=marker_color,
                    italic=base_style.italic,
                    font_family=base_style.font_family,
                )
                spans.append(ft.TextSpan(text=text, style=style))
            else:
                spans.append(ft.TextSpan(text=text, style=base_style))

        raw_offset = seg_end

    # 兜底：CODE/MATH 围栏块的 segment 只含内容（不含 $$/``` 围栏），
    # segments 拼接 != line.raw。这些块不使用 ActiveLineView（保留 CodeEditor），
    # 但为防止误调用导致拼接不一致，回退为单个 TextSpan(line.raw)。
    joined = "".join(s.text for s in spans)
    if joined != line.raw:
        return [ft.TextSpan(
            text=line.raw,
            style=ft.TextStyle(size=base_size, color=c.text, font_family=FONT_MAIN),
        )]
    return spans


def _code_language(lang: str | None) -> CodeLanguage:
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
    }
    key = aliases.get(key, key)
    return getattr(CodeLanguage, key, CodeLanguage.PYTHON)


def active_text_field(
    seg: Segment,
    draft: str,
    on_change: Callable[[str], None],
    on_submit: Callable[[str, str | None], None],
    on_blur: Callable[[], None],
    base_size: int,
    multiline: bool = False,
    on_selection_change: Callable | None = None,
    initial_cursor: int = -1,
    nav_seq: int = 0,
    field_ref: ft.Ref | None = None,
    max_width: float | None = None,
    line_height: float = 1.6,
    on_cursor_sync: Callable[[int, int], None] | None = None,
    block_language: str | None = None,
    use_code_editor: bool = False,
) -> ft.Control:
    """编辑态：段 -> 内嵌编辑控件，显示该段原生 Markdown。

    单行段：依据本地字体测量文本宽度，让编辑控件恰好包裹文本内容
    （Typora 式最小编辑块），避免撑满整行破坏阅读节奏。
    多行代码块：保持块级宽度，由父容器决定。

    宽度溢出处理：当单行段原生文本宽度超出可用区域（max_width）时，
    切换为多行换行编辑（宽度=可用宽度），避免横向溢出破坏布局；
    此时上下键在段内换行间移动，Enter 仍提交（Shift+Enter 才插入换行）。

    光标导航：
    - on_selection_change：上报光标位置变化（供外层跟踪 extent/base）
    - initial_cursor + on_focus：跨段时通过 nav_seq 变化触发 key 重建，
      autofocus 聚焦后 on_focus 强制把光标设到 initial_cursor（段首/段尾）。
      cursor_applied 标志确保仅应用一次，后续聚焦不覆盖用户光标位置。
    - ignore_up_down_keys：单行段置 True，让上下键冒泡到外层做跨行；
      多行块（代码块/溢出换行段）保持 False，让上下键在块内移动光标。
    """
    c = _current_colors()  # 当前主题颜色（亮/暗）
    is_mono = seg.seg_type in _MONO_SEGTYPES
    font_family = FONT_MONO if is_mono else FONT_MAIN
    text_size = base_size if not is_mono else max(base_size - 1, 12)

    # autofocus 在 SelectionArea 内点击 span 时不可靠（手势竞争导致不触发 focus）。
    # 用 on_focus 在聚焦后强制设置光标位置（每次 TextField 重建后应用一次）。
    # 声明式模式下控件被冻结（_frozen），需临时解冻才能命令式设置 selection 并 update。
    #
    # stale 段尾事件拦截由 editor.py 的 applied_cursor 机制负责（on_selection_change
    # 中判断 applied_cursor != extent 且 extent == len(value) 则丢弃）。
    # 不在此处用 cursor_applied 局部变量拦截：它每次渲染都重置为 False，
    # 会导致 on_change_draft → set_draft 触发重渲染后，正常的 on_selection_change
    # 被错误拦截，cursor_ref.draft_len 不更新，持续 Delete 到段尾时误判光标不在段尾。
    cursor_applied = [False]

    def _on_focus(e):
        if not cursor_applied[0]:
            cursor_applied[0] = True
            pos = initial_cursor if initial_cursor >= 0 else len(draft)
            ctrl = e.control
            frozen = getattr(ctrl, "_frozen", None)
            if frozen is not None:
                del ctrl._frozen
            try:
                ctrl.selection = ft.TextSelection(base_offset=pos, extent_offset=pos)
                ctrl.update()
            finally:
                if frozen is not None:
                    ctrl._frozen = frozen
            # 直接同步光标到外层 cursor_ref：Flutter 聚焦时先触发 on_focus，
            # 再触发默认 on_selection_change(段尾)，后者会覆盖 _sync_cursor 的正确值。
            # 通过此回调把正确位置同步给 editor，并在 editor 端用 applied_cursor
            # 识别并丢弃紧随其后的 stale 段尾事件。
            if on_cursor_sync is not None:
                on_cursor_sync(pos, len(draft))

    kwargs: dict = {
        "key": f"field-{nav_seq}",
        "value": draft,
        "autofocus": True,
        "multiline": multiline,
        "min_lines": 1,
        "max_lines": None if multiline else 1,
        "border": ft.InputBorder.NONE,
        "border_radius": 4,
        "filled": True,
        "fill_color": c.active_bg,
        "dense": True,
        "content_padding": ft.Padding.symmetric(horizontal=4, vertical=0),
        "text_size": text_size,
        "text_style": ft.TextStyle(font_family=font_family, color=c.text),
        "strut_style": ft.StrutStyle(
            force_strut_height=True, height=line_height, leading=0,
            size=text_size, font_family=font_family,
        ),
        "cursor_color": c.text,
        "cursor_width": 1.5,
        "shift_enter": multiline,
        "ignore_up_down_keys": not multiline,  # 单行段让上下键冒泡到外层跨行
        "on_focus": _on_focus,
        "on_change": lambda e: on_change(e.control.value),
        "on_submit": lambda e: on_submit(e.control.value),
        "on_blur": lambda e: on_blur(),
    }
    if on_selection_change is not None:
        # 直接透传 on_selection_change，stale 段尾事件由 editor.py 的
        # applied_cursor 机制拦截（不在此处用 cursor_applied 拦截，
        # 因为它每次渲染重置会导致正常事件被错误丢弃）。
        kwargs["on_selection_change"] = on_selection_change

    if field_ref is not None:
        kwargs["ref"] = field_ref

    if use_code_editor:
        editor_width = max_width if max_width is not None else None
        page = ft.context.page
        is_dark = page is not None and page.theme_mode == ft.ThemeMode.DARK
        code_theme = CodeTheme.ATOM_ONE_DARK if is_dark else CodeTheme.GITHUB
        line_count = max(1, draft.count("\n") + 1)
        digits = len(str(line_count))
        # 代码块行号按位数自动扩展，避免长代码块在两位/三位数时被挤压换行。
        # 同时用浅色/深色不同的 gutter 背景和右侧分隔线增强 IDE 式层次感。
        gutter_width = max(56, 24 + digits * 16)
        gutter_bg = ft.Colors.with_opacity(0.22 if is_dark else 0.07, c.text)
        gutter_border = ft.Colors.with_opacity(0.10 if is_dark else 0.14, c.border)
        editor = CodeEditor(
            value=draft,
            language=_code_language(block_language),
            code_theme=code_theme,
            gutter_style=GutterStyle(
                width=gutter_width,
                margin=0,
                show_line_numbers=True,
                show_errors=True,
                show_folding_handles=True,
                background_color=gutter_bg,
                text_style=ft.TextStyle(
                    font_family=FONT_MONO,
                    size=max(text_size - 4, 10),
                    color=c.muted,
                ),
            ),
            read_only=False,
            autofocus=True,
            text_style=ft.TextStyle(font_family=FONT_MONO, size=text_size, color=c.text),
            padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        )
        if field_ref is not None:
            editor.ref = field_ref
        if on_selection_change is not None:
            editor.on_selection_change = on_selection_change
        if on_blur is not None:
            editor.on_blur = lambda e: on_blur()
        if on_change is not None:
            editor.on_change = lambda e: on_change(e.control.value)
        return ft.Container(
            content=editor,
            width=editor_width,
            expand=editor_width is None,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            border=only_border(right=ft.BorderSide(1, gutter_border)),
            animate=ft.Animation(duration=180, curve=ft.AnimationCurve.EASE_OUT),
        )

    if not multiline:
        # 文本像素宽 + 余量；空文本给最小宽避免坍缩。
        # Pillow(FreeType) getlength 略小于 Flutter/Skia 实际渲染宽度
        # （字形度量/整形差异），按 6% 比例放大吸收差异；
        # 固定 18px 覆盖 TextField 内边距(8) + 光标(1.5) + 内部留白，避免文本被裁切。
        text_w = measure_text_width(draft or "", font_family, text_size)
        natural_w = max(text_w * 1.06 + 18, 28)
        if max_width is not None and natural_w > max_width:
            # 单段原生文本超出可用宽度：转为多行换行编辑，宽度撑满可用区域，
            # 上下键在段内换行间移动，Enter 仍提交（Shift+Enter 才插入换行）。
            kwargs["multiline"] = True
            kwargs["max_lines"] = None
            kwargs["shift_enter"] = True
            kwargs["ignore_up_down_keys"] = False
            kwargs["width"] = max_width
        else:
            kwargs["width"] = natural_w
    else:
        # 多行代码块：撑满整行宽度
        kwargs["expand"] = True

    return ft.TextField(**kwargs)
