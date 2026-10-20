"""带状态的小部件：只有「必须记住点什么」的组件才放这里。

目前只有一个——复制按钮要显示 1.2 秒的「已复制」反馈。其余全部留在 `_ui.py`
的纯函数里：状态越少，重渲染的范围越可预测。
"""

from __future__ import annotations

import asyncio

import flet as ft

from cmsite._theme import FONT_MONO, Radius, Type, current_colors, tint
from cmsite._ui import icon, on_event, text

__all__ = ["CodePanel", "copy_to_clipboard"]


async def copy_to_clipboard(clipboard_ref: ft.Ref | None, value: str) -> bool:
    """写系统剪贴板；未挂载 / 平台不支持时静默失败（介绍页不该弹错误框）。"""
    clipboard = clipboard_ref.current if clipboard_ref is not None else None
    if clipboard is None:
        return False
    try:
        await clipboard.set(value)
        return True
    except Exception:
        return False


@ft.component
def CodePanel(
    source: str,
    clipboard_ref: ft.Ref | None = None,
    label: str = "code",
    max_lines: int | None = None,
    pad: int = 14,
    height: int | None = None,
) -> ft.Control:
    """代码面板：标题条 + 复制按钮 + 等宽代码。

    复制反馈是「图标变勾 + 文案变已复制」，1.2 秒后复原。定时交 `page.run_task`
    调度——用 `time.sleep` 会卡住 UI 线程。

    传 `height` 时内部改为定高可滚动区（长源码不撑破版式）；不传则按内容自适应。
    """
    copied, set_copied = ft.use_state(False)
    c = current_colors()
    ok_tone = c.heading_colors.get(3, c.link)
    tone = ok_tone if copied else c.muted

    async def _copy() -> None:
        if not await copy_to_clipboard(clipboard_ref, source):
            return
        set_copied(True)
        await asyncio.sleep(1.2)
        set_copied(False)

    def on_click() -> None:
        page = ft.context.page
        if page is not None:
            page.run_task(_copy)

    return ft.Container(
        bgcolor=c.code_block_bg,
        border_radius=Radius.MD,
        padding=ft.Padding.all(pad),
        content=ft.Column(
            spacing=8,
            tight=True,
            controls=[
                ft.Row(
                    spacing=8,
                    tight=True,
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        text(label, size=Type.TINY, color=c.muted, mono=True),
                        ft.Container(
                            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                            border_radius=Radius.SM,
                            bgcolor=tint(ok_tone, 0.12) if copied else tint(c.text, 0.05),
                            ink=True,
                            on_click=on_event(on_click),
                            tooltip="复制到剪贴板",
                            content=ft.Row(
                                spacing=5,
                                tight=True,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    icon("CHECK" if copied else "CONTENT_COPY", size=13, color=tone),
                                    text(
                                        "已复制" if copied else "复制",
                                        size=Type.TINY,
                                        color=tone,
                                        weight=ft.FontWeight.W_500,
                                    ),
                                ],
                            ),
                        ),
                    ],
                ),
                _code_view(source, c, max_lines, height),
            ],
        ),
    )


def _code_view(source: str, c, max_lines: int | None, height: int | None) -> ft.Control:
    """代码正文：横向可滚，纵向按需限高。

    长命令（比如一行 pip install 八个包）不折行才是代码该有的样子；折行会让
    缩进和续行混在一起。横向滚动交给包住 Text 的 `Row`，纵向限高交给
    `ListView`——两者都是 Flet 原生滚动，不需要自己算宽度。
    """
    body = ft.Row(
        spacing=0,
        tight=True,
        scroll=ft.ScrollMode.AUTO,
        controls=[
            ft.Text(
                source,
                size=Type.CODE,
                color=c.code_block_fg,
                font_family=FONT_MONO,
                selectable=True,
                max_lines=max_lines,
                no_wrap=True,
            )
        ],
    )
    if height is None:
        return body
    return ft.Container(height=height, content=ft.ListView(spacing=0, padding=0, controls=[body]))
