"""大文件打开速度优化的回归护栏。

覆盖两项优化，各自对应一个独立的失效模式：

1. **解析快速构造**（`models.document.new_segment / new_line / new_document`）
   整篇解析要构造「行数 × 段数」个对象，常规 `@ft.observable` 构造每个字段赋值
   都走 `_wrap_if_collection → value_equal → _notify`，而 `_notify` 即便没有监听者
   也会迭代 WeakSet 并自增版本号（cProfile 实测占解析耗时 57%）。快速构造器用
   `object.__setattr__` 直写 `__dict__`，但**产出对象必须与常规构造同构**。
   护栏：字段集合、字段类型（含 `segments`/`lines` 仍是 `ObservableList`）、
   以及**挂载后的通知行为**都必须逐字一致——否则编辑期重渲染会静默失效。

2. **行视图窗口化**（`views/editor/_render.py` + `__init__.py::request_line_window`）
   `ListView.build_controls_on_demand` 只让 Flutter 客户端懒建 widget，Python 侧
   控件对象仍全量构造（实测 3 千行 ≈ 2.4s，95% 花在 flet `_configure_dataclass`）。
   窗口化只构造 [0, hi) 的行，未构建的行**逐行**用一个等高占位容器补齐。
   护栏：
   - 首屏构造的行控件数必须远小于总行数（这是「打开快」的本质，不是时间断言）
   - **列表项数恒等于行数**（`len(controls) == len(lines)`）：Flutter 的
     `maxScrollExtent` 按「已布局项平均高 × 项数」外推，项数与行数不一致会让
     尾部占位永远布局不到 → 文档后段滚不到（实测 1555 行文档 max 只有 4793）。
     这是窗口化最容易踩的坑，也是本文件存在的主要理由。
   - 占位容器高度必须按行高推算（不是 0 / 不是常数 / 不是单段合计）
   - 窗口**只增不减**（上界回缩会让视口上方换成估算高度的占位 → 滚动内容跳动）
   - 滚到底后取消占位，窗口覆盖全文档
   - 小文档与对比模式不窗口化（行为与优化前逐字一致）
"""

import sys
from pathlib import Path

import flet as ft
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config.settings as cs  # noqa: E402
from models.document import (  # noqa: E402
    Document,
    Line,
    Segment,
    new_document,
    new_line,
    new_segment,
)
from parser import parse_markdown  # noqa: E402
from tests.harness import RenderHarness, walk  # noqa: E402
from views.editor import (  # noqa: E402
    _WINDOW_ACTIVATE,
    _WINDOW_CHUNK,
    _WINDOW_INITIAL,
    _WINDOW_MARGIN,
    MarkdownEditor,
)
from views.editor._render import _snap_window  # noqa: E402

# 设置文件隔离（同 tests/test_boot_smoke.py：受限环境下 pytest tmp_path 不可用）
_SANDBOX = Path(__file__).resolve().parent / ".perf-sandbox"
_SANDBOX.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch):
    monkeypatch.setattr(cs, "SETTINGS_PATH", str(_SANDBOX / "settings.json"))


# ---------------------------------------------------------------------------
# 1. 解析快速构造：与常规构造同构
# ---------------------------------------------------------------------------


class _Spy:
    """强引用监听器（Observable 的监听表是 WeakSet，内联 lambda 会被立即回收）。"""

    def __init__(self):
        self.fields: list = []

    def __call__(self, _sender, field):
        self.fields.append(field)


def _md_lines(count: int) -> str:
    """生成 count 行普通列表行（足以触发窗口化）。"""
    return "\n".join(f"- 列表项 {i} with **bold** and `code`" for i in range(count))


