"""顶栏行高一致性（标签行 ↔ 大纲头部）。

诉求：「标签行高度与大纲顶部高度保持一致，使整体视觉效果更协调」——两列顶栏要连成
**同一条水平带**。真机实测（PrintWindow 整窗抓屏 + 逐列行程编码，dpr=1.5）改前：

    标签行   内容 66/1.5 = 44 + 底边线 1px  →  底边线落在第 67 物理行
    大纲头部 内容 48/1.5 = 32 + 底边线 1px  →  底边线落在第 49 物理行
    ⇒ 两条底边线相差 18 物理 px（12 逻辑 px），顶栏在编辑区与大纲列交界处出现台阶

成因：标签行高度由**最高子项**决定，而关闭 / 新建按钮原用 `ft.IconButton`——Material
的最小点击区给它 40px 固有高（`visual_density=COMPACT` 也只降到 32），把标签行顶到
40 + 上下 padding 2×2 = 44；压内边距完全无效。与 `views/code_block.py` 头部同款故障，
解法也同款：改用固定尺寸的 `Container(ink=True)`。

本文件锁定三条不变式，防止再退化：

1. **高度同源**：标签行（每个标签容器 + 「+」按钮）与大纲头部都显式声明
   `styles.TOPBAR_H`，不依赖任何隐式固有高；
2. **无 Material 固有尺寸控件**：标签行内不得出现 `IconButton` / `Dropdown`；
3. **底边线共线**：两个顶栏的内容高、上下 padding、底边线宽三者相同
   ⇒ 总高相同 ⇒ 底线共线。任一项漂移都会被这条挡住。
"""

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import flet as ft

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser import parse_markdown
from styles import TOPBAR_H, get_colors
from tests.harness import RenderHarness
from views.outline_panel import OutlinePanel
from views.tab_bar import TabBar

C = get_colors(ft.ThemeMode.LIGHT)
TABS = [
    {"file_path": r"C:\docs\a.md", "dirty": False},
    {"file_path": r"C:\docs\b.md", "dirty": True},
]
OUTLINE_MD = "# 一级标题\n\n正文\n\n## 二级标题\n\n更多正文\n"


def _noop(*_a, **_k) -> None:
    return None


def _is_container(node) -> bool:
    return isinstance(node, ft.Container)


@contextmanager
def _tab_bar(tabs: list[dict] | None = None) -> Iterator[RenderHarness]:
    @ft.component
    def _Probe():
        return TabBar(
            tabs=tabs if tabs is not None else TABS,
            active_index=0,
            theme_mode=ft.ThemeMode.LIGHT,
            on_select=_noop,
            on_close=_noop,
            on_new=_noop,
            on_context_action=_noop,
        )

    harness = RenderHarness()
    try:
        harness.render(_Probe)
        yield harness
    finally:
        harness.dispose()


@contextmanager
def _outline() -> Iterator[RenderHarness]:
    document = parse_markdown(OUTLINE_MD)

    @ft.component
    def _Probe():
        return OutlinePanel(
            document=document,
            theme_mode=ft.ThemeMode.LIGHT,
            open=True,
            on_jump_to_line=_noop,
        )

    harness = RenderHarness()
    try:
        harness.render(_Probe)
        yield harness
    finally:
        harness.dispose()


def _band_containers(h: RenderHarness) -> list[ft.Container]:
    """撑起整条顶栏的容器：显式定高 TOPBAR_H（每个标签 + 「+」按钮）。"""
    return [
        n
        for n in h.find(_is_container)
        if getattr(n, "height", None) == TOPBAR_H
    ]


def _tab_row_root(h: RenderHarness) -> ft.Container:
    """标签栏根容器：子树里唯一带边框的 Container。"""
    bordered = [n for n in h.find(_is_container) if n.border is not None]
    assert len(bordered) == 1, f"标签栏应只有一个带边框容器，实际 {len(bordered)}"
    return bordered[0]


