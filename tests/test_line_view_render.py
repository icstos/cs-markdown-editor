"""行视图端到端渲染测试（真实 `LineView` 组件，覆盖全部块类型）。

背景：第 6 轮发现 `_render_code_block` / `_render_frontmatter` 调用 `_wrap_block`
时**漏传必填位置参数 `base`**——而这两个分支只被 `LineView` 内部调用，渲染结果
是 `ft.Control` 无法断言内容，因此**长期零覆盖**，缺陷一直潜伏。

原测试（`test_line_render_branches.py`）验证的是「按调用点参数直接调用子函数」；
本测试更进一步：**构造真实 `LineView` 组件并渲染**，走完整用户路径
（组件渲染 → 分支选择 → 岛渲染 → 块级容器包裹）。任何参数 / 签名不匹配
都会在这里以异常形式暴露，而不是等到用户点开代码块才崩。
"""

import sys
from pathlib import Path

import flet as ft

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser import parse_markdown  # noqa: E402
from tests.harness import RenderHarness  # noqa: E402
from views.line_view import LineView  # noqa: E402

# (说明, Markdown 源码) —— 覆盖全部块类型分支
CASES = [
    ("段落", "普通段落文本"),
    ("一级标题", "# 标题一"),
    ("无序列表", "- 列表项"),
    ("有序列表", "1. 有序项"),
    ("任务列表", "- [x] 已完成"),
    ("引用", "> 引用文本"),
    ("代码块", "```python\nprint(1)\n```"),
    ("块级公式", "$$\nx = 1\n$$"),
    ("分隔线", "---"),
    ("前置元数据", "---\ntitle: hi\ntags: note\n---"),
    ("表格行", "| a | b |\n| --- | --- |\n| 1 | 2 |"),
    ("行内含格式", "**粗** *斜* `代码` [链接](https://x.com)"),
]


def _first_line(md: str):
    doc = parse_markdown(md)
    assert doc.lines, f"解析结果为空：{md!r}"
    return doc.lines[0]


def _render_line_view(md: str, *, active: bool):
    """渲染单个 LineView 组件（真实组件体，含分支选择与块级包裹）。"""
    line = _first_line(md)
    harness = RenderHarness()
    try:
        tree = harness.render(
            lambda: LineView(
                line,
                0,
                cursor_off=0 if active else None,
                content_width=800.0,
                is_current_line=active,
                theme_mode=ft.ThemeMode.LIGHT,
            )
        )
        return tree
    finally:
        harness.dispose()


def test_all_block_types_render_inactive():
    """所有块类型在浏览态都能渲染（无异常）。"""
    failures = {}
    for label, md in CASES:
        try:
            tree = _render_line_view(md, active=False)
            assert tree is not None, "渲染结果为 None"
        except Exception as exc:  # noqa: BLE001 - 汇总所有失败后一次性报告
            failures[label] = f"{type(exc).__name__}: {exc}"
    assert not failures, f"以下块类型渲染失败：{failures}"


def test_all_block_types_render_active():
    """所有块类型在激活态（光标在该行）都能渲染——激活态会走光标层叠加路径。"""
    failures = {}
    for label, md in CASES:
        try:
            tree = _render_line_view(md, active=True)
            assert tree is not None, "渲染结果为 None"
        except Exception as exc:  # noqa: BLE001
            failures[label] = f"{type(exc).__name__}: {exc}"
    assert not failures, f"以下块类型激活态渲染失败：{failures}"


def test_code_block_and_frontmatter_specifically():
    """代码块与前置元数据单独断言（第 6 轮修复的两个分支）。

    这两条分支曾因 `_wrap_block` 漏传 `base` 而必然抛 TypeError；渲染出的控件
    应带 `line-0` key（由块级容器包裹赋予），证明走到了正常分支而非异常兜底。
    """
    for label, md in (("代码块", "```python\nprint(1)\n```"),
                      ("前置元数据", "---\ntitle: hi\n---")):
        tree = _render_line_view(md, active=False)
        keys = _collect_keys(tree)
        assert "line-0" in keys, f"{label}不是由块级容器包裹（keys={sorted(keys)}）"


def _collect_keys(node) -> set[str]:
    """收集渲染树中所有控件的 key（用于确认块级容器已包裹）。"""
    out = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, ft.BaseControl):
            key = getattr(cur, "key", None)
            if isinstance(key, str):
                out.add(key)
            for attr in ("controls", "content", "actions"):
                value = getattr(cur, attr, None)
                if isinstance(value, (list, tuple)):
                    stack.extend(value)
                elif isinstance(value, ft.BaseControl):
                    stack.append(value)
        elif isinstance(cur, (list, tuple)):
            stack.extend(cur)
    return out


def test_every_wrap_block_call_passes_base():
    """AST 守护：所有 `wrap_block` 调用都必须传满前 3 个位置参数。

    `wrap_block(content, line, base, line_idx, ...)` 的 `base` 是**必填**位置参数。
    它被抽取成独立模块时，调用点容易漏传——本项目已两次踩到：
    `_render_code_block` / `_render_frontmatter`（第 6 轮）与代码块主分支
    （第 11 轮）。漏传的后果是渲染该行时抛 `TypeError`，而渲染层无法断言内容，
    所以只能靠这类静态检查兜住。
    """
    import ast
    from pathlib import Path as _Path

    problems = []
    for path in sorted((_Path(__file__).resolve().parent.parent / "views").rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        if "wrap_block(" not in src:
            continue
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (
                fn.attr if isinstance(fn, ast.Attribute)
                else fn.id if isinstance(fn, ast.Name)
                else None
            )
            if name != "wrap_block":
                continue
            passed = len(node.args) >= 3 or any(k.arg == "base" for k in node.keywords)
            if not passed:
                problems.append(f"{path.name}:{node.lineno}")
    assert not problems, f"以下 wrap_block 调用漏传必填的 base 参数：{problems}"
