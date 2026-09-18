"""快捷键「注册 → 分发」闭环一致性护栏。

## 为什么需要这个文件

快捷键在项目里有**三处来源**，任何一处漂移都会造成「用户按了没反应」：

1. `services/shortcuts.py` 的 `ACTION_REGISTRY` —— 「设置 → 快捷键」面板的数据源，
   面板会为每一项渲染**可编辑的键位框**；
2. `DEFAULT_SHORTCUTS` —— 两层默认键位；
3. `views/key_bindings.py` 的分发实现（动作表 + 各 `matches(combo, ...)` 分支）。

历史上这三处漂移出过三类真实缺陷，本文件把它们各自锁死：

- **A 幽灵动作**：注册了 `delete_line` / `copy_rich` / `format_underline` /
  `insert_image` / `clear_format` 等动作，面板里显示「Ctrl+U 下划线」并可自定义，
  但代码里既无实现也无分发分支 → 按下去静默无反应。
- **B 键位漂移**：`toggle_word_wrap` 在 README / 状态栏提示 / 上下文菜单三处都写
  `Alt+Z`（VSCode 约定），实际却绑定在 `Ctrl+Shift+R` → 用户按 Alt+Z 无效。
- **C 分层自定义被忽略**：全局窗口级动作统一在 layer 判定之前按**浏览层**配置匹配，
  于是「设置 → 快捷键」里编辑态那行改的键位被静默忽略。

## 不变量

- 注册表里的**每个**动作都必须被 `views/key_bindings.py` 引用（有分发路径）；
- `DEFAULT_SHORTCUTS` 与 `ACTION_REGISTRY` 的键集合、默认值必须**双向**一致；
- 全局动作必须同时认浏览层与编辑层配置的键位，且空键位不得匹配纯修饰键事件。
"""

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.shortcuts import (  # noqa: E402
    ACTION_REGISTRY,
    DEFAULT_SHORTCUTS,
    _INLINE_FORMAT_ACTIONS,
    normalize,
)
from tests.test_key_bindings import (  # noqa: E402
    evt,
    make_actions,
    make_dispatcher,
)
from views.key_bindings import _GLOBAL_ACTIONS  # noqa: E402

SRC_PATH = Path(__file__).resolve().parent.parent / "views" / "key_bindings.py"
SRC = SRC_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SRC)

REGISTRY_IDS = {a.id for a in ACTION_REGISTRY}
CONFIG_IDS = {
    action_id for layer in DEFAULT_SHORTCUTS.values() for action_id in layer
}

# 历史上被移除的幽灵动作。它们**未被实现**，因此不允许重新出现在注册表里；
# 要恢复必须先补上 EditorActions 实现 + key_bindings 分发分支。
_REMOVED_GHOSTS = (
    "delete_line",
    "copy_rich",
    "format_underline",
    "insert_image",
    "clear_format",
    "format_h1",
    "format_h2",
    "format_h3",
    "format_paragraph",
    "format_quote",
    "format_code_block",
)


def _global_table_ids() -> set[str]:
    node = next(
        n for n in TREE.body
        if isinstance(n, ast.AnnAssign)
        and isinstance(n.target, ast.Name)
        and n.target.id == "_GLOBAL_ACTIONS"
    )
    return {ast.literal_eval(t.elts[0]) for t in node.value.elts}


