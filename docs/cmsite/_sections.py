"""内容区块：首屏、特性、架构、关于。

每个区块都是**接收参数与回调的纯函数**（不是 `ft.component`）：
状态（当前导航项、主题、视口宽度）统一由外壳持有，区块只把意图上抛。
这样重渲染范围可预测，也方便在夹具里单独渲染一个区块做断言。
"""

from __future__ import annotations

import math
from collections.abc import Callable

import flet as ft

from cmsite import _content as K
from cmsite._theme import (
    FONT_MONO,
    Layout,
    Radius,
    Spacing,
    Type,
    current_colors,
    hero_gradient,
    only_border,
    tint,
)
from cmsite._ui import (
    badge,
    card,
    divider,
    feature_card,
    ghost_button,
    grid,
    icon,
    inset_panel,
    primary_button,
    section,
    section_header,
    stat,
    tag,
    text,
)
from cmsite._widgets import CodePanel

# ---------------------------------------------------------------------------
# 01 首屏
# ---------------------------------------------------------------------------


def hero_section(width: int, jump: Callable[[str], None], clipboard_ref: ft.Ref | None = None) -> ft.Control:
    """首屏：定位 + 两个入口 + 四个实测指标 + 一个「编辑中的文档」示意卡。

    首屏不罗列功能——那是下一节的事。这里只回答两件事：这是什么，
    以及它看起来是什么样。
    """
    c = current_colors()
    narrow = width < 900
    lead = ft.Column(
        spacing=Spacing.XXL,
        tight=True,
        controls=[
            ft.Row(
                spacing=8,
                wrap=True,
                controls=[
                    badge(f"v{K.RUNTIME['version']}", c.link, filled=True),
                    badge(K.APP_LICENSE, c.muted),
                    badge("Python 3.12+", c.muted),
                ],
            ),
            ft.Column(
                spacing=14,
                tight=True,
                controls=[
                    ft.Text(
                        K.APP_SUBTITLE,
                        size=Type.HERO if not narrow else 34,
                        color=c.text,
                        weight=ft.FontWeight.W_700,
                        # 行高走 TextStyle.height（倍率），不是控件的 height（像素）
                        style=ft.TextStyle(height=1.16),
                    ),
                    ft.Container(
                        width=min(width, 560),
                        content=text(K.HERO_LEAD, size=Type.LEAD, color=c.muted),
                    ),
                ],
            ),
            ft.Row(
                spacing=10,
                wrap=True,
                controls=[
                    primary_button("浏览特性", lambda: jump("features"), "BOLT"),
                    ghost_button("查看文档", lambda: jump("docs"), "MENU_BOOK"),
                ],
            ),
            ft.Container(
                content=text(K.HERO_NOTE, size=Type.TINY, color=c.muted, mono=True),
            ),
        ],
    )

    body: list[ft.Control] = []
    if narrow:
        body = [lead, _editor_mock(width, c)]
    else:
        body = [
            ft.Row(
                spacing=36,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Container(expand=True, content=lead),
                    ft.Container(width=min(400, int(width * 0.38)), content=_editor_mock(360, c)),
                ],
            )
        ]

    return ft.Container(
        key=ft.ScrollKey("hero"),
        width=width,
        margin=ft.Margin.only(top=Spacing.XXL),
        padding=ft.Padding.only(
            left=44 if not narrow else 24,
            right=44 if not narrow else 24,
            top=Layout.HERO_PAD,
            bottom=Layout.HERO_PAD,
        ),
        gradient=hero_gradient(c),
        border_radius=Radius.XXL,
        border=ft.Border.all(1, tint(c.link, 0.12)),
        content=ft.Column(
            spacing=36,
            tight=True,
            controls=[
                *body,
                ft.Row(
                    spacing=40,
                    run_spacing=20,
                    wrap=True,
                    controls=[stat(m) for m in K.METRICS],
                ),
            ],
        ),
    )


