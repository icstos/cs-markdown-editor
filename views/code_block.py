"""原生代码块（Typora 式双态）：高亮浏览态 + 原生编辑态。

背景：原实现直接内嵌 flet-code-editor 的 CodeEditor，但上游 `flutter_code_editor`
的 `CodeField.wrap` 至今是"已声明未实现"的空属性（对应 PR 仍是 Draft），Flet 侧
也没有暴露任何换行开关——"开启换行时代码块内也软换行"在产品上无法达成。故整体
改用 Flet 原生控件重建代码块。

两态：
- 浏览态：逐逻辑行 `ft.Text(spans=...)` + Pygments 语义色
  （services.code_highlight 分词 → styles.Colors.code_syntax 取色）。
  word_wrap=True 时文本按容器宽度自然折行，续行与首行同列；False 时单行不折、
  整块横向滚动。
- 编辑态：`ft.TextField(multiline=True)` + 等宽字体，选区 / IME / 撤销 / 软换行
  全部交给框架原生实现。

契约不变（换实现不换接口）：`on_change / on_focus / on_blur / on_selection_change`
四件套的语义与旧 CodeEditor 完全一致，因此 `views/editor/_fence.py` 的围栏闭包组
与 `views/key_bindings.py` 的"边界方向键跳出 / 空块 Backspace 删除 / Tab 放行"
无需任何改动。

Tab 缩进由本组件自行处理（组件内嵌 `ft.KeyboardListener`，焦点在编辑框内才触发）：
原生多行 TextField 不会插入制表符——Flutter 把 Tab 当焦点遍历键，Flet 1.0 没有
Focus / Shortcuts 控件、TextField 也没有 on_key_down，控件层无从吞掉该事件
（真机探针实测：回调返回 True 也拦不住遍历，`KEYDOWN Tab` 与 `BLUR` 同帧发生）。
故按下 Tab 时自行插入缩进，并把随后的失焦识别为遍历副作用、立即收回焦点。

编辑态由本组件内部的 `ft.use_state` 驱动（点击进入、失焦退出），不引入编辑器级
state，从而不扰动既有的 `code_focus_ref` 路由与 `ft.memo` 依赖。

依赖项：
- models（Line）
- services.code_highlight（分词，惰性 Pygments，未知语言回退纯文本）
- services.clipboard（复制代码到系统剪贴板）
- styles（FONT_MONO / Radius / Spacing / Elevation / card_shadow / code_syntax）
- utils.code_indent（Tab / Shift+Tab 的缩进变换，纯函数）
- views._block_frame（块级包裹：缩进 / diff / 当前行高亮 / 高度上报）
"""

import contextlib
from collections.abc import Callable

import flet as ft

from models.document import Line
from services.clipboard import copy_code_to_clipboard
from services.code_highlight import highlight_lines
from styles import (
    FONT_MONO,
    Elevation,
    Radius,
    Spacing,
    _current_colors,
    card_shadow,
    only_border,
)
from utils.code_indent import apply_indent
from views import _block_frame

# 代码块行高倍数：等宽字体下 1.5 倍行距，紧凑与可读的平衡点。
# 浏览态（TextStyle.height）与编辑态（TextField.text_style.height）共用同一常量，
# 保证"点击进入编辑"时文字基线不跳动。
_CODE_LINE_HEIGHT = 1.5

# 不换行（word_wrap=False）模式下编辑框的最小宽度：短代码也要有可用的输入区。
_EDIT_MIN_WIDTH = 420.0

# 代码块语言选择下拉框的常用语言清单
# （key = Markdown 围栏语言标识，需与 Pygments get_lexer_by_name 的名称对齐）
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


def _lang_options(current_lang: str) -> list[ft.DropdownOption]:
    """构造语言下拉框选项；若当前语言不在常用清单内，追加为额外选项。"""
    options = [ft.DropdownOption(key=k, text=t) for k, t in _COMMON_LANGS]
    known = {k for k, _ in _COMMON_LANGS}
    if current_lang and current_lang not in known:
        options.append(ft.DropdownOption(key=current_lang, text=current_lang))
    return options


