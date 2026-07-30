"""handle_code_backspace 单元测试（Typora 式空代码块 Backspace 删除）。

直接构造最小 mock ctx 调用 build_fence(ctx)["handle_code_backspace"]，验证：
- 空代码块（含仅空白）→ 返回 True、行替换为空段落、聚焦态清理、光标进入编辑态
- 非空代码块 → 返回 False、document 不变
- 非代码块行 → 返回 False、document 不变
- 越界行号 → 返回 False
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from models import BlockType, Document, Line, Segment, SegType  # noqa: E402
from views.editor._fence import build_fence  # noqa: E402


class FakeRef:
    """伪 ft.Ref：避免 flet.Ref weakref 限制。"""

    def __init__(self, current=None):
        self.current = current


def _make_code_line(code: str, lang: str = "") -> Line:
    """构造 CODE 行：segments[0] 为 CODE 段，text/raw 均为 code 体。"""
    line = Line(block_type=BlockType.CODE, lang=lang)
    line.segments = [Segment(SegType.CODE, code, code)]
    line.raw = f"```{lang}\n{code}\n```"
    return line


def _make_ctx(document: Document) -> tuple[types.SimpleNamespace, list]:
    """构造最小 mock EditorContext，仅含 handle_code_backspace 依赖的槽。"""
    calls: list = []
    ctx = types.SimpleNamespace(
        document=document,
        push_history=lambda: calls.append("push_history"),
        undo_push_pending=FakeRef(True),
        mark_dirty=lambda: calls.append("mark_dirty"),
        code_focus_ref=FakeRef(0),
        code_edit_snapshot=FakeRef(object()),
        code_edit_changed=FakeRef(False),
        suppress_blur=FakeRef(False),
        set_cursor=lambda li, off: calls.append(("set_cursor", li, off)),
    )
    return ctx, calls


def test_empty_code_block_deleted():
    """空代码块 → 返回 True，行替换为空段落，聚焦态清理，进入编辑态。"""
    doc = Document(lines=[_make_code_line("")])
    ctx, calls = _make_ctx(doc)
    handle = build_fence(ctx)["handle_code_backspace"]

    result = handle(0)

    assert result is True
    # 行被替换为空段落
    new_line = doc.lines[0]
    assert new_line.block_type == BlockType.PARAGRAPH
    assert new_line.raw == ""
    assert len(new_line.segments) == 1
    assert new_line.segments[0].seg_type == SegType.TEXT
    # 历史 + 脏标记
    assert "push_history" in calls
    assert "mark_dirty" in calls
    # undo_push_pending 置 True（后续编辑独立入栈）
    assert ctx.undo_push_pending.current is True
    # 代码块聚焦态清理
    assert ctx.code_focus_ref.current is None
    assert ctx.code_edit_snapshot.current is None
    assert ctx.code_edit_changed.current is False
    # suppress_blur 置 True（防 CodeEditor 卸载级联 blur）
    assert ctx.suppress_blur.current is True
    # 光标进入编辑态（行首）
    assert ("set_cursor", 0, 0) in calls


def test_whitespace_only_code_block_deleted():
    """仅空白字符的代码块也视为空 → 删除（Typora 式）。"""
    doc = Document(lines=[_make_code_line("   \n\t  ")])
    ctx, calls = _make_ctx(doc)
    handle = build_fence(ctx)["handle_code_backspace"]

    result = handle(0)

    assert result is True
    assert doc.lines[0].block_type == BlockType.PARAGRAPH


def test_nonempty_code_block_not_deleted():
    """非空代码块 → 返回 False，document 不变，不调用 set_cursor。"""
    code = "print('hello')"
    doc = Document(lines=[_make_code_line(code, "python")])
    ctx, calls = _make_ctx(doc)
    handle = build_fence(ctx)["handle_code_backspace"]

    result = handle(0)

    assert result is False
    # document 未变：仍是 CODE 行且代码体保留
    assert doc.lines[0].block_type == BlockType.CODE
    assert doc.lines[0].segments[0].text == code
    assert doc.lines[0].lang == "python"
    # 未触发历史 / 光标
    assert "push_history" not in calls
    assert not any(c == "set_cursor" or (isinstance(c, tuple) and c[:1] == ("set_cursor",)) for c in calls)


def test_non_code_block_not_deleted():
    """非代码块行（如段落）→ 返回 False，document 不变。"""
    para = Line(block_type=BlockType.PARAGRAPH, raw="hello")
    para.segments = [Segment(SegType.TEXT, "hello", "hello")]
    doc = Document(lines=[para])
    ctx, calls = _make_ctx(doc)
    handle = build_fence(ctx)["handle_code_backspace"]

    result = handle(0)

    assert result is False
    assert doc.lines[0].block_type == BlockType.PARAGRAPH
    assert doc.lines[0].raw == "hello"


def test_out_of_range_returns_false():
    """越界行号 → 返回 False，无副作用。"""
    doc = Document(lines=[_make_code_line("")])
    ctx, calls = _make_ctx(doc)
    handle = build_fence(ctx)["handle_code_backspace"]

    assert handle(-1) is False
    assert handle(5) is False
    assert "push_history" not in calls


def test_replaces_correct_line_preserves_others():
    """多行文档中删除中间空代码块：仅目标行替换，其余行不变。"""
    before = Line(block_type=BlockType.PARAGRAPH, raw="before")
    before.segments = [Segment(SegType.TEXT, "before", "before")]
    after = Line(block_type=BlockType.PARAGRAPH, raw="after")
    after.segments = [Segment(SegType.TEXT, "after", "after")]
    doc = Document(lines=[before, _make_code_line("", "js"), after])
    ctx, calls = _make_ctx(doc)
    # 聚焦中间行（li=1）
    ctx.code_focus_ref.current = 1
    handle = build_fence(ctx)["handle_code_backspace"]

    result = handle(1)

    assert result is True
    assert len(doc.lines) == 3
    # 首尾行不变
    assert doc.lines[0].raw == "before"
    assert doc.lines[2].raw == "after"
    # 中间行替换为空段落
    assert doc.lines[1].block_type == BlockType.PARAGRAPH
    assert doc.lines[1].raw == ""
    # 光标定位到被删行（li=1）
    assert ("set_cursor", 1, 0) in calls


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
