"""`on_table_op` 的**文档级**测试（views/editor/_fence.py）。

为什么单独一个文件：`tests/test_table_view_native.py` 只盯到"视图把 op 发出去"这一
层，op 发出之后文档到底有没有被改，此前**一行断言都没有**。于是把 `_fence.py` 里
任何一支 op 的实现删掉（例如本轮新增的 `delete_table` 分支），整个测试套件依然全绿，
而真机上表现为"点了没反应" —— 这正是最该被钉死的一类缺陷。

本文件驱动真实 handler（`build_fence(ctx)["on_table_op"]`），逐支 op 断言
"文档变成了什么样" + "收尾动作有没有做"：
- delete_table：整表被替换为**一行空段落**（多行结构删干净会让光标无处可落），
  聚焦态清理、suppress_blur、光标落到承接行
- delete_row / clear_row：表头与分隔行不受影响；只剩一行数据时不清空
- add_row / add_col：新行/新列的形状（含分隔行插 `---` 而非空串）
- delete_col：每一行（含表头、分隔行）都少一列
- set_align：只写分隔行，语义字符串转成 Markdown 标记
- 每支 op 恰好 push_history 一次（Ctrl+Z 能整体还原）

调用参数一律与视图侧的真实调用**同形**（见 views/table_view.py 的
`_do_*` 与 `_cell_context_items`），否则测的是另一种调用方式，等于没测。
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.document import BlockType, Document, SegType
from parser import parse_markdown
from utils.table_helpers import is_table_separator, join_row, split_row
from views.editor._fence import build_fence

TABLE_TEXT = (
    "前言段落\n\n"
    "| 名称 | 类型 | 说明 |\n"
    "| --- | :---: | ---: |\n"
    "| Alpha | 服务 | 运行中 |\n"
    "| Beta | 组件 | 已停止 |\n"
    "\n"
    "结尾段落\n"
)
# parse_markdown 保留分隔行 → 表 = 表头 + 分隔 + 2 数据 = 4 行
TABLE_SPAN = 4
COL_COUNT = 3


class FakeRef:
    """伪 ft.Ref：避免 flet.Ref 的 weakref 限制。"""

    def __init__(self, current=None):
        self.current = current


def _make_ctx(document: Document) -> tuple[types.SimpleNamespace, list]:
    """最小 mock EditorContext：只含 on_table_op 用到的槽。"""
    calls: list = []
    ctx = types.SimpleNamespace(
        document=document,
        push_history=lambda: calls.append("push_history"),
        undo_push_pending=FakeRef(False),
        mark_dirty=lambda: calls.append("mark_dirty"),
        suppress_blur=FakeRef(False),
        set_table_focus_li=lambda li: calls.append(("set_table_focus_li", li)),
        set_cursor=lambda li, off: calls.append(("set_cursor", li, off)),
    )
    return ctx, calls


def _doc() -> Document:
    return parse_markdown(TABLE_TEXT)


def _table_span(doc: Document) -> tuple[int, int]:
    """第一张表的 [start, end]（含），并断言它就是期望的 4 行。"""
    starts = [i for i, ln in enumerate(doc.lines) if ln.block_type == BlockType.TABLE]
    assert starts, "夹具里没有表格行"
    ts = starts[0]
    te = ts
    while te + 1 < len(doc.lines) and doc.lines[te + 1].block_type == BlockType.TABLE:
        te += 1
    assert te - ts + 1 == TABLE_SPAN, f"表格行数异常：{te - ts + 1}"
    return ts, te


def _op(doc: Document, op: str, params: dict):
    ctx, calls = _make_ctx(doc)
    build_fence(ctx)["on_table_op"](op, params)
    return ctx, calls


def _table_lines(doc: Document) -> list:
    return [ln for ln in doc.lines if ln.block_type == BlockType.TABLE]


# ---------------------------------------------------------------------------
# delete_table：本轮新增的能力
# ---------------------------------------------------------------------------


def test_delete_table_replaces_table_with_one_blank_paragraph():
    """整表删除 → 原位留**一行空段落**承接光标，前后段落原样保留。"""
    doc = _doc()
    before = len(doc.lines)
    ts, _te = _table_span(doc)
    ctx, calls = _op(doc, "delete_table", {"table_start": ts})

    # 4 行表 → 1 行空段落
    assert len(doc.lines) == before - TABLE_SPAN + 1, [ln.raw for ln in doc.lines]
    assert not _table_lines(doc), "仍残留 TABLE 行"

    line = doc.lines[ts]
    assert line.block_type == BlockType.PARAGRAPH, line.block_type
    assert line.raw == ""
    assert len(line.segments) == 1 and line.segments[0].seg_type == SegType.TEXT

    # 邻行不受牵连：原位置只留下 1 行空段落，两侧仍是原来的空行
    assert doc.lines[ts - 1].block_type == BlockType.BLANK
    assert doc.lines[ts + 1].block_type == BlockType.BLANK
    assert any(ln.raw == "前言段落" for ln in doc.lines)
    assert any(ln.raw == "结尾段落" for ln in doc.lines)

    # 收尾三件套：清聚焦态（否则 Tab / 方向键仍被路由给已消失的表）
    assert ("set_table_focus_li", None) in calls
    assert ctx.suppress_blur.current is True
    assert ("set_cursor", ts, 0) in calls


def test_delete_table_pushes_history_and_marks_dirty():
    """可撤销：删表前整体入栈一次，并标脏触发保存。"""
    doc = _doc()
    ts, _te = _table_span(doc)
    ctx, calls = _op(doc, "delete_table", {"table_start": ts})

    assert calls.count("push_history") == 1, calls
    assert "mark_dirty" in calls
    assert ctx.undo_push_pending.current is True


def test_delete_table_leaves_other_tables_untouched():
    """文档里有两张表时，只删掉指定的那一张。"""
    doc = parse_markdown(
        "| a | b |\n| --- | --- |\n| 1 | 2 |\n\n中间\n\n| c | d |\n| --- | --- |\n| 3 | 4 |\n"
    )
    first_start, first_end = 0, 0
    starts = [i for i, ln in enumerate(doc.lines) if ln.block_type == BlockType.TABLE]
    assert len(starts) == 6, starts  # 2 张表 × 3 行
    first_start = starts[0]
    first_end = first_start + 2

    _op(doc, "delete_table", {"table_start": first_start})

    remaining = _table_lines(doc)
    assert len(remaining) == 3, [ln.raw for ln in remaining]
    assert [split_row(ln.raw) for ln in remaining] == [
        ["c", "d"],
        ["---", "---"],
        ["3", "4"],
    ]
    assert doc.lines[first_start].block_type == BlockType.PARAGRAPH
    assert first_end - first_start == 2  # 说明夹具确实是 3 行一张表


# ---------------------------------------------------------------------------
# delete_row / clear_row
# ---------------------------------------------------------------------------


def test_delete_row_keeps_header_and_separator():
    """删数据行：表头行与分隔行必须原样留下（删错了整张表就废了）。"""
    doc = _doc()
    ts, _te = _table_span(doc)
    header_raw = doc.lines[ts].raw
    sep_raw = doc.lines[ts + 1].raw

    _op(doc, "delete_row", {"li": ts + 3})

    assert len(_table_lines(doc)) == TABLE_SPAN - 1
    assert doc.lines[ts].raw == header_raw
    assert doc.lines[ts + 1].raw == sep_raw
    assert split_row(doc.lines[ts + 2].raw) == ["Alpha", "服务", "运行中"]


def test_delete_row_refuses_when_only_one_data_row_left():
    """只剩一行数据时不动结构（视图侧改走 clear_row，此处兜底不删）。"""
    doc = _doc()
    ts, _te = _table_span(doc)
    _op(doc, "delete_row", {"li": ts + 3})  # 删到只剩 1 行数据
    assert len(_table_lines(doc)) == TABLE_SPAN - 1

    _op(doc, "delete_row", {"li": ts + 2})
    assert len(_table_lines(doc)) == TABLE_SPAN - 1, "最后一行的结构被删掉了"


def test_clear_row_blanks_cells_and_keeps_columns():
    """clear_row：清空内容但保留该行（列数不变），表格不塌。"""
    doc = _doc()
    ts, _te = _table_span(doc)

    _op(doc, "clear_row", {"li": ts + 2})

    assert len(_table_lines(doc)) == TABLE_SPAN
    cells = split_row(doc.lines[ts + 2].raw)
    assert cells == [""] * COL_COUNT, cells


# ---------------------------------------------------------------------------
# add_row / add_col / delete_col
# ---------------------------------------------------------------------------


def test_add_row_appends_row_with_column_count():
    """新增行：列数由 col_count 决定，追加在指定行之后。"""
    doc = _doc()
    _ts, te = _table_span(doc)

    _op(doc, "add_row", {"after_li": te, "col_count": COL_COUNT})

    assert len(_table_lines(doc)) == TABLE_SPAN + 1
    assert split_row(doc.lines[te + 1].raw) == [""] * COL_COUNT


def test_add_row_in_middle_does_not_disturb_following_rows():
    """在表中间（分隔行之后）插行：后面的数据行整体后移，内容不乱。"""
    doc = _doc()
    ts, _te = _table_span(doc)

    _op(doc, "add_row", {"after_li": ts + 1, "col_count": COL_COUNT})

    assert split_row(doc.lines[ts + 2].raw) == [""] * COL_COUNT
    assert split_row(doc.lines[ts + 3].raw) == ["Alpha", "服务", "运行中"]
    assert split_row(doc.lines[ts + 4].raw) == ["Beta", "组件", "已停止"]


def test_add_col_inserts_empty_cells_and_separator_marker():
    """新增列：分隔行插 `---`（插空串会让该列不再被识别为分隔行）。"""
    doc = _doc()
    ts, _te = _table_span(doc)

    _op(doc, "add_col", {"table_start": ts, "col_idx": 1})

    lines = _table_lines(doc)
    assert all(len(split_row(ln.raw)) == COL_COUNT + 1 for ln in lines), [
        ln.raw for ln in lines
    ]
    assert split_row(lines[0].raw) == ["名称", "", "类型", "说明"]
    assert split_row(lines[1].raw) == ["---", "---", ":---:", "---:"]
    assert is_table_separator(lines[1].raw), lines[1].raw
    assert split_row(lines[2].raw) == ["Alpha", "", "服务", "运行中"]


def test_delete_col_removes_column_from_every_line():
    """删列：表头 / 分隔行 / 数据行一起少一列，行数不变。"""
    doc = _doc()
    ts, _te = _table_span(doc)

    _op(doc, "delete_col", {"table_start": ts, "col_idx": 0})

    lines = _table_lines(doc)
    assert len(lines) == TABLE_SPAN
    assert all(len(split_row(ln.raw)) == COL_COUNT - 1 for ln in lines)
    assert split_row(lines[0].raw) == ["类型", "说明"]
    assert split_row(lines[2].raw) == ["服务", "运行中"]


# ---------------------------------------------------------------------------
# set_align
# ---------------------------------------------------------------------------


def test_set_align_writes_marker_on_separator_only():
    """对齐：只改分隔行，数据行一个字都不许动（写成 `center` 会毁掉整张表）。"""
    doc = _doc()
    ts, _te = _table_span(doc)
    data_before = [ln.raw for ln in doc.lines[ts + 2 : ts + 4]]

    _op(
        doc,
        "set_align",
        {"table_start": ts, "col_idx": 2, "align": "center"},
    )

    sep = _table_lines(doc)[1]
    assert split_row(sep.raw) == ["---", ":---:", ":---:"]
    assert is_table_separator(sep.raw)
    assert [ln.raw for ln in doc.lines[ts + 2 : ts + 4]] == data_before


def test_set_align_left_writes_plain_marker():
    """左对齐写成 `---`（不是空串，也不是语义字符串 `left`）。"""
    doc = _doc()
    ts, _te = _table_span(doc)

    _op(doc, "set_align", {"table_start": ts, "col_idx": 0, "align": "left"})

    cells = split_row(_table_lines(doc)[1].raw)
    assert cells == ["---", ":---:", "---:"], cells


# ---------------------------------------------------------------------------
# 通用：所有 op 都先入栈
# ---------------------------------------------------------------------------


def test_every_op_pushes_history_once():
    """每支 op 恰好 push_history 一次（否则 Ctrl+Z 要么还原不了、要么一步还原多次）。"""
    cases = [
        ("add_row", {"after_li": 3, "col_count": COL_COUNT}),
        ("delete_row", {"li": 3}),
        ("clear_row", {"li": 3}),
        ("add_col", {"table_start": 1, "col_idx": 1}),
        ("delete_col", {"table_start": 1, "col_idx": 1}),
        ("set_align", {"table_start": 1, "col_idx": 1, "align": "right"}),
        ("delete_table", {"table_start": 1}),
    ]
    for op, params in cases:
        doc = _doc()
        _ctx, calls = _op(doc, op, params)
        assert calls.count("push_history") == 1, (op, calls)


def test_join_row_roundtrip_matches_view_shape():
    """`_fence` 与视图共用 join_row/split_row：写回的行必须是 `| a | b |` 形状。"""
    doc = _doc()
    ts, _te = _table_span(doc)
    _op(doc, "add_row", {"after_li": ts, "col_count": COL_COUNT})
    raw = doc.lines[ts + 1].raw
    assert raw == join_row([""] * COL_COUNT)
    assert raw.startswith("| ") and raw.endswith(" |")
