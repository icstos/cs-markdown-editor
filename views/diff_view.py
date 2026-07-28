"""文件对比：双编辑器行级 diff 计算 + 间隙对齐。

基于 difflib.SequenceMatcher 做行级 diff，为两个 MarkdownEditor 实例计算：
- marks：每行的 diff 标记（"equal"|"added"|"removed"|"modified"）
- gaps：对齐间隙（一侧有行另一侧没有时，在缺失侧插入等高占位容器）

这样两个编辑器可以原生编辑，同时通过背景色 + 间隙对齐直观展示差异。

- compute_diff_for_editors：核心计算函数，返回 (marks_left, marks_right, gaps_left, gaps_right)
- _compute_diff_rows：行级 diff 行对列表（旧 DiffView overlay 用，保留兼容）
"""

import difflib
import os
from collections.abc import Callable

import flet as ft

from styles import FONT_MAIN, FONT_MONO, Elevation, Radius, Spacing, card_shadow, get_colors, only_border

_MAX_DIFF_ROWS = 3000  # 防止超大文件卡顿
_LN_WIDTH = 38  # 行号列宽
_DIFF_WARN_COLOR = "#FF9F0A"  # 截断提示的警告色

# 间隙默认估算高度：body_font_size(16) * line_height(1.6) + padding(4) ≈ 30
_DEFAULT_GAP_HEIGHT = 30.0


def compute_diff_for_editors(
    left_text: str,
    right_text: str,
    gap_height: float = _DEFAULT_GAP_HEIGHT,
) -> tuple[dict, dict, dict, dict]:
    """为双编辑器模式计算 diff 标记和间隙。

    返回 (marks_left, marks_right, gaps_left, gaps_right)：
    - marks_left: {line_idx: "equal"|"removed"|"modified"} 左侧每行标记
    - marks_right: {line_idx: "equal"|"added"|"modified"} 右侧每行标记
    - gaps_left: {after_line_idx: [height, ...]} 左侧需插入的间隙（-1=首行前）
    - gaps_right: {after_line_idx: [height, ...]} 右侧需插入的间隙（-1=首行前）

    算法：difflib 行级 diff，delete→左侧标记 removed + 右侧插入间隙，
    insert→右侧标记 added + 左侧插入间隙，replace→两侧标记 modified + 短侧补间隙。
    """
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    matcher = difflib.SequenceMatcher(None, left_lines, right_lines, autojunk=False)

    marks_left: dict[int, str] = {}
    marks_right: dict[int, str] = {}
    gaps_left: dict[int, list[float]] = {}
    gaps_right: dict[int, list[float]] = {}

    last_left = -1   # 最后处理的左侧行号（间隙插入锚点）
    last_right = -1  # 最后处理的右侧行号（间隙插入锚点）

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                marks_left[i] = "equal"
                marks_right[j] = "equal"
                last_left = i
                last_right = j
        elif tag == "replace":
            left_count = i2 - i1
            right_count = j2 - j1
            for i in range(i1, i2):
                marks_left[i] = "modified"
                last_left = i
            for j in range(j1, j2):
                marks_right[j] = "modified"
                last_right = j
            # 短侧补间隙对齐
            if left_count > right_count:
                gaps_right.setdefault(last_right, []).extend(
                    [gap_height] * (left_count - right_count)
                )
            elif right_count > left_count:
                gaps_left.setdefault(last_left, []).extend(
                    [gap_height] * (right_count - left_count)
                )
        elif tag == "delete":
            # 仅左侧有行：标记 removed，右侧补间隙
            for i in range(i1, i2):
                marks_left[i] = "removed"
                last_left = i
            gaps_right.setdefault(last_right, []).extend(
                [gap_height] * (i2 - i1)
            )
        elif tag == "insert":
            # 仅右侧有行：标记 added，左侧补间隙
            for j in range(j1, j2):
                marks_right[j] = "added"
                last_right = j
            gaps_left.setdefault(last_left, []).extend(
                [gap_height] * (j2 - j1)
            )

    return marks_left, marks_right, gaps_left, gaps_right


