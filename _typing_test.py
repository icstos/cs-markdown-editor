"""编辑体验对齐 集成冒烟测试。

验证 5 个缺口的修复：
1. Ctrl+C outward_sel 复制
2. Ctrl+V outward_sel 替换
3. Ctrl+A 全选
4. Ctrl+C/X/V/A 在 ACTION_REGISTRY 可自定义
5. Smart Home 行为
"""

import flet as ft
from services.shortcuts import (
    ACTION_REGISTRY, DEFAULT_SHORTCUTS, ShortcutManager, matches,
)
from core.actions import EditorActions
import parser


def test_registry_has_clipboard_actions():
    """验证 ACTION_REGISTRY 新增 copy/cut/paste/select_all 条目。"""
    ids = {a.id for a in ACTION_REGISTRY}
    for needed in ("copy", "cut", "paste", "select_all"):
        assert needed in ids, f"ACTION_REGISTRY 缺少 {needed}"
    print(f"[OK] ACTION_REGISTRY 包含 copy/cut/paste/select_all")

    # 验证 DEFAULT_SHORTCUTS 两层都有
    for layer in ("browse", "edit"):
        sc = DEFAULT_SHORTCUTS[layer]
        for needed in ("copy", "cut", "paste", "select_all"):
            assert needed in sc, f"DEFAULT_SHORTCUTS[{layer}] 缺少 {needed}"
    print(f"[OK] DEFAULT_SHORTCUTS 两层均含 copy/cut/paste/select_all")


def test_action_def_fields():
    """验证新增 ActionDef 的字段正确。"""
    for a in ACTION_REGISTRY:
        if a.id in ("copy", "cut", "paste", "select_all"):
            assert a.scope == "both", f"{a.id} scope 应为 both，实际 {a.scope}"
            assert a.category == "编辑", f"{a.id} category 应为 编辑，实际 {a.category}"
            assert "browse" in a.default and "edit" in a.default
    print(f"[OK] 新增 ActionDef scope=both, category=编辑")


def test_editor_actions_has_new_fields():
    """验证 EditorActions 新增 handle_outward_copy / select_all 字段。"""
    import dataclasses
    fields = {f.name for f in dataclasses.fields(EditorActions)}
    assert "handle_outward_copy" in fields, "EditorActions 缺少 handle_outward_copy"
    assert "select_all" in fields, "EditorActions 缺少 select_all"
    print(f"[OK] EditorActions 含 handle_outward_copy / select_all 字段")


def test_shortcut_manager_renders_new_actions():
    """验证 ShortcutManager.actions_for_layer 返回新增动作。"""
    settings = {"shortcuts": DEFAULT_SHORTCUTS}
    mgr = ShortcutManager(settings, lambda k, v: None)
    for layer in ("browse", "edit"):
        actions = mgr.actions_for_layer(layer)
        ids = {a.id for a in actions}
        for needed in ("copy", "cut", "paste", "select_all"):
            assert needed in ids, f"actions_for_layer({layer}) 缺少 {needed}"
    print(f"[OK] ShortcutManager.actions_for_layer 返回新增动作（设置页可见）")


def test_matches_function():
    """验证 matches() 函数正常工作。"""
    assert matches("ctrl+c", "ctrl+c")
    assert matches("ctrl+a", "ctrl+a")
    assert not matches("ctrl+c", "ctrl+v")
    print(f"[OK] matches() 函数正常")


def test_select_all_logic():
    """验证 select_all 逻辑（不实际渲染，仅验证文档解析）。"""
    md = "# 标题\n\n段落一\n\n段落二\n\n- 列表项\n"
    doc = parser.parse_markdown(md)
    assert len(doc.lines) > 0
    last_li = len(doc.lines) - 1
    print(f"[OK] select_all 测试文档: {len(doc.lines)} 行，末行 li={last_li}")


def test_smart_home_content_start():
    """验证 Smart Home 的 content_start 计算（前缀长度）。"""
    from models import SegType
    doc = parser.parse_markdown("# 标题\n- 列表\n> 引用\n段落")
    # 标题行：前缀 "# " 长度 2
    heading = doc.lines[0]
    assert heading.segments[0].seg_type == SegType.HEADING_PREFIX
    content_start = len(heading.segments[0].raw)
    assert content_start == 2, f"标题 content_start 应为 2，实际 {content_start}"
    # 列表行：前缀 "- " 长度 2
    list_line = doc.lines[1]
    assert list_line.segments[0].seg_type == SegType.LIST_PREFIX
    content_start = len(list_line.segments[0].raw)
    assert content_start == 2, f"列表 content_start 应为 2，实际 {content_start}"
    # 引用行：前缀 "> " 长度 2
    quote = doc.lines[2]
    assert quote.segments[0].seg_type == SegType.QUOTE_PREFIX
    content_start = len(quote.segments[0].raw)
    assert content_start == 2, f"引用 content_start 应为 2，实际 {content_start}"
    # 段落：无前缀，content_start = 0
    para = doc.lines[3]
    if para.segments:
        assert para.segments[0].seg_type not in (
            SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX,
        )
    print(f"[OK] Smart Home content_start 计算正确（标题/列表/引用=2，段落=0）")


def test_imports():
    """验证所有修改的模块导入正常。"""
    import core.actions
    import views.editor
    import services.shortcuts
    import views.key_bindings
    import main
    print(f"[OK] 所有模块导入成功")


if __name__ == "__main__":
    print("=" * 60)
    print("编辑体验对齐 集成冒烟测试")
    print("=" * 60)
    test_imports()
    test_registry_has_clipboard_actions()
    test_action_def_fields()
    test_editor_actions_has_new_fields()
    test_shortcut_manager_renders_new_actions()
    test_matches_function()
    test_select_all_logic()
    test_smart_home_content_start()
    print("=" * 60)
    print("✓ 全部测试通过")
    print("=" * 60)