def test_fast_segment_is_structurally_identical():
    """快速构造的 Segment 与原构造的字段集合 / 取值完全一致。"""
    fast = new_segment("text", "raw", "text", url="u", level=2, marks=("strong",))
    normal = Segment("text", "raw", "text", url="u", level=2, marks=("strong",))
    assert set(fast.__dict__) == set(normal.__dict__) - {
        # Observable 的两项簿记字段是**惰性**创建的：__version__ 为类属性（默认 0），
        # listeners 存储在首次 subscribe 时建立。快速构造跳过通知，故两者尚未落到
        # 实例 __dict__——这是惰性初始化差异，不是语义差异（下方通知行为测试覆盖）。
        "__version__",
        "_Observable__listeners_storage",
    }
    for k in ("seg_type", "raw", "text", "url", "level", "marks"):
        assert getattr(fast, k) == getattr(normal, k)


def test_fast_line_wraps_segments_as_observable_list():
    """`segments` 仍须是 ObservableList：编辑器依赖其类型与常规构造一致。"""
    line = new_line(raw="x", segments=[new_segment(text="x")])
    assert len(line.segments) == 1
    assert line.segments[0].text == "x"
    # 与常规构造同类型（不是裸 list）
    assert type(line.segments) is type(Line().segments)
    # 归属本行：原地变更会通知本行（_touch 依赖 _owner_ref 指回 owner）
    spy = _Spy()
    line.subscribe(spy)  # type: ignore[attr-defined]
    line.segments.append(new_segment(text="y"))
    assert spy.fields == ["segments"]


def test_fast_document_wraps_lines_as_observable_list():
    """`lines` 仍须是 ObservableList：编辑器大量原地 insert/赋值依赖其 _touch 通知。"""
    doc = new_document([new_line(raw="a")])
    assert type(doc.lines) is type(Document().lines)
    spy = _Spy()
    doc.subscribe(spy)  # type: ignore[attr-defined]
    doc.lines.insert(0, new_line(raw="b"))  # type: ignore[attr-defined]
    doc.lines[0] = new_line(raw="c")  # type: ignore[call-overload]
    assert spy.fields == ["lines", "lines"]


def test_fast_and_normal_construction_notify_identically():
    """两种构造路径的「挂载后可观察行为」必须逐字一致（同序、同次数）。"""

    def run(make_doc, make_line):
        doc = make_doc()
        spy = _Spy()
        doc.subscribe(spy)
        doc.lines.append(make_line("a"))
        doc.lines.insert(0, make_line("c"))
        doc.lines[0] = make_line("r")
        del doc.lines[0]
        line = doc.lines[0]
        line_spy = _Spy()
        line.subscribe(line_spy)
        line.segments = [new_segment(text="x")]
        line.raw = "new"
        return spy.fields, line_spy.fields

    fast = run(lambda: new_document([]), lambda r: new_line(raw=r))
    normal = run(lambda: Document(), lambda r: Line(raw=r))
    assert fast == normal
    assert fast[0] == ["lines"] * 4
    assert fast[1] == ["segments", "raw"]


def test_parse_markdown_produces_observable_collections():
    """整篇解析（走快速构造）产出的集合类型仍与常规构造一致。"""
    doc = parse_markdown("# 标题\n- [x] 任务\n\n```py\nx = 1\n```\n")
    assert type(doc.lines) is type(Document().lines)
    assert all(type(line.segments) is type(Line().segments) for line in doc.lines)
    kinds = [line.block_type for line in doc.lines]
    assert "heading" in kinds and "code_block" in kinds
    task = next(line for line in doc.lines if line.task)
    assert task.checked is True
    code = next(line for line in doc.lines if line.block_type == "code_block")
    assert code.lang == "py"


# ---------------------------------------------------------------------------
# 2. 窗口边界对齐（纯函数）
# ---------------------------------------------------------------------------


class _FakeLine:
    def __init__(self, block_type):
        self.block_type = block_type


def test_snap_window_extends_over_table_block():
    """窗口上界切在表格中间时必须向后吃掉整个表格连续段。

    表格是「连续 TABLE 行合并为一个 TableView」渲染的；窗口切在中间会渲染出
    只有后半截的表格。
    """
    lines = [_FakeLine("paragraph")] * 3 + [_FakeLine("table")] * 4 + [
        _FakeLine("paragraph")
    ] * 3
    # 上界 5 落在表格（索引 3..6）中间 → 扩到 7（表格结束后的第一行）
    assert _snap_window(lines, 0, 5) == (0, 7)
    # 下界 5 落在表格中间 → 向前扩到 3
    assert _snap_window(lines, 5, 9) == (3, 9)
    # 不含表格时原样返回
    assert _snap_window(lines, 0, 2) == (0, 2)