def _diff_colors(c, is_dark: bool) -> dict:
    """返回 diff 配色字典（GitHub 风格，亮/暗自适应）。"""
    if is_dark:
        return {
            "added_bg": "#1a2e22",
            "removed_bg": "#2e1a1d",
            "added_char": "#7ee787",
            "removed_char": "#f47067",
            "equal_text": c.text,
            "empty_bg": ft.Colors.with_opacity(0.03, c.text),
        }
    return {
        "added_bg": "#e6ffed",
        "removed_bg": "#ffeef0",
        "added_char": "#1a7f37",
        "removed_char": "#cf222e",
        "equal_text": c.text,
        "empty_bg": ft.Colors.with_opacity(0.02, c.text),
    }


def _char_diff_spans(left: str, right: str, colors: dict) -> tuple[list, list]:
    """对两行做字符级 diff，返回 (left_spans, right_spans)。

    相同部分用 equal_text 色，左侧独有部分用 removed_char 色，
    右侧独有部分用 added_char 色。加粗变更部分以突出差异。
    """
    if not left and not right:
        return [], []
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    left_spans: list[ft.TextSpan] = []
    right_spans: list[ft.TextSpan] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            text = left[i1:i2]
            if text:
                style = ft.TextStyle(color=colors["equal_text"])
                left_spans.append(ft.TextSpan(text=text, style=style))
                right_spans.append(ft.TextSpan(text=text, style=style))
        elif tag == "replace":
            l_text = left[i1:i2]
            r_text = right[j1:j2]
            if l_text:
                left_spans.append(ft.TextSpan(
                    text=l_text,
                    style=ft.TextStyle(color=colors["removed_char"], weight=ft.FontWeight.W_700),
                ))
            if r_text:
                right_spans.append(ft.TextSpan(
                    text=r_text,
                    style=ft.TextStyle(color=colors["added_char"], weight=ft.FontWeight.W_700),
                ))
        elif tag == "delete":
            l_text = left[i1:i2]
            if l_text:
                left_spans.append(ft.TextSpan(
                    text=l_text,
                    style=ft.TextStyle(color=colors["removed_char"], weight=ft.FontWeight.W_700),
                ))
        elif tag == "insert":
            r_text = right[j1:j2]
            if r_text:
                right_spans.append(ft.TextSpan(
                    text=r_text,
                    style=ft.TextStyle(color=colors["added_char"], weight=ft.FontWeight.W_700),
                ))
    return left_spans, right_spans


def _compute_diff_rows(left_text: str, right_text: str) -> list[dict]:
    """计算行级 diff，返回行对列表。

    每项: {"left_ln", "left_text", "right_ln", "right_text", "type"}
    type ∈ {"equal", "replace", "delete", "insert"}
    "replace" 行的短侧用空行填充，保证左右对齐。
    """
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    matcher = difflib.SequenceMatcher(None, left_lines, right_lines, autojunk=False)

    rows: list[dict] = []
    left_ln = 1
    right_ln = 1

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                rows.append({
                    "left_ln": left_ln, "left_text": left_lines[i],
                    "right_ln": right_ln, "right_text": right_lines[j],
                    "type": "equal",
                })
                left_ln += 1
                right_ln += 1
        elif tag == "replace":
            left_slice = left_lines[i1:i2]
            right_slice = right_lines[j1:j2]
            max_len = max(len(left_slice), len(right_slice))
            for k in range(max_len):
                l_text = left_slice[k] if k < len(left_slice) else ""
                r_text = right_slice[k] if k < len(right_slice) else ""
                l_ln = left_ln + k if k < len(left_slice) else None
                r_ln = right_ln + k if k < len(right_slice) else None
                rows.append({
                    "left_ln": l_ln, "left_text": l_text,
                    "right_ln": r_ln, "right_text": r_text,
                    "type": "replace",
                })
            left_ln += len(left_slice)
            right_ln += len(right_slice)
        elif tag == "delete":
            for i in range(i1, i2):
                rows.append({
                    "left_ln": left_ln, "left_text": left_lines[i],
                    "right_ln": None, "right_text": "",
                    "type": "delete",
                })
                left_ln += 1
        elif tag == "insert":
            for j in range(j1, j2):
                rows.append({
                    "left_ln": None, "left_text": "",
                    "right_ln": right_ln, "right_text": right_lines[j],
                    "type": "insert",
                })
                right_ln += 1

    return rows


