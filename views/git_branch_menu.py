"""分支管理面板：切换 / 新建 / 合并 / 删除分支（对标 VS Code 状态栏分支入口）。

唤起方式（两条入口，同一面板）：
- 点击底部状态栏的分支名
- 点击 Git 面板头部的分支按钮
- 命令面板 / 菜单「Git → 切换分支…」

结构（自上而下）：

    ┌ 切换分支 ───────────────────────── [刷新] [×]
    ├ 过滤框（按分支名过滤）
    ├ 创建新分支：[名称输入] [创建并切换]
    ├ ──────────
    ├ 本地分支（当前分支置顶并高亮）
    │   ● main                 ↑2 ↓1      [合并] [删除]
    │     feature/x
    ├ 远端分支（只读，点击可基于它创建本地跟踪分支）
    └

本地状态（组件内 use_state）：过滤词、新分支名输入——刻意留在局部，避免每敲一个
字符触发 App 全量重渲染（编辑器控件树很重）。分支列表本身是异步任务结果，由 App
持有（与状态栏、Git 面板共享同一份 ``git_branches``）。

``native_input_ref`` 非空时两个输入框挂 on_focus/on_blur：焦点在面板内期间
KeyDispatcher 不把文档编辑快捷键作用到编辑器（焦点域门控）。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from services.git.models import BranchInfo
from styles import (
    FONT_MAIN,
    FONT_MONO,
    Elevation,
    Radius,
    Spacing,
    card_shadow,
    get_colors,
    git_status_color,
)
from views.git_widgets import filter_box, git_icon_button, git_text_button

__all__ = ["GitBranchMenu"]


@ft.component
def GitBranchMenu(
    open_state: bool,
    branches: list[BranchInfo],
    current: str | None,
    theme_mode: ft.ThemeMode,
    *,
    busy: bool = False,
    loading: bool = False,
    native_input_ref=None,
    on_switch: Callable[[str], None] | None = None,
    on_create: Callable[[str], None] | None = None,
    on_delete: Callable[[str], None] | None = None,
    on_merge: Callable[[str], None] | None = None,
    on_refresh: Callable[[], None] | None = None,
    on_close: Callable[[], None] | None = None,
) -> ft.Control:
    """分支管理面板（居中卡片弹层）。"""
    c = get_colors(theme_mode)
    is_dark = theme_mode == ft.ThemeMode.DARK

    filter_text, set_filter_text = ft.use_state("")
    new_name, set_new_name = ft.use_state("")

    if not open_state:
        # 关闭时零尺寸：不拦截点击，也不参与布局
        return ft.Container(width=0, height=0, visible=False)

    text = filter_text.strip().lower()
    remote = [b for b in branches if b.is_remote]
    local = [b for b in branches if not b.is_remote]

    def _match(b: BranchInfo) -> bool:
        return not text or text in b.name.lower()

    local = [b for b in local if _match(b)]
    remote = [b for b in remote if _match(b)]
    # 当前分支置顶（VS Code 分支列表同款：当前分支永远在最前）
    local.sort(key=lambda b: (not b.current, b.name.lower()))
    remote.sort(key=lambda b: b.name.lower())

    def _create():
        name = new_name.strip()
        if not name or on_create is None or busy:
            return
        on_create(name)
        set_new_name("")

    # ---- 头部：标题 + 刷新 + 关闭 ----
    header = ft.Row(
        controls=[
            ft.Icon(ft.Icons.ACCOUNT_TREE, color=c.link, size=20),
            ft.Text("切换分支", size=15, weight=ft.FontWeight.W_600,
                    color=c.text, font_family=FONT_MAIN),
            ft.Container(expand=True),
            git_icon_button(
                ft.Icons.REFRESH, "重新读取分支列表",
                None if busy or on_refresh is None else on_refresh, c.muted, size=16, box=26,
            ),
            git_icon_button(
                ft.Icons.CLOSE, "关闭 (Esc)",
                on_close, c.muted, size=16, box=26,
            ),
        ],
        spacing=Spacing.MD,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # ---- 过滤 + 新建 ----
    tools = ft.Column(
        controls=[
            filter_box(
                filter_text, set_filter_text, "按分支名过滤…", c,
                native_ref=native_input_ref,
            ),
            ft.Row(
                controls=[
                    ft.Container(
                        expand=True,
                        content=ft.TextField(
                            value=new_name,
                            hint_text="新分支名称，如 feature/git-panel",
                            dense=True,
                            border=ft.InputBorder.OUTLINE,
                            border_radius=Radius.MD,
                            text_size=12,
                            content_padding=ft.Padding.symmetric(
                                horizontal=Spacing.MD, vertical=Spacing.MD
                            ),
                            on_change=lambda e: set_new_name(e.control.value or ""),
                            on_submit=lambda e: _create(),
                            **_focus_kwargs(native_input_ref),
                        ),
                    ),
                    git_text_button(
                        "创建并切换",
                        "基于当前 HEAD 创建新分支并切换过去",
                        _create if (new_name.strip() and not busy) else None,
                        c,
                        icon=ft.Icons.ADD,
                    ),
                ],
                spacing=Spacing.SM,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        ],
        spacing=Spacing.SM,
    )

    # ---- 列表 ----
    rows: list[ft.Control] = []
    rows.append(_section_label(f"本地分支（{len(local)}）", c))
    if not local:
        rows.append(_hint("没有匹配的本地分支", c))
    for b in local:
        rows.append(_branch_row(b, c, is_current=b.current, busy=busy, on_switch=on_switch,
                                on_delete=on_delete, on_merge=on_merge))
    if remote:
        rows.append(_section_label(f"远端分支（{len(remote)}）", c))
        for b in remote:
            rows.append(
                _branch_row(b, c, is_current=False, busy=busy, on_switch=on_switch,
                            on_delete=None, on_merge=None)
            )

    if loading and not branches:
        rows = [_hint("正在读取分支列表…", c)]

    return ft.Container(
        visible=True,
        expand=True,
        bgcolor=ft.Colors.with_opacity(0.28, ft.Colors.BLACK),
        alignment=ft.Alignment.CENTER,
        content=ft.Container(
            width=520,
            height=520,
            bgcolor=c.toolbar_bg,
            border_radius=Radius.XXXL,
            shadow=card_shadow(Elevation.DIALOG, is_dark),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            padding=Spacing.XXL,
            content=ft.Column(
                controls=[
                    header,
                    ft.Container(height=Spacing.SM),
                    tools,
                    ft.Container(height=Spacing.MD),
                    ft.Container(height=1, bgcolor=c.border),
                    ft.Container(
                        expand=True,
                        padding=ft.Padding.only(top=Spacing.SM),
                        content=ft.ListView(
                            controls=rows,
                            spacing=0,
                            expand=True,
                            build_controls_on_demand=True,
                        ),
                    ),
                ],
                spacing=0,
            ),
        ),
    )


def _focus_kwargs(native_input_ref) -> dict:
    """给原生 TextField 挂焦点域跟踪（无 ref 时返回空 dict）。"""
    from views.native_scope import native_focus_hooks

    focus_h, blur_h = native_focus_hooks(native_input_ref)
    if focus_h is None:
        return {}
    return {"on_focus": focus_h, "on_blur": blur_h}


def _section_label(text: str, c) -> ft.Control:
    """小节标题（本地分支 / 远端分支）。"""
    return ft.Container(
        padding=ft.Padding.only(left=Spacing.SM, top=Spacing.MD, bottom=2),
        content=ft.Text(
            text, size=10, color=c.muted, font_family=FONT_MAIN,
            weight=ft.FontWeight.W_700,
        ),
    )


def _hint(text: str, c) -> ft.Control:
    """列表内空态提示。"""
    return ft.Container(
        padding=ft.Padding.only(left=Spacing.SM, top=Spacing.MD, bottom=Spacing.MD),
        content=ft.Text(text, size=11, color=c.muted, font_family=FONT_MAIN),
    )


def _branch_row(
    b: BranchInfo,
    c,
    *,
    is_current: bool,
    busy: bool,
    on_switch,
    on_delete,
    on_merge,
) -> ft.Control:
    """单条分支行：``● 名称 ↑2 ↓1 [合并] [删除]``。"""
    name_color = c.link if is_current else c.text

    badges: list[ft.Control] = []
    if b.ahead:
        badges.append(
            ft.Text(f"↑{b.ahead}", size=10, color=git_status_color("added", c),
                    font_family=FONT_MONO, tooltip=f"领先上游 {b.ahead} 个提交")
        )
    if b.behind:
        badges.append(
            ft.Text(f"↓{b.behind}", size=10, color=git_status_color("deleted", c),
                    font_family=FONT_MONO, tooltip=f"落后上游 {b.behind} 个提交")
        )

    actions: list[ft.Control] = []
    if not is_current and not busy:
        if on_merge is not None:
            actions.append(
                git_icon_button(
                    ft.Icons.MERGE, f"把 {b.name} 合并到当前分支",
                    (lambda n=b.name: on_merge(n)), c.muted, size=13,
                )
            )
        if on_delete is not None:
            actions.append(
                git_icon_button(
                    ft.Icons.DELETE_OUTLINE, f"删除分支 {b.name}",
                    (lambda n=b.name: on_delete(n)), c.muted, size=13,
                )
            )

    def _click(_e=None):
        if on_switch is not None and not is_current and not busy:
            on_switch(b.name)

    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=Spacing.SM),
        border_radius=Radius.MD,
        ink=not is_current and not busy and on_switch is not None,
        on_click=_click,
        tooltip=(
            f"{b.name}（当前分支）" if is_current
            else f"{b.subject}\n{b.time_text}" if b.subject
            else f"切换到 {b.name}"
        ),
        content=ft.Row(
            controls=[
                ft.Container(
                    width=14,
                    alignment=ft.Alignment.CENTER,
                    content=ft.Icon(
                        ft.Icons.CIRCLE, size=8,
                        color=c.link if is_current else c.border,
                    ),
                ),
                ft.Text(
                    b.name, size=12, color=name_color, font_family=FONT_MAIN,
                    weight=ft.FontWeight.W_600 if is_current else None,
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                ),
                *badges,
                ft.Container(expand=True),
                *(
                    [
                        ft.Container(
                            bgcolor=ft.Colors.with_opacity(0.12, c.link),
                            border_radius=Radius.SM,
                            padding=ft.Padding.symmetric(horizontal=4, vertical=0),
                            content=ft.Text("当前", size=9, color=c.link,
                                            font_family=FONT_MAIN),
                        )
                    ]
                    if is_current
                    else []
                ),
                *actions,
            ],
            spacing=Spacing.SM,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