def _editor_mock(width: int, c) -> ft.Control:
    """首屏右侧的「编辑中的文档」示意：纯静态，但用真实排版 token。

    它不是截图（截图会随主题、字体版本漂移），而是用同一套标题色阶与字号
    搭出来的示意——因此切暗色主题时它会跟着变，永远与产品观感一致。
    """
    heading_tone = c.heading_colors.get(1, c.link)
    return ft.Container(
        bgcolor=c.surface,
        border_radius=Radius.LG,
        border=ft.Border.all(1, c.border),
        content=ft.Column(
            spacing=0,
            tight=True,
            controls=[
                # 标签行
                ft.Container(
                    height=34,
                    padding=ft.Padding.symmetric(horizontal=10),
                    bgcolor=tint(c.text, 0.03),
                    border=only_border(bottom=ft.BorderSide(1, c.border)),
                    content=ft.Row(
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            _dot("#FF6058"),
                            _dot("#FFBD2E"),
                            _dot("#28C840"),
                            ft.Container(width=8),
                            tag("demo.md"),
                            ft.Container(expand=True),
                            text("●", size=9, color=c.link),
                        ],
                    ),
                ),
                ft.Container(
                    padding=ft.Padding.all(18),
                    content=ft.Column(
                        spacing=12,
                        tight=True,
                        controls=[
                            ft.Row(
                                spacing=0,
                                tight=True,
                                controls=[
                                    ft.Text(
                                        "写作，本该如此",
                                        size=21,
                                        color=heading_tone,
                                        weight=ft.FontWeight.W_700,
                                    ),
                                    ft.Container(
                                        width=2,
                                        height=22,
                                        bgcolor=c.link,
                                        margin=ft.Margin.only(top=1),
                                    ),
                                ],
                            ),
                            text("光标停在哪里，就编辑哪里；语法标记只在该出现时出现。",
                                 size=12.5, color=c.muted),
                            ft.Text(
                                spans=[
                                    ft.TextSpan("所见", ft.TextStyle(size=12.5, color=c.text)),
                                    ft.TextSpan(
                                        "即所得",
                                        ft.TextStyle(
                                            size=12.5, color=c.text, weight=ft.FontWeight.W_700
                                        ),
                                    ),
                                    ft.TextSpan(
                                        "，连行内代码 ",
                                        ft.TextStyle(size=12.5, color=c.text),
                                    ),
                                    ft.TextSpan(
                                        " reparse() ",
                                        ft.TextStyle(
                                            size=11.5,
                                            color=c.code_fg,
                                            bgcolor=c.code_bg,
                                            font_family=FONT_MONO,
                                        ),
                                    ),
                                    ft.TextSpan(
                                        " 也是渲染后的样子。",
                                        ft.TextStyle(size=12.5, color=c.text),
                                    ),
                                ]
                            ),
                            ft.Column(
                                spacing=6,
                                tight=True,
                                controls=[
                                    _mock_bullet("语法噪声为零", c.heading_colors.get(1, c.link), c),
                                    _mock_bullet("中文输入不打断", c.heading_colors.get(3, c.link), c),
                                    _mock_bullet("撤销按行快照", c.heading_colors.get(5, c.link), c),
                                ],
                            ),
                            inset_panel(
                                text("reparse_line_atomic(doc, i, raw)", size=11, color=c.code_block_fg, mono=True),
                            ),
                        ],
                    ),
                ),
            ],
        ),
    )


def _dot(color: str) -> ft.Control:
    return ft.Container(width=8, height=8, border_radius=4, bgcolor=color)


def _mock_bullet(label: str, tone: str, c) -> ft.Control:
    return ft.Row(
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Container(width=4, height=4, border_radius=2, bgcolor=tone),
            text(label, size=12.5, color=c.muted),
        ],
    )


# ---------------------------------------------------------------------------
# 02 特性
# ---------------------------------------------------------------------------


