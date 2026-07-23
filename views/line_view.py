"""行视图：把一行渲染为可读样式，并在该行处于编辑态时进行段级布局。

布局策略（兼顾排版与段级编辑）：
- 非编辑态：整行作为单个 ft.Text(spans=[...])，自动换行、排版美观。
- 编辑态（该行某段被激活）：拆为 [前段 Text] + [激活段编辑器] + [后段 Text]，
  仅激活段显示原生 Markdown，其余段保持渲染样式——Typora 式最小语法编辑。
特殊块（代码块 / 分隔线 / 空行）单独处理。
"""

from typing import Callable
import asyncio

import flet as ft
from flet_code_editor import CodeEditor, CodeLanguage, CodeTheme, GutterStyle

from models import BlockType, Line, Segment, SegType
from styles import (
    FONT_MAIN,
    FONT_MONO,
    _current_colors,
    block_text_size,
    block_weight,
    image_fit_size,
    measure_text_width,
    only_border,
)
from views.segment_view import (
    _MONO_SEGTYPES,
    _display_text,
    _open_link_url,
    active_text_field,
    segment_to_span,
    segment_to_spans_partial,
    selection_highlight_bg,
)


# 代码块语言选择下拉框的常用语言清单（key 为 markdown 围栏标识，text 为展示名）。
# 支持可搜索：用户可在下拉框中输入关键字过滤。文档中已存在但不在清单内的语言
# 会被动态追加为额外选项，避免显示为空。
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
    """把 markdown 围栏语言标识映射为 CodeEditor 的 CodeLanguage 枚举。

    未知语言回退到 PYTHON（CodeEditor 仍可编辑，仅不高亮）。
    """
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

_PREFIX_SEGTYPES = (SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX)


def _seg_font_size(seg: Segment, base: int) -> tuple[str, int]:
    """获取段的字体和字号（与 segment_view 的渲染样式一致）。"""
    is_mono = seg.seg_type in _MONO_SEGTYPES
    return (FONT_MONO if is_mono else FONT_MAIN,
            max(base - 1, 12) if is_mono else base)


def _display_to_raw_offset(seg: Segment, display_offset: int) -> int:
    """将展示文本偏移映射到原始 Markdown 偏移。

    对于 **bold** / `code` 等含语法的段，display text 是去除外壳后的内容，
    需要加上语法前缀长度才能得到 raw 中的偏移。
    """
    display = _display_text(seg)
    raw = seg.raw
    if not display or display_offset <= 0:
        return 0
    if display_offset >= len(display):
        return len(raw)
    if display in raw:
        prefix_len = raw.index(display)
        return min(prefix_len + display_offset, len(raw))
    return len(raw)


def _logical_raw_offset(line: Line, seg_idx: int, seg_offset: int) -> int:
    """段内 raw 偏移 → 整行 raw 逻辑偏移。"""
    return sum(len(line.segments[i].raw) for i in range(seg_idx)) + seg_offset


def _hit_test_tap(line: Line, x: float, y: float, base: int, line_height: float = 1.6) -> tuple[int, int]:
    """根据点击位置计算 (seg_idx, raw_cursor_offset)。

    通过累加各段展示文本宽度做命中测试，再用 measure_text_width
    逐字逼近找到字符偏移。多行文本（y 超过行高）回退到 (-1, -1)。
    """
    return _hit_test_segs(line, 0, len(line.segments), x, y, base, line_height)


def _hit_test_segs(
    line: Line, seg_from: int, seg_to_excl: int,
    x: float, y: float, base: int, line_height: float = 1.6,
) -> tuple[int, int]:
    """命中测试一个段范围 [seg_from, seg_to_excl)。x 相对范围起点。

    用于编辑态 before/after Text 的精确点击定位（GestureDetector 坐标
    相对于 Text 控件，而非整行）。
    """
    if y > base * line_height:
        return (-1, -1)
    acc = 0.0
    for i in range(seg_from, seg_to_excl):
        if i >= len(line.segments):
            break
        seg = line.segments[i]
        display = _display_text(seg)
        font, size = _seg_font_size(seg, base)
        w = measure_text_width(display, font, size)
        if x < acc + w or i == seg_to_excl - 1:
            local_x = x - acc
            # 中点吸附：找到 local_x 落入的字符区间 [prev_w, cur_w)，
            # 比较与中点决定光标落在该字符前(左半, j-1)或后(右半, j)。
            # 修复最左边缘 local_x=0 → 中点 (0+w1)/2 > 0 → 落左半 → disp_off=0
            # （原「宽度 >= local_x」阈值会落到第 1 字符之后，off-by-one）。
            disp_off = len(display)
            prev_w = 0.0
            for j in range(1, len(display) + 1):
                cur_w = measure_text_width(display[:j], font, size)
                if local_x < cur_w:
                    mid = (prev_w + cur_w) / 2
                    disp_off = j if local_x >= mid else j - 1
                    break
                prev_w = cur_w
            return (i, _display_to_raw_offset(seg, disp_off))
        acc += w
    return (-1, -1)


