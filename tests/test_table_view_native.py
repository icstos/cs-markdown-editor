"""表格视图（views/table_view.py）渲染层测试：三态几何一致 / 工具栏行高 / 操作目标。

背景（本轮改动前的真机实测，DPR 1.5）：
1. 表头单元格里前置了一个**常驻对齐图标**（12px + 4px 间距），把表头文字右推
   16 逻辑 px，而数据格只缩进 10 —— 表头"名称"的墨迹左缘比数据"Alpha"右移
   **21 物理 px（14 逻辑 px）**；点击进编辑（编辑框按 10 缩进）时又跳回来。
2. 数据格 `max_lines=4` 而行高固定 40px（= 只放得下一行半）→ 第二行被**横向切半**
   （真机截图里"请求分发"只剩上半个字），既不是完整内容也没有省略号。
3. 工具栏是 `TextButton × 4 + Dropdown + IconButton`，Material 固有尺寸压不动，
   整条工具栏 **67 物理 px（≈45 逻辑 px）** —— 表格卡片里最大的一块空白，
   而代码块头部只有 22px。
4. `ft.DataRow.color` 恒为非 None（斑马纹），Flutter 侧它优先于表级
   `data_row_color`，于是 HOVERED / PRESSED **从未生效**（死配置）；
   而无活动单元格时"删行/删列"默认作用在最后一行的最后一列。

本文件把上述四条的**修正**锁死。全部断言都基于真实渲染出来的控件树
（`tests/harness.py` 进程内渲染），不复制实现细节。
"""

import sys
from contextlib import contextmanager
from pathlib import Path

import flet as ft

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.document import BlockType, Line, Segment, SegType
from parser import parse_markdown
from tests.harness import RenderHarness, invoke
from views import table_view as tv

CONTENT_WIDTH = 800.0
# 4 列（左 / 中 / 左 / 右）、3 行（1 表头 + 2 数据）
TABLE_MD = (
    "| 名称 | 类型 | 说明 | 状态 |\n"
    "| --- | :---: | --- | ---: |\n"
    "| Alpha | 服务 | 核心调度服务，负责请求分发 | 运行中 |\n"
    "| Beta | 组件 | 数据采集 | 已停止 |\n"
)
ROWS_INCL_HEADER = 3  # 表头 + 2 数据行（尺寸标签口径）
# 解析后 document.lines 里的 TABLE 行数：表头 + **分隔行** + 2 数据行。分隔行是被
# 保留的（见 tests/test_table_smoke.py 的说明），故比 ROWS_INCL_HEADER 多 1。
TABLE_LINE_COUNT = 4
COL_COUNT = 4


def _table_lines():
    """用真实解析器产出 TABLE 行（保证 block_type / raw 与线上一致）。"""
    doc = parse_markdown(TABLE_MD)
    lines = [ln for ln in doc.lines if ln.block_type == BlockType.TABLE]
    assert len(lines) == TABLE_LINE_COUNT, f"解析出的 TABLE 行数异常：{len(lines)}"
    return lines


def _synthetic_table_lines(raws: list[str]) -> list[Line]:
    """手工构造 TABLE 行（用于覆盖解析器产不出的形态，如单列表格）。"""
    out = []
    for raw in raws:
        line = Line(block_type=BlockType.TABLE, raw=raw)
        line.segments = [Segment(SegType.TEXT, raw, raw)]
        out.append(line)
    return out


@contextmanager
def _rendered(*, is_current_line: bool = False, lines=None):
    """渲染 TableView 并交出 (harness, 记录到的 on_table_op 列表)。

    `lines` 给了就用它（用于构造解析器产不出的形态，如单列表格）。
    """
    lines = _table_lines() if lines is None else lines
    ops: list[tuple[str, dict]] = []

    @ft.component
    def _Probe():
        return tv.TableView(
            lines,
            0,
            content_width=CONTENT_WIDTH,
            clipboard_ref=None,
            on_change_cell=None,
            on_table_op=lambda op, params: ops.append((op, params)),
            on_table_focus=None,
            on_table_blur=None,
            table_nav_ref=None,
            is_current_line=is_current_line,
            auto_focus_li=None,
        )

    harness = RenderHarness()
    try:
        harness.render(_Probe)
        yield harness, ops
    finally:
        harness.dispose()


