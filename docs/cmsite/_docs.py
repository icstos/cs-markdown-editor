"""文档区块：左侧目录 + 右侧正文。

站点的「文档」不是把 README 整篇倒上来——那既读不完也没人查。这里按**任务**
组织六页：装、用、配、键、结构、FAQ。每页的正文是 `_content.DOCS` 里的
纯数据结构，视图只负责把 6 种块（段落 / 要点 / 步骤 / 键值 / 代码 / 提示）
渲染成控件。

快捷键页是唯一的例外：它的数据在运行时从 `services.shortcuts.ACTION_REGISTRY`
读取（见 `_shortcuts.py`），保证站点上的键位永远等于程序的默认键位。
"""

from __future__ import annotations

import ui

from cmsite import _content as K
from cmsite._shortcuts import ShortcutGroup, load_groups
from cmsite._theme import Layout, Radius, Type, current_colors, only_border, tint
from cmsite._ui import (
    card,
    divider,
    icon,
    keycap,
    pill_tabs,
    rich,
    section,
    section_header,
    tag,
    text,
)
from cmsite._widgets import CodePanel


@ui.component
def DocsSection(width: int, clipboard_ref: ui.Ref | None = None) -> ui.Control:
    """文档区块。"""
    active, set_active = ui.use_state(K.DOCS[0].anchor)
    c = current_colors()
    narrow = width < 860
    entry = next((d for d in K.DOCS if d.anchor == active), K.DOCS[0])

    body = ui.Column(
        spacing=14,
        tight=True,
        controls=[
            text(entry.title, size=Type.H3, weight=ui.FontWeight.W_700),
            text(entry.summary, size=Type.SMALL, color=c.muted),
            divider(c),
            *_render_blocks(entry, clipboard_ref, c),
        ],
    )

    if narrow:
        content: ui.Control = ui.Column(
            spacing=14,
            tight=True,
            controls=[
                pill_tabs([(d.anchor, d.title) for d in K.DOCS], active, set_active),
                body,
            ],
        )
    else:
        content = ui.Row(
            spacing=Layout.GRID,
            vertical_alignment=ui.CrossAxisAlignment.START,
            controls=[
                ui.Container(width=196, content=_nav(active, set_active, c)),
                ui.Container(
                    expand=True,
                    content=card(
                        body,
                        pad=24,
                        bgcolor=c.surface,
                    ),
                ),
            ],
        )

    return section(
        "docs",
        width,
        section_header(
            "03 / 文档",
            "装、用、配、键、结构、FAQ",
            "键位那页读的是程序里的注册表，不会出现文档写 Ctrl+D、程序绑 Ctrl+U。",
            accent=c.heading_colors.get(4, c.link),
            width=width,
        ),
        content,
    )


def _nav(active: str, on_select, c) -> ui.Control:
    """左侧目录：选中项用左竖条 + 浅底标记。"""
    rows: list[ui.Control] = []
    for item in K.DOCS:
        selected = item.anchor == active
        rows.append(
            ui.Container(
                padding=ui.Padding.symmetric(vertical=8, horizontal=10),
                border_radius=Radius.SM,
                bgcolor=tint(c.link, 0.07) if selected else None,
                ink=True,
                on_click=lambda _e=None, a=item.anchor: on_select(a),
                content=ui.Row(
                    spacing=8,
                    controls=[
                        ui.Container(
                            width=2,
                            height=15,
                            border_radius=1,
                            bgcolor=c.link if selected else tint(c.border, 1.0),
                        ),
                        ui.Container(
                            expand=True,
                            content=text(
                                item.title,
                                size=Type.SMALL,
                                color=c.link if selected else c.text,
                                weight=ui.FontWeight.W_600 if selected else ui.FontWeight.W_400,
                            ),
                        ),
                    ],
                ),
            )
        )
    return ui.Container(
        padding=ui.Padding.all(8),
        bgcolor=tint(c.text, 0.02),
        border_radius=Radius.LG,
        border=only_border(left=ui.BorderSide(0, c.border)),
        content=ui.Column(spacing=2, tight=True, controls=rows),
    )


# ---------------------------------------------------------------------------
# 块渲染
# ---------------------------------------------------------------------------


def _render_blocks(entry: K.DocEntry, clipboard_ref: ui.Ref | None, c) -> list[ui.Control]:
    """把一个 DocEntry 的块列表渲染成控件。"""
    if entry.anchor == "docs-keys":
        return _shortcut_views(clipboard_ref, c)
    out: list[ui.Control] = []
    for block in entry.blocks:
        out.append(_render_block(block, clipboard_ref, c))
        out.append(ui.Container(height=2))
    return out