def _hit_test_x(line: Line, x: float, base: int, line_height: float = 1.6) -> tuple[int, int]:
    """y 无关命中测试：直接对全行段做 x 命中（供跨行拖拽复用）。

    与 _hit_test_tap 的区别：不基于 y 判定多行（y 固定 0.0），
    用于跨行 pan 时用同一 x 列定位目标行偏移。返回 (seg_idx, raw_offset)。
    """
    return _hit_test_segs(line, 0, len(line.segments), x, 0.0, base, line_height)


def _spans_for(
    line: Line,
    seg_from: int,
    seg_to_excl: int,
    on_activate: Callable[[int], None] | None,
    base_size: int,
    line_highlight_range: tuple[int, int] | None = None,
) -> list[ft.TextSpan]:
    """构造 [seg_from, seg_to_excl) 范围的 TextSpan 列表。

    line_highlight_range：本行向外选区高亮范围 (start_off, end_off)（行级 raw 偏移），
    非 None 时落入此范围的段注入 highlight_bg。
    """
    heading_level = line.level if line.block_type == BlockType.HEADING else 0
    hl_bg = selection_highlight_bg() if line_highlight_range is not None else None
    spans: list[ft.TextSpan] = []
    for i in range(seg_from, seg_to_excl):
        if i >= len(line.segments):
            break
        seg = line.segments[i]
        if line_highlight_range is not None:
            seg_start = sum(len(line.segments[j].raw) for j in range(i))
            seg_end = seg_start + len(seg.raw)
            hl_s, hl_e = line_highlight_range
            inter_start = max(seg_start, hl_s)
            inter_end = min(seg_end, hl_e)
            if inter_start < inter_end:
                # 有交集：判断是整段覆盖还是部分覆盖
                if hl_s <= seg_start and seg_end <= hl_e:
                    # 整段在范围内：整段高亮
                    spans.append(
                        segment_to_span(
                            seg, i, on_activate, base_size, heading_level,
                            highlight_bg=hl_bg,
                        )
                    )
                else:
                    # 部分覆盖：字符级拆分高亮
                    spans.extend(
                        segment_to_spans_partial(
                            seg, i, on_activate, base_size, heading_level,
                            hl_start_local=inter_start - seg_start,
                            hl_end_local=inter_end - seg_start,
                        )
                    )
            else:
                # 不在范围内
                spans.append(
                    segment_to_span(
                        seg, i, on_activate, base_size, heading_level,
                    )
                )
        else:
            spans.append(
                segment_to_span(
                    seg, i, on_activate, base_size, heading_level,
                )
            )
    return spans


def _has_visible_text(line: Line) -> bool:
    """是否有可见文本或前缀段。"""
    for s in line.segments:
        if s.text or s.seg_type in _PREFIX_SEGTYPES:
            return True
    return False


def _image_seg_indices(line: Line) -> list[int]:
    """返回行内 IMAGE 段索引。

    若行内含 IMAGE 以外的非空文本段（混合行），返回空列表——此类行
    仍按普通文本渲染，避免图片与文字混排时布局错乱。
    """
    idxs: list[int] = []
    for i, s in enumerate(line.segments):
        if s.seg_type == SegType.IMAGE:
            idxs.append(i)
        elif s.seg_type == SegType.TEXT and not s.text.strip():
            continue
        else:
            return []
    return idxs