def _string_literals() -> set[str]:
    """`views/key_bindings.py` 里出现的所有字符串字面量（含 .get("id", ...) 的目标）。"""
    return {
        n.value for n in ast.walk(TREE)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


# ---------------- A. 无幽灵动作 ----------------

def test_no_ghost_actions_in_registry():
    """已移除的幽灵动作不得重新出现在 ACTION_REGISTRY 中。"""
    back = sorted(set(_REMOVED_GHOSTS) & REGISTRY_IDS)
    assert not back, (
        f"这些动作没有实现与分发分支，重新登记只会让设置面板显示「可按却没反应」的键位：{back}"
    )


def test_every_registered_action_is_wired():
    """注册动作必须被分发层引用——否则设置面板在为一个不存在的功能提供改键入口。"""
    referenced = _string_literals() | set(_INLINE_FORMAT_ACTIONS) | _global_table_ids()
    unwired = sorted(REGISTRY_IDS - referenced)
    assert not unwired, (
        f"这些动作已登记进 ACTION_REGISTRY，但 views/key_bindings.py 中没有任何引用，"
        f"按快捷键不会有任何反应：{unwired}"
    )


def test_default_shortcuts_have_no_orphan_keys():
    """DEFAULT_SHORTCUTS 的每个键都必须对应一个已登记动作（防删除动作后留孤儿键）。"""
    orphans = sorted(CONFIG_IDS - REGISTRY_IDS)
    assert not orphans, f"分层配置里存在无对应动作的孤儿键（会被冲突检测误报）：{orphans}"


def test_registry_and_defaults_agree_both_ways():
    """registry.default 与 DEFAULT_SHORTCUTS 必须双向一致。

    - registry 声明了某层默认键 → DEFAULT_SHORTCUTS 同层同值；
    - DEFAULT_SHORTCUTS 有某层键位 → registry 必须声明同层同值（否则 `reset()`
      会把键位还原成一个 registry 不认识的值）。
    """
    problems: dict[str, dict[str, str]] = {}
    for action in ACTION_REGISTRY:
        bound = {lay: DEFAULT_SHORTCUTS[lay].get(action.id, "")
                 for lay in ("browse", "edit")}
        for layer in ("browse", "edit"):
            declared = action.default.get(layer)
            config = bound[layer]
            if declared is None and config:
                problems.setdefault(action.id, {})[layer] = (
                    f"配置有 {config!r}，registry 未声明"
                )
            elif declared is not None and declared != config:
                problems.setdefault(action.id, {})[layer] = (
                    f"registry={declared!r} 配置={config!r}"
                )
    assert not problems, f"registry 与 DEFAULT_SHORTCUTS 不一致：{problems}"


def test_no_duplicate_default_combos_per_layer():
    """同层默认键不得重复（后命中的动作将永远无法触发）。"""
    for layer in ("browse", "edit"):
        seen: dict[str, str] = {}
        dups: list[str] = []
        for action_id, combo in DEFAULT_SHORTCUTS[layer].items():
            norm = normalize(combo)
            if not norm:
                continue
            if norm in seen:
                dups.append(f"{norm}: {seen[norm]} / {action_id}")
            else:
                seen[norm] = action_id
        assert not dups, f"[{layer}] 默认键冲突：{dups}"


# ---------------- B. Alt+Z 自动换行（用户报告的 BUG）----------------

def test_toggle_word_wrap_bound_to_alt_z():
    """自动换行必须绑定 Alt+Z（VSCode 约定，与 README / 状态栏 / 菜单一致）。"""
    assert normalize(DEFAULT_SHORTCUTS["browse"]["toggle_word_wrap"]) == "alt+z"
    assert normalize(DEFAULT_SHORTCUTS["edit"]["toggle_word_wrap"]) == "alt+z"

    action = next(a for a in ACTION_REGISTRY if a.id == "toggle_word_wrap")
    assert normalize(action.default["browse"]) == "alt+z"
    assert normalize(action.default["edit"]) == "alt+z"

    table = {name: default for name, default, _style in _GLOBAL_ACTIONS}
    assert normalize(table["toggle_word_wrap"]) == "alt+z"


@pytest.mark.parametrize("layer_state", ["browse", "edit"])
def test_alt_z_fires_in_both_layers(layer_state):
    """Alt+Z 在浏览态与编辑态都必须触发自动换行。"""
    calls: list = []
    actions = make_actions(calls, cursor_li=0 if layer_state == "edit" else None)
    app_calls: list = []
    d, app_calls, _ = make_dispatcher(actions, app_calls)
    d.handle(evt("z", alt=True))
    assert "toggle_word_wrap" in app_calls


def test_alt_z_fires_when_native_input_focused():
    """焦点在原生输入框（搜索/替换/对话框）时 Alt+Z 仍生效（全局窗口级动作）。"""
    app_calls: list = []
    d, app_calls, _ = make_dispatcher(None, app_calls, foreign=True)
    d.handle(evt("z", alt=True))
    assert "toggle_word_wrap" in app_calls


# ---------------- C. 全局动作必须认两层配置 ----------------

def _edit_override(action_id: str, combo: str) -> dict:
    edit = {**DEFAULT_SHORTCUTS["edit"], action_id: combo}
    return {"browse": dict(DEFAULT_SHORTCUTS["browse"]), "edit": edit}


def test_global_action_honours_edit_layer_binding():
    """只改「编辑态」那行的键位，编辑态必须生效。

    全局窗口级动作在 layer 判定之前按配置匹配；若只读浏览层配置，编辑态的改键
    会被静默忽略（面板显示新键位、按下去没反应）。
    """
    app_calls: list = []
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, app_calls, _ = make_dispatcher(
        actions, app_calls, shortcuts=_edit_override("toggle_word_wrap", "ctrl+alt+z")
    )
    d.handle(evt("z", ctrl=True, alt=True))
    assert "toggle_word_wrap" in app_calls, "编辑态自定义键位未被识别"


def test_global_action_honours_browse_layer_binding():
    """只改「浏览态」那行的键位，浏览态必须生效。"""
    app_calls: list = []
    d, app_calls, _ = make_dispatcher(
        None,
        app_calls,
        shortcuts=_edit_override("toggle_word_wrap", "ctrl+alt+z"),
    )
    d.handle(evt("z", ctrl=True, alt=True))
    assert "toggle_word_wrap" in app_calls


def test_global_action_default_still_works_after_override():
    """在某层改键不应让该动作的默认键在另一层失效（并集匹配语义）。"""
    app_calls: list = []
    calls: list = []
    actions = make_actions(calls, cursor_li=0)
    d, app_calls, _ = make_dispatcher(
        actions, app_calls, shortcuts=_edit_override("toggle_word_wrap", "ctrl+alt+z")
    )
    d.handle(evt("z", alt=True))
    assert "toggle_word_wrap" in app_calls, "默认键在编辑态被自定义键挤掉了"


def test_global_action_edit_override_works_in_foreign_focus():
    """外部输入焦点域同样认两层配置（两条路径共用动作表）。"""
    app_calls: list = []
    d, app_calls, _ = make_dispatcher(
        None,
        app_calls,
        foreign=True,
        shortcuts=_edit_override("toggle_word_wrap", "ctrl+alt+z"),
    )
    d.handle(evt("z", ctrl=True, alt=True))
    assert "toggle_word_wrap" in app_calls


# ---------------- 空键位不得匹配纯修饰键 ----------------

def test_global_targets_discards_empty_combo():
    """`_global_targets` 必须剔除空串键位。

    `matches("", "")` 为真，而纯修饰键事件（单按 Ctrl/Shift/Alt）的 combo 正是空串；
    若保留空键位，动作会在每次按修饰键时误触发。
    """
    app_calls: list = []
    d, _app, _ = make_dispatcher(None, app_calls)
    # 把某动作在两层都清空，同时把表内默认键也清空，逼出「空目标」场景
    empty = {lay: {**DEFAULT_SHORTCUTS[lay], "toggle_word_wrap": ""}
             for lay in ("browse", "edit")}
    d._shortcut_mgr = type(d._shortcut_mgr)({"shortcuts": empty}, lambda k, v: None)
    targets = d._global_targets("toggle_word_wrap", "")
    assert "" not in targets
    assert targets == set()


def test_pure_modifier_key_does_not_trigger_global_actions():
    """单按修饰键不得触发任何全局动作。"""
    app_calls: list = []
    d, app_calls, fake_page = make_dispatcher(None, app_calls)
    for key in ("control", "shift", "alt", "meta"):
        d.handle(evt(key, ctrl=(key == "control"), shift=(key == "shift"),
                      alt=(key == "alt")))
    assert app_calls == [], f"纯修饰键误触发：{app_calls}"
    assert fake_page.tasks == [], "纯修饰键误调度了协程动作"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
