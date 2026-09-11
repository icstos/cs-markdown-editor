"""声明式渲染夹具自检：确认进程内渲染 / 状态驱动 / effect 执行可用。

夹具（``tests/harness.py``）是重构期间唯一的集成级安全网，因此它自身必须有
测试证明其语义与 Flet 0.86 一致：状态变更触发重渲染、effect 按依赖执行、
按钮回调可驱动状态。
"""

import flet as ft

from tests.harness import RenderHarness


@ft.component
def _Counter():
    n, set_n = ft.use_state(0)
    log, set_log = ft.use_state([])

    ft.use_effect(lambda: set_log([*log, n]), [])

    return ft.Column(
        controls=[
            ft.Text(f"n={n}"),
            ft.Button("inc", on_click=lambda: set_n(n + 1)),
        ]
    )


def _texts(h: RenderHarness) -> list[str]:
    return [c.value for c in h.find(lambda n: isinstance(n, ft.Text))]


def test_render_produces_tree():
    h = RenderHarness()
    h.render(_Counter)
    assert h.buttons(), "渲染树中应能找到按钮"


def test_initial_state_rendered():
    h = RenderHarness()
    h.render(_Counter)
    assert _texts(h) == ["n=0"]


def test_setter_triggers_rerender():
    """点击按钮 → use_state 变更 → 组件重渲染为新值（UI = f(state)）。"""
    h = RenderHarness()
    h.render(_Counter)
    h.click(h.buttons()[0])
    assert _texts(h) == ["n=1"]


def test_repeated_clicks_follow_state():
    h = RenderHarness()
    h.render(_Counter)
    for expected in (1, 2, 3):
        h.click(h.buttons()[0])
        assert _texts(h) == [f"n={expected}"]