def _span_style(color: str, size: int) -> ft.TextStyle:
    """代码片段文字样式：等宽 + 固定行高倍数 + 语义色。

    字体族 / 字号 / 行高在 span 上重复声明（而非仅依赖 Text 的 style 继承）：
    Flet 序列化只发送非空字段，继承链一旦在某个版本上表现不一致，就会退化成
    默认字体并让行高与行号错位，显式声明可完全规避该类风险。
    """
    return ft.TextStyle(
        color=color,
        font_family=FONT_MONO,
        size=size,
        height=_CODE_LINE_HEIGHT,
    )


def _syn_color(c, kind: str) -> str:
    """语义类别 → 主题色（未收录类别回退代码块正文色）。"""
    return c.code_syntax.get(kind, c.code_block_fg)


def _measure_mono_width(text: str, size: int) -> float:
    """等宽字体下单行文本的像素宽度（不换行模式用于撑开编辑框）。

    优先用项目自带的 HarfBuzz 精确测量；测量不可用时退化为"字符数 × 0.6 字号"
    的等宽估算（Consolas 实测比例约 0.55，留余量避免低估导致折行）。
    """
    if not text:
        return 0.0
    try:
        from utils.text_layout import measure_text_width

        return float(measure_text_width(text, FONT_MONO, size))
    except Exception:
        return len(text) * size * 0.6