# ---------------------------------------------------------------------------
# 3. 窗口化渲染（真实 MarkdownEditor 组件树）
# ---------------------------------------------------------------------------


class _ScrollEvent:
    """ListView.on_scroll 事件桩（只需 _on_scroll 读取的三个字段）。"""

    def __init__(self, pixels: float, viewport: float = 800.0, extent: float = 1e6):
        self.pixels = pixels
        self.viewport_dimension = viewport
        self.max_scroll_extent = extent


def _render(md: str, **props):
    harness = RenderHarness()
    doc = parse_markdown(md)
    harness.render(
        MarkdownEditor, document=doc, settings={"word_wrap": True}, **props
    )
    return harness, doc


def _list_view(harness) -> ft.ListView:
    return next(n for n in walk(harness.tree) if isinstance(n, ft.ListView))


def _built_line_count(lv: ft.ListView) -> int:
    """已构建的行控件数（LineView 的 key 形如 line-N）。"""
    return sum(1 for c in lv.controls if str(getattr(c, "key", "")).startswith("line-"))


def _placeholders(lv: ft.ListView) -> list:
    """未构建行的等高占位容器（无 key）；窗口覆盖全文档时为空列表。

    占位必须**逐行一个**，不能是单个大容器（见模块 docstring 第 2 节）。
    """
    return [c for c in lv.controls if getattr(c, "key", None) is None]


def test_item_count_equals_line_count_when_windowed():
    """窗口化后列表项数必须恒等于行数（Flutter 滚动范围外推的前提）。

    失效模式：用一个容器覆盖整段尾部 → 项数远小于行数 → Flutter 的
    `maxScrollExtent` 只能按「已布局项（窗口内的真实行）平均高 × 项数」外推，
    那个大占位永远落在布局窗口之外，于是 max 退化成窗口那几屏的高度，
    文档后段滚不到（真机实测 1555 行文档 max 只有 4793，真值 ≈50000）。
    """
    n_lines = 1200
    harness, doc = _render(_md_lines(n_lines))
    try:
        lv = _list_view(harness)
        assert len(lv.controls) == len(doc.lines)
        # 未构建的行一行一个占位，占位数 = 总行数 - 已构建行数
        assert len(_placeholders(lv)) == n_lines - _built_line_count(lv)
    finally:
        harness.dispose()


def test_placeholder_heights_follow_line_offset_prefix():
    """占位高度 = 行偏移前缀和差分：Flutter 侧第 j 项的布局位置 == 估算行偏移。

    这是「列表网格坐标」与「滚动定位尺子」同源的保证（见 _render 模块 docstring）：
    若占位高度改用「剩余总高 / 剩余行数」之类的常数，逐行位置就会与
    estimate_line_offset 错开，滚动落点与大纲跳转都会偏。
    """
    n_lines = 1200
    harness, doc = _render(_md_lines(n_lines))
    try:
        lv = _list_view(harness)
        ph = _placeholders(lv)
        assert ph
        # 逐行高度应落在单行高的合理区间（不是 0、不是把整段合并成一个数）
        assert all(20.0 <= c.height <= 60.0 for c in ph), [c.height for c in ph[:5]]
        # 行高有差异（列表行 vs 代码块/标题），说明确实按行分别推算
        assert len({round(c.height, 2) for c in ph}) > 1
        # 占位高度合计 == 未构建段的行偏移跨度
        built = _built_line_count(lv)
        assert len(ph) == n_lines - built
    finally:
        harness.dispose()


