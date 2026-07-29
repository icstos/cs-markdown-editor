"""services/shortcuts 单元测试。

覆盖 normalize / matches / ShortcutManager 读取、更新、重置、冲突检测、
行内格式动态映射。不依赖 UI 层。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from services.shortcuts import (  # noqa: E402
    ACTION_REGISTRY,
    DEFAULT_SHORTCUTS,
    ShortcutManager,
    matches,
    normalize,
)


# ---------------- normalize / matches ----------------
def test_normalize_strips_and_lowers():
    assert normalize("  Ctrl+S ") == "ctrl+s"
    assert normalize("Ctrl+Shift+Z") == "ctrl+shift+z"


def test_normalize_comma_alias():
    assert normalize("ctrl+comma") == "ctrl+,"


def test_normalize_escape_alias():
    assert normalize("escape") == "esc"
    assert normalize("ctrl+escape") == "ctrl+esc"


def test_normalize_empty():
    assert normalize("") == ""
    assert normalize(None) == ""


def test_matches_exact():
    assert matches("ctrl+s", "ctrl+s")


def test_matches_comma_symmetric():
    """matches 双侧规范化，ctrl+, 与 ctrl+comma 对称等价。"""
    assert matches("ctrl+comma", "ctrl+,")
    assert matches("ctrl+,", "ctrl+comma")
    assert matches("ctrl+,", "ctrl+,")


def test_matches_escape_symmetric():
    """escape 与 esc 对称等价（_combo 输出 esc，settings 可写 escape）。"""
    assert matches("esc", "escape")
    assert matches("escape", "esc")
    assert matches("esc", "esc")


def test_matches_no_match():
    assert not matches("ctrl+x", "ctrl+s")


# ---------------- ShortcutManager 构造 ----------------
def make_mgr(settings: dict | None = None, captured: list | None = None) -> ShortcutManager:
    """构造 ShortcutManager；update_setting 回调记录到 captured。"""
    settings = settings if settings is not None else {}
    captured = captured if captured is not None else []

    def update_setting(key: str, value: object) -> None:
        settings[key] = value
        captured.append((key, value))

    return ShortcutManager(settings, update_setting)


def test_get_returns_copy():
    """get 返回字典副本，外部修改不影响内部。"""
    mgr = make_mgr()
    d = mgr.get("browse")
    d["save"] = "ctrl+x"
    assert mgr.get("browse")["save"] == "ctrl+s"  # 内部不变


def test_get_missing_layer_returns_empty():
    mgr = make_mgr({})
    assert mgr.get("nonexistent") == {}


def test_get_missing_settings_returns_defaults():
    mgr = make_mgr({})
    assert mgr.get("browse")["save"] == "ctrl+s"


def test_shortcut_reads_single():
    mgr = make_mgr()
    assert mgr.shortcut("browse", "save") == "ctrl+s"
    assert mgr.shortcut("browse", "nonexistent") == ""


def test_layers():
    mgr = make_mgr()
    assert mgr.layers() == ("browse", "edit")


def test_actions_for_layer_both():
    """scope=both 的动作在两层都出现。"""
    mgr = make_mgr()
    browse_ids = {a.id for a in mgr.actions_for_layer("browse")}
    edit_ids = {a.id for a in mgr.actions_for_layer("edit")}
    assert "save" in browse_ids
    assert "save" in edit_ids
    assert "open" in browse_ids  # browse only
    assert "open" not in edit_ids
    assert "format_bold" in edit_ids  # edit only
    assert "format_bold" not in browse_ids


def test_action_def_found():
    mgr = make_mgr()
    a = mgr.action_def("save")
    assert a is not None
    assert a.label == "保存"


def test_action_def_not_found():
    mgr = make_mgr()
    assert mgr.action_def("nope") is None


# ---------------- inline_format_combos ----------------
def test_inline_format_combos_default():
    mgr = make_mgr()
    combos = mgr.inline_format_combos()
    assert combos["ctrl+b"] == "bold"
    assert combos["ctrl+i"] == "italic"
    assert combos["ctrl+u"] == "highlight"
    assert combos["ctrl+`"] == "code"
    assert combos["ctrl+k"] == "link"
    assert combos["ctrl+m"] == "inline_math"
    assert combos["ctrl+shift+s"] == "strike"


def test_inline_format_combos_respects_user_custom():
    """用户自定义 format_bold 键位后，映射应反映新键位。"""
    settings = {"shortcuts": {"edit": {**DEFAULT_SHORTCUTS["edit"], "format_bold": "ctrl+shift+b"}}}
    mgr = make_mgr(settings)
    combos = mgr.inline_format_combos()
    assert combos.get("ctrl+shift+b") == "bold"
    assert "ctrl+b" not in combos


def test_inline_format_combos_skips_unbound():
    """未绑定的行内格式动作不出现在映射中。"""
    settings = {"shortcuts": {"edit": {**DEFAULT_SHORTCUTS["edit"], "format_bold": ""}}}
    mgr = make_mgr(settings)
    combos = mgr.inline_format_combos()
    assert "ctrl+b" not in combos


# ---------------- update / reset ----------------
def test_update_writes_via_callback():
    captured: list = []
    mgr = make_mgr(captured=captured)
    mgr.update("edit", "format_bold", "ctrl+shift+b")
    assert captured == [("shortcuts", mgr._settings["shortcuts"])]
    assert mgr.shortcut("edit", "format_bold") == "ctrl+shift+b"


def test_reset_restores_default_for_action():
    mgr = make_mgr()
    mgr.update("edit", "format_bold", "ctrl+shift+b")
    mgr.reset("edit", "format_bold")
    assert mgr.shortcut("edit", "format_bold") == "ctrl+b"


def test_reset_unknown_action_noop():
    mgr = make_mgr()
    mgr.reset("edit", "nonexistent")  # 不抛异常


def test_reset_all_restores_all():
    mgr = make_mgr()
    mgr.update("edit", "format_bold", "ctrl+x")
    mgr.update("browse", "save", "ctrl+x")
    mgr.reset_all()
    assert mgr.shortcut("edit", "format_bold") == "ctrl+b"
    assert mgr.shortcut("browse", "save") == "ctrl+s"


# ---------------- 冲突检测 ----------------
def test_conflicts_none_by_default():
    mgr = make_mgr()
    assert mgr.conflicts("browse") == []
    assert mgr.conflicts("edit") == []


def test_conflicts_detects_duplicate_combo():
    settings = {"shortcuts": {"browse": {**DEFAULT_SHORTCUTS["browse"], "open": "ctrl+s"}}}
    mgr = make_mgr(settings)
    conflicts = mgr.conflicts("browse")
    assert len(conflicts) == 1
    combo, a, b = conflicts[0]
    assert combo == "ctrl+s"
    assert {a, b} == {"save", "open"}


def test_conflict_summary_none():
    mgr = make_mgr()
    assert mgr.conflict_summary("browse") is None


def test_conflict_summary_text():
    settings = {"shortcuts": {"browse": {**DEFAULT_SHORTCUTS["browse"], "open": "ctrl+s"}}}
    mgr = make_mgr(settings)
    summary = mgr.conflict_summary("browse")
    assert summary is not None
    assert "ctrl+s" in summary


def test_conflict_summary_truncates():
    """超过 3 项冲突显示「等 N 项」。"""
    sc = {**DEFAULT_SHORTCUTS["browse"]}
    # 让 4 个动作都与 save 冲突（产生 4 项冲突，超过 [:3] 截断阈值）
    sc["open"] = "ctrl+s"
    sc["new"] = "ctrl+s"
    sc["close_tab"] = "ctrl+s"
    sc["next_tab"] = "ctrl+s"
    settings = {"shortcuts": {"browse": sc}}
    mgr = make_mgr(settings)
    summary = mgr.conflict_summary("browse")
    assert summary is not None
    assert "等" in summary


def test_first_conflict_target_none():
    mgr = make_mgr()
    assert mgr.first_conflict_target() == (None, None)


def test_first_conflict_target_found():
    settings = {"shortcuts": {"browse": {**DEFAULT_SHORTCUTS["browse"], "open": "ctrl+s"}}}
    mgr = make_mgr(settings)
    layer, action_id = mgr.first_conflict_target()
    assert layer == "browse"
    assert action_id in {"save", "open"}


def test_conflict_map_groups():
    sc = {**DEFAULT_SHORTCUTS["browse"]}
    sc["open"] = "ctrl+s"
    sc["new"] = "ctrl+s"
    settings = {"shortcuts": {"browse": sc}}
    mgr = make_mgr(settings)
    cmap = mgr.conflict_map("browse")
    assert "ctrl+s" in cmap
    assert len(cmap["ctrl+s"]) >= 2


# ---------------- ACTION_REGISTRY 完整性 ----------------
def test_action_registry_ids_unique():
    ids = [a.id for a in ACTION_REGISTRY]
    assert len(ids) == len(set(ids))


def test_action_registry_defaults_match_default_shortcuts():
    """注册表中声明的默认值应与 DEFAULT_SHORTCUTS 一致。"""
    for a in ACTION_REGISTRY:
        for layer, combo in a.default.items():
            assert DEFAULT_SHORTCUTS[layer][a.id] == combo, f"{a.id}.{layer}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
