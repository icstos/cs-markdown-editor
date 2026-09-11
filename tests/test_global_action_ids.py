"""动作表 id 与 ShortcutManager 注册表的一致性测试。

`browse_sc.get(name, default)` 在 `name` 不存在时会**静默回退**到 default。
因此若 `_GLOBAL_ACTIONS` 里的动作 id 拼错（或对应动作没登记进 `ACTION_REGISTRY`），
后果是：用户在该动作上自定义的键位被无声忽略——快捷键看起来"部分失效"，
且没有任何报错。本测试把这类拼写/登记错误变成硬失败。

同时守护相反方向：`_GLOBAL_ACTIONS` 覆盖的动作必须是分层配置里真实存在的键，
否则 `DEFAULT_SHORTCUTS` 与表会漂移。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.shortcuts import ACTION_REGISTRY, DEFAULT_SHORTCUTS  # noqa: E402

from views.key_bindings import _GLOBAL_ACTIONS  # noqa: E402

# 有意不走 ShortcutManager 的项：
# - toggle_raw：由编辑器自身处理（EditorActions.toggle_raw），两层的默认键不同
#   （browse=ctrl+/、edit=ctrl+enter），因此表里只登记动作名与调用方式。
HANDLED_ELSEWHERE = {"toggle_raw"}

REGISTERED = {a.id for a in ACTION_REGISTRY}
CONFIG_KEYS = {
    action_id
    for layer_settings in DEFAULT_SHORTCUTS.values()
    for action_id in layer_settings
}


def test_table_ids_are_known_actions():
    """表内每个动作 id 必须已登记进 ACTION_REGISTRY（否则自定义键位被静默忽略）。"""
    unknown = sorted(
        name for name, _default, _style in _GLOBAL_ACTIONS
        if name not in REGISTERED and name not in HANDLED_ELSEWHERE
    )
    assert not unknown, (
        f"动作表引用了未登记的动作 id：{unknown}。"
        "这些 id 在 ShortcutManager.get() 中会静默回退默认键，用户自定义绑定将失效。"
    )


def test_table_ids_exist_in_default_settings():
    """表内每个动作 id 必须存在于 DEFAULT_SHORTCUTS，否则该层默认键无从取值。"""
    missing = sorted(
        name for name, _default, _style in _GLOBAL_ACTIONS
        if name not in CONFIG_KEYS and name not in HANDLED_ELSEWHERE
    )
    assert not missing, f"动作表引用了分层配置中不存在的动作 id：{missing}"


def test_table_defaults_match_registry_defaults():
    """表内默认键应与 registry 中该动作的 browse 层默认键一致（防两处漂移）。"""
    by_id = {a.id: a for a in ACTION_REGISTRY}
    problems = {}
    for name, default, _style in _GLOBAL_ACTIONS:
        action = by_id.get(name)
        if action is None:
            continue
        registry_default = action.default.get("browse")
        if registry_default is not None and registry_default != default:
            problems[name] = {"表": default, "registry": registry_default}
    assert not problems, f"动作表默认键与 ACTION_REGISTRY 不一致：{problems}"


def test_registered_global_actions_are_in_table():
    """scope 为 both/browse 的全局类动作应在动作表中出现（防新增动作漏接表）。"""
    global_categories = {"文件", "视图", "设置"}
    expected = {
        a.id for a in ACTION_REGISTRY
        if a.category in global_categories and "browse" in a.default
    }
    table_ids = {name for name, _d, _s in _GLOBAL_ACTIONS}
    missing = sorted(expected - table_ids - HANDLED_ELSEWHERE)
    assert not missing, (
        f"以下已登记的全局动作未接入 _GLOBAL_ACTIONS：{missing}。"
        "未接入意味着它们在「焦点在原生输入框」时不会生效。"
    )
