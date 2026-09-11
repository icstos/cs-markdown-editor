"""编辑器工厂装配契约测试（守护 EditorContext 同名自动装配）。

``views/editor/__init__.py`` 用循环把各 ``build_xxx(ctx)`` 返回的字典写入
``EditorContext``，依赖「工厂返回 key == ctx 字段名」这一约定。本测试用 AST
静态校验该约定，避免改名漏改时静默退化成默认 no-op（表现为按钮/快捷键失效）。

覆盖：
1. 契约集合 ``EditorContext.wiring_slots()`` 全部是真实字段（拼错即失败）
2. 每个工厂返回的 slot 名都落在契约集合内（工厂自行新增槽位即失败）
3. 同一 slot 不被多个工厂重复提供（后者静默覆盖前者即失败）
4. 契约集合中除显式手工装配外，每个槽位都有工厂提供（漏装配即失败）
"""

import ast
import dataclasses
from pathlib import Path

from views.editor._context import EditorContext

ROOT = Path(__file__).resolve().parent.parent
EDITOR = ROOT / "views" / "editor"
CONTEXT = EDITOR / "_context.py"

# 由 __init__.py 直接赋值的槽位（非工厂产物）
HAND_WIRED = frozenset({"mark_dirty", "set_outward_sel"})


def _factory_slots() -> dict[str, set[str]]:
    """工厂函数名 → 它返回字典里的 slot 名集合。"""
    out: dict[str, set[str]] = {}
    for path in sorted(EDITOR.glob("*.py")):
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


FIELDS = {f.name for f in dataclasses.fields(EditorContext)}
SLOTS = set(EditorContext.wiring_slots())
FACTORIES = _factory_slots()
PROVIDED = {s for slots in FACTORIES.values() for s in slots}


def test_factories_found():
    """至少应发现编辑器核心工厂（防止 AST 提取逻辑失效导致测试空转）。"""
    assert len(FACTORIES) >= 15, sorted(FACTORIES)
    assert len(SLOTS) >= 100, len(SLOTS)


def test_wiring_slots_are_real_fields():
    """契约集合必须与 dataclass 字段对齐，防止契约里写了不存在的名字。"""
    assert not (SLOTS - FIELDS), f"契约声明了不存在的字段: {sorted(SLOTS - FIELDS)}"


def test_factory_slots_are_declared():
    """工厂返回的 slot 必须已在契约中登记，否则 setattr 会凭空新增属性。"""
    undeclared = {fn: sorted(s - SLOTS) for fn, s in FACTORIES.items() if s - SLOTS}
    assert not undeclared, f"工厂返回了未登记的槽位: {undeclared}"


def test_no_slot_claimed_by_two_factories():
    """同一槽位不应被两个工厂提供：后者覆盖前者会让前者的接线静默失效。"""
    owners: dict[str, list[str]] = {}
    for fn, slots in FACTORIES.items():
        for slot in slots:
            owners.setdefault(slot, []).append(fn)
    dup = {slot: fns for slot, fns in owners.items() if len(fns) > 1}
    assert not dup, f"槽位被多个工厂装配: {dup}"


def test_every_slot_is_provided():
    """契约集合中的槽位必须都有来源（工厂或显式手工装配），否则回调是默认 no-op。"""
    missing = sorted(SLOTS - PROVIDED - HAND_WIRED)
    assert not missing, f"以下槽位没有任何工厂装配（回调会退化为 no-op）: {missing}"


def test_hand_wired_are_actually_handled():
    """显式手工装配的槽位不应同时出现在工厂输出里（避免重复装配误导）。"""
    assert not (HAND_WIRED & PROVIDED), sorted(HAND_WIRED & PROVIDED)