def features_section(width: int) -> ft.Control:
    """特性网格：九张卡片，三列（窄屏两列 / 一列）。"""
    c = current_colors()
    return section(
        "features",
        width,
        section_header(
            "01 / 特性",
            "为「写」打磨的每一个细节",
            "下面每一条都对应仓库里可运行、有测试的实现——没有展望，也没有「即将支持」。",
            width=width,
        ),
        grid(
            width,
            3,
            [feature_card(f, c.heading_colors.get(i % 6 + 1, c.link)) for i, f in enumerate(K.FEATURES)],
        ),
        _blocks_and_inline(width, c),
    )


def _blocks_and_inline(width: int, c) -> ft.Control:
    """块级类型 + 行内格式：宽屏一宽一窄两栏，窄屏上下堆叠。"""
    left_w = int(width * 0.56)
    right_w = max(240, width - left_w - Layout.GRID)
    blocks = card(
        ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                icon("ACCOUNT_TREE", size=17, color=c.heading_colors.get(4, c.link)),
                text("块级类型", size=Type.H4, weight=ft.FontWeight.W_600),
            ],
        ),
        ft.Column(
            spacing=0,
            tight=True,
            controls=[
                ft.Container(
                    padding=ft.Padding.symmetric(vertical=7),
                    content=ft.Row(
                        spacing=10,
                        controls=[
                            ft.Container(width=56, content=text("语法", size=Type.TINY, color=c.muted)),
                            ft.Container(width=84, content=text("类型", size=Type.TINY, color=c.muted)),
                            ft.Container(expand=True, content=text("说明", size=Type.TINY, color=c.muted)),
                        ],
                    ),
                ),
                divider(c),
                *[
                    ft.Container(
                        padding=ft.Padding.symmetric(vertical=7),
                        border=only_border(bottom=ft.BorderSide(1, tint(c.border, 0.6))),
                        content=ft.Row(
                            spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Container(width=56, content=tag(b.syntax)),
                                ft.Container(width=84, content=text(b.name, size=Type.TINY)),
                                ft.Container(
                                    expand=True,
                                    content=text(b.desc, size=Type.TINY, color=c.muted),
                                ),
                            ],
                        ),
                    )
                    for b in K.BLOCKS
                ],
            ],
        ),
    )

    inline = card(
        ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                icon("TEXT_FIELDS", size=17, color=c.heading_colors.get(5, c.link)),
                text("行内格式", size=Type.H4, weight=ft.FontWeight.W_600),
            ],
        ),
        text("十种语法可自由组合，例如 ***加粗斜体***。选中文字按快捷键即包裹并保持选区，"
             "再按一次取消。", size=Type.SMALL, color=c.muted),
        ft.Column(
            spacing=6,
            tight=True,
            controls=[
                ft.Row(
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Container(width=64, content=text(name, size=Type.TINY)),
                        tag(syntax),
                        ft.Container(expand=True),
                        text(hint, size=Type.TINY, color=c.muted),
                    ],
                )
                for name, syntax, hint in (
                    (f.name, f.syntax, f.desc) for f in K.INLINE_FORMATS
                )
            ],
        ),
    )

    if width < 820:
        return ft.Column(spacing=Layout.GRID, tight=True, controls=[blocks, inline])

    return ft.Row(
        spacing=Layout.GRID,
        run_spacing=Layout.GRID,
        wrap=True,
        controls=[
            ft.Container(width=left_w, content=blocks),
            ft.Container(width=right_w, content=inline),
        ],
    )


# ---------------------------------------------------------------------------
# 03 架构
# ---------------------------------------------------------------------------


def architecture_section(width: int) -> ft.Control:
    """分层依赖 + 关键决策 + 工程规模。"""
    c = current_colors()
    return section(
        "architecture",
        width,
        section_header(
            "04 / 架构",
            "九层单向依赖，数据只朝一个方向流",
            "上层可以调用下层，下层不知道上层的存在。这条约束让每个模块都能被单独测试，"
            "也让「界面只由状态驱动」在结构上成为必然。",
            accent=c.heading_colors.get(6, c.link),
            width=width,
        ),
        ft.Column(
            spacing=6,
            tight=True,
            controls=[
                _layer_row(i, layer, width, c, last=i == len(K.ARCH_LAYERS) - 1)
                for i, layer in enumerate(K.ARCH_LAYERS)
            ],
        ),
        _decisions(width, c),
        _scale_chart(width, c),
        _rules(width, c),
    )


