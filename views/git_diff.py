"""Git 内嵌差异视图：统一（unified）/ 分栏（split）双模式，万行级性能保护。

对标 VS Code 的 diff 编辑器，作为覆盖编辑区的整幅面板呈现（非模态 tab）：

    ┌ 头部：[A/M/D] 文件名 · 路径 ……… +12 -3  [统一|分栏] [打开] [暂存] [丢弃] [×]
    ├ 列头：原始行号 │ 内容 │ 修改后行号 │ 内容
    └ 差异行（ListView 虚拟化滚动 + 渲染预算分页，超限可「继续渲染」）

性能设计（「万行文件 diff 渲染无卡顿」的三层保护）：
1. **解析层**：``services.git.difftext.parse_unified_diff`` 以 ``MAX_DIFF_LINES``
   为预算截断，超出部分不进入内存模型（FileDiff.truncated 置位）。
2. **渲染预算**：本模块 ``_INITIAL_BUDGET``（2000 行）只构建前 N 个行控件，
   其余折叠为一行「已渲染 N / M 行 · [继续渲染]」——点击递增预算（局部 state，
   仅本组件重渲染）。避免一次性构建上万个控件树 + 序列化把首帧拖到秒级。
3. **虚拟化滚动**：行列表用 ``ft.ListView(build_controls_on_demand=True,
   item_extent=_ROW_H)``——Flet 侧的惰性构建入口，只有即将进入视口的行才实例化
   Flutter 控件；固定行高让滚动条与命中区无需测量实际内容。

行号定位联动：每行的行号格可点击 → ``on_jump(line_no)``，由 App 打开该文件
（工作区 diff 走工作区文件、历史 diff 走当前文件）并把光标定位到该行。

颜色一律经 ``styles`` 的既有 token（``diff_add_bg`` / ``diff_del_bg`` /
``git_status_color``），不写死色值。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from services.git.difftext import MAX_DIFF_LINES, build_split_rows
from services.git.models import DiffLine, DiffLineKind, FileDiff, SplitRow
from styles import FONT_MAIN, FONT_MONO, Radius, Spacing, get_colors, git_status_color, only_border
from views.git_widgets import git_icon_button, git_text_button, letter_badge

__all__ = ["MODE_SPLIT", "MODE_UNIFIED", "GitDiffView"]

MODE_UNIFIED = "unified"
MODE_SPLIT = "split"

#: 行高（px）：12px 等宽字 + 上下 3px 呼吸。固定行高让 ListView 的行高恒定，
#: 垂直对齐（统一视图的左右列 / 分栏视图的左右半栏）一次成立。
_ROW_H = 20
#: 行号栏宽（px）：容纳 4 位行号（等宽 10px 字约 32px）+ 两侧内边距
_GUTTER_W = 46
#: 变更符号栏宽（px）：仅容纳 "+" / "-" / " "，保持正文左边缘对齐
_SIGN_W = 14
#: 首帧渲染预算（行数）。超出部分折叠为「继续渲染」入口，点击按 _BUDGET_STEP 递增。
_INITIAL_BUDGET = 2000
_BUDGET_STEP = 3000


#: 变更类型 → 单字母（头部徽章）。与 ``services.git.models`` 的映射一致，
#: 这里只做展示，避免视图层为了一个字母去 import 私有表。
_LETTER_FOR = {
    "added": "A",
    "modified": "M",
    "deleted": "D",
    "renamed": "R",
    "copied": "C",
    "type_changed": "T",
    "untracked": "U",
    "ignored": "I",
    "conflicted": "!",
}

# ---------------------------------------------------------------------------
# 单行控件
# ---------------------------------------------------------------------------


def _line_bg(kind: DiffLineKind, c) -> str | None:
    """行背景色：新增用 diff_add_bg，删除用 diff_del_bg，上下文无底色。"""
    if kind == DiffLineKind.ADDED:
        return c.diff_add_bg
    if kind == DiffLineKind.REMOVED:
        return c.diff_del_bg
    return None


def _line_fg(kind: DiffLineKind, c) -> str:
    """行文字色：新增/删除用 Git 语义色，上下文用正文色。"""
    if kind == DiffLineKind.ADDED:
        return git_status_color("added", c)
    if kind == DiffLineKind.REMOVED:
        return git_status_color("deleted", c)
    return c.text


def _no_cell(
    no: int | None, bg, c, *, on_jump: Callable[[int], None] | None
) -> ft.Control:
    """行号格（可点击定位）。

    这是「diff 行号 ↔ 编辑器行号联动定位」的入口：点第 42 行的行号即打开对应
    文件并把光标落到第 42 行。无行号（分栏视图的对侧空位）时给同宽空占位，
    保证左右列横向对齐。
    """
    if no is None:
        return ft.Container(width=_GUTTER_W, height=_ROW_H, bgcolor=bg)
    return ft.Container(
        width=_GUTTER_W,
        height=_ROW_H,
        bgcolor=bg,
        alignment=ft.Alignment.CENTER_RIGHT,
        padding=ft.Padding.only(right=Spacing.SM, left=Spacing.SM),
        tooltip=f"在编辑器中定位到第 {no} 行" if on_jump else None,
        ink=on_jump is not None,
        on_click=(lambda e, n=no: on_jump(n)) if on_jump else None,
        content=ft.Text(str(no), size=10, color=c.muted, font_family=FONT_MONO),
    )


def _sign_cell(sign: str, bg, color: str) -> ft.Control:
    """变更符号格（``+`` / ``-`` / 空），宽度固定以保持正文左边缘对齐。"""
    return ft.Container(
        width=_SIGN_W,
        height=_ROW_H,
        bgcolor=bg,
        alignment=ft.Alignment.CENTER,
        content=ft.Text(sign or " ", size=11, color=color, font_family=FONT_MONO),
    )


def _code_cell(text: str, bg, fg: str) -> ft.Control:
    """代码内容格：等宽单行 + 省略号（横向不换行，行高恒等于 _ROW_H）。"""
    return ft.Container(
        expand=True,
        height=_ROW_H,
        bgcolor=bg,
        alignment=ft.Alignment.CENTER_LEFT,
        padding=ft.Padding.only(left=Spacing.SM, right=Spacing.MD),
        content=ft.Text(
            text if text else " ",
            size=12,
            color=fg,
            font_family=FONT_MONO,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            expand=True,
        ),
    )


def _unified_line_row(
    line: DiffLine, c, *, on_jump: Callable[[int], None] | None
) -> ft.Control:
    """统一视图一行：``[旧行号][新行号][符号][内容]``。"""
    bg = _line_bg(line.kind, c)
    fg = _line_fg(line.kind, c)
    sign = {"added": "+", "removed": "-"}.get(str(line.kind), "")
    return ft.Row(
        controls=[
            _no_cell(line.old_no, bg, c, on_jump=on_jump),
            _no_cell(line.new_no, bg, c, on_jump=on_jump),
            _sign_cell(sign, bg, fg),
            _code_cell(line.text, bg, fg),
        ],
        spacing=0,
        tight=True,
    )


def _header_row(text: str, c) -> ft.Control:
    """差异块头行（统一视图的 ``@@ … @@``；分栏视图同一块头横跨左右两半）。"""
    return ft.Container(
        height=_ROW_H,
        bgcolor=ft.Colors.with_opacity(0.06, c.text),
        alignment=ft.Alignment.CENTER_LEFT,
        padding=ft.Padding.only(left=Spacing.SM, right=Spacing.MD),
        content=ft.Text(
            text,
            size=10,
            color=c.muted,
            font_family=FONT_MONO,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
    )


def _split_line_row(row: SplitRow, c, *, on_jump) -> ft.Control:
    """分栏视图一行：``[左行号][左内容] │ [右行号][右内容]``。"""
    left_bg = _line_bg(row.left_kind, c)
    right_bg = _line_bg(row.right_kind, c)
    return ft.Row(
        controls=[
            _no_cell(row.left_no, left_bg, c, on_jump=on_jump),
            _code_cell(row.left_text, left_bg, _line_fg(row.left_kind, c)),
            ft.Container(width=1, height=_ROW_H, bgcolor=c.border),
            _no_cell(row.right_no, right_bg, c, on_jump=on_jump),
            _code_cell(row.right_text, right_bg, _line_fg(row.right_kind, c)),
        ],
        spacing=0,
        tight=True,
    )


# ---------------------------------------------------------------------------
# 行集合构造（供 use_memo 缓存）
# ---------------------------------------------------------------------------


def _build_unified_rows(
    diff: FileDiff, c, *, on_jump, budget: int
) -> tuple[list[ft.Control], int]:
    """构造统一视图行控件（受 budget 限制）。返回 (行控件, 总行数)。"""
    total = sum(len(h.lines) + 1 for h in diff.hunks)
    rows: list[ft.Control] = []
    for hunk in diff.hunks:
        if len(rows) >= budget:
            break
        rows.append(
            _header_row(
                hunk.header if not hunk.section else f"{hunk.header}  {hunk.section}", c
            )
        )
        for line in hunk.lines:
            if len(rows) >= budget:
                break
            rows.append(_unified_line_row(line, c, on_jump=on_jump))
    return rows, total


def _build_split_rows_controls(
    diff: FileDiff, c, *, on_jump, budget: int
) -> tuple[list[ft.Control], int]:
    """构造分栏视图行控件（受 budget 限制）。返回 (行控件, 总行数)。"""
    split_rows = build_split_rows(diff)
    total = len(split_rows)
    rows: list[ft.Control] = []
    for row in split_rows:
        if len(rows) >= budget:
            break
        if row.is_hunk_header:
            rows.append(_header_row(row.header_text, c))
        else:
            rows.append(_split_line_row(row, c, on_jump=on_jump))
    return rows, total


def _empty_body(c, diff: FileDiff | None) -> ft.Control:
    """无差异 / 二进制 / 出错时的占位提示。"""
    if diff is None:
        text, icon = "请选择要查看的文件", ft.Icons.COMPARE_ARROWS
    elif diff.error:
        text, icon = diff.error, ft.Icons.ERROR_OUTLINE
    elif diff.is_binary:
        text, icon = "二进制文件，无法显示文本差异", ft.Icons.DATA_OBJECT
    elif diff.is_empty:
        text, icon = "该文件没有差异", ft.Icons.CHECK_CIRCLE_OUTLINE
    else:
        text, icon = "无差异内容", ft.Icons.COMPARE_ARROWS
    return ft.Container(
        expand=True,
        alignment=ft.Alignment.CENTER,
        content=ft.Column(
            controls=[
                ft.Icon(icon, size=28, color=c.muted),
                ft.Container(height=Spacing.MD),
                ft.Text(
                    text,
                    size=12,
                    color=c.muted,
                    font_family=FONT_MAIN,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


# ---------------------------------------------------------------------------
# 主组件
# ---------------------------------------------------------------------------


@ft.component
def GitDiffView(
    visible: bool,
    diff: FileDiff | None,
    meta: dict,
    mode: str,
    theme_mode: ft.ThemeMode,
    *,
    busy: bool = False,
    on_set_mode: Callable[[str], None] | None = None,
    on_close: Callable[[], None] | None = None,
    on_jump: Callable[[int], None] | None = None,
    on_open_file: Callable[[], None] | None = None,
    on_stage: Callable[[], None] | None = None,
    on_unstage: Callable[[], None] | None = None,
    on_discard: Callable[[], None] | None = None,
) -> ft.Control:
    """Git 差异面板。

    meta（由 App 组装）字段：
        - ``path``：仓库相对路径
        - ``title``：展示名（重命名时为「旧 → 新」）
        - ``label``：变更类型中文标签（如「已修改」）
        - ``kind``：ChangeKind（决定字母徽章颜色）
        - ``staged``：是否为暂存区差异（决定显示「暂存」还是「取消暂存」）
        - ``history``：是否为历史提交差异（历史 diff 不提供暂存/丢弃）
        - ``jump_path``：行号跳转时要打开的文件绝对路径（历史 diff 用当前文件）
    """
    c = get_colors(theme_mode)

    # 渲染预算（局部 state）：超限时点「继续渲染」递增，仅本组件重渲染
    budget, set_budget = ft.use_state(_INITIAL_BUDGET)
    # 换文件 / 换模式时重置预算，避免上一个文件的放大预算泄漏到下一个文件
    _reset_key = (id(diff), mode)
    ft.use_effect(lambda: set_budget(_INITIAL_BUDGET), [_reset_key])

    # 回调经 ref 转发：行控件由 use_memo 缓存，直接闭包捕获会拿到过期回调；
    # ref 每次渲染写入最新值，memo 工厂读 ref.current 永远是最新回调。
    cb_ref = ft.use_ref({"jump": on_jump})
    cb_ref.current = {"jump": on_jump}

    def _rows():
        if diff is None or not diff.hunks:
            return ([], 0)
        jump = cb_ref.current["jump"]
        if mode == MODE_SPLIT:
            return _build_split_rows_controls(diff, c, on_jump=jump, budget=budget)
        return _build_unified_rows(diff, c, on_jump=jump, budget=budget)

    # 依赖：文件身份 + 模式 + 预算 + 主题（颜色变了要重建，否则主题切换后
    # 仍是旧底色）。diff 内容不可变，id(diff) 足够。
    rows, total = ft.use_memo(_rows, [id(diff), mode, budget, theme_mode])

    if not visible:
        # 不可见时零尺寸：不参与命中，也不占用布局（App 侧仍保持挂载以保留预算）
        return ft.Container(width=0, height=0, visible=False)

    # ---- 头部 ----
    path = meta.get("path") or (diff.path if diff else "")
    title = meta.get("title") or path
    kind = meta.get("kind")
    additions = diff.additions if diff else 0
    deletions = diff.deletions if diff else 0

    def _stat(text: str, fg: str, bg: str) -> ft.Control:
        return ft.Container(
            bgcolor=bg,
            border_radius=Radius.SM,
            padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=1),
            content=ft.Text(text, size=10, color=fg, font_family=FONT_MONO,
                            weight=ft.FontWeight.W_600),
        )

    mode_buttons: list[ft.Control] = [
        git_text_button(
            "统一",
            "统一视图：单列显示增删",
            (lambda: on_set_mode(MODE_UNIFIED)) if on_set_mode else None,
            c,
            icon=ft.Icons.VIEW_STREAM,
            active=mode == MODE_UNIFIED,
        ),
        git_text_button(
            "分栏",
            "分栏视图：左右并排对照",
            (lambda: on_set_mode(MODE_SPLIT)) if on_set_mode else None,
            c,
            icon=ft.Icons.VERTICAL_SPLIT,
            active=mode == MODE_SPLIT,
        ),
    ]

    file_actions: list[ft.Control] = []
    if on_open_file is not None:
        file_actions.append(
            git_icon_button(ft.Icons.OPEN_IN_NEW, "在编辑器中打开", on_open_file, c.link, size=15)
        )
    if not meta.get("history") and path:
        if meta.get("staged"):
            file_actions.append(
                git_icon_button(
                    ft.Icons.REMOVE, "取消暂存此文件",
                    None if busy or on_unstage is None else on_unstage, c.muted, size=15,
                )
            )
        elif on_stage is not None:
            file_actions.append(
                git_icon_button(
                    ft.Icons.ADD, "暂存此文件",
                    None if busy else on_stage, c.link, size=15,
                )
            )
        if on_discard is not None:
            file_actions.append(
                git_icon_button(
                    ft.Icons.UNDO, "丢弃此文件的更改",
                    None if busy else on_discard, c.muted, size=15,
                )
            )
    if on_close is not None:
        file_actions.append(
            git_icon_button(ft.Icons.CLOSE, "关闭差异视图 (Esc)", on_close, c.muted, size=15)
        )

    header = ft.Container(
        bgcolor=c.toolbar_bg,
        border=only_border(bottom=ft.BorderSide(1, c.border)),
        padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.SM),
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        letter_badge(
                            _LETTER_FOR.get(str(kind), "M") if kind else "M", kind, c
                        ),
                        ft.Text(
                            title,
                            size=12,
                            color=c.text,
                            font_family=FONT_MAIN,
                            weight=ft.FontWeight.W_600,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            tooltip=(
                                f"{meta.get('label')} · {path}"
                                if meta.get("label") and meta.get("label") != title
                                else path
                            ),
                        ),
                        ft.Container(
                            expand=True,
                            content=ft.Text(
                                path,
                                size=10,
                                color=c.muted,
                                font_family=FONT_MONO,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ),
                        _stat(f"+{additions}", git_status_color("added", c), c.diff_add_bg),
                        _stat(f"-{deletions}", git_status_color("deleted", c), c.diff_del_bg),
                        *file_actions,
                    ],
                    spacing=Spacing.SM,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    controls=[
                        *mode_buttons,
                        ft.Container(expand=True),
                        *(
                            [
                                ft.Text(
                                    f"共 {total} 行差异",
                                    size=10,
                                    color=c.muted,
                                    font_family=FONT_MONO,
                                )
                            ]
                            if total
                            else []
                        ),
                    ],
                    spacing=Spacing.SM,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=Spacing.XS,
        ),
    )

    # ---- 列头 ----
    if mode == MODE_SPLIT:
        col_header = ft.Row(
            controls=[
                ft.Container(
                    expand=True,
                    padding=ft.Padding.only(left=Spacing.SM),
                    content=ft.Text("原始（HEAD / 索引）", size=10, color=c.muted,
                                    font_family=FONT_MONO, max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS),
                ),
                ft.Container(width=1, bgcolor=c.border),
                ft.Container(
                    expand=True,
                    padding=ft.Padding.only(left=Spacing.SM),
                    content=ft.Text("修改后（工作区 / 提交）", size=10, color=c.muted,
                                    font_family=FONT_MONO, max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS),
                ),
            ],
            spacing=0,
        )
    else:
        col_header = ft.Row(
            controls=[
                ft.Container(width=_GUTTER_W * 2 + _SIGN_W),
                ft.Container(
                    expand=True,
                    padding=ft.Padding.only(left=Spacing.SM),
                    content=ft.Text("原始 / 修改后", size=10, color=c.muted,
                                    font_family=FONT_MONO, max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS),
                ),
            ],
            spacing=0,
        )
    col_header = ft.Container(
        bgcolor=c.toolbar_bg,
        border=only_border(bottom=ft.BorderSide(1, c.border)),
        padding=ft.Padding.symmetric(vertical=2),
        content=col_header,
    )

    # ---- 提示条（截断 / 二进制 / 错误）----
    hints: list[ft.Control] = []
    warn = git_status_color("modified", c)
    if diff is not None and diff.truncated:
        hints.append(
            _banner(
                f"文件差异过大，仅解析了前 {_plain_limit()} 行（可在 Git 设置中调整上限）",
                warn, c,
            )
        )
    if diff is not None and diff.is_binary:
        hints.append(_banner("二进制文件：仅显示统计信息，不展示文本差异", c.muted, c))
    if diff is not None and diff.error:
        hints.append(_banner(diff.error, git_status_color("deleted", c), c))

    # ---- 行区 ----
    if not rows:
        body: ft.Control = _empty_body(c, diff)
    else:
        controls = list(rows)
        if total > len(rows):
            controls.append(
                _budget_row(
                    len(rows), total, c,
                    on_more=(lambda: set_budget(budget + _BUDGET_STEP)),
                )
            )
        body = ft.ListView(
            controls=controls,
            spacing=0,
            expand=True,
            item_extent=_ROW_H,
            # Flet 侧的惰性构建入口：只有即将进入视口的行才实例化 Flutter 控件。
            # 与上面的渲染预算叠加——预算挡住「Python 侧构造 + 序列化」的开销，
            # build_controls_on_demand 挡住「Flutter 侧实例化 + 布局」的开销。
            build_controls_on_demand=True,
        )

    return ft.Container(
        expand=True,
        bgcolor=c.surface,
        content=ft.Column(
            controls=[header, col_header, *hints, body],
            spacing=0,
            expand=True,
        ),
    )


def _plain_limit() -> int:
    """解析层上限（仅用于提示文案）。"""
    return MAX_DIFF_LINES


def _banner(text: str, color: str, c) -> ft.Control:
    """头部提示条（截断 / 二进制 / 错误）。"""
    return ft.Container(
        bgcolor=ft.Colors.with_opacity(0.10, color),
        padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.XS),
        content=ft.Row(
            controls=[
                ft.Icon(ft.Icons.INFO_OUTLINE, size=13, color=color),
                ft.Text(
                    text,
                    size=10,
                    color=color,
                    font_family=FONT_MAIN,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                ),
            ],
            spacing=Spacing.SM,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


def _budget_row(shown: int, total: int, c, *, on_more) -> ft.Control:
    """渲染预算用尽提示 + 「继续渲染」入口（每次追加 _BUDGET_STEP 行）。"""
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.MD),
        content=ft.Row(
            controls=[
                ft.Text(
                    f"已渲染 {shown} / {total} 行",
                    size=10,
                    color=c.muted,
                    font_family=FONT_MONO,
                ),
                ft.Container(expand=True),
                git_text_button(
                    f"继续渲染 {_BUDGET_STEP} 行",
                    "继续渲染后续差异行（分页构建，避免一次性构建上万控件）",
                    on_more,
                    c,
                    icon=ft.Icons.EXPAND_MORE,
                ),
            ],
            spacing=Spacing.SM,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
