"""应用层控制器装配契约测试（守护 AppContext 同名自动装配）。

``app/__init__.py`` 把各 ``build_xxx(ctx)`` 返回的字典循环写入 ``AppContext``，
依赖「控制器返回 key == ctx 字段名」这一约定。本测试用 AST 静态校验，避免改名
漏改时静默退化成默认 no-op（表现为按钮 / 快捷键无声失效）。

覆盖：
1. 控制器返回的 slot 都存在于 AppContext（拼错即失败）
2. 三类例外 key 与代码中的排除集合保持一致
3. 同一 slot 不被多个控制器重复提供（后者覆盖前者即失败）
4. 装配发生在 ``build_keyboard`` 之前
   —— KeyDispatcher 构造期就把 ctx 回调立即求值进 app_callbacks，顺序错会让
   dispatcher 捕获默认 no-op（本项目曾因此导致 Alt+T / Ctrl+Shift+B 失效）。
"""

import ast
import dataclasses
import re
from pathlib import Path

from app._context import AppContext

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
INIT = APP / "__init__.py"

# 与 AppContext 既有字段同名的控制器 key（改名装配，不可被覆盖）
RENAMED_KEYS = {"cur_tab"}
# 由 __init__ 另行接收、不落 ctx 的控制器输出
UNSTORED_KEYS = {"bind_keyboard", "dispatcher", "autosave_on_exit"}


def _controller_slots() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in sorted(APP.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in tree.body:
            if not isinstance(fn, ast.FunctionDef) or not fn.name.startswith("build_"):
                continue
            keys = {
                k.value
                for node in ast.walk(fn)
                if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
                for k in node.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            if keys:
                out[fn.name] = keys
    return out


FIELDS = {f.name for f in dataclasses.fields(AppContext)}
CONTROLLERS = _controller_slots()
ALL_SLOTS = {s for slots in CONTROLLERS.values() for s in slots}
INIT_SRC = INIT.read_text(encoding="utf-8")


def test_controllers_found():
    """至少应发现全部应用层控制器（防止 AST 提取失效导致测试空转）。"""
    assert len(CONTROLLERS) >= 9, sorted(CONTROLLERS)
    assert len(ALL_SLOTS) >= 70, len(ALL_SLOTS)


def test_controller_slots_are_context_fields():
    """控制器返回的 slot 必须是 AppContext 字段，否则 setattr 会凭空新增属性。

    ``_UNSTORED`` 中的输出由 __init__ 另行接收（dispatcher / bind_keyboard /
    autosave_on_exit），按设计不落 ctx。
    """
    unknown = sorted(ALL_SLOTS - FIELDS - UNSTORED_KEYS)
    assert not unknown, f"控制器返回了 AppContext 未声明的槽位: {unknown}"


def test_no_slot_claimed_by_two_controllers():
    """同一 slot 不应被两个控制器提供：后者覆盖前者会让前者的接线静默失效。"""
    owners: dict[str, list[str]] = {}
    for fn, slots in CONTROLLERS.items():
        for slot in slots:
            owners.setdefault(slot, []).append(fn)
    dup = {slot: fns for slot, fns in owners.items() if len(fns) > 1}
    assert not dup, f"槽位被多个控制器装配: {dup}"


def test_exclusion_sets_documented_in_code():
    """代码里的排除集合必须与本测试声明一致（防止两边漂移）。"""
    for name, expected in (("_RENAMED", RENAMED_KEYS), ("_UNSTORED", UNSTORED_KEYS)):
        m = re.search(rf"{name} = (\[[^\]]*\]|\([^)]*\)|\{{[^}}]*\}})", INIT_SRC)
        assert m, f"未找到 {name} 定义"
        actual = set(ast.literal_eval(m.group(1)))
        assert actual == expected, f"{name} 与契约不一致: {actual} != {expected}"


def test_wiring_runs_before_keyboard_construction():
    """装配循环必须在 build_keyboard 之前执行（dispatcher 构造期立即求值 ctx 回调）。"""
    wiring = INIT_SRC.index("for _slot, _fn in _group.items():")
    keyboard = INIT_SRC.index("keyboard_cbs = build_keyboard(ctx)")
    assert wiring < keyboard, "控制器装配晚于 build_keyboard：dispatcher 会捕获默认 no-op"


def test_cur_tab_fn_explicitly_wired():
    """异名槽位 cur_tab_fn 必须显式装配（cur_tab 与派生值同名，不可被覆盖）。"""
    assert 'ctx.cur_tab_fn = tab_cbs["cur_tab"]' in INIT_SRC