def _build_diff_row(row: dict, c, colors: dict) -> ft.Control:
    """构建单行 diff 控件：[左行号 | 左内容 | 右行号 | 右内容]。"""
    rtype = row["type"]
    left_ln = str(row["left_ln"]) if row["left_ln"] is not None else ""
    right_ln = str(row["right_ln"]) if row["right_ln"] is not None else ""
    left_text = row["left_text"]
    right_text = row["right_text"]

    # 背景色：added=绿底，removed=红底，empty=浅灰底
    if rtype == "equal":
        left_bg = None
        right_bg = None
    elif rtype == "replace":
        left_bg = colors["removed_bg"]
        right_bg = colors["added_bg"]
    elif rtype == "delete":
        left_bg = colors["removed_bg"]
        right_bg = colors["empty_bg"]
    else:  # insert
        left_bg = colors["empty_bg"]
        right_bg = colors["added_bg"]

    # 内容：replace 行做字符级 diff
    if rtype == "replace" and left_text and right_text:
        left_spans, right_spans = _char_diff_spans(left_text, right_text, colors)
        left_ctrl = ft.Text(spans=left_spans, font_family=FONT_MONO, size=12,
                            expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        right_ctrl = ft.Text(spans=right_spans, font_family=FONT_MONO, size=12,
                             expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    else:
        left_ctrl = ft.Text(
            value=left_text or " ", font_family=FONT_MONO, size=12,
            color=colors["equal_text"], expand=True,
            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
        )
        right_ctrl = ft.Text(
            value=right_text or " ", font_family=FONT_MONO, size=12,
            color=colors["equal_text"], expand=True,
            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
        )

    def _ln_cell(text: str, bg) -> ft.Control:
        return ft.Container(
            width=_LN_WIDTH, bgcolor=bg,
            content=ft.Text(
                value=text, size=10, color=c.muted, font_family=FONT_MONO,
                text_align=ft.TextAlign.RIGHT,
            ),
            padding=ft.Padding.only(right=4, left=4),
        )

    def _content_cell(ctrl: ft.Control, bg) -> ft.Control:
        return ft.Container(
            expand=True, bgcolor=bg, content=ctrl,
            padding=ft.Padding.only(right=8, left=4),
        )

    return ft.Row(
        controls=[
            _ln_cell(left_ln, left_bg),
            _content_cell(left_ctrl, left_bg),
            _ln_cell(right_ln, right_bg),
            _content_cell(right_ctrl, right_bg),
        ],
        spacing=0,
        tight=True,
    )


@ft.component
def DiffView(
    visible: bool,
    left_path: str,
    right_path: str,
    left_text: str,
    right_text: str,
    theme_mode: ft.ThemeMode,
    on_close: Callable[[], None],
):
    """文件对比视图：全屏 overlay，左右并排显示行级 diff。

    - 左侧为比较源（选择以进行比较的文件），右侧为比较目标
    - 行级 diff + 替换行字符级 diff 高亮
    - 单 Column 双列 Row 布局，左右同步滚动
    - 统计信息：增删行数
    """
    c = get_colors(theme_mode)
    is_dark = theme_mode == ft.ThemeMode.DARK
    colors = _diff_colors(c, is_dark)

    # 计算 diff
    diff_rows = _compute_diff_rows(left_text, right_text)
    truncated = len(diff_rows) > _MAX_DIFF_ROWS
    if truncated:
        diff_rows = diff_rows[:_MAX_DIFF_ROWS]

    # 统计
    added = sum(1 for r in diff_rows if r["type"] in ("insert", "replace") and r["right_ln"] is not None)
    removed = sum(1 for r in diff_rows if r["type"] in ("delete", "replace") and r["left_ln"] is not None)

    # 构建 diff 行控件
    row_controls = [_build_diff_row(r, c, colors) for r in diff_rows]

    # 空文件/相同文件提示
    if not row_controls:
        row_controls = [
            ft.Container(
                expand=True, alignment=ft.Alignment.CENTER,
                content=ft.Text(
                    "文件内容相同，无差异" if left_text == right_text else "无内容",
                    size=14, color=c.muted, font_family=FONT_MAIN,
                ),
            )
        ]

    # 头部：文件名 + 统计 + 关闭
    left_name = os.path.basename(left_path) if left_path else "未命名"
    right_name = os.path.basename(right_path) if right_path else "未命名"
    header = ft.Container(
        bgcolor=c.toolbar_bg,
        border=only_border(bottom=ft.BorderSide(1, c.border)),
        padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.SM),
        content=ft.Row(
            controls=[
                ft.Icon(ft.Icons.COMPARE_ARROWS, color=c.link, size=18),
                ft.Text(
                    value=f"{left_name}  →  {right_name}",
                    size=13, color=c.text, font_family=FONT_MAIN,
                    weight=ft.FontWeight.W_600,
                ),
                ft.Container(width=Spacing.MD),
                # 统计徽章
                ft.Container(
                    bgcolor=colors["added_bg"],
                    border_radius=Radius.SM,
                    padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=2),
                    content=ft.Text(f"+{added}", size=11, color=colors["added_char"],
                                    font_family=FONT_MONO, weight=ft.FontWeight.W_600),
                ),
                ft.Container(
                    bgcolor=colors["removed_bg"],
                    border_radius=Radius.SM,
                    padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=2),
                    content=ft.Text(f"-{removed}", size=11, color=colors["removed_char"],
                                    font_family=FONT_MONO, weight=ft.FontWeight.W_600),
                ),
                ft.Container(expand=True),
                ft.IconButton(
                    icon=ft.Icons.CLOSE,
                    tooltip="关闭对比",
                    on_click=lambda e: on_close(),
                    icon_size=18,
                    style=ft.ButtonStyle(color=c.muted),
                ),
            ],
            spacing=Spacing.SM,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )

    # 截断提示
    footer_hint = ft.Container(
        visible=truncated,
        bgcolor=ft.Colors.with_opacity(0.1, _DIFF_WARN_COLOR),
        padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.XS),
        content=ft.Text(
            f"文件过大，仅显示前 {_MAX_DIFF_ROWS} 行差异",
            size=11, color=_DIFF_WARN_COLOR, font_family=FONT_MAIN,
        ),
    ) if truncated else ft.Container(height=0)

    # 中间分隔线
    divider = ft.VerticalDivider(width=1, color=c.border)

    # diff 内容：左右列头 + 滚动行列表
    # 列头
    col_header = ft.Container(
        bgcolor=c.toolbar_bg,
        border=only_border(bottom=ft.BorderSide(1, c.border)),
        content=ft.Row(
            controls=[
                ft.Container(width=_LN_WIDTH),
                ft.Container(
                    expand=True,
                    padding=ft.Padding.only(left=4, right=8),
                    content=ft.Text(left_name, size=11, color=c.muted, font_family=FONT_MONO,
                                    weight=ft.FontWeight.W_600, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ),
                ft.Container(width=_LN_WIDTH),
                ft.Container(
                    expand=True,
                    padding=ft.Padding.only(left=4, right=8),
                    content=ft.Text(right_name, size=11, color=c.muted, font_family=FONT_MONO,
                                    weight=ft.FontWeight.W_600, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ),
            ],
            spacing=0,
            tight=True,
        ),
    )

    diff_body = ft.Container(
        expand=True,
        bgcolor=c.surface,
        content=ft.Column(
            controls=[col_header] + row_controls,
            spacing=0,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
    )

    return ft.Container(
        visible=visible,
        expand=True,
        bgcolor=ft.Colors.with_opacity(0.4, ft.Colors.BLACK),
        alignment=ft.Alignment.CENTER,
        content=ft.Container(
            width=float("inf"),
            height=float("inf"),
            margin=ft.Margin.all(16),
            bgcolor=c.toolbar_bg,
            border_radius=Radius.XL,
            shadow=card_shadow(Elevation.DIALOG, is_dark),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=ft.Column(
                controls=[header, diff_body, footer_hint],
                spacing=0,
                expand=True,
            ),
        ),
    )
