"""首部元数据（YAML frontmatter）行拖拽排序逻辑测试。

覆盖：
- _drag_src_idx：从拖拽事件解析源行索引（行索引挂在 Draggable.data 上，
  替代 id() 注册表——重渲染后 e.src 可能指向旧对象，data 随控件保留）
- _reorder_pairs：源行移动到目标行位置、其余顺移的纯列表逻辑
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from views.line_view import _drag_src_idx, _reorder_pairs

# ---------------- _drag_src_idx ----------------

def _evt(src_data=None):
    """构造带 src 的伪拖拽事件；src=None 模拟 e.src 解析失败。"""
    if src_data is None:
        return types.SimpleNamespace(src=None)
    return types.SimpleNamespace(src=types.SimpleNamespace(data=src_data))


def test_src_idx_reads_draggable_data():
    """e.src.data 为行索引：直接取回。"""
    assert _drag_src_idx(_evt(2), 4) == 2
    assert _drag_src_idx(_evt(0), 4) == 0


def test_src_idx_none_src():
    """e.src 为 None（控件解析失败）：返回 None。"""
    assert _drag_src_idx(_evt(None), 4) is None


def test_src_idx_non_int_data():
    """data 非 int（异常数据）：返回 None。"""
    assert _drag_src_idx(_evt("2"), 4) is None
    assert _drag_src_idx(_evt(None), 4) is None  # src 存在但 data 为 None


def test_src_idx_out_of_range():
    """越界索引（拖拽期间行数变化）：返回 None 防御。"""
    assert _drag_src_idx(_evt(4), 4) is None
    assert _drag_src_idx(_evt(-1), 4) is None
    assert _drag_src_idx(_evt(99), 4) is None


def test_src_idx_empty_pairs():
    """无行时任何索引都无效。"""
    assert _drag_src_idx(_evt(0), 0) is None


# ---------------- _reorder_pairs ----------------

def test_reorder_down():
    """行 0 拖到行 3：移动到目标位置，其余顺移。"""
    pairs = [["a", "1"], ["b", "2"], ["c", "3"], ["d", "4"]]
    assert _reorder_pairs(pairs, 0, 3) == [
        ["b", "2"], ["c", "3"], ["d", "4"], ["a", "1"],
    ]


def test_reorder_up():
    """行 3 拖到行 0。"""
    pairs = [["a", "1"], ["b", "2"], ["c", "3"], ["d", "4"]]
    assert _reorder_pairs(pairs, 3, 0) == [
        ["d", "4"], ["a", "1"], ["b", "2"], ["c", "3"],
    ]


def test_reorder_middle():
    """行 1 拖到行 3。"""
    pairs = [["a", "1"], ["b", "2"], ["c", "3"], ["d", "4"]]
    assert _reorder_pairs(pairs, 1, 3) == [
        ["a", "1"], ["c", "3"], ["d", "4"], ["b", "2"],
    ]


def test_reorder_same_position():
    """拖到自身位置：顺序不变（调用方已拦截，此处防御）。"""
    pairs = [["a", "1"], ["b", "2"]]
    assert _reorder_pairs(pairs, 0, 0) == pairs


def test_reorder_invalid_indices_no_change():
    """越界索引：返回原顺序副本，不抛异常。"""
    pairs = [["a", "1"], ["b", "2"]]
    assert _reorder_pairs(pairs, 5, 0) == pairs
    assert _reorder_pairs(pairs, 0, 5) == pairs
    assert _reorder_pairs(pairs, -1, 0) == pairs


def test_reorder_does_not_mutate_original():
    """返回新列表，不修改原列表（含行对象拷贝）。"""
    pairs = [["a", "1"], ["b", "2"], ["c", "3"]]
    result = _reorder_pairs(pairs, 0, 2)
    assert pairs == [["a", "1"], ["b", "2"], ["c", "3"]]
    result[0][0] = "X"
    assert pairs[0][0] == "a"


def test_reorder_keeps_pending_empty_row():
    """含待定空键行（未写回文档）时同样参与排序。"""
    pairs = [["a", "1"], ["b", "2"], ["", ""]]
    assert _reorder_pairs(pairs, 0, 2) == [
        ["b", "2"], ["", ""], ["a", "1"],
    ]


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