# ---------------------------------------------------------------------------
# 控件定位
# ---------------------------------------------------------------------------


def _data_table(h: RenderHarness) -> ft.DataTable:
    tables = h.find(lambda n: isinstance(n, ft.DataTable))
    assert tables, "未渲染出 DataTable"
    return tables[0]


def _descend_to_container(node):
    """沿 content 向下找到第一个 Container（浏览态外面套着 ContextMenu 等）。"""
    for _ in range(4):
        if isinstance(node, ft.Container):
            return node
        node = getattr(node, "content", None)
        if node is None:
            break
    raise AssertionError(f"未找到单元格容器，停在 {type(node).__name__}")


def _header_inner(h: RenderHarness, ci: int) -> ft.Container:
    """表头单元格的内层容器。"""
    return _descend_to_container(_data_table(h).columns[ci].label)


def _cell(h: RenderHarness, ri: int, ci: int) -> ft.DataCell:
    """数据行 ri（0 起）第 ci 列的 DataCell。"""
    return _data_table(h).rows[ri].cells[ci]


def _cell_inner(h: RenderHarness, ri: int, ci: int) -> ft.Container:
    return _descend_to_container(_cell(h, ri, ci).content)


def _cell_field(h: RenderHarness, ri: int, ci: int) -> ft.TextField:
    inner = _cell_inner(h, ri, ci)
    assert isinstance(inner.content, ft.TextField), "该单元格不在编辑态"
    return inner.content


def _tap_cell(h: RenderHarness, ri: int, ci: int) -> None:
    """点数据单元格（走它自己的 on_tap，与真机点击同一入口）。"""
    cell = _cell(h, ri, ci)
    assert cell.on_tap is not None, "该单元格未绑定 on_tap（编辑态不绑）"
    h.interact(invoke, cell.on_tap, None)


def _tap_header(h: RenderHarness, ci: int) -> None:
    """点表头单元格（表头用 GestureDetector 的 on_tap）。"""
    node = _data_table(h).columns[ci].label
    while node is not None and not isinstance(node, ft.GestureDetector):
        node = getattr(node, "content", None)
    assert node is not None, "表头未找到 GestureDetector"
    h.interact(invoke, node.on_tap, None)


def _tb_button(h: RenderHarness, icon: str, label: str = "") -> ft.Container:
    """按 (图标, 文字) 定位工具栏按钮。

    图标是区分"增行/删行"（同为文字"行"）的唯一手段，故两者都要匹配；
    `label=""` 用于纯图标按钮（复制 / 删除整表）。
    """
    hits = []
    for node in h.find(lambda n: isinstance(n, ft.Container)):
        row = node.content
        if not isinstance(row, ft.Row):
            continue
        icons = [c.icon for c in row.controls if isinstance(c, ft.Icon)]
        texts = [c.value for c in row.controls if isinstance(c, ft.Text)]
        if icon in icons and (label in texts if label else not texts):
            hits.append(node)
    assert hits, f"未找到工具栏按钮 icon={icon!r} label={label!r}"
    return hits[0]


def _toolbar(h: RenderHarness) -> ft.Row:
    for node in h.find(lambda n: isinstance(n, ft.Row)):
        if node.height == tv._TB_H and any(
            isinstance(c, ft.Container) and isinstance(c.content, ft.Row)
            for c in node.controls
        ):
            return node
    raise AssertionError("未找到表格工具栏（height == _TB_H 的 Row）")


def _align_button(h: RenderHarness) -> ft.PopupMenuButton:
    hits = h.find(lambda n: isinstance(n, ft.PopupMenuButton) and n.tooltip)
    assert hits, "未找到对齐按钮"
    return hits[0]


# ---------------------------------------------------------------------------
# 1. 三态同一内边距（表头 / 数据格 / 编辑框）
# ---------------------------------------------------------------------------

_CELL_PADDING = ft.Padding.symmetric(
    horizontal=tv._CELL_PAD_H, vertical=tv._CELL_PAD_V
)


