"""core/cursor 单元测试。

覆盖 CursorState 默认值、reset 原子重置、slots 约束。
不依赖 UI 层。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from core.cursor import CursorState  # noqa: E402


# ---------------- 默认值 ----------------
def test_default_values():
    c = CursorState()
    assert c.base == 0
    assert c.extent == 0
    assert c.draft_len == 0


# ---------------- reset ----------------
def test_reset_sets_base_and_extent_equal():
    """reset：base == extent（无选区，单一光标位置）。"""
    c = CursorState()
    c.reset(5, 10)
    assert c.base == 5
    assert c.extent == 5
    assert c.draft_len == 10


def test_reset_overwrites_previous_selection():
    """reset 应覆盖既有选区（extent != base 的情况）。"""
    c = CursorState()
    c.base = 3
    c.extent = 8
    c.draft_len = 10
    c.reset(6, 12)
    assert c.base == 6
    assert c.extent == 6  # 选区被清除
    assert c.draft_len == 12


def test_reset_zero():
    c = CursorState()
    c.reset(0, 0)
    assert c.base == 0
    assert c.extent == 0
    assert c.draft_len == 0


def test_reset_preserves_object_identity():
    """reset 是原地修改，不返回新对象（ref 持有同一实例）。"""
    c = CursorState()
    same = c
    c.reset(1, 2)
    assert same is c
    assert same.base == 1


# ---------------- slots ----------------
def test_slots_disallow_new_attrs():
    c = CursorState()
    with pytest.raises(AttributeError):
        c.extra = 1  # type: ignore[attr-defined]


def test_slots_has_expected_fields():
    c = CursorState()
    # slots 实例无 __dict__
    assert not hasattr(c, "__dict__")
    # 可访问 __slots__
    assert {"base", "extent", "draft_len"} <= set(CursorState.__slots__)


# ---------------- 可变语义（非 frozen）----------------
def test_mutable_individual_fields():
    """CursorState 非 frozen：可单独改 base/extent（on_selection_change 用）。"""
    c = CursorState()
    c.base = 3
    c.extent = 7
    c.draft_len = 10
    assert c.base == 3
    assert c.extent == 7
    assert c.draft_len == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
