"""首部元数据（YAML frontmatter）删除/编辑后 Ctrl+Z 撤销支持测试。

覆盖两个回归点：
- _pairs_to_yaml：写回文档与“外部内容同步判定”共用的序列化口径
  （跳过空键行、两侧空白剥离）
- on_change_code（build_fence）：无论是否聚焦过字段，每次修改都产生
  可撤销历史条目——
  * 聚焦会话：首次修改推入聚焦快照，会话内后续修改合并为一个撤销条目
  * 未聚焦离散操作（×删除 / 拖拽排序 / 粘贴行等）：每次修改独立撤销条目
  * 撤销/重做后聚焦快照被清空：惰性重新捕获，继续编辑仍可撤销
  * 恢复（restoring）期间不推入历史
  * 快照在修改前捕获（撤销恢复的是修改前状态）
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import parser
from core.history import EditHistory, EditorSnapshot
from models import BlockType, Document, Line, Segment, SegType
from views.editor._fence import build_fence
from views.line_view import _pairs_to_yaml, _parse_yaml_pairs


class FakeRef:
    """伪 ft.Ref：避免 flet.Ref weakref 限制。"""

    def __init__(self, current=None):
        self.current = current


def _make_frontmatter_line(content: str) -> Line:
    """构造 FRONTMATTER 行：segments[0] 为内容（SegType.CODE），raw 带 --- 围栏。"""
    line = Line(block_type=BlockType.FRONTMATTER)
    line.segments = [Segment(SegType.CODE, content, content)]
    line.raw = f"---\n{content}\n---" if content else "---\n---"
    return line


def _make_ctx(document: Document) -> tuple[object, list, EditHistory]:
    """构造最小 mock EditorContext：仅含 on_change_code 依赖的槽。

    返回 (ctx, calls, history)。make_snapshot 用 parser.serialize 实现，
    可验证推入历史的快照是否反映修改前状态。
    """
    calls: list = []
    history = EditHistory()

    def _make_snapshot() -> EditorSnapshot:
        calls.append("make_snapshot")
        return EditorSnapshot(
            markdown=parser.serialize(document),
            cursor_li=None,
            cursor_off=0,
            raw_mode=False,
            raw_draft="",
        )

    ctx = types.SimpleNamespace(
        document=document,
        code_focus_ref=FakeRef(None),
        code_edit_snapshot=FakeRef(None),
        code_edit_changed=FakeRef(False),
        restoring=FakeRef(False),
        history_ref=FakeRef(history),
        make_snapshot=_make_snapshot,
        mark_dirty=lambda: calls.append("mark_dirty"),
    )
    return ctx, calls, history


# ---------------- _pairs_to_yaml ----------------

def test_pairs_to_yaml_basic():
    """基本序列化：key: value 逐行拼接。"""
    assert _pairs_to_yaml([["title", "我的文档"], ["tags", "note"]]) == (
        "title: 我的文档\ntags: note"
    )


def test_pairs_to_yaml_skips_empty_key_rows():
    """键为空的行跳过（含待定新增行），全部为空则返回空串。"""
    assert _pairs_to_yaml([["", "无键值"], ["title", "x"]]) == "title: x"
    assert _pairs_to_yaml([["", "无键值"]]) == ""
    assert _pairs_to_yaml([]) == ""


def test_pairs_to_yaml_strips_whitespace():
    """键/值两侧空白剥离（与解析端 _parse_yaml_pairs 对称）。"""
    assert _pairs_to_yaml([["  title ", "  我的文档  "]]) == "title: 我的文档"


def test_pairs_to_yaml_roundtrip_with_parser():
    """序列化 → 解析 可还原（与 _parse_yaml_pairs 配对）。"""
    pairs = [("title", "我的文档"), ("tags", "a, b")]
    assert _parse_yaml_pairs(_pairs_to_yaml(pairs)) == pairs


# ---------------- on_change_code 撤销历史 ----------------

def test_focused_session_first_change_pushes_snapshot():
    """聚焦会话：首次修改推入聚焦快照，会话内第二次修改不重复推入。"""
    doc = Document(lines=[_make_frontmatter_line("title: 我的文档")])
    ctx, _, history = _make_ctx(doc)
    # 模拟 on_code_focus 已捕获聚焦快照
    ctx.code_focus_ref.current = 0
    ctx.code_edit_snapshot.current = ctx.make_snapshot()
    ctx.code_edit_changed.current = False
    on_change = build_fence(ctx)["on_change_code"]

    on_change(0, "title: 新标题")
    on_change(0, "title: 新标题2")

    # 会话内两次修改只入栈一个撤销条目，且快照是修改前状态
    assert len(history.undo) == 1
    assert history.undo[0].markdown == "---\ntitle: 我的文档\n---"
    # 文档内容为最终修改结果
    assert doc.lines[0].segments[0].text == "title: 新标题2"
    assert doc.lines[0].raw == "---\ntitle: 新标题2\n---"


def test_unfocused_delete_row_pushes_history():
    """未聚焦字段直接点 × 删除行：仍产生撤销条目，快照为删除前状态。"""
    doc = Document(lines=[_make_frontmatter_line("title: 我的文档\ntags: note")])
    ctx, _, history = _make_ctx(doc)
    # 从未聚焦：code_focus_ref / code_edit_snapshot 均为 None
    on_change = build_fence(ctx)["on_change_code"]

    on_change(0, "tags: note")

    assert len(history.undo) == 1
    assert history.undo[0].markdown == "---\ntitle: 我的文档\ntags: note\n---"
    assert doc.lines[0].segments[0].text == "tags: note"


def test_unfocused_discrete_operations_each_own_entry():
    """未聚焦的多次离散操作（连续删除两行）：每次修改独立撤销条目。"""
    doc = Document(lines=[_make_frontmatter_line("a: 1\nb: 2\nc: 3")])
    ctx, _, history = _make_ctx(doc)
    on_change = build_fence(ctx)["on_change_code"]

    on_change(0, "b: 2\nc: 3")  # 删 a
    on_change(0, "c: 3")        # 删 b

    assert len(history.undo) == 2
    # 先入栈的是第一次删除前的完整状态
    assert history.undo[0].markdown == "---\na: 1\nb: 2\nc: 3\n---"
    assert history.undo[1].markdown == "---\nb: 2\nc: 3\n---"
    assert doc.lines[0].segments[0].text == "c: 3"


def test_focused_after_undo_lazily_recaptures():
    """撤销后聚焦快照被清空：再次编辑惰性重新捕获，仍可撤销。"""
    doc = Document(lines=[_make_frontmatter_line("title: 我的文档")])
    ctx, _, history = _make_ctx(doc)
    # 模拟 on_code_focus 捕获快照 + 第一次修改入栈
    ctx.code_focus_ref.current = 0
    ctx.code_edit_snapshot.current = ctx.make_snapshot()
    ctx.code_edit_changed.current = False
    on_change = build_fence(ctx)["on_change_code"]
    on_change(0, "title: 新标题")
    assert len(history.undo) == 1

    # 模拟撤销：pop 撤销条目（当前编辑态入重做栈），文档恢复为原内容，
    # _restore_snapshot 清空会话态（聚焦快照/已改标志）
    history.pop_undo(EditorSnapshot(
        markdown="---\ntitle: 新标题\n---",
        cursor_li=None, cursor_off=0, raw_mode=False, raw_draft="",
    ))
    doc.lines = [_make_frontmatter_line("title: 我的文档")]
    ctx.code_edit_snapshot.current = None
    ctx.code_edit_changed.current = False

    # 撤销后继续编辑：惰性重新捕获 → 新的撤销条目
    on_change(0, "title: 再改一次")

    # 撤销栈在 pop 后为空，新条目即撤销后再次编辑的前置状态
    assert len(history.undo) == 1
    assert history.undo[0].markdown == "---\ntitle: 我的文档\n---"
    assert doc.lines[0].segments[0].text == "title: 再改一次"


def test_restoring_does_not_push_history():
    """restoring 期间（撤销/重做恢复中）的修改不推入历史。"""
    doc = Document(lines=[_make_frontmatter_line("title: 我的文档")])
    ctx, _, history = _make_ctx(doc)
    ctx.restoring.current = True
    on_change = build_fence(ctx)["on_change_code"]

    on_change(0, "title: 恢复中的值")

    assert len(history.undo) == 0
    # 修改仍应用（恢复快照本身就是覆盖写文档）
    assert doc.lines[0].segments[0].text == "title: 恢复中的值"


def test_no_change_does_not_push():
    """内容未变化（如 on_change 回显）不推入历史、不修改。"""
    doc = Document(lines=[_make_frontmatter_line("title: 我的文档")])
    ctx, calls, history = _make_ctx(doc)
    ctx.code_focus_ref.current = 0
    on_change = build_fence(ctx)["on_change_code"]

    on_change(0, "title: 我的文档")

    assert len(history.undo) == 0
    assert "make_snapshot" not in calls


def test_non_fence_block_ignored():
    """非 CODE/FRONTMATTER 行：不推入历史、不修改。"""
    para = Line(block_type=BlockType.PARAGRAPH, raw="hello")
    para.segments = [Segment(SegType.TEXT, "hello", "hello")]
    doc = Document(lines=[para])
    ctx, _, history = _make_ctx(doc)
    on_change = build_fence(ctx)["on_change_code"]

    on_change(0, "world")

    assert len(history.undo) == 0
    assert doc.lines[0].raw == "hello"


def test_code_block_unfocused_also_records_history():
    """未聚焦的代码块修改同样产生撤销条目（与 frontmatter 一致）。"""
    code_line = Line(block_type=BlockType.CODE, lang="python")
    code_line.segments = [Segment(SegType.CODE, "print(1)", "print(1)")]
    code_line.raw = "```python\nprint(1)\n```"
    doc = Document(lines=[code_line])
    ctx, _, history = _make_ctx(doc)
    on_change = build_fence(ctx)["on_change_code"]

    on_change(0, "print(2)")

    assert len(history.undo) == 1
    assert history.undo[0].markdown == "```python\nprint(1)\n```"
    assert doc.lines[0].segments[0].text == "print(2)"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