def _active_field(
    line: Line,
    draft: str,
    on_change_draft: Callable[[str], None],
    on_submit: Callable[[str], None],
    on_blur: Callable[[], None],
    on_selection_change: Callable | None,
    initial_cursor: int,
    nav_seq: int,
    base_size: int | None = None,
    multiline: bool = False,
    field_ref: ft.Ref | None = None,
    max_width: float | None = None,
    line_height: float = 1.6,
    on_cursor_sync: Callable[[int, int], None] | None = None,
    seg: Segment | None = None,
) -> ft.Control:
    """构造激活态编辑控件（统一入口，消除重复调用）。

    seg 默认取 line.segments[0]；标题/普通编辑态传入显式 seg 以处理空 segments
    兜底或按 active_seg 索引取段。代码块不经过此入口——直接在 CODE 分支渲染
    始终可编辑的 CodeEditor。
    """
    return active_text_field(
        seg if seg is not None else line.segments[0],
        draft,
        on_change_draft,
        on_submit,
        on_blur,
        base_size=base_size if base_size is not None else block_text_size(line.block_type, line.level),
        multiline=multiline,
        on_selection_change=on_selection_change,
        initial_cursor=initial_cursor,
        nav_seq=nav_seq,
        field_ref=field_ref,
        max_width=max_width,
        line_height=line_height,
        on_cursor_sync=on_cursor_sync,
    )


