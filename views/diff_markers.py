"""对比标签 UI 组件（从 app/_render.py _build_diff_area 抽取）。

将对比头部抽取为独立 @ft.memo + @ft.component，实现组件级 memo 隔离：
- DiffHeader：文件名 / 差异统计 / 主题 / 关闭回调不变时，App 重渲染
  （侧边栏切换 / 焦点视口切换 / 滚动同步）跳过头部控件树重建。
  统计值来自 App use_memo（diff 内容签名缓存），内容不变时数值稳定；
  文件名 / 主题 / 关闭回调均为低频变化 prop，头部在 diff 内容编辑期间
  （高频 App 重渲染由 Document @ft.observable 驱动）几乎不重建。

设计要点：
- @ft.memo 启用浅比较 memoization（@ft.component 默认不 memo，每次重渲染都
  重跑函数体）。浅比较要求所有 prop 身份稳定：字符串 / int / 枚举用 == 命中，
  on_close 由 App use_memo 稳定化（close_current_tab，读 close_tab_ref + 
  active_index_ref），故 memo 在 diff 内容 / 主题 / 标签不变时成立。
- 纯展示组件，无 hooks、无内部状态：所有数据经 props 传入。
- _stat_badge 为模块级纯函数构造器，避免组件内重复构造相同徽章控件树。

依赖项：
- flet
- styles（FONT_MAIN / FONT_MONO / Radius / Spacing / get_colors / only_border）
"""

from collections.abc import Callable

import flet as ft

from styles import FONT_MAIN, FONT_MONO, Radius, Spacing, get_colors, only_border


@ft.memo
@ft.component
def DiffHeader(
    left_name: str,
    right_name: str,
    added: int,
    removed: int,
    modified: int,
    theme_mode: ft.ThemeMode,
    on_close: Callable[[], None],
):
    """对比标签头部：文件名 + 差异统计徽章 + 关闭按钮。

    纯展示组件。props 稳定时（diff 内容不变 / 主题不变 / 标签不切换）跳过
    重渲染，避免 App 高频重渲染重建头部控件树。统计值来自 App use_memo
    缓存的 diff_result（左右文档行内容签名）。
    """
    c = get_colors(theme_mode)
    is_dark = theme_mode == ft.ThemeMode.DARK
    # added / removed 字色：亮暗自适应（GitHub 风格）
    _added_char = "#7ee787" if is_dark else "#1a7f37"
    _removed_char = "#f47067" if is_dark else "#cf222e"

    return ft.Container(
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
                _stat_badge(f"+{added}", _added_char, c.diff_add_bg),
                _stat_badge(f"-{removed}", _removed_char, c.diff_del_bg),
                _stat_badge(
                    f"~{modified}", c.muted,
                    ft.Colors.with_opacity(0.08, c.text),
                ),
                ft.Container(expand=True),
                ft.IconButton(
                    icon=ft.Icons.CLOSE, tooltip="关闭对比标签",
                    on_click=lambda e: on_close(),
                    icon_size=18, style=ft.ButtonStyle(color=c.muted),
                ),
            ],
            spacing=Spacing.SM,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


def _stat_badge(text: str, color: str, bgcolor) -> ft.Control:
    """差异统计徽章：等宽字体 + 圆角底色（模块级纯构造器，复用）。"""
    return ft.Container(
        bgcolor=bgcolor, border_radius=Radius.SM,
        padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=2),
        content=ft.Text(
            text, size=11, color=color,
            font_family=FONT_MONO, weight=ft.FontWeight.W_600,
        ),
    )