def test_small_doc_not_windowed():
    """行数不超过激活阈值时不窗口化：控件数与优化前逐字一致。"""
    harness, doc = _render(_md_lines(_WINDOW_ACTIVATE - 100))
    try:
        lv = _list_view(harness)
        assert len(lv.controls) == len(doc.lines)
        assert _placeholders(lv) == []
    finally:
        harness.dispose()


def test_large_doc_first_paint_builds_only_window():
    """大文档首屏只构造窗口内的行，其余行各一个轻量占位。

    这是「打开大文件快」的本质：优化前首屏要构造全部 N 行控件（每行约 5 个
    ft 控件、每个控件都要过一遍 flet 的 dataclass 遍历）。
    """
    n_lines = 1200
    assert n_lines > _WINDOW_ACTIVATE
    harness, doc = _render(_md_lines(n_lines))
    try:
        lv = _list_view(harness)
        assert len(doc.lines) == n_lines
        assert _built_line_count(lv) == _WINDOW_INITIAL
        # 首屏工作量与总行数解耦（放宽到 1/5，留出实现微调空间）
        assert _built_line_count(lv) < n_lines // 5
        # 但列表项数仍是完整的行数（滚动范围外推的前提）
        assert len(lv.controls) == n_lines
    finally:
        harness.dispose()


def test_scroll_extends_window_and_never_shrinks():
    """滚动到窗口外 → 按块外扩；回滚到文档开头 → 窗口不回缩。

    回缩会让视口上方重新变成估算高度的占位容器，实测高度与估算不一致 →
    滚动内容跳动。故上界单调不减是**正确性约束**，不只是性能取舍。
    """
    harness, _ = _render(_md_lines(1200))
    try:
        lv = _list_view(harness)
        first = _built_line_count(lv)

        # 滚到中部：视口底约在第 850 行 → 窗口按 _WINDOW_CHUNK 向上取整外扩
        harness.interact(lv.on_scroll, _ScrollEvent(pixels=25000.0))
        lv = _list_view(harness)
        mid = _built_line_count(lv)
        assert mid > first
        assert mid >= 850 + _WINDOW_MARGIN + _WINDOW_CHUNK

        # 回滚到文档开头：窗口保持（不回缩）
        harness.interact(lv.on_scroll, _ScrollEvent(pixels=0.0))
        lv = _list_view(harness)
        assert _built_line_count(lv) == mid
    finally:
        harness.dispose()


def test_scroll_to_end_materialises_all_and_drops_placeholders():
    """滚到文档末尾 → 窗口覆盖全文档，占位容器全部消失。"""
    n_lines = 800
    harness, doc = _render(_md_lines(n_lines))
    try:
        lv = _list_view(harness)
        assert _placeholders(lv)
        harness.interact(lv.on_scroll, _ScrollEvent(pixels=1e7))
        lv = _list_view(harness)
        assert _built_line_count(lv) == len(doc.lines)
        assert len(lv.controls) == len(doc.lines)
        assert _placeholders(lv) == []
    finally:
        harness.dispose()


def test_diff_mode_is_not_windowed():
    """对比模式不窗口化：左右两侧需逐行插入对齐间隙，间隙高度不在高度前缀和里。"""
    n_lines = 1200
    harness, doc = _render(
        _md_lines(n_lines), diff_marks={0: "added", 1: "modified"}
    )
    try:
        lv = _list_view(harness)
        assert len(lv.controls) == len(doc.lines)
        assert _placeholders(lv) == []
    finally:
        harness.dispose()


def test_request_window_is_idempotent_and_monotonic():
    """request_line_window 幂等（窗口够用时零状态变更），且只增不减。"""
    harness, _ = _render(_md_lines(1200))
    try:
        lv = _list_view(harness)
        before = _built_line_count(lv)
        # 连续请求视口内的行（滚动量都落在已构建窗口内）：不应触发任何变化
        harness.interact(lv.on_scroll, _ScrollEvent(pixels=0.0))
        harness.interact(lv.on_scroll, _ScrollEvent(pixels=100.0))
        assert _built_line_count(_list_view(harness)) == before
    finally:
        harness.dispose()
