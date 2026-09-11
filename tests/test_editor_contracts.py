"""编辑器工厂依赖契约测试（守护 `views/editor/_contracts.py`）。

背景：`views/editor/_contracts.py` 为每个 ``build_xxx`` 工厂声明精确依赖契约
（只列该工厂实际读取的 ``ctx`` 字段）。契约必须与实际读取集合严格一致，否则
它就从「依赖清单」退化成「一份会过期的注释」——比没有更糟。

覆盖：
1. 每个工厂都有对应契约，且契约字段集合 == AST 实际读取的 ctx 属性集合
2. 契约声明的字段全部是 EditorContext 的真实字段（拼错即失败）
3. EditorContext 结构性满足全部契约（契约不会漏掉运行期才出现的字段）
4. 每个工厂签名都带契约注解（否则依赖又变回隐式）
"""

import ast
import dataclasses
import re
from pathlib import Path

from views.editor._context import EditorContext

ROOT = Path(__file__).resolve().parent.parent
EDITOR = ROOT / "views" / "editor"
CONTRACTS_SRC = (EDITOR / "_contracts.py").read_text(encoding="utf-8")

FIELDS = {f.name for f in dataclasses.fields(EditorContext)}

# 工厂名 → 契约类名（与 views/editor/_contracts.py 保持一致）
SIGNATURE_ENV = {
    "build_cursor": "CursorEnv",
    "build_history": "HistoryEnv",
    "build_format": "FormatEnv",
    "build_scroll": "ScrollEnv",
    "build_navigation": "NavigationEnv",
    "build_outward": "OutwardEnv",
    "build_indent": "IndentEnv",
    "build_blocks": "BlocksEnv",
    "build_inline_format": "InlineFormatEnv",
    "build_clipboard": "ClipboardEnv",
    "build_fence": "FenceEnv",
    "build_raw_mode": "RawModeEnv",
    "build_focus": "FocusEnv",
    "build_key": "KeyEnv",
    "build_image": "ImageEnv",
    "build_multi_cursor": "MultiCursorEnv",
    "build_replace": "ReplaceEnv",
    "build_actions": "ActionsEnv",
}


def _factory_reads() -> dict[str, set[str]]:
    """各 build_xxx 函数体实际读取的 ctx 属性集合（含内层闭包）。"""
    out: dict[str, set[str]] = {}
    for path in sorted(EDITOR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in tree.body:
            if isinstance(fn, ast.FunctionDef) and fn.name.startswith("build_"):
                out[fn.name] = {
                    n.attr
                    for n in ast.walk(fn)
                    if isinstance(n, ast.Attribute)
                    and isinstance(n.value, ast.Name)
                    and n.value.id == "ctx"
                }
    return out


def _contract_fields() -> dict[str, set[str]]:
    """契约类名 → 其声明的字段名集合（直接解析源码，避免导入未使用告警）。"""
    tree = ast.parse(CONTRACTS_SRC)
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        out[node.name] = {
            stmt.target.id
            for stmt in node.body
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
        }
    return out


READS = _factory_reads()
CONTRACTS = _contract_fields()


def test_contracts_parsed():
    """契约文件必须可解析出全部契约类（防止测试空转）。"""
    assert len(CONTRACTS) >= 18, sorted(CONTRACTS)
    assert len(READS) >= 18, sorted(READS)


def test_each_contract_matches_actual_reads():
    """契约字段集合必须等于工厂实际读取集合（多一个/少一个都失败）。"""
    problems: dict[str, dict[str, list[str]]] = {}
    for fn, cname in SIGNATURE_ENV.items():
        contract = CONTRACTS.get(cname)
        assert contract is not None, f"{fn} 缺少契约 {cname}"
        actual = READS.get(fn, set())
        missing = sorted(actual - contract)
        extra = sorted(contract - actual)
        if missing or extra:
            problems[fn] = {"缺声明": missing, "多余声明": extra}
    assert not problems, f"契约与实现不一致: {problems}"


def test_contract_fields_are_context_fields():
    """契约字段必须是 EditorContext 的真实字段，否则契约描述的是别的东西。"""
    bad = {name: sorted(fields - FIELDS) for name, fields in CONTRACTS.items() if fields - FIELDS}
    assert not bad, f"契约声明了不存在的字段: {bad}"


def test_context_structurally_satisfies_contracts():
    """EditorContext 必须能提供全部契约字段（运行期不再二次校验类型）。"""
    all_declared = set().union(*CONTRACTS.values())
    missing = sorted(all_declared - FIELDS)
    assert not missing, f"EditorContext 缺少契约要求的字段: {missing}"


def test_factory_signatures_annotated():
    """每个工厂签名必须带契约注解（否则依赖又变回隐式）。"""
    unannotated: list[str] = []
    for path in sorted(EDITOR.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        for fn in SIGNATURE_ENV:
            m = re.search(rf"^def {fn}\(ctx([^)]*)\)", src, re.M)
            if m and not m.group(1).startswith(": "):
                unannotated.append(f"{path.name}:{fn}")
    assert not unannotated, f"工厂签名缺少契约注解: {unannotated}"
