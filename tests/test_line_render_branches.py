"""行渲染分支调用契约测试（代码块 / 前置元数据 / 公式块）。

背景：这三条分支只被 `LineView` 内部调用，渲染出的 `ft.Control` 在无头环境里
难以断言内容，因此长期没有测试覆盖——`_render_code_block` 曾因此漏传 `base`
参数（调用与签名位置参数错位，`_wrap_block` 必抛 TypeError，且 `on_code_blur`
与 `on_change_lang` 被互换）。

本测试双重锁定：
1. **签名契约**：调用点传入的位置参数必须与函数签名逐位对齐（防漏传 / 换序）；
2. **真实调用**：在组件渲染上下文内按调用点参数调用一次，确认不抛异常
   （这些分支内部使用 `ft.use_state` / `use_memo`，必须在组件渲染期执行）。
"""

import sys
from pathlib import Path

import flet as ft

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.document import BlockType, Line  # noqa: E402
from tests.harness import RenderHarness  # noqa: E402
from views import _block_frame, _frontmatter, line_view as lv  # noqa: E402

# 渲染分支所在模块（拆分后散落多处，故显式映射：调用点函数名 → 模块内实际名）
BRANCH_MODULES = {
    "_render_code_block": (lv, "_render_code_block"),
    "_render_math_block": (lv, "_render_math_block"),
    "_render_frontmatter": (_frontmatter, "render_frontmatter"),
}


def _line(block_type, raw: str, level: int = 0) -> Line:
    return Line(raw=raw, block_type=block_type, level=level)


def _ref(value=None):
    ref = ft.Ref()
    ref.current = value
    return ref


def _noop(*_a, **_k):
    return None


def _render_in_component(fn, *args, **kwargs) -> ft.Control:
    """在组件渲染上下文内调用渲染函数（hooks 只能在组件体顶层执行）。

    通过包裹成一个 `@ft.component` 再交给夹具渲染，使 `use_state` / `use_memo`
    有合法的 renderer 与组件宿主。
    """
    captured: dict[str, ft.Control] = {}

    @ft.component
    def _Probe():
        return ft.Container(content=fn(*args, **kwargs), key="probe-result")

    harness = RenderHarness()
    try:
        tree = harness.render(_Probe)
        for node in harness.find(lambda n: getattr(n, "key", None) == "probe-result"):
            captured["control"] = node
            break
        assert "control" in captured, f"未捕获渲染结果（tree={type(tree).__name__}）"
        return captured["control"]
    finally:
        harness.dispose()


# LineView 调用点实际传入的位置参数名（必须与签名逐位一致）
CALL_ARGS = {
    "_render_code_block": (
        "line", "line_idx", "base", "content_width", "clipboard_ref",
        "on_change_code", "on_code_focus", "on_code_blur", "on_change_lang",
        "on_code_selection", "code_field_ref", "is_current_line", "is_flash",
        "on_line_size_change",
    ),
    "_render_frontmatter": (
        "line", "line_idx", "base", "content_width", "clipboard_ref",
        "on_change_code", "on_code_focus", "on_code_blur",
        "code_field_ref", "is_current_line", "is_flash", "on_line_size_change",
    ),
    "_render_math_block": (
        "line", "line_idx", "base", "content_width",
        "on_change_math", "on_math_focus", "on_math_blur", "math_field_ref",
        "is_editing", "is_current_line", "is_flash", "on_line_size_change",
    ),
}


def test_call_site_arity_matches_signature():
    """调用点传入的位置参数必须与函数签名逐位对齐（防漏传 / 换序）。"""
    import inspect

    problems = {}
    for name, positional in CALL_ARGS.items():
        module, attr = BRANCH_MODULES[name]
        fn = getattr(module, attr)
        params = list(inspect.signature(fn).parameters)
        if params[: len(positional)] != list(positional):
            problems[name] = {"调用点": list(positional), "签名前几位": params[: len(positional)]}
    assert not problems, f"调用点与签名位置参数错位: {problems}"


def test_wrap_block_base_is_required_positional():
    """`wrap_block` 的 base 是必填位置参数——正是漏传时崩溃的根因。"""
    import inspect

    params = list(inspect.signature(_block_frame.wrap_block).parameters.values())
    assert params[2].name == "base"
    assert params[2].default is inspect.Parameter.empty, "base 不应有默认值"


def test_render_code_block_real_call():
    """按调用点参数真实调用：CodeEditor 岛渲染不得抛异常。"""
    control = _render_in_component(
        lv._render_code_block,
        _line(BlockType.CODE, "```python\nprint(1)\n```"),
        0, 16, 800.0, _ref(None),
        _noop, _noop, _noop, _noop, _noop, _ref(None),
        True, False, None,
    )
    assert isinstance(control, ft.Control)


def test_render_frontmatter_real_call():
    """按调用点参数真实调用：YAML 属性卡片不得抛异常。"""
    control = _render_in_component(
        _frontmatter.render_frontmatter,
        _line(BlockType.FRONTMATTER, "---\ntitle: hi\n---"),
        0, 16, 800.0, _ref(None),
        _noop, _noop, _noop, _ref(None), True, False, None,
    )
    assert isinstance(control, ft.Control)


def test_render_math_block_real_call():
    """按调用点参数真实调用：块级公式不得抛异常。"""
    control = _render_in_component(
        lv._render_math_block,
        _line(BlockType.MATH, "$$\nx = 1\n$$"),
        0, 16, 800.0,
        _noop, _noop, _noop, _ref(None),
        False, True, False, None,
    )
    assert isinstance(control, ft.Control)
