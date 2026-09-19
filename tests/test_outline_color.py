"""大纲配色守卫：大纲文字/字重必须与编辑区标题**逐级对应**。

背景：侧栏大纲原先所有条目的文字都是同一种正文色（`c.text`），级别只体现在
左侧 3px 色条上；文档内的 `[toc]` 卡片也一样（只有色条上色）。结果「大纲里的
这一条」与「正文里的那行标题」颜色对不上——在侧栏扫到一条时，无法凭颜色认回
正文里对应的是哪一级标题，只能反查色条。

现在三处标题文字共用 `styles.heading_text_color(level)`：大纲条目文字、色条、
编辑区标题同源。本文件锁定三条不变式，防止以后两边各自调色而漂移：

1. **颜色同源**：大纲条目文字的色 == 编辑区同一级标题**真渲染出来**的色。
   注意不是比对同一个常量——编辑器侧取的是 `raw_to_visible_spans` 的实际输出，
   所以「把编辑区标题改个色」会立刻让本测试失败，而不是两边一起错。
2. **字重同序**：大纲字重 == 编辑区字重整体降一档（见 styles 的说明）。
   这条同时保证「相邻级别仍有差异」「粗细顺序逐级一致」。
3. **两处一致**：文档内 `[toc]` 卡片与侧栏大纲取到同一组色/重（两处曾各写一遍）。
"""

import sys
from pathlib import Path

import flet as ft
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.document import BlockType
from parser import parse_markdown
from styles import (
    _HEADING_WEIGHTS,
    _OUTLINE_HEADING_WEIGHTS,
    block_text_size,
    get_colors,
    heading_text_color,
    outline_heading_weight,
)
from tests.harness import RenderHarness, walk
from utils.toc import compute_toc
from views.line_view import LineView
from views.segment_view import raw_to_visible_spans
from views.toc import render_outline_panel

LEVELS = (1, 2, 3, 4, 5, 6)
MODES = (ft.ThemeMode.LIGHT, ft.ThemeMode.DARK)


# ---------------------------------------------------------------------------
# 取材：编辑区侧的标题色/字重
# ---------------------------------------------------------------------------


def _heading_doc():
    """一篇每个级别各一个标题的文档（含一个 [toc] 目录块）。"""
    body = "\n\n".join(f"{'#' * lv} 标题{lv}" for lv in LEVELS)
    return parse_markdown(f"{body}\n\n[toc]\n")


def _editor_heading_styles(doc) -> dict[int, tuple[str, ft.FontWeight]]:
    """编辑区渲染路径（raw_to_visible_spans）产出的 (色, 字重)，按级别索引。

    走的是 RenderedLine 用的同一个入口，因此这里比对的是**真实渲染结果**，
    而不是「两边都读同一个常量」这种同义反复。
    """
    out: dict[int, tuple[str, ft.FontWeight]] = {}
    for line in doc.lines:
        if line.block_type != BlockType.HEADING:
            continue
        size = block_text_size(BlockType.HEADING, line.level)
        spans = raw_to_visible_spans(line, size, None, line.level)
        text = line.raw.lstrip("#").strip()
        span = next((s for s in spans if s.text == text), None)
        assert span is not None and span.style is not None, (
            f"H{line.level} 未渲染出内容 span：{[s.text for s in spans]}"
        )
        out[line.level] = (span.style.color, span.style.weight)
    assert sorted(out) == list(LEVELS), f"取材文档不完整：{sorted(out)}"
    return out


# ---------------------------------------------------------------------------
# 取材：大纲侧的色/字重
# ---------------------------------------------------------------------------


def _outline_rows(colors) -> list[tuple[str, str, ft.FontWeight]]:
    """渲染侧栏大纲面板，按行返回 (文字, 文字色, 字重)，顺序与条目一致。"""
    doc = _heading_doc()
    panel = render_outline_panel(compute_toc(doc), lambda _i: None, colors)
    nodes = list(walk(panel))
    texts = [
        n
        for n in nodes
        if isinstance(n, ft.Text) and str(n.value or "").startswith("标题")
    ]
    return [(str(n.value), n.color, n.weight) for n in texts]


def _outline_bars(colors) -> list[str]:
    """侧栏大纲的级别色条颜色（按行顺序）。"""
    doc = _heading_doc()
    panel = render_outline_panel(compute_toc(doc), lambda _i: None, colors)
    return [
        n.bgcolor
        for n in walk(panel)
        if isinstance(n, ft.Container) and n.width == 3 and n.bgcolor is not None
    ]


def _toc_card_styles(mode) -> dict[str, tuple[str, ft.FontWeight]]:
    """渲染文档内 [toc] 卡片，返回 {标题文本: (色, 字重)}。

    卡片内部与真实 App 一致地走 `_current_colors()`（读 page.theme_mode），
    因此这里设页面主题而不是把配色塞进去。
    """
    doc = _heading_doc()
    entries = compute_toc(doc)
    line = next(ln for ln in doc.lines if ln.block_type == BlockType.TOC)

    @ft.component
    def _Root():
        return LineView(line, 0, toc_entries=entries, on_jump_to=lambda _i: None)

    harness = RenderHarness()
    try:
        harness.page.theme_mode = mode
        harness.render(_Root)
        return {
            str(node.value): (node.color, node.weight)
            for node in harness.find(
                lambda n: isinstance(n, ft.Text) and str(n.value or "").startswith("标题")
            )
        }
    finally:
        harness.dispose()