def render_code_block(
    line: Line,
    line_idx: int,
    base: int,
    content_width: float | None,
    clipboard_ref: ft.Ref | None,
    on_change_code: Callable[[int, str], None] | None,
    on_code_focus: Callable[[int], None] | None,
    on_code_blur: Callable[[int], None] | None,
    on_change_lang: Callable[[int, str], None] | None,
    on_code_selection: Callable[..., None] | None,
    code_field_ref: ft.Ref | None,
    is_current_line: bool,
    is_flash: bool = False,
    on_line_size_change: Callable[[int, float], None] | None = None,
    diff_mark: str | None = None,
    word_wrap: bool = True,
) -> ft.Control:
    """渲染代码块（CODE 围栏岛屿）：浏览态语法高亮 + 点击进入原生编辑。

    软换行：`word_wrap=True` 时浏览态按容器宽度折行（续行与首行同列），
    `False` 时单行不折、整块横向滚动。编辑器把 word_wrap=False 映射为
    `content_width = inf`（见 views/editor/__init__.py），此处以该不变量判定，
    避免再多传一个 prop。

    高度自适应：内容变化 → 行数变化 → 行高变化，`wrap_block` 的 on_size_change
    上报纸质高度供滚动定位（与旧实现一致）。

    Args:
        base: 段落左侧基准内边距（块级容器的必填位置参数，勿漏传）。
        code_field_ref: 编辑器共享的编辑框 ref（保留契约；聚焦改用组件内 ref，
            因为该 ref 被文档内每个代码块依次赋值，多块共存时指向最后渲染者）。
    """
    c = _current_colors()
    code = line.segments[0].text if line.segments else ""
    lang = line.lang or ""
    page = ft.context.page
    is_dark = page is not None and page.theme_mode == ft.ThemeMode.DARK

    # ---- 组件内状态：浏览 ⇄ 编辑 / 复制反馈 / 折叠 ----
    is_editing, set_editing = ft.use_state(False)
    copied, set_copied = ft.use_state(False)
    is_collapsed, set_collapsed = ft.use_state(False)
    edit_field_ref = ft.use_ref(None)

    # ---- Tab 缩进所需的本地状态 ----
    # caret_ref：(文本, 选区起点, 选区终点)——组件内留一份最近的光标位，Tab 缩进据此
    #   定位；不读 ctx.code_caret_ref，以免把围栏层的状态机卷进组件。
    # shift_ref / ctrl_ref：Shift、Ctrl 是否按下。KeyboardListener 的 KeyDownEvent
    #   只带 key、不带修饰键状态（与页面级 KeyboardEvent 不同），Shift+Tab 反缩进与
    #   Ctrl+Tab（全局标签切换，不能当缩进）都只能靠跟踪修饰键自身按键判定。
    # tab_pending_ref：本次失焦是否由 Tab 的焦点遍历副作用引起（见 _exit_edit）。
    # pending_caret_ref：渲染时待写入编辑框的光标 (起点, 终点)。
    caret_ref = ft.use_ref(None)
    shift_ref = ft.use_ref(False)
    ctrl_ref = ft.use_ref(False)
    tab_pending_ref = ft.use_ref(False)
    pending_caret_ref = ft.use_ref(None)
    tab_epoch, set_tab_epoch = ft.use_state(0)

    # 软换行开关：word_wrap=False 时 content_width 为 inf（编辑器侧不变量）
    wrap = bool(word_wrap) and content_width != float("inf")

    code_size = base
    text_h = round(code_size * _CODE_LINE_HEIGHT)
    line_count = max(1, code.count("\n") + 1)
    digits = len(str(line_count))
    # 行号列宽 = 位数 × 单字宽 + 与代码的间距（等宽字体单字宽 ≈ 0.62 字号）
    gutter_w = max(26, round(digits * code_size * 0.62) + Spacing.LG)
    gutter_bg = ft.Colors.with_opacity(0.18 if is_dark else 0.04, c.text)
    border_color = ft.Colors.with_opacity(0.08 if is_dark else 0.06, c.text)

    # ---- 语言选择器 ----
    lang_dropdown = ft.Dropdown(
        value=lang,
        options=_lang_options(lang),
        width=150,
        text_size=12,
        dense=True,
        content_padding=ft.Padding.symmetric(horizontal=6, vertical=0),
        border=ft.NoInputBorder(),
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
            page.run_task(copy_code_to_clipboard, clipboard_ref, txt, set_copied)
            if page is not None and not copied
            else None
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
        on_click=lambda e: _toggle_collapse(),
    )

    # ---- 头部工具栏 ----
    header = ft.Row(
        controls=[
            collapse_btn,
            ft.Icon(
                ft.Icons.DATA_OBJECT,
                size=13,
                color=ft.Colors.with_opacity(0.5, c.muted),
            ),
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

    # ============================ 浏览态 ============================

    def _gutter_cell(n: int) -> ft.Container:
        """行号单元格：固定列宽 + 顶右对齐 + 行号底色（同列拼接成"装订线"色带）。

        height=text_h 固定为**一个视觉行**的高度，不由交叉轴拉伸决定：
        行容器在滚动 Column 内高度约束无界，若用 `CrossAxisAlignment.STRETCH`
        子控件会被赋 `tightFor(height: infinity)`，整行高度直接变成 inf
        （真机探针实测：`on_size_change` 上报 height=inf，块体不渲染、后续行被挤走）。
        折行行的行号只落在首视觉行，色带在续行处自然留白——视觉上仍是行号，
        但绝不牺牲布局正确性。
        """
        return ft.Container(
            content=ft.Text(
                value=str(n),
                size=max(9, round(code_size * 0.78)),
                color=ft.Colors.with_opacity(0.55, c.muted),
                font_family=FONT_MONO,
                text_align=ft.TextAlign.RIGHT,
                height=text_h,
            ),
            width=gutter_w,
            height=text_h,
            bgcolor=gutter_bg,
            alignment=ft.Alignment.TOP_RIGHT,
            padding=ft.Padding.only(top=Spacing.XS, right=Spacing.MD),
        )

    def _build_read_body() -> ft.Control:
        """浏览态：逐逻辑行高亮渲染，折行由 Flutter 按容器宽度原生完成。"""
        rows: list[ft.Control] = []
        for i, spec in enumerate(highlight_lines(code, lang)):
            spans = [
                ft.TextSpan(text=t, style=_span_style(_syn_color(c, k), code_size))
                for t, k in spec
                if t
            ]
            if not spans:
                # 空行：给一个空格 span 撑出与普通行等高的行盒，否则行高塌陷会让
                # 行号与代码逐行错位（行号单元格高度恒为 text_h，行高由代码行决定）
                spans = [
                    ft.TextSpan(
                        text=" ", style=_span_style(c.code_block_fg, code_size)
                    )
                ]
            rows.append(
                ft.Row(
                    controls=[
                        _gutter_cell(i + 1),
                        ft.Text(
                            spans=spans,
                            style=_span_style(c.code_block_fg, code_size),
                            text_align=ft.TextAlign.LEFT,
                            # 折行模式用 expand 占满行号列右侧、由框架按宽度折行；
                            # 不折行模式必须关掉 expand（父 Row 在横向滚动容器内宽度
                            # 无界，Expanded 在无界约束下会报错），并显式 no_wrap。
                            expand=wrap,
                            no_wrap=not wrap,
                        ),
                    ],
                    spacing=Spacing.MD,
                    # 必须 START 而非 STRETCH：本行位于滚动 Column 内、交叉轴约束无界，
                    # STRETCH 会把子控件高度约束成 infinity（整行高度 inf）。
                    vertical_alignment=ft.CrossAxisAlignment.START,
                )
            )
        body = ft.Column(controls=rows, spacing=0, tight=True)
        if wrap:
            return body
        # 不换行：整块横向滚动（行号随内容滚动，行为与"关闭换行"的段落一致）
        return ft.Row(
            controls=[body],
            scroll=ft.ScrollMode.AUTO,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    # ============================ 编辑态 ============================

    def _build_edit_body() -> ft.Control:
        """编辑态：原生多行 TextField（软换行 / 选区 / IME / 撤销全由框架负责）。"""
        # Tab 缩进后的光标位置：只能经渲染参数给客户端——渲染后的控件是冻结的，
        # 在 effect 里改 `field.selection` 会抛 "Frozen controls cannot be updated."
        # （flet 1.0 用冻结标记保证"控件状态只从渲染流入"，事后改属性不会被 diff 采纳）。
        # 取用即消费：下一次渲染不再显式指定 selection（`selection=None` 在客户端是
        # 空操作，不会把光标甩回文末），此后光标由客户端自身维护。
        pending_caret = pending_caret_ref.current
        pending_caret_ref.current = None
        field = ft.TextField(
            # key 稳定（不含 is_editing）：on_change 触发的全量重渲染按 key 复用控件，
            # 不重挂载，从而不打断 IME 组合态与原生撤销栈。
            key=f"code-edit-{line_idx}",
            # ref 必须在构造时传入：flet 的 ref 是 InitVar，只在 __init__ 里绑定
            # （BaseControl.__post_init__ 的 `ref.current = self`），事后 `field.ref = x`
            # 不会绑定，ref 会一直是 None（聚焦 / 回写光标全部失效）。
            ref=edit_field_ref,
            value=code,
            selection=(
                ft.TextSelection(base_offset=pending_caret[0], extent_offset=pending_caret[1])
                if pending_caret is not None
                else None
            ),
            multiline=True,
            min_lines=line_count,
            max_lines=None,
            border=ft.NoInputBorder(),
            filled=False,
            bgcolor=ft.Colors.TRANSPARENT,
            dense=True,
            text_size=code_size,
            text_style=ft.TextStyle(
                font_family=FONT_MONO,
                size=code_size,
                height=_CODE_LINE_HEIGHT,
                color=c.code_block_fg,
            ),
            cursor_color=c.link,
            cursor_width=2,
            selection_color=ft.Colors.with_opacity(0.25, c.link),
            # 左内边距 = 行号列宽 + 间距：让代码正文与浏览态的代码列严格同 x，
            # 进出编辑态时代码水平方向零位移（真机探针实测过无内边距时的横向跳动）。
            content_padding=ft.Padding.only(
                left=gutter_w + Spacing.MD, right=Spacing.MD, top=0, bottom=0
            ),
            on_change=lambda e: (
                on_change_code(line_idx, e.control.value)
                if on_change_code is not None
                else None
            ),
            # 取得焦点：既转发给编辑器（撤销会话分组），也是"Tab 焦点往返已结束"的信号。
            on_focus=_on_edit_focus,
            on_blur=lambda e: _exit_edit(),
            # 光标/选区跟踪：组件内留一份供 Tab 缩进定位，再写入 (value, base, extent)
            # 供代码块边界方向键跳出判定
            on_selection_change=_remember_caret,
        )
        if code_field_ref is not None:
            # 保留对外契约（历史上由编辑器持有该 ref）；聚焦不依赖它——它被文档内
            # 每个代码块依次赋值，多块共存时指向最后渲染的那个。
            code_field_ref.current = field
        # 统一放进 Row 承载宽度：
        # - 折行模式：TextField 默认按内容收缩（Column 交叉轴是松约束、它又不是
        #   Flex 直接子项，expand 不生效），把 expand 交给外层编辑监听器、由 Row
        #   承载才能占满正文宽度，换行点与浏览态一致（真机探针实测过收缩导致的折行变窄）。
        # - 不换行模式：按最长行撑开宽度 + 横向滚动，与浏览态行为一致。
        listener_kwargs: dict = {}
        row_kwargs: dict = {"vertical_alignment": ft.CrossAxisAlignment.START}
        if wrap:
            listener_kwargs["expand"] = True
        else:
            longest = max(code.split("\n"), key=len, default="")
            field.width = max(
                _EDIT_MIN_WIDTH, _measure_mono_width(longest, code_size) + Spacing.XXL
            )
            row_kwargs["scroll"] = ft.ScrollMode.AUTO
        return ft.Row(
            controls=[
                # 编辑监听器只包住编辑框：焦点在代码块内才触发，因此 Tab 缩进既不会
                # 干扰正文光标的 Tab（段首缩进/表格跳格），也不需要把事件通道层层上抛。
                ft.KeyboardListener(
                    content=field,
                    on_key_down=_on_edit_key_down,
                    on_key_up=_on_edit_key_up,
                    **listener_kwargs,
                )
            ],
            **row_kwargs,
        )

    # ============================ 交互 ============================

    def _remember_caret(e) -> None:
        """编辑框光标/选区变化：组件内留一份选区快照（Tab 缩进定位用），再转发给编辑器。

        只记偏移、不记文本：`e.control.value` 是服务端控件的**渲染时**取值——flet 不会
        把客户端输入回写到控件属性上（文本以文档为唯一真相）。混用"旧文本 + 新偏移"
        会让缩进落错位置，真机探针实测甚至会吃掉刚输入的字符（旧文本 105 字 + 新偏移
        106 被裁剪到 105，末尾字符连同缩进一起写回文档）。
        """
        sel = getattr(e, "selection", None)
        caret_ref.current = (
            int(getattr(sel, "base_offset", 0) or 0),
            int(getattr(sel, "extent_offset", 0) or 0),
        )
        if on_code_selection is not None:
            on_code_selection(line_idx, e)

    def _current_caret() -> tuple[str, int, int]:
        """当前 (文本, 选区起点, 选区终点)。

        文本取文档（`on_change_code` 每次输入都写回，是唯一真相）；偏移取组件内快照，
        缺失时（进入编辑态后未收到选区事件就按 Tab）回退到编辑框控件自身的 selection。
        """
        text = line.segments[0].text if line.segments else ""
        caret = caret_ref.current
        if caret is None:
            sel = getattr(edit_field_ref.current, "selection", None)
            caret = (
                int(getattr(sel, "base_offset", 0) or 0),
                int(getattr(sel, "extent_offset", 0) or 0),
            )
        return (text, caret[0], caret[1])

    def _on_edit_key_down(e) -> None:
        """编辑态按键：跟踪修饰键，并把 Tab / Shift+Tab 变成缩进。

        Tab 必须自己处理（原因见模块 docstring）：插入缩进后标记 tab_pending，
        把随之而来的焦点遍历失焦当作副作用处理，而不是"用户离开了代码块"。
        Ctrl+Tab 是全局标签切换快捷键，不插入缩进（与表格缩进/标签切换同一取舍）。
        """
        key = (getattr(e, "key", "") or "").lower()
        if key.startswith("shift"):
            shift_ref.current = True
            return
        if key.startswith("control"):
            ctrl_ref.current = True
            return
        if key != "tab":
            return
        if ctrl_ref.current:
            return
        if not (is_editing and not is_collapsed):
            return
        _apply_indent_edit(-1 if shift_ref.current else 1)

    def _on_edit_key_up(e) -> None:
        key = (getattr(e, "key", "") or "").lower()
        if key.startswith("shift"):
            shift_ref.current = False
        elif key.startswith("control"):
            ctrl_ref.current = False

    def _apply_indent_edit(direction: int) -> None:
        """Tab / Shift+Tab：改写代码文本并保持光标列位。

        文本改写走 `on_change_code`（入撤销历史、标脏、重渲染等与手工输入完全一致），
        缩进后的光标位置记进 pending_caret_ref，随这次重渲染下发给客户端。

        Tab 一定会让 Flutter 把焦点遍历走（原生编辑框不消费 Tab），所以无论文本是否
        变化都要走一遍"重渲染 → 收回焦点"；文本无变化时不写文档，避免产生空撤销条目。
        """
        value, base, extent = _current_caret()
        new_value, new_base, new_extent = apply_indent(value, base, extent, direction)
        tab_pending_ref.current = True
        if new_value != value:
            pending_caret_ref.current = (new_base, new_extent)
            # 乐观更新组件内光标快照：客户端的光标事件回来之前再按 Tab 也落在正确位置
            caret_ref.current = (new_base, new_extent)
            if on_change_code is not None:
                on_change_code(line_idx, new_value)
        set_tab_epoch(tab_epoch + 1)

    async def _focus_edit_field() -> None:
        """把焦点交给原生编辑框。

        进入编辑态（is_editing 变化）与 Tab 缩进后（tab_epoch 变化）都要走一次：
        - 不用 `autofocus=True`：它只在控件首次挂载时生效，重渲染路径不可靠；
        - 用 `use_effect` 在提交渲染后执行，此时 ref 已指向新建控件，聚焦稳定；
        - Tab 后必须重新聚焦：焦点遍历由客户端在按键处理时同步执行完毕，等这次重渲染
          提交后再请求聚焦，才确定"收回"发生在"被带走"之后。

        这里只调方法（`focus()`），不改控件属性——渲染后的控件是冻结的，
        赋值会抛 "Frozen controls cannot be updated."。
        """
        field = edit_field_ref.current
        if is_editing and field is not None:
            with contextlib.suppress(Exception):
                await field.focus()

    ft.use_effect(_focus_edit_field, [is_editing, tab_epoch])

    def _on_edit_focus(e) -> None:
        """编辑框取得焦点：Tab 的焦点往返结束，之后的失焦都按真实失焦处理。"""
        tab_pending_ref.current = False
        if on_code_focus is not None:
            on_code_focus(line_idx)

    def _enter_edit() -> None:
        """点击代码块 → 进入编辑态（折叠态先展开；焦点由 use_effect 交给编辑框）。"""
        if is_collapsed:
            set_collapsed(False)
        if not is_editing:
            # 上一次会话的光标快照与修饰键状态都已失效，避免 Tab 落在过期位置
            # 或被上一次会话残留的 Shift/Ctrl 状态误判
            caret_ref.current = None
            shift_ref.current = False
            ctrl_ref.current = False
            set_editing(True)

    def _exit_edit() -> None:
        """编辑框失焦 → 回到高亮浏览态，并通知编辑器清理围栏聚焦态。

        Tab 引发的失焦是 Flutter 焦点遍历的副作用，不是用户要离开代码块：
        此时既不退出编辑态、也不清理围栏聚焦态（撤销会话因此不被打断），
        焦点由 `_focus_edit_field` 在本次重渲染提交后收回。
        """
        if tab_pending_ref.current:
            return
        if on_code_blur is not None:
            on_code_blur(line_idx)
        if is_editing:
            set_editing(False)

    def _toggle_collapse() -> None:
        """折叠/展开；折叠时退出编辑态（编辑框被卸载，避免聚焦态残留）。"""
        if not is_collapsed and is_editing:
            set_editing(False)
        set_collapsed(not is_collapsed)

    # ============================ 组装 ============================

    # 折叠态摘要：首行预览 + 行数（保持与展开态同一层级结构，避免布局跳动）
    first_line = code.split("\n")[0]
    preview_text = first_line[:60] + ("…" if len(first_line) > 60 else "")
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

    if is_collapsed:
        body: ft.Control = collapsed_preview
    else:
        body = ft.Container(
            content=_build_edit_body() if is_editing else _build_read_body(),
            padding=ft.Padding.only(top=Spacing.SM, bottom=Spacing.SM),
        )

    content = ft.Container(
        content=ft.Column(controls=[header, body], spacing=Spacing.XS),
        bgcolor=c.code_block_bg,
        border_radius=Radius.MD,
        padding=ft.Padding.only(
            left=Spacing.MD, right=Spacing.MD, top=Spacing.XS, bottom=Spacing.SM
        ),
        shadow=card_shadow(Elevation.LOW, is_dark),
        border=only_border(
            top=ft.BorderSide(1, border_color),
            bottom=ft.BorderSide(1, border_color),
            left=ft.BorderSide(1, border_color),
            right=ft.BorderSide(1, border_color),
        ),
    )

    return _block_frame.wrap_block(
        content,
        line,
        base,
        line_idx,
        on_click=lambda e: _enter_edit(),
        is_current_line=is_current_line,
        is_flash=is_flash,
        on_size_change=on_line_size_change,
        diff_mark=diff_mark,
    )