def test_edit_padding_compensates_textfield_leading_inset():
    """编辑框的横向内边距要比浏览态**少** `_EDIT_LEAD_INSET`。

    这不是"表头/padding 该是多少"的偏好问题：`ft.TextField` 在文字前有一段固有
    行首内缩（真机实测编辑态字形左缘比浏览态右移 6 物理 px），所以"三态 padding
    同值"反而会让文字在点进编辑时右移。护栏写成**文字位置**口径：
    `编辑框 content_padding.left + 固有内缩 == 浏览态内边距`。
    """
    assert tv._CELL_EDIT_PAD_H == tv._CELL_PAD_H - tv._EDIT_LEAD_INSET
    assert tv._EDIT_LEAD_INSET > 0, "固有内缩为 0 时这条补偿规则就没有意义了"


def test_header_is_text_only_no_decoration():
    """表头单元格**只有文字**：常驻对齐图标会把表头推离数据列（改前 14 逻辑 px）。"""
    with _rendered() as (h, _ops):
        for ci in range(COL_COUNT):
            content = _header_inner(h, ci).content
            assert isinstance(content, ft.Text), (
                f"表头第 {ci} 列的内容是 {type(content).__name__}，应只有 Text"
                "（常驻装饰控件会把表头文字推离数据列）"
            )
            assert content.value, "表头文字不应为空"


def test_header_and_data_share_cell_padding():
    """表头 / 数据格的 padding 与编辑框的 content_padding 对齐到**同一文字左缘**。"""
    with _rendered() as (h, _ops):
        for ci in range(COL_COUNT):
            assert _header_inner(h, ci).padding == _CELL_PADDING, (
                f"表头第 {ci} 列内边距与数据格不一致 → 表头会与数据列错位"
            )
        for ri in range(2):
            for ci in range(COL_COUNT):
                assert _cell_inner(h, ri, ci).padding == _CELL_PADDING

        _tap_cell(h, 1, 1)
        pad = _cell_field(h, 1, 1).content_padding
        assert pad.left == tv._CELL_EDIT_PAD_H and pad.right == tv._CELL_EDIT_PAD_H
        assert pad.top == tv._CELL_PAD_V and pad.bottom == tv._CELL_PAD_V, (
            "编辑框纵向内边距与浏览态不同 → 点击进编辑会上下跳字"
        )
        assert pad.left + tv._EDIT_LEAD_INSET == _CELL_PADDING.left, (
            "编辑框扣除固有行首内缩后，横向应与浏览态同一条竖线"
        )


def test_header_edit_field_uses_same_padding():
    """表头进入编辑态后，文字左缘同样与数据格对齐（改前这里差 14 逻辑 px）。"""
    with _rendered() as (h, _ops):
        _tap_header(h, 2)
        label = _data_table(h).columns[2].label
        assert isinstance(label, ft.Container) and isinstance(
            label.content, ft.TextField
        ), "表头未进入编辑态"
        assert label.content.content_padding == ft.Padding.symmetric(
            horizontal=tv._CELL_EDIT_PAD_H, vertical=tv._CELL_PAD_V
        )


def test_header_and_data_text_have_same_alignment_rule():
    """表头与数据格都用 Container.alignment 做水平对齐（同一条规则、同一个取色口）。"""
    with _rendered() as (h, _ops):
        for ci in range(COL_COUNT):
            assert (
                _header_inner(h, ci).alignment == _cell_inner(h, 0, ci).alignment
            ), f"第 {ci} 列的表头与数据格对齐方式不同"


# ---------------------------------------------------------------------------
# 2. 单元格内容单行省略（网格行高恒定）
# ---------------------------------------------------------------------------


def test_cells_are_single_line_with_ellipsis():
    """单元格文本单行 + 省略号：行高恒定，不会出现"第二行被横向切半"。"""
    with _rendered() as (h, _ops):
        for ri in range(2):
            for ci in range(COL_COUNT):
                text = _cell_inner(h, ri, ci).content
                assert isinstance(text, ft.Text)
                assert text.max_lines == 1, (
                    f"({ri},{ci}) max_lines={text.max_lines}：固定行高下多行会被切半"
                )
                assert text.overflow == ft.TextOverflow.ELLIPSIS