def _render_block(block: K.DocBlock, clipboard_ref: ui.Ref | None, c) -> ui.Control:
    if block.kind == "p":
        return rich(block.text, size=Type.SMALL, color=c.muted)
    if block.kind == "ul":
        return ui.Column(
            spacing=6,
            tight=True,
            controls=[_point(str(item), c) for item in block.items],
        )
    if block.kind == "steps":
        return ui.Column(
            spacing=10,
            tight=True,
            controls=[
                ui.Row(
                    spacing=10,
                    vertical_alignment=ui.CrossAxisAlignment.START,
                    controls=[
                        ui.Container(
                            width=20,
                            height=20,
                            border_radius=10,
                            bgcolor=tint(c.link, 0.10),
                            alignment=ui.Alignment.CENTER,
                            content=text(f"{i + 1}", size=Type.TINY, color=c.link, mono=True),
                        ),
                        ui.Container(
                            expand=True,
                            content=ui.Column(
                                spacing=2,
                                tight=True,
                                controls=[
                                    text(title, size=Type.SMALL, weight=ui.FontWeight.W_600),
                                    rich(desc, size=Type.TINY, color=c.muted),
                                ],
                            ),
                        ),
                    ],
                )
                for i, (title, desc) in enumerate(block.items)
            ],
        )
    if block.kind == "kv":
        return ui.Column(
            spacing=0,
            tight=True,
            controls=[
                ui.Container(
                    padding=ui.Padding.symmetric(vertical=7),
                    border=only_border(bottom=ui.BorderSide(1, tint(c.border, 0.7))),
                    content=ui.Row(
                        spacing=14,
                        vertical_alignment=ui.CrossAxisAlignment.START,
                        controls=[
                            ui.Container(
                                width=210,
                                content=text(str(key), size=Type.TINY, color=c.text, mono=True),
                            ),
                            ui.Container(
                                expand=True,
                                content=rich(str(val), size=Type.TINY, color=c.muted),
                            ),
                        ],
                    ),
                )
                for key, val in block.items
            ],
        )
    if block.kind == "code":
        return CodePanel(block.text, clipboard_ref=clipboard_ref, label="terminal")
    if block.kind == "note":
        return ui.Container(
            padding=ui.Padding.all(11),
            bgcolor=tint(c.heading_colors.get(3, c.link), 0.07),
            border_radius=Radius.MD,
            border=only_border(left=ui.BorderSide(2, c.heading_colors.get(3, c.link))),
            content=ui.Row(
                spacing=8,
                vertical_alignment=ui.CrossAxisAlignment.START,
                controls=[
                    icon("INFO_OUTLINE", size=15, color=c.heading_colors.get(3, c.link)),
                    ui.Container(expand=True, content=rich(block.text, size=Type.TINY, color=c.muted)),
                ],
            ),
        )
    # 未知块类型：退化成段落，而不是抛异常中断整页
    return text(block.text, size=Type.SMALL, color=c.muted)


def _point(content: str, c) -> ui.Control:
    return ui.Row(
        spacing=8,
        vertical_alignment=ui.CrossAxisAlignment.START,
        controls=[
            ui.Container(width=4, height=4, border_radius=2, bgcolor=c.link,
                         margin=ui.Margin.only(top=6)),
            ui.Container(expand=True, content=rich(content, size=Type.SMALL, color=c.muted)),
        ],
    )


def _shortcut_views(clipboard_ref: ui.Ref | None, c) -> list[ui.Control]:
    """快捷键页：按注册表的分类分组，每组一张卡。"""
    groups: tuple[ShortcutGroup, ...] = load_groups()
    total = sum(len(g.rows) for g in groups)
    out: list[ui.Control] = [
        ui.Row(
            spacing=8,
            wrap=True,
            vertical_alignment=ui.CrossAxisAlignment.CENTER,
            controls=[
                tag(f"{total} 个动作"),
                text("按用途分组；默认键位来自程序注册表，可在「设置 → 快捷键」修改。",
                     size=Type.TINY, color=c.muted),
            ], run_spacing=10,
        )
    ]
    for group in groups:
        out.append(
            ui.Column(
                spacing=6,
                tight=True,
                controls=[
                    ui.Row(
                        spacing=8,
                        vertical_alignment=ui.CrossAxisAlignment.CENTER,
                        controls=[
                            ui.Container(width=3, height=12, bgcolor=c.link, border_radius=2),
                            text(group.name, size=Type.SMALL, weight=ui.FontWeight.W_600),
                            tag(str(len(group.rows))),
                        ],
                    ),
                    ui.Column(
                        spacing=0,
                        tight=True,
                        controls=[
                            ui.Container(
                                padding=ui.Padding.symmetric(vertical=6),
                                border=only_border(bottom=ui.BorderSide(1, tint(c.border, 0.55))),
                                tooltip=row.desc,
                                content=ui.Row(
                                    spacing=12,
                                    controls=[
                                        ui.Container(
                                            width=132,
                                            content=text(row.label, size=Type.TINY),
                                        ),
                                        ui.Container(width=140, content=keycap(row.combo, c)),
                                        ui.Container(
                                            expand=True,
                                            content=text(row.desc, size=Type.TINY, color=c.muted,
                                                          max_lines=1),
                                        ),
                                    ],
                                ),
                            )
                            for row in group.rows
                        ],
                    ),
                ],
            )
        )
    return out