def _outline_band(h: RenderHarness) -> ft.Container:
    """大纲头部的内容带：定高 TOPBAR_H 且底色为 toolbar_bg 的 Container。"""
    bands = [
        n
        for n in h.find(_is_container)
        if getattr(n, "height", None) == TOPBAR_H and n.bgcolor == C.toolbar_bg
    ]
    assert len(bands) == 1, f"大纲头部内容带应唯一，实际 {len(bands)}"
    return bands[0]


def _outline_frame(h: RenderHarness) -> ft.Container:
    """包住内容带、只负责底边线的那层（不带高度）。"""
    band = _outline_band(h)
    frames = [n for n in h.find(_is_container) if n.content is band]
    assert len(frames) == 1, f"大纲头部外框应唯一，实际 {len(frames)}"
    return frames[0]


def _bottom_line_width(node: ft.Container) -> float:
    bottom = getattr(node.border, "bottom", None)
    return getattr(bottom, "width", 0) or 0


def _pad_top(node: ft.Container) -> float:
    return getattr(node.padding, "top", 0) or 0


def _pad_bottom(node: ft.Container) -> float:
    return getattr(node.padding, "bottom", 0) or 0


# ---------------------------------------------------------------------------
# 标签行
# ---------------------------------------------------------------------------


def test_tab_row_height_is_locked_to_token():
    """每个标签容器与「+」按钮都显式定高 TOPBAR_H。

    行高由最高子项决定，不显式锁定就会被按钮的固有尺寸顶回去——这正是"标签行比
    大纲头部高 12px"的成因，跟内边距无关（真机：压内边距后行高纹丝不动）。
    """
    with _tab_bar() as h:
        band = _band_containers(h)
        # 2 个标签 + 1 个「+」按钮
        assert len(band) == len(TABS) + 1, f"撑起顶栏的容器数不符：{[n.height for n in band]}"
        over = [
            n.height
            for n in h.find(_is_container)
            if getattr(n, "height", None) is not None and n.height > TOPBAR_H
        ]
        assert not over, f"标签行内出现高过 TOPBAR_H({TOPBAR_H}) 的容器：{over}"


def test_tab_row_has_no_material_min_tap_target_controls():
    """标签行内不得出现 IconButton / Dropdown。

    真机实测高度：IconButton 40（`visual_density=COMPACT` 也只降到 32）、Dropdown 48。
    它们会把标签行顶破 TOPBAR_H，且**无法靠内边距压下去**，只能整体换掉。
    替换物是固定尺寸的 `Container(ink=True)`（项目紧凑按钮惯例）。
    """
    with _tab_bar() as h:
        offenders = [
            type(n).__name__
            for n in h.find(lambda n: isinstance(n, (ft.IconButton, ft.Dropdown)))
        ]
        assert not offenders, f"标签行出现 Material 固有高控件：{offenders}"


def test_tab_row_compact_buttons_are_square_and_within_cap():
    """墨迹按钮为正方形且不超 TOPBAR_H，否则会被定高的标签容器静默裁切。"""
    with _tab_bar() as h:
        inks = [
            n
            for n in h.find(_is_container)
            if getattr(n, "ink", False) and getattr(n, "height", None) is not None
        ]
        assert len(inks) == len(TABS) + 1, f"墨迹按钮数不符（{len(inks)}）"
        for node in inks:
            assert node.width == node.height, f"墨迹按钮非正方形：{node.width}x{node.height}"
            assert node.height <= TOPBAR_H, (
                f"墨迹按钮高 {node.height} > TOPBAR_H({TOPBAR_H})，会被标签容器裁切"
            )
            assert node.on_click is not None, "图标按钮没有单击回调"
            assert node.tooltip, "图标按钮缺少 tooltip（悬停无提示）"


# ---------------------------------------------------------------------------
# 大纲头部
# ---------------------------------------------------------------------------


def test_outline_band_height_is_locked_to_token():
    """大纲头部内容带显式定高 TOPBAR_H（不靠 padding 撑出的隐式高度）。"""
    with _outline() as h:
        assert _outline_band(h).height == TOPBAR_H