def _layer_row(index: int, layer: K.Layer, width: int, c, last: bool) -> ft.Control:
    """分层的一行：序号 + 层名 + 模块标签（上排），职责说明（下排）。

    用色条而不是箭头连线：箭头在窄屏会被挤成锯齿，色条不会。

    ⚠️ 这里刻意**不用**「Row + expand 的职责文本」：`ft.Row(wrap=True)` 在
    Flutter 侧是 `Wrap`，而 `Wrap` 的子项不能是 flex（`expand=True`）——
    会抛 ParentDataWidget 断言，整块退化成 release 版的灰色 ErrorWidget。
    """
    tone = c.heading_colors.get(min(index + 1, 6), c.link)
    return ft.Container(
        width=width,
        padding=ft.Padding.symmetric(vertical=10, horizontal=12),
        bgcolor=c.surface,
        border_radius=Radius.MD,
        border=only_border(left=ft.BorderSide(3, tone)),
        content=ft.Column(
            spacing=5,
            tight=True,
            controls=[
                ft.Row(
                    spacing=10,
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Container(
                            width=18,
                            content=text(f"{index + 1}", size=Type.TINY, color=tone, mono=True,
                                         weight=ft.FontWeight.W_700),
                        ),
                        ft.Container(
                            width=150,
                            content=text(layer.name, size=Type.SMALL, mono=True,
                                         weight=ft.FontWeight.W_600),
                        ),
                        *[tag(m) for m in layer.modules],
                    ],
                ),
                text(layer.duty, size=Type.TINY, color=c.muted),
            ],
        ),
    )


def _decisions(width: int, c) -> ft.Control:
    return ft.Column(
        spacing=Layout.GRID,
        tight=True,
        controls=[
            text("关键设计决策", size=Type.H3, weight=ft.FontWeight.W_600),
            grid(
                width,
                2,
                [
                    card(
                        text(d.title, size=Type.H4, weight=ft.FontWeight.W_600),
                        text(d.body, size=Type.SMALL, color=c.muted),
                        pad=16,
                    )
                    for d in K.ARCH_DECISIONS
                ],
            ),
        ],
    )


def _scale_chart(width: int, c) -> ft.Control:
    """分层代码规模：对数刻度的横条。

    线性刻度下 tests/（26,878 行）会把其余八层压成一条线；对数让数量级差异
    可见，同时保证每层都读得出来。
    """
    peak = math.log10(max(lines for _, _, lines in K.LAYER_STATS) + 1)
    rows: list[ft.Control] = []
    for name, files, lines in K.LAYER_STATS:
        ratio = math.log10(lines + 1) / peak
        rows.append(
            ft.Row(
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Container(width=74, content=text(name, size=Type.TINY, mono=True)),
                    ft.Container(
                        expand=True,
                        height=8,
                        bgcolor=tint(c.border, 0.45),
                        border_radius=4,
                        content=ft.Container(
                            width=f"{max(ratio * 100, 2):.1f}%",
                            height=8,
                            border_radius=4,
                            bgcolor=c.link,
                        ),
                    ),
                    ft.Container(
                        width=112,
                        content=text(f"{lines:,} 行 · {files}", size=Type.TINY, color=c.muted, mono=True),
                    ),
                ],
            )
        )
    return ft.Column(
        spacing=10,
        tight=True,
        controls=[
            text("工程规模", size=Type.H3, weight=ft.FontWeight.W_600),
            text("按层统计的 Python 行数与文件数（对数刻度，不含构建产物）。", size=Type.SMALL, color=c.muted),
            *rows,
        ],
    )