def test_long_cell_gets_tooltip_short_cell_does_not():
    """被省略号裁切风险高的单元格挂 tooltip（完整内容不丢），短内容不挂。"""
    with _rendered() as (h, _ops):
        # 夹具里所有单元格都短于门槛 → 一个 tooltip 都不挂
        for ri in range(2):
            for ci in range(COL_COUNT):
                assert _cell_inner(h, ri, ci).tooltip is None
        # 门槛之上（≥ _TOOLTIP_MIN_CHARS 个可见字符）才挂
        assert tv._cell_tooltip(tv._render_cell_spans("很长的一段说明文字" * 3)) is not None
        assert tv._cell_tooltip(tv._render_cell_spans("短")) is None
        # 空单元格（不换行空格占位）不该挂
        assert tv._cell_tooltip(tv._render_cell_spans("")) is None
        assert tv._cell_tooltip(tv._render_cell_spans("   ")) is None
        # tooltip 是**markdown 折叠后**的可见文本（不是源码）
        bold = "**" + "很长的粗体内容" * 3 + "**"
        tip = tv._cell_tooltip(tv._render_cell_spans(bold))
        assert tip is not None and "**" not in tip
        assert tip == "很长的粗体内容" * 3


def test_long_cell_container_gets_tooltip_in_tree():
    """长单元格在**渲染树里**真的挂上 tooltip（不只是 `_cell_tooltip` 的单元行为）。

    单独盯"接线"：`_cell_tooltip` 算得再对，只要没交到 `Container.tooltip` 上，
    用户还是看不到被省略号裁掉的内容。夹具里的单元格都短于门槛，故"短格不挂
    tooltip"那半边能被树断言，长格那半边只能用构造行来渲染。
    """
    long_cell = "很长的说明文字" * 5  # 35 字 > _TOOLTIP_MIN_CHARS(20)
    lines = _synthetic_table_lines(
        ["| 名称 | 类型 |", "| --- | --- |", f"| {long_cell} | x |"]
    )
    with _rendered(lines=lines) as (h, _ops):
        assert _cell_inner(h, 0, 0).tooltip == long_cell, "长单元格未挂 tooltip"
        assert _cell_inner(h, 0, 1).tooltip is None, "短单元格不该挂 tooltip"


def test_row_height_and_head_height_are_constants():
    """行高来自模块常量：改行高必须改常量，不能各处写数字。"""
    with _rendered() as (h, _ops):
        table = _data_table(h)
        assert table.data_row_height == tv._DATA_ROW_H
        assert table.heading_row_height == tv._HEAD_ROW_H


# ---------------------------------------------------------------------------
# 3. 工具栏：行高硬锁 + 无 Material 固有尺寸控件
# ---------------------------------------------------------------------------


def test_toolbar_has_no_material_intrinsic_controls():
    """工具栏里不得出现 IconButton / TextButton / Dropdown（固有高 40/36/48）。"""
    with _rendered() as (h, _ops):
        toolbar = _toolbar(h)
        assert toolbar.height == tv._TB_H, (
            f"工具栏高度 {toolbar.height}（应硬锁 {tv._TB_H}）"
        )
        bad = [
            type(c).__name__
            for c in toolbar.controls
            if isinstance(c, (ft.IconButton, ft.TextButton, ft.Dropdown))
        ]
        assert not bad, f"工具栏出现 Material 固有尺寸控件：{bad}"


def test_toolbar_children_are_height_locked():
    """**每一个**工具栏按钮都显式定高 `_TB_H`（行高 = 最高子项，压内边距无效）。

    注意这里是"逐个查工具栏自己的子项"，不是 `h.find(...)` 全树扫 Container ——
    后者必须写 `if node.height is None: continue`（全树里有大量没定高的 Container），
    于是"把 height 删掉"这种变异**反而不会被发现**（跳过 = 通过）。守卫必须建立在
    "这个子项一定在工具栏里"，才能把"漏定高"判红。
    """
    with _rendered() as (h, _ops):
        toolbar = _toolbar(h)
        # 工具栏里有按钮子的子项：`_tb_btn`（Container + Row）与对齐触发器
        # （PopupMenuButton）。分隔条 / expander 的 content 为 None，不算按钮。
        buttons = [
            c
            for c in toolbar.controls
            if isinstance(c, ft.PopupMenuButton)
            or (isinstance(c, ft.Container) and isinstance(c.content, ft.Row))
        ]
        assert len(buttons) >= 5, f"工具栏按钮数量异常（{len(buttons)}）：定位可能失效"
        for node in buttons:
            assert node.height == tv._TB_H, (
                f"{type(node).__name__} 高度 {node.height} 未锁定 {tv._TB_H}"
                "（子项固有高会顶开整条工具栏）"
            )
        # 对齐触发器是 PopupMenuButton：它同样不能留空高度
        assert _align_button(h).height == tv._TB_H