@ft.component
def LineView(
    line: Line,
    line_idx: int,
    active_seg: int | None,
    draft: str,
    on_activate: Callable[[int, int], None],
    on_change_draft: Callable[[str], None],
    on_submit: Callable[[str], None],
    on_blur: Callable[[], None],
    on_selection_change: Callable | None = None,
    on_toggle_task: Callable[[int], None] | None = None,
    toc_entries: list[tuple[int, int, str]] | None = None,
    on_jump_to: Callable[[int], None] | None = None,
    on_change_lang: Callable[[int, str], None] | None = None,
    on_suppress_blur: Callable[[], None] | None = None,
    on_change_code: Callable[[int, str], None] | None = None,
    on_code_focus: Callable[[int], None] | None = None,
    on_code_blur: Callable[[int], None] | None = None,
    code_field_ref: ft.Ref | None = None,
    initial_cursor: int = -1,
    nav_seq: int = 0,
    field_ref: ft.Ref | None = None,
    content_width: float | None = None,
    line_height: float = 1.6,
    on_cursor_sync: Callable[[int, int], None] | None = None,
    is_current_line: bool = False,
    clipboard_ref: ft.Ref | None = None,
    outward_range: tuple[int, int] | None = None,
    on_extend_outward: Callable[[int, int], None] | None = None,
    shift_pressed_ref: ft.Ref | None = None,
    on_clear_outward: Callable[[], None] | None = None,
    on_hit_test_x: Callable[[int, float], int] | None = None,
):
    c = _current_colors()  # 当前主题颜色（亮/暗）
    base = block_text_size(line.block_type, line.level)
    weight = block_weight(line.block_type, line.level)
    line_style = ft.TextStyle(
        size=base, weight=weight, color=c.text, font_family=FONT_MAIN, height=line_height
    )

    # 编辑态 TextField 的最大可用宽度：内容区宽度 - 块级缩进 - 行内边距(8*2)。
    # 用于在单段文本过长时切换为多行换行编辑，避免横向溢出。
    if content_width is not None:
        indent = 0
        if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O):
            indent = line.level * 20
        elif line.block_type == BlockType.QUOTE:
            indent = (line.level or 1) * 12
        avail_width = max(content_width - indent - 16, 80)
    else:
        avail_width = None

    def activate(seg_idx: int = 0, cursor_at: int = -1):
        """段级激活：透传 seg_idx 给外层 on_activate。"""
        on_activate(line_idx, seg_idx, cursor_at)

    # 闭包共享标志：GestureDetector.on_tap 处理 Shift+Click 后置 True，
    # 供后续可能触发的 Container.on_click 检测并跳过（避免 activate() 覆盖
    # _start_outward 的 set_active(None)，导致选区不可见）。
    # 每次 LineView 渲染重建（新闭包新 list），同次事件同步帧内可见。
    _shift_tap_handled = [False]

    def _edit_on_click_factory(seg_idx: int):
        """编辑态点击右侧空白：抑制 blur + 激活指定段尾。

        Shift+Click 处理：
        - 若 _shift_tap_handled[0] 为 True，说明内层 GestureDetector 已处理
          span 上的精确 Shift+Click（on_extend_outward 已用精确 offset 调用），
          此处跳过避免 activate() 覆盖 _start_outward 的 set_active(None)。
        - 若 Shift 按下但 _shift_tap_handled[0] 为 False，说明点击落在右侧空白区
          （无 GestureDetector 覆盖），起始/扩展向外选区到行尾。
        """
        def _handler(e):
            if _shift_tap_handled[0]:
                # GestureDetector 已处理 Shift+Click：跳过 activate，避免覆盖
                _shift_tap_handled[0] = False
                return
            if shift_pressed_ref is not None and bool(shift_pressed_ref.current):
                # Shift+Click 右侧空白：起始/扩展向外选区到行尾
                if on_extend_outward is not None:
                    line_end_off = sum(len(s.raw) for s in line.segments)
                    on_extend_outward(line_idx, line_end_off)
                return
            if on_suppress_blur:
                on_suppress_blur()
            activate(seg_idx, cursor_at=-1)
        return _handler

    # 行间空白死区点击兜底：激活最后一个段（与 _on_tap 回退策略一致）。
    # 内层 GestureDetector 会消费其覆盖区域的 tap，此回调仅在 padding 死区触发。
    # Shift+Click 死区：起始/扩展向外选区到行尾（与 _edit_on_click_factory 一致）。
    def _fallback_activate(e):
        if _shift_tap_handled[0]:
            # GestureDetector 已处理 Shift+Click：跳过，避免覆盖精确 offset
            _shift_tap_handled[0] = False
            return
        if shift_pressed_ref is not None and bool(shift_pressed_ref.current):
            # Shift+Click 死区空白：起始/扩展向外选区到行尾
            if on_extend_outward is not None:
                line_end_off = sum(len(s.raw) for s in line.segments)
                on_extend_outward(line_idx, line_end_off)
            return
        activate(max(0, len(line.segments) - 1))

    # ============ 空行 ============
    if line.block_type == BlockType.BLANK or not _has_visible_text(line):
        if active_seg is not None:
            field = _active_field(
                line, draft, on_change_draft, on_submit, on_blur,
                on_selection_change, initial_cursor, nav_seq,
                base_size=base, field_ref=field_ref, max_width=avail_width,
                line_height=line_height, on_cursor_sync=on_cursor_sync,
            )
            # 编辑态：设置 width=float("inf") 占满整行，点击右侧空白时抑制 blur 并激活行尾
            content = ft.Container(
                content=field,
                padding=ft.Padding.symmetric(horizontal=2),
                width=float("inf"),
                on_click=_edit_on_click_factory(max(0, len(line.segments) - 1)),
                ink=True,
            )
        else:
            # Container.on_click 处理整行点击（含右侧空白），Text 占满宽度。
            # autofocus 由 editor.py 的 use_effect 显式调用 focus() 兜底。
            content = ft.Container(
                content=ft.Text(
                    spans=[
                        ft.TextSpan(" ", style=line_style)
                    ],
                    style=line_style,
                    width=float("inf"),
                ),
                height=max(base * line_height, 24),
                padding=ft.Padding.symmetric(horizontal=2),
                ink=True,
                on_click=lambda e: activate(0),
            )
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate, is_current_line=is_current_line)

    # ============ 分隔线 ============
    if line.block_type == BlockType.HR:
        if active_seg is not None:
            field = _active_field(
                line, draft, on_change_draft, on_submit, on_blur,
                on_selection_change, initial_cursor, nav_seq,
                field_ref=field_ref, max_width=avail_width,
                line_height=line_height, on_cursor_sync=on_cursor_sync,
            )
            content = ft.Container(
                content=field,
                padding=ft.Padding.symmetric(vertical=6),
                width=float("inf"),
                on_click=_edit_on_click_factory(0),
                ink=True,
            )
        else:
            content = ft.Container(
                content=ft.Divider(height=1, thickness=1, color=c.quote_bar),
                padding=ft.Padding.symmetric(vertical=8),
                on_click=lambda e: on_activate(line_idx, 0),
                ink=True,
            )
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate, is_current_line=is_current_line)

    # ============ 代码块 ============
    # 始终可编辑的 CodeEditor（Typora / VSCode 式）：语法高亮 + 行号 + 语言选择，
    # 点击即编辑，无需"激活"步骤。代码块不纳入 active/draft 系统，作为独立可编辑岛屿：
    # on_change 原地更新行模型（不触发 observable 重渲染，避免光标跳动），
    # 由外层 on_change_code 负责。全局按键在 CodeEditor 聚焦时交由其原生处理。
    if line.block_type == BlockType.CODE:
        code = line.segments[0].text if line.segments else ""
        lang = line.lang or ""
        page = ft.context.page
        is_dark = page is not None and page.theme_mode == ft.ThemeMode.DARK
        code_theme = CodeTheme.ATOM_ONE_DARK if is_dark else CodeTheme.GITHUB

        # 语言选择下拉框（可搜索）：on_change_lang 按 line_idx 更新围栏语言
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

        # 复制按钮
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

        # 行号 gutter 宽度按代码行数位数自适应，避免长代码块行号被挤压
        line_count = max(1, code.count("\n") + 1)
        digits = len(str(line_count))
        gutter_width = max(48, 24 + digits * 12)+8
        gutter_bg = ft.Colors.with_opacity(0.22 if is_dark else 0.04, c.text)
        # 编辑器高度按行数自适应；on_change_code 在行数变化时触发重渲染以更新高度
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
                text_style=ft.TextStyle(
                    font_family=FONT_MONO,
                    size=11,
                    color=c.muted,
                ),
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
        # 代码块作为独立编辑岛屿，行间空白点击仅更新 cursor_line（不进入 active 系统）
        return _wrap_block(
            content, line, base, line_idx,
            on_click=(lambda e: on_code_focus(line_idx)) if on_code_focus is not None else None,
            is_current_line=is_current_line,
        )

    # ============ 块级公式 ============
    if line.block_type == BlockType.MATH:
        if active_seg == 0:
            # 多行编辑（同代码块）：Shift+Enter 换行，Enter 触发 on_submit 仅更新 draft
            field = _active_field(
                line, draft, on_change_draft, on_submit, on_blur,
                on_selection_change, initial_cursor, nav_seq, field_ref=field_ref,
                base_size=16, multiline=True, max_width=avail_width, line_height=line_height,
                on_cursor_sync=on_cursor_sync,
            )
            content = ft.Container(
                content=field, bgcolor=c.math_bg, border_radius=6, width=float("inf"),
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            )
        else:
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
                on_click=lambda e: on_activate(line_idx, 0),
                ink=True,
            )
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate, is_current_line=is_current_line)

    # ============ 目录 [toc] ============
    if line.block_type == BlockType.TOC:
        if active_seg is not None:
            field = _active_field(
                line, draft, on_change_draft, on_submit, on_blur,
                on_selection_change, initial_cursor, nav_seq,
                field_ref=field_ref, max_width=avail_width,
                line_height=line_height, on_cursor_sync=on_cursor_sync,
            )
            content = ft.Container(
                content=field, width=float("inf"),
                padding=ft.Padding.symmetric(horizontal=2),
                on_click=_edit_on_click_factory(0),
                ink=True,
            )
        else:
            toc_items: list[ft.Control] = [
                ft.Container(
                    content=ft.Text(value=text, size=base - 1, color=c.text, font_family=FONT_MAIN),
                    padding=ft.Padding.only(left=(lvl - 1) * 16),
                    on_click=lambda e, t=li: on_jump_to(t) if on_jump_to else None,
                    ink=True,
                )
                for li, lvl, text in (toc_entries or [])
            ]
            # 目录块：左右撑满整行，灰色背景，便于与正文区分
            content = ft.Container(
                content=ft.Column(controls=toc_items, spacing=2),
                width=float("inf"),
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                bgcolor=c.code_bg, border_radius=6,
            )
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate, is_current_line=is_current_line)

    # ============ 任务列表项（非编辑态）============
    if line.task and active_seg is None:
        content_target_si = 1 if len(line.segments) > 1 else 0
        content_spans = _spans_for(line, 1, len(line.segments), activate, base, line_highlight_range=outward_range) or [
            ft.TextSpan(" ", style=line_style, on_click=lambda e: activate(content_target_si))
        ]

        content = ft.Row(
            controls=[
                ft.Checkbox(
                    value=line.checked,
                    on_change=lambda e: on_toggle_task(line_idx) if on_toggle_task else None,
                ),
                ft.Text(spans=content_spans, style=line_style),
            ],
            wrap=True, spacing=4, run_spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate)

    # ============ 图片行（非编辑态）============
    if (img_idxs := _image_seg_indices(line)) and active_seg is None:
        img_controls: list[ft.Control] = []
        for seg_idx in img_idxs:
            seg = line.segments[seg_idx]
            w, h = image_fit_size(seg.url)
            kw: dict = {
                "src": seg.url,
                "fit": ft.BoxFit.CONTAIN,
                "tooltip": seg.text,
                "error_content": ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.IMAGE_NOT_SUPPORTED_OUTLINED, color=c.muted, size=20),
                            ft.Text(value=seg.text or seg.url or "图片", color=c.muted, size=base - 1, font_family=FONT_MAIN),
                        ],
                        spacing=8, alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                    bgcolor=c.code_block_bg, border_radius=6,
                    alignment=ft.Alignment.CENTER,
                ),
            }
            if w is not None:
                kw["width"] = w
            if h is not None:
                kw["height"] = h
            img_controls.append(
                ft.Container(
                    content=ft.Image(**kw),
                    on_click=lambda e, si=seg_idx: on_activate(line_idx, si),
                    ink=True,
                )
            )
        content = ft.Column(
            controls=img_controls, spacing=4,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate)

    # ============ 普通块（段落 / 标题 / 列表 / 引用）============
    if active_seg is None:
        # spans 不绑定 on_click：GestureDetector 统一处理点击，获取精确坐标
        # 做命中测试，把光标定位到点击的字符位置（而非段尾）。
        spans = _spans_for(line, 0, len(line.segments), None, base, line_highlight_range=outward_range)

        def _on_tap(e: ft.TapEvent):
            pos = e.local_position
            if pos is not None:
                si, offset = _hit_test_tap(line, pos.x, pos.y, base, line_height)
                if si >= 0:
                    seg = line.segments[si]
                    if seg.seg_type == SegType.LINK and seg.url:
                        _open_link_url(seg.url)
                        return
                    shift_held = (
                        shift_pressed_ref is not None
                        and bool(shift_pressed_ref.current)
                    )
                    if shift_held and on_extend_outward is not None:
                        # 立即抑制 blur：TextField 失去焦点会在 on_extend_outward 之前触发 on_blur，
                        # 导致 _start_outward 检查 active 时已为 None，提前返回（选区无法起始）。
                        # 必须在 on_extend_outward 调用前设置 suppress_blur。
                        if on_suppress_blur:
                            on_suppress_blur()
                        # Shift+Click：起始/扩展向外选区到点击位置
                        active_off = _logical_raw_offset(line, si, offset)
                        on_extend_outward(line_idx, active_off)
                        # 标记已处理：防止后续 _fallback_activate 再次调用 on_extend_outward
                        # 覆盖精确 offset（外层 Container.on_click 可能在手势竞技场后触发）
                        _shift_tap_handled[0] = True
                        return
                    # 既有向外选区 + 非 Shift 点击：先清除选区再激活
                    if outward_range is not None and on_clear_outward is not None:
                        on_clear_outward()
                    # 段级激活：统一调用 activate(si, offset)（含 heading）
                    activate(si, offset)
                    return
            if outward_range is not None and on_clear_outward is not None:
                on_clear_outward()
            # 回退：点击多行区域或无法定位时，激活最后一个段
            activate(max(0, len(line.segments) - 1))

        # 拖动选区：pan_start 起始选区，pan_update 实时扩展（跨行用 y 估算行号）
        _line_h = base * line_height

        def _pan_target_off(pos) -> tuple[int, int]:
            """根据 pan 坐标估算 (target_li, target_off)。跨行用 y 估算。"""
            if pos is None:
                return (line_idx, 0)
            line_dy = round(pos.y / _line_h) if _line_h > 0 else 0
            target_li = line_idx + line_dy
            if target_li == line_idx:
                si, offset = _hit_test_tap(line, pos.x, pos.y, base, line_height)
                if si >= 0:
                    return (line_idx, _logical_raw_offset(line, si, offset))
                return (line_idx, 0)
            # 跨行：用同一 x 列命中目标行偏移（on_hit_test_x 可用时），
            # 否则回退向上用大偏移（钳制到行尾）、向下用 0（行首）。
            # 坐标一致性：各行 Container 横向 padding 均为 8，GestureDetector
            # 包裹 Text，pos.x 相对 Text 起点，跨行可直接复用。
            if on_hit_test_x is not None:
                return (target_li, on_hit_test_x(target_li, pos.x))
            if line_dy < 0:
                return (target_li, 999999)
            return (target_li, 0)

        def _on_pan_start(e: ft.DragStartEvent):
            if on_extend_outward is None:
                return
            # 拖拽起始：先清除已有选区，再以当前点为新起点起始选区
            # （修复"再次拖拽仍以上次起点为起点"的 BUG：不清除则 on_extend_outward
            # 走 _extend_outward 分支，保留旧 anchor）
            if on_clear_outward is not None:
                on_clear_outward()
            t_li, t_off = _pan_target_off(e.local_position)
            on_extend_outward(t_li, t_off)

        def _on_pan_update(e: ft.DragUpdateEvent):
            if on_extend_outward is None:
                return
            t_li, t_off = _pan_target_off(e.local_position)
            on_extend_outward(t_li, t_off)

        content = ft.Container(
            content=ft.GestureDetector(
                content=ft.Text(spans=spans, style=line_style, width=float("inf")),
                on_tap=_on_tap,
                on_pan_start=_on_pan_start,
                on_pan_update=_on_pan_update,
            ),
            ink=True,
            border_radius=8,
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        )
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate)

    # ============ 编辑态：段级 before + active + after（Typora 式 WYSIWYG）============
    # active_seg 越界兜底：退化为整行渲染
    if active_seg >= len(line.segments):
        spans = _spans_for(line, 0, len(line.segments), activate, base, line_highlight_range=outward_range)
        content = ft.Container(
            content=ft.Text(spans=spans, style=line_style, width=float("inf")),
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        )
        return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate, is_current_line=is_current_line)

    # 段级布局：前段 Text(spans) + 激活段 TextField + 后段 Text(spans)
    # 仅激活段显示原生 Markdown，前后段保持渲染态——Typora 式最小语法编辑
    # before/after spans 不绑定 on_click：GestureDetector 统一处理点击 + Shift+Click
    active_seg_obj = line.segments[active_seg]
    before_spans = _spans_for(line, 0, active_seg, None, base, line_highlight_range=outward_range)
    after_spans = _spans_for(line, active_seg + 1, len(line.segments), None, base, line_highlight_range=outward_range)

    def _shift_held() -> bool:
        return shift_pressed_ref is not None and bool(shift_pressed_ref.current)

    def _handle_seg_tap(si: int, offset: int) -> bool:
        """处理 before/after 段点击。返回 True 表示已处理（含链接打开）。"""
        if si < 0:
            return False
        seg = line.segments[si]
        if seg.seg_type == SegType.LINK and seg.url:
            _open_link_url(seg.url)
            return True
        if _shift_held() and on_extend_outward is not None:
            # 立即抑制 blur：TextField 失去焦点会在 on_extend_outward 之前触发 on_blur，
            # 导致 _start_outward 检查 active 时已为 None，提前返回（选区无法起始）。
            # 必须在 on_extend_outward 调用前设置 suppress_blur。
            if on_suppress_blur:
                on_suppress_blur()
            # Shift+Click：从编辑光标起始/扩展向外选区到点击位置
            active_off = _logical_raw_offset(line, si, offset)
            on_extend_outward(line_idx, active_off)
            # 标记已处理：防止后续 Container.on_click 触发 activate() 覆盖
            # _start_outward 的 set_active(None)（否则选区不可见）
            _shift_tap_handled[0] = True
            return True
        # 既有向外选区 + 非 Shift 点击：先清除选区再激活
        if outward_range is not None and on_clear_outward is not None:
            on_clear_outward()
        activate(si, offset)
        return True

    controls: list[ft.Control] = []
    _line_h = base * line_height
    if before_spans:
        def _on_before_tap(e: ft.TapEvent):
            pos = e.local_position
            if pos is not None:
                si, offset = _hit_test_segs(line, 0, active_seg, pos.x, pos.y, base, line_height)
                if _handle_seg_tap(si, offset):
                    return
            # 回退：激活前段最后一段
            activate(max(0, active_seg - 1))

        def _on_before_pan_start(e: ft.DragStartEvent):
            if on_extend_outward is None:
                return
            # 拖拽起始：先清除已有选区，再以当前点为新起点起始选区（同 _on_pan_start）
            if on_clear_outward is not None:
                on_clear_outward()
            pos = e.local_position
            if pos is not None:
                si, offset = _hit_test_segs(line, 0, active_seg, pos.x, pos.y, base, line_height)
                if si >= 0:
                    on_extend_outward(line_idx, _logical_raw_offset(line, si, offset))

        def _on_before_pan_update(e: ft.DragUpdateEvent):
            if on_extend_outward is None:
                return
            pos = e.local_position
            if pos is None:
                return
            line_dy = round(pos.y / _line_h) if _line_h > 0 else 0
            target_li = line_idx + line_dy
            if target_li == line_idx:
                si, offset = _hit_test_segs(line, 0, active_seg, pos.x, pos.y, base, line_height)
                if si >= 0:
                    on_extend_outward(target_li, _logical_raw_offset(line, si, offset))
            elif line_dy < 0:
                on_extend_outward(target_li, 999999)
            else:
                on_extend_outward(target_li, 0)

        controls.append(ft.GestureDetector(
            content=ft.Text(spans=before_spans, style=line_style),
            on_tap=_on_before_tap,
            on_pan_start=_on_before_pan_start,
            on_pan_update=_on_before_pan_update,
        ))
    controls.append(
        _active_field(
            line, draft, on_change_draft, on_submit, on_blur,
            on_selection_change, initial_cursor, nav_seq,
            base_size=base, field_ref=field_ref, max_width=avail_width,
            line_height=line_height, on_cursor_sync=on_cursor_sync,
            seg=active_seg_obj,
        )
    )
    if after_spans:
        def _on_after_tap(e: ft.TapEvent):
            pos = e.local_position
            if pos is not None:
                si, offset = _hit_test_segs(
                    line, active_seg + 1, len(line.segments), pos.x, pos.y, base, line_height,
                )
                if _handle_seg_tap(si, offset):
                    return
            # 回退：激活后段第一段
            nxt = active_seg + 1
            activate(nxt if nxt < len(line.segments) else active_seg)

        def _on_after_pan_start(e: ft.DragStartEvent):
            if on_extend_outward is None:
                return
            # 拖拽起始：先清除已有选区，再以当前点为新起点起始选区（同 _on_pan_start）
            if on_clear_outward is not None:
                on_clear_outward()
            pos = e.local_position
            if pos is not None:
                si, offset = _hit_test_segs(
                    line, active_seg + 1, len(line.segments), pos.x, pos.y, base, line_height,
                )
                if si >= 0:
                    on_extend_outward(line_idx, _logical_raw_offset(line, si, offset))

        def _on_after_pan_update(e: ft.DragUpdateEvent):
            if on_extend_outward is None:
                return
            pos = e.local_position
            if pos is None:
                return
            line_dy = round(pos.y / _line_h) if _line_h > 0 else 0
            target_li = line_idx + line_dy
            if target_li == line_idx:
                si, offset = _hit_test_segs(
                    line, active_seg + 1, len(line.segments), pos.x, pos.y, base, line_height,
                )
                if si >= 0:
                    on_extend_outward(target_li, _logical_raw_offset(line, si, offset))
            elif line_dy < 0:
                on_extend_outward(target_li, 999999)
            else:
                on_extend_outward(target_li, 0)

        controls.append(ft.GestureDetector(
            content=ft.Text(spans=after_spans, style=line_style),
            on_tap=_on_after_tap,
            on_pan_start=_on_after_pan_start,
            on_pan_update=_on_after_pan_update,
        ))

    row = ft.Row(
        controls=controls, wrap=True, spacing=0, run_spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    # Container 占满整行，点击右侧空白时抑制 blur 并激活行尾，保持编辑状态。
    content = ft.Container(
        content=row,
        width=float("inf"),
        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        on_click=_edit_on_click_factory(max(0, len(line.segments) - 1)),
        ink=True,
        border_radius=8,
    )
    return _wrap_block(content, line, base, line_idx, on_click=_fallback_activate, is_current_line=is_current_line)


def _wrap_block(
    content: ft.Control, line: Line, base: int, line_idx: int | None = None,
    on_click: Callable | None = None,
    is_current_line: bool = False,
) -> ft.Control:
    """包一层块级容器：缩进、引用边框。

    嵌套引用：根据 line.level 包多层带左边框的 Container，每多一层嵌套
    行首多一个灰色竖线占位（Typora 式嵌套引用视觉）。

    on_click：挂到最外层 Container 的点击回调。内层 GestureDetector 会消费
    其覆盖区域的 tap 事件，因此 on_click 仅在内层未覆盖的区域（如 top/bottom
    padding 死区）触发，作为"点击行间空白也能进入编辑"的兜底。
    """
    c = _current_colors()  # 当前主题颜色（亮/暗）
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
        # 嵌套引用：每层一个带左边框的 Container，层级由 line.level 决定
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