def _rules(width: int, c) -> ft.Control:
    return card(
        ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                icon("RULE", size=17, color=c.heading_colors.get(2, c.link)),
                text("架构红线", size=Type.H4, weight=ft.FontWeight.W_600),
            ],
        ),
        text("下面几条写在仓库的 AGENT.md 里，是这个项目不腐化的真正原因。", size=Type.SMALL, color=c.muted),
        *[
            ft.Row(
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    ft.Container(
                        margin=ft.Margin.only(top=5),
                        width=5,
                        height=5,
                        border_radius=3,
                        bgcolor=c.heading_colors.get(2, c.link),
                    ),
                    ft.Container(expand=True, content=text(rule, size=Type.SMALL)),
                ],
            )
            for rule in K.ARCH_RULES
        ],
    )


# ---------------------------------------------------------------------------
# 04 关于
# ---------------------------------------------------------------------------


def about_section(width: int, clipboard_ref: ft.Ref | None = None) -> ft.Control:
    """关于：缘起、事实卡、安装命令。"""
    c = current_colors()
    narrow = width < 820
    prose = ft.Column(
        spacing=14,
        tight=True,
        controls=[text(p, size=Type.BODY, color=c.text) for p in K.ABOUT_PARAGRAPHS],
    )
    facts = card(
        *[
            ft.Row(
                spacing=12,
                controls=[
                    ft.Container(width=64, content=text(k, size=Type.TINY, color=c.muted)),
                    ft.Container(expand=True, content=text(v, size=Type.SMALL)),
                ],
            )
            for k, v in K.ABOUT_FACTS
        ],
        text(K.FOOTER_NOTE, size=Type.TINY, color=c.muted),
        bgcolor=tint(c.link, 0.03),
    )

    columns: list[ft.Control] = (
        [prose, facts]
        if narrow
        else [
            ft.Row(
                spacing=Layout.GRID,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    ft.Container(expand=True, content=prose),
                    ft.Container(width=320, content=facts),
                ],
            )
        ]
    )

    return section(
        "about",
        width,
        section_header(
            "05 / 关于",
            "一个「先把基础操作做对」的编辑器",
            "",
            width=width,
        ),
        *columns,
        ft.Column(
            spacing=8,
            tight=True,
            controls=[
                text("技术栈", size=Type.H4, weight=ft.FontWeight.W_600),
                _tech_stack(c, narrow),
            ],
        ),
        ft.Column(
            spacing=8,
            tight=True,
            controls=[
                text("三步跑起来", size=Type.H4, weight=ft.FontWeight.W_600),
                CodePanel(K.INSTALL_CMD, clipboard_ref=clipboard_ref, label="terminal"),
            ],
        ),
    )


def _tech_stack(c, narrow: bool) -> ft.Control:
    """技术栈：名称 + 版本 + 职责。

    不用表格：只有 8 行、每行一句话，表格的边框与表头比内容更响。
    宽屏三列（名称 / 版本 / 职责），窄屏改成两行一组（名称+版本 / 职责）——
    430px 下三列会把职责挤成 4 行，读起来像碎纸条。
    """
    rows: list[ft.Control] = []
    for t in K.TECH_STACK:
        name = text(t.name, size=Type.SMALL, weight=ft.FontWeight.W_600, mono=True)
        version = text(t.version, size=Type.TINY, color=c.muted, mono=True)
        duty = text(t.duty, size=Type.TINY, color=c.muted if narrow else None)
        if narrow:
            rows.append(
                ft.Column(
                    spacing=2,
                    tight=True,
                    controls=[
                        ft.Row(
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Container(expand=True, content=name),
                                version,
                            ],
                        ),
                        duty,
                    ],
                )
            )
        else:
            # 注意别开 wrap：Flutter 的 Wrap 不接受 flex 子项，`expand=True` 会直接
            # 抛 ParentDataWidget 断言（release 版表现为一块灰色 ErrorWidget）。
            rows.append(
                ft.Row(
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        # 124 是「flet-datatable2」这类最长包名的实测宽度；96 会把
                        # 它挤成两行，名字列一折行整张表就散架了。
                        ft.Container(width=124, content=name),
                        ft.Container(width=76, content=version),
                        ft.Container(expand=True, content=duty),
                    ],
                )
            )
    return card(*rows, bgcolor=tint(c.link, 0.02))