def test_size_label_shows_rows_and_columns():
    """尺寸标签写成"N 行 × M 列"：原来的 `4 × 4` 两个数字同形，看不出哪个是行。"""
    with _rendered() as (h, _ops):
        labels = [
            n.value
            for n in h.find(lambda n: isinstance(n, ft.Text))
            if isinstance(n.value, str) and "行 ×" in n.value
        ]
        assert labels == [f"{ROWS_INCL_HEADER} 行 × {COL_COUNT} 列"], labels


# ---------------------------------------------------------------------------
# 4. 操作目标显式：无活动单元格时行/列级操作置灰
# ---------------------------------------------------------------------------

_ADD_ICON = ft.Icons.ADD
_DEL_ICON = ft.Icons.REMOVE


def test_row_col_ops_disabled_without_active_cell():
    """没有活动单元格时，删行/删列/对齐都不可点（改前会默认删掉最后一列）。"""
    with _rendered() as (h, _ops):
        for label in ("行", "列"):
            btn = _tb_button(h, _DEL_ICON, label)
            assert btn.on_click is None, f"删{label}在没有活动单元格时仍可点"
            assert not btn.ink, f"删{label}仍带水波反馈（禁用态应无水波）"
        assert _align_button(h).disabled is True, "对齐按钮在无活动单元格时应置灰"


def test_row_col_ops_enabled_after_clicking_cell():
    """点进任一单元格后，三个操作全部可用。"""
    with _rendered() as (h, _ops):
        _tap_cell(h, 0, 1)
        for label in ("行", "列"):
            btn = _tb_button(h, _DEL_ICON, label)
            assert btn.on_click is not None, f"删{label}在活动单元格下应可用"
            assert btn.ink
        assert _align_button(h).disabled is False


def test_delete_column_disabled_when_single_column():
    """单列表格：删列永远不可用（表格至少保留一列）。

    单列表格**解析器产不出来**（GFM 需要 ≥2 列，见 `parse_markdown("| a |")` →
    PARAGRAPH），但渲染层只看 `block_type` + `raw`，故直接构造 Line 来覆盖该边界。
    """
    lines = _synthetic_table_lines(["| a |", "| --- |", "| 1 |"])
    with _rendered(lines=lines) as (h, _ops):
        h.interact(invoke, _cell(h, 0, 0).on_tap, None)
        assert _tb_button(h, _DEL_ICON, "列").on_click is None, "单列表格仍可删列"
        assert _tb_button(h, _DEL_ICON, "行").on_click is not None, "单列表格仍可删行"


def test_delete_table_button_emits_op():
    """工具栏垃圾桶 = 删除整表；下拉/右键菜单同一入口（此前完全没有入口）。"""
    with _rendered() as (h, ops):
        btn = _tb_button(h, ft.Icons.DELETE_OUTLINE)
        h.interact(invoke, btn.on_click, None)
        assert ("delete_table", {"table_start": 0}) in ops, ops


def test_context_menu_has_delete_table():
    """右键菜单最后一项是「删除表格」（用分隔线与删行/删列隔开）。"""
    with _rendered() as (h, _ops):
        # 注意：`walk` 只沿 content/controls/actions/title 下钻，**不会进入**
        # `DataRow.cells` / `DataColumn.label`，所以右键菜单必须从 DataTable 对象上
        # 直接取（不能靠 h.find）。
        menus = [
            _cell(h, 0, 0).content,  # 数据单元格的右键菜单
            _data_table(h).columns[2].label,  # 表头单元格的右键菜单
        ]
        for menu in menus:
            assert isinstance(menu, ft.ContextMenu), type(menu).__name__
            items = menu.secondary_items
            labels = [i.content for i in items if isinstance(i.content, str)]
            assert labels[-1] == "删除表格", labels
            assert items[-2].content is None, "删除表格前应有分隔线"


# ---------------------------------------------------------------------------
# 5. 活动列/行的定位高亮
# ---------------------------------------------------------------------------