def test_outline_band_content_stays_centered():
    """头部内容在固定高度内居中（否则 11px 文本会贴顶，视觉失去对称）。"""
    with _outline() as h:
        band = _outline_band(h)
        assert band.alignment == ft.Alignment.CENTER
        row = band.content
        assert isinstance(row, ft.Row)
        assert row.vertical_alignment == ft.CrossAxisAlignment.CENTER


def test_outline_bottom_line_lives_outside_the_fixed_height_band():
    """底边线必须在定高内容带**之外**。

    `Container(height=H, border=bottom 1px)` 的总高**就是 H**——边线被算进 H 内，
    内容带只剩 H-1。而标签栏的边线是加在内容带之外的，两者会差 1px、底线不共线
    （真机实测：标签栏 33 vs 大纲 32）。这条守住"外挂边线"这一构造。
    """
    with _outline() as h:
        frame = _outline_frame(h)
        assert frame.border is not None, "大纲头部外框没有边框"
        assert _bottom_line_width(frame) == 1
        assert frame.height is None, "外框不应定高（定高会把边线算进去）"
        assert frame.bgcolor is None, "外框只负责边线，底色应在内容带上"


# ---------------------------------------------------------------------------
# 两列底线共线（本文件的核心不变式）
# ---------------------------------------------------------------------------


def _total_height(band_h: float, frame: ft.Container) -> float:
    """顶栏总高 = 内容带高 + 外框上下 padding + 底边线宽。"""
    return band_h + _pad_top(frame) + _pad_bottom(frame) + _bottom_line_width(frame)


def test_both_top_bars_add_no_vertical_padding():
    """两个顶栏外框的上下 padding 都为 0——总高必须全部来自 TOPBAR_H + 底边线。

    否则「内容高相同」推不出「总高相同」，底线也就不再共线。
    """
    with _tab_bar() as ht, _outline() as ho:
        for name, node in (
            ("标签栏", _tab_row_root(ht)),
            ("大纲头部", _outline_frame(ho)),
        ):
            assert _pad_top(node) == 0 and _pad_bottom(node) == 0, (
                f"{name} 的上下 padding 应为 0，"
                f"实际 top={_pad_top(node)} bottom={_pad_bottom(node)}"
            )


def test_both_top_bars_share_the_same_bottom_line():
    """标签栏总高 == 大纲头部总高：内容带 TOPBAR_H + 上下 padding 0 + 底边线 1px。

    两者相等即两条底线共线，顶栏连成同一条水平带（本文件存在的理由）。
    """
    with _tab_bar() as ht, _outline() as ho:
        bar = _tab_row_root(ht)
        frame = _outline_frame(ho)
        assert _bottom_line_width(bar) == _bottom_line_width(frame) == 1, (
            "两个顶栏的底边线宽不同："
            f"标签栏 {_bottom_line_width(bar)} / 大纲 {_bottom_line_width(frame)}"
        )
        # 两个外框都不自行定高：总高 = 定高内容带 + 1px 边线
        assert bar.height is None, "标签栏根容器不应自行定高（会与标签容器叠高）"
        assert frame.height is None
        tab_h = _band_containers(ht)[0].height
        out_h = _outline_band(ho).height
        assert _total_height(tab_h, bar) == _total_height(out_h, frame), (
            f"顶栏总高不一致：标签栏 {_total_height(tab_h, bar)} "
            f"vs 大纲 {_total_height(out_h, frame)}"
        )


def test_diff_tab_bar_keeps_the_same_height():
    """对比标签（双文件名、更宽）也必须落在同一行高上。

    对比标签走的是同一条标签容器代码路径，但宽度与图标不同，容易被后续改动分化。
    """
    diff_tabs = [
        {"type": "diff", "left_path": r"C:\docs\a.md", "right_path": r"C:\docs\b.md"},
    ]
    with _tab_bar(diff_tabs) as h:
        band = _band_containers(h)
        # 1 个对比标签 + 1 个「+」按钮
        assert len(band) == len(diff_tabs) + 1, f"对比标签行高漂移：{[n.height for n in band]}"
