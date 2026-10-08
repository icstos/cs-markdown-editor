"""拆分切换的滚动锚点：切换拆分不回到文档首行（视图层 / 编辑器层）。

背景（真机实测的行为）：单编辑器与拆分态的左视口虽用同一个 Flet key，但控件树
路径不同（`body → Container → 编辑器` ↔ `body → Row → Container → 编辑器`），
Flutter 不复用元素 → **切换拆分时两个视口都会重建**，`ListView` 默认 offset=0。
于是用户在文档中部按 Ctrl+\\ 会看到"一切分就跳回第一行"。

修法：控制器在切换时读「源视口顶部可见行号」并下发锚点（`app/_split_editor.py`），
由重建后的编辑器在**挂载期 effect** 里贴到视口顶部（`views/editor/__init__.py`
的 `initial_scroll_line`）。本文件覆盖后两段的护栏：

1. `MarkdownEditor(initial_scroll_line=N)` 挂载后确实发起了一次贴顶滚动，
   且落点就在第 N 行附近（不是 0、也不是别处）；
2. `initial_scroll_line=None` 时完全不碰滚动（不干预默认首行行为）；
3. `get_top_line()`（锚点来源）在未滚动时走快路径返回 0，滚动后由**编辑器
   自己那把尺子**（`estimate_line_offset` / 前缀和）反查出顶部可见行。

真机像素级验证在 `.workbuddy/probe/split_scroll_probe.py`（同一行内容必须同时
出现在左右窗格同一 y）；本文件是它的进程内补充：几何量不出来，但"锚点有没有被
用上"能在这里钉死。
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config.settings as cs
from app import App
from parser import parse_markdown
from tests.harness import RenderHarness, walk
from views.editor import MarkdownEditor

_SANDBOX = Path(__file__).resolve().parent / ".split-scroll-sandbox"
_SANDBOX.mkdir(parents=True, exist_ok=True)
_SETTINGS_FILE = _SANDBOX / "settings.json"


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch):
    """设置文件隔离（同 tests/test_boot_smoke.py：受限环境下 tmp_path 不可用）。"""
    monkeypatch.setattr(cs, "SETTINGS_PATH", str(_SETTINGS_FILE))


class _ScrollEvent:
    """ListView.on_scroll 事件桩（`_on_scroll` 只读这三个字段）。"""

    def __init__(self, pixels: float, viewport: float = 800.0, extent: float = 1e6):
        self.pixels = pixels
        self.viewport_dimension = viewport
        self.max_scroll_extent = extent


def _render(md: str, **props):
    """渲染真实 MarkdownEditor；返回 (harness, nav 持有者)。

    nav 持有者用 `SimpleNamespace(current=…)` 而非 `ft.Ref`：Flet 的 Ref 是弱引用，
    装不下 `EditorActions`（dataclass，无 `__weakref__`），编辑器本身也只做
    `nav_ref.current = actions` 这一处写入。
    """
    harness = RenderHarness()
    nav = SimpleNamespace(current=None)
    harness.render(
        MarkdownEditor,
        document=parse_markdown(md),
        settings={"word_wrap": True},
        nav_ref=nav,
        **props,
    )
    return harness, nav


def _record_scrolls(monkeypatch) -> list[float]:
    """截获 ListView.scroll_to 的落点（编辑器贴顶最终都走到这里）。"""
    calls: list[float] = []

    async def _rec(_self, offset, duration=0, **kwargs):
        calls.append(offset)

    monkeypatch.setattr(ft.ListView, "scroll_to", _rec)
    return calls


DOC = "\n".join(f"L{i:03d} 正文行" for i in range(40))
ANCHOR = 8


def _anchor_landing(monkeypatch, line: int) -> float:
    """渲染一次编辑器（锚点=line），返回贴顶滚动的第一个落点。"""
    calls = _record_scrolls(monkeypatch)
    h, _nav = _render(DOC, initial_scroll_line=line)
    try:
        assert calls, f"初始滚动锚点被丢弃：锚点 {line} 没有引发任何滚动"
        return calls[0]
    finally:
        h.dispose()


def test_mount_applies_initial_scroll_line(monkeypatch):
    """挂载期 effect 按锚点贴顶：落点由锚点行号决定，且不落在文档首行。"""
    near = _anchor_landing(monkeypatch, 6)
    far = _anchor_landing(monkeypatch, 20)
    assert near > 0, f"锚点 6 却停在首行（offset={near}）"
    assert far > near, (
        f"落点与锚点行号无关：锚点 6 → {near:.0f}，锚点 20 → {far:.0f}"
        "（锚点未真正参与滚动定位）"
    )


def test_mount_without_anchor_does_not_scroll(monkeypatch):
    """不传锚点（None）＝不干预：挂载期一次滚动都不发起，默认停在首行。"""
    calls = _record_scrolls(monkeypatch)
    h, _nav = _render(DOC, initial_scroll_line=None)
    try:
        assert calls == [], f"未下发锚点却滚动了：{calls}"
    finally:
        h.dispose()


def test_get_top_line_fast_path_before_any_scroll():
    """未滚动 → 顶部可见行＝0，且不构建行偏移前缀和（快路径）。"""
    h, nav = _render(DOC)
    try:
        assert nav.current.get_top_line() == 0
    finally:
        h.dispose()


def test_get_top_line_reflects_scrolled_position(monkeypatch):
    """滚动后顶部可见行随偏移增大：锚点落点处＝锚点行（同一把尺子）。"""
    calls = _record_scrolls(monkeypatch)
    h, nav = _render(DOC, initial_scroll_line=12)
    try:
        landing = calls[0]
        lv = next(n for n in walk(h.tree) if isinstance(n, ft.ListView))
        # 走真实的 on_scroll 链路（ListView.on_scroll → 编辑器上报偏移/视口高）
        h.interact(lv.on_scroll, _ScrollEvent(pixels=landing, viewport=200.0))
        top = nav.current.get_top_line()
        # 贴顶落点扣掉了内容顶部内边距，故允许差一行
        assert abs(top - 12) <= 1, f"落点 {landing:.0f} 处的顶部可见行＝{top}，应≈12"
        h.interact(lv.on_scroll, _ScrollEvent(pixels=landing * 2, viewport=200.0))
        assert nav.current.get_top_line() > top, "偏移更大时顶部可见行应更靠后"
    finally:
        h.dispose()


# ---------------------------------------------------------------------------
# 锚点查表（纯函数）：只认自己那一组的会话号
# ---------------------------------------------------------------------------


def test_anchor_line_only_matches_its_own_session():
    """会话号不匹配即失效：组内换标签后，新标签不会被上一个标签的位置拽走。"""
    from app._render import _anchor_line

    anchor = ((7, 42), (4, 42))
    assert _anchor_line(anchor, 7) == 42
    assert _anchor_line(anchor, 4) == 42
    # 该组换过标签（session 已递增）→ 锚点作废
    assert _anchor_line(anchor, 5) is None
    assert _anchor_line(anchor, 8) is None
    # 没有锚点时一律 None（不干预，视口停在首行）
    assert _anchor_line(None, 7) is None


# ---------------------------------------------------------------------------
# App 级：真的点一次「拆分」，拆分树必须构造得出来，且锚点随之下发
# ---------------------------------------------------------------------------


def _editor_list_view(h):
    """编辑器自己的 ListView（行视图 key 形如 line-N）。"""
    for n in walk(h.tree):
        if isinstance(n, ft.ListView) and any(
            str(getattr(c, "key", "")).startswith("line-") for c in (n.controls or [])
        ):
            return n
    raise AssertionError("没找到编辑器的 ListView（渲染树结构变了？）")


def _split_toggle_control(h):
    """状态栏「拆分」项：子树里带 "拆分" 文本的可点击控件。"""
    for b in h.buttons():
        if any(
            isinstance(n, ft.Text)
            and isinstance(getattr(n, "value", None), str)
            and n.value.startswith("拆分")
            for n in walk(b)
        ):
            return b
    raise AssertionError("没找到状态栏「拆分」项")


def _app_ctx(h):
    """从 page.on_keyboard_event 的闭包取 AppContext（同 test_first_run_sample）。"""
    handler = h.page.on_keyboard_event
    return next(
        c.cell_contents for c in handler.__closure__
        if type(c.cell_contents).__name__ == "AppContext"
    )


def test_split_toggle_builds_split_tree_and_carries_anchor(monkeypatch):
    """点状态栏「拆分」→ 真的进入拆分布局，且锚点行号 > 0（没回到首行）。

    跑的是**真实 App 渲染树**，这是本文件里最重要的一条：任何让拆分树构造失败的
    改动都会在这里炸出来（实际踩过：锚点辅助函数写在 `build_render` 闭包里，而
    `_build_split_area` 是模块级函数 → NameError → 状态已翻转、界面纹丝不动、
    应用日志一声不响）。真机探针看到的只是"点击没生效"，定位不到根因。
    """
    # 删掉设置文件 → 首次启动 → 内置示例文档（298 行），文档内容确定
    _SETTINGS_FILE.unlink(missing_ok=True)
    calls = _record_scrolls(monkeypatch)
    h = RenderHarness()
    h.render(App)
    try:
        ctx = _app_ctx(h)
        # 模拟"用户已读到文档中部"：走编辑器自己的滚动上报链路
        h.interact(_editor_list_view(h).on_scroll, _ScrollEvent(pixels=400.0, viewport=300.0))
        anchor = ctx.nav_ref.current.get_top_line()
        assert anchor > 0, "前置条件不成立：驱动滚动后顶部可见行仍为 0"

        split = _split_toggle_control(h)
        h.interact(lambda: split.on_click(None))

        texts = [
            n.value for n in walk(h.tree)
            if isinstance(n, ft.Text) and isinstance(getattr(n, "value", None), str)
        ]
        assert "拆分: 开" in texts, "点击状态栏「拆分」后没有进入拆分态"
        assert any(isinstance(n, ft.VerticalDivider) for n in walk(h.tree)), (
            "拆分态已打开但没有中缝分隔线 → 编辑区没走拆分分支"
        )
        # 新挂载的两个视口各按锚点贴顶（而不是停在首行）
        assert len(calls) >= 2, f"两个视口都该按锚点滚动，实际只记录了 {calls}"
        assert all(c > 0 for c in calls), (
            f"有视口落回文档首行（锚点行号 {anchor}）：{calls}"
        )
    finally:
        h.dispose()


# ---------------------------------------------------------------------------
# 渲染层接线（静态）：每个视口的锚点会话号必须与它自己的 key 一致
# ---------------------------------------------------------------------------

RENDER_SRC = (
    Path(__file__).resolve().parent.parent / "app" / "_render.py"
).read_text(encoding="utf-8")


def _session_calls() -> list[tuple[int, str, str]]:
    """(行号, key 里的会话名, 锚点表达式) —— 只统计 key 带组会话号的编辑器。

    对比模式的两个编辑器 key 是 `diff-left-{active_index}`（不带组会话号），
    不受本不变量约束：对比态下两侧各有独立的滚动同步通道。
    """
    import ast
    import re

    out: list[tuple[int, str, str]] = []
    for node in ast.walk(ast.parse(RENDER_SRC)):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "MarkdownEditor"):
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        if "key" not in kw:
            continue
        m_key = re.search(r"session_(left|right)", ast.unparse(kw["key"]))
        if not m_key:
            continue
        anchor = ast.unparse(kw["initial_scroll_line"]) if "initial_scroll_line" in kw else ""
        out.append((node.lineno, m_key.group(1), anchor))
    return out


def test_every_scroll_viewport_gets_anchor_of_its_own_session():
    """三个滚动视口（单编辑器 + 拆分左右）都要带上**自己那组**会话的锚点。

    错配（例如右视口误用 session_left）在进程内测不出来、真机探针也照样通过，
    但会让「右组换标签」不再使锚点失效——新标签会被上一个标签的浏览位置拽走。
    这是本功能唯一无法用像素量到的失效模式，故在此静态钉住。
    """
    calls = _session_calls()
    assert len(calls) == 3, f"应校验 3 个滚动视口（单编辑器 + 拆分左右），实际 {calls}"
    for lineno, key_session, anchor in calls:
        assert anchor, f"app/_render.py:{lineno} 的编辑器没继承浏览位置（缺 initial_scroll_line）"
        assert "_anchor_line(" in anchor, (
            f"app/_render.py:{lineno} 的锚点没走 _anchor_line（会话匹配会被绕过）：{anchor}"
        )
        assert f"session_{key_session}" in anchor, (
            f"app/_render.py:{lineno} 的 key 用 session_{key_session}，"
            f"锚点却是 {anchor} —— 会话号错配会让换标签后的锚点不失效"
        )
