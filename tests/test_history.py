"""core/history 单元测试。

覆盖 EditorSnapshot / LineEditSnapshot 不可变性 + EditHistory 的 push 去重、
pop_undo / pop_redo、容量限制、redo 栈清空时机、两类快照不相等。
不依赖 UI 层。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from core.history import EditHistory, EditorSnapshot, LineEditSnapshot  # noqa: E402


# ---------------- 快照构造助手 ----------------
def full_snap(markdown: str = "a", li: int | None = 0, off: int = 0) -> EditorSnapshot:
    return EditorSnapshot(
        markdown=markdown, cursor_li=li, cursor_off=off, raw_mode=False, raw_draft=""
    )


def line_snap(idx: int = 0, raw: str = "x", li: int | None = 0, off: int = 0) -> LineEditSnapshot:
    return LineEditSnapshot(
        line_idx=idx, raw=raw, cursor_li=li, cursor_off=off, raw_mode=False, raw_draft=""
    )


# ---------------- 不可变性 ----------------
def test_editor_snapshot_frozen():
    snap = full_snap()
    with pytest.raises(Exception):
        snap.markdown = "b"  # type: ignore[misc]


def test_line_snapshot_frozen():
    snap = line_snap()
    with pytest.raises(Exception):
        snap.raw = "y"  # type: ignore[misc]


def test_editor_snapshot_slots():
    """slots=True：不能新增属性。

    CPython 3.12 下 frozen+slots 对未知属性抛 TypeError（super() 限制），
    对已知字段抛 FrozenInstanceError（AttributeError 子类）；此处兼容两者。
    """
    snap = full_snap()
    with pytest.raises((AttributeError, TypeError)):
        snap.extra = 1  # type: ignore[attr-defined]


# ---------------- push 去重 ----------------
def test_push_dedup_consecutive_equal():
    h = EditHistory()
    h.push(full_snap("a"))
    h.push(full_snap("a"))
    assert len(h.undo) == 1


def test_push_no_dedup_different():
    h = EditHistory()
    h.push(full_snap("a"))
    h.push(full_snap("b"))
    assert len(h.undo) == 2


def test_push_dedup_only_consecutive():
    """非相邻相同项不去重。"""
    h = EditHistory()
    h.push(full_snap("a"))
    h.push(full_snap("b"))
    h.push(full_snap("a"))
    assert len(h.undo) == 3


def test_push_line_vs_full_never_equal():
    """两类快照字段不同，永不相等（不会误去重）。"""
    h = EditHistory()
    h.push(line_snap(0, "x"))
    h.push(full_snap("x"))
    assert len(h.undo) == 2


def test_push_clears_redo():
    h = EditHistory()
    h.push(full_snap("a"))
    h.pop_undo(full_snap("b"))  # 弹出 a 入 redo，undo 空
    assert len(h.redo) == 1
    h.push(full_snap("c"))  # 新撤销入栈应清空 redo
    assert len(h.redo) == 0
    # pop_undo 已把 a 弹出，push('c') 后 undo 仅 [c]（c 与空栈无去重）
    assert len(h.undo) == 1
    assert h.undo[0].markdown == "c"


# ---------------- pop_undo / pop_redo ----------------
def test_pop_undo_empty_returns_none():
    h = EditHistory()
    assert h.pop_undo(full_snap()) is None


def test_pop_undo_pushes_current_to_redo():
    h = EditHistory()
    h.push(full_snap("a"))
    current = full_snap("b")
    popped = h.pop_undo(current)
    assert popped is not None and popped.markdown == "a"
    assert len(h.undo) == 0
    assert len(h.redo) == 1
    assert h.redo[0] is current


def test_pop_redo_empty_returns_none():
    h = EditHistory()
    assert h.pop_redo(full_snap()) is None


def test_pop_redo_pushes_current_to_undo():
    h = EditHistory()
    h.push(full_snap("a"))
    h.pop_undo(full_snap("b"))  # redo = [b]
    popped = h.pop_redo(full_snap("c"))
    assert popped is not None and popped.markdown == "b"
    assert len(h.redo) == 0
    assert len(h.undo) == 1
    assert h.undo[0] is full_snap("c") or h.undo[0].markdown == "c"


def test_undo_redo_roundtrip():
    """push a → undo → redo 应回到 a。"""
    h = EditHistory()
    h.push(full_snap("a"))
    # 当前是 b，撤销回到 a
    snap_a = h.pop_undo(full_snap("b"))
    assert snap_a is not None and snap_a.markdown == "a"
    # 当前是 a，重做到 b
    snap_b = h.pop_redo(full_snap("a"))
    assert snap_b is not None and snap_b.markdown == "b"
    # redo 栈空，undo 栈 = [a]
    assert len(h.undo) == 1
    assert len(h.redo) == 0


# ---------------- 容量限制 ----------------
def test_capacity_limit_evicts_oldest():
    h = EditHistory(max_size=3)
    for i in range(5):
        h.push(full_snap(str(i)))
    assert len(h.undo) == 3
    # 最旧的 0、1 被丢弃，栈内为 2、3、4
    assert [s.markdown for s in h.undo] == ["2", "3", "4"]


def test_capacity_default_50():
    h = EditHistory()
    assert h._max == 50


def test_capacity_respects_dedup():
    """容量计算在去重之后：连续相同项不占容量。"""
    h = EditHistory(max_size=2)
    h.push(full_snap("a"))
    h.push(full_snap("a"))  # 去重，不入栈
    h.push(full_snap("b"))
    h.push(full_snap("c"))
    assert len(h.undo) == 2
    assert [s.markdown for s in h.undo] == ["b", "c"]


# ---------------- 混合快照栈 ----------------
def test_mixed_snapshot_stack():
    """行级与全文快照可共存于同一栈。"""
    h = EditHistory()
    h.push(line_snap(0, "old_line"))
    h.push(full_snap("full_doc"))
    assert len(h.undo) == 2
    popped = h.pop_undo(full_snap("current"))
    assert isinstance(popped, EditorSnapshot)
    popped2 = h.pop_undo(line_snap(0, "current_line"))
    assert isinstance(popped2, LineEditSnapshot)
    assert popped2.line_idx == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