def test_active_column_header_is_highlighted():
    """活动列的表头有色片，其余列没有（宽表里回答"我在哪一列"）。"""
    with _rendered() as (h, _ops):
        assert all(_header_inner(h, ci).bgcolor is None for ci in range(COL_COUNT))
        _tap_cell(h, 1, 2)
        assert _header_inner(h, 2).bgcolor is not None, "活动列表头未高亮"
        assert _header_inner(h, 0).bgcolor is None, "非活动列表头不应高亮"


def test_active_row_band_and_no_active_highlight_when_idle():
    """活动行整条行带染色；表格没被点过时**不应**出现高亮（不能谎报选区）。"""
    with _rendered() as (h, _ops):
        rows = _data_table(h).rows
        idle = [r.color[ft.ControlState.DEFAULT] for r in rows]
        assert len(set(map(str, idle))) == 2, (
            "空闲态应是纯斑马纹（相邻行两种颜色）"
        )

        _tap_cell(h, 0, 0)
        rows = _data_table(h).rows
        active = rows[0].color[ft.ControlState.DEFAULT]
        other = rows[1].color[ft.ControlState.DEFAULT]
        assert active != other, "活动行未与其它行区分"
        # 斑马纹的两种颜色都不该出现在活动行上
        assert all(active != c for c in idle)


def test_row_color_is_state_mapped_so_hover_works():
    """行底色必须是**状态映射**：单一颜色会遮住表级 `data_row_color` 的悬停色。

    改前的 `ft.DataRow.color` 恒为非 None（斑马纹），Flutter 侧它优先于表级
    `data_row_color`，HOVERED / PRESSED 从未生效 —— 悬停高亮是死配置。
    """
    with _rendered() as (h, _ops):
        for row in _data_table(h).rows:
            assert isinstance(row.color, dict), "行底色不是状态映射 → 悬停高亮会失效"
            for state in (
                ft.ControlState.DEFAULT,
                ft.ControlState.HOVERED,
                ft.ControlState.PRESSED,
            ):
                assert state in row.color, f"行底色缺少 {state} 状态"
            assert row.color[ft.ControlState.HOVERED] != row.color[ft.ControlState.DEFAULT]
        # 表级不再重复配置行底色（单一来源）
        assert _data_table(h).data_row_color is None


# ---------------------------------------------------------------------------
# 6. 对齐控件（工具栏承载列对齐状态）
# ---------------------------------------------------------------------------


def test_align_button_reflects_current_column():
    """对齐按钮显示**当前列**的对齐图标与勾选态。"""
    with _rendered() as (h, _ops):
        _tap_cell(h, 0, 1)  # 第 1 列是 :---: → center
        btn = _align_button(h)
        icon = btn.content.controls[0]
        assert icon.icon == ft.Icons.FORMAT_ALIGN_CENTER, icon.icon
        checked = [i for i in btn.items if i.checked]
        assert len(checked) == 1 and "居中" in checked[0].content.value

        _tap_cell(h, 0, 3)  # 第 3 列是 ---: → right
        btn = _align_button(h)
        assert btn.content.controls[0].icon == ft.Icons.FORMAT_ALIGN_RIGHT


def test_align_click_emits_set_align_for_current_column():
    """点对齐菜单项 → set_align 作用于当前列（不是最后一列）。"""
    with _rendered() as (h, ops):
        _tap_cell(h, 0, 1)
        btn = _align_button(h)
        item = next(i for i in btn.items if "右对齐" in i.content.value)
        h.interact(invoke, item.on_click, None)
        assert ("set_align", {"table_start": 0, "col_idx": 1, "align": "right"}) in ops


# ---------------------------------------------------------------------------
# 7. 工具栏整体渲染（不抛异常、结构完整）
# ---------------------------------------------------------------------------


def test_toolbar_renders_with_current_line_highlight():
    """`is_current_line=True`（光标在表内）时整块加高亮外框，且渲染无异常。"""
    with _rendered(is_current_line=True) as (h, _ops):
        assert _toolbar(h) is not None
        borders = [
            n
            for n in h.find(lambda n: isinstance(n, ft.Container))
            if n.border is not None and n.padding == ft.Padding.all(1)
        ]
        assert borders, "当前行高亮外框缺失"
