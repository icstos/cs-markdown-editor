"""应用层控制器依赖契约测试（守护 `app/_contracts.py`）。

与 `tests/test_editor_contracts.py` 同构：控制器的契约字段集合必须严格等于其
函数体实际读取的 ``ctx`` 字段集合，否则契约会退化成会过期的注释。
"""

import ast
import dataclasses
import re
from pathlib import Path

from app._context import AppContext

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
CONTRACTS_SRC = (APP / "_contracts.py").read_text(encoding="utf-8")

FIELDS = {f.name for f in dataclasses.fields(AppContext)}

SIGNATURE_ENV = {
    "build_tab_management": "TabManagementEnv",
    "build_file_io_ops": "FileIoEnv",
    "build_file_dialogs": "FileDialogsEnv",
    "build_diff_controller": "DiffEnv",
    "build_settings_controller": "SettingsEnv",
    "build_split_editor": "SplitEnv",
    "build_focus_router": "FocusRouterEnv",
    "build_backup_controller": "BackupEnv",
    "build_keyboard": "KeyboardEnv",
}

# build_render 按设计需要完整 ctx（构造整棵树），不加契约


def _controller_reads() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in sorted(APP.glob("*.py")):
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


READS = _controller_reads()
CONTRACTS = _contract_fields()


def test_contracts_parsed():
    assert len(CONTRACTS) >= 9, sorted(CONTRACTS)
    assert len(READS) >= 10, sorted(READS)


def test_each_contract_matches_actual_reads():
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
    bad = {n: sorted(f - FIELDS) for n, f in CONTRACTS.items() if f - FIELDS}
    assert not bad, f"契约声明了不存在的字段: {bad}"


def test_context_structurally_satisfies_contracts():
    missing = sorted(set().union(*CONTRACTS.values()) - FIELDS)
    assert not missing, f"AppContext 缺少契约要求的字段: {missing}"


def test_controller_signatures_annotated():
    unannotated: list[str] = []
    for path in sorted(APP.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        for fn in SIGNATURE_ENV:
            m = re.search(rf"^def {fn}\(ctx([^)]*)\)", src, re.M)
            if m and not m.group(1).startswith(": "):
                unannotated.append(f"{path.name}:{fn}")
    assert not unannotated, f"控制器签名缺少契约注解: {unannotated}"


def test_render_has_no_contract():
    """build_render 按设计接收完整 ctx（构造整棵渲染树），不应被加上契约。"""
    assert "build_render" in READS, "未找到 build_render"
    assert "build_render" not in SIGNATURE_ENV
    assert not any("RenderEnv" in name for name in CONTRACTS)