# ---------------------------------------------------------------------------
# 1. 颜色同源：大纲文字色 == 编辑区标题真渲染色
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
def test_outline_text_color_matches_editor_heading(mode):
    """每一级：大纲条目文字色 == 编辑区该级标题渲染出的文字色。"""
    harness = RenderHarness()
    try:
        harness.page.theme_mode = mode
        colors = get_colors(mode)
        editor, outline = harness.interact(
            lambda: (_editor_heading_styles(_heading_doc()), _outline_rows(colors))
        )
    finally:
        harness.dispose()

    assert len(outline) == len(LEVELS), f"大纲条目数不符：{outline}"
    for (text, color, _weight), lv in zip(outline, LEVELS, strict=True):
        assert text == f"标题{lv}", f"条目顺序错位：{text}"
        assert color == editor[lv][0], (
            f"H{lv} 大纲文字色 {color} != 编辑区标题色 {editor[lv][0]}"
        )


@pytest.mark.parametrize("mode", MODES)
def test_outline_bar_shares_the_text_color(mode):
    """色条与文字同色：色条只是同一级别的短竖线强调，不引入第二种颜色。"""
    harness = RenderHarness()
    try:
        harness.page.theme_mode = mode
        colors = get_colors(mode)
        rows, bars = harness.interact(
            lambda: (_outline_rows(colors), _outline_bars(colors))
        )
    finally:
        harness.dispose()

    assert len(bars) == len(rows) == len(LEVELS)
    for (_text, color, _w), bar in zip(rows, bars, strict=True):
        assert bar == color


def test_outline_colors_follow_the_theme():
    """两套主题下大纲取到不同的色组：证明没有写死颜色。"""
    light = [c for _t, c, _w in _outline_rows(get_colors(ft.ThemeMode.LIGHT))]
    dark = [c for _t, c, _w in _outline_rows(get_colors(ft.ThemeMode.DARK))]
    assert light != dark
    assert all(a != b for a, b in zip(light, dark, strict=True)), (
        f"逐级都应与暗色版不同：light={light} dark={dark}"
    )


# ---------------------------------------------------------------------------
# 2. 字重同序：大纲 == 编辑区整体降一档
# ---------------------------------------------------------------------------


def _weight_num(w: ft.FontWeight) -> int:
    """FontWeight → 数值（'w600' → 600）。"""
    raw = str(getattr(w, "value", w))
    assert raw.startswith("w") and raw[1:].isdigit(), f"非数值字重：{w}"
    return int(raw[1:])


def test_outline_weight_is_editor_weight_shifted_down_one_step():
    """大纲字重 = 编辑区字重 -100（整体降一档）。

    降档是为了 12px 紧凑列表不糊（编辑区那套按 30→15px 设计）；「同序」正是
    由「逐级 -100」保证的——不会出现 H3 比 H2 粗这类层级错乱。
    """
    for lv in LEVELS:
        editor = _weight_num(_HEADING_WEIGHTS[lv])
        outline = _weight_num(_OUTLINE_HEADING_WEIGHTS[lv])
        assert outline == editor - 100, f"H{lv} 字重未按档位换算：{editor} → {outline}"


def test_outline_weight_helper_falls_back_for_unknown_level():
    """级别越界回退常规字重，不抛错。"""
    assert outline_heading_weight(0) == ft.FontWeight.NORMAL
    assert outline_heading_weight(9) == ft.FontWeight.NORMAL


@pytest.mark.parametrize("mode", MODES)
def test_outline_weight_matches_the_helper(mode):
    """渲染出来的字重确实走 outline_heading_weight（而不是另写一套）。"""
    rows = _outline_rows(get_colors(mode))
    for (_text, _color, weight), lv in zip(rows, LEVELS, strict=True):
        assert weight == outline_heading_weight(lv)


# ---------------------------------------------------------------------------
# 3. 两处一致：文档内 [toc] 卡片 == 侧栏大纲
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
def test_toc_card_matches_outline(mode):
    """文档内 [toc] 卡片与侧栏大纲取到同一组色/重（两处曾各写一遍）。"""
    colors = get_colors(mode)
    card = _toc_card_styles(mode)
    assert len(card) == len(LEVELS), f"卡片条目数不符：{sorted(card)}"
    rows = _outline_rows(colors)
    for (text, color, weight) in rows:
        assert text in card, f"卡片缺少条目 {text}"
        assert card[text] == (color, weight), (
            f"{text} 卡片 {card[text]} != 侧栏 {(color, weight)}"
        )


# ---------------------------------------------------------------------------
# 取色口自身
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
def test_heading_text_color_reads_the_theme_token(mode):
    """唯一取色口逐级等于该主题的 heading_colors；越界回退正文色。"""
    colors = get_colors(mode)
    for lv in LEVELS:
        assert heading_text_color(lv, colors) == colors.heading_colors[lv]
    assert heading_text_color(0, colors) == colors.text
    assert heading_text_color(9, colors) == colors.text
