"""全局快捷键动作表的单一来源契约测试。

`views/key_bindings.py` 的 `_GLOBAL_ACTIONS` 是「全局窗口级动作」的**唯一**来源，
由两条路径共用：

- `_handle_global_shortcuts`：焦点在编辑器内时执行
- `_handle_foreign_only`：焦点在搜索框 / 对话框等原生输入框时执行

在表出现之前，这两条路径各自维护一份 18 项的重复 if 链，且调用方式不一致
（`open`/`open_folder` 在编辑器内路径直接调用、在外来域路径走 `page.run_task`），
极易出现「新建全局快捷键只加进一条路径 → 在某类输入框里静默失效」。

本测试守护：
1. 表存在且条目字段合法（name / default / style）
2. 两条路径都**遍历表**（而不是重新写一份 if 链）
3. 浮层优先分支（Ctrl+F / Ctrl+Shift+F）在两条路径中都存在且早于表循环
4. 表覆盖了既有的关键全局动作（防误删）
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = ROOT / "views" / "key_bindings.py"
SRC = SRC_PATH.read_text(encoding="utf-8")
LINES = SRC.splitlines()
TREE = ast.parse(SRC)

CLS = next(
    n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "KeyDispatcher"
)

# 必须保留的关键全局动作（防误删）
REQUIRED = {
    "close_tab", "next_tab", "prev_tab", "open", "open_folder", "save", "save_as",
    "new", "open_settings", "toggle_word_wrap", "zoom_in", "zoom_out", "zoom_reset",
    "toggle_split_editor", "toggle_sidebar", "toggle_theme", "toggle_raw",
    "focus_search", "toggle_replace_bar", "replace_current", "replace_all",
    "global_find",
}
VALID_STYLE = {"cb", "task", "raw"}


def _table() -> list[tuple[str, str, str]]:
    node = next(
        n for n in TREE.body
        if isinstance(n, ast.AnnAssign)
        and isinstance(n.target, ast.Name)
        and n.target.id == "_GLOBAL_ACTIONS"
    )
    return [tuple(ast.literal_eval(e) for e in t.elts) for t in node.value.elts]


def _method(name: str) -> ast.FunctionDef:
    return next(m for m in CLS.body if isinstance(m, ast.FunctionDef) and m.name == name)


def _body(name: str) -> str:
    fn = _method(name)
    return "\n".join(LINES[fn.lineno - 1:fn.end_lineno])


def test_table_shape():
    table = _table()
    assert len(table) >= 20, f"动作表条目过少：{len(table)}"
    for entry in table:
        assert len(entry) == 3, f"条目字段数不是 3：{entry}"
        name, default, style = entry
        assert name and default and style in VALID_STYLE, f"非法条目：{entry}"
    names = [e[0] for e in table]
    assert len(names) == len(set(names)), f"动作名重复：{names}"


def test_table_covers_required_actions():
    names = {e[0] for e in _table()}
    missing = sorted(REQUIRED - names)
    assert not missing, f"动作表丢失了关键全局动作：{missing}"


def test_both_paths_iterate_the_table():
    """两条路径都必须遍历 `_GLOBAL_ACTIONS`，不得各自重写 if 链。"""
    for method in ("_handle_global_shortcuts", "_handle_foreign_only"):
        body = _body(method)
        assert "for name, default, style in _GLOBAL_ACTIONS:" in body, (
            f"{method} 未遍历动作表（可能又出现了重复 if 链）"
        )


def test_overlay_branch_precedes_table_in_both_paths():
    """Ctrl+F / Ctrl+Shift+F 的浮层优先分支必须在表循环之前（否则被表项抢先）。"""
    for method in ("_handle_global_shortcuts", "_handle_foreign_only"):
        body = _body(method)
        loop = body.index("for name, default, style in _GLOBAL_ACTIONS:")
        for key in ('"focus_search"', '"global_find"'):
            assert key in body, f"{method} 缺少 {key} 分支"
            assert body.index(key) < loop, f"{method} 的 {key} 分支晚于表循环"
        # 浮层入口必须同时考虑 doc_search_open / global_search
        assert "doc_search_open" in body, f"{method} 未优先使用 doc_search_open"
        assert "global_search" in body, f"{method} 未优先使用 global_search"


def test_unique_shortcut_defaults():
    """表中默认键不应冲突（同一按键映射到两个动作会让其后的永不触发）。"""
    defaults = [e[1] for e in _table()]
    dup = sorted({d for d in defaults if defaults.count(d) > 1})
    assert not dup, f"动作表默认键冲突：{dup}"


def test_no_duplicate_global_if_chains():
    """回归护栏：编辑器内路径不应再出现按 browse_sc 逐个 if 的长链。"""
    body = _body("_handle_global_shortcuts")
    matches_count = len(re.findall(r"matches\(combo, browse_sc\.get\(", body))
    # 仅允许表外保留的少数几项（select_all / focus_search / global_find / format_markdown）
    assert matches_count <= 6, (
        f"_handle_global_shortcuts 中按 browse_sc.get 的匹配有 {matches_count} 处，"
        "疑似重新长出重复 if 链"
    )
