"""Git UI 通用部件工厂（自包含，供面板 / 差异视图 / 状态栏共用）。

与 ``views/_widgets.py`` 同一定位：只做「控件工厂」，不含业务逻辑与状态。

三条硬约束（来自 AGENT.md 红线规则，均已在既有代码中踩过坑）：
1. **不使用 ``ft.IconButton``**：Material 固有高度 40（``visual_density=COMPACT``
   也只降到 32），会顶破 Git 面板的紧凑行高。统一用固定尺寸
   ``Container(ink=True, width=height=GIT_ICON_SIZE)``（与 ``status_bar`` /
   ``activity_bar`` 同一惯例）。
2. **不实现任何「渲染后改属性」**：Flet 1.0 组件在 render 后即冻结
   （``RuntimeError: Frozen controls cannot be updated.``），因此 hover / 高亮
   一律靠 props 计算，不做命令式 update。
3. 颜色只走 ``styles.git_status_color()``（唯一取色口），不写死色值。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from styles import FONT_MAIN, FONT_MONO, Radius, Spacing, git_status_color
from views._widgets import _empty_hint
from views.native_scope import DOMAIN_GIT_COMMIT, native_focus_hooks

__all__ = [
    "GIT_ICON_SIZE",
    "divider_line",
    "empty_hint",
    "file_row",
    "filter_box",
    "git_icon_button",
    "git_text_button",
    "letter_badge",
    "multiline_box",
    "section_header",
    "stat_badge",
]

#: 紧凑图标按钮边长（与状态栏 / 活动栏一致的 22px 触控区）
GIT_ICON_SIZE = 22


def git_icon_button(
    icon: str,
    tooltip: str,
    on_click: Callable[[], None] | None,
    color: str,
    *,
    size: int = 14,
    box: int = GIT_ICON_SIZE,
    disabled: bool = False,
) -> ft.Control:
    """紧凑图标按钮（固定尺寸 + ink 反馈 + tooltip）。

    刻意不用 ``ft.IconButton``：它的 Material 固有高度会把紧凑工具行顶开。
    ``on_click is None`` 或 ``disabled`` 时降透明度，视觉上表达「不可用」。
    """
    enabled = on_click is not None and not disabled
    return ft.Container(
        width=box,
        height=box,
        border_radius=Radius.SM,
        alignment=ft.Alignment.CENTER,
        ink=enabled,
        tooltip=tooltip,
        opacity=1.0 if enabled else 0.4,
        on_click=(lambda e: on_click()) if enabled else None,
        content=ft.Icon(icon, size=size, color=color),
    )


def git_text_button(
    label: str,
    tooltip: str,
    on_click: Callable[[], None] | None,
    c,
    *,
    icon: str | None = None,
    active: bool = False,
    disabled: bool = False,
    expand: bool = False,
) -> ft.Control:
    """紧凑文本按钮（用于「提交」「拉取」这类需要文字的入口）。"""
    enabled = on_click is not None and not disabled
    controls: list[ft.Control] = []
    if icon:
        controls.append(ft.Icon(icon, size=13, color=c.text if enabled else c.muted))
    controls.append(
        ft.Text(
            label,
            size=11,
            color=c.text if enabled else c.muted,
            font_family=FONT_MAIN,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
    )
    return ft.Container(
        expand=expand,
        height=24,
        border_radius=Radius.SM,
        alignment=ft.Alignment.CENTER,
        padding=ft.Padding.symmetric(horizontal=Spacing.MD),
        bgcolor=ft.Colors.with_opacity(0.14, c.link) if active else None,
        tooltip=tooltip,
        ink=enabled,
        on_click=(lambda e: on_click()) if enabled else None,
        content=ft.Row(
            controls=controls,
            spacing=Spacing.SM,
            tight=True,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


def letter_badge(letter: str, kind: object, c) -> ft.Control:
    """变更类型字母（A/M/D/R/U/!）——等宽、类型色、固定宽度保证纵向对齐。"""
    return ft.Container(
        width=14,
        alignment=ft.Alignment.CENTER,
        content=ft.Text(
            letter,
            size=11,
            color=git_status_color(kind, c),
            font_family=FONT_MONO,
            weight=ft.FontWeight.W_700,
        ),
    )


def stat_badge(text: str, color: str, bgcolor) -> ft.Control:
    """统计徽章（+N / -N / ~N），与 ``views/diff_markers._stat_badge`` 同款。"""
    return ft.Container(
        bgcolor=bgcolor,
        border_radius=Radius.SM,
        padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=1),
        content=ft.Text(
            text, size=10, color=color, font_family=FONT_MONO, weight=ft.FontWeight.W_600
        ),
    )


def file_row(
    *,
    name: str,
    dirname: str,
    letter: str,
    kind: object,
    c,
    on_click: Callable[[], None] | None = None,
    leading: str | None = None,
    tooltip: str = "",
    actions: list[ft.Control] | None = None,
    active: bool = False,
    subtitle: str | None = None,
) -> ft.Control:
    """文件行：``[chevron?] [icon] 名称(目录灰) … [右侧操作按钮]``。

    VS Code 源代码管理视图的排布：文件名用正文色、所在目录用弱化色，
    变更字母用类型色，操作按钮右对齐（面板里常驻显示，比 hover 更易发现）。
    """
    title_controls: list[ft.Control] = [
        ft.Text(
            name,
            size=12,
            color=c.text,
            font_family=FONT_MAIN,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
    ]
    if dirname:
        title_controls.append(
            ft.Text(
                dirname,
                size=11,
                color=c.muted,
                font_family=FONT_MAIN,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                expand=True,
            )
        )
    else:
        title_controls[0].expand = True

    body = ft.Row(
        controls=[
            *([ft.Container(width=10, alignment=ft.Alignment.CENTER,
                            content=ft.Icon(leading, size=12, color=c.muted))]
              if leading else []),
            ft.Icon(ft.Icons.DESCRIPTION_OUTLINED, size=13, color=c.muted),
            ft.Row(controls=title_controls, spacing=Spacing.SM, tight=True, expand=True),
            letter_badge(letter, kind, c),
            *([ft.Row(controls=actions, spacing=0, tight=True)] if actions else []),
        ],
        spacing=Spacing.SM,
        tight=True,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    column_controls: list[ft.Control] = [body]
    if subtitle:
        column_controls.append(
            ft.Text(
                subtitle,
                size=10,
                color=c.muted,
                font_family=FONT_MAIN,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
        )
    return ft.Container(
        padding=ft.Padding.only(
            left=Spacing.XL, right=Spacing.SM, top=Spacing.SM, bottom=Spacing.SM
        ),
        border_radius=Radius.MD,
        bgcolor=ft.Colors.with_opacity(0.10, c.link) if active else None,
        ink=on_click is not None,
        on_click=(lambda e: on_click()) if on_click else None,
        tooltip=tooltip or None,
        content=ft.Column(controls=column_controls, spacing=0),
    )


def section_header(
    title: str,
    count: int,
    *,
    expanded: bool,
    on_toggle: Callable[[], None] | None,
    c,
    actions: list[ft.Control] | None = None,
) -> ft.Control:
    """分区标题行：``▾ 暂存的更改 (2)  … [全部操作]``（VS Code 可折叠分区）。"""
    return ft.Container(
        padding=ft.Padding.only(left=Spacing.SM, right=Spacing.SM, top=Spacing.MD, bottom=2),
        content=ft.Row(
            controls=[
                ft.Container(
                    width=18,
                    height=18,
                    alignment=ft.Alignment.CENTER,
                    ink=on_toggle is not None,
                    on_click=(lambda e: on_toggle()) if on_toggle else None,
                    tooltip="折叠/展开分区",
                    content=ft.Icon(
                        ft.Icons.ARROW_DROP_DOWN if expanded else ft.Icons.ARROW_RIGHT,
                        size=14,
                        color=c.muted,
                    ),
                ),
                ft.Text(
                    title,
                    size=11,
                    color=c.text,
                    font_family=FONT_MAIN,
                    weight=ft.FontWeight.W_700,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Container(
                    bgcolor=ft.Colors.with_opacity(0.12, c.text),
                    border_radius=Radius.SM,
                    padding=ft.Padding.symmetric(horizontal=4, vertical=0),
                    content=ft.Text(
                        str(count), size=10, color=c.muted, font_family=FONT_MONO
                    ),
                ),
                ft.Container(expand=True),
                *(actions or []),
            ],
            spacing=Spacing.SM,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


def empty_hint(text: str, c) -> ft.Control:
    """居中浅色提示（复用通用部件，保持与其它面板一致的观感）。"""
    return _empty_hint(text, c)


def filter_box(
    value: str,
    on_change: Callable[[str], None],
    placeholder: str,
    c,
    *,
    ref: ft.Ref | None = None,
    native_ref: ft.Ref | None = None,
    on_submit: Callable[[], None] | None = None,
) -> ft.Control:
    """面板内过滤输入框（下划线紧凑样式，与侧边栏搜索框一致）。

    ``on_submit`` 非空时回车触发（历史筛选「回车生效」）；
    ``native_ref`` 非空时挂 on_focus/on_blur：焦点在本输入框期间
    KeyDispatcher 不把文档编辑快捷键作用到编辑器（焦点域门控）。
    """
    focus_h, blur_h = native_focus_hooks(native_ref)
    return ft.TextField(
        value=value,
        hint_text=placeholder,
        dense=True,
        border=ft.InputBorder.UNDERLINE,
        text_size=12,
        content_padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=Spacing.LG),
        on_change=lambda e: on_change(e.control.value or ""),
        on_focus=focus_h,
        on_blur=blur_h,
        on_submit=(lambda e: on_submit()) if on_submit else None,
        ref=ref,
    )


def multiline_box(
    value: str,
    on_change: Callable[[str], None],
    placeholder: str,
    c,
    *,
    on_submit: Callable[[], None] | None = None,
    native_ref: ft.Ref | None = None,
    min_lines: int = 2,
    max_lines: int = 6,
) -> ft.Control:
    """提交信息输入框（多行，Enter 换行、Ctrl+Enter 提交）。

    Flet 的 ``TextField`` 在 ``multiline=True`` 下 Enter 即换行，无法直接捕获
    Ctrl+Enter（``on_submit`` 不触发），因此 Ctrl+Enter 由 KeyDispatcher 处理：
    本输入框的焦点域 token 为具名域 ``git_commit``（VSCode 同款：Ctrl+Enter 只在
    提交框内表示提交，在编辑器里仍是「切换原文模式」）。
    """
    focus_h, blur_h = native_focus_hooks(native_ref, DOMAIN_GIT_COMMIT)
    return ft.TextField(
        value=value,
        hint_text=placeholder,
        multiline=True,
        min_lines=min_lines,
        max_lines=max_lines,
        dense=True,
        border=ft.InputBorder.OUTLINE,
        border_radius=Radius.MD,
        text_size=12,
        content_padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.MD),
        on_change=lambda e: on_change(e.control.value or ""),
        on_focus=focus_h,
        on_blur=blur_h,
        on_submit=(lambda e: on_submit()) if on_submit else None,
    )


def divider_line(c) -> ft.Control:
    """1px 分隔线。"""
    return ft.Container(height=1, bgcolor=c.border)
